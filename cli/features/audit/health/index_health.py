"""§4 Index Health — unused indexes, duplicates/overlaps, size.

PostgreSQL: pg_stat_user_indexes + pg_index for uniqueness/primary + pg_indexes
(or pg_attribute) for column lists. Duplicates detected when one index's column
prefix is a subset of another's column list on the same table.

MySQL: information_schema.STATISTICS for columns + performance_schema.
table_io_waits_summary_by_index_usage for scan counts (COUNT_FETCH).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from features.audit.health.models import (
    DuplicateIndexPair,
    IndexHealthSection,
    IndexRow,
)


_PG_INDEX_SQL = """
SELECT
    s.schemaname,
    s.relname AS table_name,
    s.indexrelname AS index_name,
    i.indisunique,
    i.indisprimary,
    pg_relation_size(s.indexrelid) AS size_bytes,
    COALESCE(s.idx_scan, 0) AS scans,
    s.idx_tup_read,
    s.idx_tup_fetch,
    pg_get_indexdef(s.indexrelid) AS idx_def
FROM pg_stat_user_indexes s
JOIN pg_index i ON i.indexrelid = s.indexrelid
WHERE s.schemaname NOT IN ('pg_catalog','information_schema','pg_toast')
ORDER BY pg_relation_size(s.indexrelid) DESC;
"""


def _parse_columns(idx_def: Optional[str]) -> List[str]:
    """Extract column list from a CREATE INDEX definition.

    Example: 'CREATE INDEX idx_x ON public.t USING btree (a, b, lower(c))'
    Returns the expressions inside the outermost parens, split on top-level commas.
    """
    if not idx_def:
        return []
    try:
        start = idx_def.index("(")
    except ValueError:
        return []
    depth = 0
    cols: List[str] = []
    buf: List[str] = []
    for ch in idx_def[start:]:
        if ch == "(":
            depth += 1
            if depth == 1:
                continue
        elif ch == ")":
            depth -= 1
            if depth == 0:
                if buf:
                    cols.append("".join(buf).strip())
                break
        if depth >= 1:
            if ch == "," and depth == 1:
                cols.append("".join(buf).strip())
                buf = []
            else:
                buf.append(ch)
    return [c for c in cols if c]


def _is_prefix_of(a: List[str], b: List[str]) -> bool:
    """Returns True if a is a strict prefix of b (a shorter or equal)."""
    if len(a) > len(b):
        return False
    return a == b[: len(a)]


def collect_index_health(conn: Any, engine: str) -> Optional[IndexHealthSection]:
    engine = (engine or "").lower()
    if engine == "postgresql":
        return _collect_pg(conn)
    if engine == "mysql":
        return _collect_mysql(conn)
    return None


def _collect_pg(conn: Any) -> IndexHealthSection:
    section = IndexHealthSection()
    all_rows: List[IndexRow] = []

    try:
        with conn.cursor() as cur:
            cur.execute(_PG_INDEX_SQL)
            for row in cur.fetchall():
                (
                    schema,
                    table,
                    index,
                    is_unique,
                    is_primary,
                    size,
                    scans,
                    tup_read,
                    tup_fetch,
                    idx_def,
                ) = row
                cols = _parse_columns(idx_def)
                all_rows.append(
                    IndexRow(
                        schema=schema,
                        table=table,
                        index=index,
                        is_unique=bool(is_unique),
                        is_primary=bool(is_primary),
                        columns=cols,
                        size_bytes=int(size) if size is not None else None,
                        scans=int(scans or 0),
                        tuples_read=int(tup_read) if tup_read is not None else None,
                        tuples_fetched=int(tup_fetch) if tup_fetch is not None else None,
                    )
                )
    except Exception:
        return section

    section.all_indexes = all_rows
    section.total_indexes = len(all_rows)
    section.unused_indexes = [
        r for r in all_rows if r.scans == 0 and not r.is_primary and not r.is_unique
    ]

    # Duplicate / overlap detection: per-table, sort by column count ascending,
    # flag shorter indexes whose columns are a prefix of a longer index's columns.
    by_table: Dict[str, List[IndexRow]] = {}
    for r in all_rows:
        by_table.setdefault(f"{r.schema}.{r.table}", []).append(r)

    for idx_list in by_table.values():
        sorted_idx = sorted(idx_list, key=lambda r: len(r.columns))
        for i, shorter in enumerate(sorted_idx):
            if shorter.is_primary or shorter.is_unique or not shorter.columns:
                continue
            for longer in sorted_idx[i + 1 :]:
                if shorter.index == longer.index:
                    continue
                if _is_prefix_of(shorter.columns, longer.columns):
                    section.duplicates.append(
                        DuplicateIndexPair(
                            schema=shorter.schema,
                            table=shorter.table,
                            redundant_index=shorter.index,
                            covered_by=longer.index,
                            redundant_columns=shorter.columns,
                            covering_columns=longer.columns,
                            wasted_bytes=shorter.size_bytes,
                        )
                    )
                    break

    return section


def _collect_mysql(conn: Any) -> IndexHealthSection:
    section = IndexHealthSection()
    all_rows: List[IndexRow] = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    s.TABLE_SCHEMA,
                    s.TABLE_NAME,
                    s.INDEX_NAME,
                    MAX(s.NON_UNIQUE) = 0 AS is_unique,
                    s.INDEX_NAME = 'PRIMARY' AS is_primary,
                    GROUP_CONCAT(s.COLUMN_NAME ORDER BY s.SEQ_IN_INDEX) AS cols,
                    NULL AS size_bytes
                FROM information_schema.STATISTICS s
                WHERE s.TABLE_SCHEMA NOT IN
                    ('mysql','information_schema','performance_schema','sys')
                GROUP BY s.TABLE_SCHEMA, s.TABLE_NAME, s.INDEX_NAME;
                """
            )
            rows = cur.fetchall()

            # Scan counts from performance_schema if available
            scan_by_index: Dict[tuple, int] = {}
            try:
                cur.execute(
                    """
                    SELECT OBJECT_SCHEMA, OBJECT_NAME, INDEX_NAME, COUNT_FETCH
                    FROM performance_schema.table_io_waits_summary_by_index_usage
                    WHERE INDEX_NAME IS NOT NULL;
                    """
                )
                for schema, table, index, fetches in cur.fetchall():
                    scan_by_index[(schema, table, index)] = int(fetches or 0)
            except Exception:
                pass

            for schema, table, index, is_unique, is_primary, cols_csv, size in rows:
                cols = [c.strip() for c in (cols_csv or "").split(",") if c.strip()]
                scans = scan_by_index.get((schema, table, index), 0)
                all_rows.append(
                    IndexRow(
                        schema=schema,
                        table=table,
                        index=index,
                        is_unique=bool(is_unique),
                        is_primary=bool(is_primary),
                        columns=cols,
                        size_bytes=size,
                        scans=scans,
                    )
                )
    except Exception:
        return section

    section.all_indexes = all_rows
    section.total_indexes = len(all_rows)
    section.unused_indexes = [
        r for r in all_rows if r.scans == 0 and not r.is_primary and not r.is_unique
    ]

    by_table: Dict[str, List[IndexRow]] = {}
    for r in all_rows:
        by_table.setdefault(f"{r.schema}.{r.table}", []).append(r)
    for idx_list in by_table.values():
        sorted_idx = sorted(idx_list, key=lambda r: len(r.columns))
        for i, shorter in enumerate(sorted_idx):
            if shorter.is_primary or shorter.is_unique or not shorter.columns:
                continue
            for longer in sorted_idx[i + 1 :]:
                if shorter.index == longer.index:
                    continue
                if _is_prefix_of(shorter.columns, longer.columns):
                    section.duplicates.append(
                        DuplicateIndexPair(
                            schema=shorter.schema,
                            table=shorter.table,
                            redundant_index=shorter.index,
                            covered_by=longer.index,
                            redundant_columns=shorter.columns,
                            covering_columns=longer.columns,
                            wasted_bytes=shorter.size_bytes,
                        )
                    )
                    break
    return section

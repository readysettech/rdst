"""§3 Vacuum & Bloat Health — dead tuples, last vacuum/analyze, TXID age, workers.

PostgreSQL: pg_stat_user_tables, pg_database.age(datfrozenxid), pg_stat_activity
(for active autovacuum workers), pg_settings for autovacuum + max_workers.

MySQL has no vacuum: InnoDB purges old row versions itself. Its equivalents are the free
space a rebuild returns (information_schema.TABLES.DATA_FREE), rows changed since the last
statistics update (INNODB_TABLESTATS.MODIFIED_COUNTER) and the purge backlog
(INNODB_METRICS trx_rseg_history_len). The autovacuum fields remain None.
"""

from __future__ import annotations

from typing import Any, List, Optional

from features.audit.health.models import TableHealth, VacuumBloatSection


_PG_TABLE_SQL = """
SELECT
    schemaname,
    relname,
    -- n_live_tup counts from the last statistics reset (or reads 0 before the first
    -- analyze), so the planner's estimate stands in whenever it's the larger.
    GREATEST(COALESCE(n_live_tup, 0),
             (SELECT reltuples FROM pg_class c WHERE c.oid = relid), 0)::bigint AS live_tup,
    COALESCE(n_dead_tup, 0) AS dead_tup,
    last_vacuum,
    last_autovacuum,
    last_analyze,
    last_autoanalyze,
    COALESCE(n_mod_since_analyze, 0) AS mod_since_analyze,
    pg_total_relation_size(schemaname || '.' || relname) AS size_bytes
FROM pg_stat_user_tables
WHERE schemaname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
ORDER BY COALESCE(n_dead_tup, 0) DESC, COALESCE(n_live_tup, 0) DESC
LIMIT 50;
"""

_PG_TXID_SQL = """
SELECT MAX(age(datfrozenxid)) AS max_age
FROM pg_database
WHERE datallowconn;
"""

_PG_AV_WORKERS_SQL = """
SELECT COUNT(*)
FROM pg_stat_activity
WHERE backend_type = 'autovacuum worker';
"""

_PG_AV_SETTINGS_SQL = """
SELECT name, setting
FROM pg_settings
WHERE name IN ('autovacuum', 'autovacuum_max_workers');
"""


def _classify(dead: int, live: int, mod_since: int) -> str:
    total = dead + max(live, 0)
    if total <= 0:
        return "healthy"
    ratio = dead / total
    if ratio >= 0.30:
        return "crit"
    if ratio >= 0.15 or mod_since > max(10_000, live // 5):
        return "warn"
    return "healthy"


def collect_vacuum_bloat(conn: Any, engine: str) -> Optional[VacuumBloatSection]:
    engine = (engine or "").lower()
    if engine == "postgresql":
        return _collect_pg(conn)
    if engine == "mysql":
        return _collect_mysql(conn)
    return None


def _collect_pg(conn: Any) -> VacuumBloatSection:
    section = VacuumBloatSection()

    with conn.cursor() as cur:
        try:
            cur.execute(_PG_TABLE_SQL)
            tables: List[TableHealth] = []
            for row in cur.fetchall():
                (
                    schema,
                    name,
                    live,
                    dead,
                    lv,
                    lav,
                    la,
                    laa,
                    mod_since,
                    size,
                ) = row
                total = (dead or 0) + max(live or 0, 0)
                ratio = round(((dead or 0) / total) * 100, 1) if total > 0 else 0.0
                tables.append(
                    TableHealth(
                        schema=schema,
                        table=name,
                        live_tuples=int(live or 0),
                        dead_tuples=int(dead or 0),
                        dead_ratio_pct=ratio,
                        last_vacuum=lv.isoformat() if lv else None,
                        last_autovacuum=lav.isoformat() if lav else None,
                        last_analyze=la.isoformat() if la else None,
                        last_autoanalyze=laa.isoformat() if laa else None,
                        n_mod_since_analyze=int(mod_since or 0),
                        size_bytes=int(size) if size is not None else None,
                        status=_classify(int(dead or 0), int(live or 0), int(mod_since or 0)),
                    )
                )
            section.tables = tables
        except Exception:
            pass

        try:
            cur.execute(_PG_TXID_SQL)
            row = cur.fetchone()
            if row and row[0] is not None:
                section.txid_age = int(row[0])
                section.txid_age_pct = round(
                    (section.txid_age / section.txid_wraparound_limit) * 100, 2
                )
        except Exception:
            pass

        try:
            cur.execute(_PG_AV_WORKERS_SQL)
            row = cur.fetchone()
            if row:
                section.autovacuum_workers_active = int(row[0] or 0)
        except Exception:
            pass

        try:
            cur.execute(_PG_AV_SETTINGS_SQL)
            for name, setting in cur.fetchall():
                if name == "autovacuum":
                    section.autovacuum_enabled = setting == "on"
                elif name == "autovacuum_max_workers":
                    try:
                        section.autovacuum_max_workers = int(setting)
                    except (TypeError, ValueError):
                        pass
        except Exception:
            pass

    # Summary
    crit = sum(1 for t in section.tables if t.status == "crit")
    warn = sum(1 for t in section.tables if t.status == "warn")
    if section.txid_age_pct and section.txid_age_pct > 50:
        section.summary_status = "crit"
    elif crit > 0 or (section.txid_age_pct and section.txid_age_pct > 25):
        section.summary_status = "crit"
    elif warn > 0:
        section.summary_status = "warn"
    else:
        section.summary_status = "healthy"

    return section


_MYSQL_TABLE_SQL = """
SELECT t.TABLE_SCHEMA, t.TABLE_NAME, COALESCE(t.TABLE_ROWS, 0), COALESCE(t.DATA_FREE, 0),
       COALESCE(t.DATA_LENGTH, 0) + COALESCE(t.INDEX_LENGTH, 0)
FROM information_schema.TABLES t
WHERE t.TABLE_SCHEMA NOT IN ('mysql','information_schema','performance_schema','sys')
  AND t.TABLE_TYPE = 'BASE TABLE'
  AND (DATABASE() IS NULL OR t.TABLE_SCHEMA = DATABASE())
ORDER BY COALESCE(t.DATA_FREE, 0) DESC, COALESCE(t.TABLE_ROWS, 0) DESC
LIMIT 50;
"""
# Rows changed since InnoDB last recalculated the table's statistics (NAME is schema/table).
_MYSQL_TABLESTATS_SQL = """
SELECT NAME, NUM_ROWS, MODIFIED_COUNTER FROM information_schema.INNODB_TABLESTATS;
"""
_MYSQL_STATS_UPDATED_SQL = """
SELECT database_name, table_name, last_update FROM mysql.innodb_table_stats;
"""
_MYSQL_HISTORY_SQL = """
SELECT COUNT FROM information_schema.INNODB_METRICS
WHERE NAME = 'trx_rseg_history_len' AND STATUS = 'enabled';
"""
MYSQL_FREE_WARN_BYTES = 64 * 1024 * 1024
MYSQL_FREE_CRIT_BYTES = 1024 * 1024 * 1024


def _classify_mysql(free: int, size: int, rows: int, modified: int) -> str:
    """Free space worth a rebuild, or statistics as stale as PostgreSQL's warn line."""
    ratio = free / (free + size) if free + size > 0 else 0.0
    if free >= MYSQL_FREE_CRIT_BYTES and ratio >= 0.5:
        return "crit"
    if (free >= MYSQL_FREE_WARN_BYTES and ratio >= 0.2) or modified > max(10_000, rows // 5):
        return "warn"
    return "healthy"


def _collect_mysql(conn: Any) -> VacuumBloatSection:
    section = VacuumBloatSection()
    with conn.cursor() as cur:
        cur.execute(_MYSQL_TABLE_SQL)
        rows = cur.fetchall()
        modified: dict[tuple[str, str], tuple[int, int]] = {}
        try:
            cur.execute(_MYSQL_TABLESTATS_SQL)
            for name, num_rows, counter in cur.fetchall():
                schema, _, table = str(name or "").partition("/")
                modified[(schema, table)] = (int(num_rows or 0), int(counter or 0))
        except Exception:
            pass
        updated: dict[tuple[str, str], Any] = {}
        try:
            cur.execute(_MYSQL_STATS_UPDATED_SQL)
            updated = {(schema, table): last for schema, table, last in cur.fetchall()}
        except Exception:
            pass
        try:
            cur.execute(_MYSQL_HISTORY_SQL)
            row = cur.fetchone()
            if row and row[0] is not None:
                section.history_list_length = int(row[0])
        except Exception:
            pass

    for schema, name, table_rows, data_free, size in rows:
        free, used = int(data_free or 0), int(size or 0)
        live, changed = modified.get((schema, name), (int(table_rows or 0), None))
        analyzed = updated.get((schema, name))
        section.tables.append(
            TableHealth(
                schema=schema,
                table=name,
                live_tuples=int(live or table_rows or 0),
                dead_tuples=0,
                dead_ratio_pct=round(free / (free + used) * 100, 1) if free + used else 0.0,
                last_autoanalyze=analyzed.isoformat() if hasattr(analyzed, "isoformat") else None,
                n_mod_since_analyze=changed,
                size_bytes=used,
                free_bytes=free,
                status=_classify_mysql(free, used, int(live or 0), int(changed or 0)),
            )
        )
    crit = any(table.status == "crit" for table in section.tables)
    warn = any(table.status == "warn" for table in section.tables)
    section.summary_status = "crit" if crit else "warn" if warn else "healthy"
    return section

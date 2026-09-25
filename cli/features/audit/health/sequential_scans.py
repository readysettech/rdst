"""Cumulative full-table scan counters for nightly comparisons."""

from typing import Any

# Small tables never qualify, and the largest ones are what matters, so the read stays bounded.
MIN_TRACKED_ROWS = 10_000
MAX_TRACKED_TABLES = 500

PG_SQL = f"""
SELECT schemaname, relname, seq_scan, seq_tup_read, n_live_tup
FROM pg_stat_user_tables
WHERE n_live_tup >= {MIN_TRACKED_ROWS}
ORDER BY n_live_tup DESC
LIMIT {MAX_TRACKED_TABLES}
"""
# MySQL counts rows read without an index, not scans; scans are estimated from table size.
MYSQL_SQL = f"""
SELECT s.OBJECT_SCHEMA, s.OBJECT_NAME, s.COUNT_READ, t.TABLE_ROWS
FROM performance_schema.table_io_waits_summary_by_index_usage s
JOIN information_schema.TABLES t
  ON t.TABLE_SCHEMA = s.OBJECT_SCHEMA AND t.TABLE_NAME = s.OBJECT_NAME
WHERE s.INDEX_NAME IS NULL
  AND s.OBJECT_SCHEMA NOT IN ('mysql', 'sys', 'performance_schema', 'information_schema')
  AND t.TABLE_ROWS >= {MIN_TRACKED_ROWS}
ORDER BY t.TABLE_ROWS DESC
LIMIT {MAX_TRACKED_TABLES}
"""


def collect_sequential_scans(conn: Any, engine: str) -> dict[str, Any]:
    cursor = conn.cursor()
    try:
        cursor.execute(MYSQL_SQL if engine == "mysql" else PG_SQL)
        tables = []
        for row in cursor.fetchall():
            if engine == "mysql":
                schema, table, rows_read, rows = row[:4]
                tables.append({"schema": schema, "table": table, "scans": None,
                               "rows": int(rows or 0), "tuples_read": int(rows_read or 0)})
            else:
                schema, table, scans, rows_read, rows = row[:5]
                tables.append({"schema": schema, "table": table, "scans": int(scans or 0),
                               "rows": int(rows or 0), "tuples_read": int(rows_read or 0)})
        return {"tables": tables}
    finally:
        cursor.close()


def scan_deltas(current: dict[str, Any], prior: dict[str, Any] | None) -> dict[str, Any]:
    """A reset or a first observation needs a fresh baseline for that table."""
    before = {(row["schema"], row["table"]): row for row in (prior or {}).get("tables", [])}
    tables, unchecked = [], []
    for row in current.get("tables", []):
        old = before.get((row["schema"], row["table"]))
        if old is None or (row["scans"] or 0) < (old.get("scans") or 0) or (
                row.get("tuples_read") is not None and old.get("tuples_read") is not None and
                row["tuples_read"] < old["tuples_read"]):
            unchecked.append(f"{row['schema']}.{row['table']}")
            continue
        if row["scans"] is None:
            scans_day = (row["tuples_read"] - (old.get("tuples_read") or 0)) // max(row["rows"], 1)
        else:
            scans_day = row["scans"] - old["scans"]
        tables.append({**row, "scans_day": scans_day})
    return {**current, "deltas": tables, "unchecked_tables": unchecked,
            "compared": prior is not None}

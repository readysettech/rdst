"""Table, index and setting statistics read alongside the index list each loop cycle.

Every read goes to the engine's statistics views, never to user tables. The counters are
cumulative since the engine last reset them, so one reading already carries long-run totals and
two readings give the activity in between.
"""

from __future__ import annotations

import time
from typing import Any

from features.monitor.indexes import IndexesBusy

MAX_TABLES = 2000
MAX_INDEXES = 5000

# The settings a setting action can name; a change to one closes or updates that action.
PG_SETTINGS = sorted({"shared_buffers", "work_mem", "maintenance_work_mem", "effective_cache_size",
                      "random_page_cost", "log_min_duration_statement", "max_connections",
                      "autovacuum", "autovacuum_max_workers", "idle_in_transaction_session_timeout",
                      "statement_timeout", "default_statistics_target"})
MYSQL_SETTINGS = sorted({"innodb_buffer_pool_size", "sort_buffer_size", "tmp_table_size",
                         "max_heap_table_size", "innodb_redo_log_capacity", "innodb_log_file_size",
                         "innodb_io_capacity", "long_query_time", "slow_query_log",
                         "innodb_stats_auto_recalc", "max_connections", "join_buffer_size",
                         "wait_timeout"})

PG_META_SQL = ("/*rdst:observe*/ SELECT current_setting('server_version_num')::int, "
               "extract(epoch FROM pg_postmaster_start_time())::bigint, "
               "(SELECT extract(epoch FROM stats_reset)::bigint FROM pg_stat_database "
               "WHERE datname = current_database())")
PG_TABLES_SQL = (f"/*rdst:observe*/ SELECT schemaname, relname, n_tup_ins, n_tup_upd, n_tup_del, "
                 "n_tup_hot_upd, seq_scan, COALESCE(idx_scan, 0), seq_tup_read, "
                 # n_live_tup restarts at a statistics reset; the planner's estimate doesn't.
                 "GREATEST(n_live_tup, (SELECT reltuples FROM pg_class c WHERE c.oid = relid), "
                 "0)::bigint, n_dead_tup, pg_total_relation_size(relid), "
                 "extract(epoch FROM GREATEST(last_vacuum, last_autovacuum))::bigint, "
                 "extract(epoch FROM GREATEST(last_analyze, last_autoanalyze))::bigint "
                 "FROM pg_stat_user_tables ORDER BY n_tup_ins + n_tup_upd + n_tup_del + seq_scan + "
                 f"COALESCE(idx_scan, 0) DESC LIMIT {MAX_TABLES}")
# last_idx_scan arrived in PostgreSQL 16.
PG_INDEXES_SQL = ("/*rdst:observe*/ SELECT s.schemaname, s.relname, s.indexrelname, s.idx_scan, "
                  "pg_relation_size(s.indexrelid), i.indisunique, i.indisprimary, {last}, "
                  "i.indisvalid "
                  "FROM pg_stat_user_indexes s JOIN pg_index i ON i.indexrelid = s.indexrelid "
                  f"LIMIT {MAX_INDEXES}")
PG_SETTINGS_SQL = ("/*rdst:observe*/ SELECT name, setting, unit, pending_restart FROM pg_settings "
                   "WHERE name = ANY(%s)")

MYSQL_META_SQL = ("/*rdst:observe*/ SELECT VARIABLE_VALUE FROM performance_schema.global_status "
                  "WHERE VARIABLE_NAME = 'Uptime'")
MYSQL_TABLES_SQL = ("/*rdst:observe*/ SELECT t.OBJECT_SCHEMA, t.OBJECT_NAME, t.COUNT_INSERT, "
                    "t.COUNT_UPDATE, t.COUNT_DELETE, i.TABLE_ROWS, "
                    "i.DATA_LENGTH + i.INDEX_LENGTH "
                    "FROM performance_schema.table_io_waits_summary_by_table t "
                    "LEFT JOIN information_schema.TABLES i ON i.TABLE_SCHEMA = t.OBJECT_SCHEMA "
                    "AND i.TABLE_NAME = t.OBJECT_NAME "
                    "WHERE t.OBJECT_TYPE = 'TABLE' AND t.OBJECT_SCHEMA = DATABASE() "
                    "ORDER BY t.COUNT_INSERT + t.COUNT_UPDATE + t.COUNT_DELETE + t.COUNT_FETCH DESC "
                    f"LIMIT {MAX_TABLES}")
# Rows fetched by full scans show under no index name, and rows read through an index
# under that index. COUNT_STAR would also count the writes that maintain the index.
MYSQL_SCANS_SQL = ("/*rdst:observe*/ SELECT OBJECT_SCHEMA, OBJECT_NAME, INDEX_NAME, COUNT_FETCH "
                   "FROM performance_schema.table_io_waits_summary_by_index_usage "
                   f"WHERE OBJECT_SCHEMA = DATABASE() LIMIT {MAX_INDEXES + MAX_TABLES}")
MYSQL_UNIQUE_SQL = ("/*rdst:observe*/ SELECT TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, MIN(NON_UNIQUE) "
                    "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
                    "GROUP BY TABLE_SCHEMA, TABLE_NAME, INDEX_NAME")
MYSQL_SIZES_SQL = ("/*rdst:observe*/ SELECT table_name, index_name, stat_value * @@innodb_page_size "
                   "FROM mysql.innodb_index_stats WHERE stat_name = 'size' "
                   "AND database_name = DATABASE()")
MYSQL_SETTINGS_SQL = ("/*rdst:observe*/ SELECT VARIABLE_NAME, VARIABLE_VALUE "
                      "FROM performance_schema.global_variables WHERE VARIABLE_NAME IN ({})")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def read_stats(connection: Any, engine: str, now: float | None = None) -> dict[str, Any]:
    """One reading: {counter_since, version, tables, indexes, settings}."""
    now = int(now if now is not None else time.time())
    cursor = connection.cursor()
    try:
        if engine == "postgresql":
            return _read_postgres(cursor, now)
        return _read_mysql(cursor, now)
    finally:
        cursor.close()


def _read_postgres(cursor: Any, now: int) -> dict[str, Any]:
    # Relation sizes wait behind an ACCESS EXCLUSIVE lock, like the index list does.
    cursor.execute("BEGIN")
    try:
        cursor.execute("SET LOCAL lock_timeout = '1s'")
        cursor.execute(PG_META_SQL)
        version, started, reset = cursor.fetchone()
        cursor.execute(PG_TABLES_SQL)
        tables = [{"schema": row[0], "table": row[1], "ins": _int(row[2]), "upd": _int(row[3]),
                   "del": _int(row[4]), "hot_upd": _int(row[5]), "seq_scan": _int(row[6]),
                   "idx_scan": _int(row[7]), "scan_rows": _int(row[8]), "live_rows": _int(row[9]),
                   "dead_rows": _int(row[10]), "size_bytes": _int(row[11]),
                   "vacuumed_at": row[12], "analyzed_at": row[13]}
                  for row in cursor.fetchall()]
        last = ("CASE WHEN s.last_idx_scan IS NULL THEN NULL "
                "ELSE extract(epoch FROM s.last_idx_scan)::bigint END"
                if _int(version) >= 160000 else "NULL")
        cursor.execute(PG_INDEXES_SQL.format(last=last))
        indexes = [{"schema": row[0], "table": row[1], "index_name": row[2], "scans": _int(row[3]),
                    "size_bytes": _int(row[4]), "unique": bool(row[5]), "primary": bool(row[6]),
                    "last_scan_at": _int(row[7]) or None, "valid": bool(row[8])}
                   for row in cursor.fetchall()]
        cursor.execute(PG_SETTINGS_SQL, (PG_SETTINGS,))
        settings = {row[0]: {"value": str(row[1]), "unit": row[2], "pending_restart": bool(row[3])}
                    for row in cursor.fetchall()}
        cursor.execute("COMMIT")
    except Exception as exc:
        cursor.execute("ROLLBACK")
        if getattr(exc, "pgcode", None) == "55P03":
            raise IndexesBusy(str(exc)) from exc
        raise
    # Counters survive a clean restart, so the reset time is their age when there is one.
    # The server's start is a conservative stand-in when stats were never reset.
    since = _int(reset) or _int(started) or now
    return {"engine": "postgresql", "version": _int(version), "counter_since": since,
            "taken_at": now, "tables": tables, "indexes": indexes, "settings": settings}


def _read_mysql(cursor: Any, now: int) -> dict[str, Any]:
    cursor.execute(MYSQL_META_SQL)
    row = cursor.fetchone()
    uptime = _int(row[0]) if row else 0
    cursor.execute(MYSQL_TABLES_SQL)
    tables = {}
    for schema, table, ins, upd, dele, rows, size in cursor.fetchall():
        # MySQL has no HOT updates, scan count or dead-row counter; its scans arrive as rows.
        tables[(schema, table)] = {
            "schema": schema, "table": table, "ins": _int(ins), "upd": _int(upd),
            "del": _int(dele), "hot_upd": 0, "seq_scan": 0, "idx_scan": 0, "scan_rows": 0,
            "live_rows": _int(rows), "dead_rows": 0, "size_bytes": _int(size)}
    cursor.execute(MYSQL_UNIQUE_SQL)
    unique = {(schema, table, name): not _int(non_unique)
              for schema, table, name, non_unique in cursor.fetchall()}
    sizes: dict[tuple[str, str], int] = {}
    try:
        cursor.execute(MYSQL_SIZES_SQL)
        sizes = {(table, name): _int(size) for table, name, size in cursor.fetchall()}
    except Exception:
        # mysql.innodb_index_stats needs its own grant; sizes are optional.
        pass
    cursor.execute(MYSQL_SCANS_SQL)
    indexes = []
    for schema, table, name, count in cursor.fetchall():
        entry = tables.get((schema, table))
        if name is None:
            if entry is not None:
                entry["scan_rows"] = _int(count)
            continue
        if entry is not None:
            entry["idx_scan"] += _int(count)
        indexes.append({"schema": schema, "table": table, "index_name": name,
                        "scans": _int(count), "size_bytes": sizes.get((table, name), 0),
                        "unique": unique.get((schema, table, name), name == "PRIMARY"),
                        "primary": name == "PRIMARY", "last_scan_at": None})
    placeholders = ",".join(["%s"] * len(MYSQL_SETTINGS))
    cursor.execute(MYSQL_SETTINGS_SQL.format(placeholders), tuple(MYSQL_SETTINGS))
    settings = {str(name).lower(): {"value": str(value), "unit": None, "pending_restart": False}
                for name, value in cursor.fetchall()}
    return {"engine": "mysql", "version": 0, "counter_since": now - uptime if uptime else now,
            "taken_at": now, "tables": list(tables.values()), "indexes": indexes,
            "settings": settings}

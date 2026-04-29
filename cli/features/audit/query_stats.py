"""Workload statistics collection — database snapshot SQL for PostgreSQL and MySQL."""

import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

from .models import DatabaseSnapshot, IntermediateSnapshot, TableStats

logger = logging.getLogger(__name__)


def collect_database_snapshot(connection, db_engine: str) -> DatabaseSnapshot:
    """Collect a full point-in-time database snapshot."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if db_engine == "postgresql":
        return _collect_pg_snapshot(connection, now)
    else:
        return _collect_mysql_snapshot(connection, now)


def collect_intermediate_snapshot(
    connection, db_engine: str, elapsed_seconds: float, prev_snapshot: Optional[DatabaseSnapshot] = None
) -> IntermediateSnapshot:
    """Collect a lightweight intermediate snapshot (every 30s during capture)."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if db_engine == "postgresql":
        return _collect_pg_intermediate(connection, now, elapsed_seconds, prev_snapshot)
    else:
        return _collect_mysql_intermediate(connection, now, elapsed_seconds, prev_snapshot)


def collect_table_stats(connection, db_engine: str) -> List[TableStats]:
    """Collect per-table statistics."""
    if db_engine == "postgresql":
        return _collect_pg_table_stats(connection)
    else:
        return _collect_mysql_table_stats(connection)


def collect_pg_stat_statements(connection) -> List[Dict[str, Any]]:
    """Collect all rows from pg_stat_statements for the current database."""
    try:
        cur = connection.cursor()
        cur.execute(
            "SELECT queryid, query, calls, total_exec_time, mean_exec_time, "
            "max_exec_time, rows, shared_blks_hit, shared_blks_read "
            "FROM pg_stat_statements "
            "WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database()) "
            "ORDER BY total_exec_time DESC LIMIT 500"
        )
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        cur.close()
        return rows
    except Exception as e:
        logger.debug("pg_stat_statements not available: %s", e)
        return []


def collect_mysql_digest_stats(connection) -> List[Dict[str, Any]]:
    """Collect all rows from performance_schema.events_statements_summary_by_digest."""
    try:
        cur = connection.cursor()
        cur.execute(
            "SELECT DIGEST, DIGEST_TEXT, COUNT_STAR, "
            "SUM_TIMER_WAIT / 1000000000 as total_time_ms, "
            "AVG_TIMER_WAIT / 1000000000 as avg_time_ms, "
            "MAX_TIMER_WAIT / 1000000000 as max_time_ms, "
            "SUM_ROWS_SENT as rows_sent "
            "FROM performance_schema.events_statements_summary_by_digest "
            "WHERE SCHEMA_NAME = DATABASE() "
            "ORDER BY SUM_TIMER_WAIT DESC LIMIT 500"
        )
        rows = cur.fetchall()
        # pymysql DictCursor returns dicts already; handle both cases
        if rows and isinstance(rows[0], dict):
            cur.close()
            return list(rows)
        columns = [desc[0] for desc in cur.description]
        cur.close()
        return [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        logger.debug("performance_schema not available: %s", e)
        return []


def compute_snapshot_delta(start: DatabaseSnapshot, end: DatabaseSnapshot) -> Dict[str, Any]:
    """Compute the difference between two full snapshots."""
    delta = {}

    # Integer delta fields
    int_fields = [
        "xact_commit", "xact_rollback", "blks_read", "blks_hit",
        "tup_returned", "tup_fetched", "tup_inserted", "tup_updated", "tup_deleted",
        "temp_files", "temp_bytes", "deadlocks",
        "com_select", "com_insert", "com_update", "com_delete",
        "innodb_buffer_pool_reads", "innodb_buffer_pool_read_requests",
        "innodb_row_lock_waits",
    ]

    for field in int_fields:
        start_val = getattr(start, field, None)
        end_val = getattr(end, field, None)
        if start_val is not None and end_val is not None:
            delta[field] = end_val - start_val

    # Derived metrics
    if "blks_hit" in delta and "blks_read" in delta:
        total = delta["blks_hit"] + delta["blks_read"]
        delta["cache_hit_ratio_delta"] = round((delta["blks_hit"] / total) * 100, 2) if total > 0 else 0

    reads = delta.get("tup_returned", 0) + delta.get("tup_fetched", 0)
    writes = delta.get("tup_inserted", 0) + delta.get("tup_updated", 0) + delta.get("tup_deleted", 0)
    total_ops = reads + writes
    if total_ops > 0:
        delta["read_pct"] = round((reads / total_ops) * 100, 1)
        delta["write_pct"] = round((writes / total_ops) * 100, 1)

    # MySQL read/write
    mysql_reads = delta.get("com_select", 0)
    mysql_writes = delta.get("com_insert", 0) + delta.get("com_update", 0) + delta.get("com_delete", 0)
    if mysql_reads + mysql_writes > 0:
        delta["read_pct"] = round((mysql_reads / (mysql_reads + mysql_writes)) * 100, 1)
        delta["write_pct"] = round((mysql_writes / (mysql_reads + mysql_writes)) * 100, 1)

    return delta


# ============================================================================
# PostgreSQL implementations
# ============================================================================

def _collect_pg_snapshot(connection, timestamp: str) -> DatabaseSnapshot:
    snap = DatabaseSnapshot(timestamp=timestamp, engine="postgresql")
    cur = connection.cursor()

    try:
        cur.execute(
            "SELECT xact_commit, xact_rollback, blks_read, blks_hit, "
            "tup_returned, tup_fetched, tup_inserted, tup_updated, tup_deleted, "
            "temp_files, temp_bytes, deadlocks "
            "FROM pg_stat_database WHERE datname = current_database()"
        )
        row = cur.fetchone()
        if row:
            (snap.xact_commit, snap.xact_rollback, snap.blks_read, snap.blks_hit,
             snap.tup_returned, snap.tup_fetched, snap.tup_inserted, snap.tup_updated,
             snap.tup_deleted, snap.temp_files, snap.temp_bytes, snap.deadlocks) = [
                int(v) if v is not None else 0 for v in row
            ]
            total = (snap.blks_hit or 0) + (snap.blks_read or 0)
            snap.cache_hit_ratio = round(((snap.blks_hit or 0) / total) * 100, 2) if total > 0 else 0
    except Exception as e:
        logger.debug("pg_stat_database error: %s", e)

    try:
        cur.execute(
            "SELECT state, count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() GROUP BY state"
        )
        for row in cur.fetchall():
            state, count = row[0], int(row[1])
            if state == "active":
                snap.active_connections = count
            elif state == "idle":
                snap.idle_connections = count
            elif state == "idle in transaction":
                snap.idle_in_transaction = count
    except Exception:
        pass

    try:
        cur.execute("SELECT pg_database_size(current_database())")
        row = cur.fetchone()
        if row and row[0]:
            snap.database_size_mb = round(float(row[0]) / (1024 * 1024), 1)
    except Exception:
        pass

    try:
        cur.execute("SELECT version()")
        row = cur.fetchone()
        if row:
            snap.server_version = str(row[0]).split("\n")[0][:80]
    except Exception:
        pass

    cur.close()
    return snap


def _collect_pg_intermediate(connection, timestamp: str, elapsed: float, prev: Optional[DatabaseSnapshot]) -> IntermediateSnapshot:
    inter = IntermediateSnapshot(timestamp=timestamp, elapsed_seconds=elapsed)
    cur = connection.cursor()

    # pg_stat_activity — state distribution + active query details
    try:
        cur.execute(
            "SELECT state, count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() GROUP BY state"
        )
        state_dist = {}
        for row in cur.fetchall():
            state, count = row[0] or "unknown", int(row[1])
            state_dist[state] = count
            if state == "active":
                inter.active_connections = count
            elif state == "idle":
                inter.idle_connections = count
            elif state == "idle in transaction":
                inter.idle_in_transaction = count
        inter.state_distribution = state_dist
    except Exception:
        pass

    # Active query sampling — longest running, lock waits
    try:
        cur.execute(
            "SELECT EXTRACT(EPOCH FROM (now() - query_start)) * 1000 as duration_ms, "
            "wait_event_type "
            "FROM pg_stat_activity "
            "WHERE datname = current_database() AND state = 'active' "
            "AND pid != pg_backend_pid() "
            "ORDER BY query_start ASC"
        )
        active_count = 0
        longest_ms = 0.0
        lock_waits = 0
        for row in cur.fetchall():
            active_count += 1
            dur = float(row[0] or 0)
            if dur > longest_ms:
                longest_ms = dur
            if row[1] and row[1] in ("Lock", "LWLock"):
                lock_waits += 1
        inter.active_queries = active_count
        inter.longest_query_ms = round(longest_ms, 1)
        inter.waiting_on_lock = lock_waits
    except Exception:
        pass

    # Cache hit ratio
    try:
        cur.execute(
            "SELECT sum(blks_hit), sum(blks_read) "
            "FROM pg_stat_database WHERE datname = current_database()"
        )
        row = cur.fetchone()
        if row and row[0]:
            hits, reads = float(row[0]), float(row[1] or 0)
            total = hits + reads
            inter.cache_hit_ratio = round((hits / total) * 100, 2) if total > 0 else 0
    except Exception:
        pass

    # TPS, temp bytes, deadlocks deltas
    try:
        cur.execute(
            "SELECT xact_commit, temp_bytes, deadlocks "
            "FROM pg_stat_database WHERE datname = current_database()"
        )
        row = cur.fetchone()
        if row and prev:
            commits = int(row[0] or 0)
            prev_commits = prev.xact_commit or 0
            if elapsed > 0 and commits >= prev_commits:
                inter.transactions_per_sec = round((commits - prev_commits) / max(elapsed, 1), 1)
            inter.temp_bytes_delta = int(row[1] or 0) - (prev.temp_bytes or 0)
            inter.deadlock_count_delta = int(row[2] or 0) - (prev.deadlocks or 0)
    except Exception:
        pass

    cur.close()
    return inter


def _collect_pg_table_stats(connection) -> List[TableStats]:
    try:
        cur = connection.cursor()
        cur.execute(
            "SELECT schemaname, relname, seq_scan, idx_scan, "
            "n_tup_ins, n_tup_upd, n_tup_del, n_live_tup, n_dead_tup "
            "FROM pg_stat_user_tables "
            "ORDER BY (seq_scan + idx_scan) DESC LIMIT 100"
        )
        stats = []
        for row in cur.fetchall():
            stats.append(TableStats(
                schema_name=row[0],
                table_name=row[1],
                seq_scan=int(row[2] or 0),
                idx_scan=int(row[3] or 0),
                n_tup_ins=int(row[4] or 0),
                n_tup_upd=int(row[5] or 0),
                n_tup_del=int(row[6] or 0),
                n_live_tup=int(row[7] or 0),
                n_dead_tup=int(row[8] or 0),
            ))
        cur.close()
        return stats
    except Exception:
        return []


# ============================================================================
# MySQL implementations
# ============================================================================

def _collect_mysql_snapshot(connection, timestamp: str) -> DatabaseSnapshot:
    snap = DatabaseSnapshot(timestamp=timestamp, engine="mysql")
    cur = connection.cursor()

    def _val(row):
        if isinstance(row, dict):
            return list(row.values())
        return row

    try:
        cur.execute(
            "SELECT VARIABLE_NAME, VARIABLE_VALUE "
            "FROM performance_schema.global_status "
            "WHERE VARIABLE_NAME IN ("
            "'Com_select', 'Com_insert', 'Com_update', 'Com_delete', "
            "'Innodb_buffer_pool_reads', 'Innodb_buffer_pool_read_requests', "
            "'Innodb_row_lock_waits', 'Threads_connected', 'Threads_running')"
        )
        stats = {}
        for row in cur.fetchall():
            v = _val(row)
            stats[v[0]] = int(v[1])

        snap.com_select = stats.get("Com_select", 0)
        snap.com_insert = stats.get("Com_insert", 0)
        snap.com_update = stats.get("Com_update", 0)
        snap.com_delete = stats.get("Com_delete", 0)
        snap.innodb_buffer_pool_reads = stats.get("Innodb_buffer_pool_reads", 0)
        snap.innodb_buffer_pool_read_requests = stats.get("Innodb_buffer_pool_read_requests", 0)
        snap.innodb_row_lock_waits = stats.get("Innodb_row_lock_waits", 0)
        snap.threads_connected = stats.get("Threads_connected", 0)
        snap.threads_running = stats.get("Threads_running", 0)
        snap.active_connections = snap.threads_running
        snap.idle_connections = (snap.threads_connected or 0) - (snap.threads_running or 0)

        # Cache hit ratio
        requests = snap.innodb_buffer_pool_read_requests or 0
        reads = snap.innodb_buffer_pool_reads or 0
        if requests > 0:
            snap.cache_hit_ratio = round(((requests - reads) / requests) * 100, 2)
    except Exception as e:
        logger.debug("MySQL global_status error: %s", e)

    try:
        cur.execute("SELECT VERSION()")
        row = cur.fetchone()
        v = _val(row)
        if v:
            snap.server_version = str(v[0])[:80]
    except Exception:
        pass

    # Sum across all non-system schemas the user can see (privilege-filtered).
    try:
        cur.execute(
            "SELECT SUM(DATA_LENGTH + INDEX_LENGTH) "
            "FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA NOT IN ('mysql','information_schema',"
            "'performance_schema','sys')"
        )
        row = cur.fetchone()
        v = _val(row)
        if v and v[0]:
            snap.database_size_mb = round(float(v[0]) / (1024 * 1024), 1)
    except Exception:
        pass

    cur.close()
    return snap


def _collect_mysql_intermediate(connection, timestamp: str, elapsed: float, prev: Optional[DatabaseSnapshot]) -> IntermediateSnapshot:
    inter = IntermediateSnapshot(timestamp=timestamp, elapsed_seconds=elapsed)
    cur = connection.cursor()

    def _val(row):
        if isinstance(row, dict):
            return list(row.values())
        return row

    # Global status metrics
    try:
        cur.execute(
            "SELECT VARIABLE_NAME, VARIABLE_VALUE "
            "FROM performance_schema.global_status "
            "WHERE VARIABLE_NAME IN ('Threads_connected', 'Threads_running', "
            "'Innodb_buffer_pool_reads', 'Innodb_buffer_pool_read_requests', "
            "'Com_select', 'Innodb_row_lock_current_waits')"
        )
        stats = {}
        for row in cur.fetchall():
            v = _val(row)
            stats[v[0]] = int(v[1])

        inter.active_connections = stats.get("Threads_running", 0)
        inter.idle_connections = stats.get("Threads_connected", 0) - inter.active_connections
        inter.lock_wait_count = stats.get("Innodb_row_lock_current_waits", 0)

        requests = stats.get("Innodb_buffer_pool_read_requests", 0)
        reads = stats.get("Innodb_buffer_pool_reads", 0)
        if requests > 0:
            inter.cache_hit_ratio = round(((requests - reads) / requests) * 100, 2)

        if prev and elapsed > 0:
            prev_selects = prev.com_select or 0
            current_selects = stats.get("Com_select", 0)
            inter.transactions_per_sec = round((current_selects - prev_selects) / max(elapsed, 1), 1)
    except Exception:
        pass

    # PROCESSLIST — active query sampling
    try:
        cur.execute(
            "SELECT TIME, STATE FROM INFORMATION_SCHEMA.PROCESSLIST "
            "WHERE COMMAND != 'Sleep' AND COMMAND != 'Daemon' AND INFO IS NOT NULL"
        )
        active_count = 0
        longest_ms = 0.0
        waiting = 0
        for row in cur.fetchall():
            v = _val(row)
            active_count += 1
            dur_ms = float(v[0] or 0) * 1000  # TIME is in seconds
            if dur_ms > longest_ms:
                longest_ms = dur_ms
            state = str(v[1] or "").lower()
            if "lock" in state or "waiting" in state:
                waiting += 1
        inter.active_queries = active_count
        inter.longest_query_ms = round(longest_ms, 1)
        inter.waiting_on_lock = waiting
    except Exception:
        pass

    cur.close()
    return inter


def _collect_mysql_table_stats(connection) -> List[TableStats]:
    try:
        cur = connection.cursor()
        db_name = connection.db if hasattr(connection, 'db') else None
        if db_name and isinstance(db_name, bytes):
            db_name = db_name.decode()
        if not db_name:
            return []
        cur.execute(
            "SELECT TABLE_NAME, TABLE_ROWS "
            "FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
            "ORDER BY TABLE_ROWS DESC LIMIT 100",
            (db_name,),
        )
        stats = []
        for row in cur.fetchall():
            v = list(row.values()) if isinstance(row, dict) else row
            stats.append(TableStats(
                table_name=str(v[0]),
                n_live_tup=int(v[1] or 0),
            ))
        cur.close()
        return stats
    except Exception:
        return []


def _extract_table_names_from_queries(queries: List[Dict[str, Any]]) -> List[str]:
    """Extract unique table names from query texts using simple regex."""
    import re
    tables = set()
    pattern = re.compile(
        r'(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+[`"]?(\w+)[`"]?',
        re.IGNORECASE,
    )
    for q in queries:
        sql = q.get("normalized_query") or q.get("query") or ""
        for match in pattern.finditer(sql):
            name = match.group(1).lower()
            # Skip common keywords that look like table names
            if name not in (
                "select", "where", "set", "values", "null",
                "true", "false", "dual", "information_schema",
            ):
                tables.add(name)
    return sorted(tables)[:15]


def collect_schema_for_tables(
    connection,
    table_names: List[str],
    engine: str,
    query_texts: Optional[List[str]] = None,
) -> str:
    """Collect schema context (columns, indexes, foreign keys) for the given tables.

    Returns a compact formatted string suitable for injection into an LLM prompt.
    On any failure, returns empty string (graceful degradation).
    """
    if not table_names:
        return ""

    # Build set of referenced column names for filtering
    referenced_cols: set = set()
    if query_texts:
        import re
        col_pattern = re.compile(r'[`"]?(\w+)[`"]?\s*(?:=|<|>|IS|IN|LIKE|BETWEEN|ORDER|GROUP|,)', re.IGNORECASE)
        for sql in query_texts:
            for m in col_pattern.finditer(sql):
                referenced_cols.add(m.group(1).lower())

    try:
        cur = connection.cursor()
        sections = []

        for table in table_names:
            columns = []
            indexes = []
            fks = []

            if engine == "postgresql":
                # Columns
                cur.execute(
                    "SELECT column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_name = %s AND table_schema = 'public' "
                    "ORDER BY ordinal_position",
                    (table,),
                )
                for row in cur.fetchall():
                    v = list(row.values()) if isinstance(row, dict) else row
                    col_name = str(v[0])
                    if referenced_cols and col_name.lower() not in referenced_cols:
                        continue
                    dtype = str(v[1]).upper().replace("CHARACTER VARYING", "VARCHAR")
                    nullable = "" if str(v[2]) == "YES" else " NOT NULL"
                    columns.append(f"{col_name} {dtype}{nullable}")

                # Indexes
                cur.execute(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = %s",
                    (table,),
                )
                for row in cur.fetchall():
                    v = list(row.values()) if isinstance(row, dict) else row
                    indexes.append(f"{v[0]}: {v[1]}")

                # Foreign keys
                cur.execute(
                    "SELECT tc.constraint_name, kcu.column_name, "
                    "ccu.table_name AS foreign_table, ccu.column_name AS foreign_column "
                    "FROM information_schema.table_constraints tc "
                    "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name "
                    "JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name = ccu.constraint_name "
                    "WHERE tc.table_name = %s AND tc.constraint_type = 'FOREIGN KEY'",
                    (table,),
                )
                for row in cur.fetchall():
                    v = list(row.values()) if isinstance(row, dict) else row
                    fks.append(f"{v[1]} -> {v[2]}({v[3]})")

            else:
                # MySQL - Columns via DESCRIBE
                try:
                    cur.execute(f"DESCRIBE `{table}`")
                    for row in cur.fetchall():
                        v = list(row.values()) if isinstance(row, dict) else row
                        col_name = str(v[0])
                        if referenced_cols and col_name.lower() not in referenced_cols:
                            continue
                        dtype = str(v[1]).upper()
                        nullable = "" if str(v[2]) == "YES" else " NOT NULL"
                        key = ""
                        if str(v[3]) == "PRI":
                            key = " PK"
                        columns.append(f"{col_name} {dtype}{nullable}{key}")
                except Exception:
                    pass

                # MySQL - Indexes
                try:
                    cur.execute(f"SHOW INDEX FROM `{table}`")
                    idx_map: Dict[str, List[str]] = {}
                    for row in cur.fetchall():
                        v = list(row.values()) if isinstance(row, dict) else row
                        idx_name = str(v[2])
                        col = str(v[4])
                        idx_map.setdefault(idx_name, []).append(col)
                    for idx_name, cols in idx_map.items():
                        indexes.append(f"{idx_name}({', '.join(cols)})")
                except Exception:
                    pass

                # MySQL - Foreign keys
                try:
                    db_name = connection.db if hasattr(connection, 'db') else None
                    if db_name and isinstance(db_name, bytes):
                        db_name = db_name.decode()
                    if db_name:
                        cur.execute(
                            "SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
                            "FROM information_schema.KEY_COLUMN_USAGE "
                            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                            "AND REFERENCED_TABLE_NAME IS NOT NULL",
                            (db_name, table),
                        )
                        for row in cur.fetchall():
                            v = list(row.values()) if isinstance(row, dict) else row
                            fks.append(f"{v[0]} -> {v[1]}({v[2]})")
                except Exception:
                    pass

            # Format section for this table
            if not columns and not indexes:
                continue  # Table not found or no access
            section = f"{table}:"
            if columns:
                section += f"\n  Columns: {', '.join(columns)}"
            if indexes:
                section += f"\n  Indexes: {', '.join(indexes)}"
            if fks:
                section += f"\n  Foreign Keys: {', '.join(fks)}"
            sections.append(section)

        cur.close()

        if not sections:
            return ""

        header = (
            "== SCHEMA CONTEXT ==\n"
            "NOTE: Only showing tables and indexes referenced by captured queries.\n"
            "Use this to verify index recommendations — do NOT recommend indexes that already exist.\n\n"
        )
        return header + "\n\n".join(sections)

    except Exception as e:
        logger.debug("Schema collection failed: %s", e)
        return ""

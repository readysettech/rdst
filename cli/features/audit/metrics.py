"""Audit metrics collection — SQL queries for PostgreSQL and MySQL database health."""

import logging
from typing import Any, Dict, Optional

from shared.db_connection import create_direct_connection

from .models import AuditMetrics

logger = logging.getLogger(__name__)


def collect_metrics(target_config: Dict[str, Any]) -> AuditMetrics:
    """Collect audit metrics from a database target. Raises on connection failure."""
    engine = target_config.get("engine", "postgresql")
    if engine == "postgresql":
        return _collect_postgresql_metrics(target_config)
    else:
        return _collect_mysql_metrics(target_config)


def _collect_postgresql_metrics(target_config: Dict[str, Any]) -> AuditMetrics:
    """Collect metrics from PostgreSQL using pg_stat_* views."""
    import datetime

    metrics = AuditMetrics(
        collected_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )

    try:
        conn = create_direct_connection(target_config)
    except Exception as e:
        raise RuntimeError(f"Failed to connect: {e}") from e

    try:
        cur = conn.cursor()

        # Connection utilization
        try:
            cur.execute("SELECT current_setting('max_connections')::int")
            row = cur.fetchone()
            metrics.max_connections = int(row[0]) if row else 0
        except Exception:
            pass

        try:
            cur.execute(
                "SELECT state, count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() GROUP BY state"
            )
            for row in cur.fetchall():
                state, count = row[0], int(row[1])
                if state == "active":
                    metrics.active_connections = count
                elif state == "idle":
                    metrics.idle_connections = count
            if metrics.max_connections > 0:
                total = metrics.active_connections + metrics.idle_connections
                metrics.connection_utilization_pct = round(
                    (total / metrics.max_connections) * 100, 1
                )
        except Exception:
            pass

        # Cache hit rate
        try:
            cur.execute(
                "SELECT sum(blks_hit), sum(blks_read) "
                "FROM pg_stat_database WHERE datname = current_database()"
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                hits, reads = float(row[0]), float(row[1] or 0)
                total = hits + reads
                metrics.cache_hit_rate = round((hits / total) * 100, 2) if total > 0 else 100.0
        except Exception:
            pass

        # Working set (shared_buffers) — use pg_settings for accurate unit handling
        try:
            cur.execute("SELECT setting, unit FROM pg_settings WHERE name = 'shared_buffers'")
            row = cur.fetchone()
            if row:
                val = float(row[0])
                unit = (row[1] or "8kB").strip()
                if unit == "8kB":
                    metrics.shared_buffers_mb = round(val * 8 / 1024, 1)
                elif unit == "kB":
                    metrics.shared_buffers_mb = round(val / 1024, 1)
                elif unit == "MB":
                    metrics.shared_buffers_mb = round(val, 1)
                elif unit == "GB":
                    metrics.shared_buffers_mb = round(val * 1024, 1)
                else:
                    metrics.shared_buffers_mb = _parse_pg_size(f"{val}{unit}")
        except Exception:
            pass

        # Database size
        try:
            cur.execute("SELECT pg_database_size(current_database())")
            row = cur.fetchone()
            if row and row[0]:
                metrics.database_size_mb = round(float(row[0]) / (1024 * 1024), 1)
        except Exception:
            pass

        # Read/write ratio (from pg_stat_database)
        try:
            cur.execute(
                "SELECT tup_returned + tup_fetched, "
                "tup_inserted + tup_updated + tup_deleted "
                "FROM pg_stat_database WHERE datname = current_database()"
            )
            row = cur.fetchone()
            if row:
                reads, writes = float(row[0] or 0), float(row[1] or 0)
                total = reads + writes
                if total > 0:
                    metrics.read_pct = round((reads / total) * 100, 1)
                    metrics.write_pct = round((writes / total) * 100, 1)
        except Exception:
            pass

        # Replication status
        try:
            cur.execute("SELECT pg_is_in_recovery()")
            row = cur.fetchone()
            metrics.is_replica = bool(row and row[0])
            if metrics.is_replica:
                cur.execute(
                    "SELECT EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp()))"
                )
                row = cur.fetchone()
                if row and row[0] is not None:
                    metrics.replication_lag_seconds = round(float(row[0]), 2)
        except Exception:
            pass

        # Server version
        try:
            cur.execute("SELECT version()")
            row = cur.fetchone()
            if row:
                metrics.server_version = str(row[0]).split("\n")[0][:80]
        except Exception:
            pass

        # Server uptime
        try:
            cur.execute("SELECT EXTRACT(EPOCH FROM (now() - pg_postmaster_start_time()))")
            row = cur.fetchone()
            if row:
                metrics.uptime_seconds = float(row[0])
                metrics.stats_window_seconds = metrics.uptime_seconds
        except Exception:
            pass

        # Query stats from pg_stat_statements (if available)
        try:
            cur.execute(
                "SELECT count(*), coalesce(sum(total_exec_time), 0) "
                "FROM pg_stat_statements WHERE dbid = "
                "(SELECT oid FROM pg_database WHERE datname = current_database())"
            )
            row = cur.fetchone()
            if row:
                metrics.tracked_query_count = int(row[0])
                metrics.total_query_time_ms = float(row[1])
        except Exception:
            pass  # Extension may not be installed

        # pg_stat_statements reset time (PG14+)
        try:
            cur.execute("SELECT stats_reset FROM pg_stat_statements_info")
            row = cur.fetchone()
            if row and row[0]:
                metrics.stats_reset_at = str(row[0])
                cur.execute(
                    "SELECT EXTRACT(EPOCH FROM (now() - stats_reset)) "
                    "FROM pg_stat_statements_info"
                )
                reset_row = cur.fetchone()
                if reset_row and reset_row[0]:
                    metrics.stats_window_seconds = float(reset_row[0])
        except Exception:
            pass  # View not available on older PG versions

        cur.close()
    except Exception as e:
        logger.warning("Error collecting PostgreSQL metrics: %s", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return metrics


def _collect_mysql_metrics(target_config: Dict[str, Any]) -> AuditMetrics:
    """Collect metrics from MySQL using SHOW STATUS and performance_schema."""
    import datetime

    metrics = AuditMetrics(
        collected_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )

    try:
        conn = create_direct_connection(target_config)
    except Exception as e:
        raise RuntimeError(f"Failed to connect: {e}") from e

    try:
        cur = conn.cursor()

        # Helper for dict cursor results
        def _val(row):
            if isinstance(row, dict):
                return list(row.values())
            return row

        # Max connections
        try:
            cur.execute("SELECT @@max_connections")
            row = cur.fetchone()
            v = _val(row)
            metrics.max_connections = int(v[0]) if v else 0
        except Exception:
            pass

        # Current connections (use SHOW STATUS — works with all grant levels)
        try:
            cur.execute("SHOW GLOBAL STATUS LIKE 'Threads_connected'")
            row = cur.fetchone()
            v = _val(row)
            if v and len(v) >= 2:
                metrics.active_connections = int(v[1])
                if metrics.max_connections > 0:
                    metrics.connection_utilization_pct = round(
                        (metrics.active_connections / metrics.max_connections) * 100, 1
                    )
        except Exception:
            pass

        # Buffer pool hit rate (use SHOW STATUS — works with all grant levels)
        try:
            stats = {}
            for var in ("Innodb_buffer_pool_reads", "Innodb_buffer_pool_read_requests"):
                cur.execute(f"SHOW GLOBAL STATUS LIKE '{var}'")
                row = cur.fetchone()
                v = _val(row)
                if v and len(v) >= 2:
                    stats[v[0]] = float(v[1])
            reads = stats.get("Innodb_buffer_pool_reads", 0)
            requests = stats.get("Innodb_buffer_pool_read_requests", 0)
            if requests > 0:
                metrics.cache_hit_rate = round(((requests - reads) / requests) * 100, 2)
        except Exception:
            pass

        # Working set (buffer pool size)
        try:
            cur.execute("SELECT @@innodb_buffer_pool_size")
            row = cur.fetchone()
            v = _val(row)
            if v:
                metrics.shared_buffers_mb = round(float(v[0]) / (1024 * 1024), 1)
        except Exception:
            pass

        # Database size — sum across all non-system schemas the user can see.
        # information_schema.TABLES is privilege-filtered automatically: a tenant
        # user only sees their own schema; an admin/monitoring user sees all.
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
                metrics.database_size_mb = round(float(v[0]) / (1024 * 1024), 1)
        except Exception:
            pass

        # Read/write ratio (use SHOW STATUS — works with all grant levels)
        try:
            stats = {}
            for cmd in ("Com_select", "Com_insert", "Com_update", "Com_delete"):
                cur.execute(f"SHOW GLOBAL STATUS LIKE '{cmd}'")
                row = cur.fetchone()
                v = _val(row)
                if v and len(v) >= 2:
                    stats[v[0]] = float(v[1])
            reads = stats.get("Com_select", 0)
            writes = (
                stats.get("Com_insert", 0)
                + stats.get("Com_update", 0)
                + stats.get("Com_delete", 0)
            )
            total = reads + writes
            if total > 0:
                metrics.read_pct = round((reads / total) * 100, 1)
                metrics.write_pct = round((writes / total) * 100, 1)
        except Exception:
            pass

        # Replication status
        try:
            cur.execute("SHOW REPLICA STATUS")
            row = cur.fetchone()
            if row:
                v = _val(row) if not isinstance(row, dict) else row
                metrics.is_replica = True
                lag = v.get("Seconds_Behind_Source") if isinstance(v, dict) else None
                if lag is None and isinstance(v, dict):
                    lag = v.get("Seconds_Behind_Master")
                if lag is not None:
                    metrics.replication_lag_seconds = float(lag)
        except Exception:
            pass

        # Server version (MySQL VERSION() doesn't include engine name, so prefix it)
        try:
            cur.execute("SELECT VERSION()")
            row = cur.fetchone()
            v = _val(row)
            if v:
                ver = str(v[0])[:80]
                if not ver.lower().startswith("mysql"):
                    ver = f"MySQL {ver}"
                metrics.server_version = ver
        except Exception:
            pass

        # Server uptime (use SHOW STATUS — works with all grant levels)
        try:
            cur.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
            row = cur.fetchone()
            v = _val(row)
            if v and len(v) >= 2:
                metrics.uptime_seconds = float(v[1])
                metrics.stats_window_seconds = metrics.uptime_seconds
        except Exception:
            pass

        # Query stats from performance_schema (filter to current database only)
        try:
            cur.execute(
                "SELECT count(*), coalesce(sum(SUM_TIMER_WAIT), 0) "
                "FROM performance_schema.events_statements_summary_by_digest "
                "WHERE SCHEMA_NAME = DATABASE()"
            )
            row = cur.fetchone()
            v = _val(row)
            if v:
                metrics.tracked_query_count = int(v[0])
                # Convert picoseconds to milliseconds
                metrics.total_query_time_ms = float(v[1]) / 1_000_000_000
        except Exception:
            pass

        cur.close()
    except Exception as e:
        logger.warning("Error collecting MySQL metrics: %s", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return metrics


def _parse_pg_size(size_str: str) -> float:
    """Parse PostgreSQL size string like '128MB', '1GB' to MB."""
    s = size_str.strip().upper()
    try:
        if s.endswith("GB"):
            return float(s[:-2]) * 1024
        if s.endswith("MB"):
            return float(s[:-2])
        if s.endswith("KB"):
            return float(s[:-2]) / 1024
        # Assume bytes if just a number
        return float(s) / (1024 * 1024)
    except (ValueError, TypeError):
        return 0.0

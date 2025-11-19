"""
Query Metrics Collection for RDST Analyze

Gracefully collects additional query performance metrics from database-specific
sources like pg_stat_statements (PostgreSQL) and performance_schema (MySQL).
Handles cases where these extensions/schemas are not available.
"""

import time
from typing import Dict, Any, Optional, List
from contextlib import contextmanager

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

try:
    import pymysql
    import pymysql.cursors
except ImportError:
    pymysql = None


def collect_query_metrics(sql: str, target: str = None, query_hash: str = None, **kwargs) -> Dict[str, Any]:
    """
    Collect additional query performance metrics from database telemetry sources.

    This function gracefully attempts to gather metrics from:
    - PostgreSQL: pg_stat_statements
    - MySQL: performance_schema

    Args:
        sql: The SQL query to collect metrics for
        target: Target database configuration name
        query_hash: Pre-computed query hash for lookup
        **kwargs: Additional workflow parameters including target_config

    Returns:
        Dict containing:
        - success: boolean indicating if metrics were collected
        - metrics: dict of collected metrics
        - source: source of metrics (pg_stat_statements, performance_schema, etc.)
        - fallback_reason: reason if fallback was used
        - available_sources: list of available metric sources
    """
    try:
        # Get target configuration from workflow context
        target_config = kwargs.get('target_config')
        if not target_config and target:
            from ..cli.rdst_cli import TargetsConfig
            cfg = TargetsConfig()
            cfg.load()
            target_config = cfg.get(target)

        if not target_config:
            return {
                "success": False,
                "error": f"Target configuration not found: {target}",
                "metrics": {},
                "available_sources": []
            }

        # Determine database engine
        engine = target_config.get('engine', '').lower()

        if engine in ['postgresql', 'postgres']:
            return _collect_postgres_metrics(sql, target_config, query_hash)
        elif engine in ['mysql', 'mariadb']:
            return _collect_mysql_metrics(sql, target_config, query_hash)
        else:
            return {
                "success": False,
                "error": f"Unsupported database engine: {engine}",
                "metrics": {},
                "available_sources": []
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to collect query metrics: {str(e)}",
            "metrics": {},
            "available_sources": []
        }


def _collect_postgres_metrics(sql: str, target_config: Dict[str, Any], query_hash: str = None) -> Dict[str, Any]:
    """Collect metrics from PostgreSQL pg_stat_statements and related views."""
    if psycopg2 is None:
        return {
            "success": False,
            "error": "psycopg2 not available for PostgreSQL connections",
            "metrics": {},
            "available_sources": []
        }

    try:
        conn_params = {
            'host': target_config['host'],
            'port': target_config.get('port', 5432),
            'database': target_config['database'],
            'user': target_config['user'],
            'password': target_config.get('password', ''),
        }

        if target_config.get('tls', False):
            conn_params['sslmode'] = 'require'

        with _postgres_connection(conn_params) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                available_sources = []
                metrics = {}

                # Check if pg_stat_statements is available
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'
                    ) AS has_pg_stat_statements
                """)

                has_pg_stat_statements = cursor.fetchone()['has_pg_stat_statements']

                if has_pg_stat_statements:
                    available_sources.append('pg_stat_statements')
                    stat_metrics = _get_pg_stat_statements_metrics(cursor, sql)
                    metrics.update(stat_metrics)

                # Check for pg_stat_activity (always available)
                available_sources.append('pg_stat_activity')
                activity_metrics = _get_pg_stat_activity_metrics(cursor)
                metrics.update(activity_metrics)

                # Check database-level statistics
                available_sources.append('pg_stat_database')
                db_metrics = _get_pg_stat_database_metrics(cursor, target_config['database'])
                metrics.update(db_metrics)

                # Check table statistics if we can identify tables from the query
                table_names = _extract_table_names_from_sql(sql)
                if table_names:
                    available_sources.append('pg_stat_user_tables')
                    table_metrics = _get_pg_stat_table_metrics(cursor, table_names)
                    metrics.update(table_metrics)

                return {
                    "success": True,
                    "metrics": metrics,
                    "source": "postgresql_statistics",
                    "available_sources": available_sources,
                    "database_engine": "postgresql"
                }

    except Exception as e:
        return {
            "success": False,
            "error": f"PostgreSQL metrics collection failed: {str(e)}",
            "metrics": {},
            "available_sources": [],
            "fallback_reason": str(e)
        }


def _collect_mysql_metrics(sql: str, target_config: Dict[str, Any], query_hash: str = None) -> Dict[str, Any]:
    """Collect metrics from MySQL performance_schema and information_schema."""
    if pymysql is None:
        return {
            "success": False,
            "error": "pymysql not available for MySQL connections",
            "metrics": {},
            "available_sources": []
        }

    try:
        conn_params = {
            'host': target_config['host'],
            'port': target_config.get('port', 3306),
            'database': target_config['database'],
            'user': target_config['user'],
            'password': target_config.get('password', ''),
            'charset': 'utf8mb4',
            'cursorclass': pymysql.cursors.DictCursor
        }

        if target_config.get('tls', False):
            conn_params['ssl'] = {'ssl_disabled': False}

        with _mysql_connection(conn_params) as conn:
            with conn.cursor() as cursor:
                available_sources = []
                metrics = {}

                # Check if performance_schema is available
                cursor.execute("SELECT COUNT(*) as count FROM information_schema.schemata WHERE schema_name = 'performance_schema'")
                has_performance_schema = cursor.fetchone()['count'] > 0

                if has_performance_schema:
                    available_sources.append('performance_schema')
                    perf_metrics = _get_performance_schema_metrics(cursor, sql)
                    metrics.update(perf_metrics)

                # Check INFORMATION_SCHEMA (always available)
                available_sources.append('information_schema')
                info_metrics = _get_information_schema_metrics(cursor, target_config['database'])
                metrics.update(info_metrics)

                # Check table statistics if we can identify tables
                table_names = _extract_table_names_from_sql(sql)
                if table_names:
                    table_metrics = _get_mysql_table_metrics(cursor, table_names, target_config['database'])
                    metrics.update(table_metrics)

                # Check global status variables
                available_sources.append('global_status')
                status_metrics = _get_mysql_global_status(cursor)
                metrics.update(status_metrics)

                return {
                    "success": True,
                    "metrics": metrics,
                    "source": "mysql_statistics",
                    "available_sources": available_sources,
                    "database_engine": "mysql"
                }

    except Exception as e:
        return {
            "success": False,
            "error": f"MySQL metrics collection failed: {str(e)}",
            "metrics": {},
            "available_sources": [],
            "fallback_reason": str(e)
        }


@contextmanager
def _postgres_connection(conn_params):
    """Context manager for PostgreSQL connections."""
    conn = None
    try:
        conn = psycopg2.connect(**conn_params)
        yield conn
    finally:
        if conn:
            conn.close()


@contextmanager
def _mysql_connection(conn_params):
    """Context manager for MySQL connections."""
    conn = None
    try:
        conn = pymysql.connect(**conn_params)
        yield conn
    finally:
        if conn:
            conn.close()


# PostgreSQL metrics collection helpers
def _get_pg_stat_statements_metrics(cursor, sql: str) -> Dict[str, Any]:
    """Get metrics from pg_stat_statements for a specific query."""
    metrics = {}

    try:
        # Normalize the query for pg_stat_statements lookup
        # pg_stat_statements uses its own normalization
        cursor.execute("""
            SELECT
                calls,
                total_exec_time,
                mean_exec_time,
                stddev_exec_time,
                rows,
                100.0 * shared_blks_hit / nullif(shared_blks_hit + shared_blks_read, 0) AS hit_percent,
                shared_blks_hit,
                shared_blks_read,
                shared_blks_dirtied,
                shared_blks_written,
                local_blks_hit,
                local_blks_read,
                temp_blks_read,
                temp_blks_written,
                blk_read_time,
                blk_write_time
            FROM pg_stat_statements
            WHERE query = %s
            LIMIT 1
        """, (sql,))

        result = cursor.fetchone()
        if result:
            metrics.update({
                'pg_stat_calls': result['calls'],
                'pg_stat_total_time': result['total_exec_time'],
                'pg_stat_mean_time': result['mean_exec_time'],
                'pg_stat_stddev_time': result['stddev_exec_time'],
                'pg_stat_rows': result['rows'],
                'pg_stat_cache_hit_percent': result['hit_percent'],
                'pg_stat_shared_blocks_hit': result['shared_blks_hit'],
                'pg_stat_shared_blocks_read': result['shared_blks_read'],
            })
    except Exception:
        # pg_stat_statements lookup failed - query might not be in the view yet
        pass

    return metrics


def _get_pg_stat_activity_metrics(cursor) -> Dict[str, Any]:
    """Get current activity metrics from pg_stat_activity."""
    metrics = {}

    try:
        cursor.execute("""
            SELECT
                COUNT(*) as active_connections,
                COUNT(CASE WHEN state = 'active' THEN 1 END) as active_queries,
                COUNT(CASE WHEN state = 'idle' THEN 1 END) as idle_connections,
                COUNT(CASE WHEN wait_event_type IS NOT NULL THEN 1 END) as waiting_queries
            FROM pg_stat_activity
        """)

        result = cursor.fetchone()
        if result:
            metrics.update({
                'pg_active_connections': result['active_connections'],
                'pg_active_queries': result['active_queries'],
                'pg_idle_connections': result['idle_connections'],
                'pg_waiting_queries': result['waiting_queries']
            })
    except Exception:
        pass

    return metrics


def _get_pg_stat_database_metrics(cursor, database_name: str) -> Dict[str, Any]:
    """Get database-level statistics."""
    metrics = {}

    try:
        cursor.execute("""
            SELECT
                numbackends,
                xact_commit,
                xact_rollback,
                blks_read,
                blks_hit,
                tup_returned,
                tup_fetched,
                tup_inserted,
                tup_updated,
                tup_deleted,
                conflicts,
                temp_files,
                temp_bytes,
                deadlocks
            FROM pg_stat_database
            WHERE datname = %s
        """, (database_name,))

        result = cursor.fetchone()
        if result:
            metrics.update({
                'pg_db_backends': result['numbackends'],
                'pg_db_commits': result['xact_commit'],
                'pg_db_rollbacks': result['xact_rollback'],
                'pg_db_blocks_read': result['blks_read'],
                'pg_db_blocks_hit': result['blks_hit'],
                'pg_db_tuples_returned': result['tup_returned'],
                'pg_db_tuples_fetched': result['tup_fetched'],
                'pg_db_deadlocks': result['deadlocks']
            })
    except Exception:
        pass

    return metrics


def _get_pg_stat_table_metrics(cursor, table_names: List[str]) -> Dict[str, Any]:
    """Get table-level statistics for identified tables."""
    metrics = {}

    try:
        for table_name in table_names[:5]:  # Limit to first 5 tables
            cursor.execute("""
                SELECT
                    seq_scan,
                    seq_tup_read,
                    idx_scan,
                    idx_tup_fetch,
                    n_tup_ins,
                    n_tup_upd,
                    n_tup_del,
                    n_tup_hot_upd,
                    n_live_tup,
                    n_dead_tup,
                    vacuum_count,
                    autovacuum_count,
                    analyze_count,
                    autoanalyze_count
                FROM pg_stat_user_tables
                WHERE relname = %s
            """, (table_name,))

            result = cursor.fetchone()
            if result:
                prefix = f'pg_table_{table_name}_'
                metrics.update({
                    f'{prefix}seq_scan': result['seq_scan'],
                    f'{prefix}seq_tup_read': result['seq_tup_read'],
                    f'{prefix}idx_scan': result['idx_scan'],
                    f'{prefix}idx_tup_fetch': result['idx_tup_fetch'],
                    f'{prefix}live_tuples': result['n_live_tup'],
                    f'{prefix}dead_tuples': result['n_dead_tup']
                })
    except Exception:
        pass

    return metrics


# MySQL metrics collection helpers
def _get_performance_schema_metrics(cursor, sql: str) -> Dict[str, Any]:
    """Get metrics from MySQL performance_schema."""
    metrics = {}

    try:
        # Get recent query performance from events_statements_summary_by_digest
        cursor.execute("""
            SELECT
                COUNT_STAR,
                SUM_TIMER_WAIT / 1000000000 as total_time_sec,
                AVG_TIMER_WAIT / 1000000000 as avg_time_sec,
                SUM_ROWS_EXAMINED,
                SUM_ROWS_SENT,
                SUM_SELECT_SCAN,
                SUM_SELECT_FULL_JOIN,
                SUM_NO_INDEX_USED,
                SUM_NO_GOOD_INDEX_USED
            FROM performance_schema.events_statements_summary_by_digest
            WHERE DIGEST_TEXT LIKE %s
            LIMIT 1
        """, (f"%{sql[:50]}%",))  # Match partial query

        result = cursor.fetchone()
        if result and result['COUNT_STAR']:
            metrics.update({
                'mysql_perf_count': result['COUNT_STAR'],
                'mysql_perf_total_time': result['total_time_sec'],
                'mysql_perf_avg_time': result['avg_time_sec'],
                'mysql_perf_rows_examined': result['SUM_ROWS_EXAMINED'],
                'mysql_perf_rows_sent': result['SUM_ROWS_SENT'],
                'mysql_perf_full_scans': result['SUM_SELECT_SCAN'],
                'mysql_perf_full_joins': result['SUM_SELECT_FULL_JOIN'],
                'mysql_perf_no_index_used': result['SUM_NO_INDEX_USED']
            })
    except Exception:
        pass

    return metrics


def _get_information_schema_metrics(cursor, database_name: str) -> Dict[str, Any]:
    """Get metrics from MySQL INFORMATION_SCHEMA."""
    metrics = {}

    try:
        cursor.execute("""
            SELECT
                COUNT(*) as table_count,
                SUM(DATA_LENGTH + INDEX_LENGTH) as total_size_bytes,
                SUM(TABLE_ROWS) as total_rows
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
        """, (database_name,))

        result = cursor.fetchone()
        if result:
            metrics.update({
                'mysql_db_table_count': result['table_count'],
                'mysql_db_size_bytes': result['total_size_bytes'] or 0,
                'mysql_db_total_rows': result['total_rows'] or 0
            })
    except Exception:
        pass

    return metrics


def _get_mysql_table_metrics(cursor, table_names: List[str], database_name: str) -> Dict[str, Any]:
    """Get table-level metrics for identified tables."""
    metrics = {}

    try:
        for table_name in table_names[:5]:  # Limit to first 5 tables
            cursor.execute("""
                SELECT
                    TABLE_ROWS,
                    DATA_LENGTH,
                    INDEX_LENGTH,
                    AUTO_INCREMENT
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            """, (database_name, table_name))

            result = cursor.fetchone()
            if result:
                prefix = f'mysql_table_{table_name}_'
                metrics.update({
                    f'{prefix}rows': result['TABLE_ROWS'] or 0,
                    f'{prefix}data_size': result['DATA_LENGTH'] or 0,
                    f'{prefix}index_size': result['INDEX_LENGTH'] or 0,
                    f'{prefix}auto_increment': result['AUTO_INCREMENT'] or 0
                })
    except Exception:
        pass

    return metrics


def _get_mysql_global_status(cursor) -> Dict[str, Any]:
    """Get relevant global status variables."""
    metrics = {}

    try:
        cursor.execute("""
            SELECT VARIABLE_NAME, VARIABLE_VALUE
            FROM information_schema.GLOBAL_STATUS
            WHERE VARIABLE_NAME IN (
                'Connections', 'Threads_connected', 'Threads_running',
                'Queries', 'Slow_queries', 'Com_select', 'Com_insert',
                'Com_update', 'Com_delete', 'Innodb_buffer_pool_read_requests',
                'Innodb_buffer_pool_reads', 'Key_reads', 'Key_read_requests'
            )
        """)

        for row in cursor.fetchall():
            var_name = row['VARIABLE_NAME'].lower()
            var_value = int(row['VARIABLE_VALUE'])
            metrics[f'mysql_status_{var_name}'] = var_value

    except Exception:
        pass

    return metrics


def _extract_table_names_from_sql(sql: str) -> List[str]:
    """Extract table names from SQL query using simple regex."""
    import re

    # Simple regex to find table names after FROM, JOIN, INTO, UPDATE
    # This is a basic implementation - a full SQL parser would be more accurate
    patterns = [
        r'\bFROM\s+([`"]?)(\w+)\1',
        r'\bJOIN\s+([`"]?)(\w+)\1',
        r'\bINTO\s+([`"]?)(\w+)\1',
        r'\bUPDATE\s+([`"]?)(\w+)\1',
    ]

    tables = set()
    sql_upper = sql.upper()

    for pattern in patterns:
        matches = re.findall(pattern, sql_upper, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                table_name = match[1]  # Second group is the table name
            else:
                table_name = match

            if table_name and len(table_name) <= 64:  # Reasonable table name length
                tables.add(table_name.lower())

    return list(tables)[:10]  # Return max 10 tables
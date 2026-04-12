"""Top-specific DataManager command definitions."""

from __future__ import annotations

from shared.data_manager_service.data_manager_service_command_sets import (
    DataManagerQueryType,
)
from shared.query_capture_limits import MAX_QUERY_LENGTH


TOP_COMMAND_SETS = {
    "rdst_top_pg_stat": {
        "schema": [
            "query_hash",
            "query_text",
            "calls",
            "total_time",
            "mean_time",
            "max_time",
            "pct_load",
        ],
        "query_type": DataManagerQueryType.UPSTREAM,
        "sync_interval": 5000,
        "dedup_key": "query_hash",
        "override": True,
        "filename": "rdst_top_pg_stat.csv",
        "commands": {
            "pg_stat_queries": {
                "description": "Get top queries from pg_stat_statements",
                "query": f"""
                    WITH total_time_sum AS (
                        SELECT COALESCE(SUM(total_exec_time), 1) as total
                        FROM pg_stat_statements
                    )
                    SELECT
                        abs(queryid)::text as query_hash,
                        LEFT(query, {MAX_QUERY_LENGTH}) as query_text,
                        calls,
                        ROUND(total_exec_time::numeric, 3) as total_time,
                        ROUND(mean_exec_time::numeric, 3) as mean_time,
                        ROUND(max_exec_time::numeric, 3) as max_time,
                        ROUND((total_exec_time * 100.0 / total_time_sum.total)::numeric, 2) as pct_load
                    FROM pg_stat_statements, total_time_sum
                    WHERE query IS NOT NULL
                      AND query NOT LIKE '%pg_stat_statements%'
                      AND query NOT LIKE '%information_schema%'
                      AND query NOT LIKE 'EXPLAIN%'
                      AND query NOT LIKE 'SET %'
                      AND query NOT LIKE 'SHOW %'
                      AND query NOT LIKE 'SELECT 1%'
                      AND query NOT LIKE 'BEGIN%'
                      AND query NOT LIKE 'COMMIT%'
                      AND query NOT LIKE 'ROLLBACK%'
                    ORDER BY total_exec_time DESC
                    LIMIT 50
                """,
                "default_interval_ms": 5000,
                "default_query": True,
                "supports_latency_timing": True,
            }
        },
    },
    "rdst_top_pg_activity": {
        "schema": [
            "query_hash",
            "query_text",
            "state",
            "query_start",
            "duration_ms",
            "user_name",
            "database_name",
        ],
        "query_type": DataManagerQueryType.UPSTREAM,
        "sync_interval": 2000,
        "dedup_key": "query_hash",
        "override": True,
        "filename": "rdst_top_pg_activity.csv",
        "commands": {
            "pg_activity_queries": {
                "description": "Get currently running queries from pg_stat_activity",
                "query": f"""
                    SELECT
                        SUBSTRING(MD5(query), 1, 16) as query_hash,
                        LEFT(query, {MAX_QUERY_LENGTH}) as query_text,
                        state,
                        query_start,
                        CASE
                            WHEN state = 'active' THEN GREATEST(EXTRACT(EPOCH FROM (now() - query_start)), 0) * 1000
                            WHEN state = 'idle in transaction' AND query_start IS NOT NULL THEN GREATEST(EXTRACT(EPOCH FROM (state_change - query_start)), 0) * 1000
                            ELSE GREATEST(EXTRACT(EPOCH FROM (COALESCE(state_change, now()) - COALESCE(query_start, now()))), 0) * 1000
                        END as duration_ms,
                        usename as user_name,
                        datname as database_name
                    FROM pg_stat_activity
                    WHERE query IS NOT NULL
                      AND query != '<IDLE>'
                      AND query_start IS NOT NULL
                      AND query NOT LIKE '%START_REPLICATION%'
                      AND query NOT LIKE '%autovacuum%'
                      AND query NOT LIKE '%pg_stat_activity%'
                      AND query NOT LIKE '%pg_stat_statements%'
                      AND query NOT LIKE '%information_schema%'
                      AND query NOT LIKE 'EXPLAIN%'
                      AND query NOT LIKE 'LISTEN %'
                      AND query NOT LIKE 'UNLISTEN %'
                      AND LENGTH(TRIM(query)) > 10
                      AND (
                          (state = 'active') OR
                          (state = 'idle' AND query_start > now() - interval '10 minutes') OR
                          (state = 'idle in transaction' AND query_start > now() - interval '5 minutes') OR
                          (state IN ('idle in transaction (aborted)', 'fastpath function call') AND query_start > now() - interval '2 minutes')
                      )
                      AND pid != pg_backend_pid()
                      AND usename NOT IN ('replicator')
                    ORDER BY
                        CASE
                            WHEN state = 'active' THEN 1
                            WHEN state = 'idle in transaction' THEN 2
                            ELSE 3
                        END,
                        duration_ms DESC
                """,
                "default_interval_ms": 2000,
                "default_query": True,
                "supports_latency_timing": True,
            }
        },
    },
    "rdst_top_mysql_digest": {
        "schema": [
            "query_hash",
            "query_text",
            "count_star",
            "sum_timer_wait",
            "avg_timer_wait",
            "max_timer_wait",
            "pct_load",
        ],
        "query_type": DataManagerQueryType.UPSTREAM,
        "sync_interval": 5000,
        "dedup_key": "query_hash",
        "override": True,
        "filename": "rdst_top_mysql_digest.csv",
        "commands": {
            "mysql_digest_queries": {
                "description": "Get top queries from performance_schema digest",
                "query": f"""
                    SELECT
                        DIGEST as query_hash,
                        LEFT(REPLACE(REPLACE(REPLACE(DIGEST_TEXT, '\\n', ' '), '\\r', ' '), '\\t', ' '), {MAX_QUERY_LENGTH}) as query_text,
                        COUNT_STAR as count_star,
                        ROUND(SUM_TIMER_WAIT / 1000000000000, 6) as sum_timer_wait,
                        ROUND(AVG_TIMER_WAIT / 1000000000000, 6) as avg_timer_wait,
                        ROUND(MAX_TIMER_WAIT / 1000000000000, 6) as max_timer_wait,
                        ROUND(SUM_TIMER_WAIT * 100.0 / (
                            SELECT COALESCE(SUM(SUM_TIMER_WAIT), 1)
                            FROM performance_schema.events_statements_summary_by_digest
                        ), 2) as pct_load
                    FROM performance_schema.events_statements_summary_by_digest
                    WHERE DIGEST_TEXT IS NOT NULL
                      AND DIGEST_TEXT NOT LIKE '%performance_schema%'
                      AND DIGEST_TEXT NOT LIKE '%information_schema%'
                      AND DIGEST_TEXT NOT LIKE 'EXPLAIN%'
                      AND DIGEST_TEXT NOT LIKE 'SET %'
                      AND DIGEST_TEXT NOT LIKE 'SHOW %'
                      AND DIGEST_TEXT NOT LIKE 'SELECT 1%'
                    ORDER BY SUM_TIMER_WAIT DESC
                    LIMIT 50
                """,
                "default_interval_ms": 5000,
                "default_query": True,
                "supports_latency_timing": True,
            }
        },
    },
    "rdst_top_mysql_activity": {
        "schema": ["query_hash", "query_text", "time", "state", "user", "host", "db"],
        "query_type": DataManagerQueryType.UPSTREAM,
        "sync_interval": 2000,
        "dedup_key": "query_hash",
        "override": True,
        "filename": "rdst_top_mysql_activity.csv",
        "commands": {
            "mysql_activity_queries": {
                "description": "Get currently running queries from SHOW FULL PROCESSLIST",
                "query": f"""
                    SELECT
                        SUBSTRING(MD5(INFO), 1, 16) as query_hash,
                        LEFT(INFO, {MAX_QUERY_LENGTH}) as query_text,
                        TIME as time,
                        STATE as state,
                        USER as user,
                        HOST as host,
                        DB as db
                    FROM INFORMATION_SCHEMA.PROCESSLIST
                    WHERE INFO IS NOT NULL
                      AND COMMAND != 'Sleep'
                      AND INFO NOT LIKE '%PROCESSLIST%'
                      AND INFO NOT LIKE '%information_schema%'
                      AND INFO NOT LIKE 'EXPLAIN%'
                      AND ID != CONNECTION_ID()
                    ORDER BY TIME DESC
                """,
                "default_interval_ms": 2000,
                "default_query": True,
                "supports_latency_timing": True,
            }
        },
    },
    "rdst_top_mysql_slowlog": {
        "schema": [
            "query_hash",
            "query_text",
            "exec_count",
            "total_time",
            "avg_time",
            "max_time",
            "total_rows_examined",
        ],
        "query_type": DataManagerQueryType.UPSTREAM,
        "sync_interval": 5000,
        "dedup_key": "query_hash",
        "override": True,
        "filename": "rdst_top_mysql_slowlog.csv",
        "commands": {
            "mysql_slowlog_queries": {
                "description": "Get top queries from mysql.slow_log table",
                "query": f"""
                    SELECT
                        MD5(sql_text) as query_hash,
                        LEFT(sql_text, {MAX_QUERY_LENGTH}) as query_text,
                        COUNT(*) as exec_count,
                        ROUND(SUM(TIME_TO_SEC(query_time)), 6) as total_time,
                        ROUND(AVG(TIME_TO_SEC(query_time)), 6) as avg_time,
                        ROUND(MAX(TIME_TO_SEC(query_time)), 6) as max_time,
                        SUM(rows_examined) as total_rows_examined
                    FROM mysql.slow_log
                    WHERE sql_text IS NOT NULL
                      AND sql_text NOT LIKE '%slow_log%'
                      AND sql_text NOT LIKE '%information_schema%'
                      AND sql_text NOT LIKE '%performance_schema%'
                      AND sql_text NOT LIKE 'SET %'
                      AND sql_text NOT LIKE 'SHOW %'
                      AND sql_text NOT LIKE 'SELECT 1%'
                    GROUP BY sql_text
                    ORDER BY total_time DESC
                    LIMIT 50
                """,
                "default_interval_ms": 5000,
                "default_query": True,
                "supports_latency_timing": True,
            }
        },
    },
}

__all__ = ["TOP_COMMAND_SETS"]

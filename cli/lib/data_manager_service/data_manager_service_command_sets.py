from enum import Enum


# Constants
MAX_PROXIED_QUERIES = 1000
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 1

# Maximum query length to capture from database query logs (hard-capped at 1KB)
# This limit applies to queries captured from rdst top and saved to the registry.
# For analyzing larger queries (up to 10KB), use: rdst analyze --large-query-bypass '<query>'
MAX_QUERY_LENGTH = 1024

class DataManagerQueryType(Enum):
    UPSTREAM = "upstream"
    READYSET = "readyset"
    PROXYSQL = "proxysql"
    SYSTEM = "system"
    UNKNOWN = "unknown"

class DMSDbType(Enum):
    MySql = "mysql"
    PostgreSQL = "postgresql"

COMMAND_SETS = {
    'db_tables_mysql':{
        'schema': ["schema","table"],
        'query_type': DataManagerQueryType.UPSTREAM,
        'sync_interval': 30000,
        'dedup_key': 'table',
        'override': True,
        'filename': "db_tables.csv",
        'commands': {
            'db_tables': {
                'description': 'Get tables in database',
                'query': "SELECT table_schema AS `schema`, table_name AS `table` FROM information_schema.tables "
                         "WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys');",
                'remove_backtick': True,
                'default_interval_ms': 30000,
                'default_query': True,

                'supports_latency_timing': True,
            }
        }
    },
    'db_tables_psql':{
        'schema': ["schema","table"],
        'query_type': DataManagerQueryType.UPSTREAM,
        'sync_interval': 30000,
        'dedup_key': 'table',
        'override': True,
        'filename': "db_tables.csv",
        'commands': {
            'db_tables': {
                'description': 'Get tables in database',
                'query': """
                SELECT schemaname AS schema, tablename AS table
                FROM pg_catalog.pg_tables
                WHERE schemaname != 'pg_catalog' AND schemaname != 'information_schema';""",
                'default_interval_ms': 30000,
                'default_query': True,
                'supports_latency_timing': True,
            }
        }
    },
    'readyset_status': {
        'schema': ["key", "value"],
        'query_type': DataManagerQueryType.READYSET,
        'dedup_key': 'key',
        'sync_interval': 30000,
        'override': True,
        'commands': {
            'readyset_status': {
                'description': 'Get ReadySet status',
                'query': "SHOW READYSET STATUS;",
                'supports_latency_timing': True,
                'default_interval_ms': 30000,
                'default_query': True
            }
        }
    },
    'readyset_version': {
        'schema': ["key", "value"],
        'dedup_key': 'key',
        'query_type': DataManagerQueryType.READYSET,
        'sync_interval': 300000,
        'commands': {
            'readyset_version': {
                'description': 'Get ReadySet version',
                'query': "SHOW READYSET VERSION;",
                'default_interval_ms': 300000
            }
        }
    },
    'table_replication': {
        'schema': ["table", "status", "description"],
        'dedup_key': 'table',
        'query_type': DataManagerQueryType.READYSET,
        'sync_interval': 30000,
        'override': True,
        'commands': {
            'table_replication': {
                'description': 'Get replication status for all tables',
                'query': "SHOW READYSET ALL TABLES;",
                'remove_backtick': True,
                'default_interval_ms': 30000
            }
        }
    },
    'proxied_queries': {
        'schema': ["query_id", "proxied_query", "readyset_supported", "count"],
        'dedup_key': 'query_id',
        'query_type': DataManagerQueryType.READYSET,
        'sync_interval': 30000,
        'override': True,
        'commands': {
            'proxied_queries': {
                'description': 'Get proxied queries',
                'query': f"SHOW PROXIED QUERIES LIMIT {MAX_PROXIED_QUERIES};",
                'supports_latency_timing': True,
                'default_interval_ms': 30000
            }
        }

    },
    'cached_queries': {
        'schema': ["query_id", "cache_name", "query_text", "fallback_behavior", "count"],
        'dedup_key': 'query_id',
        'query_type': DataManagerQueryType.READYSET,
        'sync_interval': 30000,
        'override': True,
        'commands': {
            'cached_queries': {
                'description': 'Get cache information',
                'query': "SHOW CACHES;",
                'supports_latency_timing': True,
                'default_interval_ms': 30000
            }
        }
    },
    'mysql_info': {
        'query_type': DataManagerQueryType.UPSTREAM,
        'schema': ["key", "value"],
        'dedup_key': 'key',
        'sync_interval': 30000,
        'override': False,
        'commands': {
            'version': {
                'description': 'Get MySQL version',
                'query': "SELECT 'version' as `key`, VERSION() as `value`;",
                'default_interval_ms': 60000,
                'default_query': True
            },
            'superuser': {
                'description': 'Check if current user has superuser privileges',
                'query': "SELECT 'superuser' as `key`, CASE WHEN EXISTS (SELECT user FROM mysql.user WHERE Super_priv = 'Y' AND user = USER()) THEN 'YES' ELSE 'NO' END as `value`;",
                'default_interval_ms': 60000
            },
            'binlog_format': {
                'description': 'Get MySQL binlog format setting',
                'query': "SELECT 'binlog_format' as `key`, @@global.binlog_format as `value`;",
                'default_interval_ms': 60000
            },
            'binlog_row_image': {
                'description': 'Get MySQL binlog row image setting',
                'query': "SELECT 'binlog_row_image' as `key`, @@global.binlog_row_image as `value`;",
                'default_interval_ms': 60000
            },
            'binlog_transaction_compression': {
                'description': 'Get MySQL binlog transaction compression setting',
                'query': "SELECT 'binlog_transaction_compression' as `key`, @@global.binlog_transaction_compression as `value`;",
                'default_interval_ms': 60000
            },
            'binlog_encryption': {
                'description': 'Get MySQL binlog encryption setting',
                'query': "SELECT 'binlog_encryption' as `key`, @@global.binlog_encryption as `value`;",
                'default_interval_ms': 60000
            },
            'db_size': {
                'description': 'Get current database size',
                'query': "SELECT 'db_size' as `key`, ROUND(SUM(data_length + index_length), 1) as `value` FROM INFORMATION_SCHEMA.TABLES WHERE table_schema = DATABASE();",
                'default_interval_ms': 60000
            },
            'num_tables': {
                'description': 'Get number of tables in current database',
                'query': "SELECT 'num_tables' as `key`, COUNT(*) as `value` FROM information_schema.tables WHERE table_schema = DATABASE();",
                'default_interval_ms': 60000
            },
            'is_rds': {
                'description': 'Check if MySQL instance is running on AWS RDS',
                'query': "SELECT 'is_rds' as `key`, CASE WHEN @@hostname LIKE '%.rds.amazonaws.com' THEN 'true' ELSE 'false' END as `value`;",
                'default_interval_ms': 60000
            }
        }
    },
    'psql_info': {
        'query_type': DataManagerQueryType.UPSTREAM,
        'schema': ["key", "value"],
        'dedup_key': 'key',
        'sync_interval': 30000,
        'override': False,
        'commands': {
            'version': {
                'description': 'Get PostgreSQL version',
                'query': "SELECT 'version' as key, version() as value;",
                'default_interval_ms': 60000,
                'default_query': True
            },
            'superuser': {
                'description': 'Check if current user has superuser privileges',
                'query': """
                     SELECT 'superuser' as key,
                CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = CURRENT_USER AND rolsuper)
                     OR EXISTS (SELECT 1 FROM pg_roles, pg_auth_members
                               WHERE pg_roles.oid = pg_auth_members.roleid
                               AND pg_roles.rolname = 'rds_superuser'
                               AND pg_auth_members.member = (SELECT oid FROM pg_roles WHERE rolname = CURRENT_USER))
                THEN 'true'
                ELSE 'false'
                     END
                     as value;
                     """,
                'default_interval_ms': 60000
            },
            'wal_level': {
                'description': 'Get PostgreSQL WAL level setting',
                'query': "SELECT 'wal_level' as key, setting as value FROM pg_settings WHERE name = 'wal_level';",
                'default_interval_ms': 60000
            },
            'db_size': {
                'description': 'Get current database size',
                'query': "SELECT 'db_size' as key, pg_database_size(current_database())::text as value;",
                'default_interval_ms': 60000
            },
            'num_tables': {
                'description': 'Get number of tables in public schema',
                'query': "SELECT 'num_tables' as key, COUNT(*)::text as value FROM information_schema.tables WHERE table_schema = 'public';",
                'default_interval_ms': 60000
            },
            'is_rds': {
                'description': 'Check if PostgreSQL instance is running on AWS RDS',
                'query': "SELECT 'is_rds' as key, CASE WHEN inet_server_addr()::text LIKE '%.rds.amazonaws.com' THEN 'true' ELSE 'false' END as value;",
                'default_interval_ms': 60000
            }
        }
    },
    'proxysql_info': {
        'query_type': DataManagerQueryType.PROXYSQL,
        'schema': ["key", "value"],
        'dedup_key': 'key',
        'sync_interval': 30000,
        'override': False,
        'commands': {
            "uptime": {
            "description": "Get ProxySQL uptime in seconds",
            "query": "SELECT 'uptime' AS `key`, Variable_Value AS `value` FROM stats_mysql_global "
                     "WHERE Variable_Name = 'ProxySQL_Uptime' UNION ALL SELECT 'uptime', 0 "
                     "WHERE NOT EXISTS (SELECT 1 FROM stats_mysql_global WHERE Variable_Name = 'ProxySQL_Uptime');",
            "default_interval_ms": 300000
            },
            "connections": {
                "description": "Get ProxySQL active connections",
                "query": "SELECT 'active_connections' AS `key`, Variable_Value AS `value` FROM stats_mysql_global "
                         "WHERE Variable_Name = 'Active_Connections' UNION ALL SELECT 'active_connections', 0 "
                         "WHERE NOT EXISTS (SELECT 1 FROM stats_mysql_global WHERE Variable_Name = 'Active_Connections');",
                "default_interval_ms": 60000
            },
            "query_cache_entries": {
                "description": "Get ProxySQL query cache entries",
                "query": "SELECT 'query_cache_entries' AS `key`, Variable_Value AS `value` FROM stats_mysql_global "
                         "WHERE Variable_Name = 'Query_Cache_Entries' UNION ALL SELECT 'query_cache_entries', 0 "
                         "WHERE NOT EXISTS (SELECT 1 FROM stats_mysql_global WHERE Variable_Name = 'Query_Cache_Entries');",
                "default_interval_ms": 60000
            },
            "query_digest_memory": {
                "description": "Get ProxySQL query digest memory usage",
                "query": "SELECT 'query_digest_memory' AS `key`, Variable_Value AS `value` FROM stats_mysql_global "
                         "WHERE Variable_Name = 'Query_Digest_Memory' UNION ALL SELECT 'query_digest_memory', 0 "
                         "WHERE NOT EXISTS (SELECT 1 FROM stats_mysql_global WHERE Variable_Name = 'Query_Digest_Memory');",
                "default_interval_ms": 60000
            },
            "queries_per_second": {
                "description": "Get ProxySQL queries per second",
                "query": "SELECT 'queries_per_second' AS `key`, Variable_Value AS `value` FROM stats_mysql_global "
                         "WHERE Variable_Name = 'Questions' UNION ALL SELECT 'queries_per_second', 0 "
                         "WHERE NOT EXISTS (SELECT 1 FROM stats_mysql_global WHERE Variable_Name = 'Questions');",
                "default_interval_ms": 30000
            }
        },
    },
    'proxysql_version':{
        'query_type': DataManagerQueryType.PROXYSQL,
        'schema':["version()"],
        'dedup_key': 'version()',
        'sync_interval': 30000,
        'override': True,
        'commands': {
            'proxysql_version': {
                'description': 'Get ProxySQL version',
                'query': "SELECT VERSION();",
                'default_interval_ms': 30000,
                'default_query': True
            }
        },
    },
    'query_pilot_query_rules_mysql':{
        'query_type': DataManagerQueryType.PROXYSQL,
        'schema':["query_id", "cache_name"],
        'dedup_key': 'version()',
        'sync_interval': 30000,
        'override': True,
        'commands': {
            'query_pilot_query_rules': {
                'description': 'Get ProxySQL version',
                'query': "SELECT DISTINCT 'shallow_' || schemaname || '_d_' || digest AS query_id, 'shallow_' || schemaname || '_d_' || digest AS cache_name FROM mysql_query_rules WHERE comment LIKE '%shallow_cache_proxysql%';",
                'default_interval_ms': 30000,
                'default_query': True
            }
        }
    },
    "proxysql_query_metrics_mysql": {
        'query_type': DataManagerQueryType.PROXYSQL,
        'schema': ["query_id", "query_text", "count", "sum_time", "min_latency", "max_latency", "cache_type"],
        "description": "Captures the point-in-time metrics for shallow caches in ProxySQL",
        "prevent_s3_sync": True,
        'commands': {
            'proxysql_query_metrics': {
                'description': 'Get ProxySQL version',
                'query': "SELECT "
                         "CONCAT('shallow_', r.schemaname, '_d_', d.digest) AS query_id, "
                         "d.digest_text AS query_text, "
                         "SUM(d.count_star) AS count, "
                         "SUM(d.sum_time) AS sum_time, "
                         "MAX(d.min_time) AS min_latency, "
                         "MAX(d.max_time) AS max_latency, "
                         "CASE "
                         "WHEN MAX(r.comment LIKE '%shallow_cache_proxysql%') THEN 'shallow_cache' "
                         "WHEN MAX(r.comment LIKE '%deep_cache%') THEN 'deep_cache' "
                         "ELSE 'uncached' "
                         "END AS cache_type "
                         "FROM stats_mysql_query_digest d "
                         "LEFT JOIN mysql_query_rules r ON d.digest = r.digest "
                         "GROUP BY d.digest, d.digest_text;",
                'default_interval_ms': 30000,
                'default_query': True
            }
        }
    },
    'query_pilot_query_rules_psql': {
        'query_type': DataManagerQueryType.PROXYSQL,
        'schema': ["query_id", "cache_name"],
        'dedup_key': 'version()',
        'sync_interval': 30000,
        'override': True,
        'commands': {
            'query_pilot_query_rules': {
                'description': 'Get ProxySQL version',
                'query': "SELECT DISTINCT 'd_' || digest AS query_id, 'd_' || digest AS cache_name  FROM psql_query_rules WHERE comment LIKE '%shallow_cache_proxysql%';",
                'default_interval_ms': 30000,
                'default_query': True
            }

        }
    },
    'system_info': {
        'query_type': DataManagerQueryType.SYSTEM,
        'schema': ["key", "value"],
        'dedup_key': 'key',
        'sync_interval': 60000,
        'override': False,
        'commands': {
            'cpu_usage': {
                'description': 'Get current CPU usage percentage',
                'query': "printf 'cpu_usage,%s' $(top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1)",
                'default_interval_ms': 30000,
                'default_query': True
            },
            'memory_used_bytes': {
                'description': 'Get current memory used in bytes',
                'query': "printf 'memory_used_bytes,%d' $(free -b | awk 'NR==2{print $3}')",
                'default_interval_ms': 30000
            },
            'memory_total_bytes': {
                'description': 'Get total memory in bytes',
                'query': "printf 'memory_total_bytes,%d' $(free -b | awk 'NR==2{print $2}')",
                'default_interval_ms': 30000
            },
            'memory_available_bytes': {
                'description': 'Get available memory in bytes',
                'query': "printf 'memory_available_bytes,%d' $(free -b | awk 'NR==2{print $7}')",
                'default_interval_ms': 30000
            },
            'memory_usage_percent': {
                'description': 'Get memory usage as percentage',
                'query': "printf 'memory_usage_percent,%.2f' $(free | awk 'NR==2{printf \"%.2f\", $3*100/$2}')",
                'default_interval_ms': 30000
            },
            'disk_used_gb': {
                'description': 'Get system disk space used in GB',
                'query': "printf 'disk_used_gb,%d' $(df -BG / | awk 'NR==2{gsub(/G/,\"\"); print $3}')",
                'default_interval_ms': 60000
            },
            'disk_total_gb': {
                'description': 'Get total system disk space in GB',
                'query': "printf 'disk_total_gb,%d' $(df -BG / | awk 'NR==2{gsub(/G/,\"\"); print $2}')",
                'default_interval_ms': 60000
            },
            'readyset_disk_used_bytes': {
                'description': 'Get ReadySet disk space used in bytes',
                'query': "printf 'readyset_disk_used_bytes,%d' $(df -B1 /readyset 2>/dev/null | awk 'NR==2{print $3}' || echo 0)",
                'default_interval_ms': 60000
            },
            'readyset_disk_total_bytes': {
                'description': 'Get total ReadySet disk space in bytes',
                'query': "printf 'readyset_disk_total_bytes,%d' $(df -B1 /readyset 2>/dev/null | awk 'NR==2{print $2}' || echo 0)",
                'default_interval_ms': 60000
            },
            'readyset_free_disk_space_bytes': {
                'description': 'Get ReadySet free disk space in bytes',
                'query': "printf 'readyset_free_disk_space_bytes,%d' $(df -B1 /readyset 2>/dev/null | awk 'NR==2{print $4}' || echo 0)",
                'default_interval_ms': 60000
            },
            'bytes_sent_per_minute': {
                'description': 'Get network bytes sent per minute across all interfaces',
                'query': "printf 'bytes_sent_per_minute,%d\\n' $(UPTIME=$(cut -d. -f1 /proc/uptime); UPTIME_MIN=$((UPTIME/60)); BYTES=$(awk 'NR>2 && $1!=\"lo:\" {gsub(\":\", \"\", $1); sum+=$10} END {print int(sum)}' /proc/net/dev); echo $((UPTIME_MIN > 0 ? BYTES/UPTIME_MIN : 0)))",
                'default_interval_ms': 60000
            },
            'bytes_recv_per_minute': {
                'description': 'Get network bytes received per minute across all interfaces',
                'query': "printf 'bytes_recv_per_minute,%d\\n' $(UPTIME=$(cut -d. -f1 /proc/uptime); UPTIME_MIN=$((UPTIME/60)); BYTES=$(awk 'NR>2 && $1!=\"lo:\" {gsub(\":\", \"\", $1); sum+=$2} END {print int(sum)}' /proc/net/dev); echo $((UPTIME_MIN > 0 ? BYTES/UPTIME_MIN : 0)))",
                'default_interval_ms': 60000
            },

            'load_1min': {
                'description': 'Get 1-minute load average',
                'query': "printf 'load_1min,%s' $(uptime | awk -F'load average:' '{print $2}' | sed 's/^ *//' | tr ',' ' ' | awk '{print $1}')",
                'default_interval_ms': 60000
            },
            'load_5min': {
                'description': 'Get 5-minute load average',
                'query': "printf 'load_5min,%s' $(uptime | awk -F'load average:' '{print $2}' | sed 's/^ *//' | tr ',' ' ' | awk '{print $2}')",
                'default_interval_ms': 60000
            },
            'load_15min': {
                'description': 'Get 15-minute load average',
                'query': "printf 'load_15min,%s' $(uptime | awk -F'load average:' '{print $2}' | sed 's/^ *//' | tr ',' ' ' | awk '{print $3}')",
                'default_interval_ms': 60000
            },
            'uptime_seconds': {
                'description': 'Get system uptime in seconds',
                'query': "printf 'uptime_seconds,%d' $(awk '{print int($1)}' /proc/uptime)",
                'default_interval_ms': 300000
            },
            'process_count': {
                'description': 'Get total number of running processes',
                'query': "printf 'process_count,%d' $(ps aux | wc -l)",
                'default_interval_ms': 60000
            },
            'tcp_established': {
                'description': 'Get count of established TCP connections',
                'query': "printf 'tcp_established,%d' $(ss -tuln | grep ESTAB | wc -l)",
                'default_interval_ms': 60000
            },
            'tcp_listen': {
                'description': 'Get count of listening TCP connections',
                'query': "printf 'tcp_listen,%d' $(ss -tuln | grep LISTEN | wc -l)",
                'default_interval_ms': 60000
            },
            'tcp_time_wait': {
                'description': 'Get count of TCP connections in time-wait',
                'query': "printf 'tcp_time_wait,%d' $(ss -tuln | grep TIME-WAIT | wc -l)",
                'default_interval_ms': 60000
            },
            'readyset_cpu': {
                'description': 'Get ReadySet CPU usage percentage',
                'query': "printf 'readyset_cpu,%s' $(ps -eo comm,%cpu | grep -i readyset | head -1 | awk '{print $2}' || echo 0)",
                'default_interval_ms': 30000
            },
            'readyset_memory_mb': {
                'description': 'Get ReadySet memory usage in MB',
                'query': "printf 'readyset_memory_mb,%d' $(ps -eo comm,rss | grep -i readyset | head -1 | awk '{printf \"%.0f\", $2/1024}' || echo 0)",
                'default_interval_ms': 30000
            },
            'mysql_cpu': {
                'description': 'Get MySQL/MariaDB CPU usage percentage',
                'query': "printf 'mysql_cpu,%s' $(ps -eo comm,%cpu | grep -E '(mysql|mariadb)' | head -1 | awk '{print $2}' || echo 0)",
                'default_interval_ms': 60000
            },
            'mysql_memory_mb': {
                'description': 'Get MySQL/MariaDB memory usage in MB',
                'query': "printf 'mysql_memory_mb,%d' $(ps -eo comm,rss | grep -E '(mysql|mariadb)' | head -1 | awk '{printf \"%.0f\", $2/1024}' || echo 0)",
                'default_interval_ms': 60000
            },
            'proxysql_cpu': {
                'description': 'Get ProxySQL CPU usage percentage',
                'query': "printf 'proxysql_cpu,%s' $(ps -eo comm,%cpu | grep proxysql | head -1 | awk '{print $2}' || echo 0)",
                'default_interval_ms': 60000
            },
            'proxysql_memory_mb': {
                'description': 'Get ProxySQL memory usage in MB',
                'query': "printf 'proxysql_memory_mb,%d' $(ps -eo comm,rss | grep proxysql | head -1 | awk '{printf \"%.0f\", $2/1024}' || echo 0)",
                'default_interval_ms': 60000
            }
        }
    },
# RDST Top Command Sets - PostgreSQL
    'rdst_top_pg_stat': {
        'schema': ["query_hash", "query_text", "calls", "total_time", "mean_time", "max_time", "pct_load"],
        'query_type': DataManagerQueryType.UPSTREAM,
        'sync_interval': 5000,
        'dedup_key': 'query_hash',
        'override': True,
        'filename': "rdst_top_pg_stat.csv",
        'commands': {
            'pg_stat_queries': {
                'description': 'Get top queries from pg_stat_statements',
                'query': f"""
                    WITH total_time_sum AS (
                        SELECT COALESCE(SUM(total_exec_time), 1) as total
                        FROM pg_stat_statements
                    )
                    SELECT
                        abs(queryid)::text as query_hash,
                        LEFT(REGEXP_REPLACE(query, E'[\\n\\r\\t]+', ' ', 'g'), {MAX_QUERY_LENGTH}) as query_text,
                        calls,
                        ROUND(total_exec_time::numeric, 3) as total_time,
                        ROUND(mean_exec_time::numeric, 3) as mean_time,
                        ROUND(max_exec_time::numeric, 3) as max_time,
                        ROUND((total_exec_time * 100.0 / total_time_sum.total)::numeric, 2) as pct_load
                    FROM pg_stat_statements, total_time_sum
                    WHERE query IS NOT NULL
                      AND query NOT LIKE '%pg_stat_statements%'
                      AND query NOT LIKE '%information_schema%'
                    ORDER BY total_exec_time DESC
                    LIMIT 50
                """,
                'default_interval_ms': 5000,
                'default_query': True,
                'supports_latency_timing': True,
            }
        }
    },
    'rdst_top_pg_activity': {
        'schema': ["query_hash", "query_text", "state", "query_start", "duration_ms", "user_name", "database_name"],
        'query_type': DataManagerQueryType.UPSTREAM,
        'sync_interval': 2000,
        'dedup_key': 'query_hash',
        'override': True,
        'filename': "rdst_top_pg_activity.csv",
        'commands': {
            'pg_activity_queries': {
                'description': 'Get currently running queries from pg_stat_activity',
                'query': f"""
                    SELECT
                        SUBSTRING(MD5(query), 1, 16) as query_hash,
                        LEFT(REGEXP_REPLACE(query, E'[\\n\\r\\t]+', ' ', 'g'), {MAX_QUERY_LENGTH}) as query_text,
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
                'default_interval_ms': 2000,
                'default_query': True,
                'supports_latency_timing': True,
            }
        }
    },
    # RDST Top Command Sets - MySQL
    'rdst_top_mysql_digest': {
        'schema': ["query_hash", "query_text", "count_star", "sum_timer_wait", "avg_timer_wait", "max_timer_wait", "pct_load"],
        'query_type': DataManagerQueryType.UPSTREAM,
        'sync_interval': 5000,
        'dedup_key': 'query_hash',
        'override': True,
        'filename': "rdst_top_mysql_digest.csv",
        'commands': {
            'mysql_digest_queries': {
                'description': 'Get top queries from performance_schema digest',
                'query': f"""
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
                    ORDER BY SUM_TIMER_WAIT DESC 
                    LIMIT 50
                """,
                'default_interval_ms': 5000,
                'default_query': True,
                'supports_latency_timing': True,
            }
        }
    },
    'rdst_top_mysql_activity': {
        'schema': ["query_hash", "query_text", "time", "state", "user", "host", "db"],
        'query_type': DataManagerQueryType.UPSTREAM,
        'sync_interval': 2000,
        'dedup_key': 'query_hash',
        'override': True,
        'filename': "rdst_top_mysql_activity.csv",
        'commands': {
            'mysql_activity_queries': {
                'description': 'Get currently running queries from SHOW FULL PROCESSLIST',
                'query': f"""
                    SELECT
                        SUBSTRING(MD5(INFO), 1, 16) as query_hash,
                        LEFT(REPLACE(REPLACE(REPLACE(INFO, '\\n', ' '), '\\r', ' '), '\\t', ' '), {MAX_QUERY_LENGTH}) as query_text,
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
                      AND ID != CONNECTION_ID()
                    ORDER BY TIME DESC
                """,
                'default_interval_ms': 2000,
                'default_query': True,
                'supports_latency_timing': True,
            }
        }
    }
}

"""Cache-specific DataManager command definitions."""

from __future__ import annotations

from shared.data_manager_service.data_manager_service_command_sets import (
    DataManagerQueryType,
)
from shared.query_capture_limits import MAX_PROXIED_QUERIES


CACHE_COMMAND_SETS = {
    "readyset_status": {
        "schema": ["key", "value"],
        "query_type": DataManagerQueryType.READYSET,
        "dedup_key": "key",
        "sync_interval": 30000,
        "override": True,
        "commands": {
            "readyset_status": {
                "description": "Get Readyset status",
                "query": "SHOW READYSET STATUS;",
                "supports_latency_timing": True,
                "default_interval_ms": 30000,
                "default_query": True,
            }
        },
    },
    "readyset_version": {
        "schema": ["key", "value"],
        "dedup_key": "key",
        "query_type": DataManagerQueryType.READYSET,
        "sync_interval": 300000,
        "commands": {
            "readyset_version": {
                "description": "Get Readyset version",
                "query": "SHOW READYSET VERSION;",
                "default_interval_ms": 300000,
            }
        },
    },
    "table_replication": {
        "schema": ["table", "status", "description"],
        "dedup_key": "table",
        "query_type": DataManagerQueryType.READYSET,
        "sync_interval": 30000,
        "override": True,
        "commands": {
            "table_replication": {
                "description": "Get replication status for all tables",
                "query": "SHOW READYSET ALL TABLES;",
                "remove_backtick": True,
                "default_interval_ms": 30000,
            }
        },
    },
    "proxied_queries": {
        "schema": ["query_id", "proxied_query", "readyset_supported", "count"],
        "dedup_key": "query_id",
        "query_type": DataManagerQueryType.READYSET,
        "sync_interval": 30000,
        "override": True,
        "commands": {
            "proxied_queries": {
                "description": "Get proxied queries",
                "query": f"SHOW PROXIED QUERIES LIMIT {MAX_PROXIED_QUERIES};",
                "supports_latency_timing": True,
                "default_interval_ms": 30000,
            }
        },
    },
    "cached_queries": {
        "schema": [
            "query_id",
            "cache_name",
            "query_text",
            "fallback_behavior",
            "count",
        ],
        "dedup_key": "query_id",
        "query_type": DataManagerQueryType.READYSET,
        "sync_interval": 30000,
        "override": True,
        "commands": {
            "cached_queries": {
                "description": "Get cache information",
                "query": "SHOW CACHES;",
                "supports_latency_timing": True,
                "default_interval_ms": 30000,
            }
        },
    },
    "proxysql_info": {
        "query_type": DataManagerQueryType.PROXYSQL,
        "schema": ["key", "value"],
        "dedup_key": "key",
        "sync_interval": 30000,
        "override": False,
        "commands": {
            "uptime": {
                "description": "Get ProxySQL uptime in seconds",
                "query": "SELECT 'uptime' AS `key`, Variable_Value AS `value` FROM stats_mysql_global "
                "WHERE Variable_Name = 'ProxySQL_Uptime' UNION ALL SELECT 'uptime', 0 "
                "WHERE NOT EXISTS (SELECT 1 FROM stats_mysql_global WHERE Variable_Name = 'ProxySQL_Uptime');",
                "default_interval_ms": 300000,
            },
            "connections": {
                "description": "Get ProxySQL active connections",
                "query": "SELECT 'active_connections' AS `key`, Variable_Value AS `value` FROM stats_mysql_global "
                "WHERE Variable_Name = 'Active_Connections' UNION ALL SELECT 'active_connections', 0 "
                "WHERE NOT EXISTS (SELECT 1 FROM stats_mysql_global WHERE Variable_Name = 'Active_Connections');",
                "default_interval_ms": 60000,
            },
            "query_cache_entries": {
                "description": "Get ProxySQL query cache entries",
                "query": "SELECT 'query_cache_entries' AS `key`, Variable_Value AS `value` FROM stats_mysql_global "
                "WHERE Variable_Name = 'Query_Cache_Entries' UNION ALL SELECT 'query_cache_entries', 0 "
                "WHERE NOT EXISTS (SELECT 1 FROM stats_mysql_global WHERE Variable_Name = 'Query_Cache_Entries');",
                "default_interval_ms": 60000,
            },
            "query_digest_memory": {
                "description": "Get ProxySQL query digest memory usage",
                "query": "SELECT 'query_digest_memory' AS `key`, Variable_Value AS `value` FROM stats_mysql_global "
                "WHERE Variable_Name = 'Query_Digest_Memory' UNION ALL SELECT 'query_digest_memory', 0 "
                "WHERE NOT EXISTS (SELECT 1 FROM stats_mysql_global WHERE Variable_Name = 'Query_Digest_Memory');",
                "default_interval_ms": 60000,
            },
            "queries_per_second": {
                "description": "Get ProxySQL queries per second",
                "query": "SELECT 'queries_per_second' AS `key`, Variable_Value AS `value` FROM stats_mysql_global "
                "WHERE Variable_Name = 'Questions' UNION ALL SELECT 'queries_per_second', 0 "
                "WHERE NOT EXISTS (SELECT 1 FROM stats_mysql_global WHERE Variable_Name = 'Questions');",
                "default_interval_ms": 30000,
            },
        },
    },
    "proxysql_version": {
        "query_type": DataManagerQueryType.PROXYSQL,
        "schema": ["version()"],
        "dedup_key": "version()",
        "sync_interval": 30000,
        "override": True,
        "commands": {
            "proxysql_version": {
                "description": "Get ProxySQL version",
                "query": "SELECT VERSION();",
                "default_interval_ms": 30000,
                "default_query": True,
            }
        },
    },
    "query_pilot_query_rules_mysql": {
        "query_type": DataManagerQueryType.PROXYSQL,
        "schema": ["query_id", "cache_name"],
        "dedup_key": "version()",
        "sync_interval": 30000,
        "override": True,
        "commands": {
            "query_pilot_query_rules": {
                "description": "Get ProxySQL version",
                "query": "SELECT DISTINCT 'shallow_' || schemaname || '_d_' || digest AS query_id, 'shallow_' || schemaname || '_d_' || digest AS cache_name FROM mysql_query_rules WHERE comment LIKE '%shallow_cache_proxysql%';",
                "default_interval_ms": 30000,
                "default_query": True,
            }
        },
    },
    "proxysql_query_metrics_mysql": {
        "query_type": DataManagerQueryType.PROXYSQL,
        "schema": [
            "query_id",
            "query_text",
            "count",
            "sum_time",
            "min_latency",
            "max_latency",
            "cache_type",
        ],
        "description": "Captures the point-in-time metrics for shallow caches in ProxySQL",
        "prevent_s3_sync": True,
        "commands": {
            "proxysql_query_metrics": {
                "description": "Get ProxySQL version",
                "query": "SELECT "
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
                "default_interval_ms": 30000,
                "default_query": True,
            }
        },
    },
    "query_pilot_query_rules_psql": {
        "query_type": DataManagerQueryType.PROXYSQL,
        "schema": ["query_id", "cache_name"],
        "dedup_key": "version()",
        "sync_interval": 30000,
        "override": True,
        "commands": {
            "query_pilot_query_rules": {
                "description": "Get ProxySQL version",
                "query": "SELECT DISTINCT 'd_' || digest AS query_id, 'd_' || digest AS cache_name  FROM psql_query_rules WHERE comment LIKE '%shallow_cache_proxysql%';",
                "default_interval_ms": 30000,
                "default_query": True,
            }
        },
    },
}

__all__ = ["CACHE_COMMAND_SETS"]

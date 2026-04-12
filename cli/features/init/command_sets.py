"""Init-specific DataManager command definitions."""

from __future__ import annotations

from shared.data_manager_service.data_manager_service_command_sets import (
    DataManagerQueryType,
)


INIT_COMMAND_SETS = {
    "mysql_info": {
        "query_type": DataManagerQueryType.UPSTREAM,
        "schema": ["key", "value"],
        "dedup_key": "key",
        "sync_interval": 30000,
        "override": False,
        "commands": {
            "version": {
                "description": "Get MySQL version",
                "query": "SELECT 'version' as `key`, VERSION() as `value`;",
                "default_interval_ms": 60000,
                "default_query": True,
            },
            "superuser": {
                "description": "Check if current user has superuser privileges",
                "query": "SELECT 'superuser' as `key`, CASE WHEN EXISTS (SELECT user FROM mysql.user WHERE Super_priv = 'Y' AND user = USER()) THEN 'YES' ELSE 'NO' END as `value`;",
                "default_interval_ms": 60000,
            },
            "binlog_format": {
                "description": "Get MySQL binlog format setting",
                "query": "SELECT 'binlog_format' as `key`, @@global.binlog_format as `value`;",
                "default_interval_ms": 60000,
            },
            "binlog_row_image": {
                "description": "Get MySQL binlog row image setting",
                "query": "SELECT 'binlog_row_image' as `key`, @@global.binlog_row_image as `value`;",
                "default_interval_ms": 60000,
            },
            "binlog_transaction_compression": {
                "description": "Get MySQL binlog transaction compression setting",
                "query": "SELECT 'binlog_transaction_compression' as `key`, @@global.binlog_transaction_compression as `value`;",
                "default_interval_ms": 60000,
            },
            "binlog_encryption": {
                "description": "Get MySQL binlog encryption setting",
                "query": "SELECT 'binlog_encryption' as `key`, @@global.binlog_encryption as `value`;",
                "default_interval_ms": 60000,
            },
            "db_size": {
                "description": "Get current database size",
                "query": "SELECT 'db_size' as `key`, ROUND(SUM(data_length + index_length), 1) as `value` FROM INFORMATION_SCHEMA.TABLES WHERE table_schema = DATABASE();",
                "default_interval_ms": 60000,
            },
            "num_tables": {
                "description": "Get number of tables in current database",
                "query": "SELECT 'num_tables' as `key`, COUNT(*) as `value` FROM information_schema.tables WHERE table_schema = DATABASE();",
                "default_interval_ms": 60000,
            },
            "is_rds": {
                "description": "Check if MySQL instance is running on AWS RDS",
                "query": "SELECT 'is_rds' as `key`, CASE WHEN @@hostname LIKE '%.rds.amazonaws.com' THEN 'true' ELSE 'false' END as `value`;",
                "default_interval_ms": 60000,
            },
        },
    },
    "psql_info": {
        "query_type": DataManagerQueryType.UPSTREAM,
        "schema": ["key", "value"],
        "dedup_key": "key",
        "sync_interval": 30000,
        "override": False,
        "commands": {
            "version": {
                "description": "Get PostgreSQL version",
                "query": "SELECT 'version' as key, version() as value;",
                "default_interval_ms": 60000,
                "default_query": True,
            },
            "superuser": {
                "description": "Check if current user has superuser privileges",
                "query": """
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
                "default_interval_ms": 60000,
            },
            "wal_level": {
                "description": "Get PostgreSQL WAL level setting",
                "query": "SELECT 'wal_level' as key, setting as value FROM pg_settings WHERE name = 'wal_level';",
                "default_interval_ms": 60000,
            },
            "db_size": {
                "description": "Get current database size",
                "query": "SELECT 'db_size' as key, pg_database_size(current_database())::text as value;",
                "default_interval_ms": 60000,
            },
            "num_tables": {
                "description": "Get number of tables in public schema",
                "query": "SELECT 'num_tables' as key, COUNT(*)::text as value FROM information_schema.tables WHERE table_schema = 'public';",
                "default_interval_ms": 60000,
            },
            "is_rds": {
                "description": "Check if PostgreSQL instance is running on AWS RDS",
                "query": "SELECT 'is_rds' as key, CASE WHEN inet_server_addr()::text LIKE '%.rds.amazonaws.com' THEN 'true' ELSE 'false' END as value;",
                "default_interval_ms": 60000,
            },
        },
    },
}

__all__ = ["INIT_COMMAND_SETS"]

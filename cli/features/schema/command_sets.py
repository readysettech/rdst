"""Schema-specific DataManager command definitions."""

from __future__ import annotations

from shared.data_manager_service.data_manager_service_command_sets import (
    DataManagerQueryType,
)


SCHEMA_COMMAND_SETS = {
    "db_tables_mysql": {
        "schema": ["schema", "table"],
        "query_type": DataManagerQueryType.UPSTREAM,
        "sync_interval": 30000,
        "dedup_key": "table",
        "override": True,
        "filename": "db_tables.csv",
        "commands": {
            "db_tables": {
                "description": "Get tables in database",
                "query": "SELECT table_schema AS `schema`, table_name AS `table` FROM information_schema.tables "
                "WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys');",
                "remove_backtick": True,
                "default_interval_ms": 30000,
                "default_query": True,
                "supports_latency_timing": True,
            }
        },
    },
    "db_tables_psql": {
        "schema": ["schema", "table"],
        "query_type": DataManagerQueryType.UPSTREAM,
        "sync_interval": 30000,
        "dedup_key": "table",
        "override": True,
        "filename": "db_tables.csv",
        "commands": {
            "db_tables": {
                "description": "Get tables in database",
                "query": """
                SELECT schemaname AS schema, tablename AS table
                FROM pg_catalog.pg_tables
                WHERE schemaname != 'pg_catalog' AND schemaname != 'information_schema';""",
                "default_interval_ms": 30000,
                "default_query": True,
                "supports_latency_timing": True,
            }
        },
    },
}

__all__ = ["SCHEMA_COMMAND_SETS"]

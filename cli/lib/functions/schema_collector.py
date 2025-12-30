"""
Schema Collection for LLM Analysis

Collects schema information for tables referenced in queries to provide
context to LLM for better rewrite and index suggestions.
"""

import re
from typing import Dict, Any, Set


def collect_target_schema(sql: str, target: str = None, **kwargs) -> Dict[str, Any]:
    """
    Workflow step to collect schema information for tables in the query.

    Args:
        sql: The SQL query to analyze
        target: Target database name (for logging)
        **kwargs: Additional workflow parameters including target_config

    Returns:
        Dict containing:
        - success: boolean indicating if collection succeeded
        - schema_info: formatted schema string for LLM prompt
        - tables_analyzed: list of table names found
        - engine_version: database engine version string
        - engine_major_version: major version number (e.g., 12 for PostgreSQL 12.4)
        - error: error message if failed
    """
    try:
        target_config = kwargs.get('target_config')

        # Handle case where WorkflowManager passes target_config as string
        if isinstance(target_config, str):
            import json
            try:
                target_config = json.loads(target_config)
            except (json.JSONDecodeError, TypeError):
                return {
                    "success": False,
                    "schema_info": "Schema information: Not available",
                    "tables_analyzed": [],
                    "engine_version": "unknown",
                    "engine_major_version": None,
                    "error": "target_config is invalid (string parse failed)"
                }

        if not target_config:
            return {
                "success": False,
                "schema_info": "Schema information: Not available",
                "tables_analyzed": [],
                "engine_version": "unknown",
                "engine_major_version": None,
                "error": "No target_config provided"
            }

        # Collect engine version
        version_info = collect_engine_version(target_config)
        engine_version = version_info.get("version", "unknown")
        engine_major_version = version_info.get("major_version")

        schema_info = collect_schema_for_query(sql, target_config)

        # Extract table names for reporting
        table_names = _extract_table_names_from_sql(sql)

        return {
            "success": True,
            "schema_info": schema_info,
            "tables_analyzed": list(table_names),
            "engine_version": engine_version,
            "engine_major_version": engine_major_version,
            "error": None
        }

    except Exception as e:
        return {
            "success": False,
            "schema_info": "Schema information: Collection failed",
            "tables_analyzed": [],
            "engine_version": "unknown",
            "engine_major_version": None,
            "error": str(e)
        }


def collect_engine_version(target_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Collect the database engine version.

    This is important for version-specific optimization guidance:
    - PostgreSQL < 12: CTEs always materialize (can hurt performance)
    - PostgreSQL >= 12: CTEs can be inlined (NOT MATERIALIZED hint available)
    - MySQL 8.0+: Window functions, CTEs supported

    Args:
        target_config: Database target configuration

    Returns:
        Dict containing:
        - version: Full version string (e.g., "PostgreSQL 14.5")
        - major_version: Major version number (e.g., 14)
        - error: Error message if version couldn't be determined
    """
    import os

    engine = target_config.get('engine', 'unknown').lower()
    host = target_config.get('host')
    port = target_config.get('port')
    user = target_config.get('user')
    database = target_config.get('database')
    password_env = target_config.get('password_env')
    password = os.environ.get(password_env) if password_env else None

    if not all([host, user, database, password]):
        return {"version": "unknown", "major_version": None, "error": "Missing connection details"}

    try:
        if engine == 'mysql':
            import pymysql
            connection = pymysql.connect(
                host=host,
                port=port or 3306,
                user=user,
                password=password,
                database=database,
                connect_timeout=5
            )
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT VERSION()")
                    result = cursor.fetchone()
                    if result:
                        version_str = result[0]
                        # Parse version like "8.0.33" or "5.7.42-log"
                        major_version = _parse_major_version(version_str)
                        return {
                            "version": f"MySQL {version_str}",
                            "major_version": major_version,
                            "error": None
                        }
            finally:
                connection.close()

        elif engine in ['postgresql', 'postgres']:
            import psycopg2
            connection = psycopg2.connect(
                host=host,
                port=port or 5432,
                user=user,
                password=password,
                database=database,
                connect_timeout=5
            )
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT version()")
                    result = cursor.fetchone()
                    if result:
                        version_str = result[0]
                        # Parse version like "PostgreSQL 14.5 on x86_64..."
                        major_version = _parse_postgres_version(version_str)
                        return {
                            "version": version_str.split(' on ')[0] if ' on ' in version_str else version_str,
                            "major_version": major_version,
                            "error": None
                        }
            finally:
                connection.close()
        else:
            return {"version": f"Unsupported engine: {engine}", "major_version": None, "error": None}

    except Exception as e:
        return {"version": "unknown", "major_version": None, "error": str(e)}

    return {"version": "unknown", "major_version": None, "error": "Failed to get version"}


def _parse_major_version(version_str: str) -> int:
    """Parse major version from MySQL version string like '8.0.33' or '5.7.42-log'."""
    try:
        # Handle formats like "8.0.33", "5.7.42-log", "8.0.33-0ubuntu0.22.04.1"
        version_part = version_str.split('-')[0]
        parts = version_part.split('.')
        if len(parts) >= 1:
            return int(parts[0])
    except (ValueError, IndexError):
        pass
    return None


def _parse_postgres_version(version_str: str) -> int:
    """Parse major version from PostgreSQL version string like 'PostgreSQL 14.5 on x86_64...'."""
    try:
        # Handle formats like "PostgreSQL 14.5 on x86_64..."
        match = re.search(r'PostgreSQL\s+(\d+)', version_str)
        if match:
            return int(match.group(1))
    except (ValueError, AttributeError):
        pass
    return None


def collect_schema_for_query(sql: str, target_config: Dict[str, Any]) -> str:
    """
    Collect schema information for tables referenced in the query.

    Args:
        sql: The SQL query to analyze
        target_config: Database target configuration (dict or JSON string)

    Returns:
        Formatted schema string for LLM prompt
    """
    try:
        # Handle case where WorkflowManager passes target_config as string
        if isinstance(target_config, str):
            import json
            try:
                target_config = json.loads(target_config)
            except (json.JSONDecodeError, TypeError):
                return "Schema information: target_config is invalid (string parse failed)"
        # Extract table names from query
        table_names = _extract_table_names_from_sql(sql)

        if not table_names:
            return "Schema information: No tables identified in query"

        # Get schema for each table
        engine = target_config.get('engine', 'unknown').lower()

        if engine == 'mysql':
            return _collect_mysql_schema(table_names, target_config)
        elif engine in ['postgresql', 'postgres']:
            return _collect_postgres_schema(table_names, target_config)
        else:
            return f"Schema information: Unsupported database engine '{engine}'"

    except Exception as e:
        return f"Schema information: Failed to collect schema - {str(e)}"


def _extract_table_names_from_sql(sql: str) -> Set[str]:
    """Extract table names from SQL query using regex patterns."""
    table_names = set()
    sql_upper = sql.upper()

    # Common patterns for table references
    patterns = [
        r'\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*)',  # FROM table_name
        r'\bJOIN\s+([a-zA-Z_][a-zA-Z0-9_]*)',  # JOIN table_name
        r'\bINTO\s+([a-zA-Z_][a-zA-Z0-9_]*)',  # INTO table_name
        r'\bUPDATE\s+([a-zA-Z_][a-zA-Z0-9_]*)',  # UPDATE table_name
        r'\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+(?:AS\s+)?[a-zA-Z_]',  # FROM table AS alias
        r'\bJOIN\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+(?:AS\s+)?[a-zA-Z_]',  # JOIN table AS alias
    ]

    for pattern in patterns:
        matches = re.finditer(pattern, sql, re.IGNORECASE)
        for match in matches:
            table_name = match.group(1).lower()
            # Avoid SQL keywords and aliases
            if table_name not in {'select', 'where', 'order', 'group', 'having', 'limit', 'offset'}:
                table_names.add(table_name)

    return table_names


def _collect_mysql_schema(table_names: Set[str], target_config: Dict[str, Any]) -> str:
    """Collect schema information for MySQL tables."""
    try:
        import pymysql

        # Get connection details
        host = target_config.get('host')
        port = target_config.get('port', 3306)
        user = target_config.get('user')
        database = target_config.get('database')

        # Get password from environment
        import os
        password_env = target_config.get('password_env')
        password = os.environ.get(password_env) if password_env else None

        if not all([host, user, database, password]):
            return "Schema information: Missing connection details"

        # Connect to database
        connection = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=5
        )

        schema_parts = []

        try:
            with connection.cursor() as cursor:
                for table_name in table_names:
                    # Validate table name contains only safe characters
                    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name):
                        continue  # Skip potentially unsafe table names

                    # Get table structure - use backtick quoting for MySQL identifier safety
                    # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                    cursor.execute(f"DESCRIBE `{table_name}`")
                    columns = cursor.fetchall()

                    if not columns:
                        continue

                    # Get indexes - use backtick quoting for MySQL identifier safety
                    # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                    cursor.execute(f"SHOW INDEX FROM `{table_name}`")
                    indexes = cursor.fetchall()

                    # Get row count estimate (for LLM to understand scale)
                    cursor.execute("""
                        SELECT TABLE_ROWS as row_estimate
                        FROM information_schema.TABLES
                        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                    """, (database, table_name))
                    row_count_result = cursor.fetchone()
                    row_estimate = row_count_result[0] if row_count_result else 0

                    # Format table schema
                    table_schema = [f"\nTable: {table_name}"]
                    table_schema.append(f"Row estimate: {row_estimate:,}")
                    table_schema.append("Columns:")

                    for col in columns:
                        field, type_, null, key, default, extra = col
                        key_info = f" [{key}]" if key else ""
                        table_schema.append(f"  - {field} {type_}{key_info}")

                    # Format indexes (show type to prevent hallucination)
                    if indexes:
                        table_schema.append("Indexes:")
                        index_dict = {}
                        index_types = {}
                        for idx in indexes:
                            index_name = idx[2]  # Key_name
                            column_name = idx[4]  # Column_name
                            index_type = idx[10]  # Index_type (BTREE, HASH, FULLTEXT, etc.)

                            if index_name not in index_dict:
                                index_dict[index_name] = []
                                index_types[index_name] = index_type
                            index_dict[index_name].append(column_name)

                        for idx_name, cols in index_dict.items():
                            cols_str = ', '.join(cols)
                            idx_type = index_types.get(idx_name, 'BTREE')
                            # Show in format similar to PostgreSQL for consistency
                            table_schema.append(f"  - {idx_name} USING {idx_type} ({cols_str})")

                    schema_parts.append('\n'.join(table_schema))

        finally:
            connection.close()

        if schema_parts:
            return "Schema information:\n" + '\n'.join(schema_parts)
        else:
            return "Schema information: No schema found for referenced tables"

    except Exception as e:
        return f"Schema information: Error collecting MySQL schema - {str(e)}"

def _collect_postgres_schema(table_names: Set[str], target_config: Dict[str, Any]) -> str:
    """Collect schema information for PostgreSQL tables."""
    try:
        import psycopg2

        # Get connection details
        host = target_config.get('host')
        port = target_config.get('port', 5432)
        user = target_config.get('user')
        database = target_config.get('database')

        # Get password from environment
        import os
        password_env = target_config.get('password_env')
        password = os.environ.get(password_env) if password_env else None

        if not all([host, user, database, password]):
            return "Schema information: Missing connection details"

        # Connect to database
        connection = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=5
        )

        schema_parts = []

        try:
            with connection.cursor() as cursor:
                for table_name in table_names:
                    # Get table columns
                    cursor.execute("""
                        SELECT column_name, data_type, is_nullable, column_default
                        FROM information_schema.columns
                        WHERE table_name = %s
                        ORDER BY ordinal_position
                    """, (table_name,))
                    columns = cursor.fetchall()

                    if not columns:
                        continue

                    # Get indexes
                    cursor.execute("""
                        SELECT indexname, indexdef
                        FROM pg_indexes
                        WHERE tablename = %s
                    """, (table_name,))
                    indexes = cursor.fetchall()

                    # Get row count estimate (for LLM to understand scale)
                    cursor.execute("""
                        SELECT reltuples::bigint as row_estimate
                        FROM pg_class
                        WHERE relname = %s
                    """, (table_name,))
                    row_count_result = cursor.fetchone()
                    row_estimate = row_count_result[0] if row_count_result else 0

                    # Format table schema
                    table_schema = [f"\nTable: {table_name}"]
                    table_schema.append(f"Row estimate: {row_estimate:,}")
                    table_schema.append("Columns:")

                    for col in columns:
                        column_name, data_type, is_nullable, default = col
                        null_info = " NULL" if is_nullable == 'YES' else " NOT NULL"
                        table_schema.append(f"  - {column_name} {data_type}{null_info}")

                    # Format indexes (show full definition with USING clause to prevent hallucination)
                    if indexes:
                        table_schema.append("Indexes:")
                        for idx_name, idx_def in indexes:
                            # Show full CREATE INDEX definition including USING clause
                            # This is critical for LLM to understand index type (btree vs hash vs gin)
                            table_schema.append(f"  - {idx_def}")

                    schema_parts.append('\n'.join(table_schema))

                # Collect installed extensions and custom types (important for type compatibility)
                extensions_info = _collect_postgres_extensions(cursor)
                custom_types_info = _collect_postgres_custom_types(cursor)

                if extensions_info:
                    schema_parts.insert(0, extensions_info)
                if custom_types_info:
                    schema_parts.insert(1 if extensions_info else 0, custom_types_info)

        finally:
            connection.close()

        if schema_parts:
            return "Schema information:\n" + '\n'.join(schema_parts)
        else:
            return "Schema information: No schema found for referenced tables"

    except Exception as e:
        return f"Schema information: Error collecting PostgreSQL schema - {str(e)}"


def _collect_postgres_extensions(cursor) -> str:
    """
    Collect installed PostgreSQL extensions with descriptions from the database.

    Args:
        cursor: Active database cursor

    Returns:
        Formatted string describing installed extensions
    """
    from .postgres_metadata import fetch_postgres_extensions

    try:
        extensions = fetch_postgres_extensions(cursor)
        if not extensions:
            return ""
        lines = ["\nInstalled Extensions:"]
        for ext_name, ext_version, description, _ in extensions:
            if description:
                lines.append(f"  - {ext_name} v{ext_version}: {description}")
            else:
                lines.append(f"  - {ext_name} v{ext_version}")
        return '\n'.join(lines)
    except Exception:
        return ""


def _collect_postgres_custom_types(cursor) -> str:
    """
    Collect custom types and domains defined in the database.

    Args:
        cursor: Active database cursor

    Returns:
        Formatted string describing custom types
    """
    from .postgres_metadata import fetch_postgres_custom_types

    try:
        custom_types = fetch_postgres_custom_types(cursor)
        if not custom_types:
            return ""
        lines = ["\nCustom Types:"]
        for type_name, type_code, type_category, base_type, enum_values in custom_types:
            if type_code == 'e' and enum_values:
                lines.append(f"  - {type_name} (enum): [{enum_values}]")
            elif type_code == 'd' and base_type:
                lines.append(f"  - {type_name} (domain over {base_type})")
            elif type_code == 'b':
                lines.append(f"  - {type_name} (extension type): compare with same type only")
            else:
                lines.append(f"  - {type_name} ({type_category})")

        return '\n'.join(lines)
    except Exception:
        return ""

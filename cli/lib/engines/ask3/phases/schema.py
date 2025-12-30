"""
Phase 1: Schema Loading

Loads database schema from semantic layer (fast) or database (slow).
Populates context with schema information for SQL generation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..context import Ask3Context
    from ..presenter import Ask3Presenter

from ..types import SchemaInfo, TableInfo, ColumnInfo, DbType, SchemaSource

logger = logging.getLogger(__name__)


def load_schema(
    ctx: 'Ask3Context',
    presenter: 'Ask3Presenter',
    semantic_manager=None
) -> 'Ask3Context':
    """
    Load schema from semantic layer or database.

    Tries semantic layer first (fast), falls back to database (slow).
    The semantic layer is complete if >50% of tables have column types.

    Args:
        ctx: Ask3Context with target set
        presenter: For progress output
        semantic_manager: SemanticLayerManager instance (optional, creates default)

    Returns:
        Updated context with schema_info and schema_formatted populated
    """
    ctx.phase = 'schema'
    presenter.schema_loading(ctx.target)

    # Import here to avoid circular imports
    # Path: lib/engines/ask3/phases/schema.py -> lib/semantic_layer/manager.py
    from ....semantic_layer.manager import SemanticLayerManager

    if semantic_manager is None:
        semantic_manager = SemanticLayerManager()

    # Try semantic layer first
    if semantic_manager.exists(ctx.target):
        try:
            layer = semantic_manager.load(ctx.target)

            if _is_complete(layer):
                # Use semantic layer (fast path)
                ctx.schema_info = _build_schema_info_from_semantic(layer, ctx.target, ctx.db_type)
                ctx.schema_formatted = _format_semantic_schema(layer)
                ctx.schema_source = SchemaSource.SEMANTIC

                presenter.schema_loaded(
                    source='semantic layer',
                    table_count=len(layer.tables)
                )
                return ctx

            else:
                logger.info(f"Semantic layer for {ctx.target} is incomplete, falling back to database")

        except Exception as e:
            logger.warning(f"Failed to load semantic layer: {e}")

    # Fall back to database collection (slow path)
    try:
        ctx.schema_info, ctx.schema_formatted = _collect_from_database(ctx)
        ctx.schema_source = SchemaSource.DATABASE

        presenter.schema_loaded(
            source='database',
            table_count=len(ctx.schema_info.tables) if ctx.schema_info else 0
        )

    except Exception as e:
        logger.error(f"Failed to collect schema from database: {e}")
        ctx.mark_error(f"Failed to load schema: {e}")

    return ctx


def _is_complete(layer) -> bool:
    """
    Check if semantic layer has enough type information.

    A layer is complete if >50% of tables have at least one column with data_type set.
    """
    if not layer.tables:
        return False

    tables_with_types = sum(
        1 for table in layer.tables.values()
        if any(col.data_type for col in table.columns.values())
    )

    return tables_with_types > len(layer.tables) / 2


def _build_schema_info_from_semantic(layer, target: str, db_type: str) -> SchemaInfo:
    """Build SchemaInfo from semantic layer."""
    schema_info = SchemaInfo(
        target=target,
        db_type=db_type,
        source=SchemaSource.SEMANTIC
    )

    for table_name, table in layer.tables.items():
        table_info = TableInfo(
            name=table_name,
            description=table.description
        )

        for col_name, col in table.columns.items():
            table_info.columns[col_name] = ColumnInfo(
                name=col_name,
                data_type=col.data_type or 'unknown',
                description=col.description,
                is_primary_key=col_name.lower() == 'id',  # Simple heuristic
            )

        schema_info.tables[table_name] = table_info

    # Copy terminology from semantic layer for Tier 1 matching
    if hasattr(layer, 'terminology') and layer.terminology:
        schema_info.terminology = layer.terminology

    return schema_info


def _format_semantic_schema(layer) -> str:
    """
    Format semantic layer as schema string for LLM prompt.

    Includes table descriptions, column types, enum values, and extension info.
    """
    parts = []

    for table_name, table in layer.tables.items():
        # Table header with description
        if table.description:
            parts.append(f"Table: {table_name} -- {table.description}")
        else:
            parts.append(f"Table: {table_name}")

        # Columns
        col_strs = []
        for col_name, col in table.columns.items():
            col_str = f"  {col_name}"
            if col.data_type:
                col_str += f" ({col.data_type})"
            if col.description:
                col_str += f" -- {col.description}"
            if col.enum_values:
                enum_preview = ", ".join(f"{k}={v}" for k, v in list(col.enum_values.items())[:3])
                col_str += f" [enum: {enum_preview}]"
            col_strs.append(col_str)

        parts.append("\n".join(col_strs))
        parts.append("")  # Blank line between tables

    # Add extensions and custom types context if available
    extensions_context = layer.get_extensions_context()
    if extensions_context:
        parts.append(extensions_context)

    return "\n".join(parts)


def _collect_from_database(ctx: 'Ask3Context') -> tuple[Optional[SchemaInfo], str]:
    """
    Collect schema directly from database.

    This is the slow path - used when semantic layer doesn't exist or is incomplete.
    """
    if not ctx.target_config:
        logger.error("No target_config provided for database schema collection")
        return None, "Schema information: Not available (no target config)"

    db_type = ctx.db_type or ctx.target_config.get('engine', 'postgresql').lower()

    if db_type == DbType.POSTGRESQL or 'postgres' in db_type:
        return _collect_postgres_schema(ctx.target_config, ctx.target)
    elif db_type == DbType.MYSQL or 'mysql' in db_type:
        return _collect_mysql_schema(ctx.target_config, ctx.target)
    else:
        logger.error(f"Unsupported database type: {db_type}")
        return None, f"Schema information: Unsupported database type {db_type}"


def _collect_postgres_schema(config: dict, target: str) -> tuple[Optional[SchemaInfo], str]:
    """Collect schema from PostgreSQL database."""
    try:
        import psycopg2

        host = config.get('host', 'localhost')
        port = config.get('port', 5432)
        user = config.get('user') or config.get('username')
        password = config.get('password')
        database = config.get('database') or config.get('dbname')

        if not all([host, user, database]):
            return None, "Schema information: Missing connection parameters"

        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=10
        )

        schema_info = SchemaInfo(target=target, db_type=DbType.POSTGRESQL, source=SchemaSource.DATABASE)
        parts = []

        with conn.cursor() as cur:
            # Get all tables
            cur.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """)
            tables = [row[0] for row in cur.fetchall()]

            for table_name in tables:
                # Get columns for this table
                cur.execute("""
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = %s
                    ORDER BY ordinal_position
                """, (table_name,))

                table_info = TableInfo(name=table_name)
                col_strs = []

                for col_name, data_type, nullable in cur.fetchall():
                    table_info.columns[col_name] = ColumnInfo(
                        name=col_name,
                        data_type=data_type,
                    )
                    null_marker = " NULL" if nullable == 'YES' else ""
                    col_strs.append(f"  {col_name} ({data_type}){null_marker}")

                schema_info.tables[table_name] = table_info
                parts.append(f"Table: {table_name}")
                parts.append("\n".join(col_strs))
                parts.append("")

        conn.close()
        return schema_info, "\n".join(parts)

    except ImportError:
        logger.error("psycopg2 not installed")
        return None, "Schema information: psycopg2 not installed"
    except Exception as e:
        logger.error(f"PostgreSQL schema collection failed: {e}")
        return None, f"Schema information: Collection failed ({e})"


def _collect_mysql_schema(config: dict, target: str) -> tuple[Optional[SchemaInfo], str]:
    """Collect schema from MySQL database."""
    try:
        import pymysql

        host = config.get('host', 'localhost')
        port = config.get('port', 3306)
        user = config.get('user') or config.get('username')
        password = config.get('password')
        database = config.get('database') or config.get('dbname')

        if not all([host, user, database]):
            return None, "Schema information: Missing connection parameters"

        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=10
        )

        schema_info = SchemaInfo(target=target, db_type=DbType.MYSQL, source=SchemaSource.DATABASE)
        parts = []

        with conn.cursor() as cur:
            # Get all tables
            cur.execute("SHOW TABLES")
            tables = [row[0] for row in cur.fetchall()]

            for table_name in tables:
                # Get columns
                cur.execute(f"DESCRIBE `{table_name}`")

                table_info = TableInfo(name=table_name)
                col_strs = []

                for row in cur.fetchall():
                    col_name = row[0]
                    data_type = row[1]
                    nullable = row[2]

                    table_info.columns[col_name] = ColumnInfo(
                        name=col_name,
                        data_type=data_type,
                    )
                    null_marker = " NULL" if nullable == 'YES' else ""
                    col_strs.append(f"  {col_name} ({data_type}){null_marker}")

                schema_info.tables[table_name] = table_info
                parts.append(f"Table: {table_name}")
                parts.append("\n".join(col_strs))
                parts.append("")

        conn.close()
        return schema_info, "\n".join(parts)

    except ImportError:
        logger.error("pymysql not installed")
        return None, "Schema information: pymysql not installed"
    except Exception as e:
        logger.error(f"MySQL schema collection failed: {e}")
        return None, f"Schema information: Collection failed ({e})"

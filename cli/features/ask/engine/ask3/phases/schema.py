"""
Phase 1: Schema Loading

Loads the stored semantic layer or initializes one from the database.
Populates context with schema information for SQL generation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from features.schema.prompt_serialization import (
    VERBOSE_SCHEMA_FORMAT_VERSION,
    COMPACT_SCHEMA_FORMAT_VERSION,
    ADAPTIVE_SCHEMA_FORMAT_VERSION,
    ADAPTIVE_SCHEMA_MIN_SAVINGS_PERCENT,
    ADAPTIVE_SCHEMA_MIN_SAVINGS_RATIO,
    _format_semantic_schema,
    _compact_schema_escape,
    _compact_schema_code,
    _unquote_sql_identifier,
    _compact_relationship_line,
    format_semantic_schema_compact,
    SemanticSchemaSerialization,
    _choose_semantic_schema_text,
    select_semantic_schema_serialization,
    format_semantic_schema_adaptive,
)

if TYPE_CHECKING:
    from ..context import Ask3Context
    from ..presenter import Ask3Presenter

from ..types import ColumnInfo, SchemaInfo, SchemaSource, TableInfo

logger = logging.getLogger(__name__)


def load_schema(
    ctx: "Ask3Context",
    presenter: "Ask3Presenter",
    semantic_manager=None,
    semantic_schema_formatter: Callable[[Any], str] | None = None,
) -> "Ask3Context":
    """
    Load a semantic layer, initializing it from the database when missing.

    Auto-init uses the same structural introspector as ``rdst schema init`` and
    persists the result. An existing incomplete layer is never overwritten; Ask
    uses a fresh in-memory introspection for that request instead.
    The semantic layer is complete if >50% of tables have column types.

    Args:
        ctx: Ask3Context with target set
        presenter: For progress output
        semantic_manager: SemanticLayerManager instance (optional, creates default)
        semantic_schema_formatter: Optional diagnostic formatter for semantic layers.

    Returns:
        Updated context with schema_info and schema_formatted populated
    """
    ctx.phase = "schema"
    presenter.schema_loading(ctx.target)

    # Import here to avoid circular imports
    # Path: features/ask/engine/ask3/phases/schema.py -> features/schema/semantic_layer/manager.py
    from features.schema.semantic_layer.manager import SemanticLayerManager

    if semantic_manager is None:
        semantic_manager = SemanticLayerManager()

    layer_exists = semantic_manager.exists(ctx.target)
    if layer_exists:
        try:
            layer = semantic_manager.load(ctx.target)

            if _is_complete(layer):
                return _apply_semantic_layer(
                    ctx,
                    presenter,
                    layer,
                    source=SchemaSource.SEMANTIC,
                    semantic_schema_formatter=semantic_schema_formatter,
                )
            logger.warning(
                "Semantic layer for %s is incomplete; using fresh introspection "
                "without overwriting it",
                ctx.target,
            )
        except Exception as exc:
            ctx.mark_error(f"Failed to load semantic layer: {exc}")
            return ctx

    if not ctx.target_config:
        ctx.mark_error("No target configuration available for schema initialization")
        return ctx

    try:
        from features.schema.semantic_layer.introspector import SchemaIntrospector

        layer = SchemaIntrospector(ctx.target_config).introspect(
            target_name=ctx.target,
            enum_threshold=20,
            sample_enums=True,
        )
        if not _is_complete(layer):
            ctx.mark_error("Schema initialization produced no usable typed tables")
            return ctx
        if not layer_exists:
            semantic_manager.save(layer)
        return _apply_semantic_layer(
            ctx,
            presenter,
            layer,
            source=SchemaSource.SEMANTIC if not layer_exists else SchemaSource.DATABASE,
            semantic_schema_formatter=semantic_schema_formatter,
        )
    except Exception as exc:
        logger.error("Failed to initialize schema: %s", exc)
        ctx.mark_error(f"Failed to initialize schema: {exc}")

    return ctx


def _apply_semantic_layer(
    ctx: "Ask3Context",
    presenter: "Ask3Presenter",
    layer: Any,
    *,
    source: str,
    semantic_schema_formatter: Callable[[Any], str] | None,
) -> "Ask3Context":
    """Put one complete semantic representation on the Ask context."""
    ctx.schema_info = _build_schema_info_from_semantic(layer, ctx.target, ctx.db_type)
    serialization = select_semantic_schema_serialization(
        layer, forced_formatter=semantic_schema_formatter
    )
    ctx.schema_formatted = serialization.formatted
    ctx.schema_source = source
    ctx.schema_format = serialization.format_version
    ctx.schema_format_policy = serialization.policy
    ctx.schema_verbose_chars = serialization.verbose_chars
    ctx.schema_compact_chars = serialization.compact_chars
    ctx.schema_compact_savings_ratio = serialization.compact_savings_ratio
    ctx.schema_compact_fallback = serialization.compact_fallback
    presenter.schema_loaded(
        source="semantic layer" if source == SchemaSource.SEMANTIC else "database",
        table_count=len(layer.tables),
    )
    return ctx


def _is_complete(layer) -> bool:
    """
    Check if semantic layer has enough type information.

    A layer is complete if >50% of tables have at least one column with data_type set.
    """
    if not layer.tables:
        return False

    tables_with_types = sum(
        1
        for table in layer.tables.values()
        if any(col.data_type for col in table.columns.values())
    )

    return tables_with_types > len(layer.tables) / 2


def _declared_primary_key_columns(table) -> frozenset[str]:
    """Return complete primary-index membership without inferring uniqueness."""
    primary_indexes = [
        index for index in table.indexes.values() if index.is_primary is True
    ]
    if len(primary_indexes) != 1:
        return frozenset()
    columns = primary_indexes[0].columns
    if (
        not isinstance(columns, list)
        or not columns
        or any(
            not isinstance(name, str) or name not in table.columns for name in columns
        )
        or len(set(columns)) != len(columns)
    ):
        return frozenset()
    return frozenset(columns)


def _build_schema_info_from_semantic(layer, target: str, db_type: str) -> SchemaInfo:
    """Build SchemaInfo from semantic layer."""
    schema_info = SchemaInfo(
        target=target, db_type=db_type, source=SchemaSource.SEMANTIC
    )

    for table_name, table in layer.tables.items():
        primary_key_columns = _declared_primary_key_columns(table)
        table_info = TableInfo(
            name=table_name,
            description=table.description,
            business_context=table.business_context,
            relationships=list(table.relationships),
        )

        for col_name, col in table.columns.items():
            table_info.columns[col_name] = ColumnInfo(
                name=col_name,
                data_type=col.data_type or "unknown",
                description=col.description,
                is_primary_key=col_name in primary_key_columns,
            )

        schema_info.tables[table_name] = table_info

    # Copy terminology from semantic layer for Tier 1 matching
    if hasattr(layer, "terminology") and layer.terminology:
        schema_info.terminology = layer.terminology

    return schema_info

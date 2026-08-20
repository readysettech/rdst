"""
Phase 1: Schema Loading

Loads the stored semantic layer or initializes one from the database.
Populates context with schema information for SQL generation.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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


def _build_schema_info_from_semantic(layer, target: str, db_type: str) -> SchemaInfo:
    """Build SchemaInfo from semantic layer."""
    schema_info = SchemaInfo(
        target=target, db_type=db_type, source=SchemaSource.SEMANTIC
    )

    for table_name, table in layer.tables.items():
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
                is_primary_key=col_name.lower() == "id",  # Simple heuristic
            )

        schema_info.tables[table_name] = table_info

    # Copy terminology from semantic layer for Tier 1 matching
    if hasattr(layer, "terminology") and layer.terminology:
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
        if table.business_context:
            parts.append(f"  Business context: {table.business_context}")

        # Columns
        col_strs = []
        for col_name, col in table.columns.items():
            col_str = f"  {col_name}"
            if col.data_type:
                col_str += f" ({col.data_type})"
            if col.description:
                col_str += f" -- {col.description}"
            if col.enum_values:
                enum_preview = ", ".join(
                    f"{value}={meaning}"
                    if meaning
                    and meaning != str(value)
                    and not meaning.startswith("TODO:")
                    else str(value)
                    for value, meaning in col.enum_values.items()
                )
                col_str += f" [enum: {enum_preview}]"
            if col.value_pattern:
                col_str += f" [pattern: {col.value_pattern}]"
            if col.null_fraction is not None and col.null_fraction > 0.05:
                col_str += f" [null: {col.null_fraction:.0%}]"
            if col.distinct_count is not None:
                col_str += f" [distinct: {col.distinct_count:,}]"
            col_strs.append(col_str)

        parts.append("\n".join(col_strs))
        if table.relationships:
            parts.append("  Relationships:")
            for relationship in table.relationships:
                parts.append(
                    f"    {relationship.relationship_type} to "
                    f"{relationship.target_table}: {relationship.join_pattern}"
                )
        parts.append("")  # Blank line between tables

    # Add extensions and custom types context if available
    extensions_context = layer.get_extensions_context()
    if extensions_context:
        parts.append(extensions_context)

    return "\n".join(parts)


VERBOSE_SCHEMA_FORMAT_VERSION = "rdst-verbose-schema-v1"
COMPACT_SCHEMA_FORMAT_VERSION = "rdst-compact-schema-v2"
ADAPTIVE_SCHEMA_FORMAT_VERSION = "rdst-adaptive-schema-v2"
ADAPTIVE_SCHEMA_MIN_SAVINGS_PERCENT = 15
ADAPTIVE_SCHEMA_MIN_SAVINGS_RATIO = ADAPTIVE_SCHEMA_MIN_SAVINGS_PERCENT / 100
_COMPACT_SCHEMA_CODE_ALPHABET = (
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)
_SQL_IDENTIFIER = r'(?:"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_$]*)'
_CANONICAL_RELATIONSHIP = re.compile(
    rf"^\s*(?P<source_table>{_SQL_IDENTIFIER})\."
    rf"(?P<source_column>{_SQL_IDENTIFIER})\s*=\s*"
    rf"(?P<target_table>{_SQL_IDENTIFIER})\."
    rf"(?P<target_column>{_SQL_IDENTIFIER})\s*$"
)


def _compact_schema_escape(value: Any) -> str:
    """Escape compact-schema delimiters without losing identifier or prose text."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace(":", "\\:")
    )


def _compact_schema_code(index: int) -> str:
    """Return a deterministic short code for a zero-based dictionary index."""
    if index < 0:
        raise ValueError("compact schema code index must be non-negative")
    base = len(_COMPACT_SCHEMA_CODE_ALPHABET)
    if index == 0:
        return _COMPACT_SCHEMA_CODE_ALPHABET[0]
    result = ""
    while index:
        index, remainder = divmod(index, base)
        result = _COMPACT_SCHEMA_CODE_ALPHABET[remainder] + result
    return result


def _unquote_sql_identifier(identifier: str) -> str:
    if identifier.startswith('"') and identifier.endswith('"'):
        return identifier[1:-1].replace('""', '"')
    return identifier


def _compact_relationship_line(table_name: str, relationship: Any) -> str:
    """Compact a canonical foreign key, falling back to its complete prompt form."""
    match = _CANONICAL_RELATIONSHIP.fullmatch(relationship.join_pattern)
    if match:
        source_table = _unquote_sql_identifier(match.group("source_table"))
        source_column = _unquote_sql_identifier(match.group("source_column"))
        target_table = _unquote_sql_identifier(match.group("target_table"))
        target_column = _unquote_sql_identifier(match.group("target_column"))
        if source_table == table_name and target_table == relationship.target_table:
            fields = [
                f">{_compact_schema_escape(source_column)}",
                _compact_schema_escape(target_table),
            ]
            if target_column != "id" or relationship.relationship_type != "many_to_one":
                fields.append(_compact_schema_escape(target_column))
            if relationship.relationship_type != "many_to_one":
                fields.append(_compact_schema_escape(relationship.relationship_type))
            return "\t".join(fields)

    return "\t".join(
        [
            ">!",
            _compact_schema_escape(relationship.relationship_type),
            _compact_schema_escape(relationship.target_table),
            _compact_schema_escape(relationship.join_pattern),
        ]
    )


def format_semantic_schema_compact(layer) -> str:
    """Serialize the complete effective Ask schema in a compact escaped grammar.

    This retains every semantic field exposed by ``_format_semantic_schema`` while
    removing repeated prose labels, common types, and redundant foreign-key syntax.
    The most frequent type is implicit; the rest use deterministic short codes.
    ``@`` starts a table and its tab-separated columns. ``>`` starts a canonical
    relationship relative to that table. ``>!`` preserves a relationship that cannot
    be normalized safely. Backslash escapes delimiters inside values.
    """
    type_counts = Counter(
        col.data_type or ""
        for table in layer.tables.values()
        for col in table.columns.values()
    )
    ordered_types = sorted(
        type_counts,
        key=lambda data_type: (-type_counts[data_type], data_type),
    )
    default_type = ordered_types[0] if ordered_types else ""
    type_codes = {
        data_type: _compact_schema_code(index)
        for index, data_type in enumerate(ordered_types[1:])
    }
    lines = [
        (
            f"{COMPACT_SCHEMA_FORMAT_VERSION}; @table<TAB>columns; "
            "column or column:type-code, bare column uses !default_type and !type "
            "defines codes; # table metadata; +column metadata; metadata tags "
            "d=description,b=business context,e=enum,p=pattern,n=null %,k=distinct; "
            ">source-column<TAB>target-table<TAB>optional-target-column"
            "<TAB>optional-relationship-type; >!<TAB>type<TAB>target<TAB>join "
            "preserves full relationship; "
            "default target column=id and relationship=many_to_one"
        ),
        f"!default_type\t{_compact_schema_escape(default_type)}",
    ]
    lines.extend(
        f"!type\t{code}\t{_compact_schema_escape(data_type)}"
        for data_type, code in type_codes.items()
    )
    for table_name, table in layer.tables.items():
        table_parts = [f"@{_compact_schema_escape(table_name)}"]
        column_metadata: list[str] = []
        for col_name, col in table.columns.items():
            encoded_name = _compact_schema_escape(col_name)
            data_type = col.data_type or ""
            if data_type == default_type:
                table_parts.append(encoded_name)
            else:
                table_parts.append(f"{encoded_name}:{type_codes[data_type]}")

            metadata = [f"+{encoded_name}"]
            if col.description:
                metadata.append(f"d={_compact_schema_escape(col.description)}")
            if col.enum_values:
                enum_values = [
                    [value, meaning]
                    if meaning
                    and meaning != str(value)
                    and not meaning.startswith("TODO:")
                    else value
                    for value, meaning in col.enum_values.items()
                ]
                metadata.append(
                    "e="
                    + json.dumps(enum_values, ensure_ascii=False, separators=(",", ":"))
                )
            if col.value_pattern:
                metadata.append(f"p={_compact_schema_escape(col.value_pattern)}")
            if col.null_fraction is not None and col.null_fraction > 0.05:
                metadata.append(f"n={col.null_fraction:.0%}")
            if col.distinct_count is not None:
                metadata.append(f"k={col.distinct_count:,}")
            if len(metadata) > 1:
                column_metadata.append("\t".join(metadata))
        lines.append("\t".join(table_parts))

        table_metadata = ["#"]
        if table.description:
            table_metadata.append(f"d={_compact_schema_escape(table.description)}")
        if table.business_context:
            table_metadata.append(f"b={_compact_schema_escape(table.business_context)}")
        if len(table_metadata) > 1:
            lines.append("\t".join(table_metadata))
        lines.extend(column_metadata)

        if table.relationships:
            lines.extend(
                _compact_relationship_line(table_name, relationship)
                for relationship in table.relationships
            )

    extensions_context = layer.get_extensions_context()
    if extensions_context:
        lines.append(
            "!database_type_context="
            + json.dumps(extensions_context, ensure_ascii=False)
        )
    return "\n".join(lines)


@dataclass(frozen=True)
class SemanticSchemaSerialization:
    """Selected semantic schema text and the measurements behind the choice."""

    formatted: str
    format_version: str
    policy: str
    verbose_chars: int
    compact_chars: int
    compact_savings_ratio: float
    compact_fallback: str = ""


def _choose_semantic_schema_text(
    verbose: str,
    compact: str,
) -> tuple[str, str]:
    """Use compact only when it saves strictly more than the 15% cutoff."""
    if len(compact) * 100 < len(verbose) * (100 - ADAPTIVE_SCHEMA_MIN_SAVINGS_PERCENT):
        return compact, COMPACT_SCHEMA_FORMAT_VERSION
    return verbose, VERBOSE_SCHEMA_FORMAT_VERSION


def select_semantic_schema_serialization(
    layer,
    *,
    forced_formatter: Callable[[Any], str] | None = None,
) -> SemanticSchemaSerialization:
    """Serialize a real semantic layer and select the measured smaller format."""
    verbose = _format_semantic_schema(layer)
    compact = format_semantic_schema_compact(layer)
    compact_savings_ratio = (
        (len(verbose) - len(compact)) / len(verbose) if verbose else 0.0
    )

    if forced_formatter is None or forced_formatter is format_semantic_schema_adaptive:
        formatted, format_version = _choose_semantic_schema_text(verbose, compact)
        policy = ADAPTIVE_SCHEMA_FORMAT_VERSION
        compact_fallback = (
            compact if format_version == VERBOSE_SCHEMA_FORMAT_VERSION else ""
        )
    elif forced_formatter is _format_semantic_schema:
        formatted = verbose
        format_version = VERBOSE_SCHEMA_FORMAT_VERSION
        policy = "diagnostic-forced"
        compact_fallback = ""
    elif forced_formatter is format_semantic_schema_compact:
        formatted = compact
        format_version = COMPACT_SCHEMA_FORMAT_VERSION
        policy = "diagnostic-forced"
        compact_fallback = ""
    else:
        formatted = forced_formatter(layer)
        format_version = "diagnostic-custom"
        policy = "diagnostic-forced"
        compact_fallback = ""

    return SemanticSchemaSerialization(
        formatted=formatted,
        format_version=format_version,
        policy=policy,
        verbose_chars=len(verbose),
        compact_chars=len(compact),
        compact_savings_ratio=compact_savings_ratio,
        compact_fallback=compact_fallback,
    )


def format_semantic_schema_adaptive(layer) -> str:
    """Return verbose or compact schema text using the measured 15% cutoff."""
    return select_semantic_schema_serialization(layer).formatted

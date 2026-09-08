"""Complete semantic schema serialization shared by Ask and Analyze."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


def _format_semantic_schema(layer) -> str:
    """
    Format semantic layer as schema string for LLM prompt.

    Includes table descriptions, column types, enum values, and extension info.
    """
    parts = []

    for table_name, table in sorted(layer.tables.items()):
        # Table header with description
        if table.description:
            parts.append(f"Table: {table_name} -- {table.description}")
        else:
            parts.append(f"Table: {table_name}")
        if table.business_context:
            parts.append(f"  Business context: {table.business_context}")

        # Columns
        col_strs = []
        for col_name, col in sorted(table.columns.items()):
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
                    for value, meaning in sorted(col.enum_values.items())
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
    for table_name, table in sorted(layer.tables.items()):
        table_parts = [f"@{_compact_schema_escape(table_name)}"]
        column_metadata: list[str] = []
        for col_name, col in sorted(table.columns.items()):
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
                    for value, meaning in sorted(col.enum_values.items())
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

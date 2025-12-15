"""
Phase 3.5: Schema Expansion

Expands the filtered schema when LLM signals insufficiency.
Uses LLM's missing_concepts and requested_tables hints to find additional tables.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Set, List

if TYPE_CHECKING:
    from ..context import Ask3Context
    from ..presenter import Ask3Presenter

logger = logging.getLogger(__name__)


def expand_schema(
    ctx: 'Ask3Context',
    presenter: 'Ask3Presenter',
    missing_concepts: List[str],
    requested_tables: List[str]
) -> 'Ask3Context':
    """
    Expand filtered schema based on LLM feedback.

    Strategy:
    1. Direct match: Add any requested_tables that exist in schema
    2. Concept match: Use missing_concepts keywords to find related tables
    3. FK expansion: Expand via relationships from newly added tables

    Args:
        ctx: Context with current filtered schema
        presenter: For progress output
        missing_concepts: LLM's description of needed data concepts
        requested_tables: LLM's guesses at table names

    Returns:
        Updated context with expanded schema
    """
    ctx.phase = 'expand'

    if not ctx.schema_info or not ctx.all_available_tables:
        logger.warning("No schema_info available for expansion")
        return ctx

    current_tables = set(ctx.filtered_tables)
    new_tables: Set[str] = set()

    # Strategy 1: Direct table name matches
    for requested in requested_tables:
        for available in ctx.all_available_tables:
            if _tables_match(requested, available):
                new_tables.add(available)
                logger.debug(f"Direct match: '{requested}' -> '{available}'")

    # Strategy 2: Concept-based matching
    for concept in missing_concepts:
        matched = _match_concept_to_tables(concept, ctx.schema_info)
        if matched:
            logger.debug(f"Concept '{concept}' matched tables: {matched}")
        new_tables.update(matched)

    # Strategy 3: FK expansion of new tables
    if new_tables:
        from .filter import _expand_via_fk_relationships
        expanded = _expand_via_fk_relationships(new_tables, ctx.schema_info)
        fk_additions = expanded - new_tables - current_tables
        if fk_additions:
            logger.debug(f"FK expansion added: {fk_additions}")
        new_tables.update(expanded)

    # Remove already-included tables
    new_tables = new_tables - current_tables

    if new_tables:
        # Update filtered tables
        ctx.filtered_tables = list(current_tables | new_tables)

        # Rebuild schema_formatted with new tables
        ctx.schema_formatted = _rebuild_schema_for_tables(
            ctx.schema_info,
            ctx.filtered_tables
        )

        ctx.increment_expansion()

        presenter.schema_expanded(
            added=list(new_tables),
            total=len(ctx.filtered_tables)
        )

        logger.info(f"Schema expanded: +{len(new_tables)} tables ({new_tables})")
    else:
        presenter.info("No additional tables found for expansion")
        logger.info("Schema expansion found no new tables")

    return ctx


def _tables_match(requested: str, available: str) -> bool:
    """
    Check if requested table name matches available table.

    Handles exact match, singular/plural, and substring containment.
    """
    req_lower = requested.lower().strip()
    avail_lower = available.lower()

    # Exact match
    if req_lower == avail_lower:
        return True

    # Singular/plural variants
    if req_lower + 's' == avail_lower:
        return True
    if req_lower.endswith('s') and req_lower[:-1] == avail_lower:
        return True

    # Substring containment (e.g., "vote" matches "votes", "postvotes")
    if len(req_lower) >= 4:  # Only for meaningful substrings
        if req_lower in avail_lower:
            return True

    return False


def _match_concept_to_tables(concept: str, schema_info) -> Set[str]:
    """
    Match a concept description to table names via keyword extraction.

    Example: "voting records" -> keywords ["voting", "vote", "record"] -> matches "votes"
    """
    tables: Set[str] = set()
    concept_lower = concept.lower()

    # Extract keywords from concept (words 3+ chars)
    keywords = set(re.findall(r'\b[a-z]{3,}\b', concept_lower))

    # Add stemmed variants
    stemmed = set()
    for kw in keywords:
        # Remove common suffixes
        if kw.endswith('ing') and len(kw) > 5:
            stemmed.add(kw[:-3])
        if kw.endswith('ed') and len(kw) > 4:
            stemmed.add(kw[:-2])
        if kw.endswith('s') and not kw.endswith('ss') and len(kw) > 3:
            stemmed.add(kw[:-1])
        # Add plural
        stemmed.add(kw + 's')
    keywords.update(stemmed)

    # Match against table names
    for table_name in schema_info.tables.keys():
        table_lower = table_name.lower()
        for kw in keywords:
            if kw in table_lower or table_lower in kw:
                tables.add(table_name)
                break

    # Match against column names (table contains relevant column)
    for table_name, table_info in schema_info.tables.items():
        if table_name in tables:
            continue  # Already matched

        if hasattr(table_info, 'columns') and table_info.columns:
            for col_name in table_info.columns.keys():
                col_lower = col_name.lower()
                for kw in keywords:
                    if kw in col_lower:
                        tables.add(table_name)
                        break

    return tables


def _rebuild_schema_for_tables(schema_info, table_names: List[str]) -> str:
    """
    Rebuild schema text for specified tables from SchemaInfo.

    Args:
        schema_info: Full schema information
        table_names: List of table names to include

    Returns:
        Formatted schema string for LLM prompt
    """
    parts = []
    table_names_lower = {t.lower() for t in table_names}

    for table_name, table_info in schema_info.tables.items():
        if table_name.lower() not in table_names_lower:
            continue

        # Table header with description
        if table_info.description:
            parts.append(f"Table: {table_name} -- {table_info.description}")
        else:
            parts.append(f"Table: {table_name}")

        # Columns
        col_strs = []
        if hasattr(table_info, 'columns') and table_info.columns:
            for col_name, col_info in table_info.columns.items():
                col_str = f"  {col_name}"
                if hasattr(col_info, 'data_type') and col_info.data_type:
                    col_str += f" ({col_info.data_type})"
                if hasattr(col_info, 'description') and col_info.description:
                    col_str += f" -- {col_info.description}"
                col_strs.append(col_str)

        if col_strs:
            parts.append("\n".join(col_strs))
        parts.append("")  # Blank line between tables

    return "\n".join(parts)

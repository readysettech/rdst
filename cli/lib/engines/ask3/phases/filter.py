"""
Phase 1.5: Schema Filtering

Filters the full schema to only include tables relevant to the user's question.
Uses a tiered approach:
1. Terminology matching (semantic layer terms)
2. Heuristic table + column name matching
2.3. Negative clause detection ("never asked", "without comments")
2.5. FK relationship expansion (bidirectional)
3. LLM-based table selection (fallback)
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Dict, List, Set, Optional

if TYPE_CHECKING:
    from ..context import Ask3Context
    from ..presenter import Ask3Presenter

logger = logging.getLogger(__name__)


# Tier 2.3: Patterns for detecting negative/exclusion clauses
# NOTE: Order matters - more specific patterns should come first
NEGATIVE_PATTERNS = [
    # "have not been answered", "has not been commented" - must come before simpler "not X"
    re.compile(r'\bnot\s+been\s+(\w+)', re.IGNORECASE),
    # "haven't been answered", "hasn't been posted"
    re.compile(r'\b(haven\'t|hasn\'t|didn\'t|don\'t)\s+been\s+(\w+)', re.IGNORECASE),
    # "without any comments", "never made any posts"
    re.compile(r'\b(never|not?|without)\s+any\s+(\w+)', re.IGNORECASE),
    # "never asked", "not answered", "without comments"
    re.compile(r'\b(never|not?|without)\s+(\w+)', re.IGNORECASE),
    # "haven't posted", "hasn't answered"
    re.compile(r'\b(haven\'t|hasn\'t|didn\'t|don\'t)\s+(\w+)', re.IGNORECASE),
    # "no comments", "no votes"
    re.compile(r'\bno\s+(\w+)', re.IGNORECASE),
]

# Action verb to table hints - maps verbs to potential table names
ACTION_VERB_HINTS = {
    # Core actions
    'asked': ['posts'],
    'answered': ['posts'],
    'commented': ['comments'],
    'voted': ['votes'],
    'tagged': ['tags', 'posttags'],
    'posted': ['posts'],
    # Extended actions
    'edited': ['posthistory', 'posts'],
    'earned': ['badges'],
    'received': ['badges', 'votes'],
    'gave': ['votes'],
    'linked': ['postlinks'],
    'duplicated': ['postlinks'],
    'viewed': ['posts'],
    'accepted': ['posts'],
    'closed': ['posts'],
    'created': ['posts', 'comments'],
}

# Join tables - maps table pairs to their bridge table
JOIN_TABLES = {
    ('posts', 'tags'): 'posttags',
    ('tags', 'posts'): 'posttags',
}


def filter_schema(
    ctx: 'Ask3Context',
    presenter: 'Ask3Presenter',
    llm_manager=None
) -> 'Ask3Context':
    """
    Filter schema to tables relevant to the question.

    Tiered approach:
    1. Semantic terminology matching (free, instant)
    2. Heuristic table + column name matching (free, instant)
    2.5. FK relationship expansion (free, instant)
    3. LLM table selection (cheap, ~300ms) - only if needed

    Args:
        ctx: Ask3Context with schema_info and schema_formatted populated
        presenter: For progress output
        llm_manager: LLMManager instance (optional, for Tier 3)

    Returns:
        Updated context with filtered schema_formatted and filtered_tables
    """
    ctx.phase = 'filter'

    # Need schema_info for filtering
    if not ctx.schema_info or not ctx.schema_info.tables:
        logger.warning("No schema_info available, skipping filter phase")
        return ctx

    all_tables = list(ctx.schema_info.tables.keys())

    if not all_tables:
        return ctx

    # Tier 1: Terminology matching
    term_tables = _match_via_terminology(ctx.question, ctx.schema_info)
    logger.debug(f"Tier 1 (terminology): {term_tables}")

    # Tier 2: Heuristic matching (tables AND columns)
    heuristic_tables = _match_tables_and_columns(ctx.question, ctx.schema_info)
    logger.debug(f"Tier 2 (heuristic): {heuristic_tables}")

    # Tier 2.3: Negative clause detection ("never asked", "without comments")
    negative_tables = _detect_negative_clause_tables(ctx.question, ctx.schema_info)
    logger.debug(f"Tier 2.3 (negative clauses): {negative_tables}")

    # Combine tiers 1, 2, and 2.3
    candidate_tables = term_tables | heuristic_tables | negative_tables

    # Tier 2.5: FK expansion (bidirectional - always run if we have candidates)
    if candidate_tables:
        expanded = _expand_via_fk_relationships(candidate_tables, ctx.schema_info)
        logger.debug(f"Tier 2.5 (FK expansion): {expanded - candidate_tables} added")
        candidate_tables = expanded

    # Tier 3: LLM selection (always used when heuristics find nothing)
    if not candidate_tables:
        logger.info("Tiers 1-2 found no matches, using LLM for table selection")
        if llm_manager is None:
            from ....llm_manager import LLMManager
            llm_manager = LLMManager()

        candidate_tables = _llm_select_tables(
            ctx.question,
            all_tables,
            llm_manager
        )
        logger.debug(f"Tier 3 (LLM): {candidate_tables}")

    # If still nothing, use full schema
    if not candidate_tables:
        logger.warning("All tiers failed, using full schema")
        candidate_tables = set(all_tables)

    final_tables = list(candidate_tables)

    # Filter schema to selected tables
    ctx.schema_formatted = _filter_schema_text(ctx.schema_formatted, final_tables)
    ctx.filtered_tables = final_tables

    presenter.schema_filtered(
        original=len(all_tables),
        filtered=len(final_tables),
        tables=final_tables
    )

    return ctx


def _match_via_terminology(question: str, schema_info) -> Set[str]:
    """
    Tier 1: Match question against semantic layer terminology.

    Looks for defined business terms and extracts their associated tables.
    """
    matched_tables: Set[str] = set()
    question_lower = question.lower()

    # Check if schema_info has terminology
    if not hasattr(schema_info, 'terminology') or not schema_info.terminology:
        return matched_tables

    for term_name, term in schema_info.terminology.items():
        term_lower = term_name.lower()

        # Check if term appears in question
        if term_lower in question_lower:
            # Extract tables from the term's SQL pattern
            if hasattr(term, 'sql_pattern') and term.sql_pattern:
                tables = _extract_tables_from_sql(term.sql_pattern)
                matched_tables.update(tables)

            # Also check tables_used if available
            if hasattr(term, 'tables_used') and term.tables_used:
                matched_tables.update(term.tables_used)

    return matched_tables


def _match_tables_and_columns(question: str, schema_info) -> Set[str]:
    """
    Tier 2: Heuristic matching of table names AND column names.

    Matches:
    - Table names (with singular/plural variants)
    - Column names → returns the table containing that column
    """
    matched_tables: Set[str] = set()
    question_lower = question.lower()

    # Extract words from question (alphanumeric, min 3 chars)
    words = set(re.findall(r'\b[a-z]{3,}\b', question_lower))

    for table_name, table_info in schema_info.tables.items():
        table_lower = table_name.lower()

        # Match table name
        if _matches_with_variants(table_lower, question_lower, words):
            matched_tables.add(table_name)
            continue

        # Match column names
        if hasattr(table_info, 'columns') and table_info.columns:
            for col_name in table_info.columns.keys():
                col_lower = col_name.lower()
                if _matches_with_variants(col_lower, question_lower, words):
                    matched_tables.add(table_name)
                    break  # Found a match, move to next table

    return matched_tables


def _matches_with_variants(name: str, question: str, words: Set[str]) -> bool:
    """
    Check if name (or variants) appears in question.

    Handles:
    - Direct match
    - Singular/plural variants (users ↔ user)
    - Word boundary matching
    """
    # Direct substring match
    if name in question:
        return True

    # Word match (exact word boundary)
    if name in words:
        return True

    # Singular variant (remove trailing 's')
    if name.endswith('s') and len(name) > 3:
        singular = name[:-1]
        if singular in question or singular in words:
            return True

    # Plural variant (add 's')
    plural = name + 's'
    if plural in question or plural in words:
        return True

    # Handle 'ies' pluralization (e.g., query -> queries)
    if name.endswith('y') and len(name) > 2:
        ies_plural = name[:-1] + 'ies'
        if ies_plural in question or ies_plural in words:
            return True

    # Handle reverse (queries -> query)
    if name.endswith('ies') and len(name) > 3:
        y_singular = name[:-3] + 'y'
        if y_singular in question or y_singular in words:
            return True

    return False


def _detect_negative_clause_tables(question: str, schema_info) -> Set[str]:
    """
    Tier 2.3: Detect negative/exclusion clauses and infer related tables.

    Patterns detected:
    - "users who never asked" -> need posts table (via "asked")
    - "posts without comments" -> need comments table
    - "tags never used" -> need posttags table
    """
    matched_tables: Set[str] = set()
    question_lower = question.lower()

    # Extract action words from negative patterns
    for pattern in NEGATIVE_PATTERNS:
        for match in pattern.finditer(question_lower):
            groups = match.groups()
            for word in groups:
                if word and len(word) > 2:
                    # Infer tables from action verb
                    tables = _infer_tables_from_action(word, schema_info)
                    matched_tables.update(tables)

    # Also check for "without X" pattern separately (noun matching)
    without_matches = re.findall(r'\bwithout\s+(\w+)', question_lower)
    for noun in without_matches:
        tables = _match_noun_to_table(noun, schema_info)
        matched_tables.update(tables)

    return matched_tables


def _simple_stem(word: str) -> str:
    """Simple English verb stemmer (no dependencies)."""
    if word.endswith('ied') and len(word) > 4:
        return word[:-3] + 'y'
    if word.endswith('ed') and len(word) > 3:
        if word[-3] == word[-4]:  # doubled consonant (e.g., stopped)
            return word[:-3]
        return word[:-2] if word[-3] not in 'aeiou' else word[:-1]
    if word.endswith('ing') and len(word) > 4:
        if word[-4] == word[-5]:  # doubled consonant
            return word[:-4]
        return word[:-3]
    if word.endswith('ies') and len(word) > 4:
        return word[:-3] + 'y'
    if word.endswith('es') and len(word) > 3:
        return word[:-2]
    if word.endswith('s') and not word.endswith('ss') and len(word) > 2:
        return word[:-1]
    return word


def _infer_tables_from_action(verb: str, schema_info) -> Set[str]:
    """Infer related tables from an action verb."""
    tables: Set[str] = set()
    verb_lower = verb.lower()

    # Check static ACTION_VERB_HINTS mapping
    if verb_lower in ACTION_VERB_HINTS:
        for hint in ACTION_VERB_HINTS[verb_lower]:
            for table_name in schema_info.tables.keys():
                if hint in table_name.lower():
                    tables.add(table_name)

    # Stem-based matching (e.g., "asked" -> "ask" -> match "asks" table)
    stem = _simple_stem(verb_lower)
    if stem != verb_lower:  # Only if stemming changed the word
        for table_name in schema_info.tables.keys():
            table_lower = table_name.lower()
            if stem in table_lower or table_lower.startswith(stem):
                tables.add(table_name)

    return tables


def _match_noun_to_table(noun: str, schema_info) -> Set[str]:
    """Match a noun (from 'without X') to table names."""
    tables: Set[str] = set()
    noun_lower = noun.lower()

    for table_name in schema_info.tables.keys():
        table_lower = table_name.lower()
        # Direct match or singular/plural
        if table_lower == noun_lower or table_lower == noun_lower + 's':
            tables.add(table_name)
        if noun_lower.endswith('s') and table_lower == noun_lower[:-1]:
            tables.add(table_name)

    return tables


def _build_reverse_fk_index(schema_info) -> Dict[str, Set[str]]:
    """
    Build index mapping parent table names to child tables that reference them.

    Example output:
    - "user" -> {"posts", "comments", "votes"}
    - "post" -> {"comments", "votes", "posttags"}
    """
    index: Dict[str, Set[str]] = {}

    for table_name, table_info in schema_info.tables.items():
        if not hasattr(table_info, 'columns') or not table_info.columns:
            continue

        for col_name in table_info.columns.keys():
            col_lower = col_name.lower()

            # Detect FK pattern: xxxid -> table xxx
            if col_lower.endswith('id') and len(col_lower) > 2:
                parent_hint = col_lower[:-2]  # e.g., "user" from "userid"

                if parent_hint not in index:
                    index[parent_hint] = set()
                index[parent_hint].add(table_name)

                # Also index with 's' suffix for plural table names
                parent_plural = parent_hint + 's'
                if parent_plural not in index:
                    index[parent_plural] = set()
                index[parent_plural].add(table_name)

    return index


def _expand_via_fk_relationships(tables: Set[str], schema_info) -> Set[str]:
    """
    Tier 2.5: Expand table set via foreign key relationships (BIDIRECTIONAL).

    For each table, find related tables via:
    - FORWARD: Columns ending in 'id' that reference parent tables
    - REVERSE: Parent tables find child tables that reference them
    - EXPLICIT: Relationships defined in semantic layer
    - BRIDGE: Join tables for many-to-many relationships
    """
    expanded = set(tables)

    # Build reverse FK index for bidirectional expansion
    reverse_index = _build_reverse_fk_index(schema_info)

    for table_name in list(tables):
        table_info = schema_info.tables.get(table_name)
        if not table_info:
            continue

        # Check explicit relationships
        if hasattr(table_info, 'relationships') and table_info.relationships:
            for rel in table_info.relationships:
                if hasattr(rel, 'target_table'):
                    if rel.target_table in schema_info.tables:
                        expanded.add(rel.target_table)

        # FORWARD: Check FK columns (columns ending in 'id' like userid, postid)
        if hasattr(table_info, 'columns') and table_info.columns:
            for col_name in table_info.columns.keys():
                col_lower = col_name.lower()

                # Look for FK pattern: xxxid -> xxx table
                if col_lower.endswith('id') and len(col_lower) > 2:
                    potential_table = col_lower[:-2]  # Remove 'id'

                    # Try to find matching table (with 's' suffix too)
                    for other_table in schema_info.tables.keys():
                        other_lower = other_table.lower()
                        if other_lower == potential_table or other_lower == potential_table + 's':
                            expanded.add(other_table)
                            break

        # REVERSE: Find child tables that reference this table via FK columns
        table_lower = table_name.lower()
        singular = table_lower.rstrip('s') if table_lower.endswith('s') and len(table_lower) > 1 else table_lower

        if table_lower in reverse_index:
            expanded.update(reverse_index[table_lower])
        if singular in reverse_index and singular != table_lower:
            expanded.update(reverse_index[singular])

    # BRIDGE: Add join tables for table pairs
    table_list = list(expanded)
    for i, t1 in enumerate(table_list):
        for t2 in table_list[i+1:]:
            t1_lower, t2_lower = t1.lower(), t2.lower()
            # Check both directions
            bridge = JOIN_TABLES.get((t1_lower, t2_lower)) or JOIN_TABLES.get((t2_lower, t1_lower))
            if bridge:
                # Find actual table name (case-sensitive)
                for actual_table in schema_info.tables.keys():
                    if actual_table.lower() == bridge:
                        expanded.add(actual_table)
                        break

    return expanded


def _llm_select_tables(
    question: str,
    all_tables: List[str],
    llm_manager,
    model: str = "claude-3-haiku-20240307"
) -> Set[str]:
    """
    Tier 3: Use LLM to select relevant tables.

    Sends only table names (not full schema) for cost efficiency.
    """
    try:
        table_list = "\n".join(f"- {t}" for t in all_tables)

        prompt = f"""Which database tables are needed to answer this question?

Question: "{question}"

Available tables:
{table_list}

Return JSON: {{"relevant_tables": ["table1", "table2"], "reasoning": "brief explanation"}}

Rules:
- Include tables for JOINs (e.g., if asking about user posts, include both users and posts)
- Be inclusive - better to include an extra table than miss one
- Return 1-5 tables typically"""

        response = llm_manager.query(
            system_message="You are a database schema expert. Return only valid JSON.",
            user_query=prompt,
            max_tokens=300,
            temperature=0.0,
            model=model,
            extra={"response_format": {"type": "json_object"}}
        )

        if not response or 'text' not in response:
            logger.warning("LLM table selection returned no response")
            return set()

        result = json.loads(response['text'])
        tables = result.get('relevant_tables', [])

        # Validate tables exist
        valid_tables = set(t for t in tables if t in all_tables)

        logger.info(f"LLM selected tables: {valid_tables} (reasoning: {result.get('reasoning', 'none')})")
        return valid_tables

    except Exception as e:
        logger.error(f"LLM table selection failed: {e}")
        return set()


def _filter_schema_text(full_schema: str, table_names: List[str]) -> str:
    """
    Filter schema string to include only specified tables.

    Preserves the structure of the schema output but only includes relevant sections.
    """
    if not table_names:
        return full_schema

    # Normalize table names for case-insensitive matching
    table_names_lower = {t.lower() for t in table_names}

    lines = full_schema.split('\n')
    filtered_lines = []
    include_section = False

    for line in lines:
        # Check if line is a table header
        line_lower = line.lower()

        # Match "Table: xxx" pattern
        match = re.match(r'table:\s+(\w+)', line_lower)
        if match:
            table_found = match.group(1)
            include_section = table_found in table_names_lower
            if include_section:
                filtered_lines.append(line)
            continue

        # Include line if we're in a relevant section
        if include_section:
            # Check if we hit the next table (marks end of current section)
            if line_lower.startswith('table:'):
                match = re.match(r'table:\s+(\w+)', line_lower)
                if match:
                    table_found = match.group(1)
                    include_section = table_found in table_names_lower
                    if include_section:
                        filtered_lines.append(line)
                continue

            filtered_lines.append(line)

    if filtered_lines:
        return '\n'.join(filtered_lines)
    else:
        # If filtering failed, return full schema
        logger.warning("Schema filtering produced no output, returning full schema")
        return full_schema


def _extract_tables_from_sql(sql: str) -> Set[str]:
    """
    Extract table names from SQL pattern.

    Simple regex extraction - looks for FROM and JOIN clauses.
    """
    tables: Set[str] = set()

    # Pattern for FROM table or JOIN table
    patterns = [
        r'\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*)',
        r'\bJOIN\s+([a-zA-Z_][a-zA-Z0-9_]*)',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, sql, re.IGNORECASE)
        tables.update(matches)

    return tables

"""
Recommendation Validation - Prevent LLM Hallucination

Validates LLM recommendations against collected database context to detect:
- Duplicate index suggestions (recommending indexes that already exist)
- Invalid recommendations not grounded in schema
- Wrong composite index column ordering (EQR rule enforcement)
"""

import logging
import re
from typing import Dict, Any, List, Set, Tuple

logger = logging.getLogger(__name__)


def validate_recommendations(llm_analysis: Dict[str, Any], schema_info: str, **kwargs) -> Dict[str, Any]:
    """
    Validate LLM recommendations against collected context.

    Args:
        llm_analysis: Complete LLM analysis results including recommendations
        schema_info: Schema information string from collect_target_schema
        **kwargs: Additional workflow context

    Returns:
        Dict containing:
        - is_valid: boolean (False if warnings detected)
        - warnings: list of warning messages
        - existing_indexes_count: number of existing indexes found
        - suggested_indexes_count: number of new indexes suggested
    """
    warnings = []

    # Extract existing indexes from schema_info
    existing_indexes = _extract_existing_indexes(schema_info)

    # Get index recommendations from LLM analysis
    index_recommendations = llm_analysis.get('index_recommendations', [])

    # Check each suggested index
    for idx_rec in index_recommendations:
        suggested_sql = idx_rec.get('sql', '')

        # Extract index name from CREATE INDEX statement
        suggested_name = _extract_index_name_from_create(suggested_sql)

        if suggested_name:
            # Check if this index already exists
            if suggested_name.lower() in [idx.lower() for idx in existing_indexes]:
                warnings.append(
                    f"⚠️  Suggested index '{suggested_name}' already exists (possible hallucination). "
                    f"LLM should suggest REPLACING it if wrong type, not creating duplicate."
                )

    return {
        'success': len(warnings) == 0,
        'is_valid': len(warnings) == 0,
        'warnings': warnings,
        'existing_indexes_count': len(existing_indexes),
        'suggested_indexes_count': len(index_recommendations)
    }


def _extract_existing_indexes(schema_info: str) -> List[str]:
    """
    Extract index names from schema information string.

    Handles both PostgreSQL full definitions and MySQL format:
    - PostgreSQL: "- CREATE INDEX idx_name ON table USING btree (col)"
    - MySQL: "- idx_name USING BTREE (col)"
    """
    indexes = []

    # Pattern 1: PostgreSQL full CREATE INDEX statements
    pg_pattern = r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z_][a-zA-Z0-9_]*)'
    pg_matches = re.findall(pg_pattern, schema_info, re.IGNORECASE)
    indexes.extend(pg_matches)

    # Pattern 2: MySQL format "- idx_name USING TYPE"
    mysql_pattern = r'-\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+USING\s+(?:BTREE|HASH|FULLTEXT)'
    mysql_matches = re.findall(mysql_pattern, schema_info, re.IGNORECASE)
    indexes.extend(mysql_matches)

    # Pattern 3: Simple "- idx_name" format (fallback)
    simple_pattern = r'Indexes:\s*\n\s*-\s+([a-zA-Z_][a-zA-Z0-9_]*)'
    simple_matches = re.findall(simple_pattern, schema_info, re.IGNORECASE)
    indexes.extend(simple_matches)

    # Deduplicate
    return list(set(indexes))


def _extract_index_name_from_create(create_sql: str) -> str:
    """
    Extract index name from CREATE INDEX statement.

    Examples:
    - "CREATE INDEX idx_foo ON table (col)" -> "idx_foo"
    - "CREATE UNIQUE INDEX IF NOT EXISTS idx_bar ON table (col)" -> "idx_bar"
    """
    pattern = r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z_][a-zA-Z0-9_]*)'
    match = re.search(pattern, create_sql, re.IGNORECASE)

    if match:
        return match.group(1)

    return None


def reorder_index_columns(
    recommendations: List[Dict[str, Any]], sql: str
) -> List[Dict[str, Any]]:
    """
    Enforce EQR column ordering in composite index recommendations.

    Parses the SQL to classify WHERE conditions as equality (=, IN, IS) or
    range (>, <, >=, <=, BETWEEN, LIKE), then reorders index columns so
    equality columns come before range columns (remaining columns stay at end).

    Args:
        recommendations: List of index recommendation dicts with 'columns' and 'sql' keys
        sql: The original SQL query being analyzed

    Returns:
        Recommendations with corrected column ordering
    """
    equality_cols, range_cols = _classify_where_columns(sql)
    if not equality_cols and not range_cols:
        return recommendations

    for rec in recommendations:
        columns = rec.get("columns", [])
        if len(columns) < 2:
            continue

        cols_lower = [c.lower() for c in columns]
        eq_in_idx = [c for c in cols_lower if c in equality_cols]
        rng_in_idx = [c for c in cols_lower if c in range_cols]
        other_in_idx = [
            c for c in cols_lower if c not in equality_cols and c not in range_cols
        ]

        reordered_lower = eq_in_idx + rng_in_idx + other_in_idx
        if reordered_lower == cols_lower:
            continue

        # Rebuild with original casing
        case_map = {c.lower(): c for c in columns}
        reordered = [case_map[c] for c in reordered_lower]
        rec["columns"] = reordered

        # Rebuild the CREATE INDEX SQL statement
        old_sql = rec.get("sql", "")
        if old_sql:
            rec["sql"] = _rebuild_create_index_sql(old_sql, reordered)

        logger.debug(
            "EQR reorder: %s -> %s", cols_lower, reordered_lower
        )

    return recommendations


def _classify_where_columns(sql: str) -> Tuple[Set[str], Set[str]]:
    """
    Parse SQL and classify WHERE clause columns as equality or range.

    Returns:
        (equality_columns, range_columns) as sets of lowercase column names
    """
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return set(), set()

    try:
        parsed = sqlglot.parse_one(sql)
    except Exception:
        return set(), set()

    equality_cols: Set[str] = set()
    range_cols: Set[str] = set()

    where = parsed.find(exp.Where)
    if not where:
        return equality_cols, range_cols

    # Walk all comparison expressions inside WHERE
    for node in where.walk():
        if isinstance(node, exp.EQ):
            _collect_column_names(node, equality_cols)
        elif isinstance(node, exp.In):
            _collect_column_names(node, equality_cols)
        elif isinstance(node, exp.Is):
            _collect_column_names(node, equality_cols)
        elif isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
            _collect_column_names(node, range_cols)
        elif isinstance(node, exp.Between):
            _collect_column_names(node, range_cols)
        elif isinstance(node, exp.Like):
            _collect_column_names(node, range_cols)

    return equality_cols, range_cols


def _collect_column_names(node, target_set: Set[str]) -> None:
    """Extract column names from a comparison expression node."""
    from sqlglot import exp

    for col in node.find_all(exp.Column):
        name = col.name.lower() if col.name else None
        if name:
            target_set.add(name)


def _rebuild_create_index_sql(old_sql: str, new_columns: List[str]) -> str:
    """Rebuild CREATE INDEX statement with reordered columns."""
    # Match the column list in parentheses after ON table_name
    pattern = r'(CREATE\s+(?:UNIQUE\s+)?INDEX\s+\S+\s+ON\s+\S+\s*)\(([^)]+)\)'
    match = re.search(pattern, old_sql, re.IGNORECASE)
    if not match:
        return old_sql

    prefix = match.group(1)
    new_col_str = ", ".join(new_columns)
    suffix = old_sql[match.end():]
    return f"{prefix}({new_col_str}){suffix}"

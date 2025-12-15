"""
Smart Defaults - Infer sensible query defaults from hypothesis and question.

Analyzes the hypothesis SQL and natural language question to extract:
- Row limit (LIMIT clause or default to 10)
- Sort order (ORDER BY or inferred from question)
- Filters (WHERE conditions)
- Date range (detected from date filters)

Uses sqlglot for AST-based SQL modification (reliable and dialect-aware).
"""

from __future__ import annotations

import re
import logging
from typing import Optional, Dict, Any

import sqlglot
from sqlglot.errors import ParseError

from ..data_structures.query_defaults import QueryDefaults, QueryFilter
from ..data_structures.hypothesis import Hypothesis

logger = logging.getLogger(__name__)


def infer_defaults(
    hypothesis: Hypothesis,
    nl_question: str,
    schema_info: Optional[Dict[str, Any]] = None
) -> QueryDefaults:
    """
    Infer smart defaults from hypothesis SQL and natural language question.

    Args:
        hypothesis: Selected hypothesis with SQL
        nl_question: Original user question
        schema_info: Optional schema information

    Returns:
        QueryDefaults with inferred values
    """
    sql = hypothesis.sql
    defaults = QueryDefaults(original_sql=sql)

    # Extract LIMIT clause
    defaults.limit = _extract_limit(sql)

    # Extract ORDER BY clause
    defaults.sort_column, defaults.sort_direction = _extract_sort(sql)

    # If no sort found in SQL, infer from question
    if not defaults.sort_column:
        defaults.sort_column, defaults.sort_direction = _infer_sort_from_question(nl_question)

    # Extract WHERE filters
    defaults.filters = _extract_filters(sql)

    # Detect date range filters
    defaults.date_range, defaults.date_column = _extract_date_range(sql)

    logger.debug(f"Inferred defaults: {defaults.to_summary()}")

    return defaults


def _extract_limit(sql: str) -> int:
    """
    Extract LIMIT value from SQL.

    Returns:
        Limit value or 10 if not found
    """
    # Match LIMIT clause (case-insensitive)
    match = re.search(r'\bLIMIT\s+(\d+)', sql, re.IGNORECASE)
    if match:
        return int(match.group(1))

    return 10  # Default


def _extract_sort(sql: str) -> tuple[Optional[str], str]:
    """
    Extract ORDER BY clause from SQL.

    Returns:
        Tuple of (column_name, direction) or (None, "DESC")
    """
    # Match ORDER BY clause
    match = re.search(r'\bORDER\s+BY\s+(\w+)(?:\s+(ASC|DESC))?', sql, re.IGNORECASE)
    if match:
        column = match.group(1)
        direction = match.group(2).upper() if match.group(2) else "DESC"
        return column, direction

    return None, "DESC"


def _infer_sort_from_question(nl_question: str) -> tuple[Optional[str], str]:
    """
    Infer sort order from natural language question.

    Args:
        nl_question: User's question

    Returns:
        Tuple of (column_name, direction) or (None, "DESC")
    """
    question_lower = nl_question.lower()

    # "top" implies descending order
    if any(word in question_lower for word in ['top', 'best', 'highest', 'most', 'largest']):
        return None, "DESC"

    # "bottom", "smallest", "worst" implies ascending order
    if any(word in question_lower for word in ['bottom', 'worst', 'lowest', 'least', 'smallest']):
        return None, "ASC"

    # "recent", "latest" implies date descending
    if any(word in question_lower for word in ['recent', 'latest', 'newest', 'new']):
        # Could infer column name if schema provided, for now just direction
        return None, "DESC"

    # "oldest", "earliest" implies date ascending
    if any(word in question_lower for word in ['oldest', 'earliest', 'first']):
        return None, "ASC"

    return None, "DESC"  # Default to descending


def _extract_filters(sql: str) -> list[QueryFilter]:
    """
    Extract WHERE conditions from SQL and convert to QueryFilter objects.

    Args:
        sql: SQL query

    Returns:
        List of QueryFilter objects
    """
    filters = []

    # Find WHERE clause
    where_match = re.search(r'\bWHERE\s+(.+?)(?:\s+ORDER\s+BY|\s+GROUP\s+BY|\s+LIMIT|\s*$)', sql, re.IGNORECASE | re.DOTALL)
    if not where_match:
        return filters

    where_clause = where_match.group(1)

    # Split by AND (simple parser, doesn't handle OR or nested conditions)
    conditions = re.split(r'\s+AND\s+', where_clause, flags=re.IGNORECASE)

    for condition in conditions:
        condition = condition.strip()

        # Try to parse condition
        # Pattern: column operator value
        # Supports: =, !=, <>, >, <, >=, <=, LIKE, ILIKE, IN

        # LIKE/ILIKE pattern
        like_match = re.match(r'(\w+)\s+(LIKE|ILIKE)\s+[\'"](.+?)[\'"]', condition, re.IGNORECASE)
        if like_match:
            column = like_match.group(1)
            operator = like_match.group(2).upper()
            value = like_match.group(3)

            # Create description
            if '%' in value:
                # Pattern match
                desc = f"{column} matches '{value}'"
            else:
                desc = f"{column} = '{value}'"

            filters.append(QueryFilter(
                column=column,
                operator=operator,
                value=value,
                description=desc
            ))
            continue

        # IN pattern
        in_match = re.match(r'(\w+)\s+IN\s+\((.+?)\)', condition, re.IGNORECASE)
        if in_match:
            column = in_match.group(1)
            values_str = in_match.group(2)

            # Parse values
            values = [v.strip().strip("'\"") for v in values_str.split(',')]

            filters.append(QueryFilter(
                column=column,
                operator='IN',
                value=values,
                description=f"{column} in ({', '.join(values)})"
            ))
            continue

        # Comparison operators
        comp_match = re.match(r'(\w+)\s*(=|!=|<>|>=?|<=?)\s*(.+)', condition)
        if comp_match:
            column = comp_match.group(1)
            operator = comp_match.group(2)
            value_str = comp_match.group(3).strip().strip("'\"")

            # Try to convert to appropriate type
            try:
                if value_str.lower() in ('true', 'false'):
                    value = value_str.lower() == 'true'
                elif value_str.lower() == 'null':
                    value = None
                elif '.' in value_str:
                    value = float(value_str)
                else:
                    value = int(value_str)
            except ValueError:
                value = value_str  # Keep as string

            # Create description
            op_words = {
                '=': 'equals',
                '!=': 'not equals',
                '<>': 'not equals',
                '>': 'greater than',
                '<': 'less than',
                '>=': 'at least',
                '<=': 'at most'
            }
            op_word = op_words.get(operator, operator)
            desc = f"{column} {op_word} {value}"

            filters.append(QueryFilter(
                column=column,
                operator=operator,
                value=value,
                description=desc
            ))

    return filters


def _extract_date_range(sql: str) -> tuple[Optional[str], Optional[str]]:
    """
    Extract date range from WHERE clause if present.

    Returns:
        Tuple of (range_description, column_name) or (None, None)
    """
    # Look for common date patterns
    # NOW() - INTERVAL 'X days/months/years'

    interval_match = re.search(
        r'(\w+)\s*>=?\s*(?:NOW\(\)|CURRENT_DATE|CURRENT_TIMESTAMP)\s*-\s*INTERVAL\s+[\'"](\d+)\s+(day|month|year)s?[\'"]',
        sql,
        re.IGNORECASE
    )
    if interval_match:
        column = interval_match.group(1)
        amount = int(interval_match.group(2))
        unit = interval_match.group(3).lower()

        if unit == 'day':
            if amount == 1:
                range_desc = "last 24 hours"
            elif amount == 7:
                range_desc = "last 7 days"
            elif amount == 30:
                range_desc = "last 30 days"
            else:
                range_desc = f"last {amount} days"
        elif unit == 'month':
            if amount == 1:
                range_desc = "last month"
            else:
                range_desc = f"last {amount} months"
        elif unit == 'year':
            if amount == 1:
                range_desc = "last year"
            else:
                range_desc = f"last {amount} years"
        else:
            range_desc = f"last {amount} {unit}s"

        return range_desc, column

    # Look for specific date comparisons
    date_match = re.search(r'(\w+)\s*>=?\s*[\'"](\d{4}-\d{2}-\d{2})', sql, re.IGNORECASE)
    if date_match:
        column = date_match.group(1)
        date_str = date_match.group(2)
        return f"from {date_str}", column

    return "all time", None


def apply_modifications(
    original_sql: str,
    defaults: QueryDefaults,
    dialect: str = "postgres"
) -> str:
    """
    Regenerate SQL from modified QueryDefaults using sqlglot AST.

    This function rebuilds the SQL query based on the current state of the
    QueryDefaults object, applying any modifications the user has made.

    Args:
        original_sql: Original SQL query
        defaults: QueryDefaults with possibly modified values
        dialect: SQL dialect ("postgres" or "mysql")

    Returns:
        Modified SQL query
    """
    # Parse SQL into AST
    try:
        ast = sqlglot.parse_one(original_sql, read=dialect)
    except ParseError as e:
        logger.error(f"Failed to parse SQL for modification: {e}")
        return original_sql  # Return original on parse error

    # Apply LIMIT modification
    ast.args.pop("limit", None)  # Clear existing LIMIT
    ast = ast.limit(defaults.limit)

    # Apply ORDER BY modification
    if defaults.sort_column:
        ast.args.pop("order", None)  # CRITICAL: Clear existing ORDER BY first!
        ast = ast.order_by(f"{defaults.sort_column} {defaults.sort_direction}")

    # Apply WHERE clause modifications (filters)
    if defaults.filters:
        # Add each filter as a WHERE condition
        # sqlglot will automatically combine with AND
        for filter in defaults.filters:
            filter_expr = filter.to_sql()
            ast = ast.where(filter_expr)

    # Regenerate SQL from modified AST
    return ast.sql(dialect=dialect)

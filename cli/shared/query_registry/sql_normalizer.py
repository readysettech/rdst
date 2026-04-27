"""
SQL Normalization with SQLGlot

Provides robust SQL parameterization using SQLGlot's AST parser.
Replaces literals with named placeholders (:p1, :p2, etc.) while
correctly handling comments, nested queries, and edge cases.
"""

import re
import logging
from typing import Tuple, Dict, Any, Optional, Set

from sqlglot import parse_one, exp
from sqlglot.generator import Generator as _BaseGenerator

logger = logging.getLogger(__name__)

# Suppress sqlglot parser warnings about unsupported syntax (e.g. EXPLAIN
# statements).  These are noisy and harmless -- sqlglot falls back to parsing
# the statement as a Command, which is fine for our normalisation purposes.
logging.getLogger("sqlglot").setLevel(logging.ERROR)


class _ReadysetCompatGenerator(_BaseGenerator):
    """Custom SQL generator that omits AS for table aliases.

    sqlglot's default generator produces 'FROM table AS alias' but
    Readyset's query ID hashing treats 'FROM table alias' (no AS) as
    a different query. Since the wire protocol sends queries without AS,
    we must generate SQL without AS to ensure cache ID consistency.
    """

    def table_sql(self, expression: exp.Table, sep: str = " ") -> str:
        return super().table_sql(expression, sep=sep)


def normalize_and_extract(sql: str, dialect: str = None) -> Tuple[str, Dict[str, dict]]:
    """
    Parse SQL, extract literals, replace with named :p1, :p2 placeholders.

    Args:
        sql: Original SQL with actual values
        dialect: Optional dialect ('postgres', 'mysql', etc.)

    Returns:
        (normalized_sql, params) where params = {'p1': {'value': 20, 'type': 'number'}, ...}
    """
    if not sql or not sql.strip():
        return sql, {}

    try:
        tree = parse_one(sql, dialect=dialect)
    except Exception as e:
        logger.debug(f"SQLGlot parsing failed, falling back to regex: {e}")
        return _fallback_normalize(sql)

    params = {}
    for i, literal in enumerate(tree.find_all(exp.Literal), 1):
        param_name = f"p{i}"
        # Store value with type info
        if literal.is_string:
            params[param_name] = {'value': literal.this, 'type': 'string'}
        else:
            params[param_name] = {'value': literal.this, 'type': 'number'}
        # Replace with named :p1, :p2 placeholder
        literal.replace(exp.Placeholder(this=param_name))

    return _ReadysetCompatGenerator().generate(tree), params


def reconstruct_sql(normalized_sql: str, params: Dict[str, dict], dialect: str = None) -> str:
    """
    Reconstruct executable SQL by replacing :p1, :p2 placeholders with values.

    Args:
        normalized_sql: SQL with :p1, :p2 placeholders
        params: {'p1': {'value': ..., 'type': ...}, ...}
        dialect: Optional dialect

    Returns:
        Executable SQL with actual values
    """
    if not normalized_sql or not normalized_sql.strip():
        return normalized_sql

    if not params:
        return normalized_sql

    try:
        tree = parse_one(normalized_sql, dialect=dialect)
    except Exception as e:
        logger.debug(f"SQLGlot parsing failed during reconstruction, falling back to regex: {e}")
        return _fallback_reconstruct(normalized_sql, params)

    for placeholder in tree.find_all(exp.Placeholder):
        param_name = placeholder.this
        if param_name and param_name in params:
            param_info = params[param_name]
            if param_info['type'] == 'string':
                replacement = exp.Literal.string(param_info['value'])
            else:
                replacement = exp.Literal.number(param_info['value'])
            placeholder.replace(replacement)

    return _ReadysetCompatGenerator().generate(tree)


def get_placeholder_names(normalized_sql: str, dialect: str = None) -> Set[str]:
    """
    Get all placeholder names (:p1, :p2, etc.) in normalized SQL.

    Args:
        normalized_sql: SQL with :p1, :p2 placeholders
        dialect: Optional dialect

    Returns:
        Set of placeholder names (e.g., {'p1', 'p2', 'p3'})
    """
    if not normalized_sql or not normalized_sql.strip():
        return set()

    try:
        tree = parse_one(normalized_sql, dialect=dialect)
        return {p.this for p in tree.find_all(exp.Placeholder) if p.this}
    except Exception as e:
        logger.debug(f"SQLGlot parsing failed, falling back to regex: {e}")
        # Fallback: find :pN patterns with regex
        matches = re.findall(r':p(\d+)', normalized_sql)
        return {f"p{m}" for m in matches}


def _fallback_normalize(sql: str) -> Tuple[str, Dict[str, dict]]:
    """
    Fallback regex-based normalization when SQLGlot fails.

    This is the legacy approach - less accurate but works for edge cases
    where SQLGlot can't parse the SQL.
    """
    normalized = sql

    # Collapse whitespace
    normalized = re.sub(r'\s+', ' ', normalized)

    # Track extracted values
    params = {}
    param_counter = [0]

    def replace_with_placeholder(match, is_string=False):
        param_counter[0] += 1
        param_name = f"p{param_counter[0]}"
        value = match.group(0)

        if is_string:
            # Remove quotes from value
            params[param_name] = {'value': value[1:-1], 'type': 'string'}
        else:
            params[param_name] = {'value': value, 'type': 'number'}

        return f":{param_name}"

    # Replace string literals with placeholders
    normalized = re.sub(
        r"'[^']*'",
        lambda m: replace_with_placeholder(m, is_string=True),
        normalized
    )

    # Replace numeric literals with placeholders
    normalized = re.sub(
        r'\b\d+(?:\.\d+)?\b',
        lambda m: replace_with_placeholder(m, is_string=False),
        normalized
    )

    return normalized.strip(), params


def _fallback_reconstruct(normalized_sql: str, params: Dict[str, dict]) -> str:
    """
    Fallback regex-based reconstruction when SQLGlot fails.
    """
    result = normalized_sql

    for param_name, param_info in params.items():
        placeholder = f":{param_name}"
        value = param_info['value']

        if param_info['type'] == 'string':
            replacement = f"'{value}'"
        else:
            replacement = str(value)

        result = result.replace(placeholder, replacement, 1)

    return result

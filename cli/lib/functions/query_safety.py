"""
Query Safety Validation for RDST Analyze

Validates that SQL queries are safe to analyze and won't perform destructive operations.
Critical security function to prevent DROP, DELETE, UPDATE, and other dangerous operations.
"""

import re
from typing import Dict, Any, List, Tuple


# Dangerous SQL keywords that should not be allowed in analyze operations
DANGEROUS_KEYWORDS = {
    # Data modification
    'DELETE', 'UPDATE', 'INSERT', 'REPLACE', 'MERGE', 'TRUNCATE',
    # Schema modification
    'DROP', 'CREATE', 'ALTER', 'RENAME',
    # Administrative operations
    'GRANT', 'REVOKE', 'SET', 'RESET', 'FLUSH', 'PURGE', 'OPTIMIZE',
    # Transaction control (could interfere with analysis)
    'COMMIT', 'ROLLBACK', 'START TRANSACTION', 'BEGIN',
    # Stored procedures/functions
    'CALL', 'EXECUTE', 'EXEC',
    # System operations
    'SHUTDOWN', 'RESTART', 'KILL', 'LOAD DATA', 'IMPORT',
    # User management
    'CREATE USER', 'DROP USER', 'ALTER USER',
    # Database management
    'USE', 'CONNECT'
}

# Allowed keywords for read-only operations
ALLOWED_KEYWORDS = {
    'SELECT', 'WITH', 'SHOW', 'DESCRIBE', 'DESC', 'EXPLAIN',
    'ANALYZE', 'CHECK', 'CHECKSUM'
}


def validate_query_safety(sql: str, **kwargs) -> Dict[str, Any]:
    """
    Validate that a SQL query is safe for analysis operations.

    This function ensures queries are read-only and won't perform any
    destructive or administrative operations.

    Args:
        sql: The SQL query to validate
        **kwargs: Additional workflow parameters (ignored)

    Returns:
        Dict containing:
        - safe: boolean indicating if query is safe
        - normalized_sql: cleaned query text
        - issues: list of safety issues found
        - error: error message if validation failed
    """
    try:
        # Clean and normalize the query
        normalized = _normalize_query_for_safety(sql)

        if not normalized.strip():
            return {
                "safe": False,
                "normalized_sql": normalized,
                "issues": ["Empty query"],
                "error": "Query is empty or contains only whitespace"
            }

        # Extract and validate keywords
        keywords = _extract_sql_keywords(normalized)
        issues = []

        # Check for dangerous keywords
        dangerous_found = []
        for keyword in keywords:
            if keyword in DANGEROUS_KEYWORDS:
                dangerous_found.append(keyword)
                issues.append(f"Dangerous keyword found: {keyword}")

        # Check if query starts with allowed operations
        first_keyword = keywords[0] if keywords else ""
        if first_keyword not in ALLOWED_KEYWORDS and first_keyword not in {'(', 'WITH'}:
            issues.append(f"Query must start with read-only operation, found: {first_keyword}")

        # Additional pattern-based validation
        additional_issues = _validate_query_patterns(normalized)
        issues.extend(additional_issues)

        is_safe = len(issues) == 0

        return {
            "safe": is_safe,
            "normalized_sql": normalized,
            "issues": issues,
            "keywords_found": keywords[:10],  # First 10 for debugging
            "dangerous_keywords": dangerous_found
        }

    except Exception as e:
        return {
            "safe": False,
            "normalized_sql": sql,
            "issues": [f"Validation error: {str(e)}"],
            "error": f"Failed to validate query safety: {str(e)}"
        }


def _normalize_query_for_safety(sql: str) -> str:
    """
    Normalize query for safety analysis.

    Args:
        sql: Raw SQL query

    Returns:
        Normalized SQL with comments removed and whitespace cleaned
    """
    # Remove single-line comments (-- style)
    sql = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)

    # Remove multi-line comments (/* */ style)
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)

    # Replace multiple whitespace with single spaces
    sql = re.sub(r'\s+', ' ', sql)

    # Strip leading/trailing whitespace
    sql = sql.strip()

    return sql


def _extract_sql_keywords(sql: str) -> List[str]:
    """
    Extract SQL keywords from normalized query.

    Args:
        sql: Normalized SQL query

    Returns:
        List of SQL keywords found in order
    """
    # Pattern to match SQL keywords (word boundaries, case insensitive)
    keyword_pattern = r'\b([A-Za-z_][A-Za-z0-9_]*)\b'
    matches = re.findall(keyword_pattern, sql)

    # Convert to uppercase and filter for actual SQL keywords/identifiers
    keywords = []
    for match in matches:
        upper_match = match.upper()
        # Include known SQL keywords and common patterns
        if (upper_match in DANGEROUS_KEYWORDS or
            upper_match in ALLOWED_KEYWORDS or
            len(upper_match) <= 20):  # Reasonable keyword length
            keywords.append(upper_match)

    return keywords


def _validate_query_patterns(sql: str) -> List[str]:
    """
    Validate query using pattern-based rules.

    Args:
        sql: Normalized SQL query

    Returns:
        List of issues found
    """
    issues = []
    sql_upper = sql.upper()

    # Check for common dangerous patterns
    dangerous_patterns = [
        (r'\bINTO\s+OUTFILE\b', "INTO OUTFILE operations not allowed"),
        (r'\bLOAD_FILE\s*\(', "LOAD_FILE function not allowed"),
        (r'\bSYSTEM\s*\(', "SYSTEM function calls not allowed"),
        (r'\b@@\w+', "System variables access may not be safe"),
        (r'\bSLEEP\s*\(', "SLEEP function not allowed for analysis"),
        (r';\s*\w', "Multiple statements not allowed"),
    ]

    for pattern, message in dangerous_patterns:
        if re.search(pattern, sql_upper):
            issues.append(message)

    # Check for excessively long queries (potential for complexity attacks)
    if len(sql) > 50000:  # 50KB limit
        issues.append("Query exceeds maximum length for safety analysis")

    # Check for deeply nested subqueries (potential for resource exhaustion)
    paren_depth = 0
    max_depth = 0
    for char in sql:
        if char == '(':
            paren_depth += 1
            max_depth = max(max_depth, paren_depth)
        elif char == ')':
            paren_depth -= 1

    if max_depth > 50:  # Reasonable nesting limit
        issues.append("Query has excessive nesting depth")

    return issues
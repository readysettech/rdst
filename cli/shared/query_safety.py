"""
Query Safety Validation for RDST Analyze

Validates that SQL queries are safe to analyze and won't perform destructive operations.
Critical security function to prevent DROP, DELETE, UPDATE, and other dangerous operations.
"""

import re
from typing import Any, Dict, List


# Dangerous SQL keywords that should not be allowed in analyze operations
DANGEROUS_KEYWORDS = {
    # Data modification
    "DELETE",
    "UPDATE",
    "INSERT",
    "REPLACE",
    "MERGE",
    "TRUNCATE",
    # Schema modification
    "DROP",
    "CREATE",
    "ALTER",
    "RENAME",
    # Administrative operations
    "GRANT",
    "REVOKE",
    "SET",
    "RESET",
    "FLUSH",
    "PURGE",
    "OPTIMIZE",
    # Transaction control (could interfere with analysis)
    "COMMIT",
    "ROLLBACK",
    "START TRANSACTION",
    "BEGIN",
    # Stored procedures/functions
    "CALL",
    "EXECUTE",
    "EXEC",
    # System operations
    "SHUTDOWN",
    "RESTART",
    "KILL",
    "LOAD DATA",
    "IMPORT",
    # User management
    "CREATE USER",
    "DROP USER",
    "ALTER USER",
    # Database management
    "USE",
    "CONNECT",
}

# Functions that reach outside the queried tables -- server filesystem, outbound
# connections, or a parked session. They sit inside an ordinary SELECT, so
# neither the keyword scan nor the leading-keyword check ever sees them, and
# they return their result before any rollback, so the transaction the caller
# happens to be in does not undo them.
SIDE_EFFECT_FUNCTIONS = {
    "PG_READ_FILE", "PG_READ_BINARY_FILE", "PG_LS_DIR", "PG_STAT_FILE",
    "LO_IMPORT", "LO_EXPORT", "DBLINK", "DBLINK_EXEC", "QUERY_TO_XML",
    "PG_SLEEP", "PG_TERMINATE_BACKEND", "PG_CANCEL_BACKEND",
    "LOAD_FILE", "SLEEP", "BENCHMARK", "SYS_EXEC", "SYS_EVAL",
}

# Allowed keywords for read-only operations
ALLOWED_KEYWORDS = {
    "SELECT",
    "WITH",
    "SHOW",
    "DESCRIBE",
    "DESC",
    "EXPLAIN",
    "ANALYZE",
    "CHECK",
    "CHECKSUM",
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
        normalized = _normalize_query_for_safety(sql)

        if not normalized.strip():
            return {
                "safe": False,
                "normalized_sql": normalized,
                "issues": ["Empty query"],
                "error": "Query is empty or contains only whitespace",
            }

        keywords = _extract_sql_keywords(normalized)
        issues = []

        dangerous_found = []
        for keyword in keywords:
            if keyword in DANGEROUS_KEYWORDS:
                dangerous_found.append(keyword)
                issues.append(f"Dangerous keyword found: {keyword}")

        first_keyword = keywords[0] if keywords else ""
        if first_keyword not in ALLOWED_KEYWORDS and first_keyword not in {"(", "WITH"}:
            # Often a typo rather than an attack, so name what was expected
            # instead of only reporting that the query was rejected.
            allowed = ", ".join(sorted(ALLOWED_KEYWORDS))
            issues.append(
                f"query must begin with one of {allowed} (found '{first_keyword}')"
            )

        issues.extend(_validate_query_patterns(normalized))

        return {
            "safe": len(issues) == 0,
            "normalized_sql": normalized,
            "issues": issues,
            "keywords_found": keywords[:10],
            "dangerous_keywords": dangerous_found,
        }
    except Exception as exc:
        return {
            "safe": False,
            "normalized_sql": sql,
            "issues": [f"Validation error: {exc}"],
            "error": f"Failed to validate query safety: {exc}",
        }


def _normalize_query_for_safety(sql: str) -> str:
    """Normalize query for safety analysis."""
    sql = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\s+", " ", sql)
    return sql.strip()


# Row-locking clauses read rows and take locks; they modify nothing, but they
# contain UPDATE and SHARE, so they have to come out before keyword scanning.
_ROW_LOCK_CLAUSE = re.compile(
    r"\bFOR\s+(?:NO\s+KEY\s+)?UPDATE\b|\bFOR\s+(?:KEY\s+)?SHARE\b|\bLOCK\s+IN\s+SHARE\s+MODE\b",
    re.IGNORECASE,
)

# Literals and quoted identifiers cannot execute, so a keyword inside one is
# text, not an operation. 'DROP by the office' must not read as DROP.
_QUOTED_SPANS = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`", re.DOTALL)


def _extract_sql_keywords(sql: str) -> List[str]:
    """Extract SQL keywords from normalized query."""
    sql = _QUOTED_SPANS.sub(" ", sql)
    sql = _ROW_LOCK_CLAUSE.sub(" ", sql)
    keyword_pattern = r"\b([A-Za-z_][A-Za-z0-9_]*)\b"
    matches = re.findall(keyword_pattern, sql)

    keywords = []
    for match in matches:
        upper_match = match.upper()
        if (
            upper_match in DANGEROUS_KEYWORDS
            or upper_match in ALLOWED_KEYWORDS
            or len(upper_match) <= 20
        ):
            keywords.append(upper_match)

    return keywords


def _validate_query_patterns(sql: str) -> List[str]:
    """Validate query using pattern-based rules."""
    issues = []
    # Same reasoning as keyword extraction: every pattern below describes an
    # executable construct, so a match inside a literal is a false positive.
    # A semicolon in a string value is not a second statement.
    sql_upper = _QUOTED_SPANS.sub(" ", sql).upper()

    for func in sorted(SIDE_EFFECT_FUNCTIONS):
        if re.search(r"\b" + func + r"\s*\(", sql_upper):
            issues.append(f"{func}() reaches outside the queried tables")

    dangerous_patterns = [
        (r"\bINTO\s+OUTFILE\b", "INTO OUTFILE operations not allowed"),
        (r"\bLOAD_FILE\s*\(", "LOAD_FILE function not allowed"),
        (r"\bSYSTEM\s*\(", "SYSTEM function calls not allowed"),
        (r"\b@@\w+", "System variables access may not be safe"),
        (r"\bSLEEP\s*\(", "SLEEP function not allowed for analysis"),
        (r";\s*\w", "Multiple statements not allowed"),
    ]

    for pattern, message in dangerous_patterns:
        if re.search(pattern, sql_upper):
            issues.append(message)

    if len(sql) > 50000:
        issues.append("Query exceeds maximum length for safety analysis")

    paren_depth = 0
    max_depth = 0
    for char in sql:
        if char == "(":
            paren_depth += 1
            max_depth = max(max_depth, paren_depth)
        elif char == ")":
            paren_depth -= 1

    if max_depth > 50:
        issues.append("Query has excessive nesting depth")

    return issues

import re
from typing import Dict, Any, List


UNCACHEABLE_PATTERNS = {
    # Non-deterministic functions
    'NOW': 'Uses non-deterministic NOW() function',
    'CURRENT_TIMESTAMP': 'Uses non-deterministic CURRENT_TIMESTAMP function',
    'CURRENT_DATE': 'Uses non-deterministic CURRENT_DATE function',
    'CURRENT_TIME': 'Uses non-deterministic CURRENT_TIME function',
    'RAND': 'Uses non-deterministic RAND() function',
    'RANDOM': 'Uses non-deterministic RANDOM() function',
    'UUID': 'Uses non-deterministic UUID() function',

    # Locking and transaction-specific
    'FOR UPDATE': 'Contains FOR UPDATE locking clause',
    'LOCK IN SHARE MODE': 'Contains LOCK IN SHARE MODE clause',

    # Temp tables and session-specific
    'TEMPORARY': 'Uses temporary tables',
    'SESSION': 'Uses session-specific variables or tables',

    # Stored procedures
    'CALL ': 'Calls stored procedures',

    # Some aggregate window functions may not be supported
    'OVER (': 'May contain unsupported window functions',
}

# Patterns that require special attention
WARNING_PATTERNS = {
    'UNION': 'UNION queries may have limited support',
    'RECURSIVE': 'Recursive CTEs may not be supported',
    'JSON_': 'JSON functions may have limited support',
    'LATERAL': 'LATERAL joins may not be supported',
}


def check_readyset_cacheability(query: str = None, sql: str = None, **kwargs) -> Dict[str, Any]:
    """
    Check if a query can be cached by ReadySet.

    Analyzes the SQL query against known ReadySet limitations and generates
    a CREATE CACHE command if the query appears cacheable.

    Args:
        query: The SQL query to check (primary parameter)
        sql: Alternative parameter name for the query
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing:
        - cacheable: boolean indicating if query can be cached
        - confidence: 'high', 'medium', or 'low'
        - issues: list of blocking issues preventing caching
        - warnings: list of potential compatibility concerns
        - create_cache_command: ready-to-use CREATE CACHE statement
        - explanation: human-readable explanation
    """
    try:
        # Support both 'query' and 'sql' parameter names
        query_text = query or sql or kwargs.get('normalized_sql', '')

        if not query_text or not query_text.strip():
            return {
                "cacheable": False,
                "confidence": "high",
                "issues": ["Empty query provided"],
                "warnings": [],
                "create_cache_command": None,
                "explanation": "Cannot analyze cacheability of an empty query"
            }

        # Normalize query for analysis
        normalized = _normalize_for_analysis(query_text)

        # Check if it's a SELECT query
        if not _is_select_query(normalized):
            return {
                "cacheable": False,
                "confidence": "high",
                "issues": ["Only SELECT queries can be cached by ReadySet"],
                "warnings": [],
                "create_cache_command": None,
                "explanation": "ReadySet can only cache SELECT queries. INSERT, UPDATE, DELETE, and DDL statements cannot be cached."
            }

        # Check for blocking issues
        issues = _find_blocking_issues(normalized)

        # Check for warnings
        warnings = _find_warnings(normalized)

        # Determine cacheability
        is_cacheable = len(issues) == 0
        confidence = _determine_confidence(issues, warnings, normalized)

        # Generate CREATE CACHE command if cacheable
        create_cache_command = None
        if is_cacheable or confidence in ['medium', 'low']:
            create_cache_command = _generate_create_cache_command(query_text, confidence)

        # Generate explanation
        explanation = _generate_explanation(is_cacheable, issues, warnings, confidence)

        return {
            "cacheable": is_cacheable,
            "confidence": confidence,
            "issues": issues,
            "warnings": warnings,
            "create_cache_command": create_cache_command,
            "explanation": explanation,
            "query_parameterized": _has_parameters(query_text),
            "recommended_options": _recommend_cache_options(normalized, kwargs)
        }

    except Exception as e:
        return {
            "cacheable": False,
            "confidence": "low",
            "issues": [f"Analysis error: {str(e)}"],
            "warnings": [],
            "create_cache_command": None,
            "explanation": f"Failed to analyze query cacheability: {str(e)}"
        }


def _normalize_for_analysis(query: str) -> str:
    """Normalize query for pattern matching."""
    # Remove comments
    query = re.sub(r'--.*$', '', query, flags=re.MULTILINE)
    query = re.sub(r'/\*.*?\*/', '', query, flags=re.DOTALL)

    # Normalize whitespace
    query = re.sub(r'\s+', ' ', query)

    return query.strip().upper()


def _is_select_query(normalized_query: str) -> bool:
    """Check if the query is a SELECT statement."""
    # Remove leading whitespace and check if it starts with SELECT or WITH (for CTEs)
    return normalized_query.startswith('SELECT') or normalized_query.startswith('WITH')


def _find_blocking_issues(normalized_query: str) -> List[str]:
    """Find patterns that prevent caching."""
    issues = []

    for pattern, reason in UNCACHEABLE_PATTERNS.items():
        if pattern in normalized_query:
            issues.append(reason)

    return issues


def _find_warnings(normalized_query: str) -> List[str]:
    """Find patterns that may cause issues."""
    warnings = []

    for pattern, reason in WARNING_PATTERNS.items():
        if pattern in normalized_query:
            warnings.append(reason)

    return warnings


def _determine_confidence(issues: List[str], warnings: List[str], normalized_query: str) -> str:
    """Determine confidence level in cacheability assessment."""
    if len(issues) > 0:
        return "high"  # High confidence it's NOT cacheable

    if len(warnings) > 2:
        return "low"  # Multiple warnings suggest uncertainty

    if len(warnings) > 0:
        return "medium"  # Some warnings but likely cacheable

    # Check complexity factors
    complexity_score = 0

    # Multiple joins
    join_count = normalized_query.count(' JOIN ')
    if join_count > 3:
        complexity_score += 1

    # Subqueries
    subquery_count = normalized_query.count('(SELECT')
    if subquery_count > 2:
        complexity_score += 1

    # Complex aggregations
    if 'GROUP BY' in normalized_query and 'HAVING' in normalized_query:
        complexity_score += 1

    if complexity_score > 1:
        return "medium"

    return "high"  # High confidence it IS cacheable


def _has_parameters(query: str) -> bool:
    """Check if query contains parameters/placeholders."""
    # Check for common parameter styles
    return bool(
        re.search(r'\$\d+', query) or  # PostgreSQL style: $1, $2
        re.search(r'\?', query) or      # MySQL/JDBC style: ?
        re.search(r':\w+', query)       # Named parameters: :param
    )


def _generate_create_cache_command(query: str, confidence: str) -> str:
    """Generate a CREATE CACHE command for the query."""
    # Clean up the query for the command
    query_clean = query.strip()

    # Remove trailing semicolon if present
    if query_clean.endswith(';'):
        query_clean = query_clean[:-1]

    # Basic CREATE CACHE command
    # Use CONCURRENTLY to avoid blocking other operations
    command = f"CREATE CACHE CONCURRENTLY FROM {query_clean};"

    # Add comment about confidence level
    if confidence == "medium":
        command = f"-- Medium confidence - test before production use\n{command}"
    elif confidence == "low":
        command = f"-- Low confidence - verify with EXPLAIN CREATE CACHE first\n{command}"

    return command


def _recommend_cache_options(normalized_query: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Recommend cache options based on query characteristics."""
    options = {
        "use_always": False,
        "use_concurrently": True,  # Default to CONCURRENTLY
        "cache_type": None,  # "DEEP" or "SHALLOW"
    }

    # Recommend ALWAYS if query is simple and frequently accessed
    if 'GROUP BY' not in normalized_query and 'JOIN' not in normalized_query:
        try:
            query_freq = int(context.get('query_frequency', 0))
            if query_freq > 100:  # High frequency
                options["use_always"] = True
        except (ValueError, TypeError):
            pass  # Skip if frequency is not a valid number

    # Shallow cache recommendations (if supported)
    if 'GROUP BY' in normalized_query or 'AGGREGATE' in normalized_query:
        options["cache_type"] = "DEEP"  # Aggregations need deep caching

    return options


def _generate_explanation(is_cacheable: bool, issues: List[str],
                         warnings: List[str], confidence: str) -> str:
    """Generate human-readable explanation of cacheability."""
    if is_cacheable:
        explanation = f"✓ This query appears cacheable by ReadySet (confidence: {confidence})."

        if warnings:
            explanation += f"\n\nWarnings ({len(warnings)}):"
            for warning in warnings:
                explanation += f"\n  • {warning}"
            explanation += "\n\nConsider testing with EXPLAIN CREATE CACHE before production use."
        else:
            explanation += "\n\nNo compatibility issues detected. You can create a cache using the provided command."

        return explanation

    else:
        explanation = f"✗ This query is NOT cacheable by ReadySet."

        if issues:
            explanation += f"\n\nBlocking issues ({len(issues)}):"
            for issue in issues:
                explanation += f"\n  • {issue}"

        if warnings:
            explanation += f"\n\nAdditional concerns ({len(warnings)}):"
            for warning in warnings:
                explanation += f"\n  • {warning}"

        explanation += "\n\nConsider rewriting the query to avoid these patterns, or use traditional database optimization techniques."

        return explanation


def generate_explain_create_cache(query: str = None, sql: str = None, **kwargs) -> Dict[str, Any]:
    """
    Generate an EXPLAIN CREATE CACHE command for testing.

    This generates a command that can be used to test if ReadySet
    will accept the query without actually creating the cache.

    Args:
        query: The SQL query to check
        sql: Alternative parameter name for the query
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing the EXPLAIN command and usage instructions
    """
    query_text = query or sql or kwargs.get('normalized_sql', '')

    if not query_text or not query_text.strip():
        return {
            "explain_command": None,
            "error": "No query provided"
        }

    query_clean = query_text.strip()
    if query_clean.endswith(';'):
        query_clean = query_clean[:-1]

    explain_command = f"EXPLAIN CREATE CACHE FROM {query_clean};"

    return {
        "explain_command": explain_command,
        "usage": "Run this command against your ReadySet instance to verify cacheability",
        "note": "This will not create the cache, only validate if it can be created"
    }

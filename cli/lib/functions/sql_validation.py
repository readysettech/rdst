"""
SQL Validation and Safety for RDST Ask Command

Multi-layer validation to ensure queries are safe for execution:
1. Read-only enforcement (block write operations)
2. LIMIT injection (prevent unbounded result sets)
3. Query timeout preparation
4. Dangerous pattern detection
"""

import re
import sqlparse
from sqlparse.sql import Statement, Token, TokenList
from sqlparse.tokens import Keyword, DML
from typing import Dict, Any, Tuple, Optional, List


# Dangerous keywords that indicate write operations
WRITE_KEYWORDS = {
    'INSERT', 'UPDATE', 'DELETE', 'DROP', 'CREATE', 'ALTER', 'TRUNCATE',
    'REPLACE', 'MERGE', 'GRANT', 'REVOKE', 'SET', 'RESET'
}

# Safe read-only keywords
READ_KEYWORDS = {
    'SELECT', 'WITH', 'SHOW', 'DESCRIBE', 'DESC', 'EXPLAIN'
}


def validate_sql_for_ask(
    sql: str,
    max_limit: int = 1000,
    default_limit: int = 100,
    **kwargs
) -> Dict[str, Any]:
    """
    Comprehensive SQL validation for ask command.

    Performs multiple safety checks:
    1. Read-only validation (no writes)
    2. LIMIT injection/enforcement
    3. Dangerous pattern detection
    4. Syntax validation

    Args:
        sql: SQL query to validate
        max_limit: Maximum allowed LIMIT value
        default_limit: Default LIMIT to inject if missing
        **kwargs: Additional parameters

    Returns:
        Dict containing:
        - is_valid: bool
        - is_safe: bool
        - validated_sql: Potentially modified SQL with LIMIT injected
        - issues: List of validation issues
        - warnings: List of warnings
        - has_limit: bool
        - limit_value: int or None
    """
    issues = []
    warnings = []

    # Step 1: Basic validation
    if not sql or not sql.strip():
        return {
            'is_valid': False,
            'is_safe': False,
            'validated_sql': sql,
            'issues': ['Empty or whitespace-only query'],
            'warnings': [],
            'has_limit': False,
            'limit_value': None
        }

    # Step 2: Check for dangerous keywords
    read_only_check = _check_read_only(sql)
    if not read_only_check['is_read_only']:
        issues.extend(read_only_check['issues'])
        return {
            'is_valid': False,
            'is_safe': False,
            'validated_sql': sql,
            'issues': issues,
            'warnings': warnings,
            'has_limit': False,
            'limit_value': None,
            'dangerous_keywords': read_only_check.get('dangerous_keywords', [])
        }

    # Step 3: Parse SQL and check structure
    try:
        parsed = sqlparse.parse(sql)
        if not parsed:
            issues.append('Failed to parse SQL statement')
            return {
                'is_valid': False,
                'is_safe': False,
                'validated_sql': sql,
                'issues': issues,
                'warnings': warnings,
                'has_limit': False,
                'limit_value': None
            }

        statement = parsed[0]

        # Check if it's a SELECT statement
        if not _is_select_statement(statement):
            issues.append('Only SELECT statements are allowed')
            return {
                'is_valid': False,
                'is_safe': False,
                'validated_sql': sql,
                'issues': issues,
                'warnings': warnings,
                'has_limit': False,
                'limit_value': None
            }

    except Exception as e:
        issues.append(f'SQL parsing error: {str(e)}')
        return {
            'is_valid': False,
            'is_safe': False,
            'validated_sql': sql,
            'issues': issues,
            'warnings': warnings,
            'has_limit': False,
            'limit_value': None
        }

    # Step 4: Check for existing LIMIT clause
    limit_info = _extract_limit_clause(sql)
    has_limit = limit_info['has_limit']
    limit_value = limit_info['limit_value']

    # Step 5: Inject or validate LIMIT
    validated_sql = sql
    if not has_limit:
        # Inject default LIMIT
        validated_sql = _inject_limit(sql, default_limit)
        warnings.append(f'Added LIMIT {default_limit} to prevent unbounded results')
        limit_value = default_limit
    elif limit_value and limit_value > max_limit:
        # Reduce excessive LIMIT
        validated_sql = _replace_limit(sql, max_limit)
        warnings.append(f'Reduced LIMIT from {limit_value} to maximum {max_limit}')
        limit_value = max_limit

    # Step 6: Additional safety checks
    safety_warnings = _check_dangerous_patterns(validated_sql)
    warnings.extend(safety_warnings)

    return {
        'is_valid': True,
        'is_safe': True,
        'validated_sql': validated_sql,
        'issues': issues,
        'warnings': warnings,
        'has_limit': True,  # After injection
        'limit_value': limit_value
    }


def _check_read_only(sql: str) -> Dict[str, Any]:
    """
    Check if SQL is read-only (no write operations).

    Uses multiple approaches:
    1. Keyword pattern matching
    2. SQL parsing to check statement type
    """
    sql_upper = sql.upper()
    dangerous_found = []

    # Check for write keywords
    for keyword in WRITE_KEYWORDS:
        # Use word boundary to avoid false positives (e.g., "DESCRIPTION" contains "DESC")
        pattern = r'\b' + keyword + r'\b'
        if re.search(pattern, sql_upper):
            dangerous_found.append(keyword)

    if dangerous_found:
        return {
            'is_read_only': False,
            'issues': [f'Write operation detected: {", ".join(dangerous_found)}'],
            'dangerous_keywords': dangerous_found
        }

    # Additional check: ensure it starts with SELECT or WITH
    first_keyword = _get_first_keyword(sql)
    if first_keyword and first_keyword not in READ_KEYWORDS:
        return {
            'is_read_only': False,
            'issues': [f'Query must start with SELECT or WITH, found: {first_keyword}'],
            'dangerous_keywords': [first_keyword]
        }

    return {
        'is_read_only': True,
        'issues': [],
        'dangerous_keywords': []
    }


def _is_select_statement(statement: Statement) -> bool:
    """Check if parsed statement is a SELECT query."""
    for token in statement.tokens:
        if token.ttype is DML and token.value.upper() == 'SELECT':
            return True
        # Check for WITH clause (CTEs)
        if token.ttype is Keyword and token.value.upper() == 'WITH':
            return True
    return False


def _get_first_keyword(sql: str) -> Optional[str]:
    """Extract the first SQL keyword from query."""
    # Remove leading whitespace and comments
    sql = sql.strip()
    if not sql:
        return None

    # Parse and get first keyword token
    try:
        parsed = sqlparse.parse(sql)
        if parsed:
            statement = parsed[0]
            for token in statement.tokens:
                if token.ttype in (Keyword, DML):
                    return token.value.upper()
    except:
        pass

    # Fallback: regex
    match = re.match(r'\s*(\w+)', sql, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    return None


def _extract_limit_clause(sql: str) -> Dict[str, Any]:
    """
    Extract LIMIT clause from SQL query.

    Returns:
        Dict with has_limit (bool) and limit_value (int or None)
    """
    # Pattern for LIMIT clause (PostgreSQL and MySQL)
    # Matches: LIMIT 100, LIMIT 100 OFFSET 10, LIMIT 10, 100 (MySQL)
    pattern = r'\bLIMIT\s+(\d+)(?:\s*,\s*(\d+)|\s+OFFSET\s+\d+)?'
    match = re.search(pattern, sql, re.IGNORECASE)

    if match:
        # For MySQL format "LIMIT offset, count", the count is in group 2
        limit_value = int(match.group(2) if match.group(2) else match.group(1))
        return {
            'has_limit': True,
            'limit_value': limit_value
        }

    return {
        'has_limit': False,
        'limit_value': None
    }


def _inject_limit(sql: str, limit: int) -> str:
    """
    Inject LIMIT clause into SQL query if missing.

    Handles:
    - Simple SELECT queries
    - Queries with ORDER BY
    - Queries with trailing semicolon
    - CTEs (WITH clauses)
    """
    sql = sql.rstrip()

    # Remove trailing semicolon if present
    if sql.endswith(';'):
        sql = sql[:-1].rstrip()

    # Add LIMIT before any trailing semicolon
    sql_with_limit = f"{sql} LIMIT {limit}"

    return sql_with_limit


def _replace_limit(sql: str, new_limit: int) -> str:
    """
    Replace existing LIMIT clause with new value.

    Args:
        sql: SQL query with existing LIMIT
        new_limit: New LIMIT value to use

    Returns:
        Modified SQL with updated LIMIT
    """
    # Pattern to match and replace LIMIT clause
    pattern = r'\bLIMIT\s+\d+(?:\s*,\s*\d+|\s+OFFSET\s+\d+)?'

    def replace_func(match):
        # Check if it's MySQL format with offset
        if ',' in match.group(0):
            # Extract offset from "LIMIT offset, count"
            offset_match = re.match(r'LIMIT\s+(\d+)\s*,\s*\d+', match.group(0), re.IGNORECASE)
            if offset_match:
                offset = offset_match.group(1)
                return f'LIMIT {offset}, {new_limit}'
        # Check for OFFSET syntax
        elif 'OFFSET' in match.group(0).upper():
            offset_match = re.search(r'OFFSET\s+(\d+)', match.group(0), re.IGNORECASE)
            if offset_match:
                offset = offset_match.group(1)
                return f'LIMIT {new_limit} OFFSET {offset}'
        # Simple LIMIT
        return f'LIMIT {new_limit}'

    return re.sub(pattern, replace_func, sql, flags=re.IGNORECASE)


def _check_dangerous_patterns(sql: str) -> List[str]:
    """
    Check for potentially dangerous SQL patterns even in SELECT queries.

    Returns:
        List of warning messages
    """
    warnings = []
    sql_upper = sql.upper()

    # Check for INTO OUTFILE (MySQL) - can write to filesystem
    if 'INTO OUTFILE' in sql_upper or 'INTO DUMPFILE' in sql_upper:
        warnings.append('Query contains INTO OUTFILE/DUMPFILE - file write operations not allowed')

    # Check for LOAD_FILE or similar functions
    if 'LOAD_FILE' in sql_upper:
        warnings.append('Query contains LOAD_FILE - file read operations may be restricted')

    # Check for potentially expensive operations
    if 'CROSS JOIN' in sql_upper:
        warnings.append('Query contains CROSS JOIN - may produce very large result set')

    # Check for user-defined functions that might have side effects
    if 'PROCEDURE' in sql_upper or 'FUNCTION' in sql_upper:
        warnings.append('Query calls stored procedures/functions - verify they are read-only')

    return warnings


def estimate_query_complexity(sql: str) -> str:
    """
    Estimate query complexity based on structure.

    Returns:
        'simple', 'moderate', 'complex', or 'very_complex'
    """
    sql_upper = sql.upper()

    # Count indicators of complexity
    join_count = sql_upper.count('JOIN')
    subquery_count = sql_upper.count('SELECT') - 1  # Subtract main SELECT
    aggregate_count = sum(sql_upper.count(agg) for agg in ['COUNT', 'SUM', 'AVG', 'MAX', 'MIN'])
    has_group_by = 'GROUP BY' in sql_upper
    has_having = 'HAVING' in sql_upper
    has_window = 'OVER(' in sql_upper or 'OVER (' in sql_upper

    complexity_score = 0
    if join_count > 0:
        complexity_score += min(join_count * 2, 10)
    if subquery_count > 0:
        complexity_score += min(subquery_count * 3, 15)
    if aggregate_count > 0:
        complexity_score += 2
    if has_group_by:
        complexity_score += 3
    if has_having:
        complexity_score += 2
    if has_window:
        complexity_score += 5

    if complexity_score <= 5:
        return 'simple'
    elif complexity_score <= 10:
        return 'moderate'
    elif complexity_score <= 20:
        return 'complex'
    else:
        return 'very_complex'


def extract_table_names(sql: str) -> List[str]:
    """
    Extract table names from SQL query.

    Uses sqlparse to parse the SQL and extract table identifiers.

    Args:
        sql: SQL query string

    Returns:
        List of table names found in the query
    """
    try:
        parsed = sqlparse.parse(sql)
        if not parsed:
            return []

        tables = set()

        def extract_from_token(token):
            """Recursively extract table names from tokens."""
            if isinstance(token, TokenList):
                # Look for FROM or JOIN clauses
                from_seen = False
                join_seen = False

                for item in token.tokens:
                    # Check if this is a FROM or JOIN keyword
                    if item.ttype is Keyword and item.value.upper() in ('FROM', 'JOIN', 'INNER', 'LEFT', 'RIGHT', 'OUTER', 'CROSS'):
                        from_seen = True
                        continue

                    # If we just saw FROM/JOIN, next identifier is likely a table
                    if from_seen and item.ttype is None:
                        # Clean up the identifier (remove schema prefix if present)
                        table_name = str(item).strip()
                        # Remove aliases (everything after AS or space)
                        table_name = re.split(r'\s+(?:as\s+)?', table_name, flags=re.IGNORECASE)[0]
                        # Remove schema prefix (schema.table -> table)
                        if '.' in table_name:
                            table_name = table_name.split('.')[-1]
                        # Remove quotes
                        table_name = table_name.strip('"\'`')
                        if table_name and not table_name.upper() in ('SELECT', 'WHERE', 'ORDER', 'GROUP', 'HAVING', 'LIMIT'):
                            tables.add(table_name)
                        from_seen = False

                    # Recursively process sublists
                    extract_from_token(item)

        for statement in parsed:
            extract_from_token(statement)

        # Fallback: simple regex extraction if sqlparse fails
        if not tables:
            # Look for patterns like "FROM table" or "JOIN table"
            from_pattern = r'(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)'
            matches = re.findall(from_pattern, sql, re.IGNORECASE)
            tables.update(matches)

        return sorted(list(tables))

    except Exception as e:
        # If parsing fails, try simple regex fallback
        from_pattern = r'(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)'
        matches = re.findall(from_pattern, sql, re.IGNORECASE)
        return sorted(list(set(matches)))


def extract_column_references(sql: str) -> List[Dict[str, Any]]:
    """
    Extract column references from SQL query.

    Returns list of dicts with:
    - column: column name
    - table_alias: table alias or name if specified (e.g., 'c' from 'c.userid')
    - full_ref: the full reference as written (e.g., 'c.userid')

    Args:
        sql: SQL query string

    Returns:
        List of column reference dictionaries
    """
    columns = []

    # Pattern to match table.column or alias.column references
    # Matches: c.userid, posts.id, p.ownerdisplayname, etc.
    qualified_pattern = r'([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)'

    for match in re.finditer(qualified_pattern, sql):
        table_alias = match.group(1).lower()
        column = match.group(2).lower()
        full_ref = match.group(0)

        # Skip common SQL keywords that might match
        if table_alias in ('order', 'group', 'inner', 'left', 'right', 'outer', 'cross'):
            continue

        columns.append({
            'column': column,
            'table_alias': table_alias,
            'full_ref': full_ref
        })

    return columns


def extract_table_aliases(sql: str) -> Dict[str, str]:
    """
    Extract table aliases from SQL query.

    Returns dict mapping alias -> table_name.

    Args:
        sql: SQL query string

    Returns:
        Dict of alias -> table_name
    """
    aliases = {}

    # Pattern for "table_name alias" or "table_name AS alias"
    # Matches: FROM comments c, FROM posts AS p, JOIN users u
    pattern = r'(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+(?:AS\s+)?([a-zA-Z_][a-zA-Z0-9_]*)'

    for match in re.finditer(pattern, sql, re.IGNORECASE):
        table_name = match.group(1).lower()
        alias = match.group(2).lower()

        # Skip if "alias" is actually a keyword
        if alias in ('on', 'where', 'inner', 'left', 'right', 'outer', 'cross', 'join', 'and', 'or'):
            continue

        aliases[alias] = table_name

    # Also add tables without aliases (they reference themselves)
    tables = extract_table_names(sql)
    for table in tables:
        table_lower = table.lower()
        if table_lower not in aliases:
            aliases[table_lower] = table_lower

    return aliases


def validate_columns_against_schema(
    sql: str,
    schema: Dict[str, List[str]]
) -> Dict[str, Any]:
    """
    Validate that all column references in SQL exist in the schema.

    Args:
        sql: SQL query string
        schema: Dict mapping table_name -> list of column_names
            Example: {'posts': ['id', 'title', 'body'], 'comments': ['id', 'text']}

    Returns:
        Dict containing:
        - is_valid: bool - True if all columns exist
        - invalid_columns: List of invalid column references
        - suggestions: Dict mapping invalid column -> suggested alternatives
        - error_message: Human-readable error if invalid
    """
    # Normalize schema to lowercase
    schema_lower = {
        table.lower(): [col.lower() for col in cols]
        for table, cols in schema.items()
    }

    # Extract column references and aliases
    column_refs = extract_column_references(sql)
    aliases = extract_table_aliases(sql)

    invalid_columns = []
    suggestions = {}

    for ref in column_refs:
        alias = ref['table_alias']
        column = ref['column']
        full_ref = ref['full_ref']

        # Resolve alias to table name
        table_name = aliases.get(alias, alias)

        # Check if table exists
        if table_name not in schema_lower:
            # Table doesn't exist - will be caught by database anyway
            continue

        # Check if column exists in table
        valid_columns = schema_lower[table_name]
        if column not in valid_columns:
            invalid_columns.append({
                'reference': full_ref,
                'table': table_name,
                'column': column,
                'alias': alias
            })

            # Find similar column names for suggestions
            similar = _find_similar_columns(column, valid_columns)
            if similar:
                suggestions[full_ref] = similar

    if invalid_columns:
        # Build error message
        errors = []
        for inv in invalid_columns:
            msg = f"Column '{inv['reference']}' does not exist in table '{inv['table']}'"
            if inv['reference'] in suggestions:
                msg += f". Did you mean: {', '.join(suggestions[inv['reference']])}?"
            errors.append(msg)

        return {
            'is_valid': False,
            'invalid_columns': invalid_columns,
            'suggestions': suggestions,
            'error_message': '\n'.join(errors)
        }

    return {
        'is_valid': True,
        'invalid_columns': [],
        'suggestions': {},
        'error_message': None
    }


def _find_similar_columns(target: str, candidates: List[str], max_suggestions: int = 3) -> List[str]:
    """
    Find column names similar to target using edit distance.

    Args:
        target: The column name to match
        candidates: List of valid column names
        max_suggestions: Maximum number of suggestions to return

    Returns:
        List of similar column names, sorted by similarity
    """
    def levenshtein_distance(s1: str, s2: str) -> int:
        """Simple Levenshtein distance implementation."""
        if len(s1) < len(s2):
            return levenshtein_distance(s2, s1)

        if len(s2) == 0:
            return len(s1)

        prev_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            curr_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = prev_row[j + 1] + 1
                deletions = curr_row[j] + 1
                substitutions = prev_row[j] + (c1 != c2)
                curr_row.append(min(insertions, deletions, substitutions))
            prev_row = curr_row

        return prev_row[-1]

    # Calculate distances and filter
    scored = []
    for candidate in candidates:
        distance = levenshtein_distance(target.lower(), candidate.lower())
        # Only suggest if reasonably similar (within 50% of target length)
        max_distance = max(3, len(target) // 2)
        if distance <= max_distance:
            scored.append((candidate, distance))

    # Sort by distance (closest first)
    scored.sort(key=lambda x: x[1])

    return [col for col, _ in scored[:max_suggestions]]

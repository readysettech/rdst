"""
SQL Error Parsing and Auto-Correction

Detects common SQL syntax errors and suggests corrections.
"""

import re
from typing import Dict, Any, Optional, List, Tuple


# Common SQL keyword typos
COMMON_TYPOS = {
    'FORM': 'FROM',
    'FORM ': 'FROM',
    'WERE': 'WHERE',
    'WHER': 'WHERE',
    'SELCT': 'SELECT',
    'SLECT': 'SELECT',
    'SELET': 'SELECT',
    'GROPU': 'GROUP',
    'GROUPE': 'GROUP',
    'GORUP': 'GROUP',
    'ORDRE': 'ORDER',
    'ODER': 'ORDER',
    'OERDER': 'ORDER',
    'JION': 'JOIN',
    'INNE': 'INNER',
    'LMIT': 'LIMIT',
    'LIMTI': 'LIMIT',
}


def parse_syntax_error(
    error_message: str,
    sql: str,
    database_engine: str
) -> Optional[Dict[str, Any]]:
    """
    Parse syntax error and attempt to identify root cause.

    Args:
        error_message: Database error message
        sql: SQL that caused the error
        database_engine: 'mysql' or 'postgresql'

    Returns:
        Dict with diagnosis and corrected SQL, or None if can't parse
    """
    error_lower = error_message.lower()

    # Check if it's a syntax error
    is_syntax_error = any([
        'syntax error' in error_lower,
        'syntax' in error_lower and 'near' in error_lower,
        'unexpected' in error_lower,
    ])

    if not is_syntax_error:
        return None

    # Try different error detection strategies

    # Strategy 1: Detect typos in SQL keywords
    typo_result = _detect_keyword_typo(sql, error_message)
    if typo_result:
        return typo_result

    # Strategy 2: Detect missing/extra quotes
    quote_result = _detect_quote_mismatch(sql, error_message)
    if quote_result:
        return quote_result

    # Strategy 3: Detect unbalanced parentheses
    paren_result = _detect_unbalanced_parens(sql, error_message)
    if paren_result:
        return paren_result

    # Strategy 4: Detect missing commas
    comma_result = _detect_missing_comma(sql, error_message)
    if comma_result:
        return comma_result

    return None


def _detect_keyword_typo(sql: str, error_message: str) -> Optional[Dict[str, Any]]:
    """Detect typos in SQL keywords."""
    sql_upper = sql.upper()

    for typo, correct in COMMON_TYPOS.items():
        # Use word boundaries to avoid partial matches
        pattern = r'\b' + re.escape(typo.strip()) + r'\b'
        if re.search(pattern, sql_upper):
            # Found typo
            corrected_sql = re.sub(pattern, correct, sql, flags=re.IGNORECASE)

            return {
                'error_type': 'keyword_typo',
                'diagnosis': f"Typo detected: '{typo.strip()}' should be '{correct}'",
                'original_sql': sql,
                'corrected_sql': corrected_sql,
                'confidence': 0.9,
                'suggestion': f"Replace '{typo.strip()}' with '{correct}'"
            }

    return None


def _detect_quote_mismatch(sql: str, error_message: str) -> Optional[Dict[str, Any]]:
    """Detect missing or unbalanced quotes."""
    # Count single and double quotes
    single_quotes = sql.count("'")
    double_quotes = sql.count('"')

    issues = []

    if single_quotes % 2 != 0:
        issues.append("Unbalanced single quotes (')")

    if double_quotes % 2 != 0:
        issues.append("Unbalanced double quotes (\")")

    if not issues:
        return None

    return {
        'error_type': 'quote_mismatch',
        'diagnosis': ', '.join(issues),
        'original_sql': sql,
        'corrected_sql': None,  # Can't auto-correct, need LLM
        'confidence': 0.7,
        'suggestion': 'Check for missing or extra quotes in string values'
    }


def _detect_unbalanced_parens(sql: str, error_message: str) -> Optional[Dict[str, Any]]:
    """Detect unbalanced parentheses."""
    open_count = sql.count('(')
    close_count = sql.count(')')

    if open_count == close_count:
        return None

    if open_count > close_count:
        diagnosis = f"Missing {open_count - close_count} closing parenthesis"
        corrected_sql = sql + ')' * (open_count - close_count)
    else:
        diagnosis = f"Extra {close_count - open_count} closing parenthesis"
        corrected_sql = None  # Can't safely auto-correct

    return {
        'error_type': 'unbalanced_parens',
        'diagnosis': diagnosis,
        'original_sql': sql,
        'corrected_sql': corrected_sql,
        'confidence': 0.8,
        'suggestion': 'Check for missing or extra parentheses'
    }


def _detect_missing_comma(sql: str, error_message: str) -> Optional[Dict[str, Any]]:
    """Detect missing commas in SELECT or WHERE clauses."""
    # This is hard to detect automatically, look for patterns like:
    # SELECT id name FROM ... (missing comma between id and name)

    # Pattern: word followed by another word without comma
    select_clause_pattern = r'SELECT\s+(\w+)\s+(\w+)\s+FROM'
    match = re.search(select_clause_pattern, sql, re.IGNORECASE)

    if match and 'AS' not in sql.upper()[match.start():match.end()]:
        # Might be missing comma
        col1, col2 = match.groups()

        corrected_sql = re.sub(
            f'{col1}\\s+{col2}',
            f'{col1}, {col2}',
            sql,
            count=1,
            flags=re.IGNORECASE
        )

        return {
            'error_type': 'missing_comma',
            'diagnosis': f"Possible missing comma between '{col1}' and '{col2}'",
            'original_sql': sql,
            'corrected_sql': corrected_sql,
            'confidence': 0.6,
            'suggestion': f"Try adding comma: {col1}, {col2}"
        }

    return None


def format_syntax_error_diagnosis(result: Dict[str, Any]) -> str:
    """
    Format syntax error diagnosis for display.

    Args:
        result: Result from parse_syntax_error()

    Returns:
        Formatted string for user display
    """
    lines = [
        "\n🔍 Syntax Error Analysis:",
        f"   Diagnosis: {result['diagnosis']}"
    ]

    if result.get('suggestion'):
        lines.append(f"   Suggestion: {result['suggestion']}")

    if result.get('corrected_sql'):
        lines.append(f"\n   Original:  {result['original_sql']}")
        lines.append(f"   Corrected: {result['corrected_sql']}")

    lines.append(f"   Confidence: {result['confidence']*100:.0f}%")

    return '\n'.join(lines)

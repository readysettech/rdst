"""
Query Parameterization for RDST Analyze

Implements dual parameterization strategy:
1. Registry normalization - for consistent hashing of logically identical queries
2. LLM parameterization - for PII protection when sending to external APIs

The registry normalization preserves logical query structure for consistent lookup,
while LLM parameterization removes sensitive data before external API calls.
"""

import re
import hashlib
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime


def normalize_for_registry(sql: str, **kwargs) -> Dict[str, Any]:
    """
    Normalize query for consistent registry hashing.

    This normalization focuses on logical query structure rather than
    parameter values, allowing queries with different literal values
    but identical structure to be grouped together.

    Args:
        sql: Raw SQL query
        **kwargs: Additional workflow parameters (ignored)

    Returns:
        Dict containing:
        - normalized_sql: Query normalized for consistent hashing
        - original_sql: Original query text
        - hash: Computed hash of normalized query
        - parameters_detected: List of parameter types found
    """
    try:
        normalized = _normalize_sql_for_registry(sql)
        query_hash = hashlib.md5(normalized.encode('utf-8')).hexdigest()[:12]

        # Detect what types of parameters were normalized
        param_types = _detect_parameter_types(sql, normalized)

        return {
            "normalized_sql": normalized,
            "original_sql": sql.strip(),
            "hash": query_hash,
            "parameters_detected": param_types,
            "normalization_type": "registry"
        }

    except Exception as e:
        return {
            "normalized_sql": sql,
            "original_sql": sql,
            "hash": hashlib.md5(sql.encode('utf-8')).hexdigest()[:12],
            "parameters_detected": [],
            "error": f"Normalization failed: {str(e)}"
        }


def parameterize_for_llm(sql: str, **kwargs) -> Dict[str, Any]:
    """
    Parameterize query to remove PII before sending to LLM.

    This parameterization is more aggressive than registry normalization,
    focusing on removing any potentially sensitive information that could
    be considered PII or proprietary data.

    Args:
        sql: Raw SQL query
        **kwargs: Additional workflow parameters (ignored)

    Returns:
        Dict containing:
        - parameterized_sql: Query safe for LLM analysis
        - original_sql: Original query text
        - replacements_made: List of replacements for debugging
        - sensitivity_score: Estimated sensitivity level (1-10)
    """
    try:
        parameterized, replacements = _parameterize_for_llm_safety(sql)
        sensitivity = _calculate_sensitivity_score(replacements)

        return {
            "parameterized_sql": parameterized,
            "original_sql": sql.strip(),
            "replacements_made": replacements,
            "sensitivity_score": sensitivity,
            "parameterization_type": "llm_safe",
            "safe_for_llm": sensitivity <= 7  # Threshold for LLM safety
        }

    except Exception as e:
        return {
            "parameterized_sql": sql,
            "original_sql": sql,
            "replacements_made": [],
            "sensitivity_score": 10,  # Assume unsafe on error
            "safe_for_llm": False,
            "error": f"LLM parameterization failed: {str(e)}"
        }


def _normalize_sql_for_registry(sql: str) -> str:
    """
    Normalize SQL for consistent registry hashing.

    This focuses on structural consistency rather than PII protection.
    """
    # Start with basic cleanup
    normalized = sql.strip()

    # Remove comments (but preserve structure)
    normalized = re.sub(r'--.*$', '', normalized, flags=re.MULTILINE)
    normalized = re.sub(r'/\*.*?\*/', '', normalized, flags=re.DOTALL)

    # Normalize whitespace
    normalized = re.sub(r'\s+', ' ', normalized)

    # Remove trailing semicolon
    normalized = re.sub(r';\s*$', '', normalized)

    # Normalize common variations that don't affect query structure
    # String literals with different values but same structure
    normalized = re.sub(r"'[^']*'", "'?'", normalized)
    normalized = re.sub(r'"[^"]*"', '"?"', normalized)

    # Numeric literals
    normalized = re.sub(r'\b\d+\.?\d*\b', '?', normalized)

    # Date literals (various formats)
    normalized = re.sub(r'\b\d{4}-\d{2}-\d{2}\b', '?', normalized)
    normalized = re.sub(r'\b\d{2}/\d{2}/\d{4}\b', '?', normalized)

    # Normalize case for keywords but preserve identifiers case sensitivity
    # This is complex, so we'll be conservative and only normalize obvious keywords
    keyword_patterns = [
        (r'\bSELECT\b', 'SELECT', re.IGNORECASE),
        (r'\bFROM\b', 'FROM', re.IGNORECASE),
        (r'\bWHERE\b', 'WHERE', re.IGNORECASE),
        (r'\bJOIN\b', 'JOIN', re.IGNORECASE),
        (r'\bON\b', 'ON', re.IGNORECASE),
        (r'\bGROUP BY\b', 'GROUP BY', re.IGNORECASE),
        (r'\bHAVING\b', 'HAVING', re.IGNORECASE),
        (r'\bORDER BY\b', 'ORDER BY', re.IGNORECASE),
        (r'\bLIMIT\b', 'LIMIT', re.IGNORECASE),
    ]

    for pattern, replacement, flags in keyword_patterns:
        normalized = re.sub(pattern, replacement, normalized, flags=flags)

    return normalized.strip()


def _parameterize_for_llm_safety(sql: str) -> Tuple[str, List[Dict[str, str]]]:
    """
    Aggressively parameterize query for LLM safety.

    Returns tuple of (parameterized_query, list_of_replacements)
    """
    parameterized = sql.strip()
    replacements = []

    # Remove comments entirely (could contain sensitive info)
    original_parameterized = parameterized
    parameterized = re.sub(r'--.*$', '', parameterized, flags=re.MULTILINE)
    parameterized = re.sub(r'/\*.*?\*/', '', parameterized, flags=re.DOTALL)
    if original_parameterized != parameterized:
        replacements.append({"type": "comments", "action": "removed", "reason": "potential_pii"})

    # Replace string literals (most likely to contain PII)
    string_count = len(re.findall(r"'[^']*'", parameterized))
    parameterized = re.sub(r"'[^']*'", "'<STRING_VALUE>'", parameterized)
    if string_count > 0:
        replacements.append({"type": "string_literals", "count": string_count, "action": "parameterized"})

    # Replace quoted identifiers that might be sensitive
    quoted_id_count = len(re.findall(r'"[^"]*"', parameterized))
    parameterized = re.sub(r'"[^"]*"', '"<IDENTIFIER>"', parameterized)
    if quoted_id_count > 0:
        replacements.append({"type": "quoted_identifiers", "count": quoted_id_count, "action": "parameterized"})

    # Replace numeric literals (could be sensitive IDs, amounts, etc.)
    numeric_count = len(re.findall(r'\b\d+\.?\d*\b', parameterized))
    parameterized = re.sub(r'\b\d+\.?\d*\b', '<NUMBER>', parameterized)
    if numeric_count > 0:
        replacements.append({"type": "numeric_literals", "count": numeric_count, "action": "parameterized"})

    # Replace date patterns
    date_patterns = [
        (r'\b\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?\b', '<DATE>'),
        (r'\b\d{2}/\d{2}/\d{4}\b', '<DATE>'),
        (r'\b\d{4}/\d{2}/\d{2}\b', '<DATE>'),
    ]

    for pattern, replacement in date_patterns:
        matches = len(re.findall(pattern, parameterized))
        if matches > 0:
            parameterized = re.sub(pattern, replacement, parameterized)
            replacements.append({"type": "date_literals", "count": matches, "action": "parameterized"})

    # Replace potential email patterns in string contexts
    email_count = len(re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', parameterized))
    parameterized = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '<EMAIL>', parameterized)
    if email_count > 0:
        replacements.append({"type": "email_addresses", "count": email_count, "action": "parameterized"})

    # Replace potential phone numbers
    phone_patterns = [
        r'\b\d{3}-\d{3}-\d{4}\b',
        r'\(\d{3}\)\s?\d{3}-\d{4}\b',
        r'\b\d{10,15}\b'  # Generic long number sequences
    ]

    for pattern in phone_patterns:
        matches = len(re.findall(pattern, parameterized))
        if matches > 0:
            parameterized = re.sub(pattern, '<PHONE>', parameterized)
            replacements.append({"type": "phone_numbers", "count": matches, "action": "parameterized"})

    # Normalize whitespace
    parameterized = re.sub(r'\s+', ' ', parameterized)
    parameterized = parameterized.strip()

    return parameterized, replacements


def _detect_parameter_types(original: str, normalized: str) -> List[str]:
    """
    Detect what types of parameters were normalized for registry.
    """
    param_types = []

    if "'?'" in normalized:
        param_types.append("string_literals")

    if '"?"' in normalized:
        param_types.append("quoted_identifiers")

    # Count parameter placeholders
    param_count = normalized.count('?')
    original_numbers = len(re.findall(r'\b\d+\.?\d*\b', original))

    if param_count >= original_numbers:
        param_types.append("numeric_literals")

    if re.search(r'\b\d{4}-\d{2}-\d{2}\b', original):
        param_types.append("date_literals")

    return param_types


def _calculate_sensitivity_score(replacements: List[Dict[str, str]]) -> int:
    """
    Calculate sensitivity score based on replacements made.

    Returns score from 1-10 where 10 is highest sensitivity.
    """
    if not replacements:
        return 1  # No sensitive data detected

    score = 1

    for replacement in replacements:
        rep_type = replacement.get("type", "")
        count = replacement.get("count", 1)

        # Score based on type of data replaced
        if rep_type == "email_addresses":
            score += 3 + min(count, 3)  # Email is high sensitivity
        elif rep_type == "phone_numbers":
            score += 2 + min(count, 2)  # Phone numbers are sensitive
        elif rep_type == "string_literals":
            score += min(count * 0.5, 2)  # String literals might be names, etc.
        elif rep_type == "comments":
            score += 1  # Comments might contain context
        elif rep_type in ["numeric_literals", "date_literals"]:
            score += min(count * 0.3, 1)  # Numbers/dates might be IDs

    return min(int(score), 10)  # Cap at 10
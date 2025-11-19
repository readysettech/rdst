"""
Recommendation Validation - Prevent LLM Hallucination

Based on Gautam's ValidationLayer from hacks/gautam/rdst/rdst_analyze.py
Validates LLM recommendations against collected database context to detect:
- Duplicate index suggestions (recommending indexes that already exist)
- Invalid recommendations not grounded in schema
"""

import re
from typing import Dict, Any, List


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

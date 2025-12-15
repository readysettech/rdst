"""
Phase 4: SQL Validation

Validates generated SQL before execution:
1. Read-only check (no writes)
2. Column validation against schema
3. LIMIT enforcement
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:
    from ..context import Ask3Context
    from ..presenter import Ask3Presenter

from ..types import ValidationError

logger = logging.getLogger(__name__)


def validate_sql(
    ctx: 'Ask3Context',
    presenter: 'Ask3Presenter'
) -> 'Ask3Context':
    """
    Validate SQL before execution.

    Checks:
    1. SQL is read-only (no INSERT, UPDATE, DELETE, etc.)
    2. Column references exist in schema
    3. LIMIT is enforced

    Args:
        ctx: Ask3Context with sql populated
        presenter: For output

    Returns:
        Updated context with validation_errors populated if issues found
    """
    ctx.phase = 'validate'
    ctx.clear_validation_errors()

    if not ctx.sql:
        ctx.validation_errors.append(ValidationError(
            column='',
            table_alias=None,
            message='No SQL to validate',
            suggestions=[]
        ))
        return ctx

    # Import validation functions
    # Path: lib/engines/ask3/phases/validate.py -> lib/functions/sql_validation.py
    from ....functions.sql_validation import (
        validate_sql_for_ask,
        validate_columns_against_schema,
        extract_column_references,
    )

    # Step 1: Read-only and LIMIT validation
    validation_result = validate_sql_for_ask(
        sql=ctx.sql,
        max_limit=1000,
        default_limit=ctx.max_rows
    )

    if not validation_result.get('is_valid'):
        issues = validation_result.get('issues', [])
        for issue in issues:
            ctx.validation_errors.append(ValidationError(
                column='',
                table_alias=None,
                message=issue,
                suggestions=[]
            ))
        presenter.validation_error(ctx.validation_errors)
        return ctx

    # Update SQL with LIMIT if it was added
    validated_sql = validation_result.get('validated_sql')
    if validated_sql and validated_sql != ctx.sql:
        ctx.sql = validated_sql

    # Step 2: Column validation against schema
    schema_dict = ctx.get_schema_as_dict()

    if schema_dict:
        column_validation = validate_columns_against_schema(ctx.sql, schema_dict)

        if not column_validation.get('is_valid', True):
            invalid_columns = column_validation.get('invalid_columns', [])

            for col_info in invalid_columns:
                ctx.validation_errors.append(ValidationError(
                    column=col_info.get('column', ''),
                    table_alias=col_info.get('table_alias'),
                    message=col_info.get('error', 'Column not found'),
                    suggestions=col_info.get('suggestions', [])
                ))

            presenter.validation_error(ctx.validation_errors)

    # Check for any warnings
    warnings = validation_result.get('warnings', [])
    for warning in warnings:
        presenter.warning(warning)

    return ctx


def build_error_message(errors: List[ValidationError]) -> str:
    """
    Build a human-readable error message from validation errors.

    Used when passing errors to LLM for recovery.
    """
    if not errors:
        return ""

    parts = []
    for err in errors:
        ref = f"{err.table_alias}.{err.column}" if err.table_alias else err.column
        msg = f"- {ref}: {err.message}"
        if err.suggestions:
            msg += f" (suggestions: {', '.join(err.suggestions[:3])})"
        parts.append(msg)

    return "SQL Validation Errors:\n" + "\n".join(parts)


def _build_schema_dict_from_formatted(schema_formatted: str) -> Dict[str, List[str]]:
    """
    Build schema dict from formatted schema string.

    Fallback when schema_info is not available.
    """
    import re

    schema_dict = {}
    current_table = None

    for line in schema_formatted.split('\n'):
        line = line.strip()

        # Table header: "Table: table_name" or "Table: table_name -- description"
        table_match = re.match(r'Table:\s+(\w+)', line, re.IGNORECASE)
        if table_match:
            current_table = table_match.group(1).lower()
            schema_dict[current_table] = []
            continue

        # Column line: "  column_name (type)" or "  column_name (type) -- description"
        if current_table and line.startswith(' '):
            col_match = re.match(r'\s*(\w+)', line)
            if col_match:
                col_name = col_match.group(1).lower()
                # Skip if it looks like a keyword
                if col_name not in ('table', 'column', 'index', 'primary', 'foreign'):
                    schema_dict[current_table].append(col_name)

    return schema_dict

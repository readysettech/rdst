"""
Phase 3: SQL Generation

Generates SQL from natural language using LLM.
Incorporates clarifications from Phase 2.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context import Ask3Context
    from ..presenter import Ask3Presenter

from ..types import Status

logger = logging.getLogger(__name__)


def generate_sql(
    ctx: 'Ask3Context',
    presenter: 'Ask3Presenter',
    llm_manager=None
) -> 'Ask3Context':
    """
    Generate SQL from natural language question.

    Uses refined question if clarifications were collected,
    otherwise uses original question.

    Args:
        ctx: Ask3Context with question and schema
        presenter: For progress output
        llm_manager: LLMManager instance (optional, creates default)

    Returns:
        Updated context with sql and sql_explanation populated
    """
    ctx.phase = 'generate'
    presenter.generating_sql()

    # Import here to avoid circular imports
    # Path: lib/engines/ask3/phases/generate.py -> lib/functions/, lib/llm_manager/
    from ....functions.sql_generation import generate_sql_from_nl
    from ....llm_manager import LLMManager

    if llm_manager is None:
        llm_manager = LLMManager()

    # Use refined question if available
    question = ctx.refined_question or ctx.question

    # Generate SQL
    result = generate_sql_from_nl(
        nl_question=question,
        filtered_schema=ctx.schema_formatted,
        database_engine=ctx.db_type,
        target_database=ctx.target,
        llm_manager=llm_manager,
        callback=lambda **kw: _track_llm_call(ctx, 'generate', **kw)
    )

    if not result.get('success'):
        error = result.get('error', 'Unknown error')
        logger.error(f"SQL generation failed: {error}")
        ctx.mark_error(f"Failed to generate SQL: {error}")
        presenter.error(error)
        return ctx

    # Store results
    ctx.sql = result.get('sql', '')
    ctx.sql_explanation = result.get('explanation', '')
    ctx.generation_response = result.get('raw_response', {})

    if not ctx.sql:
        ctx.mark_error("LLM returned empty SQL")
        presenter.error("LLM returned empty SQL")
        return ctx

    # Show generated SQL
    presenter.sql_generated(ctx.sql, ctx.sql_explanation)

    return ctx


def regenerate_sql_with_error(
    ctx: 'Ask3Context',
    presenter: 'Ask3Presenter',
    error_message: str,
    llm_manager=None
) -> 'Ask3Context':
    """
    Regenerate SQL after validation or execution error.

    Uses LLM to analyze the error and generate corrected SQL.

    Args:
        ctx: Ask3Context with failed SQL
        presenter: For progress output
        error_message: Error message from validation or execution
        llm_manager: LLMManager instance (optional)

    Returns:
        Updated context with corrected sql
    """
    # Import here to avoid circular imports
    from ....functions.sql_generation import recover_from_error
    from ....llm_manager import LLMManager

    if llm_manager is None:
        llm_manager = LLMManager()

    presenter.retry_info(ctx.retry_count, ctx.max_retries)

    # Try to recover
    result = recover_from_error(
        nl_question=ctx.refined_question or ctx.question,
        failed_sql=ctx.sql,
        error_message=error_message,
        filtered_schema=ctx.schema_formatted,
        database_engine=ctx.db_type,
        rows_returned=0,
        execution_time_ms=0.0,
        llm_manager=llm_manager
    )

    if not result.get('success'):
        error = result.get('error', 'Recovery failed')
        logger.error(f"SQL recovery failed: {error}")
        return ctx

    corrected_sql = result.get('corrected_sql', '')
    if corrected_sql:
        ctx.sql = corrected_sql
        explanation = result.get('explanation', '')
        presenter.sql_generated(ctx.sql, explanation)

    return ctx


def _track_llm_call(ctx: 'Ask3Context', phase: str, **kwargs) -> None:
    """Track LLM call for debugging and cost analysis."""
    try:
        ctx.add_llm_call(
            prompt=kwargs.get('prompt', ''),
            response=kwargs.get('response', ''),
            tokens=kwargs.get('tokens', 0),
            latency_ms=kwargs.get('latency_ms', 0),
            model=kwargs.get('model', 'unknown'),
            phase=phase
        )
    except Exception as e:
        logger.warning(f"Failed to track LLM call: {e}")

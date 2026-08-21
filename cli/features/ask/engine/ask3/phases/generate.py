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


logger = logging.getLogger(__name__)


def generate_sql(
    ctx: "Ask3Context", presenter: "Ask3Presenter", llm_manager=None
) -> "Ask3Context":
    """
    Generate SQL from natural language question.

    Uses refined question if clarifications were collected,
    otherwise uses original question. Includes conversation context
    for follow-up questions if available.

    Args:
        ctx: Ask3Context with question and schema
        presenter: For progress output
        llm_manager: LLMManager instance (optional, creates default)

    Returns:
        Updated context with sql and sql_explanation populated
    """
    ctx.phase = "generate"
    presenter.generating_sql()

    # Import here to avoid circular imports
    # Path: lib/engines/ask3/phases/generate.py -> lib/functions/, lib/llm_manager/
    from features.ask.engine.ask3.phases.schema import COMPACT_SCHEMA_FORMAT_VERSION
    from features.ask.sql_generation import generate_sql_from_nl
    from shared.llm_manager import LLMManager

    if llm_manager is None:
        llm_manager = LLMManager()

    # Use refined question if available
    question = ctx.refined_question or ctx.question

    # Prepend conversation context if available (for follow-up questions)
    if ctx.conversation_context:
        question = f"{ctx.conversation_context}\nCurrent question: {question}"

    # Generate SQL
    result = generate_sql_from_nl(
        nl_question=question,
        filtered_schema=ctx.schema_formatted,
        database_engine=ctx.db_type,
        target_database=ctx.target,
        llm_manager=llm_manager,
        provided_context=ctx.provided_context,
        schema_format=ctx.schema_format,
        compact_fallback_schema=ctx.schema_compact_fallback,
        compact_fallback_format=COMPACT_SCHEMA_FORMAT_VERSION,
        matched_database_values=ctx.matched_database_values,
        callback=lambda **kw: _track_llm_call(ctx, "generate", **kw),
    )

    ctx.schema_prompt_utf8_bytes = result.get("prompt_utf8_bytes", 0)
    ctx.schema_context_fallback_reason = result.get(
        "schema_context_fallback_reason", ""
    )
    if result.get("schema_context_fallback_used"):
        ctx.schema_formatted = ctx.schema_compact_fallback
        ctx.schema_format = result.get("schema_format", COMPACT_SCHEMA_FORMAT_VERSION)
        ctx.schema_context_fallback_used = True
    ctx.schema_compact_fallback = ""

    if not result.get("success"):
        error = result.get("error", "Unknown error")
        error_code = result.get("schema_request_failure_code") or None
        error_category = "model-limit" if error_code else None
        logger.error(f"SQL generation failed: {error}")
        ctx.mark_error(error, code=error_code, category=error_category)
        presenter.error(error)
        return ctx

    ctx.generated_sql = result.get("sql", "")
    ctx.generation_confidence = result.get("confidence", 1.0)
    ctx.sql_explanation = result.get("explanation", "")
    ctx.generation_response = result.get("raw_response", {})

    if result.get("cannot_answer", False):
        assumptions = result.get("assumptions", [])
        reason = result.get("cannot_answer_reason") or "unspecified"
        missing_schema = result.get("missing_schema", [])
        details = [*assumptions, *missing_schema]
        explanation = "; ".join(details) if details else reason
        ctx.mark_error(f"Cannot answer this question ({reason}). {explanation}")
        presenter.error(ctx.error_message)
        return ctx

    ctx.sql = ctx.generated_sql

    if not ctx.sql:
        ctx.mark_error("LLM returned empty SQL")
        presenter.error("LLM returned empty SQL")
        return ctx

    # Show generated SQL
    presenter.sql_generated(ctx.sql, ctx.sql_explanation)

    return ctx


def regenerate_sql_with_error(
    ctx: "Ask3Context", presenter: "Ask3Presenter", error_message: str, llm_manager=None
) -> "Ask3Context":
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
    from features.ask.sql_generation import recover_from_error
    from shared.llm_manager import LLMManager

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
        llm_manager=llm_manager,
    )

    if not result.get("success"):
        error = result.get("error", "Recovery failed")
        logger.error(f"SQL recovery failed: {error}")
        return ctx

    corrected_sql = result.get("corrected_sql", "")
    if corrected_sql:
        ctx.generated_sql = corrected_sql
        ctx.sql = corrected_sql
        explanation = result.get("explanation", "")
        presenter.sql_generated(ctx.sql, explanation)

    return ctx


def repair_validation_error(
    ctx: "Ask3Context",
    presenter: "Ask3Presenter",
    error_message: str,
    llm_manager=None,
) -> "Ask3Context":
    """Apply one narrowly scoped LLM repair for deterministic validation errors."""
    from features.ask.sql_generation import repair_sql_after_validation
    from shared.llm_manager import LLMManager

    if llm_manager is None:
        llm_manager = LLMManager()
    result = repair_sql_after_validation(
        nl_question=ctx.refined_question or ctx.question,
        failed_sql=ctx.sql or "",
        error_message=error_message,
        filtered_schema=ctx.schema_formatted,
        database_engine=ctx.db_type,
        llm_manager=llm_manager,
        provided_context=ctx.provided_context,
        matched_database_values=ctx.matched_database_values,
        callback=lambda **kw: _track_llm_call(ctx, "validation_repair", **kw),
    )
    if not result.get("success"):
        logger.warning("SQL validation repair failed: %s", result.get("error"))
        return ctx
    ctx.generated_sql = result["sql"]
    ctx.sql = result["sql"]
    ctx.sql_explanation = result["explanation"]
    ctx.generation_response = result.get("raw_response", {})
    presenter.sql_generated(ctx.sql, ctx.sql_explanation)
    return ctx


def _track_llm_call(ctx: "Ask3Context", phase: str, **kwargs) -> None:
    """Track LLM call for debugging and cost analysis."""
    try:
        ctx.add_llm_call(
            prompt=kwargs.get("prompt", ""),
            response=kwargs.get("response", ""),
            tokens=kwargs.get("tokens", 0),
            latency_ms=kwargs.get("latency_ms", 0),
            model=kwargs.get("model", "unknown"),
            phase=phase,
        )
    except Exception as e:
        logger.warning(f"Failed to track LLM call: {e}")

"""
Escalation Logic - Decides when to switch from linear flow to agent mode.

The linear flow handles ~80% of queries efficiently. This module detects
when the linear flow is struggling and agent exploration would help.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:
    from .context import Ask3Context

logger = logging.getLogger(__name__)


# Escalation reason constants
class EscalationReason:
    """Constants for escalation reasons."""
    ZERO_ROWS = 'zero_rows'
    LOW_CONFIDENCE = 'low_confidence'
    VALIDATION_EXHAUSTED = 'validation_exhausted'
    SCHEMA_EXHAUSTED = 'schema_exhausted'
    EXECUTION_ERROR = 'execution_error'
    USER_REQUEST = 'user_request'


def should_escalate(ctx: 'Ask3Context') -> Tuple[bool, str]:
    """
    Check if we should escalate from linear flow to agent mode.

    Checks multiple conditions in priority order and returns the first
    matching escalation reason.

    Args:
        ctx: The Ask3Context after linear flow execution

    Returns:
        Tuple of (should_escalate: bool, reason: str)
    """
    # Check 1: Zero rows returned after successful execution
    if _check_zero_rows(ctx):
        logger.info("Escalation trigger: zero rows returned")
        return True, EscalationReason.ZERO_ROWS

    # Check 2: Low confidence from SQL generation
    if _check_low_confidence(ctx):
        logger.info("Escalation trigger: low confidence")
        return True, EscalationReason.LOW_CONFIDENCE

    # Check 3: Validation retries exhausted
    if _check_validation_exhausted(ctx):
        logger.info("Escalation trigger: validation retries exhausted")
        return True, EscalationReason.VALIDATION_EXHAUSTED

    # Check 4: Schema expansion exhausted without success
    if _check_schema_exhausted(ctx):
        logger.info("Escalation trigger: schema expansion exhausted")
        return True, EscalationReason.SCHEMA_EXHAUSTED

    # Check 5: Execution error (not schema-related, which triggers retry)
    if _check_execution_error(ctx):
        logger.info("Escalation trigger: execution error")
        return True, EscalationReason.EXECUTION_ERROR

    return False, ""


def _check_zero_rows(ctx: 'Ask3Context') -> bool:
    """
    Check if query executed successfully but returned zero rows.

    This often indicates a semantic misunderstanding - the query ran but
    doesn't match what the user actually wanted.
    """
    if not ctx.execution_result:
        return False

    # Only trigger if no error (successful execution)
    if ctx.execution_result.error:
        return False

    return ctx.execution_result.row_count == 0


def _check_low_confidence(ctx: 'Ask3Context') -> bool:
    """
    Check if LLM expressed low confidence in its interpretation.

    Low confidence suggests the LLM isn't sure how to interpret the question
    with the current schema - agent exploration could help.
    """
    if not ctx.generation_response:
        return False

    # Check confidence in sql_generation section
    sql_gen = ctx.generation_response.get('sql_generation', {})
    confidence = sql_gen.get('confidence', 1.0)

    # Threshold: below 0.3 is considered low confidence
    return confidence < 0.3


def _check_validation_exhausted(ctx: 'Ask3Context') -> bool:
    """
    Check if we've exhausted validation retries with errors remaining.

    If we've tried max_retries times and still have validation errors,
    the linear approach isn't working.
    """
    return (
        ctx.retry_count >= ctx.max_retries and
        ctx.has_validation_errors()
    )


def _check_schema_exhausted(ctx: 'Ask3Context') -> bool:
    """
    Check if schema expansion is exhausted without resolving issues.

    If we've expanded schema max times but still have low confidence or
    validation errors, agent exploration might find a better approach.
    """
    if ctx.schema_expansion_count < ctx.max_schema_expansions:
        return False

    # Exhausted expansions AND still have issues
    has_issues = (
        ctx.has_validation_errors() or
        _check_low_confidence(ctx)
    )

    return has_issues


def _check_execution_error(ctx: 'Ask3Context') -> bool:
    """
    Check for execution errors that weren't schema-related.

    Schema-related errors (column not found, etc.) trigger retries in the
    linear flow. Other errors might benefit from agent exploration.
    """
    if not ctx.execution_result or not ctx.execution_result.error:
        return False

    error = ctx.execution_result.error.lower()

    # Schema-related errors are handled by retry logic, not escalation
    schema_patterns = [
        'column', 'does not exist', 'relation', 'undefined',
        'unknown column', 'table', "doesn't exist"
    ]

    for pattern in schema_patterns:
        if pattern in error:
            return False

    # Other errors (timeout, syntax, etc.) might benefit from agent
    return True


def format_escalation_message(reason: str) -> str:
    """
    Get a user-friendly message for the escalation reason.

    Args:
        reason: The escalation reason constant

    Returns:
        Human-readable explanation
    """
    messages = {
        EscalationReason.ZERO_ROWS: (
            "The query executed successfully but returned no results. "
            "Switching to exploration mode to investigate the data."
        ),
        EscalationReason.LOW_CONFIDENCE: (
            "I'm not confident about how to interpret your question with "
            "the current schema. Let me explore further."
        ),
        EscalationReason.VALIDATION_EXHAUSTED: (
            "I'm having trouble finding the right tables and columns. "
            "Let me explore the schema more thoroughly."
        ),
        EscalationReason.SCHEMA_EXHAUSTED: (
            "The available schema doesn't seem to have what I need. "
            "Let me investigate the data structure more carefully."
        ),
        EscalationReason.EXECUTION_ERROR: (
            "The query encountered an error. Let me try a different approach."
        ),
        EscalationReason.USER_REQUEST: (
            "Entering exploration mode as requested."
        ),
    }

    return messages.get(reason, "Switching to exploration mode.")

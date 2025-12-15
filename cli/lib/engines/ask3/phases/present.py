"""
Phase 6: Results Presentation

Displays query results to the user.
This is the final phase in the linear flow.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context import Ask3Context
    from ..presenter import Ask3Presenter

from ..types import Status

logger = logging.getLogger(__name__)


def present_results(
    ctx: 'Ask3Context',
    presenter: 'Ask3Presenter'
) -> 'Ask3Context':
    """
    Present query results to the user.

    Args:
        ctx: Ask3Context with execution_result populated
        presenter: For output

    Returns:
        Updated context (status finalized)
    """
    ctx.phase = 'present'

    # Check if we have results
    if not ctx.execution_result:
        presenter.error("No execution results to display")
        return ctx

    # Check for execution errors
    if ctx.execution_result.error:
        presenter.execution_error(ctx.execution_result.error)
        return ctx

    # Display results
    presenter.execution_result(
        columns=ctx.execution_result.columns,
        rows=ctx.execution_result.rows,
        time_ms=ctx.execution_result.execution_time_ms,
        truncated=ctx.execution_result.truncated
    )

    # Finalize success status if not already set
    if ctx.status == Status.PENDING:
        ctx.mark_success()

    return ctx


def summarize_session(ctx: 'Ask3Context', presenter: 'Ask3Presenter') -> None:
    """
    Display a summary of the session.

    Shows:
    - Total LLM calls and tokens
    - Execution time
    - Final status
    """
    if not ctx.verbose:
        return

    presenter.info(f"\nSession Summary:")
    presenter.info(f"  Status: {ctx.status}")
    presenter.info(f"  LLM Calls: {len(ctx.llm_calls)}")
    presenter.info(f"  Total Tokens: {ctx.total_tokens}")
    presenter.info(f"  LLM Time: {ctx.total_llm_time_ms:.0f}ms")

    if ctx.execution_result:
        presenter.info(f"  Query Time: {ctx.execution_result.execution_time_ms:.0f}ms")
        presenter.info(f"  Rows: {ctx.execution_result.row_count}")

    if ctx.retry_count > 0:
        presenter.info(f"  Retries: {ctx.retry_count}")

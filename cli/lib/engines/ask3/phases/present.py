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


def prompt_save_query(ctx: 'Ask3Context', presenter: 'Ask3Presenter') -> None:
    """
    Prompt user to save the query to the registry for later analysis.

    Only prompts if:
    - Query executed successfully
    - Not in non-interactive mode
    """
    # Skip if non-interactive or no successful query
    if ctx.no_interactive:
        return
    if ctx.status != Status.SUCCESS:
        return
    if not ctx.sql:
        return

    try:
        # Ask user if they want to save
        presenter._print("")
        response = input("Save this query to registry for later analysis? [y/N]: ").strip().lower()

        if response not in ('y', 'yes'):
            return

        # Get optional name
        name = input("Query name (optional, press Enter to skip): ").strip()

        # Import registry here to avoid circular imports
        from ....query_registry import QueryRegistry

        registry = QueryRegistry()
        query_hash, is_new = registry.add_query(
            sql=ctx.sql,
            tag=name if name else "",
            source="ask",
            target=ctx.target
        )

        if is_new:
            if name:
                presenter._print(f"\n[green]Query saved as '{name}' (hash: {query_hash[:8]})[/green]"
                               if presenter.use_rich else f"\nQuery saved as '{name}' (hash: {query_hash[:8]})")
            else:
                presenter._print(f"\n[green]Query saved (hash: {query_hash[:8]})[/green]"
                               if presenter.use_rich else f"\nQuery saved (hash: {query_hash[:8]})")
        else:
            existing = registry.get_query(query_hash)
            existing_name = existing.tag if existing and existing.tag else None
            if existing_name:
                presenter._print(f"\n[yellow]Query already in registry as '{existing_name}' (hash: {query_hash[:8]})[/yellow]"
                               if presenter.use_rich else f"\nQuery already in registry as '{existing_name}' (hash: {query_hash[:8]})")
            else:
                presenter._print(f"\n[yellow]Query already in registry (hash: {query_hash[:8]})[/yellow]"
                               if presenter.use_rich else f"\nQuery already in registry (hash: {query_hash[:8]})")

        presenter._print("\nNext steps:")
        if name:
            presenter._print(f"  - Analyze: rdst analyze --tag {name}")
            presenter._print(f"  - Run again: rdst query run {name}")
        else:
            presenter._print(f"  - Analyze: rdst analyze --hash {query_hash[:8]}")
            presenter._print(f"  - Run again: rdst query run {query_hash[:8]}")

    except KeyboardInterrupt:
        presenter._print("\nSkipped saving.")
    except Exception as e:
        logger.warning(f"Failed to save query: {e}")

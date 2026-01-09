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

# Import UI system for consistent styling
from lib.ui import StyleTokens, Prompt, QueryPanel, NextSteps

logger = logging.getLogger(__name__)


def present_results(ctx: "Ask3Context", presenter: "Ask3Presenter") -> "Ask3Context":
    """
    Present query results to the user.

    Args:
        ctx: Ask3Context with execution_result populated
        presenter: For output

    Returns:
        Updated context (status finalized)
    """
    ctx.phase = "present"

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
        truncated=ctx.execution_result.truncated,
    )

    # Finalize success status if not already set
    if ctx.status == Status.PENDING:
        ctx.mark_success()

    return ctx


def summarize_session(ctx: "Ask3Context", presenter: "Ask3Presenter") -> None:
    """
    Display a summary of the session.

    Shows:
    - Total LLM calls and tokens
    - Execution time
    - Final status
    """
    if not ctx.verbose:
        return

    presenter.info("\nSession Summary:")
    presenter.info(f"  Status: {ctx.status}")
    presenter.info(f"  LLM Calls: {len(ctx.llm_calls)}")
    presenter.info(f"  Total Tokens: {ctx.total_tokens}")
    presenter.info(f"  LLM Time: {ctx.total_llm_time_ms:.0f}ms")

    if ctx.execution_result:
        presenter.info(f"  Query Time: {ctx.execution_result.execution_time_ms:.0f}ms")
        presenter.info(f"  Rows: {ctx.execution_result.row_count}")

    if ctx.retry_count > 0:
        presenter.info(f"  Retries: {ctx.retry_count}")


def prompt_save_query(ctx: "Ask3Context", presenter: "Ask3Presenter") -> None:
    """
    Prompt user to save the query to the registry for later analysis.

    Flow:
    1. Check if query already exists (by hash) - if so, show info and return (no prompt)
    2. Generate auto-name from the question
    3. Single prompt to confirm/rename

    Only runs if:
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
        # Import registry utilities here to avoid circular imports
        from ....query_registry import QueryRegistry, hash_sql, generate_query_name

        registry = QueryRegistry()

        # Check if query already exists BEFORE prompting
        query_hash = hash_sql(ctx.sql)
        existing = registry.get_query(query_hash)

        if existing:
            # Query already exists - no prompt needed, just inform
            existing_name = existing.tag if existing.tag else None
            presenter._print("")
            if existing_name:
                presenter._print(
                    f"[{StyleTokens.SECONDARY}]Query already in registry as '{existing_name}' (hash: {query_hash[:8]})[/{StyleTokens.SECONDARY}]"
                )
            else:
                presenter._print(
                    f"[{StyleTokens.SECONDARY}]Query already in registry (hash: {query_hash[:8]})[/{StyleTokens.SECONDARY}]"
                )

            # Show next steps with existing name/hash
            name_ref = existing_name or query_hash[:8]
            steps = NextSteps(
                [
                    (f"rdst analyze --name {name_ref}", "Analyze query"),
                    (f"rdst query run {name_ref}", "Run again"),
                ]
            )
            presenter._console.print(steps)
            return

        # New query - generate auto-name and prompt
        # Get existing names for collision detection
        existing_names = {e.tag for e in registry.list_queries() if e.tag}
        auto_name = generate_query_name(ctx.question, existing_names)

        # Display the query that will be saved using QueryPanel
        presenter._print("")
        presenter._console.print(
            QueryPanel(ctx.sql, title="Query to save", border_style=StyleTokens.SUCCESS)
        )
        presenter._print("")

        response = (
            Prompt.ask(f"Save as '{auto_name}'? [Y/n/rename]", default="y")
            .strip()
            .lower()
        )

        if response in ("n", "no"):
            return

        if response in ("r", "rename"):
            custom_name = Prompt.ask("Enter name", default="").strip()
            if custom_name:
                auto_name = custom_name

        # Save the query
        _, _ = registry.add_query(
            sql=ctx.sql, tag=auto_name, source="ask", target=ctx.target
        )

        presenter._print(
            f"\n[{StyleTokens.SUCCESS}]Query saved as {auto_name!r} (hash: {query_hash[:8]})[/{StyleTokens.SUCCESS}]"
        )

        # Show next steps
        steps = NextSteps(
            [
                (f"rdst analyze --name {auto_name}", "Analyze query"),
                (f"rdst query run {auto_name}", "Run again"),
            ]
        )
        presenter._console.print(steps)

    except KeyboardInterrupt:
        presenter._print("\nSkipped saving.")
    except Exception as e:
        logger.warning(f"Failed to save query: {e}")

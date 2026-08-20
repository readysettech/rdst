"""
Ask3Engine - Hybrid Linear + Agent Orchestrator

Uses a fast linear flow for most queries, with agent escalation for complex cases:
  SCHEMA → CLARIFY → GENERATE ↔ VALIDATE → EXECUTE → [AGENT?] → PRESENT

Key improvements:
- Single source of truth (Ask3Context) instead of dual state
- Pure functions for each phase (easy to test)
- Clear retry logic for validation errors
- Agent escalation when linear flow struggles (zero rows, low confidence, etc.)
- All output via Presenter (separated concerns)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from . import escalation
from .context import Ask3Context
from .phases import (
    clarify_question,
    execute_query,
    generate_sql,
    load_schema,
    present_results,
    validate_sql,
)
from .phases.generate import regenerate_sql_with_error
from .phases.present import prompt_save_query, summarize_session
from .phases.validate import build_error_message
from .presenter import Ask3Presenter
from .types import DbType, SchemaSource, Status

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Feature flag for automatic legacy-agent escalation
AGENT_ENABLED = os.getenv("RDST_ASK3_AGENT_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)


class Ask3Engine:
    """
    Hybrid linear + agent orchestrator for natural language to SQL conversion.

    Uses a fast linear flow for most queries (~80%), escalating to an
    intelligent agent when the linear flow struggles.

    Usage:
        engine = Ask3Engine()
        result = engine.run("Find users named John", target="mydb", target_config={...})

    Or with custom components:
        engine = Ask3Engine(
            presenter=CustomPresenter(),
            llm_manager=my_llm,
            semantic_manager=my_semantic_mgr
        )

    Agent mode can be disabled via environment variable:
        RDST_ASK3_AGENT_ENABLED=false
    """

    def __init__(
        self,
        presenter: Optional[Ask3Presenter] = None,
        llm_manager=None,
        semantic_manager=None,
        db_executor=None,
    ):
        """
        Initialize the engine.

        Args:
            presenter: Output handler. Defaults to Ask3Presenter.
            llm_manager: LLM client. Creates default if None.
            semantic_manager: Semantic layer manager. Creates default if None.
            db_executor: Custom database executor (for testing).
        """
        self.presenter = presenter or Ask3Presenter()
        self.llm_manager = llm_manager
        self.semantic_manager = semantic_manager
        self.db_executor = db_executor
        self._agent = None  # Lazy init for agent

    def run(
        self,
        question: str,
        target: str,
        target_config: Optional[Dict[str, Any]] = None,
        db_type: str = DbType.POSTGRESQL,
        max_retries: int = 2,
        timeout_seconds: int = 600,  # 10 minutes default
        max_rows: int = 100,
        verbose: bool = False,
        no_interactive: bool = False,
        conversation_context: str = "",
        pre_execute_validator: Callable[[str], None] | None = None,
        allow_agent_escalation: bool = True,
        enforce_result_limit: bool = True,
        raise_unexpected_errors: bool = False,
    ) -> Ask3Context:
        """
        Run the complete ask3 flow.

        Args:
            question: Natural language question
            target: Target database name
            target_config: Database connection configuration
            db_type: Database type ('postgresql' or 'mysql')
            max_retries: Max SQL generation retries on validation error
            timeout_seconds: Query timeout
            max_rows: Max rows to return
            verbose: Show detailed progress
            no_interactive: Apply only interpretations that pass the ranked resolver
            conversation_context: Previous conversation history for follow-up questions
            pre_execute_validator: Optional callback to validate SQL before execution.
                Takes SQL string, raises exception if blocked. Used by guards.
            allow_agent_escalation: Permit automatic escalation after linear execution
            enforce_result_limit: Add and cap result LIMIT clauses during validation
            raise_unexpected_errors: Propagate unexpected phase exceptions to the caller

        Returns:
            Ask3Context with all results (check ctx.status for outcome)
        """
        # Initialize context
        ctx = Ask3Context(
            question=question,
            target=target,
            db_type=db_type,
            target_config=target_config,
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
            max_rows=max_rows,
            verbose=verbose,
            no_interactive=no_interactive,
            allow_agent_escalation=allow_agent_escalation,
            enforce_result_limit=enforce_result_limit,
            conversation_context=conversation_context,
        )

        # Update presenter verbosity
        self.presenter.verbose = verbose

        try:
            # Phase 1: Load or initialize the complete schema.
            ctx = load_schema(ctx, self.presenter, self.semantic_manager)
            if ctx.status == Status.ERROR:
                return ctx

            # Phase 2: Clarify question
            ctx = clarify_question(ctx, self.presenter, self.llm_manager)
            if ctx.status == Status.CANCELLED:
                return ctx
            if ctx.status == Status.ERROR:
                return ctx

            # Phases 3-4: Generate and validate SQL (with retry loop)
            ctx = self._generate_and_validate(ctx, pre_execute_validator)
            if ctx.status == Status.ERROR:
                return ctx

            # Phase 5: Execute query
            ctx = execute_query(ctx, self.presenter, self.db_executor)

            # Handle execution errors with retry
            if ctx.execution_result and ctx.execution_result.error:
                if (
                    self._is_schema_error(ctx.execution_result.error)
                    and ctx.can_retry()
                ):
                    ctx = self._retry_on_execution_error(ctx)

            # Check for agent escalation (after execute, before present)
            if (
                AGENT_ENABLED
                and ctx.allow_agent_escalation
                and ctx.status != Status.ERROR
            ):
                should_escalate, reason = escalation.should_escalate(ctx)
                if should_escalate:
                    self.presenter.info(escalation.format_escalation_message(reason))
                    ctx = self._run_agent(ctx, reason)

                    # If agent found a new query, execute it
                    if ctx.sql and ctx.status == Status.PENDING:
                        ctx = execute_query(ctx, self.presenter, self.db_executor)

            # Phase 6: Present results
            ctx = present_results(ctx, self.presenter)

            # Show session summary if verbose
            summarize_session(ctx, self.presenter)

            # Offer to save query to registry
            prompt_save_query(ctx, self.presenter)

        except KeyboardInterrupt:
            self.presenter.cancelled()
            ctx.mark_cancelled()

        except Exception as e:
            if raise_unexpected_errors:
                raise
            logger.exception(f"Unexpected error in ask3 engine: {e}")
            ctx.mark_error(str(e))
            self.presenter.error(str(e))

        # Hint: suggest schema commands if semantic layer was not used
        if ctx.schema_source == SchemaSource.DATABASE:
            self.presenter.schema_hint(ctx.target)

        return ctx

    def _generate_and_validate(
        self,
        ctx: Ask3Context,
        pre_execute_validator: Callable[[str], None] | None = None,
    ) -> Ask3Context:
        """
        Generate SQL with a bounded validation retry loop.

        Flow:
        1. Generate SQL (skip if just regenerated with error feedback)
        2. Validate SQL
        3. If valid AND pre_execute_validator: run guard check
           - If blocked AND can_retry(): regenerate with guard feedback, goto 2
        4. If validation errors AND can_retry(): regenerate with error, goto 2
        """
        skip_generate = False  # Skip generate_sql after regenerate_sql_with_error

        while True:
            # Generate SQL (skip if we just regenerated with error feedback)
            if not skip_generate:
                ctx = generate_sql(ctx, self.presenter, self.llm_manager)
                if ctx.status == Status.ERROR:
                    return ctx

            skip_generate = False  # Reset for next iteration

            # Validate SQL
            ctx = validate_sql(ctx, self.presenter)

            # If valid, run pre-execute guard check
            if not ctx.has_validation_errors():
                if pre_execute_validator and ctx.sql:
                    try:
                        pre_execute_validator(ctx.sql)
                    except Exception as e:
                        # Guard blocked - treat like validation error, can retry
                        if ctx.can_retry():
                            ctx.increment_retry()
                            error_msg = f"Query blocked by guard: {e}"
                            self.presenter.warning(error_msg)
                            ctx = regenerate_sql_with_error(
                                ctx, self.presenter, error_msg, self.llm_manager
                            )
                            skip_generate = True  # Don't overwrite the regenerated SQL
                            continue  # Retry validation/guard with regenerated SQL
                        else:
                            ctx.mark_error(f"Guard blocked: {e}")
                            return ctx
                break  # Valid and guard passed (or no guard)

            # If we can't retry, fail
            if not ctx.can_retry():
                error_msg = build_error_message(ctx.validation_errors)
                ctx.mark_error(
                    f"Validation failed after {ctx.max_retries} retries: {error_msg}"
                )
                break

            # Retry with error feedback
            ctx.increment_retry()
            error_msg = build_error_message(ctx.validation_errors)
            ctx = regenerate_sql_with_error(
                ctx, self.presenter, error_msg, self.llm_manager
            )
            skip_generate = True  # Don't overwrite the regenerated SQL

        return ctx

    def _retry_on_execution_error(self, ctx: Ask3Context) -> Ask3Context:
        """
        Retry SQL generation after execution error.

        Only retries for schema-related errors (wrong column/table names).
        """
        error = ctx.execution_result.error
        ctx.increment_retry()

        # Regenerate SQL with error context
        ctx = regenerate_sql_with_error(ctx, self.presenter, error, self.llm_manager)
        if ctx.status == Status.ERROR:
            return ctx

        # Re-validate
        ctx = validate_sql(ctx, self.presenter)
        if ctx.has_validation_errors():
            return ctx

        # Re-execute
        ctx = execute_query(ctx, self.presenter, self.db_executor)

        return ctx

    def _run_agent(self, ctx: Ask3Context, reason: str) -> Ask3Context:
        """
        Run agent exploration mode for complex queries.

        Args:
            ctx: Ask3Context from linear flow
            reason: Why we escalated (e.g., 'zero_rows', 'low_confidence')

        Returns:
            Updated Ask3Context with agent's findings
        """
        from .agent import Ask3Agent
        from .agent_context import AgentExplorationContext

        # Lazy init the agent
        if self._agent is None:
            # Ensure LLM manager is initialized
            if self.llm_manager is None:
                from shared.llm_manager import LLMManager

                self.llm_manager = LLMManager()

            self._agent = Ask3Agent(
                llm_manager=self.llm_manager,
                presenter=self.presenter,
                db_executor=self.db_executor,
            )

        # Create agent context from linear context
        agent_ctx = AgentExplorationContext.from_ask3_context(ctx, reason)

        # Run agent
        return self._agent.run(agent_ctx)

    def _is_schema_error(self, error_message: str) -> bool:
        """
        Check if error is schema-related (column/table not found).

        These errors are worth retrying with LLM correction.
        """
        error_lower = error_message.lower()

        # PostgreSQL patterns
        pg_patterns = ["column", "does not exist", "relation", "undefined"]
        # MySQL patterns
        mysql_patterns = ["unknown column", "table", "doesn't exist"]

        for pattern in pg_patterns + mysql_patterns:
            if pattern in error_lower:
                return True

        return False


def create_engine(verbose: bool = False) -> Ask3Engine:
    """
    Factory function to create a configured Ask3Engine.

    Args:
        verbose: Enable verbose output

    Returns:
        Configured Ask3Engine instance
    """
    presenter = Ask3Presenter(verbose=verbose)
    return Ask3Engine(presenter=presenter)

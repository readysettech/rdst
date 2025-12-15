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
from typing import TYPE_CHECKING, Optional, Dict, Any

from .context import Ask3Context
from .presenter import Ask3Presenter
from .types import Status, DbType, SchemaExpansionRequest
from .phases import (
    load_schema,
    filter_schema,
    clarify_question,
    generate_sql,
    validate_sql,
    execute_query,
    present_results,
    expand_schema,
)
from .phases.generate import regenerate_sql_with_error
from .phases.validate import build_error_message
from .phases.present import summarize_session
from . import escalation

if TYPE_CHECKING:
    from ..semantic_layer.manager import SemanticLayerManager
    from ..llm_manager import LLMManager

logger = logging.getLogger(__name__)

# Feature flag for agent mode
AGENT_ENABLED = os.getenv("RDST_ASK3_AGENT_ENABLED", "true").lower() in ("true", "1", "yes")


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
        db_executor=None
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
        timeout_seconds: int = 30,
        max_rows: int = 100,
        verbose: bool = False,
        no_interactive: bool = False,
        agent_mode: bool = False
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
            no_interactive: Auto-select first option for clarifications
            agent_mode: Skip linear flow and go directly to agent exploration

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
            no_interactive=no_interactive
        )

        # Update presenter verbosity
        self.presenter.verbose = verbose

        try:
            # Phase 1: Load schema (always needed, even for agent mode)
            ctx = load_schema(ctx, self.presenter, self.semantic_manager)
            if ctx.status == Status.ERROR:
                return ctx

            # Capture full table list before filtering (for expansion/agent)
            if ctx.schema_info and ctx.schema_info.tables:
                ctx.all_available_tables = list(ctx.schema_info.tables.keys())

            # Direct agent mode - skip linear flow entirely
            if agent_mode and AGENT_ENABLED:
                self.presenter.info("Entering direct agent exploration mode")
                ctx = self._run_agent(ctx, escalation.EscalationReason.USER_REQUEST)

                # Execute the agent's query if found
                if ctx.sql and ctx.status == Status.PENDING:
                    ctx = execute_query(ctx, self.presenter, self.db_executor)

                # Present results
                ctx = present_results(ctx, self.presenter)
                summarize_session(ctx, self.presenter)
                return ctx

            # Normal linear flow continues below...

            # Phase 1.5: Filter schema to relevant tables
            ctx = filter_schema(ctx, self.presenter, self.llm_manager)

            # Phase 2: Clarify question
            ctx = clarify_question(ctx, self.presenter, self.llm_manager)
            if ctx.status == Status.CANCELLED:
                return ctx
            if ctx.status == Status.ERROR:
                return ctx

            # Phases 3-4: Generate and validate SQL (with retry loop)
            ctx = self._generate_and_validate(ctx)
            if ctx.status == Status.ERROR:
                return ctx

            # Phase 5: Execute query
            ctx = execute_query(ctx, self.presenter, self.db_executor)

            # Handle execution errors with retry
            if ctx.execution_result and ctx.execution_result.error:
                if self._is_schema_error(ctx.execution_result.error) and ctx.can_retry():
                    ctx = self._retry_on_execution_error(ctx)

            # Check for agent escalation (after execute, before present)
            if AGENT_ENABLED and ctx.status != Status.ERROR:
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

        except KeyboardInterrupt:
            self.presenter.cancelled()
            ctx.mark_cancelled()

        except Exception as e:
            logger.exception(f"Unexpected error in ask3 engine: {e}")
            ctx.mark_error(str(e))
            self.presenter.error(str(e))

        return ctx

    def _generate_and_validate(self, ctx: Ask3Context) -> Ask3Context:
        """
        Generate SQL with validation retry loop AND schema expansion loop.

        Flow:
        1. Generate SQL
        2. Check for schema_insufficient signal from LLM
           - If true AND can_expand_schema(): expand schema, goto 1
        3. Validate SQL
        4. If validation errors AND can_retry(): regenerate with error, goto 3
        """
        while True:
            # Generate SQL
            ctx = generate_sql(ctx, self.presenter, self.llm_manager)
            if ctx.status == Status.ERROR:
                return ctx

            # Check for schema expansion request
            expansion_request = self._detect_expansion_request(ctx)
            if expansion_request and ctx.can_expand_schema():
                self.presenter.info(
                    f"LLM signaled schema insufficiency "
                    f"(expansion {ctx.schema_expansion_count + 1}/{ctx.max_schema_expansions})"
                )

                # Perform expansion
                prev_table_count = len(ctx.filtered_tables)
                ctx = expand_schema(
                    ctx,
                    self.presenter,
                    expansion_request.missing_concepts,
                    expansion_request.requested_tables
                )

                # Only retry if expansion actually added tables
                if len(ctx.filtered_tables) > prev_table_count:
                    continue  # Retry generation with expanded schema
                else:
                    self.presenter.warning("Expansion found no new tables, proceeding with current schema")

            # Validate SQL
            ctx = validate_sql(ctx, self.presenter)

            # If valid, we're done
            if not ctx.has_validation_errors():
                break

            # If we can't retry, fail
            if not ctx.can_retry():
                error_msg = build_error_message(ctx.validation_errors)
                ctx.mark_error(f"Validation failed after {ctx.max_retries} retries: {error_msg}")
                break

            # Retry with error feedback
            ctx.increment_retry()
            error_msg = build_error_message(ctx.validation_errors)
            ctx = regenerate_sql_with_error(ctx, self.presenter, error_msg, self.llm_manager)

        return ctx

    def _detect_expansion_request(self, ctx: Ask3Context) -> Optional[SchemaExpansionRequest]:
        """
        Detect if LLM is signaling schema insufficiency.

        Only uses generate phase response. Clarify phase hints are informational only -
        the generate phase LLM has final say on whether schema is sufficient.

        Returns SchemaExpansionRequest if expansion is needed, None otherwise.
        """
        if not ctx.generation_response:
            return None

        analysis = ctx.generation_response.get('analysis', {})

        # Check explicit signal from generate phase
        if analysis.get('schema_insufficient', False):
            return SchemaExpansionRequest(
                missing_concepts=analysis.get('missing_concepts', []),
                requested_tables=analysis.get('requested_tables', []),
                reason='LLM signaled schema insufficient'
            )

        # Backup: very low confidence without clarification needed
        sql_gen = ctx.generation_response.get('sql_generation', {})
        confidence = sql_gen.get('confidence', 1.0)
        needs_clarification = analysis.get('needs_clarification', False)

        if confidence <= 0.1 and not needs_clarification:
            # LLM gave up but didn't ask for clarification -> likely schema issue
            logger.debug(f"Implicit expansion signal: confidence={confidence}, no clarification")
            return SchemaExpansionRequest(
                missing_concepts=[],
                requested_tables=[],
                reason=f'Implicit: confidence {confidence} without clarification request'
            )

        return None

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
                from ...llm_manager import LLMManager
                self.llm_manager = LLMManager()

            self._agent = Ask3Agent(
                llm_manager=self.llm_manager,
                presenter=self.presenter,
                db_executor=self.db_executor
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
        pg_patterns = ['column', 'does not exist', 'relation', 'undefined']
        # MySQL patterns
        mysql_patterns = ['unknown column', 'table', "doesn't exist"]

        for pattern in pg_patterns + mysql_patterns:
            if pattern in error_lower:
                return True

        return False


def create_engine(
    verbose: bool = False,
    use_rich: bool = True
) -> Ask3Engine:
    """
    Factory function to create a configured Ask3Engine.

    Args:
        verbose: Enable verbose output
        use_rich: Use Rich library for formatting

    Returns:
        Configured Ask3Engine instance
    """
    presenter = Ask3Presenter(verbose=verbose, use_rich=use_rich)
    return Ask3Engine(presenter=presenter)

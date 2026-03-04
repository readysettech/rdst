"""AskService - Unified streaming text-to-SQL service.

Fully event-driven: yields events during execution, pauses at
ClarificationNeededEvent for user input, then resumes via resume().

Usage:
    service = AskService()

    # Start execution
    async for event in service.ask(input, options):
        if isinstance(event, AskClarificationNeededEvent):
            # Collect user input (CLI: prompt, Web: return to client)
            answers = collect_answers(event.questions)
            # Resume with answers
            async for event in service.resume(event.session_id, answers):
                handle(event)
        else:
            handle(event)
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, List, Optional

from .types import (
    AskEvent,
    AskInput,
    AskOptions,
    AskStatusEvent,
    AskSchemaLoadedEvent,
    AskClarificationNeededEvent,
    AskSqlGeneratedEvent,
    AskResultEvent,
    AskErrorEvent,
    AskInterpretation,
    AskClarificationQuestion,
)

if TYPE_CHECKING:
    from ..engines.ask3.context import Ask3Context

# Session storage for paused executions awaiting clarification
_sessions: Dict[str, "Ask3Context"] = {}


class AskService:
    """Unified streaming service for text-to-SQL.

    Fully event-driven architecture:
    - Yields events during execution
    - Pauses at ClarificationNeededEvent
    - Resume with resume() method after collecting user input

    Both CLI and Web use the same interface - they differ only in
    how they handle ClarificationNeededEvent:
    - CLI: Prompts user immediately, calls resume()
    - Web: Returns to client, waits for follow-up request
    """

    def __init__(self) -> None:
        """Initialize the ask service."""
        pass

    async def ask(
        self,
        input: AskInput,
        options: AskOptions,
    ) -> AsyncGenerator[AskEvent, None]:
        """Execute text-to-SQL and yield events.

        Yields events during execution. When clarification is needed,
        yields ClarificationNeededEvent and pauses. Call resume() with
        user's answers to continue.

        Yields:
            AskStatusEvent: Progress updates
            AskSchemaLoadedEvent: Schema info
            AskClarificationNeededEvent: User input needed (pauses here)
            AskSqlGeneratedEvent: SQL ready
            AskResultEvent: Final results
            AskErrorEvent: Error occurred
        """
        from ..engines.ask3 import Ask3Context, Status
        from ..engines.ask3.phases import (
            load_schema,
            filter_schema,
        )

        try:
            # New session - load config first
            yield AskStatusEvent(
                type="status",
                phase="config",
                message="Loading configuration...",
            )

            target_name, target_config = await self._load_config(input.target)
            if target_name is None:
                yield AskErrorEvent(
                    type="error",
                    message="No target specified and no default configured",
                )
                return

            if target_config is None:
                yield AskErrorEvent(
                    type="error",
                    message=f"Target '{target_name}' not found",
                )
                return

            # Determine database type
            engine_type = target_config.get("engine", "postgresql").lower()
            db_type = "mysql" if "mysql" in engine_type else "postgresql"

            # Create context
            ctx = Ask3Context(
                question=input.question,
                target=target_name,
                db_type=db_type,
                target_config=target_config,
                timeout_seconds=options.timeout_seconds,
                verbose=options.verbose,
                no_interactive=True,  # We handle interaction via events
                dry_run=options.dry_run,
            )

            # Phase 1: Load schema
            yield AskStatusEvent(
                type="status",
                phase="schema",
                message="Loading schema...",
            )
            ctx = await asyncio.to_thread(load_schema, ctx, _NullPresenter(), None)

            if ctx.status == Status.ERROR:
                yield AskErrorEvent(
                    type="error",
                    message=ctx.error_message or "Failed to load schema",
                    phase="schema",
                )
                return

            # Guard: stop if schema is null or empty (rdst-9cq.7)
            if not ctx.schema_info or not ctx.schema_info.tables:
                yield AskErrorEvent(
                    type="error",
                    message=ctx.error_message or "No schema loaded — check target connection and credentials",
                    phase="schema",
                )
                return

            # Yield schema info
            tables = list(ctx.schema_info.tables.keys())
            ctx.all_available_tables = tables
            yield AskSchemaLoadedEvent(
                type="schema_loaded",
                source=ctx.schema_source,
                table_count=len(tables),
                tables=tables[:10],
            )

            # Phase 1.5: Filter schema
            yield AskStatusEvent(
                type="status",
                phase="filter",
                message="Filtering relevant tables...",
            )
            ctx = await asyncio.to_thread(filter_schema, ctx, _NullPresenter(), None)

            # Phase 2: Detect ambiguities
            yield AskStatusEvent(
                type="status",
                phase="clarify",
                message="Analyzing question...",
            )
            ctx, interpretations, ambiguities = await asyncio.to_thread(
                self._detect_ambiguities, ctx
            )

            if ctx.status == Status.ERROR:
                yield AskErrorEvent(
                    type="error",
                    message=ctx.error_message or "Failed to analyze question",
                    phase="clarify",
                )
                return

            # Check if clarification is needed
            if ambiguities and not options.no_interactive:
                # Save session and yield event - execution pauses here
                session_id = str(uuid.uuid4())
                _sessions[session_id] = ctx

                # Build interpretations for display
                event_interpretations = [
                    AskInterpretation(
                        id=interp.id,
                        description=interp.description,
                        likelihood=interp.likelihood,
                        assumptions=interp.assumptions,
                    )
                    for interp in interpretations
                ]

                # Build questions from ambiguities
                event_questions = [
                    AskClarificationQuestion(
                        id=amb.category,
                        question=amb.clarifying_question,
                        options=amb.possible_interpretations,
                    )
                    for amb in ambiguities
                ]

                yield AskClarificationNeededEvent(
                    type="clarification_needed",
                    session_id=session_id,
                    interpretations=event_interpretations,
                    questions=event_questions,
                )
                return  # Pause - consumer will call resume()

            # No clarification needed (or no_interactive) - continue to generate
            async for event in self._run_from_generate(ctx):
                yield event

        except Exception as e:
            yield AskErrorEvent(
                type="error",
                message=str(e),
            )

    async def resume(
        self,
        session_id: str,
        clarification_answers: Optional[Dict[str, str]] = None,
    ) -> AsyncGenerator[AskEvent, None]:
        """Resume execution after clarification.

        Args:
            session_id: Session ID from ClarificationNeededEvent
            clarification_answers: Dict mapping question_id -> selected answer

        Yields:
            Remaining events (AskSqlGeneratedEvent, AskResultEvent, etc.)
        """
        if session_id not in _sessions:
            yield AskErrorEvent(
                type="error",
                message=f"Session '{session_id}' not found or expired",
            )
            return

        ctx = _sessions.pop(session_id)

        # Apply clarification answers
        if clarification_answers:
            ctx.clarifications.update(clarification_answers)
            # Build refined question with clarifications
            ctx.refined_question = self._build_refined_question(
                ctx.question, clarification_answers
            )

        # Continue from generate phase
        async for event in self._run_from_generate(ctx):
            yield event

    async def _run_from_generate(
        self,
        ctx: "Ask3Context",
    ) -> AsyncGenerator[AskEvent, None]:
        """Continue execution from generate phase."""
        from ..engines.ask3 import Status
        from ..engines.ask3.phases import (
            generate_sql,
            validate_sql,
            execute_query,
        )

        # Phase 3: Generate SQL
        yield AskStatusEvent(
            type="status",
            phase="generate",
            message="Generating SQL...",
        )
        ctx = await asyncio.to_thread(generate_sql, ctx, _NullPresenter(), None)

        if ctx.status == Status.ERROR:
            yield AskErrorEvent(
                type="error",
                message=ctx.error_message or "Failed to generate SQL",
                phase="generate",
            )
            return

        # Yield generated SQL
        yield AskSqlGeneratedEvent(
            type="sql_generated",
            sql=ctx.sql or "",
            explanation=ctx.sql_explanation,
        )

        # Phase 4: Validate SQL
        yield AskStatusEvent(
            type="status",
            phase="validate",
            message="Validating SQL...",
        )
        ctx = await asyncio.to_thread(validate_sql, ctx, _NullPresenter())

        if ctx.has_validation_errors():
            errors = [str(e) for e in ctx.validation_errors]
            yield AskErrorEvent(
                type="error",
                message=f"SQL validation failed: {'; '.join(errors)}",
                phase="validate",
            )
            return

        # Phase 5: Execute query (skip if dry_run)
        if ctx.dry_run:
            yield AskResultEvent(
                type="result",
                success=True,
                sql=ctx.sql or "",
                rows=[],
                columns=[],
                row_count=0,
                execution_time_ms=0.0,
                llm_calls=len(ctx.llm_calls),
                total_tokens=ctx.total_tokens,
            )
            return

        yield AskStatusEvent(
            type="status",
            phase="execute",
            message="Executing query...",
        )
        ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), None)

        # Yield final result
        if ctx.execution_result and not ctx.execution_result.error:
            ctx.mark_success()
            yield AskResultEvent(
                type="result",
                success=True,
                sql=ctx.sql or "",
                rows=ctx.execution_result.rows,
                columns=ctx.execution_result.columns,
                row_count=ctx.execution_result.row_count,
                execution_time_ms=ctx.execution_result.execution_time_ms,
                llm_calls=len(ctx.llm_calls),
                total_tokens=ctx.total_tokens,
            )
        else:
            error_msg = ctx.execution_result.error if ctx.execution_result else "Execution failed"
            yield AskErrorEvent(
                type="error",
                message=error_msg,
                phase="execute",
            )

    def _detect_ambiguities(
        self, ctx: "Ask3Context"
    ) -> tuple["Ask3Context", List, List]:
        """Detect ambiguities without prompting for clarification.

        Returns:
            Tuple of (ctx, interpretations, ambiguities)
        """
        from ..functions.ambiguity_detection import detect_ambiguities
        from ..engines.ask3.types import Interpretation
        from ..llm_manager import LLMManager

        ctx.phase = "clarify"
        llm_manager = LLMManager()

        result = detect_ambiguities(
            nl_question=ctx.question,
            filtered_schema=ctx.schema_formatted,
            database_engine=ctx.db_type,
            llm_manager=llm_manager,
            preference_tree=None,
            confidence_threshold=0.85,
        )

        if not result.get("success"):
            return ctx, [], []

        report = result.get("report")
        if not report:
            return ctx, [], []

        if report.overall_confidence >= 0.85 and not report.requires_clarification:
            return ctx, [], []

        if not report.ambiguities:
            return ctx, [], []

        # Store actual ambiguities for clarification collection
        ambiguities = report.ambiguities

        # Build interpretations for display
        interpretations = []
        seen = set()
        for i, amb in enumerate(ambiguities, 1):
            for j, interp_opt in enumerate(amb.possible_interpretations):
                if isinstance(interp_opt, str):
                    description = interp_opt
                    likelihood = 0.5
                else:
                    description = getattr(interp_opt, "text", str(interp_opt))
                    likelihood = getattr(interp_opt, "likelihood", 0.5)

                if description not in seen:
                    seen.add(description)
                    interpretations.append(
                        Interpretation(
                            id=i * 10 + j,
                            description=description,
                            assumptions=[amb.reason] if amb.reason else [],
                            sql_approach=amb.category,
                            likelihood=likelihood,
                        )
                    )
                    if len(interpretations) >= 5:
                        break
            if len(interpretations) >= 5:
                break

        ctx.interpretations = interpretations
        return ctx, interpretations, ambiguities

    def _build_refined_question(self, original: str, clarifications: Dict[str, str]) -> str:
        """Build refined question incorporating clarifications."""
        if not clarifications:
            return original

        clarification_text = "; ".join(
            f"{category}: {answer}"
            for category, answer in clarifications.items()
        )

        return f"{original} ({clarification_text})"

    async def _load_config(
        self, target: Optional[str]
    ) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Load target configuration."""
        from ..cli.rdst_cli import TargetsConfig

        cfg = TargetsConfig()
        cfg.load()
        target_name = target or cfg.get_default()

        if not target_name:
            return None, None

        target_config = cfg.get(target_name)
        return target_name, target_config


class _NullPresenter:
    """Presenter that does nothing - events handle all output."""

    verbose = False

    def __getattr__(self, name):
        return lambda *args, **kwargs: None

"""Unified streaming text-to-SQL service."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, AsyncGenerator, Optional

from .ask3 import (
    create_context,
    create_interpretation,
    execute_query,
    filter_schema,
    generate_sql,
    get_status_enum,
    load_schema,
    validate_sql,
)
from shared.config.targets import create_targets_config
from shared.llm_manager import LLMManager
from shared.query_registry import QueryRegistry, generate_query_name

from .ambiguity_detection import detect_ambiguities
from .events import (
    AskClarificationNeededEvent,
    AskErrorEvent,
    AskEvent,
    AskResultEvent,
    AskSchemaLoadedEvent,
    AskSqlGeneratedEvent,
    AskStatusEvent,
)
from .models import AskClarificationQuestion, AskInput, AskInterpretation, AskOptions, AskPhase

_sessions: dict[str, Any] = {}


class AskService:
    """Unified streaming service for text-to-SQL."""

    async def ask(
        self,
        input: AskInput,
        options: AskOptions,
    ) -> AsyncGenerator[AskEvent, None]:
        try:
            yield AskStatusEvent(
                type="status",
                phase=AskPhase.CONFIG,
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

            engine_type = target_config.get("engine", "postgresql").lower()
            db_type = "mysql" if "mysql" in engine_type else "postgresql"

            ctx = create_context(
                question=input.question,
                target=target_name,
                db_type=db_type,
                target_config=target_config,
                timeout_seconds=options.timeout_seconds,
                verbose=options.verbose,
                no_interactive=True,
                dry_run=options.dry_run,
            )
            Status = get_status_enum()

            yield AskStatusEvent(
                type="status",
                phase=AskPhase.SCHEMA,
                message="Loading schema...",
            )
            ctx = await asyncio.to_thread(load_schema, ctx, _NullPresenter(), None)

            if ctx.status == Status.ERROR:
                yield AskErrorEvent(
                    type="error",
                    message=ctx.error_message or "Failed to load schema",
                    phase=AskPhase.SCHEMA,
                )
                return

            if not ctx.schema_info or not ctx.schema_info.tables:
                yield AskErrorEvent(
                    type="error",
                    message=ctx.error_message
                    or "No schema loaded — check target connection and credentials",
                    phase=AskPhase.SCHEMA,
                )
                return

            tables = list(ctx.schema_info.tables.keys())
            ctx.all_available_tables = tables
            yield AskSchemaLoadedEvent(
                type="schema_loaded",
                source=ctx.schema_source,
                table_count=len(tables),
                tables=tables[:10],
            )

            yield AskStatusEvent(
                type="status",
                phase=AskPhase.FILTER,
                message="Filtering relevant tables...",
            )
            ctx = await asyncio.to_thread(filter_schema, ctx, _NullPresenter(), None)

            yield AskStatusEvent(
                type="status",
                phase=AskPhase.CLARIFY,
                message="Analyzing question...",
            )
            ctx, interpretations, ambiguities = await asyncio.to_thread(
                self._detect_ambiguities, ctx
            )

            if ctx.status == Status.ERROR:
                yield AskErrorEvent(
                    type="error",
                    message=ctx.error_message or "Failed to analyze question",
                    phase=AskPhase.CLARIFY,
                )
                return

            if ambiguities and not options.no_interactive:
                session_id = str(uuid.uuid4())
                _sessions[session_id] = ctx
                yield AskClarificationNeededEvent(
                    type="clarification_needed",
                    session_id=session_id,
                    interpretations=[
                        AskInterpretation(
                            id=interp.id,
                            description=interp.description,
                            likelihood=interp.likelihood,
                            assumptions=interp.assumptions,
                        )
                        for interp in interpretations
                    ],
                    questions=[
                        AskClarificationQuestion(
                            id=amb.category,
                            question=amb.clarifying_question,
                            options=amb.possible_interpretations,
                        )
                        for amb in ambiguities
                    ],
                )
                return

            async for event in self._run_from_generate(ctx):
                yield event

        except Exception as exc:
            yield AskErrorEvent(type="error", message=str(exc))

    async def resume(
        self,
        session_id: str,
        clarification_answers: Optional[dict[str, str]] = None,
    ) -> AsyncGenerator[AskEvent, None]:
        if session_id not in _sessions:
            yield AskErrorEvent(
                type="error",
                message=f"Session '{session_id}' not found or expired",
            )
            return

        ctx = _sessions.pop(session_id)
        if clarification_answers:
            ctx.clarifications.update(clarification_answers)
            ctx.refined_question = self._build_refined_question(
                ctx.question, clarification_answers
            )

        async for event in self._run_from_generate(ctx):
            yield event

    async def _run_from_generate(
        self,
        ctx: Any,
    ) -> AsyncGenerator[AskEvent, None]:
        Status = get_status_enum()

        yield AskStatusEvent(
            type="status",
            phase=AskPhase.GENERATE,
            message="Generating SQL...",
        )
        ctx = await asyncio.to_thread(generate_sql, ctx, _NullPresenter(), None)
        if ctx.status == Status.ERROR:
            yield AskErrorEvent(
                type="error",
                message=ctx.error_message or "Failed to generate SQL",
                phase=AskPhase.GENERATE,
            )
            return

        yield AskSqlGeneratedEvent(
            type="sql_generated",
            sql=ctx.sql or "",
            explanation=ctx.sql_explanation,
        )

        yield AskStatusEvent(
            type="status",
            phase=AskPhase.VALIDATE,
            message="Validating SQL...",
        )
        ctx = await asyncio.to_thread(validate_sql, ctx, _NullPresenter())
        if ctx.has_validation_errors():
            errors = [str(e) for e in ctx.validation_errors]
            yield AskErrorEvent(
                type="error",
                message=f"SQL validation failed: {'; '.join(errors)}",
                phase=AskPhase.VALIDATE,
            )
            return

        if ctx.dry_run:
            qhash, qtag = self._auto_save_query(ctx)
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
                query_hash=qhash,
                query_tag=qtag,
            )
            return

        yield AskStatusEvent(
            type="status",
            phase=AskPhase.EXECUTE,
            message="Executing query...",
        )
        ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), None)

        if ctx.execution_result and not ctx.execution_result.error:
            ctx.mark_success()
            qhash, qtag = self._auto_save_query(ctx)
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
                query_hash=qhash,
                query_tag=qtag,
            )
            return

        error_msg = (
            ctx.execution_result.error if ctx.execution_result else "Execution failed"
        )
        yield AskErrorEvent(
            type="error",
            message=error_msg,
            phase=AskPhase.EXECUTE,
        )

    def _auto_save_query(self, ctx: Any) -> tuple[str, str]:
        if not ctx.sql:
            return "", ""
        try:
            registry = QueryRegistry()
            existing_names = {entry.tag for entry in registry.list_queries() if entry.tag}
            tag = generate_query_name(ctx.question, existing_names)
            query_hash, _ = registry.add_query(
                sql=ctx.sql,
                source="ask",
                target=ctx.target or "",
                tag=tag,
            )
            return query_hash, tag
        except Exception:
            logging.getLogger(__name__).debug(
                "Failed to auto-save query", exc_info=True
            )
            return "", ""

    def _detect_ambiguities(self, ctx: Any) -> tuple[Any, list, list]:
        ctx.phase = AskPhase.CLARIFY.value
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
        if not report or (
            report.overall_confidence >= 0.85 and not report.requires_clarification
        ):
            return ctx, [], []
        if not report.ambiguities:
            return ctx, [], []

        ambiguities = report.ambiguities
        interpretations = []
        seen = set()
        for i, ambiguity in enumerate(ambiguities, 1):
            for j, option in enumerate(ambiguity.possible_interpretations):
                if isinstance(option, str):
                    description = option
                    likelihood = 0.5
                else:
                    description = getattr(option, "text", str(option))
                    likelihood = getattr(option, "likelihood", 0.5)
                if description in seen:
                    continue
                seen.add(description)
                interpretations.append(
                    create_interpretation(
                        id=i * 10 + j,
                        description=description,
                        assumptions=[ambiguity.reason] if ambiguity.reason else [],
                        sql_approach=ambiguity.category,
                        likelihood=likelihood,
                    )
                )
                if len(interpretations) >= 5:
                    break
            if len(interpretations) >= 5:
                break

        ctx.interpretations = interpretations
        return ctx, interpretations, ambiguities

    def _build_refined_question(
        self, original: str, clarifications: dict[str, str]
    ) -> str:
        if not clarifications:
            return original
        clarification_text = "; ".join(
            f"{category}: {answer}" for category, answer in clarifications.items()
        )
        return f"{original} ({clarification_text})"

    async def _load_config(
        self, target: Optional[str]
    ) -> tuple[Optional[str], Optional[dict[str, Any]]]:
        cfg = create_targets_config()
        cfg.load()
        target_name = target or cfg.get_default()
        if not target_name:
            return None, None
        return target_name, cfg.get(target_name)


class _NullPresenter:
    """Presenter that does nothing."""

    verbose = False

    def __getattr__(self, name):
        return lambda *args, **kwargs: None

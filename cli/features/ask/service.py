"""Unified streaming text-to-SQL service."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncGenerator, Callable, Collection, MutableMapping
from dataclasses import dataclass, field
from typing import Any

from shared.config.targets import create_targets_config
from shared.llm_manager import LLMManager
from shared.query_registry import QueryRegistry, generate_query_name

from .aggregate_domain_normalization import (
    normalize_context as normalize_all_rows_aggregate_context,
)
from .ambiguity_detection import (
    NON_INTERACTIVE_CLARIFICATION_POLICY,
    RANKED_RESOLVER_POLICY,
    detect_ambiguities,
    detect_missing_intent_ambiguities,
)
from .ask3 import (
    create_context,
    create_interpretation,
    execute_query,
    generate_sql,
    get_status_enum,
    load_schema,
    validate_sql,
)
from .categorical_normalization import (
    normalize_context as normalize_categorical_context,
)
from .correction_intent_routing import (
    CORRECTION_INTENT_ACTIONABILITY_VERSION,
    CORRECTION_INTENT_PRODUCT_SCOPE,
    CORRECTION_INTENT_ROUTING_PURPOSE,
    CORRECTION_INTENTS,
    route_correction_intent,
    selected_correction_intents,
)
from .derived_metric_normalization import (
    normalize_context as normalize_scalar_derived_metric_context,
)
from .dual_candidate import generate_and_select
from .engine.ask3.phases.generate import repair_validation_error
from .engine.ask3.phases.validate import build_error_message
from .events import (
    AskClarificationNeededEvent,
    AskErrorEvent,
    AskEvent,
    AskResultEvent,
    AskSchemaLoadedEvent,
    AskSqlGeneratedEvent,
    AskStatusEvent,
)
from .models import (
    AskClarificationQuestion,
    AskInput,
    AskInterpretation,
    AskOptions,
    AskPhase,
)
from .numeric_normalization import normalize_context as normalize_numeric_context
from .ranking_normalization import normalize_context as normalize_ranking_context
from .shared_entity_scope_normalization import (
    normalize_context as normalize_shared_entity_scope_context,
)


@dataclass
class _PendingAskSession:
    context: Any
    persist_query: bool
    raise_unexpected_errors: bool = False
    clarification_context: dict[str, dict[str, str]] = field(default_factory=dict)


_sessions: dict[str, _PendingAskSession] = {}


_CORRECTION_STATE_FIELDS = (
    "sql",
    "generated_sql",
    "sql_explanation",
    "generation_confidence",
    "status",
    "error_message",
    "error_code",
    "error_category",
    "phase",
    "validation_errors",
    "limit_added",
    "limit_reduced",
    "explicit_ratio_normalization",
    "scalar_derived_metric_normalization",
    "all_rows_aggregate_normalization",
    "extremum_entity_normalization",
    "unbounded_categorical_normalization",
    "shared_entity_scope_normalization",
)
_CORRECTION_COPIED_FIELDS = frozenset(
    {
        "validation_errors",
        "explicit_ratio_normalization",
        "scalar_derived_metric_normalization",
        "all_rows_aggregate_normalization",
        "extremum_entity_normalization",
        "unbounded_categorical_normalization",
        "shared_entity_scope_normalization",
    }
)


def _snapshot_correction_state(ctx: Any) -> dict[str, Any]:
    return {
        name: (
            copy.deepcopy(getattr(ctx, name))
            if name in _CORRECTION_COPIED_FIELDS
            else getattr(ctx, name)
        )
        for name in _CORRECTION_STATE_FIELDS
    }


def _restore_correction_state(ctx: Any, snapshot: dict[str, Any]) -> None:
    for name, value in snapshot.items():
        setattr(
            ctx,
            name,
            copy.deepcopy(value)
            if name in _CORRECTION_COPIED_FIELDS
            else value,
        )


def _sql_candidates_equivalent(left: str, right: str, dialect: str) -> bool:
    if left == right:
        return True
    try:
        import sqlglot

        read_dialect = "postgres" if dialect == "postgresql" else dialect or None
        left_tree = sqlglot.parse_one(left, dialect=read_dialect)
        right_tree = sqlglot.parse_one(right, dialect=read_dialect)
        return left_tree == right_tree
    except Exception:
        return " ".join(left.split()) == " ".join(right.split())


class AskService:
    """Unified streaming service for text-to-SQL."""

    def __init__(
        self,
        *,
        llm_manager=None,
        semantic_manager=None,
        db_executor=None,
        targets_config_factory: Callable[[], Any] | None = None,
        query_registry_factory: Callable[[], Any] | None = None,
        session_store: MutableMapping[str, _PendingAskSession] | None = None,
        persist_queries: bool = True,
        phase_observer: Callable[[str, Any], None] | None = None,
        diagnostic_schema_formatter_fn: Callable[[Any], str] | None = None,
        correction_intent_routing_enabled: bool = False,
        correction_intent_routing_shadow: bool = False,
        correction_intent_routing_fn: Callable[..., Any] | None = None,
        correction_intent_routing_intent_scope: Collection[str] | None = None,
        dual_candidate_selection_enabled: bool = False,
        dual_candidate_selection_fn: Callable[..., Any] | None = None,
        explicit_ratio_normalization_enabled: bool = True,
        explicit_ratio_normalization_fn: Callable[..., Any] | None = None,
        scalar_derived_metric_normalization_enabled: bool = True,
        scalar_derived_metric_normalization_fn: Callable[..., Any] | None = None,
        all_rows_aggregate_normalization_enabled: bool = True,
        all_rows_aggregate_normalization_fn: Callable[..., Any] | None = None,
        extremum_entity_normalization_enabled: bool = True,
        extremum_entity_normalization_fn: Callable[..., Any] | None = None,
        unbounded_categorical_normalization_enabled: bool = True,
        unbounded_categorical_normalization_fn: Callable[..., Any] | None = None,
        shared_entity_scope_normalization_enabled: bool = True,
        shared_entity_scope_normalization_fn: Callable[..., Any] | None = None,
    ):
        self._llm_manager = llm_manager
        self._semantic_manager = semantic_manager
        self._db_executor = db_executor
        self._targets_config_factory = targets_config_factory
        self._query_registry_factory = query_registry_factory
        self._session_store = session_store if session_store is not None else _sessions
        self._persist_queries = persist_queries
        self._phase_observer = phase_observer
        self._diagnostic_schema_formatter_fn = diagnostic_schema_formatter_fn
        self._correction_intent_routing_enabled = bool(
            correction_intent_routing_enabled or correction_intent_routing_shadow
        )
        self._correction_intent_routing_shadow = bool(
            correction_intent_routing_shadow
        )
        self._correction_intent_routing_fn = (
            correction_intent_routing_fn or route_correction_intent
        )
        if isinstance(correction_intent_routing_intent_scope, str):
            raise TypeError("correction intent scope must be a collection of names")
        requested_scope = (
            CORRECTION_INTENT_PRODUCT_SCOPE
            if correction_intent_routing_intent_scope is None
            else tuple(correction_intent_routing_intent_scope)
        )
        unknown_intents = set(requested_scope).difference(CORRECTION_INTENTS)
        if unknown_intents:
            raise ValueError(
                "unknown correction intent scope: "
                + ", ".join(sorted(unknown_intents))
            )
        if len(set(requested_scope)) != len(requested_scope):
            raise ValueError("correction intent scope must contain unique names")
        self._correction_intent_routing_intent_scope = tuple(
            intent for intent in CORRECTION_INTENTS if intent in requested_scope
        )
        self._dual_candidate_selection_enabled = bool(
            dual_candidate_selection_enabled
        )
        self._dual_candidate_selection_fn = (
            dual_candidate_selection_fn or generate_and_select
        )
        self._explicit_ratio_normalization_enabled = bool(
            explicit_ratio_normalization_enabled
        )
        self._explicit_ratio_normalization_fn = (
            explicit_ratio_normalization_fn or normalize_numeric_context
        )
        self._scalar_derived_metric_normalization_enabled = bool(
            scalar_derived_metric_normalization_enabled
        )
        self._scalar_derived_metric_normalization_fn = (
            scalar_derived_metric_normalization_fn
            or normalize_scalar_derived_metric_context
        )
        self._all_rows_aggregate_normalization_enabled = bool(
            all_rows_aggregate_normalization_enabled
        )
        self._all_rows_aggregate_normalization_fn = (
            all_rows_aggregate_normalization_fn
            or normalize_all_rows_aggregate_context
        )
        self._extremum_entity_normalization_enabled = bool(
            extremum_entity_normalization_enabled
        )
        self._extremum_entity_normalization_fn = (
            extremum_entity_normalization_fn or normalize_ranking_context
        )
        self._unbounded_categorical_normalization_enabled = bool(
            unbounded_categorical_normalization_enabled
        )
        self._unbounded_categorical_normalization_fn = (
            unbounded_categorical_normalization_fn or normalize_categorical_context
        )
        self._shared_entity_scope_normalization_enabled = bool(
            shared_entity_scope_normalization_enabled
        )
        self._shared_entity_scope_normalization_fn = (
            shared_entity_scope_normalization_fn
            or normalize_shared_entity_scope_context
        )

    def _observe(self, phase: AskPhase, ctx: Any) -> None:
        if self._phase_observer is not None:
            self._phase_observer(phase.value, ctx)

    @staticmethod
    def _database_error(
        message: str,
        phase: AskPhase,
        target: str,
        target_config: dict[str, Any],
    ) -> AskErrorEvent:
        from shared.api.ssh_errors import connectivity_error_payload

        failure = connectivity_error_payload(
            RuntimeError(message), target, target_config
        )
        return AskErrorEvent(
            type="error",
            message=failure["message"] if failure else message,
            phase=phase,
            code=failure["category"] if failure else None,
            category=failure["category"] if failure else None,
            target=target,
        )

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
                    message=(
                        "No target specified and no default target configured. "
                        "Run 'rdst configure add' to set one up."
                    ),
                )
                return

            if target_config is None:
                yield AskErrorEvent(
                    type="error",
                    message=f"Target '{target_name}' not found. Run 'rdst configure list' to see available targets",
                )
                return

            engine_type = target_config.get("engine", "postgresql").lower()
            db_type = "mysql" if "mysql" in engine_type else "postgresql"

            ctx = create_context(
                question=input.question,
                target=target_name,
                db_type=db_type,
                provided_context=input.provided_context,
                matched_database_values=input.matched_database_values,
                target_config=target_config,
                timeout_seconds=options.timeout_seconds,
                max_rows=options.max_rows,
                verbose=options.verbose,
                no_interactive=options.no_interactive,
                dry_run=options.dry_run,
                enforce_result_limit=options.enforce_result_limit,
            )
            Status = get_status_enum()

            yield AskStatusEvent(
                type="status",
                phase=AskPhase.SCHEMA,
                message="Loading or initializing schema...",
            )
            load_args = [ctx, _NullPresenter(), self._semantic_manager]
            if self._diagnostic_schema_formatter_fn is not None:
                load_args.append(self._diagnostic_schema_formatter_fn)
            ctx = await asyncio.to_thread(load_schema, *load_args)

            if ctx.status == Status.ERROR:
                self._observe(AskPhase.SCHEMA, ctx)
                yield self._database_error(
                    ctx.error_message or "Failed to load schema",
                    AskPhase.SCHEMA,
                    target_name,
                    target_config,
                )
                return

            if not ctx.schema_info or not ctx.schema_info.tables:
                self._observe(AskPhase.SCHEMA, ctx)
                yield AskErrorEvent(
                    type="error",
                    message=ctx.error_message
                    or "No schema loaded — check target connection and credentials",
                    phase=AskPhase.SCHEMA,
                )
                return

            self._observe(AskPhase.SCHEMA, ctx)
            tables = list(ctx.schema_info.tables.keys())
            yield AskSchemaLoadedEvent(
                type="schema_loaded",
                source=ctx.schema_source,
                table_count=len(tables),
                tables=tables[:10],
                target=ctx.target or "",
            )

            yield AskStatusEvent(
                type="status",
                phase=AskPhase.CLARIFY,
                message=(
                    "Checking required intent..."
                    if options.no_interactive
                    else "Analyzing question..."
                ),
            )
            if options.no_interactive:
                deterministic_ambiguities = self._check_non_interactive_intent(ctx)
                self._observe(AskPhase.CLARIFY, ctx)
                if deterministic_ambiguities:
                    yield AskErrorEvent(
                        type="error",
                        message=(
                            "The question is missing information required to generate "
                            "SQL safely. Run again interactively to answer the "
                            "clarification question."
                        ),
                        phase=AskPhase.CLARIFY,
                        code="clarification_required",
                        category="clarification_required",
                        target=target_name,
                    )
                    return
            else:
                ctx, interpretations, ambiguities = await asyncio.to_thread(
                    self._detect_ambiguities, ctx
                )
                self._observe(AskPhase.CLARIFY, ctx)
                if ctx.status == Status.ERROR:
                    yield AskErrorEvent(
                        type="error",
                        message=ctx.error_message or "Failed to analyze question",
                        phase=AskPhase.CLARIFY,
                    )
                    return
                if ambiguities:
                    questions = self._clarification_questions(ambiguities)
                    clarification_context = {
                        question.id: {
                            "ambiguity_id": ambiguity.id,
                            "term": ambiguity.term,
                            "question": ambiguity.clarifying_question,
                        }
                        for question, ambiguity in zip(questions, ambiguities)
                    }
                    session_id = str(uuid.uuid4())
                    self._session_store[session_id] = _PendingAskSession(
                        context=ctx,
                        persist_query=options.persist_query,
                        raise_unexpected_errors=options.raise_unexpected_errors,
                        clarification_context=clarification_context,
                    )
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
                        questions=questions,
                    )
                    return

            async for event in self._run_from_generate(
                ctx, persist_query=options.persist_query
            ):
                yield event

        except Exception as exc:
            if options.raise_unexpected_errors:
                raise
            yield AskErrorEvent(type="error", message=str(exc))

    async def resume(
        self,
        session_id: str,
        clarification_answers: dict[str, str] | None = None,
    ) -> AsyncGenerator[AskEvent, None]:
        pending = self._session_store.get(session_id)
        if pending is None:
            yield AskErrorEvent(
                type="error",
                message=f"Session '{session_id}' not found or expired",
            )
            return

        answers = clarification_answers or {}
        unknown_keys = sorted(set(answers) - set(pending.clarification_context))
        if unknown_keys:
            yield AskErrorEvent(
                type="error",
                message=(
                    "Clarification answers contain unknown question IDs: "
                    + ", ".join(unknown_keys)
                ),
                phase=AskPhase.CLARIFY,
                code="invalid_clarification_answer",
            )
            return
        invalid_keys = sorted(
            key
            for key, answer in answers.items()
            if not isinstance(answer, str) or not answer.strip()
        )
        if invalid_keys:
            yield AskErrorEvent(
                type="error",
                message=(
                    "Clarification answers must be non-empty text for: "
                    + ", ".join(invalid_keys)
                ),
                phase=AskPhase.CLARIFY,
                code="invalid_clarification_answer",
            )
            return

        self._session_store.pop(session_id, None)
        ctx = pending.context
        for answer_key, context in pending.clarification_context.items():
            answer = answers.get(answer_key)
            ctx.clarification_resolutions.append(
                {
                    "ambiguity_id": context["ambiguity_id"],
                    "answer_key": answer_key,
                    "term": context["term"],
                    "question": context["question"],
                    "action": "answer" if answer else "skip",
                    "answer": answer,
                    "source": "user",
                    "applied": bool(answer),
                }
            )
        if answers:
            ctx.clarifications.update(answers)
            ctx.refined_question = self._build_refined_question(
                ctx.question,
                answers,
                pending.clarification_context,
            )
        try:
            async for event in self._run_from_generate(
                ctx, persist_query=pending.persist_query
            ):
                yield event
        except Exception as exc:
            if pending.raise_unexpected_errors:
                raise
            yield AskErrorEvent(type="error", message=str(exc))

    def abandon(self, session_id: str) -> bool:
        """Discard a pending clarification session."""
        return self._session_store.pop(session_id, None) is not None

    async def _run_from_generate(
        self,
        ctx: Any,
        *,
        persist_query: bool = True,
    ) -> AsyncGenerator[AskEvent, None]:
        Status = get_status_enum()

        yield AskStatusEvent(
            type="status",
            phase=AskPhase.GENERATE,
            message="Generating SQL...",
        )
        ctx = await asyncio.to_thread(
            generate_sql, ctx, _NullPresenter(), self._llm_manager
        )
        self._observe(AskPhase.GENERATE, ctx)
        if ctx.status == Status.ERROR:
            yield AskErrorEvent(
                type="error",
                message=ctx.error_message or "Failed to generate SQL",
                phase=AskPhase.GENERATE,
                code=ctx.error_code,
                category=ctx.error_category,
                target=ctx.target,
            )
            return

        if self._dual_candidate_selection_enabled:
            ctx = await asyncio.to_thread(
                self._dual_candidate_selection_fn,
                ctx,
                self._llm_manager,
            )
            self._observe(AskPhase.GENERATE, ctx)

        if self._correction_intent_routing_enabled:
            ctx = await self._apply_correction_intent_routing(ctx)
            self._observe(AskPhase.GENERATE, ctx)

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
        self._observe(AskPhase.VALIDATE, ctx)
        if ctx.has_validation_errors():
            failed_sql = ctx.sql
            validation_message = build_error_message(ctx.validation_errors)
            ctx.increment_retry()
            yield AskStatusEvent(
                type="status",
                phase=AskPhase.VALIDATE,
                message="Repairing SQL after validation failure...",
            )
            ctx = await asyncio.to_thread(
                repair_validation_error,
                ctx,
                _NullPresenter(),
                validation_message,
                self._llm_manager,
            )
            if ctx.sql != failed_sql:
                yield AskSqlGeneratedEvent(
                    type="sql_generated",
                    sql=ctx.sql or "",
                    explanation=ctx.sql_explanation,
                )
            # increment_retry() clears the original errors. Always validate
            # again, including when repair failed or returned unchanged SQL.
            ctx = await asyncio.to_thread(validate_sql, ctx, _NullPresenter())
            self._observe(AskPhase.VALIDATE, ctx)
            if ctx.has_validation_errors():
                errors = [str(e) for e in ctx.validation_errors]
                yield AskErrorEvent(
                    type="error",
                    message=f"SQL validation failed: {'; '.join(errors)}",
                    phase=AskPhase.VALIDATE,
                )
                return

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
                query_hash="",
                query_tag="",
            )
            return

        yield AskStatusEvent(
            type="status",
            phase=AskPhase.EXECUTE,
            message="Executing query...",
        )
        ctx = await asyncio.to_thread(
            execute_query, ctx, _NullPresenter(), self._db_executor
        )
        self._observe(AskPhase.EXECUTE, ctx)

        if ctx.execution_result and not ctx.execution_result.error:
            ctx.mark_success()
            qhash, qtag = self._auto_save_query(ctx, persist_query=persist_query)
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
                limit_added=bool(getattr(ctx, "limit_added", False)),
            )
            return

        error_msg = (
            ctx.execution_result.error if ctx.execution_result else "Execution failed"
        )
        yield self._database_error(
            error_msg,
            AskPhase.EXECUTE,
            ctx.target,
            ctx.target_config or {},
        )

    async def _apply_selected_generation_normalizers(
        self,
        ctx: Any,
        selected_intents: Collection[str],
    ) -> Any:
        """Apply only the deterministic corrections selected by the router."""
        selected = frozenset(selected_intents)
        normalizers = (
            (
                {"percentage_output", "ratio_output"},
                self._explicit_ratio_normalization_enabled,
                self._explicit_ratio_normalization_fn,
            ),
            (
                {"scalar_difference_output"},
                self._scalar_derived_metric_normalization_enabled,
                self._scalar_derived_metric_normalization_fn,
            ),
            (
                {"all_rows_population"},
                self._all_rows_aggregate_normalization_enabled,
                self._all_rows_aggregate_normalization_fn,
            ),
            (
                {"entity_at_extremum"},
                self._extremum_entity_normalization_enabled,
                self._extremum_entity_normalization_fn,
            ),
            (
                {"all_matching_categories"},
                self._unbounded_categorical_normalization_enabled,
                self._unbounded_categorical_normalization_fn,
            ),
            (
                {"shared_scope_all_answers"},
                self._shared_entity_scope_normalization_enabled,
                self._shared_entity_scope_normalization_fn,
            ),
        )
        for routed_intents, enabled, normalizer in normalizers:
            if enabled and selected.intersection(routed_intents):
                ctx = await asyncio.to_thread(normalizer, ctx)
        return ctx

    async def _apply_correction_intent_routing(self, ctx: Any) -> Any:
        """Route once, then apply and validate only the selected corrections."""
        original_state = _snapshot_correction_state(ctx)
        original_sql = str(ctx.sql or "")
        original_hash = hashlib.sha256(original_sql.encode()).hexdigest()
        base_diagnostics = {
            "actionability_version": CORRECTION_INTENT_ACTIONABILITY_VERSION,
            "allowed_intents": list(self._correction_intent_routing_intent_scope),
            "router_called_before_application": True,
            "pre_normalizer_sql_sha256": original_hash,
            "baseline_sql_sha256": original_hash,
            "selected_sql_sha256": original_hash,
            "application_status": "not_selected",
            "selected_sql_applied": False,
        }
        if not original_sql.strip():
            ctx.correction_intent_routing = {
                **base_diagnostics,
                "status": "no_generated_sql",
                "verdict": "abstain",
                "selected_intent": "none",
                "selected_intents": [],
                "activation_allowed": False,
                "shadow": self._correction_intent_routing_shadow,
            }
            return ctx

        ctx = await asyncio.to_thread(
            self._route_correction_intent,
            ctx,
            proposed_sql=original_sql,
        )
        routing = {**ctx.correction_intent_routing, **base_diagnostics}
        selected_intents = selected_correction_intents(
            {**routing, "activation_allowed": True}
        )
        routing["selected_intent_set_sha256"] = (
            hashlib.sha256(
                json.dumps(list(selected_intents), separators=(",", ":")).encode()
            ).hexdigest()
            if selected_intents
            else ""
        )
        if not selected_intents:
            _restore_correction_state(ctx, original_state)
            ctx.correction_intent_routing = routing
            return ctx

        ctx.correction_intent_routing = {
            **routing,
            "status": "activate",
            "verdict": "activate",
            "activation_allowed": True,
            "selected_intent": (
                selected_intents[0] if len(selected_intents) == 1 else "none"
            ),
            "selected_intents": list(selected_intents),
        }
        try:
            candidate_ctx = await self._apply_selected_generation_normalizers(
                ctx,
                selected_intents,
            )
            candidate_state = _snapshot_correction_state(candidate_ctx)
            valid, issues = await self._preflight_correction_candidate(
                candidate_ctx,
                candidate_state,
            )
            candidate_sql = str(candidate_state.get("sql") or "")
            if not valid or not candidate_sql:
                routing.update(
                    {
                        "status": "selected_intent_set_not_applicable",
                        "activation_allowed": False,
                        "application_status": "invalid",
                        "application_issue_count": len(issues),
                    }
                )
            elif _sql_candidates_equivalent(
                candidate_sql,
                original_sql,
                ctx.db_type,
            ):
                routing.update(
                    {
                        "status": "selected_intent_set_not_applicable",
                        "activation_allowed": False,
                        "application_status": "equivalent",
                        "application_issue_count": 0,
                    }
                )
            else:
                candidate_hash = hashlib.sha256(candidate_sql.encode()).hexdigest()
                routing.update(
                    {
                        "application_status": "validated",
                        "application_issue_count": 0,
                        "candidate_sql_sha256": candidate_hash,
                        "selected_sql_sha256": (
                            original_hash
                            if self._correction_intent_routing_shadow
                            else candidate_hash
                        ),
                    }
                )
                if self._correction_intent_routing_shadow:
                    routing["activation_allowed"] = False
                    _restore_correction_state(ctx, original_state)
                else:
                    routing["activation_allowed"] = True
                    routing["selected_sql_applied"] = True
                    _restore_correction_state(ctx, candidate_state)
                ctx.correction_intent_routing = routing
                return ctx
        except Exception as exc:
            routing.update(
                {
                    "status": "selected_intent_set_not_applicable",
                    "activation_allowed": False,
                    "application_status": "error",
                    "application_error_kind": type(exc).__name__,
                }
            )

        _restore_correction_state(ctx, original_state)
        routing["selected_sql_sha256"] = original_hash
        ctx.correction_intent_routing = routing
        return ctx

    async def _preflight_correction_candidate(
        self,
        ctx: Any,
        candidate_state: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        """Validate a candidate without repair, execution, or database access."""
        if not ctx.get_schema_as_dict():
            return False, ["loaded schema is unavailable"]
        validation_ctx = copy.copy(ctx)
        _restore_correction_state(validation_ctx, candidate_state)
        validation_ctx.enforce_result_limit = False
        validation_ctx = await asyncio.to_thread(
            validate_sql,
            validation_ctx,
            _NullPresenter(),
        )
        issues = [str(error) for error in validation_ctx.validation_errors]
        if issues or not validation_ctx.sql:
            return False, issues or ["validated candidate SQL is unavailable"]
        candidate_state.clear()
        candidate_state.update(_snapshot_correction_state(validation_ctx))
        return True, []

    def _route_correction_intent(
        self,
        ctx: Any,
        *,
        proposed_sql: str | None = None,
    ) -> Any:
        """Classify correction intent without allowing the model to write SQL."""
        if not self._correction_intent_routing_enabled:
            ctx.correction_intent_routing = {
                "status": "disabled",
                "activation_allowed": False,
                "shadow": False,
            }
            return ctx
        try:
            llm_manager = self._llm_manager
            if llm_manager is None:
                llm_manager = LLMManager()
                self._llm_manager = llm_manager
            result = self._correction_intent_routing_fn(
                effective_question=ctx.refined_question or ctx.question,
                dialect=ctx.db_type,
                proposed_sql=(
                    proposed_sql if proposed_sql is not None else ctx.sql or ""
                ),
                llm_manager=llm_manager,
                callback=lambda **kwargs: ctx.add_llm_call(
                    phase=CORRECTION_INTENT_ROUTING_PURPOSE,
                    **kwargs,
                ),
                allowed_intents=self._correction_intent_routing_intent_scope,
                allowed_intent_sets=None,
            )
            if hasattr(result, "to_dict"):
                diagnostics = result.to_dict()
            elif isinstance(result, dict):
                diagnostics = dict(result)
            else:
                raise TypeError("correction intent router returned an invalid result")
            diagnostics["shadow"] = self._correction_intent_routing_shadow
            selected = selected_correction_intents(
                {**diagnostics, "activation_allowed": True}
            )
            selection_is_allowed = bool(selected) and all(
                intent in self._correction_intent_routing_intent_scope
                for intent in selected
            )
            diagnostics["activation_allowed"] = bool(
                not self._correction_intent_routing_shadow
                and diagnostics.get("status") == "activate"
                and diagnostics.get("verdict") == "activate"
                and selection_is_allowed
            )
            ctx.correction_intent_routing = diagnostics
        except Exception as exc:
            ctx.correction_intent_routing = {
                "status": "error",
                "verdict": "abstain",
                "selected_intent": "none",
                "selected_intents": [],
                "activation_allowed": False,
                "shadow": self._correction_intent_routing_shadow,
                "error_kind": type(exc).__name__,
            }
        return ctx

    def _auto_save_query(
        self, ctx: Any, *, persist_query: bool = True
    ) -> tuple[str, str]:
        if not ctx.sql or not persist_query or not self._persist_queries:
            return "", ""
        try:
            registry_factory = self._query_registry_factory or QueryRegistry
            registry = registry_factory()
            existing_names = {
                entry.tag for entry in registry.list_queries() if entry.tag
            }
            tag = generate_query_name(ctx.question, existing_names)
            query_hash, _ = registry.add_query(
                sql=ctx.sql,
                source="ask",
                target=ctx.target or "",
                tag=tag,
                question=ctx.question or "",
                ask_target=ctx.target or "",
            )
            return query_hash, tag
        except Exception:
            logging.getLogger(__name__).debug(
                "Failed to auto-save query", exc_info=True
            )
            return "", ""

    def _detect_ambiguities(self, ctx: Any) -> tuple[Any, list, list]:
        ctx.phase = AskPhase.CLARIFY.value
        llm_manager = self._llm_manager or LLMManager()
        ctx.clarification_policy = RANKED_RESOLVER_POLICY
        ctx.ambiguity_schema_chars = len(ctx.schema_formatted)
        ctx.ambiguity_schema_sha256 = hashlib.sha256(
            ctx.schema_formatted.encode("utf-8")
        ).hexdigest()

        result = detect_ambiguities(
            nl_question=ctx.question,
            filtered_schema=ctx.schema_formatted,
            database_engine=ctx.db_type,
            llm_manager=llm_manager,
            preference_tree=None,
            provided_context=ctx.provided_context,
            matched_database_values=ctx.matched_database_values,
            callback=lambda **kw: ctx.add_llm_call(phase="clarify", **kw),
        )
        raw_response = str(result.get("raw_response", ""))
        ctx.ambiguity_response_sha256 = hashlib.sha256(
            raw_response.encode("utf-8")
        ).hexdigest()
        if not result.get("success"):
            ctx.ambiguity_report = {
                "error": result.get("error", "unknown"),
                "fallback": "fail_closed",
            }
            ctx.mark_error(
                "Failed to analyze whether the question requires clarification"
            )
            return ctx, [], []

        report = result.get("report")
        if report:
            ctx.ambiguity_report = report.to_dict()
            if result.get("normalizations"):
                ctx.ambiguity_report["normalizations"] = result["normalizations"]
        if not report or not report.requires_clarification:
            return ctx, [], []
        if not report.ambiguities:
            return ctx, [], []

        ambiguities = report.ambiguities
        interpretations = []
        seen = set()
        for i, ambiguity in enumerate(ambiguities, 1):
            for j, option in enumerate(ambiguity.possible_interpretations):
                description = option.text
                likelihood = option.score
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

    def _check_non_interactive_intent(self, ctx: Any) -> list[Any]:
        """Apply deterministic blockers without invoking the ambiguity model."""
        ctx.phase = AskPhase.CLARIFY.value
        ctx.clarification_policy = NON_INTERACTIVE_CLARIFICATION_POLICY
        ambiguities = detect_missing_intent_ambiguities(ctx.question)
        ctx.ambiguity_report = {
            "ambiguities": [ambiguity.to_dict() for ambiguity in ambiguities],
            "total_ambiguities": len(ambiguities),
            "requires_clarification": bool(ambiguities),
            "can_proceed_with_assumptions": not ambiguities,
            "overall_confidence": 0.5 if ambiguities else 1.0,
            "decision": "deterministic_only_non_interactive",
            "llm_detector_invoked": False,
        }
        self._record_non_interactive_clarification_decisions(ctx, ambiguities)
        return ambiguities

    @staticmethod
    def _record_non_interactive_clarification_decisions(
        ctx: Any,
        deterministic_ambiguities: list[Any],
    ) -> None:
        """Record deterministic blockers without treating them as user intent."""
        for ambiguity in deterministic_ambiguities:
            scores = sorted(
                (option.score for option in ambiguity.possible_interpretations),
                reverse=True,
            )
            top_score = scores[0] if scores else 0.0
            runner_up_score = scores[1] if len(scores) > 1 else 0.0
            ctx.clarification_resolutions.append(
                {
                    "ambiguity_id": ambiguity.id,
                    "action": "abstain",
                    "selected_option_id": None,
                    "selected_text": None,
                    "top_score": top_score,
                    "runner_up_score": runner_up_score,
                    "margin": top_score - runner_up_score,
                    "reason": "deterministic_missing_intent_requires_user_input",
                    "policy": NON_INTERACTIVE_CLARIFICATION_POLICY,
                    "term": ambiguity.term,
                    "question": ambiguity.clarifying_question,
                    "source": "deterministic_intent",
                    "applied": False,
                }
            )

    @staticmethod
    def _clarification_questions(ambiguities: list[Any]):
        questions = []
        for ambiguity in ambiguities:
            questions.append(
                AskClarificationQuestion(
                    id=ambiguity.id,
                    question=ambiguity.clarifying_question,
                    options=[
                        option.text for option in ambiguity.possible_interpretations
                    ],
                )
            )
        return questions

    def _build_refined_question(
        self,
        original: str,
        clarifications: dict[str, str],
        clarification_context: dict[str, dict[str, str]] | None = None,
    ) -> str:
        if not clarifications:
            return original
        if clarification_context:
            resolved = []
            for answer_key, answer in clarifications.items():
                context = clarification_context.get(answer_key, {})
                term = context.get("term") or answer_key
                question = context.get("question")
                if question:
                    resolved.append(f"- {term}: {question} Answer: {answer}")
                else:
                    resolved.append(f"- {term}: {answer}")
            return (
                original + "\n\nResolved user clarifications:\n" + "\n".join(resolved)
            )
        clarification_text = "; ".join(
            f"{category}: {answer}" for category, answer in clarifications.items()
        )
        return f"{original} ({clarification_text})"

    @staticmethod
    def _build_refined_question_from_resolutions(
        original: str, resolutions: list[dict[str, Any]]
    ) -> str | None:
        selected = [
            resolution
            for resolution in resolutions
            if resolution.get("action") == "select" and resolution.get("applied", True)
        ]
        if not selected:
            return None
        clarification_text = "; ".join(
            f"For '{resolution['term']}', use: {resolution['selected_text']}"
            for resolution in selected
        )
        return f"{original}\n\nResolved interpretations:\n{clarification_text}"

    async def _load_config(
        self, target: str | None
    ) -> tuple[str | None, dict[str, Any] | None]:
        config_factory = self._targets_config_factory or create_targets_config
        cfg = config_factory()
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

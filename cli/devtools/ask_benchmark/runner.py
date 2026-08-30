from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import sqlglot
from sqlglot import exp

from features.ask.ambiguity_detection import NON_INTERACTIVE_CLARIFICATION_POLICY
from features.ask.engine.ask3.phases.schema import (
    ADAPTIVE_SCHEMA_FORMAT_VERSION,
    COMPACT_SCHEMA_FORMAT_VERSION,
    _format_semantic_schema,
    format_semantic_schema_compact,
)
from features.ask.engine.ask3.types import SchemaSource
from features.ask.events import (
    AskClarificationNeededEvent,
    AskErrorEvent,
    AskResultEvent,
    AskSqlGeneratedEvent,
)
from features.ask.models import AskInput, AskOptions
from features.ask.service import AskService
from features.ask.sql_validation import validate_sql_for_ask
from features.schema.semantic_layer.manager import SemanticLayerManager

from .anthropic_adapter import AnthropicSDKAdapter
from .artifacts import ArtifactStore, make_attempt_key
from .bird_dataset import DATASET_REVISION
from .claude_subscription_adapter import (
    CLAUDE_SUBSCRIPTION_TRANSPORT,
    ClaudeSubscriptionAdapter,
)
from .executor import MySQLExecutor, QueryBounds
from .models import (
    AttemptRecord,
    BenchmarkCase,
    ContextMode,
    EvaluationTrack,
    InteractionMode,
    ModelCallRecord,
    ModelSpec,
    Outcome,
    QueryResult,
    RunLimits,
)
from .oracle import UNSTABLE_GOLD_CASE_IDS, result_fingerprint, score_results
from .pipeline import (
    build_model_only_prompt,
    extract_sql,
    provided_context_for_case,
    question_for_rdst,
)
from .pydantic_adapter import (
    BenchmarkModelOutputError,
    BenchmarkTransportError,
    PydanticAIAdapter,
    RouteMismatchError,
)
from .schema import MySQLSchemaLoader, load_semantic_schema
from .value_profiles import (
    VALUE_GROUNDING_CONTEXT_VERSION,
    ExactValueProfileStore,
    ValueMatchResult,
)

class _BenchmarkTargetsConfig:
    def __init__(self, target: str, config: dict[str, Any]):
        self._target = target
        self._config = config

    def load(self) -> None:
        return None

    def get_default(self):
        return self._target

    def get(self, target: str):
        return self._config if target == self._target else None


class BenchmarkBudgetExceeded(RuntimeError):
    """Raised before a provider call that would violate a run limit."""


class _RunBudget:
    def __init__(
        self,
        limits: RunLimits,
        existing_receipts: list[dict[str, Any]],
    ) -> None:
        self.limits = limits
        self.started = perf_counter()
        self.provider_calls = len(existing_receipts)
        self.normalized_cost_usd = sum(
            (
                Decimal(str(receipt.get("normalized_cold_cost_usd", "0")))
                for receipt in existing_receipts
            ),
            Decimal(0),
        )
        self.stop_reason: str | None = None
        self._refresh_stop_reason()

    @property
    def enabled(self) -> bool:
        return any(
            value is not None
            for value in (
                self.limits.max_provider_calls,
                self.limits.max_normalized_cost_usd,
                self.limits.max_wall_time_seconds,
            )
        )

    def before_work(self) -> None:
        self._refresh_stop_reason()
        if self.stop_reason:
            raise BenchmarkBudgetExceeded(self.stop_reason)

    def record(self, call: ModelCallRecord | dict[str, Any]) -> None:
        self.provider_calls += 1
        value = (
            call.normalized_cold_cost_usd
            if isinstance(call, ModelCallRecord)
            else call.get("normalized_cold_cost_usd", "0")
        )
        self.normalized_cost_usd += Decimal(str(value))
        self._refresh_stop_reason()

    def _refresh_stop_reason(self) -> None:
        if self.stop_reason:
            return
        if (
            self.limits.max_provider_calls is not None
            and self.provider_calls >= self.limits.max_provider_calls
        ):
            self.stop_reason = (
                "provider-call limit reached "
                f"({self.provider_calls}/{self.limits.max_provider_calls})"
            )
            return
        if (
            self.limits.max_normalized_cost_usd is not None
            and self.normalized_cost_usd >= self.limits.max_normalized_cost_usd
        ):
            self.stop_reason = (
                "normalized-cost limit reached "
                f"(${self.normalized_cost_usd}/"
                f"${self.limits.max_normalized_cost_usd})"
            )
            return
        if (
            self.limits.max_wall_time_seconds is not None
            and perf_counter() - self.started >= self.limits.max_wall_time_seconds
        ):
            self.stop_reason = (
                f"wall-time limit reached ({self.limits.max_wall_time_seconds}s)"
            )


class _BudgetedAdapter:
    def __init__(self, inner: Any, budget: _RunBudget) -> None:
        self._inner = inner
        self._budget = budget
        self._receipt_sink: (
            Callable[[ModelCallRecord | dict[str, Any]], None] | None
        ) = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def set_call_receipt_sink(
        self,
        sink: Callable[[ModelCallRecord | dict[str, Any]], None] | None,
    ) -> None:
        self._receipt_sink = sink
        if hasattr(self._inner, "set_call_receipt_sink"):
            self._inner.set_call_receipt_sink(self._receive_receipt)
        elif self._budget.enabled:
            raise RuntimeError(
                "Paid run limits require an adapter with durable call receipts"
            )

    def _receive_receipt(self, call: ModelCallRecord | dict[str, Any]) -> None:
        if self._receipt_sink is not None:
            self._receipt_sink(call)
        self._budget.record(call)

    def query(self, **kwargs: Any) -> Any:
        self._budget.before_work()
        return self._inner.query(**kwargs)

    def generate_response(self, **kwargs: Any) -> Any:
        self._budget.before_work()
        return self._inner.generate_response(**kwargs)


def _build_adapter(spec: ModelSpec, aliases: dict[str, ModelSpec]):
    if spec.transport == "anthropic":
        return AnthropicSDKAdapter(spec, model_aliases=aliases)
    if spec.transport == CLAUDE_SUBSCRIPTION_TRANSPORT:
        return ClaudeSubscriptionAdapter(spec, model_aliases=aliases)
    return PydanticAIAdapter(spec, model_aliases=aliases)


class BenchmarkRunner:
    def __init__(
        self,
        *,
        run_id: str,
        track: EvaluationTrack,
        context_mode: ContextMode,
        executor: MySQLExecutor,
        schema_loader: MySQLSchemaLoader,
        artifact_store: ArtifactStore,
        filter_spec: ModelSpec,
        filter_aliases: tuple[str, ...],
        semantic_dir: Path,
        interaction_mode: InteractionMode | None = None,
        protocol_fingerprint: str = "",
        oracle_executor: MySQLExecutor | None = None,
        adapter_factory: Callable[[ModelSpec, dict[str, ModelSpec]], Any] | None = None,
        run_limits: RunLimits | None = None,
        semantic_schema_format: str = ADAPTIVE_SCHEMA_FORMAT_VERSION,
    ):
        self.run_id = run_id
        self.track = track
        self.context_mode = context_mode
        self.interaction_mode = interaction_mode or (
            InteractionMode.INTERACTIVE_NO_ANSWER
            if track == EvaluationTrack.RDST
            else InteractionMode.NOT_APPLICABLE
        )
        if (
            track == EvaluationTrack.MODEL_ONLY
            and self.interaction_mode != InteractionMode.NOT_APPLICABLE
        ):
            raise ValueError("Interaction mode only applies to the rdst-ask track")
        self.executor = executor
        if oracle_executor is not None:
            self.oracle_executor = oracle_executor
        elif isinstance(executor, MySQLExecutor):
            self.oracle_executor = MySQLExecutor(
                executor.config,
                QueryBounds(
                    timeout_seconds=max(300, executor.bounds.timeout_seconds),
                    max_rows=executor.bounds.max_rows,
                    max_result_bytes=executor.bounds.max_result_bytes,
                ),
            )
        else:
            self.oracle_executor = executor
        self.schema_loader = schema_loader
        self.artifact_store = artifact_store
        self.filter_spec = filter_spec
        self.filter_aliases = filter_aliases
        self.semantic_dir = semantic_dir
        self.value_profile_store = (
            ExactValueProfileStore(semantic_dir)
            if context_mode == ContextMode.AUTO_INIT_PROFILED_VALUES
            else None
        )
        self.protocol_fingerprint = protocol_fingerprint
        self.adapter_factory = adapter_factory or _build_adapter
        self.run_limits = run_limits or RunLimits()
        if semantic_schema_format not in {
            ADAPTIVE_SCHEMA_FORMAT_VERSION,
            "verbose-v1",
            COMPACT_SCHEMA_FORMAT_VERSION,
        }:
            raise ValueError(
                f"Unsupported semantic schema format: {semantic_schema_format}"
            )
        if (
            semantic_schema_format == COMPACT_SCHEMA_FORMAT_VERSION
            and context_mode == ContextMode.RAW
        ):
            raise ValueError("Compact schema formatting requires a semantic context")
        self.semantic_schema_format = semantic_schema_format
        self.budget_stop_reason: str | None = None
        self._gold_fingerprints: dict[int, str] = {}
        self._gold_results: dict[int, QueryResult] = {}

    def preflight_gold(
        self, cases: list[BenchmarkCase]
    ) -> tuple[list[BenchmarkCase], dict[int, str]]:
        valid = []
        failures = {}
        for case in cases:
            result = self.oracle_executor.execute(case.gold_sql, db_id=case.db_id)
            if result.succeeded:
                self._gold_results[case.question_id] = result
                self._gold_fingerprints[case.question_id] = _multiset_result_hash(
                    result
                )
                valid.append(case)
            else:
                failures[case.question_id] = result.error or "Gold query failed"
        return valid, failures

    def gold_fingerprints(self) -> dict[int, str]:
        return dict(sorted(self._gold_fingerprints.items()))

    def run(
        self,
        cases: list[BenchmarkCase],
        model_specs: list[ModelSpec],
        *,
        repetitions: int = 1,
    ) -> list[dict[str, Any]]:
        completed = self.artifact_store.completed_keys()
        budget = _RunBudget(
            self.run_limits,
            self.artifact_store.load_call_receipts(),
        )
        self.budget_stop_reason = None
        aliases = {alias: self.filter_spec for alias in self.filter_aliases}
        adapters = {}
        fingerprints = {}
        for spec in model_specs:
            adapter = _BudgetedAdapter(self.adapter_factory(spec, aliases), budget)
            if hasattr(adapter, "set_call_receipt_sink"):
                adapter.set_call_receipt_sink(self.artifact_store.append_call_receipt)
            adapters[spec.name] = adapter
            fingerprints[spec.name] = self.configuration_fingerprint(spec)

        for repetition in range(repetitions):
            for case in cases:
                for spec in model_specs:
                    configuration_fingerprint = fingerprints[spec.name]
                    key = make_attempt_key(
                        dataset_revision=DATASET_REVISION,
                        dialect=case.dialect,
                        track=self.track.value,
                        context_mode=self.context_mode.value,
                        model_identity=configuration_fingerprint,
                        interaction_mode=self.interaction_mode.value,
                        question_id=case.question_id,
                        repetition=repetition,
                    )
                    if key in completed:
                        continue
                    try:
                        budget.before_work()
                    except BenchmarkBudgetExceeded as exc:
                        self.budget_stop_reason = str(exc)
                        return self.artifact_store.load_attempts()
                    attempt = self._run_case(
                        case,
                        spec,
                        repetition,
                        key,
                        adapters[spec.name],
                        configuration_fingerprint,
                    )
                    self.artifact_store.append_attempt(attempt)
                    if attempt.scored:
                        completed.add(key)
                    if self.budget_stop_reason:
                        return self.artifact_store.load_attempts()
        return self.artifact_store.load_attempts()

    def _run_case(
        self,
        case: BenchmarkCase,
        spec: ModelSpec,
        repetition: int,
        attempt_key: str,
        adapter: Any,
        configuration_fingerprint: str,
    ) -> AttemptRecord:
        invocation_id = uuid4().hex
        if hasattr(adapter, "set_attempt_id"):
            adapter.set_attempt_id(invocation_id)
        started_at = _now_iso()
        start = perf_counter()
        scored = True
        try:
            if self.track == EvaluationTrack.MODEL_ONLY:
                attempt = self._run_model_only(case, adapter)
            else:
                attempt = self._run_rdst(case, adapter)
        except RouteMismatchError as exc:
            scored = False
            attempt = _failed_attempt(Outcome.UNCONTROLLED_ROUTE, "transport", str(exc))
        except BenchmarkTransportError as exc:
            scored = False
            attempt = _failed_attempt(Outcome.TRANSPORT_ERROR, "transport", str(exc))
        except BenchmarkModelOutputError as exc:
            attempt = _failed_attempt(Outcome.GENERATION_ERROR, "generation", str(exc))
        except BenchmarkBudgetExceeded as exc:
            scored = False
            self.budget_stop_reason = str(exc)
            attempt = _failed_attempt(Outcome.BUDGET_EXCEEDED, "budget", str(exc))
        call_records = adapter.drain_call_records()
        internal_errors = [
            record.error for record in call_records if record.error_kind == "internal"
        ]
        if internal_errors:
            raise RuntimeError(
                "; ".join(error for error in internal_errors if error)
                or "Internal model adapter failure"
            )
        route_errors = [
            record.route_error for record in call_records if record.route_error
        ]
        transport_errors = [
            record.error
            for record in call_records
            if record.error_kind
            in {"transport", "rate_limit", "timeout", "provider_5xx"}
        ]
        if route_errors:
            scored = False
            attempt = _failed_attempt(
                Outcome.UNCONTROLLED_ROUTE,
                "transport",
                "; ".join(route_errors),
            )
        elif transport_errors:
            scored = False
            attempt = _failed_attempt(
                Outcome.TRANSPORT_ERROR,
                "transport",
                "; ".join(error for error in transport_errors if error),
            )
        actual_cost = _sum_actual_cost(call_records)
        normalized_cost = sum(
            (record.normalized_cold_cost_usd for record in call_records), Decimal(0)
        )
        oracle_latency_ms = float(attempt.get("oracle_latency_ms", 0.0))
        completed_at = _now_iso()
        return AttemptRecord(
            attempt_key=attempt_key,
            invocation_id=invocation_id,
            run_id=self.run_id,
            question_id=case.question_id,
            db_id=case.db_id,
            difficulty=case.difficulty,
            dialect=case.dialect,
            track=self.track.value,
            context_mode=self.context_mode.value,
            interaction_mode=self.interaction_mode.value,
            model_name=spec.name,
            model=spec.model,
            transport=spec.transport,
            repetition=repetition,
            outcome=attempt["outcome"],
            scored=scored,
            configuration_fingerprint=configuration_fingerprint,
            reasoning_effort=spec.reasoning_effort,
            max_tokens=spec.max_tokens,
            generated_sql=attempt.get("generated_sql"),
            validated_sql=attempt.get("validated_sql"),
            execution_correct=attempt.get("execution_correct", False),
            soft_f1=attempt.get("soft_f1", 0.0),
            multiset_correct=attempt.get("multiset_correct", False),
            ordered_correct=attempt.get("ordered_correct", False),
            candidate_row_count=attempt.get("candidate_row_count", 0),
            gold_row_count=attempt.get("gold_row_count", 0),
            latency_ms=max((perf_counter() - start) * 1000 - oracle_latency_ms, 0.0),
            candidate_execution_ms=float(attempt.get("candidate_execution_ms", 0.0)),
            oracle_latency_ms=oracle_latency_ms,
            started_at=started_at,
            completed_at=completed_at,
            actual_cost_usd=actual_cost,
            normalized_cold_cost_usd=normalized_cost,
            failure_stage=attempt.get("failure_stage"),
            error=attempt.get("error"),
            call_records=call_records,
            diagnostics={
                **attempt.get("diagnostics", {}),
                "headline_eligible": case.question_id not in UNSTABLE_GOLD_CASE_IDS,
            },
        )

    def _run_model_only(self, case: BenchmarkCase, adapter: Any) -> dict[str, Any]:
        schema = (
            self.schema_loader.load(case.db_id)
            if self.context_mode == ContextMode.RAW
            else load_semantic_schema(self.semantic_dir, case.db_id)
        )
        value_matches = self._matched_database_values(case)
        system, prompt = build_model_only_prompt(
            case,
            schema,
            self.context_mode,
            matched_database_values=value_matches.context,
        )
        provided_context = provided_context_for_case(case, self.context_mode)
        context_diagnostics = {
            **_provided_context_diagnostics(provided_context),
            "matched_database_values": value_matches.to_dict(),
        }
        response = adapter.query(
            system_message=system,
            user_query=prompt,
            max_tokens=adapter.default_spec.max_tokens,
            temperature=adapter.default_spec.temperature,
            purpose="model_only_generation",
        )
        try:
            generated_sql = extract_sql(response["text"])
        except ValueError as exc:
            return {
                **_failed_attempt(Outcome.GENERATION_ERROR, "generation", str(exc)),
                "diagnostics": context_diagnostics,
            }
        validation = validate_sql_for_ask(
            generated_sql,
            enforce_result_limit=False,
        )
        if not validation["is_valid"]:
            issues = "; ".join(validation.get("issues", []))
            outcome = (
                Outcome.READ_ONLY_REJECTION
                if not validation.get("is_safe")
                else Outcome.VALIDATION_ERROR
            )
            return {
                **_failed_attempt(outcome, "validation", issues),
                "generated_sql": generated_sql,
                "validated_sql": validation.get("validated_sql"),
                "diagnostics": context_diagnostics,
            }
        candidate = self.executor.execute(generated_sql, db_id=case.db_id)
        result = self._score_execution(case, generated_sql, generated_sql, candidate)
        result.setdefault("diagnostics", {}).update(context_diagnostics)
        return result

    def _run_rdst(self, case: BenchmarkCase, adapter: Any) -> dict[str, Any]:
        semantic_base = (
            self.artifact_store.run_dir / "raw-semantic-empty"
            if self.context_mode == ContextMode.RAW
            else self.semantic_dir
        )
        semantic_manager = SemanticLayerManager(base_dir=semantic_base)
        if self.context_mode != ContextMode.RAW and not semantic_manager.exists(
            case.db_id
        ):
            raise RuntimeError(f"Semantic layer is missing for {case.db_id}")

        config = self._target_config(case.db_id)
        value_matches = self._matched_database_values(case)
        observed: dict[str, Any] = {
            "context": None,
            "phases": [],
            "phase_snapshots": [],
        }

        def observe(phase: str, ctx: Any) -> None:
            observed["context"] = ctx
            observed["phases"].append(phase)
            snapshot = (
                ctx.to_dict() if callable(getattr(ctx, "to_dict", None)) else vars(ctx)
            )
            observed["phase_snapshots"].append(
                {"phase": phase, "context": copy.deepcopy(snapshot)}
            )

        service = AskService(
            llm_manager=adapter,
            semantic_manager=semantic_manager,
            db_executor=self.executor.as_ask_executor(case.db_id),
            targets_config_factory=lambda: _BenchmarkTargetsConfig(case.db_id, config),
            persist_queries=False,
            session_store={},
            phase_observer=observe,
            diagnostic_schema_formatter_fn=(
                format_semantic_schema_compact
                if self.semantic_schema_format == COMPACT_SCHEMA_FORMAT_VERSION
                else _format_semantic_schema
                if self.semantic_schema_format == "verbose-v1"
                else None
            ),
        )
        options = AskOptions(
            timeout_seconds=self.executor.bounds.timeout_seconds,
            max_rows=self.executor.bounds.max_rows,
            no_interactive=self.interaction_mode == InteractionMode.AUTO,
            enforce_result_limit=False,
            persist_query=False,
            raise_unexpected_errors=True,
        )

        async def collect_events():
            return [
                event
                async for event in service.ask(
                    AskInput(
                        question=question_for_rdst(case, self.context_mode),
                        target=case.db_id,
                        source="benchmark",
                        provided_context=provided_context_for_case(
                            case, self.context_mode
                        ),
                        matched_database_values=value_matches.context,
                    ),
                    options,
                )
            ]

        events = asyncio.run(collect_events())
        ctx = observed["context"]
        diagnostics = {
            "ask_service_phases": observed["phases"],
            "ask_service_phase_snapshots": observed["phase_snapshots"],
            "ask_service_events": [event.type for event in events],
            "matched_database_values": value_matches.to_dict(),
        }
        if ctx is not None:
            diagnostics.update(_context_diagnostics(ctx, case))
            required_source = (
                SchemaSource.DATABASE
                if self.context_mode == ContextMode.RAW
                else SchemaSource.SEMANTIC
            )
            if ctx.schema_source != required_source:
                raise RuntimeError(
                    f"AskService used {ctx.schema_source}; expected {required_source}"
                )

        clarification = next(
            (
                event
                for event in events
                if isinstance(event, AskClarificationNeededEvent)
            ),
            None,
        )
        if clarification is not None:
            service.abandon(clarification.session_id)
            diagnostics["clarification"] = {
                "session_id": clarification.session_id,
                "questions": [
                    {
                        "id": question.id,
                        "question": question.question,
                        "options": question.options,
                    }
                    for question in clarification.questions
                ],
                "interpretations": [
                    {
                        "id": interpretation.id,
                        "description": interpretation.description,
                        "likelihood": interpretation.likelihood,
                        "assumptions": interpretation.assumptions,
                    }
                    for interpretation in clarification.interpretations
                ],
            }
            return {
                **_failed_attempt(
                    Outcome.CLARIFICATION_REQUIRED,
                    "clarification",
                    "Clear benchmark case triggered clarification",
                ),
                "generated_sql": getattr(ctx, "generated_sql", None),
                "validated_sql": getattr(ctx, "sql", None),
                "diagnostics": diagnostics,
            }

        error_event = next(
            (event for event in reversed(events) if isinstance(event, AskErrorEvent)),
            None,
        )
        if error_event is not None:
            raw_phase = error_event.phase
            phase = raw_phase.value if hasattr(raw_phase, "value") else raw_phase
            phase = phase or getattr(ctx, "phase", "unknown")
            if error_event.code == "clarification_required":
                return {
                    **_failed_attempt(
                        Outcome.CLARIFICATION_REQUIRED,
                        "clarification",
                        error_event.message,
                    ),
                    "generated_sql": getattr(ctx, "generated_sql", None),
                    "validated_sql": getattr(ctx, "sql", None),
                    "diagnostics": diagnostics,
                }
            if phase in {"config", "schema", "filter"}:
                raise RuntimeError(
                    f"RDST benchmark failed during {phase}: {error_event.message}"
                )
            outcome, stage = _classify_context_error(phase, error_event.message)
            if phase == "execute" and ctx is not None:
                execution_result = getattr(ctx, "execution_result", None)
                outcome = _execution_error_outcome(
                    execution_result.error_kind if execution_result else None
                )
                stage = "execute"
            return {
                **_failed_attempt(outcome, stage, error_event.message),
                "generated_sql": getattr(ctx, "generated_sql", None),
                "validated_sql": getattr(ctx, "sql", None),
                "diagnostics": diagnostics,
            }

        result_event = next(
            (event for event in reversed(events) if isinstance(event, AskResultEvent)),
            None,
        )
        if result_event is None:
            raise RuntimeError("AskService completed without a result or error event")
        generated_event = next(
            (event for event in events if isinstance(event, AskSqlGeneratedEvent)),
            None,
        )
        generated_sql = getattr(ctx, "generated_sql", None) or (
            generated_event.sql if generated_event else None
        )
        candidate = QueryResult(
            columns=tuple(result_event.columns),
            rows=tuple(tuple(row) for row in result_event.rows),
            execution_time_ms=result_event.execution_time_ms,
        )
        result = self._score_execution(case, generated_sql, result_event.sql, candidate)
        result["diagnostics"].update(diagnostics)
        return result

    def _score_execution(
        self,
        case: BenchmarkCase,
        generated_sql: str | None,
        validated_sql: str | None,
        candidate: QueryResult,
    ) -> dict[str, Any]:
        candidate_execution_ms = candidate.execution_time_ms
        if candidate.timed_out:
            return {
                **_failed_attempt(
                    Outcome.TIMEOUT, "execute", candidate.error or "Timeout"
                ),
                "generated_sql": generated_sql,
                "validated_sql": validated_sql,
                "candidate_execution_ms": candidate_execution_ms,
            }
        if candidate.result_too_large:
            return {
                **_failed_attempt(
                    Outcome.RESULT_TOO_LARGE,
                    "execute",
                    candidate.error or "Result exceeded bounds",
                ),
                "generated_sql": generated_sql,
                "validated_sql": validated_sql,
                "candidate_execution_ms": candidate_execution_ms,
            }
        if not candidate.succeeded:
            return {
                **_failed_attempt(
                    Outcome.EXECUTION_ERROR,
                    "execute",
                    candidate.error or "Execution failed",
                ),
                "generated_sql": generated_sql,
                "validated_sql": validated_sql,
                "candidate_execution_ms": candidate_execution_ms,
            }
        oracle_started = perf_counter()
        expected_gold_hash = self._gold_fingerprints.get(case.question_id)
        if expected_gold_hash is None:
            raise ValueError(f"Gold query {case.question_id} has not passed preflight")
        gold = self._gold_results.get(case.question_id)
        if gold is None:
            raise RuntimeError(
                f"Gold result is unavailable for question {case.question_id}"
            )
        if _multiset_result_hash(gold) != expected_gold_hash:
            raise RuntimeError(
                f"Cached gold result changed for question {case.question_id}"
            )
        score = score_results(candidate, gold)
        return {
            "outcome": (
                Outcome.CORRECT.value
                if score.execution_correct
                else Outcome.INCORRECT_RESULT.value
            ),
            "generated_sql": generated_sql,
            "validated_sql": validated_sql,
            "execution_correct": score.execution_correct,
            "soft_f1": score.soft_f1,
            "multiset_correct": score.multiset_correct,
            "ordered_correct": score.ordered_correct,
            "candidate_row_count": score.candidate_row_count,
            "gold_row_count": score.gold_row_count,
            "candidate_execution_ms": candidate_execution_ms,
            "oracle_latency_ms": (perf_counter() - oracle_started) * 1000,
            "diagnostics": {
                "candidate_result_sha256": _result_hash(candidate),
                "gold_result_sha256": _result_hash(gold),
                "candidate_execution_ms": candidate.execution_time_ms,
                "gold_execution_ms": gold.execution_time_ms,
                "gold_tables": sorted(_sql_tables(case.gold_sql, case.dialect)),
            },
        }

    def configuration_fingerprint(self, spec: ModelSpec) -> str:
        payload = {
            "track": self.track.value,
            "context_mode": self.context_mode.value,
            "interaction_mode": self.interaction_mode.value,
            "protocol_fingerprint": self.protocol_fingerprint,
            "model": _spec_identity(spec),
            "filter_model": _spec_identity(self.filter_spec)
            if self.track == EvaluationTrack.RDST
            else None,
            "pipeline": {
                "entrypoint": "features.ask.service.AskService",
                "clarification_policy": (
                    NON_INTERACTIVE_CLARIFICATION_POLICY
                    if self.interaction_mode == InteractionMode.AUTO
                    else self.interaction_mode.value
                ),
                "provided_context_policy": "first-class-authoritative-v1",
                "matched_database_values_policy": (
                    VALUE_GROUNDING_CONTEXT_VERSION
                    if self.value_profile_store is not None
                    else "none"
                ),
                "semantic_schema_format": self.semantic_schema_format,
                "generation_attempts": 1,
                "max_validation_repair_attempts": 1,
                "validation_attempts": 2,
                "query_persistence": False,
                "enforce_result_limit": False,
            },
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _matched_database_values(self, case: BenchmarkCase) -> ValueMatchResult:
        if self.value_profile_store is None:
            return ValueMatchResult(
                context_version="none",
                target=case.db_id,
                profile_sha256="",
                matches=(),
            )
        return self.value_profile_store.match(case.db_id, case.question)

    def _target_config(self, db_id: str):
        config = self.executor.config
        return {
            "engine": "mysql",
            "host": config.host,
            "port": config.port,
            "user": config.user_for(db_id),
            "password": config.password,
            "database": config.database_for(db_id),
            "unix_socket": config.unix_socket,
        }


def _spec_identity(spec: ModelSpec):
    return {
        "name": spec.name,
        "model": spec.model,
        "transport": spec.transport,
        "provider_order": list(spec.provider_order),
        "temperature": spec.temperature,
        "max_tokens": spec.max_tokens,
        "timeout_seconds": spec.timeout_seconds,
        "reasoning_effort": spec.reasoning_effort,
        "require_parameters": spec.require_parameters,
        "allow_fallbacks": spec.allow_fallbacks,
        "provider_data_training": spec.provider_data_training,
        "provider_retains_prompts": spec.provider_retains_prompts,
        "historical": spec.historical,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _failed_attempt(outcome: Outcome, stage: str, error: str):
    return {
        "outcome": outcome.value,
        "failure_stage": stage,
        "error": error,
    }


def _classify_context_error(phase: str, message: str | None):
    text = (message or "").lower()
    if "confidence" in text and "cannot answer" in text:
        return Outcome.LOW_CONFIDENCE_REFUSAL, "generation"
    if phase == "schema":
        return Outcome.SCHEMA_LOAD_ERROR, "schema"
    if phase == "validate" or "validation" in text:
        return Outcome.VALIDATION_ERROR, "validation"
    if phase == "execute":
        return Outcome.EXECUTION_ERROR, "execute"
    return Outcome.GENERATION_ERROR, phase


def _execution_error_outcome(error_kind: str | None):
    if error_kind == "timeout":
        return Outcome.TIMEOUT
    if error_kind == "result_too_large":
        return Outcome.RESULT_TOO_LARGE
    return Outcome.EXECUTION_ERROR


def _context_diagnostics(ctx, case: BenchmarkCase):
    gold_tables = {table.lower() for table in _sql_tables(case.gold_sql, case.dialect)}
    schema_tables = list(ctx.schema_info.tables) if ctx.schema_info else []
    normalized_schema_tables = {table.lower() for table in schema_tables}
    gold_table_recall = (
        len(gold_tables & normalized_schema_tables) / len(gold_tables)
        if gold_tables
        else None
    )
    return {
        "schema_source": ctx.schema_source,
        "schema_format": getattr(ctx, "schema_format", ""),
        "schema_format_policy": getattr(ctx, "schema_format_policy", ""),
        "schema_verbose_chars": getattr(ctx, "schema_verbose_chars", 0),
        "schema_compact_chars": getattr(ctx, "schema_compact_chars", 0),
        "schema_compact_savings_ratio": getattr(
            ctx, "schema_compact_savings_ratio", 0.0
        ),
        "schema_context_fallback_used": getattr(
            ctx, "schema_context_fallback_used", False
        ),
        "schema_context_fallback_reason": getattr(
            ctx, "schema_context_fallback_reason", ""
        ),
        "schema_prompt_utf8_bytes": getattr(ctx, "schema_prompt_utf8_bytes", 0),
        "schema_tables": schema_tables,
        # Historical aliases keep old analysis scripts readable. They now refer
        # to the complete schema; filtering and expansion are gone.
        "filtered_tables": schema_tables,
        "all_available_tables": schema_tables,
        "retry_count": ctx.retry_count,
        "generation_confidence": ctx.generation_confidence,
        "clarifications": dict(getattr(ctx, "clarifications", {})),
        "used_refined_question": bool(getattr(ctx, "refined_question", None)),
        "refined_question_sha256": hashlib.sha256(
            (getattr(ctx, "refined_question", None) or "").encode("utf-8")
        ).hexdigest(),
        "ambiguity_report": dict(getattr(ctx, "ambiguity_report", {})),
        "clarification_resolutions": list(
            getattr(ctx, "clarification_resolutions", [])
        ),
        "clarification_policy": getattr(ctx, "clarification_policy", ""),
        "ambiguity_schema_chars": getattr(ctx, "ambiguity_schema_chars", 0),
        "ambiguity_schema_sha256": getattr(ctx, "ambiguity_schema_sha256", ""),
        "ambiguity_response_sha256": getattr(ctx, "ambiguity_response_sha256", ""),
        "limit_added": ctx.limit_added,
        "limit_reduced": ctx.limit_reduced,
        "gold_table_recall": gold_table_recall,
        **_provided_context_diagnostics(getattr(ctx, "provided_context", "")),
    }


def _provided_context_diagnostics(provided_context: str) -> dict[str, Any]:
    return {
        "provided_context_present": bool(provided_context),
        "provided_context_chars": len(provided_context),
        "provided_context_sha256": hashlib.sha256(
            provided_context.encode("utf-8")
        ).hexdigest(),
    }


def _sum_actual_cost(records: list[ModelCallRecord]) -> Decimal | None:
    if not records or any(record.actual_cost_usd is None for record in records):
        return None
    return sum((record.actual_cost_usd for record in records), Decimal(0))


def _result_hash(result: QueryResult) -> str:
    payload = json.dumps(result.rows, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _multiset_result_hash(result: QueryResult) -> str:
    return result_fingerprint(result)


def _sql_tables(sql: str, dialect: str) -> set[str]:
    try:
        tree = sqlglot.parse_one(
            sql, read="postgres" if dialect == "postgresql" else dialect
        )
    except sqlglot.errors.ParseError:
        return set()
    cte_names = {
        cte.alias_or_name.casefold()
        for cte in tree.find_all(exp.CTE)
        if cte.alias_or_name
    }
    return {
        table.name
        for table in tree.find_all(exp.Table)
        if table.name.casefold() not in cte_names
    }

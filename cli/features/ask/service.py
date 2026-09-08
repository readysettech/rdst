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
from shared.llm_manager.inference_attribution import inference_workflow
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
    CORRECTION_INTENT_ROUTING_MAX_ACTIVATIONS,
    CORRECTION_INTENT_ROUTING_PURPOSE,
    CORRECTION_INTENTS,
    route_correction_intent,
    selected_correction_intents,
)
from .derived_metric_normalization import (
    normalize_context as normalize_scalar_derived_metric_context,
)
from .identifier_quoting import (
    VERSION as IDENTIFIER_QUOTING_VERSION,
    plan_identifier_quoting,
    candidate_from_keywords,
)
from .declared_count import apply_declared_count as apply_declared_count_correction
from .count_name_completion import (
    VERSION as COUNT_NAME_COMPLETION_VERSION,
    plan_count_name,
    name_from_domain,
    candidate_sql as completed_name_sql,
    count_proof_sql,
    proved_result as proved_completed_count,
    accepts_result as accepts_completed_count,
)
from .request_entity import (
    route as route_request_entity,
    accepts as accepts_request_entity,
)
from .name_identity import route as route_name_identity
from .dual_candidate import generate_and_select
from .encoded_identifier_storage import (
    normalize_context as normalize_encoded_identifier_storage_context,
)
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
from .month_axis_storage import (
    normalize_context as normalize_month_axis_storage_context,
)
from .endpoint_component import (
    VERSION as ENDPOINT_COMPONENT_VERSION,
    plan_endpoint_component,
    prove_endpoint_component,
    accepts_endpoint_count,
    route_endpoint_component,
    route_endpoint_request_contract,
    verified_storage_facts,
)
from .calendar_component import (
    VERSION as MONTH_COMPONENT_VERSION,
    plan_month_component,
    prove_yearmonth_axis,
    accepts_month_component,
    route_month_component,
)
from .text_mean_precision import (
    VERSION as TEXT_MEAN_PRECISION_VERSION,
    plan_text_mean,
    safe_text_mean_proof,
    accepts_text_mean_result,
    route_text_mean_precision,
)
from .percentage_threshold import (
    VERSION as PERCENTAGE_THRESHOLD_VERSION,
    plan_percentage_threshold,
    proven_fraction_witness,
    accepts_percentage_threshold,
    route_percentage_threshold,
)
from .state_lookup import (
    VERSION as STATE_LOOKUP_VERSION,
    plan_state_lookup,
    proven_state_key,
    proven_state_count,
    accepts_state_count,
    route_state_lookup,
)
from .period_literal import (
    VERSION as PERIOD_LITERAL_VERSION,
    plan_period_literal,
    prove_period_storage,
    accepts_period_result,
    route_period_literal,
)
from .annual_request import route_annual_request, matches_supported_annual_request
from .metric_source import VERSION as METRIC_SOURCE_VERSION, route_metric_source
from .metric_source_binding import (
    plan_annual_binding,
    proves_dimension_key,
    proved_annual_winner,
    accepts_annual_winner,
)
from .list_membership import (
    VERSION as LIST_MEMBERSHIP_VERSION,
    plan_list_membership,
    proved_other_count,
    accepts_other_count,
    route_list_membership,
)
from .outer_rounding import (
    VERSION as OUTER_ROUNDING_VERSION,
    plan_outer_rounding,
    proved_unrounded_value,
    accepts_unrounded,
    route_outer_rounding,
)
from .comparison_ratio import (
    VERSION as COMPARISON_RATIO_VERSION,
    plan_comparison_ratio,
    proven_comparison_ratio,
    accepts_comparison_ratio,
    route_comparison_ratio,
)
from .fraction_precision import (
    VERSION as FRACTION_PRECISION_VERSION,
    plan_fraction_precision,
    fraction_candidate,
    accepts_fraction_precision,
    route_fraction_precision,
)
from .name_format import (
    VERSION as NAME_FORMAT_VERSION,
    plan_name_format,
    accepts_name_format,
    route_name_format,
)
from .grouped_extremum import (
    VERSION as GROUPED_EXTREMUM_VERSION,
    plan_grouped_extremum,
    safe_grouped_extremum_proof,
    route_grouped_extremum,
)
from .null_extremum import (
    VERSION as NULL_EXTREMUM_VERSION,
    plan_null_extremum,
    original_missing_proved,
    candidate_measured_projection,
    route_null_extremum,
)
from .ranked_union import (
    VERSION as RANKED_UNION_VERSION,
    plan_ranked_union,
    route_ranked_union,
    accepts_ranked_union,
)
from .calendar_day import (
    VERSION as CALENDAR_DAY_VERSION,
    plan_calendar_day,
    route_calendar_day,
    proves_iso_boundary,
    accepts_calendar_result,
)
from .matched_percentage import (
    VERSION as MATCHED_PERCENTAGE_VERSION,
    plan_matched_percentage,
    route_matched_percentage,
    prove_matched_percentage,
    accepts_matched_percentage,
)
from .scaled_ratio import (
    VERSION as SCALED_RATIO_VERSION,
    plan_scaled_ratio,
    route_scaled_ratio,
    prove_scaled_ratio,
    accepts_scaled_ratio,
)
from .occurrence_percentage import (
    VERSION as OCCURRENCE_PERCENTAGE_VERSION,
    plan_occurrence_percentage,
    route_occurrence_percentage,
    prove_occurrence_percentage,
    accepts_occurrence_percentage,
    has_percentage_authorization,
)
from .output_completion import (
    VERSION as OUTPUT_COMPLETION_VERSION,
    output_shape,
    plan_output_completion,
    route_output_completion,
    accepts_output_completion,
)
from .projection_order import (
    VERSION as PROJECTION_ORDER_VERSION,
    projection_order_shape,
    plan_projection_order,
    route_projection_order,
)
from .projection_contract import (
    VERSION as PROJECTION_CONTRACT_VERSION,
    projection_shape,
    plan_projection_subset,
    accepts_projection_subset,
    route_projection_contract,
)
from .stable_first import (
    VERSION as STABLE_FIRST_VERSION,
    plan_stable_first,
    proven_stable_first_rows,
    accepts_stable_first_rows,
)
from .value_probe import create_stable_first_probe_executor
from .mean_precision import (
    VERSION as MEAN_PRECISION_VERSION,
    accepts_mean_result,
    accepts_hundred_indicator_result,
    plan_integer_mean,
    route_mean_precision,
    safe_mean_proof,
)
from .numeric_normalization import normalize_context as normalize_numeric_context
from .ranking_normalization import normalize_context as normalize_ranking_context
from .shared_entity_scope_normalization import (
    normalize_context as normalize_shared_entity_scope_context,
)
from .temporal_text_storage import (
    normalize_context as normalize_temporal_text_storage_context,
)
from .value_location_normalization import (
    normalize_context as normalize_value_location_context,
)
from .value_probe import (
    create_month_component_probe_executor,
    create_endpoint_component_probe_executor,
    create_period_literal_probe_executor,
    create_metric_source_probe_executor,
    create_list_membership_probe_executor,
    create_outer_rounding_probe_executor,
    create_integer_mean_probe_executor,
    create_text_mean_probe_executor,
    create_count_name_domain_executor,
    create_count_name_candidate_executor,
    create_identifier_quoting_probe_executor,
    create_percentage_threshold_probe_executor,
    create_null_extremum_probe_executor,
    create_projection_contract_probe_executor,
    create_projection_order_probe_executor,
    create_output_completion_probe_executor,
    create_occurrence_percentage_probe_executor,
    create_scaled_ratio_probe_executor,
    create_matched_percentage_probe_executor,
    create_calendar_day_probe_executor,
    create_ranked_union_probe_executor,
    create_grouped_extremum_probe_executor,
    create_name_format_probe_executor,
    create_fraction_precision_probe_executor,
    create_comparison_ratio_probe_executor,
    create_state_lookup_probe_executor,
    create_month_axis_probe_executor,
    create_value_probe_executor,
)


@dataclass
class _PendingAskSession:
    context: Any
    persist_query: bool
    workflow_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    surface: str = "unknown"
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
    "value_location_normalization",
    "encoded_identifier_storage",
    "temporal_text_storage",
    "month_axis_storage",
    "execution_result",
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
        "value_location_normalization",
        "encoded_identifier_storage",
        "temporal_text_storage",
        "month_axis_storage",
        "execution_result",
    }
)
_DEFERRED_CORRECTION_INTENTS = frozenset(
    {"temporal_text_storage", "month_axis_storage"}
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
            copy.deepcopy(value) if name in _CORRECTION_COPIED_FIELDS else value,
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
        value_location_normalization_enabled: bool = False,
        value_location_normalization_fn: Callable[..., Any] | None = None,
        encoded_identifier_storage_enabled: bool = False,
        encoded_identifier_storage_fn: Callable[..., Any] | None = None,
        temporal_text_storage_enabled: bool = False,
        temporal_text_storage_fn: Callable[..., Any] | None = None,
        month_axis_storage_enabled: bool = False,
        month_axis_storage_fn: Callable[..., Any] | None = None,
        period_literal_enabled: bool = False,
        metric_source_enabled: bool = False,
        list_membership_enabled: bool = False,
        outer_rounding_enabled: bool = False,
        integer_mean_precision_enabled: bool = False,
        text_mean_precision_enabled: bool = False,
        count_name_completion_enabled: bool = False,
        identifier_quoting_enabled: bool = False,
        percentage_threshold_enabled: bool = False,
        projection_contract_enabled: bool = False,
        grouped_extremum_enabled: bool = False,
        name_format_enabled: bool = False,
        fraction_precision_enabled: bool = False,
        comparison_ratio_enabled: bool = False,
        state_lookup_enabled: bool = False,
        month_component_enabled: bool = False,
        endpoint_component_enabled: bool = False,
        null_extremum_enabled: bool = False,
        projection_order_enabled: bool = False,
        output_completion_enabled: bool = False,
        occurrence_percentage_enabled: bool = False,
        scaled_ratio_enabled: bool = False,
        matched_percentage_enabled: bool = False,
        ranked_union_enabled: bool = False,
        calendar_day_enabled: bool = False,
        resolved_column_validation_enabled: bool = False,
        enum_overlap_advisory: bool = False,
        stable_first_enabled: bool = False,
        declared_count_enabled: bool = False,
    ):
        self._llm_manager = llm_manager
        self._period_literal_enabled = period_literal_enabled
        self._metric_source_enabled = metric_source_enabled
        self._list_membership_enabled = list_membership_enabled
        self._outer_rounding_enabled = outer_rounding_enabled
        self._integer_mean_precision_enabled = integer_mean_precision_enabled
        self._text_mean_precision_enabled = text_mean_precision_enabled
        self._count_name_completion_enabled = count_name_completion_enabled
        self._identifier_quoting_enabled = identifier_quoting_enabled
        self._percentage_threshold_enabled = percentage_threshold_enabled
        self._projection_contract_enabled = projection_contract_enabled
        self._grouped_extremum_enabled = grouped_extremum_enabled
        self._name_format_enabled = name_format_enabled
        self._fraction_precision_enabled = fraction_precision_enabled
        self._comparison_ratio_enabled = comparison_ratio_enabled
        self._state_lookup_enabled = state_lookup_enabled
        self._month_component_enabled = month_component_enabled
        self._endpoint_component_enabled = endpoint_component_enabled
        self._null_extremum_enabled = null_extremum_enabled
        self._projection_order_enabled = projection_order_enabled
        self._output_completion_enabled = output_completion_enabled
        self._occurrence_percentage_enabled = occurrence_percentage_enabled
        self._scaled_ratio_enabled = scaled_ratio_enabled
        self._matched_percentage_enabled = matched_percentage_enabled
        self._ranked_union_enabled = ranked_union_enabled
        self._calendar_day_enabled = calendar_day_enabled
        self._resolved_column_validation_enabled = resolved_column_validation_enabled
        self._enum_overlap_advisory = enum_overlap_advisory
        self._stable_first_enabled = stable_first_enabled
        self._declared_count_enabled = declared_count_enabled
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
        self._correction_intent_routing_shadow = bool(correction_intent_routing_shadow)
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
                "unknown correction intent scope: " + ", ".join(sorted(unknown_intents))
            )
        if len(set(requested_scope)) != len(requested_scope):
            raise ValueError("correction intent scope must contain unique names")
        self._correction_intent_routing_intent_scope = tuple(
            intent for intent in CORRECTION_INTENTS if intent in requested_scope
        )
        self._dual_candidate_selection_enabled = bool(dual_candidate_selection_enabled)
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
            all_rows_aggregate_normalization_fn or normalize_all_rows_aggregate_context
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
        self._value_location_normalization_enabled = bool(
            value_location_normalization_enabled
        )
        self._value_location_normalization_fn = (
            value_location_normalization_fn or normalize_value_location_context
        )
        self._encoded_identifier_storage_enabled = bool(
            encoded_identifier_storage_enabled
        )
        self._encoded_identifier_storage_fn = (
            encoded_identifier_storage_fn
            or normalize_encoded_identifier_storage_context
        )
        self._temporal_text_storage_enabled = bool(temporal_text_storage_enabled)
        self._temporal_text_storage_fn = (
            temporal_text_storage_fn or normalize_temporal_text_storage_context
        )
        self._month_axis_storage_enabled = bool(month_axis_storage_enabled)
        self._month_axis_storage_fn = (
            month_axis_storage_fn or normalize_month_axis_storage_context
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
        error_kind: str | None = None,
    ) -> AskErrorEvent:
        if error_kind == "query_timeout":
            return AskErrorEvent(
                type="error",
                message=message,
                phase=phase,
                code="query_timeout",
                category="query_timeout",
                target=target,
            )
        if error_kind in {"query_execution", "local_dependency"}:
            code = (
                "database_query_failed"
                if error_kind == "query_execution"
                else "database_driver_missing"
            )
            return AskErrorEvent(
                type="error",
                message=message,
                phase=phase,
                code=code,
                category=code,
                target=target,
            )
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
        workflow_id = str(uuid.uuid4())
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
            ctx.enum_overlap_advisory = self._enum_overlap_advisory
            ctx.resolved_column_validation_enabled = (
                self._resolved_column_validation_enabled
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
                with inference_workflow("ask", input.source, workflow_id):
                    ctx, interpretations, ambiguities = await asyncio.to_thread(
                        self._detect_ambiguities, ctx
                    )
                self._observe(AskPhase.CLARIFY, ctx)
                if ctx.status == Status.ERROR:
                    yield AskErrorEvent(
                        type="error",
                        message=ctx.error_message or "Failed to analyze question",
                        phase=AskPhase.CLARIFY,
                        code=ctx.error_code,
                        category=ctx.error_category,
                        target=target_name,
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
                        workflow_id=workflow_id,
                        surface=input.source,
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

            with inference_workflow("ask", input.source, workflow_id):
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
            with inference_workflow("ask", pending.surface, pending.workflow_id):
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

        if self._value_location_normalization_enabled:
            ctx = await self._apply_value_location_normalization(ctx)
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

        executed_sql = str(ctx.sql or "")
        if self._identifier_quoting_enabled:
            ctx = await self._apply_identifier_quoting(ctx)
        ctx = await self._apply_post_execution_repairs(ctx)
        if self._ranked_union_enabled:
            ctx = await self._apply_ranked_union(ctx)
        if self._calendar_day_enabled:
            ctx = await self._apply_calendar_day(ctx)
        if self._outer_rounding_enabled:
            ctx = await self._apply_outer_rounding(ctx)
        if self._integer_mean_precision_enabled:
            ctx = await self._apply_integer_mean_precision(ctx)
        if self._text_mean_precision_enabled:
            ctx = await self._apply_text_mean_precision(ctx)
        if self._state_lookup_enabled:
            ctx = await self._apply_state_lookup(ctx)
        if self._comparison_ratio_enabled:
            ctx = await self._apply_comparison_ratio(ctx)
        if self._fraction_precision_enabled:
            ctx = await self._apply_fraction_precision(ctx)
        if self._percentage_threshold_enabled:
            ctx = await self._apply_percentage_threshold(ctx)
        if self._projection_contract_enabled:
            ctx = await self._apply_projection_contract(ctx)
        if self._name_format_enabled:
            ctx = await self._apply_name_format(ctx)
        if self._grouped_extremum_enabled:
            ctx = await self._apply_grouped_extremum(ctx)
        if self._month_component_enabled:
            ctx = await self._apply_month_component(ctx)
        if self._null_extremum_enabled:
            ctx = await self._apply_null_extremum(ctx)
        if self._projection_order_enabled:
            ctx = await self._apply_projection_order(ctx)
        if self._list_membership_enabled:
            ctx = await self._apply_list_membership(ctx)
        if self._period_literal_enabled:
            ctx = await self._apply_period_literal(ctx)
        if self._metric_source_enabled:
            ctx = await self._apply_metric_source(ctx)
        if self._output_completion_enabled:
            ctx = await self._apply_output_completion(ctx)
        if self._matched_percentage_enabled:
            ctx = await self._apply_matched_percentage(ctx)
        if self._scaled_ratio_enabled:
            ctx = await self._apply_scaled_ratio(ctx)
        if self._occurrence_percentage_enabled:
            ctx = await self._apply_occurrence_percentage(ctx)
        if self._endpoint_component_enabled:
            ctx = await self._apply_endpoint_component(ctx)
        if self._count_name_completion_enabled:
            ctx = await self._apply_count_name_completion(ctx)
        if self._stable_first_enabled:
            ctx = await self._apply_stable_first(ctx)
        if self._declared_count_enabled:
            ctx = await self._apply_declared_count(ctx)
        sql_changed = not _sql_candidates_equivalent(
            executed_sql,
            str(ctx.sql or ""),
            ctx.db_type,
        )
        if (
            sql_changed
            or self._period_literal_enabled
            or self._metric_source_enabled
            or self._list_membership_enabled
            or self._outer_rounding_enabled
            or self._integer_mean_precision_enabled
            or self._text_mean_precision_enabled
            or self._count_name_completion_enabled
            or self._identifier_quoting_enabled
            or self._percentage_threshold_enabled
            or self._projection_contract_enabled
            or self._grouped_extremum_enabled
            or self._name_format_enabled
            or self._fraction_precision_enabled
            or self._comparison_ratio_enabled
            or self._state_lookup_enabled
            or self._month_component_enabled
            or self._endpoint_component_enabled
            or self._null_extremum_enabled
            or self._projection_order_enabled
            or self._output_completion_enabled
            or self._occurrence_percentage_enabled
            or self._scaled_ratio_enabled
            or self._matched_percentage_enabled
            or self._ranked_union_enabled
            or self._calendar_day_enabled
            or self._stable_first_enabled
            or self._declared_count_enabled
        ):
            self._observe(AskPhase.EXECUTE, ctx)
        if sql_changed:
            yield AskSqlGeneratedEvent(
                type="sql_generated",
                sql=ctx.sql or "",
                explanation=ctx.sql_explanation,
            )

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
            ctx.execution_result.error_kind if ctx.execution_result else None,
        )

    async def _apply_declared_count(self, ctx: Any) -> Any:
        return await apply_declared_count_correction(
            ctx, self._llm_manager, self._db_executor,
            snapshot_correction=_snapshot_correction_state,
            restore_correction=_restore_correction_state,
        )

    async def _apply_period_literal(self, ctx: Any) -> Any:
        """Prove compact month storage and independently verify the aggregate."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": PERIOD_LITERAL_VERSION, "status": "unchanged"}
        ctx.period_literal = diagnostics
        if result is None or result.error or result.truncated:
            diagnostics["reason"] = "primary-result-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        plan = plan_period_literal(ctx.sql or "", ctx.db_type, ctx.schema_info)
        if plan is None:
            diagnostics["reason"] = "unsupported-shape-or-type"
            return ctx
        try:
            routing = await asyncio.to_thread(
                route_period_literal,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="period_literal_routing", **kwargs
                ),
            )
            diagnostics["routing"] = routing
            if not routing["activate"]:
                diagnostics["reason"] = "model-abstained"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = plan.candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_period_literal_probe_executor(ctx, self._db_executor)
            proof = await asyncio.to_thread(bounded, plan.proof_sql, ctx.target_config)
            diagnostics["proof_sql"] = plan.proof_sql
            diagnostics["proof"] = proof
            if not proof.get("success") or not prove_period_storage(
                plan, proof.get("rows", [])
            ):
                diagnostics["reason"] = "unsafe-or-unavailable-proof"
                return ctx
            witness = await asyncio.to_thread(
                bounded, plan.witness_sql, ctx.target_config
            )
            diagnostics["witness_sql"] = plan.witness_sql
            diagnostics["witness"] = witness
            if not witness.get("success"):
                diagnostics["reason"] = "independent-witness-unavailable"
                return ctx
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = plan.candidate_sql
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_period_result(
                    result.rows, witness.get("rows", []), after.rows
                )
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted",
                    reason="candidate-disagrees-with-formatted-period-witness",
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.period_literal = diagnostics
        return ctx

    async def _apply_metric_source(self, ctx: Any) -> Any:
        """Bind an explicitly requested measure after scope and annual-value proofs."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": METRIC_SOURCE_VERSION, "status": "unchanged"}
        ctx.metric_source = diagnostics
        if result is None or result.error or result.truncated:
            diagnostics["reason"] = "primary-result-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        plan = plan_annual_binding(ctx.sql or "", ctx.db_type, ctx.schema_info)
        if plan is None:
            diagnostics["reason"] = "unsupported-shape-or-type"
            return ctx
        try:
            request = await asyncio.to_thread(
                route_annual_request,
                ctx.refined_question or ctx.question,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="annual_request_routing", **kwargs
                ),
            )
            diagnostics["annual_request"] = request
            if not matches_supported_annual_request(
                ctx.refined_question or ctx.question,
                request["contract"],
                plan.facts()["dimension_filters"],
            ):
                diagnostics["reason"] = "unsupported-request-contract"
                return ctx
            routing = await asyncio.to_thread(
                route_metric_source,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                ctx.schema_info.to_dict(),
                plan.facts(),
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="metric_source_routing", **kwargs
                ),
            )
            diagnostics["routing"] = routing
            if not routing["activate"]:
                diagnostics["reason"] = "model-abstained"
                return ctx
            selected = routing["option_id"]
            sql = plan.candidate_sql(selected)
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not sql or not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            diagnostics["selected_binding"] = next(
                x for x in plan.options if x["option_id"] == selected
            )
            bounded = create_metric_source_probe_executor(ctx, self._db_executor)
            diagnostics["dimension_proof_sql"] = plan.dimension_proof_sql()
            dimension = await asyncio.to_thread(
                bounded, diagnostics["dimension_proof_sql"], ctx.target_config
            )
            diagnostics["dimension_proof"] = dimension
            if not dimension.get("success") or not proves_dimension_key(
                dimension.get("rows", [])
            ):
                diagnostics["reason"] = "dimension-key-not-proved-unique"
                return ctx
            diagnostics["period_proof_sql"] = plan.period_proof_sql(selected)
            periods = await asyncio.to_thread(
                bounded, diagnostics["period_proof_sql"], ctx.target_config
            )
            diagnostics["period_proof"] = periods
            expected = (
                proved_annual_winner(periods.get("rows", []))
                if periods.get("success")
                else None
            )
            if expected is None:
                diagnostics["reason"] = "calendar-or-unique-winner-not-proved"
                return ctx
            diagnostics["expected_year"] = expected
            diagnostics["candidate_sql"] = sql
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_annual_winner(expected, after.rows)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted", reason="candidate-disagrees-with-period-witness"
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.metric_source = diagnostics
        return ctx

    async def _apply_list_membership(self, ctx: Any) -> Any:
        """Prove complete scoped list storage before changing a membership count."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": LIST_MEMBERSHIP_VERSION, "status": "unchanged"}
        ctx.list_membership = diagnostics
        if result is None or result.error or result.truncated:
            diagnostics["reason"] = "primary-result-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        plan = plan_list_membership(ctx.sql or "", ctx.db_type)
        if plan is None:
            diagnostics["reason"] = "unsupported-shape-or-type"
            return ctx
        try:
            routing = await asyncio.to_thread(
                route_list_membership,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="list_membership_routing", **kwargs
                ),
            )
            diagnostics["routing"] = routing
            if not routing["activate"]:
                diagnostics["reason"] = "model-abstained"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = plan.candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_list_membership_probe_executor(
                ctx,
                self._db_executor,
                require_complete=plan.predicate_kind == "null_empty_set_absence",
            )
            proof = await asyncio.to_thread(bounded, plan.proof_sql, ctx.target_config)
            diagnostics["proof_sql"] = plan.proof_sql
            diagnostics["proof"] = proof
            expected = (
                proved_other_count(plan, result.rows, proof.get("rows", []))
                if proof.get("success")
                else None
            )
            if expected is None:
                diagnostics["reason"] = "unsafe-or-unavailable-proof"
                return ctx
            diagnostics["expected_other_count"] = str(expected)
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = plan.candidate_sql
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_other_count(expected, after.rows)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted",
                    reason="candidate-disagrees-with-token-count-witness",
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.list_membership = diagnostics
        return ctx

    async def _apply_outer_rounding(self, ctx: Any) -> Any:
        """Remove only outer rounding after proving both expression values."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": OUTER_ROUNDING_VERSION, "status": "unchanged"}
        ctx.outer_rounding = diagnostics
        if result is None or result.error or result.truncated:
            diagnostics["reason"] = "primary-result-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        plan = plan_outer_rounding(ctx.sql or "", ctx.db_type)
        if plan is None:
            diagnostics["reason"] = "unsupported-shape-or-type"
            return ctx
        try:
            routing = await asyncio.to_thread(
                route_outer_rounding,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="outer_rounding_routing", **kwargs
                ),
            )
            diagnostics["routing"] = routing
            if not routing["activate"]:
                diagnostics["reason"] = "model-abstained"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = plan.candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_outer_rounding_probe_executor(ctx, self._db_executor)
            proof = await asyncio.to_thread(bounded, plan.proof_sql, ctx.target_config)
            diagnostics["proof_sql"] = plan.proof_sql
            diagnostics["proof"] = proof
            expected = (
                proved_unrounded_value(result.rows, proof.get("rows", []))
                if proof.get("success")
                else None
            )
            if expected is None:
                diagnostics["reason"] = "unsafe-or-unavailable-proof"
                return ctx
            diagnostics["expected_unrounded"] = str(expected)
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = plan.candidate_sql
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_unrounded(expected, after.rows)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted",
                    reason="candidate-disagrees-with-simultaneous-witness",
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.outer_rounding = diagnostics
        return ctx

    async def _apply_integer_mean_precision(self, ctx: Any) -> Any:
        """Try one model-approved mean with a range proof and result agreement."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": MEAN_PRECISION_VERSION, "status": "unchanged"}
        ctx.integer_mean_precision = diagnostics
        if result is None or result.error or result.truncated:
            diagnostics["reason"] = "primary-result-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        plan = plan_integer_mean(ctx.sql or "", ctx.db_type, ctx.schema_info)
        if plan is None:
            diagnostics["reason"] = "unsupported-shape-or-type"
            return ctx
        try:
            routing = await asyncio.to_thread(
                route_mean_precision,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="integer_mean_precision_routing", **kwargs
                ),
            )
            diagnostics["routing"] = routing
            if not routing["activate"]:
                diagnostics["reason"] = "model-abstained"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = plan.candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_integer_mean_probe_executor(
                ctx, self._db_executor, require_complete=plan.binary_ceiling == 100
            )
            proof = await asyncio.to_thread(bounded, plan.proof_sql, ctx.target_config)
            diagnostics["proof_sql"] = plan.proof_sql
            diagnostics["proof"] = proof
            if not proof.get("success") or not safe_mean_proof(
                proof.get("rows", []),
                binary=plan.binary,
                binary_ceiling=plan.binary_ceiling,
            ):
                diagnostics["reason"] = "unsafe-or-unavailable-proof"
                return ctx
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = plan.candidate_sql
            if (
                after is None
                or after.error
                or after.truncated
                or not (
                    accepts_hundred_indicator_result(
                        result.rows,
                        after.rows,
                        proof.get("rows", []),
                        native_fractional_places=plan.native_fractional_places,
                    )
                    if plan.binary_ceiling == 100
                    else accepts_mean_result(result.rows, after.rows, scale=plan.scale)
                )
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted",
                    reason="candidate-result-not-equivalent-within-native-precision",
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.integer_mean_precision = diagnostics
        return ctx

    async def _apply_text_mean_precision(self, ctx: Any) -> Any:
        """Try one model-approved mean with a range proof and result agreement."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": TEXT_MEAN_PRECISION_VERSION, "status": "unchanged"}
        ctx.text_mean_precision = diagnostics
        if result is None or result.error or result.truncated:
            diagnostics["reason"] = "primary-result-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        plan = plan_text_mean(ctx.sql or "", ctx.db_type, ctx.schema_info)
        if plan is None:
            diagnostics["reason"] = "unsupported-shape-or-type"
            return ctx
        try:
            routing = await asyncio.to_thread(
                route_text_mean_precision,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="text_mean_precision_routing", **kwargs
                ),
            )
            diagnostics["routing"] = routing
            if not routing["activate"]:
                diagnostics["reason"] = "model-abstained"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = plan.candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_text_mean_probe_executor(ctx, self._db_executor)
            proof = await asyncio.to_thread(bounded, plan.proof_sql, ctx.target_config)
            diagnostics["proof_sql"] = plan.proof_sql
            diagnostics["proof"] = proof
            if not proof.get("success") or not safe_text_mean_proof(
                proof.get("rows", []), plan.scale
            ):
                diagnostics["reason"] = "unsafe-or-unavailable-proof"
                return ctx
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = plan.candidate_sql
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_text_mean_result(
                    result.rows, after.rows, proof.get("rows", []), plan.scale
                )
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted",
                    reason="candidate-result-outside-original-quantization",
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.text_mean_precision = diagnostics
        return ctx

    async def _apply_stable_first(self, ctx: Any) -> Any:
        """Resolve an existing top-one tie after ranking and identity proofs."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": STABLE_FIRST_VERSION, "status": "unchanged"}
        ctx.stable_first = diagnostics
        if result is None or result.error or result.truncated or len(result.rows) != 1:
            diagnostics["reason"] = "complete-primary-unavailable"
            return ctx
        plan = plan_stable_first(ctx.sql or "", ctx.db_type, ctx.schema_info)
        if plan is None or len(result.rows[0]) != plan.output_columns:
            diagnostics["reason"] = "unsupported-shape-or-identity"
            return ctx
        try:
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = plan.candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_stable_first_probe_executor(ctx, self._db_executor)
            baseline = await asyncio.to_thread(
                bounded, plan.baseline_rank_sql, ctx.target_config
            )
            diagnostics.update(baseline_rank_sql=plan.baseline_rank_sql, baseline=baseline)
            if not baseline.get("success") or baseline.get("truncated"):
                diagnostics["reason"] = "incomplete-ranking-proof"
                return ctx
            proof = await asyncio.to_thread(
                bounded, plan.candidate_rank_sql, ctx.target_config
            )
            diagnostics.update(candidate_rank_sql=plan.candidate_rank_sql, proof=proof)
            expected = (
                proven_stable_first_rows(plan, baseline.get("rows", []), proof.get("rows", []))
                if proof.get("success") and not proof.get("truncated")
                else None
            )
            if expected is None:
                diagnostics["reason"] = "rank-or-identity-not-proven"
                return ctx
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = plan.candidate_sql
            if after is None or after.error or after.truncated or not accepts_stable_first_rows(expected, after.rows):
                _restore_correction_state(ctx, original)
                diagnostics.update(status="reverted", reason="candidate-disagrees-with-proof")
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(status="reverted", reason="proof-failed", error_kind=type(exc).__name__)
        ctx.stable_first = diagnostics
        return ctx

    async def _apply_state_lookup(self, ctx: Any) -> Any:
        """Ground an explicit state using a unique lookup and a scoped subset proof."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": STATE_LOOKUP_VERSION, "status": "unchanged"}
        ctx.state_lookup = diagnostics
        if result is None or result.error or result.truncated or len(result.rows) != 1:
            diagnostics["reason"] = "primary-result-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        plan = plan_state_lookup(ctx.sql or "", ctx.db_type, ctx.schema_info)
        if plan is None:
            diagnostics["reason"] = "unsupported-shape-or-type"
            return ctx
        diagnostics["structural_facts"] = plan.structural_facts()
        try:
            routing = await asyncio.to_thread(
                route_state_lookup,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="state_lookup_routing", **kwargs
                ),
                structural_facts=diagnostics["structural_facts"],
            )
            diagnostics["routing"] = routing
            if not routing["activate"]:
                diagnostics["reason"] = "model-abstained"
                return ctx
            bounded = create_state_lookup_probe_executor(
                ctx, self._db_executor, require_complete=True
            )
            state = routing["classification"]["state_excerpt"]
            lookup_sql = plan.lookup_sql(state)
            if lookup_sql is None:
                diagnostics["reason"] = "state-label-unavailable"
                return ctx
            lookup = await asyncio.to_thread(bounded, lookup_sql, ctx.target_config)
            diagnostics.update(lookup_sql=lookup_sql, lookup=lookup)
            key = (
                proven_state_key(lookup.get("rows", []), state)
                if lookup.get("success")
                else None
            )
            if key is None:
                diagnostics["reason"] = "state-label-or-key-not-unique"
                return ctx
            candidate_sql = plan.candidate_sql(key)
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            proof_sql = plan.scope_proof_sql(key)
            proof = await asyncio.to_thread(bounded, proof_sql, ctx.target_config)
            diagnostics.update(scope_proof_sql=proof_sql, scope_proof=proof)
            expected = (
                proven_state_count(proof.get("rows", []), result.rows)
                if proof.get("success")
                else None
            )
            if expected is None:
                diagnostics["reason"] = "state-subset-not-proven"
                return ctx
            diagnostics["expected_count"] = expected
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = candidate_sql
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_state_count(expected, after.rows)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted", reason="candidate-state-count-disagrees"
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.state_lookup = diagnostics
        return ctx

    async def _apply_comparison_ratio(self, ctx: Any) -> Any:
        """Return a requested multiplicative factor after proving a single pair."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": COMPARISON_RATIO_VERSION, "status": "unchanged"}
        ctx.comparison_ratio = diagnostics
        if result is None or result.error or result.truncated or len(result.rows) != 1:
            diagnostics["reason"] = "primary-result-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        plan = plan_comparison_ratio(ctx.sql or "", ctx.db_type, ctx.schema_info)
        if plan is None:
            diagnostics["reason"] = "unsupported-shape-or-type"
            return ctx
        try:
            routing = await asyncio.to_thread(
                route_comparison_ratio,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="comparison_ratio_routing", **kwargs
                ),
            )
            diagnostics["routing"] = routing
            if not routing["activate"]:
                diagnostics["reason"] = "model-abstained"
                return ctx
            bounded = create_comparison_ratio_probe_executor(ctx, self._db_executor)
            proof = await asyncio.to_thread(bounded, plan.proof_sql, ctx.target_config)
            diagnostics.update(proof_sql=plan.proof_sql, proof=proof)
            numerator_operand = routing["classification"]["numerator_operand"]
            expected = (
                proven_comparison_ratio(
                    proof.get("rows", []), result.rows, numerator_operand
                )
                if proof.get("success")
                else None
            )
            if expected is None:
                diagnostics["reason"] = "single-comparison-pair-not-proven"
                return ctx
            diagnostics["expected_ratio"] = expected
            candidate_sql = plan.candidate_sql(numerator_operand)
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = candidate_sql
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_comparison_ratio(expected, after.rows)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted", reason="candidate-ratio-disagrees"
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.comparison_ratio = diagnostics
        return ctx

    async def _apply_fraction_precision(self, ctx: Any) -> Any:
        """Recompute an unrounded rate after proving its stored fraction relation."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": FRACTION_PRECISION_VERSION, "status": "unchanged"}
        ctx.fraction_precision = diagnostics
        if (
            result is None
            or result.error
            or result.truncated
            or not 1 <= len(result.rows) <= 10_000
        ):
            diagnostics["reason"] = "primary-result-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        plan = plan_fraction_precision(ctx.sql or "", ctx.db_type, ctx.schema_info)
        if plan is None:
            diagnostics["reason"] = "unsupported-shape-or-type"
            return ctx
        try:
            routing = await asyncio.to_thread(
                route_fraction_precision,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="fraction_precision_routing", **kwargs
                ),
            )
            diagnostics["routing"] = routing
            if not routing["activate"]:
                diagnostics["reason"] = "model-abstained"
                return ctx
            bounded = create_fraction_precision_probe_executor(ctx, self._db_executor)
            proof = await asyncio.to_thread(bounded, plan.proof_sql, ctx.target_config)
            diagnostics.update(proof_sql=plan.proof_sql, proof=proof)
            candidate_sql = (
                fraction_candidate(plan, proof.get("rows", []))
                if proof.get("success")
                else None
            )
            if candidate_sql is None:
                diagnostics["reason"] = "fractional-storage-not-uniquely-proven"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = candidate_sql
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_fraction_precision(plan, result.rows, after.rows)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted", reason="candidate-fraction-values-disagree"
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.fraction_precision = diagnostics
        return ctx

    async def _apply_count_name_completion(self, ctx: Any) -> Any:
        """Complete one independently named entity in a proved count comparison."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": COUNT_NAME_COMPLETION_VERSION, "status": "unchanged"}
        ctx.count_name_completion = diagnostics
        if result is None or result.error or result.truncated:
            diagnostics["reason"] = "complete-primary-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        try:
            plan = plan_count_name(ctx.sql or "", ctx.db_type, ctx.schema_info)
            if plan is None:
                diagnostics["reason"] = "unsupported-count-comparison"
                return ctx
            domain_executor = create_count_name_domain_executor(ctx, self._db_executor)
            domain = await asyncio.to_thread(
                domain_executor, plan.domain_sql, ctx.target_config
            )
            diagnostics.update(domain_sql=plan.domain_sql, domain=domain)
            witness = (
                name_from_domain(plan, domain.get("rows"))
                if domain.get("success")
                else None
            )
            if witness is None:
                diagnostics["reason"] = "unique-missing-name-not-proven"
                return ctx
            question = ctx.refined_question or ctx.question
            request = await asyncio.to_thread(
                route_request_entity,
                question,
                witness.short,
                self._llm_manager,
                lambda **kw: ctx.add_llm_call(phase="request_entity_expansion", **kw),
            )
            diagnostics["request_entity"] = request
            if not accepts_request_entity(
                question, witness.short, witness.full, request["classification"]
            ):
                diagnostics["reason"] = "independent-request-name-disagrees"
                return ctx
            identity = await asyncio.to_thread(
                route_name_identity,
                question,
                witness.short,
                witness.full,
                self._llm_manager,
                lambda **kw: ctx.add_llm_call(
                    phase="named_entity_label_completion", **kw
                ),
            )
            diagnostics["name_identity"] = identity
            if not identity["activate"]:
                diagnostics["reason"] = "name-identity-not-established"
                return ctx
            sql = completed_name_sql(plan, witness)
            proof_sql = count_proof_sql(plan, witness)
            if sql is None or proof_sql is None:
                diagnostics["reason"] = "candidate-unavailable"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid or candidate["sql"] != sql:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_count_name_candidate_executor(ctx, self._db_executor)
            proof = await asyncio.to_thread(bounded, proof_sql, ctx.target_config)
            diagnostics.update(
                count_proof_sql=proof_sql,
                count_proof=proof,
                short_name=witness.short,
                full_name=witness.full,
                operand_index=witness.index,
                candidate_sql=sql,
            )
            expected = (
                proved_completed_count(plan, witness, result.rows, proof.get("rows"))
                if proof.get("success")
                else None
            )
            if expected is None:
                diagnostics["reason"] = "count-components-not-proven"
                return ctx
            diagnostics["expected_result"] = expected
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            if (
                ctx.sql != sql
                or after is None
                or after.error
                or after.truncated
                or not accepts_completed_count(expected, after.rows)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted", reason="candidate-count-disagrees"
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.count_name_completion = diagnostics
        return ctx

    async def _apply_identifier_quoting(self, ctx: Any) -> Any:
        """Quote proved reserved table tokens after a native MySQL syntax error."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": IDENTIFIER_QUOTING_VERSION, "status": "unchanged"}
        ctx.identifier_quoting = diagnostics
        if result is None or not result.error:
            diagnostics["reason"] = "no-primary-error"
            return ctx
        try:
            plan = plan_identifier_quoting(
                ctx.sql or "", ctx.db_type, ctx.schema_info, result.error
            )
            if plan is None:
                diagnostics["reason"] = "unsupported-syntax-error-or-shape"
                return ctx
            bounded = create_identifier_quoting_probe_executor(ctx, self._db_executor)
            proof = await asyncio.to_thread(bounded, plan.proof_sql, ctx.target_config)
            diagnostics.update(
                original_sql=ctx.sql, proof_sql=plan.proof_sql, proof=proof
            )
            candidate_sql = (
                candidate_from_keywords(plan, proof.get("rows"))
                if proof.get("success")
                else None
            )
            if candidate_sql is None:
                diagnostics["reason"] = "reserved-table-word-not-proven"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid or candidate["sql"] != candidate_sql:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = candidate_sql
            if (
                ctx.sql != candidate_sql
                or after is None
                or after.error
                or after.truncated
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted", reason="candidate-incomplete-or-failed"
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.identifier_quoting = diagnostics
        return ctx

    async def _apply_percentage_threshold(self, ctx: Any) -> Any:
        """Scale one explicit percentage threshold after a fractional-unit proof."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": PERCENTAGE_THRESHOLD_VERSION, "status": "unchanged"}
        ctx.percentage_threshold = diagnostics
        if result is None or result.error or result.truncated:
            diagnostics["reason"] = "primary-result-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        plan = plan_percentage_threshold(ctx.sql or "", ctx.db_type, ctx.schema_info)
        if plan is None:
            diagnostics["reason"] = "unsupported-shape-or-type"
            return ctx
        try:
            routing = await asyncio.to_thread(
                route_percentage_threshold,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="percentage_threshold_routing", **kwargs
                ),
            )
            diagnostics["routing"] = routing
            if not routing["activate"]:
                diagnostics["reason"] = "model-abstained"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = plan.candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_percentage_threshold_probe_executor(
                ctx, self._db_executor, require_complete=True
            )
            proof = await asyncio.to_thread(bounded, plan.proof_sql, ctx.target_config)
            diagnostics.update(proof_sql=plan.proof_sql, proof=proof)
            witness = (
                proven_fraction_witness(plan, proof.get("rows", []))
                if proof.get("success")
                else None
            )
            if witness is None:
                diagnostics["reason"] = "fractional-storage-not-uniquely-proven"
                return ctx
            diagnostics["witness_columns"] = list(witness)
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = plan.candidate_sql
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_percentage_threshold(plan, result.rows, after.rows)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted", reason="candidate-count-not-monotonic"
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.percentage_threshold = diagnostics
        return ctx

    async def _apply_null_extremum(self, ctx):
        """Replace a proved missing ranking winner with a measured one."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": NULL_EXTREMUM_VERSION, "status": "unchanged"}
        ctx.null_extremum = diagnostics
        if result is None or result.error or result.truncated or len(result.rows) != 1:
            diagnostics["reason"] = "complete-singleton-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        plan = plan_null_extremum(ctx.sql or "", ctx.db_type, ctx.schema_info)
        if plan is None:
            diagnostics["reason"] = "unsupported-shape-or-type"
            return ctx
        try:
            routing = await asyncio.to_thread(
                route_null_extremum,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="null_extremum_routing", **kwargs
                ),
            )
            diagnostics["routing"] = routing
            if not routing["activate"]:
                diagnostics["reason"] = "model-abstained"
                return ctx
            bounded = create_null_extremum_probe_executor(ctx, self._db_executor)
            proof = await asyncio.to_thread(
                bounded, plan.original_proof_sql, ctx.target_config
            )
            diagnostics.update(
                original_proof_sql=plan.original_proof_sql, original_proof=proof
            )
            if not proof.get("success") or not original_missing_proved(
                plan, result.rows, proof.get("rows", [])
            ):
                diagnostics["reason"] = "original-missing-value-not-proved"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = plan.candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            proof = await asyncio.to_thread(
                bounded, plan.candidate_proof_sql, ctx.target_config
            )
            diagnostics.update(
                candidate_proof_sql=plan.candidate_proof_sql, candidate_proof=proof
            )
            expected = (
                candidate_measured_projection(plan, proof.get("rows", []))
                if proof.get("success")
                else None
            )
            if expected is None:
                diagnostics["reason"] = "measured-candidate-not-proved"
                return ctx
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = plan.candidate_sql
            if (
                after is None
                or after.error
                or after.truncated
                or [tuple(row) for row in after.rows] != expected
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted", reason="candidate-result-disagrees"
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.null_extremum = diagnostics
        return ctx

    async def _apply_projection_contract(self, ctx: Any) -> Any:
        """Keep a requested output subset only when complete rows agree exactly."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": PROJECTION_CONTRACT_VERSION, "status": "unchanged"}
        ctx.projection_contract = diagnostics
        if (
            result is None
            or result.error
            or result.truncated
            or not 1 <= len(result.rows) <= 10_000
        ):
            diagnostics["reason"] = "complete-nonempty-primary-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        if projection_shape(ctx.sql or "", ctx.db_type) is None:
            diagnostics["reason"] = "unsupported-shape-or-type"
            return ctx
        try:
            routing = await asyncio.to_thread(
                route_projection_contract,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="projection_contract_routing", **kwargs
                ),
            )
            diagnostics["routing"] = routing
            plan = plan_projection_subset(ctx.sql, ctx.db_type, routing["keep_indices"])
            if plan is None:
                diagnostics["reason"] = "no-safe-requested-subset"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = plan.candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_projection_contract_probe_executor(ctx, self._db_executor)
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics.update(
                candidate_sql=plan.candidate_sql, keep_indices=list(plan.keep_indices)
            )
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_projection_subset(plan, result.rows, after.rows)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted", reason="projected-result-disagrees"
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.projection_contract = diagnostics
        return ctx

    async def _apply_ranked_union(self, ctx: Any) -> Any:
        original = _snapshot_correction_state(ctx)
        diagnostics = {"version": RANKED_UNION_VERSION, "status": "unchanged"}
        ctx.ranked_union = diagnostics
        if ctx.execution_result is None or not ctx.execution_result.error:
            diagnostics["reason"] = "original-execution-error-required"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        plan = plan_ranked_union(ctx.sql or "", ctx.db_type)
        if plan is None:
            diagnostics["reason"] = "unsupported-shape-or-type"
            return ctx
        try:
            routing = await asyncio.to_thread(
                route_ranked_union,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="ranked_union_routing", **kwargs
                ),
            )
            diagnostics["routing"] = routing
            if not routing["activate"]:
                diagnostics["reason"] = "requested-row-positions-not-established"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = plan.candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_ranked_union_probe_executor(ctx, self._db_executor)
            branches = []
            for sql in plan.branch_sql:
                proof = await asyncio.to_thread(bounded, sql, ctx.target_config)
                if (
                    not proof.get("success")
                    or proof.get("truncated")
                    or len(proof.get("rows", [])) != 1
                ):
                    diagnostics["reason"] = "independent-singleton-proof-failed"
                    return ctx
                branches.append(proof["rows"])
            diagnostics.update(
                candidate_sql=plan.candidate_sql,
                branch_sql=list(plan.branch_sql),
                positions=list(plan.positions),
                branch_rows=branches,
            )
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            result = ctx.execution_result
            if (
                result is None
                or result.error
                or result.truncated
                or not accepts_ranked_union(plan, branches, result.rows)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted", reason="combined-row-proof-disagrees"
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.ranked_union = diagnostics
        return ctx

    async def _apply_calendar_day(self, ctx: Any) -> Any:
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": CALENDAR_DAY_VERSION, "status": "unchanged"}
        ctx.calendar_day = diagnostics
        if result is None or result.error or result.truncated:
            diagnostics["reason"] = "complete-primary-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        plan = plan_calendar_day(ctx.sql or "", ctx.db_type, ctx.schema_info)
        if plan is None:
            diagnostics["reason"] = "unsupported-shape-or-type"
            return ctx
        try:
            routing = await asyncio.to_thread(
                route_calendar_day,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="calendar_day_routing", **kwargs
                ),
                structural_facts=plan.facts,
            )
            diagnostics["routing"] = routing
            if not routing["activate"]:
                diagnostics["reason"] = "calendar-day-intent-not-established"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = plan.candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_calendar_day_probe_executor(ctx, self._db_executor)
            storage = await asyncio.to_thread(
                bounded, plan.storage_sql, ctx.target_config
            )
            diagnostics.update(storage_sql=plan.storage_sql, storage_proof=storage)
            if (
                not storage.get("success")
                or storage.get("truncated")
                or not proves_iso_boundary(plan, storage.get("rows", []))
            ):
                diagnostics["reason"] = "complete-iso-boundary-not-proved"
                return ctx
            expected = await asyncio.to_thread(
                bounded, plan.date_result_sql, ctx.target_config
            )
            diagnostics.update(
                date_result_sql=plan.date_result_sql, date_result_proof=expected
            )
            if (
                not expected.get("success")
                or expected.get("truncated")
                or len(expected.get("rows", [])) > 10000
            ):
                diagnostics["reason"] = "date-extraction-result-unavailable"
                return ctx
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = plan.candidate_sql
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_calendar_result(
                    plan, expected.get("rows", []), after.rows
                )
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted", reason="calendar-result-disagrees"
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.calendar_day = diagnostics
        return ctx

    async def _apply_matched_percentage(self, ctx: Any) -> Any:
        """Use a proved matched-entity count for an explicitly requested percent."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": MATCHED_PERCENTAGE_VERSION, "status": "unchanged"}
        ctx.matched_percentage = diagnostics
        if result is None or result.error or result.truncated or len(result.rows) != 1:
            diagnostics["reason"] = "complete-scalar-primary-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        try:
            plan = plan_matched_percentage(ctx.sql or "", ctx.db_type, ctx.schema_info)
            if plan is None:
                diagnostics["reason"] = "unsupported-shape-or-type"
                return ctx
            routing = await asyncio.to_thread(
                route_matched_percentage,
                ctx.refined_question or ctx.question,
                plan,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(**kwargs),
            )
            diagnostics["routing"] = routing
            if not routing["apply"]:
                diagnostics["reason"] = "no-unambiguous-matched-entity-denominator"
                return ctx
            candidate = dict(original)
            candidate["sql"] = plan.candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_matched_percentage_probe_executor(ctx, self._db_executor)
            stats = await asyncio.to_thread(bounded, plan.proof_sql, ctx.target_config)
            proof = (
                prove_matched_percentage(result.rows, stats.get("rows", []))
                if stats.get("success")
                else None
            )
            diagnostics.update(
                proof_sql=plan.proof_sql, proof_result=stats, proof=proof
            )
            if proof is None:
                diagnostics["reason"] = "unmatched-population-not-proven"
                return ctx
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = plan.candidate_sql
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_matched_percentage(proof, after.rows)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted", reason="matched-percentage-result-disagrees"
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.matched_percentage = diagnostics
        return ctx

    async def _apply_scaled_ratio(self, ctx: Any) -> Any:
        """Use one rounding step only after exact operand and result proofs."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": SCALED_RATIO_VERSION, "status": "unchanged"}
        ctx.scaled_ratio = diagnostics
        if result is None or result.error or result.truncated or len(result.rows) != 1:
            diagnostics["reason"] = "complete-scalar-primary-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        try:
            plan = plan_scaled_ratio(ctx.sql or "", ctx.db_type)
            if plan is None:
                diagnostics["reason"] = "unsupported-shape-or-type"
                return ctx
            routing = await asyncio.to_thread(
                route_scaled_ratio,
                ctx.refined_question or ctx.question,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(**kwargs),
            )
            diagnostics["routing"] = routing
            if not routing["activate"]:
                diagnostics["reason"] = "numeric-presentation-constrained-or-uncertain"
                return ctx
            for sql in (plan.proof_sql, plan.candidate_sql):
                trial = dict(original)
                trial["sql"] = sql
                valid, issues = await self._preflight_correction_candidate(ctx, trial)
                if not valid:
                    diagnostics.update(
                        reason="candidate-validation-failed", issues=issues
                    )
                    return ctx
            bounded = create_scaled_ratio_probe_executor(ctx, self._db_executor)
            stats = await asyncio.to_thread(bounded, plan.proof_sql, ctx.target_config)
            proof = (
                prove_scaled_ratio(result.rows, stats.get("rows", []))
                if stats.get("success")
                else None
            )
            diagnostics.update(
                proof_sql=plan.proof_sql, proof_result=stats, proof=proof
            )
            if proof is None:
                diagnostics["reason"] = (
                    "exact-operands-or-strict-precision-gain-not-proven"
                )
                return ctx
            candidate = dict(original)
            candidate["sql"] = plan.candidate_sql
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = plan.candidate_sql
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_scaled_ratio(proof, after.rows)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted", reason="candidate-not-proved-nearest-float"
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.scaled_ratio = diagnostics
        return ctx

    async def _apply_occurrence_percentage(self, ctx: Any) -> Any:
        """Remove key deduplication only for independently requested linked occurrences."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": OCCURRENCE_PERCENTAGE_VERSION, "status": "unchanged"}
        ctx.occurrence_percentage = diagnostics
        if (
            ctx.schema_info is None
            or result is None
            or result.error
            or result.truncated
            or len(result.rows) != 1
        ):
            diagnostics["reason"] = "complete-scalar-primary-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        if not has_percentage_authorization(
            ctx.refined_question or ctx.question,
            ctx.sql or "",
            ctx.correction_intent_routing,
        ):
            diagnostics["reason"] = "prior-percentage-authorization-required"
            return ctx
        try:
            plan = plan_occurrence_percentage(
                ctx.sql or "", ctx.db_type, ctx.schema_info
            )
            if plan is None:
                diagnostics["reason"] = "unsupported-shape-or-type"
                return ctx
            routing = await asyncio.to_thread(
                route_occurrence_percentage,
                ctx.refined_question or ctx.question,
                plan,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(**kwargs),
            )
            diagnostics["routing"] = routing
            if not routing["apply"]:
                diagnostics["reason"] = "distinct-or-incompatible-request"
                return ctx
            for sql in (plan.proof_sql, plan.candidate_sql):
                trial = dict(original)
                trial["sql"] = sql
                valid, issues = await self._preflight_correction_candidate(ctx, trial)
                if not valid:
                    diagnostics.update(
                        reason="candidate-validation-failed", issues=issues
                    )
                    return ctx
            bounded = create_occurrence_percentage_probe_executor(
                ctx, self._db_executor
            )
            stats = await asyncio.to_thread(bounded, plan.proof_sql, ctx.target_config)
            proof = (
                prove_occurrence_percentage(result.rows, stats.get("rows", []))
                if stats.get("success")
                else None
            )
            diagnostics.update(
                proof_sql=plan.proof_sql, proof_result=stats, proof=proof
            )
            if proof is None:
                diagnostics["reason"] = "distinct-and-occurrence-counts-not-proven"
                return ctx
            candidate = dict(original)
            candidate["sql"] = plan.candidate_sql
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = plan.candidate_sql
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_occurrence_percentage(proof, after.rows)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted",
                    reason="candidate-disagrees-with-proved-occurrence-counts",
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.occurrence_percentage = diagnostics
        return ctx

    async def _apply_output_completion(self, ctx: Any) -> Any:
        """Add one explicit field only when the complete original result survives."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": OUTPUT_COMPLETION_VERSION, "status": "unchanged"}
        ctx.output_completion = diagnostics
        if (
            result is None
            or result.error
            or result.truncated
            or not 1 <= len(result.rows) <= 10000
        ):
            diagnostics["reason"] = "complete-nonempty-primary-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        try:
            shape = output_shape(ctx.sql or "", ctx.db_type, ctx.schema_info)
            if shape is None:
                diagnostics["reason"] = "unsupported-shape-or-type"
                return ctx
            routing = await asyncio.to_thread(
                route_output_completion,
                ctx.refined_question or ctx.question,
                shape,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(**kwargs),
            )
            diagnostics["routing"] = routing
            plan = plan_output_completion(
                shape, routing["option_index"], routing["insertion_index"]
            )
            if plan is None:
                diagnostics["reason"] = "no-safe-explicit-missing-field"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = plan.candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_output_completion_probe_executor(ctx, self._db_executor)
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics.update(
                candidate_sql=plan.candidate_sql,
                insertion_index=plan.insertion_index,
                selected_column=shape.options[routing["option_index"]],
            )
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_output_completion(plan, result.rows, after.rows)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted", reason="original-result-disagrees"
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.output_completion = diagnostics
        return ctx

    async def _apply_projection_order(self, ctx: Any) -> Any:
        """Permute an explicit output list only when complete rows agree exactly."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": PROJECTION_ORDER_VERSION, "status": "unchanged"}
        ctx.projection_order = diagnostics
        if (
            result is None
            or result.error
            or result.truncated
            or not 1 <= len(result.rows) <= 10_000
        ):
            diagnostics["reason"] = "complete-nonempty-primary-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        if projection_order_shape(ctx.sql or "", ctx.db_type) is None:
            diagnostics["reason"] = "unsupported-shape-or-type"
            return ctx
        try:
            routing = await asyncio.to_thread(
                route_projection_order,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="projection_order_routing", **kwargs
                ),
            )
            diagnostics["routing"] = routing
            plan = plan_projection_order(ctx.sql, ctx.db_type, routing["keep_indices"])
            if plan is None:
                diagnostics["reason"] = "no-safe-requested-permutation"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = plan.candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_projection_order_probe_executor(ctx, self._db_executor)
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics.update(
                candidate_sql=plan.candidate_sql, keep_indices=list(plan.keep_indices)
            )
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_projection_subset(plan, result.rows, after.rows)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted", reason="projected-result-disagrees"
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.projection_order = diagnostics
        return ctx

    async def _apply_name_format(self, ctx: Any) -> Any:
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": NAME_FORMAT_VERSION, "status": "unchanged"}
        ctx.name_format = diagnostics
        if (
            result is None
            or result.error
            or result.truncated
            or not 1 <= len(result.rows) <= 10000
        ):
            diagnostics["reason"] = "complete-nonempty-primary-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        plan = plan_name_format(ctx.sql or "", ctx.db_type)
        if plan is None:
            diagnostics["reason"] = "unsupported-shape-or-type"
            return ctx
        try:
            routing = await asyncio.to_thread(
                route_name_format,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="name_format_routing", **kwargs
                ),
            )
            diagnostics["routing"] = routing
            if not routing["activate"]:
                diagnostics["reason"] = "explicit-format-or-unresolved-content"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = plan.candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_name_format_probe_executor(ctx, self._db_executor)
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = plan.candidate_sql
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_name_format(result.rows, after.rows, plan)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted", reason="complete-name-reconstruction-failed"
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.name_format = diagnostics
        return ctx

    async def _apply_grouped_extremum(self, ctx):
        import asyncio
        from collections import Counter
        from features.ask.engine.ask3.phases.execute import execute_query

        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": GROUPED_EXTREMUM_VERSION, "status": "unchanged"}
        ctx.grouped_extremum = diagnostics
        if (
            result is None
            or result.truncated
            or ctx.provided_context
            or ctx.conversation_context
        ):
            diagnostics["reason"] = "unavailable-result-or-additional-context"
            return ctx
        # A reserved identifier may fail only at the MySQL server. Parser-preserving
        # quoting is allowed with this syntax code, not arbitrary execution failures.
        if result.error:
            if ctx.db_type != "mysql" or not str(result.error).startswith("(1064,"):
                diagnostics["reason"] = "unsupported-primary-error"
                return ctx
        elif len(result.rows) != 1 or len(result.rows[0]) != 1:
            diagnostics["reason"] = "primary-not-one-entity"
            return ctx
        plan = plan_grouped_extremum(ctx.sql or "", ctx.db_type)
        if plan is None:
            diagnostics["reason"] = "unsupported-shape"
            return ctx
        try:
            routing = await asyncio.to_thread(
                route_grouped_extremum,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="grouped_request_routing", **kwargs
                ),
            )
            diagnostics["routing"] = routing
            if not routing["activate"]:
                diagnostics["reason"] = "model-abstained"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = plan.candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_grouped_extremum_probe_executor(ctx, self._db_executor)
            proof = await asyncio.to_thread(bounded, plan.proof_sql, ctx.target_config)
            diagnostics.update(proof_sql=plan.proof_sql, proof=proof)
            if not proof.get("success") or not safe_grouped_extremum_proof(
                proof.get("rows", [])
            ):
                diagnostics["reason"] = "no-bounded-numeric-tie-proof"
                return ctx
            expected = [(row[0],) for row in proof["rows"]]
            if not result.error and tuple(result.rows[0]) not in expected:
                diagnostics["reason"] = "primary-not-in-proven-ties"
                return ctx
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = plan.candidate_sql
            if (
                after is None
                or after.error
                or after.truncated
                or Counter(map(tuple, after.rows)) != Counter(expected)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(status="reverted", reason="candidate-ties-changed")
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.grouped_extremum = diagnostics
        return ctx

    async def _apply_endpoint_component(self, ctx: Any) -> Any:
        """Prove complete mirrored storage before final semantic authorization."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": ENDPOINT_COMPONENT_VERSION, "status": "unchanged"}
        ctx.endpoint_component = diagnostics
        if result is None or result.error or result.truncated:
            diagnostics["reason"] = "primary-result-unavailable"
            return ctx
        if (
            len(result.rows) != 1
            or len(result.rows[0]) != 1
            or type(result.rows[0][0]) is not int
            or result.rows[0][0] != 0
        ):
            diagnostics["reason"] = "primary-not-zero-count"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        try:
            plan = plan_endpoint_component(ctx.sql or "", ctx.db_type, ctx.schema_info)
            if plan is None:
                diagnostics["reason"] = "unsupported-shape-or-type"
                return ctx
            question = ctx.refined_question or ctx.question
            preliminary = await asyncio.to_thread(
                route_endpoint_request_contract,
                question,
                plan,
                self._llm_manager,
                ctx.add_llm_call,
            )
            diagnostics["routing"] = dict(preliminary)
            if not preliminary["activate"]:
                diagnostics["reason"] = "model-abstained"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = plan.candidate_sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_endpoint_component_probe_executor(ctx, self._db_executor)
            stats = await asyncio.to_thread(bounded, plan.proof_sql, ctx.target_config)
            diagnostics.update(proof_sql=plan.proof_sql, proof_rows=stats)
            proof = (
                prove_endpoint_component(plan, result.rows, stats.get("rows", []))
                if stats.get("success") and not stats.get("truncated")
                else None
            )
            diagnostics["proof"] = proof
            if proof is None:
                diagnostics["reason"] = "unsafe-or-unavailable-endpoint-proof"
                return ctx
            facts = verified_storage_facts(plan, result.rows, stats["rows"])
            diagnostics["native_storage_facts"] = facts
            base = await asyncio.to_thread(
                route_endpoint_component,
                question,
                ctx.sql,
                ctx.db_type,
                plan,
                ctx.schema_info,
                facts,
                self._llm_manager,
                ctx.add_llm_call,
            )
            diagnostics["routing"].update(base=base, activate=base["activate"])
            if not base["activate"]:
                diagnostics["reason"] = "proven-storage-semantic-abstention"
                return ctx
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = plan.candidate_sql
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_endpoint_count(proof, after.rows)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(status="reverted", reason="candidate-count-changed")
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.endpoint_component = diagnostics
        return ctx

    async def _apply_month_component(self, ctx: Any) -> Any:
        """Prove one calendar encoding, then preserve the selected period as a month."""
        original = _snapshot_correction_state(ctx)
        result = ctx.execution_result
        diagnostics = {"version": MONTH_COMPONENT_VERSION, "status": "unchanged"}
        ctx.month_component = diagnostics
        if result is None or result.error or result.truncated:
            diagnostics["reason"] = "primary-result-unavailable"
            return ctx
        if ctx.provided_context or ctx.conversation_context:
            diagnostics["reason"] = "additional-context-requires-abstention"
            return ctx
        plan = plan_month_component(ctx.sql or "", ctx.db_type, ctx.schema_info)
        if plan is None:
            diagnostics["reason"] = "unsupported-shape-or-type"
            return ctx
        try:
            routing = await asyncio.to_thread(
                route_month_component,
                ctx.refined_question or ctx.question,
                ctx.sql,
                ctx.db_type,
                self._llm_manager,
                lambda **kwargs: ctx.add_llm_call(
                    phase="month_component_routing", **kwargs
                ),
            )
            diagnostics["routing"] = routing
            if not routing["activate"]:
                diagnostics["reason"] = "model-abstained"
                return ctx
            candidate = _snapshot_correction_state(ctx)
            candidate["sql"] = plan.sql
            valid, issues = await self._preflight_correction_candidate(ctx, candidate)
            if not valid:
                diagnostics.update(reason="candidate-validation-failed", issues=issues)
                return ctx
            bounded = create_month_component_probe_executor(ctx, self._db_executor)
            proof = await asyncio.to_thread(bounded, plan.proof_sql, ctx.target_config)
            diagnostics["proof_sql"] = plan.proof_sql
            diagnostics["proof"] = proof
            if not proof.get("success") or not prove_yearmonth_axis(
                proof.get("rows", []), plan.year
            ):
                diagnostics["reason"] = "unsafe-or-unavailable-proof"
                return ctx
            _restore_correction_state(ctx, candidate)
            ctx = await asyncio.to_thread(execute_query, ctx, _NullPresenter(), bounded)
            after = ctx.execution_result
            diagnostics["candidate_sql"] = plan.sql
            if (
                after is None
                or after.error
                or after.truncated
                or not accepts_month_component(result.rows, after.rows, plan.year)
            ):
                _restore_correction_state(ctx, original)
                diagnostics.update(
                    status="reverted",
                    reason="candidate-does-not-reconstruct-original-periods",
                )
            else:
                diagnostics["status"] = "normalized"
        except Exception as exc:
            _restore_correction_state(ctx, original)
            diagnostics.update(
                status="reverted", reason="repair-failed", error_kind=type(exc).__name__
            )
        ctx.month_component = diagnostics
        return ctx

    async def _apply_post_execution_repairs(self, ctx: Any) -> Any:
        """Try selected repairs that require feedback from the primary result."""
        routing = dict(getattr(ctx, "correction_intent_routing", {}) or {})
        selected = tuple(routing.get("deferred_intents") or ())
        deferred = (
            set(selected) & _DEFERRED_CORRECTION_INTENTS
            if routing.get("activation_allowed") is True
            and routing.get("application_status")
            in {
                "deferred",
                "validated_with_deferred",
                "validated_partial_with_deferred",
            }
            else set()
        )
        if self._correction_intent_routing_shadow or not deferred:
            return ctx
        if len(deferred) != 1:
            routing["post_execution_status"] = "ambiguous_deferred_intents"
            ctx.correction_intent_routing = routing
            return ctx

        repair_intent = next(iter(deferred))
        if repair_intent == "temporal_text_storage":
            if not self._temporal_text_storage_enabled:
                return ctx
            repair_fn = self._temporal_text_storage_fn
            repair_executor = create_value_probe_executor(ctx, self._db_executor)
        else:
            if not self._month_axis_storage_enabled:
                return ctx
            repair_fn = self._month_axis_storage_fn
            repair_executor = create_month_axis_probe_executor(ctx, self._db_executor)

        original_state = _snapshot_correction_state(ctx)
        original_sql = str(ctx.sql or "")
        primary_result = getattr(ctx, "execution_result", None)
        if (
            primary_result is None
            or primary_result.error
            or primary_result.truncated
            or primary_result.row_count != 0
        ):
            setattr(
                ctx,
                repair_intent,
                {
                    "status": "unchanged",
                    "reason": "primary-result-not-empty-and-complete",
                    "probe_count": 0,
                    "candidate_execution_count": 0,
                },
            )
            routing["post_execution_status"] = "not_applicable"
            ctx.correction_intent_routing = routing
            return ctx

        try:
            ctx = await asyncio.to_thread(
                repair_fn,
                ctx,
                repair_executor,
            )
        except Exception as exc:
            _restore_correction_state(ctx, original_state)
            setattr(
                ctx,
                repair_intent,
                {
                    "status": "error",
                    "error_kind": type(exc).__name__,
                    "probe_count": 0,
                    "candidate_execution_count": 0,
                },
            )
            routing["post_execution_status"] = "error"
            ctx.correction_intent_routing = routing
            return ctx

        diagnostics = dict(getattr(ctx, repair_intent))
        candidate_state = _snapshot_correction_state(ctx)
        candidate_sql = str(candidate_state.get("sql") or "")
        if (
            diagnostics.get("status") != "normalized"
            or not candidate_sql
            or _sql_candidates_equivalent(candidate_sql, original_sql, ctx.db_type)
        ):
            _restore_correction_state(ctx, original_state)
            setattr(ctx, repair_intent, diagnostics)
            routing["post_execution_status"] = "not_applicable"
            ctx.correction_intent_routing = routing
            return ctx

        valid, issues = await self._preflight_correction_candidate(
            ctx,
            candidate_state,
        )
        if not valid:
            _restore_correction_state(ctx, original_state)
            diagnostics.update(
                {
                    "status": "reverted",
                    "reason": "candidate-validation-failed",
                    "validation_issue_count": len(issues),
                }
            )
            setattr(ctx, repair_intent, diagnostics)
            routing["post_execution_status"] = "reverted"
            ctx.correction_intent_routing = routing
            return ctx

        _restore_correction_state(ctx, candidate_state)
        ctx.execution_result = None
        ctx = await asyncio.to_thread(
            execute_query,
            ctx,
            _NullPresenter(),
            self._db_executor,
        )
        candidate_result = ctx.execution_result
        diagnostics["candidate_execution_count"] = 1
        if (
            candidate_result is not None
            and not candidate_result.error
            and not candidate_result.truncated
            and candidate_result.row_count > 0
        ):
            diagnostics["status"] = "accepted"
            diagnostics["primary_row_count"] = primary_result.row_count
            diagnostics["candidate_row_count"] = candidate_result.row_count
            setattr(ctx, repair_intent, diagnostics)
            routing.update(
                {
                    "post_execution_status": "accepted",
                    "selected_sql_applied": True,
                    "applied_intents": [
                        *routing.get("applied_intents", []),
                        repair_intent,
                    ],
                    "selected_sql_sha256": hashlib.sha256(
                        str(ctx.sql or "").encode()
                    ).hexdigest(),
                }
            )
            ctx.correction_intent_routing = routing
            return ctx

        _restore_correction_state(ctx, original_state)
        diagnostics.update(
            {
                "status": "reverted",
                "reason": (
                    "candidate-execution-failed"
                    if candidate_result is None or candidate_result.error
                    else "candidate-result-empty-or-truncated"
                ),
            }
        )
        setattr(ctx, repair_intent, diagnostics)
        routing["post_execution_status"] = "reverted"
        ctx.correction_intent_routing = routing
        return ctx

    async def _apply_value_location_normalization(self, ctx: Any) -> Any:
        """Ground one ambiguous sibling-column value with two bounded probes."""
        original_state = _snapshot_correction_state(ctx)
        original_sql = str(ctx.sql or "")
        probe = create_value_probe_executor(ctx, self._db_executor)
        try:
            ctx = await asyncio.to_thread(
                self._value_location_normalization_fn,
                ctx,
                probe,
            )
        except Exception as exc:
            _restore_correction_state(ctx, original_state)
            ctx.value_location_normalization = {
                "status": "error",
                "error_kind": type(exc).__name__,
            }
            return ctx
        diagnostics = dict(ctx.value_location_normalization)
        if diagnostics.get("status") != "normalized":
            return ctx
        candidate_state = _snapshot_correction_state(ctx)
        valid, issues = await self._preflight_correction_candidate(
            ctx,
            candidate_state,
        )
        candidate_sql = str(candidate_state.get("sql") or "")
        if (
            valid
            and candidate_sql
            and not _sql_candidates_equivalent(
                candidate_sql,
                original_sql,
                ctx.db_type,
            )
        ):
            _restore_correction_state(ctx, candidate_state)
            ctx.value_location_normalization = diagnostics
            return ctx
        _restore_correction_state(ctx, original_state)
        diagnostics["status"] = "reverted"
        diagnostics["reason"] = "candidate-validation-failed"
        diagnostics["validation_issue_count"] = len(issues)
        ctx.value_location_normalization = diagnostics
        return ctx

    async def _apply_selected_generation_normalizers(
        self,
        ctx: Any,
        selected_intents: Collection[str],
    ) -> tuple[Any, tuple[str, ...]]:
        """Apply only the deterministic corrections selected by the router."""
        selected = frozenset(selected_intents)
        applied: list[str] = []
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
                before = str(ctx.sql or "")
                ctx = await asyncio.to_thread(normalizer, ctx)
                if not _sql_candidates_equivalent(
                    str(ctx.sql or ""), before, ctx.db_type
                ):
                    applied.extend(sorted(selected.intersection(routed_intents)))
        if (
            self._encoded_identifier_storage_enabled
            and "encoded_identifier_storage" in selected
        ):
            before = str(ctx.sql or "")
            probe = create_value_probe_executor(ctx, self._db_executor)
            ctx = await asyncio.to_thread(
                self._encoded_identifier_storage_fn,
                ctx,
                probe,
            )
            if not _sql_candidates_equivalent(str(ctx.sql or ""), before, ctx.db_type):
                applied.append("encoded_identifier_storage")
        return ctx, tuple(applied)

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
        routed_status_active = (
            routing.get("status") == "activate" and routing.get("verdict") == "activate"
        )
        raw_selected = routing.get("selected_intents")
        if isinstance(raw_selected, (list, tuple)):
            selected_intents = tuple(raw_selected)
        else:
            legacy_selected = routing.get("selected_intent")
            selected_intents = (
                (legacy_selected,)
                if isinstance(legacy_selected, str)
                and legacy_selected in self._correction_intent_routing_intent_scope
                else ()
            )
        selection_allowed = (
            routed_status_active
            and (
                routing.get("activation_allowed") is True
                or self._correction_intent_routing_shadow
            )
            and 0 < len(selected_intents) <= CORRECTION_INTENT_ROUTING_MAX_ACTIVATIONS
            and all(
                intent in self._correction_intent_routing_intent_scope
                for intent in selected_intents
            )
        )
        if not selection_allowed:
            selected_intents = ()
            routing["activation_allowed"] = False
        deferred_intents = tuple(
            intent
            for intent in selected_intents
            if intent in _DEFERRED_CORRECTION_INTENTS
        )
        routing["deferred_intents"] = list(deferred_intents)
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
            (
                candidate_ctx,
                applied_intents,
            ) = await self._apply_selected_generation_normalizers(
                ctx,
                selected_intents,
            )
            generation_intents = tuple(
                intent for intent in selected_intents if intent not in deferred_intents
            )
            unapplied_intents = tuple(
                intent for intent in generation_intents if intent not in applied_intents
            )
            routing.update(
                {
                    "applied_intents": list(applied_intents),
                    "unapplied_intents": list(unapplied_intents),
                }
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
                if deferred_intents:
                    routing.update(
                        {
                            "status": "deferred_until_execution",
                            "activation_allowed": (
                                not self._correction_intent_routing_shadow
                            ),
                            "application_status": (
                                "shadow_deferred"
                                if self._correction_intent_routing_shadow
                                else "deferred"
                            ),
                            "application_issue_count": 0,
                        }
                    )
                    _restore_correction_state(ctx, original_state)
                    ctx.correction_intent_routing = routing
                    return ctx
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
                        "application_status": (
                            "validated_partial_with_deferred"
                            if unapplied_intents and deferred_intents
                            else "validated_with_deferred"
                            if deferred_intents
                            else "validated_partial"
                            if unapplied_intents
                            else "validated"
                        ),
                        "application_issue_count": 0,
                        "candidate_sql_sha256": candidate_hash,
                        "selected_sql_sha256": (
                            original_hash
                            if self._correction_intent_routing_shadow
                            else candidate_hash
                        ),
                        "deferred_intents": list(deferred_intents),
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
        from .sql_validation import validate_resolved_columns_against_schema

        strict_resolution = await asyncio.to_thread(
            validate_resolved_columns_against_schema,
            validation_ctx.sql,
            validation_ctx.get_schema_as_dict(),
            validation_ctx.db_type,
        )
        if not strict_resolution.get("is_valid"):
            return False, [
                str(
                    strict_resolution.get("error_message") or "column resolution failed"
                )
            ]
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
            error_code = result.get("error_code")
            ctx.ambiguity_report = {
                "error": result.get("error", "unknown"),
                "error_code": error_code,
                "fallback": "fail_closed",
            }
            message = (
                str(result.get("error"))
                if error_code
                else "Failed to analyze whether the question requires clarification"
            )
            ctx.mark_error(
                message,
                code=error_code,
                category="rdst-service" if error_code else None,
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

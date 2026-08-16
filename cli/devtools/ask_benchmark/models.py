from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any


class EvaluationTrack(str, Enum):
    MODEL_ONLY = "model-only"
    RDST = "rdst-ask"


class InteractionMode(str, Enum):
    AUTO = "auto"
    INTERACTIVE_NO_ANSWER = "interactive-no-answer"
    NOT_APPLICABLE = "not-applicable"


class ContextMode(str, Enum):
    RAW = "raw"
    AUTO_INIT = "auto-init"
    LLM_ENRICHED = "llm-enriched"
    BIRD_CURATED = "bird-curated"
    SEMANTIC = "semantic"
    EVIDENCE = "evidence"


class Outcome(str, Enum):
    CORRECT = "correct"
    INCORRECT_RESULT = "incorrect_result"
    SCHEMA_LOAD_ERROR = "schema_load_error"
    SCHEMA_FILTER_ERROR = "schema_filter_error"
    CLARIFICATION_REQUIRED = "clarification_required"
    GENERATION_ERROR = "generation_error"
    LOW_CONFIDENCE_REFUSAL = "low_confidence_refusal"
    VALIDATION_ERROR = "validation_error"
    READ_ONLY_REJECTION = "read_only_rejection"
    COLUMN_VALIDATION_REJECTION = "column_validation_rejection"
    EXECUTION_ERROR = "execution_error"
    TIMEOUT = "timeout"
    RESULT_TOO_LARGE = "result_too_large"
    AGENT_ERROR = "agent_error"
    TRANSPORT_ERROR = "transport_error"
    UNCONTROLLED_ROUTE = "uncontrolled_route"
    BUDGET_EXCEEDED = "budget_exceeded"


@dataclass(frozen=True)
class BenchmarkCase:
    question_id: int
    db_id: str
    question: str
    evidence: str
    gold_sql: str
    difficulty: str
    dialect: str


@dataclass(frozen=True)
class Pricing:
    input_usd_per_token: Decimal
    output_usd_per_token: Decimal
    cached_input_usd_per_token: Decimal = Decimal(0)
    reasoning_usd_per_token: Decimal | None = None

    def cold_cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int = 0,
    ) -> Decimal:
        return self.input_usd_per_token * input_tokens + self._output_cost(
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
        )

    def expected_billed_cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int = 0,
        cached_input_tokens: int = 0,
    ) -> Decimal:
        cached_tokens = min(max(cached_input_tokens, 0), max(input_tokens, 0))
        uncached_tokens = max(input_tokens - cached_tokens, 0)
        return (
            self.input_usd_per_token * uncached_tokens
            + self.cached_input_usd_per_token * cached_tokens
            + self._output_cost(
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
            )
        )

    def _output_cost(self, *, output_tokens: int, reasoning_tokens: int) -> Decimal:
        if self.reasoning_usd_per_token is None:
            return self.output_usd_per_token * output_tokens
        visible_output_tokens = max(output_tokens - reasoning_tokens, 0)
        return (
            self.output_usd_per_token * visible_output_tokens
            + self.reasoning_usd_per_token * reasoning_tokens
        )


@dataclass(frozen=True)
class ModelSpec:
    name: str
    model: str
    transport: str
    provider_order: tuple[str, ...]
    pricing: Pricing
    temperature: float | None = 0.0
    max_tokens: int | None = None
    timeout_seconds: float = 300.0
    reasoning_effort: str | None = None
    require_parameters: bool = True
    allow_fallbacks: bool = False
    provider_data_training: bool | None = None
    provider_retains_prompts: bool | None = None
    historical: bool = False

    @property
    def controlled_route(self) -> bool:
        return self.transport != "openrouter" or bool(self.provider_order)


@dataclass(frozen=True)
class RunLimits:
    """Immutable safety limits for a paid benchmark artifact."""

    max_provider_calls: int | None = None
    max_normalized_cost_usd: Decimal | None = None
    max_wall_time_seconds: float | None = None


@dataclass(frozen=True)
class ModelCallRecord:
    stage: str
    transport: str
    requested_model: str
    returned_model: str | None
    upstream_provider: str | None
    request_id: str | None
    prompt_sha256: str
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cached_input_tokens: int
    latency_ms: float
    transport_attempts: int
    actual_cost_usd: Decimal | None
    normalized_cold_cost_usd: Decimal
    expected_billed_cost_usd: Decimal
    response_sha256: str | None = None
    model_name: str = ""
    benchmark_model_name: str = ""
    attempt_id: str | None = None
    requested_settings: dict[str, Any] = field(default_factory=dict)
    effective_settings: dict[str, Any] = field(default_factory=dict)
    configuration_fingerprint: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    error_kind: str | None = None
    route_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()
    execution_time_ms: float = 0.0
    error: str | None = None
    timed_out: bool = False
    result_too_large: bool = False

    @property
    def succeeded(self) -> bool:
        return self.error is None and not self.timed_out and not self.result_too_large


@dataclass(frozen=True)
class OracleScore:
    execution_correct: bool
    soft_f1: float
    multiset_correct: bool
    ordered_correct: bool
    candidate_row_count: int
    gold_row_count: int


@dataclass
class AttemptRecord:
    attempt_key: str
    invocation_id: str
    run_id: str
    question_id: int
    db_id: str
    difficulty: str
    dialect: str
    track: str
    context_mode: str
    interaction_mode: str
    model_name: str
    model: str
    transport: str
    repetition: int
    outcome: str
    scored: bool = True
    configuration_fingerprint: str = ""
    reasoning_effort: str | None = None
    max_tokens: int | None = None
    generated_sql: str | None = None
    validated_sql: str | None = None
    execution_correct: bool = False
    soft_f1: float = 0.0
    multiset_correct: bool = False
    ordered_correct: bool = False
    candidate_row_count: int = 0
    gold_row_count: int = 0
    latency_ms: float = 0.0
    candidate_execution_ms: float = 0.0
    oracle_latency_ms: float = 0.0
    started_at: str | None = None
    completed_at: str | None = None
    actual_cost_usd: Decimal | None = None
    normalized_cold_cost_usd: Decimal = Decimal(0)
    failure_stage: str | None = None
    error: str | None = None
    call_records: list[ModelCallRecord] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["call_records"] = [record.to_dict() for record in self.call_records]
        return _jsonable(data)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value

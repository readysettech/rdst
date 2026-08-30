"""
Ask3Context - Single source of truth for ask3 session.

Replaces the dual state (EngineState + Ask3Session) with a single
typed dataclass that flows through all phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .types import (
    DbType,
    ExecutionResult,
    Interpretation,
    SchemaInfo,
    SchemaSource,
    Status,
    ValidationError,
)


@dataclass
class Ask3Context:
    """
    Single source of truth for ask3 session.

    This context flows through all phases, accumulating data as it goes.
    Each phase reads what it needs and writes its outputs to the context.
    """

    # === Input (set at start) ===
    question: str
    target: str
    db_type: str = DbType.POSTGRESQL
    provided_context: str = ""
    matched_database_values: str = ""

    # === Configuration ===
    max_retries: int = 2
    timeout_seconds: int = 600  # 10 minutes default
    max_rows: int = 100
    verbose: bool = False
    no_interactive: bool = False
    dry_run: bool = False
    enforce_result_limit: bool = True
    allow_agent_escalation: bool = True

    # === Conversation Context (for agent chat mode) ===
    conversation_context: str = ""

    # === Target Config (for database connection) ===
    target_config: Optional[Dict[str, Any]] = None

    # === Schema (Phase 1) ===
    schema_info: Optional[SchemaInfo] = None
    schema_formatted: str = ""
    schema_source: str = SchemaSource.SEMANTIC
    schema_format: str = ""
    schema_format_policy: str = ""
    schema_verbose_chars: int = 0
    schema_compact_chars: int = 0
    schema_compact_savings_ratio: float = 0.0
    # Retained only in memory when the normal 15% policy selected verbose.
    # Generation can retry this complete, lossless form if the provider rejects
    # the verbose request for exceeding its context or request-body limit.
    schema_compact_fallback: str = ""
    schema_context_fallback_used: bool = False
    schema_context_fallback_reason: str = ""
    schema_prompt_utf8_bytes: int = 0

    generation_response: Dict[str, Any] = field(default_factory=dict)
    correction_intent_routing: Dict[str, Any] = field(default_factory=dict)
    dual_candidate_selection: Dict[str, Any] = field(default_factory=dict)
    explicit_ratio_normalization: Dict[str, Any] = field(default_factory=dict)
    scalar_derived_metric_normalization: Dict[str, Any] = field(
        default_factory=dict
    )
    all_rows_aggregate_normalization: Dict[str, Any] = field(default_factory=dict)
    extremum_entity_normalization: Dict[str, Any] = field(default_factory=dict)
    unbounded_categorical_normalization: Dict[str, Any] = field(
        default_factory=dict
    )
    shared_entity_scope_normalization: Dict[str, Any] = field(
        default_factory=dict
    )
    value_location_normalization: Dict[str, Any] = field(default_factory=dict)
    db_probe_diagnostics: Dict[str, Any] = field(default_factory=dict)

    # === Clarification (Phase 2) ===
    interpretations: List[Interpretation] = field(default_factory=list)
    selected_interpretation: Optional[Interpretation] = None
    refined_question: Optional[str] = None
    clarifications: Dict[str, str] = field(default_factory=dict)
    ambiguity_report: Dict[str, Any] = field(default_factory=dict)
    clarification_resolutions: List[Dict[str, Any]] = field(default_factory=list)
    clarification_policy: str = ""
    ambiguity_schema_chars: int = 0
    ambiguity_schema_sha256: str = ""
    ambiguity_response_sha256: str = ""

    # === SQL Generation (Phase 3) ===
    generated_sql: Optional[str] = None
    generation_confidence: Optional[float] = None
    sql: Optional[str] = None
    sql_explanation: Optional[str] = None

    # === Validation (Phase 4) ===
    validation_errors: List[ValidationError] = field(default_factory=list)
    retry_count: int = 0
    limit_added: bool = False  # True when validation injected a missing LIMIT
    limit_reduced: bool = False

    # === Execution (Phase 5) ===
    execution_result: Optional[ExecutionResult] = None

    # === Overall Status ===
    status: str = Status.PENDING
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    error_category: Optional[str] = None
    phase: str = "init"  # Current phase for tracking

    # === LLM Tracking ===
    llm_calls: List[Dict[str, Any]] = field(default_factory=list)
    total_tokens: int = 0
    total_llm_time_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize context to dictionary for saving/logging."""
        return {
            # Input
            "question": self.question,
            "target": self.target,
            "db_type": self.db_type,
            "provided_context": self.provided_context,
            "matched_database_values": self.matched_database_values,
            # Config
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
            "max_rows": self.max_rows,
            "verbose": self.verbose,
            "no_interactive": self.no_interactive,
            "enforce_result_limit": self.enforce_result_limit,
            "allow_agent_escalation": self.allow_agent_escalation,
            # Schema
            "schema_source": self.schema_source,
            "schema_format": self.schema_format,
            "schema_format_policy": self.schema_format_policy,
            "schema_verbose_chars": self.schema_verbose_chars,
            "schema_compact_chars": self.schema_compact_chars,
            "schema_compact_savings_ratio": self.schema_compact_savings_ratio,
            "schema_context_fallback_used": self.schema_context_fallback_used,
            "schema_context_fallback_reason": self.schema_context_fallback_reason,
            "schema_prompt_utf8_bytes": self.schema_prompt_utf8_bytes,
            "schema_formatted_length": len(self.schema_formatted)
            if self.schema_formatted
            else 0,
            # Clarification
            "interpretations": [i.to_dict() for i in self.interpretations],
            "selected_interpretation": self.selected_interpretation.to_dict()
            if self.selected_interpretation
            else None,
            "refined_question": self.refined_question,
            "clarifications": self.clarifications,
            "ambiguity_report": self.ambiguity_report,
            "clarification_resolutions": self.clarification_resolutions,
            "clarification_policy": self.clarification_policy,
            "ambiguity_schema_chars": self.ambiguity_schema_chars,
            "ambiguity_schema_sha256": self.ambiguity_schema_sha256,
            "ambiguity_response_sha256": self.ambiguity_response_sha256,
            # SQL
            "generated_sql": self.generated_sql,
            "generation_confidence": self.generation_confidence,
            "sql": self.sql,
            "sql_explanation": self.sql_explanation,
            "correction_intent_routing": self.correction_intent_routing,
            "dual_candidate_selection": self.dual_candidate_selection,
            "explicit_ratio_normalization": self.explicit_ratio_normalization,
            "scalar_derived_metric_normalization": (
                self.scalar_derived_metric_normalization
            ),
            "all_rows_aggregate_normalization": (
                self.all_rows_aggregate_normalization
            ),
            "extremum_entity_normalization": self.extremum_entity_normalization,
            "unbounded_categorical_normalization": (
                self.unbounded_categorical_normalization
            ),
            "shared_entity_scope_normalization": (
                self.shared_entity_scope_normalization
            ),
            "value_location_normalization": self.value_location_normalization,
            "db_probe_diagnostics": self.db_probe_diagnostics,
            # Validation
            "validation_errors": [e.to_dict() for e in self.validation_errors],
            "retry_count": self.retry_count,
            "limit_added": self.limit_added,
            "limit_reduced": self.limit_reduced,
            # Execution
            "execution_result": self.execution_result.to_dict()
            if self.execution_result
            else None,
            # Status
            "status": self.status,
            "error_message": self.error_message,
            "error_code": self.error_code,
            "error_category": self.error_category,
            "phase": self.phase,
            # LLM tracking
            "total_tokens": self.total_tokens,
            "total_llm_time_ms": self.total_llm_time_ms,
            "llm_call_count": len(self.llm_calls),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Ask3Context:
        """Deserialize context from dictionary."""
        ctx = cls(
            question=data.get("question", ""),
            target=data.get("target", ""),
            db_type=data.get("db_type", DbType.POSTGRESQL),
            provided_context=data.get("provided_context", ""),
            matched_database_values=data.get("matched_database_values", ""),
        )

        # Config
        ctx.max_retries = data.get("max_retries", 2)
        ctx.timeout_seconds = data.get("timeout_seconds", 30)
        ctx.max_rows = data.get("max_rows", 100)
        ctx.verbose = data.get("verbose", False)
        ctx.no_interactive = data.get("no_interactive", False)
        ctx.enforce_result_limit = data.get("enforce_result_limit", True)
        ctx.allow_agent_escalation = data.get("allow_agent_escalation", True)

        # Schema
        ctx.schema_source = data.get("schema_source", SchemaSource.SEMANTIC)
        ctx.schema_format = data.get("schema_format", "")
        ctx.schema_format_policy = data.get("schema_format_policy", "")
        ctx.schema_verbose_chars = data.get("schema_verbose_chars", 0)
        ctx.schema_compact_chars = data.get("schema_compact_chars", 0)
        ctx.schema_compact_savings_ratio = data.get("schema_compact_savings_ratio", 0.0)
        ctx.schema_context_fallback_used = data.get(
            "schema_context_fallback_used", False
        )
        ctx.schema_context_fallback_reason = data.get(
            "schema_context_fallback_reason", ""
        )
        ctx.schema_prompt_utf8_bytes = data.get("schema_prompt_utf8_bytes", 0)

        # Clarification
        ctx.interpretations = [
            Interpretation.from_dict(i) for i in data.get("interpretations", [])
        ]
        if data.get("selected_interpretation"):
            ctx.selected_interpretation = Interpretation.from_dict(
                data["selected_interpretation"]
            )
        ctx.refined_question = data.get("refined_question")
        ctx.clarifications = data.get("clarifications", {})
        ctx.ambiguity_report = data.get("ambiguity_report", {})
        ctx.clarification_resolutions = data.get("clarification_resolutions", [])
        ctx.clarification_policy = data.get("clarification_policy", "")
        ctx.ambiguity_schema_chars = data.get("ambiguity_schema_chars", 0)
        ctx.ambiguity_schema_sha256 = data.get("ambiguity_schema_sha256", "")
        ctx.ambiguity_response_sha256 = data.get("ambiguity_response_sha256", "")

        # SQL
        ctx.generated_sql = data.get("generated_sql")
        ctx.generation_confidence = data.get("generation_confidence")
        ctx.sql = data.get("sql")
        ctx.sql_explanation = data.get("sql_explanation")
        ctx.correction_intent_routing = data.get(
            "correction_intent_routing", {}
        )
        ctx.dual_candidate_selection = data.get("dual_candidate_selection", {})
        ctx.explicit_ratio_normalization = data.get(
            "explicit_ratio_normalization", {}
        )
        ctx.scalar_derived_metric_normalization = data.get(
            "scalar_derived_metric_normalization", {}
        )
        ctx.all_rows_aggregate_normalization = data.get(
            "all_rows_aggregate_normalization", {}
        )
        ctx.extremum_entity_normalization = data.get(
            "extremum_entity_normalization", {}
        )
        ctx.unbounded_categorical_normalization = data.get(
            "unbounded_categorical_normalization", {}
        )
        ctx.shared_entity_scope_normalization = data.get(
            "shared_entity_scope_normalization", {}
        )
        ctx.value_location_normalization = data.get(
            "value_location_normalization", {}
        )
        ctx.db_probe_diagnostics = data.get("db_probe_diagnostics", {})

        # Validation
        ctx.validation_errors = [
            ValidationError.from_dict(e) for e in data.get("validation_errors", [])
        ]
        ctx.retry_count = data.get("retry_count", 0)
        ctx.limit_added = data.get("limit_added", False)
        ctx.limit_reduced = data.get("limit_reduced", False)

        # Status
        ctx.status = data.get("status", Status.PENDING)
        ctx.error_message = data.get("error_message")
        ctx.error_code = data.get("error_code")
        ctx.error_category = data.get("error_category")
        ctx.phase = data.get("phase", "init")

        # LLM tracking
        ctx.total_tokens = data.get("total_tokens", 0)
        ctx.total_llm_time_ms = data.get("total_llm_time_ms", 0.0)

        return ctx

    def mark_error(
        self,
        message: str,
        *,
        code: str | None = None,
        category: str | None = None,
    ) -> None:
        """Mark context as errored with message."""
        self.status = Status.ERROR
        self.error_message = message
        self.error_code = code
        self.error_category = category

    def mark_cancelled(self) -> None:
        """Mark context as cancelled by user."""
        self.status = Status.CANCELLED

    def mark_success(self) -> None:
        """Mark context as successfully completed."""
        self.status = Status.SUCCESS

    def add_llm_call(
        self,
        prompt: str,
        response: str,
        tokens: int,
        latency_ms: float,
        model: str,
        phase: str,
    ) -> None:
        """Track an LLM call for debugging and cost analysis."""
        self.llm_calls.append(
            {
                "prompt_preview": prompt[:200] + "..." if len(prompt) > 200 else prompt,
                "response_preview": response[:200] + "..."
                if len(response) > 200
                else response,
                "tokens": tokens,
                "latency_ms": latency_ms,
                "model": model,
                "phase": phase,
            }
        )
        self.total_tokens += tokens
        self.total_llm_time_ms += latency_ms

    def get_schema_as_dict(self) -> Dict[str, List[str]]:
        """
        Get schema as dict mapping table names to column names.

        Used by column validation phase.
        """
        if not self.schema_info:
            return {}

        return {
            table_name: list(table.columns.keys())
            for table_name, table in self.schema_info.tables.items()
        }

    def clear_validation_errors(self) -> None:
        """Clear validation errors for retry."""
        self.validation_errors = []

    def has_validation_errors(self) -> bool:
        """Check if there are validation errors."""
        return len(self.validation_errors) > 0

    def can_retry(self) -> bool:
        """Check if we can retry SQL generation."""
        return self.retry_count < self.max_retries

    def increment_retry(self) -> None:
        """Increment retry counter."""
        self.retry_count += 1
        self.clear_validation_errors()

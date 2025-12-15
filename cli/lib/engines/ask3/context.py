"""
Ask3Context - Single source of truth for ask3 session.

Replaces the dual state (EngineState + Ask3Session) with a single
typed dataclass that flows through all phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .types import (
    Interpretation,
    ValidationError,
    ExecutionResult,
    SchemaInfo,
    Status,
    DbType,
    SchemaSource,
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

    # === Configuration ===
    max_retries: int = 2
    timeout_seconds: int = 30
    max_rows: int = 100
    verbose: bool = False
    no_interactive: bool = False

    # === Target Config (for database connection) ===
    target_config: Optional[Dict[str, Any]] = None

    # === Schema (Phase 1) ===
    schema_info: Optional[SchemaInfo] = None
    schema_formatted: str = ''
    schema_source: str = SchemaSource.SEMANTIC

    # === Schema Filtering (Phase 1.5) ===
    filtered_tables: List[str] = field(default_factory=list)
    all_available_tables: List[str] = field(default_factory=list)  # Full table list before filtering

    # === Schema Expansion (Phase 3.5) ===
    schema_expansion_count: int = 0
    max_schema_expansions: int = 2  # Hard limit to prevent infinite loops
    generation_response: Dict[str, Any] = field(default_factory=dict)  # Raw LLM response for expansion detection

    # === Clarification (Phase 2) ===
    interpretations: List[Interpretation] = field(default_factory=list)
    selected_interpretation: Optional[Interpretation] = None
    refined_question: Optional[str] = None
    clarifications: Dict[str, str] = field(default_factory=dict)

    # === SQL Generation (Phase 3) ===
    sql: Optional[str] = None
    sql_explanation: Optional[str] = None

    # === Validation (Phase 4) ===
    validation_errors: List[ValidationError] = field(default_factory=list)
    retry_count: int = 0

    # === Execution (Phase 5) ===
    execution_result: Optional[ExecutionResult] = None

    # === Overall Status ===
    status: str = Status.PENDING
    error_message: Optional[str] = None
    phase: str = 'init'  # Current phase for tracking

    # === LLM Tracking ===
    llm_calls: List[Dict[str, Any]] = field(default_factory=list)
    total_tokens: int = 0
    total_llm_time_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize context to dictionary for saving/logging."""
        return {
            # Input
            'question': self.question,
            'target': self.target,
            'db_type': self.db_type,

            # Config
            'max_retries': self.max_retries,
            'timeout_seconds': self.timeout_seconds,
            'max_rows': self.max_rows,
            'verbose': self.verbose,
            'no_interactive': self.no_interactive,

            # Schema
            'schema_source': self.schema_source,
            'schema_formatted_length': len(self.schema_formatted) if self.schema_formatted else 0,
            'filtered_tables': self.filtered_tables,
            'all_available_tables': self.all_available_tables,

            # Schema Expansion
            'schema_expansion_count': self.schema_expansion_count,
            'max_schema_expansions': self.max_schema_expansions,

            # Clarification
            'interpretations': [i.to_dict() for i in self.interpretations],
            'selected_interpretation': self.selected_interpretation.to_dict() if self.selected_interpretation else None,
            'refined_question': self.refined_question,
            'clarifications': self.clarifications,

            # SQL
            'sql': self.sql,
            'sql_explanation': self.sql_explanation,

            # Validation
            'validation_errors': [e.to_dict() for e in self.validation_errors],
            'retry_count': self.retry_count,

            # Execution
            'execution_result': self.execution_result.to_dict() if self.execution_result else None,

            # Status
            'status': self.status,
            'error_message': self.error_message,
            'phase': self.phase,

            # LLM tracking
            'total_tokens': self.total_tokens,
            'total_llm_time_ms': self.total_llm_time_ms,
            'llm_call_count': len(self.llm_calls),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Ask3Context:
        """Deserialize context from dictionary."""
        ctx = cls(
            question=data.get('question', ''),
            target=data.get('target', ''),
            db_type=data.get('db_type', DbType.POSTGRESQL),
        )

        # Config
        ctx.max_retries = data.get('max_retries', 2)
        ctx.timeout_seconds = data.get('timeout_seconds', 30)
        ctx.max_rows = data.get('max_rows', 100)
        ctx.verbose = data.get('verbose', False)
        ctx.no_interactive = data.get('no_interactive', False)

        # Schema
        ctx.schema_source = data.get('schema_source', SchemaSource.SEMANTIC)
        ctx.filtered_tables = data.get('filtered_tables', [])
        ctx.all_available_tables = data.get('all_available_tables', [])

        # Schema Expansion
        ctx.schema_expansion_count = data.get('schema_expansion_count', 0)
        ctx.max_schema_expansions = data.get('max_schema_expansions', 2)

        # Clarification
        ctx.interpretations = [
            Interpretation.from_dict(i) for i in data.get('interpretations', [])
        ]
        if data.get('selected_interpretation'):
            ctx.selected_interpretation = Interpretation.from_dict(data['selected_interpretation'])
        ctx.refined_question = data.get('refined_question')
        ctx.clarifications = data.get('clarifications', {})

        # SQL
        ctx.sql = data.get('sql')
        ctx.sql_explanation = data.get('sql_explanation')

        # Validation
        ctx.validation_errors = [
            ValidationError.from_dict(e) for e in data.get('validation_errors', [])
        ]
        ctx.retry_count = data.get('retry_count', 0)

        # Status
        ctx.status = data.get('status', Status.PENDING)
        ctx.error_message = data.get('error_message')
        ctx.phase = data.get('phase', 'init')

        # LLM tracking
        ctx.total_tokens = data.get('total_tokens', 0)
        ctx.total_llm_time_ms = data.get('total_llm_time_ms', 0.0)

        return ctx

    def mark_error(self, message: str) -> None:
        """Mark context as errored with message."""
        self.status = Status.ERROR
        self.error_message = message

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
        phase: str
    ) -> None:
        """Track an LLM call for debugging and cost analysis."""
        self.llm_calls.append({
            'prompt_preview': prompt[:200] + '...' if len(prompt) > 200 else prompt,
            'response_preview': response[:200] + '...' if len(response) > 200 else response,
            'tokens': tokens,
            'latency_ms': latency_ms,
            'model': model,
            'phase': phase,
        })
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

    def can_expand_schema(self) -> bool:
        """Check if we can attempt another schema expansion."""
        return self.schema_expansion_count < self.max_schema_expansions

    def increment_expansion(self) -> None:
        """Increment schema expansion counter."""
        self.schema_expansion_count += 1

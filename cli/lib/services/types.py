"""Service layer types and dataclasses for RDST analysis."""

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Union


# ============================================================================
# Input Types
# ============================================================================


@dataclass
class AnalyzeInput:
    """Input for analyze service after resolution."""

    sql: str
    normalized_sql: str
    source: str  # "cli_inline", "cli_file", "cli_stdin", "registry", "web"
    hash: Optional[str] = None
    tag: Optional[str] = None
    save_as: Optional[str] = None


@dataclass
class AnalyzeOptions:
    """Options for analyze service execution."""

    target: Optional[str] = None
    fast: bool = False
    readyset_cache: bool = False
    test_rewrites: bool = False
    model: Optional[str] = None


# ============================================================================
# Event Types (Discriminated Union)
# ============================================================================


@dataclass
class ProgressEvent:
    """Progress update during analysis."""

    type: Literal["progress"]
    stage: str
    percent: int
    message: str


@dataclass
class ExplainCompleteEvent:
    """EXPLAIN ANALYZE execution completed."""

    type: Literal["explain_complete"]
    success: bool
    database_engine: str
    execution_time_ms: float
    rows_examined: int
    rows_returned: int
    cost_estimate: float
    explain_plan: Optional[Dict[str, Any]] = None


@dataclass
class RewritesTestedEvent:
    """Query rewrites have been tested."""

    type: Literal["rewrites_tested"]
    tested: bool
    skipped_reason: Optional[str] = None
    message: Optional[str] = None
    original_performance: Optional[Dict[str, Any]] = None
    rewrite_results: Optional[List[Dict[str, Any]]] = None
    best_rewrite: Optional[Dict[str, Any]] = None


@dataclass
class ReadysetCheckedEvent:
    """Readyset cacheability check completed."""

    type: Literal["readyset_checked"]
    checked: bool
    cacheable: Optional[bool] = None
    confidence: Optional[Literal["high", "medium", "low", "unknown"]] = None
    method: Optional[str] = None
    explanation: Optional[str] = None
    issues: Optional[List[str]] = None
    warnings: Optional[List[str]] = None


@dataclass
class CompleteEvent:
    """Analysis completed successfully."""

    type: Literal["complete"]
    success: bool
    analysis_id: Optional[str] = None
    query_hash: Optional[str] = None
    explain_results: Optional[Dict[str, Any]] = None
    llm_analysis: Optional[Dict[str, Any]] = None
    rewrite_testing: Optional[Dict[str, Any]] = None
    readyset_cacheability: Optional[Dict[str, Any]] = None
    formatted: Optional[Dict[str, Any]] = None


@dataclass
class ErrorEvent:
    """Analysis encountered an error."""

    type: Literal["error"]
    message: str
    stage: Optional[str] = None
    partial_results: Optional[Dict[str, Any]] = None


# Union type for all analysis events
AnalyzeEvent = Union[
    ProgressEvent,
    ExplainCompleteEvent,
    RewritesTestedEvent,
    ReadysetCheckedEvent,
    CompleteEvent,
    ErrorEvent,
]


# ============================================================================
# Result Types
# ============================================================================


@dataclass
class AnalyzeResult:
    """Final result from analyze service."""

    success: bool
    analysis_id: Optional[str] = None
    query_hash: Optional[str] = None
    explain_results: Optional[Dict[str, Any]] = None
    llm_analysis: Optional[Dict[str, Any]] = None
    rewrite_testing: Optional[Dict[str, Any]] = None
    readyset_cacheability: Optional[Dict[str, Any]] = None
    formatted: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# ============================================================================
# Init Types
# ============================================================================


@dataclass
class InitStatus:
    """Initialization status summary."""

    initialized: bool
    targets: List[Dict[str, Any]]
    default_target: Optional[str]
    llm_configured: bool


@dataclass
class InitValidationResult:
    """Validation results for targets + LLM."""

    target_results: List[Dict[str, Any]]
    llm_result: Dict[str, Any]


@dataclass
class InitStatusEvent:
    """Streaming status event for init workflows."""

    type: Literal["status"]
    message: str


@dataclass
class InitTargetValidationEvent:
    """Per-target validation result for streaming init validation."""

    type: Literal["target_validation"]
    name: str
    success: bool
    error: Optional[str] = None


@dataclass
class InitLlmValidationEvent:
    """LLM validation result for streaming init validation."""

    type: Literal["llm_validation"]
    result: Dict[str, Any]


@dataclass
class InitCompleteEvent:
    """Init workflow completion event."""

    type: Literal["complete"]
    success: bool
    status: Optional[InitStatus] = None
    validation: Optional[InitValidationResult] = None


@dataclass
class InitErrorEvent:
    """Init workflow error event."""

    type: Literal["error"]
    message: str


InitEvent = Union[
    InitStatusEvent,
    InitTargetValidationEvent,
    InitLlmValidationEvent,
    InitCompleteEvent,
    InitErrorEvent,
]


# ============================================================================
# Query Types - Query Registry and Benchmark
# ============================================================================


@dataclass
class QueryCommandInput:
    """Input for query service command execution."""

    subcommand: str
    kwargs: Dict[str, Any]


@dataclass
class QueryStatusEvent:
    """Status update for query operations."""

    type: Literal["status"]
    message: str


@dataclass
class QueryCompleteEvent:
    """Query operation complete with result payload."""

    type: Literal["complete"]
    success: bool
    result: Dict[str, Any]


@dataclass
class QueryErrorEvent:
    """Query operation failed."""

    type: Literal["error"]
    message: str


@dataclass
class QueryBenchmarkStats:
    """Statistics for a single benchmarked query."""

    query_name: str
    query_hash: str
    executions: int
    successes: int
    failures: int
    min_ms: float
    avg_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    last_error: Optional[str] = None


@dataclass
class QueryBenchmarkProgressEvent:
    """Benchmark progress update."""

    type: Literal["progress", "complete", "error"]
    elapsed_seconds: float
    total_executions: int
    total_successes: int
    total_failures: int
    qps: float
    queries: List[QueryBenchmarkStats]
    error: Optional[str] = None


QueryEvent = Union[QueryStatusEvent, QueryCompleteEvent, QueryErrorEvent]
QueryBenchmarkEvent = QueryBenchmarkProgressEvent


# ============================================================================
# Interactive Types
# ============================================================================


@dataclass
class InteractiveRequest:
    """Request for interactive analysis conversation."""

    query_hash: str
    message: str
    continue_existing: bool = False


@dataclass
class InteractiveResponse:
    """Response from interactive analysis conversation."""

    response_text: str
    conversation_id: str
    error: Optional[str] = None


# ============================================================================
# Interactive Event Types (for streaming)
# ============================================================================


@dataclass
class ChunkEvent:
    """Streaming chunk during message generation."""

    type: Literal["chunk"]
    text: str  # Token/chunk text


@dataclass
class MessageEvent:
    """Complete message (kept for non-streaming fallback)."""

    type: Literal["message"]
    text: str


@dataclass
class InteractiveCompleteEvent:
    """Interactive session complete."""

    type: Literal["complete"]
    conversation_id: str


@dataclass
class InteractiveErrorEvent:
    """Interactive session error."""

    type: Literal["error"]
    error: str


# Union type for all interactive events
InteractiveEvent = Union[
    ChunkEvent, MessageEvent, InteractiveCompleteEvent, InteractiveErrorEvent
]


# ============================================================================
# Ask Types - Unified Streaming Events
# ============================================================================


@dataclass
class AskInput:
    """Input for ask service.

    For resuming after clarification, use service.resume() instead.
    """

    question: str
    target: Optional[str] = None
    source: str = "cli"  # "cli" or "web"


@dataclass
class AskOptions:
    """Options for ask service execution."""

    dry_run: bool = False
    timeout_seconds: int = 30
    verbose: bool = False
    agent_mode: bool = False
    no_interactive: bool = False


# --- Ask Events (yielded during streaming execution) ---


@dataclass
class AskStatusEvent:
    """Status update during ask execution."""

    type: Literal["status"]
    phase: str  # "schema", "filter", "clarify", "generate", "validate", "execute"
    message: str


@dataclass
class AskSchemaLoadedEvent:
    """Schema has been loaded."""

    type: Literal["schema_loaded"]
    source: str  # "semantic" or "introspection"
    table_count: int
    tables: List[str]


@dataclass
class AskInterpretation:
    """A possible interpretation of the user's question."""

    id: int
    description: str
    likelihood: float
    assumptions: List[str]


@dataclass
class AskClarificationQuestion:
    """A clarification question for the user."""

    id: str
    question: str
    options: List[str]


@dataclass
class AskClarificationNeededEvent:
    """Clarification needed from user - execution pauses here.

    For CLI: Display options and prompt user, then resume.
    For Web: Return to client, wait for follow-up request with selection.
    """

    type: Literal["clarification_needed"]
    session_id: str
    interpretations: List[AskInterpretation]
    questions: List[AskClarificationQuestion]


@dataclass
class AskSqlGeneratedEvent:
    """SQL has been generated."""

    type: Literal["sql_generated"]
    sql: str
    explanation: Optional[str] = None


@dataclass
class AskResultEvent:
    """Ask completed with results."""

    type: Literal["result"]
    success: bool
    sql: str
    rows: List[Dict[str, Any]]
    columns: List[str]
    row_count: int
    execution_time_ms: float
    llm_calls: int
    total_tokens: int


@dataclass
class AskErrorEvent:
    """Ask encountered an error."""

    type: Literal["error"]
    message: str
    phase: Optional[str] = None


# Union type for all ask events
AskEvent = Union[
    AskStatusEvent,
    AskSchemaLoadedEvent,
    AskClarificationNeededEvent,
    AskSqlGeneratedEvent,
    AskResultEvent,
    AskErrorEvent,
]


# ============================================================================
# Top Types - Input/Options
# ============================================================================


@dataclass
class TopInput:
    """Input for top service."""

    target: Optional[str] = None
    source: str = "auto"  # auto, pg_stat, activity, digest


@dataclass
class TopOptions:
    """Options for top service execution."""

    limit: int = 10
    sort: str = "total_time"  # total_time, freq, avg_time, load
    filter_pattern: Optional[str] = None
    # Real-time specific
    poll_interval_ms: int = 200
    auto_save_registry: bool = True


# ============================================================================
# Top Event Types
# ============================================================================


@dataclass
class TopStatusEvent:
    """Progress status update."""

    type: Literal["status"]
    message: str


@dataclass
class TopConnectedEvent:
    """Database connection established."""

    type: Literal["connected"]
    target_name: str
    db_engine: str
    source: str


@dataclass
class TopSourceFallbackEvent:
    """Source fallback occurred (e.g., pg_stat → activity)."""

    type: Literal["source_fallback"]
    from_source: str
    to_source: str
    reason: str


@dataclass
class TopQueryData:
    """Individual query data."""

    query_hash: str
    query_text: str
    normalized_query: str
    freq: int
    total_time: str  # formatted "X.XXXs"
    avg_time: str
    pct_load: str
    # Real-time specific (optional)
    max_duration_ms: Optional[float] = None
    current_instances: Optional[int] = None
    observation_count: Optional[int] = None


@dataclass
class TopQueriesEvent:
    """Batch of top queries (historical snapshot or real-time update)."""

    type: Literal["queries"]
    queries: List[TopQueryData]
    source: str
    target_name: str
    db_engine: str
    # Real-time specific
    runtime_seconds: Optional[float] = None
    total_tracked: Optional[int] = None


@dataclass
class TopQuerySavedEvent:
    """Query saved to registry."""

    type: Literal["query_saved"]
    query_hash: str
    is_new: bool


@dataclass
class TopCompleteEvent:
    """Operation complete (for historical one-shot)."""

    type: Literal["complete"]
    success: bool
    queries: List[TopQueryData]
    source: str
    newly_saved: int


@dataclass
class TopErrorEvent:
    """Error occurred."""

    type: Literal["error"]
    message: str
    stage: Optional[str] = None


# Union type for all top events
TopEvent = Union[
    TopStatusEvent,
    TopConnectedEvent,
    TopSourceFallbackEvent,
    TopQueriesEvent,
    TopQuerySavedEvent,
    TopCompleteEvent,
    TopErrorEvent,
]


# ============================================================================
# Configure Types - Input/Options
# ============================================================================


@dataclass
class ConfigureInput:
    """Input for configure service."""

    target_name: Optional[str] = None  # None for list operations


@dataclass
class ConfigureOptions:
    """Options for configure service execution."""

    operation: str = "list"  # add, edit, remove, test, list
    target_data: Optional[Dict[str, Any]] = None  # Connection details


# ============================================================================
# Configure Event Types
# ============================================================================


@dataclass
class ConfigureStatusEvent:
    """Progress status update."""

    type: Literal["status"]
    message: str


@dataclass
class ConfigureTargetListEvent:
    """List of configured targets."""

    type: Literal["target_list"]
    targets: List[Dict[str, Any]]  # name, engine, has_password, is_default
    default_target: Optional[str] = None


@dataclass
class ConfigureTargetDetailEvent:
    """Details of a single target."""

    type: Literal["target_detail"]
    target_name: str
    engine: str
    host: str
    port: int
    database: str
    user: str
    has_password: bool
    is_default: bool
    tls: bool = False
    read_only: bool = False


@dataclass
class ConfigureConnectionTestEvent:
    """Connection test in progress or result."""

    type: Literal["connection_test"]
    target_name: str
    status: Literal["in_progress", "success", "failed"]
    message: Optional[str] = None
    server_version: Optional[str] = None


@dataclass
class ConfigureSuccessEvent:
    """Configure operation completed successfully."""

    type: Literal["success"]
    operation: str  # add, edit, remove, test
    target_name: Optional[str] = None
    message: Optional[str] = None


@dataclass
class ConfigureErrorEvent:
    """Configure operation encountered an error."""

    type: Literal["error"]
    message: str
    operation: Optional[str] = None
    target_name: Optional[str] = None


@dataclass
class ConfigureInputNeededEvent:
    """User input needed for interactive prompts (CLI only)."""

    type: Literal["input_needed"]
    prompt: str
    field_name: str
    field_type: str  # "text", "password", "number", "choice"
    choices: Optional[List[str]] = None
    default: Optional[str] = None


# Union type for all configure events
ConfigureEvent = Union[
    ConfigureStatusEvent,
    ConfigureTargetListEvent,
    ConfigureTargetDetailEvent,
    ConfigureConnectionTestEvent,
    ConfigureSuccessEvent,
    ConfigureErrorEvent,
    ConfigureInputNeededEvent,
]


# ============================================================================
# Schema Types - Semantic Layer Management
# ============================================================================


@dataclass
class SchemaStatus:
    """Semantic layer status for a target."""

    target: str
    exists: bool
    tables: int
    columns: int
    relationships: int
    terminology: int
    updated_at: Optional[str] = None


@dataclass
class SchemaTableColumn:
    """Column annotation details."""

    name: str
    data_type: Optional[str]
    description: Optional[str]
    unit: Optional[str]
    is_pii: bool
    enum_values: Optional[Dict[str, str]]
    value_pattern: Optional[str] = None


@dataclass
class SchemaTableRelationship:
    """Table relationship."""

    target_table: str
    relationship_type: str
    join_pattern: str


@dataclass
class SchemaTable:
    """Table annotation details."""

    name: str
    description: Optional[str]
    business_context: Optional[str]
    row_estimate: Optional[str]
    columns: List["SchemaTableColumn"]
    relationships: List["SchemaTableRelationship"]


@dataclass
class SchemaTerminology:
    """Business terminology entry."""

    term: str
    definition: str
    sql_pattern: str
    synonyms: List[str]


@dataclass
class SchemaMetric:
    """Business metric definition."""

    name: str
    definition: str
    sql: str


@dataclass
class SchemaExtension:
    """Database extension info."""

    name: str
    version: str
    description: Optional[str]
    types_provided: List[str]


@dataclass
class SchemaCustomType:
    """Custom database type."""

    name: str
    type_category: str  # "enum", "domain", "base"
    base_type: Optional[str]
    enum_values: Optional[List[str]]
    description: Optional[str]


@dataclass
class SchemaDetails:
    """Full semantic layer details."""

    target: str
    tables: List[SchemaTable]
    terminology: List[SchemaTerminology]
    extensions: List[SchemaExtension]
    custom_types: List[SchemaCustomType]
    metrics: List[SchemaMetric]


@dataclass
class SchemaTargetSummary:
    """Summary for a target's semantic layer."""

    name: str
    tables: int
    terminology: int
    updated_at: Optional[str]


@dataclass
class SchemaTargetList:
    """List of targets with semantic layers."""

    targets: List[SchemaTargetSummary]


@dataclass
class SchemaInitOptions:
    """Options for schema init operation."""

    enum_threshold: int = 20
    force: bool = False
    sample_enums: bool = True


@dataclass
class SchemaInitResult:
    """Result of schema init operation."""

    success: bool
    target: str
    tables: int
    columns: int
    relationships: int
    enum_columns: List[str]
    path: Optional[str] = None
    error: Optional[str] = None


@dataclass
class SchemaExportResult:
    """Result of schema export."""

    success: bool
    format: str
    content: str
    error: Optional[str] = None


@dataclass
class SchemaDeleteResult:
    """Result of schema delete."""

    success: bool
    target: str
    error: Optional[str] = None


@dataclass
class SchemaUpdateResult:
    """Result of schema update operations (add-table, add-term)."""

    success: bool
    message: str
    error: Optional[str] = None


@dataclass
class SchemaStatusEvent:
    """Status update for schema operations."""

    type: Literal["status"]
    operation: str
    message: str


@dataclass
class SchemaCompleteEvent:
    """Schema operation completion event with typed payload."""

    type: Literal["complete"]
    operation: str
    success: bool
    status: Optional[SchemaStatus] = None
    details: Optional[SchemaDetails] = None
    target_list: Optional[SchemaTargetList] = None
    init_result: Optional[SchemaInitResult] = None
    export_result: Optional[SchemaExportResult] = None
    delete_result: Optional[SchemaDeleteResult] = None
    update_result: Optional[SchemaUpdateResult] = None


@dataclass
class SchemaErrorEvent:
    """Schema operation error event."""

    type: Literal["error"]
    operation: str
    message: str


SchemaEvent = Union[SchemaStatusEvent, SchemaCompleteEvent, SchemaErrorEvent]


# ============================================================================
# Annotate Event Types - LLM Schema Annotation
# ============================================================================


@dataclass
class AnnotateStartedEvent:
    """Annotation process started."""

    type: Literal["annotate_started"]
    tables: int
    message: str


@dataclass
class AnnotateProgressEvent:
    """Progress update during annotation."""

    type: Literal["annotate_progress"]
    table: str
    table_index: int
    total_tables: int
    message: str


@dataclass
class AnnotateTableCompleteEvent:
    """A table has been annotated."""

    type: Literal["annotate_table_complete"]
    table: str
    table_index: int
    total_tables: int
    columns_annotated: int


@dataclass
class AnnotateCompleteEvent:
    """Annotation process completed successfully."""

    type: Literal["annotate_complete"]
    success: bool
    tables_annotated: int
    columns_annotated: int
    message: str


@dataclass
class AnnotateErrorEvent:
    """Annotation process encountered an error."""

    type: Literal["annotate_error"]
    message: str


# Union type for all annotate events
AnnotateEvent = Union[
    AnnotateStartedEvent,
    AnnotateProgressEvent,
    AnnotateTableCompleteEvent,
    AnnotateCompleteEvent,
    AnnotateErrorEvent,
]


# ============================================================================
# Trial Types
# ============================================================================


@dataclass
class TrialRegisterResult:
    """Result from trial registration attempt."""

    success: bool
    limit_display: Optional[str] = None
    email_tier: Optional[str] = None
    error_code: Optional[str] = None
    detail: Optional[str] = None
    did_you_mean: Optional[str] = None
    status_code: int = 200


@dataclass
class TrialActivateResult:
    """Result from trial token activation."""

    success: bool
    message: Optional[str] = None


@dataclass
class TrialStatusResult:
    """Current trial status and balance."""

    active: bool
    email: Optional[str] = None
    status: Optional[str] = None
    remaining_cents: Optional[int] = None
    limit_cents: Optional[int] = None
    remaining_tokens_display: Optional[str] = None
    limit_tokens_display: Optional[str] = None
    percent_remaining: Optional[int] = None

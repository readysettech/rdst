"""Fleet and audit data models."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class SizingVerdict(str, Enum):
    UNDER_PROVISIONED = "under_provisioned"
    OVERSIZED = "oversized"
    RIGHT_SIZED = "right_sized"
    UNKNOWN = "unknown"


class TargetType(str, Enum):
    DATABASE = "database"
    READYSET = "readyset"
    READ_REPLICA = "read_replica"


# ============================================================================
# Fleet Models
# ============================================================================


@dataclass
class FleetMember:
    """A database instance in the fleet. Maps to a target in config.toml."""

    name: str
    engine: str  # "postgresql" or "mysql"
    host: str
    port: int
    database: str
    user: str
    password_env: str
    # Fleet-specific fields
    group: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    instance_class: Optional[str] = None  # e.g., "db.r6g.xlarge"
    region: Optional[str] = None  # e.g., "us-east-1"
    target_type: str = "database"
    primary_target: Optional[str] = None  # For read replicas
    tls: bool = False
    read_only: bool = False
    password_secret_arn: Optional[str] = None

    def to_target_config(self) -> Dict[str, Any]:
        """Convert to TargetsConfig-compatible dict for storage in config.toml."""
        d: Dict[str, Any] = {
            "engine": self.engine,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "password_env": self.password_env,
            "tls": self.tls,
            "read_only": self.read_only,
        }
        if self.target_type != "database":
            d["target_type"] = self.target_type
        if self.group:
            d["group"] = self.group
        if self.tags:
            d["tags"] = self.tags
        if self.instance_class:
            d["instance_class"] = self.instance_class
        if self.region:
            d["region"] = self.region
        if self.primary_target:
            d["primary_target"] = self.primary_target
        if self.password_secret_arn:
            d["password_secret_arn"] = self.password_secret_arn
        return d


# ============================================================================
# Audit Models
# ============================================================================


@dataclass
class AuditMetrics:
    """Raw metrics collected from a single database via SQL queries."""

    # Connection utilization
    max_connections: int = 0
    active_connections: int = 0
    idle_connections: int = 0
    connection_utilization_pct: float = 0.0
    # Cache performance
    cache_hit_rate: float = 0.0
    # Working set
    shared_buffers_mb: float = 0.0
    working_set_mb: float = 0.0
    # Read/Write ratio
    read_pct: float = 0.0
    write_pct: float = 0.0
    # Replication
    is_replica: bool = False
    replication_lag_seconds: Optional[float] = None
    # Size
    database_size_mb: float = 0.0
    # Server info
    server_version: str = ""
    # Query stats (from pg_stat_statements / performance_schema)
    tracked_query_count: int = 0
    total_query_time_ms: float = 0.0
    # Server uptime and stats window (for QPS computation)
    uptime_seconds: float = 0.0
    stats_reset_at: Optional[str] = None
    stats_window_seconds: float = 0.0
    # Storage (from AWS API if available)
    storage_allocated_gb: Optional[float] = None
    storage_used_pct: Optional[float] = None
    storage_type: Optional[str] = None
    # Timestamp
    collected_at: Optional[str] = None


@dataclass
class CacheOpportunityScore:
    """ReadySet cache opportunity assessment (0-100)."""

    score: int = 0
    factors: Dict[str, float] = field(default_factory=dict)
    level: str = "low"  # "high" (>=70), "medium" (40-69), "low" (<40)
    explanation: str = ""


@dataclass
class SizingAssessment:
    """Instance sizing assessment with optional cost data."""

    verdict: SizingVerdict = SizingVerdict.UNKNOWN
    explanation: str = ""
    # Pricing (populated when instance_class is known)
    current_monthly_cost_usd: Optional[float] = None
    suggested_instance_class: Optional[str] = None
    suggested_monthly_cost_usd: Optional[float] = None
    potential_savings_usd: Optional[float] = None


@dataclass
class AuditResult:
    """Complete audit result for a single target."""

    target_name: str
    engine: str
    host: str
    region: Optional[str] = None
    instance_class: Optional[str] = None
    group: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metrics: Optional[AuditMetrics] = None
    sizing: Optional[SizingAssessment] = None
    cache_opportunity: Optional[CacheOpportunityScore] = None
    top_queries: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    audited_at: Optional[str] = None


@dataclass
class FleetAuditSnapshot:
    """A complete fleet audit snapshot for persistence and diffing."""

    snapshot_id: str
    name: Optional[str] = None
    created_at: str = ""
    targets_audited: int = 0
    targets_failed: int = 0
    results: List[AuditResult] = field(default_factory=list)
    # Summary stats
    total_monthly_cost_usd: Optional[float] = None
    potential_savings_usd: Optional[float] = None
    avg_cache_opportunity: Optional[float] = None


@dataclass
class FleetDiffEntry:
    """Diff entry for one target between two snapshots."""

    target_name: str
    field_name: str
    old_value: Any = None
    new_value: Any = None
    change_pct: Optional[float] = None


@dataclass
class FleetDiff:
    """Diff between two fleet audit snapshots."""

    baseline_id: str
    current_id: str
    baseline_date: str = ""
    current_date: str = ""
    entries: List[FleetDiffEntry] = field(default_factory=list)
    new_targets: List[str] = field(default_factory=list)
    removed_targets: List[str] = field(default_factory=list)


# ============================================================================
# Workload Models
# ============================================================================


@dataclass
class DatabaseSnapshot:
    """Full point-in-time database-level statistics."""

    timestamp: str  # ISO 8601
    engine: str = ""
    # PostgreSQL: pg_stat_database
    xact_commit: Optional[int] = None
    xact_rollback: Optional[int] = None
    blks_read: Optional[int] = None
    blks_hit: Optional[int] = None
    tup_returned: Optional[int] = None
    tup_fetched: Optional[int] = None
    tup_inserted: Optional[int] = None
    tup_updated: Optional[int] = None
    tup_deleted: Optional[int] = None
    temp_files: Optional[int] = None
    temp_bytes: Optional[int] = None
    deadlocks: Optional[int] = None
    # Connection counts
    active_connections: Optional[int] = None
    idle_connections: Optional[int] = None
    idle_in_transaction: Optional[int] = None
    # MySQL equivalents (via SHOW GLOBAL STATUS)
    com_select: Optional[int] = None
    com_insert: Optional[int] = None
    com_update: Optional[int] = None
    com_delete: Optional[int] = None
    innodb_buffer_pool_reads: Optional[int] = None
    innodb_buffer_pool_read_requests: Optional[int] = None
    innodb_row_lock_waits: Optional[int] = None
    threads_connected: Optional[int] = None
    threads_running: Optional[int] = None
    # Derived
    cache_hit_ratio: Optional[float] = None
    database_size_mb: Optional[float] = None
    server_version: Optional[str] = None
    # Raw dict for engine-specific extras
    raw: Optional[Dict[str, Any]] = None


@dataclass
class IntermediateSnapshot:
    """Lightweight periodic snapshot during workload capture (every 30s)."""

    timestamp: str  # ISO 8601
    elapsed_seconds: float
    active_connections: int = 0
    idle_connections: int = 0
    idle_in_transaction: int = 0
    cache_hit_ratio: float = 0.0
    transactions_per_sec: float = 0.0  # Delta since last snapshot
    lock_wait_count: int = 0
    temp_bytes_delta: int = 0  # Temp file usage since last snapshot
    deadlock_count_delta: int = 0
    # Active query sampling from pg_stat_activity / SHOW PROCESSLIST
    active_queries: int = 0  # Number of currently running queries
    longest_query_ms: float = 0.0  # Duration of longest running query
    waiting_on_lock: int = 0  # Queries waiting on locks
    # State distribution
    state_distribution: Optional[Dict[str, int]] = None


@dataclass
class TableStats:
    """Per-table statistics from pg_stat_user_tables / information_schema."""

    table_name: str
    schema_name: str = "public"
    seq_scan: Optional[int] = None
    idx_scan: Optional[int] = None
    n_tup_ins: Optional[int] = None
    n_tup_upd: Optional[int] = None
    n_tup_del: Optional[int] = None
    n_live_tup: Optional[int] = None
    n_dead_tup: Optional[int] = None
    last_vacuum: Optional[str] = None
    last_analyze: Optional[str] = None


@dataclass
class WorkloadQuery:
    """A single query captured in the workload."""

    query_hash: str
    query_text: str
    normalized_query: str
    calls: int  # Total executions during window
    total_time_ms: float  # Total execution time
    avg_time_ms: float
    min_time_ms: Optional[float] = None
    max_time_ms: Optional[float] = None
    rows_returned: Optional[int] = None
    shared_blks_hit: Optional[int] = None  # PG: buffer cache hits
    shared_blks_read: Optional[int] = None  # PG: disk reads
    pct_total_time: float = 0.0  # % of total workload time
    source: str = "pg_stat"  # Where this query came from


@dataclass
class WorkloadAnalysis:
    """LLM-generated holistic workload analysis."""

    model_used: str = ""
    workload_characterization: str = ""
    health_score: int = 0  # 1-100
    read_write_ratio: str = ""
    top_bottlenecks: List[Dict[str, Any]] = field(default_factory=list)
    index_recommendations: List[Dict[str, Any]] = field(default_factory=list)
    caching_candidates: List[Dict[str, Any]] = field(default_factory=list)
    capacity_insights: List[str] = field(default_factory=list)
    optimization_priorities: List[Dict[str, Any]] = field(default_factory=list)
    raw_response: str = ""
    schema_context: str = ""


@dataclass
class WorkloadRun:
    """Complete workload run — the storage unit."""

    run_id: str
    target_name: str
    db_engine: str
    started_at: str  # ISO 8601
    ended_at: str
    duration_seconds: int
    source: str  # "live", "snapshot", "import"
    # Before/after snapshots
    snapshot_start: Optional[DatabaseSnapshot] = None
    snapshot_end: Optional[DatabaseSnapshot] = None
    # Time series of intermediate snapshots
    intermediate_snapshots: List[IntermediateSnapshot] = field(default_factory=list)
    # Delta stats (computed)
    delta_stats: Dict[str, Any] = field(default_factory=dict)
    # Table-level stats
    table_stats_start: List[TableStats] = field(default_factory=list)
    table_stats_end: List[TableStats] = field(default_factory=list)
    # Captured queries
    queries: List[WorkloadQuery] = field(default_factory=list)
    total_queries: int = 0
    total_query_time_ms: float = 0.0
    # Analysis
    analysis: Optional[WorkloadAnalysis] = None
    # Metadata
    version: str = "1.0"


@dataclass
class WorkloadDiff:
    """Comparison between two workload runs."""

    run_a_id: str
    run_b_id: str
    query_changes: Dict[str, Any] = field(default_factory=dict)
    performance_changes: Dict[str, Any] = field(default_factory=dict)
    stat_deltas: Dict[str, Any] = field(default_factory=dict)

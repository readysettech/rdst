"""Audit and workload models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from features.fleet.models import SizingVerdict


@dataclass
class AuditMetrics:
    max_connections: int = 0
    active_connections: int = 0
    idle_connections: int = 0
    connection_utilization_pct: float = 0.0
    cache_hit_rate: float = 0.0
    shared_buffers_mb: float = 0.0
    working_set_mb: float = 0.0
    read_pct: float = 0.0
    write_pct: float = 0.0
    is_replica: bool = False
    replication_lag_seconds: float | None = None
    database_size_mb: float = 0.0
    server_version: str = ""
    tracked_query_count: int = 0
    total_query_time_ms: float = 0.0
    uptime_seconds: float = 0.0
    stats_reset_at: str | None = None
    stats_window_seconds: float = 0.0
    storage_allocated_gb: float | None = None
    storage_used_pct: float | None = None
    storage_type: str | None = None
    collected_at: str | None = None


@dataclass
class CacheOpportunityScore:
    score: int = 0
    factors: dict[str, float] = field(default_factory=dict)
    level: str = "low"
    explanation: str = ""


@dataclass
class SizingAssessment:
    verdict: SizingVerdict = SizingVerdict.UNKNOWN
    explanation: str = ""
    current_monthly_cost_usd: float | None = None
    suggested_instance_class: str | None = None
    suggested_monthly_cost_usd: float | None = None
    potential_savings_usd: float | None = None


@dataclass
class AuditResult:
    target_name: str
    engine: str
    host: str
    region: str | None = None
    instance_class: str | None = None
    group: str | None = None
    tags: list[str] = field(default_factory=list)
    metrics: AuditMetrics | None = None
    sizing: SizingAssessment | None = None
    cache_opportunity: CacheOpportunityScore | None = None
    top_queries: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    audited_at: str | None = None


@dataclass
class DatabaseSnapshot:
    timestamp: str
    engine: str = ""
    xact_commit: int | None = None
    xact_rollback: int | None = None
    blks_read: int | None = None
    blks_hit: int | None = None
    tup_returned: int | None = None
    tup_fetched: int | None = None
    tup_inserted: int | None = None
    tup_updated: int | None = None
    tup_deleted: int | None = None
    temp_files: int | None = None
    temp_bytes: int | None = None
    deadlocks: int | None = None
    active_connections: int | None = None
    idle_connections: int | None = None
    idle_in_transaction: int | None = None
    com_select: int | None = None
    com_insert: int | None = None
    com_update: int | None = None
    com_delete: int | None = None
    innodb_buffer_pool_reads: int | None = None
    innodb_buffer_pool_read_requests: int | None = None
    innodb_row_lock_waits: int | None = None
    threads_connected: int | None = None
    threads_running: int | None = None
    cache_hit_ratio: float | None = None
    database_size_mb: float | None = None
    server_version: str | None = None
    raw: dict[str, Any] | None = None


@dataclass
class IntermediateSnapshot:
    timestamp: str
    elapsed_seconds: float
    active_connections: int = 0
    idle_connections: int = 0
    idle_in_transaction: int = 0
    cache_hit_ratio: float = 0.0
    transactions_per_sec: float = 0.0
    lock_wait_count: int = 0
    temp_bytes_delta: int = 0
    deadlock_count_delta: int = 0
    active_queries: int = 0
    longest_query_ms: float = 0.0
    waiting_on_lock: int = 0
    state_distribution: dict[str, int] | None = None


@dataclass
class TableStats:
    table_name: str
    schema_name: str = "public"
    seq_scan: int | None = None
    idx_scan: int | None = None
    n_tup_ins: int | None = None
    n_tup_upd: int | None = None
    n_tup_del: int | None = None
    n_live_tup: int | None = None
    n_dead_tup: int | None = None
    last_vacuum: str | None = None
    last_analyze: str | None = None


@dataclass
class WorkloadQuery:
    query_hash: str
    query_text: str
    normalized_query: str
    calls: int
    total_time_ms: float
    avg_time_ms: float
    min_time_ms: float | None = None
    max_time_ms: float | None = None
    rows_returned: int | None = None
    shared_blks_hit: int | None = None
    shared_blks_read: int | None = None
    pct_total_time: float = 0.0
    source: str = "pg_stat"


@dataclass
class WorkloadAnalysis:
    model_used: str = ""
    workload_characterization: str = ""
    health_score: int = 0
    read_write_ratio: str = ""
    top_bottlenecks: list[dict[str, Any]] = field(default_factory=list)
    index_recommendations: list[dict[str, Any]] = field(default_factory=list)
    caching_candidates: list[dict[str, Any]] = field(default_factory=list)
    capacity_insights: list[str] = field(default_factory=list)
    optimization_priorities: list[dict[str, Any]] = field(default_factory=list)
    raw_response: str = ""
    schema_context: str = ""


@dataclass
class WorkloadRun:
    run_id: str
    target_name: str
    db_engine: str
    started_at: str
    ended_at: str
    duration_seconds: int
    source: str
    snapshot_start: DatabaseSnapshot | None = None
    snapshot_end: DatabaseSnapshot | None = None
    intermediate_snapshots: list[IntermediateSnapshot] = field(default_factory=list)
    delta_stats: dict[str, Any] = field(default_factory=dict)
    table_stats_start: list[TableStats] = field(default_factory=list)
    table_stats_end: list[TableStats] = field(default_factory=list)
    queries: list[WorkloadQuery] = field(default_factory=list)
    total_queries: int = 0
    total_query_time_ms: float = 0.0
    analysis: WorkloadAnalysis | None = None
    version: str = "1.0"


@dataclass
class WorkloadDiff:
    run_a_id: str
    run_b_id: str
    query_changes: dict[str, Any] = field(default_factory=dict)
    performance_changes: dict[str, Any] = field(default_factory=dict)
    stat_deltas: dict[str, Any] = field(default_factory=dict)

"""Audit and workload service contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Union


@dataclass
class AuditInput:
    target: str | None = None
    targets: list[str] | None = None


@dataclass
class AuditOptions:
    save_name: str | None = None
    diff_baseline: str | None = None
    output_json: bool = False
    insights: bool = False


@dataclass
class AuditStatusEvent:
    type: Literal["status"]
    phase: str
    message: str
    # Fleet audit status can be scoped to one target. These fields are
    # optional so existing single-target/global status consumers keep the
    # exact same contract.
    target_name: str | None = None
    elapsed_seconds: float | None = None
    total_seconds: float | None = None
    step: str | None = None


@dataclass
class AuditTargetStartEvent:
    type: Literal["target_start"]
    target_name: str
    index: int
    total: int


@dataclass
class AuditMetricsCollectedEvent:
    type: Literal["metrics_collected"]
    target_name: str
    metrics: dict[str, Any]


@dataclass
class AuditTargetCompleteEvent:
    type: Literal["target_complete"]
    target_name: str
    result: dict[str, Any]
    index: int
    total: int


@dataclass
class AuditTargetErrorEvent:
    type: Literal["target_error"]
    target_name: str
    error: str
    index: int
    total: int


@dataclass
class AuditLlmInsightsEvent:
    type: Literal["llm_insights"]
    insights: str


@dataclass
class AuditSnapshotSavedEvent:
    type: Literal["snapshot_saved"]
    snapshot_id: str
    name: str | None = None
    path: str | None = None


@dataclass
class AuditDiffEvent:
    type: Literal["diff"]
    diff: dict[str, Any]


@dataclass
class AuditCompleteEvent:
    type: Literal["complete"]
    success: bool
    snapshot_id: str | None = None
    summary: dict[str, Any] | None = None


@dataclass
class AuditErrorEvent:
    type: Literal["error"]
    message: str
    phase: str | None = None


AuditEvent = Union[
    AuditStatusEvent,
    AuditTargetStartEvent,
    AuditMetricsCollectedEvent,
    AuditTargetCompleteEvent,
    AuditTargetErrorEvent,
    AuditLlmInsightsEvent,
    AuditSnapshotSavedEvent,
    AuditDiffEvent,
    AuditCompleteEvent,
    AuditErrorEvent,
]


@dataclass
class WorkloadInput:
    target: str | None = None
    source: str = "auto"
    file_path: str | None = None
    subcommand: str = "run"
    run_id: str | None = None
    run_a: str | None = None
    run_b: str | None = None


@dataclass
class WorkloadOptions:
    duration_seconds: int | None = None
    snapshot_only: bool = False
    analyze: bool = True
    model: str | None = None
    limit: int = 50
    save_top_queries: int | None = None
    output_json: bool = False
    poll_interval_ms: int = 2000


@dataclass
class WorkloadStatusEvent:
    type: Literal["status"]
    phase: str
    message: str


@dataclass
class WorkloadConnectedEvent:
    type: Literal["connected"]
    target_name: str
    db_engine: str
    source: str


@dataclass
class WorkloadSnapshotEvent:
    type: Literal["snapshot"]
    when: str
    cache_hit_ratio: float | None = None
    active_connections: int = 0
    stats_summary: dict[str, Any] | None = None


@dataclass
class WorkloadCaptureProgressEvent:
    type: Literal["capture_progress"]
    elapsed_seconds: float
    total_seconds: float | None = None
    unique_queries: int = 0
    total_executions: int = 0
    cache_hit_ratio: float | None = None
    active_connections: int = 0
    tps: float = 0.0


@dataclass
class WorkloadCaptureCompleteEvent:
    type: Literal["capture_complete"]
    unique_queries: int
    total_executions: int
    total_query_time_ms: float
    duration_seconds: float


@dataclass
class WorkloadAnalysisProgressEvent:
    type: Literal["analysis_progress"]
    message: str
    percent: int


@dataclass
class WorkloadQueriesSavedEvent:
    type: Literal["queries_saved"]
    count: int
    hashes: list[str]


@dataclass
class WorkloadCompleteEvent:
    type: Literal["complete"]
    success: bool
    run_id: str
    summary: dict[str, Any] | None = None
    analysis: dict[str, Any] | None = None


@dataclass
class WorkloadErrorEvent:
    type: Literal["error"]
    message: str
    phase: str | None = None


WorkloadEvent = Union[
    WorkloadStatusEvent,
    WorkloadConnectedEvent,
    WorkloadSnapshotEvent,
    WorkloadCaptureProgressEvent,
    WorkloadCaptureCompleteEvent,
    WorkloadAnalysisProgressEvent,
    WorkloadQueriesSavedEvent,
    WorkloadCompleteEvent,
    WorkloadErrorEvent,
]

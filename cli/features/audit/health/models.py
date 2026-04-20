"""Dataclasses for the health report (Gautam's §3–§6, §8 sections)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TableHealth:
    """Vacuum/bloat state for one table (pg_stat_user_tables)."""

    schema: str
    table: str
    live_tuples: int = 0
    dead_tuples: int = 0
    dead_ratio_pct: float = 0.0
    last_vacuum: Optional[str] = None
    last_autovacuum: Optional[str] = None
    last_analyze: Optional[str] = None
    last_autoanalyze: Optional[str] = None
    n_mod_since_analyze: Optional[int] = None
    size_bytes: Optional[int] = None
    status: str = "healthy"  # healthy | warn | crit


@dataclass
class VacuumBloatSection:
    """§3 Vacuum & Bloat Health."""

    tables: List[TableHealth] = field(default_factory=list)
    txid_age: Optional[int] = None
    txid_wraparound_limit: int = 2_000_000_000
    txid_age_pct: Optional[float] = None
    autovacuum_workers_active: Optional[int] = None
    autovacuum_max_workers: Optional[int] = None
    autovacuum_enabled: Optional[bool] = None
    summary_status: str = "healthy"


@dataclass
class IndexRow:
    """One entry in pg_stat_user_indexes / information_schema.STATISTICS."""

    schema: str
    table: str
    index: str
    is_unique: bool = False
    is_primary: bool = False
    columns: List[str] = field(default_factory=list)
    size_bytes: Optional[int] = None
    scans: int = 0
    tuples_read: Optional[int] = None
    tuples_fetched: Optional[int] = None


@dataclass
class DuplicateIndexPair:
    """A redundant index pair — one covers the other."""

    schema: str
    table: str
    redundant_index: str
    covered_by: str
    redundant_columns: List[str] = field(default_factory=list)
    covering_columns: List[str] = field(default_factory=list)
    wasted_bytes: Optional[int] = None


@dataclass
class IndexHealthSection:
    """§4 Index Health."""

    total_indexes: int = 0
    unused_indexes: List[IndexRow] = field(default_factory=list)
    duplicates: List[DuplicateIndexPair] = field(default_factory=list)
    all_indexes: List[IndexRow] = field(default_factory=list)


@dataclass
class LongRunningTransaction:
    pid: Optional[int] = None
    state: str = ""
    duration_seconds: float = 0.0
    wait_event: Optional[str] = None
    application_name: Optional[str] = None
    client_addr: Optional[str] = None
    query_preview: Optional[str] = None


@dataclass
class ConnectionAnalysisSection:
    """§5 Connection Analysis."""

    total_connections: int = 0
    max_connections: int = 0
    by_state: Dict[str, int] = field(default_factory=dict)
    idle_in_transaction_count: int = 0
    long_running_idle_in_tx: List[LongRunningTransaction] = field(default_factory=list)
    pooler_detected: bool = False
    pooler_type: Optional[str] = None  # "pgbouncer" | "pgpool" | None


@dataclass
class ConfigSetting:
    parameter: str
    current: str
    recommended: str
    status: str = "ok"  # ok | warn | crit
    note: str = ""


@dataclass
class ConfigAuditSection:
    """§6 Configuration Audit."""

    settings: List[ConfigSetting] = field(default_factory=list)
    instance_ram_gb: Optional[float] = None
    instance_vcpus: Optional[int] = None


@dataclass
class ReplicationPeer:
    application_name: Optional[str] = None
    client_addr: Optional[str] = None
    state: str = ""
    sync_state: Optional[str] = None
    sent_lsn: Optional[str] = None
    write_lsn: Optional[str] = None
    flush_lsn: Optional[str] = None
    replay_lsn: Optional[str] = None
    write_lag_ms: Optional[float] = None
    flush_lag_ms: Optional[float] = None
    replay_lag_ms: Optional[float] = None


@dataclass
class ReplicationSlot:
    slot_name: str
    slot_type: Optional[str] = None
    active: bool = False
    restart_lsn: Optional[str] = None
    confirmed_flush_lsn: Optional[str] = None
    retained_bytes: Optional[int] = None


@dataclass
class ReplicationHealthSection:
    """§8 Replication Health."""

    is_replica: bool = False
    primary_conninfo_host: Optional[str] = None
    upstream_lag_seconds: Optional[float] = None
    wal_receiver_status: Optional[str] = None
    peers: List[ReplicationPeer] = field(default_factory=list)
    slots: List[ReplicationSlot] = field(default_factory=list)
    inactive_slots: int = 0
    wal_generation_mb_per_min: Optional[float] = None


@dataclass
class HealthReport:
    """Aggregate of §3–§6, §8 sections for a single target."""

    engine: str
    vacuum_bloat: Optional[VacuumBloatSection] = None
    index_health: Optional[IndexHealthSection] = None
    connections: Optional[ConnectionAnalysisSection] = None
    config_audit: Optional[ConfigAuditSection] = None
    replication: Optional[ReplicationHealthSection] = None
    collection_error: Optional[str] = None
    section_errors: Dict[str, str] = field(default_factory=dict)


__all__ = [
    "TableHealth",
    "VacuumBloatSection",
    "IndexRow",
    "DuplicateIndexPair",
    "IndexHealthSection",
    "LongRunningTransaction",
    "ConnectionAnalysisSection",
    "ConfigSetting",
    "ConfigAuditSection",
    "ReplicationPeer",
    "ReplicationSlot",
    "ReplicationHealthSection",
    "HealthReport",
]

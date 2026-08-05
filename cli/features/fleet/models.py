"""Fleet-owned data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SizingVerdict(str, Enum):
    UNDER_PROVISIONED = "under_provisioned"
    OVERSIZED = "oversized"
    RIGHT_SIZED = "right_sized"
    UNKNOWN = "unknown"


class TargetType(str, Enum):
    DATABASE = "database"
    READYSET = "readyset"
    READ_REPLICA = "read_replica"


@dataclass
class FleetMember:
    """A database instance in the fleet. Maps to a target in config.toml."""

    name: str
    engine: str
    host: str
    port: int
    database: str
    user: str
    password_env: str | None = None
    group: str | None = None
    tags: list[str] = field(default_factory=list)
    instance_class: str | None = None
    region: str | None = None
    target_type: str = "database"
    primary_target: str | None = None
    tls: bool = False
    tls_verify: bool = False
    tls_ca: str | None = None
    read_only: bool = False
    password_secret_arn: str | None = None
    password_secret_key: str | None = None
    publicly_accessible: bool | None = None
    vpc_id: str | None = None
    password: str | None = field(default=None, repr=False, compare=False)

    def to_target_config(self) -> dict[str, Any]:
        target_config: dict[str, Any] = {
            "engine": self.engine,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "tls": self.tls,
            "tls_verify": self.tls_verify,
            "read_only": self.read_only,
        }
        if self.password_env:
            target_config["password_env"] = self.password_env
        if self.tls_ca:
            target_config["tls_ca"] = self.tls_ca
        if self.target_type != "database":
            target_config["target_type"] = self.target_type
        if self.group:
            target_config["group"] = self.group
        if self.tags:
            target_config["tags"] = self.tags
        if self.instance_class:
            target_config["instance_class"] = self.instance_class
        if self.region:
            target_config["region"] = self.region
        if self.primary_target:
            target_config["primary_target"] = self.primary_target
        if self.password_secret_arn:
            target_config["password_secret_arn"] = self.password_secret_arn
            target_config["password_secret_key"] = self.password_secret_key or "password"
        if self.publicly_accessible is not None:
            target_config["publicly_accessible"] = self.publicly_accessible
        if self.vpc_id:
            target_config["vpc_id"] = self.vpc_id
        return target_config


@dataclass
class FleetAuditSnapshot:
    """A complete fleet audit snapshot for persistence and diffing."""

    snapshot_id: str
    name: str | None = None
    created_at: str = ""
    targets_audited: int = 0
    targets_failed: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
    total_monthly_cost_usd: float | None = None
    potential_savings_usd: float | None = None
    avg_cache_opportunity: float | None = None
    # Parsed fleet LLM output (health_score, top_findings, fleet_findings,
    # next_steps, executive_summary). Persisted so `rdst audit show` against
    # a fleet snapshot can regenerate the report without a fresh LLM call.
    fleet_insights: dict[str, Any] | None = None


@dataclass
class FleetDiffEntry:
    """Diff entry for one target between two snapshots."""

    target_name: str
    field_name: str
    old_value: Any = None
    new_value: Any = None
    change_pct: float | None = None


@dataclass
class FleetDiff:
    """Diff between two fleet audit snapshots."""

    baseline_id: str
    current_id: str
    baseline_date: str = ""
    current_date: str = ""
    entries: list[FleetDiffEntry] = field(default_factory=list)
    new_targets: list[str] = field(default_factory=list)
    removed_targets: list[str] = field(default_factory=list)

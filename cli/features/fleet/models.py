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
    password_env: str
    group: str | None = None
    tags: list[str] = field(default_factory=list)
    instance_class: str | None = None
    region: str | None = None
    target_type: str = "database"
    primary_target: str | None = None
    tls: bool = False
    read_only: bool = False
    password_secret_arn: str | None = None

    def to_target_config(self) -> dict[str, Any]:
        target_config: dict[str, Any] = {
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

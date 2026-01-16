"""
Guard configuration data structures.

Defines GuardConfig and related dataclasses for persisting guard definitions.
Storage: ~/.rdst/guards/<name>.yaml
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import yaml


def _utcnow_iso() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()


GUARDS_DIR = Path.home() / ".rdst" / "guards"


@dataclass
class MaskingConfig:
    """Column masking patterns.

    Patterns map column patterns to mask types:
        "*.email": "email"      -> u***@d***.com
        "*.ssn": "redact"       -> [REDACTED]
        "*.phone": "partial:4"  -> ****1234
        "*.api_key": "hash"     -> a1b2c3d4
    """
    patterns: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, str]:
        return self.patterns.copy()

    @classmethod
    def from_dict(cls, data: dict[str, str] | None) -> "MaskingConfig":
        return cls(patterns=data.copy() if data else {})


@dataclass
class RestrictionsConfig:
    """Access restrictions for columns and tables.

    Attributes:
        denied_columns: Column patterns that are blocked (e.g., "*password*")
        allowed_tables: Whitelist of accessible tables (None = all allowed)
        required_filters: Tables that require filtering on specific columns.
            Format: {"users": ["id", "email"]} means queries on users must
            have a WHERE filter on id OR email with an actual value
            (not just IS NOT NULL).
    """

    denied_columns: list[str] | None = None
    allowed_tables: list[str] | None = None
    required_filters: dict[str, list[str]] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.denied_columns:
            result["denied_columns"] = self.denied_columns.copy()
        if self.allowed_tables:
            result["allowed_tables"] = self.allowed_tables.copy()
        if self.required_filters:
            result["required_filters"] = {
                k: v.copy() for k, v in self.required_filters.items()
            }
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RestrictionsConfig":
        if not data:
            return cls()
        required_filters = data.get("required_filters")
        if required_filters:
            # Ensure lists are copied
            required_filters = {k: list(v) for k, v in required_filters.items()}
        return cls(
            denied_columns=data.get("denied_columns"),
            allowed_tables=data.get("allowed_tables"),
            required_filters=required_filters,
        )


@dataclass
class GuardsConfig:
    """Query guards - structural and cost-based checks.

    Attributes:
        require_where: Block queries without WHERE clause
        require_limit: Block queries without LIMIT clause
        no_select_star: Warn on SELECT * usage
        max_tables: Maximum tables in a query (warn if exceeded)
        cost_limit: Maximum query cost from EXPLAIN (block if exceeded)
        max_estimated_rows: Maximum estimated rows from EXPLAIN (block if exceeded).
            This is stronger than require_where because it catches trivial
            bypasses like "WHERE id IS NOT NULL" - the database planner knows
            this returns all rows.
    """

    require_where: bool = False
    require_limit: bool = False
    no_select_star: bool = False
    max_tables: int | None = None
    cost_limit: int | None = None
    max_estimated_rows: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.require_where:
            result["require_where"] = True
        if self.require_limit:
            result["require_limit"] = True
        if self.no_select_star:
            result["no_select_star"] = True
        if self.max_tables is not None:
            result["max_tables"] = self.max_tables
        if self.cost_limit is not None:
            result["cost_limit"] = self.cost_limit
        if self.max_estimated_rows is not None:
            result["max_estimated_rows"] = self.max_estimated_rows
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "GuardsConfig":
        if not data:
            return cls()
        return cls(
            require_where=data.get("require_where", False),
            require_limit=data.get("require_limit", False),
            no_select_star=data.get("no_select_star", False),
            max_tables=data.get("max_tables"),
            cost_limit=data.get("cost_limit"),
            max_estimated_rows=data.get("max_estimated_rows"),
        )


@dataclass
class LimitsConfig:
    """Execution limits."""

    max_rows: int = 1000
    timeout_seconds: int = 30

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_rows": self.max_rows,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LimitsConfig":
        if not data:
            return cls()
        return cls(
            max_rows=data.get("max_rows", 1000),
            timeout_seconds=data.get("timeout_seconds", 30),
        )


@dataclass
class GuardConfig:
    """Complete guard configuration.

    A guard defines reusable safety policies that can be applied to agents:
    - Output masking (redact sensitive columns)
    - Access restrictions (deny columns, allow tables)
    - Query guards (require WHERE, cost limits)
    - Execution limits (max rows, timeout)

    Guards can be created from natural language intent using --intent flag,
    which uses LLM to derive concrete rules at creation time.
    """

    name: str
    description: str = ""
    intent: str = ""  # Natural language policy (used for LLM-based rule derivation)
    derived: bool = False  # True if rules were auto-derived from intent
    created_at: str = field(default_factory=_utcnow_iso)

    masking: MaskingConfig = field(default_factory=MaskingConfig)
    restrictions: RestrictionsConfig = field(default_factory=RestrictionsConfig)
    guards: GuardsConfig = field(default_factory=GuardsConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for YAML serialization."""
        result: dict[str, Any] = {
            "name": self.name,
            "created_at": self.created_at,
        }

        if self.description:
            result["description"] = self.description

        if self.intent:
            result["intent"] = self.intent

        if self.derived:
            result["derived"] = True

        # Only include non-empty sections
        masking = self.masking.to_dict()
        if masking:
            result["masking"] = masking

        restrictions = self.restrictions.to_dict()
        if restrictions:
            result["restrictions"] = restrictions

        guards = self.guards.to_dict()
        if guards:
            result["guards"] = guards

        # Always include limits
        result["limits"] = self.limits.to_dict()

        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GuardConfig":
        """Create from dictionary loaded from YAML."""
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            intent=data.get("intent", ""),
            derived=data.get("derived", False),
            created_at=data.get("created_at", _utcnow_iso()),
            masking=MaskingConfig.from_dict(data.get("masking")),
            restrictions=RestrictionsConfig.from_dict(data.get("restrictions")),
            guards=GuardsConfig.from_dict(data.get("guards")),
            limits=LimitsConfig.from_dict(data.get("limits")),
        )

    @classmethod
    def load(cls, path: Path) -> "GuardConfig":
        """Load guard config from a YAML file."""
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    def save(self, path: Path | None = None) -> Path:
        """Save guard config to a YAML file."""
        if path is None:
            path = GUARDS_DIR / f"{self.name}.yaml"

        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            yaml.dump(
                self.to_dict(),
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

        return path

    def has_masking(self) -> bool:
        """Check if guard has any masking patterns."""
        return bool(self.masking.patterns)

    def has_guards(self) -> bool:
        """Check if guard has any query guards enabled."""
        return (
            self.guards.require_where
            or self.guards.require_limit
            or self.guards.no_select_star
            or self.guards.max_tables is not None
            or self.guards.cost_limit is not None
            or self.guards.max_estimated_rows is not None
        )

    def has_restrictions(self) -> bool:
        """Check if guard has any access restrictions."""
        return (
            bool(self.restrictions.denied_columns)
            or bool(self.restrictions.allowed_tables)
            or bool(self.restrictions.required_filters)
        )

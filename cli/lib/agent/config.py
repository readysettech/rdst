"""
Agent configuration data structures.

Defines AgentConfig and SafetyConfig for persisting agent definitions.
Storage: ~/.rdst/agents/<name>.yaml
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


AGENTS_DIR = Path.home() / ".rdst" / "agents"


@dataclass
class SafetyConfig:
    """Safety configuration for an agent."""

    read_only: bool = True
    max_rows: int = 1000
    timeout_seconds: int = 30

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for YAML serialization."""
        return {
            "read_only": self.read_only,
            "max_rows": self.max_rows,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SafetyConfig":
        """Create from dictionary loaded from YAML."""
        return cls(
            read_only=data.get("read_only", True),
            max_rows=data.get("max_rows", 1000),
            timeout_seconds=data.get("timeout_seconds", 30),
        )


@dataclass
class RestrictionsConfig:
    """Column-level restrictions for an agent."""

    allowed_tables: list[str] | None = None
    denied_columns: list[str] | None = None
    masked_columns: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for YAML serialization."""
        result: dict[str, Any] = {}
        if self.allowed_tables:
            result["allowed_tables"] = self.allowed_tables
        if self.denied_columns:
            result["denied_columns"] = self.denied_columns
        if self.masked_columns:
            result["masked_columns"] = self.masked_columns
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RestrictionsConfig":
        """Create from dictionary loaded from YAML."""
        return cls(
            allowed_tables=data.get("allowed_tables"),
            denied_columns=data.get("denied_columns"),
            masked_columns=data.get("masked_columns"),
        )


@dataclass
class AgentConfig:
    """Configuration for a data agent.

    Agents can use either:
    1. A guard reference (preferred): `guard: "pii-safe"` references ~/.rdst/guards/pii-safe.yaml
    2. Inline config (legacy): `safety` and `restrictions` fields

    When a guard is specified, it takes precedence over inline config.
    """

    name: str
    target: str
    description: str = ""
    created_at: str = field(default_factory=_utcnow_iso)

    # Guard reference (preferred) - references a guard by name
    guard: str | None = None

    # Legacy inline config (used when no guard specified)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    restrictions: RestrictionsConfig = field(default_factory=RestrictionsConfig)

    semantic_layer: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for YAML serialization."""
        result: dict[str, Any] = {
            "name": self.name,
            "target": self.target,
            "created_at": self.created_at,
        }
        if self.description:
            result["description"] = self.description

        # Guard reference (preferred)
        if self.guard:
            result["guard"] = self.guard

        # Legacy inline config
        result["safety"] = self.safety.to_dict()

        restrictions = self.restrictions.to_dict()
        if restrictions:
            result["restrictions"] = restrictions

        if self.semantic_layer:
            result["semantic_layer"] = self.semantic_layer

        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentConfig":
        """Create from dictionary loaded from YAML."""
        safety_data = data.get("safety", {})
        restrictions_data = data.get("restrictions", {})

        return cls(
            name=data["name"],
            target=data["target"],
            description=data.get("description", ""),
            created_at=data.get("created_at", _utcnow_iso()),
            guard=data.get("guard"),
            safety=SafetyConfig.from_dict(safety_data),
            restrictions=RestrictionsConfig.from_dict(restrictions_data),
            semantic_layer=data.get("semantic_layer"),
        )

    @classmethod
    def load(cls, path: Path) -> "AgentConfig":
        """Load agent config from a YAML file."""
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    def save(self, path: Path | None = None) -> Path:
        """Save agent config to a YAML file."""
        if path is None:
            path = AGENTS_DIR / f"{self.name}.yaml"

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

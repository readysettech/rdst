"""
Agent Manager

Handles CRUD operations for data agents.
Storage: ~/.rdst/agents/<name>.yaml
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import AgentConfig, SafetyConfig, RestrictionsConfig, AGENTS_DIR


class AgentManagerError(Exception):
    """Base exception for agent manager errors."""

    pass


class AgentNotFoundError(AgentManagerError):
    """Agent does not exist."""

    pass


class AgentExistsError(AgentManagerError):
    """Agent already exists."""

    pass


class InvalidAgentNameError(AgentManagerError):
    """Agent name is invalid."""

    pass


class TargetNotFoundError(AgentManagerError):
    """Database target does not exist."""

    pass


# Valid agent name pattern: alphanumeric, hyphens, underscores
AGENT_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


def validate_agent_name(name: str) -> None:
    """
    Validate agent name.

    Args:
        name: Agent name to validate.

    Raises:
        InvalidAgentNameError: If name is invalid.
    """
    if not name:
        raise InvalidAgentNameError("Agent name cannot be empty")

    if len(name) > 64:
        raise InvalidAgentNameError("Agent name cannot exceed 64 characters")

    if not AGENT_NAME_PATTERN.match(name):
        raise InvalidAgentNameError(
            f"Invalid agent name '{name}'. "
            "Name must start with a letter and contain only "
            "letters, numbers, hyphens, and underscores."
        )


class AgentManager:
    """
    Manager for data agent CRUD operations.

    Handles creating, listing, loading, and deleting agents.
    """

    def __init__(self, base_dir: Path | None = None):
        """
        Initialize the agent manager.

        Args:
            base_dir: Base directory for agent files.
                      Defaults to ~/.rdst/agents/
        """
        self.base_dir = base_dir if base_dir else AGENTS_DIR

    def _get_path(self, name: str) -> Path:
        """Get the file path for an agent."""
        return self.base_dir / f"{name}.yaml"

    def exists(self, name: str) -> bool:
        """Check if an agent exists."""
        return self._get_path(name).exists()

    def list(self) -> list[str]:
        """
        List all agent names.

        Returns:
            Sorted list of agent names.
        """
        if not self.base_dir.exists():
            return []

        names = []
        for path in self.base_dir.glob("*.yaml"):
            names.append(path.stem)

        return sorted(names)

    def get(self, name: str) -> AgentConfig:
        """
        Load an agent configuration.

        Args:
            name: Agent name.

        Returns:
            AgentConfig object.

        Raises:
            AgentNotFoundError: If agent does not exist.
        """
        path = self._get_path(name)
        if not path.exists():
            raise AgentNotFoundError(f"Agent '{name}' not found")

        return AgentConfig.load(path)

    def create(
        self,
        name: str,
        target: str,
        description: str = "",
        max_rows: int = 1000,
        timeout_seconds: int = 30,
        denied_columns: list[str] | None = None,
        allowed_tables: list[str] | None = None,
        masked_columns: dict[str, str] | None = None,
        guard: str | None = None,
        validate_target: bool = True,
    ) -> AgentConfig:
        """
        Create a new agent.

        Args:
            name: Unique agent name.
            target: Database target name (from ~/.rdst/config.toml).
            description: Human-readable description.
            max_rows: Maximum rows to return (default 1000).
            timeout_seconds: Query timeout in seconds (default 30).
            denied_columns: List of column patterns to deny access.
            allowed_tables: List of tables to allow (None = all).
            masked_columns: Dict of column -> mask pattern.
            guard: Name of guard to apply (from ~/.rdst/guards/).
            validate_target: Whether to validate target exists.

        Returns:
            Created AgentConfig.

        Raises:
            AgentExistsError: If agent already exists.
            InvalidAgentNameError: If name is invalid.
            TargetNotFoundError: If target does not exist.
        """
        validate_agent_name(name)

        if self.exists(name):
            raise AgentExistsError(f"Agent '{name}' already exists")

        if validate_target:
            self._validate_target(target)

        # Validate guard exists if specified
        if guard:
            self._validate_guard(guard)

        safety = SafetyConfig(
            read_only=True,
            max_rows=max_rows,
            timeout_seconds=timeout_seconds,
        )

        restrictions = RestrictionsConfig(
            allowed_tables=allowed_tables,
            denied_columns=denied_columns,
            masked_columns=masked_columns,
        )

        config = AgentConfig(
            name=name,
            target=target,
            description=description,
            guard=guard,
            safety=safety,
            restrictions=restrictions,
        )

        config.save(self._get_path(name))
        return config

    def delete(self, name: str) -> bool:
        """
        Delete an agent.

        Args:
            name: Agent name.

        Returns:
            True if deleted, False if not found.
        """
        path = self._get_path(name)
        if path.exists():
            path.unlink()
            return True
        return False

    def update(self, config: AgentConfig) -> AgentConfig:
        """
        Update an existing agent.

        Args:
            config: Updated AgentConfig.

        Returns:
            Updated AgentConfig.

        Raises:
            AgentNotFoundError: If agent does not exist.
        """
        if not self.exists(config.name):
            raise AgentNotFoundError(f"Agent '{config.name}' not found")

        config.save(self._get_path(config.name))
        return config

    def _validate_target(self, target: str) -> None:
        """
        Validate that a database target exists.

        Args:
            target: Target name.

        Raises:
            TargetNotFoundError: If target does not exist.
        """
        # Lazy import to avoid circular dependencies
        from ..cli.rdst_cli import TargetsConfig

        cfg = TargetsConfig()
        cfg.load()

        if target not in cfg.list_targets():
            available = cfg.list_targets()
            if available:
                msg = f"Target '{target}' not found. Available targets: {', '.join(available)}"
            else:
                msg = f"Target '{target}' not found. No targets configured. Run 'rdst configure' first."
            raise TargetNotFoundError(msg)

    def _validate_guard(self, guard: str) -> None:
        """
        Validate that a guard exists.

        Args:
            guard: Guard name.

        Raises:
            AgentManagerError: If guard does not exist.
        """
        from ..guard import GuardManager, GuardNotFoundError

        mgr = GuardManager()
        try:
            mgr.get(guard)
        except GuardNotFoundError:
            available = mgr.list()
            if available:
                msg = f"Guard '{guard}' not found. Available guards: {', '.join(available)}"
            else:
                msg = f"Guard '{guard}' not found. No guards configured. Run 'rdst guard create' first."
            raise AgentManagerError(msg)

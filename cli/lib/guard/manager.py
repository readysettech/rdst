"""
Guard manager - CRUD operations for guard configurations.

Handles creating, listing, loading, and deleting guards stored in ~/.rdst/guards/.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from .config import GuardConfig, GUARDS_DIR


class GuardNotFoundError(Exception):
    """Raised when a guard is not found."""
    pass


class GuardExistsError(Exception):
    """Raised when trying to create a guard that already exists."""
    pass


class GuardManager:
    """Manages guard configurations.

    Guards are stored as YAML files in ~/.rdst/guards/<name>.yaml
    """

    def __init__(self, guards_dir: Path | None = None):
        """Initialize the guard manager.

        Args:
            guards_dir: Directory to store guards. Defaults to ~/.rdst/guards/
        """
        self.guards_dir = guards_dir or GUARDS_DIR

    def _guard_path(self, name: str) -> Path:
        """Get the path for a guard file."""
        return self.guards_dir / f"{name}.yaml"

    def exists(self, name: str) -> bool:
        """Check if a guard exists."""
        return self._guard_path(name).exists()

    def create(self, config: GuardConfig, overwrite: bool = False) -> Path:
        """Create a new guard.

        Args:
            config: Guard configuration to save.
            overwrite: If True, overwrite existing guard.

        Returns:
            Path to the created guard file.

        Raises:
            GuardExistsError: If guard exists and overwrite is False.
        """
        path = self._guard_path(config.name)

        if path.exists() and not overwrite:
            raise GuardExistsError(f"Guard '{config.name}' already exists")

        return config.save(path)

    def get(self, name: str) -> GuardConfig:
        """Load a guard by name.

        Args:
            name: Name of the guard to load.

        Returns:
            Loaded GuardConfig.

        Raises:
            GuardNotFoundError: If guard doesn't exist.
        """
        path = self._guard_path(name)

        if not path.exists():
            raise GuardNotFoundError(f"Guard '{name}' not found")

        return GuardConfig.load(path)

    def delete(self, name: str) -> None:
        """Delete a guard.

        Args:
            name: Name of the guard to delete.

        Raises:
            GuardNotFoundError: If guard doesn't exist.
        """
        path = self._guard_path(name)

        if not path.exists():
            raise GuardNotFoundError(f"Guard '{name}' not found")

        path.unlink()

    def list(self) -> list[str]:
        """List all guard names.

        Returns:
            List of guard names (without .yaml extension).
        """
        if not self.guards_dir.exists():
            return []

        return sorted([
            p.stem for p in self.guards_dir.glob("*.yaml")
        ])

    def list_configs(self) -> Iterator[GuardConfig]:
        """Iterate over all guard configurations.

        Yields:
            GuardConfig for each guard.
        """
        for name in self.list():
            try:
                yield self.get(name)
            except Exception:
                # Skip malformed configs
                continue

    def update(self, config: GuardConfig) -> Path:
        """Update an existing guard.

        Args:
            config: Updated guard configuration.

        Returns:
            Path to the updated guard file.

        Raises:
            GuardNotFoundError: If guard doesn't exist.
        """
        if not self.exists(config.name):
            raise GuardNotFoundError(f"Guard '{config.name}' not found")

        return config.save(self._guard_path(config.name))

    def rename(self, old_name: str, new_name: str) -> Path:
        """Rename a guard.

        Args:
            old_name: Current guard name.
            new_name: New guard name.

        Returns:
            Path to the renamed guard file.

        Raises:
            GuardNotFoundError: If old guard doesn't exist.
            GuardExistsError: If new name already exists.
        """
        if not self.exists(old_name):
            raise GuardNotFoundError(f"Guard '{old_name}' not found")

        if self.exists(new_name):
            raise GuardExistsError(f"Guard '{new_name}' already exists")

        config = self.get(old_name)
        config.name = new_name

        # Save with new name
        new_path = config.save(self._guard_path(new_name))

        # Delete old file
        self._guard_path(old_name).unlink()

        return new_path

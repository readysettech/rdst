"""Tests for GuardManager."""

import pytest
from pathlib import Path
import tempfile

from lib.guard.config import (
    GuardConfig,
    MaskingConfig,
    GuardsConfig,
    RestrictionsConfig,
    LimitsConfig,
)
from lib.guard.manager import (
    GuardManager,
    GuardNotFoundError,
    GuardExistsError,
)


class TestGuardManager:
    """Test GuardManager CRUD operations."""

    @pytest.fixture
    def manager(self):
        """Create manager with temp directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield GuardManager(guards_dir=Path(tmpdir))

    def test_list_empty(self, manager):
        """Should return empty list when no guards exist."""
        assert manager.list() == []

    def test_create_guard(self, manager):
        """Should create a guard successfully."""
        config = GuardConfig(name="test-guard", description="Test description")
        manager.create(config)

        assert manager.exists("test-guard")

    def test_create_with_options(self, manager):
        """Should create guard with all options."""
        config = GuardConfig(
            name="full-guard",
            description="Full options",
            masking=MaskingConfig(patterns={"*.email": "email", "*.ssn": "redact"}),
            restrictions=RestrictionsConfig(
                denied_columns=["*.password"],
                allowed_tables=["users", "orders"],
            ),
            guards=GuardsConfig(
                require_where=True,
                require_limit=True,
                no_select_star=True,
                max_tables=5,
                cost_limit=10000,
            ),
            limits=LimitsConfig(max_rows=500, timeout_seconds=60),
        )
        manager.create(config)

        loaded = manager.get("full-guard")
        assert loaded.masking.patterns == {"*.email": "email", "*.ssn": "redact"}
        assert loaded.restrictions.denied_columns == ["*.password"]
        assert loaded.restrictions.allowed_tables == ["users", "orders"]
        assert loaded.guards.require_where is True
        assert loaded.guards.require_limit is True
        assert loaded.guards.no_select_star is True
        assert loaded.guards.max_tables == 5
        assert loaded.guards.cost_limit == 10000
        assert loaded.limits.max_rows == 500
        assert loaded.limits.timeout_seconds == 60

    def test_create_duplicate(self, manager):
        """Should raise error when creating duplicate guard."""
        config = GuardConfig(name="dup-guard")
        manager.create(config)

        with pytest.raises(GuardExistsError):
            manager.create(config)

    def test_create_overwrite(self, manager):
        """Should allow overwriting when specified."""
        config = GuardConfig(name="overwrite-test", description="Original")
        manager.create(config)

        config.description = "Updated"
        manager.create(config, overwrite=True)

        loaded = manager.get("overwrite-test")
        assert loaded.description == "Updated"

    def test_get_guard(self, manager):
        """Should retrieve created guard."""
        config = GuardConfig(name="get-test", description="To be retrieved")
        manager.create(config)

        loaded = manager.get("get-test")
        assert loaded.name == "get-test"
        assert loaded.description == "To be retrieved"

    def test_get_nonexistent(self, manager):
        """Should raise error for nonexistent guard."""
        with pytest.raises(GuardNotFoundError):
            manager.get("does-not-exist")

    def test_list_guards(self, manager):
        """Should list all guards in sorted order."""
        manager.create(GuardConfig(name="zebra-guard"))
        manager.create(GuardConfig(name="alpha-guard"))
        manager.create(GuardConfig(name="beta-guard"))

        names = manager.list()
        assert names == ["alpha-guard", "beta-guard", "zebra-guard"]

    def test_delete_guard(self, manager):
        """Should delete existing guard."""
        manager.create(GuardConfig(name="to-delete"))
        assert manager.exists("to-delete")

        manager.delete("to-delete")
        assert not manager.exists("to-delete")

    def test_delete_nonexistent(self, manager):
        """Should raise error when deleting nonexistent guard."""
        with pytest.raises(GuardNotFoundError):
            manager.delete("does-not-exist")

    def test_update_guard(self, manager):
        """Should update existing guard."""
        manager.create(GuardConfig(name="to-update", description="Original"))
        config = manager.get("to-update")
        config.description = "Updated"
        config.guards.require_where = True

        manager.update(config)

        reloaded = manager.get("to-update")
        assert reloaded.description == "Updated"
        assert reloaded.guards.require_where is True

    def test_update_nonexistent(self, manager):
        """Should raise error when updating nonexistent guard."""
        config = GuardConfig(name="does-not-exist")
        with pytest.raises(GuardNotFoundError):
            manager.update(config)

    def test_rename_guard(self, manager):
        """Should rename guard successfully."""
        manager.create(GuardConfig(name="old-name", description="To be renamed"))
        manager.rename("old-name", "new-name")

        assert not manager.exists("old-name")
        assert manager.exists("new-name")

        config = manager.get("new-name")
        assert config.name == "new-name"
        assert config.description == "To be renamed"

    def test_rename_to_existing(self, manager):
        """Should raise error when renaming to existing name."""
        manager.create(GuardConfig(name="guard-a"))
        manager.create(GuardConfig(name="guard-b"))

        with pytest.raises(GuardExistsError):
            manager.rename("guard-a", "guard-b")

    def test_rename_nonexistent(self, manager):
        """Should raise error when renaming nonexistent guard."""
        with pytest.raises(GuardNotFoundError):
            manager.rename("does-not-exist", "new-name")

    def test_exists(self, manager):
        """Should correctly report existence."""
        assert not manager.exists("not-yet")
        manager.create(GuardConfig(name="not-yet"))
        assert manager.exists("not-yet")

    def test_list_configs(self, manager):
        """Should iterate over all guard configurations."""
        manager.create(GuardConfig(name="guard-1", description="First"))
        manager.create(GuardConfig(name="guard-2", description="Second"))

        configs = list(manager.list_configs())
        assert len(configs) == 2

        names = [c.name for c in configs]
        assert "guard-1" in names
        assert "guard-2" in names

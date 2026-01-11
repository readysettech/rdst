"""
Unit tests for lib/agent/manager.py

Tests AgentManager CRUD operations.
"""

import pytest
import tempfile
from pathlib import Path

from lib.agent.manager import (
    AgentManager,
    AgentNotFoundError,
    AgentExistsError,
    InvalidAgentNameError,
    validate_agent_name,
)
from lib.agent.config import AgentConfig


class TestValidateAgentName:
    """Tests for agent name validation."""

    def test_valid_names(self):
        """Test valid agent names pass validation."""
        valid_names = [
            "agent",
            "my-agent",
            "my_agent",
            "Agent1",
            "agent-1",
            "agent_1",
            "MyAgent",
            "a",
            "A",
        ]
        for name in valid_names:
            validate_agent_name(name)  # Should not raise

    def test_empty_name_raises(self):
        """Test empty name raises error."""
        with pytest.raises(InvalidAgentNameError, match="cannot be empty"):
            validate_agent_name("")

    def test_too_long_name_raises(self):
        """Test name over 64 chars raises error."""
        long_name = "a" * 65
        with pytest.raises(InvalidAgentNameError, match="cannot exceed 64"):
            validate_agent_name(long_name)

    def test_name_starting_with_number_raises(self):
        """Test name starting with number raises error."""
        with pytest.raises(InvalidAgentNameError, match="must start with a letter"):
            validate_agent_name("123agent")

    def test_name_starting_with_hyphen_raises(self):
        """Test name starting with hyphen raises error."""
        with pytest.raises(InvalidAgentNameError, match="must start with a letter"):
            validate_agent_name("-agent")

    def test_name_with_spaces_raises(self):
        """Test name with spaces raises error."""
        with pytest.raises(InvalidAgentNameError, match="must start with a letter"):
            validate_agent_name("my agent")

    def test_name_with_special_chars_raises(self):
        """Test name with special characters raises error."""
        invalid_names = ["agent@test", "agent.test", "agent/test", "agent:test"]
        for name in invalid_names:
            with pytest.raises(InvalidAgentNameError):
                validate_agent_name(name)


class TestAgentManager:
    """Tests for AgentManager class."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create a manager with temporary directory."""
        return AgentManager(base_dir=tmp_path)

    def test_list_empty(self, manager):
        """Test listing with no agents returns empty list."""
        assert manager.list() == []

    def test_exists_returns_false_for_missing(self, manager):
        """Test exists returns False for non-existent agent."""
        assert manager.exists("nonexistent") is False

    def test_create_agent(self, manager):
        """Test creating an agent."""
        agent = manager.create(
            name="test-agent",
            target="test-db",
            description="Test description",
            validate_target=False,  # Skip target validation
        )

        assert agent.name == "test-agent"
        assert agent.target == "test-db"
        assert agent.description == "Test description"
        assert manager.exists("test-agent")

    def test_create_with_safety_options(self, manager):
        """Test creating agent with custom safety options."""
        agent = manager.create(
            name="safe-agent",
            target="db",
            max_rows=500,
            timeout_seconds=60,
            validate_target=False,
        )

        assert agent.safety.max_rows == 500
        assert agent.safety.timeout_seconds == 60
        assert agent.safety.read_only is True

    def test_create_with_restrictions(self, manager):
        """Test creating agent with column restrictions."""
        agent = manager.create(
            name="restricted",
            target="db",
            denied_columns=["*.password", "users.ssn"],
            allowed_tables=["public.users"],
            validate_target=False,
        )

        assert agent.restrictions.denied_columns == ["*.password", "users.ssn"]
        assert agent.restrictions.allowed_tables == ["public.users"]

    def test_create_duplicate_raises(self, manager):
        """Test creating duplicate agent raises error."""
        manager.create(name="agent", target="db", validate_target=False)

        with pytest.raises(AgentExistsError, match="already exists"):
            manager.create(name="agent", target="db", validate_target=False)

    def test_create_invalid_name_raises(self, manager):
        """Test creating agent with invalid name raises error."""
        with pytest.raises(InvalidAgentNameError):
            manager.create(name="123invalid", target="db", validate_target=False)

    def test_get_agent(self, manager):
        """Test getting an agent."""
        manager.create(
            name="my-agent",
            target="my-db",
            description="My agent",
            validate_target=False,
        )

        agent = manager.get("my-agent")
        assert agent.name == "my-agent"
        assert agent.target == "my-db"
        assert agent.description == "My agent"

    def test_get_nonexistent_raises(self, manager):
        """Test getting non-existent agent raises error."""
        with pytest.raises(AgentNotFoundError, match="not found"):
            manager.get("nonexistent")

    def test_list_agents(self, manager):
        """Test listing agents returns sorted names."""
        manager.create(name="zebra", target="db", validate_target=False)
        manager.create(name="alpha", target="db", validate_target=False)
        manager.create(name="beta", target="db", validate_target=False)

        names = manager.list()
        assert names == ["alpha", "beta", "zebra"]

    def test_delete_agent(self, manager):
        """Test deleting an agent."""
        manager.create(name="to-delete", target="db", validate_target=False)
        assert manager.exists("to-delete")

        result = manager.delete("to-delete")
        assert result is True
        assert manager.exists("to-delete") is False

    def test_delete_nonexistent_returns_false(self, manager):
        """Test deleting non-existent agent returns False."""
        result = manager.delete("nonexistent")
        assert result is False

    def test_update_agent(self, manager):
        """Test updating an agent."""
        manager.create(
            name="updatable",
            target="old-db",
            description="Old description",
            validate_target=False,
        )

        agent = manager.get("updatable")
        agent.description = "New description"
        agent.target = "new-db"

        updated = manager.update(agent)
        assert updated.description == "New description"

        # Verify persistence
        reloaded = manager.get("updatable")
        assert reloaded.description == "New description"
        assert reloaded.target == "new-db"

    def test_update_nonexistent_raises(self, manager):
        """Test updating non-existent agent raises error."""
        config = AgentConfig(name="nonexistent", target="db")

        with pytest.raises(AgentNotFoundError):
            manager.update(config)

    def test_persistence_across_instances(self, tmp_path):
        """Test agents persist across manager instances."""
        # Create with first manager
        manager1 = AgentManager(base_dir=tmp_path)
        manager1.create(
            name="persistent",
            target="db",
            description="Should persist",
            validate_target=False,
        )

        # Load with second manager
        manager2 = AgentManager(base_dir=tmp_path)
        assert manager2.exists("persistent")
        agent = manager2.get("persistent")
        assert agent.description == "Should persist"

    def test_multiple_agents(self, manager):
        """Test managing multiple agents."""
        for i in range(5):
            manager.create(
                name=f"agent-{i}",
                target=f"db-{i}",
                validate_target=False,
            )

        assert len(manager.list()) == 5

        # Delete some
        manager.delete("agent-1")
        manager.delete("agent-3")

        names = manager.list()
        assert len(names) == 3
        assert "agent-1" not in names
        assert "agent-3" not in names

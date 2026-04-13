"""
Tests for consistency between agent and guard commands.

Covers:
- Agent name validation accepts underscore-prefixed names (matching guard behavior)
- Agent and guard share compatible name patterns
- Agent empty-state message includes a helpful suggestion
- Guard empty-state message includes a helpful suggestion (regression)
- Error message consistency between agent and guard
"""

from __future__ import annotations

import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from features.agent.manager import (
    AgentManager,
    InvalidAgentNameError,
    validate_agent_name,
)
from features.agent.cli.command import AgentCommand
from features.guard.cli.command import GuardCommand
from features.guard.manager import GuardManager
from features.guard.config import GuardConfig
from shared.cli.types import RdstResult


class TestAgentNameValidationUnderscorePrefix:
    """Agent name validation should accept underscore-prefixed names."""

    def test_leading_underscore_is_valid(self):
        """Agent should accept names starting with underscore."""
        validate_agent_name("_test_name")  # must not raise

    def test_double_leading_underscore_is_valid(self):
        """Agent should accept names with multiple leading underscores."""
        validate_agent_name("__internal")  # must not raise

    def test_underscore_only_is_valid(self):
        """A single underscore is a valid agent name."""
        validate_agent_name("_")  # must not raise

    def test_underscore_prefix_with_numbers_is_valid(self):
        """Underscore-prefixed name with numbers should be valid."""
        validate_agent_name("_agent_42")  # must not raise

    def test_underscore_prefix_with_hyphens_is_valid(self):
        """Underscore-prefixed name with hyphens should be valid."""
        validate_agent_name("_my-agent")  # must not raise


class TestAgentAndGuardNamePatternCompatibility:
    """Agent and guard should accept the same categories of names."""

    # Names that both agent and guard should accept
    SHARED_VALID_NAMES = [
        "agent",
        "my-agent",
        "my_agent",
        "Agent1",
        "agent-1",
        "agent_1",
        "MyAgent",
        "a",
        "A",
        "_test_name",
        "_agent",
        "__double_underscore",
        "z" * 64,  # Maximum length
    ]

    # Names that both agent and guard should reject
    SHARED_INVALID_NAMES = [
        "my agent",        # space
        "agent@host",      # @ symbol
        "agent.sub",       # dot
        "agent/path",      # slash
        "agent:port",      # colon
        "-starts-hyphen",  # leading hyphen
        "123numeric",      # leading digit
        "",                # empty
    ]

    def test_agent_accepts_letter_prefixed_names(self):
        """Agent accepts standard letter-prefixed names."""
        letter_names = ["agent", "my-agent", "my_agent", "Agent1", "MyAgent", "a", "A"]
        for name in letter_names:
            validate_agent_name(name)  # must not raise

    def test_agent_accepts_underscore_prefixed_names(self):
        """Agent accepts underscore-prefixed names (same as guard)."""
        underscore_names = ["_test_name", "_agent", "__double_underscore"]
        for name in underscore_names:
            validate_agent_name(name)  # must not raise

    def test_agent_accepts_names_with_numbers_after_first_char(self):
        """Agent accepts names with numbers after the first character."""
        validate_agent_name("agent1")
        validate_agent_name("_agent1")
        validate_agent_name("a1b2c3")

    def test_agent_accepts_names_with_hyphens(self):
        """Agent accepts names with hyphens."""
        validate_agent_name("my-agent")
        validate_agent_name("_my-agent")

    def test_agent_rejects_space_in_name(self):
        """Agent rejects names containing spaces."""
        with pytest.raises(InvalidAgentNameError):
            validate_agent_name("my agent")

    def test_agent_rejects_at_symbol(self):
        """Agent rejects names containing @ symbol."""
        with pytest.raises(InvalidAgentNameError):
            validate_agent_name("agent@host")

    def test_agent_rejects_dot(self):
        """Agent rejects names containing dots."""
        with pytest.raises(InvalidAgentNameError):
            validate_agent_name("agent.sub")

    def test_agent_rejects_slash(self):
        """Agent rejects names containing slashes."""
        with pytest.raises(InvalidAgentNameError):
            validate_agent_name("agent/path")

    def test_agent_rejects_colon(self):
        """Agent rejects names containing colons."""
        with pytest.raises(InvalidAgentNameError):
            validate_agent_name("agent:port")

    def test_agent_rejects_leading_hyphen(self):
        """Agent rejects names starting with a hyphen."""
        with pytest.raises(InvalidAgentNameError):
            validate_agent_name("-starts-hyphen")

    def test_agent_rejects_leading_digit(self):
        """Agent rejects names starting with a digit."""
        with pytest.raises(InvalidAgentNameError):
            validate_agent_name("123numeric")

    def test_agent_rejects_empty_name(self):
        """Agent rejects empty names."""
        with pytest.raises(InvalidAgentNameError):
            validate_agent_name("")

    def test_agent_accepts_64_char_max(self):
        """Agent accepts names exactly 64 characters long."""
        validate_agent_name("z" * 64)  # must not raise

    def test_agent_rejects_65_char_name(self):
        """Agent rejects names longer than 64 characters."""
        with pytest.raises(InvalidAgentNameError, match="cannot exceed 64"):
            validate_agent_name("z" * 65)


class TestAgentManagerUnderscoreNames:
    """AgentManager should successfully create agents with underscore names."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create a manager with temporary directory."""
        return AgentManager(base_dir=tmp_path)

    def test_create_agent_with_underscore_prefix(self, manager):
        """AgentManager.create should succeed for underscore-prefixed names."""
        agent = manager.create(
            name="_test_name",
            target="prod-db",
            description="test",
            validate_target=False,
        )
        assert agent.name == "_test_name"
        assert manager.exists("_test_name")

    def test_create_agent_with_double_underscore(self, manager):
        """AgentManager.create should succeed for double-underscore names."""
        agent = manager.create(
            name="__internal",
            target="prod-db",
            validate_target=False,
        )
        assert agent.name == "__internal"

    def test_get_agent_with_underscore_name(self, manager):
        """Created underscore-named agent can be retrieved."""
        manager.create(name="_lookup", target="db", validate_target=False)
        loaded = manager.get("_lookup")
        assert loaded.name == "_lookup"


class TestEmptyStateMessages:
    """Empty-state messages should include helpful next-step suggestions."""

    def test_agent_list_empty_includes_suggestion(self, tmp_path):
        """rdst agent list (empty) should suggest how to create an agent."""
        cmd = AgentCommand()
        # Redirect manager to empty temp dir
        with patch.object(cmd, "_get_manager") as mock_get_mgr:
            mock_mgr = MagicMock()
            mock_mgr.list.return_value = []
            mock_get_mgr.return_value = mock_mgr

            result = cmd.execute("list")

        assert result.ok is True
        assert "rdst agent create" in result.message
        assert "--name" in result.message
        assert "--target" in result.message

    def test_agent_list_empty_message_content(self, tmp_path):
        """Agent empty-state message should mention 'No agents configured'."""
        cmd = AgentCommand()
        with patch.object(cmd, "_get_manager") as mock_get_mgr:
            mock_mgr = MagicMock()
            mock_mgr.list.return_value = []
            mock_get_mgr.return_value = mock_mgr

            result = cmd.execute("list")

        assert "No agents configured" in result.message

    def test_guard_list_empty_includes_suggestion(self, tmp_path):
        """rdst guard list (empty) should suggest how to create a guard.

        This is a regression test — guard had this right from the start.
        """
        cmd = GuardCommand()
        with patch.object(cmd, "manager") as mock_mgr:
            mock_mgr.list_configs.return_value = iter([])

            result = cmd.execute("list")

        assert result.ok is True
        assert "rdst guard create" in result.message
        assert "--name" in result.message

    def test_guard_list_empty_message_content(self, tmp_path):
        """Guard empty-state message should mention 'No guards configured'."""
        cmd = GuardCommand()
        with patch.object(cmd, "manager") as mock_mgr:
            mock_mgr.list_configs.return_value = iter([])

            result = cmd.execute("list")

        assert "No guards configured" in result.message


class TestErrorMessageConsistency:
    """Agent and guard error messages should follow consistent patterns."""

    def test_agent_create_missing_name_returns_error(self):
        """Agent create without --name returns a failure result."""
        cmd = AgentCommand()
        result = cmd.execute("create", name=None, target="some-db")
        assert result.ok is False
        assert "name" in result.message.lower()

    def test_guard_create_missing_name_returns_error(self):
        """Guard create without --name returns a failure result."""
        cmd = GuardCommand()
        result = cmd.execute("create", name=None)
        assert result.ok is False
        assert "name" in result.message.lower()

    def test_agent_show_missing_name_returns_error(self):
        """Agent show without --name returns a failure result."""
        cmd = AgentCommand()
        result = cmd.execute("show", name=None)
        assert result.ok is False
        assert "name" in result.message.lower()

    def test_guard_show_missing_name_returns_error(self):
        """Guard show without a name returns a failure result."""
        cmd = GuardCommand()
        result = cmd.execute("show", name=None)
        assert result.ok is False
        assert "name" in result.message.lower()

    def test_agent_delete_missing_name_returns_error(self):
        """Agent delete without --name returns a failure result."""
        cmd = AgentCommand()
        result = cmd.execute("delete", name=None)
        assert result.ok is False
        assert "name" in result.message.lower()

    def test_guard_delete_missing_name_returns_error(self):
        """Guard delete without a name returns a failure result."""
        cmd = GuardCommand()
        result = cmd.execute("delete", name=None)
        assert result.ok is False
        assert "name" in result.message.lower()

    def test_agent_create_missing_target_returns_error(self):
        """Agent create without --target returns a failure result."""
        cmd = AgentCommand()
        result = cmd.execute("create", name="my-agent", target=None)
        assert result.ok is False
        assert "target" in result.message.lower()

    def test_agent_unknown_subcommand_returns_error(self):
        """Agent with unknown subcommand returns a failure result."""
        cmd = AgentCommand()
        result = cmd.execute("unknown-cmd")
        assert result.ok is False

    def test_guard_unknown_subcommand_returns_error(self):
        """Guard with unknown subcommand returns a failure result."""
        cmd = GuardCommand()
        result = cmd.execute("unknown-cmd")
        assert result.ok is False

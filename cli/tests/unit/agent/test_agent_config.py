"""
Unit tests for lib/agent/config.py

Tests AgentConfig, SafetyConfig, and RestrictionsConfig dataclasses
and their YAML serialization.
"""

import pytest
import tempfile
from pathlib import Path

from lib.agent.config import (
    AgentConfig,
    SafetyConfig,
    RestrictionsConfig,
    AGENTS_DIR,
)


class TestSafetyConfig:
    """Tests for SafetyConfig dataclass."""

    def test_default_values(self):
        """Test default safety configuration values."""
        config = SafetyConfig()

        assert config.read_only is True
        assert config.max_rows == 1000
        assert config.timeout_seconds == 30

    def test_custom_values(self):
        """Test custom safety configuration values."""
        config = SafetyConfig(
            read_only=False,
            max_rows=500,
            timeout_seconds=60,
        )

        assert config.read_only is False
        assert config.max_rows == 500
        assert config.timeout_seconds == 60

    def test_to_dict(self):
        """Test serialization to dictionary."""
        config = SafetyConfig(max_rows=100, timeout_seconds=15)
        result = config.to_dict()

        assert result == {
            "read_only": True,
            "max_rows": 100,
            "timeout_seconds": 15,
        }

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "read_only": False,
            "max_rows": 200,
            "timeout_seconds": 45,
        }
        config = SafetyConfig.from_dict(data)

        assert config.read_only is False
        assert config.max_rows == 200
        assert config.timeout_seconds == 45

    def test_from_dict_with_defaults(self):
        """Test deserialization with missing fields uses defaults."""
        config = SafetyConfig.from_dict({})

        assert config.read_only is True
        assert config.max_rows == 1000
        assert config.timeout_seconds == 30


class TestRestrictionsConfig:
    """Tests for RestrictionsConfig dataclass."""

    def test_default_values(self):
        """Test default restrictions are None."""
        config = RestrictionsConfig()

        assert config.allowed_tables is None
        assert config.denied_columns is None
        assert config.masked_columns is None

    def test_with_restrictions(self):
        """Test restrictions with values."""
        config = RestrictionsConfig(
            allowed_tables=["users", "orders"],
            denied_columns=["users.ssn", "users.password"],
            masked_columns={"users.email": "****@****.com"},
        )

        assert config.allowed_tables == ["users", "orders"]
        assert config.denied_columns == ["users.ssn", "users.password"]
        assert config.masked_columns == {"users.email": "****@****.com"}

    def test_to_dict_empty(self):
        """Test serialization with no restrictions returns empty dict."""
        config = RestrictionsConfig()
        result = config.to_dict()

        assert result == {}

    def test_to_dict_with_values(self):
        """Test serialization with restrictions."""
        config = RestrictionsConfig(
            denied_columns=["sensitive.col"],
            masked_columns={"pii.email": "***"},
        )
        result = config.to_dict()

        assert result == {
            "denied_columns": ["sensitive.col"],
            "masked_columns": {"pii.email": "***"},
        }

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "allowed_tables": ["public.users"],
            "denied_columns": ["*.password"],
        }
        config = RestrictionsConfig.from_dict(data)

        assert config.allowed_tables == ["public.users"]
        assert config.denied_columns == ["*.password"]
        assert config.masked_columns is None


class TestAgentConfig:
    """Tests for AgentConfig dataclass."""

    def test_minimal_config(self):
        """Test creating agent with minimal required fields."""
        config = AgentConfig(name="test-agent", target="prod-db")

        assert config.name == "test-agent"
        assert config.target == "prod-db"
        assert config.description == ""
        assert config.created_at is not None
        assert isinstance(config.safety, SafetyConfig)
        assert isinstance(config.restrictions, RestrictionsConfig)

    def test_full_config(self):
        """Test creating agent with all fields."""
        config = AgentConfig(
            name="sales-agent",
            target="sales-db",
            description="Sales data agent",
            created_at="2025-01-01T00:00:00Z",
            safety=SafetyConfig(max_rows=500),
            restrictions=RestrictionsConfig(denied_columns=["*.ssn"]),
            semantic_layer="/path/to/layer.yaml",
        )

        assert config.name == "sales-agent"
        assert config.target == "sales-db"
        assert config.description == "Sales data agent"
        assert config.created_at == "2025-01-01T00:00:00Z"
        assert config.safety.max_rows == 500
        assert config.restrictions.denied_columns == ["*.ssn"]
        assert config.semantic_layer == "/path/to/layer.yaml"

    def test_to_dict(self):
        """Test serialization to dictionary."""
        config = AgentConfig(
            name="test",
            target="db",
            description="Test agent",
            created_at="2025-01-01T00:00:00Z",
        )
        result = config.to_dict()

        assert result["name"] == "test"
        assert result["target"] == "db"
        assert result["description"] == "Test agent"
        assert result["created_at"] == "2025-01-01T00:00:00Z"
        assert "safety" in result
        assert result["safety"]["read_only"] is True

    def test_to_dict_without_optional_fields(self):
        """Test serialization omits empty optional fields."""
        config = AgentConfig(
            name="test",
            target="db",
            created_at="2025-01-01T00:00:00Z",
        )
        result = config.to_dict()

        # Description is empty string, not included
        assert "description" not in result or result.get("description") == ""
        # Restrictions should be empty dict (omitted from top level)
        assert "restrictions" not in result
        # Semantic layer not set
        assert "semantic_layer" not in result

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "name": "my-agent",
            "target": "my-db",
            "description": "My agent",
            "created_at": "2025-01-01T00:00:00Z",
            "safety": {"max_rows": 100},
            "restrictions": {"denied_columns": ["*.password"]},
        }
        config = AgentConfig.from_dict(data)

        assert config.name == "my-agent"
        assert config.target == "my-db"
        assert config.description == "My agent"
        assert config.safety.max_rows == 100
        assert config.restrictions.denied_columns == ["*.password"]

    def test_save_and_load(self):
        """Test saving and loading from YAML file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test-agent.yaml"

            # Create and save
            config = AgentConfig(
                name="test-agent",
                target="test-db",
                description="Test description",
            )
            config.save(path)

            # Verify file exists
            assert path.exists()

            # Load and verify
            loaded = AgentConfig.load(path)
            assert loaded.name == config.name
            assert loaded.target == config.target
            assert loaded.description == config.description
            assert loaded.safety.max_rows == config.safety.max_rows

    def test_save_creates_directory(self):
        """Test saving creates parent directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "nested" / "dir" / "agent.yaml"

            config = AgentConfig(name="test", target="db")
            config.save(path)

            assert path.exists()

    def test_roundtrip_with_restrictions(self):
        """Test full roundtrip with restrictions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "agent.yaml"

            config = AgentConfig(
                name="restricted",
                target="db",
                restrictions=RestrictionsConfig(
                    allowed_tables=["users", "orders"],
                    denied_columns=["*.ssn", "*.password"],
                    masked_columns={"users.email": "***@***.com"},
                ),
            )
            config.save(path)

            loaded = AgentConfig.load(path)
            assert loaded.restrictions.allowed_tables == ["users", "orders"]
            assert loaded.restrictions.denied_columns == ["*.ssn", "*.password"]
            assert loaded.restrictions.masked_columns == {"users.email": "***@***.com"}

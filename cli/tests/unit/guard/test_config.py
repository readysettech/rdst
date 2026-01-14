"""Tests for GuardConfig and related dataclasses."""

import pytest
from pathlib import Path
import tempfile
import yaml

from lib.guard.config import (
    GuardConfig,
    MaskingConfig,
    RestrictionsConfig,
    GuardsConfig,
    LimitsConfig,
)


class TestMaskingConfig:
    """Test MaskingConfig dataclass."""

    def test_default_values(self):
        """Default masking config should have empty patterns."""
        config = MaskingConfig()
        assert config.patterns == {}

    def test_with_patterns(self):
        """Should store masking patterns."""
        config = MaskingConfig(patterns={"*.email": "email", "*.ssn": "redact"})
        assert config.patterns == {"*.email": "email", "*.ssn": "redact"}

    def test_to_dict(self):
        """Should convert to dict (patterns only)."""
        config = MaskingConfig(patterns={"*.email": "email"})
        # to_dict returns just the patterns dict
        assert config.to_dict() == {"*.email": "email"}

    def test_from_dict(self):
        """Should create from dict correctly."""
        data = {"*.phone": "partial:4"}
        config = MaskingConfig.from_dict(data)
        assert config.patterns == {"*.phone": "partial:4"}


class TestRestrictionsConfig:
    """Test RestrictionsConfig dataclass."""

    def test_default_values(self):
        """Default restrictions config should have no restrictions."""
        config = RestrictionsConfig()
        assert config.denied_columns is None
        assert config.allowed_tables is None

    def test_with_restrictions(self):
        """Should store restrictions correctly."""
        config = RestrictionsConfig(
            denied_columns=["*.password"],
            allowed_tables=["users", "orders"],
        )
        assert config.denied_columns == ["*.password"]
        assert config.allowed_tables == ["users", "orders"]

    def test_to_dict(self):
        """Should convert to dict, omitting None values."""
        config = RestrictionsConfig(denied_columns=["*.secret"])
        d = config.to_dict()
        assert d == {"denied_columns": ["*.secret"]}
        assert "allowed_tables" not in d

    def test_from_dict(self):
        """Should create from dict correctly."""
        data = {"allowed_tables": ["orders"]}
        config = RestrictionsConfig.from_dict(data)
        assert config.allowed_tables == ["orders"]
        assert config.denied_columns is None


class TestGuardsConfig:
    """Test GuardsConfig dataclass."""

    def test_default_values(self):
        """Default guards config should have all guards disabled."""
        config = GuardsConfig()
        assert config.require_where is False
        assert config.require_limit is False
        assert config.no_select_star is False
        assert config.max_tables is None
        assert config.cost_limit is None

    def test_with_guards(self):
        """Should store guard settings correctly."""
        config = GuardsConfig(
            require_where=True,
            require_limit=True,
            max_tables=5,
            cost_limit=10000,
        )
        assert config.require_where is True
        assert config.require_limit is True
        assert config.max_tables == 5
        assert config.cost_limit == 10000

    def test_to_dict_only_truthy(self):
        """Should only include truthy values in dict."""
        config = GuardsConfig(require_where=True)
        d = config.to_dict()
        # Only truthy values are included
        assert d == {"require_where": True}
        assert "require_limit" not in d

    def test_from_dict(self):
        """Should create from dict correctly."""
        data = {"require_where": True, "cost_limit": 5000}
        config = GuardsConfig.from_dict(data)
        assert config.require_where is True
        assert config.cost_limit == 5000


class TestLimitsConfig:
    """Test LimitsConfig dataclass."""

    def test_default_values(self):
        """Default limits config should have sensible defaults."""
        config = LimitsConfig()
        assert config.max_rows == 1000
        assert config.timeout_seconds == 30

    def test_with_limits(self):
        """Should store limit settings correctly."""
        config = LimitsConfig(max_rows=500, timeout_seconds=60)
        assert config.max_rows == 500
        assert config.timeout_seconds == 60


class TestGuardConfig:
    """Test GuardConfig dataclass."""

    def test_minimal_creation(self):
        """Should create guard with just a name."""
        config = GuardConfig(name="test-guard")
        assert config.name == "test-guard"
        assert config.description == ""
        assert isinstance(config.masking, MaskingConfig)
        assert isinstance(config.restrictions, RestrictionsConfig)
        assert isinstance(config.guards, GuardsConfig)
        assert isinstance(config.limits, LimitsConfig)

    def test_full_creation(self):
        """Should create guard with all options."""
        config = GuardConfig(
            name="pii-safe",
            description="PII protection guard",
            masking=MaskingConfig(patterns={"*.email": "email"}),
            restrictions=RestrictionsConfig(denied_columns=["*.password"]),
            guards=GuardsConfig(require_where=True),
            limits=LimitsConfig(max_rows=500),
        )
        assert config.name == "pii-safe"
        assert config.description == "PII protection guard"
        assert config.masking.patterns == {"*.email": "email"}
        assert config.restrictions.denied_columns == ["*.password"]
        assert config.guards.require_where is True
        assert config.limits.max_rows == 500

    def test_to_dict(self):
        """Should convert to dict for YAML serialization."""
        config = GuardConfig(
            name="test",
            description="Test guard",
            masking=MaskingConfig(patterns={"*.email": "email"}),
            guards=GuardsConfig(require_where=True),
        )
        d = config.to_dict()
        assert d["name"] == "test"
        assert d["description"] == "Test guard"
        assert d["masking"] == {"*.email": "email"}
        assert d["guards"] == {"require_where": True}
        assert "created_at" in d
        assert "limits" in d

    def test_from_dict(self):
        """Should create from dict correctly."""
        data = {
            "name": "loaded-guard",
            "description": "Loaded from dict",
            "masking": {"*.ssn": "redact"},
            "guards": {"require_limit": True},
            "limits": {"max_rows": 100},
        }
        config = GuardConfig.from_dict(data)
        assert config.name == "loaded-guard"
        assert config.description == "Loaded from dict"
        assert config.masking.patterns == {"*.ssn": "redact"}
        assert config.guards.require_limit is True
        assert config.limits.max_rows == 100

    def test_save_and_load(self):
        """Should save and load from YAML file."""
        config = GuardConfig(
            name="file-test",
            description="Test save/load",
            masking=MaskingConfig(patterns={"*.email": "email"}),
            guards=GuardsConfig(require_where=True),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test-guard.yaml"
            config.save(path)

            # Verify file exists
            assert path.exists()

            # Load and verify
            loaded = GuardConfig.load(path)
            assert loaded.name == "file-test"
            assert loaded.description == "Test save/load"
            assert loaded.masking.patterns == {"*.email": "email"}
            assert loaded.guards.require_where is True

    def test_yaml_format(self):
        """YAML output should be human-readable."""
        config = GuardConfig(
            name="yaml-test",
            description="Test YAML format",
            masking=MaskingConfig(patterns={"*.email": "email"}),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.yaml"
            config.save(path)

            with open(path) as f:
                content = f.read()

            # Check it's valid YAML
            data = yaml.safe_load(content)
            assert data["name"] == "yaml-test"

            # Check it's human-readable (not flow style)
            assert "{" not in content

    def test_has_masking(self):
        """Should correctly report masking presence."""
        config1 = GuardConfig(name="no-mask")
        assert config1.has_masking() is False

        config2 = GuardConfig(
            name="with-mask",
            masking=MaskingConfig(patterns={"*.email": "email"}),
        )
        assert config2.has_masking() is True

    def test_has_guards(self):
        """Should correctly report guard presence."""
        config1 = GuardConfig(name="no-guards")
        assert config1.has_guards() is False

        config2 = GuardConfig(
            name="with-guards",
            guards=GuardsConfig(require_where=True),
        )
        assert config2.has_guards() is True

    def test_has_restrictions(self):
        """Should correctly report restrictions presence."""
        config1 = GuardConfig(name="no-restrictions")
        assert config1.has_restrictions() is False

        config2 = GuardConfig(
            name="with-restrictions",
            restrictions=RestrictionsConfig(denied_columns=["password"]),
        )
        assert config2.has_restrictions() is True

"""
Unit tests for CLI modules.

Tests output_formatter, rdst_cli (TargetsConfig), and related CLI utilities.
"""

import pytest
import importlib.util
import sys
from pathlib import Path

# Import module directly to avoid package __init__.py issues
def _import_module_directly(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

_lib_path = Path(__file__).parent.parent.parent / "lib"
output_formatter = _import_module_directly("output_formatter", _lib_path / "cli" / "output_formatter.py")
rdst_cli = _import_module_directly("rdst_cli", _lib_path / "cli" / "rdst_cli.py")

# Import functions
format_analyze_output = output_formatter.format_analyze_output
_wrap_text = output_formatter._wrap_text
_format_header = output_formatter._format_header
_divider = output_formatter._divider
_format_query = output_formatter._format_query
_format_performance_summary = output_formatter._format_performance_summary

TargetsConfig = rdst_cli.TargetsConfig
RdstResult = rdst_cli.RdstResult
normalize_db_type = rdst_cli.normalize_db_type
default_port_for = rdst_cli.default_port_for


class TestWrapText:
    """Tests for _wrap_text function."""

    def test_empty_string(self):
        """Test wrapping empty string."""
        result = _wrap_text("")
        assert result == []

    def test_short_text_no_wrap(self):
        """Test text shorter than width is not wrapped."""
        result = _wrap_text("Hello world", width=100)
        assert len(result) == 1
        assert result[0] == "Hello world"

    def test_long_text_wraps(self):
        """Test text longer than width is wrapped."""
        text = "This is a very long line that should be wrapped because it exceeds the width"
        result = _wrap_text(text, width=30)
        assert len(result) > 1
        for line in result:
            assert len(line) <= 30

    def test_indent_applied(self):
        """Test initial indent is applied."""
        result = _wrap_text("Hello world", width=100, indent="  ")
        assert result[0] == "  Hello world"

    def test_subsequent_indent(self):
        """Test subsequent indent on wrapped lines."""
        text = "This is a very long line that should be wrapped multiple times"
        result = _wrap_text(text, width=30, indent="• ", subsequent_indent="  ")
        assert result[0].startswith("• ")
        if len(result) > 1:
            assert result[1].startswith("  ")


class TestDivider:
    """Tests for _divider function."""

    def test_divider_length(self):
        """Test divider has correct length."""
        result = _divider()
        assert len(result) == 65
        assert result == "━" * 65


class TestFormatQuery:
    """Tests for _format_query function."""

    def test_single_line_query(self):
        """Test formatting single line query."""
        result = _format_query("SELECT * FROM users")
        assert result[0] == "Query:"
        assert "  SELECT * FROM users" in result

    def test_multiline_query(self):
        """Test formatting multiline query."""
        query = "SELECT *\nFROM users\nWHERE id = 1"
        result = _format_query(query)
        assert result[0] == "Query:"
        assert "  SELECT *" in result
        assert "  FROM users" in result
        assert "  WHERE id = 1" in result


class TestFormatHeader:
    """Tests for _format_header function."""

    def test_header_with_metadata(self):
        """Test header formatting with full metadata."""
        formatted_output = {
            "metadata": {
                "target": "test-target",
                "database_engine": "postgresql",
                "analysis_id": "abc123456789xyz"
            }
        }
        result = _format_header(formatted_output)

        # Should have box borders
        assert any("╭" in line for line in result)
        assert any("╰" in line for line in result)
        assert any("RDST Query Analysis" in line for line in result)
        assert any("test-target" in line for line in result)
        assert any("POSTGRESQL" in line for line in result)

    def test_header_with_missing_metadata(self):
        """Test header formatting with missing metadata."""
        formatted_output = {"metadata": {}}
        result = _format_header(formatted_output)

        # Should still produce valid output
        assert len(result) > 0
        assert any("RDST Query Analysis" in line for line in result)


class TestFormatPerformanceSummary:
    """Tests for _format_performance_summary function."""

    def test_basic_summary(self):
        """Test basic performance summary formatting."""
        summary = {
            "execution_time_ms": 45.5,
            "execution_time_rating": "fast",
            "overall_rating": "good",
            "efficiency_score": 85,
            "rows_processed": {
                "examined": 1000,
                "returned": 10
            },
            "cost_estimate": 500.0,
            "primary_concerns": []
        }
        perf_metrics = {}

        result = _format_performance_summary(summary, perf_metrics)

        assert any("PERFORMANCE SUMMARY" in line for line in result)
        assert any("45.5ms" in line for line in result)
        assert any("1,000" in line for line in result)  # Rows examined
        assert any("10" in line for line in result)  # Rows returned

    def test_summary_with_concerns(self):
        """Test performance summary with primary concerns."""
        summary = {
            "execution_time_ms": 1000.0,
            "execution_time_rating": "slow",
            "overall_rating": "poor",
            "efficiency_score": 30,
            "rows_processed": {"examined": 100000, "returned": 100},
            "primary_concerns": ["Full table scan detected", "Missing index"]
        }

        result = _format_performance_summary(summary, {})

        assert any("Primary Concerns" in line for line in result)
        assert any("Full table scan" in line for line in result)


class TestFormatAnalyzeOutput:
    """Tests for format_analyze_output function."""

    def test_empty_workflow_result(self):
        """Test handling of empty workflow result."""
        result = format_analyze_output({})
        assert isinstance(result, str)

    def test_basic_workflow_result(self):
        """Test formatting basic workflow result."""
        workflow_result = {
            "FormatFinalResults": {
                "success": True,
                "metadata": {
                    "target": "test",
                    "database_engine": "postgresql",
                    "analysis_id": "abc123",
                    "query": "SELECT * FROM users"
                },
                "analysis_summary": {
                    "execution_time_ms": 10.0,
                    "overall_rating": "good",
                    "efficiency_score": 90,
                    "rows_processed": {"examined": 100, "returned": 10}
                }
            }
        }

        result = format_analyze_output(workflow_result)
        assert "RDST Query Analysis" in result
        assert "SELECT * FROM users" in result

    def test_fallback_on_format_failure(self):
        """Test fallback formatting when FormatFinalResults fails."""
        workflow_result = {
            "target": "test",
            "query": "SELECT * FROM users",
            "explain_results": {
                "success": True,
                "database_engine": "postgresql",
                "execution_time_ms": 20.0,
                "rows_examined": 500,
                "rows_returned": 25
            }
        }

        result = format_analyze_output(workflow_result)
        assert isinstance(result, str)


class TestTargetsConfig:
    """Tests for TargetsConfig class."""

    @pytest.fixture
    def temp_config_file(self, temp_dir):
        """Create a temporary config file path."""
        return temp_dir / "config.toml"

    def test_init_default_path(self):
        """Test default config path is set correctly."""
        cfg = TargetsConfig()
        assert cfg.path == Path.home() / ".rdst" / "config.toml"

    def test_init_custom_path(self, temp_config_file):
        """Test custom config path."""
        cfg = TargetsConfig(path=str(temp_config_file))
        assert cfg.path == temp_config_file

    def test_load_nonexistent_file(self, temp_config_file):
        """Test loading when file doesn't exist."""
        cfg = TargetsConfig(path=str(temp_config_file))
        cfg.load()

        assert cfg._data == {"targets": {}, "default": None, "init": {"completed": False}, "llm": {}}

    def test_save_and_load(self, temp_config_file):
        """Test saving and loading configuration."""
        cfg = TargetsConfig(path=str(temp_config_file))
        cfg.load()
        cfg.upsert("test-target", {
            "host": "localhost",
            "port": 5432,
            "database": "testdb",
            "user": "testuser",
            "engine": "postgresql"
        })
        cfg.save()

        # Load in a new instance
        cfg2 = TargetsConfig(path=str(temp_config_file))
        cfg2.load()

        assert "test-target" in cfg2.list_targets()
        assert cfg2.get("test-target")["host"] == "localhost"

    def test_list_targets(self, temp_config_file):
        """Test listing targets."""
        cfg = TargetsConfig(path=str(temp_config_file))
        cfg.load()
        cfg.upsert("alpha", {"host": "a"})
        cfg.upsert("beta", {"host": "b"})
        cfg.upsert("gamma", {"host": "c"})

        targets = cfg.list_targets()
        assert targets == ["alpha", "beta", "gamma"]  # Sorted

    def test_upsert_target(self, temp_config_file):
        """Test upserting a target."""
        cfg = TargetsConfig(path=str(temp_config_file))
        cfg.load()
        cfg.upsert("new-target", {"host": "newhost"})

        assert cfg.get("new-target") == {"host": "newhost"}

    def test_remove_target(self, temp_config_file):
        """Test removing a target."""
        cfg = TargetsConfig(path=str(temp_config_file))
        cfg.load()
        cfg.upsert("to-remove", {"host": "remove-me"})

        result = cfg.remove("to-remove")
        assert result is True
        assert cfg.get("to-remove") is None

    def test_remove_nonexistent_target(self, temp_config_file):
        """Test removing a target that doesn't exist."""
        cfg = TargetsConfig(path=str(temp_config_file))
        cfg.load()

        result = cfg.remove("nonexistent")
        assert result is False

    def test_set_and_get_default(self, temp_config_file):
        """Test setting and getting default target."""
        cfg = TargetsConfig(path=str(temp_config_file))
        cfg.load()
        cfg.upsert("primary", {"host": "primary-host"})
        cfg.set_default("primary")

        assert cfg.get_default() == "primary"

    def test_remove_target_clears_default(self, temp_config_file):
        """Test that removing default target clears the default."""
        cfg = TargetsConfig(path=str(temp_config_file))
        cfg.load()
        cfg.upsert("default-target", {"host": "host"})
        cfg.set_default("default-target")
        cfg.remove("default-target")

        assert cfg.get_default() is None

    def test_init_completed_tracking(self, temp_config_file):
        """Test init completion tracking."""
        cfg = TargetsConfig(path=str(temp_config_file))
        cfg.load()

        assert cfg.is_init_completed() is False

        cfg.mark_init_completed(version="1.0.0")

        assert cfg.is_init_completed() is True

    def test_llm_config_methods(self, temp_config_file):
        """Test LLM configuration methods."""
        cfg = TargetsConfig(path=str(temp_config_file))
        cfg.load()

        # Set LLM config
        cfg.set_llm_config({"provider": "openai", "model": "gpt-4"})

        assert cfg.get_llm_config() == {"provider": "openai", "model": "gpt-4"}
        assert cfg.get_llm_provider() == "openai"
        assert cfg.get_llm_model() == "gpt-4"

    def test_set_llm_provider(self, temp_config_file):
        """Test setting LLM provider with options."""
        cfg = TargetsConfig(path=str(temp_config_file))
        cfg.load()

        cfg.set_llm_provider("lmstudio", base_url="http://localhost:1234", model="qwen-2.5")

        assert cfg.get_llm_provider() == "lmstudio"
        assert cfg.get_llm_base_url() == "http://localhost:1234"
        assert cfg.get_llm_model() == "qwen-2.5"


class TestRdstResult:
    """Tests for RdstResult dataclass."""

    def test_truthy_result(self):
        """Test that successful result is truthy."""
        result = RdstResult(ok=True, message="Success")
        assert result
        assert bool(result) is True

    def test_falsy_result(self):
        """Test that failed result is falsy."""
        result = RdstResult(ok=False, message="Failed")
        assert not result
        assert bool(result) is False

    def test_with_data(self):
        """Test result with data."""
        result = RdstResult(ok=True, message="Done", data={"key": "value"})
        assert result.data == {"key": "value"}


class TestNormalizeDbType:
    """Tests for normalize_db_type function."""

    def test_postgresql_variants(self):
        """Test PostgreSQL type normalization."""
        assert normalize_db_type("postgres") == "postgresql"
        assert normalize_db_type("postgresql") == "postgresql"
        assert normalize_db_type("psql") == "postgresql"
        assert normalize_db_type("POSTGRES") == "postgresql"

    def test_mysql_variants(self):
        """Test MySQL type normalization."""
        assert normalize_db_type("mysql") == "mysql"
        assert normalize_db_type("mariadb") == "mysql"
        assert normalize_db_type("MYSQL") == "mysql"

    def test_none_input(self):
        """Test None input returns None."""
        assert normalize_db_type(None) is None

    def test_unknown_type(self):
        """Test unknown type is returned as-is (lowercase)."""
        assert normalize_db_type("oracle") == "oracle"


class TestDefaultPortFor:
    """Tests for default_port_for function."""

    def test_postgresql_default_port(self):
        """Test PostgreSQL default port."""
        assert default_port_for("postgresql") == 5432
        assert default_port_for("postgres") == 5432

    def test_mysql_default_port(self):
        """Test MySQL default port."""
        assert default_port_for("mysql") == 3306
        assert default_port_for("mariadb") == 3306

    def test_unknown_defaults_to_mysql(self):
        """Test unknown type defaults to MySQL port."""
        assert default_port_for("unknown") == 3306

    def test_none_defaults_to_mysql(self):
        """Test None defaults to MySQL port."""
        assert default_port_for(None) == 3306

"""
Unit tests for output_formatter engine display (rdst-2vr.13).

Tests that the analyze header displays the correct database engine,
falling back to target_config when explain_results lacks database_engine.
"""

from lib.cli.output_formatter import format_analyze_output


class TestEngineDisplay:
    """Engine must never show 'Unknown' when target_config has engine."""

    def _make_workflow_result(self, explain_engine="", target_engine="mysql"):
        """Build a minimal workflow_result dict for testing."""
        return {
            "success": True,
            "target": "myimdb",
            "query": "SELECT 1",
            "normalized_query": "SELECT 1",
            "explain_results": {
                "success": True,
                "database_engine": explain_engine,
                "execution_time_ms": 1.0,
                "explain_plan": "mock plan",
            },
            "llm_analysis": {},
            "rewrite_test_results": {},
            "target_config": {"engine": target_engine},
            # No FormatFinalResults → triggers _format_from_raw_workflow path
        }

    def test_engine_from_explain_results(self):
        """When explain_results has database_engine, use it."""
        result = self._make_workflow_result(explain_engine="mysql")
        output = format_analyze_output(result)
        assert "MYSQL" in output
        assert "Unknown" not in output

    def test_engine_fallback_to_target_config(self):
        """When explain_results has empty database_engine, fall back to target_config."""
        result = self._make_workflow_result(explain_engine="", target_engine="mysql")
        output = format_analyze_output(result)
        assert "MYSQL" in output
        assert "Unknown" not in output

    def test_engine_fallback_postgresql(self):
        """Fallback works for PostgreSQL targets too."""
        result = self._make_workflow_result(explain_engine="", target_engine="postgresql")
        output = format_analyze_output(result)
        assert "POSTGRESQL" in output
        assert "Unknown" not in output

    def test_format_final_results_path(self):
        """When FormatFinalResults is present but has empty engine, fall back."""
        result = self._make_workflow_result(explain_engine="", target_engine="mysql")
        result["FormatFinalResults"] = {
            "success": True,
            "metadata": {
                "query": "SELECT 1",
                "target": "myimdb",
                "database_engine": "",
                "analysis_id": "abc123",
            },
            "analysis_summary": {},
            "performance_metrics": {},
        }
        output = format_analyze_output(result)
        assert "MYSQL" in output
        assert "Unknown" not in output

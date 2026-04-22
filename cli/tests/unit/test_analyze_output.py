"""
Unit tests for analyze bug fixes.

Covers:
- _ensure_dict() JSON string parsing in workflow_integration and output_formatter
- Rewrite suggestions populated from llm_analysis
- --fast mode ExplainCompleteEvent rendering (EXPLAIN vs EXPLAIN ANALYZE)
- --duration not capped by default --count
- Count display shows user count, not internal total
- Inline SQL removed from query run help
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from features.analyze.cli.output_formatter import (
    _ensure_dict as formatter_ensure_dict,
)
from features.analyze.functions.workflow_integration import (
    _ensure_dict as workflow_ensure_dict,
    format_analysis_output,
)
from features.analyze.cli.renderer import AnalyzeRenderer
from features.analyze.events import ExplainCompleteEvent


# =========================================================================
# 1. _ensure_dict handles JSON strings
# =========================================================================


class TestEnsureDictJsonStrings:
    """_ensure_dict() should parse JSON strings into dicts."""

    def test_workflow_ensure_dict_parses_json_string(self):
        """workflow_integration._ensure_dict parses a JSON string into a dict."""
        json_str = json.dumps({"success": True, "data": [1, 2, 3]})
        result = workflow_ensure_dict(json_str)
        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["data"] == [1, 2, 3]

    def test_formatter_ensure_dict_parses_json_string(self):
        """output_formatter._ensure_dict parses a JSON string into a dict."""
        json_str = json.dumps({"key": "value", "nested": {"a": 1}})
        result = formatter_ensure_dict(json_str)
        assert isinstance(result, dict)
        assert result["key"] == "value"
        assert result["nested"]["a"] == 1

    def test_ensure_dict_returns_dict_unchanged(self):
        """A plain dict is returned as-is."""
        d = {"already": "a dict"}
        assert workflow_ensure_dict(d) is d

    def test_ensure_dict_returns_empty_for_invalid_json(self):
        """Invalid JSON string returns empty dict."""
        assert workflow_ensure_dict("not json at all") == {}

    def test_ensure_dict_returns_empty_for_json_array(self):
        """A JSON array (not dict) returns empty dict."""
        assert workflow_ensure_dict("[1, 2, 3]") == {}

    def test_ensure_dict_returns_empty_for_empty_string(self):
        """Empty or whitespace-only string returns empty dict."""
        assert workflow_ensure_dict("") == {}
        assert workflow_ensure_dict("   ") == {}

    def test_ensure_dict_returns_empty_for_none(self):
        """None returns empty dict."""
        assert workflow_ensure_dict(None) == {}


# =========================================================================
# 2. Rewrite suggestions populated from llm_analysis
# =========================================================================


class TestRewriteSuggestionsFromLlmAnalysis:
    """When optimization_suggestions is empty, format_analysis_output should
    populate it from llm_analysis rewrite_suggestions."""

    def test_rewrite_suggestions_populated_from_llm(self):
        """Rewrite suggestions from llm_analysis appear in formatted output."""
        rewrite = {
            "suggestion_id": "r1",
            "type": "index_hint",
            "priority": "high",
            "confidence": "high",
            "rewritten_sql": "SELECT id FROM users USE INDEX (idx_users_email) WHERE email = 'a@b.com'",
            "explanation": "Use index hint",
            "expected_improvement": "50%",
            "trade_offs": "none",
            "test_recommended": True,
        }
        result = format_analysis_output(
            explain_results={"success": True},
            llm_analysis={
                "success": True,
                "rewrite_suggestions": [rewrite],
                "index_recommendations": [],
                "caching_recommendations": {},
            },
            optimization_suggestions={},  # empty - should be populated
            query="SELECT id FROM users WHERE email = 'a@b.com'",
            target="mydb",
            analysis_id="abc123",
        )

        assert result["success"] is True
        recs = result["recommendations"]
        assert recs["available"] is True
        assert len(recs["query_rewrites"]) == 1
        assert recs["query_rewrites"][0]["sql"] == rewrite["rewritten_sql"]

    def test_no_rewrite_when_optimization_suggestions_present(self):
        """When optimization_suggestions already has success=True, llm_analysis
        rewrite_suggestions are not used (no double-population)."""
        result = format_analysis_output(
            explain_results={"success": True},
            llm_analysis={
                "success": True,
                "rewrite_suggestions": [{"suggestion_id": "should_not_appear"}],
            },
            optimization_suggestions={
                "success": True,
                "rewrite_suggestions": [],
                "index_suggestions": [],
                "caching_recommendations": {},
            },
            query="SELECT 1",
            target="mydb",
            analysis_id="abc",
        )
        recs = result["recommendations"]
        assert recs["available"] is True
        # The explicit empty list from optimization_suggestions should be used
        assert recs["query_rewrites"] == []


# =========================================================================
# 2b. Index impact field name fallback (estimated_impact vs estimated_benefit)
# =========================================================================


class TestIndexImpactFieldFallback:
    """When an index recommendation uses 'estimated_impact' instead of
    'estimated_benefit', the formatted output should still show the impact."""

    def test_workflow_format_index_uses_estimated_impact(self):
        """_format_index_suggestions should read estimated_impact when
        estimated_benefit is absent."""
        from features.analyze.functions.workflow_integration import _format_index_suggestions

        index = {
            "recommendation_id": "idx1",
            "table": "users",
            "index_type": "btree",
            "columns": ["email"],
            "sql_statement": "CREATE INDEX idx_users_email ON users(email);",
            "estimated_impact": "HIGH",
            "rationale": "Frequently filtered column",
        }
        result = _format_index_suggestions([index])
        assert len(result) == 1
        assert result[0]["expected_benefit"] == "HIGH", (
            f"expected_benefit should be 'HIGH' from estimated_impact fallback, "
            f"got: {result[0]['expected_benefit']!r}"
        )

    def test_output_formatter_index_recommendations_uses_estimated_impact(self):
        """_format_index_recommendations in output_formatter should render
        the impact when the index uses estimated_impact (not expected_benefit)."""
        from features.analyze.cli.output_formatter import _format_index_recommendations

        recommendations = {
            "available": True,
            "index_suggestions": [
                {
                    "type": "btree",
                    "table": "orders",
                    "columns": ["customer_id"],
                    "sql_statement": "CREATE INDEX idx_orders_cust ON orders(customer_id);",
                    "estimated_impact": "MEDIUM",
                    "rationale": "Used in joins",
                }
            ],
        }
        lines = _format_index_recommendations(recommendations)
        combined = "\n".join(lines)
        # The impact should appear in the output (not blank)
        assert "MEDIUM" in combined.upper(), (
            f"Expected 'MEDIUM' impact to appear in output, got:\n{combined}"
        )


# =========================================================================
# 3. --fast mode ExplainCompleteEvent renderer shows "EXPLAIN" not "EXPLAIN ANALYZE"
# =========================================================================


class TestFastModeExplainRendering:
    """When explain_analyze_skipped=True, renderer should display 'EXPLAIN'."""

    def test_explain_header_when_skipped(self):
        """Renderer prints 'EXPLAIN' when explain_analyze_skipped is True."""
        renderer = AnalyzeRenderer()
        renderer._console = Mock()

        event = ExplainCompleteEvent(
            type="explain_complete",
            success=True,
            database_engine="postgresql",
            execution_time_ms=2.5,
            rows_examined=100,
            rows_returned=10,
            cost_estimate=15.0,
            explain_analyze_skipped=True,
        )
        renderer.render(event)

        renderer._console.print.assert_called_once()
        printed_arg = renderer._console.print.call_args[0][0]
        # StatusLine stores its label; check the rendered text contains "EXPLAIN"
        # but not "EXPLAIN ANALYZE"
        from io import StringIO
        from rich.console import Console

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        console.print(printed_arg)
        output = buf.getvalue()
        assert "EXPLAIN" in output
        assert "EXPLAIN ANALYZE" not in output

    def test_explain_analyze_header_when_not_skipped(self):
        """Renderer prints 'EXPLAIN ANALYZE' when explain_analyze_skipped is False."""
        renderer = AnalyzeRenderer()
        renderer._console = Mock()

        event = ExplainCompleteEvent(
            type="explain_complete",
            success=True,
            database_engine="postgresql",
            execution_time_ms=12.0,
            rows_examined=500,
            rows_returned=50,
            cost_estimate=30.0,
            explain_analyze_skipped=False,
        )
        renderer.render(event)

        renderer._console.print.assert_called_once()
        printed_arg = renderer._console.print.call_args[0][0]

        from io import StringIO
        from rich.console import Console

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        console.print(printed_arg)
        output = buf.getvalue()
        assert "EXPLAIN ANALYZE" in output


# =========================================================================
# 4. --duration not capped by default --count
# =========================================================================


class TestDurationNotCappedByCount:
    """When --duration is set without explicit --count, count should remain
    None (not be set to a default like 1)."""

    def test_duration_only_count_stays_none(self):
        """With --duration but no --count, the run method should not cap count."""
        cmd = QueryCommand()

        entry = SimpleNamespace(tag="dur-test", hash="d" * 12, last_target="prod")
        target_cfg = {"engine": "postgresql", "host": "localhost", "port": 5432}

        cfg = Mock()
        cfg.load.return_value = None
        cfg.get_default.return_value = "prod"
        cfg.get.return_value = target_cfg

        captured_args = {}

        def fake_interval(queries, tc, stats, interval, stop, duration, count, *a, **k):
            captured_args["duration"] = duration
            captured_args["count"] = count
            # Simulate one run
            stats.record_execution("d" * 12, "dur-test", 1.0, True)
            stats.record_execution("d" * 12, "dur-test", 1.0, True)

        with (
            patch.object(cmd, "_resolve_queries", return_value=[(entry, "SELECT 1")]),
            patch.object(cmd, "_print_run_summary"),
            patch("shared.config.targets.create_targets_config", return_value=cfg),
            patch.object(cmd, "_run_interval", side_effect=fake_interval),
        ):
            result = cmd.run(queries=["dur-test"], duration=30, quiet=True)

        assert result.ok is True
        # count should remain None when only --duration is set
        assert captured_args["count"] is None
        assert captured_args["duration"] == 30


# =========================================================================
# 5. Count display shows user count, not internal total
# =========================================================================


class TestCountDisplayShowsUserCount:
    """The summary box should show the user's --count, not the inflated
    count that includes warm-up iterations."""

    def test_user_count_in_result_message(self):
        """With --count=5 and 1 query, the internal count is inflated to 6
        (for warm-up), but the result message should report measured count
        (excluding warm-up), not the inflated total."""
        from features.query_registry.cli.command import QueryCommand

        cmd = QueryCommand()

        entry = SimpleNamespace(tag="cnt-test", hash="c" * 12, last_target="prod")
        target_cfg = {"engine": "postgresql", "host": "localhost", "port": 5432}

        cfg = Mock()
        cfg.load.return_value = None
        cfg.get_default.return_value = "prod"
        cfg.get.return_value = target_cfg

        def fake_interval(queries, tc, stats, interval, stop, duration, count, *a, **k):
            # count passed to _run_interval should be 6 (5 user + 1 warmup)
            assert count == 6, f"Internal count should be 6 (5+1), got {count}"
            # Simulate: 1 warmup + 5 measured
            for _ in range(6):
                stats.record_execution("c" * 12, "cnt-test", 1.0, True)

        with (
            patch.object(cmd, "_resolve_queries", return_value=[(entry, "SELECT 1")]),
            patch.object(cmd, "_print_run_summary"),
            patch("shared.config.targets.create_targets_config", return_value=cfg),
            patch.object(cmd, "_run_interval", side_effect=fake_interval),
        ):
            result = cmd.run(queries=["cnt-test"], count=5, quiet=True)

        assert result.ok is True
        # Result message reports measured executions (total - warmup)
        assert "5 measured executions" in result.message
        # Result data should also reflect measured count
        assert result.data["total_executions"] == 5


# =========================================================================
# 6. Inline SQL removed from query run help
# =========================================================================


class TestInlineSqlRemovedFromRunHelp:
    """The help text for 'query run' should not contain 'inline SQL'."""

    def test_no_inline_sql_in_run_help(self):
        """Verify 'inline SQL' is not in the query run subcommand help text."""
        from shared.cli.parser_data import COMMANDS

        query_cmd = COMMANDS["query"]
        run_subcmd = None
        for sub in query_cmd.subcommand_defs:
            if sub.name == "run":
                run_subcmd = sub
                break

        assert run_subcmd is not None, "Expected 'run' subcommand in query command"

        # Check help text of all args in run subcommand
        all_help_text = run_subcmd.help
        for arg in run_subcmd.args:
            if hasattr(arg, "help") and arg.help:
                all_help_text += " " + arg.help

        assert "inline sql" not in all_help_text.lower(), (
            "Help text for 'query run' should not mention 'inline SQL'"
        )

    def test_no_inline_sql_in_queries_arg_help(self):
        """The 'queries' positional arg specifically should not mention inline SQL."""
        from shared.cli.parser_data import COMMANDS

        query_cmd = COMMANDS["query"]
        for sub in query_cmd.subcommand_defs:
            if sub.name == "run":
                for arg in sub.args:
                    if hasattr(arg, "name") and arg.name == "queries":
                        assert "inline" not in (arg.help or "").lower()
                        return
        pytest.fail("Could not find 'queries' arg in 'query run' subcommand")


# Import here to avoid issues if the import fails for unrelated reasons
from features.query_registry.cli.command import QueryCommand

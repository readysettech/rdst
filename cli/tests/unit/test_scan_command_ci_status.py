"""Regression tests for scan CI status normalization."""

import json
from unittest.mock import MagicMock, patch

import pytest

from features.scan.cli.command import ScanCommand


@pytest.mark.parametrize("output_json", [True, False])
def test_shallow_scan_passes_target_through_to_cache_key(monkeypatch, output_json):
    from features.analyze.functions import shallow_analysis
    from features.schema import schema_from_yaml
    from features.schema.inference_context import schema_cache_key

    schema = "Table: items\n  id integer"
    monkeypatch.setattr(schema_from_yaml, "collect_schema_from_yaml",
                        lambda **kwargs: {"success": True, "schema_info": schema})
    manager = MagicMock()
    manager.generate_response.return_value = {
        "response": json.dumps({
            "performance_assessment": {"risk_score": 90, "overall_rating": "good"},
            "structural_analysis": {}, "index_coverage": {},
            "optimization_opportunities": [], "rewrite_suggestions": [],
            "index_recommendations": [],
        }),
        "tokens_used": 10,
    }
    monkeypatch.setattr(shallow_analysis, "LLMManager", lambda: manager)
    command = ScanCommand()
    monkeypatch.setattr(command, "_detect_sql_dialect", lambda target: "postgresql")
    result = command._analyze_shallow_all_queries(
        queries=[{"sql": "SELECT id FROM items", "hash": "fixture"}],
        target="production", output_json=output_json, batch_size=1,
    )
    assert result["successful"] == 1, result
    assert manager.generate_response.call_args.kwargs["extra"]["_rdst_schema_cache_key"] == (
        schema_cache_key(schema, "postgresql", "production")
    )


def test_deep_analysis_all_failures_sets_ci_status_fail():
    """When deep analysis returns no scores, CI status should be tri-state compatible."""
    cmd = ScanCommand()

    queries = [
        {
            "hash": "abc12345",
            "file": "example.py",
            "function": "get_rows",
            "line": 10,
            "status": "sql",
            "sql": "SELECT 1",
        }
    ]

    with patch.object(
        cmd,
        "_analyze_single_query",
        return_value={
            "success": False,
            "hash": "abc12345",
            "error": "LLM unavailable",
        },
    ):
        result = cmd._analyze_all_queries(
            queries=queries,
            target="prod",
            output_json=True,
            warn_threshold=60,
            fail_threshold=40,
            batch_size=1,
        )

    assert result["successful"] == 0
    assert result["failed"] == 1
    assert result["ci_status"] == "fail"
    assert result["ci_exit_code"] == 1

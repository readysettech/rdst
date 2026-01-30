"""Regression tests for scan CI status normalization."""

from unittest.mock import patch

from lib.cli.scan_command import ScanCommand


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

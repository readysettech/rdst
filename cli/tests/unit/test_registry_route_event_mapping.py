"""Tests for registry route SSE event mapping."""

import json

from lib.api.routes.registry import _progress_to_sse


class _UnknownProgress:
    type = "mystery"
    elapsed_seconds = 0.0
    total_executions = 0
    total_successes = 0
    total_failures = 0
    qps = 0.0
    queries = []
    error = None


def test_progress_mapping_unknown_event_name_is_unknown():
    mapped = _progress_to_sse(_UnknownProgress())

    assert mapped["event"] == "unknown"
    payload = json.loads(mapped["data"])
    assert payload["type"] == "mystery"

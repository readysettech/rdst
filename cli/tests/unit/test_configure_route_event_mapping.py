"""Tests for configure route SSE mapping."""

import json

from features.configure.api.routes import _event_to_sse
from features.configure.events import ConfigureConnectionTestEvent


def test_connection_test_carries_query_capture():
    capture = {"available": False, "source": "pg_stat_statements", "instructions": ["x"]}
    mapped = _event_to_sse(
        ConfigureConnectionTestEvent(
            type="connection_test",
            target_name="shop",
            status="success",
            server_version="PostgreSQL 14.8",
            query_capture=capture,
        )
    )

    assert mapped["event"] == "connection_test"
    assert json.loads(mapped["data"])["query_capture"] == capture

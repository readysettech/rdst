"""Tests for registry route SSE event mapping."""

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import features.query_registry.api.routes as query_routes
from features.query_registry.api.routes import _progress_to_sse
from features.query_registry.discovery import QueryDiscoveryEvent
from shared.api.target_guard import TargetGuard, require_target


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


class _FakeCollector:
    def __init__(self, events: list[QueryDiscoveryEvent]):
        self.events = events
        self.after_cursor: object = "unset"

    async def subscribe(self, after_cursor=None):
        self.after_cursor = after_cursor
        for event in self.events:
            yield event


class _FakeCoordinator:
    def __init__(self, collector: _FakeCollector):
        self.collector = collector

    def collector_for(self, target: str) -> _FakeCollector:
        assert target == "demo"
        return self.collector


@pytest.mark.asyncio
async def test_discovery_stream_forwards_last_event_id_and_emits_seq_ids(
    monkeypatch,
):
    collector = _FakeCollector(
        [
            QueryDiscoveryEvent(
                7,
                "resync",
                {"reason": "cursor_not_retained", "latest_seq": 7, "state": {}},
            )
        ]
    )
    monkeypatch.setattr(query_routes, "query_discovery", _FakeCoordinator(collector))

    app = FastAPI()
    app.include_router(query_routes.router, prefix="/api")
    app.dependency_overrides[require_target] = lambda: TargetGuard(
        "demo", {}, "postgresql"
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        async with client.stream(
            "GET",
            "/api/query-registry/discovery/stream",
            headers={"Last-Event-ID": "5"},
        ) as response:
            assert response.status_code == 200
            lines = [line async for line in response.aiter_lines()]

    assert collector.after_cursor == 5
    # The stream opens by bounding the browser's reconnect backoff.
    assert lines[0] == f"retry: {query_routes.SSE_RETRY_MILLISECONDS}"
    assert query_routes.SSE_RETRY_MILLISECONDS == 5000
    assert "id: 7" in lines
    assert "event: resync" in lines
    data_line = next(line for line in lines if line.startswith("data:"))
    payload = json.loads(data_line.split(":", 1)[1])
    assert payload["cursor"] == 7
    assert payload["reason"] == "cursor_not_retained"
    assert payload["latest_seq"] == 7

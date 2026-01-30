#!/usr/bin/env python3
"""Integration tests for Top API endpoints."""

import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from lib.api.app import create_app
from lib.api.routes.target_guard import TargetGuard
from lib.services.types import (
    TopCompleteEvent,
    TopConnectedEvent,
    TopErrorEvent,
    TopQueriesEvent,
    TopQueryData,
    TopStatusEvent,
)


@pytest.fixture
def app():
    """Create FastAPI app for testing."""
    return create_app()


@pytest.fixture(autouse=True)
def _allow_target_password(monkeypatch):
    monkeypatch.setattr(
        "lib.api.routes.target_guard.ensure_target_password",
        lambda target=None: TargetGuard(
            target or "prod",
            {"engine": "postgresql", "password": "test-password"},
        ),
    )


async def _collect_sse_events(response):
    """Collect SSE events as {event, data} dicts."""
    events = []
    current_event = None

    async for line in response.aiter_lines():
        if line.startswith("event:"):
            current_event = line[6:].strip()
        elif line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                events.append({"event": current_event, "data": json.loads(payload)})

    return events


def _sample_query(query_hash="q_1"):
    return TopQueryData(
        query_hash=query_hash,
        query_text="SELECT * FROM users WHERE id = $1",
        normalized_query="SELECT * FROM users WHERE id = ?",
        freq=12,
        total_time="1.200s",
        avg_time="0.100s",
        pct_load="34.0%",
        max_duration_ms=120.5,
        current_instances=3,
        observation_count=19,
    )


@pytest.mark.asyncio
async def test_top_historical_json_success(app):
    """/api/top returns JSON snapshot in non-stream mode."""
    with patch("lib.api.routes.top.TopService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_get_top_queries(input_data, options_data):
            yield TopConnectedEvent(
                type="connected",
                target_name="prod",
                db_engine="postgresql",
                source="pg_stat",
            )
            yield TopCompleteEvent(
                type="complete",
                success=True,
                queries=[_sample_query()],
                source="pg_stat",
                newly_saved=1,
            )

        mock_service.get_top_queries = mock_get_top_queries

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/top?target=prod&limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["target"] == "prod"
    assert payload["engine"] == "postgresql"
    assert payload["source"] == "pg_stat"
    assert len(payload["queries"]) == 1
    assert payload["queries"][0]["query_hash"] == "q_1"
    assert payload["queries"][0]["max_duration_ms"] == 120.5
    assert payload["queries"][0]["current_instances_running"] == 3
    assert payload["queries"][0]["observation_count"] == 19
    assert payload["newly_saved"] == 1


@pytest.mark.asyncio
async def test_top_historical_json_error(app):
    """/api/top returns JSON error payload when service emits TopErrorEvent."""
    with patch("lib.api.routes.top.TopService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_get_top_queries(input_data, options_data):
            yield TopErrorEvent(
                type="error", message="Database unavailable", stage="connect"
            )

        mock_service.get_top_queries = mock_get_top_queries

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/top?target=prod")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"] == "Database unavailable"


@pytest.mark.asyncio
async def test_top_historical_stream_sse(app):
    """/api/top?stream=true streams historical events via SSE."""
    with patch("lib.api.routes.top.TopService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_get_top_queries(input_data, options_data):
            yield TopStatusEvent(type="status", message="Loading query stats")
            yield TopQueriesEvent(
                type="queries",
                queries=[_sample_query("q_hist")],
                source="pg_stat",
                target_name="prod",
                db_engine="postgresql",
                runtime_seconds=2.1,
                total_tracked=25,
            )
            yield TopCompleteEvent(
                type="complete",
                success=True,
                queries=[_sample_query("q_hist")],
                source="pg_stat",
                newly_saved=1,
            )

        mock_service.get_top_queries = mock_get_top_queries

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "GET",
                "/api/top?target=prod&stream=true",
            ) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")
                events = await _collect_sse_events(response)

    assert any(e["event"] == "status" for e in events)
    query_events = [e for e in events if e["event"] == "queries"]
    assert len(query_events) == 1
    assert query_events[0]["data"]["target_name"] == "prod"
    assert query_events[0]["data"]["queries"][0]["query_hash"] == "q_hist"
    assert query_events[0]["data"]["runtime_seconds"] == 2.1

    complete_events = [e for e in events if e["event"] == "complete"]
    assert len(complete_events) == 1
    assert complete_events[0]["data"]["success"] is True


@pytest.mark.asyncio
async def test_top_realtime_stream_sse(app):
    """/api/top?realtime=true streams realtime monitoring events."""
    with patch("lib.api.routes.top.TopService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_stream_realtime(input_data, options_data, duration):
            yield TopConnectedEvent(
                type="connected",
                target_name="prod",
                db_engine="postgresql",
                source="activity",
            )
            yield TopQueriesEvent(
                type="queries",
                queries=[_sample_query("q_rt")],
                source="activity",
                target_name="prod",
                db_engine="postgresql",
                runtime_seconds=0.8,
                total_tracked=4,
            )
            yield TopCompleteEvent(
                type="complete",
                success=True,
                queries=[_sample_query("q_rt")],
                source="activity",
                newly_saved=0,
            )

        mock_service.stream_realtime = mock_stream_realtime

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "GET",
                "/api/top?target=prod&realtime=true&duration=1",
            ) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")
                events = await _collect_sse_events(response)

    connected_events = [e for e in events if e["event"] == "connected"]
    assert len(connected_events) == 1
    assert connected_events[0]["data"]["source"] == "activity"

    query_events = [e for e in events if e["event"] == "queries"]
    assert len(query_events) == 1
    assert query_events[0]["data"]["queries"][0]["query_hash"] == "q_rt"

    complete_events = [e for e in events if e["event"] == "complete"]
    assert len(complete_events) == 1
    assert complete_events[0]["data"]["success"] is True

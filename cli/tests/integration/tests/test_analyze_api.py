#!/usr/bin/env python3
"""Integration tests for Analyze API endpoint."""

import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from lib.api.app import create_app
from lib.api.routes.target_guard import TargetGuard
from lib.services.types import (
    CompleteEvent,
    ErrorEvent,
    ProgressEvent,
    RewritesTestedEvent,
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


@pytest.mark.asyncio
async def test_analyze_streams_progress_events(app):
    """Analyze endpoint streams progress events during analysis."""
    with patch("lib.api.routes.analyze.AnalyzeService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_analyze(input_data, options_data):
            yield ProgressEvent(
                type="progress",
                stage="loading_config",
                percent=2,
                message="Loading configuration...",
            )
            yield ProgressEvent(
                type="progress",
                stage="explain",
                percent=40,
                message="Running EXPLAIN ANALYZE...",
            )
            yield CompleteEvent(type="complete", success=True)

        mock_service.analyze = mock_analyze

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/api/analyze",
                json={"query": "SELECT 1", "target": "prod"},
            ) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")
                events = await _collect_sse_events(response)

    progress_events = [e for e in events if e["event"] == "progress"]
    assert len(progress_events) == 2
    assert progress_events[0]["data"]["stage"] == "loading_config"
    assert progress_events[1]["data"]["stage"] == "explain"


@pytest.mark.asyncio
async def test_analyze_emits_rewrites_event_when_enabled(app):
    """Analyze endpoint emits rewrites_tested when test_rewrites=true."""
    captured_options = []

    with patch("lib.api.routes.analyze.AnalyzeService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_analyze(input_data, options_data):
            captured_options.append(options_data)
            yield RewritesTestedEvent(
                type="rewrites_tested",
                tested=True,
                message="Tested 2 rewrites",
                original_performance={"execution_time_ms": 100.0},
                rewrite_results=[{"sql": "SELECT 1", "execution_time_ms": 60.0}],
                best_rewrite={"sql": "SELECT 1", "execution_time_ms": 60.0},
            )
            yield CompleteEvent(type="complete", success=True)

        mock_service.analyze = mock_analyze

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/api/analyze",
                json={"query": "SELECT * FROM users", "skip_rewrites": False},
            ) as response:
                events = await _collect_sse_events(response)

    assert captured_options
    assert captured_options[0].test_rewrites is True

    rewrite_events = [e for e in events if e["event"] == "rewrites_tested"]
    assert len(rewrite_events) == 1
    assert rewrite_events[0]["data"]["tested"] is True
    assert rewrite_events[0]["data"]["best_rewrite"]["execution_time_ms"] == 60.0


@pytest.mark.asyncio
async def test_analyze_complete_event_has_expected_fields(app):
    """Analyze endpoint returns complete event with expected fields."""
    with patch("lib.api.routes.analyze.AnalyzeService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_analyze(input_data, options_data):
            yield CompleteEvent(
                type="complete",
                success=True,
                analysis_id="analysis_123",
                query_hash="hash_456",
                explain_results={"success": True, "execution_time_ms": 22.5},
                llm_analysis={"recommendations": ["Add index on users(email)"]},
                rewrite_testing={"tested": True, "best": "rewrite_a"},
                readyset_cacheability={"checked": True, "cacheable": True},
                formatted={"summary": "Looks good"},
            )

        mock_service.analyze = mock_analyze

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/api/analyze",
                json={"query": "SELECT * FROM users"},
            ) as response:
                events = await _collect_sse_events(response)

    complete_events = [e for e in events if e["event"] == "complete"]
    assert len(complete_events) == 1

    payload = complete_events[0]["data"]
    assert payload["success"] is True
    assert payload["analysis_id"] == "analysis_123"
    assert payload["query_hash"] == "hash_456"
    assert payload["explain_results"]["execution_time_ms"] == 22.5
    assert payload["llm_analysis"]["recommendations"]
    assert payload["rewrite_testing"]["tested"] is True
    assert payload["readyset_cacheability"]["cacheable"] is True
    assert payload["formatted"]["summary"] == "Looks good"


@pytest.mark.asyncio
async def test_analyze_error_includes_partial_results(app):
    """Analyze endpoint streams error event with partial results."""
    with patch("lib.api.routes.analyze.AnalyzeService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_analyze(input_data, options_data):
            yield ErrorEvent(
                type="error",
                message="LLM analysis failed",
                stage="llm_analysis",
                partial_results={
                    "explain_results": {"success": True, "execution_time_ms": 10.0}
                },
            )

        mock_service.analyze = mock_analyze

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/api/analyze",
                json={"query": "SELECT * FROM users"},
            ) as response:
                events = await _collect_sse_events(response)

    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) == 1
    payload = error_events[0]["data"]
    assert payload["message"] == "LLM analysis failed"
    assert payload["stage"] == "llm_analysis"
    assert payload["partial_results"]["explain_results"]["success"] is True

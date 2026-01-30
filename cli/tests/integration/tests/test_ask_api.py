#!/usr/bin/env python3
"""Integration tests for Ask API endpoint."""

import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from lib.api.app import create_app
from lib.api.routes.target_guard import TargetGuard
from lib.services.types import (
    AskClarificationNeededEvent,
    AskClarificationQuestion,
    AskErrorEvent,
    AskInterpretation,
    AskResultEvent,
    AskSchemaLoadedEvent,
    AskSqlGeneratedEvent,
    AskStatusEvent,
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
async def test_ask_streams_nl_to_sql_flow(app):
    """Ask endpoint streams status -> sql -> result for NL question."""
    with patch("lib.api.routes.ask.AskService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_ask(input_data, options_data):
            yield AskStatusEvent(
                type="status", phase="schema", message="Loading schema"
            )
            yield AskSchemaLoadedEvent(
                type="schema_loaded",
                source="semantic",
                table_count=2,
                tables=["users", "orders"],
            )
            yield AskSqlGeneratedEvent(
                type="sql_generated",
                sql="SELECT COUNT(*) AS total_users FROM users",
                explanation="Counts users",
            )
            yield AskResultEvent(
                type="result",
                success=True,
                sql="SELECT COUNT(*) AS total_users FROM users",
                rows=[[42]],
                columns=["total_users"],
                row_count=1,
                execution_time_ms=12.5,
                llm_calls=2,
                total_tokens=180,
            )

        mock_service.ask = mock_ask

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/api/ask",
                json={"question": "How many users do we have?", "target": "prod"},
            ) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")
                events = await _collect_sse_events(response)

    assert any(e["event"] == "status" for e in events)
    assert any(e["event"] == "schema_loaded" for e in events)

    sql_events = [e for e in events if e["event"] == "sql_generated"]
    assert len(sql_events) == 1
    assert "SELECT COUNT(*)" in sql_events[0]["data"]["sql"]

    result_events = [e for e in events if e["event"] == "result"]
    assert len(result_events) == 1
    assert result_events[0]["data"]["success"] is True
    assert result_events[0]["data"]["rows"] == [[42]]
    assert result_events[0]["data"]["columns"] == ["total_users"]


@pytest.mark.asyncio
async def test_ask_streams_clarification_needed(app):
    """Ask endpoint returns clarification_needed event when ambiguous."""
    with patch("lib.api.routes.ask.AskService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_ask(input_data, options_data):
            yield AskClarificationNeededEvent(
                type="clarification_needed",
                session_id="sess_123",
                interpretations=[
                    AskInterpretation(
                        id=1,
                        description="Count total users",
                        likelihood=0.7,
                        assumptions=["all rows"],
                    )
                ],
                questions=[
                    AskClarificationQuestion(
                        id="time_range",
                        question="Which period?",
                        options=["all time", "last 30 days"],
                    )
                ],
            )

        mock_service.ask = mock_ask

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/api/ask",
                json={"question": "How many users?"},
            ) as response:
                events = await _collect_sse_events(response)

    clarify_events = [e for e in events if e["event"] == "clarification_needed"]
    assert len(clarify_events) == 1
    payload = clarify_events[0]["data"]
    assert payload["session_id"] == "sess_123"
    assert payload["interpretations"][0]["description"] == "Count total users"
    assert payload["questions"][0]["id"] == "time_range"


@pytest.mark.asyncio
async def test_ask_resume_uses_resume_path(app):
    """Ask endpoint uses service.resume when session_id is provided."""
    with patch("lib.api.routes.ask.AskService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_resume(session_id, clarification_answers):
            assert session_id == "sess_123"
            assert clarification_answers == {"time_range": "last 30 days"}
            yield AskSqlGeneratedEvent(
                type="sql_generated",
                sql="SELECT COUNT(*) FROM users WHERE created_at >= NOW() - INTERVAL '30 days'",
                explanation="Filtered to last 30 days",
            )

        async def mock_ask(input_data, options_data):
            raise AssertionError("ask() should not be called for resume requests")
            yield

        mock_service.resume = mock_resume
        mock_service.ask = mock_ask

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/api/ask",
                json={
                    "question": "ignored by resume path",
                    "session_id": "sess_123",
                    "clarification_answers": {"time_range": "last 30 days"},
                },
            ) as response:
                events = await _collect_sse_events(response)

    sql_events = [e for e in events if e["event"] == "sql_generated"]
    assert len(sql_events) == 1
    assert "INTERVAL '30 days'" in sql_events[0]["data"]["sql"]


@pytest.mark.asyncio
async def test_ask_streams_error_event(app):
    """Ask endpoint streams error event when service returns AskErrorEvent."""
    with patch("lib.api.routes.ask.AskService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_ask(input_data, options_data):
            yield AskErrorEvent(
                type="error",
                message="Could not generate SQL",
                phase="generate",
            )

        mock_service.ask = mock_ask

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/api/ask",
                json={"question": "bad input"},
            ) as response:
                events = await _collect_sse_events(response)

    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) == 1
    payload = error_events[0]["data"]
    assert payload["message"] == "Could not generate SQL"
    assert payload["phase"] == "generate"

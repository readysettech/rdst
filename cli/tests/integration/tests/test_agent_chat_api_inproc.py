"""In-process integration tests for the agent chat session endpoints.

Chat processing is mocked at the `ChatAgent.chat` boundary — it needs an
LLM and a database; the session registry, SSE framing, event bridging, and
concurrency guard run for real.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from features.agent.chat_tools import ToolResult
from features.agent.chat_agent import ChatResponse
from shared.config.targets import TargetsConfig


@pytest.fixture(autouse=True)
def _fresh_session_store():
    from features.agent.service import chat_sessions

    chat_sessions.clear()
    yield
    chat_sessions.clear()


def _seed(client_needed=None) -> None:
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        "chattest",
        {
            "engine": "postgresql",
            "host": "127.0.0.1",
            "port": 5432,
            "database": "appdb",
            "user": "appuser",
            "password_env": "CHAT_PASSWORD",
        },
    )
    cfg.save()


async def _create_agent_and_session(client, monkeypatch) -> str:
    _seed()
    monkeypatch.setenv("CHAT_PASSWORD", "irrelevant")
    response = await client.post(
        "/api/agents", json={"name": "chatbot", "target": "chattest"}
    )
    assert response.status_code == 200

    response = await client.post("/api/agents/chatbot/chat/sessions")
    assert response.status_code == 200
    body = response.json()
    assert body["agent"] == "chatbot"
    return body["session_id"]


async def test_chat_session_lifecycle(client, tmp_rdst_home, monkeypatch):
    session_id = await _create_agent_and_session(client, monkeypatch)

    # Empty history initially.
    response = await client.get(f"/api/agents/chat/sessions/{session_id}")
    assert response.status_code == 200
    assert response.json()["message_count"] == 0

    # Delete ends the session.
    response = await client.delete(f"/api/agents/chat/sessions/{session_id}")
    assert response.status_code == 200
    response = await client.get(f"/api/agents/chat/sessions/{session_id}")
    assert response.status_code == 404


async def test_chat_session_404_for_unknown_agent(client, tmp_rdst_home):
    response = await client.post("/api/agents/nope/chat/sessions")
    assert response.status_code == 404


async def test_chat_session_locked_when_password_missing(
    client, tmp_rdst_home, monkeypatch
):
    _seed()
    monkeypatch.delenv("CHAT_PASSWORD", raising=False)
    monkeypatch.setenv("CHAT_PASSWORD", "x")
    response = await client.post(
        "/api/agents", json={"name": "chatbot", "target": "chattest"}
    )
    assert response.status_code == 200
    monkeypatch.delenv("CHAT_PASSWORD")

    response = await client.post("/api/agents/chatbot/chat/sessions")
    assert response.status_code == 423
    assert response.json()["detail"]["code"] == "TARGET_PASSWORD_REQUIRED"


async def test_chat_message_streams_events(
    client, tmp_rdst_home, monkeypatch, collect_sse_events
):
    session_id = await _create_agent_and_session(client, monkeypatch)

    def fake_chat(self, message, on_event=None):
        assert message == "How many customers?"
        on_event("llm_call", {"iteration": 0})
        on_event("thinking", {"text": "I'll count the customers.", "iteration": 0})
        on_event("tool_call", {"name": "query_database", "input": {"question": "count customers"}})
        on_event(
            "tool_result",
            {
                "result": ToolResult(
                    tool_use_id="tu_1",
                    success=True,
                    content="1 row",
                    data={
                        "sql": "SELECT COUNT(*) FROM customers",
                        "columns": ["count"],
                        "rows": [[42]],
                        "row_count": 1,
                        "execution_time_ms": 3.2,
                        "truncated": False,
                        "query_hash": "abc123",
                        "query_tag": "chat-count",
                    },
                ),
            },
        )
        return ChatResponse(text="There are 42 customers.", tool_results=[object()])

    with patch("features.agent.chat_agent.ChatAgent.chat", fake_chat):
        events = await collect_sse_events(
            client,
            "POST",
            f"/api/agents/chat/sessions/{session_id}/message",
            json_body={"message": "How many customers?"},
        )

    types = [e["event"] for e in events]
    assert types == [
        "status",
        "thinking",
        "tool_call",
        "tool_result",
        "response",
        "complete",
    ]

    tool_result = next(e for e in events if e["event"] == "tool_result")
    assert tool_result["data"]["data"]["rows"] == [[42]]
    assert tool_result["data"]["data"]["query_hash"] == "abc123"

    response_event = next(e for e in events if e["event"] == "response")
    assert response_event["data"]["text"] == "There are 42 customers."

    complete = events[-1]["data"]
    assert complete["success"] is True
    assert complete["tool_result_count"] == 1


async def test_chat_message_surfaces_agent_exception_as_error_event(
    client, tmp_rdst_home, monkeypatch, collect_sse_events
):
    session_id = await _create_agent_and_session(client, monkeypatch)

    with patch(
        "features.agent.chat_agent.ChatAgent.chat",
        side_effect=ValueError("No API key found"),
    ):
        events = await collect_sse_events(
            client,
            "POST",
            f"/api/agents/chat/sessions/{session_id}/message",
            json_body={"message": "hi"},
        )

    assert [e["event"] for e in events] == ["error"]
    assert "No API key" in events[0]["data"]["message"]


async def test_chat_message_rejects_concurrent_send(
    client, tmp_rdst_home, monkeypatch, collect_sse_events
):
    from features.agent.service import chat_sessions

    session_id = await _create_agent_and_session(client, monkeypatch)
    session = chat_sessions.get(session_id)
    # Simulate a turn whose worker thread is still running (e.g. after the
    # client that started it disconnected).
    session.busy = True
    try:
        events = await collect_sse_events(
            client,
            "POST",
            f"/api/agents/chat/sessions/{session_id}/message",
            json_body={"message": "hi"},
        )
    finally:
        session.busy = False

    assert events[-1]["event"] == "error"
    assert "already being processed" in events[-1]["data"]["message"]


async def test_chat_message_404_for_unknown_session(client, tmp_rdst_home):
    response = await client.post(
        "/api/agents/chat/sessions/nope/message", json={"message": "hi"}
    )
    assert response.status_code == 404


async def test_chat_history_reflects_messages(client, tmp_rdst_home, monkeypatch, collect_sse_events):
    from features.agent.service import chat_sessions

    session_id = await _create_agent_and_session(client, monkeypatch)

    def fake_chat(self, message, on_event=None):
        # Mimic the real agent's history bookkeeping.
        self.messages.append({"role": "user", "content": message})
        self.messages.append({"role": "assistant", "content": "hello back"})
        return ChatResponse(text="hello back")

    with patch("features.agent.chat_agent.ChatAgent.chat", fake_chat):
        await collect_sse_events(
            client,
            "POST",
            f"/api/agents/chat/sessions/{session_id}/message",
            json_body={"message": "hello"},
        )

    response = await client.get(f"/api/agents/chat/sessions/{session_id}")
    body = response.json()
    assert body["message_count"] == 2
    assert body["history"][0] == {"role": "user", "summary": "hello"}
    assert body["history"][1] == {"role": "assistant", "summary": "hello back"}

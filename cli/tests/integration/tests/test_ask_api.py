"""Integration tests for the ask API endpoint.

In-process: drives the real `AskService` end-to-end against an isolated
`~/.rdst/`. Happy-path NL→SQL flow lives in the realdb suite — without a
live DB and a real LLM, an in-process happy-path test would only verify
SSE marshaling. What we cover here:

- The `Depends(require_target_body)` guard (locked / unknown target).
- `service.resume(session_id=...)` is wired up and reports a missing
  session as an `error` event (proves the route routes to resume,
  not ask, when `session_id` is present).
- Generator-level error events are propagated as SSE `error` frames.
"""

from __future__ import annotations

import json
import os

import pytest

from shared.config.targets import TargetsConfig


@pytest.fixture(autouse=True)
def _isolate_anthropic_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RDST_TRIAL_TOKEN", raising=False)


def _seed_target(
    name: str = "prod",
    password_env: str = "PROD_DB_PASSWORD",
    engine: str = "postgresql",
) -> None:
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        name,
        {
            "engine": engine,
            "host": "127.0.0.1",
            "port": 1,  # Unreachable on purpose; schema load will fail.
            "database": "appdb",
            "user": "appuser",
            "password_env": password_env,
        },
    )
    cfg.set_default(name)
    cfg.save()


async def _stream_events(client, body: dict) -> list[dict]:
    events: list[dict] = []
    async with client.stream("POST", "/api/ask", json=body) as response:
        if response.status_code != 200:
            return [{"_status": response.status_code, "_body": await response.aread()}]
        current: dict = {}
        async for line in response.aiter_lines():
            if not line:
                if current:
                    events.append(current)
                    current = {}
                continue
            if line.startswith("event:"):
                current["event"] = line[len("event:") :].strip()
            elif line.startswith("data:"):
                payload = line[len("data:") :].strip()
                try:
                    current["data"] = json.loads(payload)
                except json.JSONDecodeError:
                    current["data"] = payload
        if current:
            events.append(current)
    return events


async def test_ask_returns_404_for_unknown_target(client, tmp_rdst_home):
    """Unknown target name fails at the guard before any service work."""
    response = await client.post(
        "/api/ask",
        json={"question": "anything", "target": "does-not-exist"},
    )
    assert response.status_code == 404, response.text


async def test_ask_returns_423_when_target_password_env_missing(
    client, tmp_rdst_home, monkeypatch
):
    """Target exists but `password_env` not set in process env → guard 423s
    with the TARGET_PASSWORD_REQUIRED code."""
    _seed_target(password_env="PROD_DB_PASSWORD")
    monkeypatch.delenv("PROD_DB_PASSWORD", raising=False)

    response = await client.post(
        "/api/ask",
        json={"question": "anything", "target": "prod"},
    )
    assert response.status_code == 423, response.text
    detail = response.json().get("detail") or {}
    assert detail.get("code") == "TARGET_PASSWORD_REQUIRED"
    assert detail.get("target") == "prod"


async def test_ask_resume_with_unknown_session_id_streams_error(
    client, tmp_rdst_home, monkeypatch
):
    """`session_id` present in the body must take the resume path; with an
    unknown id the real `service.resume` yields an error event."""
    _seed_target(password_env="PROD_DB_PASSWORD")
    monkeypatch.setenv("PROD_DB_PASSWORD", "irrelevant")

    events = await _stream_events(
        client,
        {
            "question": "ignored on resume path",
            "target": "prod",
            "session_id": "does-not-exist",
            "clarification_answers": {"x": "y"},
        },
    )
    error_events = [e for e in events if e.get("event") == "error"]
    assert error_events, f"expected error event; got {events}"
    msg = error_events[0]["data"].get("message", "")
    assert "does-not-exist" in msg
    assert "not found" in msg or "expired" in msg


async def test_ask_streams_error_when_schema_load_fails(
    client, tmp_rdst_home, monkeypatch
):
    """No DB reachable → real `load_schema` errors → service yields
    `AskErrorEvent`, route marshals it as an `error` SSE frame.

    This exercises the real route → service → schema-phase pipeline
    end-to-end without needing an LLM or a live DB.
    """
    _seed_target(password_env="PROD_DB_PASSWORD")
    monkeypatch.setenv("PROD_DB_PASSWORD", "irrelevant")

    events = await _stream_events(
        client,
        {"question": "How many users?", "target": "prod"},
    )
    error_events = [e for e in events if e.get("event") == "error"]
    assert error_events, f"expected error event; got {events}"

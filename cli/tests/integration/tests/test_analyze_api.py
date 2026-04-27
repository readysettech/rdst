"""In-process integration tests for the analyze API endpoint.

The happy paths (`progress` → `complete`, rewrites tested, full
`complete` payload shape) are covered end-to-end against a live DB by
`test_realdb_configure_analyze_api.py`. Mocking those in-process would
just verify SSE marshaling.

What we cover here:
- The `Depends(require_target_body)` guard on `/api/analyze`.
- A real error path: targeting a configured-but-unreachable database
  produces an `error` SSE frame from the real `AnalyzeService`.
"""

from __future__ import annotations

import json

import pytest

from shared.config.targets import TargetsConfig


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RDST_TRIAL_TOKEN", raising=False)


def _seed_unreachable_target(name: str = "prod", env: str = "PROD_PASSWORD") -> None:
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        name,
        {
            "engine": "postgresql",
            "host": "127.0.0.1",
            "port": 1,  # Unreachable on purpose.
            "database": "appdb",
            "user": "appuser",
            "password_env": env,
        },
    )
    cfg.save()


async def _stream_events(client, body: dict) -> list[dict]:
    events: list[dict] = []
    async with client.stream("POST", "/api/analyze", json=body) as response:
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


async def test_analyze_returns_404_for_unknown_target(client, tmp_rdst_home):
    """Unknown target → guard 404s before service runs."""
    response = await client.post(
        "/api/analyze",
        json={"query": "SELECT 1", "target": "does-not-exist"},
    )
    assert response.status_code == 404, response.text


async def test_analyze_returns_423_when_password_env_missing(
    client, tmp_rdst_home, monkeypatch
):
    """Target exists but `password_env` is unset → 423 with
    TARGET_PASSWORD_REQUIRED detail."""
    _seed_unreachable_target(env="PROD_PASSWORD")
    monkeypatch.delenv("PROD_PASSWORD", raising=False)

    response = await client.post(
        "/api/analyze",
        json={"query": "SELECT 1", "target": "prod"},
    )
    assert response.status_code == 423, response.text
    assert response.json()["detail"]["code"] == "TARGET_PASSWORD_REQUIRED"


async def test_analyze_reports_explain_failure_for_unreachable_target(
    client, tmp_rdst_home, monkeypatch
):
    """Service runs end-to-end but EXPLAIN fails because the DB is
    unreachable. The route still completes (the analyze flow is fault-
    tolerant — partial results bubble up through the same `complete`
    payload), but the `explain_complete` event must be marked unsuccessful
    and `complete.explain_results.error` must mention the connection
    failure.
    """
    _seed_unreachable_target(env="PROD_PASSWORD")
    monkeypatch.setenv("PROD_PASSWORD", "irrelevant")

    events = await _stream_events(
        client,
        {
            "query": "SELECT 1",
            "target": "prod",
            "fast": True,
            "skip_rewrites": True,
        },
    )
    by_event = {e["event"]: e["data"] for e in events if "event" in e}

    explain = by_event.get("explain_complete")
    assert explain is not None, f"missing explain_complete; got {events}"
    assert explain["success"] is False

    complete = by_event.get("complete")
    assert complete is not None
    assert complete["explain_results"]["success"] is False
    err = complete["explain_results"].get("error", "")
    assert "Connection refused" in err or "connection" in err.lower()

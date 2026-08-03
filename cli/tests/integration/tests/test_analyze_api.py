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


@pytest.mark.usefixtures("run_blocking_inline")
async def test_analyze_emits_categorized_error_for_unreachable_target(
    client, tmp_rdst_home, monkeypatch
):
    """An unreachable database fails fast with a categorized EXPLAIN error."""
    _seed_unreachable_target(env="PROD_PASSWORD")
    monkeypatch.setenv("PROD_PASSWORD", "irrelevant")

    # Keep the in-process contract test deterministic: some libpq builds
    # produce an empty message for a refused localhost connection, and an
    # unrelated ephemeral Readyset probe must not delay this error path.
    def refuse_connection(_conn_params):
        raise ConnectionError("Connection refused")

    monkeypatch.setattr(
        "features.analyze.functions.explain_analysis._postgres_connection",
        refuse_connection,
    )
    monkeypatch.setattr(
        "features.analyze.service.AnalyzeService._run_readyset_analysis_sync",
        lambda *args, **kwargs: {"success": False, "error": "Not configured"},
    )

    events = await _stream_events(
        client,
        {
            "query": "SELECT 1",
            "target": "prod",
            "fast": True,
            "skip_rewrites": True,
        },
    )
    event_names = [event.get("event") for event in events]
    errors = [event["data"] for event in events if event.get("event") == "error"]

    assert any(
        error.get("code") == "database_connection_failed"
        and error.get("stage") == "executing_explain"
        for error in errors
    ), f"missing categorized EXPLAIN error; got {events}"
    assert "explain_complete" not in event_names

"""Integration tests for the env API endpoints.

Drives the real `EnvRequirementsService` and `SecretStoreService` against
an isolated `~/.rdst/` (per-test tmp dir) and an in-memory keyring backend.
The OS keychain is the only system boundary mocked here — everything else
runs end-to-end.
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from shared.api.routes import env as env_routes
from shared.config.targets import TargetsConfig


@pytest.fixture(autouse=True)
def _isolate_anthropic_env(monkeypatch):
    """Strip ANTHROPIC_API_KEY / RDST_TRIAL_TOKEN so requirement reporting
    is deterministic. Tests that want them set should re-add explicitly."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RDST_TRIAL_TOKEN", raising=False)


def _seed_target_with_password_env(env_name: str, target: str = "prod") -> None:
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        target,
        {
            "engine": "postgresql",
            "host": "db.example.com",
            "port": 5432,
            "database": "appdb",
            "user": "appuser",
            "password_env": env_name,
        },
    )
    cfg.save()


async def test_get_env_requirements_lists_seeded_target_password(
    client, tmp_rdst_home, inmemory_keyring, monkeypatch
):
    """Seeded target with `password_env` shows up as an unsatisfied
    `target_password` requirement; missing Anthropic key shows up too."""
    _seed_target_with_password_env("PROD_DB_PASSWORD", target="prod")

    response = await client.get("/api/env/requirements")
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["keyring_available"] is True

    by_kind = {req["kind"]: req for req in payload["requirements"]}
    assert "target_password" in by_kind
    assert by_kind["target_password"]["accepted_names"] == ["PROD_DB_PASSWORD"]
    assert by_kind["target_password"]["target"] == "prod"
    assert by_kind["target_password"]["satisfied"] is False
    assert by_kind["target_password"]["source"] == "missing"

    assert "anthropic_api_key" in by_kind
    assert by_kind["anthropic_api_key"]["satisfied"] is False
    assert by_kind["anthropic_api_key"]["source"] == "missing"


async def test_set_env_secret_persists_to_keyring(
    client, tmp_rdst_home, inmemory_keyring, monkeypatch
):
    """`POST /api/env/set` with an allow-listed name and `persist=True`
    actually writes to the keyring backend (in-memory here) and updates
    `os.environ` as a side effect."""
    _seed_target_with_password_env("PROD_DB_PASSWORD")
    monkeypatch.delenv("PROD_DB_PASSWORD", raising=False)

    response = await client.post(
        "/api/env/set",
        json={"name": "PROD_DB_PASSWORD", "value": "s3cret", "persist": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["persisted"] is True
    assert body["session_only"] is False

    # Side effects landed where we expect.
    assert os.environ["PROD_DB_PASSWORD"] == "s3cret"
    assert (
        inmemory_keyring.get_password("rdst-web", "PROD_DB_PASSWORD") == "s3cret"
    )


async def test_set_env_secret_session_only_when_persist_false(
    client, tmp_rdst_home, inmemory_keyring, monkeypatch
):
    """`persist=False` should set the env var but skip keyring writes."""
    _seed_target_with_password_env("PROD_DB_PASSWORD")
    monkeypatch.delenv("PROD_DB_PASSWORD", raising=False)

    response = await client.post(
        "/api/env/set",
        json={"name": "PROD_DB_PASSWORD", "value": "transient", "persist": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["persisted"] is False
    assert body["session_only"] is True

    assert os.environ["PROD_DB_PASSWORD"] == "transient"
    # Persisted=False must mean: did NOT touch the keyring.
    assert (
        inmemory_keyring.get_password("rdst-web", "PROD_DB_PASSWORD") is None
    )


async def test_set_anthropic_secret_notifies_parked_work(
    client, tmp_rdst_home, inmemory_keyring, monkeypatch
):
    class Registry:
        wake_calls = 0

        def wake_needs_key(self):
            self.wake_calls += 1

    registry = Registry()
    monkeypatch.setattr(env_routes, "run_registry", registry)

    response = await client.post(
        "/api/env/set",
        json={"name": "ANTHROPIC_API_KEY", "value": "sk-ant-test", "persist": False},
    )

    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert registry.wake_calls == 1


async def test_set_env_secret_rejects_non_allowlisted_name(
    client, tmp_rdst_home, inmemory_keyring
):
    """An env name that is not derivable from any target's `password_env`
    nor in the Anthropic accepted set must be refused with success=False."""
    response = await client.post(
        "/api/env/set",
        json={"name": "NOT_ALLOWED", "value": "x", "persist": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "not allowed" in (body.get("message") or "").lower()


async def test_set_env_secret_rejects_mismatched_origin(
    app, tmp_rdst_home, inmemory_keyring
):
    """Loopback request whose `Origin` header points at a different
    loopback alias must be 403'd by the same-host check."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8787"
    ) as c:
        response = await c.post(
            "/api/env/set",
            headers={"origin": "http://localhost:8787"},
            json={"name": "PROD_DB_PASSWORD", "value": "x", "persist": True},
        )
    assert response.status_code == 403


async def test_env_routes_reject_non_loopback_client(app, tmp_rdst_home):
    """Non-loopback peer is forbidden even before request parsing."""
    transport = ASGITransport(app=app, client=("203.0.113.10", 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/api/env/requirements")
    assert response.status_code == 403

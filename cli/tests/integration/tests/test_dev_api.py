"""Integration tests for the dev API endpoints.

The clear-keyring path runs end-to-end: real `EnvRequirementsService`,
real `SecretStoreService` against an in-memory keyring backend, real
`TargetsConfig` on a tmp `~/.rdst/`. The OS keychain is the only
boundary mocked.
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from shared.config.targets import TargetsConfig


@pytest.fixture(autouse=True)
def _isolate_anthropic_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RDST_TRIAL_TOKEN", raising=False)


def _seed_target(env_name: str = "PROD_DB_PASSWORD", target: str = "prod") -> None:
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


async def test_clear_keyring_clears_secrets_and_trial_config(
    app, tmp_rdst_home, inmemory_keyring, monkeypatch
):
    """Pre-seed: a target with `password_env`, the trial token in the
    keyring, and a trial config block on disk. After /dev/clear-keyring:
    secret is gone from keyring, env var is gone from process, trial
    config block on disk is empty.
    """
    _seed_target("PROD_DB_PASSWORD")

    # Seed trial state on disk.
    cfg = TargetsConfig()
    cfg.load()
    cfg.set_trial_config(
        {
            "token": "trial-token",
            "email": "user@example.com",
            "status": "exhausted",
            "remaining_cents": 0,
            "limit_cents": 500,
        }
    )
    cfg.save()

    # Seed token both in env (so clear has something to remove) and
    # keyring (so clear has something to delete).
    monkeypatch.setenv("RDST_TRIAL_TOKEN", "trial-token")
    inmemory_keyring.set_password("rdst-web", "RDST_TRIAL_TOKEN", "trial-token")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8787"
    ) as c:
        response = await c.post(
            "/api/dev/clear-keyring",
            headers={"origin": "http://127.0.0.1:8787"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert "RDST_TRIAL_TOKEN" in body["cleared"]
    assert body["errors"] == []
    assert "Reset" in (body.get("message") or "")
    assert "local trial state" in body["message"]

    # Side effects: keyring entry removed, env var removed, trial config gone.
    assert (
        inmemory_keyring.get_password("rdst-web", "RDST_TRIAL_TOKEN") is None
    )
    assert "RDST_TRIAL_TOKEN" not in os.environ

    cfg2 = TargetsConfig()
    cfg2.load()
    assert cfg2.get_trial_config() == {}


async def test_clear_keyring_rejects_mismatched_origin_host(app, tmp_rdst_home):
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8787"
    ) as c:
        response = await c.post(
            "/api/dev/clear-keyring",
            headers={"origin": "http://localhost:8787"},
        )
    assert response.status_code == 403
    assert response.json() == {"detail": "Origin/Referer host mismatch"}


async def test_clear_keyring_rejects_non_loopback_client(app, tmp_rdst_home):
    transport = ASGITransport(app=app, client=("203.0.113.10", 50000))
    async with AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8787"
    ) as c:
        response = await c.post(
            "/api/dev/clear-keyring",
            headers={"origin": "http://127.0.0.1:8787"},
        )
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}

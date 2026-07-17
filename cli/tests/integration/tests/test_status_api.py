"""Integration tests for the /api/status endpoint.

Drives the real `TargetsConfig` against `tmp_rdst_home` — no service
mocks. Status reports a snapshot of configured targets and which one
is default, so the assertions track config state directly.
"""

from __future__ import annotations

from shared.config.targets import TargetsConfig


async def test_status_empty_state_reports_unconfigured(client, tmp_rdst_home):
    """No targets on disk → `configured=false`, empty target list."""
    response = await client.get("/api/status")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["configured"] is False
    assert body["targets"] == []
    assert body["default_target"] is None
    # Version always populated (falls back to "unknown" if introspection fails).
    assert body["version"]


async def test_status_reports_configured_targets_with_password_state(
    client, tmp_rdst_home, monkeypatch
):
    """Two targets: one with password env set, one without. Status
    should reflect both, mark the default, and `configured=true`
    because at least one has a password available."""
    monkeypatch.setenv("PROD_DB_PASSWORD", "live")
    monkeypatch.delenv("STAGING_DB_PASSWORD", raising=False)

    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        "prod",
        {
            "engine": "postgresql",
            "host": "db.example.com",
            "port": 5432,
            "database": "appdb",
            "user": "appuser",
            "password_env": "PROD_DB_PASSWORD",
        },
    )
    cfg.upsert(
        "staging",
        {
            "engine": "postgresql",
            "host": "stg.example.com",
            "port": 5432,
            "database": "stagingdb",
            "user": "appuser",
            "password_env": "STAGING_DB_PASSWORD",
        },
    )
    cfg.set_default("prod")
    cfg.save()

    response = await client.get("/api/status")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["configured"] is True
    assert body["default_target"] == "prod"

    by_name = {t["name"]: t for t in body["targets"]}
    assert by_name["prod"]["is_default"] is True
    assert by_name["prod"]["has_password"] is True
    assert by_name["staging"]["is_default"] is False
    assert by_name["staging"]["has_password"] is False


async def test_status_hides_readyset_cache_targets(
    client, tmp_rdst_home, monkeypatch
):
    """The global Web selector contains databases, not cache endpoints."""
    monkeypatch.setenv("DATABASE_PASSWORD", "database-secret")
    monkeypatch.setenv("CACHE_PASSWORD", "cache-secret")

    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        "orders",
        {
            "engine": "postgresql",
            "host": "db.example.com",
            "port": 5432,
            "database": "orders",
            "user": "appuser",
            "password_env": "DATABASE_PASSWORD",
        },
    )
    cfg.upsert(
        "orders-cache",
        {
            "engine": "postgresql",
            "host": "127.0.0.1",
            "port": 5433,
            "database": "orders",
            "user": "appuser",
            "password_env": "CACHE_PASSWORD",
            "target_type": "readyset",
            "upstream_target": "orders",
        },
    )
    cfg.save()

    response = await client.get("/api/status")

    assert response.status_code == 200, response.text
    assert [target["name"] for target in response.json()["targets"]] == ["orders"]

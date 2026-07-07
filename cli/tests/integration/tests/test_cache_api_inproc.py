"""In-process integration tests for the cache API endpoints.

Drives the real `CacheService` against `tmp_rdst_home`. We exercise
the config-only paths (no Readyset container required): "no cache
deployed" status and the empty-list error path. Full deploy/run/remove
lifecycle lives in the realdb suite (slice 5), gated on a Readyset
container.
"""

from __future__ import annotations

from shared.config.targets import TargetsConfig


def _seed_target(name: str = "cachetest", env: str = "CACHE_PASSWORD") -> None:
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        name,
        {
            "engine": "postgresql",
            "host": "127.0.0.1",
            "port": 5432,
            "database": "appdb",
            "user": "appuser",
            "password_env": env,
        },
    )
    cfg.save()


async def test_status_returns_not_deployed_when_no_cache_configured(
    client, tmp_rdst_home, monkeypatch
):
    """A configured target with no Readyset deployment → `cache_status`
    event with deployed=false, running=false."""
    _seed_target()
    monkeypatch.setenv("CACHE_PASSWORD", "irrelevant")

    response = await client.get("/api/cache/status?target=cachetest")
    assert response.status_code == 200, response.text

    body = response.json()
    # `_event_to_dict` collapses CacheStatusEvent into a flat dict.
    assert body.get("deployed") is False
    assert body.get("running") is False


async def test_list_for_undeployed_target_returns_error_payload(
    client, tmp_rdst_home, monkeypatch
):
    """`/cache/list` for a target with no deployment surfaces an error
    payload (success=false) — there's no cache to query against."""
    _seed_target()
    monkeypatch.setenv("CACHE_PASSWORD", "irrelevant")

    response = await client.get("/api/cache/list?target=cachetest")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body.get("success") is False
    assert "No cache deployed" in (body.get("error") or "")


async def test_start_for_undeployed_target_returns_error_payload(
    client, tmp_rdst_home, monkeypatch
):
    """`/cache/start` for a target with no deployment surfaces an error
    payload — there's no cache container to start."""
    _seed_target()
    monkeypatch.setenv("CACHE_PASSWORD", "irrelevant")

    response = await client.post("/api/cache/start", json={"target": "cachetest"})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body.get("success") is False
    assert "No cache target found" in (body.get("error") or "")


async def test_status_locked_when_target_password_missing(
    client, tmp_rdst_home, monkeypatch
):
    """The `Depends(require_target)` guard 423s before any cache work
    when the target's `password_env` isn't set in the process env."""
    _seed_target(env="MISSING_CACHE_PASSWORD")
    monkeypatch.delenv("MISSING_CACHE_PASSWORD", raising=False)

    response = await client.get("/api/cache/status?target=cachetest")
    assert response.status_code == 423
    assert response.json()["detail"]["code"] == "TARGET_PASSWORD_REQUIRED"


async def test_status_404_for_unknown_target(client, tmp_rdst_home):
    """No such target → guard returns 404 before any service work."""
    response = await client.get("/api/cache/status?target=does-not-exist")
    assert response.status_code == 404

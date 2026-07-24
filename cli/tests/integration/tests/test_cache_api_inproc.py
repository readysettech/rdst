"""In-process integration tests for the temporary Readyset sandbox API."""

from __future__ import annotations

import pytest


@pytest.fixture
def seeded_target_defaults() -> dict:
    return {"name": "cachetest", "env": "CACHE_PASSWORD"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/cache/status"),
        ("post", "/api/cache/deploy"),
        ("get", "/api/cache/list"),
        ("post", "/api/cache/add"),
        ("post", "/api/cache/register"),
        ("delete", "/api/cache/remove"),
        ("delete", "/api/cache/drop-all"),
        ("post", "/api/cache/start"),
        ("post", "/api/cache/stop"),
        ("post", "/api/cache/restart"),
        ("post", "/api/cache/run"),
        ("get", "/api/readyset/status"),
        ("post", "/api/readyset/setup"),
        ("post", "/api/readyset/explain"),
        ("post", "/api/readyset/cache"),
    ],
)
async def test_persistent_cache_management_routes_are_absent(
    client, method, path
):
    response = await getattr(client, method)(path)

    assert response.status_code == 404


async def test_sandbox_diagnostics_remain_available(client):
    response = await client.get("/api/cache/sandbox")

    assert response.status_code == 200
    assert response.json()["container_name"] == "rdst-readyset-sandbox"


async def test_temporary_comparison_requires_target_password(
    client, tmp_rdst_home, monkeypatch, seed_target
):
    seed_target(env="MISSING_CACHE_PASSWORD")
    monkeypatch.delenv("MISSING_CACHE_PASSWORD", raising=False)

    response = await client.post(
        "/api/cache/test-runs",
        json={"target": "cachetest", "query": "SELECT 1"},
    )

    assert response.status_code == 423
    assert response.json()["detail"]["code"] == "TARGET_PASSWORD_REQUIRED"


async def test_temporary_comparison_rejects_unknown_target(client, tmp_rdst_home):
    response = await client.post(
        "/api/cache/test-runs",
        json={"target": "does-not-exist", "query": "SELECT 1"},
    )

    assert response.status_code == 404


async def test_temporary_comparison_registers_a_speed_test(
    client, tmp_rdst_home, monkeypatch, seed_target
):
    from features.cache.api import routes

    class Registry:
        def __init__(self):
            self.started = None

        def find_active_matching(self, *args, **kwargs):
            return None

        def start_factory(self, kind, target, factory, metadata=None):
            self.started = (kind, target, metadata)
            return "speed_test_cachetest_new"

    async def ready_runtime():
        return None

    async def healthy_upstream(_guard):
        return None

    registry = Registry()
    monkeypatch.setattr(routes, "_run_registry", registry)
    monkeypatch.setattr(routes, "_require_readyset_runtime", ready_runtime)
    monkeypatch.setattr(routes, "_require_healthy_upstream", healthy_upstream)
    seed_target()
    monkeypatch.setenv("CACHE_PASSWORD", "irrelevant")

    response = await client.post(
        "/api/cache/test-runs",
        json={
            "target": "cachetest",
            "query": "SELECT 1",
            "query_hash": "abc123",
            "iterations": 3,
            "warmup": 1,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"run_id": "speed_test_cachetest_new"}
    assert registry.started is not None
    kind, target, metadata = registry.started
    assert (kind, target) == ("speed_test", "cachetest")
    assert metadata["query_hash"] == "abc123"

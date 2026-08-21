"""API coverage for the target-scoped Query Library lifecycle read model."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import features.query_registry.api.routes as query_routes
from shared.query_registry.query_registry import QueryRegistry


pytestmark = pytest.mark.usefixtures("run_blocking_inline")


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(query_routes.router, prefix="/api")
    return app


@pytest.fixture
def registry(monkeypatch, tmp_path) -> QueryRegistry:
    instance = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    instance.load()

    import shared.query_registry as shared_registry

    monkeypatch.setattr(shared_registry, "QueryRegistry", lambda *a, **k: instance)
    return instance


@pytest.mark.asyncio
async def test_registry_response_uses_requested_target_lifecycle(app, registry):
    registry.add_query(
        "SELECT * FROM users",
        source="top-historical",
        target="demo",
        observed=True,
    )
    registry.add_query(
        "SELECT * FROM users",
        source="scan",
        target="analytics",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/query-registry",
            params={"target": "demo"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    entry = body["queries"][0]
    assert entry["target"] == "demo"
    assert entry["sources"] == ["top-historical"]
    assert entry["first_observed_at"]
    assert entry["last_observed_at"]
    assert entry["saved_at"] == ""
    assert entry["starred"] is False
    assert entry["is_new"] is True


@pytest.mark.asyncio
async def test_mark_reviewed_clears_new_for_only_requested_target(app, registry):
    query_hash, _ = registry.add_query(
        "SELECT * FROM users",
        source="top-historical",
        target="demo",
        observed=True,
    )
    registry.add_query(
        "SELECT * FROM users",
        source="top-historical",
        target="analytics",
        observed=True,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        reviewed = await client.post(
            f"/api/query-registry/{query_hash}/reviewed",
            json={"target": "demo"},
        )
        demo = await client.get(
            "/api/query-registry",
            params={"target": "demo"},
        )
        analytics = await client.get(
            "/api/query-registry",
            params={"target": "analytics"},
        )

    assert reviewed.status_code == 200
    assert reviewed.json() == {"success": True, "error": None}
    assert demo.json()["queries"][0]["is_new"] is False
    assert demo.json()["queries"][0]["reviewed_at"]
    assert analytics.json()["queries"][0]["is_new"] is True

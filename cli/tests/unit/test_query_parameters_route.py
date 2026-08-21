"""API coverage for storing a query's parameter values.

The Compare and Load Test dialogs collect concrete values for a templated
query; this endpoint is where those values become stored state, so the same
query can be run again without retyping them.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import features.query_registry.api.routes as query_routes
from shared.query_registry.query_registry import QueryRegistry

LOOPBACK_CLIENT = ("127.0.0.1", 54321)

PG_TEXT = "SELECT * FROM orders WHERE customer_id = $1 LIMIT $2"


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


async def _patch(app: FastAPI, query_hash: str, payload: dict, **kwargs):
    transport = ASGITransport(app=app, client=LOOPBACK_CLIENT)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8787") as client:
        return await client.patch(
            f"/api/query-registry/queries/{query_hash}/parameters",
            json=payload,
            **kwargs,
        )


@pytest.mark.asyncio
async def test_values_are_stored_and_returned(app, registry):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo"
    )

    response = await _patch(
        app, query_hash, {"values": {"p1": "42", "p2": "10"}, "source": "user"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "hash": query_hash,
        "parameters": {
            "p1": {"value": 42, "type": "number", "source": "user"},
            "p2": {"value": 10, "type": "number", "source": "user"},
        },
    }


@pytest.mark.asyncio
async def test_stored_values_make_the_query_runnable(app, registry):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo"
    )

    await _patch(
        app, query_hash, {"values": {"p1": "42", "p2": "10"}, "source": "suggested"}
    )
    executable = registry.get_executable_query(query_hash, interactive=False)

    assert registry.get_query(query_hash).most_recent_params == {
        "p1": "42",
        "p2": "10",
    }
    assert "$1" not in executable
    assert executable.endswith("LIMIT 10")


@pytest.mark.asyncio
async def test_unknown_hash_is_not_found(app, registry):
    response = await _patch(app, "deadbeef0000", {"values": {"p1": "42"}})

    assert response.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"values": {}},
        {"values": {"p1": ["42"]}},
        {"values": {"p1": "42"}, "source": "invented"},
    ],
)
@pytest.mark.asyncio
async def test_malformed_body_is_rejected(app, registry, payload):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo"
    )

    response = await _patch(app, query_hash, payload)

    assert response.status_code == 422
    assert registry.get_query(query_hash).parameters == {}


@pytest.mark.asyncio
async def test_cross_site_origin_is_forbidden(app, registry):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo"
    )

    response = await _patch(
        app,
        query_hash,
        {"values": {"p1": "42"}},
        headers={"origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert registry.get_query(query_hash).parameters == {}

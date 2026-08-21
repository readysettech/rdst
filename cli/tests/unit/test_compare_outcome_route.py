"""API coverage for recording what a Compare run found.

Comparing a query against Readyset is the measurement the Query Library
exists for. Until this endpoint the result lived in one browser, so the
library could not say whether anything had ever been measured; these tests
pin the write and the durability of what it writes.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import features.query_registry.api.routes as query_routes
from shared.query_registry.query_registry import QueryRegistry

LOOPBACK_CLIENT = ("127.0.0.1", 54321)

PG_TEXT = "SELECT title, score FROM posts WHERE score > $1"


@pytest.fixture
def registry_path(tmp_path):
    return str(tmp_path / "queries.toml")


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(query_routes.router, prefix="/api")
    return app


@pytest.fixture
def registry(monkeypatch, registry_path) -> QueryRegistry:
    """Every request opens its own registry, as the real server does."""
    import shared.query_registry as shared_registry

    monkeypatch.setattr(
        shared_registry,
        "QueryRegistry",
        lambda *a, **k: QueryRegistry(registry_path=registry_path),
    )
    instance = QueryRegistry(registry_path=registry_path)
    instance.load()
    return instance


async def _post(app: FastAPI, query_hash: str, payload: dict, **kwargs):
    transport = ASGITransport(app=app, client=LOOPBACK_CLIENT)
    async with AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8787"
    ) as client:
        return await client.post(
            f"/api/query-registry/queries/{query_hash}/compare-outcome",
            json=payload,
            **kwargs,
        )


def _lifecycle(registry_path, query_hash, target="demo"):
    reader = QueryRegistry(registry_path=registry_path)
    reader.load()
    return reader.get_query(query_hash).lifecycle_for(target)


@pytest.mark.asyncio
async def test_outcome_counts_the_run_and_is_readable_afterwards(
    app, registry, registry_path
):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo"
    )

    response = await _post(
        app,
        query_hash,
        {
            "target": "demo",
            "status": "improved",
            "readyset_ms": 1.5,
            "origin_ms": 42.0,
            "detail": "10 runs each",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["hash"] == query_hash
    assert body["target"] == "demo"
    assert body["comparison_count"] == 1
    assert body["last_compare"]["status"] == "improved"
    assert body["last_compare"]["readyset_ms"] == 1.5

    lifecycle = _lifecycle(registry_path, query_hash)
    assert lifecycle.comparison_count == 1
    assert lifecycle.last_compared_at == body["last_compared_at"]
    assert lifecycle.last_compare == body["last_compare"]


@pytest.mark.asyncio
async def test_a_second_run_counts_again_and_replaces_the_outcome(
    app, registry, registry_path
):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo"
    )

    await _post(app, query_hash, {"target": "demo", "status": "improved"})
    response = await _post(
        app, query_hash, {"target": "demo", "status": "regressed"}
    )

    assert response.json()["comparison_count"] == 2
    lifecycle = _lifecycle(registry_path, query_hash)
    assert lifecycle.comparison_count == 2
    assert lifecycle.last_compare["status"] == "regressed"


@pytest.mark.asyncio
async def test_unmeasured_fields_are_left_out(app, registry, registry_path):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo"
    )

    response = await _post(
        app,
        query_hash,
        {"target": "demo", "status": "not_comparable", "readyset_ms": None},
    )

    outcome = response.json()["last_compare"]
    assert set(outcome) == {"status", "at"}


@pytest.mark.asyncio
async def test_the_outcome_survives_another_writer(app, registry, registry_path):
    """Discovery re-observing the query must not erase the measurement."""
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo"
    )
    await _post(app, query_hash, {"target": "demo", "status": "equivalent"})

    observer = QueryRegistry(registry_path=registry_path)
    observer.load()
    observer.add_query(
        sql=PG_TEXT,
        source="top-historical",
        target="demo",
        frequency=99,
        observed=True,
        save_intent=False,
    )

    lifecycle = _lifecycle(registry_path, query_hash)
    assert lifecycle.comparison_count == 1
    assert lifecycle.last_compare["status"] == "equivalent"


@pytest.mark.asyncio
async def test_readyset_verdict_is_persisted(app, registry, registry_path):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo"
    )

    response = await _post(
        app,
        query_hash,
        {
            "target": "demo",
            "status": "not_comparable",
            "readyset_supported": "no",
            "unsupported_reason": "correlated subquery",
        },
    )

    assert response.json()["readyset_supported"] == (
        "unsupported: correlated subquery"
    )
    reader = QueryRegistry(registry_path=registry_path)
    reader.load()
    assert reader.get_query(query_hash).readyset_supported == (
        "unsupported: correlated subquery"
    )


@pytest.mark.asyncio
async def test_a_supported_verdict_is_persisted(app, registry, registry_path):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo"
    )

    response = await _post(
        app,
        query_hash,
        {"target": "demo", "status": "improved", "readyset_supported": "yes"},
    )

    assert response.json()["readyset_supported"] == "yes"


@pytest.mark.asyncio
async def test_an_omitted_verdict_leaves_the_stored_one_alone(
    app, registry, registry_path
):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo"
    )
    registry.update_readyset_identity(query_hash, "q_abc", "yes", "demo")

    response = await _post(app, query_hash, {"target": "demo", "status": "improved"})

    assert response.json()["readyset_supported"] == "yes"
    reader = QueryRegistry(registry_path=registry_path)
    reader.load()
    assert reader.get_query(query_hash).readyset_query_id == "q_abc"


@pytest.mark.asyncio
async def test_an_empty_target_uses_the_query_home(app, registry, registry_path):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo"
    )

    response = await _post(app, query_hash, {"target": "", "status": "improved"})

    assert response.json()["target"] == "demo"
    assert _lifecycle(registry_path, query_hash).comparison_count == 1


@pytest.mark.asyncio
async def test_unknown_hash_is_not_found(app, registry):
    response = await _post(
        app, "deadbeef0000", {"target": "demo", "status": "improved"}
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"target": "demo"},
        {"target": "demo", "status": "faster"},
        {"target": "demo", "status": "improved", "readyset_ms": -1},
        {"target": "demo", "status": "improved", "readyset_supported": "maybe"},
        {"target": "demo", "status": "improved", "detail": "x" * 501},
    ],
)
@pytest.mark.asyncio
async def test_malformed_body_is_rejected(app, registry, registry_path, payload):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo"
    )

    response = await _post(app, query_hash, payload)

    assert response.status_code == 422
    assert _lifecycle(registry_path, query_hash).comparison_count == 0


@pytest.mark.asyncio
async def test_cross_site_origin_is_forbidden(app, registry, registry_path):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo"
    )

    response = await _post(
        app,
        query_hash,
        {"target": "demo", "status": "improved"},
        headers={"origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert _lifecycle(registry_path, query_hash).comparison_count == 0

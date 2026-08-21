"""The star: the Query Library's one user-authored mark.

Everything RDST captures on its own arrives unstarred, the toggle both sets
and clears the mark, and what it writes outlives the process that wrote it.
"""

from __future__ import annotations

import itertools
from unittest.mock import AsyncMock, MagicMock, patch

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


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, client=LOOPBACK_CLIENT),
        base_url="http://127.0.0.1:8787",
    )


async def _star(app: FastAPI, query_hash: str, payload: dict, **kwargs):
    async with _client(app) as client:
        return await client.patch(
            f"/api/query-registry/queries/{query_hash}/starred",
            json=payload,
            **kwargs,
        )


async def _rows(app: FastAPI, params: dict) -> list[dict]:
    async with _client(app) as client:
        response = await client.get("/api/query-registry", params=params)
    return response.json()["queries"]


def _stored_saved_at(registry_path: str, query_hash: str, target="demo") -> str:
    """Read saved_at back through a fresh registry, as the next request does."""
    reader = QueryRegistry(registry_path=registry_path)
    reader.load()
    return reader.get_query(query_hash).lifecycle_for(target).saved_at


# -- the toggle --------------------------------------------------------------


@pytest.mark.asyncio
async def test_star_then_unstar_round_trips_through_the_store(
    app, registry, registry_path
):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo", observed=True,
    )

    starred = await _star(app, query_hash, {"starred": True, "target": "demo"})

    assert starred.status_code == 200
    body = starred.json()
    assert body["hash"] == query_hash
    assert body["target"] == "demo"
    assert body["starred"] is True
    assert body["starred_at"] == _stored_saved_at(registry_path, query_hash)
    row = (await _rows(app, {"target": "demo", "view": "all"}))[0]
    assert row["starred"] is True
    assert row["starred_at"] == body["starred_at"]
    assert row["saved_at"] == body["starred_at"]

    cleared = await _star(app, query_hash, {"starred": False, "target": "demo"})

    assert cleared.status_code == 200
    assert cleared.json()["starred"] is False
    assert cleared.json()["starred_at"] == ""
    assert _stored_saved_at(registry_path, query_hash) == ""
    row = (await _rows(app, {"target": "demo", "view": "all"}))[0]
    assert row["starred"] is False
    assert row["starred_at"] == ""
    assert row["saved_at"] == ""


@pytest.mark.asyncio
async def test_starring_twice_keeps_the_moment_it_was_first_starred(
    app, registry, registry_path
):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo",
    )

    first = await _star(app, query_hash, {"starred": True, "target": "demo"})
    again = await _star(app, query_hash, {"starred": True, "target": "demo"})

    assert again.json()["starred_at"] == first.json()["starred_at"]


@pytest.mark.asyncio
async def test_the_star_is_per_target(app, registry, registry_path):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo", observed=True,
    )
    registry.add_query(
        sql=PG_TEXT, source="top-historical", target="analytics", observed=True,
    )

    await _star(app, query_hash, {"starred": True, "target": "demo"})

    assert _stored_saved_at(registry_path, query_hash, "demo")
    assert _stored_saved_at(registry_path, query_hash, "analytics") == ""


@pytest.mark.asyncio
async def test_an_empty_target_uses_the_query_home(app, registry, registry_path):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo",
    )

    response = await _star(app, query_hash, {"starred": True})

    assert response.json()["target"] == "demo"
    assert _stored_saved_at(registry_path, query_hash)


@pytest.mark.asyncio
async def test_unstarring_a_target_with_no_history_writes_nothing(
    app, registry, registry_path
):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo",
    )

    response = await _star(app, query_hash, {"starred": False, "target": "other"})

    assert response.status_code == 200
    assert response.json() == {
        "hash": query_hash, "target": "other", "starred": False, "starred_at": "",
    }
    reader = QueryRegistry(registry_path=registry_path)
    reader.load()
    assert reader.get_query(query_hash).belongs_to_target("other") is False


@pytest.mark.asyncio
async def test_unknown_hash_is_not_found(app, registry):
    response = await _star(app, "deadbeef0000", {"starred": True, "target": "demo"})

    assert response.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"target": "demo"},
        {"starred": "yes", "target": "demo"},
        {"starred": 1, "target": "demo"},
        {"starred": None, "target": "demo"},
        {"starred": True, "target": 7},
    ],
)
@pytest.mark.asyncio
async def test_malformed_body_is_rejected(app, registry, registry_path, payload):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo",
    )

    response = await _star(app, query_hash, payload)

    assert response.status_code == 422
    assert _stored_saved_at(registry_path, query_hash) == ""


@pytest.mark.asyncio
async def test_cross_site_origin_is_forbidden(app, registry, registry_path):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo",
    )

    response = await _star(
        app,
        query_hash,
        {"starred": True, "target": "demo"},
        headers={"origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert _stored_saved_at(registry_path, query_hash) == ""


# -- nothing RDST does on its own stars a query ------------------------------


@pytest.mark.asyncio
async def test_the_add_query_dialog_stars_what_it_adds(app, registry, registry_path):
    async with _client(app) as client:
        response = await client.post(
            "/api/query-registry", json={"sql": PG_TEXT, "target": "demo"},
        )

    query_hash = response.json()["hash"]
    assert _stored_saved_at(registry_path, query_hash)
    row = (await _rows(app, {"target": "demo", "view": "all"}))[0]
    assert row["starred"] is True


@pytest.mark.asyncio
async def test_the_cache_save_path_leaves_the_row_unstarred(
    app, registry, registry_path
):
    from features.cache.service import CacheService

    query_hash = CacheService()._save_to_registry(PG_TEXT, "posts by score", "demo")

    assert query_hash
    assert _stored_saved_at(registry_path, query_hash) == ""
    row = (await _rows(app, {"target": "demo", "view": "all"}))[0]
    assert row["starred"] is False


@pytest.mark.asyncio
async def test_audit_capture_leaves_the_rows_it_saves_unstarred(
    app, registry, registry_path
):
    """Capture admits whatever the database ran; none of it is the user's."""
    from features.audit import capture_service as capture_module
    from features.audit.capture_service import CaptureService
    from features.audit.models import DatabaseSnapshot

    end_rows = [{
        "queryid": "1",
        "query": PG_TEXT,
        "calls": 5,
        "total_exec_time": 50.0,
        "mean_exec_time": 10.0,
        "max_exec_time": 12.0,
        "shared_blks_hit": 0,
        "shared_blks_read": 0,
        "rows": 5,
    }]
    snapshot = DatabaseSnapshot(
        timestamp="t", cache_hit_ratio=0.9, active_connections=1
    )
    fake_time = MagicMock()
    fake_time.monotonic.side_effect = itertools.count(0, 100)

    class _Config:
        def load(self):
            return None

        def get(self, name):
            return {"engine": "postgresql", "host": "localhost"}

    service = CaptureService(config=_Config())

    with (
        patch(
            "shared.db_connection.create_direct_connection", return_value=MagicMock()
        ),
        patch.object(capture_module, "time", fake_time),
        patch.object(
            capture_module.query_stats_module, "collect_database_snapshot",
            return_value=snapshot,
        ),
        patch.object(
            capture_module.query_stats_module, "collect_table_stats", return_value=[],
        ),
        patch.object(
            capture_module.query_stats_module, "collect_pg_stat_statements",
            side_effect=[[], end_rows],
        ),
        patch.object(
            capture_module.query_stats_module, "compute_snapshot_delta",
            return_value={},
        ),
        patch.object(
            CaptureService, "_collect_metrics_audit", new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        captured = service.run_capture(
            "demo",
            duration_seconds=10,
            run_analysis=False,
            save_top_queries=1,
            save_capture=False,
        )
        assert [event async for event in captured]

    reader = QueryRegistry(registry_path=registry_path)
    reader.load()
    saved = reader.list_queries()
    assert len(saved) == 1
    assert saved[0].lifecycle_for("demo").saved_at == ""
    row = (await _rows(app, {"target": "demo", "view": "all"}))[0]
    assert row["starred"] is False


# -- the compare outcome the drawer reads ------------------------------------


@pytest.mark.asyncio
async def test_last_compare_is_null_until_a_run_records_one(app, registry):
    registry.add_query(sql=PG_TEXT, source="top-historical", target="demo")

    row = (await _rows(app, {"target": "demo", "view": "all"}))[0]

    assert row["last_compare"] is None


@pytest.mark.asyncio
async def test_last_compare_is_exposed_once_a_run_records_one(app, registry):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo",
    )

    async with _client(app) as client:
        outcome = await client.post(
            f"/api/query-registry/queries/{query_hash}/compare-outcome",
            json={
                "target": "demo",
                "status": "improved",
                "readyset_ms": 4.0,
                "origin_ms": 180.0,
                "detail": "10 runs each",
            },
        )

    row = (await _rows(app, {"target": "demo", "view": "all"}))[0]
    assert row["last_compare"] == {
        "status": "improved",
        "at": outcome.json()["last_compare"]["at"],
        "readyset_ms": 4.0,
        "origin_ms": 180.0,
        "detail": "10 runs each",
    }


@pytest.mark.asyncio
async def test_unmeasured_compare_fields_come_back_null(app, registry):
    query_hash, _ = registry.add_query(
        sql=PG_TEXT, source="top-historical", target="demo",
    )

    async with _client(app) as client:
        await client.post(
            f"/api/query-registry/queries/{query_hash}/compare-outcome",
            json={"target": "demo", "status": "not_comparable"},
        )

    row = (await _rows(app, {"target": "demo", "view": "all"}))[0]
    assert row["last_compare"]["status"] == "not_comparable"
    assert row["last_compare"]["readyset_ms"] is None
    assert row["last_compare"]["origin_ms"] is None
    assert row["last_compare"]["detail"] is None

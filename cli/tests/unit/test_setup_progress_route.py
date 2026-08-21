"""Setup progress: five booleans, each derived from real state.

Every signal is asserted from the state that produces it -- a target in
the config, a semantic-layer file, a registry row, a stored analysis, a
recorded compare -- rather than from a flag the client could have set.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import toml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from shared.api.routes import setup_progress as setup_progress_routes
from shared.query_registry import QueryRegistry
from shared.query_registry.analysis_results import (
    AnalysisResultsRegistry,
    create_analysis_result,
)

# An exact instance of the profiler's top-values template: structurally
# RDST's own traffic, whatever admission let it in.
SELF_TRAFFIC_SQL = (
    'SELECT "body"::text, COUNT(*) AS cnt FROM "posts" TABLESAMPLE SYSTEM($1) '
    'WHERE "body" IS NOT NULL GROUP BY "body" ORDER BY cnt DESC LIMIT $2'
)


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(setup_progress_routes.router, prefix="/api")
    return app


async def _progress(app: FastAPI, query: str = "") -> dict:
    transport = ASGITransport(app=app, client=("127.0.0.1", 54321))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/setup-progress{query}")
    assert response.status_code == 200
    return response.json()


def _connect(tmp_rdst_home: Path, name: str = "demo", default: bool = True) -> None:
    path = tmp_rdst_home / "config.toml"
    data = toml.load(path) if path.exists() else {"targets": {}, "default": None}
    data.setdefault("targets", {})[name] = {
        "engine": "postgresql",
        "host": "localhost",
        "database": "demo",
    }
    if default:
        data["default"] = name
    with open(path, "w", encoding="utf-8") as handle:
        toml.dump(data, handle)


def _build_schema(tmp_rdst_home: Path, name: str = "demo") -> None:
    directory = tmp_rdst_home / "semantic-layer"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.yaml").write_text("tables: {}\n")


def _observe_query(sql: str, target: str = "demo") -> str:
    registry = QueryRegistry()
    registry.load()
    query_hash, _ = registry.add_query(sql=sql, source="manual", target=target)
    return query_hash


def _analyze(query_hash: str, target: str = "demo") -> None:
    AnalysisResultsRegistry().store_analysis_result(
        query_hash,
        create_analysis_result(
            query_hash=query_hash,
            target=target,
            performance_metrics={},
            llm_analysis={},
            explain_plan={},
            query_metrics={},
        ),
    )


def _compare(query_hash: str, target: str = "demo") -> None:
    registry = QueryRegistry()
    registry.load()
    lifecycle = registry.get_query(query_hash).lifecycle_for(target, create=True)
    lifecycle.comparison_count += 1
    lifecycle.last_compared_at = "2026-08-18T19:59:00Z"
    registry.save()


@pytest.mark.asyncio
async def test_a_fresh_install_has_done_nothing(app):
    assert await _progress(app) == {
        "target": "",
        "connected": False,
        "schema_built": False,
        "queries_found": False,
        "analyzed": False,
        "compared": False,
        "error": None,
    }


@pytest.mark.asyncio
async def test_connecting_a_database_completes_only_that_step(app, tmp_rdst_home):
    _connect(tmp_rdst_home)

    body = await _progress(app)

    assert body["target"] == "demo"
    assert body["connected"] is True
    assert [body["schema_built"], body["queries_found"]] == [False, False]
    assert [body["analyzed"], body["compared"]] == [False, False]


@pytest.mark.asyncio
async def test_a_semantic_layer_completes_the_schema_step(app, tmp_rdst_home):
    _connect(tmp_rdst_home)
    _build_schema(tmp_rdst_home)

    body = await _progress(app)

    assert body["schema_built"] is True
    assert body["queries_found"] is False


@pytest.mark.asyncio
async def test_a_registry_row_completes_the_queries_step(app, tmp_rdst_home):
    _connect(tmp_rdst_home)
    _observe_query("SELECT title FROM posts WHERE score > 10")

    body = await _progress(app)

    assert body["queries_found"] is True
    assert body["analyzed"] is False


@pytest.mark.asyncio
async def test_self_traffic_alone_does_not_count_as_queries(app, tmp_rdst_home):
    _connect(tmp_rdst_home)
    _observe_query(SELF_TRAFFIC_SQL)

    body = await _progress(app)

    assert body["queries_found"] is False


@pytest.mark.asyncio
async def test_a_stored_analysis_completes_the_analyze_step(app, tmp_rdst_home):
    _connect(tmp_rdst_home)
    _analyze(_observe_query("SELECT title FROM posts WHERE score > 10"))

    body = await _progress(app)

    assert body["analyzed"] is True
    assert body["compared"] is False


@pytest.mark.asyncio
async def test_an_analysis_on_another_target_does_not_count(app, tmp_rdst_home):
    _connect(tmp_rdst_home)
    _analyze(
        _observe_query("SELECT title FROM posts WHERE score > 10"), target="staging"
    )

    assert (await _progress(app))["analyzed"] is False


@pytest.mark.asyncio
async def test_a_recorded_compare_completes_the_last_step(app, tmp_rdst_home):
    _connect(tmp_rdst_home)
    query_hash = _observe_query("SELECT title FROM posts WHERE score > 10")
    _analyze(query_hash)
    _compare(query_hash)

    body = await _progress(app)

    assert [body[step] for step in ("connected", "queries_found", "analyzed")] == [
        True,
        True,
        True,
    ]
    assert body["compared"] is True


@pytest.mark.asyncio
async def test_the_target_parameter_overrides_the_default(app, tmp_rdst_home):
    _connect(tmp_rdst_home)
    _connect(tmp_rdst_home, name="staging", default=False)
    _build_schema(tmp_rdst_home, name="staging")
    _observe_query("SELECT title FROM posts WHERE score > 10", target="staging")

    default_target = await _progress(app)
    named = await _progress(app, "?target=staging")

    assert default_target["target"] == "demo"
    assert [default_target["schema_built"], default_target["queries_found"]] == [
        False,
        False,
    ]
    assert named["target"] == "staging"
    assert [named["schema_built"], named["queries_found"]] == [True, True]


@pytest.mark.asyncio
async def test_a_non_loopback_caller_is_refused(app):
    transport = ASGITransport(app=app, client=("10.0.0.9", 54321))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/setup-progress")

    assert response.status_code == 403

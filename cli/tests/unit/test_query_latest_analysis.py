"""Latest-analysis summary: workflow persistence and the registry API."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import features.analyze.functions.workflow_integration as workflow_integration
import features.query_registry.api.routes as query_routes
from shared.query_registry.analysis_results import (
    AnalysisResultsRegistry,
    create_analysis_result,
)
from shared.query_registry.query_registry import QueryRegistry


pytestmark = pytest.mark.usefixtures("run_blocking_inline")


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(query_routes.router, prefix="/api")
    return app


@pytest.fixture
def analysis_registry(monkeypatch, tmp_path) -> AnalysisResultsRegistry:
    instance = AnalysisResultsRegistry(
        registry_path=str(tmp_path / "analysis_results.toml")
    )

    import shared.query_registry as shared_registry

    monkeypatch.setattr(
        shared_registry, "AnalysisResultsRegistry", lambda *a, **k: instance
    )
    return instance


@pytest.fixture
def registry(monkeypatch, tmp_path) -> QueryRegistry:
    instance = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    instance.load()
    monkeypatch.setattr(workflow_integration, "QueryRegistry", lambda: instance)
    return instance


@pytest.mark.asyncio
async def test_latest_analysis_present_for_analyzed_query(app, analysis_registry):
    analysis_registry.store_analysis_result(
        "hash-analyzed",
        create_analysis_result(
            query_hash="hash-analyzed",
            target="demo",
            performance_metrics={},
            llm_analysis={
                "performance_assessment": {
                    "overall_rating": "good",
                    "efficiency_score": 82,
                }
            },
            explain_plan={},
            query_metrics={},
        ),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/query-registry/hash-analyzed/analysis/latest"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["found"] is True
    assert body["error"] is None
    analysis = body["analysis"]
    assert analysis["analysis_id"]
    assert analysis["analyzed_at"]
    assert analysis["target"] == "demo"
    assert analysis["overall_rating"] == "good"
    assert analysis["efficiency_score"] == 82.0


@pytest.mark.asyncio
async def test_latest_analysis_absent_for_unanalyzed_query(app, analysis_registry):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/query-registry/hash-unanalyzed/analysis/latest"
        )

    assert response.status_code == 200
    assert response.json() == {"found": False, "analysis": None, "error": None}


@pytest.mark.asyncio
async def test_latest_analysis_returns_newest_of_multiple(app, analysis_registry):
    for score, timestamp in ((40, "2026-08-01T00:00:00Z"), (90, "2026-08-02T00:00:00Z")):
        result = create_analysis_result(
            query_hash="hash-multi",
            target="demo",
            performance_metrics={},
            llm_analysis={
                "performance_assessment": {
                    "overall_rating": "good",
                    "efficiency_score": score,
                }
            },
            explain_plan={},
            query_metrics={},
        )
        result.timestamp = timestamp
        analysis_registry.store_analysis_result("hash-multi", result)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/query-registry/hash-multi/analysis/latest")

    analysis = response.json()["analysis"]
    assert analysis["analyzed_at"] == "2026-08-02T00:00:00Z"
    assert analysis["efficiency_score"] == 90.0


def test_store_analysis_results_persists_compact_summary(
    registry, analysis_registry
):
    result = workflow_integration.store_analysis_results(
        query="SELECT * FROM users WHERE id = 7",
        target="demo",
        llm_analysis={
            "performance_assessment": {
                "overall_rating": "fair",
                "efficiency_score": 55,
            },
            "llm_model": "test-model",
            "tokens_used": 1234,
        },
    )

    assert result["success"] is True
    stored = analysis_registry.get_latest_analysis(result["query_hash"])
    assert stored is not None
    assert stored.analysis_id
    assert stored.target == "demo"
    assert stored.llm_analysis == {
        "performance_assessment": {"overall_rating": "fair", "efficiency_score": 55.0}
    }
    assert stored.llm_model_used == "test-model"
    assert stored.tokens_used == 1234


def test_store_analysis_results_survives_summary_store_failure(
    monkeypatch, registry
):
    import shared.query_registry as shared_registry

    def broken(*a, **k):
        raise RuntimeError("summary store unavailable")

    monkeypatch.setattr(shared_registry, "AnalysisResultsRegistry", broken)

    result = workflow_integration.store_analysis_results(
        query="SELECT * FROM users WHERE id = 8",
        target="demo",
        llm_analysis={"performance_assessment": {"overall_rating": "good"}},
    )

    assert result["success"] is True
    assert result["analysis_id"] == result["query_hash"]

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


def test_store_analysis_results_persists_the_whole_record(
    registry, analysis_registry
):
    llm_analysis = {
        "performance_assessment": {
            "overall_rating": "fair",
            "efficiency_score": 55,
        },
        "llm_model": "test-model",
        "tokens_used": 1234,
    }
    result = workflow_integration.store_analysis_results(
        query="SELECT * FROM users WHERE id = 7",
        target="demo",
        llm_analysis=llm_analysis,
        explain_results={
            "database_engine": "postgresql",
            "execution_time_ms": 12.5,
            "rows_returned": 3,
            "explain_plan": {"Node Type": "Index Scan"},
        },
        query_metrics={"calls": 42},
        optimization_suggestions={
            "rewrite_suggestions": [{"title": "add a filter"}],
            "index_recommendations": [{"table": "users"}],
        },
        rewrite_test_results={"tested": True},
    )

    assert result["success"] is True
    stored = analysis_registry.get_latest_analysis(result["query_hash"])
    assert stored is not None
    assert stored.analysis_id == result["stored_analysis_id"]
    assert stored.target == "demo"
    # The viewer reopens this record instead of re-running, so it keeps the
    # measured numbers and the plan, not the assessment alone.
    assert stored.llm_analysis == llm_analysis
    assert stored.performance_metrics == {
        "execution_time_ms": 12.5,
        "rows_returned": 3,
    }
    assert stored.explain_plan == {"Node Type": "Index Scan"}
    assert stored.query_metrics == {"calls": 42}
    assert stored.rewrite_suggestions == [{"title": "add a filter"}]
    assert stored.index_suggestions == [{"table": "users"}]
    assert stored.rewrite_test_results == {"tested": True}
    assert stored.database_engine == "postgresql"
    assert stored.llm_model_used == "test-model"
    assert stored.tokens_used == 1234


def test_store_analysis_results_reports_summary_store_failure(
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

    assert result["success"] is False
    assert result["error"] == "Failed to store analysis results: summary store unavailable"
    assert result["analysis_id"] is None


@pytest.mark.parametrize("literal", ["'a,b),($4'", "$$a,b),($4$$"])
def test_store_postgres_values_with_adjacent_parameterized_rows(
    registry, analysis_registry, literal, monkeypatch
):
    from unittest.mock import Mock

    add_query = Mock(wraps=registry.add_query)
    monkeypatch.setattr(registry, "add_query", add_query)
    result = workflow_integration.store_analysis_results(
        query=("SELECT key, pg_catalog.set_config(key, val, $1) as val "
               "FROM (VALUES ($2, ($3)::pg_catalog.text),($4, ($5)::pg_catalog.text)) "
               f"as v(key, val) WHERE key <> {literal}"),
        target="demo",
        source="monitor",
        explain_results={"success": True, "database_engine": "postgresql"},
    )

    assert result["success"] is True
    assert result["query_hash"]
    stored = analysis_registry.get_latest_analysis(result["query_hash"])
    assert stored.analysis_id == result["stored_analysis_id"]
    assert literal in add_query.call_args.kwargs["sql"]


@pytest.mark.asyncio
async def test_monitor_values_analysis_stores_complete_result(
    monkeypatch, registry, analysis_registry
):
    from unittest.mock import AsyncMock

    from features.analyze.models import AnalyzeInput, AnalyzeOptions
    from features.analyze.service import ANALYZE_WORKFLOW_FUNCTIONS, AnalyzeService

    sql = ("SELECT key, pg_catalog.set_config(key, val, $1) as val "
           "FROM (VALUES ($2, ($3)::pg_catalog.text),($4, ($5)::pg_catalog.text)) as v(key, val)")
    service = AnalyzeService()
    monkeypatch.setattr(service, "_load_config", AsyncMock(return_value=(
        "demo", {"engine": "postgresql"},
    )))
    outputs = {
        "execute_explain_analyze": {"success": True, "database_engine": "postgresql",
                                    "explain_plan": {"Node Type": "Values Scan"}},
        "collect_query_metrics": {"success": True},
        "collect_target_schema": {"success": True, "schema_info": {}},
        "analyze_with_llm": {"success": True},
        "test_index_recommendations": {"tested": False},
    }
    for name, output in outputs.items():
        monkeypatch.setitem(ANALYZE_WORKFLOW_FUNCTIONS, name, lambda result=output, **kwargs: result)

    events = [event async for event in service.analyze(
        AnalyzeInput(sql=sql, normalized_sql=sql, source="monitor", hash="monitor_hash"),
        AnalyzeOptions(target="demo", fast=True, non_interactive=True,
                       statement_timeout_ms=1000, test_rewrites=False),
    )]

    assert events[-1].type == "complete"
    complete = events[-1]
    assert complete.success
    assert complete.query_hash and complete.stored_analysis_id
    assert analysis_registry.get_analysis_by_id(complete.query_hash, complete.stored_analysis_id)

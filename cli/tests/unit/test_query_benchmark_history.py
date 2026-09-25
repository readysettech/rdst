"""Per-query benchmark history: what a finished benchmark keeps about each query it
measured, and the query detail's read of it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.query_registry.api import routes
from features.query_registry.models import QueryBenchmarkStats
from shared.agent_activity import recorder
from shared.agent_activity.store import AgentActivityStore


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _stats(query_hash, p50, p95, successes, readyset=None):
    stats = QueryBenchmarkStats(
        query_name=f"q {query_hash}", query_hash=query_hash, executions=successes,
        successes=successes, failures=0, min_ms=p50, avg_ms=p50, p50_ms=p50, p95_ms=p95,
        p99_ms=p95, max_ms=p95)
    if readyset:
        stats.lanes = {"origin": {}, "readyset": readyset}
    return stats


def _record(target, queries, *, lanes=("origin",), setup=None):
    event = SimpleNamespace(type="complete", elapsed_seconds=30.0, total_executions=0,
                            queries=queries, readyset_setup=setup)
    return recorder.record_benchmark(
        target, event, mode="interval", lanes=list(lanes), duration_seconds=30,
        concurrency=1, interval_ms=100, statement_timeout_ms=5000)


def test_a_benchmark_keeps_each_querys_numbers(data_dir):
    readyset = {"executions": 600, "successes": 600, "failures": 0, "p50_ms": 0.4,
                "p95_ms": 0.9, "avg_ms": 0.5}
    _record("shop", [_stats("aaa", 12.0, 30.0, 300, readyset), _stats("bbb", 2.0, 4.0, 90)],
            lanes=("origin", "readyset"), setup={"status": "ok"})
    _record("shop", [_stats("bbb", 3.0, 5.0, 60)])
    _record("other", [_stats("aaa", 1.0, 1.0, 10)])
    store = AgentActivityStore()

    runs = store.benchmarks_for_query("shop", "aaa")
    assert len(runs) == 1
    run = runs[0]
    assert (run["mode"], run["lanes"], run["duration_seconds"]) == (
        "interval", ["origin", "readyset"], 30)
    assert run["readyset_setup"] == {"status": "ok"}
    assert (run["query"]["p50_ms"], run["query"]["p95_ms"], run["query"]["qps"]) == (
        12.0, 30.0, 10.0)
    assert run["query"]["readyset"]["p50_ms"] == 0.4
    assert run["query"]["readyset"]["qps"] == 20.0

    newest_first = store.benchmarks_for_query("shop", "bbb")
    assert [run["query"]["p50_ms"] for run in newest_first] == [3.0, 2.0]
    assert "readyset" not in newest_first[0]["query"]
    assert store.benchmarks_for_query("shop", "zzz") == []


def test_history_route_reads_the_query_on_one_database(data_dir):
    _record("shop", [_stats("aaa", 12.0, 30.0, 300)])
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    client = TestClient(app)

    body = client.get("/api/query-registry/queries/aaa/benchmarks?target=shop").json()
    assert len(body["runs"]) == 1
    run = body["runs"][0]
    assert run["outcome"] == "done" and run["query"]["hash"] == "aaa"
    assert run["query"]["readyset"] is None
    assert client.get(
        "/api/query-registry/queries/aaa/benchmarks?target=other").json() == {"runs": []}
    assert client.get("/api/query-registry/queries/aaa/benchmarks").status_code == 422


def test_readyset_only_history_keeps_lane_identity(data_dir):
    stats = _stats("aaa", 0.4, 0.9, 600)
    stats.lanes = {"readyset": {"executions": 600, "successes": 600,
                              "failures": 0, "p50_ms": 0.4, "p95_ms": 0.9}}
    _record("shop", [stats], lanes=("readyset",))
    run = AgentActivityStore().benchmarks_for_query("shop", "aaa")[0]
    assert run["lanes"] == ["readyset"]
    assert run["query"]["readyset"]["p50_ms"] == 0.4

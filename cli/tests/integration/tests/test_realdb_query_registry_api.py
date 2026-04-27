"""Real-database integration tests for the query registry API.

Covers the only path that *needs* a live DB: `/query-registry/benchmark`.
Register a query, run it for a brief duration against the container,
assert SSE progress + complete events with non-zero execution counts.
That timing assertion is what the in-process suite cannot make.

To run locally:

    cd rdst/tests/integration
    docker compose up -d postgres   # or: mysql
    export RDST_TEST_PASSWORD=testpassword
    export RDST_TEST_ENGINE=postgresql
    pytest tests/test_realdb_query_registry_api.py -v -m realdb
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.realdb


TARGET_NAME = "itregistry"
SAMPLE_SQL = (
    "SELECT primarytitle FROM title_basics "
    "WHERE titletype = 'movie' LIMIT 5"
)


@pytest.fixture(autouse=True)
def _set_test_password(monkeypatch):
    if not os.environ.get("RDST_TEST_PASSWORD"):
        monkeypatch.setenv("RDST_TEST_PASSWORD", "testpassword")


async def _add_target(client, payload: dict) -> None:
    response = await client.post(
        "/api/configure/targets",
        json={"name": TARGET_NAME, "target": payload},
    )
    assert response.status_code == 200, response.text


async def test_register_then_benchmark_runs_against_db(
    client, db_target_payload, collect_sse_events
):
    """Register a query, run a short benchmark, assert real execution
    happened (non-zero successes, completion event)."""
    await _add_target(client, db_target_payload)

    # Step 1: register a query in the shared registry.
    add = await client.post(
        "/api/query-registry",
        json={"sql": SAMPLE_SQL, "target": TARGET_NAME},
    )
    assert add.status_code == 200, add.text
    add_body = add.json()
    assert add_body["success"] is True, add_body
    query_hash = add_body["hash"]
    assert query_hash, add_body

    # Step 2: run a 1-second interval benchmark.
    events = await collect_sse_events(
        client,
        "POST",
        "/api/query-registry/benchmark",
        json_body={
            "queries": [query_hash],
            "target": TARGET_NAME,
            "mode": "interval",
            "interval_ms": 50,
            "duration_seconds": 1,
        },
    )

    error_events = [e for e in events if e.get("event") == "error"]
    assert not error_events, f"Benchmark emitted error events: {error_events}"

    complete = next((e for e in events if e.get("event") == "complete"), None)
    assert complete is not None, (
        f"Benchmark stream did not reach 'complete'. "
        f"Events seen: {[e.get('event') for e in events]}"
    )

    data = complete["data"]
    assert data["total_executions"] > 0, f"No executions recorded: {data}"
    assert data["total_successes"] > 0, f"No successful executions: {data}"

    # The per-query stats should pin our query and show real timing.
    queries = data.get("queries") or []
    assert queries, f"complete carried no per-query stats: {data}"
    stats = queries[0]
    assert stats["executions"] > 0
    assert stats["successes"] > 0
    assert stats["max_ms"] >= stats["min_ms"] >= 0.0

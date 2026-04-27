"""
Real-database API integration tests: configure → analyze.

Drives the FastAPI app over httpx (in-process via ASGITransport) but lets
every call land on the real Postgres/MySQL container started by
`run_api_integration_tests.sh`. The service layer is NOT mocked — that is
the whole point of the `realdb` slice. If `features/configure/...` or
`features/analyze/...` regresses, this file should catch it.

To run locally:

    cd rdst/tests/integration
    docker compose up -d postgres
    export RDST_TEST_PASSWORD=testpassword
    export RDST_TEST_ENGINE=postgresql
    pytest tests/test_realdb_configure_analyze_api.py -v -m realdb
"""

from __future__ import annotations

import json
import os
import re

import pytest

# Every test in this module is gated on a real DB container being up.
pytestmark = pytest.mark.realdb

# The Readyset-cache assertion in `test_analyze_with_readyset_cache_flag`
# additionally requires a Readyset container; that single test is skipped
# via the same gate as the rest of slice 5.
_SKIP_READYSET = (
    os.environ.get("SKIP_READYSET_CACHE_TESTS", "false").lower() == "true"
)


TARGET_NAME = "ittest"

# Hits the indexed `titletype` column from the seeded IMDb-like schema
# (see tests/integration/init-scripts/postgres/01-schema.sql). Works
# unchanged on MySQL because both seeds expose the same table shape.
SAMPLE_QUERY = (
    "SELECT primarytitle, startyear "
    "FROM title_basics "
    "WHERE titletype = 'movie' "
    "ORDER BY startyear DESC "
    "LIMIT 10"
)


@pytest.fixture(autouse=True)
def _set_test_password(monkeypatch):
    """Make sure RDST_TEST_PASSWORD is set even if a developer runs the
    file without going through the runner script. CI exports it for real."""
    if not os.environ.get("RDST_TEST_PASSWORD"):
        monkeypatch.setenv("RDST_TEST_PASSWORD", "testpassword")


async def _add_target(client, name: str, target_payload: dict) -> dict:
    """Helper: POST /api/configure/targets and return the parsed body."""
    response = await client.post(
        "/api/configure/targets",
        json={"name": name, "target": target_payload},
    )
    assert response.status_code == 200, response.text
    return response.json()


# ============================================================================
# Tests
# ============================================================================


async def test_add_target_then_list_returns_it(client, db_target_payload, db_engine):
    """Configure a target against the live container, then read it back via the API.

    Asserts that what we POSTed survives the round-trip through the real
    ConfigureService + on-disk config file. No service-layer mocks.
    """
    add_body = await _add_target(client, TARGET_NAME, db_target_payload)
    assert add_body["success"] is True
    assert add_body["target_name"] == TARGET_NAME

    list_response = await client.get("/api/configure/targets")
    assert list_response.status_code == 200, list_response.text

    targets = list_response.json()["targets"]
    by_name = {t["name"]: t for t in targets}
    assert TARGET_NAME in by_name, f"Configured target missing from list: {targets}"

    entry = by_name[TARGET_NAME]
    assert entry["engine"] == db_engine
    # Host/port are stringified in some list views; compare loosely.
    assert str(entry["host"]) == str(db_target_payload["host"])
    assert str(entry["port"]) == str(db_target_payload["port"])


async def test_add_target_then_analyze_real_query(
    client, db_target_payload, collect_sse_events
):
    """End-to-end: configure → analyze → SSE stream completes against the live DB.

    We only assert the shape of the stream (no `error` events, a `complete`
    event arrived, EXPLAIN actually ran). We deliberately do not pin the
    contents of `explain_plan` because plan strings vary across PG/MySQL
    versions.
    """
    await _add_target(client, TARGET_NAME, db_target_payload)

    events = await collect_sse_events(
        client,
        "POST",
        "/api/analyze",
        json_body={
            "query": SAMPLE_QUERY,
            "target": TARGET_NAME,
            "fast": True,
            "skip_rewrites": True,
        },
    )

    by_event = {e["event"]: e["data"] for e in events if "event" in e}
    error_events = [e for e in events if e.get("event") == "error"]

    assert not error_events, f"Analyze stream emitted error events: {error_events}"
    assert "complete" in by_event, (
        f"Analyze stream did not reach 'complete'. Events seen: "
        f"{[e.get('event') for e in events]}"
    )

    explain = by_event.get("explain_complete")
    assert explain is not None, "No explain_complete event from real DB"
    assert explain.get("success") is True, f"EXPLAIN failed: {explain}"
    assert explain.get("explain_plan"), "explain_plan is empty"

    # Sanity: the query we sent is what the analyzer ran. Cheap check that
    # we did not accidentally short-circuit somewhere upstream.
    plan_blob = json.dumps(explain["explain_plan"])
    assert re.search(r"title_basics", plan_blob, re.IGNORECASE), (
        f"EXPLAIN plan does not reference title_basics; got: {plan_blob[:500]}"
    )


@pytest.mark.skipif(
    _SKIP_READYSET,
    reason="Readyset container path disabled via SKIP_READYSET_CACHE_TESTS",
)
async def test_analyze_with_readyset_cache_flag(
    client, db_target_payload, collect_sse_events
):
    """`readyset_cache=true` should drive the analyze flow through the
    Readyset cacheability check and emit a `readyset_checked` event
    with `cacheable` populated (true or false — both are valid; we
    only assert the field is present, not its value).
    """
    await _add_target(client, TARGET_NAME, db_target_payload)

    events = await collect_sse_events(
        client,
        "POST",
        "/api/analyze",
        json_body={
            "query": SAMPLE_QUERY,
            "target": TARGET_NAME,
            "fast": True,
            "skip_rewrites": True,
            "readyset_cache": True,
        },
    )

    error_events = [e for e in events if e.get("event") == "error"]
    assert not error_events, f"Analyze stream emitted error events: {error_events}"

    checked = next(
        (e for e in events if e.get("event") == "readyset_checked"), None
    )
    assert checked is not None, (
        f"Analyze stream did not emit 'readyset_checked'. Events seen: "
        f"{[e.get('event') for e in events]}"
    )
    data = checked["data"]
    assert data.get("checked") is True, data
    assert "cacheable" in data, data

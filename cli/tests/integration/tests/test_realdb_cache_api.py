"""Real-database integration tests for the cache API lifecycle.

These tests stand up a Readyset container against the live test DB
and walk through the full deploy → add → run → list → remove cycle
through `/api/cache/*`. They share a single test on purpose because
container startup dominates the cost and splitting per endpoint just
multiplies that cost.

Skipped when `SKIP_READYSET_CACHE_TESTS=true` (which the CI runner
sets — Readyset can't reach the upstream DB across the compose
network in our current Buildkite shape). Locally, leave that var
unset to run the full lifecycle.

To run locally:

    cd rdst/tests/integration
    docker compose up -d postgres
    export RDST_TEST_PASSWORD=testpassword
    export RDST_TEST_ENGINE=postgresql
    unset SKIP_READYSET_CACHE_TESTS
    pytest tests/test_realdb_cache_api.py -v -m realdb
"""

from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = [
    pytest.mark.realdb,
    pytest.mark.skipif(
        os.environ.get("SKIP_READYSET_CACHE_TESTS", "false").lower() == "true",
        reason="Readyset container path disabled via SKIP_READYSET_CACHE_TESTS",
    ),
]


TARGET_NAME = "itcache"
SAMPLE_QUERY = (
    "SELECT primarytitle, startyear FROM title_basics "
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


async def _wait_for_running(client, *, timeout_s: int = 60) -> dict:
    """Poll `/api/cache/status` until `running=True` or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    last_body: dict = {}
    while asyncio.get_event_loop().time() < deadline:
        response = await client.get(f"/api/cache/status?target={TARGET_NAME}")
        assert response.status_code == 200, response.text
        last_body = response.json()
        if last_body.get("running") is True:
            return last_body
        await asyncio.sleep(2)
    pytest.fail(
        f"Cache never reached running=True within {timeout_s}s; "
        f"last status: {last_body!r}"
    )


async def test_cache_deploy_add_run_remove_lifecycle(
    client, db_target_payload, collect_sse_events
):
    """End-to-end: deploy a Readyset container, cache a query, run the
    speedup comparison, list it, then remove and confirm status flips.

    Asserts only the high-level flow: every step succeeds, terminal
    events are emitted, the listed cache references our query. We do
    not pin speedup numbers — they are environment-dependent.
    """
    await _add_target(client, db_target_payload)

    # Step 1: deploy. Container creation can take a while on cold start.
    deploy_events = await collect_sse_events(
        client,
        "POST",
        "/api/cache/deploy",
        json_body={"target": TARGET_NAME, "mode": "docker"},
    )
    deploy_errors = [e for e in deploy_events if e.get("event") == "error"]
    assert not deploy_errors, f"Deploy emitted errors: {deploy_errors}"
    deploy_complete = next(
        (e for e in deploy_events if e.get("event") == "complete"), None
    )
    assert deploy_complete is not None, (
        f"Deploy never completed; events: {[e.get('event') for e in deploy_events]}"
    )
    assert deploy_complete["data"].get("running") is True, deploy_complete

    # Step 2: confirm running via status (defensive; deploy may have
    # already reported running, but status is the canonical source).
    status_body = await _wait_for_running(client)
    assert status_body.get("deployed") is True

    # Step 3: add the query as a cache.
    add = await client.post(
        "/api/cache/add",
        json={"target": TARGET_NAME, "query": SAMPLE_QUERY},
    )
    assert add.status_code == 200, add.text
    add_body = add.json()
    assert add_body.get("success") is True, add_body
    assert add_body.get("supported") is True, add_body

    # Step 4: run the comparison (origin DB vs cache).
    run_events = await collect_sse_events(
        client,
        "POST",
        "/api/cache/run",
        json_body={
            "target": TARGET_NAME,
            "query": SAMPLE_QUERY,
            "iterations": 5,
            "warmup": 2,
        },
    )
    run_errors = [e for e in run_events if e.get("event") == "error"]
    assert not run_errors, f"Run emitted errors: {run_errors}"
    run_complete = next(
        (e for e in run_events if e.get("event") == "complete"), None
    )
    assert run_complete is not None, (
        f"Run never completed; events: {[e.get('event') for e in run_events]}"
    )
    assert run_complete["data"].get("success") is True, run_complete

    # Step 5: list — our cache should be visible.
    listing = await client.get(f"/api/cache/list?target={TARGET_NAME}")
    assert listing.status_code == 200, listing.text
    listing_body = listing.json()
    assert listing_body.get("success") is True, listing_body
    caches = listing_body.get("caches") or []
    assert caches, f"List returned no caches: {listing_body}"

    # Step 6: remove the cache target — status must reflect it's gone.
    remove = await client.delete(f"/api/cache/remove?target={TARGET_NAME}")
    assert remove.status_code == 200, remove.text
    assert remove.json().get("success") is True, remove.text

    final_status = await client.get(f"/api/cache/status?target={TARGET_NAME}")
    assert final_status.status_code == 200, final_status.text
    final_body = final_status.json()
    assert final_body.get("running") is False, final_body

"""Real-database integration tests for the readyset setup API.

`POST /api/readyset/setup` brings up a Readyset container alongside
the test DB. We assert the SSE stream reaches a `complete` event with
`success=True` and no `error` events. The cache lifecycle test in
`test_realdb_cache_api.py` exercises the same setup as part of its
deploy step, but the dedicated `/readyset/setup` route is a separate
public surface and worth pinning.

Skipped when `SKIP_READYSET_CACHE_TESTS=true`.

To run locally:

    cd rdst/tests/integration
    docker compose up -d postgres
    export RDST_TEST_PASSWORD=testpassword
    export RDST_TEST_ENGINE=postgresql
    unset SKIP_READYSET_CACHE_TESTS
    pytest tests/test_realdb_readyset_api.py -v -m realdb
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.realdb,
    pytest.mark.skipif(
        os.environ.get("SKIP_READYSET_CACHE_TESTS", "false").lower() == "true",
        reason="Readyset container path disabled via SKIP_READYSET_CACHE_TESTS",
    ),
]


TARGET_NAME = "itreadyset"


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


async def test_readyset_setup_streams_to_completion(
    client, db_target_payload, collect_sse_events
):
    """`POST /api/readyset/setup` must reach a `complete` SSE event
    with `success=True`. No `error` events allowed."""
    await _add_target(client, db_target_payload)

    events = await collect_sse_events(
        client,
        "POST",
        "/api/readyset/setup",
        json_body={"target": TARGET_NAME},
    )

    errors = [e for e in events if e.get("event") == "error"]
    assert not errors, f"Setup emitted errors: {errors}"

    complete = next((e for e in events if e.get("event") == "complete"), None)
    assert complete is not None, (
        f"Setup never completed; events: {[e.get('event') for e in events]}"
    )
    data = complete["data"]
    assert data.get("success") is True, data
    # Setup should report a Readyset port — a real container is up.
    assert data.get("readyset_port"), data

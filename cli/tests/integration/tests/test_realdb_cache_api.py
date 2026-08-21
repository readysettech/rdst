"""Docker-backed proof of the web Readyset comparison flow.

This test enters through the API used by the React comparison page, lets the
production lifecycle manager create the managed Readyset container, drains the
real background-run stream, and inspects the physical Docker resource before
deleting the target. Docker, the lifecycle manager, and the cache service are
not replaced with test doubles.

To run locally:

    cd rdst/tests/integration
    docker compose up -d postgres
    export RDST_TEST_PASSWORD=testpassword
    export RDST_TEST_ENGINE=postgresql
    pytest tests/test_realdb_cache_api.py -v -m realdb
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from shared.deploy import READYSET_IMAGE
from shared.deploy.local_docker import inspect_managed_sandbox
from shared.deploy.sandbox_manager import sandbox_manager
from shared.run_registry import run_registry

pytestmark = [
    pytest.mark.realdb,
    pytest.mark.skipif(
        os.environ.get("SKIP_READYSET_CACHE_TESTS", "false").lower() == "true",
        reason="Readyset container path disabled via SKIP_READYSET_CACHE_TESTS",
    ),
]

TARGET_NAME = "itcache"
SAMPLE_QUERY = (
    "SELECT tconst, primarytitle FROM title_basics "
    "WHERE titletype = 'movie' ORDER BY tconst LIMIT 5"
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
    assert response.json()["success"] is True


def _docker_inspect(container_id: str) -> dict:
    result = subprocess.run(
        ["docker", "inspect", container_id],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)[0]


async def test_web_comparison_creates_uses_and_removes_real_container(
    client, db_target_payload, collect_sse_events
):
    """Prove the current web comparison API owns one real Docker sandbox."""
    run_registry.reset()
    await _add_target(client, db_target_payload)

    try:
        start = await client.post(
            "/api/cache/compare-runs",
            json={
                "target": TARGET_NAME,
                "query": SAMPLE_QUERY,
                "concurrency": 1,
                "duration_seconds": 10,
            },
        )
        assert start.status_code == 200, start.text
        run_id = start.json()["run_id"]

        events = await collect_sse_events(
            client,
            "GET",
            f"/api/runs/{run_id}/events",
        )
        errors = [event for event in events if event.get("event") == "error"]
        assert not errors, f"Comparison emitted errors: {errors}"

        complete = next(
            (
                event
                for event in events
                if event.get("event") == "cache_compare_complete"
            ),
            None,
        )
        assert complete is not None, (
            "Comparison never completed; events: "
            f"{[event.get('event') for event in events]}"
        )
        assert complete["data"]["success"] is True
        assert complete["data"]["origin"]["completed"] > 0
        assert complete["data"]["readyset"]["completed"] > 0
        assert events[-1]["event"] == "run_end"
        assert events[-1]["data"]["status"] == "done"

        physical = inspect_managed_sandbox()
        assert physical is not None
        assert physical["running"] is True
        assert physical["managed"] == "true"
        assert physical["target"] == TARGET_NAME
        container_id = physical["id"]

        inspected = _docker_inspect(container_id)
        assert inspected["Id"] == container_id
        assert inspected["Config"]["Image"] == READYSET_IMAGE
        environment = set(inspected["Config"]["Env"])
        assert "PROMETHEUS_METRICS=false" in environment
        assert "SHALLOW_MEMORY_PERCENT=80" in environment
        assert inspected["State"]["Running"] is True

        sandbox_status = await client.get("/api/cache/sandbox")
        assert sandbox_status.status_code == 200, sandbox_status.text
        status = sandbox_status.json()
        assert status["phase"] == "ready"
        assert status["current_target"] == TARGET_NAME
        assert status["lease_owner"] is None
        assert status["queued_requests"] == 0
        assert status["healthy"] is True
        assert status["docker_installed"] is True
        assert status["docker_running"] is True
    finally:
        delete = await client.delete(f"/api/configure/targets/{TARGET_NAME}")
        assert delete.status_code == 200, delete.text
        assert delete.json()["success"] is True
        await sandbox_manager.stop()
        run_registry.reset()

    assert inspect_managed_sandbox() is None

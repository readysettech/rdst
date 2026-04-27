"""Real-database integration tests for the init validate flow.

`/api/init/validate` opens a real connection to each requested target
to confirm credentials + reachability before init is marked done. This
file pins the validate path against a live DB; the in-process suite
covers the rest of the init lifecycle (status / complete) without
needing a container.

To run locally:

    cd rdst/tests/integration
    docker compose up -d postgres   # or: mysql
    export RDST_TEST_PASSWORD=testpassword
    export RDST_TEST_ENGINE=postgresql
    pytest tests/test_realdb_init_api.py -v -m realdb
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.realdb


TARGET_NAME = "itinit"


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


async def test_validate_with_specific_targets_against_live_db(
    client, db_target_payload
):
    """`POST /api/init/validate` with the live target should report it
    as `success=True` (the LLM result may fail — we don't pin it)."""
    await _add_target(client, db_target_payload)

    response = await client.post(
        "/api/init/validate",
        json={"targets": [TARGET_NAME]},
    )
    assert response.status_code == 200, response.text

    body = response.json()
    target_results = body.get("target_results") or []
    by_name = {r["name"]: r for r in target_results}

    assert TARGET_NAME in by_name, (
        f"validate did not return our target; got {target_results!r}"
    )
    result = by_name[TARGET_NAME]
    assert result["success"] is True, (
        f"validate reported failure against live DB: {result!r}"
    )
    # Most drivers populate `version` when the connection succeeds.
    assert result.get("error") is None

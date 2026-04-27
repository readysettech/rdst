"""Real-database API integration tests for `/api/configure/...`.

These tests run against the live Postgres or MySQL container started by
`run_api_integration_tests.sh`. The service layer is NOT mocked: every
call lands on the real `ConfigureService` and (for `/test`) opens an
actual driver connection to the container.

The "what survives the round-trip" pieces of configure (add/list/remove/
update/set-default) are mostly covered by the in-process
`test_configure_api.py`. What we add here is:

- `test_test_connection_against_live_db` — only realdb can prove the
  driver path works end-to-end.
- A pair of CRUD tests that double as smoke checks against the real
  filesystem under `tmp_rdst_home` and the live engine surface.
- `test_set_default_then_omit_target_in_analyze` — proves the default-
  target fallback in `resolve_target_config()` actually wires through
  the analyze flow against a live DB.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.realdb


TARGET_NAME = "ittest"

SAMPLE_QUERY = (
    "SELECT primarytitle, startyear "
    "FROM title_basics "
    "WHERE titletype = 'movie' "
    "ORDER BY startyear DESC "
    "LIMIT 10"
)


@pytest.fixture(autouse=True)
def _set_test_password(monkeypatch):
    """Ensure RDST_TEST_PASSWORD is set even when running locally without
    going through the runner script. CI exports it for real."""
    if not os.environ.get("RDST_TEST_PASSWORD"):
        monkeypatch.setenv("RDST_TEST_PASSWORD", "testpassword")


async def _add_target(client, name: str, target_payload: dict) -> dict:
    response = await client.post(
        "/api/configure/targets",
        json={"name": name, "target": target_payload},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True, body
    return body


# ============================================================================
# Tests
# ============================================================================


async def test_test_connection_against_live_db(
    client, db_target_payload, db_engine, collect_sse_events
):
    """POST /api/configure/targets/{name}/test must open a real driver
    connection and report `status="success"` with a non-empty
    `server_version`. The only test in the configure surface that
    actually exercises psycopg2/pymysql.
    """
    await _add_target(client, TARGET_NAME, db_target_payload)

    events = await collect_sse_events(
        client, "POST", f"/api/configure/targets/{TARGET_NAME}/test", json_body=None
    )

    by_event: dict[str, dict] = {}
    for e in events:
        if "event" in e and isinstance(e.get("data"), dict):
            by_event[e["event"]] = e["data"]

    error_events = [e for e in events if e.get("event") == "error"]
    assert not error_events, f"connection-test stream emitted errors: {error_events}"

    final = by_event.get("connection_test")
    assert final is not None, f"no connection_test event in stream: {events}"
    assert final.get("status") == "success", final
    assert final.get("server_version"), "server_version is empty"
    if db_engine == "postgresql":
        assert "PostgreSQL" in final["server_version"]
    else:
        assert "MySQL" in final["server_version"] or final["server_version"]


async def test_update_target_changes_persist(client, db_target_payload):
    """Update-then-get must reflect the new field value."""
    await _add_target(client, TARGET_NAME, db_target_payload)

    new_payload = dict(db_target_payload)
    new_payload["database"] = "testdb"  # explicit, so we can change it back
    new_payload["tls"] = False

    # Flip a field and verify it round-trips.
    new_payload["read_only"] = True
    update = await client.put(
        f"/api/configure/targets/{TARGET_NAME}", json={"target": new_payload}
    )
    assert update.status_code == 200, update.text
    assert update.json()["success"] is True

    detail = await client.get(f"/api/configure/targets/{TARGET_NAME}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["read_only"] is True
    assert body["database"] == "testdb"


async def test_remove_target_then_list_excludes_it(client, db_target_payload):
    await _add_target(client, TARGET_NAME, db_target_payload)

    delete = await client.delete(f"/api/configure/targets/{TARGET_NAME}")
    assert delete.status_code == 200, delete.text
    assert delete.json()["success"] is True

    listing = await client.get("/api/configure/targets")
    assert listing.status_code == 200
    names = [t["name"] for t in listing.json()["targets"]]
    assert TARGET_NAME not in names


async def test_set_default_then_omit_target_in_analyze(
    client, db_target_payload, collect_sse_events
):
    """`POST /api/analyze` with no `target` in the body must fall back to
    the default target via `resolve_target_config()`. Proves the default-
    fallback path through a real flow against a real DB.
    """
    await _add_target(client, TARGET_NAME, db_target_payload)

    set_default = await client.put(
        "/api/configure/default", json={"name": TARGET_NAME}
    )
    assert set_default.status_code == 200, set_default.text
    assert set_default.json()["success"] is True

    events = await collect_sse_events(
        client,
        "POST",
        "/api/analyze",
        json_body={
            "query": SAMPLE_QUERY,
            # no "target" — guard must use the default we just set.
            "fast": True,
            "skip_rewrites": True,
        },
    )

    error_events = [e for e in events if e.get("event") == "error"]
    assert not error_events, f"analyze stream emitted error events: {error_events}"

    by_event = {e["event"]: e["data"] for e in events if "event" in e}
    assert "complete" in by_event, (
        f"analyze stream did not reach 'complete'. Events: "
        f"{[e.get('event') for e in events]}"
    )

    explain = by_event.get("explain_complete")
    assert explain is not None, "no explain_complete event from real DB"
    assert explain.get("success") is True, f"EXPLAIN failed: {explain}"

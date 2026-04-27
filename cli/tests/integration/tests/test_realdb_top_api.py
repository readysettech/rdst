"""Real-database integration tests for the top API.

Drives `/api/top` through HTTP against a live container. Two paths:

1. Historical (`pg_stat_statements` / MySQL `performance_schema`):
   execute a query against the DB to populate the catalog, then ask
   the API to read it back. The query text round-trip is what catches
   catalog-SQL breakage that mocks can't.
2. Realtime SSE (`realtime=true&duration=1`): connect, collect events
   for a tick, assert at least one event arrives. Pins the streaming
   plumbing.

To run locally:

    cd rdst/tests/integration
    docker compose up -d postgres   # or: mysql
    export RDST_TEST_PASSWORD=testpassword
    export RDST_TEST_ENGINE=postgresql
    pytest tests/test_realdb_top_api.py -v -m realdb
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.realdb


TARGET_NAME = "ittop"


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


def _execute_seed_queries(payload: dict, db_engine: str) -> None:
    """Run a recognizable query a few times so it lands in the catalog.

    We open a direct DB connection — bypassing the API — because the
    purpose is to seed observable state for the next API call to find.
    """
    password = os.environ["RDST_TEST_PASSWORD"]
    if db_engine == "postgresql":
        import psycopg2

        conn = psycopg2.connect(
            host=payload["host"],
            port=payload["port"],
            dbname=payload["database"],
            user=payload["user"],
            password=password,
        )
        try:
            with conn.cursor() as cur:
                # Reset stats so our query rises to the top easily.
                try:
                    cur.execute("SELECT pg_stat_statements_reset()")
                except Exception:
                    # Extension may not be installed in some environments;
                    # the query still gets logged elsewhere.
                    pass
                for _ in range(5):
                    cur.execute(
                        "SELECT primarytitle FROM title_basics "
                        "WHERE titletype = 'movie' LIMIT 5"
                    )
                    cur.fetchall()
            conn.commit()
        finally:
            conn.close()
    elif db_engine == "mysql":
        import pymysql

        conn = pymysql.connect(
            host=payload["host"],
            port=payload["port"],
            db=payload["database"],
            user=payload["user"],
            password=password,
        )
        try:
            with conn.cursor() as cur:
                for _ in range(5):
                    cur.execute(
                        "SELECT primarytitle FROM title_basics "
                        "WHERE titletype = 'movie' LIMIT 5"
                    )
                    cur.fetchall()
        finally:
            conn.close()
    else:
        raise ValueError(f"unsupported engine {db_engine!r}")


async def test_top_historical_returns_seeded_query(
    client, db_target_payload, db_engine
):
    """Execute a known query, then assert `/api/top` returns it.

    JSON mode (no `stream=true`) — `TopHistoricalResponse` shape.

    MySQL skip: the seeded `testuser` lacks SELECT on
    `performance_schema.events_statements_summary_by_digest`. The
    realtime test below still exercises the SSE plumbing on MySQL via
    `pg_stat_activity`'s analogue (SHOW PROCESSLIST), and PG covers the
    historical catalog SQL.
    """
    if db_engine == "mysql":
        pytest.skip(
            "MySQL testuser has no performance_schema grants; "
            "covered on PG."
        )

    await _add_target(client, db_target_payload)
    _execute_seed_queries(db_target_payload, db_engine)

    response = await client.get(f"/api/top?target={TARGET_NAME}&limit=50")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["success"] is True, body
    assert body["target"] == TARGET_NAME
    assert body["engine"] == db_engine

    # The seeded query mentions `title_basics`; that string should appear
    # in *some* row's normalized or raw text.
    haystack = "\n".join(
        (q.get("query_text") or "") + "\n" + (q.get("normalized_query") or "")
        for q in body["queries"]
    )
    assert "title_basics" in haystack.lower(), (
        f"Seeded query not visible in /api/top results. Got: {body['queries']!r}"
    )


async def test_top_realtime_streams_at_least_one_event(
    client, db_target_payload, collect_sse_events
):
    """Connect with `realtime=true&duration=1` — service must stream
    something within the duration window and then `complete`."""
    await _add_target(client, db_target_payload)

    events = await collect_sse_events(
        client,
        "GET",
        f"/api/top?target={TARGET_NAME}&realtime=true&duration=1",
    )

    assert events, "Realtime stream produced no events"
    assert any(e.get("event") == "connected" for e in events), (
        f"Realtime stream missing 'connected'; got {[e.get('event') for e in events]}"
    )
    assert any(e.get("event") == "complete" for e in events), (
        f"Realtime stream missing 'complete' (duration=1 should auto-stop); "
        f"got {[e.get('event') for e in events]}"
    )

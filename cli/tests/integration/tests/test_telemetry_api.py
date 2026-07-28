"""Real-database telemetry assertions for the SSE API surface.

Drives the FastAPI app through HTTP against a live container (same lane
as `test_realdb_configure_analyze_api.py` and `test_realdb_top_api.py`)
and asserts that `telemetry.command_run` actually fires the right event
on the real route → service → finalizer pipeline.

Why a separate file: the `realdb` slice tests assert *user-visible*
behavior (SSE shape, query results). Telemetry is a side channel —
checking `tm.track` from those tests would mix concerns. Keeping the
telemetry assertions here lets the realdb files stay focused on what
the API returns to the caller, while still exercising the same live
pipeline (no mocks).

What this catches: a regression where a route quietly stops calling
`command_run` (the original web-side `first_analyze` bug). Unit tests
in `test_telemetry_runs.py` can't see that — they call the CM
directly. These tests exercise the actual route entrypoint.

Coverage rationale: analyze has a bespoke finalizer (`first_analyze`,
`successful_analyzes`); top exercises the generic finalizer with the
shared `total_top_runs` counter and the realtime/historical event-name
split; scan exercises the generic finalizer in `dry_run=True` mode
(skips the LLM). ask uses the same generic finalizer path as scan and
needs an LLM, so it's left to unit coverage.

Failure-path coverage of `command_run` (success=False, error_type
populated, `first_analyze` gated off) lives in
`tests/unit/test_telemetry_runs.py`, where synthesized terminal events
exercise the CM directly without depending on an upstream pipeline that
naturally fails — analyze's workflow is resilient enough that nonexistent
tables and the like are reported as soft errors with `success=True`.

To run locally:

    cd rdst/tests/integration
    docker compose up -d postgres
    export RDST_TEST_PASSWORD=testpassword
    export RDST_TEST_ENGINE=postgresql
    pytest tests/test_telemetry_api.py -v -m realdb
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.realdb


TARGET_NAME = "ittel"


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


async def _drain_sse(client, method: str, url: str, body: dict | None = None) -> None:
    """Drain an SSE response. Telemetry is the side effect under test —
    we don't care about the event payload here."""
    async with client.stream(method, url, json=body) as response:
        assert response.status_code == 200, await response.aread()
        async for _ in response.aiter_lines():
            pass


def _track_calls(tm, event_name: str) -> list[tuple[str, dict]]:
    return [
        (
            call.args[0],
            call.args[1] if len(call.args) > 1 else (call.kwargs.get("properties") or {}),
        )
        for call in tm.track.call_args_list
        if call.args and call.args[0] == event_name
    ]


# ---------------------------------------------------------------------------
# Analyze — bespoke finalizer covers `analyze_run` + `first_analyze`
# ---------------------------------------------------------------------------


_ANALYZE_QUERY = (
    "SELECT primarytitle FROM title_basics "
    "WHERE titletype = 'movie' LIMIT 5"
)


async def test_analyze_fires_analyze_run_and_first_analyze(
    client, db_target_payload, db_engine, isolated_telemetry
):
    """First successful analyze on a fresh device must emit both
    `analyze_run` (with `source=web`, `target_engine`, `query_hash`,
    `success=True`) and the `first_analyze` PostHog alert event."""
    await _add_target(client, db_target_payload)

    await _drain_sse(
        client, "POST", "/api/analyze",
        {
            "query": _ANALYZE_QUERY,
            "target": TARGET_NAME,
            "fast": True,
            "skip_rewrites": True,
        },
    )

    runs = _track_calls(isolated_telemetry, "analyze_run")
    assert len(runs) == 1, isolated_telemetry.track.call_args_list
    props = runs[0][1]
    assert props["source"] == "web"
    assert props["target_engine"] == db_engine
    assert props["success"] is True
    assert props.get("query_hash"), props
    assert props["duration_ms"] >= 0

    first = _track_calls(isolated_telemetry, "first_analyze")
    assert len(first) == 1, "first_analyze must fire on the first successful web analyze"
    assert isolated_telemetry._get_stats().get("successful_analyzes") == 1


async def test_analyze_does_not_refire_first_analyze(
    client, db_target_payload, isolated_telemetry
):
    """`first_analyze` is gated by `successful_analyzes == 0`. Pre-seeding
    the counter must suppress the second emission."""
    await _add_target(client, db_target_payload)
    isolated_telemetry._stats = {"successful_analyzes": 5}

    await _drain_sse(
        client, "POST", "/api/analyze",
        {
            "query": _ANALYZE_QUERY,
            "target": TARGET_NAME,
            "fast": True,
            "skip_rewrites": True,
        },
    )

    assert _track_calls(isolated_telemetry, "analyze_run"), "analyze_run still fires"
    assert not _track_calls(isolated_telemetry, "first_analyze")


# ---------------------------------------------------------------------------
# Scan — generic finalizer; LLM is skipped under `dry_run=True`
# ---------------------------------------------------------------------------


_MINIMAL_SCHEMA_YAML = """\
target: ittel
engine: postgresql
tables:
  users:
    columns:
      id: {type: integer}
      active: {type: boolean}
"""


async def test_scan_dry_run_fires_scan_run(
    client, db_target_payload, db_engine, isolated_telemetry, tmp_rdst_home, tmp_path
):
    """Scan in `dry_run=True` mode must emit `scan_run` with the
    user-facing flags echoed in event properties.

    `dry_run=True` short-circuits before the LLM-conversion phase
    (see `features/scan/service.py` Phase 4 gate), so this test exercises
    the route → service → CM path end-to-end without an LLM dependency.
    A minimal semantic-layer YAML is required — the scan service refuses
    to run without one (config-phase gate at `features/scan/service.py:88`).
    """
    await _add_target(client, db_target_payload)

    # Scan requires a semantic-layer schema for the target. Write a
    # minimal one under the isolated HOME so the config gate passes
    # without depending on `rdst schema init` having run.
    schema_dir = tmp_rdst_home / "semantic-layer"
    schema_dir.mkdir(parents=True, exist_ok=True)
    (schema_dir / f"{TARGET_NAME}.yaml").write_text(_MINIMAL_SCHEMA_YAML)

    # Empty corpus is fine — scan emits ScanCompleteEvent(success=True)
    # when no ORM files are found, which is the path under test here.
    corpus = tmp_path / "scan_corpus"
    corpus.mkdir()

    await _drain_sse(
        client, "POST", "/api/scan",
        {
            "target": TARGET_NAME,
            "directory": str(corpus),
            "analyze": False,
            "shallow": False,
            "dry_run": True,
        },
    )

    runs = _track_calls(isolated_telemetry, "scan_run")
    assert len(runs) == 1, isolated_telemetry.track.call_args_list
    props = runs[0][1]
    assert props["source"] == "web"
    assert props["target_engine"] == db_engine
    assert props["analyze"] is False
    assert props["shallow"] is False
    assert props["dry_run"] is True
    assert props["success"] is True

    # Generic finalizer increments `total_scans`.
    assert isolated_telemetry._get_stats().get("total_scans") == 1


# ---------------------------------------------------------------------------
# Top — generic finalizer; historical and realtime share `total_top_runs`
# ---------------------------------------------------------------------------


async def test_top_historical_fires_top_run(
    client, db_target_payload, db_engine, isolated_telemetry
):
    """Historical mode (JSON response, generic finalizer) emits `top_run`
    and increments `total_top_runs`."""
    if db_engine == "mysql":
        pytest.skip(
            "MySQL testuser lacks performance_schema grants; PG covers it."
        )

    await _add_target(client, db_target_payload)

    response = await client.get(f"/api/top?target={TARGET_NAME}&limit=5")
    assert response.status_code == 200, response.text

    runs = _track_calls(isolated_telemetry, "top_run")
    assert len(runs) == 1
    props = runs[0][1]
    assert props["source"] == "web"
    # `target_engine` may be the configured value or the connected one
    # (the route updates it from `TopConnectedEvent.db_engine` if seen).
    assert props["target_engine"] in (db_engine, "postgresql", "mysql")
    assert isolated_telemetry._get_stats().get("total_top_runs") == 1


async def test_top_realtime_fires_top_realtime_run_and_shares_counter(
    client, db_target_payload, isolated_telemetry
):
    """Realtime mode emits `top_realtime_run` (separate event from
    `top_run`) but shares `total_top_runs` so NPS pacing reflects all
    top usage."""
    await _add_target(client, db_target_payload)

    await _drain_sse(
        client, "GET",
        f"/api/top?target={TARGET_NAME}&realtime=true&duration=1",
    )

    runs = _track_calls(isolated_telemetry, "top_realtime_run")
    assert len(runs) == 1
    assert runs[0][1]["source"] == "web"
    assert isolated_telemetry._get_stats().get("total_top_runs") == 1
    # `top_run` must NOT also fire for the realtime path.
    assert not _track_calls(isolated_telemetry, "top_run")

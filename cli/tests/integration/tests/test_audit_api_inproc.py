"""In-process integration tests for the audit API endpoints.

Drives the real routes against `tmp_rdst_home`. The audit run itself is
mocked at the `AuditService.audit_target` boundary — collection needs a
live database; everything above it (background-run scheduling, SSE framing,
snapshot persistence, run history) runs for real.
"""

from __future__ import annotations

import asyncio
import json

from unittest.mock import MagicMock, patch

import pytest

from features.audit.models import (
    AuditMetrics,
    AuditResult,
    CacheOpportunityScore,
    DatabaseSnapshot,
    SizingAssessment,
)
from shared.run_registry import run_registry


@pytest.fixture
def seeded_target_defaults() -> dict:
    return {"name": "audittest", "env": "AUDIT_PASSWORD"}


def _fake_result(target: str = "audittest") -> AuditResult:
    return AuditResult(
        target_name=target,
        engine="postgresql",
        host="127.0.0.1",
        metrics=AuditMetrics(max_connections=100, cache_hit_rate=98.5),
        audited_at="2026-07-06T00:00:00+00:00",
    )


pytestmark = pytest.mark.usefixtures("run_blocking_inline")


@pytest.fixture(autouse=True)
async def _isolated_run_registry():
    """Audits are detached background runs; leave none behind between tests."""
    run_registry.reset()
    yield
    run_registry.reset()


async def _start_and_collect(client, collect_sse_events, url, json_body):
    """Start an audit background run and drain its event stream."""
    response = await client.post(url, json=json_body)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reused"] is False
    events = await collect_sse_events(
        client, "GET", f"/api/runs/{body['run_id']}/events"
    )
    assert events[-1]["event"] == "run_end"
    return events[:-1]


async def test_run_audit_streams_events_and_saves_snapshot(
    client, tmp_rdst_home, monkeypatch, collect_sse_events, seed_target
):
    seed_target()
    monkeypatch.setenv("AUDIT_PASSWORD", "irrelevant")

    with patch(
        "features.audit.service.AuditService.audit_target",
        return_value=_fake_result(),
    ):
        events = await _start_and_collect(
            client, collect_sse_events, "/api/audit",
            {"target": "audittest", "insights": False},
        )

    types = [e["event"] for e in events]
    assert "target_start" in types
    assert "metrics_collected" in types
    assert "snapshot_saved" in types
    assert types[-1] == "complete"

    complete = events[-1]["data"]
    assert complete["success"] is True
    snapshot_id = complete["snapshot_id"]
    assert snapshot_id.startswith("audit_audittest_")

    snapshot_path = tmp_rdst_home / "fleet" / "snapshots" / f"{snapshot_id}.json"
    assert snapshot_path.exists()

    # The saved run shows up in history and can be fetched back.
    response = await client.get("/api/audit/runs")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["runs"][0]["run_id"] == snapshot_id
    assert body["runs"][0]["source"] == "quick"

    response = await client.get(f"/api/audit/runs/{snapshot_id}")
    assert response.status_code == 200
    assert response.json()["target_name"] == "audittest"


async def test_run_audit_locked_when_password_missing(client, tmp_rdst_home, monkeypatch, seed_target):
    seed_target(env="MISSING_AUDIT_PASSWORD")
    monkeypatch.delenv("MISSING_AUDIT_PASSWORD", raising=False)

    response = await client.post("/api/audit", json={"target": "audittest"})
    assert response.status_code == 423
    assert response.json()["detail"]["code"] == "TARGET_PASSWORD_REQUIRED"


async def test_run_audit_404_for_unknown_target(client, tmp_rdst_home):
    response = await client.post("/api/audit", json={"target": "does-not-exist"})
    assert response.status_code == 404


async def test_runs_list_empty(client, tmp_rdst_home):
    response = await client.get("/api/audit/runs")
    assert response.status_code == 200
    assert response.json() == {"runs": [], "count": 0}


async def test_run_detail_404_for_unknown_id(client, tmp_rdst_home):
    response = await client.get("/api/audit/runs/nope")
    assert response.status_code == 404


async def test_runs_list_filters_by_target(client, tmp_rdst_home):
    snapshots = tmp_rdst_home / "fleet" / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    for target in ("db1", "db2"):
        (snapshots / f"audit_{target}_20260706_000000.json").write_text(
            json.dumps({
                "target_name": target,
                "audited_at": "2026-07-06T00:00:00+00:00",
                "top_queries": [],
            })
        )

    response = await client.get("/api/audit/runs?target=db1")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["runs"][0]["target_name"] == "db1"


async def test_capture_streams_workload_events(
    client, tmp_rdst_home, monkeypatch, collect_sse_events, seed_target
):
    from features.audit.events import (
        WorkloadCaptureProgressEvent,
        WorkloadCompleteEvent,
        WorkloadStatusEvent,
    )

    seed_target()
    monkeypatch.setenv("AUDIT_PASSWORD", "irrelevant")

    async def fake_run_capture(self, target_name, **kwargs):
        assert kwargs["duration_seconds"] == 30
        assert kwargs["run_analysis"] is False
        yield WorkloadStatusEvent(type="status", phase="config", message="Connecting...")
        yield WorkloadCaptureProgressEvent(
            type="capture_progress", elapsed_seconds=15.0, total_seconds=30.0,
            unique_queries=3, total_executions=42,
        )
        yield WorkloadCompleteEvent(
            type="complete", success=True, run_id="audit_audittest_x",
            summary={"unique_queries": 3},
        )

    with patch("features.audit.capture_service.CaptureService.run_capture", fake_run_capture):
        events = await _start_and_collect(
            client, collect_sse_events, "/api/audit/capture",
            {"target": "audittest", "duration": 30, "analysis": False},
        )

    types = [e["event"] for e in events]
    assert types == ["status", "capture_progress", "complete"]
    assert events[-1]["data"]["run_id"] == "audit_audittest_x"


async def _run_real_api_capture(client, collect_sse_events, audit_effect):
    import time

    connection = MagicMock()
    connection.cursor.return_value.fetchone.return_value = (1,)
    snapshots = [
        DatabaseSnapshot(timestamp="2026-07-06T00:00:00+00:00", engine="postgresql"),
        DatabaseSnapshot(timestamp="2026-07-06T00:00:10+00:00", engine="postgresql"),
    ]
    real_monotonic = time.monotonic
    monotonic_calls = 0

    def advancing_monotonic():
        nonlocal monotonic_calls
        monotonic_calls += 1
        return real_monotonic() + monotonic_calls * 20

    with (
        patch(
            "features.audit.service.AuditService.audit_target",
            side_effect=audit_effect,
        ),
        patch("shared.db_connection.create_direct_connection", return_value=connection),
        patch(
            "features.audit.capture_service.CaptureService.check_query_stats",
            return_value={"status": "ok", "detail": "available", "remediation": None},
        ),
        patch(
            "features.audit.capture_service.query_stats_module.collect_database_snapshot",
            side_effect=snapshots,
        ),
        patch(
            "features.audit.capture_service.query_stats_module.collect_table_stats",
            return_value=[],
        ),
        patch(
            "features.audit.capture_service.query_stats_module.collect_pg_stat_statements",
            return_value=[],
        ),
        patch(
            "features.audit.capture_service.query_stats_module.compute_snapshot_delta",
            return_value={},
        ),
        patch(
            "features.audit.capture_service.time.monotonic",
            side_effect=advancing_monotonic,
        ),
    ):
        return await _start_and_collect(
            client,
            collect_sse_events,
            "/api/audit/capture",
            {"target": "audittest", "duration": 10, "analysis": False},
        )


async def test_api_capture_saves_and_streams_full_metrics_audit(
    client, tmp_rdst_home, monkeypatch, collect_sse_events, seed_target
):
    seed_target()
    monkeypatch.setenv("AUDIT_PASSWORD", "irrelevant")
    audit_result = AuditResult(
        target_name="audittest",
        engine="postgresql",
        host="db.example.com",
        region="us-east-1",
        instance_class="db.r6g.large",
        instance_class_source="aws",
        metrics=AuditMetrics(max_connections=200, cache_hit_rate=99.2),
        sizing=SizingAssessment(
            current_monthly_cost_usd=410.0,
            potential_savings_usd=125.0,
        ),
        cache_opportunity=CacheOpportunityScore(score=82),
        health_report={"connection_health": {"status": "healthy"}},
        cloudwatch_cpu={"avg": 23.4},
        audited_at="2026-07-06T00:00:00+00:00",
    )

    events = await _run_real_api_capture(
        client, collect_sse_events, lambda *_args, **_kwargs: audit_result,
    )

    complete = events[-1]["data"]
    assert events[-1]["event"] == "complete"
    assert complete["summary"]["metrics"]["max_connections"] == 200
    assert complete["summary"]["sizing"]["potential_savings_usd"] == 125.0
    assert complete["summary"]["health_report"]["connection_health"]["status"] == "healthy"
    assert complete["summary"]["instance_class_source"] == "aws"

    run_path = (
        tmp_rdst_home
        / "audits"
        / "audittest"
        / f"{complete['run_id']}.json"
    )
    saved = json.loads(run_path.read_text())
    assert saved["metrics"]["max_connections"] == 200
    assert saved["sizing"]["current_monthly_cost_usd"] == 410.0
    assert saved["health_report"]["connection_health"]["status"] == "healthy"
    assert saved["cloudwatch_cpu"] == {"avg": 23.4}


async def test_api_capture_audit_failure_still_saves_workload_run(
    client, tmp_rdst_home, monkeypatch, collect_sse_events, seed_target
):
    seed_target()
    monkeypatch.setenv("AUDIT_PASSWORD", "irrelevant")

    events = await _run_real_api_capture(
        client,
        collect_sse_events,
        RuntimeError("metrics unavailable"),
    )

    complete = events[-1]["data"]
    assert events[-1]["event"] == "complete"
    assert complete["success"] is True
    assert "metrics" not in complete["summary"]
    run_path = (
        tmp_rdst_home
        / "audits"
        / "audittest"
        / f"{complete['run_id']}.json"
    )
    saved = json.loads(run_path.read_text())
    assert saved["run_id"] == complete["run_id"]
    assert "metrics" not in saved
    assert "sizing" not in saved
    assert "health_report" not in saved


async def test_capture_reuses_the_run_already_in_flight(
    client, tmp_rdst_home, monkeypatch, seed_target
):
    seed_target()
    monkeypatch.setenv("AUDIT_PASSWORD", "irrelevant")

    async def never_finishing_capture(self, **kwargs):
        from features.audit.events import WorkloadStatusEvent

        yield WorkloadStatusEvent(type="status", phase="config", message="Connecting...")
        await asyncio.Event().wait()

    with patch(
        "features.audit.capture_service.CaptureService.run_capture",
        never_finishing_capture,
    ):
        first = await client.post("/api/audit/capture", json={"target": "audittest"})
        assert first.status_code == 200
        run_id = first.json()["run_id"]

        second = await client.post("/api/audit/capture", json={"target": "audittest"})
        assert second.json() == {"run_id": run_id, "reused": True}

        # One health check at a time across every target and both kinds.
        quick = await client.post("/api/audit", json={"target": "audittest"})
        assert quick.json() == {"run_id": run_id, "reused": True}

        cancelled = await client.delete(f"/api/runs/{run_id}")
        assert cancelled.json() == {"run_id": run_id, "cancelled": True}


async def test_capture_requirements_ok(
    client, tmp_rdst_home, monkeypatch, seed_target, override_target_guard
):
    seed_target()
    monkeypatch.setenv("AUDIT_PASSWORD", "irrelevant")
    connection = MagicMock()
    result = {
        "status": "ok",
        "detail": "pg_stat_statements is available",
        "remediation": None,
    }

    with (
        override_target_guard("audittest"),
        patch(
            "shared.db_connection.create_direct_connection",
            return_value=connection,
        ) as create_connection,
        patch(
            "features.audit.capture_service.CaptureService.check_query_stats",
            return_value=result,
        ) as check_query_stats,
        patch(
            "features.audit.readyset_benchmark.docker_available",
            return_value=False,
        ),
    ):
        response = await client.get("/api/audit/requirements?target=audittest")

    assert response.status_code == 200
    assert response.json() == {
        "target": "audittest",
        "engine": "postgresql",
        "query_stats": "ok",
        "detail": "pg_stat_statements is available",
        "remediation": None,
        "docker_available": False,
    }
    create_connection.assert_called_once()
    check_query_stats.assert_called_once_with(connection, "postgresql")
    connection.close.assert_called_once_with()


async def test_capture_requirements_unknown_target_is_4xx(
    client, tmp_rdst_home, override_target_guard
):
    with override_target_guard("does-not-exist"):
        response = await client.get(
            "/api/audit/requirements?target=does-not-exist"
        )

    assert 400 <= response.status_code < 500

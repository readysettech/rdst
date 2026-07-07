"""In-process integration tests for the audit API endpoints.

Drives the real routes against `tmp_rdst_home`. The audit run itself is
mocked at the `AuditService.audit_target` boundary — collection needs a
live database; everything above it (SSE framing, snapshot persistence,
run history) runs for real.
"""

from __future__ import annotations

import json

from unittest.mock import patch

from features.audit.models import AuditMetrics, AuditResult
from shared.config.targets import TargetsConfig


def _seed_target(name: str = "audittest", env: str = "AUDIT_PASSWORD") -> None:
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        name,
        {
            "engine": "postgresql",
            "host": "127.0.0.1",
            "port": 5432,
            "database": "appdb",
            "user": "appuser",
            "password_env": env,
        },
    )
    cfg.save()


def _fake_result(target: str = "audittest") -> AuditResult:
    return AuditResult(
        target_name=target,
        engine="postgresql",
        host="127.0.0.1",
        metrics=AuditMetrics(max_connections=100, cache_hit_rate=98.5),
        audited_at="2026-07-06T00:00:00+00:00",
    )


async def test_run_audit_streams_events_and_saves_snapshot(
    client, tmp_rdst_home, monkeypatch, collect_sse_events
):
    _seed_target()
    monkeypatch.setenv("AUDIT_PASSWORD", "irrelevant")

    with patch(
        "features.audit.service.AuditService.audit_target",
        return_value=_fake_result(),
    ):
        events = await collect_sse_events(
            client, "POST", "/api/audit",
            json_body={"target": "audittest", "insights": False},
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


async def test_run_audit_locked_when_password_missing(client, tmp_rdst_home, monkeypatch):
    _seed_target(env="MISSING_AUDIT_PASSWORD")
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
    client, tmp_rdst_home, monkeypatch, collect_sse_events
):
    from features.audit.events import (
        WorkloadCaptureProgressEvent,
        WorkloadCompleteEvent,
        WorkloadStatusEvent,
    )

    _seed_target()
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
        events = await collect_sse_events(
            client, "POST", "/api/audit/capture",
            json_body={"target": "audittest", "duration": 30, "analysis": False},
        )

    types = [e["event"] for e in events]
    assert types == ["status", "capture_progress", "complete"]
    assert events[-1]["data"]["run_id"] == "audit_audittest_x"


async def test_capture_rejects_concurrent_run_for_same_target(
    client, tmp_rdst_home, monkeypatch, collect_sse_events
):
    from features.audit.api import routes as audit_routes

    _seed_target()
    monkeypatch.setenv("AUDIT_PASSWORD", "irrelevant")

    audit_routes._active_captures.add("audittest")
    try:
        events = await collect_sse_events(
            client, "POST", "/api/audit/capture", json_body={"target": "audittest"},
        )
    finally:
        audit_routes._active_captures.discard("audittest")

    assert events[-1]["event"] == "error"
    assert "already running" in events[-1]["data"]["message"]

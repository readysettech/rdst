"""The web capture path asks the model for the health verdict, like the CLI audit."""
from __future__ import annotations

import itertools
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import features.audit.capture_service as capture_module
from features.audit.capture_service import CaptureService
from features.audit.service import AuditService
from features.audit.query_stats import DatabaseSnapshot


class _Config:
    def load(self):
        return None

    def get(self, name):
        return {"engine": "postgresql", "host": "localhost"}


async def _run(verdict):
    metrics_audit = {
        "target_name": "demo",
        "engine": "postgresql",
        "health_report": {"connections": {"total_connections": 3}},
        "metrics": {},
        "sizing": {},
        "top_queries": [],
    }
    snapshot = DatabaseSnapshot(timestamp="t", cache_hit_ratio=0.9, active_connections=1)
    fake_time = MagicMock()
    fake_time.monotonic.side_effect = itertools.count(0, 100)
    service = CaptureService(config=_Config())
    with (
        patch("shared.db_connection.create_direct_connection", return_value=MagicMock()),
        patch.object(capture_module, "time", fake_time),
        patch.object(capture_module.query_stats_module, "collect_database_snapshot", return_value=snapshot),
        patch.object(capture_module.query_stats_module, "collect_table_stats", return_value=[]),
        patch.object(capture_module.query_stats_module, "collect_pg_stat_statements", side_effect=[[], []]),
        patch.object(capture_module.query_stats_module, "compute_snapshot_delta", return_value={}),
        patch.object(CaptureService, "_collect_metrics_audit", new_callable=AsyncMock, return_value=metrics_audit),
        patch.object(AuditService, "run_health_llm", staticmethod(lambda audit: verdict)) as _,
    ):
        events = [
            event
            async for event in service.run_capture(
                "demo",
                duration_seconds=10,
                run_analysis=True,
                collect_metrics_audit=True,
                save_capture=False,
            )
        ]
    complete = next(e for e in events if getattr(e, "type", "") == "complete")
    phases = [getattr(e, "phase", "") for e in events if getattr(e, "type", "") == "status"]
    return complete, phases


@pytest.mark.asyncio
async def test_capture_attaches_the_health_verdict():
    verdict = {"summary": "Healthy", "health_score": 90, "model_used": "m"}
    complete, phases = await _run(verdict)
    assert "health_insights" in phases
    assert complete.summary["health_analysis"] == verdict
    assert complete.summary["analysis_error"] is None
    assert complete.summary["phase_timings_ms"].get("health_insights") is not None


@pytest.mark.asyncio
async def test_capture_reports_a_failed_health_verdict():
    complete, _ = await _run({"error": "model unavailable"})
    assert complete.summary["health_analysis"] == {"error": "model unavailable"}
    assert complete.summary["analysis_error"] == "Health findings failed: model unavailable"

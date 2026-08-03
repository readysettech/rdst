"""Tests for AuditService.audit_single and audit run history lookup."""

from __future__ import annotations

import asyncio
import json

import pytest
from unittest.mock import MagicMock, patch

from features.audit.models import AuditMetrics, AuditResult
from features.audit.service import AuditService

pytestmark = pytest.mark.usefixtures("run_blocking_inline")


def _fake_result(target: str = "mydb", error: str | None = None) -> AuditResult:
    return AuditResult(
        target_name=target,
        engine="postgresql",
        host="db.example.com",
        metrics=None if error else AuditMetrics(max_connections=100, cache_hit_rate=99.1),
        error=error,
        audited_at="2026-07-06T00:00:00+00:00",
    )


class _FakeConfig:
    def __init__(self, targets: dict):
        self._targets = targets

    def get(self, name):
        return self._targets.get(name)


class TestAuditSingle:
    @pytest.mark.asyncio
    async def test_unknown_target_yields_error(self):
        service = AuditService(config=_FakeConfig({}))
        events = [e async for e in service.audit_single("nope", save=False)]
        assert events[-1].type == "error"
        assert "not found" in events[-1].message

    @pytest.mark.asyncio
    async def test_success_streams_progress_and_completes(self, tmp_path):
        service = AuditService(config=_FakeConfig({"mydb": {"engine": "postgresql"}}))

        def fake_audit_target(name, config, on_progress=None):
            if on_progress:
                on_progress("Collecting database metrics...")
                on_progress("Collecting health data...")
            return _fake_result(name)

        with patch.object(AuditService, "audit_target", side_effect=fake_audit_target):
            events = [e async for e in service.audit_single("mydb", save=False)]

        types = [e.type for e in events]
        assert types[0] == "status"
        assert "target_start" in types
        assert "metrics_collected" in types
        assert types[-2] == "target_complete"
        assert types[-1] == "complete"
        assert events[-1].success is True
        assert events[-1].snapshot_id is None
        # Progress messages from the worker thread surface as status events.
        status_messages = [e.message for e in events if e.type == "status"]
        assert "Collecting database metrics..." in status_messages

        complete = next(e for e in events if e.type == "target_complete")
        assert complete.result["target_name"] == "mydb"
        assert complete.result["metrics"]["max_connections"] == 100
    @pytest.mark.asyncio
    async def test_save_persists_snapshot(self, tmp_rdst_home):
        service = AuditService(config=_FakeConfig({"mydb": {"engine": "postgresql"}}))

        with patch.object(AuditService, "audit_target", return_value=_fake_result()):
            events = [e async for e in service.audit_single("mydb", save=True)]

        saved = next(e for e in events if e.type == "snapshot_saved")
        assert saved.snapshot_id.startswith("audit_mydb_")
        assert events[-1].snapshot_id == saved.snapshot_id

        snapshot_path = tmp_rdst_home / "fleet" / "snapshots" / f"{saved.snapshot_id}.json"
        assert snapshot_path.exists()
        assert json.loads(snapshot_path.read_text())["target_name"] == "mydb"

    @pytest.mark.asyncio
    async def test_insights_merges_health_analysis(self):
        service = AuditService(config=_FakeConfig({"mydb": {"engine": "postgresql"}}))
        analysis = {"health_score": 87, "findings": []}

        with patch.object(AuditService, "audit_target", return_value=_fake_result()):
            with patch.object(AuditService, "run_health_llm", return_value=analysis):
                events = [
                    e async for e in service.audit_single("mydb", insights=True, save=False)
                ]

        complete = next(e for e in events if e.type == "target_complete")
        assert complete.result["health_analysis"] == analysis

    @pytest.mark.asyncio
    async def test_collection_error_yields_target_error(self):
        service = AuditService(config=_FakeConfig({"mydb": {"engine": "postgresql"}}))

        with patch.object(
            AuditService, "audit_target", return_value=_fake_result(error="connection refused")
        ):
            events = [e async for e in service.audit_single("mydb", save=False)]

        types = [e.type for e in events]
        assert "target_error" in types
        assert "snapshot_saved" not in types
        assert events[-1].type == "complete"
        assert events[-1].success is False


class TestAuditFleet:
    @pytest.mark.asyncio
    async def test_fleet_streams_per_target_and_saves_combined_snapshot(
        self, tmp_rdst_home
    ):
        config = _FakeConfig({
            "db1": {"engine": "postgresql"},
            "db2": {"engine": "postgresql"},
        })
        service = AuditService(config=config)

        def fake_audit_target(name, target_config, on_progress=None):
            if name == "db2":
                return _fake_result(name, error="connection refused")
            return _fake_result(name)

        with patch.object(AuditService, "audit_target", side_effect=fake_audit_target):
            events = [
                e async for e in service.audit_fleet(
                    ["db1", "db2"], save_name="fleet_test",
                )
            ]

        types = [e.type for e in events]
        assert types.count("target_start") == 2
        assert types.count("target_complete") == 1
        assert types.count("target_error") == 1
        assert "snapshot_saved" in types

        complete = events[-1]
        assert complete.type == "complete"
        assert complete.success is True
        assert complete.snapshot_id == "fleet_test"
        assert complete.summary == {
            "targets_audited": 2, "successes": 1, "failures": 1,
        }

        import json
        snapshots_dir = tmp_rdst_home / "fleet" / "snapshots"
        combined = json.loads((snapshots_dir / "fleet_test.json").read_text())
        assert combined["targets_audited"] == 2
        assert combined["targets_failed"] == 1
        assert {r["target_name"] for r in combined["results"]} == {"db1", "db2"}
        # Only successful targets get individual snapshots.
        individual = [p.name for p in snapshots_dir.glob("audit_*.json")]
        assert len(individual) == 1
        assert individual[0].startswith("audit_db1_")

    @pytest.mark.asyncio
    async def test_fleet_insights_land_in_summary_and_snapshot(
        self, tmp_rdst_home
    ):
        service = AuditService(config=_FakeConfig({"db1": {"engine": "postgresql"}}))
        insights = {"health_score": 80, "fleet_findings": []}

        with patch.object(AuditService, "audit_target", return_value=_fake_result("db1")):
            with patch.object(AuditService, "run_fleet_insights", return_value=insights):
                events = [
                    e async for e in service.audit_fleet(
                        ["db1"], insights=True, save_name="fleet_ins",
                    )
                ]

        assert events[-1].summary["fleet_insights"] == insights

        import json
        snapshot = json.loads(
            (tmp_rdst_home / "fleet" / "snapshots" / "fleet_ins.json").read_text()
        )
        assert snapshot["fleet_insights"] == insights

    @pytest.mark.asyncio
    async def test_fleet_no_save(self, tmp_rdst_home):
        service = AuditService(config=_FakeConfig({"db1": {"engine": "postgresql"}}))

        with patch.object(AuditService, "audit_target", return_value=_fake_result("db1")):
            events = [e async for e in service.audit_fleet(["db1"])]

        types = [e.type for e in events]
        assert "snapshot_saved" not in types
        assert events[-1].snapshot_id is None
        assert not (tmp_rdst_home / "fleet").exists()


class TestInstanceClassSource:
    def _audit(self, host, extra_config=None, shared_buffers_mb=4096.0):
        service = AuditService(config=_FakeConfig({}))
        metrics = AuditMetrics(shared_buffers_mb=shared_buffers_mb)
        target_config = {"engine": "postgresql", "host": host, **(extra_config or {})}
        with (
            patch("features.audit.service.collect_metrics", return_value=metrics),
            patch.object(AuditService, "_collect_top_queries", return_value=[]),
            patch.object(AuditService, "_enrich_aws_storage"),
            patch(
                "features.audit.health.collect_health_report",
                side_effect=Exception("skipped"),
            ),
            patch(
                "features.fleet.cloudwatch_metrics.collect_cloudwatch_cpu",
                return_value=None,
            ),
            patch(
                "features.fleet.cloudwatch_metrics.derive_rds_identifier",
                return_value=None,
            ),
        ):
            return service.audit_target("t1", target_config)

    def test_local_host_gets_no_estimate(self):
        result = self._audit("localhost")
        assert result.instance_class is None
        assert result.instance_class_source is None

    def test_rds_host_without_metadata_is_estimated(self):
        result = self._audit("mydb.abc123.us-east-1.rds.amazonaws.com")
        assert result.instance_class is not None
        assert result.instance_class_source == "estimated"

    def test_configured_instance_class_is_aws(self):
        result = self._audit("localhost", extra_config={"instance_class": "db.r6g.xlarge"})
        assert result.instance_class == "db.r6g.xlarge"
        assert result.instance_class_source == "aws"


class TestAuditFleetDuration:
    @pytest.mark.asyncio
    async def test_duration_runs_capture_and_attaches_workload(self):
        from features.audit.capture_service import CaptureService
        from features.audit.events import WorkloadCompleteEvent

        service = AuditService(config=_FakeConfig({"db1": {"engine": "postgresql"}}))
        seen_kwargs: dict = {}

        async def fake_run_capture(self, **kwargs):
            seen_kwargs.update(kwargs)
            yield WorkloadCompleteEvent(
                type="complete",
                success=True,
                run_id="r1",
                summary={
                    "duration_seconds": kwargs["duration_seconds"],
                    "unique_queries": 2,
                },
                analysis={"health_score": 70},
            )

        with (
            patch.object(AuditService, "audit_target", return_value=_fake_result("db1")),
            patch.object(AuditService, "run_fleet_insights", return_value={}),
            patch.object(CaptureService, "run_capture", fake_run_capture),
        ):
            events = [
                e async for e in service.audit_fleet(
                    ["db1"], duration_seconds=45, insights=True,
                )
            ]

        complete = next(e for e in events if e.type == "target_complete")
        workload = complete.result["workload"]
        assert workload["duration_seconds"] == 45
        assert workload["analysis"] == {"health_score": 70}
        assert seen_kwargs["target_name"] == "db1"
        assert seen_kwargs["run_analysis"] is True
        assert seen_kwargs["save_capture"] is False

    @pytest.mark.asyncio
    async def test_no_duration_skips_capture(self):
        from features.audit.capture_service import CaptureService

        service = AuditService(config=_FakeConfig({"db1": {"engine": "postgresql"}}))

        with (
            patch.object(AuditService, "audit_target", return_value=_fake_result("db1")),
            patch.object(
                CaptureService, "run_capture",
                side_effect=AssertionError("capture should not run"),
            ),
        ):
            events = [e async for e in service.audit_fleet(["db1"])]

        complete = next(e for e in events if e.type == "target_complete")
        assert "workload" not in complete.result

    @pytest.mark.asyncio
    async def test_capture_error_recorded_without_failing_target(self):
        from features.audit.capture_service import CaptureService
        from features.audit.events import WorkloadErrorEvent

        service = AuditService(config=_FakeConfig({"db1": {"engine": "postgresql"}}))

        async def fake_run_capture(self, **kwargs):
            yield WorkloadErrorEvent(type="error", message="capture boom", phase="capture")

        with (
            patch.object(AuditService, "audit_target", return_value=_fake_result("db1")),
            patch.object(CaptureService, "run_capture", fake_run_capture),
        ):
            events = [
                e async for e in service.audit_fleet(["db1"], duration_seconds=30)
            ]

        complete = next(e for e in events if e.type == "target_complete")
        assert complete.result["workload"] == {"error": "capture boom"}
        assert events[-1].success is True


def test_collect_top_queries_passes_target_name_to_connection():
    service = AuditService()
    connection = MagicMock()
    target_config = {
        "engine": "postgresql",
        "host": "database.internal",
        "port": 5432,
    }

    with (
        patch("shared.db_connection.create_direct_connection") as connect,
        patch(
            "features.audit.service.collect_pg_stat_statements",
            return_value=[],
        ),
    ):
        connect.return_value = connection
        service._collect_top_queries("prod", target_config, "postgresql")

    connect.assert_called_once_with(target_config, target="prod")


class TestAuditRunHistory:
    def _seed_snapshot(self, home, snapshot_id: str, data: dict) -> None:
        snapshots = home / "fleet" / "snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        (snapshots / f"{snapshot_id}.json").write_text(json.dumps(data))

    def _seed_capture(self, home, target: str, run_id: str, data: dict) -> None:
        captures = home / "audits" / target
        captures.mkdir(parents=True, exist_ok=True)
        (captures / f"{run_id}.json").write_text(json.dumps(data))

    def test_list_merges_snapshots_and_captures(self, tmp_rdst_home):
        from features.audit.storage import list_audit_runs

        self._seed_snapshot(tmp_rdst_home, "audit_mydb_20260706_010101", {
            "target_name": "mydb",
            "audited_at": "2026-07-06T01:01:01+00:00",
            "top_queries": [{"query_hash": "abc"}],
            "health_analysis": {"health_score": 90},
        })
        self._seed_snapshot(tmp_rdst_home, "audit_mydb_20260704_010101", {
            "target_name": "mydb",
            "audited_at": "2026-07-04T01:01:01+00:00",
            "top_queries": [],
            "health_analysis": {"error": "Claude error: HTTP 401"},
        })
        self._seed_capture(tmp_rdst_home, "mydb", "audit_mydb_20260705_010101", {
            "run_id": "audit_mydb_20260705_010101",
            "target_name": "mydb",
            "started_at": "2026-07-05T01:01:01+00:00",
            "duration_seconds": 30,
            "total_queries": 5,
            "source": "pg_stat",
            "analysis": None,
        })

        runs = list_audit_runs()
        assert len(runs) == 3
        # Newest first: the quick-audit snapshot from the 6th sorts before
        # the capture from the 5th.
        assert runs[0]["run_id"] == "audit_mydb_20260706_010101"
        assert runs[0]["source"] == "quick"
        assert runs[0]["has_analysis"] is True
        assert runs[1]["run_id"] == "audit_mydb_20260705_010101"
        # A failed health-LLM attempt does not count as analyzed.
        assert runs[2]["run_id"] == "audit_mydb_20260704_010101"
        assert runs[2]["has_analysis"] is False

        assert list_audit_runs(target="otherdb") == []

    def test_find_run_prefers_snapshots_and_matches_prefix(self, tmp_rdst_home):
        from features.audit.storage import find_audit_run

        self._seed_snapshot(tmp_rdst_home, "audit_mydb_20260706_010101", {
            "target_name": "mydb", "audited_at": "2026-07-06T01:01:01+00:00",
        })
        self._seed_capture(tmp_rdst_home, "mydb", "capture_xyz", {
            "run_id": "capture_xyz", "target_name": "mydb",
            "started_at": "2026-07-05T01:01:01+00:00",
        })

        assert find_audit_run("audit_mydb_20260706_010101")["target_name"] == "mydb"
        assert find_audit_run("audit_mydb_2026")["target_name"] == "mydb"
        assert find_audit_run("capture_xyz")["run_id"] == "capture_xyz"
        assert find_audit_run("does-not-exist") is None

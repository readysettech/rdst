"""Tests for workload storage."""

import tempfile
from pathlib import Path

from features.audit.models import DatabaseSnapshot, WorkloadQuery, WorkloadRun
from features.audit.storage import AuditStorage


def _make_run(run_id="test_run_1", target="mydb"):
    return WorkloadRun(
        run_id=run_id,
        target_name=target,
        db_engine="postgresql",
        started_at="2026-03-17T12:00:00Z",
        ended_at="2026-03-17T12:30:00Z",
        duration_seconds=1800,
        source="live",
        snapshot_start=DatabaseSnapshot(timestamp="2026-03-17T12:00:00Z"),
        snapshot_end=DatabaseSnapshot(timestamp="2026-03-17T12:30:00Z"),
        queries=[
            WorkloadQuery(
                query_hash="abc123",
                query_text="SELECT * FROM users",
                normalized_query="SELECT * FROM users",
                calls=500,
                total_time_ms=15000.0,
                avg_time_ms=30.0,
                pct_total_time=100.0,
            ),
        ],
        total_queries=500,
        total_query_time_ms=15000.0,
    )


class TestAuditStorage:
    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.storage = AuditStorage(base_dir=self.tmpdir)

    def test_generate_run_id(self):
        rid = self.storage.generate_run_id()
        assert len(rid) > 10
        assert "_" in rid

    def test_save_and_load(self):
        run = _make_run()
        path = self.storage.save_run(run)
        assert Path(path).exists()

        loaded = self.storage.load_run("mydb", "test_run_1")
        assert loaded is not None
        assert loaded["run_id"] == "test_run_1"
        assert loaded["total_queries"] == 500
        assert len(loaded["queries"]) == 1

    def test_list_runs(self):
        self.storage.save_run(_make_run("run_a", "mydb"))
        self.storage.save_run(_make_run("run_b", "mydb"))

        runs = self.storage.list_runs(target="mydb")
        assert len(runs) == 2

    def test_list_runs_all_targets(self):
        self.storage.save_run(_make_run("run_a", "db1"))
        self.storage.save_run(_make_run("run_b", "db2"))

        runs = self.storage.list_runs()
        assert len(runs) == 2

    def test_list_with_limit(self):
        for i in range(5):
            self.storage.save_run(_make_run(f"run_{i}", "mydb"))

        runs = self.storage.list_runs(target="mydb", limit=3)
        assert len(runs) == 3

    def test_load_nonexistent(self):
        assert self.storage.load_run("mydb", "nonexistent") is None

    def test_delete(self):
        self.storage.save_run(_make_run())
        assert self.storage.delete_run("mydb", "test_run_1") is True
        assert self.storage.load_run("mydb", "test_run_1") is None

    def test_prefix_match(self):
        self.storage.save_run(_make_run("20260317_120000_abc123", "mydb"))
        loaded = self.storage.load_run("mydb", "20260317_120000")
        assert loaded is not None


class TestSnapshotDelta:
    def test_compute_delta(self):
        from features.audit.query_stats import compute_snapshot_delta

        start = DatabaseSnapshot(
            timestamp="t1", engine="postgresql",
            xact_commit=100, blks_hit=5000, blks_read=50,
            tup_returned=10000, tup_fetched=5000,
            tup_inserted=100, tup_updated=50, tup_deleted=10,
        )
        end = DatabaseSnapshot(
            timestamp="t2", engine="postgresql",
            xact_commit=200, blks_hit=10000, blks_read=80,
            tup_returned=20000, tup_fetched=10000,
            tup_inserted=150, tup_updated=80, tup_deleted=20,
        )

        delta = compute_snapshot_delta(start, end)
        assert delta["xact_commit"] == 100
        assert delta["blks_hit"] == 5000
        assert delta["blks_read"] == 30
        assert delta["tup_returned"] == 10000
        assert delta["tup_inserted"] == 50
        assert "read_pct" in delta
        assert "cache_hit_ratio_delta" in delta

    def test_mysql_delta(self):
        from features.audit.query_stats import compute_snapshot_delta

        start = DatabaseSnapshot(
            timestamp="t1", engine="mysql",
            com_select=1000, com_insert=100, com_update=50, com_delete=10,
        )
        end = DatabaseSnapshot(
            timestamp="t2", engine="mysql",
            com_select=2000, com_insert=150, com_update=70, com_delete=15,
        )

        delta = compute_snapshot_delta(start, end)
        assert delta["com_select"] == 1000
        assert delta["com_insert"] == 50
        assert "read_pct" in delta


class TestCaptureServiceHelpers:
    def test_parse_duration(self):
        from features.audit.capture_service import _parse_duration

        assert _parse_duration("30m") == 1800
        assert _parse_duration("1h") == 3600
        assert _parse_duration("5m") == 300
        assert _parse_duration("30s") == 30
        assert _parse_duration("120") == 120

    def test_normalize(self):
        from features.audit.capture_service import _normalize

        assert "?" in _normalize("SELECT * FROM users WHERE id = 123")
        assert "?" in _normalize("SELECT * FROM users WHERE name = 'alice'")

"""Tests for snapshot store."""

import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from features.audit.models import (
    AuditMetrics,
    AuditResult,
    CacheOpportunityScore,
    SizingAssessment,
)
from features.fleet.models import (
    FleetAuditSnapshot,
    SizingVerdict,
)
from features.fleet.snapshot_store import SnapshotStore


def _make_snapshot(snapshot_id, targets=2, cache_hit=99.0):
    results = []
    for i in range(targets):
        results.append(asdict(AuditResult(
            target_name=f"db{i+1}",
            engine="postgresql",
            host=f"h{i+1}",
            metrics=AuditMetrics(
                cache_hit_rate=cache_hit + i,
                connection_utilization_pct=10.0 + i * 5,
                database_size_mb=100.0 * (i + 1),
            ),
            sizing=SizingAssessment(verdict=SizingVerdict.RIGHT_SIZED),
            cache_opportunity=CacheOpportunityScore(score=50),
        )))
    return FleetAuditSnapshot(
        snapshot_id=snapshot_id,
        name=snapshot_id,
        created_at="2026-03-17T12:00:00Z",
        targets_audited=targets,
        results=results,
    )


class TestSnapshotStore:
    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.store = SnapshotStore(base_dir=self.tmpdir)

    def test_save_and_load(self):
        snap = _make_snapshot("test-1")
        path = self.store.save(snap)
        assert Path(path).exists()

        loaded = self.store.load("test-1")
        assert loaded is not None
        assert loaded["snapshot_id"] == "test-1"
        assert loaded["targets_audited"] == 2

    def test_list_snapshots(self):
        self.store.save(_make_snapshot("snap-a"))
        self.store.save(_make_snapshot("snap-b"))

        snapshots = self.store.list_snapshots()
        assert len(snapshots) == 2
        names = {s["name"] for s in snapshots}
        assert "snap-a" in names
        assert "snap-b" in names

    def test_delete(self):
        self.store.save(_make_snapshot("to-delete"))
        assert self.store.delete("to-delete") is True
        assert self.store.load("to-delete") is None

    def test_load_nonexistent(self):
        assert self.store.load("nonexistent") is None

    def test_prefix_match(self):
        self.store.save(_make_snapshot("march-baseline"))
        loaded = self.store.load("march")
        assert loaded is not None
        assert loaded["snapshot_id"] == "march-baseline"

    def test_diff_basic(self):
        snap1 = _make_snapshot("before", cache_hit=95.0)
        snap2 = _make_snapshot("after", cache_hit=99.0)
        self.store.save(snap1)
        self.store.save(snap2)

        diff = self.store.diff("before", "after")
        assert diff is not None
        assert diff.baseline_id == "before"
        assert diff.current_id == "after"
        # Should detect cache_hit_rate change
        cache_entries = [e for e in diff.entries if e.field_name == "cache_hit_rate"]
        assert len(cache_entries) > 0

    def test_diff_new_and_removed_targets(self):
        # Baseline has db1, db2; current has db1, db3
        snap1_data = asdict(_make_snapshot("base"))
        snap1_data["results"][1]["target_name"] = "db2"
        self.store.save_raw("base", snap1_data)

        snap2_data = asdict(_make_snapshot("curr"))
        snap2_data["results"][1]["target_name"] = "db3"
        self.store.save_raw("curr", snap2_data)

        diff = self.store.diff("base", "curr")
        assert diff is not None
        assert "db3" in diff.new_targets
        assert "db2" in diff.removed_targets

    def test_diff_missing_snapshot(self):
        self.store.save(_make_snapshot("exists"))
        diff = self.store.diff("exists", "nonexistent")
        assert diff is None

    def test_save_raw(self):
        data = {"custom_key": "custom_value", "nested": {"a": 1}}
        path = self.store.save_raw("raw-test", data)
        assert Path(path).exists()

        loaded = self.store.load("raw-test")
        assert loaded["custom_key"] == "custom_value"

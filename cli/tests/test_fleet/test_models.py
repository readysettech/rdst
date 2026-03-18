"""Tests for fleet and workload data models."""

import json
from dataclasses import asdict

from lib.fleet.models import (
    AuditMetrics,
    AuditResult,
    CacheOpportunityScore,
    DatabaseSnapshot,
    FleetAuditSnapshot,
    FleetDiff,
    FleetDiffEntry,
    FleetMember,
    IntermediateSnapshot,
    SizingAssessment,
    SizingVerdict,
    TableStats,
    WorkloadAnalysis,
    WorkloadDiff,
    WorkloadQuery,
    WorkloadRun,
)


class TestSizingVerdict:
    def test_enum_values(self):
        assert SizingVerdict.UNDER_PROVISIONED == "under_provisioned"
        assert SizingVerdict.OVERSIZED == "oversized"
        assert SizingVerdict.RIGHT_SIZED == "right_sized"
        assert SizingVerdict.UNKNOWN == "unknown"

    def test_string_conversion(self):
        assert str(SizingVerdict.RIGHT_SIZED) == "SizingVerdict.RIGHT_SIZED"
        assert SizingVerdict.RIGHT_SIZED.value == "right_sized"


class TestAuditMetrics:
    def test_default_values(self):
        m = AuditMetrics()
        assert m.max_connections == 0
        assert m.cache_hit_rate == 0.0
        assert m.is_replica is False
        assert m.replication_lag_seconds is None

    def test_serialization_roundtrip(self):
        m = AuditMetrics(
            max_connections=100,
            active_connections=42,
            cache_hit_rate=99.5,
            database_size_mb=1024.0,
            server_version="15.4",
            is_replica=True,
            replication_lag_seconds=0.5,
        )
        d = asdict(m)
        assert d["max_connections"] == 100
        assert d["is_replica"] is True
        # Can serialize to JSON
        j = json.dumps(d)
        assert "99.5" in j


class TestFleetAuditSnapshot:
    def test_snapshot_creation(self):
        snap = FleetAuditSnapshot(
            snapshot_id="test-123",
            name="baseline",
            created_at="2026-03-17T12:00:00Z",
            targets_audited=4,
            results=[
                AuditResult(target_name="db1", engine="postgresql", host="h1"),
                AuditResult(target_name="db2", engine="mysql", host="h2"),
            ],
        )
        assert snap.targets_audited == 4
        assert len(snap.results) == 2

    def test_serialization(self):
        snap = FleetAuditSnapshot(snapshot_id="x", results=[])
        d = asdict(snap)
        j = json.dumps(d)
        assert '"snapshot_id": "x"' in j


class TestDatabaseSnapshot:
    def test_postgres_fields(self):
        s = DatabaseSnapshot(
            timestamp="2026-03-17T12:00:00Z",
            engine="postgresql",
            xact_commit=1000,
            blks_hit=50000,
            blks_read=500,
            cache_hit_ratio=99.0,
        )
        assert s.xact_commit == 1000
        assert s.cache_hit_ratio == 99.0

    def test_mysql_fields(self):
        s = DatabaseSnapshot(
            timestamp="2026-03-17T12:00:00Z",
            engine="mysql",
            com_select=5000,
            com_insert=100,
            innodb_buffer_pool_read_requests=100000,
        )
        assert s.com_select == 5000


class TestIntermediateSnapshot:
    def test_creation(self):
        s = IntermediateSnapshot(
            timestamp="2026-03-17T12:05:00Z",
            elapsed_seconds=300.0,
            active_connections=15,
            cache_hit_ratio=99.2,
            transactions_per_sec=150.5,
        )
        assert s.elapsed_seconds == 300.0
        assert s.transactions_per_sec == 150.5


class TestWorkloadRun:
    def test_full_run(self):
        run = WorkloadRun(
            run_id="20260317_120000_abc",
            target_name="mydb",
            db_engine="postgresql",
            started_at="2026-03-17T12:00:00Z",
            ended_at="2026-03-17T12:30:00Z",
            duration_seconds=1800,
            source="live",
            queries=[
                WorkloadQuery(
                    query_hash="abc123",
                    query_text="SELECT * FROM users",
                    normalized_query="SELECT * FROM users",
                    calls=500,
                    total_time_ms=15000.0,
                    avg_time_ms=30.0,
                    pct_total_time=45.0,
                ),
            ],
            total_queries=500,
            total_query_time_ms=33000.0,
            intermediate_snapshots=[
                IntermediateSnapshot(
                    timestamp="2026-03-17T12:05:00Z",
                    elapsed_seconds=300.0,
                    active_connections=10,
                    cache_hit_ratio=99.5,
                ),
            ],
        )
        assert run.total_queries == 500
        assert len(run.queries) == 1
        assert len(run.intermediate_snapshots) == 1

        # Serialization
        d = asdict(run)
        j = json.dumps(d)
        assert "abc123" in j
        assert "20260317_120000_abc" in j


class TestFleetDiff:
    def test_diff_entries(self):
        diff = FleetDiff(
            baseline_id="snap1",
            current_id="snap2",
            entries=[
                FleetDiffEntry(
                    target_name="db1",
                    field_name="cache_hit_rate",
                    old_value=95.0,
                    new_value=99.0,
                    change_pct=4.2,
                ),
            ],
            new_targets=["db3"],
            removed_targets=["db4"],
        )
        assert len(diff.entries) == 1
        assert diff.new_targets == ["db3"]

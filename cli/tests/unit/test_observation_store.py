"""Tests for the cache.db observation store.

Covers creation-time configuration (user_version, WAL, incremental
auto_vacuum), write atomicity, the event log's seq contract, fenced
leases, collector-state round-trips, chunked retention, rebuild on a
too-new schema version, cross-connection visibility, RDST self-execution
attribution records, the best-effort evidence helper, and the journal
strategy (WAL on fixed runtimes, the DELETE rollback fallback otherwise).
"""

import logging
import sqlite3

import pytest

from shared.query_registry import observation_store
from shared.query_registry.observation_store import (
    OPEN_EXECUTION_MAX_AGE_SECONDS,
    ExecutionEvidenceWriter,
    SCHEMA_VERSION,
    LeaseLostError,
    ObservationStore,
    record_execution_evidence,
)
from shared.query_registry.query_registry import hash_sql


@pytest.fixture
def store(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    yield store
    store.close()


def snapshot_row(engine_key, calls=10, total=1.5):
    return {
        "engine_key": engine_key,
        "calls": calls,
        "rows": calls * 2,
        "total_exec_time": total,
        "mean_exec_time": total / calls,
        "min_exec_time": 0.01,
        "max_exec_time": 0.5,
        "stats_since": 1000,
    }


class TestCreation:
    """Creation-time pragmas and schema versioning."""

    def test_fresh_create_sets_version_wal_and_auto_vacuum(self, tmp_path):
        path = tmp_path / "cache.db"
        store = ObservationStore(path)
        try:
            conn = sqlite3.connect(path)
            try:
                assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
                assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
                # 2 = INCREMENTAL
                assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
            finally:
                conn.close()
        finally:
            store.close()

    def test_reopen_existing_file_keeps_version(self, tmp_path):
        path = tmp_path / "cache.db"
        first = ObservationStore(path)
        first.append_event("t1", "collector", '{"a":1}', created_at=100)
        first.close()

        second = ObservationStore(path)
        try:
            assert second.latest_seq("t1") == 1
        finally:
            second.close()

    def test_v1_integer_windows_migrate_atomically_to_real(self, tmp_path):
        path = tmp_path / "cache.db"
        conn = sqlite3.connect(path, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in observation_store._schema_v1(False):
                conn.execute(statement)
            conn.execute("PRAGMA user_version = 1")
            conn.execute(
                "INSERT INTO counter_snapshot VALUES "
                "('t','k','e',100,1,1,1.0,1.0,1.0,1.0,0)"
            )
            conn.execute(
                "INSERT INTO recent_observation VALUES "
                "('t','h',100,160,1,1.0,0.1,'fresh','complete','production_only')"
            )
            conn.execute(
                "INSERT INTO rdst_execution VALUES "
                "('t','h','rdst/ask','r',120,121,1)"
            )
            conn.execute("COMMIT")
        finally:
            conn.close()

        ObservationStore(path).close()
        conn = sqlite3.connect(path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
            assert conn.execute(
                "SELECT typeof(captured_at), captured_at FROM counter_snapshot"
            ).fetchone() == ("real", 100.0)
            assert conn.execute(
                "SELECT typeof(window_start), typeof(window_end) "
                "FROM recent_observation"
            ).fetchone() == ("real", "real")
            assert conn.execute(
                "SELECT typeof(started_at), typeof(ended_at) FROM rdst_execution"
            ).fetchone() == ("real", "real")
        finally:
            conn.close()

    def test_failed_v2_migration_rolls_back_all_table_rebuilds(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "cache.db"
        conn = sqlite3.connect(path, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in observation_store._schema_v1(False):
                conn.execute(statement)
            conn.execute("PRAGMA user_version = 1")
            conn.execute("COMMIT")
        finally:
            conn.close()

        monkeypatch.setitem(
            observation_store._MIGRATIONS,
            2,
            lambda _strict: (
                "ALTER TABLE counter_snapshot RENAME TO counter_snapshot_v1",
                "THIS IS NOT SQL",
            ),
        )
        with pytest.raises(sqlite3.OperationalError):
            ObservationStore(path)

        conn = sqlite3.connect(path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "counter_snapshot" in names
            assert "counter_snapshot_v1" not in names
        finally:
            conn.close()


class TestCounterSnapshots:
    def test_batch_write_round_trip(self, store):
        written = store.record_counter_snapshots(
            "t1", "epoch-1", [snapshot_row("k1"), snapshot_row("k2")], captured_at=500
        )
        assert written == 2

        conn = sqlite3.connect(store.path)
        try:
            rows = conn.execute(
                "SELECT engine_key, epoch_id, captured_at, calls FROM counter_snapshot "
                "WHERE target_id = 't1' ORDER BY engine_key"
            ).fetchall()
        finally:
            conn.close()
        assert rows == [("k1", "epoch-1", 500, 10), ("k2", "epoch-1", 500, 10)]

    def test_batch_write_is_atomic(self, store):
        # engine_key None violates NOT NULL on the third row; the whole
        # batch must roll back, including the two valid rows before it.
        bad_batch = [snapshot_row("k1"), snapshot_row("k2"), snapshot_row(None)]
        with pytest.raises(sqlite3.IntegrityError):
            store.record_counter_snapshots("t1", "epoch-1", bad_batch, captured_at=500)

        conn = sqlite3.connect(store.path)
        try:
            count = conn.execute("SELECT COUNT(*) FROM counter_snapshot").fetchone()[0]
        finally:
            conn.close()
        assert count == 0

    def test_retried_batch_is_idempotent(self, store):
        batch = [snapshot_row("k1", calls=10)]
        store.record_counter_snapshots("t1", "epoch-1", batch, captured_at=500)
        store.record_counter_snapshots(
            "t1", "epoch-1", [snapshot_row("k1", calls=12)], captured_at=500
        )

        conn = sqlite3.connect(store.path)
        try:
            rows = conn.execute(
                "SELECT calls FROM counter_snapshot WHERE target_id = 't1'"
            ).fetchall()
        finally:
            conn.close()
        assert rows == [(12,)]


class TestLatestCounterSnapshots:
    def test_latest_row_per_engine_key_within_epoch(self, store):
        store.record_counter_snapshots(
            "t1",
            "e1",
            [snapshot_row("k1", calls=10), snapshot_row("k2", calls=3)],
            captured_at=500,
        )
        store.record_counter_snapshots(
            "t1", "e1", [snapshot_row("k1", calls=12)], captured_at=600
        )

        rows = {
            row["engine_key"]: row
            for row in store.latest_counter_snapshots("t1", "e1")
        }
        assert set(rows) == {"k1", "k2"}
        assert (rows["k1"]["calls"], rows["k1"]["captured_at"]) == (12, 600)
        assert (rows["k2"]["calls"], rows["k2"]["captured_at"]) == (3, 500)
        assert rows["k1"]["total_exec_time"] == 1.5
        assert rows["k1"]["stats_since"] == 1000

    def test_scoped_per_target_and_epoch(self, store):
        store.record_counter_snapshots(
            "t1", "e1", [snapshot_row("k1", calls=10)], captured_at=500
        )
        store.record_counter_snapshots(
            "t1", "e2", [snapshot_row("k1", calls=99)], captured_at=600
        )

        assert [
            row["calls"] for row in store.latest_counter_snapshots("t1", "e1")
        ] == [10]
        assert [
            row["calls"] for row in store.latest_counter_snapshots("t1", "e2")
        ] == [99]
        assert store.latest_counter_snapshots("t2", "e1") == []


class TestIdentityAliases:
    def test_round_trip_and_upsert(self, store):
        assert store.record_identity_aliases("t1", "e1", {"k1": "h1", "k2": "h2"}) == 2
        assert store.get_identity_aliases("t1", "e1") == {"k1": "h1", "k2": "h2"}

        store.record_identity_aliases("t1", "e1", {"k1": "h9"})
        assert store.get_identity_aliases("t1", "e1") == {"k1": "h9", "k2": "h2"}

    def test_aliases_are_scoped_per_target_and_epoch(self, store):
        store.record_identity_aliases("t1", "e1", {"k1": "h1"})
        assert store.get_identity_aliases("t1", "e2") == {}
        assert store.get_identity_aliases("t2", "e1") == {}

    def test_empty_mapping_writes_nothing(self, store):
        assert store.record_identity_aliases("t1", "e1", {}) == 0


def observation_row(normalized_hash, window_end=160, calls_delta=10,
                    completeness="complete"):
    return {
        "normalized_hash": normalized_hash,
        "window_start": 100,
        "window_end": window_end,
        "calls_delta": calls_delta,
        "exec_time_delta": 25.5,
        "approximate_qps": calls_delta / 60 if calls_delta is not None else None,
        "completeness": completeness,
    }


class TestRecentObservations:
    def test_batch_round_trip_with_defaults(self, store):
        written = store.record_recent_observations(
            "t1", [observation_row("h1"), observation_row("h2")]
        )
        assert written == 2

        conn = sqlite3.connect(store.path)
        try:
            rows = conn.execute(
                "SELECT normalized_hash, window_start, window_end, calls_delta, "
                "freshness, completeness, attribution FROM recent_observation "
                "WHERE target_id = 't1' ORDER BY normalized_hash"
            ).fetchall()
        finally:
            conn.close()
        assert rows == [
            ("h1", 100, 160, 10, "interval", "complete", "production_only"),
            ("h2", 100, 160, 10, "interval", "complete", "production_only"),
        ]

    def test_retried_batch_is_idempotent(self, store):
        store.record_recent_observations("t1", [observation_row("h1", calls_delta=10)])
        store.record_recent_observations("t1", [observation_row("h1", calls_delta=12)])

        conn = sqlite3.connect(store.path)
        try:
            rows = conn.execute(
                "SELECT calls_delta FROM recent_observation WHERE target_id = 't1'"
            ).fetchall()
        finally:
            conn.close()
        assert rows == [(12,)]

    def test_incomplete_window_stores_null_metrics(self, store):
        row = observation_row("h1", calls_delta=None, completeness="epoch_changed")
        row["exec_time_delta"] = None
        store.record_recent_observations("t1", [row])

        conn = sqlite3.connect(store.path)
        try:
            stored = conn.execute(
                "SELECT calls_delta, exec_time_delta, approximate_qps, completeness "
                "FROM recent_observation WHERE target_id = 't1'"
            ).fetchone()
        finally:
            conn.close()
        assert stored == (None, None, None, "epoch_changed")

    def test_per_row_attribution_overrides_batch_default(self, store):
        marked = observation_row("h1")
        marked["attribution"] = "contains_rdst_traffic"
        store.record_recent_observations("t1", [marked, observation_row("h2")])

        conn = sqlite3.connect(store.path)
        try:
            rows = conn.execute(
                "SELECT normalized_hash, attribution FROM recent_observation "
                "WHERE target_id = 't1' ORDER BY normalized_hash"
            ).fetchall()
        finally:
            conn.close()
        assert rows == [
            ("h1", "contains_rdst_traffic"),
            ("h2", "production_only"),
        ]

    def test_empty_batch_writes_nothing(self, store):
        assert store.record_recent_observations("t1", []) == 0


def execution_row(
    normalized_hash="h1",
    run_id="r1",
    started_at=100,
    ended_at=200,
    exec_count=5,
    lane="rdst/loadtest",
    target_id="t1",
):
    return {
        "target_id": target_id,
        "normalized_hash": normalized_hash,
        "lane": lane,
        "run_id": run_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "exec_count": exec_count,
    }


class TestRdstExecutions:
    def test_batch_round_trip(self, store):
        written = store.record_rdst_executions(
            [
                execution_row("h1", run_id="r1", exec_count=5),
                execution_row("h2", run_id="r1", exec_count=1, lane="rdst/ask"),
            ]
        )
        assert written == 2

        rows = store.rdst_executions_overlapping("t1", "h1", 0, 1000)
        assert rows == [
            {
                "lane": "rdst/loadtest",
                "run_id": "r1",
                "started_at": 100,
                "ended_at": 200,
                "exec_count": 5,
            }
        ]
        assert store.rdst_executions_overlapping("t1", "h2", 0, 1000) == [
            {
                "lane": "rdst/ask",
                "run_id": "r1",
                "started_at": 100,
                "ended_at": 200,
                "exec_count": 1,
            }
        ]

    def test_retried_run_overwrites_its_own_rows(self, store):
        store.record_rdst_executions([execution_row(exec_count=5, ended_at=200)])
        store.record_rdst_executions([execution_row(exec_count=7, ended_at=250)])

        rows = store.rdst_executions_overlapping("t1", "h1", 0, 1000)
        assert len(rows) == 1
        assert rows[0]["exec_count"] == 7
        assert rows[0]["ended_at"] == 250

    def test_unknown_count_round_trips_as_none(self, store):
        store.record_rdst_executions([execution_row(exec_count=None)])
        rows = store.rdst_executions_overlapping("t1", "h1", 0, 1000)
        assert rows[0]["exec_count"] is None

    def test_empty_batch_writes_nothing(self, store):
        assert store.record_rdst_executions([]) == 0

    def test_direct_store_suppresses_zero_and_boolean_counts(self, store):
        assert (
            store.record_rdst_executions(
                [
                    execution_row(run_id="zero", exec_count=0),
                    execution_row(run_id="false", exec_count=False),
                ]
            )
            == 0
        )
        assert store.rdst_executions_overlapping("t1", "h1", 0, 1000) == []

    @pytest.mark.parametrize(
        ("window_start", "window_end", "overlaps"),
        [
            (200, 300, False),  # (start, end]: shared start belongs prior window
            (50, 100, True),  # touching: window ends the second the run starts
            (120, 180, True),  # window inside the execution span
            (50, 300, True),  # window spans the execution
            (201, 300, False),  # entirely after
            (10, 99, False),  # entirely before
        ],
    )
    def test_overlap_boundaries(self, store, window_start, window_end, overlaps):
        # The recorded execution spans [100, 200].
        store.record_rdst_executions([execution_row(started_at=100, ended_at=200)])
        rows = store.rdst_executions_overlapping(
            "t1", "h1", window_start, window_end
        )
        assert bool(rows) is overlaps

    def test_open_ended_execution_overlaps_windows_up_to_the_age_bound(self, store):
        store.record_rdst_executions(
            [execution_row(started_at=100, ended_at=None)]
        )
        implied_end = 100 + OPEN_EXECUTION_MAX_AGE_SECONDS
        assert store.rdst_executions_overlapping("t1", "h1", 500, 600)
        assert store.rdst_executions_overlapping("t1", "h1", 50, 100)
        assert store.rdst_executions_overlapping("t1", "h1", 10, 99) == []
        # A row whose end was never recorded is treated as ended at
        # started_at plus the bound: a stale open row (e.g. from a crashed
        # lane) stops marking much-later windows.
        rows = store.rdst_executions_overlapping(
            "t1", "h1", implied_end - 1, implied_end + 100
        )
        assert [row["ended_at"] for row in rows] == [None]
        assert (
            store.rdst_executions_overlapping(
                "t1", "h1", implied_end, implied_end + 100
            )
            == []
        )

    def test_results_ordered_oldest_first(self, store):
        store.record_rdst_executions(
            [
                execution_row(run_id="r2", started_at=300, ended_at=400),
                execution_row(run_id="r1", started_at=100, ended_at=200),
            ]
        )
        rows = store.rdst_executions_overlapping("t1", "h1", 0, 1000)
        assert [row["run_id"] for row in rows] == ["r1", "r2"]

    def test_scoped_per_target_and_identity(self, store):
        store.record_rdst_executions([execution_row("h1", target_id="t1")])
        assert store.rdst_executions_overlapping("t2", "h1", 0, 1000) == []
        assert store.rdst_executions_overlapping("t1", "h2", 0, 1000) == []

    def test_batched_reader_matches_single_reader_per_window(self, store):
        store.record_rdst_executions(
            [
                execution_row("h1", run_id="r1", started_at=100, ended_at=200),
                execution_row("h1", run_id="r2", started_at=300, ended_at=400),
                execution_row("h2", run_id="r1", started_at=50, ended_at=None),
                execution_row("h2", run_id="r3", started_at=500, ended_at=600),
            ]
        )
        windows = [
            ("h1", 0.0, 1000.0),
            ("h1", 250.0, 350.0),
            ("h1", 900.0, 950.0),
            ("h2", 0.0, 1000.0),
            ("h3", 0.0, 1000.0),
        ]

        batched = store.rdst_executions_overlapping_many("t1", windows)

        assert set(batched) == set(windows)
        for normalized_hash, window_start, window_end in windows:
            assert batched[
                (normalized_hash, window_start, window_end)
            ] == store.rdst_executions_overlapping(
                "t1", normalized_hash, window_start, window_end
            )

    def test_batched_reader_with_no_windows_returns_empty(self, store):
        assert store.rdst_executions_overlapping_many("t1", []) == {}

    def test_fenced_write_with_stale_token_persists_nothing(self, store):
        store.acquire_lease("t1", "owner-a", ttl_s=30, now=1000)
        with pytest.raises(LeaseLostError):
            store.record_rdst_executions(
                [execution_row()],
                owner_id="owner-a",
                fencing_token=99,
                now=1005,
            )
        assert store.rdst_executions_overlapping("t1", "h1", 0, 1000) == []

    def test_fenced_write_with_live_token_succeeds(self, store):
        token = store.acquire_lease("t1", "owner-a", ttl_s=30, now=1000)
        written = store.record_rdst_executions(
            [execution_row()],
            owner_id="owner-a",
            fencing_token=token,
            now=1005,
        )
        assert written == 1


class TestRecordExecutionEvidence:
    """The best-effort helper the execution lanes call."""

    def test_missing_cache_db_is_a_noop_that_creates_nothing(self, tmp_path):
        empty = tmp_path / "rdst-data"
        empty.mkdir()
        record_execution_evidence(
            "t1",
            [{"sql": "SELECT 1", "exec_count": 1}],
            lane="rdst/ask",
            run_id="r1",
            started_at=100,
            ended_at=200,
            cache_db_path=empty / "cache.db",
        )
        assert list(empty.iterdir()) == []

    def test_default_path_missing_is_a_noop(self, tmp_path, monkeypatch):
        # A CLI-only user who never ran web discovery has no cache.db, and
        # recording through the default path must not create one.
        empty = tmp_path / "rdst-data"
        empty.mkdir()
        monkeypatch.setattr(
            observation_store,
            "default_cache_db_path",
            lambda: empty / "cache.db",
        )
        record_execution_evidence(
            "t1",
            [{"sql": "SELECT 1", "exec_count": 1}],
            lane="rdst/ask",
            run_id="r1",
            started_at=100,
            ended_at=200,
        )
        assert list(empty.iterdir()) == []

    def test_records_and_merges_by_identity(self, tmp_path):
        path = tmp_path / "cache.db"
        ObservationStore(path).close()

        # The first two statements normalize to the same identity; the third
        # is a structurally different query and stays separate.
        record_execution_evidence(
            "t1",
            [
                {"sql": "SELECT a FROM users WHERE id = 1", "exec_count": 2},
                {"sql": "select a from users where id = 2", "exec_count": 3},
                {"sql": "SELECT b FROM orders", "exec_count": 1},
            ],
            lane="rdst/loadtest",
            run_id="r1",
            started_at=100,
            ended_at=200,
            cache_db_path=path,
        )

        store = ObservationStore(path)
        try:
            merged = store.rdst_executions_overlapping(
                "t1", hash_sql("SELECT a FROM users WHERE id = 1"), 0, 1000
            )
            assert merged == [
                {
                    "lane": "rdst/loadtest",
                    "run_id": "r1",
                    "started_at": 100,
                    "ended_at": 200,
                    "exec_count": 5,
                }
            ]
            other = store.rdst_executions_overlapping(
                "t1", hash_sql("SELECT b FROM orders"), 0, 1000
            )
            assert [row["exec_count"] for row in other] == [1]
        finally:
            store.close()

    def test_unknown_count_poisons_the_merge(self, tmp_path):
        path = tmp_path / "cache.db"
        ObservationStore(path).close()

        record_execution_evidence(
            "t1",
            [
                {"sql": "SELECT 1", "exec_count": 2},
                {"sql": "SELECT 1", "exec_count": None},
            ],
            lane="rdst/analyze",
            run_id="r1",
            started_at=100,
            ended_at=200,
            cache_db_path=path,
        )

        store = ObservationStore(path)
        try:
            rows = store.rdst_executions_overlapping(
                "t1", hash_sql("SELECT 1"), 0, 1000
            )
            assert rows[0]["exec_count"] is None
        finally:
            store.close()

    def test_store_failure_never_raises(self, tmp_path, monkeypatch):
        path = tmp_path / "cache.db"
        ObservationStore(path).close()

        def broken_store(*args, **kwargs):
            raise RuntimeError("store exploded")

        monkeypatch.setattr(observation_store, "ObservationStore", broken_store)
        record_execution_evidence(
            "t1",
            [{"sql": "SELECT 1", "exec_count": 1}],
            lane="rdst/compare",
            run_id="r1",
            started_at=100,
            ended_at=200,
            cache_db_path=path,
        )

    def test_missing_target_or_sql_is_a_noop(self, tmp_path):
        path = tmp_path / "cache.db"
        ObservationStore(path).close()

        record_execution_evidence(
            None,
            [{"sql": "SELECT 1", "exec_count": 1}],
            lane="rdst/ask",
            run_id="r1",
            started_at=100,
            ended_at=200,
            cache_db_path=path,
        )
        record_execution_evidence(
            "t1",
            [{"sql": "", "exec_count": 1}],
            lane="rdst/ask",
            run_id="r2",
            started_at=100,
            ended_at=200,
            cache_db_path=path,
        )

        store = ObservationStore(path)
        try:
            conn = sqlite3.connect(store.path)
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM rdst_execution"
                ).fetchone()[0]
            finally:
                conn.close()
            assert count == 0
        finally:
            store.close()

    def test_zero_only_evidence_writes_no_row(self, tmp_path):
        path = tmp_path / "cache.db"
        ObservationStore(path).close()

        record_execution_evidence(
            "t1",
            [
                {"sql": "SELECT 1", "exec_count": 0},
                {"sql": "SELECT 2", "exec_count": False},
            ],
            lane="rdst/loadtest",
            run_id="zero",
            started_at=100.1,
            ended_at=100.2,
            cache_db_path=path,
        )

        conn = sqlite3.connect(path)
        try:
            assert conn.execute("SELECT COUNT(*) FROM rdst_execution").fetchone()[0] == 0
        finally:
            conn.close()

    def test_run_scoped_writer_reuses_one_store_for_many_flushes(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "cache.db"
        ObservationStore(path).close()
        real_store = observation_store.ObservationStore
        opened = 0

        def counting_store(*args, **kwargs):
            nonlocal opened
            opened += 1
            return real_store(*args, **kwargs)

        monkeypatch.setattr(observation_store, "ObservationStore", counting_store)
        writer = ExecutionEvidenceWriter(
            "t1", lane="rdst/compare", cache_db_path=path
        )
        try:
            for second in range(3):
                writer.record(
                    [{"sql": "SELECT 1", "exec_count": 1}],
                    run_id=f"r:{second}",
                    started_at=100.0 + second,
                    ended_at=100.1 + second,
                )
        finally:
            writer.close()
        assert opened == 1

    def test_run_scoped_writer_detects_cache_created_after_run_start(self, tmp_path):
        path = tmp_path / "cache.db"
        writer = ExecutionEvidenceWriter(
            "t1", lane="rdst/loadtest", cache_db_path=path
        )
        try:
            writer.record(
                [{"sql": "SELECT 1", "exec_count": 1}],
                run_id="before",
                started_at=100.0,
                ended_at=100.1,
            )
            assert not path.exists()

            ObservationStore(path).close()
            writer.record(
                [{"sql": "SELECT 1", "exec_count": 1}],
                run_id="after",
                started_at=101.0,
                ended_at=101.1,
            )
        finally:
            writer.close()

        with ObservationStore(path) as store:
            rows = store.rdst_executions_overlapping(
                "t1", hash_sql("SELECT 1"), 0.0, 200.0
            )
        assert [row["run_id"] for row in rows] == ["after"]


class TestEventLog:
    def test_seq_is_monotonic(self, store):
        seqs = [
            store.append_event("t1", "collector", {"n": n}, created_at=100 + n)
            for n in range(3)
        ]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == 3

    def test_events_since_returns_only_newer(self, store):
        first = store.append_event("t1", "collector", '{"n":1}', created_at=100)
        store.append_event("t1", "collector", '{"n":2}', created_at=101)
        store.append_event("t1", "collector", '{"n":3}', created_at=102)
        store.append_event("t2", "collector", '{"n":4}', created_at=103)

        events = store.events_since("t1", first)
        assert [event["payload"] for event in events] == ['{"n":2}', '{"n":3}']
        assert all(event["seq"] > first for event in events)
        assert [event["created_at"] for event in events] == [101, 102]

    def test_events_since_respects_limit(self, store):
        for n in range(5):
            store.append_event("t1", "collector", str(n), created_at=100 + n)
        events = store.events_since("t1", 0, limit=2)
        assert [event["payload"] for event in events] == ["0", "1"]

    def test_latest_seq(self, store):
        assert store.latest_seq("t1") == 0
        store.append_event("t1", "collector", "a", created_at=100)
        last = store.append_event("t1", "collector", "b", created_at=101)
        assert store.latest_seq("t1") == last
        assert store.latest_seq("t2") == 0

    def test_earliest_seq(self, store):
        assert store.earliest_seq("t1") == 0
        store.append_event("t2", "collector", "other", created_at=99)
        first = store.append_event("t1", "collector", "a", created_at=100)
        store.append_event("t1", "collector", "b", created_at=101)
        assert store.earliest_seq("t1") == first
        assert store.earliest_seq("t3") == 0


class TestLease:
    def test_fresh_acquire_returns_token_one(self, store):
        assert store.acquire_lease("t1", "owner-a", ttl_s=30, now=1000) == 1

    def test_second_owner_rejected_while_live(self, store):
        assert store.acquire_lease("t1", "owner-a", ttl_s=30, now=1000) == 1
        assert store.acquire_lease("t1", "owner-b", ttl_s=30, now=1010) is None

    def test_expired_lease_taken_over_with_incremented_token(self, store):
        assert store.acquire_lease("t1", "owner-a", ttl_s=30, now=1000) == 1
        # Lease expired at 1030; owner-b takes over and the fencing token
        # advances so a stalled owner-a can be rejected at write time.
        assert store.acquire_lease("t1", "owner-b", ttl_s=30, now=1031) == 2

    def test_same_owner_renewal_increments_token(self, store):
        assert store.acquire_lease("t1", "owner-a", ttl_s=30, now=1000) == 1
        assert store.acquire_lease("t1", "owner-a", ttl_s=30, now=1010) == 2
        # Renewal extended expiry, so another owner is still rejected.
        assert store.acquire_lease("t1", "owner-b", ttl_s=30, now=1035) is None

    def test_release_expires_lease_but_keeps_token_sequence(self, store):
        assert store.acquire_lease("t1", "owner-a", ttl_s=30, now=1000) == 1
        assert store.release_lease("t1", "owner-a") is True
        # The released row survives, so a new lease continues the token
        # sequence instead of restarting at 1 (the ABA hazard).
        assert store.acquire_lease("t1", "owner-b", ttl_s=30, now=1001) == 2

    def test_token_strictly_increases_across_release_cycles(self, store):
        tokens = []
        for cycle, owner in enumerate(("owner-a", "owner-b", "owner-a")):
            tokens.append(store.acquire_lease("t1", owner, ttl_s=30, now=1000 + cycle))
            assert store.release_lease("t1", owner) is True
        assert tokens == [1, 2, 3]

    def test_release_by_non_owner_is_a_noop(self, store):
        store.acquire_lease("t1", "owner-a", ttl_s=30, now=1000)
        assert store.release_lease("t1", "owner-b") is False
        assert store.acquire_lease("t1", "owner-b", ttl_s=30, now=1010) is None

    def test_leases_are_per_target(self, store):
        assert store.acquire_lease("t1", "owner-a", ttl_s=30, now=1000) == 1
        assert store.acquire_lease("t2", "owner-b", ttl_s=30, now=1000) == 1


class TestFencedWrites:
    """Write-time fencing: mutators verify the lease inside the transaction."""

    def test_correct_token_write_succeeds(self, store):
        token = store.acquire_lease("t1", "owner-a", ttl_s=30, now=1000)
        written = store.record_counter_snapshots(
            "t1",
            "e1",
            [snapshot_row("k1")],
            captured_at=1005,
            owner_id="owner-a",
            fencing_token=token,
            now=1005,
        )
        assert written == 1

    def test_stale_token_write_raises_and_persists_nothing(self, store):
        stale = store.acquire_lease("t1", "owner-a", ttl_s=30, now=1000)
        # owner-b takes over the expired lease; owner-a's token is now stale.
        assert store.acquire_lease("t1", "owner-b", ttl_s=30, now=1031) == stale + 1

        with pytest.raises(LeaseLostError):
            store.record_counter_snapshots(
                "t1",
                "e1",
                [snapshot_row("k1"), snapshot_row("k2")],
                captured_at=1032,
                owner_id="owner-a",
                fencing_token=stale,
                now=1032,
            )

        conn = sqlite3.connect(store.path)
        try:
            count = conn.execute("SELECT COUNT(*) FROM counter_snapshot").fetchone()[0]
        finally:
            conn.close()
        assert count == 0

    def test_expired_lease_write_raises_even_for_same_owner(self, store):
        token = store.acquire_lease("t1", "owner-a", ttl_s=30, now=1000)
        with pytest.raises(LeaseLostError):
            store.append_event(
                "t1",
                "collector",
                "x",
                created_at=1031,
                owner_id="owner-a",
                fencing_token=token,
                now=1031,
            )
        assert store.latest_seq("t1") == 0

    def test_released_lease_write_raises_with_old_token(self, store):
        token = store.acquire_lease("t1", "owner-a", ttl_s=30, now=1000)
        store.release_lease("t1", "owner-a")
        with pytest.raises(LeaseLostError):
            store.record_recent_observations(
                "t1",
                [observation_row("h1")],
                owner_id="owner-a",
                fencing_token=token,
                now=1005,
            )

    def test_fence_covers_every_mutating_api(self, store):
        store.acquire_lease("t1", "owner-a", ttl_s=30, now=1000)
        stale = {"owner_id": "owner-a", "fencing_token": 99, "now": 1005}
        with pytest.raises(LeaseLostError):
            store.upsert_collector_state("t1", state="watching", **stale)
        with pytest.raises(LeaseLostError):
            store.record_identity_aliases("t1", "e1", {"k1": "h1"}, **stale)
        with pytest.raises(LeaseLostError):
            store.record_recent_observations("t1", [observation_row("h1")], **stale)
        assert store.get_collector_state("t1") is None
        assert store.get_identity_aliases("t1", "e1") == {}

    def test_fenced_write_without_any_lease_row_raises(self, store):
        with pytest.raises(LeaseLostError):
            store.append_event(
                "t1", "collector", "x", created_at=100,
                owner_id="owner-a", fencing_token=1, now=100,
            )

    def test_partial_fence_arguments_are_rejected(self, store):
        store.acquire_lease("t1", "owner-a", ttl_s=30, now=1000)
        with pytest.raises(ValueError):
            store.append_event(
                "t1", "collector", "x", created_at=100, owner_id="owner-a"
            )
        assert store.latest_seq("t1") == 0

    def test_omitted_fence_arguments_keep_writes_unfenced(self, store):
        # No lease exists at all; unfenced writes behave exactly as before.
        seq = store.append_event("t1", "collector", "x", created_at=100)
        assert seq == 1
        assert store.record_counter_snapshots(
            "t1", "e1", [snapshot_row("k1")], captured_at=100
        ) == 1


class TestCollectorState:
    def test_upsert_round_trip(self, store):
        store.upsert_collector_state(
            "t1",
            state="idle",
            last_attempt_at=100,
            last_success_at=100,
            duration_ms=250,
            next_due_at=400,
            source_capabilities={"pg_stat_statements": True},
            epoch_id="epoch-1",
        )
        state = store.get_collector_state("t1")
        assert state["state"] == "idle"
        assert state["last_attempt_at"] == 100
        assert state["last_success_at"] == 100
        assert state["duration_ms"] == 250
        assert state["next_due_at"] == 400
        assert state["error_code"] is None
        assert state["source_capabilities"] == {"pg_stat_statements": True}
        assert state["epoch_id"] == "epoch-1"

    def test_upsert_replaces_previous_row(self, store):
        store.upsert_collector_state(
            "t1", state="idle", last_success_at=100, epoch_id="epoch-1"
        )
        store.upsert_collector_state(
            "t1", state="error", last_attempt_at=200, error_code="timeout"
        )
        state = store.get_collector_state("t1")
        assert state["state"] == "error"
        assert state["error_code"] == "timeout"
        # Full-row replace: fields omitted from the second call are cleared.
        assert state["last_success_at"] is None
        assert state["epoch_id"] is None

    def test_missing_target_returns_none(self, store):
        assert store.get_collector_state("nope") is None


class TestPrune:
    def test_prune_deletes_old_snapshots_in_chunks(self, store):
        for captured_at in (100, 200, 300):
            store.record_counter_snapshots(
                "t1",
                "epoch-1",
                [snapshot_row("k1"), snapshot_row("k2")],
                captured_at=captured_at,
            )
        # 4 old rows against chunk=1 forces multiple delete iterations.
        deleted = store.prune(cutoff=300, chunk=1)
        assert deleted == 4

        conn = sqlite3.connect(store.path)
        try:
            remaining = conn.execute(
                "SELECT DISTINCT captured_at FROM counter_snapshot"
            ).fetchall()
        finally:
            conn.close()
        assert remaining == [(300,)]

    def test_prune_with_nothing_to_delete(self, store):
        store.record_counter_snapshots(
            "t1", "epoch-1", [snapshot_row("k1")], captured_at=500
        )
        assert store.prune(cutoff=100) == 0

    def test_prune_keeps_latest_snapshot_per_engine_key_and_epoch(self, store):
        for captured_at in (100, 200):
            store.record_counter_snapshots(
                "t1", "epoch-1", [snapshot_row("k1")], captured_at=captured_at
            )
        store.record_counter_snapshots(
            "t1", "epoch-2", [snapshot_row("k1")], captured_at=150
        )

        # A cutoff above every row may only remove superseded snapshots:
        # each (engine_key, epoch) keeps its newest row so a restarted
        # collector can restore its delta baseline.
        deleted = store.prune(cutoff=1000)

        assert deleted == 1
        epoch_one = store.latest_counter_snapshots("t1", "epoch-1")
        assert [row["captured_at"] for row in epoch_one] == [200]
        epoch_two = store.latest_counter_snapshots("t1", "epoch-2")
        assert [row["captured_at"] for row in epoch_two] == [150]

    def test_prune_rdst_executions_is_chunked_and_keeps_open_unknown(self, store):
        store.record_rdst_executions(
            [
                execution_row(run_id="old-1", ended_at=100.0),
                execution_row(run_id="old-2", ended_at=110.0),
                execution_row(run_id="recent", ended_at=200.0),
                execution_row(run_id="open", started_at=90.0, ended_at=None, exec_count=None),
            ]
        )

        assert store.prune_rdst_executions(150.0, chunk=1) == 2
        rows = store.rdst_executions_overlapping("t1", "h1", 0.0, 1000.0)
        assert {row["run_id"] for row in rows} == {"recent", "open"}

    def test_prune_rdst_executions_closes_stale_open_rows(self, store):
        bound = OPEN_EXECUTION_MAX_AGE_SECONDS
        now = 10_000.0
        store.record_rdst_executions(
            [
                execution_row(run_id="stale-open", started_at=100.0, ended_at=None),
                execution_row(
                    run_id="fresh-open", started_at=now - bound + 1, ended_at=None
                ),
                execution_row(
                    run_id="long-closed",
                    started_at=now - bound + 1,
                    ended_at=now - 1,
                ),
            ]
        )

        store.prune_rdst_executions(0.0, now=now)

        conn = sqlite3.connect(store.path)
        try:
            rows = dict(
                conn.execute(
                    "SELECT run_id, ended_at FROM rdst_execution"
                ).fetchall()
            )
        finally:
            conn.close()
        # The stale open row is closed at started_at plus the bound; the
        # fresh open row and the legitimately long closed row are untouched.
        assert rows == {
            "stale-open": 100.0 + bound,
            "fresh-open": None,
            "long-closed": now - 1,
        }

    def test_prune_rdst_executions_deletes_closed_stale_opens_past_cutoff(
        self, store
    ):
        store.record_rdst_executions(
            [execution_row(run_id="stale-open", started_at=100.0, ended_at=None)]
        )
        now = 100.0 + 10 * OPEN_EXECUTION_MAX_AGE_SECONDS
        assert store.prune_rdst_executions(now - 900, now=now) == 1
        assert store.rdst_executions_overlapping("t1", "h1", 0.0, now) == []

    def test_prune_rdst_executions_without_now_retains_open_rows(self, store):
        store.record_rdst_executions(
            [execution_row(run_id="stale-open", started_at=100.0, ended_at=None)]
        )
        assert store.prune_rdst_executions(100.0 + 10 * OPEN_EXECUTION_MAX_AGE_SECONDS) == 0
        rows = store.rdst_executions_overlapping("t1", "h1", 0.0, 1000.0)
        assert [row["run_id"] for row in rows] == ["stale-open"]


class TestEventRetention:
    def test_keeps_newest_events_and_recent_window_per_target(self, store):
        for n in range(4):
            store.append_event("t1", "k", str(n), created_at=100)
        for n in range(2):
            store.append_event("t1", "k", str(n), created_at=200)
        for n in range(2):
            store.append_event("t2", "k", str(n), created_at=100)

        deleted = store.prune_events(cutoff=150, keep=3)

        # t1 keeps the union of its newest 3 (seqs 4-6) and everything
        # newer than the cutoff (seqs 5-6); t2 holds fewer than keep
        # events, so its old rows all survive.
        assert deleted == 3
        assert [row["seq"] for row in store.events_since("t1", 0)] == [4, 5, 6]
        assert [row["seq"] for row in store.events_since("t2", 0)] == [7, 8]
        assert store.earliest_seq("t1") == 4

    def test_recent_window_retains_more_than_keep(self, store):
        for n in range(5):
            store.append_event("t1", "k", str(n), created_at=200)
        assert store.prune_events(cutoff=150, keep=1) == 0
        assert store.earliest_seq("t1") == 1

    def test_prune_events_deletes_in_chunks(self, store):
        for n in range(5):
            store.append_event("t1", "k", str(n), created_at=100)
        deleted = store.prune_events(cutoff=150, keep=2, chunk=1)
        assert deleted == 3
        assert store.earliest_seq("t1") == 4

    def test_prune_events_with_empty_log(self, store):
        assert store.prune_events(cutoff=150) == 0


class TestRecreate:
    def test_too_new_user_version_rebuilds(self, tmp_path):
        path = tmp_path / "cache.db"
        first = ObservationStore(path)
        first.append_event("t1", "collector", "old", created_at=100)
        first.close()

        conn = sqlite3.connect(path)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.close()

        store = ObservationStore(path)
        try:
            assert store.latest_seq("t1") == 0
            store.append_event("t1", "collector", "new", created_at=200)
            assert store.latest_seq("t1") == 1
        finally:
            store.close()
        # The old file is renamed aside, never deleted.
        assert list(tmp_path.glob("cache.db.corrupt-*"))

    def test_recreate_from_scratch_discards_data(self, tmp_path):
        path = tmp_path / "cache.db"
        store = ObservationStore(path)
        try:
            store.append_event("t1", "collector", "old", created_at=100)
            store.recreate_from_scratch()
            assert store.latest_seq("t1") == 0
            store.append_event("t1", "collector", "new", created_at=200)
            assert store.latest_seq("t1") == 1
        finally:
            store.close()
        assert list(tmp_path.glob("cache.db.corrupt-*"))


class TestMultiConnection:
    def test_second_store_sees_first_stores_writes(self, tmp_path):
        path = tmp_path / "cache.db"
        writer = ObservationStore(path)
        reader = ObservationStore(path)
        try:
            seq = writer.append_event("t1", "collector", '{"x":1}', created_at=100)
            writer.upsert_collector_state("t1", state="collecting")

            events = reader.events_since("t1", 0)
            assert [event["seq"] for event in events] == [seq]
            assert reader.get_collector_state("t1")["state"] == "collecting"

            # And the reverse direction: reader's writes reach writer.
            reader.acquire_lease("t1", "owner-b", ttl_s=30, now=1000)
            assert writer.acquire_lease("t1", "owner-a", ttl_s=30, now=1010) is None
        finally:
            reader.close()
            writer.close()


def _journal_mode(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()


class TestSqliteRuntimeCheck:
    def _fake_version(self, monkeypatch, version_info, version):
        monkeypatch.setattr(observation_store, "_unsafe_wal_warned", False)
        monkeypatch.setattr(observation_store, "_delete_fallback_warned", False)
        monkeypatch.delenv("RDST_UNSAFE_SQLITE_OK", raising=False)
        monkeypatch.setattr(sqlite3, "sqlite_version_info", version_info)
        monkeypatch.setattr(sqlite3, "sqlite_version", version)

    def test_unsafe_runtime_falls_back_to_delete_journal(
        self, tmp_path, monkeypatch, caplog
    ):
        self._fake_version(monkeypatch, (3, 40, 0), "3.40.0")
        path = tmp_path / "cache.db"
        with caplog.at_level(
            logging.WARNING, logger=observation_store.logger.name
        ):
            store = ObservationStore(path)
            try:
                # The store stays fully usable end-to-end in DELETE mode.
                store.append_event("t1", "collector", '{"a":1}', created_at=100)
                assert store.latest_seq("t1") == 1
                store.record_counter_snapshots(
                    "t1", "e1", [snapshot_row("k1")], captured_at=100.0
                )
                assert store.latest_counter_snapshots("t1", "e1")
                assert store.acquire_lease("t1", "o1", ttl_s=30, now=1000) == 1
                assert store.prune(cutoff=0) == 0
                # Rollback journal needs synchronous=FULL (2) for the same
                # power-loss guarantee WAL gets from NORMAL.
                assert (
                    store._write_conn.execute("PRAGMA synchronous").fetchone()[0]
                    == 2
                )
            finally:
                store.close()
            # A second store open in the same process stays quiet.
            second = ObservationStore(tmp_path / "cache2.db")
            second.close()
        assert _journal_mode(path) == "delete"
        assert not (tmp_path / "cache.db-wal").exists()
        warnings = [
            r.getMessage()
            for r in caplog.records
            if "rollback-journal" in r.getMessage()
        ]
        assert len(warnings) == 1
        assert "3.40.0" in warnings[0]

    def test_safe_runtime_uses_wal_with_normal_synchronous(self, tmp_path):
        path = tmp_path / "cache.db"
        store = ObservationStore(path)
        try:
            assert (
                store._write_conn.execute("PRAGMA synchronous").fetchone()[0] == 1
            )
        finally:
            store.close()
        assert _journal_mode(path) == "wal"

    def test_existing_wal_file_switches_to_delete_on_unsafe_runtime(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "cache.db"
        first = ObservationStore(path)
        first.append_event("t1", "collector", "kept", created_at=100)
        first.close()
        assert _journal_mode(path) == "wal"

        self._fake_version(monkeypatch, (3, 40, 0), "3.40.0")
        second = ObservationStore(path)
        try:
            assert second.latest_seq("t1") == 1
            second.append_event("t1", "collector", "more", created_at=200)
            assert second.latest_seq("t1") == 2
        finally:
            second.close()
        assert _journal_mode(path) == "delete"

    def test_existing_delete_file_upgrades_to_wal_on_safe_runtime(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "cache.db"
        self._fake_version(monkeypatch, (3, 40, 0), "3.40.0")
        first = ObservationStore(path)
        first.append_event("t1", "collector", "kept", created_at=100)
        first.close()
        assert _journal_mode(path) == "delete"

        monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 51, 3))
        monkeypatch.setattr(sqlite3, "sqlite_version", "3.51.3")
        second = ObservationStore(path)
        try:
            assert second.latest_seq("t1") == 1
            second.append_event("t1", "collector", "more", created_at=200)
            assert second.latest_seq("t1") == 2
        finally:
            second.close()
        assert _journal_mode(path) == "wal"

    def test_contended_journal_switch_fails_with_clear_message(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "cache.db"
        ObservationStore(path).close()

        holder = sqlite3.connect(path, isolation_level=None)
        try:
            holder.execute("BEGIN")
            holder.execute("SELECT COUNT(*) FROM collector_state").fetchone()
            self._fake_version(monkeypatch, (3, 40, 0), "3.40.0")
            monkeypatch.setattr(
                observation_store, "_JOURNAL_SWITCH_TIMEOUT_SECONDS", 0.3
            )
            with pytest.raises(RuntimeError, match="could not switch"):
                ObservationStore(path)
        finally:
            holder.close()

    def test_two_instances_share_a_delete_mode_store(self, tmp_path, monkeypatch):
        self._fake_version(monkeypatch, (3, 40, 0), "3.40.0")
        path = tmp_path / "cache.db"
        writer = ObservationStore(path)
        reader = ObservationStore(path)
        try:
            seq = writer.append_event("t1", "collector", '{"x":1}', created_at=100)
            assert [e["seq"] for e in reader.events_since("t1", 0)] == [seq]
            reader.acquire_lease("t1", "owner-b", ttl_s=30, now=1000)
            assert writer.acquire_lease("t1", "owner-a", ttl_s=30, now=1010) is None
        finally:
            reader.close()
            writer.close()
        assert _journal_mode(path) == "delete"

    def test_unsafe_override_forces_wal_with_single_warning(
        self, tmp_path, monkeypatch, caplog
    ):
        self._fake_version(monkeypatch, (3, 40, 0), "3.40.0")
        monkeypatch.setenv("RDST_UNSAFE_SQLITE_OK", "true")
        path = tmp_path / "cache.db"
        with caplog.at_level(
            logging.WARNING, logger=observation_store.logger.name
        ):
            store = ObservationStore(path)
            try:
                store.append_event("t1", "collector", '{"a":1}', created_at=100)
                assert store.latest_seq("t1") == 1
            finally:
                store.close()
            # A second store open in the same process stays quiet.
            second = ObservationStore(tmp_path / "cache2.db")
            second.close()
        assert _journal_mode(path) == "wal"
        warnings = [
            r.getMessage()
            for r in caplog.records
            if "WAL corruption risk accepted" in r.getMessage()
        ]
        assert len(warnings) == 1
        assert "3.40.0" in warnings[0]

    def test_override_does_not_bypass_version_floor(self, tmp_path, monkeypatch):
        self._fake_version(monkeypatch, (3, 34, 1), "3.34.1")
        monkeypatch.setenv("RDST_UNSAFE_SQLITE_OK", "1")
        with pytest.raises(RuntimeError, match="too old"):
            ObservationStore(tmp_path / "cache.db")

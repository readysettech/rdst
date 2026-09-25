"""monitor.db write transactions stay short, and a locked write is retried, not lost."""

from __future__ import annotations

import sqlite3
import statistics
import threading
import time
from types import SimpleNamespace

import pytest

import shared.monitor.monitor_store as monitor_store
from features.query_registry.discovery import QueryDiscoveryCollector
from shared.monitor.monitor_store import MonitorStore
from shared.query_registry.delta_processor import COMPLETE, DeltaResult

# The median write transaction. The row counts below pin the batching; this bound only catches a
# transaction that grows by an order of magnitude, with headroom for a slow shared CI runner.
HOLD_LIMIT_MS = 1000


@pytest.fixture(autouse=True)
def _rollback_journal(monkeypatch):
    """The rollback journal, where readers and writers block each other, as on this runtime."""
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 37, 2))
    monkeypatch.setattr(sqlite3, "sqlite_version", "3.37.2")


@pytest.fixture
def holds(monkeypatch):
    """Milliseconds each write transaction held the file's write lock."""
    durations: list[float] = []
    original = MonitorStore._write_once

    def timed(self, action):
        started = time.perf_counter()
        try:
            return original(self, action)
        finally:
            durations.append((time.perf_counter() - started) * 1000)

    monkeypatch.setattr(MonitorStore, "_write_once", timed)
    return durations


@pytest.fixture
def changes(monkeypatch):
    """Rows each write transaction changed."""
    counts: list[int] = []
    original = MonitorStore._write_once

    def counted(self, action):
        def run(conn):
            before = conn.total_changes
            try:
                return action(conn)
            finally:
                counts.append(conn.total_changes - before)
        return original(self, run)

    monkeypatch.setattr(MonitorStore, "_write_once", counted)
    return counts


def _seed_history(store: MonitorStore, keys: int, minutes: int, now: int) -> None:
    start = now - minutes * 60

    def write(conn):
        conn.executemany("INSERT INTO query_text VALUES ('db',?,'SELECT 1',?,?)",
                         [(f"q{i}", start, now) for i in range(keys)])
        conn.executemany(
            "INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES ('db',?,?,?,1,?,0,0,0)",
            [(f"q{i}", 60, start + minute * 60, float(i))
             for i in range(keys) for minute in range(minutes)] +
            [(f"q{i}", 300, bucket, float(i)) for i in range(keys)
             for bucket in range(start // 300 * 300, now, 300)])
        conn.executemany("INSERT INTO query_sample (target,query_key,text,observed_at) "
                         "VALUES ('db',?,?,?)",
                         [(f"q{i}", f"SELECT {n}", start + n) for i in range(keys)
                          for n in range(5)])
    store._write(write)


def test_writer_over_budget_holds_the_lock_briefly(tmp_path, changes, holds):
    now = 10 * 86400
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: now)
    store.ensure_open()
    _seed_history(store, keys=200, minutes=120, now=now)
    fine_rows = store.read("SELECT COUNT(*) n FROM stmt_series WHERE res=60")[0]["n"]
    store.budget_bytes = 1
    holds.clear()
    for minute in range(3):
        store.record("db", now + minute * 60, [{"query_key": "q1", "calls": 1, "total_ms": 1.0}],
                     {}, [], None)
    assert statistics.median(holds) < HOLD_LIMIT_MS
    # The collector's write leaves pruning to maintenance.
    assert store.read("SELECT COUNT(*) n FROM stmt_series WHERE res=60")[0]["n"] >= fine_rows
    holds.clear()
    changes.clear()
    store.maintain(pause_s=0)
    assert len(changes) > 1
    assert max(changes) <= monitor_store.MAINTENANCE_BATCH_ROWS
    assert statistics.median(holds) < HOLD_LIMIT_MS
    assert not store.read("SELECT 1 FROM stmt_series WHERE res=60 AND bucket_start<? LIMIT 1",
                          (now // 300 * 300,))


def test_budget_prunes_in_batches_across_calls(tmp_path, holds, monkeypatch):
    now = 10 * 86400
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: now)
    store.ensure_open()
    store._write(lambda conn: conn.executemany(
        "INSERT INTO query_sample (target,query_key,text,observed_at) VALUES ('db','q',?,?)",
        [(f"SELECT {n}", now - 100 + n) for n in range(35)]))
    # One byte per stored sample, so the budget keeps exactly the newest five.
    monkeypatch.setattr(MonitorStore, "_target_size", staticmethod(
        lambda conn, target: conn.execute("SELECT COUNT(*) FROM query_sample").fetchone()[0]))
    monkeypatch.setattr(monitor_store, "SAMPLE_BATCH_ROWS", 10)
    store.budget_bytes = 5
    holds.clear()
    store._enforce_budget("db", pause_s=0)
    assert len(holds) == 3
    assert [row["text"] for row in store.read(
        "SELECT text FROM query_sample ORDER BY observed_at")] == [
        f"SELECT {n}" for n in range(30, 35)]
    # Later rows are pruned by later passes, oldest first.
    store._write(lambda conn: conn.execute(
        "INSERT INTO query_sample (target,query_key,text,observed_at) VALUES ('db','q','new',?)",
        (now,)))
    monkeypatch.setattr(monitor_store, "SAMPLE_BATCH_ROWS", 2)
    store._enforce_budget("db", pause_s=0)
    assert [row["text"] for row in store.read(
        "SELECT text FROM query_sample ORDER BY observed_at")] == [
        f"SELECT {n}" for n in range(32, 35)] + ["new"]


@pytest.mark.parametrize("holder", ["writer", "reader"])
def test_locked_record_is_retried_not_lost(tmp_path, monkeypatch, holds, holder):
    # Each attempt gives up quickly; the record keeps trying past SQLite's own busy wait.
    monkeypatch.setattr(monitor_store, "WRITE_BUSY_MS", 200)
    store = MonitorStore(tmp_path / "monitor.db")
    assert store.read("PRAGMA journal_mode") == [{"journal_mode": "delete"}]
    locked = threading.Event()

    def hold():
        conn = sqlite3.connect(store.path, isolation_level=None)
        # A writer takes the write lock; an open read transaction blocks every commit.
        conn.execute("BEGIN IMMEDIATE" if holder == "writer" else "BEGIN")
        conn.execute("SELECT COUNT(*) FROM stmt_series").fetchone()
        locked.set()
        time.sleep(1.5)
        conn.execute("COMMIT")
        conn.close()

    thread = threading.Thread(target=hold)
    thread.start()
    locked.wait()
    holds.clear()
    store.record("db", 60, [{"query_key": "q", "calls": 3, "total_ms": 3.0}], {}, [], None)
    thread.join()
    assert len(holds) > 1
    assert store.read("SELECT calls FROM stmt_series WHERE res=60") == [{"calls": 3}]


def test_collector_requeues_a_minute_that_meets_a_locked_file(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    collector = QueryDiscoveryCollector("db", two_phase=True)
    collector._monitor = store
    collector._alias_map = {"engine": "key"}
    registry = SimpleNamespace(get_query=lambda key: None)
    record = store.record
    failures = [sqlite3.OperationalError("database is locked")]

    def locked_once(*args, **kwargs):
        if failures:
            raise failures.pop()
        return record(*args, **kwargs)

    store.record = locked_once
    first = DeltaResult("engine", 60, 120, COMPLETE, 2, 20.0, 2 / 60, 2)
    with pytest.raises(sqlite3.OperationalError):
        collector._record_monitor_cycle(120, [first], registry, [])
    second = DeltaResult("engine", 120, 180, COMPLETE, 5, 50.0, 5 / 60, 5)
    collector._record_monitor_cycle(180, [second], registry, [])
    assert store.read("SELECT bucket_start,calls FROM stmt_series WHERE res=60 "
                      "ORDER BY bucket_start") == [{"bucket_start": 120, "calls": 2},
                                                   {"bucket_start": 180, "calls": 5}]
    assert not collector._monitor_pending


def test_reads_keep_row_order_and_the_first_repeated_column(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.ensure_open()
    store._write(lambda conn: conn.executemany(
        "INSERT INTO query_text VALUES ('db',?,?,0,0)",
        [(f"q{i}", f"SELECT {i}") for i in range(50)]))
    rows = store.read("SELECT a.query_key,b.text AS query_key FROM query_text a "
                      "JOIN query_text b ON b.query_key=a.query_key ORDER BY a.query_key DESC")
    assert rows[0] == {"query_key": "q9"} and len(rows) == 50
    assert store.read("SELECT COUNT(*),MAX(first_seen) FROM query_text WHERE target=?",
                      ("db",)) == [{"COUNT(*)": 50, "MAX(first_seen)": 0}]

"""Automatic Query Library discovery stays web-owned and lifecycle-correct."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from features.query_registry import discovery
from features.query_registry.discovery import (
    RESYNC_GAP_THRESHOLD,
    RETENTION_EVENT_KEEP,
    RETENTION_MIN_INTERVAL_SECONDS,
    RETENTION_WINDOW_SECONDS,
    QueryDiscoveryCollector,
    QueryDiscoveryCoordinator,
    _capability_error_code,
    two_phase_discovery_enabled,
)
from features.top.events import TopConnectedEvent, TopErrorEvent, TopQueriesEvent
from features.top.models import TopQueryData
from shared.query_registry import QueryRegistry, hash_sql
from shared.query_registry.observation_store import (
    OPEN_EXECUTION_MAX_AGE_SECONDS,
    LeaseLostError,
    ObservationStore,
)


@pytest.fixture(autouse=True)
def _legacy_flag_default(monkeypatch):
    """Pin the rollback mode so the legacy-path tests exercise it explicitly;
    two-phase tests opt back in per collector."""
    monkeypatch.setenv("RDST_TWO_PHASE_DISCOVERY", "0")


def top_query(sql: str = "SELECT * FROM users") -> TopQueryData:
    return TopQueryData(
        query_hash="upstream-hash",
        query_text=sql,
        normalized_query=sql,
        freq=42,
        total_time="1.250s",
        avg_time="0.025s",
        pct_load="12.0%",
    )


class FakeTopService:
    def __init__(self, queries: list[TopQueryData]):
        self.queries = queries
        self.calls = 0
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def get_top_queries(self, input_data, options):
        self.calls += 1
        assert input_data.target == "demo"
        assert input_data.source == "auto"
        assert options.auto_save_registry is False
        yield TopConnectedEvent(
            type="connected",
            target_name="demo",
            db_engine="postgresql",
            source="pg_stat",
        )
        yield TopQueriesEvent(
            type="queries",
            queries=self.queries,
            source="pg_stat",
            target_name="demo",
            db_engine="postgresql",
        )


@pytest.mark.asyncio
async def test_discovery_observes_without_creating_saved_intent(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    service = FakeTopService([top_query()])
    collector = QueryDiscoveryCollector(
        "demo",
        service_factory=lambda: service,
        registry_factory=lambda: registry,
        clock=lambda: "2026-08-10T10:00:00Z",
    )

    first = await collector.collect_now()
    second = await collector.collect_now()

    registry.load()
    entry = registry.list_queries()[0]
    lifecycle = entry.lifecycle_for("demo")
    assert lifecycle is not None
    assert lifecycle.first_observed_at
    assert lifecycle.saved_at == ""
    assert lifecycle.sources == ["top-historical"]
    assert entry.frequency == 42
    assert entry.avg_duration_ms == 25.0
    assert first.event == "discovery_update"
    assert first.data["new_hashes"] == [entry.hash]
    assert second.data["new_hashes"] == []
    assert service.calls == 2


@pytest.mark.asyncio
async def test_snapshot_persists_once_and_reports_delta_state(tmp_path, monkeypatch):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    service = FakeTopService([top_query(), top_query("SELECT * FROM orders")])
    collector = QueryDiscoveryCollector(
        "demo",
        service_factory=lambda: service,
        registry_factory=lambda: registry,
        clock=lambda: "2026-08-10T10:00:00Z",
    )

    saves: list[int] = []
    original_save = QueryRegistry.save

    def counting_save(instance):
        saves.append(1)
        original_save(instance)

    monkeypatch.setattr(QueryRegistry, "save", counting_save)

    first = await collector.collect_now()
    assert saves == [1]
    second = await collector.collect_now()
    assert saves == [1, 1]

    assert first.data["changed"] is True
    assert second.data["changed"] is False
    stats = first.data["stats"]
    assert stats["rows_returned"] == 2
    assert stats["new_identity_count"] == 2
    for field in ("collection_duration_ms", "db_fetch_duration_ms", "persistence_ms"):
        assert stats[field] >= 0
    assert second.data["stats"]["new_identity_count"] == 0


class ErrorTopService:
    def __init__(self):
        self.closed = False

    async def get_top_queries(self, input_data, options):
        yield TopErrorEvent(type="error", message="db down", stage="execution")

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_consecutive_cycles_reuse_one_service(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    constructed: list[FakeTopService] = []

    def factory():
        service = FakeTopService([top_query()])
        constructed.append(service)
        return service

    collector = QueryDiscoveryCollector(
        "demo",
        service_factory=factory,
        registry_factory=lambda: registry,
        clock=lambda: "2026-08-10T10:00:00Z",
    )

    first = await collector.collect_now()
    second = await collector.collect_now()

    assert len(constructed) == 1
    assert constructed[0].calls == 2
    assert first.data["stats"]["service_reused"] is False
    assert second.data["stats"]["service_reused"] is True


@pytest.mark.asyncio
async def test_error_cycle_disposes_service_and_next_cycle_rebuilds(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    failing = ErrorTopService()
    services = [failing, FakeTopService([top_query()])]
    constructed: list[object] = []

    def factory():
        service = services[len(constructed)]
        constructed.append(service)
        return service

    collector = QueryDiscoveryCollector(
        "demo",
        service_factory=factory,
        registry_factory=lambda: registry,
        clock=lambda: "2026-08-10T10:00:00Z",
    )

    first = await collector.collect_now()
    assert first.event == "discovery_error"
    assert failing.closed is True

    second = await collector.collect_now()
    assert second.event == "discovery_update"
    assert len(constructed) == 2
    assert second.data["stats"]["service_reused"] is False


@pytest.mark.asyncio
async def test_collector_close_releases_reused_service(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    service = FakeTopService([top_query()])
    collector = QueryDiscoveryCollector(
        "demo",
        service_factory=lambda: service,
        registry_factory=lambda: registry,
        clock=lambda: "2026-08-10T10:00:00Z",
    )

    await collector.collect_now()
    await collector.close()

    assert service.closed is True


@pytest.mark.asyncio
async def test_reconnect_replays_retained_events_or_resyncs(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    service = FakeTopService([top_query()])
    collector = QueryDiscoveryCollector(
        "demo",
        service_factory=lambda: service,
        registry_factory=lambda: registry,
        clock=lambda: "2026-08-10T10:00:00Z",
        history_size=2,
    )

    first = await collector.collect_now()
    second = await collector.collect_now()

    assert collector.replay_after(first.cursor) == [second]
    assert collector.replay_after(-100)[0].event == "resync"
    assert collector.replay_after(second.cursor) == []


def test_coordinator_reuses_one_collector_per_target():
    created: list[str] = []

    def factory(target: str):
        created.append(target)
        return QueryDiscoveryCollector(target)

    coordinator = QueryDiscoveryCoordinator(collector_factory=factory)

    first = coordinator.collector_for("demo")
    assert coordinator.collector_for("demo") is first
    assert coordinator.collector_for("analytics") is not first
    assert created == ["demo", "analytics"]


@pytest.mark.asyncio
async def test_subscription_deduplicates_event_published_during_replay(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    service = FakeTopService([top_query()])
    collector = QueryDiscoveryCollector(
        "demo",
        interval_seconds=3600,
        service_factory=lambda: service,
        registry_factory=lambda: registry,
    )

    subscription = collector.subscribe()
    initial = await anext(subscription)
    update = await asyncio.wait_for(anext(subscription), timeout=1)

    assert initial.event == "discovery_snapshot"
    assert update.event == "discovery_update"
    assert update.cursor > initial.cursor

    await subscription.aclose()
    assert collector.task is None


def store_collector(tmp_path, store, service=None, **kwargs):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    return QueryDiscoveryCollector(
        "demo",
        service_factory=lambda: service or FakeTopService([top_query()]),
        registry_factory=lambda: registry,
        clock=lambda: "2026-08-10T10:00:00Z",
        unix_clock=lambda: 1234,
        store=store,
        **kwargs,
    )


class BrokenStore:
    """Store double whose every method fails; collection must not notice."""

    def _fail(self, *args, **kwargs):
        raise RuntimeError("store down")

    latest_seq = earliest_seq = events_since = _fail
    append_event = upsert_collector_state = record_counter_snapshots = _fail
    get_collector_state = get_identity_aliases = record_identity_aliases = _fail
    record_recent_observations = latest_counter_snapshots = _fail
    prune = prune_events = prune_rdst_executions = _fail


@pytest.mark.asyncio
async def test_collection_cycle_persists_state_events_and_counters(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        collector = store_collector(tmp_path, store)
        event = await collector.collect_now()

        state = store.get_collector_state("demo")
        assert state["state"] == "watching"
        assert state["last_attempt_at"] == 1234
        assert state["last_success_at"] == 1234
        assert state["duration_ms"] >= 0
        assert state["error_code"] is None
        assert state["epoch_id"] == ""

        rows = store.events_since("demo", 0)
        assert [row["kind"] for row in rows] == ["discovery_update"]
        assert rows[0]["seq"] == event.cursor
        assert json.loads(rows[0]["payload"]) == event.data

        conn = sqlite3.connect(store.path)
        try:
            snapshots = conn.execute(
                "SELECT engine_key, calls, total_exec_time, mean_exec_time, "
                "captured_at FROM counter_snapshot WHERE target_id = 'demo'"
            ).fetchall()
        finally:
            conn.close()
        assert snapshots == [("upstream-hash", 42, 1250.0, 25.0, 1234)]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_error_cycle_persists_unavailable_collector_state(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        collector = store_collector(tmp_path, store, service=ErrorTopService())
        event = await collector.collect_now()

        assert event.event == "discovery_error"
        state = store.get_collector_state("demo")
        assert state["state"] == "unavailable"
        assert state["last_attempt_at"] == 1234
        assert state["last_success_at"] is None
        assert state["error_code"] == "collection_failed"
        assert [row["kind"] for row in store.events_since("demo", 0)] == [
            "discovery_error"
        ]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_store_failure_never_fails_a_collection_cycle(tmp_path):
    collector = store_collector(tmp_path, BrokenStore())

    event = await collector.collect_now()

    assert event.event == "discovery_update"
    assert event.cursor == 1
    assert event.data["query_count"] == 1
    # Replay degrades to the in-memory deque when the store cannot be read.
    assert collector.replay_after(0) == [event]


class RetentionSpyStore:
    """Delegates to a real store while recording retention calls."""

    def __init__(self, inner):
        self._inner = inner
        self.retention_calls: list[tuple] = []

    def prune(self, cutoff, chunk=10000):
        self.retention_calls.append(("prune", cutoff))
        return self._inner.prune(cutoff, chunk)

    def prune_events(self, cutoff, keep=1000, chunk=10000):
        self.retention_calls.append(("prune_events", cutoff, keep))
        return self._inner.prune_events(cutoff, keep, chunk)

    def prune_rdst_executions(self, cutoff, chunk=10000, *, now=None):
        self.retention_calls.append(("prune_rdst_executions", cutoff, now))
        return self._inner.prune_rdst_executions(cutoff, chunk, now=now)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class RetentionFailureStore(RetentionSpyStore):
    """Retention always fails; every other store call passes through."""

    def prune(self, cutoff, chunk=10000):
        raise RuntimeError("retention down")

    def prune_events(self, cutoff, keep=1000, chunk=10000):
        raise RuntimeError("retention down")

    def prune_rdst_executions(self, cutoff, chunk=10000, *, now=None):
        raise RuntimeError("retention down")


@pytest.mark.asyncio
async def test_retention_runs_only_on_the_gated_cadence(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        spy = RetentionSpyStore(store)
        clock = {"now": 10_000}
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        collector = QueryDiscoveryCollector(
            "demo",
            service_factory=lambda: FakeTopService([top_query()]),
            registry_factory=lambda: registry,
            clock=lambda: "2026-08-10T10:00:00Z",
            unix_clock=lambda: clock["now"],
            store=spy,
        )

        await collector.collect_now()
        assert spy.retention_calls == [
            ("prune", 10_000 - RETENTION_WINDOW_SECONDS),
            ("prune_events", 10_000 - RETENTION_WINDOW_SECONDS, RETENTION_EVENT_KEEP),
            ("prune_rdst_executions", 10_000 - RETENTION_WINDOW_SECONDS, 10_000),
        ]

        # Inside the gate a successful cycle skips retention entirely.
        clock["now"] += RETENTION_MIN_INTERVAL_SECONDS - 1
        await collector.collect_now()
        assert len(spy.retention_calls) == 3

        # Once the gate elapses the next successful cycle compacts again.
        clock["now"] += 1
        await collector.collect_now()
        assert len(spy.retention_calls) == 6
    finally:
        store.close()


@pytest.mark.asyncio
async def test_retention_failure_never_fails_a_collection_cycle(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        collector = store_collector(tmp_path, RetentionFailureStore(store))
        event = await collector.collect_now()

        assert event.event == "discovery_update"
        assert store.get_collector_state("demo")["state"] == "watching"
        assert [row["kind"] for row in store.events_since("demo", 0)] == [
            "discovery_update"
        ]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_replay_after_retention_prune_resyncs(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        collector = store_collector(tmp_path, store)
        events = [await collector.collect_now() for _ in range(4)]
        assert [event.cursor for event in events] == [1, 2, 3, 4]

        # Compact the two oldest events away, as scheduler-tick retention
        # does once the cutoff passes their created_at.
        assert store.prune_events(cutoff=2000, keep=2) == 2
        assert store.earliest_seq("demo") == 3

        # A restarted subscriber whose cursor fell into the pruned range is
        # resynced in band rather than silently skipped ahead.
        restarted = store_collector(tmp_path, store)
        replayed = restarted.replay_after(1)
        assert [event.event for event in replayed] == ["resync"]
        assert replayed[0].data["reason"] == "cursor_not_retained"

        # A cursor at the pruned/retained boundary still replays normally.
        boundary = restarted.replay_after(2)
        assert [event.cursor for event in boundary] == [3, 4]
        assert {event.event for event in boundary} == {"discovery_update"}
    finally:
        store.close()


@pytest.mark.asyncio
async def test_reconnect_after_restart_replays_from_store(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        first_process = store_collector(tmp_path, store)
        first = await first_process.collect_now()
        second = await first_process.collect_now()

        # A fresh collector has no in-memory history; the store serves replay.
        restarted = store_collector(tmp_path, store)
        replayed = restarted.replay_after(first.cursor)
        assert [event.cursor for event in replayed] == [second.cursor]
        assert replayed[0].event == "discovery_update"
        assert replayed[0].data == second.data
        assert restarted.replay_after(second.cursor) == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_unretained_cursor_yields_in_band_resync(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        # Advance the global seq so demo's earliest retained seq is above 1,
        # standing in for pruned history.
        for n in range(3):
            store.append_event("other", "discovery_update", {"n": n}, created_at=100)
        collector = store_collector(tmp_path, store)
        latest = await collector.collect_now()

        for stale_cursor in (1, latest.cursor + 100):
            events = collector.replay_after(stale_cursor)
            assert [event.event for event in events] == ["resync"]
            assert events[0].cursor == latest.cursor
            assert events[0].data["reason"] == "cursor_not_retained"
            assert events[0].data["latest_seq"] == latest.cursor
            assert events[0].data["state"]["target"] == "demo"
    finally:
        store.close()


def bulk_events(store, count, target="demo"):
    """Insert events directly, in one transaction, so paging tests stay fast."""
    conn = sqlite3.connect(store.path)
    try:
        conn.executemany(
            "INSERT INTO observation_event (target_id, kind, payload, created_at)"
            " VALUES (?,?,?,?)",
            [
                (target, "discovery_update", json.dumps({"n": n}), 100 + n)
                for n in range(count)
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_replay_pages_through_backlog_larger_than_one_store_page(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        bulk_events(store, 2500)
        collector = store_collector(tmp_path, store)
        # The store itself still serves at most 1000 events per call.
        assert len(store.events_since("demo", 0)) == 1000

        replay = collector.replay_after(0)

        assert [event.cursor for event in replay] == list(range(1, 2501))
        assert all(event.event == "discovery_update" for event in replay)
        assert replay[0].data == {"n": 0}
        assert replay[-1].data == {"n": 2499}
    finally:
        store.close()


def test_replay_gap_beyond_threshold_yields_single_resync(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        total = RESYNC_GAP_THRESHOLD + 100
        bulk_events(store, total)
        collector = store_collector(tmp_path, store)

        events = collector.replay_after(50)

        assert [event.event for event in events] == ["resync"]
        assert events[0].data["reason"] == "cursor_not_retained"
        assert events[0].data["latest_seq"] == total
    finally:
        store.close()


@pytest.mark.asyncio
async def test_subscriber_overflow_coalesces_backlog_into_resync(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    collector = QueryDiscoveryCollector(
        "demo",
        interval_seconds=3600,
        service_factory=lambda: FakeTopService([top_query()]),
        registry_factory=lambda: registry,
        queue_maxsize=2,
    )

    subscription = collector.subscribe()
    assert (await anext(subscription)).event == "discovery_snapshot"
    assert (
        await asyncio.wait_for(anext(subscription), timeout=1)
    ).event == "discovery_update"

    await collector.collect_now()
    await collector.collect_now()
    overflow = await collector.collect_now()

    event = await asyncio.wait_for(anext(subscription), timeout=1)
    assert event.event == "resync"
    assert event.data["reason"] == "subscriber_overflow"
    assert event.cursor == overflow.cursor

    # The stream keeps flowing normally after the coalescing resync.
    fourth = await collector.collect_now()
    assert await asyncio.wait_for(anext(subscription), timeout=1) == fourth

    await subscription.aclose()


@pytest.mark.asyncio
async def test_idle_subscriber_gets_bookmark_that_is_never_logged(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(discovery, "BOOKMARK_INTERVAL_SECONDS", 0.05)
    store = ObservationStore(tmp_path / "cache.db")
    try:
        collector = store_collector(tmp_path, store, interval_seconds=3600)

        subscription = collector.subscribe()
        assert (await anext(subscription)).event == "discovery_snapshot"
        update = await asyncio.wait_for(anext(subscription), timeout=1)
        assert update.event == "discovery_update"

        bookmark = await asyncio.wait_for(anext(subscription), timeout=1)
        assert bookmark.event == "bookmark"
        assert bookmark.cursor == update.cursor
        assert bookmark.to_sse() == {
            "id": str(update.cursor),
            "event": "bookmark",
            "data": json.dumps({"cursor": update.cursor}),
        }

        # Bookmarks are per-subscriber timing, never durable log entries.
        assert store.latest_seq("demo") == update.cursor
        assert all(
            row["kind"] != "bookmark" for row in store.events_since("demo", 0)
        )

        await subscription.aclose()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_real_event_resets_the_bookmark_idle_timer(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "BOOKMARK_INTERVAL_SECONDS", 1.0)
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    collector = QueryDiscoveryCollector(
        "demo",
        interval_seconds=3600,
        service_factory=lambda: FakeTopService([top_query()]),
        registry_factory=lambda: registry,
    )

    subscription = collector.subscribe()
    assert (await anext(subscription)).event == "discovery_snapshot"
    assert (
        await asyncio.wait_for(anext(subscription), timeout=1)
    ).event == "discovery_update"

    # A real event mid-interval is delivered as itself, not a bookmark.
    pending = asyncio.ensure_future(anext(subscription))
    await asyncio.sleep(0.4)
    published = collector._publish("discovery_update", dict(collector._snapshot))
    assert await asyncio.wait_for(pending, timeout=1) == published

    # The idle clock restarts on delivery: nothing arrives at the original
    # deadline (0.6 out), only a full interval after the real event.
    pending = asyncio.ensure_future(anext(subscription))
    done, _ = await asyncio.wait({pending}, timeout=0.7)
    assert not done
    bookmark = await asyncio.wait_for(pending, timeout=2)
    assert bookmark.event == "bookmark"
    assert bookmark.cursor == published.cursor

    await subscription.aclose()


def test_cli_style_import_creates_no_store_and_no_task(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    code = (
        "import asyncio\n"
        "from features.query_registry import discovery\n"
        "assert discovery.query_discovery._store is None\n"
        "assert discovery.query_discovery._collectors == {}\n"
        "try:\n"
        "    asyncio.get_running_loop()\n"
        "except RuntimeError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('unexpected event loop at import')\n"
    )
    env = {**os.environ, "HOME": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / ".rdst" / "cache.db").exists()


def forbidden_service():
    raise AssertionError("legacy TopService path must stay unused")


def forbidden_connection(target):
    raise AssertionError("two-phase connection must not be opened")


class FakeCursor:
    def __init__(self, connection):
        self._connection = connection
        self._rows = []
        self.description = None

    def execute(self, sql, params=None):
        self._connection.executed.append((sql, params))
        self._rows = self._connection.rows_for(sql)
        self.description = ("fake",)

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConnection:
    """Substring-routed canned rows; rules can be swapped between cycles."""

    def __init__(self, rules):
        self.rules = dict(rules)
        self.executed = []
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def rows_for(self, sql):
        for fragment, rows in self.rules.items():
            if fragment in sql:
                if isinstance(rows, Exception):
                    raise rows
                return rows
        return []

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


PG_VERSION = 150002


def pg_stat_row(queryid, calls, total, mean=20.0, minimum=1.0, maximum=40.0, rows=50):
    return (10, 16384, queryid, True, calls, rows, total, mean, minimum, maximum, 2.5)


def pg_text_row(queryid, text):
    return (10, 16384, queryid, True, text)


def pg_rules(now, stat_rows, text_rows=(), stats_reset=555.0):
    return {
        "server_version_num": [(PG_VERSION,)],
        "pg_stat_statements_info": [(now, 1111.0, stats_reset, 0)],
        "pg_stat_statements(false)": list(stat_rows),
        "queryid = ANY": list(text_rows),
        "pg_settings": [
            ("pg_stat_statements.max", "5000"),
            ("pg_stat_statements.track", "top"),
        ],
    }


def two_phase_collector(tmp_path, connection_factory, *, store=None, limit=100,
                        registry=None, local_clock=time.time):
    registry = registry or QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    return QueryDiscoveryCollector(
        "demo",
        limit=limit,
        service_factory=forbidden_service,
        registry_factory=lambda: registry,
        clock=lambda: "2026-08-10T10:00:00Z",
        unix_clock=lambda: 1234,
        local_clock=local_clock,
        store=store,
        two_phase=True,
        connection_factory=connection_factory,
    )


def recent_observation_rows(store):
    conn = sqlite3.connect(store.path)
    try:
        rows = conn.execute(
            "SELECT normalized_hash, window_start, window_end, calls_delta, "
            "exec_time_delta, approximate_qps, completeness, freshness, attribution "
            "FROM recent_observation WHERE target_id = 'demo'"
        ).fetchall()
    finally:
        conn.close()
    return {row[0]: row[1:] for row in rows}


def test_two_phase_flag_semantics(monkeypatch):
    monkeypatch.delenv("RDST_TWO_PHASE_DISCOVERY", raising=False)
    assert two_phase_discovery_enabled() is True
    for value in ("0", "false", "FALSE", " 0 "):
        monkeypatch.setenv("RDST_TWO_PHASE_DISCOVERY", value)
        assert two_phase_discovery_enabled() is False
    for value in ("1", "true", "anything-else"):
        monkeypatch.setenv("RDST_TWO_PHASE_DISCOVERY", value)
        assert two_phase_discovery_enabled() is True


@pytest.mark.asyncio
async def test_flag_off_forces_legacy_top_service_path(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    service = FakeTopService([top_query()])
    collector = QueryDiscoveryCollector(
        "demo",
        service_factory=lambda: service,
        registry_factory=lambda: registry,
        clock=lambda: "2026-08-10T10:00:00Z",
        connection_factory=forbidden_connection,
    )

    event = await collector.collect_now()

    assert event.event == "discovery_update"
    assert service.calls == 1


@pytest.mark.asyncio
async def test_flag_default_enables_two_phase_path(tmp_path, monkeypatch):
    monkeypatch.delenv("RDST_TWO_PHASE_DISCOVERY", raising=False)
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    connection = FakeConnection(
        pg_rules(
            2000.0,
            [pg_stat_row(111, calls=5, total=100.0)],
            text_rows=[pg_text_row(111, "SELECT * FROM users")],
        )
    )
    collector = QueryDiscoveryCollector(
        "demo",
        service_factory=forbidden_service,
        registry_factory=lambda: registry,
        connection_factory=lambda target: (connection, "postgresql"),
    )

    event = await collector.collect_now()

    assert event.event == "discovery_update"
    assert event.data["source"] == "pg_stat"
    assert event.data["engine"] == "postgresql"


@pytest.mark.asyncio
async def test_two_phase_pg_two_cycles_baseline_then_deltas(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        connection = FakeConnection(
            pg_rules(
                2000.0,
                [
                    pg_stat_row(111, calls=5, total=100.0),
                    pg_stat_row(222, calls=2, total=10.0, mean=5.0),
                ],
                text_rows=[
                    pg_text_row(111, "SELECT * FROM users"),
                    pg_text_row(222, "SELECT * FROM orders"),
                ],
            )
        )
        collector = two_phase_collector(
            tmp_path,
            lambda target: (connection, "postgresql"),
            store=store,
            limit=1,
            registry=registry,
        )

        first = await collector.collect_now()
        assert first.event == "discovery_update"
        assert first.data["changed"] is True
        assert first.data["query_count"] == 2
        assert first.data["stats"]["incremental"] is False
        epoch = first.data["stats"]["epoch_id"]
        assert epoch

        # Both identities were resolved, but the Library gained only the
        # top-K (limit=1) entry, ranked by cumulative time on the baseline.
        registry.load()
        entries = registry.list_queries()
        assert len(entries) == 1
        assert entries[0].original_sql == "SELECT * FROM users"
        assert first.data["new_hashes"] == [entries[0].hash]
        aliases = store.get_identity_aliases("demo", epoch)
        assert set(aliases) == {"10:16384:111:t", "10:16384:222:t"}
        assert aliases["10:16384:111:t"] == entries[0].hash
        # A baseline cycle writes no window results.
        assert recent_observation_rows(store) == {}

        connection.rules = pg_rules(
            2060.0,
            [
                pg_stat_row(111, calls=15, total=400.0),
                pg_stat_row(222, calls=2, total=10.0, mean=5.0),
            ],
            text_rows=[pg_text_row(222, "SELECT * FROM orders")],
        )
        second = await collector.collect_now()
        # The known entry's evidence was refreshed from the changed counters,
        # and the identity that lost cycle 1's single slot is late-admitted:
        # its text comes from a bounded extra Phase B fetch.
        assert second.data["changed"] is True
        assert second.data["new_hashes"] == [aliases["10:16384:222:t"]]
        assert second.data["stats"]["deltas_computed"] == 2
        assert second.data["stats"]["refreshed_identity_count"] == 1
        assert second.data["stats"]["service_reused"] is True
        registry.load()
        refreshed = registry.get_query(entries[0].hash)
        assert refreshed.frequency == 15
        assert refreshed.avg_duration_ms == 20.0
        admitted = registry.get_query(aliases["10:16384:222:t"])
        assert admitted.original_sql == "SELECT * FROM orders"
        assert admitted.frequency == 2
        # Phase B ran once for the fresh keys and once for late admission.
        assert (
            len([sql for sql, _ in connection.executed if "queryid = ANY" in sql]) == 2
        )

        observed = recent_observation_rows(store)
        users_row = observed[aliases["10:16384:111:t"]]
        assert users_row[:3] == (2000, 2060, 10)
        assert users_row[3] == pytest.approx(300.0)
        assert users_row[4] == pytest.approx(10 / 60)
        assert users_row[5:] == ("complete", "interval", "production_only")
        orders_row = observed[aliases["10:16384:222:t"]]
        assert orders_row[:5] == (2000, 2060, 0, 0.0, 0.0)
        assert orders_row[5] == "complete"

        conn = sqlite3.connect(store.path)
        try:
            epochs = conn.execute(
                "SELECT DISTINCT epoch_id FROM counter_snapshot"
            ).fetchall()
            captures = conn.execute(
                "SELECT DISTINCT captured_at FROM counter_snapshot ORDER BY captured_at"
            ).fetchall()
        finally:
            conn.close()
        assert epochs == [(epoch,)]
        assert captures == [(2000,), (2060,)]
        state = store.get_collector_state("demo")
        assert state["epoch_id"] == epoch
        assert state["source_capabilities"]["pgss_max"] == 5000
    finally:
        store.close()


async def run_rdst_attribution_cycles(tmp_path, store, *, executions=(),
                                      break_lookup=False,
                                      server_times=(2000.0, 2060.0),
                                      local_times=None):
    """Baseline cycle, seeded self-executions, then one delta cycle.

    The delta cycle observes calls 5 -> 15 over the server-clock window
    (server_times[0], server_times[1]] (calls_delta 10, exec_time_delta
    300.0). local_times supplies the local clock read at each fetch and
    defaults to server_times, i.e. zero clock offset; executions are
    seeded in local time. Returns the second cycle's event and the
    identity's stored recent_observation row as (window_start, window_end,
    calls_delta, exec_time_delta, approximate_qps, completeness,
    freshness, attribution).
    """
    local = iter(local_times if local_times is not None else server_times)
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    connection = FakeConnection(
        pg_rules(
            server_times[0],
            [pg_stat_row(111, calls=5, total=100.0)],
            text_rows=[pg_text_row(111, "SELECT * FROM users")],
        )
    )
    collector = two_phase_collector(
        tmp_path,
        lambda target: (connection, "postgresql"),
        store=store,
        registry=registry,
        local_clock=lambda: next(local),
    )
    first = await collector.collect_now()
    aliases = store.get_identity_aliases("demo", first.data["stats"]["epoch_id"])
    normalized = aliases["10:16384:111:t"]
    store.record_rdst_executions(
        [
            {
                "target_id": "demo",
                "normalized_hash": normalized,
                "lane": "compare",
                "run_id": f"run-{index}",
                **execution,
            }
            for index, execution in enumerate(executions)
        ]
    )
    if break_lookup:
        def boom(*args, **kwargs):
            raise sqlite3.OperationalError("lookup failed")

        store.rdst_executions_overlapping_many = boom
    connection.rules = pg_rules(
        server_times[1], [pg_stat_row(111, calls=15, total=400.0)]
    )
    second = await collector.collect_now()
    return second, recent_observation_rows(store)[normalized]


@pytest.mark.asyncio
async def test_rdst_overlap_with_exact_count_subtracts_and_marks(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        event, row = await run_rdst_attribution_cycles(
            tmp_path,
            store,
            executions=[{"started_at": 2010, "ended_at": 2030, "exec_count": 4}],
        )
        assert event.event == "discovery_update"
        assert row[:3] == (2000, 2060, 6)
        # Execution time cannot be attributed exactly, so it stays as measured
        # while qps tracks the production-adjusted calls.
        assert row[3] == pytest.approx(300.0)
        assert row[4] == pytest.approx(6 / 60)
        assert row[5:] == ("complete", "interval", "contains_rdst_traffic")
    finally:
        store.close()


@pytest.mark.asyncio
async def test_rdst_overlap_with_unknown_count_marks_partial_without_subtracting(
    tmp_path,
):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        event, row = await run_rdst_attribution_cycles(
            tmp_path,
            store,
            executions=[{"started_at": 2010, "ended_at": 2030, "exec_count": None}],
        )
        assert event.event == "discovery_update"
        assert row[:3] == (2000, 2060, 10)
        assert row[3] == pytest.approx(300.0)
        assert row[4] == pytest.approx(10 / 60)
        assert row[5:] == ("partial", "interval", "contains_rdst_traffic")
    finally:
        store.close()


@pytest.mark.asyncio
async def test_rdst_mixed_known_and_unknown_counts_never_subtract(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        _, row = await run_rdst_attribution_cycles(
            tmp_path,
            store,
            executions=[
                {"started_at": 2010, "ended_at": 2020, "exec_count": 4},
                {"started_at": 2030, "ended_at": 2040, "exec_count": None},
            ],
        )
        assert row[2] == 10
        assert row[5:] == ("partial", "interval", "contains_rdst_traffic")
    finally:
        store.close()


@pytest.mark.asyncio
async def test_rdst_execution_outside_window_leaves_production_only(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        _, row = await run_rdst_attribution_cycles(
            tmp_path,
            store,
            executions=[{"started_at": 2100, "ended_at": 2200, "exec_count": 4}],
        )
        assert row[:3] == (2000, 2060, 10)
        assert row[4] == pytest.approx(10 / 60)
        assert row[5:] == ("complete", "interval", "production_only")
    finally:
        store.close()


@pytest.mark.asyncio
async def test_rdst_subtraction_floors_at_zero(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        _, row = await run_rdst_attribution_cycles(
            tmp_path,
            store,
            executions=[{"started_at": 2010, "ended_at": 2030, "exec_count": 25}],
        )
        assert row[2] == 0
        assert row[4] == pytest.approx(0.0)
        assert row[5:] == ("complete", "interval", "contains_rdst_traffic")
    finally:
        store.close()


@pytest.mark.asyncio
async def test_rdst_lookup_failure_leaves_row_unattributed_and_cycle_succeeds(
    tmp_path,
):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        event, row = await run_rdst_attribution_cycles(
            tmp_path,
            store,
            executions=[{"started_at": 2010, "ended_at": 2030, "exec_count": 4}],
            break_lookup=True,
        )
        assert event.event == "discovery_update"
        assert event.data["error"] is None
        assert row[:3] == (2000, 2060, 10)
        assert row[5:] == ("complete", "interval", "production_only")
    finally:
        store.close()


@pytest.mark.asyncio
async def test_server_clock_skew_is_removed_before_attribution(tmp_path):
    """Server clock 30s ahead: windows are stored in server time while the
    execution was recorded in local time inside the true window. Raw bounds
    miss the overlap; the per-cycle offset recovers it."""
    store = ObservationStore(tmp_path / "cache.db")
    try:
        event, row = await run_rdst_attribution_cycles(
            tmp_path,
            store,
            executions=[{"started_at": 2010, "ended_at": 2030, "exec_count": 4}],
            server_times=(2030.0, 2090.0),
            local_times=(2000.0, 2060.0),
        )
        assert event.event == "discovery_update"
        # Comparing the server-time window bounds raw against the local-time
        # execution finds nothing; only the offset-shifted query attributes.
        normalized = hash_sql("SELECT * FROM users")
        assert (
            store.rdst_executions_overlapping("demo", normalized, 2030, 2090) == []
        )
        assert row[:3] == (2030, 2090, 6)
        assert row[3] == pytest.approx(300.0)
        assert row[4] == pytest.approx(6 / 60)
        assert row[5:] == ("complete", "interval", "contains_rdst_traffic")
    finally:
        store.close()


@pytest.mark.asyncio
async def test_stale_open_execution_no_longer_marks_later_windows(tmp_path):
    """A crashed lane's never-closed row ages out of attribution once the
    window starts more than the open-execution bound after it."""
    store = ObservationStore(tmp_path / "cache.db")
    try:
        _, row = await run_rdst_attribution_cycles(
            tmp_path,
            store,
            executions=[
                {
                    "started_at": 2000 - OPEN_EXECUTION_MAX_AGE_SECONDS - 10,
                    "ended_at": None,
                    "exec_count": None,
                }
            ],
        )
        assert row[:3] == (2000, 2060, 10)
        assert row[5:] == ("complete", "interval", "production_only")
    finally:
        store.close()


@pytest.mark.asyncio
async def test_fresh_open_execution_still_marks_the_window_partial(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        _, row = await run_rdst_attribution_cycles(
            tmp_path,
            store,
            executions=[{"started_at": 2010, "ended_at": None, "exec_count": 3}],
        )
        assert row[:3] == (2000, 2060, 10)
        assert row[5:] == ("partial", "interval", "contains_rdst_traffic")
    finally:
        store.close()


def test_attribution_uses_one_batched_store_read_per_cycle(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        store.record_rdst_executions(
            [
                {
                    "target_id": "demo",
                    "normalized_hash": "h1",
                    "lane": "rdst/compare",
                    "run_id": "r1",
                    "started_at": 2010.0,
                    "ended_at": 2030.0,
                    "exec_count": 4,
                }
            ]
        )
        reads = []
        batched = store.rdst_executions_overlapping_many

        def spy_many(target_id, windows):
            reads.append("many")
            return batched(target_id, windows)

        def spy_single(*args, **kwargs):
            reads.append("single")
            raise AssertionError("attribution must use the batched reader")

        store.rdst_executions_overlapping_many = spy_many
        store.rdst_executions_overlapping = spy_single
        collector = object.__new__(QueryDiscoveryCollector)
        collector.target = "demo"
        collector._store = store
        rows = [
            {
                "normalized_hash": normalized,
                "window_start": 2000.0,
                "window_end": 2060.0,
                "calls_delta": 10,
                "exec_time_delta": 300.0,
                "approximate_qps": 10 / 60,
                "completeness": "complete",
            }
            for normalized in ("h1", "h2", "h3")
        ]
        rows.append(
            {
                "normalized_hash": "h4",
                "window_start": 2000.0,
                "window_end": 2060.0,
                "calls_delta": None,
                "exec_time_delta": None,
                "approximate_qps": None,
                "completeness": "partial",
            }
        )

        attributed = collector._apply_rdst_attribution(rows)

        assert reads == ["many"]
        assert attributed[0]["calls_delta"] == 6
        assert attributed[0]["attribution"] == "contains_rdst_traffic"
        assert all("attribution" not in row for row in attributed[1:])
    finally:
        store.close()


def test_exact_aggregate_spanning_windows_is_partial_and_never_double_subtracted(
    tmp_path,
):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        store.record_rdst_executions(
            [
                {
                    "target_id": "demo",
                    "normalized_hash": "h",
                    "lane": "rdst/loadtest",
                    "run_id": "aggregate",
                    "started_at": 2010.0,
                    "ended_at": 2110.0,
                    "exec_count": 8,
                }
            ]
        )
        collector = object.__new__(QueryDiscoveryCollector)
        collector.target = "demo"
        collector._store = store
        rows = [
            {
                "normalized_hash": "h",
                "window_start": start,
                "window_end": end,
                "calls_delta": 10,
                "exec_time_delta": 1.0,
                "approximate_qps": 10 / 60,
                "completeness": "complete",
            }
            for start, end in ((2000.0, 2060.0), (2060.0, 2120.0))
        ]

        attributed = collector._apply_rdst_attribution(rows)

        assert [row["calls_delta"] for row in attributed] == [10, 10]
        assert [row["completeness"] for row in attributed] == ["partial", "partial"]
        assert all(
            row["attribution"] == "contains_rdst_traffic" for row in attributed
        )
    finally:
        store.close()


def test_fractional_completion_point_subtracts_from_only_one_counter_window(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        store.record_rdst_executions(
            [
                {
                    "target_id": "demo",
                    "normalized_hash": "h",
                    "lane": "rdst/compare",
                    "run_id": "point",
                    "started_at": 2060.05,
                    "ended_at": 2060.05,
                    "exec_count": 1,
                }
            ]
        )
        collector = object.__new__(QueryDiscoveryCollector)
        collector.target = "demo"
        collector._store = store
        rows = [
            {
                "normalized_hash": "h",
                "window_start": start,
                "window_end": end,
                "calls_delta": 5,
                "exec_time_delta": 1.0,
                "approximate_qps": 5 / (end - start),
                "completeness": "complete",
            }
            for start, end in ((2000.1, 2060.1), (2060.1, 2120.1))
        ]

        attributed = collector._apply_rdst_attribution(rows)

        # The subtraction lands in exactly the window containing the point;
        # the neighbor, admitted only by the skew allowance, is marked
        # without subtracting or downgrading.
        assert [row["calls_delta"] for row in attributed] == [4, 5]
        assert [row["completeness"] for row in attributed] == [
            "complete",
            "complete",
        ]
        assert attributed[0]["attribution"] == "contains_rdst_traffic"
        assert attributed[1]["attribution"] == "contains_rdst_traffic"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_two_phase_epoch_change_resets_baseline_and_writes_no_deltas(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        connection = FakeConnection(
            pg_rules(
                2000.0,
                [pg_stat_row(111, calls=5, total=100.0)],
                text_rows=[pg_text_row(111, "SELECT * FROM users")],
            )
        )
        collector = two_phase_collector(
            tmp_path, lambda target: (connection, "postgresql"), store=store
        )
        first = await collector.collect_now()
        old_epoch = first.data["stats"]["epoch_id"]

        # A stats reset starts a new epoch: no deltas may span it.
        connection.rules = pg_rules(
            2060.0,
            [pg_stat_row(111, calls=2, total=30.0)],
            text_rows=[pg_text_row(111, "SELECT * FROM users")],
            stats_reset=999.0,
        )
        second = await collector.collect_now()
        new_epoch = second.data["stats"]["epoch_id"]
        assert new_epoch != old_epoch
        assert second.data["stats"]["epoch_changed"] is True
        assert second.data["stats"]["deltas_computed"] == 0
        # The identity was already in the Library; it is not re-added as New,
        # though its evidence refresh from the reset counters is a change.
        assert second.data["changed"] is True
        assert second.data["new_hashes"] == []
        assert recent_observation_rows(store) == {}
        assert store.get_collector_state("demo")["epoch_id"] == new_epoch

        # The next cycle diffs against the new-epoch baseline.
        connection.rules = pg_rules(
            2120.0, [pg_stat_row(111, calls=6, total=90.0)], stats_reset=999.0
        )
        third = await collector.collect_now()
        assert third.data["stats"]["epoch_changed"] is False
        assert third.data["stats"]["deltas_computed"] == 1
        aliases = store.get_identity_aliases("demo", new_epoch)
        observed = recent_observation_rows(store)
        assert observed[aliases["10:16384:111:t"]][:4] == (2060, 2120, 4, 60.0)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_two_phase_refreshes_known_entry_without_readding(tmp_path, monkeypatch):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    connection = FakeConnection(
        pg_rules(
            2000.0,
            [pg_stat_row(111, calls=5, total=100.0)],
            text_rows=[pg_text_row(111, "SELECT * FROM users")],
        )
    )
    collector = two_phase_collector(
        tmp_path, lambda target: (connection, "postgresql"), registry=registry
    )

    await collector.collect_now()
    registry.load()
    entry = registry.list_queries()[0]
    assert (entry.frequency, entry.avg_duration_ms) == (5, 20.0)
    first_observed = entry.lifecycle_for("demo").first_observed_at

    saves: list[int] = []
    original_save = QueryRegistry.save

    def counting_save(instance):
        saves.append(1)
        original_save(instance)

    monkeypatch.setattr(QueryRegistry, "save", counting_save)

    connection.rules = pg_rules(
        2060.0, [pg_stat_row(111, calls=15, total=400.0, mean=25.0)]
    )
    second = await collector.collect_now()

    # Exactly one batched save refreshed the known entry's evidence.
    assert saves == [1]
    assert second.data["changed"] is True
    assert second.data["new_hashes"] == []
    assert second.data["stats"]["refreshed_identity_count"] == 1
    registry.load()
    assert len(registry.list_queries()) == 1
    refreshed = registry.get_query(entry.hash)
    assert refreshed.frequency == 15
    assert refreshed.avg_duration_ms == 25.0
    lifecycle = refreshed.lifecycle_for("demo")
    assert lifecycle.first_observed_at == first_observed
    assert lifecycle.saved_at == ""
    assert lifecycle.sources == ["top-historical"]

    # Idle counters refresh nothing user-visible.
    connection.rules = pg_rules(
        2120.0, [pg_stat_row(111, calls=15, total=400.0, mean=25.0)]
    )
    third = await collector.collect_now()
    assert third.data["changed"] is False
    assert third.data["new_hashes"] == []


@pytest.mark.asyncio
async def test_two_phase_refresh_matches_legacy_evidence_fields(tmp_path):
    sql = "SELECT * FROM users"

    def legacy_query(freq, avg_ms):
        return TopQueryData(
            query_hash="upstream-hash",
            query_text=sql,
            normalized_query=sql,
            freq=freq,
            total_time="1.000s",
            avg_time=f"{avg_ms}ms",
            pct_load="12.0%",
        )

    legacy_registry = QueryRegistry(registry_path=str(tmp_path / "legacy.toml"))
    service = FakeTopService([legacy_query(5, 20.0)])
    legacy_collector = QueryDiscoveryCollector(
        "demo",
        service_factory=lambda: service,
        registry_factory=lambda: legacy_registry,
        clock=lambda: "2026-08-10T10:00:00Z",
    )
    await legacy_collector.collect_now()
    service.queries = [legacy_query(15, 25.0)]
    await legacy_collector.collect_now()

    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    connection = FakeConnection(
        pg_rules(
            2000.0,
            [pg_stat_row(111, calls=5, total=100.0)],
            text_rows=[pg_text_row(111, sql)],
        )
    )
    collector = two_phase_collector(
        tmp_path, lambda target: (connection, "postgresql"), registry=registry
    )
    await collector.collect_now()
    connection.rules = pg_rules(
        2060.0, [pg_stat_row(111, calls=15, total=400.0, mean=25.0)]
    )
    await collector.collect_now()

    legacy_registry.load()
    registry.load()
    legacy_entry = legacy_registry.list_queries()[0]
    two_phase_entry = registry.list_queries()[0]
    assert two_phase_entry.hash == legacy_entry.hash
    assert two_phase_entry.frequency == legacy_entry.frequency == 15
    assert two_phase_entry.avg_duration_ms == legacy_entry.avg_duration_ms == 25.0
    assert two_phase_entry.source == legacy_entry.source == "top-historical"
    legacy_lifecycle = legacy_entry.lifecycle_for("demo")
    two_phase_lifecycle = two_phase_entry.lifecycle_for("demo")
    assert two_phase_lifecycle.sources == legacy_lifecycle.sources
    assert two_phase_lifecycle.saved_at == legacy_lifecycle.saved_at == ""
    assert bool(two_phase_lifecycle.last_observed_at)
    assert bool(legacy_lifecycle.last_observed_at)


@pytest.mark.asyncio
async def test_two_phase_needs_text_add_matches_legacy_registry_hash(tmp_path):
    sql = "SELECT * FROM users WHERE id = 42"
    legacy_registry = QueryRegistry(registry_path=str(tmp_path / "legacy.toml"))
    expected_hash, _ = legacy_registry.add_query(
        sql=sql,
        source="top-historical",
        target="demo",
        observed=True,
        save_intent=False,
    )

    connection = FakeConnection(
        pg_rules(
            2000.0,
            [pg_stat_row(111, calls=5, total=100.0)],
            text_rows=[pg_text_row(111, sql)],
        )
    )
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    collector = two_phase_collector(
        tmp_path, lambda target: (connection, "postgresql"), registry=registry
    )

    event = await collector.collect_now()

    assert event.data["new_hashes"] == [expected_hash]
    registry.load()
    assert registry.get_query(expected_hash) is not None


@pytest.mark.asyncio
async def test_two_phase_failure_reports_error_and_rebuilds_connection(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        broken_rules = pg_rules(2000.0, [])
        broken_rules["pg_stat_statements(false)"] = RuntimeError("db down")
        failing = FakeConnection(broken_rules)
        healthy = FakeConnection(
            pg_rules(
                2000.0,
                [pg_stat_row(111, calls=5, total=100.0)],
                text_rows=[pg_text_row(111, "SELECT * FROM users")],
            )
        )
        connections = [failing, healthy]
        handed_out = []

        def factory(target):
            connection = connections[len(handed_out)]
            handed_out.append(connection)
            return connection, "postgresql"

        collector = two_phase_collector(tmp_path, factory, store=store)

        first = await collector.collect_now()
        assert first.event == "discovery_error"
        assert first.data["error"] == "Query discovery is temporarily unavailable."
        assert failing.closed is True
        state = store.get_collector_state("demo")
        assert state["state"] == "unavailable"
        assert state["error_code"] == "collection_failed"

        second = await collector.collect_now()
        assert second.event == "discovery_update"
        assert len(handed_out) == 2
        assert second.data["stats"]["service_reused"] is False
    finally:
        store.close()


@pytest.mark.asyncio
async def test_two_phase_store_failure_never_fails_a_cycle(tmp_path):
    connection = FakeConnection(
        pg_rules(
            2000.0,
            [pg_stat_row(111, calls=5, total=100.0)],
            text_rows=[pg_text_row(111, "SELECT * FROM users")],
        )
    )
    collector = two_phase_collector(
        tmp_path, lambda target: (connection, "postgresql"), store=BrokenStore()
    )

    event = await collector.collect_now()

    assert event.event == "discovery_update"
    assert event.data["query_count"] == 1
    assert len(event.data["new_hashes"]) == 1


MYSQL_START = 1700000000.0


def mysql_digest_row(digest, text, calls, total_ps, first_seen, last_seen,
                     schema="app", sample=None):
    row = (
        schema, digest, text, calls, total_ps, 1e9, 1e12, 2e12,
        calls * 2, calls * 3, 0, first_seen, last_seen,
    )
    if sample is not None:
        row += (sample,)
    return row


def mysql_rules(now_dt, digest_rows, sample_capable=False):
    return {
        "information_schema.columns": [(1,)] if sample_capable else [],
        "UNIX_TIMESTAMP": [(MYSQL_START,)],
        "NOW(6)": [(now_dt,)],
        "DIGEST_TEXT": list(digest_rows),
        "DIGEST IS NULL": [(0,)],
        "COUNT(*)": [(len(digest_rows),)],
        "SHOW GLOBAL STATUS": [],
        "SHOW VARIABLES": [],
    }


@pytest.mark.asyncio
async def test_two_phase_mysql_inline_texts_and_cursor_restore(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        now1 = datetime(2026, 8, 10, 10, 0, 0, 500000)
        row = mysql_digest_row(
            "abc",
            "SELECT * FROM users WHERE id = ?",
            calls=5,
            total_ps=5e12,
            first_seen=datetime(2026, 8, 10, 9, 0, 0),
            last_seen=datetime(2026, 8, 10, 9, 59, 0),
        )
        conn1 = FakeConnection(mysql_rules(now1, [row]))
        collector = two_phase_collector(
            tmp_path, lambda target: (conn1, "mysql"), store=store
        )

        first = await collector.collect_now()
        assert first.event == "discovery_update"
        assert first.data["source"] == "digest"
        assert first.data["stats"]["incremental"] is True
        assert len(first.data["new_hashes"]) == 1
        # DIGEST_TEXT arrives with the counters; there is no Phase B.
        assert not any("queryid = ANY" in sql for sql, _ in conn1.executed)
        cursor = store.get_collector_state("demo")["source_capabilities"]["cursor_state"]
        assert cursor == {
            "last_seen": "2026-08-10 10:00:00.500000",
            "server_start": MYSQL_START,
        }

        # A fresh collector restores the persisted cursor and fetches
        # incrementally from it.
        conn2 = FakeConnection(
            mysql_rules(datetime(2026, 8, 10, 10, 1, 0, 500000), [])
        )
        restarted = two_phase_collector(
            tmp_path, lambda target: (conn2, "mysql"), store=store
        )
        await restarted.collect_now()
        incremental = [
            params for sql, params in conn2.executed if "LAST_SEEN >=" in sql
        ]
        assert incremental == [("2026-08-10 10:00:00.500000",)]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_two_phase_mysql_sample_supplies_observed_params(tmp_path):
    digest_text = "SELECT * FROM `users` WHERE `id` = ? LIMIT ?"
    sample_text = "SELECT * FROM `users` WHERE `id` = 42 LIMIT 10"
    row = mysql_digest_row(
        "abc",
        digest_text,
        calls=5,
        total_ps=5e12,
        first_seen=datetime(2026, 8, 10, 9, 0, 0),
        last_seen=datetime(2026, 8, 10, 9, 59, 0),
        sample=sample_text,
    )
    conn = FakeConnection(
        mysql_rules(
            datetime(2026, 8, 10, 10, 0, 0, 500000), [row], sample_capable=True
        )
    )
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    collector = two_phase_collector(
        tmp_path, lambda target: (conn, "mysql"), registry=registry
    )

    event = await collector.collect_now()

    assert event.event == "discovery_update"
    assert event.data["new_hashes"] == [hash_sql(digest_text)]
    entry = registry.get_query(hash_sql(digest_text))
    # Identity and stored text stay keyed on DIGEST_TEXT; the sample's
    # literals become observed parameter values ('?' slot k resolves as pk).
    assert entry.original_sql == digest_text
    assert entry.most_recent_params == {"p1": "42", "p2": "10"}
    assert entry.parameters == {
        "p1": {"value": "42", "type": "number"},
        "p2": {"value": "10", "type": "number"},
    }


@pytest.mark.asyncio
async def test_two_phase_mysql_refresh_updates_observed_params(tmp_path):
    digest_text = "SELECT * FROM `users` WHERE `id` = ?"
    first_row = mysql_digest_row(
        "abc",
        digest_text,
        calls=5,
        total_ps=5e12,
        first_seen=datetime(2026, 8, 10, 9, 0, 0),
        last_seen=datetime(2026, 8, 10, 9, 59, 0),
        sample="SELECT * FROM `users` WHERE `id` = 42",
    )
    conn = FakeConnection(
        mysql_rules(
            datetime(2026, 8, 10, 10, 0, 0, 500000), [first_row], sample_capable=True
        )
    )
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    collector = two_phase_collector(
        tmp_path, lambda target: (conn, "mysql"), registry=registry
    )
    await collector.collect_now()

    second_row = mysql_digest_row(
        "abc",
        digest_text,
        calls=9,
        total_ps=9e12,
        first_seen=datetime(2026, 8, 10, 9, 0, 0),
        last_seen=datetime(2026, 8, 10, 10, 0, 30),
        sample="SELECT * FROM `users` WHERE `id` = 7",
    )
    conn.rules = mysql_rules(
        datetime(2026, 8, 10, 10, 1, 0, 500000), [second_row], sample_capable=True
    )
    event = await collector.collect_now()

    assert event.data["new_hashes"] == []
    entry = registry.get_query(hash_sql(digest_text))
    assert entry.most_recent_params == {"p1": "7"}


@pytest.mark.asyncio
async def test_pg_activity_snapshot_fills_pgss_identity_params(tmp_path):
    pgss_text = "SELECT * FROM users WHERE id = $1"
    rules = pg_rules(
        2000.0,
        [pg_stat_row(111, calls=5, total=100.0)],
        text_rows=[pg_text_row(111, pgss_text)],
    )
    rules["pg_stat_activity"] = [("SELECT * FROM users WHERE id = 42",)]
    connection = FakeConnection(rules)
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    collector = two_phase_collector(
        tmp_path, lambda target: (connection, "postgresql"), registry=registry
    )

    event = await collector.collect_now()

    assert event.event == "discovery_update"
    assert event.data["stats"]["activity_sampled"] == 1
    assert event.data["stats"]["params_captured"] == 1
    [new_hash] = event.data["new_hashes"]
    entry = registry.get_query(new_hash)
    # Identity and stored text stay keyed on the engine-normalized $N text;
    # the sampled execution only supplies observed values ($k resolves as pk).
    assert entry.original_sql == pgss_text
    assert entry.most_recent_params == {"p1": "42"}
    assert entry.parameters == {"p1": {"value": "42", "type": "number"}}
    # Exactly one bounded snapshot ran, excluding RDST's own sessions by SQL.
    [activity_sql] = [
        sql for sql, _ in connection.executed if "pg_stat_activity" in sql
    ]
    assert "state = 'active'" in activity_sql
    assert "pid != pg_backend_pid()" in activity_sql
    assert "NOT LIKE 'rdst/%'" in activity_sql
    assert activity_sql.endswith("LIMIT 200")


@pytest.mark.asyncio
async def test_pg_activity_values_key_by_textual_slot_position(tmp_path):
    # Literal extraction visits the LIMIT literal before the WHERE literal in
    # AST order; captured values must still key by textual slot position so
    # $1 resolves to the email and $2 to the limit.
    pgss_text = "SELECT id FROM users WHERE email = $1 LIMIT $2"
    rules = pg_rules(
        2000.0,
        [pg_stat_row(111, calls=5, total=100.0)],
        text_rows=[pg_text_row(111, pgss_text)],
    )
    rules["pg_stat_activity"] = [
        ("SELECT id FROM users WHERE email = 'a@b.c' LIMIT 10",)
    ]
    connection = FakeConnection(rules)
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    collector = two_phase_collector(
        tmp_path, lambda target: (connection, "postgresql"), registry=registry
    )

    event = await collector.collect_now()

    assert event.data["stats"]["params_captured"] == 1
    entry = registry.get_query(event.data["new_hashes"][0])
    assert entry.original_sql == pgss_text
    assert entry.most_recent_params == {"p1": "a@b.c", "p2": "10"}


@pytest.mark.asyncio
async def test_pg_activity_snapshot_refreshes_saved_literal_entry(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    saved_hash, _ = registry.add_query(
        sql="SELECT * FROM invoices WHERE id = 1",
        source="manual",
        target="demo",
    )
    saved_at = registry.get_query(saved_hash).lifecycle_for("demo").saved_at
    assert saved_at

    rules = pg_rules(2000.0, [])
    rules["pg_stat_activity"] = [("SELECT * FROM invoices WHERE id = 99",)]
    connection = FakeConnection(rules)
    collector = two_phase_collector(
        tmp_path, lambda target: (connection, "postgresql"), registry=registry
    )

    event = await collector.collect_now()

    # The sampled execution matches the saved identity through hash_sql and
    # refreshes its observed values without re-adding it as New or touching
    # its saved state.
    assert event.data["new_hashes"] == []
    assert event.data["changed"] is True
    assert event.data["stats"]["params_captured"] == 1
    entry = registry.get_query(saved_hash)
    assert entry.most_recent_params == {"p1": "99"}
    assert entry.lifecycle_for("demo").saved_at == saved_at


@pytest.mark.asyncio
async def test_activity_text_matching_no_identity_is_dropped(tmp_path):
    rules = pg_rules(
        2000.0,
        [pg_stat_row(111, calls=5, total=100.0)],
        text_rows=[pg_text_row(111, "SELECT * FROM users")],
    )
    # Activity sampling is unrepresentative evidence for admission (research
    # Q7): a sampled text matching no known identity never enters the Library.
    rules["pg_stat_activity"] = [("SELECT * FROM unknown_stuff WHERE x = 7",)]
    connection = FakeConnection(rules)
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    collector = two_phase_collector(
        tmp_path, lambda target: (connection, "postgresql"), registry=registry
    )

    event = await collector.collect_now()

    assert event.data["stats"]["activity_sampled"] == 1
    assert event.data["stats"]["params_captured"] == 0
    registry.load()
    entries = registry.list_queries()
    assert len(entries) == 1
    assert entries[0].original_sql == "SELECT * FROM users"


@pytest.mark.asyncio
async def test_activity_sample_error_never_fails_the_cycle(tmp_path):
    rules = pg_rules(
        2000.0,
        [pg_stat_row(111, calls=5, total=100.0)],
        text_rows=[pg_text_row(111, "SELECT * FROM users")],
    )
    rules["pg_stat_activity"] = RuntimeError("activity read failed")
    connection = FakeConnection(rules)
    collector = two_phase_collector(
        tmp_path, lambda target: (connection, "postgresql")
    )

    event = await collector.collect_now()

    assert event.event == "discovery_update"
    assert len(event.data["new_hashes"]) == 1
    assert event.data["stats"]["activity_sampled"] == 0
    assert event.data["stats"]["params_captured"] == 0


@pytest.mark.asyncio
async def test_mysql_processlist_snapshot_refreshes_literal_entry(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    seeded_hash, _ = registry.add_query(
        sql="SELECT * FROM users WHERE id = 5",
        source="top",
        target="demo",
        observed=True,
        save_intent=False,
    )

    rules = mysql_rules(datetime(2026, 8, 10, 10, 0, 0, 500000), [])
    rules["PROCESSLIST"] = [("SELECT * FROM users WHERE id = 55",)]
    conn = FakeConnection(rules)
    collector = two_phase_collector(
        tmp_path, lambda target: (conn, "mysql"), registry=registry
    )

    event = await collector.collect_now()

    # PROCESSLIST samples reach literal-minted identities only; digest-minted
    # identities take their observed values from QUERY_SAMPLE_TEXT instead.
    assert event.data["new_hashes"] == []
    assert event.data["stats"]["activity_sampled"] == 1
    assert event.data["stats"]["params_captured"] == 1
    entry = registry.get_query(seeded_hash)
    assert entry.most_recent_params == {"p1": "55"}
    [activity_sql] = [sql for sql, _ in conn.executed if "PROCESSLIST" in sql]
    assert "session_account_connect_attrs" in activity_sql
    assert "ATTR_VALUE LIKE 'rdst/%'" in activity_sql
    assert "ID != CONNECTION_ID()" in activity_sql


class CapabilityError(Exception):
    def __init__(self, message, pgcode=None):
        super().__init__(message)
        if pgcode is not None:
            self.pgcode = pgcode


def test_capability_error_classification():
    class PgError(Exception):
        pgcode = "42P01"

    class MysqlError(Exception):
        pass

    assert _capability_error_code(PgError("boom")) == "sqlstate_42P01"
    assert (
        _capability_error_code(MysqlError(1146, "Table doesn't exist"))
        == "errno_1146"
    )
    assert (
        _capability_error_code(RuntimeError("SELECT command denied to user 'ro'"))
        == "message_match"
    )
    assert (
        _capability_error_code(
            ValueError("unsupported engine for two-phase discovery: 'sqlite'")
        )
        == "unsupported_engine"
    )
    assert _capability_error_code(RuntimeError("connection refused")) is None
    assert _capability_error_code(TimeoutError()) is None


@pytest.mark.asyncio
async def test_capability_error_falls_back_to_legacy_for_collector_lifetime(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        broken_rules = pg_rules(2000.0, [])
        broken_rules["pg_stat_statements(false)"] = CapabilityError(
            "permission denied for view pg_stat_statements", pgcode="42501"
        )
        connections = []

        def factory(target):
            connection = FakeConnection(broken_rules)
            connections.append(connection)
            return connection, "postgresql"

        service = FakeTopService([top_query()])
        collector = QueryDiscoveryCollector(
            "demo",
            service_factory=lambda: service,
            registry_factory=lambda: registry,
            clock=lambda: "2026-08-10T10:00:00Z",
            unix_clock=lambda: 1234,
            store=store,
            two_phase=True,
            connection_factory=factory,
        )

        # The incapable source falls back to the legacy path in the same cycle.
        first = await collector.collect_now()
        assert first.event == "discovery_update"
        assert service.calls == 1
        assert len(connections) == 1
        state = store.get_collector_state("demo")
        assert state["state"] == "watching"
        unavailable = state["source_capabilities"]["source_unavailable"]
        assert unavailable["error_code"] == "sqlstate_42501"

        # The fallback is sticky: later cycles never re-probe the source.
        second = await collector.collect_now()
        assert second.event == "discovery_update"
        assert service.calls == 2
        assert len(connections) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_transient_error_keeps_two_phase_without_fallback(tmp_path):
    broken_rules = pg_rules(2000.0, [])
    broken_rules["pg_stat_statements(false)"] = RuntimeError("connection timed out")
    connections = []

    def factory(target):
        connection = FakeConnection(broken_rules)
        connections.append(connection)
        return connection, "postgresql"

    collector = two_phase_collector(tmp_path, factory)

    first = await collector.collect_now()
    second = await collector.collect_now()

    # Each cycle re-probes the two-phase source; the legacy service factory
    # (forbidden_service) was never invoked.
    assert first.event == "discovery_error"
    assert second.event == "discovery_error"
    assert len(connections) == 2


@pytest.mark.asyncio
async def test_pg_restart_restores_baseline_and_first_sweep_yields_deltas(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        conn1 = FakeConnection(
            pg_rules(
                2000.0,
                [pg_stat_row(111, calls=5, total=100.0)],
                text_rows=[pg_text_row(111, "SELECT * FROM users")],
            )
        )
        first_process = two_phase_collector(
            tmp_path, lambda target: (conn1, "postgresql"), store=store
        )
        await first_process.collect_now()

        # Calls made while no collector ran surface as deltas after restart
        # because the baseline is restored together with the epoch.
        conn2 = FakeConnection(
            pg_rules(2060.0, [pg_stat_row(111, calls=15, total=400.0)])
        )
        restarted = two_phase_collector(
            tmp_path, lambda target: (conn2, "postgresql"), store=store
        )
        event = await restarted.collect_now()

        assert event.data["stats"]["epoch_changed"] is False
        assert event.data["stats"]["deltas_computed"] == 1
        epoch = event.data["stats"]["epoch_id"]
        aliases = store.get_identity_aliases("demo", epoch)
        row = recent_observation_rows(store)[aliases["10:16384:111:t"]]
        assert row[:3] == (2000, 2060, 10)
        assert row[3] == pytest.approx(300.0)
        assert row[5] == "complete"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_mysql_restart_gap_calls_become_deltas(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        text = "SELECT * FROM users WHERE id = ?"
        first_seen = datetime(2026, 8, 10, 9, 0, 0)
        conn1 = FakeConnection(
            mysql_rules(
                datetime(2026, 8, 10, 10, 0, 0, 500000),
                [
                    mysql_digest_row(
                        "abc", text, calls=5, total_ps=5e12,
                        first_seen=first_seen,
                        last_seen=datetime(2026, 8, 10, 9, 59, 0),
                    )
                ],
            )
        )
        first_process = two_phase_collector(
            tmp_path, lambda target: (conn1, "mysql"), store=store
        )
        first = await first_process.collect_now()
        epoch = first.data["stats"]["epoch_id"]

        # Calls made during the restart gap: same key, advanced counters.
        conn2 = FakeConnection(
            mysql_rules(
                datetime(2026, 8, 10, 10, 1, 0),
                [
                    mysql_digest_row(
                        "abc", text, calls=9, total_ps=9e12,
                        first_seen=first_seen,
                        last_seen=datetime(2026, 8, 10, 10, 0, 30),
                    )
                ],
            )
        )
        restarted = two_phase_collector(
            tmp_path, lambda target: (conn2, "mysql"), store=store
        )
        second = await restarted.collect_now()

        # The restored cursor kept the fetch incremental, and the restored
        # baseline turned the restart-gap rows into real deltas.
        assert any("LAST_SEEN >=" in sql for sql, _ in conn2.executed)
        assert second.data["stats"]["epoch_changed"] is False
        assert second.data["stats"]["deltas_computed"] == 1
        aliases = store.get_identity_aliases("demo", epoch)
        row = recent_observation_rows(store)[aliases["app:abc"]]
        assert row[2] == 4
        assert row[3] == pytest.approx(4000.0)
        assert row[5] == "complete"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_mysql_epoch_mismatch_discards_cursor_and_full_sweeps(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        text = "SELECT * FROM users WHERE id = ?"
        row = mysql_digest_row(
            "abc", text, calls=5, total_ps=5e12,
            first_seen=datetime(2026, 8, 10, 9, 0, 0),
            last_seen=datetime(2026, 8, 10, 9, 59, 0),
        )
        conn1 = FakeConnection(
            mysql_rules(datetime(2026, 8, 10, 10, 0, 0, 500000), [row])
        )
        first_process = two_phase_collector(
            tmp_path, lambda target: (conn1, "mysql"), store=store
        )
        await first_process.collect_now()

        # The server restarted while no collector ran: a new epoch begins.
        restart_rules = mysql_rules(datetime(2026, 8, 10, 10, 5, 0), [row])
        restart_rules["UNIX_TIMESTAMP"] = [(MYSQL_START + 5000.0,)]
        conn2 = FakeConnection(restart_rules)
        restarted = two_phase_collector(
            tmp_path, lambda target: (conn2, "mysql"), store=store
        )
        second = await restarted.collect_now()

        assert not any("LAST_SEEN >=" in sql for sql, _ in conn2.executed)
        assert second.data["stats"]["epoch_changed"] is True
        assert second.data["stats"]["deltas_computed"] == 0
        assert recent_observation_rows(store) == {}
    finally:
        store.close()


@pytest.mark.asyncio
async def test_missing_stored_baseline_discards_cursor_and_full_sweeps(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        text = "SELECT * FROM users WHERE id = ?"
        row = mysql_digest_row(
            "abc", text, calls=5, total_ps=5e12,
            first_seen=datetime(2026, 8, 10, 9, 0, 0),
            last_seen=datetime(2026, 8, 10, 9, 59, 0),
        )
        conn1 = FakeConnection(
            mysql_rules(datetime(2026, 8, 10, 10, 0, 0, 500000), [row])
        )
        first_process = two_phase_collector(
            tmp_path, lambda target: (conn1, "mysql"), store=store
        )
        await first_process.collect_now()

        # The persisted cursor outlived its snapshots (e.g. pruned): a
        # cursor without a matching baseline must not be restored.
        conn = sqlite3.connect(store.path)
        try:
            conn.execute("DELETE FROM counter_snapshot")
            conn.commit()
        finally:
            conn.close()

        conn2 = FakeConnection(
            mysql_rules(datetime(2026, 8, 10, 10, 1, 0), [row])
        )
        restarted = two_phase_collector(
            tmp_path, lambda target: (conn2, "mysql"), store=store
        )
        second = await restarted.collect_now()

        assert not any("LAST_SEEN >=" in sql for sql, _ in conn2.executed)
        assert second.data["stats"]["deltas_computed"] == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_identity_missing_first_topk_is_late_admitted_when_hot(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        connection = FakeConnection(
            pg_rules(
                2000.0,
                [
                    pg_stat_row(111, calls=5, total=100.0),
                    pg_stat_row(222, calls=2, total=10.0),
                ],
                text_rows=[
                    pg_text_row(111, "SELECT * FROM users"),
                    pg_text_row(222, "SELECT * FROM orders"),
                ],
            )
        )
        collector = two_phase_collector(
            tmp_path,
            lambda target: (connection, "postgresql"),
            store=store,
            limit=1,
            registry=registry,
        )

        first = await collector.collect_now()
        assert len(first.data["new_hashes"]) == 1
        epoch = first.data["stats"]["epoch_id"]
        aliases = store.get_identity_aliases("demo", epoch)

        # The skipped identity becomes the hottest by interval delta and is
        # admitted with its text from a bounded extra Phase B fetch.
        connection.rules = pg_rules(
            2060.0,
            [
                pg_stat_row(111, calls=6, total=110.0),
                pg_stat_row(222, calls=500, total=900.0),
            ],
            text_rows=[pg_text_row(222, "SELECT * FROM orders")],
        )
        second = await collector.collect_now()

        assert second.data["new_hashes"] == [aliases["10:16384:222:t"]]
        registry.load()
        entry = registry.get_query(aliases["10:16384:222:t"])
        assert entry.original_sql == "SELECT * FROM orders"
        assert entry.frequency == 500
    finally:
        store.close()


@pytest.mark.asyncio
async def test_mysql_incremental_refresh_never_shrinks_identity_frequency(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    text = "SELECT * FROM users WHERE id = ?"
    first_seen = datetime(2026, 8, 10, 9, 0, 0)
    connection = FakeConnection(
        mysql_rules(
            datetime(2026, 8, 10, 10, 0, 0),
            [
                mysql_digest_row(
                    "abc", text, calls=1000, total_ps=2e12,
                    first_seen=first_seen,
                    last_seen=datetime(2026, 8, 10, 9, 59, 0),
                    schema="app",
                ),
                mysql_digest_row(
                    "abc", text, calls=500, total_ps=1e12,
                    first_seen=first_seen,
                    last_seen=datetime(2026, 8, 10, 9, 58, 0),
                    schema="web",
                ),
            ],
        )
    )
    collector = two_phase_collector(
        tmp_path, lambda target: (connection, "mysql"), registry=registry
    )

    first = await collector.collect_now()
    # One Library slot per normalized identity, evidence summed across
    # engine keys.
    assert len(first.data["new_hashes"]) == 1
    registry.load()
    entry = registry.get_query(first.data["new_hashes"][0])
    assert entry.frequency == 1500
    assert entry.avg_duration_ms == pytest.approx(1000.0)

    # An incremental cycle sweeping only one schema must aggregate from the
    # merged baseline, never shrinking the identity's cumulative evidence.
    connection.rules = mysql_rules(
        datetime(2026, 8, 10, 10, 1, 0),
        [
            mysql_digest_row(
                "abc", text, calls=700, total_ps=1.4e12,
                first_seen=first_seen,
                last_seen=datetime(2026, 8, 10, 10, 0, 30),
                schema="web",
            ),
        ],
    )
    second = await collector.collect_now()
    assert any("LAST_SEEN >=" in sql for sql, _ in connection.executed)
    assert second.data["stats"]["refreshed_identity_count"] == 1
    registry.load()
    refreshed = registry.get_query(entry.hash)
    assert refreshed.frequency == 1700
    assert refreshed.avg_duration_ms == pytest.approx(1000.0)


@pytest.mark.parametrize("bad_text", [None, "<insufficient privilege>"])
@pytest.mark.asyncio
async def test_unusable_text_stays_unresolved_and_is_retried(tmp_path, bad_text):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        connection = FakeConnection(
            pg_rules(
                2000.0,
                [pg_stat_row(111, calls=5, total=100.0)],
                text_rows=[pg_text_row(111, bad_text)],
            )
        )
        collector = two_phase_collector(
            tmp_path, lambda target: (connection, "postgresql"), store=store
        )

        first = await collector.collect_now()
        epoch = first.data["stats"]["epoch_id"]
        assert first.data["new_hashes"] == []
        assert store.get_identity_aliases("demo", epoch) == {}

        # Text is requested again and resolves once it becomes valid.
        connection.rules = pg_rules(
            2060.0,
            [pg_stat_row(111, calls=6, total=120.0)],
            text_rows=[pg_text_row(111, "SELECT * FROM users")],
        )
        second = await collector.collect_now()
        assert len(second.data["new_hashes"]) == 1
        assert store.get_identity_aliases("demo", epoch) == {
            "10:16384:111:t": second.data["new_hashes"][0]
        }
        assert (
            len([sql for sql, _ in connection.executed if "queryid = ANY" in sql])
            == 2
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_rejected_candidate_text_leaves_no_persisted_alias(tmp_path):
    store = ObservationStore(tmp_path / "cache.db")
    try:
        connection = FakeConnection(
            pg_rules(
                2000.0,
                [pg_stat_row(111, calls=5, total=100.0)],
                text_rows=[
                    pg_text_row(111, "SELECT * FROM users; SELECT * FROM orders")
                ],
            )
        )
        collector = two_phase_collector(
            tmp_path, lambda target: (connection, "postgresql"), store=store
        )

        # add_query rejects the multi-statement text; the alias minted from
        # it must not persist as resolved. The text references user relations
        # so it reaches registry validation instead of the system skip.
        first = await collector.collect_now()
        epoch = first.data["stats"]["epoch_id"]
        assert first.data["new_hashes"] == []
        assert store.get_identity_aliases("demo", epoch) == {}

        connection.rules = pg_rules(
            2060.0,
            [pg_stat_row(111, calls=6, total=120.0)],
            text_rows=[pg_text_row(111, "SELECT * FROM users")],
        )
        second = await collector.collect_now()
        assert len(second.data["new_hashes"]) == 1
        assert store.get_identity_aliases("demo", epoch) == {
            "10:16384:111:t": second.data["new_hashes"][0]
        }
    finally:
        store.close()


@pytest.mark.asyncio
async def test_last_subscriber_stops_target_collector(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    service = FakeTopService([top_query()])
    collector = QueryDiscoveryCollector(
        "demo",
        interval_seconds=3600,
        service_factory=lambda: service,
        registry_factory=lambda: registry,
    )

    subscription = collector.subscribe()
    await anext(subscription)
    await asyncio.wait_for(anext(subscription), timeout=1)
    assert collector.task is not None

    await subscription.aclose()

    assert collector.task is None


class SpyObservationStore:
    """Records the fence kwargs each store write receives."""

    def __init__(self, fail_on: str | None = None):
        self.fail_on = fail_on
        self.calls: list[tuple] = []

    def _record(self, method, owner_id, fencing_token, now):
        self.calls.append((method, owner_id, fencing_token, now))
        if self.fail_on == method:
            raise LeaseLostError("lease moved")

    @contextmanager
    def write_fence(self, target, *, owner_id, fencing_token, now):
        self._record("write_fence", owner_id, fencing_token, now)
        yield

    def latest_seq(self, target):
        return 0

    def append_event(
        self, target, kind, payload, created_at, *, owner_id=None, fencing_token=None, now=None
    ):
        self._record("append_event", owner_id, fencing_token, now)
        return len(self.calls)

    def record_counter_snapshots(
        self, target, epoch_id, rows, captured_at, *, owner_id=None, fencing_token=None, now=None
    ):
        self._record("record_counter_snapshots", owner_id, fencing_token, now)
        return 0

    def upsert_collector_state(
        self, target, *, owner_id=None, fencing_token=None, now=None, **kwargs
    ):
        self._record("upsert_collector_state", owner_id, fencing_token, now)

    def get_collector_state(self, target):
        return None

    def prune(self, cutoff, chunk=10000):
        return 0

    def prune_events(self, cutoff, keep=1000):
        return 0

    def prune_rdst_executions(self, cutoff, chunk=10000, *, now=None):
        return 0


@pytest.mark.asyncio
async def test_write_fence_forwards_owner_and_token_to_store(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    store = SpyObservationStore()
    collector = QueryDiscoveryCollector(
        "demo",
        service_factory=lambda: FakeTopService([top_query()]),
        registry_factory=lambda: registry,
        store=store,
    )

    collector.set_write_fence("owner-a", 7, lambda: 123)
    await collector.collect_now()
    assert store.calls
    assert all(call[1:] == ("owner-a", 7, 123) for call in store.calls)

    # Without the scheduler's fence, writes stay unfenced exactly as before.
    collector.clear_write_fence()
    store.calls.clear()
    await collector.collect_now()
    assert store.calls
    assert all(call[1:] == (None, None, None) for call in store.calls)


@pytest.mark.asyncio
async def test_lease_lost_mid_cycle_aborts_remaining_store_writes(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    store = SpyObservationStore(fail_on="record_counter_snapshots")
    collector = QueryDiscoveryCollector(
        "demo",
        service_factory=lambda: FakeTopService([top_query()]),
        registry_factory=lambda: registry,
        store=store,
    )
    collector.set_write_fence("owner-a", 7, lambda: 123)

    with pytest.raises(LeaseLostError):
        await collector.collect_now()

    methods = [call[0] for call in store.calls]
    assert methods[-1] == "record_counter_snapshots"
    assert "upsert_collector_state" not in methods
    assert "append_event" not in methods


def test_coordinator_reports_subscriber_state():
    coordinator = QueryDiscoveryCoordinator(
        collector_factory=lambda target: QueryDiscoveryCollector(target)
    )
    assert not coordinator.has_subscribers("demo")
    collector = coordinator.collector_for("demo")
    assert not coordinator.has_subscribers("demo")
    collector._subscribers.add(object())
    assert coordinator.has_subscribers("demo")


@pytest.mark.asyncio
async def test_system_catalog_identities_never_enter_the_library(tmp_path):
    """RDST's own catalog/statistics queries stay out of the Query Library
    and stop consuming top-K slots after their first classification."""
    store = ObservationStore(tmp_path / "cache.db")
    try:
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        connection = FakeConnection(
            pg_rules(
                2000.0,
                [
                    # The system query dominates the ranking on an idle target.
                    pg_stat_row(111, calls=900, total=9000.0),
                    pg_stat_row(222, calls=2, total=10.0, mean=5.0),
                ],
                text_rows=[
                    pg_text_row(111, "SELECT * FROM pg_stat_user_indexes"),
                    pg_text_row(222, "SELECT * FROM orders"),
                ],
            )
        )
        collector = two_phase_collector(
            tmp_path,
            lambda target: (connection, "postgresql"),
            store=store,
            limit=1,
            registry=registry,
        )

        first = await collector.collect_now()
        assert first.event == "discovery_update"
        # The system identity won the only slot and was skipped there.
        assert first.data["stats"]["system_skipped"] == 1
        assert first.data["new_hashes"] == []

        second = await collector.collect_now()
        assert second.event == "discovery_update"
        # Excluded before the cap now, so the user query takes the slot.
        assert second.data["stats"]["system_skipped"] == 0
        assert len(second.data["new_hashes"]) == 1

        registry.load()
        sqls = [entry.original_sql for entry in registry.list_queries(limit=None)]
        assert sqls == ["SELECT * FROM orders"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_self_template_identities_never_enter_the_library(tmp_path):
    """Marker-less diagnostic statements replayed by the statement store are
    recognized by shape and stay out of the Query Library."""
    profiler_text = (
        'SELECT "body"::text, COUNT(*) AS cnt FROM "posts" '
        'TABLESAMPLE SYSTEM($1) WHERE "body" IS NOT NULL '
        'GROUP BY "body" ORDER BY cnt DESC LIMIT $2'
    )
    store = ObservationStore(tmp_path / "cache.db")
    try:
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        connection = FakeConnection(
            pg_rules(
                2000.0,
                [
                    pg_stat_row(111, calls=900, total=9000.0),
                    pg_stat_row(222, calls=2, total=10.0, mean=5.0),
                ],
                text_rows=[
                    pg_text_row(111, profiler_text),
                    pg_text_row(222, "SELECT * FROM orders"),
                ],
            )
        )
        collector = two_phase_collector(
            tmp_path,
            lambda target: (connection, "postgresql"),
            store=store,
            limit=1,
            registry=registry,
        )

        first = await collector.collect_now()
        assert first.event == "discovery_update"
        assert first.data["stats"]["system_skipped"] == 1
        assert first.data["new_hashes"] == []

        second = await collector.collect_now()
        assert second.event == "discovery_update"
        assert second.data["stats"]["system_skipped"] == 0
        assert len(second.data["new_hashes"]) == 1

        registry.load()
        sqls = [entry.original_sql for entry in registry.list_queries(limit=None)]
        assert sqls == ["SELECT * FROM orders"]
    finally:
        store.close()

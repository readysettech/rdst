"""Resource limits under duplicate input, slow consumers and failed databases."""

import asyncio
import json
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from features.monitor.param_sampler import ParamSampler, sample_interval
from features.query_registry import scheduler as scheduler_module
from features.query_registry.scheduler import ObservationScheduler, _TargetSchedule
from shared.monitor.monitor_store import MonitorStore
from shared.query_registry.delta_processor import CounterRow, advance_baseline, process_snapshot
from shared.query_registry.library_store import LibraryStore
from shared.target_backoff import TargetBackoff


def test_shared_backoff_has_one_probe_exponential_cap_and_success_reset():
    now = [0.0]
    backoff = TargetBackoff(lambda: now[0])
    for delay in (30, 60, 120, 240, 480, 900, 900):
        assert backoff.acquire("db")
        assert not backoff.acquire("db")
        backoff.finish("db", success=False)
        now[0] += delay - 1
        assert not backoff.acquire("db")
        now[0] += 1
    assert backoff.acquire("db")
    backoff.finish("db", success=True)
    assert not backoff.failed("db")
    assert backoff.acquire("db")
    backoff.finish("db", success=False)
    now[0] += 30
    assert backoff.acquire("db")


def test_backoff_admission_and_target_state_are_bounded():
    backoff = TargetBackoff()
    for index in range(1024):
        assert backoff.acquire(str(index))
    assert not backoff.acquire("overflow")
    backoff.forget("0")
    assert not backoff.acquire("overflow")
    backoff.release("0")
    backoff.forget("0")
    assert backoff.acquire("overflow")


@pytest.mark.parametrize("value,expected", [("nan", 3), ("inf", 3), ("-1", 3), ("0", 0), (".001", 1)])
def test_sampler_interval_is_finite_and_has_a_timer_floor(monkeypatch, value, expected):
    monkeypatch.setenv("RDST_MONITOR_SAMPLE_INTERVAL_S", value)
    assert sample_interval() == expected


def test_sampler_deduplicates_bounds_batch_and_reuses_connection(tmp_path, monkeypatch):
    store = MonitorStore(tmp_path / "monitor.db")
    store.record("db", 1, [], {"key": "SELECT * FROM orders"}, [])
    sampler = ParamSampler(store)
    monkeypatch.setattr(sampler, "wanted_keys", lambda target: {"key"})
    from features.query_registry.discovery import query_discovery
    monkeypatch.setattr(query_discovery, "collector_for", lambda target: SimpleNamespace(
        _engine="mysql", _alias_map={"shop:digest": "key"}))
    monkeypatch.setattr(sampler, "_read", lambda *args: [("key", "SELECT 7")] * 1000)
    assert sampler.sample_target("db")
    connection = sampler._sample_connection
    statements = []
    connection.set_trace_callback(statements.append)
    assert not sampler.sample_target("db")
    assert not any(sql.startswith("INSERT") for sql in statements)
    assert not any(sql.startswith("BEGIN") for sql in statements)
    assert sampler._sample_connection is connection
    assert sampler.captured == 1
    monkeypatch.setattr(sampler, "_read", lambda *args: [("key", f"SELECT {i}") for i in range(1000)])
    sampler.sample_target("db")
    assert sampler.captured <= 6
    assert store.read("SELECT COUNT(*) n FROM query_sample")[0]["n"] <= 5
    sampler._close_all()


def test_sampler_idle_backoff_and_disabled_target_cleanup(monkeypatch):
    now, targets = [0.0], {"db"}
    store = SimpleNamespace(monitored_targets=lambda: targets)
    sampler = ParamSampler(store, clock=lambda: now[0], interval_s=3,
                           backoff=TargetBackoff(lambda: now[0]))
    calls = []
    monkeypatch.setattr(sampler, "sample_target", lambda target: calls.append(target) or False)
    assert not sampler.tick()
    assert sampler._next_poll["db"] == (6, 6)
    now[0] = 3
    sampler.tick()
    assert calls == ["db"]
    now[0] = 6
    sampler.tick()
    assert sampler._next_poll["db"] == (18, 12)
    closed = []
    sampler._connections["db"] = SimpleNamespace(close=lambda: closed.append(True))
    sampler._wanted["db"] = (0, {"key"})
    targets.clear()
    sampler.tick()
    assert closed == [True]
    assert not sampler._connections and not sampler._wanted and not sampler._next_poll


def test_sampler_without_work_preserves_an_outage(monkeypatch):
    now = [0.0]
    backoff = TargetBackoff(lambda: now[0])
    assert backoff.acquire("db")
    backoff.finish("db", success=False)
    now[0] = 30
    sampler = ParamSampler(SimpleNamespace(monitored_targets=lambda: {"db"}),
                           clock=lambda: now[0], interval_s=3, backoff=backoff)
    monkeypatch.setattr(sampler, "sample_target", lambda target: None)
    assert not sampler.tick()
    assert backoff.failed("db")

def test_baselines_prune_stale_keys_and_cap_cardinality_without_false_deltas():
    row = CounterRow("old", 10, 0, 10, 1, 1, 1, 0, "epoch")
    current = replace(row, engine_key="new", captured_at=3601)
    baseline = advance_baseline({"old": row}, [current])
    assert set(baseline) == {"new"}
    returning = replace(row, calls=100, captured_at=3661)
    result = process_snapshot(baseline, [returning], 3601, 3661)
    assert next(item for item in result if item.engine_key == "old").is_baseline
    rows = [replace(current, engine_key=str(i), captured_at=3601 + i) for i in range(30)]
    bounded = advance_baseline({}, rows, max_keys=10)
    assert set(bounded) == {str(i) for i in range(20, 30)}


def test_library_integrity_is_cached_and_invalidated_on_replacement(tmp_path, monkeypatch):
    path, toml = tmp_path / "library.db", tmp_path / "queries.toml"
    LibraryStore(path, toml).ensure_open()
    statements = []
    connect = LibraryStore._connect
    def traced(self):
        connection = connect(self)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(LibraryStore, "_connect", traced)
    LibraryStore(path, toml).ensure_open()
    LibraryStore(path, toml).ensure_open()
    assert statements.count("PRAGMA quick_check") == 1
    replacement = tmp_path / "replacement.db"
    LibraryStore(replacement, toml).ensure_open()
    replacement.replace(path)
    LibraryStore(path, toml).ensure_open()
    assert statements.count("PRAGMA quick_check") == 2
    LibraryStore.invalidate_integrity(path)
    LibraryStore(path, toml).ensure_open()
    assert statements.count("PRAGMA quick_check") == 3


@pytest.mark.asyncio
async def test_scheduler_admission_stays_held_after_timeout(monkeypatch):
    gate = asyncio.Event()
    calls = []
    async def collect():
        calls.append(True)
        await gate.wait()
        return SimpleNamespace(event="discovery_update", data={})
    collector = SimpleNamespace(collect_now=collect)
    coordinator = SimpleNamespace(collector_for=lambda name: collector, has_subscribers=lambda name: False)
    scheduler = ObservationScheduler(coordinator, clock=lambda: 100, targets_provider=lambda: [])
    scheduler._schedules = {str(i): _TargetSchedule(next_due=0) for i in range(50)}
    monkeypatch.setattr(scheduler_module, "COLLECTION_DEADLINE_SECONDS", .01)
    scheduler._launch_due(100)
    assert sum(s.task is not None for s in scheduler._schedules.values()) == 3
    await asyncio.sleep(.03)
    scheduler._launch_due(100)
    assert len(calls) == 3 and len(scheduler._collections) == 3
    tasks = [s.task for s in scheduler._schedules.values() if s.task is not None]
    gate.set()
    await asyncio.gather(*tasks)
    assert not scheduler._collections


@pytest.mark.asyncio
async def test_slow_stream_drops_oldest_and_cancels_producers(monkeypatch):
    from features.monitor.api import routes
    ended = []
    async def subscribe():
        try:
            for i in range(200):
                yield SimpleNamespace(event="discovery_update", data={"state": i})
            await asyncio.Event().wait()
        finally:
            ended.append(True)
    monkeypatch.setattr(routes.query_discovery, "collector_for", lambda target: SimpleNamespace(subscribe=subscribe))
    monkeypatch.setattr(routes, "_store", lambda: SimpleNamespace(
        read=lambda *args: [{"id": 0}], pulses=lambda *args, **kwargs: []))
    response = await routes.stream("db")
    assert response.send_timeout == 15
    iterator = response.body_iterator
    first = await anext(iterator)
    assert json.loads(first["data"])["state"] == 136
    await iterator.aclose()
    assert ended == [True]


@pytest.mark.asyncio
async def test_analysis_deadline_covers_parameter_suggestions_and_cancels_job(monkeypatch):
    from features.monitor.analyze_queue import AnalyzeQueue
    from shared import agent_activity
    from contextlib import contextmanager
    @contextmanager
    def action(*args, **kwargs):
        yield SimpleNamespace(describe=lambda **kwargs: None)
    monkeypatch.setattr(agent_activity, "action", action)
    finished, cancelled = [], []
    store = SimpleNamespace(analysis_owned=lambda job: True,
                            finish_analysis=lambda *args, **kwargs: finished.append((args, kwargs)),
                            read=lambda sql, *args: [{"text": "SELECT 1"}] if "SELECT text" in sql else [])
    async def suggest(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    queue = AnalyzeQueue(store, service=SimpleNamespace(suggest_parameter_values=suggest),
                         job_timeout_s=.01)
    await queue._process_recorded({"id": 1, "target": "db", "query_key": "key", "reason": "user", "owner": "test", "attempt": 1})
    assert cancelled == [True]
    assert finished[0][0][1] == "failed"
    assert finished[0][1]["error"] == "Analysis job deadline exceeded"
    assert not queue._active_runs


@pytest.mark.asyncio
async def test_cancelled_scheduler_retains_admission_for_native_work(monkeypatch):
    gate = threading.Event()
    started = threading.Event()
    def native():
        started.set()
        gate.wait(2)
        return SimpleNamespace(event="discovery_update", data={})
    async def collect():
        return await asyncio.to_thread(native)
    coordinator = SimpleNamespace(collector_for=lambda name: SimpleNamespace(collect_now=collect),
                                  has_subscribers=lambda name: False)
    scheduler = ObservationScheduler(coordinator, clock=lambda: 100, targets_provider=lambda: [])
    scheduler._schedules = {str(i): _TargetSchedule(next_due=0) for i in range(50)}
    scheduler._launch_due(100)
    try:
        assert await asyncio.to_thread(started.wait, 1)
        parents = list(scheduler._admitted)
        for parent in parents:
            parent.cancel()
        await asyncio.gather(*parents, return_exceptions=True)
        scheduler._launch_due(100)
        assert len(scheduler._admitted) == 3
        assert all(s.task is None for s in scheduler._schedules.values())
        work = list(scheduler._collections)
    finally:
        gate.set()
    await asyncio.gather(*work)
    assert not scheduler._admitted


@pytest.mark.asyncio
async def test_scheduler_releases_tasks_cancelled_before_they_start():
    coordinator = SimpleNamespace(has_subscribers=lambda name: False)
    scheduler = ObservationScheduler(coordinator, clock=lambda: 100, targets_provider=lambda: [])
    scheduler._schedules = {str(i): _TargetSchedule(next_due=0) for i in range(10)}
    scheduler._launch_due(100)
    parents = list(scheduler._admitted)
    for parent in parents:
        parent.cancel()
    await asyncio.gather(*parents, return_exceptions=True)
    assert not scheduler._admitted
    assert all(s.task is None for s in scheduler._schedules.values())


@pytest.mark.asyncio
async def test_report_initial_write_runs_in_worker_thread():
    from features.monitor.report import run_report
    main = threading.get_ident()
    threads = []
    def update(*args, **kwargs):
        threads.append(threading.get_ident())
        raise ValueError("stop after initial write")
    store = SimpleNamespace(update_report=update)
    with pytest.raises(ValueError, match="stop after initial write"):
        await run_report("guard-test", "user", store=store, claimed_run={"id": 1, "ts": 1})
    assert threads and threads[0] != main

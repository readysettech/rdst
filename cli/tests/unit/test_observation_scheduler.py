"""Flag-gated observation scheduler: cadence math, leases, and lifecycle."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.monitor.api import routes as monitor_routes
from features.monitor.connection import LOST_AFTER_FAILURES, ConnectionWatch
from shared.monitor.monitor_store import MonitorStore

from features.query_registry import scheduler as scheduler_module
from features.query_registry.scheduler import (
    ACTIVE_INTERVAL_SECONDS,
    BACKGROUND_INTERVAL_SECONDS,
    BACKOFF_BASE_SECONDS,
    BACKOFF_CAP_SECONDS,
    DEGRADED_AFTER_FAILURES,
    DEGRADED_INTERVAL_SECONDS,
    GLOBAL_CONCURRENCY,
    LEASE_TTL_SECONDS,
    MAX_INTERVAL_SECONDS,
    ObservationScheduler,
    _stable_hash,
    observation_scheduler_enabled,
)
from shared.query_registry.observation_store import LeaseLostError, ObservationStore
from shared.query_registry.query_registry import QueryRegistry

RDST_ROOT = Path(__file__).resolve().parents[2]


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeCollector:
    def __init__(self, clock: FakeClock | None = None) -> None:
        self.clock = clock
        self.collects = 0
        self.duration = 0.0
        self.db_touch_ms = None
        self.fail_with: Exception | None = None
        self.gate: asyncio.Event | None = None
        self.fences: list[tuple[str, int]] = []
        self.clear_calls = 0

    def set_write_fence(self, owner_id, fencing_token, now_fn) -> None:
        self.fences.append((owner_id, fencing_token))

    def clear_write_fence(self) -> None:
        self.clear_calls += 1

    async def collect_now(self):
        self.collects += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail_with is not None:
            raise self.fail_with
        if self.clock is not None and self.duration:
            self.clock.advance(self.duration)
        stats = ({"db_touch_duration_ms": self.db_touch_ms}
                 if self.db_touch_ms is not None else {})
        return SimpleNamespace(event="discovery_update", data={"stats": stats})


class FakeCoordinator:
    def __init__(self, store=None, clock: FakeClock | None = None) -> None:
        self._store = store
        self.clock = clock
        self.collectors: dict[str, FakeCollector] = {}
        self.subscribed: set[str] = set()
        self.scheduler_owned = False
        self.subscription_listener = None

    def set_scheduler_owned(self, owned: bool) -> None:
        self.scheduler_owned = owned

    def set_subscription_listener(self, listener) -> None:
        self.subscription_listener = listener

    def store(self):
        return self._store

    def has_subscribers(self, target: str) -> bool:
        return target in self.subscribed

    def collector_for(self, target: str) -> FakeCollector:
        return self.collectors.setdefault(target, FakeCollector(self.clock))


class FakeLeaseStore:
    def __init__(self) -> None:
        self.tokens: dict[str, int] = {}
        self.acquires: list[tuple[str, str, int, int]] = []
        self.releases: list[tuple[str, str]] = []
        self.deny: set[str] = set()

    def acquire_lease(self, target, owner, ttl_s, now):
        self.acquires.append((target, owner, ttl_s, now))
        if target in self.deny:
            return None
        self.tokens[target] = self.tokens.get(target, 0) + 1
        return self.tokens[target]

    def release_lease(self, target, owner):
        self.releases.append((target, owner))
        return True


def make_scheduler(targets, *, store=None, clock=None, rand=lambda: 0.5):
    clock = clock or FakeClock()
    coordinator = FakeCoordinator(store=store, clock=clock)
    scheduler = ObservationScheduler(
        coordinator,
        targets_provider=lambda: list(targets),
        clock=clock.time,
        rand=rand,
        owner_id="owner-a",
    )
    scheduler._store = coordinator.store()
    scheduler._refresh_targets(clock.now)
    return scheduler, coordinator, clock


async def run_tick(scheduler, schedule, clock):
    """Advance to the retry slot, launch it, and wait for the collection task."""
    clock.now = max(clock.now, schedule.next_due)
    schedule.next_due = clock.now
    scheduler._launch_due(clock.now)
    if schedule.task is not None:
        await schedule.task


# -- flag ---------------------------------------------------------------------


def test_flag_defaults_off_and_accepts_trimmed_values(monkeypatch):
    monkeypatch.delenv(scheduler_module.SCHEDULER_ENV, raising=False)
    assert not observation_scheduler_enabled()
    for value in ("0", "false", "", "  ", "yes", "on"):
        monkeypatch.setenv(scheduler_module.SCHEDULER_ENV, value)
        assert not observation_scheduler_enabled()
    for value in ("1", "true", " TRUE ", " 1 "):
        monkeypatch.setenv(scheduler_module.SCHEDULER_ENV, value)
        assert observation_scheduler_enabled()


# -- lifespan wiring ----------------------------------------------------------


class StubSandbox:
    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class RecordingScheduler:
    instances: list["RecordingScheduler"] = []

    def __init__(self, coordinator, **kwargs) -> None:
        self.coordinator = coordinator
        self.started = False
        self.stopped = False
        RecordingScheduler.instances.append(self)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def stub_lifespan(monkeypatch):
    import shared.deploy.sandbox_manager as sandbox_module
    monkeypatch.setattr("features.query_registry.assessment.query_assessment_worker",
                        SimpleNamespace(start=AsyncMock(), stop=AsyncMock(),
                                        set_background_targets=lambda targets: None))
    monkeypatch.setattr(sandbox_module, "sandbox_manager", StubSandbox())
    monkeypatch.setattr(scheduler_module, "ObservationScheduler", RecordingScheduler)
    RecordingScheduler.instances = []
    from shared.api.app import lifespan

    return lifespan


async def test_lifespan_flag_off_starts_nothing(monkeypatch, stub_lifespan):
    monkeypatch.delenv(scheduler_module.SCHEDULER_ENV, raising=False)
    async with stub_lifespan(None):
        pass
    assert RecordingScheduler.instances == []


async def test_lifespan_flag_on_starts_and_stops_scheduler(monkeypatch, stub_lifespan):
    monkeypatch.setenv(scheduler_module.SCHEDULER_ENV, "1")
    async with stub_lifespan(None):
        (instance,) = RecordingScheduler.instances
        assert instance.started and not instance.stopped
        from features.query_registry.discovery import query_discovery

        assert instance.coordinator is query_discovery
    assert instance.stopped


async def test_lifespan_monitored_target_starts_without_flag(monkeypatch, stub_lifespan):
    monkeypatch.delenv(scheduler_module.SCHEDULER_ENV, raising=False)
    monkeypatch.setattr(scheduler_module, "monitored_targets", lambda: {"demo"})
    async with stub_lifespan(None):
        (instance,) = RecordingScheduler.instances
        assert instance.started
    assert instance.stopped


@pytest.mark.asyncio
async def test_scheduler_refuses_to_run_without_fencing_store():
    coordinator = FakeCoordinator(store=None)
    scheduler = ObservationScheduler(
        coordinator,
        targets_provider=lambda: ["demo"],
        owner_id="owner-a",
    )

    with pytest.raises(RuntimeError, match="requires cache.db"):
        await scheduler.start()

    assert scheduler._task is None
    assert not coordinator.scheduler_owned


@pytest.mark.asyncio
async def test_scheduler_owned_sse_never_starts_or_stops_a_second_cadence():
    """Flag-on integration: SSE observes scheduler collectors without owning them."""
    from features.query_registry.discovery import (
        QueryDiscoveryCollector,
        QueryDiscoveryCoordinator,
    )

    collector = QueryDiscoveryCollector("demo")
    lease_store = FakeLeaseStore()
    coordinator = QueryDiscoveryCoordinator(
        collector_factory=lambda target: collector,
        store_factory=lambda: lease_store,
    )
    scheduler = ObservationScheduler(
        coordinator,
        targets_provider=lambda: [],
        owner_id="owner-a",
    )

    await scheduler.start()
    try:
        assert coordinator.collector_for("demo").scheduler_owned
        sentinel_service = object()
        collector._service = sentinel_service

        subscription = collector.subscribe()
        assert (await anext(subscription)).event == "discovery_snapshot"
        assert collector.task is None

        await subscription.aclose()
        assert collector.task is None
        assert collector._service is sentinel_service
    finally:
        await scheduler.stop()

    assert not collector.scheduler_owned


@pytest.mark.asyncio
async def test_first_subscriber_accelerates_background_target_then_unsubscribe_restores_it():
    """Subscriber edges change scheduler cadence without giving SSE ownership."""
    from features.query_registry.discovery import (
        QueryDiscoveryCollector,
        QueryDiscoveryCoordinator,
    )

    clock = FakeClock()
    lease_store = FakeLeaseStore()
    collector = QueryDiscoveryCollector("demo")
    collection_started = asyncio.Event()
    release_collection = asyncio.Event()

    async def gated_collect():
        collection_started.set()
        await release_collection.wait()
        return SimpleNamespace(event="discovery_update", data={})

    collector.collect_now = gated_collect
    coordinator = QueryDiscoveryCoordinator(
        collector_factory=lambda target: collector,
        store_factory=lambda: lease_store,
    )
    scheduler = ObservationScheduler(
        coordinator,
        targets_provider=lambda: ["demo"],
        clock=clock.time,
        owner_id="owner-a",
    )
    assert coordinator.collector_for("demo") is collector

    await scheduler.start()
    try:
        schedule = scheduler._schedules["demo"]
        original_background_due = schedule.next_due
        assert clock.now < original_background_due <= clock.now + BACKGROUND_INTERVAL_SECONDS

        sentinel_service = object()
        collector._service = sentinel_service
        subscription = collector.subscribe()
        assert (await anext(subscription)).event == "discovery_snapshot"
        await asyncio.wait_for(collection_started.wait(), timeout=1)

        collection_task = schedule.task
        assert collection_task is not None
        assert schedule.next_due == clock.now
        # Only the scheduler task exists; subscribe never starts collector._run.
        assert collector.task is None

        await subscription.aclose()
        assert not coordinator.has_subscribers("demo")
        assert schedule.task is collection_task
        assert collector.task is None
        assert collector._service is sentinel_service

        release_collection.set()
        await collection_task

        assert schedule.task is None
        assert clock.now < schedule.next_due <= clock.now + BACKGROUND_INTERVAL_SECONDS
        assert schedule.next_due % BACKGROUND_INTERVAL_SECONDS == (
            _stable_hash("demo") % int(BACKGROUND_INTERVAL_SECONDS)
        )
    finally:
        release_collection.set()
        await scheduler.stop()


# -- import-graph guard -------------------------------------------------------


def _run_import_probe(code: str, home: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=RDST_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
    )
    assert result.returncode == 0, result.stderr


def test_cli_import_graph_excludes_scheduler(tmp_path):
    """Mechanical proof (research Q5): no CLI entry point's import graph
    reaches the scheduler or the web app factory, and importing them starts
    no event loop."""
    cli_modules = sorted(
        ".".join(path.relative_to(RDST_ROOT).with_suffix("").parts)
        for path in (RDST_ROOT / "features").glob("*/cli/command.py")
    ) + ["shared.cli.rdst_cli", "rdst"]
    code = "\n".join(
        [
            "import asyncio, importlib, sys",
            f"for name in {cli_modules!r}:",
            "    importlib.import_module(name)",
            "assert 'features.query_registry.scheduler' not in sys.modules, 'scheduler is on a CLI import graph'",
            "assert 'shared.api.app' not in sys.modules, 'web app factory is on a CLI import graph'",
            "assert asyncio.events._get_running_loop() is None",
        ]
    )
    _run_import_probe(code, tmp_path)


def test_scheduler_import_is_side_effect_free(tmp_path):
    code = "\n".join(
        [
            "import asyncio",
            "import features.query_registry.scheduler",
            "assert asyncio.events._get_running_loop() is None",
        ]
    )
    _run_import_probe(code, tmp_path)
    assert not (tmp_path / ".rdst" / "cache.db").exists()


# -- scheduling math ----------------------------------------------------------


def test_phase_offset_is_stable_per_target():
    scheduler, _, _ = make_scheduler([])
    interval = BACKGROUND_INTERVAL_SECONDS
    offset = _stable_hash("alpha") % int(interval)
    first = scheduler._aligned_next("alpha", 1_000.0, interval)
    second = scheduler._aligned_next("alpha", 987_654.0, interval)
    assert first % interval == offset
    assert second % interval == offset
    assert 1_000.0 < first <= 1_000.0 + interval
    assert 987_654.0 < second <= 987_654.0 + interval


def test_duty_cycle_ceiling_stretches_and_clamps():
    scheduler, _, _ = make_scheduler([])
    assert scheduler._next_interval(60.0, 0.2) == 60.0
    assert scheduler._next_interval(60.0, 30.0) == 600.0
    assert scheduler._next_interval(60.0, 1_000.0) == MAX_INTERVAL_SECONDS


def test_cadence_class_follows_subscribers():
    scheduler, coordinator, _ = make_scheduler(["demo", "other"])
    coordinator.subscribed = {"demo"}
    assert scheduler._base_interval("demo") == ACTIVE_INTERVAL_SECONDS
    assert scheduler._base_interval("other") == BACKGROUND_INTERVAL_SECONDS


async def test_slow_collection_backs_off_next_due():
    scheduler, coordinator, clock = make_scheduler(["demo"])
    collector = coordinator.collector_for("demo")
    collector.duration = 30.0
    schedule = scheduler._schedules["demo"]

    await run_tick(scheduler, schedule, clock)

    assert collector.collects == 1
    assert schedule.last_duration_s == 30.0
    # base 300s stretched to 30 / 0.05 = 600s, aligned with the phase offset.
    stretched = 600.0
    assert schedule.next_due % stretched == _stable_hash("demo") % int(stretched)
    assert clock.now < schedule.next_due <= clock.now + stretched


async def test_local_library_work_does_not_charge_database_duty():
    scheduler, coordinator, clock = make_scheduler(["demo"])
    scheduler._monitored.add("demo")
    collector = coordinator.collector_for("demo")
    collector.duration = 30.0
    collector.db_touch_ms = 40.0
    schedule = scheduler._schedules["demo"]

    await run_tick(scheduler, schedule, clock)

    assert schedule.last_duration_s == pytest.approx(0.04)
    assert schedule.next_due - clock.now <= ACTIVE_INTERVAL_SECONDS


async def test_failure_backoff_is_shared_and_retains_degraded_class():
    jitter = 0.5
    scheduler, coordinator, clock = make_scheduler(["demo"], rand=lambda: jitter)
    collector = coordinator.collector_for("demo")
    collector.fail_with = RuntimeError("boom")
    schedule = scheduler._schedules["demo"]

    await run_tick(scheduler, schedule, clock)
    assert schedule.consecutive_failures == 1
    assert schedule.next_due == clock.now + BACKOFF_BASE_SECONDS
    assert clock.now < schedule.next_due <= clock.now + BACKOFF_CAP_SECONDS

    await run_tick(scheduler, schedule, clock)
    assert schedule.consecutive_failures == 2
    assert schedule.next_due == clock.now + BACKOFF_BASE_SECONDS * 2

    await run_tick(scheduler, schedule, clock)
    assert schedule.consecutive_failures == DEGRADED_AFTER_FAILURES
    assert scheduler._base_interval("demo", schedule) == DEGRADED_INTERVAL_SECONDS
    assert schedule.next_due == clock.now + BACKOFF_BASE_SECONDS * 4

    collector.fail_with = None
    await run_tick(scheduler, schedule, clock)
    assert schedule.consecutive_failures == 0
    assert scheduler._base_interval("demo", schedule) == BACKGROUND_INTERVAL_SECONDS


# -- single-flight and the global cap ------------------------------------------


async def test_single_flight_per_target():
    scheduler, coordinator, clock = make_scheduler(["demo"])
    collector = coordinator.collector_for("demo")
    collector.gate = asyncio.Event()
    schedule = scheduler._schedules["demo"]
    schedule.next_due = clock.now

    scheduler._launch_due(clock.now)
    task = schedule.task
    assert task is not None
    await asyncio.sleep(0)
    scheduler._launch_due(clock.now)
    assert schedule.task is task

    collector.gate.set()
    await task
    assert collector.collects == 1


async def test_global_concurrency_cap():
    tracker = SimpleNamespace(active=0, max_active=0, gate=asyncio.Event())

    class GatedCollector(FakeCollector):
        async def collect_now(self):
            tracker.active += 1
            tracker.max_active = max(tracker.max_active, tracker.active)
            try:
                await tracker.gate.wait()
            finally:
                tracker.active -= 1
            return SimpleNamespace(event="discovery_update", data={})

    targets = [f"t{i}" for i in range(5)]
    scheduler, coordinator, clock = make_scheduler(targets)
    for name in targets:
        coordinator.collectors[name] = GatedCollector()
        scheduler._schedules[name].next_due = clock.now

    scheduler._launch_due(clock.now)
    tasks = [scheduler._schedules[name].task for name in targets
             if scheduler._schedules[name].task is not None]
    for _ in range(10):
        await asyncio.sleep(0)
    assert tracker.active == GLOBAL_CONCURRENCY

    tracker.gate.set()
    await asyncio.gather(*tasks)
    scheduler._launch_due(clock.now)
    await asyncio.gather(*(s.task for s in scheduler._schedules.values() if s.task is not None))
    assert tracker.max_active == GLOBAL_CONCURRENCY


# -- leases ---------------------------------------------------------------------


async def test_unacquirable_lease_skips_tick_without_collecting():
    store = FakeLeaseStore()
    store.deny.add("demo")
    scheduler, coordinator, clock = make_scheduler(["demo"], store=store)
    schedule = scheduler._schedules["demo"]

    await run_tick(scheduler, schedule, clock)

    assert coordinator.collectors == {}
    assert store.acquires[0][:3] == ("demo", "owner-a", LEASE_TTL_SECONDS)
    assert schedule.consecutive_failures == 0
    assert schedule.next_due > clock.now


async def test_fence_set_per_tick_with_renewed_token():
    store = FakeLeaseStore()
    scheduler, coordinator, clock = make_scheduler(["demo"], store=store)
    schedule = scheduler._schedules["demo"]

    await run_tick(scheduler, schedule, clock)
    await run_tick(scheduler, schedule, clock)

    collector = coordinator.collectors["demo"]
    assert collector.fences == [("owner-a", 1), ("owner-a", 2)]
    assert collector.clear_calls == 2
    assert collector.collects == 2


async def test_lease_lost_mid_cycle_is_failed_tick_without_release():
    store = FakeLeaseStore()
    scheduler, coordinator, clock = make_scheduler(["demo"], store=store)
    collector = coordinator.collector_for("demo")
    collector.fail_with = LeaseLostError("lease moved")
    schedule = scheduler._schedules["demo"]

    await run_tick(scheduler, schedule, clock)

    assert schedule.consecutive_failures == 1
    assert store.releases == []
    assert "demo" not in scheduler._leased
    assert collector.clear_calls == 1


def test_library_commit_is_ordered_before_sleep_wake_lease_takeover(
    tmp_path, monkeypatch
):
    """A resumed stale holder never commits library.db after a new lease owner.

    The first write deliberately stalls after its fence check. A takeover on
    another cache.db connection must remain blocked until that already-fenced
    library commit finishes. Once the takeover gets a newer token, another
    write from the old process is rejected and never reaches library.db.
    """
    cache_path = tmp_path / "cache.db"
    old_store = ObservationStore(cache_path)
    new_store = ObservationStore(cache_path)
    registry_path = tmp_path / "queries.toml"
    try:
        old_token = old_store.acquire_lease("demo", "old", ttl_s=10, now=100)
        assert old_token == 1

        registry = QueryRegistry(registry_path=str(registry_path))
        registry.load()
        registry.set_write_guard(
            lambda: old_store.write_fence(
                "demo",
                owner_id="old",
                fencing_token=old_token,
                now=105,
            )
        )

        inside_guard = threading.Event()
        release_old_write = threading.Event()
        takeover_started = threading.Event()
        takeover_finished = threading.Event()
        errors: list[BaseException] = []
        takeover_token: list[int | None] = []
        original_apply = registry._store.apply_changes

        def delayed_apply(*args, **kwargs):
            inside_guard.set()
            if not release_old_write.wait(timeout=2):
                raise AssertionError("test did not release the fenced writer")
            return original_apply(*args, **kwargs)

        monkeypatch.setattr(registry._store, "apply_changes", delayed_apply)

        def old_write() -> None:
            try:
                registry.add_query("SELECT * FROM users", target="demo")
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        def takeover() -> None:
            try:
                takeover_started.set()
                takeover_token.append(
                    new_store.acquire_lease("demo", "new", ttl_s=10, now=111)
                )
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                takeover_finished.set()

        old_thread = threading.Thread(target=old_write)
        takeover_thread = threading.Thread(target=takeover)
        old_thread.start()
        assert inside_guard.wait(timeout=2)
        takeover_thread.start()
        assert takeover_started.wait(timeout=2)

        # The cache.db writer lock held by write_fence orders takeover after
        # the independent library.db commit.
        assert not takeover_finished.wait(timeout=0.1)
        release_old_write.set()
        old_thread.join(timeout=2)
        takeover_thread.join(timeout=2)

        assert not old_thread.is_alive()
        assert not takeover_thread.is_alive()
        assert errors == []
        assert takeover_token == [2]

        # The old token is now stale. Its next mutation fails at guard entry,
        # before the library transaction starts.
        with pytest.raises(LeaseLostError):
            registry.add_query("SELECT * FROM orders", target="demo")

        reloaded = QueryRegistry(registry_path=str(registry_path))
        reloaded.load()
        assert [entry.original_sql for entry in reloaded.list_queries()] == [
            "SELECT * FROM users"
        ]
    finally:
        new_store.close()
        old_store.close()


async def test_stop_releases_owned_leases():
    store = FakeLeaseStore()
    scheduler, _, clock = make_scheduler(["a", "b"], store=store)
    for name in ("a", "b"):
        await run_tick(scheduler, scheduler._schedules[name], clock)
    assert scheduler._leased == {"a", "b"}

    await scheduler.stop()

    assert sorted(store.releases) == [("a", "owner-a"), ("b", "owner-a")]
    assert scheduler._leased == set()


async def test_stop_cancels_in_flight_collections():
    store = FakeLeaseStore()
    scheduler, coordinator, clock = make_scheduler(["demo"], store=store)
    collector = coordinator.collector_for("demo")
    collector.gate = asyncio.Event()
    schedule = scheduler._schedules["demo"]
    schedule.next_due = clock.now
    scheduler._launch_due(clock.now)
    await asyncio.sleep(0)
    assert schedule.task is not None

    await scheduler.stop()

    assert schedule.task is None
    assert ("demo", "owner-a") in store.releases


# -- target enumeration -----------------------------------------------------------


async def test_target_removal_cancels_schedule_and_releases_lease():
    store = FakeLeaseStore()
    targets = ["demo"]
    clock = FakeClock()
    coordinator = FakeCoordinator(store=store, clock=clock)
    scheduler = ObservationScheduler(
        coordinator,
        targets_provider=lambda: list(targets),
        clock=clock.time,
        rand=lambda: 0.5,
        owner_id="owner-a",
    )
    scheduler._store = store
    scheduler._refresh_targets(clock.now)
    collector = coordinator.collector_for("demo")
    collector.gate = asyncio.Event()
    schedule = scheduler._schedules["demo"]
    schedule.next_due = clock.now
    scheduler._launch_due(clock.now)
    task = schedule.task
    await asyncio.sleep(0)
    assert "demo" in scheduler._leased

    targets.clear()
    scheduler._refresh_targets(clock.now)

    await asyncio.gather(task, return_exceptions=True)
    assert "demo" not in scheduler._schedules
    assert ("demo", "owner-a") in store.releases
    assert "demo" not in scheduler._leased


async def test_target_added_on_refresh_gets_aligned_slot():
    targets = ["a"]
    scheduler, _, clock = make_scheduler(targets)
    targets.append("b")
    scheduler._refresh_targets(clock.now)
    schedule = scheduler._schedules["b"]
    interval = BACKGROUND_INTERVAL_SECONDS
    assert schedule.next_due % interval == _stable_hash("b") % int(interval)
    assert clock.now < schedule.next_due <= clock.now + interval


async def test_collection_outcomes_reach_the_listener_and_recovery_collects_soon():
    scheduler, coordinator, clock = make_scheduler(["demo"])
    seen = []
    scheduler.set_outcome_listener(lambda *args: seen.append(args))
    collector = coordinator.collector_for("demo")
    collector.fail_with = RuntimeError("connection refused")
    schedule = scheduler._schedules["demo"]

    await run_tick(scheduler, schedule, clock)
    collector.fail_with = None
    await run_tick(scheduler, schedule, clock)
    assert [(name, ok, error) for name, ok, _, error in seen] == [
        ("demo", False, "connection refused"), ("demo", True, None)]

    schedule.next_due = clock.now + DEGRADED_INTERVAL_SECONDS
    scheduler.collect_soon("demo")
    assert schedule.next_due == clock.now


async def test_scheduler_handoff_deadline_preserves_previous_reader(monkeypatch):
    scheduler, coordinator, clock = make_scheduler(["demo"], store=FakeLeaseStore())
    collector = coordinator.collector_for("demo")
    gate = asyncio.Event()
    collector.task = asyncio.create_task(gate.wait())
    monkeypatch.setattr(scheduler_module, "COLLECTION_DEADLINE_SECONDS", 0)
    schedule = scheduler._schedules["demo"]
    try:
        await asyncio.wait_for(run_tick(scheduler, schedule, clock), 2)
        assert collector.collects == 0
        assert collector.fences == []
        assert not collector.task.done()
        assert schedule.task is None
        assert schedule.error == "Waiting for the previous reader to finish"
        assert schedule.next_due > clock.now
        gate.set()
        await asyncio.wait_for(collector.task, 2)
        monkeypatch.setattr(scheduler_module, "COLLECTION_DEADLINE_SECONDS", 120)
        await asyncio.wait_for(run_tick(scheduler, schedule, clock), 2)
        assert collector.collects == 1
    finally:
        gate.set()
        await asyncio.wait_for(collector.task, 2)
        await asyncio.wait_for(scheduler.stop(), 2)


@pytest.mark.parametrize("interval", [60.0, 3.0])
async def test_monitored_connection_recovers_on_next_reading(tmp_path, monkeypatch, interval):
    monkeypatch.setattr(scheduler_module, "ACTIVE_INTERVAL_SECONDS", interval)
    scheduler, coordinator, clock = make_scheduler(["demo"])
    scheduler._monitored.add("demo")
    store = MonitorStore(tmp_path / "monitor.db", clock=clock.time)
    store.set_settings("demo", {"enabled": True})
    watch = ConnectionWatch(store, clock=clock.time)
    scheduler.set_outcome_listener(watch.collection)
    collector = coordinator.collector_for("demo")
    collector.fail_with = ConnectionError("connection refused")
    schedule = scheduler._schedules["demo"]
    first_failure = None
    for _ in range(6):
        await asyncio.wait_for(run_tick(scheduler, schedule, clock), 2)
        if first_failure is None:
            first_failure = clock.now
        filed = store.read("SELECT state FROM inbox_item WHERE subject='lost_connection'")
        assert bool(filed) == (schedule.consecutive_failures >= LOST_AFTER_FAILURES)
        if schedule.consecutive_failures == LOST_AFTER_FAILURES:
            assert clock.now - first_failure <= (LOST_AFTER_FAILURES - 1) * interval
        assert 0 < schedule.next_due - clock.now <= interval
        assert 0 < scheduler._backoff.retry_after("demo") <= interval
        assert not scheduler._backoff.acquire("demo")
    query = "SELECT id, state FROM inbox_item WHERE subject='lost_connection'"
    lost = store.read(query)[0]
    assert lost["state"] != "resolved"

    # The index scan shares admission and can fail between scheduled readings.
    clock.now = schedule.next_due
    assert scheduler._backoff.acquire("demo")
    scheduler._backoff.finish("demo", success=False)
    assert scheduler._backoff.retry_after("demo") > interval
    collector.fail_with = None
    restored = clock.now
    await asyncio.wait_for(run_tick(scheduler, schedule, clock), 2)
    clock.advance(interval)
    await asyncio.wait_for(run_tick(scheduler, schedule, clock), 2)
    assert clock.now - restored <= interval
    assert store.read(query) == [{"id": lost["id"], "state": "resolved"}]
    assert not scheduler._backoff.failed("demo")
    await scheduler.stop()


@pytest.mark.parametrize("recovered", [True, False])
async def test_timed_out_collection_reports_one_check_and_preserves_recovery(
    tmp_path, monkeypatch, recovered,
):
    scheduler, coordinator, clock = make_scheduler(["demo"])
    scheduler._monitored.add("demo")
    store = MonitorStore(tmp_path / "monitor.db", clock=clock.time)
    store.set_settings("demo", {"enabled": True})
    watch = ConnectionWatch(store, clock=clock.time)
    failed = asyncio.Event()
    def outcome(name, ok, ts, error):
        watch.collection(name, ok, ts, error)
        if not ok:
            failed.set()
    scheduler.set_outcome_listener(outcome)
    collector = coordinator.collector_for("demo")
    collector.gate = asyncio.Event()
    collect = collector.collect_now
    async def reading():
        event = await collect()
        if not recovered:
            event.event = "discovery_error"
            event.data = {"error": "connection refused"}
        return event
    monkeypatch.setattr(collector, "collect_now", reading)
    monkeypatch.setattr(scheduler_module, "COLLECTION_DEADLINE_SECONDS", 0)
    schedule = scheduler._schedules["demo"]
    task = asyncio.create_task(run_tick(scheduler, schedule, clock))
    try:
        await asyncio.wait_for(failed.wait(), 2)
        clock.advance(60)
        collector.gate.set()
        await asyncio.wait_for(task, 2)
        assert schedule.latest_attempt_ok == recovered
        assert schedule.consecutive_failures == (0 if recovered else 1)
        clock.advance(120 if recovered else 59)
        watch.tick()
        assert store.inbox() == []
    finally:
        collector.gate.set()
        await scheduler.stop()


class FrozenCollector(FakeCollector):
    """Reads like the real collector: each answered reading records an ok collect pulse. While
    frozen, a read stays in flight until the server thaws or the connection is aborted, and a
    liveness probe gets no answer unless the server is only slow."""

    def __init__(self, clock: FakeClock, store: MonitorStore) -> None:
        super().__init__(clock)
        self.store = store
        self.frozen = False
        self.slow = False
        self.probes: list[float] = []
        self.thawed = asyncio.Event()
        self.aborts = 0
        self._aborted = asyncio.Event()
        self._read_started: float | None = None

    def unanswered_for(self) -> float | None:
        return None if self._read_started is None else self.clock.now - self._read_started

    def abort_connection(self) -> None:
        self.aborts += 1
        self._aborted.set()

    def probe_answering(self, seconds: float) -> None:
        self.probes.append(self.clock.now)
        if not self.slow:
            raise TimeoutError(f"The database didn't answer within {seconds:g} s.")

    async def collect_now(self):
        self.collects += 1
        self._aborted = asyncio.Event()
        self._read_started = self.clock.now
        try:
            if self.frozen:
                answered = asyncio.ensure_future(self.thawed.wait())
                aborted = asyncio.ensure_future(self._aborted.wait())
                await asyncio.wait({answered, aborted}, return_when=asyncio.FIRST_COMPLETED)
                answered.cancel()
                aborted.cancel()
                if not self.thawed.is_set():
                    raise ConnectionError("server closed the connection unexpectedly")
        finally:
            self._read_started = None
        self.store.record_pulse("demo", int(self.clock.now), "collect", True, "Read 1 query")
        return SimpleNamespace(event="discovery_update", data={"stats": {}})


def _frozen_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler_module, "TICK_SECONDS", 0.01)
    scheduler, coordinator, clock = make_scheduler(["demo"])
    scheduler._monitored.add("demo")
    store = MonitorStore(tmp_path / "monitor.db", clock=clock.time)
    store.set_settings("demo", {"enabled": True})
    scheduler.set_outcome_listener(ConnectionWatch(store, clock=clock.time).collection)
    collector = FrozenCollector(clock, store)
    coordinator.collectors["demo"] = collector
    return scheduler, collector, clock, store


def _pulse_state(store: MonitorStore, clock: FakeClock) -> str:
    from features.monitor.api import views

    now = int(clock.now)
    state = monitor_routes._pulse_state(store, "demo", now,
                                        monitor_routes._collect_timing(store, "demo")[0])
    home = views.home(store, now, configured=[{"name": "demo"}])["databases"][0]["state"]
    return f"{state}/{home}"


async def test_frozen_database_reads_as_unreachable_within_a_reading_interval(
        tmp_path, monkeypatch):
    from features.monitor import database_limit
    from features.monitor.api import views

    monkeypatch.setattr(views, "registry_entries", lambda keys=None: {})
    monkeypatch.setattr(database_limit, "_signed_in", lambda: True)
    scheduler, collector, clock, store = _frozen_setup(tmp_path, monkeypatch)
    schedule = scheduler._schedules["demo"]
    try:
        await asyncio.wait_for(run_tick(scheduler, schedule, clock), 2)
        assert _pulse_state(store, clock) == "live/watching"

        # Frozen right after a reading: the worst case waits a whole interval for the next one.
        frozen_at = clock.now
        collector.frozen = True
        clock.now = schedule.next_due
        assert clock.now - frozen_at <= ACTIVE_INTERVAL_SECONDS
        reading_started = clock.now
        scheduler._launch_due(clock.now)
        task = schedule.task
        await asyncio.sleep(0.03)
        while views.failing_since(store, "demo") is None:
            # Reported within about one reading interval of the freeze.
            assert clock.now - frozen_at <= 75
            clock.advance(1)
            await asyncio.sleep(0.03)
        assert views.failing_since(store, "demo") == reading_started
        assert _pulse_state(store, clock) == "connection_lost/cant_reach"
        assert "didn't answer" in store.pulses("demo", 1)[0]["detail"]
        # The collection keeps waiting, and the outage stays visible while the server is frozen.
        clock.advance(30)
        await asyncio.sleep(0.05)
        assert not task.done()
        assert _pulse_state(store, clock) == "connection_lost/cant_reach"
        assert views.home(store, int(clock.now), configured=[{"name": "demo"}])[
            "databases"][0]["state_since"] == reading_started

        # The first answer after the freeze clears it.
        collector.thawed.set()
        await asyncio.wait_for(task, 2)
        assert views.failing_since(store, "demo") is None
        assert _pulse_state(store, clock) == "live/watching"
        assert schedule.error is None and schedule.consecutive_failures == 0
    finally:
        collector.thawed.set()
        await scheduler.stop()


async def test_slow_read_on_a_database_that_answers_probes_is_not_reported(
        tmp_path, monkeypatch):
    from features.monitor import database_limit
    from features.monitor.api import views
    from features.query_registry.scheduler import (NOT_ANSWERING_SECONDS,
                                                   PROBE_INTERVAL_SECONDS)

    monkeypatch.setattr(views, "registry_entries", lambda keys=None: {})
    monkeypatch.setattr(database_limit, "_signed_in", lambda: True)
    scheduler, collector, clock, store = _frozen_setup(tmp_path, monkeypatch)
    schedule = scheduler._schedules["demo"]
    try:
        await asyncio.wait_for(run_tick(scheduler, schedule, clock), 2)
        # A busy server: the read takes far longer than usual but the server still answers.
        collector.frozen = collector.slow = True
        clock.now = schedule.next_due
        reading_started = clock.now
        scheduler._launch_due(clock.now)
        task = schedule.task
        while clock.now - reading_started < 100:
            clock.advance(1)
            await asyncio.sleep(0.02)
            assert views.failing_since(store, "demo") is None
            # A slow reading can read as late, never as a lost connection.
            assert _pulse_state(store, clock) in ("live/watching", "late/watching")
        assert not task.done() and schedule.error is None
        assert NOT_ANSWERING_SECONDS <= collector.probes[0] - reading_started <= \
            NOT_ANSWERING_SECONDS + 2
        gaps = [later - earlier for earlier, later in zip(collector.probes, collector.probes[1:])]
        assert gaps and PROBE_INTERVAL_SECONDS <= min(gaps) <= max(gaps) <= \
            PROBE_INTERVAL_SECONDS + 2
        collector.thawed.set()
        await asyncio.wait_for(task, 2)
        assert schedule.latest_attempt_ok and schedule.consecutive_failures == 0
        assert store.pulses("demo", 1)[0]["detail"] == "Read 1 query"
    finally:
        collector.thawed.set()
        await scheduler.stop()


async def test_collection_deadline_reports_each_timeout_and_retries_within_an_interval(
        tmp_path, monkeypatch):
    from features.monitor.api import views

    monkeypatch.setattr(scheduler_module, "COLLECTION_DEADLINE_SECONDS", 0.05)
    scheduler, collector, clock, store = _frozen_setup(tmp_path, monkeypatch)
    schedule = scheduler._schedules["demo"]
    try:
        await asyncio.wait_for(run_tick(scheduler, schedule, clock), 2)
        collector.frozen = True
        first_failure = None
        for attempt in range(1, 5):
            await asyncio.wait_for(run_tick(scheduler, schedule, clock), 5)
            pulse = store.pulses("demo", 1)[0]
            assert (pulse["ok"], pulse["detail"]) == (
                0, "The database didn't answer within 0.05 s.")
            assert collector.aborts == attempt
            assert schedule.consecutive_failures == attempt
            first_failure = first_failure or pulse["ts"]
            assert views.failing_since(store, "demo") == first_failure
            # Backoff never pushes the next check past one reading interval.
            assert 0 < schedule.next_due - clock.now <= ACTIVE_INTERVAL_SECONDS
            assert scheduler._backoff.retry_after("demo") <= ACTIVE_INTERVAL_SECONDS
        collector.thawed.set()
        await asyncio.wait_for(run_tick(scheduler, schedule, clock), 2)
        assert views.failing_since(store, "demo") is None
    finally:
        collector.thawed.set()
        await scheduler.stop()


async def test_monitored_reading_goes_ahead_of_a_reader_stuck_on_the_database(monkeypatch):
    from features.query_registry.scheduler import ADMISSION_WAIT_SECONDS

    scheduler, coordinator, clock = make_scheduler(["demo", "other"])
    scheduler._monitored.add("demo")
    # Another reader (a parameter sample, an index scan) holds the database and never returns.
    for name in ("demo", "other"):
        assert scheduler._backoff.acquire(name)
    for name in ("demo", "other"):
        schedule = scheduler._schedules[name]
        await run_tick(scheduler, schedule, clock)
        assert coordinator.collector_for(name).collects == 0
    clock.advance(ADMISSION_WAIT_SECONDS)
    for name in ("demo", "other"):
        await run_tick(scheduler, scheduler._schedules[name], clock)
    assert coordinator.collector_for("demo").collects == 1
    assert coordinator.collector_for("other").collects == 0
    # The stuck reader keeps its admission, whatever the reading's outcome.
    assert scheduler._backoff.busy("demo")
    coordinator.collector_for("demo").fail_with = ConnectionError("timeout expired")
    for _ in range(2):
        await run_tick(scheduler, scheduler._schedules["demo"], clock)
        clock.advance(ADMISSION_WAIT_SECONDS)
    assert coordinator.collector_for("demo").collects == 2
    assert scheduler._backoff._states["demo"].active
    assert scheduler._backoff.failed("demo")
    await scheduler.stop()


async def test_timed_out_collection_stays_in_flight_until_its_work_returns(monkeypatch):
    from features.query_registry.scheduler import ADMISSION_WAIT_SECONDS

    monkeypatch.setattr(scheduler_module, "COLLECTION_DEADLINE_SECONDS", 0.02)
    monkeypatch.setattr(scheduler_module, "ABORT_GRACE_SECONDS", 0.02)
    scheduler, coordinator, clock = make_scheduler(["demo"])
    scheduler._monitored.add("demo")
    collector = coordinator.collector_for("demo")
    # Aborting can't unblock the read: it keeps the collector's connection and state.
    collector.gate = asyncio.Event()
    try:
        result = await asyncio.wait_for(scheduler.check_now("demo"), 2)
        assert result == {"ok": False, "detail": "The database didn't answer within 0.02 s."}
        schedule = scheduler._schedules["demo"]
        assert schedule.task is None and collector.collects == 1
        assert scheduler._backoff._states["demo"].active
        assert scheduler._backoff.failed("demo")
        # Later ticks, a manual check, and a reader waiting out a busy holder never read again
        # on the same collector while the first collection runs.
        for _ in range(4):
            clock.advance(ACTIVE_INTERVAL_SECONDS + 1)
            scheduler._launch_due(clock.now)
            assert schedule.task is None
            result = await asyncio.wait_for(scheduler.check_now("demo"), 1)
            assert result["ok"] is False and "didn't answer" in result["detail"]
            await scheduler._collect_target("demo", schedule)
            clock.advance(ADMISSION_WAIT_SECONDS)
            await scheduler._collect_target("demo", schedule)
        assert collector.collects == 1
        scheduler._remove_target("demo")
        scheduler._refresh_targets(clock.now)
        schedule = scheduler._schedules["demo"]
        schedule.next_due = clock.now
        scheduler._launch_due(clock.now)
        assert schedule.task is None and collector.collects == 1

        collector.gate.set()
        for _ in range(5):
            await asyncio.sleep(0)
        assert "demo" not in scheduler._running
        assert not scheduler._backoff.busy("demo")
        await asyncio.wait_for(run_tick(scheduler, schedule, clock), 2)
        assert collector.collects == 2 and schedule.latest_attempt_ok
    finally:
        collector.gate.set()
        await scheduler.stop()


def test_a_reading_after_a_failed_check_records_that_the_database_answered(tmp_path):
    clock = FakeClock()
    store = MonitorStore(tmp_path / "monitor.db", clock=clock.time)
    store.set_settings("demo", {"enabled": True})
    watch = ConnectionWatch(store, clock=clock.time)
    store.record_pulse("demo", int(clock.now), "collect", True, "Read 1 query")
    watch.collection("demo", False, int(clock.now) - 20, "The database didn't answer within 12 s.")
    watch.collection("demo", True, int(clock.now))
    assert [(row["ok"], row["detail"]) for row in store.pulses("demo", 3)] == [
        (1, "The database answered"), (0, "The database didn't answer within 12 s."),
        (1, "Read 1 query")]
    watch.collection("demo", True, int(clock.now) + 60)
    assert len(store.pulses("demo", 10)) == 3


def test_answered_pulse_clears_the_failure_without_counting_as_a_reading(tmp_path):
    from features.monitor.api import views

    clock = FakeClock()
    now = int(clock.now)
    store = MonitorStore(tmp_path / "monitor.db", clock=clock.time)
    store.set_settings("demo", {"enabled": True})
    watch = ConnectionWatch(store, clock=clock.time)
    for ts in (now - 60, now):
        store.record_pulse("demo", ts, "collect", True, "Read 1 query")
    # The next reading's read went unanswered, then the collection returned without a reading.
    watch.collection("demo", False, now + 60, "The database didn't answer within 12 s.")
    assert views.failing_since(store, "demo") == now + 60
    watch.collection("demo", True, now + 75)
    assert views.failing_since(store, "demo") is None
    assert views.collect_timing(store, "demo") == (now, 60)
    last, interval = monitor_routes._collect_timing(store, "demo")
    assert (last["ts"], interval) == (now + 60, 60)
    assert monitor_routes._readings(store, "demo")["last_reading_at"] == now
    assert watch._last_reading("demo", now + 100) == now


# -- first-run fast phase ----------------------------------------------------


def _monitored_scheduler(monitored: set[str]):
    clock = FakeClock()
    coordinator = FakeCoordinator(store=FakeLeaseStore(), clock=clock)
    scheduler = ObservationScheduler(
        coordinator, targets_provider=lambda: ["orders", "other"], clock=clock.time,
        rand=lambda: 0.5, owner_id="owner-a", monitored_provider=lambda: set(monitored))
    scheduler._store = coordinator.store()
    scheduler._refresh_targets(clock.now)
    return scheduler, coordinator, clock


async def test_fast_phase_reads_now_then_every_ten_seconds_for_five_minutes():
    from features.query_registry.scheduler import (FIRST_RUN_FAST_SECONDS,
                                                   FIRST_RUN_INTERVAL_SECONDS)

    monitored: set[str] = set()
    scheduler, coordinator, clock = _monitored_scheduler(monitored)
    heard: list[tuple[str, bool]] = []
    monitored.add("orders")
    assert scheduler.start_fast_phase("orders", lambda name, first: heard.append((name, first)))
    schedule = scheduler._schedules["orders"]
    # The first reading launches right away, without waiting for the next slot.
    assert schedule.task is not None
    await schedule.task
    assert coordinator.collectors["orders"].collects == 1
    assert heard == [("orders", True)]
    assert scheduler.in_fast_phase("orders")
    assert schedule.next_due - clock.now <= FIRST_RUN_INTERVAL_SECONDS
    assert scheduler._base_interval("orders", schedule) == FIRST_RUN_INTERVAL_SECONDS

    clock.advance(20)
    await run_tick(scheduler, schedule, clock)
    assert heard[-1] == ("orders", False)

    clock.advance(FIRST_RUN_FAST_SECONDS)
    assert not scheduler.in_fast_phase("orders")
    assert scheduler._base_interval("orders", schedule) == ACTIVE_INTERVAL_SECONDS
    await run_tick(scheduler, schedule, clock)
    assert schedule.fast_until == 0.0
    assert len(heard) == 2
    assert schedule.next_due - clock.now > FIRST_RUN_INTERVAL_SECONDS
    # Other targets keep their own cadence.
    assert scheduler._base_interval("other", scheduler._schedules["other"]) == \
        BACKGROUND_INTERVAL_SECONDS


async def test_fast_phase_respects_the_duty_cycle_ceiling():
    scheduler, coordinator, clock = _monitored_scheduler({"orders"})
    coordinator.collector_for("orders").db_touch_ms = 2000
    assert scheduler.start_fast_phase("orders")
    schedule = scheduler._schedules["orders"]
    await schedule.task
    # 2 s of database time at a 5% ceiling stretches the 10 s cadence to 40 s.
    assert schedule.last_duration_s == 2.0
    assert scheduler._next_interval(
        scheduler._base_interval("orders", schedule), schedule.last_duration_s) == 40


def test_fast_phase_for_an_unknown_target_is_refused():
    scheduler, _, _ = _monitored_scheduler(set())
    assert not scheduler.start_fast_phase("missing")


@pytest.mark.parametrize("mode", ["active", "fast", "backoff", "duty_cycle"])
async def test_api_reports_the_actual_scheduler_slot(mode, monkeypatch, tmp_path):
    scheduler, coordinator, clock = make_scheduler({"orders"})
    coordinator.subscribed.add("orders")
    collector = coordinator.collector_for("orders")
    schedule = scheduler._schedules["orders"]
    if mode == "fast":
        assert scheduler.start_fast_phase("orders")
        await schedule.task
    else:
        if mode == "backoff":
            collector.fail_with = RuntimeError("database unavailable")
        if mode == "duty_cycle":
            collector.db_touch_ms = 6000
        await run_tick(scheduler, schedule, clock)
    interval = {"fast": 10, "duty_cycle": 120}.get(mode, 60)
    expected = (clock.now + 30 if mode == "backoff" else
                scheduler._aligned_next("orders", clock.now, interval))
    assert scheduler.next_reading_at("orders") == expected == schedule.next_due
    store = MonitorStore(tmp_path / "monitor.db", clock=clock.time)
    store.set_settings("orders", {"enabled": True})
    monkeypatch.setattr(monitor_routes, "_store", lambda: store)
    monkeypatch.setattr(monitor_routes, "_now", lambda: int(clock.now))
    monkeypatch.setattr(monitor_routes.views, "registry_entries", lambda keys=None: {})
    app = FastAPI()
    app.state.monitor_scheduler = scheduler
    app.include_router(monitor_routes.router)
    with TestClient(app) as client:
        page = client.get("/targets/orders/pulse").json()
        assert page["pulses"] == []
        assert page["next_reading_at"] == expected
        # Opening a stream can insert an early reading; its gap is not the next interval.
        store.record_pulse("orders", int(clock.now) - 5, "collect", True, "ok")
        store.record_pulse("orders", int(clock.now), "collect", True, "ok")
        page = client.get("/targets/orders/pulse").json()
        assert page["next_reading_at"] == expected
        assert page["pulses"][0]["next_reading_at"] == expected
        assert client.get("/targets/orders/health").json()["next_reading_at"] == expected
        store.set_settings("orders", {"enabled": False})
        page = client.get("/targets/orders/pulse").json()
        assert page["next_reading_at"] is None
        assert page["pulses"][0]["next_reading_at"] is None


def test_next_reading_includes_shared_backoff_and_unknown_targets():
    scheduler, _, clock = make_scheduler({"orders"})
    scheduler.collect_soon("orders")
    scheduler._backoff.record_failure("orders")
    assert scheduler.next_reading_at("orders") == clock.now + 30
    assert scheduler.next_reading_at("missing") is None


def test_configured_targets_skip_monitoring_off_and_resume_when_on(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("shared.config.targets.TargetsConfig", lambda: SimpleNamespace(
        load=lambda: None, list_targets=lambda: ["orders"]))
    store = MonitorStore()
    store.set_settings("orders", {"enabled": False})
    assert scheduler_module._configured_targets() == []
    store.set_settings("orders", {"enabled": True})
    assert scheduler_module._configured_targets() == ["orders"]

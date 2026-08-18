"""Flag-gated observation scheduler: cadence math, leases, and lifecycle."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

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
        return SimpleNamespace(event="discovery_update", data={})


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
    """Force one target due, launch it, and wait for the collection task."""
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


async def test_failure_backoff_uses_full_jitter_then_degraded_class():
    jitter = 0.5
    scheduler, coordinator, clock = make_scheduler(["demo"], rand=lambda: jitter)
    collector = coordinator.collector_for("demo")
    collector.fail_with = RuntimeError("boom")
    schedule = scheduler._schedules["demo"]

    await run_tick(scheduler, schedule, clock)
    assert schedule.consecutive_failures == 1
    ceiling = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * 2)
    assert schedule.next_due == clock.now + jitter * ceiling
    assert clock.now < schedule.next_due <= clock.now + BACKOFF_CAP_SECONDS

    await run_tick(scheduler, schedule, clock)
    assert schedule.consecutive_failures == 2
    ceiling = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * 4)
    assert schedule.next_due == clock.now + jitter * ceiling

    await run_tick(scheduler, schedule, clock)
    assert schedule.consecutive_failures == DEGRADED_AFTER_FAILURES
    assert scheduler._base_interval("demo", schedule) == DEGRADED_INTERVAL_SECONDS
    assert schedule.next_due % DEGRADED_INTERVAL_SECONDS == _stable_hash("demo") % int(
        DEGRADED_INTERVAL_SECONDS
    )

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
    tasks = [scheduler._schedules[name].task for name in targets]
    for _ in range(10):
        await asyncio.sleep(0)
    assert tracker.active == GLOBAL_CONCURRENCY

    tracker.gate.set()
    await asyncio.gather(*tasks)
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

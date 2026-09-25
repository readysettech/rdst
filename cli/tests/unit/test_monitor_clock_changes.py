"""Independent wall and elapsed clocks exercise wake-up without real suspension."""

import asyncio
import threading

import pytest

from features.monitor import report
from features.query_registry.scheduler import ObservationScheduler, LEASE_TTL_SECONDS
from shared.monitor.monitor_store import MonitorStore
from shared.query_registry.observation_store import LeaseLostError, ObservationStore
from tests.unit.test_monitor_report import NOW, _seed, _narrative
from tests.unit.test_observation_scheduler import FakeCoordinator


class Clocks:
    def __init__(self):
        self.wall = NOW
        self.elapsed = 0

    def time(self):
        return self.wall

    def monotonic(self):
        return self.elapsed

    def advance(self, seconds):
        self.wall += seconds
        self.elapsed += seconds


@pytest.mark.parametrize("jump", [1800, -3600])
async def test_report_deadline_survives_wall_jump_and_provider_suspend(tmp_path, jump):
    store = MonitorStore(tmp_path / "monitor.db")
    _seed(store)
    clocks = Clocks()
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
        assert len(waits) <= 3, "Reporter deadline followed the wall clock"
        if len(waits) == 1:
            clocks.wall += jump
        clocks.advance(report.USER_WAIT_SECONDS)
        await asyncio.sleep(0)

    result = await report.run_report(
        "db", "user", store=store, clock=clocks.time, monotonic=clocks.monotonic,
        sleep=sleep, explain=lambda *_: {"plan": ["scan"]},
        collect=lambda _: ({"engine": "postgresql"}, {"tables": {}}), narrate=_narrative)
    assert result["state"] == "done"
    assert result["provisional"] is True
    assert len(waits) == 1
    assert len(store.read("SELECT * FROM report_run")) == 1


@pytest.mark.parametrize("jump", [1800, -3600])
async def test_clock_jump_collects_once_and_expires_first_run(tmp_path, jump):
    clocks = Clocks()
    coordinator = FakeCoordinator()
    scheduler = ObservationScheduler(
        coordinator, clock=clocks.time, monotonic=clocks.monotonic,
        targets_provider=lambda: ["db"], monitored_provider=lambda: {"db"})
    scheduler._refresh_targets(clocks.wall)
    assert scheduler.start_fast_phase("db")
    await scheduler._schedules["db"].task
    await asyncio.sleep(0)
    clocks.wall += jump
    clocks.advance(3601)
    scheduler._launch_due(clocks.wall)
    await scheduler._schedules["db"].task
    await asyncio.sleep(0)
    for _ in range(20):
        scheduler._launch_due(clocks.wall)
    assert coordinator.collector_for("db").collects == 2
    assert not scheduler.in_fast_phase("db")
    assert clocks.wall < scheduler.next_reading_at("db") <= clocks.wall + 60
    assert scheduler._schedules["db"].task is None


@pytest.mark.parametrize("jump", [1800, -3600])
async def test_suspended_worker_is_fenced_after_lease_takeover(tmp_path, jump):
    clocks = Clocks()
    store = ObservationStore(tmp_path / "cache.db")
    entered, resume = threading.Event(), threading.Event()
    token = store.acquire_lease("db", "sleeping", LEASE_TTL_SECONDS, clocks.wall)

    def worker():
        entered.set()
        assert resume.wait(5), "Suspended worker was not released"
        store.upsert_collector_state("db", state="idle", owner_id="sleeping",
                                     fencing_token=token, now=clocks.wall)

    task = asyncio.create_task(asyncio.to_thread(worker))
    assert await asyncio.to_thread(entered.wait, 2)
    clocks.wall += jump
    clocks.advance(7200)
    fresh = store.acquire_lease("db", "awake", LEASE_TTL_SECONDS, clocks.wall)
    assert fresh > token
    resume.set()
    with pytest.raises(LeaseLostError):
        await task
    store.upsert_collector_state("db", state="idle", owner_id="awake",
                                 fencing_token=fresh, now=clocks.wall)
    assert task.done()
    store.close()


@pytest.mark.parametrize("jump", [1800, -3600])
@pytest.mark.parametrize("suspend", [0, 7200])
def test_resume_records_fresh_reading_with_chart_gap(tmp_path, jump, suspend):
    store = MonitorStore(tmp_path / "monitor.db")
    clocks = Clocks()

    def record(calls):
        store.record("db", clocks.wall, [{"query_key": "q", "calls": calls,
                     "total_ms": calls * 2}], {"q": "SELECT * FROM orders"}, [],
                     {"calls": calls, "total_ms": calls * 2}, workload_checked=True)

    record(60)
    clocks.advance(60)
    record(60)
    clocks.wall += jump
    clocks.advance(suspend)
    record(7200)
    assert not store.read("SELECT * FROM db_series WHERE res=60 AND bucket_start=?",
                          (clocks.wall // 60 * 60,))
    clocks.advance(60)
    record(60)
    assert store.read("SELECT calls FROM db_series WHERE res=60 AND bucket_start=?",
                      (clocks.wall // 60 * 60,)) == [{"calls": 60}]
    assert len(store.read("SELECT * FROM monitor_pulse WHERE kind='collect'")) == 4


@pytest.mark.parametrize("jump", [1800, -3600])
def test_analysis_timeout_after_resume_reuses_job_and_finishes(tmp_path, jump):
    from features.monitor.analyze_queue import enqueue_user

    clocks = Clocks()
    store = MonitorStore(tmp_path / "monitor.db", clock=clocks.time)
    store.record("db", clocks.wall, [{"query_key": "q", "calls": 60, "total_ms": 120}],
                 {"q": "SELECT * FROM orders"}, [], None)
    job = enqueue_user("db", "q", store=store, now=clocks.wall)
    claimed = store.claim_analysis("db", clocks.wall)
    assert claimed["id"] == job["id"]
    clocks.wall += jump
    clocks.advance(7200)
    store.recover_analysis(clocks.wall, 600)
    # Completion from the suspended attempt cannot finish a queued retry.
    store.finish_analysis(job["id"], "done", clocks.wall, analysis_ref="stale")
    [pending] = store.analyze_jobs("db")
    assert pending["state"] == "queued"
    assert pending["analysis_ref"] is None
    assert enqueue_user("db", "q", store=store, now=clocks.wall)["id"] == job["id"]
    current = store.claim_analysis("db", clocks.wall)
    assert current["id"] == job["id"]
    store.finish_analysis(job["id"], "done", clocks.wall + 1, analysis_ref="fresh",
                          owner=current["owner"], attempt=current["attempt"])
    [finished] = store.analyze_jobs("db")
    assert finished["state"] == "done"
    assert finished["analysis_ref"] == "fresh"
    assert store.claim_analysis("db", clocks.wall + 1) is None


@pytest.mark.parametrize("jump", [1800, -3600])
def test_suspend_crosses_first_hour_without_replaying_ramp(tmp_path, jump):
    from features.monitor.analyze_queue import AnalyzeQueue, first_hour_facts
    from tests.unit.test_monitor_first_hour import reads

    clocks = Clocks()
    store = MonitorStore(tmp_path / "monitor.db", clock=clocks.time)
    store.set_settings("db", {"enabled": True, "deep_analysis": True})
    store.start_first_hour("db", clocks.wall)
    reads(store, 12, at=clocks.wall)
    queue = AnalyzeQueue(store, clock=clocks.time)
    clocks.advance(10)
    queue._prepare(clocks.wall)
    assert len(store.analyze_jobs("db")) == 10
    clocks.wall += jump
    clocks.advance(7200)
    queue._prepare(clocks.wall)
    queue._prepare(clocks.wall)
    assert first_hour_facts(store, "db", clocks.wall) is None
    jobs = store.analyze_jobs("db")
    assert len(jobs) <= 12
    assert len({job["query_key"] for job in jobs}) == len(jobs)
    assert all(job["reason"] == "new" for job in jobs if job["enqueued_at"] == clocks.wall)

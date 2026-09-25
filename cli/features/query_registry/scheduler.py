"""Flag-gated observation scheduler for configured targets.

Runs inside the web API process and is started only by an explicit
``await scheduler.start()`` from the web app factory's ASGI lifespan
(research Q5). Importing this module has no side effects: no task, no
store, no self-starting singleton, so CLI import graphs stay clean.

Cadence follows research Q6: wall-clock aligned periods with a stable
per-target phase offset, a per-target duty-cycle ceiling as the
load-bearing safety control, full jitter for failure backoff only, and
priority evaluated only among due targets.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import random
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional

from features.query_registry.discovery import QueryDiscoveryCoordinator
from shared.query_registry.observation_store import LeaseLostError, collection_owner
from shared.monitor.monitor_store import MonitorStore, default_monitor_db_path
from shared.target_backoff import TargetBackoff, target_backoff

logger = logging.getLogger(__name__)

SCHEDULER_ENV = "RDST_OBSERVATION_SCHEDULER"

# Cadence classes (research Q6). The visible 15s class is deliberately not
# enabled this wave: it needs the Phase 0 per-collection cost measurement
# before it is safe to offer.
# A watched target (live SSE subscriber) matches pganalyze's 60s statement floor.
ACTIVE_INTERVAL_SECONDS = 60.0
# Right after monitoring is turned on, a target reads every 10s for its first
# five minutes so the chart and the first analyses appear within the first visit.
FIRST_RUN_INTERVAL_SECONDS = 10.0
FIRST_RUN_FAST_SECONDS = 300.0
# Unwatched configured targets need freshness, not immediacy.
BACKGROUND_INTERVAL_SECONDS = 300.0
# Repeatedly failing targets park on a long, stable period instead of retrying.
DEGRADED_INTERVAL_SECONDS = 900.0
# The duty-cycle ceiling never stretches an interval past one hour.
MAX_INTERVAL_SECONDS = 3600.0

# Customer database time may consume at most 5% of its interval. Local
# registry parsing and SQLite writes have a separate per-pass work budget.
MAX_DUTY_CYCLE = 0.05

# Per-cycle budget upper bound: the 30s statement timeout plus the 120s text
# fetch budget cap; a cycle cancelled at the deadline is a failed tick.
COLLECTION_DEADLINE_SECONDS = 120
# TTL is three collection deadlines. Confirmed dead local owners are reclaimed at
# startup; remote or suspended owners retain the TTL and their write-time fence.
LEASE_TTL_SECONDS = 3 * COLLECTION_DEADLINE_SECONDS

# Collections are IO-bound threads against customer databases; three at once
# keeps event-loop stalls and cache.db write contention bounded.
GLOBAL_CONCURRENCY = 3

# Database retries share per-target admission and backoff with parameter and index reads.
BACKOFF_BASE_SECONDS = 30.0
BACKOFF_CAP_SECONDS = DEGRADED_INTERVAL_SECONDS
# Consecutive failures beyond this demote the target to the degraded class.
DEGRADED_AFTER_FAILURES = 3

# The configured-target list is re-read on this cadence so add/remove takes
# effect without a restart.
TARGET_REFRESH_SECONDS = 60.0
# Run-loop polling resolution.
TICK_SECONDS = 1.0


def observation_scheduler_enabled() -> bool:
    """Default OFF (research M4): flip after the Fleet validation matrix."""
    return os.environ.get(SCHEDULER_ENV, "").strip().lower() in ("1", "true")


def _configured_targets() -> List[str]:
    from shared.config.targets import TargetsConfig

    cfg = TargetsConfig()
    cfg.load()
    targets = cfg.list_targets()
    if default_monitor_db_path().exists():
        disabled = {row["target"] for row in MonitorStore().read(
            "SELECT target FROM monitor_setting WHERE key='enabled' AND value='0'")}
        targets = [target for target in targets if target not in disabled]
    return targets


def monitored_targets() -> set[str]:
    if not default_monitor_db_path().exists():
        return set()
    try:
        return MonitorStore().monitored_targets()
    except RuntimeError:
        # A monitor.db this build can't open (written by a newer RDST, or damaged) keeps
        # monitoring off; the rest of the app still starts, and the file is left as it is.
        logger.error("Monitoring is off: monitor.db can't be opened", exc_info=True)
        return set()


def _stable_hash(target: str) -> int:
    """Restart-stable target hash for the phase offset (research Q6)."""
    return int.from_bytes(hashlib.sha256(target.encode()).digest()[:8], "big")


@dataclass
class _TargetSchedule:
    next_due: float
    consecutive_failures: int = 0
    last_duration_s: float = 0.0
    reading_duration_s: float = 0.0
    task: Optional["asyncio.Task[None]"] = None
    # End of the first-run fast phase (0 when the target isn't in one).
    fast_until: float = 0.0
    fast_collections: int = 0
    completed: bool = False
    latest_attempt_ok: bool = False
    started_at: Optional[float] = None
    error: Optional[str] = None
    waiting_for_lease: bool = False


class ObservationScheduler:
    """Schedules fenced background collections for every configured target.

    Lifecycle is explicit: ``await start()`` from the web lifespan and
    ``await stop()`` on shutdown. The clock, jitter source, and sleep are
    injectable so scheduling math is testable without real time.
    """

    def __init__(
        self,
        coordinator: QueryDiscoveryCoordinator,
        *,
        targets_provider: Optional[Callable[[], List[str]]] = None,
        clock: Callable[[], float] = time.time,
        monotonic: Optional[Callable[[], float]] = None,
        rand: Callable[[], float] = random.random,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        owner_id: Optional[str] = None,
        monitored_provider: Optional[Callable[[], set[str]]] = None,
        backoff: Optional[TargetBackoff] = None,
    ) -> None:
        self._coordinator = coordinator
        self._targets_provider = targets_provider or _configured_targets
        self._monitored_provider = monitored_provider or monitored_targets
        self._monitored: set[str] = set()
        self._clock = clock
        self._monotonic = monotonic or (time.monotonic if clock is time.time else clock)
        self._clock_sample = (clock(), self._monotonic())
        self._rand = rand
        self._sleep = sleep
        self._backoff = backoff or (target_backoff if clock is time.time else TargetBackoff(self._monotonic))
        # One lease owner per process lifetime, shared by every target.
        self._owner_id = owner_id or collection_owner()
        self._store = None
        self._schedules: Dict[str, _TargetSchedule] = {}
        self._leased: set[str] = set()
        self._semaphore = asyncio.Semaphore(GLOBAL_CONCURRENCY)
        self._collections: set[asyncio.Task] = set()
        self._admitted: set[asyncio.Task] = set()
        self._retained: set[asyncio.Task] = set()
        self._task: Optional[asyncio.Task[None]] = None
        self._stopping = False
        self._next_target_refresh = 0.0
        # Told each collection's outcome (target, ok, ts, error), off the event loop.
        self._outcome_listener: Optional[Callable[[str, bool, int, Optional[str]], None]] = None
        # Told each successful first-run collection (target, first), on the event loop.
        self._fast_listener: Optional[Callable[[str, bool], None]] = None

    def set_outcome_listener(
        self, listener: Optional[Callable[[str, bool, int, Optional[str]], None]]
    ) -> None:
        self._outcome_listener = listener

    def collect_soon(self, name: str) -> None:
        """Make an idle target due now; safe from any thread (the run loop picks it up)."""
        schedule = self._schedules.get(name)
        if schedule is not None and schedule.task is None:
            schedule.next_due = min(schedule.next_due, self._clock())

    def start_fast_phase(
        self, name: str, on_collected: Optional[Callable[[str, bool], None]] = None
    ) -> bool:
        """Read a newly monitored target now, then every 10s for its first five minutes.

        ``on_collected(target, first)`` hears each successful collection of the phase.
        """
        now = self._clock()
        schedule = self._schedules.get(name)
        if schedule is None:
            self._refresh_targets(now)
            schedule = self._schedules.get(name)
        if schedule is None:
            return False
        self._monitored.add(name)
        schedule.fast_until = now + FIRST_RUN_FAST_SECONDS
        schedule.fast_collections = 0
        if on_collected is not None:
            self._fast_listener = on_collected
        if schedule.task is None:
            schedule.next_due = now
            self._launch_due(now)
        return True

    def next_reading_at(self, name: str) -> Optional[float]:
        """Return the scheduled collection slot in Unix seconds, including retry admission."""
        schedule = self._schedules.get(name)
        if schedule is None or self._stopping:
            return None
        retry = self._backoff.retry_after(name)
        return max(schedule.next_due, self._clock() + retry) if retry else schedule.next_due

    def collection_status(self, name: str) -> dict:
        schedule = self._schedules.get(name)
        if schedule is None:
            return {}
        first = not schedule.completed and schedule.error is None
        if schedule.error:
            reason = f"Retrying after an error: {schedule.error}"
        elif schedule.waiting_for_lease:
            reason = "Waiting for another reader to finish"
        elif first:
            reason = "Starting up after a restart"
        elif schedule.started_at is not None:
            reason = "Waiting for the database to answer"
        elif schedule.reading_duration_s > ACTIVE_INTERVAL_SECONDS:
            reason = f"The last reading took {round(schedule.reading_duration_s)} s"
        else:
            reason = None
        return {"first_reading": first, "delay_reason": reason}

    async def check_now(self, name: str) -> dict:
        """Join or admit one fenced reading using the normal collection budget."""
        self._refresh_targets(self._clock())
        schedule = self._schedules.get(name)
        if schedule is None or self._stopping:
            raise ValueError("This database isn't scheduled for monitoring")
        if schedule.task is None:
            self._backoff.forget(name)
            schedule.next_due = self._clock()
            self._launch_due(self._clock())
        task = schedule.task
        if task is None:
            return {"ok": False, "detail": "Readers are busy. Your reading is queued."}
        await asyncio.shield(task)
        ok = schedule.latest_attempt_ok
        return {"ok": ok, "detail": "Reading complete" if ok else
                schedule.error or self.collection_status(name).get("delay_reason") or
                "Your reading is queued for a retry"}

    def in_fast_phase(self, name: str) -> bool:
        schedule = self._schedules.get(name)
        return schedule is not None and self._clock() < schedule.fast_until

    def _notify_fast(self, name: str, schedule: _TargetSchedule) -> None:
        schedule.fast_collections += 1
        listener = self._fast_listener
        if listener is None or self._stopping:
            return
        try:
            listener(name, schedule.fast_collections == 1)
        except Exception:
            logger.debug("First-run listener failed for %s", name, exc_info=True)

    async def _report_outcome(self, name: str, ok: bool, error: Optional[str] = None) -> None:
        listener = self._outcome_listener
        if listener is None or self._stopping:
            return
        try:
            await asyncio.to_thread(listener, name, ok, int(self._clock()), error)
        except Exception:
            logger.debug("Collection outcome listener failed for %s", name, exc_info=True)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._store = self._coordinator.store()
        if self._store is None:
            raise RuntimeError(
                "Observation scheduler requires cache.db for cross-process fencing"
            )
        self._coordinator.set_scheduler_owned(True)
        self._refresh_targets(self._clock())
        for name in self._monitored:
            if name in self._schedules:
                self._schedules[name].next_due = self._clock()
        self._coordinator.set_subscription_listener(self._subscriber_state_changed)
        logger.info(
            "Observation scheduler started (owner=%s, targets=%d)",
            self._owner_id,
            len(self._schedules),
        )
        self._task = asyncio.create_task(self._run(), name="observation-scheduler")

    async def stop(self) -> None:
        """Stop admitting, cancel in-flight with grace, release owned leases."""
        self._stopping = True
        self._coordinator.set_subscription_listener(None)
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        inflight = [s.task for s in self._schedules.values() if s.task is not None]
        for collect_task in inflight:
            collect_task.cancel()
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)
        self._release_leases(tuple(self._leased))
        self._coordinator.set_scheduler_owned(False)
        logger.info("Observation scheduler stopped")
        self._schedules.clear()

    def _subscriber_state_changed(self, name: str, subscribed: bool) -> None:
        """Move a target between active and background cadence on SSE edges.

        The first subscriber makes an idle target due immediately and launches
        it through the scheduler's existing single-flight/global semaphore.
        SSE still never calls the collector directly. The last unsubscribe
        restores a background-aligned next tick unless a collection is already
        in flight, in which case its completion computes the background slot.
        """
        if self._stopping:
            return
        now = self._clock()
        schedule = self._schedules.get(name)
        if schedule is None:
            self._refresh_targets(now)
            schedule = self._schedules.get(name)
        if schedule is None:
            return
        if subscribed:
            if schedule.task is None:
                schedule.next_due = min(schedule.next_due, now)
                self._launch_due(now)
            return
        if schedule.task is None:
            interval = self._base_interval(name, schedule)
            schedule.next_due = self._aligned_next(name, now, interval)

    async def _run(self) -> None:
        while not self._stopping:
            now = self._clock()
            if now >= self._next_target_refresh:
                self._refresh_targets(now)
            self._launch_due(now)
            await self._sleep(TICK_SECONDS)

    # -- target enumeration ---------------------------------------------------

    def _refresh_targets(self, now: float) -> None:
        self._next_target_refresh = now + TARGET_REFRESH_SECONDS
        try:
            targets = set(self._targets_provider())
            monitored = self._monitored_provider()
        except Exception:
            logger.warning("Failed to list configured targets", exc_info=True)
            return
        changed = self._monitored ^ monitored
        self._monitored = monitored
        for name in changed & set(self._schedules):
            schedule = self._schedules[name]
            if schedule.task is None:
                schedule.next_due = self._aligned_next(
                    name, now, self._base_interval(name, schedule)
                )
        for name in targets - set(self._schedules):
            interval = self._base_interval(name)
            self._schedules[name] = _TargetSchedule(
                next_due=self._aligned_next(name, now, interval)
            )
        for name in set(self._schedules) - targets:
            self._remove_target(name)

    def _remove_target(self, name: str) -> None:
        schedule = self._schedules.pop(name, None)
        if schedule is not None and schedule.task is not None:
            schedule.task.cancel()
        if name in self._leased:
            self._release_leases((name,))
        self._backoff.forget(name)

    # -- scheduling math (research Q6) ----------------------------------------

    def _base_interval(self, name: str, schedule: Optional[_TargetSchedule] = None) -> float:
        if (
            schedule is not None
            and schedule.consecutive_failures >= DEGRADED_AFTER_FAILURES
        ):
            return DEGRADED_INTERVAL_SECONDS
        if schedule is not None and self._clock() < schedule.fast_until:
            return FIRST_RUN_INTERVAL_SECONDS
        if self._coordinator.has_subscribers(name) or name in self._monitored:
            return ACTIVE_INTERVAL_SECONDS
        return BACKGROUND_INTERVAL_SECONDS

    def _next_interval(self, base: float, last_duration_s: float) -> float:
        """Duty-cycle ceiling: next_interval >= last_duration / MAX_DUTY_CYCLE."""
        return min(max(base, last_duration_s / MAX_DUTY_CYCLE),
                   MAX_INTERVAL_SECONDS)

    def _aligned_next(self, name: str, now: float, interval: float) -> float:
        """Next wall-clock aligned slot, phase-offset by the target's hash."""
        offset = _stable_hash(name) % int(interval)
        return (math.floor((now - offset) / interval) + 1) * interval + offset

    def _priority(self, name: str, schedule: _TargetSchedule, now: float) -> float:
        """Evaluated only among due targets, so starvation is impossible."""
        base = self._base_interval(name, schedule)
        staleness = min(max(now - schedule.next_due, 0.0) / base, 2.0)
        watched = 3.0 if self._coordinator.has_subscribers(name) else 0.0
        return watched + staleness - min(schedule.consecutive_failures, 4)

    # -- collection -------------------------------------------------------------

    def _launch_due(self, now: float) -> None:
        if self._stopping:
            return
        elapsed = self._monotonic()
        wall_before, elapsed_before = self._clock_sample
        jump = now - wall_before - (elapsed - elapsed_before)
        self._clock_sample = (now, elapsed)
        if abs(jump) > TICK_SECONDS * 5:
            self._next_target_refresh = now
            for schedule in self._schedules.values():
                if schedule.fast_until:
                    schedule.fast_until += jump
                if schedule.task is None:
                    schedule.next_due = now
        due = [
            (name, schedule)
            for name, schedule in self._schedules.items()
            if schedule.task is None and schedule.next_due <= now
        ]
        due.sort(key=lambda item: self._priority(item[0], item[1], now), reverse=True)
        slots = GLOBAL_CONCURRENCY - len(self._admitted)
        for name, schedule in due[:max(0, slots)]:
            schedule.task = asyncio.create_task(
                self._collect_target(name, schedule), name=f"observe:{name}"
            )
            self._admitted.add(schedule.task)
            def finished(task: asyncio.Task, entry: _TargetSchedule = schedule) -> None:
                if task not in self._retained:
                    self._admitted.discard(task)
                if entry.task is task:
                    entry.task = None
                if not task.cancelled():
                    task.exception()
            schedule.task.add_done_callback(finished)

    async def _collect_target(self, name: str, schedule: _TargetSchedule) -> None:
        admitted = False
        schedule.latest_attempt_ok = False
        work = None
        token = None
        collector = None
        try:
            async with self._semaphore:
                if self._stopping:
                    return
                if name in self._monitored:
                    self._backoff.limit_retry(name, ACTIVE_INTERVAL_SECONDS)
                if not self._backoff.acquire(name):
                    schedule.next_due = self._clock() + TICK_SECONDS
                    return
                admitted = True
                started = self._clock()
                schedule.started_at = started
                schedule.waiting_for_lease = False
                started_elapsed = self._monotonic()
                token = self._acquire_lease(name, int(started))
                if self._store is not None and token is None:
                    self._backoff.release(name)
                    admitted = False
                    schedule.waiting_for_lease = True
                    # Another live process owns this target; try again next period.
                    schedule.next_due = self._aligned_next(
                        name, started, self._base_interval(name, schedule)
                    )
                    return
                collector = self._coordinator.collector_for(name)
                polling = getattr(collector, "task", None)
                if polling is not None and not polling.done():
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(polling), COLLECTION_DEADLINE_SECONDS
                        )
                    except asyncio.TimeoutError:
                        schedule.error = "Waiting for the previous reader to finish"
                        self._backoff.finish(name, success=False)
                        admitted = False
                        self._record_failure(name, schedule)
                        await self._report_outcome(name, False, schedule.error)
                        return
                if token is not None:
                    collector.set_write_fence(
                        self._owner_id, token, lambda: int(self._clock())
                    )
                try:
                    work = asyncio.create_task(collector.collect_now())
                    self._collections.add(work)
                    work.add_done_callback(self._collections.discard)
                    event = await asyncio.wait_for(
                        asyncio.shield(work), COLLECTION_DEADLINE_SECONDS
                    )
                except asyncio.CancelledError:
                    raise
                except LeaseLostError:
                    # The store already rejected the cycle's remaining writes;
                    # the lease moved, so there is nothing to release.
                    logger.warning("Lease lost mid-collection for target %s", name)
                    self._leased.discard(name)
                    self._backoff.finish(name, success=False)
                    admitted = False
                    self._record_failure(name, schedule)
                    return
                except Exception as exc:
                    logger.warning(
                        "Scheduled collection failed for target %s",
                        name,
                        exc_info=True,
                    )
                    schedule.error = ("The reading timed out" if isinstance(exc, asyncio.TimeoutError)
                                      else type(exc).__name__)
                    await self._report_outcome(name, False, str(exc))
                    event = None
                    if isinstance(exc, asyncio.TimeoutError):
                        # Cancellation of to_thread cannot stop the database driver. Hold
                        # this admission slot until the collection really returns.
                        try:
                            event = await asyncio.shield(work)
                        except Exception:
                            logger.debug("Timed-out collection completed with an error", exc_info=True)
                    if event is None or event.event == "discovery_error":
                        self._backoff.finish(name, success=False)
                        admitted = False
                        self._record_failure(name, schedule)
                        return
                finally:
                    if token is not None and work.done():
                        collector.clear_write_fence()
                if event.event == "discovery_error":
                    self._backoff.finish(name, success=False)
                    admitted = False
                    self._record_failure(name, schedule)
                    error = (event.data or {}).get("error")
                    schedule.error = "The database reading failed"
                    await self._report_outcome(
                        name, False, error if isinstance(error, str) else None
                    )
                    return
                finished = self._clock()
                self._backoff.finish(name, success=True)
                admitted = False
                schedule.reading_duration_s = max(self._monotonic() - started_elapsed, 0.0)
                stats = event.data.get("stats") or {}
                db_ms = stats.get("db_touch_duration_ms")
                schedule.last_duration_s = (
                    max(float(db_ms) / 1000, 0.0) if db_ms is not None
                    else max(self._monotonic() - started_elapsed, 0.0)
                )
                schedule.consecutive_failures = 0
                schedule.completed = True
                schedule.latest_attempt_ok = True
                schedule.error = None
                interval = self._next_interval(
                    self._base_interval(name, schedule), schedule.last_duration_s,
                )
                schedule.next_due = self._aligned_next(name, finished, interval)
                if schedule.fast_until:
                    if finished < schedule.fast_until:
                        self._notify_fast(name, schedule)
                    else:
                        schedule.fast_until = 0.0
                await self._report_outcome(name, True)
        finally:
            if admitted and (work is None or work.done()):
                self._backoff.release(name)
            parent = asyncio.current_task()
            if work is not None and not work.done():
                # Native work keeps admission and its write fence across cancellation.
                self._retained.add(parent)
                def completed(done: asyncio.Task) -> None:
                    self._admitted.discard(parent)
                    self._retained.discard(parent)
                    if admitted:
                        self._backoff.finish(name, success=False)
                    if name not in self._schedules:
                        self._backoff.forget(name)
                    if token is not None:
                        collector.clear_write_fence()
                    if not done.cancelled():
                        done.exception()
                work.add_done_callback(completed)
            else:
                self._admitted.discard(parent)
            schedule.started_at = None
            schedule.task = None

    def _record_failure(self, name: str, schedule: _TargetSchedule) -> None:
        schedule.consecutive_failures += 1
        if name in self._monitored:
            # A restored monitored database gets checked within one reading interval.
            self._backoff.limit_retry(name, ACTIVE_INTERVAL_SECONDS)
        schedule.next_due = self._clock() + max(TICK_SECONDS, self._backoff.retry_after(name))

    # -- leases (research Q5) ---------------------------------------------------

    def _acquire_lease(self, name: str, now: int) -> Optional[int]:
        """Acquire or renew the target's fenced lease; None means skip the tick."""
        if self._store is None:
            return None
        try:
            token = self._store.acquire_lease(
                name, self._owner_id, LEASE_TTL_SECONDS, now
            )
        except Exception:
            logger.warning(
                "Lease acquisition failed for target %s", name, exc_info=True
            )
            return None
        if token is not None:
            self._leased.add(name)
        return token

    def _release_leases(self, names: tuple[str, ...]) -> None:
        for name in names:
            self._leased.discard(name)
            if self._store is None:
                continue
            try:
                self._store.release_lease(name, self._owner_id)
            except Exception:
                logger.warning(
                    "Failed to release the collection lease for target %s",
                    name,
                    exc_info=True,
                )

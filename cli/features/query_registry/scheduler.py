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
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional

from features.query_registry.discovery import QueryDiscoveryCoordinator
from shared.query_registry.observation_store import LeaseLostError

logger = logging.getLogger(__name__)

SCHEDULER_ENV = "RDST_OBSERVATION_SCHEDULER"

# Cadence classes (research Q6). The visible 15s class is deliberately not
# enabled this wave: it needs the Phase 0 per-collection cost measurement
# before it is safe to offer.
# A watched target (live SSE subscriber) matches pganalyze's 60s statement floor.
ACTIVE_INTERVAL_SECONDS = 60.0
# Unwatched configured targets need freshness, not immediacy.
BACKGROUND_INTERVAL_SECONDS = 300.0
# Repeatedly failing targets park on a long, stable period instead of retrying.
DEGRADED_INTERVAL_SECONDS = 900.0
# The duty-cycle ceiling never stretches an interval past one hour.
MAX_INTERVAL_SECONDS = 3600.0

# A collection may consume at most 5% of its interval. This self-calibrating
# ceiling is the load-bearing control that makes shipping ahead of Phase 0
# measurement safe (research Q6).
MAX_DUTY_CYCLE = 0.05

# Per-cycle budget upper bound: the 30s statement timeout plus the 120s text
# fetch budget cap; a cycle cancelled at the deadline is a failed tick.
COLLECTION_DEADLINE_SECONDS = 120
# Research Q5: TTL is three collection deadlines, so a killed or suspended
# process self-heals within one TTL while a live cycle (bounded by the
# deadline) can never outlast its own lease, making mid-cycle renewal moot.
LEASE_TTL_SECONDS = 3 * COLLECTION_DEADLINE_SECONDS

# Collections are IO-bound threads against customer databases; three at once
# keeps event-loop stalls and cache.db write contention bounded.
GLOBAL_CONCURRENCY = 3

# Failure backoff uses AWS full jitter: sleep = random(0, min(cap, base * 2**n)).
# Jitter applies to failures only; periodic collection stays aligned.
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
    return cfg.list_targets()


def _stable_hash(target: str) -> int:
    """Restart-stable target hash for the phase offset (research Q6)."""
    return int.from_bytes(hashlib.sha256(target.encode()).digest()[:8], "big")


@dataclass
class _TargetSchedule:
    next_due: float
    consecutive_failures: int = 0
    last_duration_s: float = 0.0
    task: Optional["asyncio.Task[None]"] = None


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
        rand: Callable[[], float] = random.random,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        owner_id: Optional[str] = None,
    ) -> None:
        self._coordinator = coordinator
        self._targets_provider = targets_provider or _configured_targets
        self._clock = clock
        self._rand = rand
        self._sleep = sleep
        # One lease owner per process lifetime, shared by every target.
        self._owner_id = owner_id or f"rdst-web-{uuid.uuid4().hex}"
        self._store = None
        self._schedules: Dict[str, _TargetSchedule] = {}
        self._leased: set[str] = set()
        self._semaphore = asyncio.Semaphore(GLOBAL_CONCURRENCY)
        self._task: Optional[asyncio.Task[None]] = None
        self._stopping = False
        self._next_target_refresh = 0.0

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
        except Exception:
            logger.warning("Failed to list configured targets", exc_info=True)
            return
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

    # -- scheduling math (research Q6) ----------------------------------------

    def _base_interval(self, name: str, schedule: Optional[_TargetSchedule] = None) -> float:
        if (
            schedule is not None
            and schedule.consecutive_failures >= DEGRADED_AFTER_FAILURES
        ):
            return DEGRADED_INTERVAL_SECONDS
        if self._coordinator.has_subscribers(name):
            return ACTIVE_INTERVAL_SECONDS
        return BACKGROUND_INTERVAL_SECONDS

    def _next_interval(self, base: float, last_duration_s: float) -> float:
        """Duty-cycle ceiling: next_interval >= last_duration / MAX_DUTY_CYCLE."""
        return min(max(base, last_duration_s / MAX_DUTY_CYCLE), MAX_INTERVAL_SECONDS)

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
        due = [
            (name, schedule)
            for name, schedule in self._schedules.items()
            if schedule.task is None and schedule.next_due <= now
        ]
        due.sort(key=lambda item: self._priority(item[0], item[1], now), reverse=True)
        for name, schedule in due:
            schedule.task = asyncio.create_task(
                self._collect_target(name, schedule), name=f"observe:{name}"
            )

    async def _collect_target(self, name: str, schedule: _TargetSchedule) -> None:
        try:
            async with self._semaphore:
                if self._stopping:
                    return
                started = self._clock()
                token = self._acquire_lease(name, int(started))
                if self._store is not None and token is None:
                    # Another live process owns this target; try again next period.
                    schedule.next_due = self._aligned_next(
                        name, started, self._base_interval(name, schedule)
                    )
                    return
                collector = self._coordinator.collector_for(name)
                if token is not None:
                    collector.set_write_fence(
                        self._owner_id, token, lambda: int(self._clock())
                    )
                try:
                    event = await asyncio.wait_for(
                        collector.collect_now(), COLLECTION_DEADLINE_SECONDS
                    )
                except asyncio.CancelledError:
                    raise
                except LeaseLostError:
                    # The store already rejected the cycle's remaining writes;
                    # the lease moved, so there is nothing to release.
                    logger.warning("Lease lost mid-collection for target %s", name)
                    self._leased.discard(name)
                    self._record_failure(name, schedule)
                    return
                except Exception:
                    logger.warning(
                        "Scheduled collection failed for target %s",
                        name,
                        exc_info=True,
                    )
                    self._record_failure(name, schedule)
                    return
                finally:
                    if token is not None:
                        collector.clear_write_fence()
                if event.event == "discovery_error":
                    self._record_failure(name, schedule)
                    return
                finished = self._clock()
                schedule.last_duration_s = max(finished - started, 0.0)
                schedule.consecutive_failures = 0
                interval = self._next_interval(
                    self._base_interval(name, schedule), schedule.last_duration_s
                )
                schedule.next_due = self._aligned_next(name, finished, interval)
        finally:
            schedule.task = None

    def _record_failure(self, name: str, schedule: _TargetSchedule) -> None:
        schedule.consecutive_failures += 1
        now = self._clock()
        if schedule.consecutive_failures >= DEGRADED_AFTER_FAILURES:
            schedule.next_due = self._aligned_next(name, now, DEGRADED_INTERVAL_SECONDS)
            return
        ceiling = min(
            BACKOFF_CAP_SECONDS,
            BACKOFF_BASE_SECONDS * (2 ** schedule.consecutive_failures),
        )
        schedule.next_due = now + self._rand() * ceiling

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

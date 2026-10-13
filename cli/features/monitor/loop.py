"""One process-local attention loop for opted-in targets."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any, Callable

from shared.db_connection import IoDeadline
from shared.target_backoff import TargetBackoff, target_backoff

from features.monitor import actions, database_limit
from features.monitor.attention import baselines, shortlist
from features.monitor.connection import ConnectionWatch
from features.monitor.delivery import attention_gate, relevance
from features.monitor.indexes import IndexesBusy, read_indexes
from features.monitor.judge import AttentionJudge
from features.monitor.recurring import RecurringCheck
from features.monitor.table_stats import read_stats
from features.monitor.run_guard import RunGuard
from features.query_registry.discovery import _default_connection_factory, query_discovery
from shared.monitor.monitor_store import MonitorStore
from shared.monitor.statements import alert_member

logger = logging.getLogger(__name__)
TARGETS_RETRY_S = 5
# A pass's reads hold the database's admission; one that stopped answering is aborted after
# this long so the collector can still record the outage.
READ_DEADLINE_S = 120
# Collected minutes an hour needs before its rate stands for the normal level.
HOUR_RATE_MIN_MINUTES = 10
_active_lock = threading.Lock()
_active_loop: MonitorLoop | None = None

LOCK_WAIT_THRESHOLD = 3
LOCK_WAIT_OPEN_MINUTES = 5
LOCK_WAIT_RECOVERY_MINUTES = 3
PG_TOP_BLOCKER_SQL = (
    "/*rdst:observe*/ SELECT b.query,b.state,"
    "EXTRACT(EPOCH FROM (clock_timestamp()-b.xact_start)) FROM pg_stat_activity b "
    "WHERE b.pid = (SELECT unnest(pg_blocking_pids(w.pid)) FROM pg_stat_activity w "
    "WHERE w.datname=current_database() AND w.wait_event_type='Lock' "
    "AND w.pid<>pg_backend_pid() AND COALESCE(w.application_name,'') NOT LIKE 'rdst/%' "
    "ORDER BY w.query_start LIMIT 1) LIMIT 1"
)


def _top_blocker(target: str) -> dict[str, Any] | None:
    connection = None
    try:
        connection, engine = _default_connection_factory(target)
        if engine != "postgresql":
            return None
        from shared.query_registry.sql_normalizer import normalize_and_extract

        with IoDeadline(connection, READ_DEADLINE_S), connection.cursor() as cursor:
            # SET LOCAL inside a transaction ends with it, so a pooled server connection
            # never keeps the timeout.
            cursor.execute("BEGIN")
            cursor.execute("SET LOCAL statement_timeout = '2s'")
            cursor.execute(PG_TOP_BLOCKER_SQL)
            row = cursor.fetchone()
            cursor.execute("COMMIT")
        if row:
            text, _ = normalize_and_extract(row[0] or "", dialect="postgres")
            return {"query": text[:1024], "state": row[1],
                    "xact_age_s": float(row[2]) if row[2] is not None else None}
    except Exception:
        logger.debug("Couldn't read lock blocker for %s", target, exc_info=True)
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                logger.debug("Couldn't close blocker connection for %s", target, exc_info=True)
    return None


def _window_rows(store: MonitorStore, target: str, start: int, end: int,
                 res: int) -> list[dict[str, Any]]:
    return store.read("SELECT query_key,calls,total_ms,temp_blks FROM stmt_series WHERE target=? "
                      "AND res=? AND bucket_start>=? AND bucket_start<?",
                      (target, res, start, end))


def signed_in() -> bool:
    """Whether the account session yields a usable access token right now."""
    from shared import account_session

    try:
        return bool(account_session.access_token())
    except Exception:
        return False


def _scan_indexes(target: str) -> list[dict[str, str]]:
    connection, engine = _default_connection_factory(target)
    try:
        with IoDeadline(connection, READ_DEADLINE_S):
            return read_indexes(connection, engine)
    finally:
        try:
            if engine == "postgresql":
                connection.rollback()
        finally:
            connection.close()


def _scan_stats(target: str) -> dict[str, Any]:
    connection, engine = _default_connection_factory(target)
    try:
        with IoDeadline(connection, READ_DEADLINE_S):
            return read_stats(connection, engine)
    finally:
        try:
            if engine == "postgresql":
                connection.rollback()
        finally:
            connection.close()


def _check_database(target: str) -> dict[str, Any]:
    from features.monitor.report import collect_health

    return collect_health(target)


# How long a newly added database gets minute-by-minute retries for its first readings.
SETTLING_S = 3600
# How long a pass waits for the collector or sampler to finish with a database before
# leaving its reads to the next pass.
ADMIT_WAIT_S = 20


def _assess_in_background(targets: set[str]) -> None:
    from features.query_registry.assessment import query_assessment_worker

    query_assessment_worker.set_background_targets(targets)


class MonitorLoop:
    def __init__(self, store: MonitorStore | None = None, *, clock: Callable[[], float] = time.time,
                 index_reader: Callable[[str], list[dict[str, str]]] = _scan_indexes,
                 stats_reader: Callable[[str], dict[str, Any]] | None = None,
                 health_reader: Callable[[str], dict[str, Any]] | None = None,
                 judge: Callable[[list[dict[str, Any]], dict[str, Any]], Any] | None = None,
                 scheduler: Any = None, interval: int | None = None,
                 setup_checker: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
                 assessments: Callable[[set[str]], None] | None = None,
                 sampler: Any = None,
                 slot_refresh: Callable[[MonitorStore, set[str]], None] | None = None,
                 backoff: TargetBackoff | None = None,
                 ) -> None:
        self.store = store or MonitorStore()
        self.clock = clock
        self.backoff = backoff or (target_backoff if clock is time.time else TargetBackoff(clock))
        self.index_reader = index_reader
        # Statistics come from the same database as the index list.
        self.stats_reader = stats_reader or (_scan_stats if index_reader is _scan_indexes else None)
        # Settings and maintenance findings come from SQL checks, no model, every 15 minutes.
        self.health_reader = health_reader or (_check_database if index_reader is _scan_indexes
                                               else None)
        self.action_client = None
        self._ranked_at: dict[str, int] = {}
        self.judge = judge or AttentionJudge(self.store, clock=clock)
        self.scheduler = scheduler
        self.interval = max(1, interval if interval is not None else
                            int(os.environ.get("RDST_MONITOR_JUDGE_INTERVAL_S", "300")))
        self._task: asyncio.Task | None = None
        self._connection_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._guard = RunGuard()
        self._threads: set[asyncio.Task] = set()
        # Each database's cycle in flight, so a slow one isn't started twice.
        self._cycling: dict[str, asyncio.Task] = {}
        self._thread_local = threading.local()
        # Shortlisted rows held between AI checks when a target's attention_every_min
        # lever is longer than the code-rule cycle.
        self._pending: dict[str, dict[str, dict[str, Any]]] = {}
        self._last_judged: dict[str, int] = {}
        # The prerequisite check that explains a statement-source outage, run once per
        # outage (it opens connections) and kept until the source works again.
        self.setup_checker = setup_checker
        self._setup_failure: dict[str, dict[str, Any]] = {}
        # Monitored targets get their per-query Jev assessments without a browser open.
        self.assessments = assessments or _assess_in_background
        # Real parameter values for top-load queries, captured every few seconds.
        if sampler is None:
            from features.monitor.param_sampler import ParamSampler

            sampler = ParamSampler(self.store, clock=clock)
        self.sampler = sampler
        # Enabled databases keep their AI monitoring slots on the Key Service fresh.
        self.slot_refresh = slot_refresh or database_limit.refresh_claims
        # Reachability, debounced. The scheduler reports every collection; the loop's own
        # index scan is a second check.
        self.watch = ConnectionWatch(self.store, clock=clock, on_recovered=self._collect_soon)
        self._scheduler_wired = callable(getattr(scheduler, "set_outcome_listener", None))
        if self._scheduler_wired:
            scheduler.set_outcome_listener(self.watch.collection)

    def _read_stats(self, target: str) -> None:
        """Table, index and setting statistics; a failure here never marks the database down."""
        if self.stats_reader is None:
            return
        try:
            self.store.record_stats(target, self.stats_reader(target))
        except Exception:
            logger.warning("Statistics read failed for %s", target, exc_info=True)
            return
        now = int(self.clock())
        try:
            actions.refresh(self.store, target, now, database=self._database_check(target, now))
        except Exception:
            logger.exception("Action refresh failed for %s", target)
            return
        self._rank_actions(target)

    def _database_check(self, target: str, now: int) -> list[dict[str, Any]] | None:
        """Fresh settings and maintenance findings once they're due; None keeps the last."""
        if self.health_reader is None:
            return None
        check = self.store.database_check(target)
        if check is not None:
            # The first checks run as soon as the database is added, the next once a few
            # minutes of readings give rates, then every 15 minutes.
            enabled = self.store.enabled_at(target) or 0
            early = int(check["at"]) - enabled < actions.FIRST_DATABASE_CHECK_S
            every = actions.FIRST_DATABASE_CHECK_S if early else actions.DATABASE_CHECK_S
            if now - int(check["at"]) < every:
                return None
        from features.monitor import snapshot

        health = self.health_reader(target)
        if health.get("collection_error"):
            logger.debug("Database checks failed for %s: %s", target, health["collection_error"])
            return None
        found = snapshot.database_check(self.store, target, now, health)
        if health.get("section_errors") and check is not None:
            # A check that failed can't clear a finding: earlier findings it would have
            # confirmed stay until a complete pass.
            fresh = {item.get("id") for item in found}
            found += [item for item in check.get("actions") or [] if item.get("id") not in fresh]
        self.store.save_database_check(target, now, found)
        return found

    def _rank_actions(self, target: str) -> None:
        """Jev's read of the open actions, at the attention cadence while AI monitoring runs."""
        now = int(self.clock())
        interval = self.store.levers(target)["attention_every_min"] * 60
        if now < self._ranked_at.get(target, 0) + interval or not signed_in() or \
                database_limit.paused(self.store, target, now):
            return
        self._ranked_at[target] = now
        try:
            actions.jev_rank(self.store, target, now, self.action_client)
        except Exception as exc:
            # The rubric may not be served yet; the measured order stands until it is.
            self._ranked_at[target] = now + 3600
            logger.debug("Action ranking unavailable for %s: %s", target, exc)

    def _collect_soon(self, target: str) -> None:
        """After an outage, read the database now rather than at the degraded cadence."""
        collect_soon = getattr(self.scheduler, "collect_soon", None)
        if callable(collect_soon):
            collect_soon(target)

    def wake(self) -> None:
        """Start the next pass now (a database was just turned on); call from the event loop."""
        self._wake.set()

    def connection_changed(self, target: str) -> None:
        """Read the database with its saved settings on the next pass; call from the event
        loop."""
        self._setup_failure.pop(target, None)
        self.backoff.forget(target)
        self.watch.settings_changed(target)
        reconnect = getattr(self.sampler, "reconnect", None)
        if callable(reconnect):
            reconnect(target)
        self.wake()

    async def start(self) -> None:
        if self._task is None:
            self._guard = RunGuard()
            self._stop.clear()
            self._wake.clear()
            self._task = asyncio.create_task(self._run(), name="monitor-attention")
            self._connection_task = asyncio.create_task(self._run_connections(),
                                                        name="monitor-connections")
            await self.sampler.start()

    async def stop(self) -> None:
        global _active_loop
        if self._task is not None:
            self._stop.set()
            self._wake.set()
            self._guard.stopped.set()
            if self._connection_task is not None:
                self._connection_task.cancel()
                try:
                    await self._connection_task
                except asyncio.CancelledError:
                    pass
                self._connection_task = None
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            self.assessments(set())
            await self.sampler.stop()
            if self._threads:
                done, pending = await asyncio.wait(self._threads, timeout=15)
                if pending:
                    logger.warning("Monitor thread did not finish within shutdown grace")
        with _active_lock:
            if _active_loop is self:
                _active_loop = None

    async def _run_connections(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.watch.tick)
            except Exception:
                logger.warning("Connection deadlines not checked", exc_info=True)
            await asyncio.sleep(1)

    async def _run(self) -> None:
        while not self._stop.is_set():
            # A busy store (database is locked) skips this pass; the loop itself must survive.
            try:
                targets = await asyncio.to_thread(self.store.monitored_targets)
            except Exception:
                logger.warning("Monitored targets unavailable; retrying shortly", exc_info=True)
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=min(self.interval,
                                                                          TARGETS_RETRY_S))
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                continue
            # A database removed or turned off leaves nothing behind for one re-added under
            # the same name.
            for state in (self._pending, self._ranked_at, self._last_judged, self._setup_failure):
                for gone in set(state) - set(targets):
                    state.pop(gone, None)
            try:
                self.assessments(targets)
            except Exception:
                logger.debug("Background assessment admission failed", exc_info=True)
            # Databases cycle side by side, so a slow or hung one only delays itself; one
            # whose last cycle is still running sits this pass out.
            started = []
            for target in targets:
                if target in self._cycling:
                    continue
                guard = self._guard
                thread = asyncio.create_task(asyncio.to_thread(
                    guard.run, target, self._guarded_cycle, guard, target))
                self._threads.add(thread)
                self._cycling[target] = thread
                def finished(done: asyncio.Task, target: str = target) -> None:
                    self._threads.discard(done)
                    self._cycling.pop(target, None)
                    if not done.cancelled() and (error := done.exception()) is not None:
                        logger.warning("Monitor cycle failed for %s", target, exc_info=error)
                thread.add_done_callback(finished)
                started.append(thread)
            if started:
                # A wake (a database added or turned on) starts its cycle without waiting for
                # slow ones still running here.
                finished_all = asyncio.ensure_future(
                    asyncio.wait([asyncio.shield(thread) for thread in started]))
                woken = asyncio.ensure_future(self._wake.wait())
                try:
                    await asyncio.wait({finished_all, woken}, timeout=self.interval,
                                       return_when=asyncio.FIRST_COMPLETED)
                finally:
                    finished_all.cancel()
                    woken.cancel()
            # The storage budget is kept here, between passes, in short transactions.
            try:
                await asyncio.to_thread(self.store.maintain)
            except Exception:
                logger.warning("Monitor storage maintenance failed", exc_info=True)
            try:
                await asyncio.to_thread(self.slot_refresh, self.store, targets)
            except Exception:
                logger.debug("Monitoring slot refresh failed", exc_info=True)
            try:
                timeout = self.interval
                # A database just added, or whose first statistics read failed, goes again
                # within a minute rather than waiting out a whole pass.
                settling = await asyncio.to_thread(self._settling, targets)
                if settling or any(target not in self._last_judged for target in targets):
                    timeout = min(60, timeout)
                await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    def _settling(self, targets: set[str]) -> bool:
        """A database in its first hour without its first statistics or database checks yet;
        one that stays unreadable past that waits for the normal pass."""
        if self.stats_reader is None:
            return False
        now = int(self.clock())
        read = {row["target"] for row in self.store.read("SELECT target FROM stats_meta")}

        def unsettled(target: str) -> bool:
            if target not in read or self.health_reader is None:
                return target not in read
            check = self.store.database_check(target)
            # Only the check at add time has run: the next one, with rates, comes due soon.
            return check is None or actions._measuring(self.store, target)

        return any(now - (self.store.enabled_at(target) or 0) < SETTLING_S and unsettled(target)
                   for target in targets)

    def _guarded_cycle(self, guard: RunGuard, target: str) -> None:
        self._thread_local.guard = guard
        try:
            self.cycle(target)
        finally:
            del self._thread_local.guard

    def _admit(self, target: str) -> bool:
        """Admission for this pass's reads. The collector and sampler hold it for a few seconds
        at a time, so a busy database is waited for rather than skipped until the next pass."""
        deadline = time.monotonic() + ADMIT_WAIT_S
        while True:
            if self.backoff.acquire(target):
                return True
            if not self.backoff.busy(target) or time.monotonic() >= deadline:
                if self.backoff.busy(target):
                    logger.info("Monitoring read for %s postponed: another read held the "
                                "database for %ss", target, ADMIT_WAIT_S)
                return False
            self._check()
            time.sleep(0.25)

    def _check(self) -> None:
        guard = getattr(self._thread_local, "guard", None)
        if guard is not None:
            guard.check()

    def cycle(self, target: str) -> None:
        enabled = self.store.read("SELECT value FROM monitor_setting WHERE target=? AND key='enabled'",
                                  (target,))
        if enabled and enabled[0]["value"] == "0":
            return
        now = int(self.clock())
        changes = []
        connection_failed = self.backoff.failed(target)
        read = self._admit(target)
        if read:
            try:
                indexes = self.index_reader(target)
                self._check()
                # An index that appeared closes its action as applied when the statistics
                # read below refreshes the actions; their check measures it from there.
                changes = self.store.sync_indexes(target, indexes, now)
                connection_failed = False
                self._read_stats(target)
            except IndexesBusy:
                # The database answered; a held lock only postponed this index read.
                changes = []
                connection_failed = False
            except Exception:
                connection_failed = True
            finally:
                self.backoff.finish(target, success=not connection_failed)
            if self._scheduler_wired:
                self.watch.observe(target, not connection_failed)
        self._check()
        current = _window_rows(self.store, target, now - 300, now + 1, 60)
        history = {"1h": _window_rows(self.store, target, now - 3600 - 300, now - 300, 60),
                   "1d": _window_rows(self.store, target, now - 86400 - 300, now - 300, 300),
                   "7d": _window_rows(self.store, target, now - 7 * 86400 - 300, now - 300, 3600)}
        known = self.store.read("SELECT query_key,first_seen,text FROM query_text WHERE target=?",
                                (target,))
        first_seen = {row["query_key"]: row["first_seen"] for row in known}
        # Only the application's reads and writes move alerts: DDL, maintenance and RDST's
        # own statements are never shortlisted or counted in a group.
        texts = {row["query_key"]: row["text"] for row in known}
        system = self.store.system_queries(target)
        members = {key for key in {row["query_key"] for rows in (current, *history.values())
                                   for row in rows}
                   if key not in system and alert_member(texts.get(key, ""))}
        current = [row for row in current if row["query_key"] in members]
        history = {window: [row for row in rows if row["query_key"] in members]
                   for window, rows in history.items()}
        for window, seconds in (("1h", 3600), ("1d", 86400), ("7d", 7 * 86400)):
            history[window] = [row for row in history[window]
                               if first_seen.get(row["query_key"], 0) <= now - seconds - 300]
        stuck = {row["query_key"] for row in self.store.read(
            "SELECT DISTINCT query_key FROM stuck_sample WHERE target=? AND ts>=? "
            "AND query_key IS NOT NULL AND duration_s>=60", (target, now - 300))}
        recent_keys = {row["query_key"] for row in _window_rows(
            self.store, target, now - 1800, now + 1, 60) if row["calls"]}
        settle_seconds = max(1800, int(os.environ.get(
            "RDST_MONITOR_ATTENTION_SETTLE_S", "86400")))
        settled_keys = set()
        for res, retention in ((60, 48 * 3600), (300, 7 * 86400),
                               (3600, 90 * 86400), (86400, settle_seconds)):
            span = min(settle_seconds, retention)
            settled_keys.update(row["query_key"] for row in _window_rows(
                self.store, target, now - span - res + 1, now + 1, res) if row["calls"])
            if settle_seconds <= retention:
                break
        current_keys = {row["query_key"] for row in current}
        for window in history:
            history[window] = [row for row in history[window]
                               if row["query_key"] in current_keys or
                               row["query_key"] in settled_keys]
        # Rates count the minutes RDST was collecting, so a restart doesn't look like a jump.
        collected = {row["bucket_start"] // 60 * 60 for row in self.store.read(
            "SELECT bucket_start FROM db_series WHERE target=? AND res=60 AND bucket_start>=? "
            "AND bucket_start<? UNION SELECT bucket_start FROM stmt_series WHERE target=? "
            "AND res=60 AND bucket_start>=? AND bucket_start<?",
            (target, now - 3600 - 300, now + 1) * 2)}
        current_minutes = sum(1 for ts in collected if ts >= now - 300)
        hour_minutes = sum(1 for ts in collected if ts < now - 300)
        # Only for a target with a mostly collected hour: a new target's first minutes (or a
        # backdated sample) stand for nothing. The hour's rate counts its collected minutes
        # once there are ten, so a restart inside the hour doesn't dilute the normal level
        # into a false traffic jump that never reads as recovered.
        established = hour_minutes >= 30
        measured = baselines(current + [
            {"query_key": key, "calls": 0, "total_ms": 0}
            for key in stuck if key not in {row["query_key"] for row in current}
        ], history,
            current_seconds=current_minutes * 60 if established and current_minutes else 300,
            window_seconds={"1h": hour_minutes * 60}
            if HOUR_RATE_MIN_MINUTES <= hour_minutes < 60 else None)
        stopped_keys = {row["query_key"] for row in measured if row["query_key"] not in recent_keys}
        # Only changes that matter reach Jev: queries that carry load, new heavy ones and big
        # absolute changes, minus a query's usual recurring bursts.
        rel = relevance(self.store, target, now)
        gated: dict[str, dict[str, Any]] = {}
        recurring_notes: dict[str, dict[str, Any]] = {}
        schedule = RecurringCheck(self.store, target, now)
        gate = attention_gate(rel, sum(float(row["total_ms"] or 0) for row in measured),
                              schedule.check, recurring_notes, schedule.intermittent)
        chosen = shortlist(measured, stuck, stopped_keys=stopped_keys, gate=gate, gated=gated)
        episodes = self.store.read("SELECT query_key,kind,baseline_mean_ms,settled_at,judged_qps "
                                   "FROM attention_episode "
                                   "WHERE target=?", (target,))
        by_key = {row["query_key"]: row for row in measured}
        cleared = [(row["query_key"], row["kind"]) for row in episodes
                   if (now - row["settled_at"] >= settle_seconds and
                       row["query_key"] not in current_keys and
                       row["query_key"] not in settled_keys)
                   or (row["kind"] == "query_stopped_running" and
                       row["query_key"] in recent_keys)
                   or (row["kind"] == "query_improved" and
                       by_key.get(row["query_key"], {}).get("calls", 0) > 0 and
                       by_key[row["query_key"]]["mean_ms"] >=
                       row["baseline_mean_ms"] * 0.8)]
        if cleared:
            self.store.clear_attention_episodes(target, cleared)
        active = {(row["query_key"], row["kind"]): row for row in episodes
                  if (row["query_key"], row["kind"]) not in cleared}
        chosen = [row for row in chosen
                  if not (row["calls"] == 0 and
                          (row["query_key"], "query_stopped_running") in active)
                  and not (row["reason"] == "mover" and
                           (episode := active.get((row["query_key"], "query_improved"))) and
                           row["calls"] and row["mean_ms"] < episode["baseline_mean_ms"] * 0.8
                           and episode["judged_qps"] > 0 and
                           row["qps"] < episode["judged_qps"] * 1.2)]
        db = self.store.read("SELECT * FROM db_series WHERE target=? "
                             "AND res=60 AND bucket_start>=? ORDER BY bucket_start DESC LIMIT 1",
                             (target, now - 300))
        near = bool(db and db[0]["max_conns"] and db[0]["active_conns"] is not None
                    and db[0]["active_conns"] >= db[0]["max_conns"] * 0.9)
        if near:
            self._check()
            self.store.upsert_item(target, "connections_near_max", "database", 0.9,
                                   "Connections near maximum", db[0], now)
        else:
            self._check()
            self.store.action_item_for_subject(target, "connections_near_max", "database", now)
        self._check()
        self._lock_contention(target, now)
        self._check()
        self._monitoring_issues(target, now, connection_failed)
        self._check()
        if read or connection_failed:
            self._pulse(target, now, "index_scan", not connection_failed,
                        "Couldn't read indexes" if connection_failed else _index_detail(changes))
        context = {"target": target, "index_changes": changes, "database": db[0] if db else {},
                   "inbox": self.store.inbox(target=target), "measured": measured,
                   "relevance": rel, "gated": gated, "recurring": recurring_notes}
        signed_in = database_limit._signed_in()
        if not signed_in:
            self._last_judged.pop(target, None)
        interval = self.store.levers(target)["attention_every_min"] * 60
        # The AI check keeps its own cadence however often passes run (a settling database
        # shortens passes to a minute).
        if now - self._last_judged.get(target, 0) < interval - 30:
            pending = self._pending.setdefault(target, {})
            pending.update({row["query_key"]: row for row in chosen})
            due = self._last_judged[target] + interval
            self._pulse(target, now, "attention", True,
                        f"Checked {len(measured)} {'query' if len(measured) == 1 else 'queries'}, next AI check at "
                        f"{time.strftime('%H:%M', time.localtime(due))}")
            return
        held = self._pending.pop(target, {})
        if held:
            current_keys = {row["query_key"] for row in chosen}
            chosen = chosen + [row for key, row in held.items() if key not in current_keys]
        if signed_in:
            self._last_judged[target] = now
        self._check()
        self.judge(chosen, context)
        # A check that failed retries on the client's own backoff, not the five-minute cadence.
        last = self.store.read("SELECT outcome FROM judge_run WHERE target=? ORDER BY id DESC "
                               "LIMIT 1", (target,))
        if last and last[0]["outcome"] in ("error", "backoff"):
            self._last_judged.pop(target, None)
        self._attention_pulse(target, now, len(measured))

    def _lock_contention(self, target: str, now: int) -> None:
        rows = self.store.read("SELECT bucket_start,lock_waits FROM db_series WHERE target=? "
                               "AND res=60 AND bucket_start<=? ORDER BY bucket_start DESC LIMIT 60",
                               (target, now))
        if not rows or now - rows[0]["bucket_start"] >= 120 or rows[0]["lock_waits"] is None:
            return
        open_minutes, recovery_minutes = LOCK_WAIT_OPEN_MINUTES, LOCK_WAIT_RECOVERY_MINUTES
        if os.environ.get("RDST_TESTING") == "1":
            open_minutes = max(1, int(os.environ.get(
                "RDST_MONITOR_LOCK_WAIT_OPEN_MINUTES", str(open_minutes))))
            recovery_minutes = max(1, int(os.environ.get(
                "RDST_MONITOR_LOCK_WAIT_RECOVERY_MINUTES", str(recovery_minutes))))
        waiting = rows[0]["lock_waits"] >= LOCK_WAIT_THRESHOLD
        count = 0
        for index, row in enumerate(rows):
            if (row["bucket_start"] != rows[0]["bucket_start"] - index * 60 or
                    row["lock_waits"] is None or
                    (row["lock_waits"] >= LOCK_WAIT_THRESHOLD) != waiting):
                break
            count += 1
        items = self.store.read("SELECT * FROM inbox_item WHERE target=? "
                                "AND type='lock_contention' AND subject='database'", (target,))
        item = items[0] if items else None
        body = json.loads(item["body_json"]) if item else {}
        if waiting and (count >= open_minutes or item and item["state"] != "resolved"):
            opening = item is None or item["state"] == "resolved"
            if opening:
                body = {"started_at": rows[0]["bucket_start"] - (count - 1) * 60,
                        "at_filing": {"lock_waits": rows[0]["lock_waits"]}}
                blocker = _top_blocker(target)
                if blocker:
                    body["blocker"] = blocker
            body["lock_waits"] = rows[0]["lock_waits"]
            body["minutes"] = max(count, (rows[0]["bucket_start"] - body.get(
                "started_at", rows[0]["bucket_start"])) // 60 + 1)
            self.store.upsert_item(target, "lock_contention", "database", 0.9,
                                   "Queries waiting on locks", body, now, reopen_resolved=True)
        elif not waiting and count >= recovery_minutes and item and item["state"] != "resolved":
            body["resolution"] = {"before": body.get("at_filing", {}),
                                  "after": {"lock_waits": rows[0]["lock_waits"]}}
            body["resumed_at"] = now
            self.store.update_item_body(item["id"], body)
            self.store.action_item(item["id"], "resolve", now, resolved_by="code",
                                   measured_recovery=True)

    def _pulse(self, target: str, now: int, kind: str, ok: bool, detail: str) -> None:
        try:
            self.store.record_pulse(target, now, kind, ok, detail)
        except Exception:
            logger.debug("Monitor pulse not recorded for %s", target, exc_info=True)

    def _attention_pulse(self, target: str, now: int, measured: int) -> None:
        runs = self.store.read("SELECT outcome FROM judge_run WHERE target=? AND ts=? "
                               "ORDER BY id DESC LIMIT 1", (target, now))
        outcome = runs[0]["outcome"] if runs else "noop"
        filed = self.store.read(
            "SELECT COUNT(DISTINCT e.item_id) n FROM inbox_event e JOIN inbox_item i "
            "ON i.id=e.item_id WHERE i.target=? AND e.ts=? AND e.kind IN "
            "('created','worse','reopened')", (target, now))[0]["n"]
        checked = f"Checked {measured} {'query' if measured == 1 else 'queries'}"
        if outcome == "daily_cap":
            self._pulse(target, now, "attention", False, "Daily AI check limit reached")
        elif outcome == "signed_out":
            self._pulse(target, now, "attention", True, "Sign in to turn on AI monitoring")
        elif outcome == "local_only":
            self._pulse(target, now, "attention", True, f"{checked}, AI checks are off")
        elif outcome == "allowance":
            self._pulse(target, now, "attention", True,
                        f"{checked}, AI checks paused at the trial limit")
        elif outcome in ("error", "backoff"):
            self._pulse(target, now, "attention", False, "AI check unavailable, retrying soon")
        elif filed:
            self._pulse(target, now, "attention", True,
                        f"{checked}, filed {filed} {'item' if filed == 1 else 'items'}")
        else:
            self._pulse(target, now, "attention", True, f"{checked}, nothing new")

    def _failed_setup_check(self, target: str, settings: dict[str, Any]) -> dict[str, Any]:
        """Which prerequisite fails, from the same check the setup screens run."""
        if not self.backoff.acquire(target):
            return {}
        try:
            checker = self.setup_checker
            if checker is None:
                from features.configure.prerequisites import check_target as checker
            return _first_failed_check(checker(target, dict(settings)))
        except Exception as exc:
            logger.debug("prerequisite check for %s failed: %s", target, exc)
            return {}
        finally:
            self.backoff.release(target)

    def _monitoring_issues(self, target: str, now: int, connection_failed: bool) -> None:
        from shared.config.targets import TargetsConfig

        try:
            cfg = TargetsConfig()
            cfg.load()
            settings = cfg.get(target) or {}
        except Exception:
            settings = {}
        env = settings.get("password_env")
        missing = bool(env and not os.environ.get(env))
        self._check()
        if missing:
            self.store.upsert_item(target, "monitoring_issue", "password_missing", 1.0,
                                   "Database password not set",
                                   {"target": target, "password_env": env}, now)
        else:
            self.store.action_item_for_subject(target, "monitoring_issue", "password_missing", now)
        try:
            collector = query_discovery.collector_for(target)
            snapshot = getattr(collector, "_snapshot", {})
        except Exception:
            snapshot = {}
            connection_failed = True
        warning = snapshot.get("capture_warning") or {}
        self._check()
        missing_source = STATEMENT_SOURCES.get(warning.get("source"))
        if missing_source:
            if target not in self._setup_failure:
                self._setup_failure[target] = self._failed_setup_check(target, settings)
            body = dict(warning)
            if self._setup_failure[target]:
                body["failed_check"] = self._setup_failure[target]
            self.store.upsert_item(target, "monitoring_issue", missing_source, 1.0,
                                   f"{warning['source']} isn't available", body, now)
        else:
            self._setup_failure.pop(target, None)
        # Only a clean reading clears a missing source; before the first reading since a
        # restart, the snapshot says nothing about it yet.
        read_cleanly = snapshot.get("state") == "watching"
        for subject in STATEMENT_SOURCES.values():
            if subject != missing_source and read_cleanly:
                self.store.action_item_for_subject(target, "monitoring_issue", subject, now)
        # A scheduler that doesn't report its collections is read here instead.
        failed = bool(not self._scheduler_wired and self.scheduler and
                      target in self.scheduler._schedules and
                      self.scheduler._schedules[target].consecutive_failures)
        self._check()
        # A missing password has its own item; it isn't also a lost connection.
        if not missing and not self._scheduler_wired:
            self.watch.observe(target, not (failed or connection_failed), now)
        # Sign-in problems clear as soon as the account session works again, whether or not
        # an AI check or an analysis has run since.
        waiting = [item for item in self.store.inbox(target=target)
                   if item["type"] == "monitoring_issue" and
                   item["subject"] in ("jev_login", "llm_login") and
                   item["state"] not in ("resolved", "dismissed")]
        if waiting and signed_in():
            for item in waiting:
                self.store.action_item(item["id"], "resolve", now, resolved_by="code")


# The statement-statistics source each engine needs, and the monitoring issue filed while
# it can't be read.
STATEMENT_SOURCES = {"pg_stat_statements": "pg_stat_statements_missing",
                     "performance_schema": "performance_schema_missing"}


def _first_failed_check(result: dict[str, Any]) -> dict[str, Any]:
    """The check to name in the inbox: the first required one that fails."""
    for check in result.get("checks") or []:
        if check.get("blocking") and check.get("status") in ("missing", "unknown"):
            return {key: check.get(key) for key in ("id", "label", "detail", "fix")}
    return {}


def _index_detail(changes: list[dict[str, Any]]) -> str:
    if not changes:
        return "No index changes"
    if len(changes) == 1:
        change = changes[0]
        return f"{change['index_name']} {change['kind']}"
    return f"{len(changes)} index changes"


def _bound_to_running_loop(task: asyncio.Task | None) -> bool:
    """A singleton left behind by a closed event loop can't be reused."""
    return task is None or (not task.done() and task.get_loop() is asyncio.get_running_loop())


async def start_monitor_loop(scheduler: Any = None) -> MonitorLoop:
    """Return the process's running loop, starting it on first monitored target."""
    global _active_loop
    with _active_lock:
        if _active_loop is None or not _bound_to_running_loop(_active_loop._task):
            _active_loop = MonitorLoop(scheduler=scheduler)
        loop = _active_loop
    await loop.start()
    return loop


def connection_changed(target: str, scheduler: Any = None) -> None:
    """A database's connection settings changed, or its monitoring was turned off or on: every
    reader drops its open connection and the next reading uses the saved settings, so a
    failing connection shows as unreachable at once. Call from the event loop."""
    query_discovery.reconnect(target)
    target_backoff.forget(target)
    loop = _active_loop
    if loop is not None:
        loop.connection_changed(target)
    collect_soon = getattr(scheduler, "collect_soon", None)
    if callable(collect_soon):
        collect_soon(target)


async def setup_check_passed(target: str, scheduler: Any = None) -> None:
    """The monitoring prerequisites pass for a database. A statement-source gap its collector
    cached before the fix stops blocking: the setup item closes and the database is read now,
    as after a connection edit. Call from the event loop."""
    if not query_discovery.setup_passed(target):
        return
    loop = _active_loop
    if loop is not None:
        def resolve() -> None:
            now = int(loop.clock())
            for subject in STATEMENT_SOURCES.values():
                loop.store.action_item_for_subject(target, "monitoring_issue", subject, now)

        try:
            await asyncio.to_thread(resolve)
        except Exception:
            # The next clean reading closes the item instead.
            logger.debug("Setup item not closed for %s", target, exc_info=True)
    connection_changed(target, scheduler)


def cycling(target: str) -> bool:
    """A monitor pass is checking this database right now."""
    loop = _active_loop
    return loop is not None and target in loop._cycling

"""The daily `monitoring_daily_summary` event: one per monitored database per day.

Counts only, read from monitor.db and agent_activity.db. The database is named by its
salted `database_id`; no query text, names or hosts leave the machine.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from shared import product_telemetry
from shared.monitor.monitor_store import MonitorStore, default_monitor_db_path

logger = logging.getLogger(__name__)

PERIOD_S = 86400
CHECK_EVERY_S = 3600
FIRST_CHECK_S = 300


def _one(store: Any, sql: str, params: tuple[Any, ...]) -> Any:
    rows = store.read(sql, params)
    if not rows:
        return 0
    return next(iter(rows[0].values())) or 0


def _ai_usage(target: str, start: int, end: int) -> tuple[int, float]:
    try:
        from shared.agent_activity.store import AgentActivityStore

        rows = AgentActivityStore().read(
            "SELECT COALESCE(SUM(ai_call_count),0) AS calls, COALESCE(SUM(cost_usd),0) AS cost "
            "FROM activity_entry WHERE target=? AND ts>=? AND ts<?", (target, start, end))
        return int(rows[0]["calls"]), float(rows[0]["cost"])
    except Exception:
        logger.debug("agent activity unavailable for the daily summary", exc_info=True)
        return 0, 0.0


def summary(store: MonitorStore, target: str, start: int, end: int) -> dict[str, Any]:
    """What monitoring did for one database in [start, end)."""
    window = (target, start, end)
    jobs = {row["state"]: int(row["n"]) for row in store.read(
        "SELECT state, COUNT(*) n FROM analyze_job WHERE target=? AND finished_at>=? "
        "AND finished_at<? GROUP BY state", window)}
    judge = {row["outcome"]: int(row["n"]) for row in store.read(
        "SELECT outcome, COUNT(*) n FROM judge_run WHERE target=? AND ts>=? AND ts<? "
        "AND request_json IS NOT NULL GROUP BY outcome", window)}
    filed = {row["kind"]: int(row["n"]) for row in store.read(
        "SELECT e.kind, COUNT(*) n FROM inbox_event e JOIN inbox_item i ON i.id=e.item_id "
        "WHERE i.target=? AND e.ts>=? AND e.ts<? AND e.kind IN "
        "('created','reopened','worse','notified') GROUP BY e.kind", window)}
    collected_minutes = int(_one(store, "SELECT COUNT(DISTINCT bucket_start) FROM db_series "
                                        "WHERE target=? AND res=60 AND bucket_start>=? "
                                        "AND bucket_start<?", window))
    ai_calls, ai_cost = _ai_usage(target, start, end)
    enabled_at = store.enabled_at(target)
    minutes = max(1, (end - start) // 60)
    return {
        "period_hours": round((end - start) / 3600, 1),
        "queries_seen": int(_one(store, "SELECT COUNT(DISTINCT query_key) FROM stmt_series "
                                        "WHERE target=? AND res=60 AND bucket_start>=? "
                                        "AND bucket_start<? AND calls>0", window)),
        "queries_known": int(_one(store, "SELECT COUNT(*) FROM query_text WHERE target=?",
                                  (target,))),
        "new_queries": int(_one(store, "SELECT COUNT(*) FROM query_text WHERE target=? "
                                       "AND first_seen>=? AND first_seen<?", window)),
        "analyses_done": jobs.get("done", 0),
        "analyses_failed": jobs.get("failed", 0),
        "analyses_skipped": jobs.get("skipped", 0),
        "analyses_queued": int(_one(store, "SELECT COUNT(*) FROM analyze_job WHERE target=? "
                                           "AND state='queued'", (target,))),
        "ai_checks": sum(judge.values()),
        "ai_check_errors": judge.get("error", 0) + judge.get("filing_error", 0),
        "inbox_items": filed.get("created", 0) + filed.get("reopened", 0) + filed.get("worse", 0),
        "inbox_items_new": filed.get("created", 0),
        "notifications": filed.get("notified", 0),
        "inbox_open": int(_one(store, "SELECT COUNT(*) FROM inbox_item WHERE target=? "
                                      "AND state IN ('new','seen')", (target,))),
        "reports": int(_one(store, "SELECT COUNT(*) FROM report_run WHERE target=? AND ts>=? "
                                   "AND ts<? AND state='done'", window)),
        "ai_calls": ai_calls,
        "ai_cost_usd": round(ai_cost, 4),
        "collection_errors": int(_one(store, "SELECT COUNT(*) FROM monitor_pulse WHERE "
                                             "target=? AND kind='collect' AND ok=0 AND ts>=? "
                                             "AND ts<?", window)),
        "collected_minutes": collected_minutes,
        "uptime_pct": round(min(100.0, collected_minutes * 100 / minutes), 1),
        "days_monitored": round((end - enabled_at) / 86400, 1) if enabled_at else None,
    }


def send_due(store: MonitorStore | None = None, *, now: float | None = None,
             send: Callable[[str, dict[str, Any]], None] = product_telemetry.capture) -> int:
    """Send the summary for each monitored database whose last one is a day old.

    A database's clock starts the first time it's seen here, so its first summary covers a
    full day. Returns how many were sent.
    """
    if not product_telemetry.enabled():
        return 0
    if store is None:
        # Never create monitor.db for an install that has never monitored anything.
        if not default_monitor_db_path().exists():
            return 0
        store = MonitorStore()
    now = int(time.time() if now is None else now)
    sent = 0
    for target in sorted(store.monitored_targets()):
        database_id = product_telemetry.database_id(target)
        if not database_id:
            continue
        key = f"daily_summary:{database_id}"
        last = product_telemetry.state_value(key)
        if last is None:
            product_telemetry.set_state_value(key, now)
            continue
        if now - int(last) < PERIOD_S:
            continue
        product_telemetry.set_state_value(key, now)
        try:
            counts = summary(store, target, now - PERIOD_S, now)
        except Exception:
            logger.warning("Daily monitoring summary failed", exc_info=True)
            continue
        send("monitoring_daily_summary", {
            "database_id": database_id, "engine": product_telemetry.engine_of(target),
            "monitored_count": len(store.monitored_targets()),
            "own_key": product_telemetry.own_key(), **counts})
        sent += 1
    return sent


class DailySummaries:
    """Checks hourly, in the background, whether a database's daily summary is due."""

    def __init__(self, store: MonitorStore | None = None) -> None:
        self.store = store
        self._task: asyncio.Task | None = None

    def start(self) -> "DailySummaries":
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="monitor-daily-summary")
        return self

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        await asyncio.sleep(FIRST_CHECK_S)
        while True:
            try:
                await asyncio.to_thread(send_due, self.store)
            except Exception:
                logger.warning("Daily monitoring summary check failed", exc_info=True)
            await asyncio.sleep(CHECK_EVERY_S)


def analysis_finished(job: dict[str, Any], outcome: dict[str, Any], duration_s: float) -> None:
    """`deep_analysis_completed` for a background job that finished or failed."""
    if outcome.get("state") not in ("done", "failed") or not product_telemetry.enabled():
        return
    score = None
    if outcome.get("state") == "done" and outcome.get("analysis_ref"):
        try:
            from features.monitor.analysis_digest import digest_for_ref

            digest = digest_for_ref(job["target"], job["query_key"], outcome["analysis_ref"],
                                    outcome.get("mode"))
            score = (digest or {}).get("efficiency_score")
        except Exception:
            logger.debug("no score for the analysis event", exc_info=True)
    # Background analyses also count as analyze runs (source=monitor), so the first one
    # sends the existing first_analyze event like a first CLI or web analysis does.
    try:
        product_telemetry._telemetry().track_analyze(
            query_hash=str(job["query_key"]), mode=outcome.get("mode") or "monitor",
            duration_ms=int(duration_s * 1000), success=outcome["state"] == "done",
            error_type=None if outcome["state"] == "done"
            else product_telemetry._error_class(outcome.get("error")),
            target_engine=product_telemetry.engine_of(job["target"]), source="monitor")
    except Exception:
        logger.debug("analyze_run not sent for a background analysis", exc_info=True)
    product_telemetry.deep_analysis_completed(
        job["target"], ok=outcome["state"] == "done", duration_s=duration_s,
        reason=str(job.get("reason") or "unknown"), mode=outcome.get("mode"),
        param_source=outcome.get("param_source"), user_requested=job.get("reason") == "user",
        score=score, error=outcome.get("error"))

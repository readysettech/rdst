"""Monitor history and per-target switches."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from functools import lru_cache
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from features.monitor.api import models, views
from features.monitor.api.models import documented
from features.monitor.connection import ANSWERED
from features.monitor.naming import target_names
from features.monitor.presentation import RECOVERED_WINDOW_S
from features.query_registry.discovery import query_discovery
from features.schema.assessment_context import referenced_tables
from shared import product_telemetry
from shared.monitor.monitor_store import LocalMonitorLimitError, MonitorStore

router = APIRouter()
logger = logging.getLogger(__name__)
WINDOWS = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "90d": 90 * 86400}
RangeParam = Literal["5m", "15m", "1h", "24h", "7d", "30d", "90d", "all"]
# `from` of a brushed [from, to] window, epoch seconds.
FromParam = Annotated[int | None, Query(alias="from")]


def _now() -> int:
    return int(time.time())


def _store() -> MonitorStore:
    return MonitorStore()


def _period(window: str) -> tuple[int, int, int]:
    if window not in WINDOWS:
        raise HTTPException(422, "Unsupported window")
    seconds = WINDOWS[window]
    now = _now()
    res = 60 if seconds <= 48 * 3600 else 300 if seconds <= 7 * 86400 else 3600
    return now, seconds, res


def _points(store: MonitorStore, target: str, res: int, start: int, end: int):
    return store.read(
        "SELECT * FROM db_series WHERE target=? AND res=? "
        "AND bucket_start>=? AND bucket_start<? ORDER BY bucket_start",
        (target, res, start, end),
    )


def _legacy_tiles(store: MonitorStore, target: str, window: str) -> dict:
    """Pre-contract tiles and sparkline read by the classic Database page."""
    now, seconds, res = _period(window if window in WINDOWS else "24h")
    rows = _points(store, target, res, now - seconds, now + 1)
    calls = sum(row["calls"] for row in rows)
    total_ms = sum(row["total_ms"] for row in rows)
    latest = rows[-1] if rows else {}
    observed_span = (
        max(res, latest["bucket_start"] - rows[0]["bucket_start"] + res)
        if rows else seconds
    )
    live_rows = store.read(
        "SELECT * FROM db_series WHERE target=? AND res=60 AND bucket_start>=? "
        "ORDER BY bucket_start DESC LIMIT 1", (target, now - 120))
    live = live_rows[0] if live_rows else {}
    return {
        "tiles": {
            "qps": calls / observed_span, "mean_ms": total_ms / calls if calls else 0.0,
            "qps_now": live.get("calls", 0) / 60,
            "mean_ms_now": live["total_ms"] / live["calls"] if live.get("calls") else 0.0,
            "db_time_ms_now": live.get("total_ms", 0),
            "active_conns_now": live.get("active_conns"),
            "lock_waits_now": live.get("lock_waits"),
            "max_conns_now": live.get("max_conns"),
            "cache_hit_ratio_now": live.get("cache_hit_ratio"),
            "db_time_ms": total_ms,
            "active_conns": latest.get("active_conns"),
            "lock_waits": latest.get("lock_waits"),
            "max_conns": latest.get("max_conns"),
            "cache_hit_ratio": latest.get("cache_hit_ratio"),
            "xact_commit": sum(row["xact_commit"] or 0 for row in rows),
            "xact_rollback": sum(row["xact_rollback"] or 0 for row in rows),
            "deadlocks": sum(row["deadlocks"] or 0 for row in rows),
        },
        "sparkline": [
            {"ts": row["bucket_start"], "qps": row["calls"] / res,
             "total_ms": row["total_ms"]} for row in rows
        ],
    }


def _range(range: str | None, window: str | None) -> str:
    try:
        return views.resolve_range(range, window)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/targets/{target}/health", **documented(models.MonitorHealth))
def health(target: str, request: Request, range: RangeParam | None = None, window: str | None = None,
           from_: FromParam = None, to: int | None = None):
    """Health over a range. `window=` is the pre-contract name, kept for one release."""
    selected = _range(range, window)
    store = _store()
    return {**views.health(store, target, selected, _now(), window=_window(from_, to)), "window": selected,
            **_legacy_tiles(store, target, selected),
            **_readings(store, target, getattr(request.app.state, "monitor_scheduler", None)),
            "outages": _outages(store, target)}


def _outages(store: MonitorStore, target: str) -> list[dict]:
    """Spans RDST was running but couldn't reach the database: each lost-connection
    episode, from the last reading before it to when monitoring resumed (null while
    ongoing). Chart gaps outside these have no known cause."""
    rows = store.read("SELECT e.ts,e.kind,e.detail_json,i.id,i.body_json,i.state "
                      "FROM inbox_event e JOIN inbox_item i ON i.id=e.item_id "
                      "WHERE i.target=? AND i.type='monitoring_issue' "
                      "AND i.subject='lost_connection' "
                      "AND e.kind IN ('created','reopened','resolved') ORDER BY e.id",
                      (target,))
    spans: list[dict] = []
    for row in rows:
        if row["kind"] in ("created", "reopened"):
            if spans and spans[-1]["end"] is None:
                continue
            try:
                detail = json.loads(row["detail_json"])
            except ValueError:
                detail = {}
            start = detail.get("gap_from") or detail.get("failed_since") or row["ts"]
            spans.append({"start": int(start), "end": None, "cause": "unreachable",
                          "item_id": row["id"]})
        elif spans and spans[-1]["end"] is None:
            spans[-1]["end"] = row["ts"]
    if spans and rows:
        body = json.loads(rows[-1]["body_json"])
        last = spans[-1]
        if last["end"] is not None and body.get("gap_to") and body.get("gap_from"):
            last["start"], last["end"] = int(body["gap_from"]), int(body["gap_to"])
    return spans


SETUP_BLOCKERS = {
    "password_missing": "The database password isn't set",
    "pg_stat_statements_missing": "pg_stat_statements isn't set up, so there are no query "
                                  "statistics to read. Finish setup to start readings",
    "performance_schema_missing": "performance_schema isn't set up, so there are no query "
                                  "statistics to read. Finish setup to start readings",
}


def _setup_blocker(store: MonitorStore, target: str) -> str | None:
    rows = store.read("SELECT subject FROM inbox_item WHERE target=? AND type='monitoring_issue' "
                      "AND state NOT IN ('resolved','dismissed')", (target,))
    return next((SETUP_BLOCKERS[row["subject"]] for row in rows
                 if row["subject"] in SETUP_BLOCKERS), None)


def _readings(store: MonitorStore, target: str, scheduler: Any = None) -> dict[str, Any]:
    """The last good reading and the next one expected; null next while paused."""
    last, interval = _collect_timing(store, target)
    reading_at = views.collect_timing(store, target)[0]
    enabled = bool(store.levers(target)["enabled"])
    next_at = scheduler.next_reading_at(target) if scheduler is not None else None
    if next_at is None:
        next_at = (last["ts"] if last else _now()) + interval
    status_reader = getattr(scheduler, "collection_status", None)
    status = status_reader(target) if enabled and callable(status_reader) else {}
    if enabled and (blocker := _setup_blocker(store, target)):
        # A setup problem explains the missing readings better than the scheduler can.
        status = {**status, "delay_reason": blocker, "setup_blocked": True}
    return {"last_reading_at": reading_at,
            "next_reading_at": next_at if enabled else None,
            "checks_ran_at": views.checks_ran_at(store, target), **status}


def _query_totals(store: MonitorStore, target: str, start: int, end: int):
    from features.monitor.delivery import query_totals

    return query_totals(store, target, start, end)


def _tables(sql: str) -> list[str]:
    return list(_parsed_tables(sql or ""))


@lru_cache(maxsize=4096)
def _parsed_tables(sql: str) -> tuple[str, ...]:
    if not sql:
        return ()
    try:
        return tuple(referenced_tables(sql))
    except Exception:
        return ()


def _window(from_: int | None, to: int | None) -> tuple[int, int] | None:
    """An explicit [from, to] (epoch seconds) from a brushed chart, clamped to now."""
    if from_ is None and to is None:
        return None
    if from_ is None or to is None:
        raise HTTPException(422, "Pass both from and to")
    end = min(to, _now())
    if from_ < 0 or end - from_ < 60:
        raise HTTPException(422, "from must be at least 60 seconds before to")
    return from_, end


@router.get("/targets/{target}/queries", **documented(models.MonitorQueryPage))
def queries(
    target: str, range: RangeParam | None = None, window: str | None = None,
    sort: str = "db_time_share", filter: str = "", q: str = "", limit: int = 200,
    offset: int = 0, from_: FromParam = None, to: int | None = None,
    keys: str | None = None, locate: str | None = None,
):
    """The query list over a range, or over an explicit [from, to] window when both are set.

    `locate=<key>` adds `located`: that query's 0-based place in the whole filtered, sorted
    list (null when the filters leave it out), so a client can open the page it's on.
    """
    selected = _range(range, window)
    span = _window(from_, to)
    if sort not in {"qps", "mean_ms", "db_time_share", "change", "score_low", "score_high"}:
        raise HTTPException(422, "Unsupported sort")
    if filter not in {"", "all", "changed", "attention", "new", "fixed", "watch", "fix_ready", "healthy",
                      "not_analyzed", "index_coverage", "join_growth", "broad_work", "repeated_work",
                      "access_expression_risk", "none", "unassessed"}:
        raise HTTPException(422, "Unsupported filter")
    if not 1 <= limit <= 500 or offset < 0:
        raise HTTPException(422, "Invalid pagination")
    return views.queries(_store(), target, selected, _now(), sort=sort, filter=filter, q=q,
                         limit=limit, offset=offset, window=span,
                         keys=keys.split(",") if keys is not None else None, locate=locate)


@router.get("/targets/{target}/queries/{key}", **documented(models.MonitorQuery))
def query(target: str, key: str, range: RangeParam | None = None,
          from_: FromParam = None, to: int | None = None):
    """One query row, the same as its row in the list, even if it didn't run in the range."""
    store = _store()
    page = views.queries(store, target, _range(range, None), _now(), sort="db_time_share",
                         filter="", q="", limit=1, offset=0, window=_window(from_, to),
                         only=key)
    if not page["queries"]:
        raise HTTPException(404, "Query not found")
    row = page["queries"][0]
    meta = store.read("SELECT engine FROM stats_meta WHERE target=?", (target,))
    if meta:
        from features.monitor.plan_inputs import write_cost

        row["write_cost"] = write_cost(store, target, row["text"], meta[0]["engine"], _now())
    return row


def _jobs_with_reasons(store: MonitorStore, target: str, jobs: list[dict],
                       plan: dict | None = None) -> list[dict]:
    """Jobs with their reason sentence and, while waiting, where they stand in the queue."""
    from features.monitor.analyze_queue import queue_plan, waiting_reason
    from features.monitor.presentation import analysis_reason

    if plan is None and any(job["state"] == "queued" for job in jobs):
        plan = queue_plan(store, target, _now())
    rows = []
    for job in jobs:
        fact = plan["jobs"].get(job["id"], {}) if plan and job["state"] == "queued" else {}
        waiting = waiting_reason(plan, job["id"]) if fact else None
        rows.append({**job, **fact,
                     "queue_state": "running" if job["state"] == "running"
                     else fact.get("queue_state"),
                     "reason_text": waiting or analysis_reason(job["state"], job["error"])})
    return rows


# Queued jobs the queue summary lists, in the order they start.
QUEUE_LISTED = 20


def _queue_summary(store: MonitorStore, target: str, plan: dict) -> dict:
    """Today's budget, the running job, the queue in order and the last ten that ended."""
    recent = store.read("SELECT * FROM analyze_job WHERE target=? AND state IN ('done','failed') "
                        "ORDER BY finished_at DESC,id DESC LIMIT 10", (target,))
    running = [plan["running"]] if plan["running"] else []
    waiting = plan["queued"][:QUEUE_LISTED]
    listed = _jobs_with_reasons(store, target, running + waiting + recent, plan)
    names = target_names(store, target)
    entries = [{**job, "display_name": names.get(job["query_key"], f"Query {job['query_key']}")}
               for job in listed]
    states = [fact["queue_state"] for fact in plan["jobs"].values()]
    return {key: plan[key] for key in ("cap", "used", "left", "reserved", "automatic_left",
                                       "reset_at", "average_run_s", "automatic_enabled")} | {
        "running": entries[0] if running else None,
        "next": entries[len(running):len(running) + len(waiting)],
        "recent": entries[len(running) + len(waiting):],
        "waiting_today": states.count("today"), "held": states.count("held"),
        "first_hour": plan["first_hour"]}


# Finished analyses the all-databases list names individually; the rest only count.
WORK_DONE_LISTED = 25


@router.get("/work", **documented(models.MonitorWork))
def monitor_work():
    """Monitoring's real work across every monitored database, for the app-wide list.

    Deep analyses running or queued (in the order each database starts them), how many
    finished today and the latest of those with their scores, and reports being written.
    Collection, checks and other background chores aren't work here.
    """
    from features.monitor.analysis_digest import summary_for_job
    from features.monitor.analyze_queue import queue_plan, retry_plan
    from features.monitor import database_limit
    from shared.monitor.monitor_store import local_day_bounds

    store, now = _store(), _now()
    targets = sorted(store.monitored_targets() | {row["target"] for row in store.read(
        "SELECT DISTINCT target FROM analyze_job WHERE reason='user' AND state IN ('queued','running')")})
    running, queued, done, retrying = [], [], [], []
    day_start = local_day_bounds(now)[0]
    done_count = 0
    for target in targets:
        plan = queue_plan(store, target, now)
        if plan["running"]:
            running.append(plan["running"])
        # Held jobs never start on their own, so they aren't work.
        queued += [{**job, **plan["jobs"][job["id"]]} for job in plan["queued"]
                   if plan["jobs"].get(job["id"], {}).get("queue_state") == "today"]
        retrying += [{"target": target, "query_key": key, **retry}
                     for key, retry in retry_plan(store, target, now).items()
                     if retry["retry_at"] is not None and (database_limit._signed_in()
                         or retry["reason"] == "user" and database_limit.own_key())]
        done += store.read("SELECT * FROM analyze_job WHERE target=? AND state='done' "
                           "AND finished_at>=? ORDER BY finished_at DESC,id DESC LIMIT ?",
                           (target, day_start, WORK_DONE_LISTED))
        done_count += store.read("SELECT COUNT(*) AS n FROM analyze_job WHERE target=? "
                                 "AND state='done' AND finished_at>=?",
                                 (target, day_start))[0]["n"]
    done = sorted(done, key=lambda job: -(job["finished_at"] or 0))[:WORK_DONE_LISTED]
    jobs = running + queued + done
    names: dict[tuple[str, str], str] = {}
    for target in {job["target"] for job in jobs} | {row["target"] for row in retrying}:
        names.update({(target, key): name for key, name in target_names(store, target).items()})

    def entry(job: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {"id": job["id"], "target": job["target"], "query_key": job["query_key"],
                "display_name": names.get((job["target"], job["query_key"]))
                or f"Query {job['query_key']}",
                "state": job["state"], "source": "you" if job["reason"] == "user" else "monitoring",
                "enqueued_at": job["enqueued_at"], "started_at": job["started_at"],
                "finished_at": job["finished_at"],
                "queue_position": job.get("queue_position"),
                "queue_state": job.get("queue_state"),
                "estimated_start_at": job.get("estimated_start_at"), **extra}

    reports = store.read("SELECT id,target,trigger,ts,progress_json FROM report_run "
                         "WHERE state='running' ORDER BY ts")
    return {
        "running": [entry(job) for job in running],
        "queued": [entry(job) for job in queued],
        "retrying": [{"target": row["target"], "query_key": row["query_key"],
                      "display_name": names.get((row["target"], row["query_key"]))
                      or f"Query {row['query_key']}",
                      "attempts": row["attempts"], "retry_at": row["retry_at"]}
                     for row in retrying],
        "done_today": [entry(job, efficiency_score=(summary_for_job(job) or {}).get(
            "efficiency_score")) for job in done],
        "done_today_count": done_count,
        "reports": [{"id": row["id"], "target": row["target"], "trigger": row["trigger"],
                     "started_at": row["ts"],
                     "message": (json.loads(row["progress_json"] or "{}") or {}).get("message")}
                    for row in reports if row["target"] in targets],
    }


@router.get("/targets/{target}/analyze-jobs", **documented(models.AnalyzeJobPage))
def analyze_jobs(target: str, state: str | None = None):
    from features.monitor.analyze_queue import queue_plan

    store = _store()
    try:
        jobs = store.analyze_jobs(target, state)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    plan = queue_plan(store, target, _now())
    return {"jobs": _jobs_with_reasons(store, target, jobs, plan),
            "queue": _queue_summary(store, target, plan)}


@router.get("/targets/{target}/queries/{key}/analyze-jobs", **documented(models.AnalyzeJobPage))
def query_analyze_jobs(target: str, key: str, limit: int = 5):
    if not 1 <= limit <= 50:
        raise HTTPException(422, "limit must be between 1 and 50")
    store = _store()
    jobs = store.read("SELECT * FROM analyze_job WHERE target=? AND query_key=? "
                      "ORDER BY enqueued_at DESC,id DESC LIMIT ?", (target, key, limit))
    return {"jobs": _jobs_with_reasons(store, target, jobs)}


@router.post("/targets/{target}/queries/{key}/analyze", **documented(models.AnalyzeJob))
async def enqueue_analysis(target: str, key: str, request: Request):
    from features.monitor.analyze_queue import enqueue_user
    from features.monitor import database_limit

    from shared.monitor.statements import analyzability

    store = _store()
    rows = await asyncio.to_thread(
        store.read, "SELECT text FROM query_text WHERE target=? AND query_key=?", (target, key))
    if not rows:
        raise HTTPException(404, "Query not found")
    verdict = analyzability(rows[0]["text"])
    if not verdict["analyzable"]:
        # Refuse up front with the reason; a queued job for these only ever fails.
        raise HTTPException(422, verdict["not_analyzable_reason"])
    if not await asyncio.to_thread(database_limit._signed_in) and not await asyncio.to_thread(
            database_limit.own_key):
        raise HTTPException(401, "Sign in to run deep analysis")
    job = await asyncio.to_thread(enqueue_user, target, key, store=store, now=_now())
    if job is None:
        raise HTTPException(409, "This query already has an analysis in progress. Try again shortly.")
    already = job.pop("already_queued")
    queue = getattr(request.app.state, "analyze_queue", None)
    if queue is None:
        from features.monitor.analyze_queue import start_analyze_queue

        queue = await start_analyze_queue()
        request.app.state.analyze_queue = queue
    queue.wake()
    presented = await asyncio.to_thread(_jobs_with_reasons, store, target, [job])
    return {**presented[0], "already_queued": already}


def _cancel_job(request: Request, store: MonitorStore, target: str, job_id: int) -> dict:
    from features.monitor.analyze_queue import _active_queue

    rows = store.read("SELECT * FROM analyze_job WHERE id=? AND target=?", (job_id, target))
    if not rows:
        raise HTTPException(404, "Analysis job not found")
    queue = getattr(request.app.state, "analyze_queue", None) or _active_queue
    row = queue.cancel(job_id) if queue is not None else store.cancel_analysis(job_id, _now())
    if row is None:
        raise HTTPException(409, "This analysis has already ended")
    presented = _jobs_with_reasons(store, target, [row])[0]
    return {**presented, "cancelled": True}


@router.post("/targets/{target}/analyze-jobs/{job_id}/cancel", **documented(models.AnalyzeJob))
def cancel_analyze_job(target: str, job_id: int, request: Request):
    """Stop a queued or running analysis; the query's latest analysis stays as it was."""
    return _cancel_job(request, _store(), target, job_id)


@router.post("/targets/{target}/queries/{key}/analyze/cancel", **documented(models.AnalyzeJob))
def cancel_query_analysis(target: str, key: str, request: Request):
    """Stop the query's queued or running analysis, whichever job holds it."""
    store = _store()
    rows = store.read("SELECT id FROM analyze_job WHERE target=? AND query_key=? "
                      "AND state IN ('queued','running') ORDER BY id DESC LIMIT 1", (target, key))
    if not rows:
        raise HTTPException(409, "This query has no analysis in progress")
    return _cancel_job(request, store, target, int(rows[0]["id"]))


class AddQueryRequest(BaseModel):
    # The SQL as pasted, placeholders and all: it names the query in the list.
    sql: str
    # The finished analysis from POST /api/analyze, to attach to the query's row.
    query_hash: str | None = None
    analysis_id: str | None = None
    mode: Literal["analyze", "explain"] | None = None


@router.post("/targets/{target}/queries", **documented(models.AddedQuery))
def add_query(target: str, body: AddQueryRequest):
    """Put a query analyzed from "Analyze a query" in the list, with its analysis attached."""
    from shared.config.targets import TargetsConfig
    from shared.monitor.statements import analyzability, is_workload
    from shared.query_registry import hash_sql

    config = TargetsConfig()
    config.load()
    if config.get(target) is None:
        raise HTTPException(404, "Target not found")
    sql = body.sql.strip().rstrip(";").strip()
    if not sql or len(sql) > 100_000:
        raise HTTPException(422, "Paste one SQL statement")
    if not is_workload(sql):
        raise HTTPException(422, "RDST lists application queries, not catalog or RDST reads")
    if (body.query_hash is None) != (body.analysis_id is None):
        raise HTTPException(422, "Pass query_hash and analysis_id together")
    if body.analysis_id is None:
        # Pasted to be analyzed: one RDST would refuse to analyze stays out of the list.
        verdict = analyzability(sql)
        if not verdict["analyzable"]:
            raise HTTPException(422, verdict["not_analyzable_reason"])
    ref = (f"/api/query-registry/{body.query_hash}/analysis/{body.analysis_id}"
           if body.query_hash else None)
    store = _store()
    key = hash_sql(sql)
    created = not store.read("SELECT 1 FROM query_text WHERE target=? AND query_key=?",
                             (target, key))
    recorded = store.record_user_analysis(target, key, sql, _now(), analysis_ref=ref,
                                          mode=body.mode)
    job = _jobs_with_reasons(store, target, [recorded["job"]])[0] if recorded["job"] else None
    return {"key": key, "created": created, "job": job}


@router.post("/targets/{target}/queries/{key}/library", **documented(models.LibraryEntry))
def ensure_library_entry(target: str, key: str):
    """Give a monitored query its Query Library row, so Compare can select it.

    Library admission is capped per collector pass, so a quiet monitored query may have
    no row yet. Monitor keys are Library hashes; the row is created from monitor text.
    """
    from features.query_registry.api.routes import _admit_monitored_query
    from shared.query_registry import QueryRegistry

    registry = QueryRegistry()
    registry.load()
    if registry.get_query(key) is not None:
        return {"hash": key, "created": False}
    if _admit_monitored_query(registry, key, target) is None:
        raise HTTPException(404, "RDST has no text for this query yet")
    registry.save()
    views.forget_registry()
    return {"hash": key, "created": True}


@router.get("/targets/{target}/queries/{key}/analysis", **documented(models.DeepAnalysisDigest))
def query_analysis(target: str, key: str):
    """The latest finished deep analysis of a query, in the report card's shape."""
    from features.monitor.analysis_digest import latest_digest

    store = _store()
    digest = latest_digest(store, target, key)
    if digest is None:
        raise HTTPException(404, "No finished analysis for this query")
    return views.analysis_view(store, target, key, digest, _now())


@router.post("/targets/{target}/benchmark-fixes", **documented(models.BenchmarkFixesResult))
def benchmark_fixes(target: str, body: models.BenchmarkFixesRequest):
    """Before and after for each query with a fix. The after is the planner's estimate;
    a timed run on a scratch schema is not done, and `scratch_schema` reads false."""
    if not 1 <= body.runs <= 100:
        raise HTTPException(422, "runs must be between 1 and 100")
    if not body.query_keys or len(body.query_keys) > 100:
        raise HTTPException(422, "Pass between 1 and 100 query keys")
    result = views.benchmark_fixes(_store(), target, list(dict.fromkeys(body.query_keys)),
                                   body.runs, _now())
    _record_fix_benchmark(result)
    return result


def _record_fix_benchmark(result: dict[str, Any]) -> None:
    """The fixes benchmark in the agent activity log, as the planner estimate it is."""
    from shared.agent_activity.recorder import record_entry

    rows = result["rows"]
    names = [row["name"] for row in rows]
    faster = sum(1 for row in rows if (row["change_pct"] or 0) < 0)
    measured = sum(1 for row in rows if row["after_ms"] is not None)
    subject = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    record_entry({
        "target": result["target"], "action": "benchmark", "subject": subject,
        "outcome": "done",
        "summary": (f"{faster} of {measured} faster with their fixes, estimated" if measured
                    else "No estimate for the fixes"),
        "decision": "Read the planner's estimates from the analyses; nothing ran on the "
                    "database",
        "details": {"mode": "planner_estimate", "runs": result["runs"],
                    "queries": [{"query_key": row["key"], "name": row["name"],
                                 "before_ms": row["before_ms"], "after_ms": row["after_ms"]}
                                for row in rows]}})


def _configured_targets() -> list[dict[str, Any]]:
    """Every configured database, for Home; empty when the config can't be read."""
    from shared.config.targets import TargetsConfig

    try:
        config = TargetsConfig()
        config.load()
        return [{"name": name, **(config.get(name) or {})} for name in config.list_targets()]
    except Exception:
        logger.debug("Targets config unavailable for Home", exc_info=True)
        return []


@router.get("/home", **documented(models.HomeModel))
def home(target: str | None = None, limit: int = views.HOME_LOG_LIMIT):
    """Home: the status line, the day card, every database and today's log."""
    if not 1 <= limit <= 50:
        raise HTTPException(422, "limit must be between 1 and 50")
    return views.home(_store(), _now(), configured=_configured_targets(), selected=target,
                      limit=limit)


@router.get("/targets/{target}/load", **documented(models.LoadBreakdown))
def load(target: str, range: RangeParam = "1h", top: int = views.LOAD_TOP,
         from_: FromParam = None, to: int | None = None,
         rank: Literal["time", "calls"] = "time"):
    """Recorded execution time and calls per bucket, split by the top queries in the range.

    `rank` picks the top queries by execution time or by calls. `from` and `to` (epoch
    seconds) scope it to a brushed window instead of `range`.
    """
    if not 1 <= top <= 12:
        raise HTTPException(422, "top must be between 1 and 12")
    return views.load(_store(), target, range, _now(), top, window=_window(from_, to),
                      rank=rank)


@router.get("/targets/{target}/load/latency", **documented(models.LatencyBreakdown))
def load_latency(target: str, keys: str, range: RangeParam = "1h",
                 from_: FromParam = None, to: int | None = None):
    """Average latency per bucket for up to eight queries, with the overall average."""
    selected = [key for key in (part.strip() for part in keys.split(",")) if key]
    if not 1 <= len(selected) <= 8:
        raise HTTPException(422, "Pass between 1 and 8 query keys")
    return views.load_latency(_store(), target, range, _now(), selected,
                              window=_window(from_, to))


@router.get("/targets/{target}/queries/{key}/series", **documented(models.QuerySeries))
def series(target: str, key: str, range: RangeParam = "24h",
           from_: FromParam = None, to: int | None = None):
    return views.series(_store(), target, key, range, _now(), window=_window(from_, to))


class MonitorSettingsUpdate(BaseModel):
    enabled: bool | None = None
    deep_analysis: bool | None = None
    execute: bool | None = None
    analysis_mode: Literal["plans_only", "execute"] | None = None
    auto_analyze_new: bool | None = None
    analysis_daily_cap: int | None = None
    newcomer_daily_cap: int | None = None
    attention_every_min: Literal[5, 15, 60] | None = None
    nightly_report: bool | None = None
    nightly_hour: int | None = None
    push_daily_cap: int | None = None
    # Partial: only the switches sent change.
    notify_events: dict[str, bool] | None = None
    # Both hours; equal hours switch quiet hours off.
    quiet_hours: dict[str, int] | None = None
    # Dev/demo only: shorten this database's first-connection learning period (seconds).
    dev_learning_period_s: int | None = None


def _start_first_run(state: Any, target: str) -> None:
    """A database just turned on: read it now and often, and queue analyses as queries land."""
    from features.monitor.analyze_queue import wake_active_queue

    def collected(_name: str, first: bool) -> None:
        wake_active_queue()
        loop = getattr(state, "monitor_loop", None)
        if first and loop is not None:
            loop.wake()

    scheduler = getattr(state, "monitor_scheduler", None)
    if scheduler is not None:
        scheduler.start_fast_phase(target, collected)
    loop = getattr(state, "monitor_loop", None)
    if loop is not None:
        loop.wake()


def _release_monitoring_slot(target: str) -> None:
    """Monitoring turned off: free the account's slot for this database and its limit item."""
    from features.monitor import database_limit
    from shared.config.targets import TargetsConfig

    database_limit.clear(_store(), target, _now())
    # Nothing runs automatic analyses for a database that isn't monitored, so they'd wait forever.
    queued = [row["query_key"] for row in _store().read(
        "SELECT query_key FROM analyze_job WHERE target=? AND state='queued'", (target,))]
    if queued:
        _store().drop_queued_analyses(target, queued)
    # The next turn-on starts a fresh "since" for its AI state.
    _store().write_setting(target, database_limit.AI_SINCE_KEY, "")
    try:
        config = TargetsConfig()
        config.load()
        database_limit.release(config.get(target))
    except Exception:
        logger.warning("Couldn't release the monitoring slot for %s", target, exc_info=True)


AURORA_READER_UNAVAILABLE = ("RDST monitors an Aurora cluster through its writer for now. "
                             "Turn on monitoring for the writer; cluster-wide monitoring, "
                             "readers included, is coming.")


def _monitor_unavailable(target: str) -> str | None:
    """Why monitoring can't be turned on for this target, if it can't."""
    from shared.config.targets import TargetsConfig

    try:
        config = TargetsConfig()
        config.load()
        tags = (config.get(target) or {}).get("tags") or []
    except Exception:
        return None
    return AURORA_READER_UNAVAILABLE if "role:reader" in tags else None


def _settings(target: str) -> dict[str, Any]:
    from features.monitor import database_limit

    store = _store()
    reason = database_limit.ai_reason(store, target)
    return {**store.settings(target),
            "unavailable_reason": _monitor_unavailable(target),
            "ai_monitoring": database_limit.display_state(store, target),
            "ai_reason": reason["reason"], "ai_since": reason["since"]}


@router.get("/limit", **documented(models.MonitoringLimit))
async def monitoring_limit(target: str | None = None):
    """AI monitoring on `in_use` of `limit` databases for the signed-in account.

    The Key Service owns the limit: 3 databases, or 10 when the user's own Anthropic key
    pays for deep analysis and reports. `limit` is null when it couldn't be asked and
    hasn't answered before. `targets` lists this machine's monitored databases with
    their AI state.
    """
    from features.monitor import database_limit

    return await asyncio.to_thread(database_limit.status, _store(), target=target)


@router.post("/limit/release", **documented(models.MonitoringLimit))
async def release_monitoring_slot(body: models.MonitoringSlotRelease):
    """Free a slot held for a database this computer doesn't have."""
    from features.monitor import database_limit

    if not await asyncio.to_thread(database_limit.release_fingerprint, body.fingerprint):
        raise HTTPException(502, "Readyset didn't release that slot. Try again.")
    return await asyncio.to_thread(database_limit.status, _store())


@router.post("/targets/{target}/ai-monitoring", **documented(models.MonitorSettings))
async def claim_ai_monitoring(target: str):
    """Ask again for AI monitoring on a database that's monitored locally."""
    from features.monitor import database_limit

    store = _store()
    if not store.levers(target)["enabled"]:
        raise HTTPException(409, "Turn on monitoring for this database first")
    await asyncio.to_thread(database_limit.claim_target, store, target, where="retry")
    return _settings(target)


@router.get("/targets/{target}/settings", **documented(models.MonitorSettings))
def settings(target: str):
    return _settings(target)


# Guards the lazy start of the monitor's background services.
_SERVICES_LOCK = asyncio.Lock()


@router.put("/targets/{target}/settings", **documented(models.MonitorSettings))
async def put_settings(target: str, body: MonitorSettingsUpdate, request: Request):
    from features.monitor import database_limit

    try:
        update = body.model_dump(exclude_none=True)
        mode = update.pop("analysis_mode", None)
        if mode is not None:
            update["execute"] = mode == "execute"
        was_enabled = _store().levers(target)["enabled"]
        if update.get("enabled") and not was_enabled and (blocked := _monitor_unavailable(target)):
            raise HTTPException(409, blocked)
        values = _store().set_settings(target, update)
    except LocalMonitorLimitError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    turned_on = values["enabled"] and not was_enabled
    if turned_on:
        # A full account still gets local monitoring; the claim decides whether AI joins.
        ai_state = await asyncio.to_thread(database_limit.claim_target, _store(), target)
        await asyncio.to_thread(product_telemetry.monitoring_enabled, target,
                                monitored_count=len(_store().monitored_targets()),
                                ai_monitoring={"on": "on", "local": "local_only"}.get(
                                    ai_state, "pending"))
    if was_enabled and not values["enabled"]:
        await asyncio.to_thread(_release_monitoring_slot, target)
        await asyncio.to_thread(product_telemetry.monitoring_disabled, target,
                                monitored_count=len(_store().monitored_targets()))
    if was_enabled and not values["enabled"]:
        from features.monitor.loop import connection_changed

        scheduler = getattr(request.app.state, "monitor_scheduler", None)
        connection_changed(target, scheduler)
        if scheduler is not None:
            scheduler._refresh_targets(scheduler._clock())
        loop = getattr(request.app.state, "monitor_loop", None)
        if loop is not None:
            loop.wake()
    # Two databases turned on together must not each start the background services.
    async with _SERVICES_LOCK:
        if values["enabled"] and hasattr(request.app.state, "monitor_scheduler"):
            scheduler = request.app.state.monitor_scheduler
            if scheduler is None:
                from features.query_registry.scheduler import ObservationScheduler

                scheduler = ObservationScheduler(query_discovery)
                await scheduler.start()
                request.app.state.monitor_scheduler = scheduler
            else:
                scheduler._refresh_targets(scheduler._clock())
            if turned_on:
                collect_soon = getattr(scheduler, "collect_soon", None)
                if collect_soon is not None:
                    collect_soon(target)
        if values["enabled"] and hasattr(request.app.state, "monitor_loop"):
            loop = request.app.state.monitor_loop
            if loop is None:
                from features.monitor.loop import start_monitor_loop

                loop = await start_monitor_loop(
                    scheduler=getattr(request.app.state, "monitor_scheduler", None))
                request.app.state.monitor_loop = loop
        if values["enabled"] and getattr(request.app.state, "analyze_queue", None) is None:
            from features.monitor.analyze_queue import start_analyze_queue

            request.app.state.analyze_queue = await start_analyze_queue()
        if turned_on:
            from features.monitor.loop import connection_changed

            connection_changed(target, getattr(request.app.state, "monitor_scheduler", None))
            values["enabled_at"] = _now()
            await asyncio.to_thread(_store().start_first_hour, target, values["enabled_at"])
            values["first_hour_started_at"] = await asyncio.to_thread(
                lambda: _store().first_hour(target)[0] or None)
            _start_first_run(request.app.state, target)
            queue = getattr(request.app.state, "analyze_queue", None)
            if queue is not None and callable(getattr(queue, "wake", None)):
                queue.wake()
        if values["enabled"] and hasattr(request.app.state, "report_scheduler") and \
                request.app.state.report_scheduler is None:
            from features.monitor.report import ReportScheduler

            request.app.state.report_scheduler = await ReportScheduler().start()
    if not values["enabled"] and getattr(request.app.state, "monitor_loop", None):
        if not _store().monitored_targets():
            await request.app.state.monitor_loop.stop()
            request.app.state.monitor_loop = None
    if not _store().monitored_targets() and not _store().read(
            "SELECT 1 FROM analyze_job WHERE reason='user' AND (state IN ('queued','running') "
            "OR state='failed' AND finished_at>=?) LIMIT 1", (_now() - 86400,)
    ) and getattr(request.app.state, "analyze_queue", None):
        await request.app.state.analyze_queue.stop()
        request.app.state.analyze_queue = None
    if not _store().monitored_targets() and getattr(request.app.state, "report_scheduler", None):
        await request.app.state.report_scheduler.stop()
        request.app.state.report_scheduler = None
    reason = await asyncio.to_thread(database_limit.ai_reason, _store(), target)
    return {**values, "ai_monitoring": await asyncio.to_thread(
        database_limit.display_state, _store(), target),
            "ai_reason": reason["reason"], "ai_since": reason["since"]}


@router.get("/targets/{target}/usage", **documented(models.MonitorUsage))
def usage(target: str, days: int = 7):
    """Billable work per local day, newest last, with the caps that bound it."""
    from datetime import date, timedelta

    from shared.monitor.monitor_store import jev_daily_cap, local_day_bounds

    if not 1 <= days <= 90:
        raise HTTPException(422, "days must be between 1 and 90")
    store = _store()
    # The same local day the analysis queue's daily cap counts.
    since = local_day_bounds(_now())[0]
    for _ in range(days - 1):
        since = local_day_bounds(since - 1)[0]
    first = date.fromtimestamp(since)
    totals = {(first + timedelta(days=offset)).isoformat():
              {"jev_calls": 0, "analyses": 0, "reports": 0, "billable_calls": 0,
               "notifications": 0}
              for offset in range(days)}
    for field, sql in (
        ("jev_calls", "SELECT ts,billable_calls FROM judge_run WHERE target=? AND ts>=? "
                      "AND request_json IS NOT NULL"),
        # Every analysis that started counts: automatic, first-hour, report and clicks.
        ("analyses", "SELECT started_at ts,billable_calls "
                     "FROM analyze_job WHERE target=? AND started_at>=? AND state!='skipped'"),
        ("reports", "SELECT ts,billable_calls FROM report_run WHERE target=? AND sample=0 "
                    "AND ts>=?"),
    ):
        for row in store.read(sql, (target, since)):
            day = date.fromtimestamp(row["ts"]).isoformat()
            if day in totals:
                totals[day][field] += 1
                totals[day]["billable_calls"] += row["billable_calls"] or 0
    for row in store.read("SELECT e.ts FROM inbox_event e JOIN inbox_item i ON i.id=e.item_id "
                          "WHERE i.target=? AND e.kind='notified' AND e.ts>=?", (target, since)):
        day = date.fromtimestamp(row["ts"]).isoformat()
        if day in totals:
            totals[day]["notifications"] += 1
    return {"days": [{"date": day, **values} for day, values in totals.items()],
            "caps": {"jev_daily": jev_daily_cap(),
                     "analysis_daily": store.levers(target)["analysis_daily_cap"],
                     "push_daily": store.levers(target)["push_daily_cap"]}}


class MuteRuleInput(BaseModel):
    target: str | None = None
    query_key: str | None = None
    type: str | None = None
    until: int | None = None
    note: str | None = None


@router.get("/mute-rules", **documented(models.MuteRulePage))
def mute_rules(target: str | None = None):
    return {"rules": _store().mute_rules(target)}


@router.post("/mute-rules", **documented(models.MuteRule))
def create_mute_rule(body: MuteRuleInput):
    try:
        return _store().create_mute_rule(target=body.target, query_key=body.query_key,
                                         type=body.type, until=body.until, note=body.note,
                                         now=_now())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/mute-rules/{rule_id}", **documented(models.DeletedMuteRule))
def delete_mute_rule(rule_id: int):
    if not _store().delete_mute_rule(rule_id):
        raise HTTPException(404, "Mute rule not found")
    return {"deleted": rule_id}


INBOX_TABS = {"open", "muted", "done"}
# Closed items stay 7 days.
DONE_WINDOW_S = 7 * 86400


@router.get("/inbox", **documented(models.InboxPage))
def inbox(state: str | None = None, target: str | None = None, include_muted: bool = False,
          include_digest: bool = False, incidents: bool = False):
    """Tabs (open, muted, done) newest first; a raw item state keeps the classic filter.

    Digest items only reach the daily report, so every view leaves them out unless
    `include_digest` is set. Open also lists recoveries from the last 24 h until seen.
    Incident windows are measured only when `incidents` is set (the Inbox page).
    """
    store = _store()
    try:
        rows = store.inbox(None if state in INBOX_TABS else state, target)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not include_digest:
        rows = [row for row in rows if row["delivery"] != "digest"]
    items = views.inbox_items(store, rows, incidents=incidents)
    if state in INBOX_TABS:
        finished = {"resolved", "dismissed"}
        now = _now()

        def fresh_recovery(item: dict) -> bool:
            return (item["severity_band"] == "recovered" and
                    (item["resolved_at"] or 0) >= now - RECOVERED_WINDOW_S and
                    not json.loads(item["body_json"]).get("recovery_seen_at"))

        def keep(item: dict) -> bool:
            closed = item["state"] in finished
            if state == "done":
                return closed
            if state == "muted":
                return not closed and item["muted_by"] is not None
            if closed:
                return fresh_recovery(item)
            return item["muted_by"] is None or include_muted

        items = [item for item in items if keep(item)]
        if state == "done":
            # Closed items stay 7 days.
            items = [item for item in items
                     if (item["resolved_at"] or item["updated_at"]) >= now - DONE_WINDOW_S]
        items.sort(key=lambda item: (-item["created_at"], -item["id"]))
    return {"items": items}


class InboxAction(BaseModel):
    until: int | None = None


@router.post("/inbox/{item_id}/{action}", **documented(models.InboxItem))
def inbox_action(item_id: int, action: Literal["seen", "snooze", "dismiss", "resolve", "reopen"],
                 body: InboxAction | None = None):
    if action == "snooze" and (body is None or body.until is None or body.until <= _now()):
        raise HTTPException(422, "A future snooze time is required")
    store = _store()
    item = store.action_item(item_id, action, _now(),
                             snooze_until=body.until if body else None)
    if item is None:
        raise HTTPException(404, "Inbox item not found")
    return views.inbox_items(store, [item], incidents=True)[0]


class ActionVerb(BaseModel):
    """An action verb's optional body: `until` (epoch seconds) sets aside until a date, and
    `reason` is the user's words for a set aside, a dismissal or an undo."""
    until: int | None = None
    reason: str | None = Field(default=None, max_length=500)


@router.get("/targets/{target}/actions", **documented(models.ActionsPage))
def target_actions(target: str):
    from features.monitor import actions

    return actions.present(_store(), target, _now())


ActionVerbName = Literal["set_aside", "snooze", "dismiss", "did_it", "undid_it", "bring_back",
                         "reopen", "check"]


def _action_targets(store: MonitorStore) -> tuple[list[str], list[str]]:
    """The databases Actions covers (monitored, or with actions on record) in config order,
    and the configured ones that aren't monitored."""
    configured = [str(entry["name"]) for entry in _configured_targets() if entry.get("name")]
    monitored = store.monitored_targets()
    known = monitored | {row["target"] for row in store.read("SELECT DISTINCT target FROM action")}
    ordered = [name for name in configured if name in known] + sorted(known - set(configured))
    return ordered, [name for name in configured if name not in monitored]


@router.get("/actions", **documented(models.GlobalActionsPage))
def global_actions(db: str | None = None):
    """Every database's open actions in one ranked plan, or one database's with `db`."""
    from features.monitor import actions

    store = _store()
    targets, unmonitored = _action_targets(store)
    return actions.global_page(store, targets, _now(), db=db, unmonitored=unmonitored)


@router.get("/actions/applied", **documented(models.AppliedPage))
def applied_actions(db: str | None = None):
    """Changes applied in the last 30 days with their measured effect, then the undone and
    dismissed ones."""
    from features.monitor import actions

    store = _store()
    return actions.applied_page(store, _action_targets(store)[0], _now(), db=db)


@router.post("/targets/{target}/actions/opened", **documented(models.ActionsOpened))
def actions_opened(target: str):
    """Actions was opened for this database: its Changes ready event closes."""
    from features.monitor import actions

    return {"target": target, "closed": actions.mark_opened(_store(), target, _now())}


def _read_database(store: MonitorStore, target: str, now: int) -> None:
    """Read the database's indexes and statistics now and refresh its actions."""
    from features.monitor import actions
    from features.monitor.loop import _scan_indexes, _scan_stats

    store.sync_indexes(target, _scan_indexes(target), now)
    store.record_stats(target, _scan_stats(target))
    actions.refresh(store, target, now)


@router.post("/targets/{target}/actions/{action_id}/{verb}", **documented(models.ActionsPage))
def action_verb(target: str, action_id: int, verb: ActionVerbName,
                body: ActionVerb | None = None):
    """A user's verb on one action, answered with the database's Actions page.

    set_aside (snooze is its older name) takes `until` and `reason`; dismiss takes `reason`;
    did_it reads the database now and, unless it already shows the change, marks it applied
    by you so measuring starts; undid_it marks an applied change undone with `reason`;
    bring_back (or reopen) opens it again; check reads the database to see if it's done."""
    from features.monitor import actions

    store, now = _store(), _now()
    if not any(row["id"] == action_id for row in store.actions(target)):
        raise HTTPException(404, "Action not found")
    if verb == "check":
        try:
            _read_database(store, target, now)
        except Exception as exc:
            raise HTTPException(503, f"Couldn't read the database: {exc}") from exc
        return actions.present(store, target, now)
    if verb in ("snooze", "set_aside") and body is not None and body.until is not None and \
            body.until <= now:
        raise HTTPException(422, "A future date is required")
    if verb == "did_it":
        try:
            _read_database(store, target, now)
        except Exception:
            logger.debug("Couldn't read %s for did_it; recording it as yours", target,
                         exc_info=True)
    try:
        actions.set_state(store, target, action_id, verb, now,
                          until=body.until if body else None, reason=body.reason if body else None)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return actions.present(store, target, now)


@router.get("/targets/{target}/index-plan", **documented(models.IndexPlan))
def index_plan(target: str):
    """The latest index plan; null until the first statistics read computes one."""
    from features.monitor import actions

    return actions.present_plan(_store(), target)


@router.get("/targets/{target}/index-changes")
def index_changes(target: str, since: int = 0):
    return {"changes": _store().read(
        "SELECT * FROM index_change WHERE target=? AND ts>=? ORDER BY ts DESC,id DESC",
        (target, since))}


@router.get("/targets/{target}/judge-runs")
def judge_runs(target: str, limit: int = 20):
    if not 1 <= limit <= 100:
        raise HTTPException(422, "limit must be between 1 and 100")
    rows = _store().read(
        "SELECT id,target,ts,shortlist_json,request_json,response_json,dry_run,outcome "
        "FROM judge_run WHERE target=? ORDER BY ts DESC,id DESC LIMIT ?", (target, limit))
    runs = []
    for row in rows:
        runs.append({"id": row["id"], "target": row["target"], "ts": row["ts"],
                     "dry_run": bool(row["dry_run"]), "outcome": row["outcome"],
                     "shortlist": json.loads(row["shortlist_json"]),
                     "request": json.loads(row["request_json"]) if row["request_json"] else None,
                     "verdict": json.loads(row["response_json"]) if row["response_json"] else None})
    return {"runs": runs}


@router.get("/inbox/stream")
async def inbox_stream(request: Request):
    store = _store()
    latest = (await asyncio.to_thread(
        store.read, "SELECT COALESCE(MAX(id),0) AS id FROM inbox_event"))[0]["id"]
    previous = request.headers.get("last-event-id")
    try:
        since = int(getattr(request, "query_params", {}).get("since"))
    except (TypeError, ValueError):
        since = None
    if previous and previous.isdecimal():
        cursor = min(int(previous), latest)
    elif since is not None and since >= 0:
        first = (await asyncio.to_thread(
            store.read, "SELECT MIN(id) AS id FROM inbox_event WHERE ts>=?", (since,)))[0]["id"]
        cursor = first - 1 if first is not None else latest
    else:
        cursor = latest

    async def events():
        nonlocal cursor
        # A snapshot fetch may have finished just before this stream opened.
        # Make the browser refetch once so that edge cannot leave it stale.
        yield {"id": str(cursor), "event": "upsert", "data": "{}"}
        while not await request.is_disconnected():
            for row in await asyncio.to_thread(store.inbox_events, cursor):
                cursor = row["id"]
                if row["kind"] == "notified":
                    # The Inbox's own one-line wording, so a system notification reads the same.
                    row["summary"] = await asyncio.to_thread(_item_summary, store, row["item_id"])
                yield {"id": str(cursor), "event": "notified" if row["kind"] == "notified"
                       else "upsert", "data": json.dumps(row)}
            await asyncio.sleep(1)
    return EventSourceResponse(events())


def _item_summary(store: MonitorStore, item_id: int) -> str | None:
    try:
        rows = store.read("SELECT * FROM inbox_item WHERE id=?", (item_id,))
        return views.inbox_items(store, rows)[0].get("summary") if rows else None
    except Exception:
        logger.debug("No summary for inbox item %s", item_id, exc_info=True)
        return None


@router.get("/inbox/{item_id}", **documented(models.InboxItem))
def inbox_item(item_id: int):
    """One item in any state, for a page opened from it (the Database page's context)."""
    store = _store()
    rows = store.read("SELECT * FROM inbox_item WHERE id=?", (item_id,))
    if not rows:
        raise HTTPException(404, "Inbox item not found")
    return views.inbox_items(store, rows, incidents=True)[0]


COLLECT_LATE_SECONDS = 150
PULSE_POLL_SECONDS = 1.0


def _pulse_state(store: MonitorStore, target: str, now: int,
                 last_collect: dict | None) -> str:
    if not store.levers(target)["enabled"]:
        return "paused"
    issues = {row["subject"] for row in store.read(
        "SELECT subject FROM inbox_item WHERE target=? AND type='monitoring_issue' "
        "AND state NOT IN ('resolved','dismissed')", (target,))}
    if issues & {"lost_connection", "password_missing"} or \
            views.failing_since(store, target) is not None:
        return "connection_lost"
    if last_collect is None or now - last_collect["ts"] > COLLECT_LATE_SECONDS:
        return "late"
    if issues & {"jev_login"}:
        return "ai_paused"
    judged = store.read("SELECT outcome FROM judge_run WHERE target=? ORDER BY ts DESC,id DESC "
                        "LIMIT 1", (target,))
    if judged and judged[0]["outcome"] in ("daily_cap", "error", "backoff"):
        return "ai_paused"
    return "live"


def _collect_timing(store: MonitorStore, target: str) -> tuple[dict | None, int]:
    # The ok pulse ending a failed check repeats that check; it is no reading of its own.
    collects = store.read("SELECT ts FROM monitor_pulse WHERE target=? AND kind='collect' "
                          "AND detail!=? ORDER BY id DESC LIMIT 2", (target, ANSWERED))
    last = collects[0] if collects else None
    interval = (max(30, min(600, collects[0]["ts"] - collects[1]["ts"]))
                if len(collects) == 2 else 60)
    return last, interval


def _activity(store: MonitorStore, target: str, readings: dict[str, Any],
              every_s: int) -> dict[str, Any]:
    levers = store.levers(target)
    jobs, reports = views.running_work(store, [target])
    return views.activity(store, target, _now(), enabled=bool(levers["enabled"]),
                          last_reading_at=readings.get("last_reading_at"), every_s=every_s,
                          checks_ran_at=readings.get("checks_ran_at"),
                          check_every_s=levers["attention_every_min"] * 60,
                          running=jobs.get(target), report=reports.get(target))


def _present_pulses(store: MonitorStore, target: str, rows: list[dict],
                    scheduler: Any = None) -> list[dict]:
    now = _now()
    last, interval = _collect_timing(store, target)
    state = _pulse_state(store, target, now, last)
    next_collect = (last["ts"] if last else now) + interval
    readings = _readings(store, target, scheduler)
    if readings.get("first_reading") and state != "paused":
        state = "live"
    doing = _activity(store, target, readings, interval) if rows else None
    return [{"ts": row["ts"], "kind": row["kind"], "ok": bool(row["ok"]), "detail": row["detail"],
             "next_collect_at": next_collect, "state": state,
             **readings, "activity": doing} for row in rows]


def _connection_problem(store: MonitorStore, target: str) -> dict[str, str] | None:
    """Why the database can't be reached right now, and what to check; None while it can."""
    from features.monitor.connection import connection_problem

    rows = store.read("SELECT subject,body_json FROM inbox_item WHERE target=? "
                      "AND type='monitoring_issue' AND subject IN ('lost_connection',"
                      "'password_missing') AND state NOT IN ('resolved','dismissed') "
                      "ORDER BY id DESC LIMIT 1", (target,))
    if not rows:
        # Before the outage is filed, the latest failed check says why.
        latest = store.read("SELECT ok,detail FROM monitor_pulse WHERE target=? "
                            "AND kind='collect' ORDER BY id DESC LIMIT 1", (target,))
        return connection_problem(latest[0]["detail"]) if latest and not latest[0]["ok"] \
            else None
    if rows[0]["subject"] == "password_missing":
        return {"reason": "No password is saved for this database.",
                "fix": "Add the password for this database in Settings."}
    try:
        error = json.loads(rows[0]["body_json"] or "{}").get("error")
    except ValueError:
        error = None
    return connection_problem(error)


@router.get("/targets/{target}/pulse", **documented(models.MonitorPulsePage))
def pulse(target: str, request: Request, limit: int = 5):
    if not 1 <= limit <= 50:
        raise HTTPException(422, "limit must be between 1 and 50")
    store = _store()
    scheduler = getattr(request.app.state, "monitor_scheduler", None)
    readings = _readings(store, target, scheduler)
    return {"pulses": _present_pulses(store, target, store.pulses(target, limit), scheduler),
            **readings, "activity": _activity(store, target, readings,
                                              _collect_timing(store, target)[1]),
            "connection_problem": _connection_problem(store, target)}


@router.post("/targets/{target}/collect-now", **documented(models.CollectNowResult))
async def collect_now(target: str, request: Request):
    from shared.api.guards import require_local_request

    require_local_request(request)
    store = _store()
    if not (await asyncio.to_thread(store.levers, target))["enabled"]:
        raise HTTPException(409, "Turn on monitoring to check this database")
    scheduler = getattr(request.app.state, "monitor_scheduler", None)
    if scheduler is None:
        raise HTTPException(503, "The reader is starting. Try again in a moment.")
    try:
        return await scheduler.check_now(target)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/targets/{target}/stream")
async def stream(target: str, request: Request = None):
    """`pulse` after every collector tick and monitor pass; `tick` for the classic page."""
    collector = query_discovery.collector_for(target)
    store = _store()
    scheduler = getattr(request.app.state, "monitor_scheduler", None) if request else None
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)

    def offer(event: dict) -> None:
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(event)

    async def ticks():
        async for event in collector.subscribe():
            if event.event in ("discovery_update", "discovery_error"):
                offer({"event": "tick", "data": json.dumps({
                    "target": target, "ts": _now(), "state": event.data.get("state")})})

    def read_pulses(after: int) -> tuple[int, list[dict]]:
        rows = store.pulses(target, 50, after=after)
        return (rows[-1]["id"] if rows else after,
                _present_pulses(store, target, rows, scheduler) if rows else [])

    async def pulses():
        # SQLite reads wait on other writers; they run in a worker, never on the loop.
        latest = (await asyncio.to_thread(
            store.read, "SELECT COALESCE(MAX(id),0) id FROM monitor_pulse WHERE target=?",
            (target,)))[0]["id"]
        while True:
            latest, presented = await asyncio.to_thread(read_pulses, latest)
            if presented:
                for row in presented:
                    offer({"event": "pulse", "data": json.dumps(row)})
            await asyncio.sleep(PULSE_POLL_SECONDS)

    async def events():
        tasks = [asyncio.create_task(ticks()), asyncio.create_task(pulses())]
        try:
            while True:
                if request is not None and await request.is_disconnected():
                    return
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=5)
                except asyncio.TimeoutError:
                    continue
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    return EventSourceResponse(events(), send_timeout=15)


@router.post("/targets/{target}/report", **documented(models.DatabaseReport))
async def start_report(target: str, request: Request):
    from features.monitor.report import _wake_analyze_queue, present_report, run_report
    from features.monitor.snapshot import report_period

    store = _store()
    if not (await asyncio.to_thread(store.settings, target))["enabled"]:
        raise HTTPException(409, "Turn on monitoring to analyze this database")
    now = _now()
    run, claimed = await asyncio.to_thread(store.claim_report, target, "user", now,
                                           report_period("user", now)[0])
    if claimed:
        task = asyncio.create_task(run_report(target, "user", store=store,
                                              claimed_run=run, wake_queue=_wake_analyze_queue))
        tasks = getattr(request.app.state, "report_tasks", None)
        if tasks is None:
            tasks = request.app.state.report_tasks = set()
        tasks.add(task)
        def finished(done: asyncio.Task) -> None:
            tasks.discard(done)
            if not done.cancelled() and done.exception():
                logger.warning("Report run failed for %s: %s", target, done.exception())
        task.add_done_callback(finished)
    return await asyncio.to_thread(present_report, run, store)


ReportView = Literal["quick", "detailed"]


@router.get("/targets/{target}/report/latest", **documented(models.DatabaseReport))
def latest_report(target: str, daily: bool = False, view: ReportView = "quick"):
    """The newest run of any kind, or with `daily` the newest one the Reports tab shows.
    `view=detailed` adds the detailed sections."""
    from features.monitor.report import present_report

    store = _store()
    # Sample days are demo history; the latest is always a real run when there is one.
    rows = store.read("SELECT * FROM report_run WHERE target=? "
                      + (DAILY_ONLY if daily else "") +
                      "ORDER BY sample,COALESCE(period_end,ts) DESC,ts DESC,id DESC LIMIT 1",
                      (target,))
    # Null until the first checkup; a fresh database's landing page reads this.
    return present_report(rows[0], store, view=view) if rows else None


# The Reports tab shows the finished nightly reports; a checkup someone runs, like the first
# one, feeds Actions.
DAILY_ONLY = "AND trigger='nightly' AND state='done' "


@router.get("/targets/{target}/report/history", **documented(models.ReportHistory))
def report_history(target: str, limit: int = 10, daily: bool = False):
    """Finished reports only, newest day first; sample days included. Every run counts, or
    with `daily` only the ones the Reports tab shows."""
    from features.monitor.report import report_summary

    if not 1 <= limit <= 100:
        raise HTTPException(422, "limit must be between 1 and 100")
    store = _store()
    rows = store.read("SELECT id,target,ts,trigger,finished_at,report_json,period_start,"
                         "period_end,sample FROM report_run WHERE target=? AND state='done' "
                         + (DAILY_ONLY if daily else "") +
                         "ORDER BY COALESCE(period_end,ts) DESC,sample,ts DESC,id DESC LIMIT ?",
                         (target, limit))
    texts = {item["query_key"]: item.get("text") or "" for row in rows
             for item in ((json.loads(row["report_json"] or "null") or {}).get("computed")
                          or {}).get("top_queries") or [] if isinstance(item, dict)}
    names = target_names(store, target, texts) if rows else {}
    return {"reports": [report_summary(row, names) for row in rows]}


def _target_report(store: MonitorStore, target: str, report_id: int) -> dict:
    row = store.report(report_id)
    if row is None or row["target"] != target:
        raise HTTPException(404, "Report not found")
    return row


@router.get("/targets/{target}/report/{report_id}", **documented(models.DatabaseReport))
def get_report(target: str, report_id: int, view: ReportView = "quick"):
    """One run; `view=detailed` adds the detailed sections."""
    from features.monitor.report import present_report

    store = _store()
    return present_report(_target_report(store, target, report_id), store, view=view)


REPORT_EVENT_POLL_SECONDS = 0.5


@router.get("/targets/{target}/report/{report_id}/events")
async def report_events(target: str, report_id: int, request: Request):
    """Progress as unnamed SSE frames; `id` is the event sequence, replayed from the start."""
    store = _store()
    row = await asyncio.to_thread(_target_report, store, target, report_id)
    previous = request.headers.get("last-event-id")
    cursor = int(previous) if previous and previous.isdecimal() else -1

    async def stream():
        nonlocal cursor
        if not await asyncio.to_thread(store.report_events, report_id) and \
                row["state"] != "running":
            # Runs from before progress events existed replay as their outcome alone.
            yield {"id": "0", "data": json.dumps({
                "type": "run", "state": row["state"], "started_at": row["ts"],
                "elapsed_s": max(0, (row["finished_at"] or row["ts"]) - row["ts"])})}
            return
        while True:
            entries = await asyncio.to_thread(store.report_events, report_id, cursor)
            for entry in entries:
                cursor = entry["seq"]
                yield {"id": str(cursor), "data": json.dumps(entry["event"])}
                event = entry["event"]
                if event.get("type") == "run" and event.get("state") != "running":
                    return
            if not entries:
                current = await asyncio.to_thread(store.report, report_id)
                if current is not None and current["state"] != "running":
                    # A run stopped by a restart never wrote its last frame.
                    yield {"id": str(cursor + 1), "data": json.dumps({
                        "type": "run", "state": current["state"], "started_at": current["ts"],
                        "elapsed_s": max(0, (current["finished_at"] or current["ts"]) -
                                         current["ts"])})}
                    return
            if await request.is_disconnected():
                return
            await asyncio.sleep(REPORT_EVENT_POLL_SECONDS)

    return EventSourceResponse(stream())


@router.post("/targets/{target}/report/{report_id}/cancel", **documented(models.DatabaseReport))
def cancel_report(target: str, report_id: int):
    from features.monitor.report import present_report, request_cancel

    store = _store()
    _target_report(store, target, report_id)
    row = request_cancel(store, report_id, _now())
    return present_report(row, store)

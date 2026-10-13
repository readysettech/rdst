"""Durable, low-priority Deep Analyze work for monitored targets."""

from __future__ import annotations

import asyncio
import heapq
import json
import logging
import os
import re
import time
from collections.abc import Callable
from typing import Any

from sqlglot import exp, parse

from features.analyze.parameter_suggestions import parameter_literal as _literal, plan_only_sql
from features.analyze.models import AnalyzeInput, AnalyzeOptions
from features.analyze.service import AnalyzeService
from features.monitor import database_limit
from features.monitor import telemetry as monitor_telemetry
from features.monitor.analysis_digest import digest_for_ref, summary_for_job
from features.monitor.presentation import analysis_error_kind, analysis_reason, display_name
from shared.monitor.write_lookup import lookup_for
from shared.llm_manager.inference_attribution import (count_billable_calls, inference_workflow,
                                                      monitoring_database)
from shared.monitor.monitor_store import (ANALYSIS_CLAIM_ORDER, ANALYSIS_SPENDS_CAP,
                                          FIRST_HOUR_JOB, FIRST_HOUR_S, NEWCOMER_REASONS,
                                          REANALYSIS_REASONS, MonitorStore,
                                          default_analysis_daily_cap, local_day_bounds)
from shared import agent_activity, analysis_lane
from shared.monitor.statements import statement_kind
from shared.query_safety import is_plain_explainable_write, validate_query_safety

logger = logging.getLogger(__name__)
TICK_SECONDS = 300
JOB_TIMEOUT_SECONDS = 600
AUTOMATIC_REASONS = ("new", "top15_stale")
# A PostgreSQL `$N` placeholder left in a statement after the real-value pass.
_PG_PLACEHOLDER = re.compile(r"\$\d+\b")
# Queue estimates use this until a few analyses have finished.
DEFAULT_RUN_SECONDS = 120
# The first hour: the top N reads get their first deep analysis as soon as queries appear.
# N is FIRST_HOUR_TOP, or the analysis_daily_cap setting when that is smaller; the cap
# itself only budgets the day. The queue runs one analysis at a time per database, which
# keeps the extra load on it small. The top list is read on every pass while the first
# readings settle (FIRST_HOUR_SETTLE_S), then every FIRST_HOUR_RECHECK_S (once a
# reading), so a query that reshuffles onto the first page is queued right away. The hour
# analyzes at most FIRST_HOUR_SPREAD times N queries.
FIRST_HOUR_TOP = 10
FIRST_HOUR_SETTLE_S = 300
FIRST_HOUR_RECHECK_S = 60
FIRST_HOUR_SPREAD = 3
# Where each running analysis is: sample (finding real parameter values), plan (EXPLAIN),
# run (EXPLAIN ANALYZE executing the query), schema (reading the schema), advise (the AI
# reading the plan) or verify (testing the suggested fixes). In memory; rows and the report
# run show it live.
_phases: dict[tuple[str, str], str] = {}
_ADVISE_STAGES = {"analyzing_llm", "storing_results", "complete"}
_VERIFY_STAGES = {"testing_rewrites", "testing_indexes", "checking_readyset"}
# A query new to the top that today's allowance can't start isn't queued; the next day's
# pass picks it up if it's still among the busiest. A job seen over the allowance before
# that pass trims it says so.
OVER_ALLOWANCE = ("Today's automatic analyses are done. RDST picks this query up tomorrow "
                  "if it's still among the busiest, or you can analyze it now.")
# Automatic analyses need a Readyset account, even with an AI key of your own.
SIGN_IN_FOR_MONITORING = "Sign in to turn on AI monitoring"
# A failure that's the AI service's rather than the query's (the Key Service or hosted AI
# erroring, busy or not answering) retries by itself after each of these waits. Past the
# last one, the query waits for the normal policy and the row explains why.
RETRY_BACKOFF_S = (60, 300, 1800)
# How soon the queue looks again when jobs are waiting but none could start.
WAITING_RECHECK_S = 60
# Network-level failures on the way to the AI service, beyond the hosted codes the row
# presentation already calls "AI unavailable".
_TRANSIENT_CODES = ("deadline exceeded", "proxy_unreachable", "connection_error",
                    "stream_interrupted", "stream_failed", "anthropic_unavailable")


def is_transient_failure(error: str | None) -> bool:
    """Whether a failed job's error is the AI service's and worth retrying on its own."""
    if not error:
        return False
    lower = error.lower()
    return (analysis_error_kind("failed", error) in {"ai_unavailable", "ai_bad_answer"}
            or any(code in lower for code in _TRANSIENT_CODES))


def retry_plan(store: MonitorStore, target: str, now: int) -> dict[str, dict[str, Any]]:
    """Per query whose latest analysis failed transiently: attempts so far and the retry time.

    ``retry_at`` is None once every backoff step was used (the retries gave up).
    """
    rows = store.read(
        "SELECT j.query_key,j.state,j.error,j.finished_at,j.reason FROM analyze_job j "
        "WHERE j.target=? AND j.query_key IN (SELECT query_key FROM analyze_job "
        "WHERE target=? AND state='failed' AND finished_at>=?) ORDER BY j.query_key,j.id DESC",
        (target, target, now - 86400))
    plans: dict[str, dict[str, Any]] = {}
    streak: dict[str, int] = {}
    closed: set[str] = set()
    for row in rows:
        key = row["query_key"]
        if key in closed:
            continue
        if row["state"] != "failed" or not is_transient_failure(row["error"]):
            closed.add(key)
            continue
        if key not in plans:
            plans[key] = {"finished_at": row["finished_at"] or now, "reason": row["reason"]}
        streak[key] = streak.get(key, 0) + 1
    result = {}
    for key, plan in plans.items():
        attempts = streak[key]
        wait = RETRY_BACKOFF_S[attempts - 1] if attempts <= len(RETRY_BACKOFF_S) else None
        result[key] = {"attempts": attempts, "reason": plan["reason"],
                       "retry_at": plan["finished_at"] + wait if wait is not None else None}
    return result


def analysis_retry(job: dict[str, Any], retry: dict[str, Any] | None) -> dict[str, Any]:
    """A failed job's retry facts for a query row, overriding its kind and reason.

    While a retry is pending the row says ``retrying`` (soft, no error); once the retries
    gave up it says calmly why and that the normal schedule picks it up.
    """
    if job["state"] != "failed" or not retry:
        return {"attempts": None, "retry_at": None}
    facts: dict[str, Any] = {"attempts": retry["attempts"], "retry_at": retry["retry_at"]}
    if not database_limit._signed_in() and (job["reason"] != "user" or not database_limit.own_key()):
        return {**facts, "retry_at": None, "error_kind": "login_required",
                "reason": SIGN_IN_FOR_MONITORING if job["reason"] != "user"
                else "Sign in to run deep analysis"}
    if retry["retry_at"] is not None:
        return {**facts, "error_kind": "retrying",
                "reason": "The AI service didn't answer this time. RDST tries again shortly."}
    return {**facts, "reason": (f"RDST tried {retry['attempts']} times and the AI service "
                                "didn't answer. It tries again tomorrow, or click Analyze now.")}


def enqueue_transient_retries(target: str, *, store: MonitorStore | None = None,
                              now: int | None = None) -> list[dict[str, Any]]:
    """Queue again the analyses that failed on the AI service, once their backoff is up."""
    store = store or MonitorStore()
    now = int(time.time()) if now is None else now
    start, end = store.first_hour(target)
    jobs = []
    for key, plan in retry_plan(store, target, now).items():
        if plan["retry_at"] is None or plan["retry_at"] > now:
            continue
        if plan["reason"] != "user" and not store.levers(target)["deep_analysis"]:
            continue
        if job := _enqueue(target, key, plan["reason"], store=store, now=now,
                           first_hour=plan["reason"] != "user" and start <= now < end,
                           uncapped=True):
            jobs.append(job)
    return jobs


def next_retry_at(store: MonitorStore, targets: list[str], now: int) -> int | None:
    """The soonest pending transient retry across `targets`, so the queue wakes for it."""
    times = [plan["retry_at"] for target in targets
             for plan in retry_plan(store, target, now).values()
             if plan["retry_at"] is not None and plan["retry_at"] > now]
    return min(times) if times else None


def current_phase(target: str, key: str) -> str | None:
    return _phases.get((target, key))


class _PhaseTimer:
    """Wall time per analysis phase: sampling values, planning or running the query, the AI
    advice and the rewrite and index tests."""

    def __init__(self) -> None:
        self.started = self.since = time.monotonic()
        self.phase: str | None = "sample"
        self.spent: dict[str, float] = {}

    def enter(self, phase: str | None) -> None:
        now = time.monotonic()
        if self.phase is not None:
            self.spent[self.phase] = self.spent.get(self.phase, 0.0) + now - self.since
        self.phase, self.since = phase, now

    def describe(self, ai_calls: int) -> str:
        total = time.monotonic() - self.started
        parts = [f"{name} {seconds:.0f} s" for name, seconds in self.spent.items()
                 if seconds >= 0.5]
        calls = f", {ai_calls} AI calls" if ai_calls > 1 else ""
        return f"in {total:.0f} s ({', '.join(parts)}{calls})"


def _phase_for(stage: str, mode: str | None) -> str:
    if stage in _ADVISE_STAGES:
        return "advise"
    if stage in _VERIFY_STAGES:
        return "verify"
    if stage == "collecting_schema":
        return "schema"
    if stage == "executing_explain" and mode == "analyze":
        return "run"
    return "plan"


# Room for two databases' first passes side by side; each database keeps its own cap.
GLOBAL_ANALYSIS_LIMIT = 6

# A new database's first pass tests rewrites for only this many of its busiest queries.
FIRST_PASS_REWRITE_TESTS = 3


def _tests_rewrites(store: MonitorStore, job: dict[str, Any]) -> bool:
    """Whether this automatic analysis tests rewrites: always, except a first-pass job outside
    the database's busiest few."""
    start, end = store.first_hour(job["target"])
    if not (start <= int(job["enqueued_at"] or 0) < end):
        return True
    heavier = store.read("SELECT COUNT(*) n FROM analyze_job WHERE target=? AND id!=? "
                         f"AND {FIRST_HOUR_JOB} AND COALESCE(priority,0)>?",
                         (job["target"], job["id"], start, end, float(job["priority"] or 0)))
    return heavier[0]["n"] < FIRST_PASS_REWRITE_TESTS


def global_limit() -> int:
    """Deep analyses running at once across every monitored database."""
    try:
        return max(1, int(os.environ.get("RDST_MONITOR_ANALYSIS_LIMIT", GLOBAL_ANALYSIS_LIMIT)))
    except ValueError:
        return GLOBAL_ANALYSIS_LIMIT


def daily_cap(target: str | None = None, store: MonitorStore | None = None) -> int:
    """The target's analysis_daily_cap lever, or the process default."""
    if target is not None:
        return (store or MonitorStore()).levers(target)["analysis_daily_cap"]
    return default_analysis_daily_cap()


def top_n(target: str, store: MonitorStore) -> int:
    """How many of the busiest reads the first hour and the top list cover."""
    return min(FIRST_HOUR_TOP, daily_cap(target, store))


def newcomer_cap(target: str, store: MonitorStore) -> int:
    """The target's newcomer_daily_cap lever: queries new to the top analyzed per day."""
    return store.levers(target)["newcomer_daily_cap"]


def report_reserve(cap: int) -> int:
    """The part of the automatic daily allowance reserved for report analyses."""
    return min(5, cap // 5)


def started_today(store: MonitorStore, target: str, now: int) -> int:
    """Re-analyses and report analyses started in the machine's local day; the allowance
    resets at local midnight. The first monitored hour's automatic analyses have their own
    allowance, and queries new to the top have theirs."""
    return store.analyses_started_today(target, now, REANALYSIS_REASONS)


def budget(store: MonitorStore, target: str, now: int) -> dict[str, int]:
    cap = daily_cap(target, store)
    used = started_today(store, target, now)
    reserve = report_reserve(cap)
    newcomers = newcomer_cap(target, store)
    newcomers_used = store.analyses_started_today(target, now, NEWCOMER_REASONS)
    return {"cap": cap, "used": used, "left": max(0, cap - used), "reserved": reserve,
            "automatic_left": max(0, cap - reserve - used),
            "newcomer_cap": newcomers, "newcomer_left": max(0, newcomers - newcomers_used),
            "reset_at": local_day_bounds(now)[1]}


def average_run_seconds(store: MonitorStore, target: str) -> int:
    """Mean run time of the recent finished analyses, for queue estimates."""
    rows = store.read("SELECT finished_at-started_at AS s FROM analyze_job WHERE target=? "
                      "AND state IN ('done','failed') AND started_at IS NOT NULL "
                      "AND finished_at>=started_at ORDER BY finished_at DESC LIMIT 20", (target,))
    return round(sum(row["s"] for row in rows) / len(rows)) if rows else DEFAULT_RUN_SECONDS


def queue_plan(store: MonitorStore, target: str, now: int) -> dict[str, Any]:
    """Where each waiting job stands, walking the queue in claim order against today's budget.

    Returns the budget, the running job, the average run time and, per queued job id, its
    `queue_state` (today, or held with a reason), its position among the jobs that run today
    and an estimated start. Nothing waits for a later day: an automatic job over today's
    allowance is held until the next pass drops it.
    """
    facts = budget(store, target, now)
    running = store.read("SELECT * FROM analyze_job WHERE target=? AND state='running' "
                         "ORDER BY started_at DESC LIMIT 1", (target,))
    queued = store.read(f"SELECT * FROM analyze_job WHERE target=? AND state='queued' "
                        f"ORDER BY {ANALYSIS_CLAIM_ORDER}", (target,))
    signed_in = database_limit._signed_in()
    automatic_on = bool(store.levers(target)["deep_analysis"])
    own_key = database_limit.own_key()
    eligible = store.full_detail_keys(target)
    enabled = target in store.monitored_targets()
    ai_held = signed_in and database_limit.paused(store, target, now)
    average = average_run_seconds(store, target)
    started = facts["used"]
    newcomers_left = facts["newcomer_left"]
    first_start = now
    if running:
        first_start = now + max(0, average - max(0, now - (running[0]["started_at"] or now)))
    hour_start, hour_end = store.first_hour(target)
    jobs: dict[int, dict[str, Any]] = {}
    position = 0
    for job in queued:
        automatic = job["reason"] in AUTOMATIC_REASONS
        first_hour = automatic and hour_start <= job["enqueued_at"] < hour_end
        limit = facts["cap"] - facts["reserved"] if automatic else facts["cap"]
        newcomer = job["reason"] in NEWCOMER_REASONS and not first_hour
        if not signed_in and (job["reason"] != "user" or not own_key):
            jobs[job["id"]] = {"queue_state": "held", "held_reason": "sign_in", "reason_text":
                               SIGN_IN_FOR_MONITORING if job["reason"] != "user"
                               else "Sign in to run deep analysis"}
        elif job["reason"] != "user" and not enabled:
            jobs[job["id"]] = {"queue_state": "held", "held_reason": "monitoring_off",
                               "reason_text": "Monitoring is off"}
        elif job["reason"] != "user" and job["query_key"] not in eligible:
            jobs[job["id"]] = {"queue_state": "held", "held_reason": "outside_top", "reason_text":
                               "This query is outside the busiest 1,000. Click Analyze now to run it."}
        elif job["reason"] != "user" and ai_held:
            jobs[job["id"]] = {"queue_state": "held", "held_reason": "ai_paused", "reason_text":
                               "AI monitoring is paused. Check this database's AI monitoring status."}
        elif automatic and not automatic_on:
            jobs[job["id"]] = {"queue_state": "held", "held_reason": "automatic_off"}
        elif (job["reason"] == "user" or first_hour or newcomer and newcomers_left > 0
              or not newcomer and started < limit):
            position += 1
            if newcomer:
                newcomers_left -= 1
            else:
                started += job["reason"] != "user" and not first_hour
            jobs[job["id"]] = {"queue_state": "today", "queue_position": position,
                               "estimated_start_at": first_start + (position - 1) * average}
        else:
            jobs[job["id"]] = {"queue_state": "held", "held_reason": "over_allowance",
                               "reason_text": OVER_ALLOWANCE}
    return {**facts, "average_run_s": average, "automatic_enabled": automatic_on,
            "first_hour": first_hour_facts(store, target, now),
            "running": running[0] if running else None, "queued": queued, "jobs": jobs}


def waiting_reason(plan: dict[str, Any], job_id: int) -> str | None:
    """One sentence for a queued job that won't start on its own."""
    fact = plan["jobs"].get(job_id) or {}
    if fact.get("reason_text"):
        return fact["reason_text"]
    if fact.get("queue_state") == "held":
        return "Automatic analysis is off. Click Analyze now to run this one."
    return None


def timeout_ms(mean_ms: float) -> int:
    return max(1000, min(30000, round(5 * max(0, mean_ms))))


def analysis_mode(sql: str, mean_ms: float, execute: bool) -> str:
    safety = validate_query_safety(sql)
    return "analyze" if (execute and mean_ms <= 3000 and safety.get("safe")
                         and _read_only_select(sql)) else "explain"


def _lexical_read_only_select(sql: str) -> bool | None:
    """Conservative fallback for PostgreSQL syntax the parser does not know."""
    bare = []
    pos = 0
    while pos < len(sql):
        if sql.startswith("--", pos):
            end = sql.find("\n", pos)
            pos = len(sql) if end < 0 else end
        elif sql.startswith("/*", pos):
            end = sql.find("*/", pos + 2)
            if end < 0:
                return None
            pos = end + 2
        elif sql[pos] in "'\"`":
            quote = sql[pos]
            pos += 1
            while pos < len(sql):
                if sql[pos] == quote:
                    pos += 1
                    if pos < len(sql) and sql[pos] == quote:
                        pos += 1
                    else:
                        break
                else:
                    pos += 1
            else:
                return None
            bare.append(" ")
        elif sql[pos] == "$" and (match := re.match(r"\$[A-Za-z_]*\$", sql[pos:])):
            marker = match.group()
            end = sql.find(marker, pos + len(marker))
            if end < 0:
                return None
            pos = end + len(marker)
            bare.append(" ")
        else:
            bare.append(sql[pos])
            pos += 1
    visible = "".join(bare).strip()
    if ";" in visible.rstrip(";").strip():
        return False
    words = re.findall(r"[A-Za-z_][A-Za-z_0-9]*", visible.upper())
    if not words:
        return None
    if words[0] not in {"SELECT", "WITH"}:
        return False
    if set(words) & {"INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "ALTER",
                     "DROP", "TRUNCATE", "GRANT", "COPY", "REPLACE", "INTO"}:
        return False
    # Locking reads, in PostgreSQL's and MySQL's spellings, take row locks when run.
    return not bool(re.search(r"\bFOR\s+(?:NO\s+KEY\s+UPDATE|UPDATE|KEY\s+SHARE|SHARE)\b"
                              r"|\bLOCK\s+IN\s+SHARE\s+MODE\b", visible, re.I))


def _read_only_select(sql: str) -> bool | None:
    try:
        statements = [statement for statement in parse(sql, read="postgres") if statement is not None]
    except Exception:
        return _lexical_read_only_select(sql)
    return bool(len(statements) == 1 and isinstance(
        statements[0], (exp.Select, exp.Union, exp.Except, exp.Intersect))
        and not any(isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Into, exp.Lock))
                    for node in statements[0].walk()))


# SELECTs that exist to change state, like the session setup drivers and poolers send.
_STATE_CHANGING_CALL = re.compile(
    r"\b(?:pg_catalog\.)?(set_config|pg_advisory_(?:xact_)?lock\w*|pg_try_advisory_\w*|nextval|"
    r"setval|pg_terminate_backend|pg_cancel_backend|pg_notify|pg_reload_conf|"
    r"pg_stat_statements_reset|pg_stat_reset\w*|dblink_exec|lo_unlink)\s*\(", re.IGNORECASE)


def automatic_skip_reason(sql: str) -> str | None:
    from shared.monitor.statements import analyzability

    if statement_kind(sql) == "write" and (lookup := lookup_for(sql)) is not None:
        # A write is analyzed through the SELECT that finds its rows, never run or explained.
        sql = lookup["sql"]
    verdict = analyzability(sql)
    if not verdict["analyzable"] and verdict["analyzable_code"] != "no_text":
        return verdict["not_analyzable_reason"]
    safety = validate_query_safety(sql)
    if not safety.get("safe"):
        issues = "; ".join(safety.get("issues") or [])
        return f"Read-only safety validation rejected the statement: {issues}"
    read_only = _read_only_select(sql)
    if read_only is None:
        return "RDST couldn't parse this statement"
    if not read_only:
        return "Only SELECT statements are eligible for automatic Deep Analyze"
    if match := _STATE_CHANGING_CALL.search(sql):
        return (f"It calls {match.group(1).lower()}, which changes session or database state, "
                "so it isn't analyzed automatically")
    return None


def _weights(store: MonitorStore, target: str, now: int) -> dict[str, dict[str, Any]]:
    # Minute data covers the full 24h window unless the store's size budget
    # demoted it. The rollup-aware total handles that case as well.
    from features.monitor.delivery import query_totals
    from features.monitor.ranking import blended_ms

    eligible = store.full_detail_keys(target)
    priors = store.priors(target)
    # Ranked the way the query list ranks: live calls at a cost per call steadied by the
    # engine's long-run average, so the queue analyzes what page 1 shows.
    return {key: {**row, "total_ms": blended_ms(row.get("calls") or 0, row.get("total_ms") or 0.0,
                                                priors.get(key))}
            for key, row in query_totals(store, target, now - 86400, now + 1).items()
            if key in eligible}


def _priority(store: MonitorStore, target: str, key: str, now: int) -> float:
    return float(_weights(store, target, now).get(key, {}).get("total_ms", 0))


def _enqueue(target: str, key: str, reason: str, *, store: MonitorStore | None = None,
             now: int | None = None, priority: float | None = None,
             first_hour: bool = False, uncapped: bool = False) -> dict[str, Any] | None:
    store = store or MonitorStore()
    now = int(time.time()) if now is None else now
    rows = store.read("SELECT text FROM query_text WHERE target=? AND query_key=?", (target, key))
    if not rows:
        return None
    if reason != "user":
        if key not in store.full_detail_keys(target):
            return None
        skip = automatic_skip_reason(rows[0]["text"])
        if skip:
            store.skip_analysis_once(target, key, reason, now, skip)
            return None
        if store.read("SELECT 1 FROM analyze_job WHERE target=? AND query_key=? "
                      "AND state='skipped' LIMIT 1", (target, key)):
            return None
    cap = newcomer_cap(target, store) if reason in NEWCOMER_REASONS else daily_cap(target, store)
    return store.enqueue_analysis(target, key, reason,
                                  _priority(store, target, key, now) if priority is None else priority,
                                  now, cap=None if first_hour or uncapped or reason == "user"
                                  else cap)


def enqueue_user(target: str, key: str, *, store: MonitorStore | None = None,
                 now: int | None = None) -> dict[str, Any] | None:
    """A click: a new job goes to the front of the queue. A job already waiting its turn
    today keeps its place and comes back with `already_queued`; a held one moves to the
    front. A click always runs and never spends an allowance."""
    store = store or MonitorStore()
    now = int(time.time()) if now is None else now
    existing = store.read("SELECT * FROM analyze_job WHERE target=? AND query_key=? "
                          "AND state IN ('queued','running') ORDER BY id DESC LIMIT 1",
                          (target, key))
    if existing:
        job = existing[0]
        if job["state"] == "running":
            return {**job, "already_queued": True}
        if queue_plan(store, target, now)["jobs"].get(job["id"], {}).get(
                "queue_state") == "today":
            return {**job, "already_queued": True}
    job = _enqueue(target, key, "user", store=store, now=now)
    return {**job, "already_queued": False} if job else None


def enqueue_for_report(target: str, keys: list[str], *, store: MonitorStore | None = None,
                       now: int | None = None) -> list[dict[str, Any]]:
    """Analyses for the report's queries, heaviest first, as many as today's allowance can
    still start; the report reads the plans of the rest."""
    store = store or MonitorStore()
    now = int(time.time()) if now is None else now
    latest = store.latest_analysis_jobs(target)
    room = budget(store, target, now)["left"] - store.read(
        "SELECT COUNT(*) AS n FROM analyze_job WHERE target=? AND state='queued' "
        "AND reason='report'", (target,))[0]["n"]
    jobs = []
    for key in dict.fromkeys(keys):
        previous = latest.get(key)
        if previous and previous["state"] == "done" and (previous["finished_at"] or 0) > now - 86400:
            continue
        if previous and previous["state"] in ("queued", "running"):
            job = _enqueue(target, key, "report", store=store, now=now)
            if job:
                jobs.append(job)
            continue
        if room <= 0:
            continue
        job = _enqueue(target, key, "report", store=store, now=now)
        if job:
            room -= 1
            jobs.append(job)
    return jobs


def top_reads(store: MonitorStore, target: str, weights: dict[str, dict[str, Any]],
              count: int) -> list[str]:
    """The `count` heaviest queries by load that automatic analysis may run: reads with
    load, then, when fewer than `count` are running, reads RDST has seen but that are idle
    in the window. Writes and statements RDST never analyzes on its own don't take a place."""
    keys = [key for key in sorted(weights, key=lambda key: (-weights[key]["total_ms"], key))
            if weights[key]["total_ms"]]
    top: list[str] = []
    for chunk in range(0, len(keys), 50):
        if len(top) >= count:
            break
        batch = keys[chunk:chunk + 50]
        texts = {row["query_key"]: row["text"] for row in store.read(
            "SELECT query_key,text FROM query_text WHERE target=? "
            "AND query_key IN (SELECT value FROM json_each(?))", (target, json.dumps(batch)))}
        for key in batch:
            if len(top) >= count:
                break
            if not automatic_skip_reason(texts.get(key) or ""):
                top.append(key)
    if len(top) < count:
        # A small database runs only a few queries; the spare places go to the others it
        # shows, newest first, so every query on its page gets a score.
        for row in store.read(
                "SELECT q.query_key,q.text FROM query_text q WHERE q.target=? "
                "AND q.query_key NOT IN (SELECT value FROM json_each(?)) AND NOT EXISTS ("
                "SELECT 1 FROM system_query s WHERE s.target=q.target "
                "AND s.query_key=q.query_key) ORDER BY q.last_seen DESC LIMIT ?",
                (target, json.dumps(keys), 4 * count)):
            if len(top) >= count:
                break
            if not automatic_skip_reason(row["text"] or ""):
                top.append(row["query_key"])
    return top


def _load(weights: dict[str, dict[str, Any]], key: str) -> float:
    """A query's load in the window; an idle one has none."""
    return float((weights.get(key) or {}).get("total_ms") or 0)


def first_hour_max(top: int) -> int:
    """At most this many queries get a first-hour analysis while the top list settles."""
    return FIRST_HOUR_SPREAD * top


def _first_hour_keys(store: MonitorStore, target: str, start: int) -> set[str]:
    """Queries the first hour has queued or analyzed so far."""
    return {row["query_key"] for row in store.read(
        "SELECT DISTINCT query_key FROM analyze_job WHERE target=? "
        f"AND state!='skipped' AND {FIRST_HOUR_JOB}", (target, start, start + FIRST_HOUR_S))}


def _next_first_hour_check(start: int, now: int) -> int:
    """The next scheduled read of the top list after the first readings settled."""
    settled = start + FIRST_HOUR_SETTLE_S
    if now < settled:
        return settled
    return settled + ((now - settled) // FIRST_HOUR_RECHECK_S + 1) * FIRST_HOUR_RECHECK_S


def first_hour_facts(store: MonitorStore, target: str, now: int) -> dict[str, Any] | None:
    """Where the first hour stands: how many top reads it covers, how many it has analyzed
    and when it next checks the top list. None outside it."""
    start, end = store.first_hour(target)
    if not start or not start <= now < end:
        return None
    top = top_n(target, store)
    next_check = _next_first_hour_check(start, now)
    from features.monitor.connection import ANSWERED

    readings = store.read("SELECT MIN(ts) AS ts,COUNT(*) AS n FROM monitor_pulse WHERE target=? "
                          "AND kind='collect' AND ok=1 AND detail!=? AND ts>=?",
                          (target, ANSWERED, start))[0]
    found = store.read("SELECT MIN(first_seen) AS ts FROM query_text WHERE target=? "
                       "AND first_seen>=?", (target, start))[0]["ts"]
    counts = store.read(
        "SELECT COUNT(*) AS scheduled,COALESCE(SUM(state='done'),0) AS analyzed,"
        "COALESCE(SUM(state='failed'),0) AS failed FROM analyze_job WHERE target=? "
        f"AND {FIRST_HOUR_JOB}", (target, start, start + FIRST_HOUR_S))[0]
    return {"started_at": start, "ends_at": end, "top": top, **counts,
            "max": first_hour_max(top),
            "next_check_at": next_check if next_check < end else None,
            "first_reading_at": readings["ts"], "readings": readings["n"],
            "first_queries_at": found}


def _first_hour_check_due(store: MonitorStore, target: str, start: int, now: int) -> bool:
    """The top list is read on every pass while the first readings settle, then every
    FIRST_HOUR_RECHECK_S."""
    if now - start <= FIRST_HOUR_SETTLE_S:
        return True
    rows = store.read("SELECT value FROM monitor_setting WHERE target=? "
                      "AND key='first_hour_checked_at'", (target,))
    last = int(rows[0]["value"]) if rows and str(rows[0]["value"]).isdigit() else 0
    return last < start + FIRST_HOUR_SETTLE_S or now - last >= FIRST_HOUR_RECHECK_S


def _recently_covered(previous: dict[str, Any] | None, now: int) -> bool:
    return bool(previous) and (
        previous["state"] in ("queued", "running", "skipped")
        or previous["state"] == "done" and (previous["finished_at"] or 0) > now - 86400)


def enqueue_first_hour(target: str, *, store: MonitorStore | None = None,
                       now: int | None = None) -> list[dict[str, Any]]:
    """The first hour: deep analysis for the top N reads as soon as queries appear, so the
    first page is scored within minutes. Writes never count. The queue starts them
    heaviest first within the database connection budget. A query analyzed in the last day
    counts as covered, and the hour analyzes at most first_hour_max(N) queries, each once."""
    store = store or MonitorStore()
    now = int(time.time()) if now is None else now
    start, end = store.first_hour(target)
    if not start or not start <= now < end or not store.levers(target)["deep_analysis"]:
        return []
    if not _first_hour_check_due(store, target, start, now):
        return []
    weights = _weights(store, target, now)
    # Impact needs live calls, which the second reading is the first to measure: until a
    # query has some, nothing is ranked, so nothing is queued (and the check stays due).
    if not any(_load(weights, key) > 0 for key in weights):
        return []
    store.write_setting(target, "first_hour_checked_at", str(now))
    top = top_n(target, store)
    ranked = top_reads(store, target, weights, top + top // 2)
    if len(ranked) == top + top // 2:
        # While readings settle, a waiting query that fell well out of the top makes room
        # for the one that replaced it; it can come back as a newcomer later.
        waiting = {row["query_key"] for row in store.read(
            "SELECT query_key FROM analyze_job WHERE target=? AND state='queued' "
            f"AND {FIRST_HOUR_JOB}", (target, start, end))}
        if dropped := sorted(waiting - set(ranked)):
            store.drop_queued_analyses(target, dropped)
    # Waiting jobs follow the latest reading: the heaviest query now goes next.
    store.reprioritize_queued(target, {key: _load(weights, key) for key in ranked})
    latest = store.latest_analysis_jobs(target)
    covered = _first_hour_keys(store, target, start)
    jobs = []
    for key in ranked[:top]:
        previous = latest.get(key)
        if key in covered or _recently_covered(previous, now):
            continue
        if len(covered) >= first_hour_max(top):
            break
        job = _enqueue(target, key, "top15_stale" if previous else "new", store=store, now=now,
                       priority=_load(weights, key), first_hour=True)
        if job:
            covered.add(key)
            jobs.append(job)
    return jobs


class _Slots:
    """Automatic queue room for one allowance: only what today's allowance can still start
    waits in line. A candidate heavier than the lightest queued job of the same allowance
    still gets in; the queue's trim then drops the lightest.
    """

    def __init__(self, store: MonitorStore, target: str, now: int, reasons: tuple[str, ...]):
        facts = budget(store, target, now)
        self.room = (facts["newcomer_left"] if reasons == NEWCOMER_REASONS
                     else facts["automatic_left"])
        self.weights = [row["priority"] for row in store.read(
            "SELECT priority FROM analyze_job WHERE target=? AND state='queued' "
            "AND reason IN (SELECT value FROM json_each(?))",
            (target, json.dumps([reason for reason in reasons if reason in AUTOMATIC_REASONS])))]
        heapq.heapify(self.weights)
        while len(self.weights) > self.room:
            heapq.heappop(self.weights)

    def admits(self, weight: float) -> bool:
        return self.room > 0 and (len(self.weights) < self.room or weight > self.weights[0])

    def take(self, weight: float) -> None:
        heapq.heappush(self.weights, weight)
        if len(self.weights) > self.room:
            heapq.heappop(self.weights)


def enqueue_new_queries(target: str, *, store: MonitorStore | None = None,
                        now: int | None = None) -> list[dict[str, Any]]:
    """Queries entering the top N for the first time: never analyzed (or only timed out
    before today). They spend the newcomer allowance; one that doesn't fit today isn't
    queued, and a later pass picks it up while it stays in the top N."""
    store = store or MonitorStore()
    levers = store.levers(target)
    if not (levers["deep_analysis"] and levers["auto_analyze_new"]):
        return []
    now = int(time.time()) if now is None else now
    weights = _weights(store, target, now)
    top = top_reads(store, target, weights, top_n(target, store))
    if not top:
        return []
    rows = store.read(
        "SELECT q.query_key FROM query_text q WHERE q.target=? "
        "AND q.query_key IN (SELECT value FROM json_each(?)) AND (NOT EXISTS ("
        "SELECT 1 FROM analyze_job j WHERE j.target=q.target AND j.query_key=q.query_key) "
        "OR (NOT EXISTS (SELECT 1 FROM analyze_job j WHERE j.target=q.target "
        "AND j.query_key=q.query_key AND j.state IN ('queued','running','done')) "
        "AND EXISTS (SELECT 1 FROM analyze_job j WHERE j.target=q.target "
        "AND j.query_key=q.query_key AND j.state='failed' "
        "AND lower(j.error) LIKE '%timed out%' AND j.finished_at<?)))",
        (target, json.dumps(top), local_day_bounds(now)[0]))
    fresh = {row["query_key"] for row in rows}
    slots = _Slots(store, target, now, NEWCOMER_REASONS)
    jobs = []
    for key in top:
        if key not in fresh:
            continue
        weight = _load(weights, key)
        if slots.admits(weight) and (job := _enqueue(target, key, "new", store=store, now=now,
                                                     priority=weight)):
            slots.take(weight)
            jobs.append(job)
    return jobs


def enqueue_stale_top(target: str, *, store: MonitorStore | None = None,
                      now: int | None = None) -> list[dict[str, Any]]:
    store = store or MonitorStore()
    if not store.levers(target)["deep_analysis"]:
        return []
    now = int(time.time()) if now is None else now
    weights = _weights(store, target, now)
    top = sorted(weights, key=lambda key: (-weights[key]["total_ms"], key))[:15]
    latest = store.latest_analysis_jobs(target)
    slots = _Slots(store, target, now, ("top15_stale",))
    jobs = []
    for key in top:
        previous = latest.get(key)
        if previous is None or previous["state"] in ("queued", "running", "skipped"):
            continue
        if previous["state"] == "failed" and "timed out" in (previous["error"] or "").lower():
            if previous["finished_at"] and previous["finished_at"] >= local_day_bounds(now)[0]:
                continue
        elif not previous["finished_at"] or previous["finished_at"] > now - 86400:
            continue
        weight = float(weights[key]["total_ms"])
        if slots.admits(weight) and (job := _enqueue(target, key, "top15_stale", store=store,
                                                     now=now, priority=weight)):
            slots.take(weight)
            jobs.append(job)
    return jobs


# Where a real value came from, by its suggestion's provenance. Live traffic: a running
# session, a digest or statement-history sample, or the monitor's stored activity sample.
# The table's own data: pg_stats or a bounded read of the column. Anything else (a query
# shape hint such as LIMIT 10) is reserved for plain EXPLAIN.
_LIVE_PROVENANCE = ("activity", "digest sample", "statement history")
_TABLE_PROVENANCE = ("pg_stats", "Sampled from")
# PostgreSQL typed literals (interval '7 days') normalize to `interval $1`, which doesn't
# parse; the cast form keeps the placeholder for a generic plan.
_TYPED_PLACEHOLDER_RE = re.compile(
    r"\b(interval|date|timestamptz|timestamp)\s+(\$\d+)(?!\w)", re.IGNORECASE)


def _real_suggestion(options: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    for option in options:
        provenance = str(option.get("provenance") or "")
        if option.get("value") is None:
            continue
        if any(mark in provenance for mark in _LIVE_PROVENANCE):
            return option, "sampled"
        if any(mark in provenance for mark in _TABLE_PROVENANCE):
            return option, "table_sampled"
    return None, None


def apply_suggestions(sql: str, data: dict[str, Any]) -> tuple[str, str | None]:
    """Fill placeholders with real values only, and name where the values came from.

    `sampled`: every placeholder has a value from live traffic. `table_sampled`: every
    placeholder has a real value and at least one came from the table's own data.
    `none`: some placeholder has no real value; it stays a placeholder, so the query can
    only be planned (a generic plan), never run. None: the query has no placeholders.
    """
    chosen: list[tuple[str, str | None, str | None, str | None]] = []
    kinds: list[str | None] = []
    for placeholder in data.get("placeholders", []):
        option, kind = _real_suggestion(placeholder.get("suggestions") or [])
        kinds.append(kind)
        chosen.append((placeholder["placeholder"],
                       None if option is None else str(option["value"]),
                       None if option is None else option.get("literal_sql"),
                       None if option is None else option.get("column_type")))
    if not chosen:
        return sql, None
    if any(kind is None for kind in kinds):
        source = "none"
    elif all(kind == "sampled" for kind in kinds):
        source = "sampled"
    else:
        source = "table_sampled"

    def fill(token: str, value: str | None, literal: str | None, column_type: str | None) -> str:
        if value is None:
            return token
        return literal if literal is not None else _literal(value, column_type)

    if all(token.startswith("$") for token, _, _, _ in chosen):
        values = {token: fill(token, value, literal, column_type)
                  for token, value, literal, column_type in chosen}
        sql = re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|\$\d+",
                     lambda match: values.get(match.group(), match.group()), sql)
        sql = _TYPED_PLACEHOLDER_RE.sub(lambda match: f"{match.group(2)}::{match.group(1)}", sql)
    elif all(token == "?" for token, _, _, _ in chosen):
        values = iter(fill(token, value, literal, column_type)
                      for token, value, literal, column_type in chosen)
        sql = re.sub(r"'[^']*'|\"[^\"]*\"|\?", lambda match: next(values) if match.group() == "?" else match.group(), sql)
    else:
        values = {token: fill(token, value, literal, column_type)
                  for token, value, literal, column_type in chosen}
        sql = re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|(?<![:\w]):[A-Za-z_]\w*",
                     lambda match: values.get(match.group(), match.group()), sql)
    return sql, source


def sample_params(data: dict[str, Any]) -> list[dict[str, str]]:
    """The values apply_suggestions fills in, each placeholder with its SQL literal, in the
    query's order. Placeholders without a real value are left out."""
    found = []
    for placeholder in data.get("placeholders", []):
        option, _ = _real_suggestion(placeholder.get("suggestions") or [])
        if option is not None:
            literal = option.get("literal_sql")
            found.append({"name": placeholder["placeholder"],
                          "value": literal if literal is not None else
                          _literal(str(option["value"]), option.get("column_type"))})
    return found


def _attach_parameters(query_hash: str, analysis_id: str, params: list[dict[str, str]],
                       source: str | None) -> None:
    """Best effort: the analysis has succeeded, and a view without its values beats a failure."""
    from shared.query_registry import AnalysisResultsRegistry

    try:
        AnalysisResultsRegistry().attach_parameters(query_hash, analysis_id, params, source)
    except Exception:
        logger.warning("Could not store the parameters of analysis %s of %s", analysis_id,
                       query_hash, exc_info=True)


def parameter_note(suggestions: dict[str, Any], source: str | None) -> str | None:
    """What sampling found, without the values: "3 parameters sampled from live sessions"."""
    count = len(suggestions.get("placeholders") or [])
    if not count:
        return None
    noun = "parameter" if count == 1 else "parameters"
    return {"sampled": f"{count} {noun} sampled from live sessions",
            "table_sampled": f"{count} {noun} sampled from the table",
            "estimated": f"{count} {noun} used for a plan estimate; EXPLAIN ANALYZE was not run"}.get(
                source, f"{count} {noun} without a sampled value, so the plan is generic")


def analysis_outcome(job: dict[str, Any], statements: list[dict[str, Any]]) -> dict[str, Any]:
    """The audit entry's outcome and decision for one finished job."""
    # A job still marked running was stopped before it finished.
    state = job["state"] if job["state"] in ("done", "failed", "skipped") else "cancelled"
    timeouts = [row["timeout_ms"] for row in statements
                if row["kind"] == "explain_analyze" and row.get("timeout_ms")]
    if job.get("mode") == "analyze":
        decision = "Ran EXPLAIN ANALYZE" + (
            f" with a {timeouts[0] / 1000:g} s timeout" if timeouts else "")
    elif job.get("mode") == "explain":
        decision = "Ran EXPLAIN only, without executing the query"
    else:
        decision = None
    if state == "skipped":
        decision = "Not run: " + (job.get("error") or "not eligible")
    return {"outcome": state, "decision": decision,
            "summary": (job.get("error") or "")[:300] if state == "failed" else "",
            "analysis_ref": job.get("analysis_ref"), "mode": job.get("mode"),
            "param_source": job.get("param_source")}


class AnalyzeQueue:
    def __init__(self, store: MonitorStore | None = None, *,
                 service: AnalyzeService | None = None, clock: Callable[[], float] = time.time,
                 interval_s: int = TICK_SECONDS, job_timeout_s: int = JOB_TIMEOUT_SECONDS):
        self.store = store or MonitorStore()
        self.service = service or AnalyzeService()
        self.clock = clock
        self.interval_s = interval_s
        self.job_timeout_s = job_timeout_s
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        # Set by wake(): a running tick stops waiting on its jobs and claims again.
        self._nudge = asyncio.Event()
        self._active_runs: dict[int, analysis_lane.AnalysisRun] = {}
        # While a pass is busy running jobs, a wake queues new work without waiting for it.
        self._busy = False
        self._refresh: asyncio.Task | None = None
        # The soonest transient-failure retry; the queue wakes for it before its next tick.
        self._retry_at: int | None = None
        # Which database the next claim round starts with.
        self._turn = 0

    async def start(self) -> "AnalyzeQueue":
        if self._task is None:
            self.store.recover_analysis(int(self.clock()), self.job_timeout_s)
            self._stop.clear()
            self._wake.clear()
            self._task = asyncio.create_task(self._run(), name="monitor-analyze-queue")
        return self

    async def stop(self) -> None:
        if self._task is not None:
            self._stop.set()
            self._wake.set()
            for run in self._active_runs.values():
                run.stopped.set()
            if self._active_runs:
                try:
                    await asyncio.wait_for(asyncio.gather(
                        *(asyncio.to_thread(analysis_lane.stop, run)
                          for run in list(self._active_runs.values()))), timeout=5)
                except asyncio.TimeoutError:
                    logger.warning("Analysis cancellation exceeded shutdown grace")
            task, self._task = self._task, None
            _, pending = await asyncio.wait({task}, timeout=10)
            if pending:
                task.cancel()
                _, pending = await asyncio.wait({task}, timeout=1)
                if pending:
                    logger.warning("Analysis task did not finish within shutdown grace")
        global _active_queue
        if _active_queue is self:
            _active_queue = None

    def wake(self) -> None:
        self._wake.set()
        self._nudge.set()
        if self._busy and self._task is not None and (
                self._refresh is None or self._refresh.done()):
            self._refresh = asyncio.create_task(
                asyncio.to_thread(self._prepare, int(self.clock())), name="monitor-analyze-refresh")

    def cancel(self, job_id: int) -> dict[str, Any] | None:
        """Stop a queued or running job. The store marks it cancelled first, so its worker's
        result never lands; a running worker is then told to stop. Returns the job row, or
        None when it had already ended."""
        row = self.store.cancel_analysis(job_id, int(self.clock()))
        if row is None:
            return None
        run = self._active_runs.get(job_id)
        if run is not None:
            run.stopped.set()
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                analysis_lane.stop(run)
            else:
                stop = loop.create_task(asyncio.to_thread(analysis_lane.stop, run))
                stop.add_done_callback(
                    lambda task: task.exception() if not task.cancelled() else None)
        return row

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:
                logger.exception("Deep Analyze queue tick failed")
            timeout = self.interval_s
            if self._retry_at is not None and self._retry_at <= int(self.clock()):
                self._retry_at = None
            if self._retry_at is not None:
                timeout = max(1, min(timeout, self._retry_at - int(self.clock())))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def tick(self) -> None:
        # Store work runs in worker threads: SQLite can wait on another writer's lock, and
        # that wait must never stall the event loop.
        targets = await asyncio.to_thread(self._prepare, int(self.clock()))
        self._busy = True
        try:
            active: set[asyncio.Task] = set()
            try:
                while not self._stop.is_set():
                    # A database added while this tick runs joins it at the next round
                    # instead of waiting for every other database's queue to empty.
                    monitored = await asyncio.to_thread(self.store.monitored_targets)
                    if monitored - set(targets):
                        targets = await asyncio.to_thread(self._prepare, int(self.clock()))
                    # One claim per database per round, under a cap for the whole install, so
                    # every database's first pass moves and the model isn't swamped.
                    claimed = True
                    while claimed and not self._stop.is_set() and len(active) < global_limit():
                        claimed = False
                        # Each round starts one database further on, so a freed slot doesn't
                        # always go back to the first database in the list.
                        start = self._turn % len(targets) if targets else 0
                        for target in targets[start:] + targets[:start]:
                            if len(active) >= global_limit():
                                break
                            job = await asyncio.to_thread(self._claim, target)
                            if job:
                                claimed = True
                                self._turn += 1
                                active.add(asyncio.create_task(self._process_recorded(job)))
                    if not active:
                        break
                    nudged = asyncio.ensure_future(self._nudge.wait())
                    try:
                        done, _ = await asyncio.wait(active | {nudged},
                                                     return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        nudged.cancel()
                    if nudged in done:
                        # Cleared only once seen, so a wake during this round's claims counts.
                        self._nudge.clear()
                        done.discard(nudged)
                    active -= done
                    for task in done:
                        await task
            finally:
                if active:
                    await asyncio.gather(*active)
            # Work waiting that couldn't start (a busy database, a brief pause): look again
            # within a minute instead of a whole tick, so a queue never sits idle for long.
            if await asyncio.to_thread(self._waiting, targets):
                soon = int(self.clock()) + WAITING_RECHECK_S
                self._retry_at = min(filter(None, (self._retry_at, soon)))
        finally:
            self._busy = False

    def _prepare(self, now: int) -> list[str]:
        self.store.recover_analysis(now, self.job_timeout_s)
        targets = sorted(self.store.monitored_targets() | {
            row["target"] for row in self.store.read(
                "SELECT DISTINCT target FROM analyze_job WHERE reason='user' "
                "AND (state='queued' OR state='failed' AND finished_at>=?)", (now - 86400,))})
        for target in targets:
            enqueue_transient_retries(target, store=self.store, now=now)
        self._retry_at = next_retry_at(self.store, targets, now)
        for target in targets:
            if target not in self.store.monitored_targets():
                continue
            start, end = self.store.first_hour(target)
            if start <= now < end:
                enqueue_first_hour(target, store=self.store, now=now)
                self._refresh_priorities(target, now)
                continue
            enqueue_new_queries(target, store=self.store, now=now)
            enqueue_stale_top(target, store=self.store, now=now)
            self._refresh_priorities(target, now)
            facts = budget(self.store, target, now)
            self.store.trim_automatic_analyses(target, facts["automatic_left"], ("top15_stale",))
            self.store.trim_automatic_analyses(target, facts["newcomer_left"], NEWCOMER_REASONS)
        return targets

    def _waiting(self, targets: list[str]) -> bool:
        """Whether a job that could start today is waiting (budget left, not held)."""
        now = int(self.clock())
        for target in targets:
            if not self.store.read("SELECT 1 FROM analyze_job WHERE target=? "
                                   "AND state='queued' LIMIT 1", (target,)):
                continue
            # Held jobs count while their hold can lift by itself (a sign-in, AI resuming);
            # one over today's allowance waits for nothing and gets dropped instead.
            plan = queue_plan(self.store, target, now)
            automatic_on = plan["automatic_enabled"]
            if any(plan["jobs"].get(job["id"], {}).get("reason_text") != OVER_ALLOWANCE
                   and (automatic_on or job["reason"] in ("user", "report"))
                   for job in plan["queued"]):
                return True
        return False

    def _refresh_priorities(self, target: str, now: int) -> None:
        weights = _weights(self.store, target, now)
        self.store.update_analysis_priorities(
            target, {key: float(row["total_ms"]) for key, row in weights.items()})
        jobs = self.store.read(
            "SELECT j.* FROM analyze_job j WHERE j.target=? AND j.state='done' "
            "AND j.query_key IN (SELECT value FROM json_each(?)) AND j.id=("
            "SELECT MAX(id) FROM analyze_job WHERE target=j.target AND query_key=j.query_key "
            "AND state='done') AND NOT EXISTS (SELECT 1 FROM query_ui u "
            "WHERE u.target=j.target AND u.query_key=j.query_key AND u.analyzed=1) "
            "ORDER BY j.id DESC LIMIT 50", (target, json.dumps(list(weights))))
        if jobs:
            summaries = {job["query_key"]: summary for job in jobs if (summary := summary_for_job(job))}
            self.store.sync_query_ui(target, summaries=summaries)

    def _claim(self, target: str) -> dict[str, Any] | None:
        now = int(self.clock())
        # Rank by the load right now; jobs can wait long enough for it to shift.
        self._refresh_priorities(target, now)
        cap = daily_cap(target, self.store)
        signed_in = database_limit._signed_in()
        if not signed_in:
            database_limit.hold_signed_out(self.store, target, now)
        ai_held = database_limit.paused(self.store, target, now)
        return self.store.claim_analysis(
            target, now, cap=cap, reserve=report_reserve(cap),
            newcomer_cap=newcomer_cap(target, self.store),
            eligible_keys=sorted(self.store.full_detail_keys(target)),
            allow_report=signed_in and not ai_held,
            allow_user=signed_in or database_limit.own_key(),
            allow_automatic=self.store.levers(target)["enabled"] and self.store.levers(target)["deep_analysis"]
            and not ai_held)

    def _pulse(self, target: str, ok: bool, detail: str) -> None:
        try:
            self.store.record_pulse(target, int(self.clock()), "analyze", ok, detail)
        except Exception:
            logger.debug("Analyze pulse not recorded for %s", target, exc_info=True)

    async def _process_recorded(self, job: dict[str, Any]) -> None:
        """One job as one agent activity entry: its statements, AI calls and decision."""
        with agent_activity.action("deep_analysis", job["target"], subject=job["query_key"],
                                   run_ref=f"analyze_job:{job['id']}", keep=True,
                                   query_key=job["query_key"], reason=job["reason"]) as act:
            started = time.monotonic()
            worker = asyncio.create_task(self._process(job))
            try:
                done, _ = await asyncio.wait({worker}, timeout=self.job_timeout_s)
            except asyncio.CancelledError:
                run = self._active_runs.get(job["id"])
                if run is not None:
                    run.stopped.set()
                worker.cancel()
                worker.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
                raise
            if not done:
                run = self._active_runs.get(job["id"])
                stop = None
                if run is not None:
                    run.stopped.set()
                    stop = asyncio.create_task(asyncio.to_thread(analysis_lane.stop, run))
                    stop.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
                worker.cancel()
                cleanup = {worker, stop} if stop is not None else {worker}
                done, pending = await asyncio.wait(cleanup, timeout=5)
                if pending:
                    # Retain admission while cancellation-resistant async work is still alive.
                    worker.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
                    self._stop.set()
                await asyncio.to_thread(self.store.finish_analysis, job["id"], "failed",
                                        int(self.clock()), owner=job["owner"], attempt=job["attempt"],
                                        error="Analysis job deadline exceeded")
                if worker.done() and not worker.cancelled():
                    worker.exception()
            else:
                await worker
            rows = await asyncio.to_thread(
                self.store.read, "SELECT state,error,mode,param_source,analysis_ref,owner,attempt "
                "FROM analyze_job WHERE id=?", (job["id"],))
            if rows and rows[0]["owner"] == job["owner"] and rows[0]["attempt"] == job["attempt"]:
                act.describe(**analysis_outcome(rows[0], act.statements))
                await asyncio.to_thread(monitor_telemetry.analysis_finished, job, rows[0],
                                        time.monotonic() - started)

    async def _process(self, job: dict[str, Any]) -> None:
        target, key = job["target"], job["query_key"]
        if self._stop.is_set() or not await asyncio.to_thread(self.store.analysis_owned, job):
            return
        if job["reason"] != "user" and not await asyncio.to_thread(database_limit._signed_in):
            await asyncio.to_thread(self.store.hold_analysis, job["id"],
                                    owner=job["owner"], attempt=job["attempt"])
            return
        run = analysis_lane.begin(target, execute=job.get("mode") != "explain")
        self._active_runs[job["id"]] = run
        _phases[(target, key)] = "sample"
        now = int(self.clock())
        mode = None
        source = None
        billable_calls = 0
        # Seconds spent in each phase, so the activity line says where an analysis's time went.
        timings = _PhaseTimer()
        try:
            rows = await asyncio.to_thread(
                self.store.read, "SELECT text FROM query_text WHERE target=? AND query_key=?",
                (target, key))
            if not rows:
                raise ValueError("Query text is no longer available")
            sql = rows[0]["text"]
            agent_activity.describe(subject=display_name(sql))
            if job["reason"] != "user":
                skip = automatic_skip_reason(sql)
                if skip:
                    await asyncio.to_thread(self.store.finish_analysis, job["id"], "skipped",
                                            int(self.clock()), owner=job["owner"], attempt=job["attempt"],
                                            error=skip)
                    return
            from shared.config.targets import TargetsConfig
            config = TargetsConfig()
            config.load()
            target_config = config.get(target) or {}
            if not await asyncio.to_thread(self.store.analysis_owned, job):
                return
            sample_key = key
            lookup = lookup_for(sql, str(target_config.get("engine") or "postgresql").lower()) \
                if statement_kind(sql) == "write" else None
            if lookup is not None:
                sql = lookup["sql"]
                # Positional values sampled for the write start with its SET list.
                sample_key = key if not lookup["skip_params"] else None
                agent_activity.describe(subject=f"How {display_name(rows[0]['text'])} finds its rows")
            suggestions = await self.service.suggest_parameter_values(
                sql, target, target_config, sample_key)
            if run.stopped.is_set():
                return
            _phases[(target, key)] = "plan"
            timings.enter("plan")
            original_sql = sql
            sql, source = apply_suggestions(sql, suggestions)
            if source == "none" and str(target_config.get("engine", "")).lower() in ("mysql", "mariadb"):
                sql = plan_only_sql(original_sql, "mysql", suggestions)
                source = "estimated"
            agent_activity.describe(parameters=parameter_note(suggestions, source))
            weights = (await asyncio.to_thread(_weights, self.store, target, now)).get(key, {})
            calls = weights.get("calls", 0)
            mean_ms = weights.get("total_ms", 0) / calls if calls else 0
            execute = job.get("mode") != "explain"
            mode = analysis_mode(sql, mean_ms, execute)
            # Without real values only a plan estimate runs, even with consent to measure:
            # EXPLAIN ANALYZE never takes placeholders or made-up values.
            if (source in ("none", "estimated") and suggestions.get("placeholders")) or (
                    source in (None, "none") and _PG_PLACEHOLDER.search(sql)):
                mode = "explain"
            if lookup is not None:
                # A write's row search is only planned: its WHERE may call a function that writes.
                mode = "explain"
            if not validate_query_safety(sql).get("safe") and not (
                mode == "explain" and is_plain_explainable_write(sql)
            ):
                raise ValueError("Query failed read-only safety validation")
            user_requested = job["reason"] == "user"
            # Analyses test the suggested rewrites the same way they read the query: planned
            # only, or run under the same timeout. Testing roughly doubles an analysis, so a new
            # database's first pass tests only its busiest few; the rest wait for their daily
            # re-analysis. The user's own Anthropic key pays when set; otherwise the hosted
            # service does.
            test_rewrites = user_requested or await asyncio.to_thread(
                _tests_rewrites, self.store, job)
            options = AnalyzeOptions(target=target, fast=mode == "explain",
                                     non_interactive=True, statement_timeout_ms=timeout_ms(mean_ms),
                                     test_rewrites=test_rewrites)
            final = None
            error = None
            fingerprint = None if user_requested else await asyncio.to_thread(
                database_limit.target_fingerprint, target)
            if not user_requested and not await asyncio.to_thread(database_limit._signed_in):
                await asyncio.to_thread(self.store.hold_analysis, job["id"],
                                        owner=job["owner"], attempt=job["attempt"])
                return
            if not await asyncio.to_thread(self.store.analysis_owned, job):
                return
            with count_billable_calls() as calls, inference_workflow(
                "analyze" if user_requested else "monitor",
                "web" if user_requested else ("desktop" if os.getenv("RDST_DESKTOP") == "1" else "cli"),
                operation=None if user_requested else "monitor_analyze",
            ), monitoring_database(fingerprint):
                try:
                    for retry in range(2):
                        if run.stopped.is_set() or self._stop.is_set() or not await asyncio.to_thread(
                                self.store.analysis_owned, job):
                            return
                        if not user_requested and not await asyncio.to_thread(database_limit._signed_in):
                            await asyncio.to_thread(self.store.hold_analysis, job["id"],
                                                    owner=job["owner"], attempt=job["attempt"])
                            return
                        final = None
                        error = None
                        try:
                            async for event in self.service.analyze(
                                AnalyzeInput(sql=sql, normalized_sql=rows[0]["text"], source="monitor", hash=key),
                                options,
                            ):
                                if not await asyncio.to_thread(self.store.analysis_owned, job):
                                    return
                                if event.type == "progress":
                                    _phases[(target, key)] = _phase_for(event.stage, mode)
                                    timings.enter(_phases[(target, key)])
                                elif event.type == "complete":
                                    final = event
                                elif event.type == "error":
                                    error = event.message
                                elif event.type == "explain_complete" and not event.success:
                                    error = event.error
                            if final is None or error:
                                raise RuntimeError(error or "Deep Analyze returned no result")
                            if final.llm_analysis and not final.llm_analysis.get("success", True):
                                code = final.llm_analysis.get("error_code")
                                detail = final.llm_analysis.get("error")
                                # Keep the host's detail next to its code, so failures can be told apart.
                                raise RuntimeError(f"{code}: {detail}" if code and detail else
                                                   code or detail or "LLM analysis failed")
                            break
                        except Exception as exc:
                            if retry or not is_transient_failure(str(exc)):
                                raise
                            marked = await asyncio.to_thread(self.store.retry_analysis, job)
                            if not marked:
                                return
                finally:
                    billable_calls = calls.count
            if final.explain_results and final.explain_results.get("explain_analyze_timeout"):
                raise TimeoutError("EXPLAIN ANALYZE timed out; retry tomorrow")
            if not final.query_hash or not final.stored_analysis_id:
                detail = next((result.get("error") or result.get("message")
                               for result in (final.formatted, final.explain_results, final.llm_analysis)
                               if result and (result.get("error") or result.get("message"))), None)
                missing = ", ".join(name for name in ("query_hash", "stored_analysis_id")
                                    if not getattr(final, name))
                raise RuntimeError(f"Deep Analyze did not store a result: {detail or f'missing {missing}'}")
            plan_format = (final.explain_results or {}).get("plan_format")
            if (final.explain_results or {}).get("param_source") == "estimated":
                source = "estimated"
            if plan_format:
                mode = "analyze" if plan_format == "analyze" else "explain"
            if run.stopped.is_set():
                return
            ref = (f"/api/query-registry/{final.query_hash}/analysis/"
                   f"{final.stored_analysis_id}")
            # A write's values belong to its row search, not to the statement itself.
            params = sample_params(suggestions) if lookup is None else []
            if params or source == "estimated":
                await asyncio.to_thread(_attach_parameters, final.query_hash,
                                        final.stored_analysis_id, params, source)
            finished = await asyncio.to_thread(
                self.store.finish_analysis, job["id"], "done", int(self.clock()),
                owner=job["owner"], attempt=job["attempt"], analysis_ref=ref, mode=mode, param_source=source,
                billable_calls=billable_calls)
            if not finished:
                return
            summary = await asyncio.to_thread(digest_for_ref, target, job["query_key"], ref, mode)
            if summary:
                await asyncio.to_thread(self.store.update_query_ui, target, job["query_key"], summary=summary)
            timings.enter(None)
            await asyncio.to_thread(self._pulse, target, True,
                                    f"Analyzed {display_name(rows[0]['text'])} "
                                    f"{timings.describe(billable_calls)}")
            if run.stopped.is_set():
                return
            await asyncio.to_thread(self.store.action_item_for_subject, target,
                                    "monitoring_issue", "llm_login", int(self.clock()))
            if not user_requested:
                await asyncio.to_thread(database_limit.clear, self.store, target, int(self.clock()))
                await asyncio.to_thread(database_limit.clear_allowance, self.store, "hosted",
                                        int(self.clock()))
        except Exception as exc:
            if not await asyncio.to_thread(self.store.analysis_owned, job):
                return
            if job["reason"] != "user" and not await asyncio.to_thread(database_limit._signed_in):
                await asyncio.to_thread(self.store.hold_analysis, job["id"],
                                        owner=job["owner"], attempt=job["attempt"])
                return
            if run.stopped.is_set():
                return
            message = str(exc)
            if database_limit.is_limit_error(exc):
                message = f"{database_limit.LIMIT_CODE}: {database_limit.LIMIT_MESSAGE}"
                await asyncio.to_thread(database_limit.file_refusal, self.store, target,
                                        int(self.clock()))
            if allowance := database_limit.is_allowance_error(exc):
                # Automatic analyses hold until the allowance is raised; one retries now and then.
                await asyncio.to_thread(database_limit.file_allowance, self.store, target,
                                        allowance, int(self.clock()))
            if "LOGIN_REQUIRED" in message:
                message = "LOGIN_REQUIRED: Sign in to run Deep Analyze"
                await asyncio.to_thread(
                    self.store.upsert_item, target, "monitoring_issue", "llm_login", .6,
                    "Sign in to continue deep analysis", {"error": message}, int(self.clock()),
                    notify=False)
            finished = await asyncio.to_thread(
                self.store.finish_analysis, job["id"], "failed", int(self.clock()),
                owner=job["owner"], attempt=job["attempt"], error=message, mode=mode, param_source=source, billable_calls=billable_calls)
            if not finished:
                return
            if is_transient_failure(message):
                retry = (await asyncio.to_thread(retry_plan, self.store, target,
                                                 int(self.clock()))).get(key) or {}
                if retry.get("retry_at"):
                    # Wake for the retry instead of waiting out the regular tick.
                    self._retry_at = min(filter(None, (self._retry_at, retry["retry_at"])))
            await asyncio.to_thread(self._pulse, target, False,
                                    analysis_reason("failed", message) or "Analysis failed")
        finally:
            _phases.pop((target, key), None)
            analysis_lane.finish(run)
            if self._active_runs.get(job["id"]) is run:
                del self._active_runs[job["id"]]


_active_queue: AnalyzeQueue | None = None


def _bound_to_running_loop(task: asyncio.Task | None) -> bool:
    """A singleton left behind by a closed event loop can't be reused."""
    return task is None or (not task.done() and task.get_loop() is asyncio.get_running_loop())


def wake_active_queue() -> bool:
    """Run the queue's next pass now (new queries get queued); call from the event loop."""
    queue = _active_queue
    if queue is None or queue._task is None or not _bound_to_running_loop(queue._task):
        return False
    queue.wake()
    return True


async def start_analyze_queue() -> AnalyzeQueue:
    global _active_queue
    if _active_queue is None or not _bound_to_running_loop(_active_queue._task):
        _active_queue = AnalyzeQueue()
    return await _active_queue.start()

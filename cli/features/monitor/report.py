"""Daily and on-demand database reports for monitored targets."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import logging
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict

from features.monitor import action_explain, actions, database_limit, report_prompt, snapshot
from features.monitor.analyze_queue import daily_cap, enqueue_for_report, started_today
from features.monitor.run_guard import ThreadWork
from features.monitor.naming import target_names
from shared import product_telemetry
from shared.agent_activity.recorder import recorded_snapshot
from shared.llm_manager.inference_attribution import (
    count_billable_calls, inference_workflow, monitoring_database,
)
from shared.monitor.monitor_store import EVERYTHING_ELSE, RETIRED_TYPES, MonitorStore
from shared.monitor.statements import statement_kind

logger = logging.getLogger(__name__)
USER_WAIT_SECONDS = 60
# A nightly run waits this long for its analyses, then finishes with plans only for the rest
# (they keep running in the background).
NIGHTLY_WAIT_SECONDS = 20 * 60
NARRATIVE_RETRY_LIMIT = 4
NARRATIVE_RETRY_WINDOW_SECONDS = 86400
MIN_NIGHTLY_OBSERVED_SPAN_SECONDS = 60
_thread_work: dict[str, ThreadWork] = {}


def _work(target: str, *, restart: bool = False) -> ThreadWork:
    work = _thread_work.get(target)
    if work is None or (restart and work.guard.stopped.is_set()):
        work = _thread_work[target] = ThreadWork()
    return work


async def _to_thread(target: str, function: Callable[..., Any], *args: Any) -> Any:
    return await _work(target).run(target, function, *args)


async def stop_report_threads() -> None:
    work = list(_thread_work.items())
    await asyncio.gather(*(entry.stop() for _, entry in work))
    for target, entry in work:
        if _thread_work.get(target) is entry:
            del _thread_work[target]


class FixFirst(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_key: str
    title: str | None = None
    why: str
    action: str


class SharedFix(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str
    columns: list[str]
    query_keys: list[str]
    action: str


class HealthFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check: str
    status: str
    note: str
    action: str | None = None


class BriefItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str | None = None
    text: str
    why: str | None = None


class DatabasePick(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str
    why: str | None = None


class QueryNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_key: str
    title: str
    why: str
    action: str = ""


class ActionPick(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str
    why: str | None = None


class ActionOverlap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_ids: list[str]
    note: str


class ReportNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    fix_first: list[FixFirst]
    one_fix_several_queries: list[SharedFix]
    health: list[HealthFinding]
    changed_since_yesterday: list[str]
    # The analysis prompt's checked answer (REPORT_PROMPT.md); absent on older reports.
    brief_items: list[BriefItem] | None = None
    database_recommendations: list[DatabasePick] | None = None
    query_notes: list[QueryNote] | None = None
    # The checkup's order and wording for the open actions; absent before checkups.
    action_picks: list[ActionPick] | None = None
    index_plan_explanation: str | None = None
    overlaps: list[ActionOverlap] | None = None


def validate_narrative(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        raw = json.loads(text)
    if isinstance(raw, dict) and isinstance(raw.get("fix_first"), list):
        # An ungrounded model recommendation cannot be attached to a query.
        # Preserve the valid narrative while discarding only that finding.
        raw = {**raw, "fix_first": [finding for finding in raw["fix_first"]
                                     if not isinstance(finding, dict) or
                                     finding.get("query_key") is not None]}
    return ReportNarrative.model_validate(raw).model_dump(exclude_none=True)


def _delta(current: float, prior: float) -> dict[str, float | None]:
    return {"current": current, "prior": prior, "absolute": current - prior,
            "ratio": current / prior - 1 if prior else None}


def _database_window(store: MonitorStore, target: str, start: int, end: int) -> dict[str, float]:
    rows = store.read(
        "SELECT s.* FROM db_series s WHERE s.target=? AND s.bucket_start>=? "
        "AND s.bucket_start<? AND (s.res=60 OR s.bucket_start+s.res<=?) "
        "AND NOT EXISTS (SELECT 1 FROM db_series p WHERE p.target=s.target "
        "AND p.res>s.res AND p.bucket_start<=s.bucket_start "
        "AND p.bucket_start+p.res>=s.bucket_start+s.res "
        "AND p.bucket_start>=? AND p.bucket_start+p.res<=?)",
        (target, start, end, end, start, end))
    if not rows:
        return {}
    totals = {field: float(sum(row[field] or 0 for row in rows))
              for field in ("calls", "total_ms", "xact_commit", "xact_rollback", "deadlocks")}
    for field in ("cache_hit_ratio", "active_conns", "max_conns"):
        measured = [(float(row[field]), row["res"]) for row in rows if row[field] is not None]
        if measured:
            totals[field] = sum(value * weight for value, weight in measured) / sum(
                weight for _, weight in measured)
    hits = sum(row["blks_hit"] or 0 for row in rows)
    reads = sum(row["blks_read"] or 0 for row in rows)
    if hits + reads:
        totals["cache_hit_ratio"] = hits / (hits + reads)
    return totals


def rolling_deltas(store: MonitorStore, target: str, now: int) -> dict[str, Any]:
    from features.monitor.delivery import query_totals

    result: dict[str, Any] = {}
    for label, seconds in (("24h", 86400), ("7d", 7 * 86400)):
        current = query_totals(store, target, now - seconds, now + 1)
        prior = query_totals(store, target, now - 2 * seconds, now - seconds)
        keys = current.keys() | prior.keys()
        queries = {}
        for key in sorted(keys):
            a, b = current.get(key, {}), prior.get(key, {})
            calls, old_calls = int(a.get("calls") or 0), int(b.get("calls") or 0)
            total, old_total = float(a.get("total_ms") or 0), float(b.get("total_ms") or 0)
            queries[key] = {"calls": _delta(calls, old_calls),
                            "total_ms": _delta(total, old_total),
                            "mean_ms": _delta(total / calls if calls else 0,
                                              old_total / old_calls if old_calls else 0)}
        a_calls = sum(int(row.get("calls") or 0) for row in current.values())
        b_calls = sum(int(row.get("calls") or 0) for row in prior.values())
        a_total = sum(float(row.get("total_ms") or 0) for row in current.values())
        b_total = sum(float(row.get("total_ms") or 0) for row in prior.values())
        db_current = _database_window(store, target, now - seconds, now + 1)
        db_prior = _database_window(store, target, now - 2 * seconds, now - seconds)
        if not db_current:
            db_current = {"calls": a_calls, "total_ms": a_total}
        if not db_prior:
            db_prior = {"calls": b_calls, "total_ms": b_total}
        database = {field: _delta(db_current.get(field, 0), db_prior.get(field, 0))
                    for field in db_current.keys() | db_prior.keys()}
        current_calls, prior_calls = db_current["calls"], db_prior["calls"]
        database["mean_ms"] = _delta(db_current["total_ms"] / current_calls if current_calls else 0,
                                     db_prior["total_ms"] / prior_calls if prior_calls else 0)
        result[label] = {"queries": queries, "database": database}
    return result


def _index_facts(store: MonitorStore, target: str) -> list[dict[str, Any]]:
    """Index changes as plain facts, so advice never asks for an index that already exists."""
    changes = store.read('SELECT ts,kind,schema,"table",index_name,definition FROM index_change '
                         'WHERE target=? ORDER BY ts DESC', (target,))
    active = {(row["schema"], row["table"], row["index_name"], row["definition"])
              for row in store.read('SELECT schema,"table",index_name,definition FROM index_snapshot '
                                    'WHERE target=?', (target,))}
    return [{"fact": "index_change", **row,
             "active": (row["schema"], row["table"], row["index_name"], row["definition"]) in active}
            for row in changes]


DIGEST_WINDOW_S = 86400


def digest(store: MonitorStore, target: str, now: int) -> list[dict[str, Any]]:
    """Findings from the last 24 h that stayed out of the inbox, and pushes over budget.

    Each entry is one line for the report: what happened, to which query, and why it
    waited for the report (digest level, or the daily push limit).
    """
    from features.monitor.api.views import inbox_items

    since = now - DIGEST_WINDOW_S
    rows = store.read(
        "SELECT * FROM inbox_item WHERE target=? AND updated_at>=? AND (delivery='digest' "
        "OR id IN (SELECT item_id FROM inbox_event WHERE ts>=? "
        "AND json_extract(detail_json,'$.push_skipped')='daily_limit')) "
        "ORDER BY updated_at DESC,id DESC LIMIT 50", (target, since, since))
    # Items retired by the schema 15 migration are history, not news.
    rows = [row for row in rows if row["type"] not in RETIRED_TYPES and not (
        row["resolved_by"] == "code" and row["created_at"] < since and
        json.loads(row["body_json"]).get("resolution") is None)]
    return [{"id": item["id"], "type": item["type"], "title": item["title"],
             "summary": item["summary"], "query_keys": item["query_keys"],
             "subject_label": item["subject_label"], "ts": item["updated_at"],
             "reason": "digest" if item["delivery"] == "digest" else "push_limit"}
            for item in inbox_items(store, rows)]


def _analysis_for(job: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    if not job or job["state"] != "done" or not job.get("analysis_ref"):
        return None
    from shared.query_registry.analysis_results import AnalysisResultsRegistry

    analysis_id = job["analysis_ref"].rstrip("/").split("/")[-1]
    query_hash = job["analysis_ref"].split("/analysis/")[0].rstrip("/").split("/")[-1]
    analysis = AnalysisResultsRegistry().get_analysis_by_id(query_hash, analysis_id)
    if analysis is None or analysis.target != job["target"]:
        return None
    return {"query_key": key, "analysis_ref": job["analysis_ref"], "mode": job["mode"],
            "explain_plan": analysis.explain_plan, "llm_analysis": analysis.llm_analysis,
            "index_suggestions": analysis.index_suggestions,
            "rewrite_suggestions": analysis.rewrite_suggestions,
            "caching_recommendations": analysis.caching_recommendations}


def plain_explain(target: str, sql: str) -> dict[str, Any]:
    """Obtain a plan without executing user SQL; failures remain provisional."""
    from shared.config.targets import TargetsConfig
    from shared.db_connection import create_direct_connection, rdst_self_marker
    from shared.query_safety import is_plain_explainable_write, validate_query_safety

    if not validate_query_safety(sql).get("safe") and not is_plain_explainable_write(sql):
        return {"error": "SQL is not safe to EXPLAIN"}
    config = TargetsConfig()
    config.load()
    target_config = config.get(target) or {}
    connection = create_direct_connection(
        target_config, lane="rdst/analyze", query_timeout_seconds=5)
    work = _work(target)
    try:
        work.register_connection(connection, target_config.get("engine", "postgresql"), target_config)
        cursor = connection.cursor()
        try:
            work.guard.check()
            mysql = target_config.get("engine") == "mysql"
            # Placeholders left without a real value get PostgreSQL 16+'s generic plan.
            generic = (not mysql and re.search(r"\$\d+", sql)
                       and getattr(connection, "server_version", 0) >= 160000)
            # MySQL's tabular EXPLAIN puts the id first; its tree format is the plan text.
            cursor.execute(rdst_self_marker("rdst/analyze") +
                           ("EXPLAIN FORMAT=TREE " if mysql else
                            "EXPLAIN (GENERIC_PLAN) " if generic else "EXPLAIN ") + sql)
            rows = [next(iter(row.values())) if isinstance(row, dict) else row[0]
                    for row in cursor.fetchall()]
            return {"plan": [line for row in rows for line in str(row).splitlines()]
                    if mysql else [str(row) for row in rows]}
        finally:
            cursor.close()
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        work.unregister_connection(connection)
        connection.rollback()
        connection.close()


async def _fallback_explain(target: str, key: str, sql: str,
                            explain: Callable[[str, str], dict[str, Any]]) -> dict[str, Any]:
    if explain is not plain_explain:
        try:
            return await _to_thread(target, explain, target, sql)
        except Exception as exc:
            return {"error": str(exc)}
    from features.analyze.service import AnalyzeService
    from features.monitor.analyze_queue import apply_suggestions
    from shared.config.targets import TargetsConfig

    config = TargetsConfig()
    config.load()
    suggestion_error = None
    try:
        suggestions = await AnalyzeService().suggest_parameter_values(
            sql, target, config.get(target), key)
        sql, source = apply_suggestions(sql, suggestions)
    except Exception as exc:
        source = "none"
        suggestion_error = str(exc)
    try:
        result = {**await _to_thread(target, explain, target, sql), "param_source": source}
    except Exception as exc:
        result = {"error": str(exc), "param_source": source}
    if suggestion_error:
        result["suggestion_error"] = suggestion_error
    return result


def _target_config(target: str) -> dict[str, Any]:
    from shared.config.targets import TargetsConfig

    config = TargetsConfig()
    config.load()
    return config.get(target) or {}


def collect_health(target: str, target_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """The database checks (vacuum, indexes, connections, settings, replication, full
    scans): SQL against the database, no model."""
    from features.audit.health import collect_health_report

    target_config = target_config if target_config is not None else _target_config(target)
    engine = target_config.get("engine", "postgresql")
    try:
        return asdict(collect_health_report(target_config, engine, target=target))
    except Exception as exc:
        return {"collection_error": str(exc), "engine": engine}


def _collect_context(target: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from features.schema.schema_collector import collect_all_tables_schema

    target_config = _target_config(target)
    health = collect_health(target, target_config)
    try:
        schema = collect_all_tables_schema(target_config, target)
    except Exception as exc:
        schema = {"error": str(exc)}
    return health, schema


def _checkup_actions(store: MonitorStore, target: str, now: int, day: dict[str, Any]) -> None:
    """Refresh the actions with this run's database actions; their whys are written apart."""
    actions.refresh(store, target, now, database=day.get("actions") or [])


def _narrate(context: dict[str, Any]) -> dict[str, Any]:
    """One model call over the day's computed facts (REPORT_PROMPT.md).

    The user's own Anthropic key pays when one is set; otherwise the hosted service does.
    """
    from features.monitor import report_prompt
    from shared.llm_manager import LLMManager

    data = context.get("model_input") or report_prompt.minimal_input(context)
    # Reasoning shares the output budget; the hosted limit leaves room for the whole JSON.
    result = LLMManager().generate_response(
        report_prompt.prompt(data), purpose="monitor_report",
        system_message=report_prompt.SYSTEM_PROMPT, max_tokens=8192, temperature=0.0)
    raw = result["response"]
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        raw = json.loads(text)
    if isinstance(raw, dict) and "brief" in raw:
        raw = report_prompt.parse_answer(raw, data)
    return validate_narrative(raw)


def health_findings(day: dict[str, Any]) -> list[dict[str, Any]]:
    """The health list older clients read, from the day's checks and actions."""
    steps: dict[str, str] = {}
    for action in day.get("actions") or []:
        steps.setdefault(action["check"], action["steps"])
    return [{"check": check["label"],
             "status": "info" if check["status"] == "unknown" else check["status"],
             "note": check["value"],
             **({"action": steps[check["check"]]} if check["status"] in ("warn", "bad")
                and check["check"] in steps else {})}
            for check in (day.get("overview") or {}).get("checks") or []]


def _computed_narrative(context: dict[str, Any]) -> dict[str, Any]:
    top = context["top_queries"]
    health = context["health_collectors"]
    checks = []
    for name in ("vacuum_bloat", "index_health", "connections", "config_audit", "replication"):
        section = health.get(name) or {}
        if isinstance(section, dict):
            checks.append({"check": name.replace("_", " ").title(),
                           "status": str(section.get("summary_status") or
                                         ("unavailable" if name in health.get("section_errors", {}) else "collected")),
                           "note": str(health.get("section_errors", {}).get(name) or "Measured by health collector")})
    if health.get("collection_error"):
        checks.append({"check": "Health collection", "status": "unavailable",
                       "note": str(health["collection_error"])})
    change = context["deltas"]["24h"]["database"]["total_ms"]
    return ReportNarrative(
        summary="The measured report is ready. The written summary retries shortly.",
        fix_first=[FixFirst(query_key=row["query_key"], title="Review the deep analysis",
                            why=f"{row['db_time_share'] * 100:.1f}% of database time in the last 24 hours",
                            action="Review deep analysis") for row in top[:5]],
        one_fix_several_queries=[], health=[HealthFinding(**check) for check in checks],
        changed_since_yesterday=[f"Database time: {change['current']:.0f} ms in the last 24 hours, "
                                 f"{change['prior']:.0f} ms in the prior 24 hours."]
    ).model_dump(exclude_none=True)


def _bound_narrative(narrative: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Keep model priorities tied to measured queries, and skip indexes that already exist."""
    from features.monitor.indexes import index_columns

    from features.monitor.presentation import lead_with_finding

    if "summary" in narrative:
        narrative["summary"] = lead_with_finding(narrative["summary"])
    keys = {row["query_key"] for row in context["top_queries"]}
    narrative["fix_first"] = [finding for finding in narrative["fix_first"]
                              if finding["query_key"] in keys]
    added = [(str(row["table"]).lower(), index_columns(row["definition"]))
             for row in context["fix_applied"] if row["fact"] == "index_change"
             and row["kind"] == "added" and row["active"]]
    shared = []
    for finding in narrative["one_fix_several_queries"]:
        finding["query_keys"] = sorted(set(finding["query_keys"]) & keys)
        columns = [column.strip().lower() for column in finding["columns"]]
        if len(finding["query_keys"]) < 2 or not columns:
            continue
        if "index" in finding["action"].lower() and any(
            table == finding["table"].strip().lower() and existing[:len(columns)] == columns
            for table, existing in added
        ):
            continue
        shared.append(finding)
    narrative["one_fix_several_queries"] = shared
    return narrative


def _scan_health(store: MonitorStore, target: str, ts: int, health: dict[str, Any]) -> None:
    from features.audit.health.sequential_scans import scan_deltas
    from features.monitor.presentation import display_name

    errors = health.get("section_errors") or {}
    if health.get("collection_error") or "sequential_scans" in errors or not isinstance(
            health.get("sequential_scans"), dict):
        return
    previous = store.previous_health_collectors(target, ts)
    prior = previous.get("sequential_scans")
    if previous.get("collection_error") or "sequential_scans" in (previous.get("section_errors") or {}):
        prior = None
    scans = scan_deltas(health["sequential_scans"], prior)
    scans["queries"] = [{**query, "display_name": query.get("display_name") or
                        display_name(query.get("normalized_sql") or "")}
                       for query in store.monitored_table_queries(target)]
    health["sequential_scans"] = scans


def _local_day(ts: int) -> str:
    return datetime.fromtimestamp(ts).date().isoformat()


HEADLINE_QUIET_S = 7 * 86400


def _file_report_items(store: MonitorStore, target: str, run_id: int, ts: int,
                       narrative: dict[str, Any], trigger: str) -> None:
    """File measured health findings and one report notification for new bad findings."""
    if trigger != "nightly":
        return
    from features.monitor.db_actions import SECTION_OF

    row = store.report(run_id)
    report = json.loads(row["report_json"] or "{}") if row else {}
    collectors = (report.get("computed") or {}).get("health_collectors") or {}
    actions = (report.get("snapshot") or {}).get("actions") or []
    errors = collectors.get("section_errors") or {}
    valid = not collectors.get("collection_error") and any(
        isinstance(collectors.get(section), dict) and section not in errors
        for section in SECTION_OF.values())

    def checked(check: str, subject: str) -> bool:
        section = SECTION_OF.get(check)
        if not valid:
            return False
        if section and (section in errors or not isinstance(collectors.get(section), dict)):
            return False
        if check == "sequential_scans":
            scans = collectors[section]
            table = subject.removeprefix("sequential_scans_")
            return scans.get("compared", False) and table not in scans.get("unchecked_tables", [])
        return check in SECTION_OF or check in ("cache", "transactions")

    # Findings live on the Actions tab; the Inbox keeps only the checkup's own notice. Items
    # filed before Actions existed close here.
    for item in store.inbox(target=target):
        if item["type"] == "health_finding" and item["state"] not in ("resolved", "dismissed"):
            store.action_item(item["id"], "resolve", ts, resolved_by="code")
    # Headline a bad finding only the night its action first opens, so it never nags.
    opened = {row["key"] for row in store.actions(target)
              if row["opened_at"] >= int(row_ts(store, run_id) or ts)}
    headlines = [action for action in actions
                 if action["severity"] == "bad" and checked(action["check"], action["id"])
                 and f"db:{action['id']}" in opened]
    body = {"report_id": run_id, "summary": narrative["summary"]}
    title = "Checkup ready"
    if headlines:
        title = headlines[0]["title"] if len(headlines) == 1 else f"{len(headlines)} things to fix on {target}"
        body.update(headline_title=title, headline_summary=headlines[0]["finding"] +
                    (f" Plus {len(headlines) - 1} more in today's report." if len(headlines) > 1 else ""))
    existing = next((item for item in store.inbox(target=target)
                     if item["type"] == "daily_summary" and item["subject"] == _local_day(ts)), None)
    if existing:
        prior = json.loads(existing["body_json"])
        if prior.get("report_id") == run_id and prior.get("headline_title"):
            body.update(headline_title=prior["headline_title"], headline_summary=prior["headline_summary"])
    item = store.upsert_item(target, "daily_summary", _local_day(ts), .4, title, body, ts,
                             delivery="notify" if headlines else "inbox")
    close_older_reports(store, target, item["id"], ts)


def row_ts(store: MonitorStore, run_id: int) -> int | None:
    row = store.report(run_id)
    return int(row["ts"]) if row else None


def close_older_reports(store: MonitorStore, target: str, keep_id: int, ts: int) -> None:
    """Only the latest daily report stays open; earlier ones go to Done."""
    for row in store.read("SELECT id FROM inbox_item WHERE target=? AND type='daily_summary' "
                          "AND id!=? AND state NOT IN ('resolved','dismissed')",
                          (target, keep_id)):
        store.action_item(row["id"], "resolve", ts, resolved_by="code")


HEALTH_CHECKS = (("vacuum_bloat", "Vacuum and bloat"), ("index_health", "Index health"),
                 ("connections", "Connections"), ("config_audit", "Configuration"),
                 ("replication", "Replication"), ("sequential_scans", "Full table scans"))
SECTIONS = ("summary", "fix_first", "one_fix_several_queries", "health", "changed")
_running: dict[int, asyncio.Task] = {}
_cancel_requested: set[int] = set()


class _Events:
    """Append-only progress events for one run; a failed write never fails the run."""

    def __init__(self, store: MonitorStore, run_id: int, clock: Callable[[], float]):
        self.store, self.run_id, self.clock = store, run_id, clock

    def __call__(self, event: dict[str, Any]) -> None:
        try:
            self.store.append_report_event(self.run_id, event, int(self.clock()))
        except Exception:
            logger.debug("Report event not recorded for %s", self.run_id, exc_info=True)

    def step(self, step: str, state: str, detail: str, *, done: int | None = None,
             total: int | None = None) -> None:
        event: dict[str, Any] = {"type": "step", "step": step, "state": state, "detail": detail}
        if total is not None:
            event.update(done=done or 0, total=total)
        self(event)


def _job_event(key: str, job: dict[str, Any] | None, *, cap_reached: bool,
               name: str | None = None) -> dict[str, Any]:
    from features.monitor.analyze_queue import current_phase
    from features.monitor.presentation import analysis_error_kind, analysis_reason

    state = job["state"] if job else "queued"
    duration = (job["finished_at"] - job["started_at"]
                if job and job.get("finished_at") and job.get("started_at") else None)
    result_line = None
    if state == "done":
        result_line = "Plan measured with EXPLAIN ANALYZE" if job.get("mode") == "analyze" \
            else "Plan read with EXPLAIN"
    error = (job or {}).get("error")
    event = {"type": "analysis", "key": key, "state": state,
             "reason": analysis_reason(state, error, cap_reached=cap_reached),
             "result_line": result_line, "duration_s": duration,
             "phase": (current_phase(job["target"], key) or "plan"
                       if job and state == "running" else None),
             "error_kind": analysis_error_kind(state, error, cap_reached=cap_reached)}
    if name is not None:
        event["display_name"] = name
    return event


def _headline(job: dict[str, Any] | None) -> str | None:
    from features.monitor.analysis_digest import job_digest

    try:
        digest = job_digest(job)
    except Exception:
        logger.debug("Analysis headline unavailable", exc_info=True)
        return None
    return digest["headline"] if digest else None


def request_cancel(store: MonitorStore, report_id: int, now: int) -> dict[str, Any] | None:
    """Stop a running report. The run's own task records the cancellation when it is here."""
    task = _running.get(report_id)
    if task is not None and not task.done():
        _cancel_requested.add(report_id)
        # Routes may call this from a worker thread; the task belongs to the server loop.
        task.get_loop().call_soon_threadsafe(task.cancel)
    row = store.cancel_report(report_id, now)
    if row is not None and task is None and row["state"] == "cancelled":
        events = store.report_events(report_id)
        if not events or events[-1]["event"].get("type") != "run" or \
                events[-1]["event"].get("state") == "running":
            store.append_report_event(report_id, {
                "type": "run", "state": "cancelled", "started_at": row["ts"],
                "elapsed_s": max(0, now - row["ts"])}, now)
    return row


@recorded_snapshot
async def run_report(target: str, trigger: Literal["nightly", "user"], *,
                     store: MonitorStore | None = None, clock: Callable[[], float] = time.time,
                     sleep: Callable[[float], Any] = asyncio.sleep,
                     monotonic: Callable[[], float] | None = None,
                     wake_queue: Callable[[], None] | None = None,
                     explain: Callable[[str, str], dict[str, Any]] = plain_explain,
                     collect: Callable[[str], tuple[dict[str, Any], dict[str, Any]]] = _collect_context,
                     narrate: Callable[[dict[str, Any]], dict[str, Any]] = _narrate,
                     claimed_run: dict[str, Any] | None = None) -> dict[str, Any]:
    from features.monitor.presentation import display_name

    _work(target, restart=True)
    monotonic = monotonic or (time.monotonic if clock is time.time else clock)
    store = store or MonitorStore()
    now = int(clock())
    if trigger == "nightly" and not await asyncio.to_thread(database_limit._signed_in):
        database_limit.hold_signed_out(store, target, now)
        if claimed_run is not None:
            await asyncio.to_thread(store.update_report, claimed_run["id"], state="cancelled",
                                    finished_at=now, progress={"error": "signed out"})
        return {"state": "queued", "error": "Sign in to turn on AI monitoring"}
    run, claimed = (claimed_run, True) if claimed_run is not None else await asyncio.to_thread(
        store.claim_report, target, trigger, now, snapshot.report_period(trigger, now)[0])
    if not claimed:
        return _present(run)
    run_id = run["id"]
    started_at = run["ts"]
    period_start, period_end, partial = snapshot.report_period(trigger, now)
    await asyncio.to_thread(store.update_report, run_id, period=(period_start, period_end))
    emit = _Events(store, run_id, clock)
    task = asyncio.current_task()
    if task is not None:
        _running[run_id] = task
    await asyncio.to_thread(emit, {"type": "run", "state": "running", "started_at": started_at, "elapsed_s": 0})
    step = "select"
    try:
        from features.monitor.delivery import query_totals
        from features.monitor.api.views import workload_keys

        step = "history"
        await asyncio.to_thread(emit.step, "history", "running", "Reading the day's history")

        def read_history() -> tuple[dict, dict, int, float]:
            found = query_totals(store, target, period_start, period_end)
            known = {row["query_key"]: row["text"] for row in store.read(
                "SELECT query_key,text FROM query_text WHERE target=?", (target,))}
            user = workload_keys(store, target, found, known)
            count = store.read("SELECT COUNT(*) n FROM db_series WHERE target=? AND res=60 "
                               "AND bucket_start>=? AND bucket_start<?",
                               (target, period_start, period_end))[0]["n"]
            total_ms = (sum(found[key]["total_ms"] for key in user)
                        + found.get(EVERYTHING_ELSE, {}).get("total_ms", 0.0))
            return {key: row for key, row in found.items() if key in user}, known, count, total_ms

        # Store work runs in the target's worker thread, never on the event loop.
        totals, texts, readings, total_db_ms = await _to_thread(target, read_history)
        await asyncio.to_thread(emit.step, "history", "done", f"{len(totals)} {'query' if len(totals) == 1 else 'queries'}, "
                  f"{readings:,} {'reading' if readings == 1 else 'readings'}",
                  done=readings, total=readings)
        step = "select"
        await asyncio.to_thread(emit.step, "select", "running", "Picking the queries with the most database time")
        keys = sorted(totals, key=lambda key: (-totals[key]["total_ms"], key))[:15]
        names = await _to_thread(target, target_names, store, target, texts)
        names.update({key: f"Query {key}" for key in keys if key not in names})
        await asyncio.to_thread(store.update_report, run_id, top=keys)
        await asyncio.to_thread(emit, {"type": "selected", "queries": [
            {"key": key, "display_name": names[key],
             "db_time_share": totals[key]["total_ms"] / total_db_ms if total_db_ms else 0.0}
            for key in keys]})
        await asyncio.to_thread(emit.step, "select", "done", f"Picked the {len(keys)} "
                  f"{'query' if len(keys) == 1 else 'queries'} with the most database time",
                  done=len(keys), total=len(keys))
        step = "analyses"
        await asyncio.to_thread(enqueue_for_report, target, keys, store=store, now=now)
        if wake_queue:
            wake_queue()
        await asyncio.to_thread(emit.step, "analyses", "running", f"0 of {len(keys)}", done=0, total=len(keys))
        deadline = monotonic() + (USER_WAIT_SECONDS if trigger == "user" else NIGHTLY_WAIT_SECONDS)
        latest: dict[str, dict[str, Any]] = {}
        shown: dict[str, tuple] = {}
        progress_done = -1
        def poll() -> tuple[dict, set, set, bool]:
            jobs = store.latest_analysis_jobs(target)
            done = {key for key in keys if jobs.get(key, {}).get("state") == "done"
                    and (jobs[key].get("finished_at") or 0) > now - 86400}
            ended = {key for key in keys if jobs.get(key, {}).get("state") not in
                     {"queued", "running"} and key not in done}
            waiting = {key for key in keys if jobs.get(key, {}).get("state") == "queued"}
            limited = bool(waiting) and started_today(store, target, int(clock())) >= \
                daily_cap(target, store)
            if limited:
                ended.update(waiting)
            return jobs, done, ended, limited

        while True:
            latest, ready, terminal, capped = await _to_thread(target, poll)
            for key in keys:
                job = latest.get(key)
                if job and job["state"] == "done" and key not in ready:
                    job = {**job, "state": "queued"}
                event = _job_event(key, job, cap_reached=capped, name=names[key])
                if capped and event["state"] == "queued":
                    event["state"] = "skipped"
                signature = (event["state"], event["reason"], event["result_line"],
                             event["phase"])
                if shown.get(key) != signature:
                    shown[key] = signature
                    if event["state"] == "done":
                        event["headline"] = await _to_thread(target, _headline, job)
                    await asyncio.to_thread(emit, event)
            finished = len(ready | terminal)
            if finished != progress_done:
                progress_done = finished
                await asyncio.to_thread(emit.step, "analyses", "running", f"{finished} of {len(keys)}", done=finished,
                          total=len(keys))
            await asyncio.to_thread(store.update_report, run_id, progress={
                "message": f"Analyzing top queries, {len(ready)} of {len(keys)}",
                "done": len(ready), "total": len(keys)})
            if len(ready | terminal) == len(keys) or monotonic() >= deadline:
                break
            await sleep(min(1.0, max(0.0, deadline - monotonic())))
        pending = [key for key in keys if key not in ready and key not in terminal]
        if pending:
            await asyncio.to_thread(emit, {"type": "budget", "message": (
                f"Finishing with plans only for {len(pending)} "
                f"{'query' if len(pending) == 1 else 'queries'} - full analyses continue "
                "in the background")})
        analyses = []
        provisional_keys = []
        for key in keys:
            job = latest.get(key)
            try:
                detail = (await _to_thread(target, _analysis_for, job, key)
                          if job and (job.get("finished_at") or 0) > now - 86400 else None)
            except Exception:
                logger.warning("Stored analysis unavailable for %s", key, exc_info=True)
                detail = None
            if detail is None:
                provisional_keys.append(key)
                detail = {"query_key": key, "provisional": True,
                          "explain": await _fallback_explain(target, key,
                                                              texts.get(key, ""), explain)}
                if key in pending:
                    await asyncio.to_thread(emit, {"type": "analysis", "key": key, "state": "provisional",
                          "display_name": names[key],
                          "reason": "Full analysis continues in the background.",
                          "result_line": ("Plan read with EXPLAIN"
                                          if "plan" in detail["explain"] else None),
                          "duration_s": None, "phase": None, "error_kind": None})
            analyses.append(detail)
        await asyncio.to_thread(emit.step, "analyses", "done", f"{len(ready)} of {len(keys)} analyzed", done=len(ready),
                  total=len(keys))
        step = "health"
        await asyncio.to_thread(emit.step, "health", "running", f"0 of {len(HEALTH_CHECKS)}", done=0,
                  total=len(HEALTH_CHECKS))
        for check, label in HEALTH_CHECKS:
            await asyncio.to_thread(emit, {"type": "health_check", "check": check, "label": label, "state": "running"})
        health, schema = await _to_thread(target, collect, target)
        if trigger == "nightly":
            await asyncio.to_thread(_scan_health, store, target, now, health)
        errors = health.get("section_errors") or {}
        failed_all = bool(health.get("collection_error"))
        for check, label in HEALTH_CHECKS:
            await asyncio.to_thread(emit, {"type": "health_check", "check": check, "label": label,
                  "state": "failed" if failed_all or check in errors else "done"})
        healthy = sum(1 for check, _ in HEALTH_CHECKS if not failed_all and check not in errors)
        await asyncio.to_thread(emit.step, "health", "failed" if failed_all else "done",
                  f"{healthy} of {len(HEALTH_CHECKS)}", done=healthy, total=len(HEALTH_CHECKS))
        step = "narrative"
        deltas = await _to_thread(target, rolling_deltas, store, target, now)
        day = await _to_thread(target, lambda: snapshot.build(
            store, target, period_start, period_end, now, partial=partial,
            run_analyses=analyses, provisional=provisional_keys, collectors=health))
        inbox = [{"type": item["type"], "subject": item["subject"],
                  "title": item["title"], "body": json.loads(item["body_json"])}
                 for item in store.inbox(target=target) if item["state"] in {"new", "seen", "snoozed"}]
        index_facts = await _to_thread(target, _index_facts, store, target)
        await _to_thread(target, _checkup_actions, store, target, now, day)
        context = {"target": target, "top_queries": [{"query_key": key, "display_name": names[key],
                   "text": texts.get(key, ""), "total_ms": totals[key]["total_ms"],
                   "db_time_share": totals[key]["total_ms"] / total_db_ms if total_db_ms else 0}
                   for key in keys],
                   "analyses": analyses, "deltas": deltas, "health_collectors": health,
                   "schema": schema, "open_inbox": inbox,
                   "fix_applied": index_facts,
                   "day_facts": day["facts"],
                   "model_input": report_prompt.model_input(day, target, index_facts)}
        await asyncio.to_thread(store.update_report, run_id, progress={"message": "Writing report", "done": len(keys),
                                              "total": len(keys)})
        await asyncio.to_thread(emit.step, "narrative", "running", "Writing the report")
        for section in SECTIONS:
            await asyncio.to_thread(emit, {"type": "section", "section": section, "state": "writing"})
        fingerprint = await asyncio.to_thread(database_limit.target_fingerprint, target)
        with count_billable_calls() as calls, monitoring_database(fingerprint), inference_workflow(
            "monitor" if trigger == "nightly" else "analyze", "web",
            operation="monitor_report" if trigger == "nightly" else None,
        ):
            try:
                if trigger == "nightly" and database_limit.paused(store, target, int(clock())):
                    # Local-only, a used-up allowance or a recent refusal: no model call.
                    raise RuntimeError("AI_PAUSED: AI monitoring is paused for this database")
                narrative = _bound_narrative(
                    validate_narrative(await _to_thread(target, narrate, context)), context)
                narrative_error = None
            except Exception as exc:
                logger.warning("Report narrative unavailable for %s: %s", target, exc)
                narrative = _computed_narrative(context)
                narrative_error = str(exc)
                if database_limit.is_limit_error(exc):
                    await asyncio.to_thread(database_limit.file_refusal, store, target,
                                            int(clock()))
                elif allowance := database_limit.is_allowance_error(exc):
                    await asyncio.to_thread(database_limit.file_allowance, store, target,
                                            allowance, int(clock()))
        if narrative_error is None:
            await asyncio.to_thread(database_limit.clear_allowance, store, "hosted", int(clock()))
        if not narrative.get("health"):
            narrative["health"] = health_findings(day)
        if isinstance(health.get("sequential_scans"), dict):
            narrative["health"] = [check for check in narrative["health"]
                                   if check["check"].lower() != "full table scans"] + [
                check for check in health_findings(day) if check["check"] == "Full table scans"]
        from features.monitor.report_v2 import REPORT_VERSION

        report = {**narrative, "version": REPORT_VERSION, "computed": context,
                  "snapshot": day,
                  "provisional_query_keys": provisional_keys,
                  "digest": await _to_thread(target, digest, store, target, now),
                  "narrative_error": narrative_error,
                  "narrative_retry_at": now + 900 if narrative_error else None,
                  "narrative_retry_count": 0}
        finished_at = int(clock())
        await asyncio.to_thread(store.update_report, run_id, report=report, provisional=bool(provisional_keys),
                            state="done", finished_at=finished_at,
                            billable_calls=calls.count,
                            progress={"message": "Report ready", "done": len(keys),
                                      "total": len(keys)})
        # Hosted inference answers in one piece, so the text arrives whole, not streamed.
        brief = snapshot.with_brief(day, None if narrative_error else narrative.get("summary"),
                                    None if narrative_error else narrative)
        day_names = day.get("names") or {}
        summary = re.sub(r"\{\{q:([0-9a-f]{12})\}\}",
                         lambda match: day_names.get(match.group(1)) or names.get(match.group(1))
                         or (display_name(texts[match.group(1)]) if texts.get(match.group(1))
                             else "a query"), " ".join(brief["brief"]))
        await asyncio.to_thread(emit, {"type": "narrative", "delta": summary, "final": True})
        for section in SECTIONS:
            await asyncio.to_thread(emit, {"type": "section", "section": section, "state": "ready"})
        await asyncio.to_thread(emit.step, "narrative", "done", "Report written" if narrative_error is None else
                  "Measured sections ready; the written summary retries shortly")
        await asyncio.to_thread(emit, {"type": "run", "state": "done", "started_at": started_at,
              "elapsed_s": max(0, finished_at - started_at)})
        product_telemetry.report_generated(target, trigger=trigger, ok=True,
                                           duration_s=finished_at - started_at,
                                           narrative_ok=narrative_error is None)
        try:
            await asyncio.to_thread(store.record_pulse, target, finished_at, "report", True,
                                    "Report written")
        except Exception:
            logger.debug("Report pulse not recorded for %s", target, exc_info=True)
        await asyncio.to_thread(_file_report_items, store, target, run_id, now, narrative, trigger)
        return _present(store.report(run_id))
    except asyncio.CancelledError:
        cancelled = run_id in _cancel_requested
        state = "cancelled" if cancelled else "failed"
        await asyncio.to_thread(store.update_report, run_id, state=state, finished_at=int(clock()),
                            progress={"message": "Report cancelled" if cancelled
                                      else "Report interrupted", "done": 0, "total": 0})
        await asyncio.to_thread(emit.step, step, "skipped" if cancelled else "failed",
                  "Cancelled" if cancelled else "Interrupted")
        await asyncio.to_thread(emit, {"type": "run", "state": state, "started_at": started_at,
              "elapsed_s": max(0, int(clock()) - started_at)})
        product_telemetry.report_generated(target, trigger=trigger, ok=False,
                                           duration_s=int(clock()) - started_at,
                                           cancelled=cancelled)
        if cancelled:
            # Cancelling is an ordinary outcome for the caller, not an error.
            return _present(store.report(run_id))
        raise
    except Exception as exc:
        product_telemetry.report_generated(target, trigger=trigger, ok=False,
                                           duration_s=int(clock()) - started_at,
                                           error_class=type(exc).__name__)
        await asyncio.to_thread(store.update_report, run_id, state="failed", finished_at=int(clock()),
                            progress={"message": "Report failed", "done": 0, "total": 0})
        await asyncio.to_thread(emit.step, step, "failed", "Something went wrong")
        await asyncio.to_thread(emit, {"type": "run", "state": "failed", "started_at": started_at,
              "elapsed_s": max(0, int(clock()) - started_at)})
        raise
    finally:
        _running.pop(run_id, None)
        _cancel_requested.discard(run_id)


def _present(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        raise ValueError("Report not found")
    return {**row, "provisional": bool(row["provisional"]),
            "top_json": json.loads(row["top_json"] or "[]"),
            "report_json": json.loads(row["report_json"] or "null"),
            "progress": json.loads(row["progress_json"] or "null")}


def present_report(row: dict[str, Any], store: MonitorStore) -> dict[str, Any]:
    """The contract DatabaseReport: no internal counters or raw collector payloads."""
    from features.monitor.presentation import (
        fix_title, health_label, health_status, health_value, lead_with_finding, mentioned_keys,
        query_refs,
    )

    from features.monitor import report_v2

    report = json.loads(row["report_json"] or "null")
    body = None
    refs: dict[str, dict[str, str]] = {}
    day = None
    if isinstance(report, dict):
        computed = report.get("computed") or {}
        report_texts = {item["query_key"]: item.get("text") or ""
                        for item in computed.get("top_queries") or [] if isinstance(item, dict)}
        stored_day = report.get("snapshot") or {}
        report_texts.update(_texts(store, row["target"], stored_day))
        names = target_names(store, row["target"], report_texts)
        for item in computed.get("top_queries") or []:
            if isinstance(item, dict) and item.get("query_key") in names:
                item["display_name"] = names[item["query_key"]]
        day = day_snapshot(report, row)
        if day is not None:
            day["names"] = {key: names.get(key, name)
                            for key, name in (day.get("names") or {}).items()}
            for item in [*(day.get("queries") or []), *(day.get("look_at") or []),
                         *((day.get("chart") or {}).get("bands") or [])]:
                key = item.get("query_key") or item.get("key")
                if key in names:
                    item["display_name"] = names[key]
    if isinstance(report, dict) and not report.get("computed") and day is not None:
        # A compacted or sample day keeps only its snapshot.
        body = {"summary": "", "fix_first": [], "one_fix_several_queries": [], "health": [],
                "changed_since_yesterday": [], "provisional_query_keys": [],
                "narrative_error": report.get("narrative_error"), "narrative_retry_at": None,
                "digest": [], "version": report_v2.REPORT_VERSION, "verdict": None,
                "since_yesterday": [], "snapshot": day}
        refs = query_refs(set(day.get("names") or {}), _texts(store, row["target"], day),
                          day.get("names") or {})
    elif isinstance(report, dict):
        computed = report.get("computed") or {}
        v2 = report_v2.build({
            **report,
            "fix_first": [{"query_key": item.get("query_key", ""),
                           "title": item.get("title") or fix_title(item.get("action", "")),
                           "why": item.get("why", ""), "action": item.get("action", "")}
                          for item in report.get("fix_first") or [] if isinstance(item, dict)],
            "health": [{"check": str(item.get("check", "")),
                        "label": health_label(str(item.get("check", ""))),
                        "value": health_value(str(item.get("check", "")), computed),
                        "status": health_status(str(item.get("status", ""))),
                        "note": str(item.get("note", "")),
                        "action": str(item.get("action") or "") or None}
                       for item in report.get("health") or [] if isinstance(item, dict)],
        }, row["target"])
        body = {
            "summary": report_v2.measured_summary(
                lead_with_finding(str(report.get("summary") or ""))),
            **v2,
            "changed_since_yesterday": (
                report_v2.since_yesterday_lines(v2["since_yesterday"]) if v2["since_yesterday"]
                else [str(line) for line in report.get("changed_since_yesterday") or []]),
            "provisional_query_keys": list(report.get("provisional_query_keys") or []),
            "narrative_error": report.get("narrative_error"),
            "narrative_retry_at": report.get("narrative_retry_at"),
            "digest": [item for item in report.get("digest") or [] if isinstance(item, dict)],
            "snapshot": day,
        }
        texts = {item["query_key"]: item.get("text", "")
                 for item in computed.get("top_queries") or [] if isinstance(item, dict)}
        known = set(texts) | {row["query_key"] for row in store.read(
            "SELECT query_key FROM query_text WHERE target=?", (row["target"],))}
        keys = mentioned_keys({key: value for key, value in body.items() if key != "snapshot"},
                              known) | set(json.loads(row["top_json"] or "[]"))
        keys |= set((day or {}).get("names") or {})
        missing = keys - set(texts)
        if missing:
            placeholders = ",".join("?" for _ in missing)
            texts.update({item["query_key"]: item["text"] for item in store.read(
                f"SELECT query_key,text FROM query_text WHERE target=? "
                f"AND query_key IN ({placeholders})", (row["target"], *sorted(missing)))})
        refs = query_refs(keys, texts, names)
    period = _period_of(row)
    return {"id": row["id"], "target": row["target"], "ts": row["ts"], "trigger": row["trigger"],
            "state": row["state"], "provisional": bool(row["provisional"]),
            "finished_at": row.get("finished_at"), "period_start": period[0],
            "period_end": period[1], "period_partial": period[2],
            "sample": bool(row.get("sample")),
            "progress": json.loads(row["progress_json"] or "null"),
            "report_json": body, "refs": refs,
            "queries": _analyzed_queries(row, report, store, refs)}


def _period_of(row: dict[str, Any]) -> tuple[int, int, bool]:
    """The day a run covers; runs from before periods covered the 24 h before them."""
    if row.get("period_start") is not None and row.get("period_end") is not None:
        start, end = int(row["period_start"]), int(row["period_end"])
        return start, end, end - start < 86400 - 3600
    return int(row["ts"]) - 86400, int(row["ts"]), False


def _legacy_day(row: dict[str, Any]) -> str:
    """The day an older run mostly covered: a nightly run in the small hours reported on
    the day before."""
    ts = int(row["ts"])
    if row.get("trigger") == "nightly" and datetime.fromtimestamp(ts).hour < 6:
        ts -= 6 * 3600
    return datetime.fromtimestamp(ts).date().isoformat()


def _texts(store: MonitorStore, target: str, day: dict[str, Any]) -> dict[str, str]:
    keys = sorted(day.get("names") or {})
    if not keys:
        return {}
    placeholders = ",".join("?" for _ in keys)
    found = {row["query_key"]: row["text"] for row in store.read(
        f"SELECT query_key,text FROM query_text WHERE target=? AND query_key IN ({placeholders})",
        (target, *keys))}
    for row in day.get("look_at") or []:
        if row.get("text"):
            found.setdefault(row["query_key"], row["text"])
    return found


def day_snapshot(report: dict[str, Any], row: dict[str, Any]) -> dict[str, Any] | None:
    """The stored snapshot with its day in brief, or one rebuilt from an older report."""
    stored = report.get("snapshot")
    if isinstance(stored, dict):
        if report.get("narrative_error"):
            return snapshot.with_brief(stored, None)
        return snapshot.with_brief(stored, report.get("summary"), report)
    try:
        return snapshot.legacy(report, row)
    except Exception:
        logger.debug("Older report %s has no readable snapshot", row.get("id"), exc_info=True)
        return None


def _analyzed_queries(row: dict[str, Any], report: Any, store: MonitorStore,
                      refs: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """The run's selected queries with their final analysis state, from its progress events."""
    selected: list[dict[str, Any]] = []
    latest: dict[str, dict[str, Any]] = {}
    for entry in store.report_events(row["id"]):
        event = entry["event"]
        if event.get("type") == "selected":
            selected = event["queries"]
        elif event.get("type") == "analysis":
            latest[event["key"]] = event
    if not selected and isinstance(report, dict):
        # Runs from before progress events: rebuild from the stored context.
        provisional = set(report.get("provisional_query_keys") or [])
        top = (report.get("computed") or {}).get("top_queries") or []
        selected = [{"key": item["query_key"],
                     "display_name": refs.get(item["query_key"], {}).get("display_name",
                                                                        item["query_key"]),
                     "db_time_share": item.get("db_time_share", 0.0)} for item in top]
        latest = {item["key"]: {"state": "provisional" if item["key"] in provisional else "done",
                                "result_line": None, "reason": None} for item in selected}
    names = {key: ref["display_name"] for key, ref in refs.items()}
    if any(item["key"] not in names for item in selected):
        names.update(target_names(store, row["target"]))
    return [{"key": item["key"], "display_name": names.get(item["key"], item["display_name"]),
             "db_time_share": item["db_time_share"],
             "state": latest.get(item["key"], {}).get("state", "queued"),
             "result_line": latest.get(item["key"], {}).get("result_line"),
             "reason": latest.get(item["key"], {}).get("reason")} for item in selected]


def report_summary(row: dict[str, Any], names: dict[str, str] | None = None) -> dict[str, Any]:
    """One done run for the history list."""
    import re

    from features.monitor.presentation import health_status, lead_with_finding

    from features.monitor.report_v2 import verdict_sentence

    report = json.loads(row["report_json"] or "null") or {}
    for item in (report.get("computed") or {}).get("top_queries") or []:
        if isinstance(item, dict) and item.get("query_key") in (names or {}):
            item["display_name"] = names[item["query_key"]]
    summary = lead_with_finding(str(report.get("summary") or ""))
    headline = re.split(r"(?<=[.!?])\s", summary.strip(), maxsplit=1)[0] if summary else ""
    day = report.get("snapshot") if isinstance(report.get("snapshot"), dict) else None
    verdict = "" if day is not None or not row.get("target") else \
        verdict_sentence(report, row["target"])
    statuses = [health_status(str(item.get("status", "")))
                for item in report.get("health") or [] if isinstance(item, dict)]
    return {"id": row["id"], "ts": row["ts"], "trigger": row["trigger"],
            "duration_s": max(0, (row.get("finished_at") or row["ts"]) - row["ts"]),
            "headline": verdict or headline, "verdict": verdict,
            "counts": {"fix_first": len(report.get("fix_first") or []),
                       "one_fix": len(report.get("one_fix_several_queries") or []),
                       "health_warn": sum(status in ("warn", "bad") for status in statuses),
                       "health_ok": sum(status == "ok" for status in statuses)},
            "provisional_count": len(report.get("provisional_query_keys") or []),
            **_day_summary(row, day)}


def _day_summary(row: dict[str, Any], day: dict[str, Any] | None) -> dict[str, Any]:
    """What the day list shows: which day, and a few counts from its snapshot."""
    start, end, partial = _period_of(row)
    result: dict[str, Any] = {"period_start": start, "period_end": end,
                              "period_partial": partial, "sample": bool(row.get("sample")),
                              "day": datetime.fromtimestamp(start).date().isoformat()
                              if row.get("period_start") is not None else _legacy_day(row),
                              "events": None, "open_events": None, "look_at": None,
                              "health_problems": None, "qps": None}
    if day is None:
        return result
    events = [event for event in day.get("events") or [] if event.get("kind") != "index"]
    qps = next((number.get("value") for number in day.get("numbers") or []
                if number.get("metric") == "qps"), None)
    return {**result, "events": len(events),
            "open_events": sum(1 for event in events if event.get("state") == "open"),
            "look_at": len(day.get("look_at") or []),
            "health_problems": len((day.get("health") or {}).get("problems") or []),
            "qps": qps}


FIRST_CHECKUP_MIN_S = 120
FIRST_CHECKUP_MAX_S = 600
FIRST_PASS = 10
CHECKUP_SLOTS = 2
# When the first pass of analyses finished: the page stops showing its progress.
FIRST_PASS_DONE = "first_pass_done"


def first_pass(store: MonitorStore, target: str, now: int) -> dict[str, Any] | None:
    """The first deep-analysis pass after monitoring turned on: how far along it is.

    None outside the first monitored hour or once it's recorded as done. It's done ("due") once
    the top queries are analyzed and two minutes have passed, or at ten minutes regardless.
    """
    start, end = store.first_hour(target)
    if not start or not start <= now < end:
        return None
    done = store.read("SELECT value FROM monitor_setting WHERE target=? AND key=?",
                      (target, FIRST_PASS_DONE))
    if done and str(done[0]["value"]).isdigit() and int(done[0]["value"]) >= start:
        return None
    jobs = store.read("SELECT state FROM analyze_job WHERE target=? AND enqueued_at>=? "
                      "AND enqueued_at<? AND reason IN ('new','top15_stale')", (target, start, end))
    finished = sum(1 for job in jobs if job["state"] in ("done", "failed", "skipped"))
    # The first pass analyzes reads only, so writes never count toward what it waits for.
    tracked = sum(1 for row in store.read("SELECT text FROM query_text WHERE target=? AND "
                                          "first_seen>=?", (target, start))
                  if statement_kind(row["text"] or "") == "select")
    total = max(len(jobs), min(FIRST_PASS, tracked))
    elapsed = now - start
    due = elapsed >= FIRST_CHECKUP_MAX_S or (
        elapsed >= FIRST_CHECKUP_MIN_S and total > 0 and finished >= total)
    return {"started_at": start, "analyzed": finished, "total": total, "due": due,
            "due_by": start + FIRST_CHECKUP_MAX_S}


# A claimed checkup waiting for a slot renews its claim this often, well inside the stale limit.
SLOT_HEARTBEAT_S = 120


def _nightly_ready(store: MonitorStore, target: str, now: int) -> bool:
    """Wait for a minute of observed query history and a positive query delta in the day
    the nightly report covers."""
    start, end, _ = snapshot.report_period("nightly", now)
    span = store.read(
        "SELECT MIN(first_seen) AS first,MAX(last_seen) AS last "
        "FROM query_text WHERE target=? AND last_seen>=?",
        (target, start))
    if (not span or span[0]["first"] is None or
            span[0]["last"] - span[0]["first"] < MIN_NIGHTLY_OBSERVED_SPAN_SECONDS):
        return False
    return bool(store.read(
        "SELECT 1 FROM stmt_series WHERE target=? AND bucket_start>=? AND bucket_start<? "
        "AND calls>0 AND total_ms>0 LIMIT 1", (target, start, end)))


class ReportScheduler:
    """Start one snapshot per monitored target after its nightly hour, for the day that
    ended at midnight."""

    def __init__(self, store: MonitorStore | None = None,
                 clock: Callable[[], float] = time.time):
        self.store = store or MonitorStore()
        self.clock = clock
        self._task: asyncio.Task | None = None
        self._runs: set[asyncio.Task] = set()
        # Checkups share the model with deep analyses; a few at a time keeps both moving.
        self._slots = asyncio.Semaphore(CHECKUP_SLOTS)
        self._explaining: set[str] = set()

    async def start(self) -> "ReportScheduler":
        if self._task is None:
            await asyncio.to_thread(self.store.recover_reports, int(self.clock()))
            self._task = asyncio.create_task(self._run(), name="monitor-report-scheduler")
        return self

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        for task in list(self._runs):
            task.cancel()
        if self._runs:
            await asyncio.gather(*self._runs, return_exceptions=True)
        await stop_report_threads()

    async def _run(self) -> None:
        while True:
            try:
                # Store reads and claims run in a worker; SQLite can wait on another
                # writer's lock, and that wait must not stall the event loop.
                self._launch(await asyncio.to_thread(self._plan, int(self.clock())))
            except Exception:
                logger.exception("Report scheduler tick failed")
            await asyncio.sleep(max(0.1, 60 - self.clock() % 60))

    def tick(self) -> None:
        self._launch(self._plan(int(self.clock())))

    def _plan(self, now: int) -> list[tuple[str, tuple]]:
        """What this minute starts: narrative retries and claimed nightly runs."""
        plan: list[tuple[str, tuple]] = []
        self.store.recover_reports(now)
        for row in self.store.read("SELECT * FROM report_run WHERE state='done' "
                                   "AND ts>=? AND report_json LIKE '%narrative_retry_at%'",
                                   (now - NARRATIVE_RETRY_WINDOW_SECONDS,)):
            report = json.loads(row["report_json"])
            if database_limit.paused(self.store, row["target"], now):
                continue
            if (report.get("narrative_error") and
                    int(report.get("narrative_retry_count") or 0) < NARRATIVE_RETRY_LIMIT and
                    (report.get("narrative_retry_at") or 0) <= now):
                report["narrative_retry_at"] = now + 900
                self.store.update_report(row["id"], report=report,
                                         provisional=bool(row["provisional"]))
                plan.append(("retry", (row["id"], row["target"], report)))
        local = datetime.fromtimestamp(now)
        for target in self.store.monitored_targets():
            if not database_limit._signed_in():
                database_limit.hold_signed_out(self.store, target, now)
                continue
            nightly = self._nightly(target, now, local)
            progress = first_pass(self.store, target, now)
            if progress is not None and progress["due"]:
                self.store.write_setting(target, FIRST_PASS_DONE, str(now))
            if nightly is not None:
                plan.append(("nightly", (target, nightly)))
            if target not in self._explaining and action_explain.due(self.store, target, now):
                plan.append(("explain", (target,)))
        return plan

    def _nightly(self, target: str, now: int, local: datetime) -> dict[str, Any] | None:
        """The nightly run this tick claims for the target, if one is due."""
        levers = self.store.levers(target)
        if not levers["nightly_report"] or local.hour < levers["nightly_hour"]:
            return None
        cutoff = int(local.replace(hour=levers["nightly_hour"], minute=0, second=0,
                                   microsecond=0).timestamp())
        if not _nightly_ready(self.store, target, now):
            return None
        day_start, _, _ = snapshot.report_period("nightly", now)
        # One nightly snapshot per day: any real run already covering it counts.
        if self.store.read(
            "SELECT 1 FROM report_run WHERE target=? AND sample=0 "
            "AND (state IN ('running','done') OR (trigger='nightly' AND state='failed')) "
            "AND ((trigger='nightly' AND period_start=?) OR (period_start IS NULL AND "
            "(ts>=? OR COALESCE(finished_at,0)>=?))) LIMIT 1",
            (target, day_start, cutoff, cutoff)):
            return None
        run, claimed = self.store.claim_report(target, "nightly", now, day_start)
        return run if claimed else None

    async def _in_slot(self, work: Awaitable[Any], run_id: int | None = None) -> Any:
        if run_id is None:
            async with self._slots:
                return await work
        # A claimed run waiting for a slot stays alive, and runs only if nothing has failed or
        # replaced it meanwhile.
        while True:
            try:
                await asyncio.wait_for(self._slots.acquire(), timeout=SLOT_HEARTBEAT_S)
                break
            except asyncio.TimeoutError:
                if not await asyncio.to_thread(self.store.touch_report, run_id, int(self.clock())):
                    if inspect.iscoroutine(work):
                        work.close()
                    return None
        try:
            if not await asyncio.to_thread(self.store.touch_report, run_id, int(self.clock())):
                if inspect.iscoroutine(work):
                    work.close()
                return None
            return await work
        finally:
            self._slots.release()

    def _launch(self, plan: list[tuple[str, tuple]]) -> None:
        for kind, args in plan:
            if kind == "retry":
                work = self._retry_narrative(*args)
            elif kind == "explain":
                self._explain(args[0])
                continue
            else:
                target, run = args
                work = run_report(target, "user" if kind == "first" else "nightly",
                                  store=self.store, clock=self.clock, claimed_run=run,
                                  wake_queue=_wake_analyze_queue)
            run_id = None if kind == "retry" else args[1]["id"]
            task = asyncio.create_task(self._in_slot(work, run_id))
            self._runs.add(task)
            task.add_done_callback(self._finished)

    def _explain(self, target: str) -> None:
        """The whys for a database's new changes: its own short call, apart from the slots
        nightly checkups wait on."""
        self._explaining.add(target)

        async def run() -> None:
            try:
                await _to_thread(target, action_explain.explain, self.store, target,
                                 int(self.clock()))
            finally:
                self._explaining.discard(target)

        task = asyncio.create_task(run())
        self._runs.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task) -> None:
        self._runs.discard(task)
        if not task.cancelled() and task.exception():
            logger.warning("Nightly report failed: %s", task.exception())

    async def _retry_narrative(self, report_id: int, target: str,
                               report: dict[str, Any]) -> None:
        calls = None
        if database_limit.paused(self.store, target, int(self.clock())):
            return
        try:
            fingerprint = await asyncio.to_thread(database_limit.target_fingerprint, target)
            with count_billable_calls() as calls, monitoring_database(fingerprint), inference_workflow(
                "monitor", "web", operation="monitor_report",
            ):
                narrative = _bound_narrative(
                    validate_narrative(await _to_thread(target, _narrate, report["computed"])),
                    report["computed"])
            if isinstance(report.get("snapshot"), dict):
                checks = health_findings(report["snapshot"])
                if not narrative.get("health"):
                    narrative["health"] = checks
                if isinstance(report["computed"]["health_collectors"].get("sequential_scans"), dict):
                    narrative["health"] = [check for check in narrative["health"]
                                           if check["check"].lower() != "full table scans"] + [
                        check for check in checks if check["check"] == "Full table scans"]
            await asyncio.to_thread(database_limit.clear_allowance, self.store, "hosted", int(self.clock()))
            report.update(narrative)
            report["narrative_error"] = None
            report["narrative_retry_at"] = None
            self.store.update_report(report_id, report=report,
                                     provisional=bool(report["provisional_query_keys"]),
                                     billable_calls=calls.count)
            row = self.store.report(report_id)
            if row:
                _file_report_items(self.store, target, report_id, row["ts"], narrative,
                                   row["trigger"])
        except Exception as exc:
            if database_limit.is_limit_error(exc):
                await asyncio.to_thread(database_limit.file_refusal, self.store, target, int(self.clock()))
            elif allowance := database_limit.is_allowance_error(exc):
                await asyncio.to_thread(database_limit.file_allowance, self.store, target,
                                        allowance, int(self.clock()))
            report["narrative_error"] = str(exc)
            report["narrative_retry_count"] = int(report.get("narrative_retry_count") or 0) + 1
            self.store.update_report(report_id, report=report,
                                     provisional=bool(report["provisional_query_keys"]),
                                     billable_calls=calls.count if calls else 0)
            logger.warning("Report narrative retry failed for %s: %s", target, exc)


def _wake_analyze_queue() -> None:
    from features.monitor.analyze_queue import _active_queue

    if _active_queue is not None:
        _active_queue.wake()

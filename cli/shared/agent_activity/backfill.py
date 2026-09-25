"""One-time backfill of the agent activity log from monitor.db history.

Runs once, when agent_activity.db is created, so the log isn't empty on day one. The
monitor recorded what ran and how it ended, not the statements, so backfilled entries
carry no statement rows; judge runs kept their Jev usage, so those AI calls are exact.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from shared.agent_activity.recorder import (
    JEV_MODEL,
    ai_data_kinds,
    jev_cost_usd,
    judge_summary,
)

Row = tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]
BACKFILL_NOTE = "Recorded before the activity log existed; statements weren't captured."


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def backfill_rows(monitor_db: Path, since: int, before: int) -> list[Row]:
    """Finished monitor work in [since, before)."""
    if not Path(monitor_db).exists():
        return []
    from shared.monitor.statements import display_name

    conn = sqlite3.connect(f"file:{monitor_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = _tables(conn)
        texts: dict[tuple[str, str], str] = {}
        if "query_text" in tables:
            texts = {(row["target"], row["query_key"]): row["text"] for row in conn.execute(
                "SELECT target,query_key,text FROM query_text")}
        rows: list[Row] = []
        if "analyze_job" in tables:
            rows += _analyze_jobs(conn, since, before, texts, display_name)
        if "judge_run" in tables:
            rows += _judge_runs(conn, since, before,
                                "inbox_event" in tables and "inbox_item" in tables)
        if "report_run" in tables:
            rows += _report_runs(conn, since, before)
        return rows
    finally:
        conn.close()


def _analyze_jobs(conn: sqlite3.Connection, since: int, before: int, texts: dict,
                  display_name) -> list[Row]:
    billable = "billable_calls" in _columns(conn, "analyze_job")
    out: list[Row] = []
    for row in conn.execute(
            "SELECT * FROM analyze_job WHERE state IN ('done','failed','skipped') "
            "AND COALESCE(started_at, finished_at, enqueued_at)>=? "
            "AND COALESCE(started_at, finished_at, enqueued_at)<?", (since, before)):
        job = dict(row)
        text = texts.get((job["target"], job["query_key"]))
        mode = job.get("mode")
        decision = None
        if job["state"] == "done":
            decision = ("Ran EXPLAIN ANALYZE" if mode == "analyze" else "Ran EXPLAIN only")
            decision += {"sampled": " with parameters sampled from live sessions",
                         "table_sampled": " with parameters sampled from the table"}.get(
                             job.get("param_source") or "", "")
        calls = int(job.get("billable_calls") or 0) if billable else 0
        out.append(({
            "ts": job.get("started_at") or job.get("finished_at") or job["enqueued_at"],
            "finished_at": job.get("finished_at"),
            "target": job["target"], "action": "deep_analysis",
            "subject": display_name(text) if text else job["query_key"],
            "outcome": job["state"],
            "summary": (job.get("error") or "")[:500] if job["state"] != "done" else "",
            "decision": decision,
            "details": {"query_key": job["query_key"], "reason": job["reason"],
                        "mode": mode, "param_source": job.get("param_source"),
                        "analysis_ref": job.get("analysis_ref"), "ai_calls_billed": calls,
                        "note": BACKFILL_NOTE},
            "run_ref": f"analyze_job:{job['id']}", "ai_call_count": calls,
        }, [], []))
    return out


def _judge_runs(conn: sqlite3.Connection, since: int, before: int,
                with_items: bool) -> list[Row]:
    filed: dict[tuple[str, int], int] = {}
    if with_items:
        for row in conn.execute(
                "SELECT i.target, e.ts, COUNT(*) AS n FROM inbox_event e "
                "JOIN inbox_item i ON i.id=e.item_id WHERE e.ts>=? "
                "AND e.kind IN ('created','reopened','worse') GROUP BY i.target, e.ts", (since,)):
            filed[(row["target"], row["ts"])] = row["n"]
    out: list[Row] = []
    for row in conn.execute("SELECT id,target,ts,request_json,response_json,dry_run,outcome "
                            "FROM judge_run WHERE ts>=? AND ts<?", (since, before)):
        run = dict(row)
        items = filed.get((run["target"], run["ts"]), 0) if run["outcome"] == "filed" else 0
        calls = []
        if run["request_json"]:
            try:
                response = json.loads(run["response_json"] or "{}")
                request = json.loads(run["request_json"])
            except ValueError:
                response, request = {}, {}
            usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
            tokens_in = usage.get("input_tokens")
            calls.append({
                "ts": run["ts"], "provider": "readyset-jev",
                "model": response.get("model") or JEV_MODEL, "operation": "monitor_attention",
                "purpose": "attention_check", "data_kinds": ai_data_kinds("monitor_attention"),
                "tokens_in": tokens_in, "tokens_out": usage.get("output_tokens"),
                "cost_usd": jev_cost_usd(tokens_in) if tokens_in is not None else None,
                "cost_estimated": True, "request_id": request.get("request_id"),
                "error": response.get("code") if run["outcome"] == "error" else None})
        outcome, summary = judge_summary(run["outcome"], items, bool(run["dry_run"]))
        out.append(({
            "ts": run["ts"], "finished_at": run["ts"], "target": run["target"],
            "action": "attention_check", "subject": "", "outcome": outcome, "summary": summary,
            "details": {"judge_outcome": run["outcome"], "note": BACKFILL_NOTE},
            "run_ref": f"judge_run:{run['id']}", "items_filed": items}, [], calls))
    return out


def _report_runs(conn: sqlite3.Connection, since: int, before: int) -> list[Row]:
    columns = _columns(conn, "report_run")
    out: list[Row] = []
    for row in conn.execute("SELECT * FROM report_run WHERE ts>=? AND ts<? AND state IN "
                            "('done','failed','cancelled')", (since, before)):
        run = dict(row)
        calls = int(run.get("billable_calls") or 0) if "billable_calls" in columns else 0
        trigger = run.get("trigger")
        out.append(({
            "ts": run["ts"], "finished_at": run.get("finished_at"), "target": run["target"],
            "action": "daily_snapshot",
            "subject": "Nightly" if trigger == "nightly" else "On request",
            "outcome": run["state"], "summary": "",
            "details": {"report_id": run["id"], "trigger": trigger, "ai_calls_billed": calls,
                        "period_start": run.get("period_start"),
                        "period_end": run.get("period_end"), "note": BACKFILL_NOTE},
            "run_ref": f"report_run:{run['id']}", "ai_call_count": calls}, [], []))
    return out

"""Human-facing names, sentences and bands for monitor API responses (UI brief section 5)."""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from shared.monitor.monitor_store import item_query_keys

HEX_KEY = re.compile(r"\b[0-9a-f]{12}\b")
QUERY_TOKEN = re.compile(r"\{\{q:([0-9a-f]{12})\}\}")


def collapse(text: str) -> str:
    return " ".join(text.split())


def display_name(text: str, tag: str | None = None) -> str:
    """The one naming scheme for every monitored statement ("COUNT on orders by status").

    Library tags are not used, so every query on every page reads the same way.
    """
    del tag
    from shared.monitor.statements import display_name as statement_name

    return statement_name(text or "")


def snippet(text: str, limit: int = 120) -> str:
    text = collapse(text or "")
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def format_size(value: Any) -> str:
    size = float(value or 0)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:,.0f} {unit}" if unit == "bytes" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} TB"


def plain(text: Any) -> str:
    """Collector wording without typographic dashes."""
    value = str(text or "")
    value = re.sub("(\\w)\\s*\u2013\\s*(\\d)", r"\1 to \2", value)
    return " ".join(value.replace("\u2014", " - ").replace("\u2013", " - ").split())


def format_ms(value: float | None) -> str:
    if value is None:
        return "no data"
    if value >= 1000:
        return f"{value / 1000:.1f} s"
    if value >= 10:
        return f"{value:.0f} ms"
    if value >= 1:
        return f"{value:.1f} ms"
    return f"{value:.2f} ms"


def format_pct(value: float) -> str:
    percent = value * 100
    return f"{percent:.0f}%" if percent >= 10 or percent == 0 else f"{percent:.1f}%"


# Analysis job reasons (brief 5.11)

_AI_UNAVAILABLE = ("upstream_error", "hosted_inference_unavailable", "hosted_deadline_exceeded",
                   "hosted_inference_error", "accounting_unavailable", "llm analysis failed",
                   "no response from llm", "hosted_upstream_busy")
_AI_BAD_ANSWER = ("parse llm response", "hosted_inference_invalid_response")
_NOT_ANALYZABLE = ("dangerous keyword found", "reaches outside", "only select statements",
                   "not safe to explain", "read-only safety", "so rdst doesn't run it",
                   "no query plan to analyze", "which rdst won't run", "isn't a query")


def analysis_error_kind(state: str, error: str | None, *,
                        cap_reached: bool = False) -> str | None:
    """Why an analysis didn't finish, as one of a few kinds the UI can style.

    sign_in, ai_unavailable (the hosted AI failed or was busy), ai_bad_answer (its answer was
    cut off or unreadable), not_analyzable, timeout, plan_failed, or retrying (the AI service
    failed and RDST is trying again).
    """
    if state == "running" and error in {"retrying", "retrying_after_recovery"}:
        return "retrying"
    if state not in ("failed", "skipped") or not error:
        return None
    lower = error.lower()
    if "login_required" in lower:
        return "sign_in"
    if any(code in lower for code in _AI_UNAVAILABLE):
        return "ai_unavailable"
    if any(code in lower for code in _AI_BAD_ANSWER):
        return "ai_bad_answer"
    if any(code in lower for code in _NOT_ANALYZABLE):
        return "not_analyzable"
    if "timed out" in lower or "timeout" in lower:
        return "timeout"
    if "explain failed" in lower or "syntax error" in lower or "does not exist" in lower:
        return "plan_failed"
    return None


def analysis_reason(state: str, error: str | None, *, cap_reached: bool = False) -> str | None:
    """Turn a stored job error or skip into one sentence a person can act on."""
    if state == "running" and error in {"retrying", "retrying_after_recovery"}:
        return "The AI service didn't answer this time. RDST is trying again."
    if state == "queued" and cap_reached:
        return "Today's automatic analyses are done. You can analyze this one from its row."
    if state not in ("failed", "skipped") or not error:
        return None
    message = error.strip()
    lower = message.lower()
    kind = analysis_error_kind(state, error)
    if kind == "sign_in":
        return "Sign in to run deep analysis."
    if kind == "ai_unavailable":
        if "busy" in lower:
            return "The AI service was busy. RDST tries again later."
        return "The AI service didn't return an answer for this query. RDST tries again later."
    if kind == "ai_bad_answer":
        return "The AI's answer for this query was cut off. RDST tries again later."
    keyword = re.search(r"dangerous keyword found: (\w+)", message, re.I)
    if keyword:
        return (f"RDST only analyzes read-only queries. This statement runs "
                f"{keyword.group(1).upper()}.")
    function = re.search(r"(\w+)\(\) reaches outside", message)
    if function:
        return f"This query calls {function.group(1).lower()}, which RDST won't run."
    if "only select statements" in lower:
        return "RDST only analyzes SELECT statements on its own. Run it from the query to analyze it."
    if "does not exist" in lower and "relation" in lower:
        return "A table this query reads no longer exists."
    if kind == "timeout":
        return "The plan took too long to collect. RDST tries again tomorrow."
    if "syntax error" in lower:
        return "The database couldn't plan this statement with sample values."
    if "no longer available" in lower:
        return "RDST no longer has this query's text."
    if kind == "not_analyzable":
        return "RDST only analyzes read-only queries."
    first = re.split(r"(?<=[.!?])\s|\n", message, maxsplit=1)[0]
    return first if len(first) <= 160 else first[:157] + "..."


# Inbox presentation (brief 5.7, NOTIFICATION_MODEL.md)

POSITIVE_TYPES = {"query_improved"}
FYI_TYPES = {"daily_summary", "query_improved"}
ACT_NOW_TYPES = {"lock_contention", "stuck_query", "monitoring_issue"}
GROUP_BUCKET_SECONDS = 6 * 3600
RECOVERED_WINDOW_S = 86400


def recovered(item: dict[str, Any], body: dict[str, Any]) -> bool:
    """RDST closed the item because its measurements went back to normal."""
    return (item["state"] == "resolved" and item.get("resolved_by") == "code" and
            isinstance(body.get("resolution"), dict) and not body.get("settled"))


def severity_band(type: str, severity: float, *, is_recovered: bool = False) -> str:
    if is_recovered:
        return "recovered"
    if severity >= 0.85 or type in ACT_NOW_TYPES:
        return "act_now"
    if type in FYI_TYPES or type in POSITIVE_TYPES:
        return "fyi"
    return "watch" if severity >= 0.6 else "fyi"


def tone(type: str, band: str, body: dict[str, Any]) -> str:
    if band == "recovered" or type in POSITIVE_TYPES:
        return "positive"
    return {"act_now": "negative", "watch": "warning"}.get(band, "neutral")


def _numbers(body: dict[str, Any]) -> dict[str, Any]:
    for field in ("reported_numbers", "at_filing", "numbers"):
        value = body.get(field)
        if isinstance(value, dict) and value:
            return value
    return {}


def _baseline(body: dict[str, Any]) -> dict[str, Any]:
    baseline = body.get("baseline")
    if isinstance(baseline, dict):
        for window in ("1h", "1d", "7d"):
            if isinstance(baseline.get(window), dict):
                return baseline[window]
    return {}


def format_rate(qps: float) -> str:
    """Calls a second, a minute or an hour, whichever reads as a plain number."""
    if qps >= 1:
        return f"{qps:.1f} calls a second"
    if qps * 60 >= 1:
        return f"{qps * 60:.1f} calls a minute"
    return f"{qps * 3600:.1f} calls an hour"


def _per_minute(value: float) -> str:
    return f"{value:.1f}" if value < 10 else f"{value:.0f}"


def _recovery_summary(type: str, body: dict[str, Any]) -> str:
    if type == "health_finding":
        return "The latest report no longer flags this."
    if type == "lock_contention":
        after = (body.get("resolution") or {}).get("after") or {}
        return f"Lock waits back to normal: {after.get('lock_waits', 0)} sessions waiting."
    if type == "monitoring_issue" and body.get("message"):
        return str(body["message"])
    resolution = body.get("resolution") or {}
    before, after = resolution.get("before") or {}, resolution.get("after") or {}
    if type == "rollback_or_deadlock_spike" and "rollbacks_per_min" in after:
        return (f"Rollbacks back to normal: {_per_minute(after['rollbacks_per_min'])} a "
                "minute.")
    if type == "cache_hit_ratio_drop" and after.get("cache_hit_ratio") is not None:
        return f"Cache hit ratio back to normal: {format_pct(after['cache_hit_ratio'])}."
    if type == "traffic_spike" and after.get("qps") is not None:
        return (f"Calls back to normal: {format_rate(after['qps'])}." if after["qps"] else
                "Calls back to normal: no calls now.")
    if type == "query_stopped_running":
        return "Running again."
    if type == "new_heavy_query":
        share = after.get("db_time_share") or 0
        return (f"Settled at {format_pct(share)} of database time." if share else
                "No longer running.")
    if isinstance(before.get("mean_ms"), (int, float)) and isinstance(
            after.get("mean_ms"), (int, float)) and after["mean_ms"]:
        return (f"Latency back to normal: {format_ms(before['mean_ms'])} -> "
                f"{format_ms(after['mean_ms'])}.")
    if type == "many_queries_changed":
        return "Every query in it measured back to normal."
    return "Measured back to normal."


_TARGET_PREAMBLE = re.compile(r"^\s*(?:On|For)\s+(?:target\s+)?'[^']*'\s*(?:\([^)]*\))?\s*,\s*",
                              re.I)


def lead_with_finding(text: str) -> str:
    """Drop an "On target 'x' (PostgreSQL), " opening so the sentence starts with the finding."""
    stripped = _TARGET_PREAMBLE.sub("", text or "", count=1)
    return stripped[:1].upper() + stripped[1:] if stripped else stripped


def inbox_summary(item: dict[str, Any], body: dict[str, Any], names: dict[str, str]) -> str:
    type = item["type"]
    if recovered(item, body):
        return _recovery_summary(type, body)
    if type == "health_finding":
        return str(body.get("finding") or item["title"])
    now, before = _numbers(body), _baseline(body)
    share = now.get("db_time_share")
    load = (f", {format_pct(share)} of database time"
            if isinstance(share, (int, float)) and share else "")
    if type in ("latency_regression", "plan_change", "table_slowdown", "slow_creep"):
        if before.get("mean_ms") is not None and now.get("mean_ms") is not None:
            return f"Latency {format_ms(before['mean_ms'])} -> {format_ms(now['mean_ms'])}{load}."
    if type == "traffic_spike" and now.get("qps") is not None:
        usual = (f", usually {format_rate(before['qps']).split(' calls')[0]}"
                 if before.get("qps") else "")
        return f"{format_rate(now['qps'])}{usual}{load}."
    if type == "query_stopped_running" and before.get("qps") is not None:
        return f"Ran {format_rate(before['qps'])}, then stopped."
    if type == "new_heavy_query" and now.get("mean_ms") is not None:
        return f"New query, {format_ms(now['mean_ms'])} a call{load}."
    if type == "query_improved":
        resolution = body.get("resolution") or {}
        old, new = (resolution.get("before") or {}), (resolution.get("after") or {})
        if old.get("mean_ms") is None and body.get("baseline_mean_ms") is not None:
            old, new = {"mean_ms": body["baseline_mean_ms"]}, now
        if old.get("mean_ms") is not None and new.get("mean_ms") is not None:
            return f"Latency {format_ms(old['mean_ms'])} -> {format_ms(new['mean_ms'])}."
    if type == "rollback_or_deadlock_spike":
        numbers = body.get("numbers") or {}
        if "rollbacks_per_min" in numbers:
            parts = []
            if numbers["rollbacks_per_min"] >= 1:
                parts.append(f"Rollbacks {_per_minute(numbers.get('rollbacks_per_min_before', 0))}"
                             f" -> {_per_minute(numbers['rollbacks_per_min'])} a minute")
            if numbers.get("deadlocks_per_min", 0) > 0:
                parts.append(f"deadlocks {_per_minute(numbers.get('deadlocks_per_min_before', 0))}"
                             f" -> {_per_minute(numbers['deadlocks_per_min'])} a minute")
            if parts:
                return ", ".join(parts)[:1].upper() + ", ".join(parts)[1:] + "."
        if numbers.get("rollbacks"):
            # Filed before rates were recorded: the last minute it saw.
            return f"{numbers['rollbacks']:.0f} rollbacks in the last minute RDST checked."
        return "Rollbacks or deadlocks rose."
    if type == "cache_hit_ratio_drop":
        numbers = body.get("numbers") or {}
        if numbers.get("cache_hit_ratio_before") and numbers.get("cache_hit_ratio") is not None:
            return (f"Cache hit ratio {format_pct(numbers['cache_hit_ratio_before'])} -> "
                    f"{format_pct(numbers['cache_hit_ratio'])}.")
        return "Cache hit ratio fell."
    if type == "lock_contention":
        summary = (f"{body.get('lock_waits', 0)} sessions waiting on locks for "
                   f"{body.get('minutes', 0)} minutes.")
        blocker = body.get("blocker") or {}
        if blocker.get("query"):
            summary += f" Blocked by {snippet(blocker['query'], 100)}."
        return summary
    if type == "connections_near_max":
        active, limit = body.get("active_conns"), body.get("max_conns")
        if active is not None and limit:
            return f"{active} of {limit} connections in use."
    if type == "many_queries_changed":
        keys = body.get("episode_query_keys") or body.get("query_keys") or []
        return f"{len(keys)} {'query' if len(keys) == 1 else 'queries'} changed at the same time."
    if type == "stuck_query":
        return "Ran for over a minute." if item["state"] in ("resolved", "dismissed") else \
            "Running for over a minute and still going."
    if type == "daily_summary":
        if body.get("headline_summary"):
            return str(body["headline_summary"])
        text = QUERY_TOKEN.sub(lambda m: names.get(m.group(1)) or "a query",
                               str(body.get("summary") or ""))
        return snippet(lead_with_finding(text) or "The nightly report is ready.", 140)
    if type == "monitoring_issue":
        env = body.get("password_env")
        failed = body.get("failed_check") or {}
        if item["subject"] == "lost_connection" and body.get("message"):
            return str(body["message"])
        if item["subject"] in SOURCE_MISSING and failed.get("label"):
            detail = f" {failed['detail']}" if failed.get("detail") else ""
            return f"Setup check failed: {failed['label']}.{detail}"
        return {"password_missing": (f"{env} isn't set, so RDST can't connect." if env else
                                     "The password variable isn't set, so RDST can't connect."),
                "pg_stat_statements_missing": "RDST reads queries from pg_stat_statements, "
                                              "and it isn't available.",
                "performance_schema_missing": "RDST reads queries from performance_schema, "
                                              "and it isn't available.",
                "lost_connection": "RDST couldn't connect on its last check.",
                "jev_login": "Alerts need a signed-in RDST account.",
                "llm_login": "Deep analysis needs a signed-in RDST account.",
                "storage_budget": "Monitoring history is at its storage limit.",
                "database_limit": "This account's AI monitoring slots are all in use, so AI "
                                  "checks for this one are paused. Readings keep recording.",
                "jev_allowance": "This account reached its AI limit for AI checks. "
                                 "Readings keep recording.",
                "hosted_allowance": "This account reached its AI limit for deep analysis "
                                    "and reports. Readings keep recording.",
                }.get(item["subject"], item["title"] + ".")
    return item["title"] + "."


QUERY_TAB = {"plan_change": "analysis", "new_heavy_query": "analysis"}


def inbox_link(item: dict[str, Any], body: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    """Where the item's Open button goes: a query row and tab, the report, or a setting."""
    type, target = item["type"], item["target"]
    if type in ("daily_summary", "health_finding") and body.get("report_id"):
        return {"kind": "report", "target": target, "report_id": body["report_id"],
                "label": "Open report"}
    if type == "monitoring_issue":
        subject = item["subject"]
        if subject in ("jev_login", "llm_login"):
            return {"kind": "sign_in", "target": target, "label": "Sign in"}
        if subject == "lost_connection" and body.get("gap_from"):
            # The Database page zooms to the gap in readings (the item's incident window).
            closed = item["state"] in ("resolved", "dismissed")
            return {"kind": "database", "target": target,
                    "label": "See the gap" if closed else "See when it stopped"}
        if subject in ("password_missing", "lost_connection"):
            return {"kind": "configure", "target": target, "label": "Check connection"}
        if subject in ("storage_budget", "database_limit", "jev_allowance", "hosted_allowance"):
            return {"kind": "settings", "target": target, "label": "Monitoring settings"}
        if subject in SOURCE_MISSING:
            # The live prerequisites checker: each failed check with its fix.
            return {"kind": "setup", "target": target, "label": "Check setup"}
        return {"kind": "database", "target": target, "label": "Open database"}
    if len(keys) == 1:
        return {"kind": "query", "target": target, "query_key": keys[0],
                "tab": QUERY_TAB.get(type, "performance"), "label": "Open query"}
    if len(keys) > 1:
        # The Database page filtered to exactly these queries, inside the item's time frame.
        return {"kind": "database", "target": target, "query_keys": keys,
                "label": "Open affected queries"}
    return {"kind": "database", "target": target, "label": "Open database"}


def _tables(text: str) -> list[str]:
    return list(_parsed_tables(text))


@lru_cache(maxsize=4096)
def _parsed_tables(text: str) -> tuple[str, ...]:
    try:
        from features.schema.assessment_context import referenced_tables

        return tuple(table.rsplit(".", 1)[-1] for table in referenced_tables(text))[:4]
    except Exception:
        return ()


def inbox_advice(item: dict[str, Any], body: dict[str, Any], engine: str,
                 texts: dict[str, str], keys: list[str]) -> dict[str, Any] | None:
    """"What you can do" for steps RDST can't take itself, or facts it can't see.

    None when the fix lives inside RDST (the link covers it) or the item is closed.
    """
    if item["state"] in ("resolved", "dismissed"):
        return None
    type, subject = item["type"], item["subject"]
    mysql = engine == "mysql"
    tables = _tables(texts.get(keys[0], "")) if len(keys) == 1 else []
    analyze = (f"ANALYZE TABLE {', '.join(tables)};" if mysql else
               f"ANALYZE {', '.join(tables)};") if tables else None
    if type == "stuck_query":
        if mysql:
            return {"summary": "RDST doesn't stop running statements. From a SQL session:",
                    "steps": ["Find it: SELECT id, time, state, info FROM "
                              "information_schema.processlist WHERE command <> 'Sleep' "
                              "ORDER BY time DESC;",
                              "Stop the statement and keep the connection: KILL QUERY <id>;"]}
        return {"summary": "RDST doesn't stop running statements. From a SQL session:",
                "steps": ["Find it and what it waits on: SELECT pid, now() - query_start AS "
                          "running_for, wait_event_type, wait_event, left(query, 80) FROM "
                          "pg_stat_activity WHERE state <> 'idle' ORDER BY query_start;",
                          "Cancel it: SELECT pg_cancel_backend(<pid>);",
                          "If it won't cancel and others wait on its locks: "
                          "SELECT pg_terminate_backend(<pid>);"]}
    if type == "connections_near_max":
        return {"summary": "New connections will fail at the limit.",
                "steps": (["Find idle sessions: SELECT user, host, command, time FROM "
                           "information_schema.processlist WHERE command = 'Sleep' "
                           "ORDER BY time DESC;"] if mysql else
                          ["Find sessions left idle in a transaction: SELECT pid, usename, "
                           "application_name, now() - state_change AS idle_for FROM "
                           "pg_stat_activity WHERE state = 'idle in transaction';"]) +
                         ["Lower the application's pool size, or put a connection pooler "
                          f"such as {'ProxySQL' if mysql else 'PgBouncer'} in front of the "
                          "database.",
                          "Raise max_connections only if the server has memory to spare."]}
    if type == "rollback_or_deadlock_spike":
        return {"summary": "RDST counts rollbacks but can't see why the application rolled back.",
                "steps": ["Check application logs for failed transactions from the time "
                          "this started.",
                          "For deadlocks, turn on lock logging (" +
                          ("innodb_print_all_deadlocks = ON" if mysql else
                           "log_lock_waits = on") +
                          ") and read the server log."]}
    if type == "cache_hit_ratio_drop":
        return {"summary": "More reads are going to disk.",
                "steps": ["Sort the query list by load and look for a new large scan.",
                          ("If the working set grew, raise innodb_buffer_pool_size."
                           if mysql else
                           "If the working set grew, raise shared_buffers.")]}
    if type == "query_stopped_running":
        return {"summary": "RDST can't see your application.",
                "steps": ["If a release or a job change removed this query, resolve this item.",
                          "If not, check the service or scheduled job that runs it."]}
    if type == "traffic_spike":
        return {"summary": "The extra calls come from outside the database.",
                "steps": ["Check for a release, a retry loop or a batch job that started "
                          "at the same time.",
                          "If the same reads repeat, try caching this query on the Readyset tab."]}
    if type in ("plan_change", "latency_regression", "table_slowdown") and analyze:
        return {"summary": "If the table's data changed a lot, the planner may be using old "
                           "statistics.",
                "steps": [f"Refresh them: {analyze}",
                          "Check for a bulk job or a lock on the same tables at that time."]}
    if type == "many_queries_changed":
        return {"summary": "Many queries changing together usually has one outside cause.",
                "steps": ["Check for a release, a bulk job, a lock or host load at the time "
                          "this started."]}
    if type == "new_heavy_query":
        return {"summary": "RDST can't see which release introduced a query.",
                "steps": ["Find the code path that runs it and check it runs as often as "
                          "intended."]}
    if type == "monitoring_issue":
        env = body.get("password_env") or "the password variable"
        if subject == "password_missing":
            return {"summary": "RDST reads the password from the environment.",
                    "steps": [f"Set {env} where RDST runs, then restart RDST."]}
        if subject in ("jev_allowance", "hosted_allowance"):
            contact = body.get("contact") or "hello@readyset.io"
            return {"summary": "This account reached its AI limit this month. Local "
                               "monitoring keeps running: charts, lost-connection alerts and "
                               "rule-based items.",
                    "steps": [f"Contact us at {contact} to raise your AI limit.",
                              "AI checks, deep analysis and reports pick back up on their own "
                              "once it's raised. Nothing to restart."]}
        if subject == "database_limit":
            limit = body.get("limit")
            return {"summary": (f"You can use AI monitoring on up to {limit} databases. "
                                if isinstance(limit, int) else
                                "This account's AI monitoring slots are all in use. ") +
                               "Turn off monitoring on another database to free a slot.",
                    "steps": ["Turn off monitoring on a database you no longer need, on this "
                              "machine or another one signed in to the same account.",
                              "RDST retries this database's AI checks every hour and resumes "
                              "on its own once a slot is free."]}
        if subject == "lost_connection":
            return {"summary": "RDST keeps retrying every minute.",
                    "steps": ["Check the database is up and reachable from this machine "
                              "(VPN, firewall, security group).",
                              "Check the host, port and user in the connection settings."]}
        if subject in SOURCE_MISSING:
            fix = (body.get("failed_check") or {}).get("fix") or {}
            if fix.get("summary"):
                steps = [*fix.get("steps", []), *(f"Run: {sql}" for sql in fix.get("sql", [])),
                         *fix.get("commands", []), *fix.get("after", [])]
                return {"summary": fix["summary"], "steps": steps}
            if subject == "performance_schema_missing":
                return {"summary": "Monitoring needs performance_schema's statement digests.",
                        "steps": ["Check setup shows what this server is missing and the "
                                  "exact fix, and checks again as you fix it.",
                                  "Usually: set performance_schema=ON in the server "
                                  "configuration, restart, and grant the RDST user SELECT on "
                                  "performance_schema."]}
            return {"summary": "Monitoring needs the pg_stat_statements extension.",
                    "steps": ["Check setup shows what this server is missing and the exact "
                              "fix for its provider, and checks again as you fix it.",
                              "Usually: add pg_stat_statements to shared_preload_libraries, "
                              "restart, then run CREATE EXTENSION IF NOT EXISTS "
                              "pg_stat_statements;"]}
    return None


# Current titles for items filed under older wording.
MONITORING_TITLES = {"jev_login": "Sign in to resume alerts",
                     "llm_login": "Sign in to continue deep analysis",
                     "database_limit": "Monitoring limit reached",
                     "jev_allowance": "AI limit reached",
                     "hosted_allowance": "AI limit reached",
                     "password_missing": "Database password not set",
                     "lost_connection": "Can't reach the database",
                     "pg_stat_statements_missing": "pg_stat_statements isn't available",
                     "performance_schema_missing": "performance_schema isn't available"}
# Monitoring issues for an engine's statement-statistics source that can't be read.
SOURCE_MISSING = ("pg_stat_statements_missing", "performance_schema_missing")


def current_title(item: dict[str, Any]) -> str:
    if item["type"] == "daily_summary":
        body = item.get("body_json") or {}
        if isinstance(body, str):
            body = json.loads(body)
        return str(body.get("headline_title") or "Daily report ready")
    if item["type"] == "monitoring_issue":
        return MONITORING_TITLES.get(item["subject"], item["title"])
    return item["title"]


# Alert measurements (DESIGN_V4 section 5): every alert says what was measured, which way it
# moved and by how much.

SLOWER_KINDS = {"latency_regression", "plan_change", "table_slowdown", "slow_creep"}
FASTER_KINDS = {"query_improved"}
BUSIER_KINDS = {"traffic_spike", "new_heavy_query"}
STOPPED_KINDS = {"query_stopped_running"}
GROUP_MAJORITY = 2 / 3


def change_text(change: float | None) -> str:
    """The size of a move, for a sentence that already says up or down: "3.2x" or "35%"."""
    if change is None:
        return ""
    factor = 1 + change
    if factor >= 2:
        return f"{factor:.1f}x"
    if 0 < factor <= 0.5:
        return f"{1 / factor:.1f}x"
    return f"{abs(change) * 100:.0f}%"


def _move(metric: str, before: float | None, after: float | None, unit: str,
          label: str, render: Any) -> dict[str, Any] | None:
    if after is None:
        return None
    change = after / before - 1 if before else None
    direction = "down" if change is not None and change < 0 or (before and after < before) \
        else "up"
    if before is None:
        text = f"{label} {render(after)}"
    elif change is None:
        text = f"{label} {render(before)} -> {render(after)}"
    else:
        text = f"{label} {direction} {change_text(change)}: {render(before)} -> {render(after)}"
    return {"metric": metric, "direction": direction, "before": before, "after": after,
            "change": change, "unit": unit, "text": text}


def _rate(qps: float) -> str:
    return format_rate(qps).replace(" calls", "")


def group_class(kinds: list[str]) -> str:
    """slower, faster, busier, stopped or mixed: the direction most members share."""
    if not kinds:
        return "mixed"
    for name, members in (("slower", SLOWER_KINDS), ("faster", FASTER_KINDS),
                          ("busier", BUSIER_KINDS), ("stopped", STOPPED_KINDS)):
        if sum(kind in members for kind in kinds) >= GROUP_MAJORITY * len(kinds):
            return name
    return "mixed"


def group_title(kinds: list[str]) -> str:
    count = len(kinds)
    return {"slower": f"{count} {'query' if count == 1 else 'queries'} got slower at the same time",
            "faster": f"{count} {'query' if count == 1 else 'queries'} got faster at the same time",
            "busier": f"Traffic jumped on {count} {'query' if count == 1 else 'queries'}",
            "stopped": f"{count} {'query' if count == 1 else 'queries'} stopped running"}.get(
        group_class(kinds), f"{count} {'query' if count == 1 else 'queries'} changed at the same time")


def _member_kind(key: str, body: dict[str, Any], stats: dict[str, Any]) -> str:
    for field in ("recovery_queries", "recovered_queries"):
        kind = ((body.get(field) or {}).get(key) or {}).get("kind")
        if kind:
            return kind
    latency = stats.get("latency_change")
    calls = stats.get("calls_change")
    if stats.get("after_qps") == 0 and stats.get("before_qps"):
        return "query_stopped_running"
    if latency is not None and (calls is None or abs(latency) >= abs(calls)):
        return "latency_regression" if latency > 0 else "query_improved"
    if calls is not None:
        return "traffic_spike" if calls > 0 else "query_stopped_running"
    return "latency_regression"


def member_signal(key: str, body: dict[str, Any], stats: dict[str, Any],
                  group_metric: str) -> tuple[str, str]:
    """The metric and direction one group member's own change follows: a slower member is
    measured on latency even in a group about calls."""
    return incident_signal(_member_kind(key, body, stats)) or (group_metric, "up")


def _member_numbers(key: str, body: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    """Before/after for one member: the filing numbers when kept (a recovered member keeps
    them as `before`), else measured around filing."""
    filing = (body.get("recovery_queries") or {}).get(key) or {}
    at = filing.get("at_filing") or {}
    if not at:
        filing = (body.get("recovered_queries") or {}).get(key) or {}
        at = filing.get("before") or {}
    if at and (filing.get("baseline_mean_ms") or filing.get("baseline_qps")):
        return {"before_ms": filing.get("baseline_mean_ms") or None, "after_ms": at.get("mean_ms"),
                "before_qps": filing.get("baseline_qps") or None, "after_qps": at.get("qps")}
    return {"before_ms": stats.get("before_ms"), "after_ms": stats.get("after_ms"),
            "before_qps": stats.get("before_qps"), "after_qps": stats.get("after_qps")}


def _median(values: list[float]) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def group_alert(keys: list[str], body: dict[str, Any], names: dict[str, str],
                member_stats: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Title, summary, measurement and the top three members by impact of a group alert."""
    kinds = [_member_kind(key, body, member_stats.get(key) or {}) for key in keys]
    kind_class = group_class(kinds)
    latency = kind_class in ("slower", "faster") or (
        kind_class == "mixed" and sum(kind in SLOWER_KINDS | FASTER_KINDS for kind in kinds) >=
        len(kinds) / 2)
    members = []
    for key in keys:
        numbers = _member_numbers(key, body, member_stats.get(key) or {})
        before = numbers["before_ms"] if latency else numbers["before_qps"]
        after = numbers["after_ms"] if latency else numbers["after_qps"]
        change = after / before - 1 if before and after is not None else None
        if latency:
            impact = abs((after or 0) - (before or 0)) * (numbers["after_qps"] or 0) * 60
        else:
            impact = abs((after or 0) - (before or 0)) * (numbers["after_ms"] or
                                                          numbers["before_ms"] or 0) * 60
        members.append({"query_key": key, "display_name": names.get(key, f"Query {key}"),
                        "before": before, "after": after, "change": change, "impact_ms": impact})
    members.sort(key=lambda member: (-member["impact_ms"], member["query_key"]))
    median = _median([member["change"] for member in members if member["change"] is not None])
    metric = "latency" if latency else "calls"
    render = format_ms if latency else _rate
    label = "Median latency" if latency else "Median calls"
    direction = "down" if median is not None and median < 0 else "up"
    measurement = ({"metric": metric, "direction": direction, "before": None, "after": None,
                    "change": median, "unit": "ms" if latency else "calls_per_second",
                    "text": f"{label} {direction} {change_text(median)} across {len(keys)} "
                            "queries"} if median is not None else None)
    top = members[:3]
    worst = ", ".join(f"{member['display_name']} {render(member['before'])} -> "
                      f"{render(member['after'])}" for member in top
                      if member["before"] is not None and member["after"] is not None)
    lead = (f"{label} {direction} {change_text(median)}." if median is not None else
            f"{len(keys)} {'query' if len(keys) == 1 else 'queries'} changed together.")
    return {"title": group_title(kinds), "summary": f"{lead} Most impact: {worst}." if worst
            else lead, "measurement": measurement, "members": top, "median_change": median}


def alert_measurement(item: dict[str, Any], body: dict[str, Any]) -> dict[str, Any] | None:
    """What a single alert measured: metric, direction and magnitude."""
    type = item["type"]
    now, before = _numbers(body), _baseline(body)
    if type in SLOWER_KINDS | FASTER_KINDS:
        resolution = body.get("resolution") or {}
        old = before.get("mean_ms", body.get("baseline_mean_ms"))
        new = now.get("mean_ms")
        if type == "query_improved" and (resolution.get("before") or {}).get("mean_ms"):
            old, new = resolution["before"]["mean_ms"], (resolution.get("after") or {}).get(
                "mean_ms", new)
        return _move("latency", old, new, "ms", "Latency", format_ms)
    if type == "traffic_spike":
        return _move("calls", before.get("qps"), now.get("qps"), "calls_per_second", "Calls",
                     lambda qps: format_rate(qps).replace(" calls", ""))
    if type == "query_stopped_running":
        if before.get("qps") is None:
            return None
        return {"metric": "calls", "direction": "down", "before": before["qps"], "after": 0.0,
                "change": -1.0, "unit": "calls_per_second",
                "text": f"Calls down to 0 from {format_rate(before['qps'])}"}
    if type == "new_heavy_query" and now.get("db_time_share") is not None:
        return {"metric": "share", "direction": "up", "before": None,
                "after": now["db_time_share"], "change": None, "unit": "ratio",
                "text": f"New: {format_pct(now['db_time_share'])} of database time"}
    if type == "rollback_or_deadlock_spike":
        numbers = body.get("numbers") or {}
        if "rollbacks_per_min" in numbers:
            return _move("rollbacks", numbers.get("rollbacks_per_min_before"),
                         numbers["rollbacks_per_min"], "per_minute", "Rollbacks",
                         lambda value: f"{_per_minute(value)} a minute")
    if type == "cache_hit_ratio_drop":
        numbers = body.get("numbers") or {}
        if numbers.get("cache_hit_ratio") is not None:
            return _move("cache_hit_ratio", numbers.get("cache_hit_ratio_before"),
                         numbers["cache_hit_ratio"], "ratio", "Cache hit ratio", format_pct)
    if type == "connections_near_max":
        active, limit = body.get("active_conns"), body.get("max_conns")
        if active is not None and limit:
            return {"metric": "connections", "direction": "up", "before": None,
                    "after": float(active), "change": None, "unit": "connections",
                    "text": f"{active} of {limit} connections in use"}
    if type == "stuck_query":
        duration = body.get("duration_s") or (body.get("numbers") or {}).get("duration_s")
        if isinstance(duration, (int, float)):
            return {"metric": "running_time", "direction": "up", "before": None,
                    "after": float(duration), "change": None, "unit": "seconds",
                    "text": f"Running for {duration / 60:.0f} min" if duration >= 120 else
                            f"Running for {duration:.0f} s"}
    return None


def present_inbox_item(item: dict[str, Any], names: dict[str, str],
                       trend: list[dict[str, Any]], live_rules: set[int], *,
                       engine: str = "postgresql",
                       texts: dict[str, str] | None = None,
                       excluded: set[str] | None = None,
                       member_stats: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    body = item["body_json"]
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            body = {}
    body = body if isinstance(body, dict) else {}
    keys = item_query_keys(item["subject"], body)
    if item["type"] == "many_queries_changed":
        # DDL, maintenance and RDST's own statements are never group members.
        keys = [key for key in keys if key not in (excluded or set())]
    band = severity_band(item["type"], item["severity"], is_recovered=recovered(item, body))
    if band == "act_now" and routine_repeat(body):
        band = "watch"
    # Something that already ended, like a stuck query that finished, never asks to act now.
    if band == "act_now" and item["state"] == "resolved":
        band = "recovered"
    muted_by = item.get("muted_by")
    title, summary = current_title(item), inbox_summary(item, body, names)
    extra: dict[str, Any] = {"measurement": alert_measurement(item, body), "members": [],
                             "median_change": None}
    if item["type"] == "many_queries_changed":
        group = group_alert(keys, body, names, member_stats or {})
        title = group["title"]
        repeats = item_occurrences(body)
        if len(repeats) >= 2:
            # Named by the query that leads it, the one repeats match on.
            lead = (body.get("occurrences") or [{}])[-1].get("top")
            top = (names.get(lead) if lead else None) or (
                group["members"][0]["display_name"] if group["members"] else None)
            title = repeat_title(top, len(keys), repeats, int(time.time()))
        if not recovered(item, body):
            summary = group["summary"]
        extra = {"measurement": group["measurement"], "members": group["members"],
                 "median_change": group["median_change"]}
    return {**item, "title": title,
            "severity_band": band, "tone": tone(item["type"], band, body),
            "summary": summary, "query_keys": keys,
            "subject_label": (names.get(keys[0], keys[0]) if len(keys) == 1 else item["target"]),
            "group_key": f"{item['type']}:{item['target']}:"
                         f"{item['created_at'] // GROUP_BUCKET_SECONDS}",
            "muted_by": muted_by if muted_by in live_rules else None, "trend": trend,
            "query_labels": {key: names[key] for key in keys if key in names},
            "delivery": item.get("delivery") or "inbox",
            "relevance_rule": body.get("relevance_rule"),
            "smaller_moved": len(body.get("smaller_query_keys") or []),
            "recurring": recurring_note(body),
            "occurrences": item_occurrences(body) if item["type"] == "many_queries_changed"
            else [],
            "link": inbox_link(item, body, keys),
            "advice": inbox_advice(item, body, engine, texts or {}, keys), **extra}


def routine_repeat(body: dict[str, Any]) -> bool:
    """A repeating burst on its usual schedule ranks Watch at most; only a bigger than usual
    occurrence, while it lasts, can put it in Act now."""
    entries = body.get("occurrences")
    if not isinstance(entries, list) or len(entries) < 2:
        return False
    latest = entries[-1]
    if latest.get("verdict") == "bigger" and latest.get("end") is None:
        return False
    return any(entry.get("verdict") for entry in entries)


def item_status(item: dict[str, Any], body: dict[str, Any],
                incident: dict[str, Any] | None) -> tuple[str, int | None]:
    """Where a presented item stands, read from the same measurements its time frame shows:
    `ongoing`, `back_to_normal`, `closed` or `unknown`, with the time it got there.

    A repeating burst follows its latest occurrence: the members whose own change began with
    it. A member already changed well before the occurrence started (a level that never moved,
    misread as a jump) doesn't keep the occurrence going.
    """
    if item["severity_band"] == "recovered":
        return "back_to_normal", body.get("resumed_at") or item.get("resolved_at")
    if item["state"] in ("resolved", "dismissed"):
        return "closed", item.get("resolved_at") or item.get("dismissed_at")
    entries = body.get("occurrences")
    if isinstance(entries, list) and len(entries) >= 2 and isinstance(entries[-1], dict):
        latest = entries[-1]
        if latest.get("end") is not None:
            return "back_to_normal", int(latest["end"])
        keys = set(latest.get("keys") or [])
        own = [query for query in (incident or {}).get("queries") or []
               if query["start"] is not None and query["query_key"] in keys and
               query["start"] >= latest["start"] - INCIDENT_NEAR_S]
        if (incident or {}).get("measured") and own and all(
                query["end"] is not None for query in own):
            return "back_to_normal", max(int(query["end"]) for query in own)
        return "ongoing", int(latest["start"])
    if incident:
        if incident["ongoing"]:
            return "ongoing", incident["start"]
        if incident["end"] is not None:
            return "back_to_normal", incident["end"]
        return "unknown", None
    if item["type"] == "query_stopped_running":
        return "ongoing", body.get("filed_at", item["created_at"])
    if item["type"] in ACT_NOW_TYPES:
        return "ongoing", item["created_at"]
    return "unknown", None


BAND_LABELS = {"act_now": "Act now", "watch": "Watch", "fyi": "FYI",
               "recovered": "Back to normal"}
RULE_PHRASES = {"top_10_24h": "a top-10 query today", "top_10_1h": "a top-10 query this hour",
                "top_10_calls_24h": "a top-10 query by calls today",
                "top_10_calls_1h": "a top-10 query by calls this hour",
                "share_24h": "a query with over 2% of database time",
                "share_1h": "a query with over 2% of database time this hour"}
METRIC_WORDS = {"latency": "latency", "calls": "calls", "rollbacks": "rollbacks",
                "cache_hit_ratio": "cache hit ratio"}


def _what_changed(item: dict[str, Any]) -> str:
    """'latency up 3.2x', 'latency up 2.1x on 18 queries at once', 'it stopped running'."""
    type, measurement = item["type"], item.get("measurement") or {}
    if type == "monitoring_issue":
        return "the monitoring issue is resolved" if item["state"] == "resolved" or \
            item.get("status") == "back_to_normal" else "monitoring can't run until this is fixed"
    if type == "stuck_query":
        return "a query ran for over a minute" if item.get("status") in (
            "back_to_normal", "closed") else "a query has been running for over a minute"
    if type == "connections_near_max":
        return "connections are near the limit"
    if type == "query_stopped_running":
        return f"{RULE_PHRASES.get(item.get('relevance_rule') or '', 'a query')} stopped running"
    if type == "new_heavy_query":
        return "a new query carrying real load"
    word = METRIC_WORDS.get(measurement.get("metric"))
    size = change_text(measurement.get("change")).replace(".0x", "x")
    move = f"{word} {measurement.get('direction', 'up')} {size}" if word and size else None
    if type == "many_queries_changed":
        count = len(item.get("query_keys") or [])
        return f"{move} on {count} {'query' if count == 1 else 'queries'} at once" if move else f"{count} {'query' if count == 1 else 'queries'} changed at once"
    return move or item["title"][:1].lower() + item["title"][1:]


def band_reason(item: dict[str, Any], body: dict[str, Any], status: str) -> str:
    """One short line on why the item sits where it does: 'Act now: latency up 3.2x on a top-10
    query today, still happening', 'Watch: a routine burst, back to normal'."""
    band = item["severity_band"]
    label = ("Back to normal" if status == "back_to_normal" else "Closed"
             if status == "closed" else BAND_LABELS.get("watch" if band == "fyi" else band, "Watch"))
    if item["type"] == "daily_summary":
        return f"{label}: the daily report is ready"
    routine = routine_repeat(body)
    what = "a routine burst" if routine else _what_changed(item)
    rule = RULE_PHRASES.get(item.get("relevance_rule") or "")
    if rule and not routine and len(item.get("query_keys") or []) == 1 and item["type"] not in (
            "query_stopped_running", "new_heavy_query"):
        what = f"{what} on {rule}"
    state = {"ongoing": "still happening", "back_to_normal": "back to normal"}.get(status)
    if item["type"] == "query_stopped_running" and status == "ongoing":
        state = None
    if band == "recovered" or not state:
        return f"{label}: {what}"
    return f"{label}: {what}, {state}"


def settle_status(item: dict[str, Any], body: dict[str, Any],
                  incident: dict[str, Any] | None) -> dict[str, Any]:
    """The item with its status, reason line and `incident`, its band, latest occurrence and
    incident made to agree: an open item measured back to normal reads green, never Act now
    or "Still happening"; RDST closes it by the usual rules. The incident is copied, never
    changed in place (windows are cached)."""
    status, at = item_status(item, body, incident)
    item = {**item, "status": status, "status_at": at}
    if status == "back_to_normal":
        item["severity_band"], item["tone"] = "recovered", "positive"
        occurrences = item.get("occurrences") or []
        if occurrences and occurrences[0]["end"] is None:
            item["occurrences"] = [{**occurrences[0], "end": at}, *occurrences[1:]]
    if status in ("back_to_normal", "closed"):
        if item["type"] == "stuck_query":
            item["summary"] = "Ran for over a minute."
        if incident is not None and incident.get("ongoing"):
            incident = {**incident, "ongoing": False, "end": incident.get("end") or at}
    item["incident"] = incident
    item["band_reason"] = band_reason(item, body, status)
    return item


def item_occurrences(body: dict[str, Any]) -> list[dict[str, Any]]:
    """A repeating item's occurrences, newest first: when, how big, how many queries."""
    entries = body.get("occurrences")
    if not isinstance(entries, list) or len(entries) < 2:
        return []
    return [{"start": int(entry["start"]),
             "end": int(entry["end"]) if entry.get("end") is not None else None,
             "size": entry.get("size"), "query_count": len(entry.get("keys") or []),
             "quiet": bool(entry.get("quiet")),
             "verdict": entry.get("verdict") if entry.get("verdict") in (
                 "usual", "bigger") else None}
            for entry in reversed(entries) if isinstance(entry, dict) and entry.get("start")]


def repeat_title(top: str | None, count: int, entries: list[dict[str, Any]], now: int) -> str:
    """'COUNT on orders and 3 more queries burst 14 times today'."""
    from shared.monitor.monitor_store import local_day_bounds

    who = (f"{top} and {count - 1} more {'query' if count == 2 else 'queries'}"
           if top and count > 1 else top or f"{count} {'query' if count == 1 else 'queries'}")
    today = local_day_bounds(now)[0]
    first = min(entry["start"] for entry in entries)
    when = ("today" if first >= today else "since yesterday" if first >= today - 86400
            else f"in {(today - first) // 86400 + 1} days")
    return f"{who} burst {len(entries)} times {when}"


def recurring_note(body: dict[str, Any]) -> dict[str, Any] | None:
    """A member's usual schedule when this burst broke it (bigger or longer than usual)."""
    notes = body.get("recurring")
    if not isinstance(notes, dict):
        return None
    for note in notes.values():
        if isinstance(note, dict) and note.get("verdict") in ("bigger", "longer"):
            return {name: note.get(name) for name in
                    ("kind", "period_s", "repeats", "last_onset", "verdict")}
    return None


def trend_metric(type: str) -> str:
    """The subject metric an item's trend plots: latency in ms, traffic in calls a second,
    rollbacks a minute, the cache hit ratio as 0..1, or active connections."""
    if type in ("traffic_spike", "query_stopped_running"):
        return "qps"
    if type == "rollback_or_deadlock_spike":
        return "rollbacks"
    if type == "cache_hit_ratio_drop":
        return "cache_hit_ratio"
    if type == "connections_near_max":
        return "active_conns"
    return "mean_ms"


# Incident windows (DESIGN_V5 section 3): when the change began and when it ended

INCIDENT_LOOKBACK_S = 3 * 3600
INCIDENT_LOOKAHEAD_S = 6 * 3600
# The change must show in the readings this close before filing.
INCIDENT_NEAR_S = 20 * 60
# A run of changed readings holds through this many ordinary ones.
INCIDENT_GAP = 3
# This many ordinary readings in a row end it.
INCIDENT_QUIET = 10
# A smaller move is noise, not the change an item is about (CONTRACT: significant).
INCIDENT_MIN_CHANGE = 0.2
# A measured factor needs this many calls in the hour before the onset.
INCIDENT_MIN_CALLS = 30


def incident_signal(type: str) -> tuple[str, str] | None:
    """The per-query metric ('mean_ms' or 'qps') and direction an item's window follows."""
    if type in SLOWER_KINDS:
        return "mean_ms", "up"
    if type in FASTER_KINDS:
        return "mean_ms", "down"
    if type in BUSIER_KINDS:
        return "qps", "up"
    if type in STOPPED_KINDS:
        return "qps", "down"
    return None


def group_is_latency(item: dict[str, Any]) -> bool:
    """Whether a presented group item is about latency (else about calls)."""
    measurement = item.get("measurement")
    if isinstance(measurement, dict):
        return measurement.get("metric") == "latency"
    return not item["title"].startswith("Traffic") and "stopped" not in item["title"]


def incident_levels(type: str, body: dict[str, Any], key: str, metric: str,
                    stats: dict[str, Any]) -> tuple[float | None, float | None]:
    """The before and after levels of one query's change, the same numbers the item
    shows: from filing for single items, per member for group items."""
    if type == "many_queries_changed":
        numbers = _member_numbers(key, body, stats)
        return ((numbers["before_ms"], numbers["after_ms"]) if metric == "mean_ms" else
                (numbers["before_qps"], numbers["after_qps"]))
    now, base = _numbers(body), _baseline(body)
    if type == "new_heavy_query":
        return 0.0, now.get("qps")
    if type in STOPPED_KINDS:
        return base.get("qps"), 0.0
    if metric == "qps":
        return base.get("qps"), now.get("qps")
    return base.get("mean_ms", body.get("baseline_mean_ms")), now.get("mean_ms")


def _smooth(values: list[float | None], metric: str, radius: int = 2) -> list[float | None]:
    """Centered smoothing over 2 * radius + 1 buckets: mean calls a second, median latency.
    Unread buckets (and latency buckets without calls) stay empty."""
    out: list[float | None] = []
    for index, value in enumerate(values):
        near = [v for v in values[max(0, index - radius):index + radius + 1] if v is not None]
        if value is None or not near:
            out.append(None)
        elif metric == "qps":
            out.append(sum(near) / len(near))
        else:
            ordered = sorted(near)
            out.append(ordered[len(ordered) // 2])
    return out


def _incident_level(direction: str, before: float | None, after: float | None) -> float | None:
    """The reading that separates changed from ordinary: the geometric middle of the
    before and after levels. None when the numbers show no change that way."""
    if before is None or after is None or before < 0 or after < 0:
        return None
    if direction == "up":
        if after <= before:
            return None
        return 0.0 if before == 0 else math.sqrt(before * after)
    if before == 0 or after >= before:
        return None
    return before * 0.25 if after == 0 else math.sqrt(before * after)


def incident_span(points: list[tuple[int, float | None]], filed_at: int, *, metric: str,
                  direction: str, before: float | None, after: float | None,
                  step: int = 60) -> dict[str, Any] | None:
    """Onset and end of one query's change, from its per-bucket readings around filing.

    `points` holds one (bucket start, value) per step, oldest first, value None when
    nothing was read. The onset is the first bucket of the run of changed readings that
    reaches filing; the end is the bucket after the last changed one, once INCIDENT_QUIET
    ordinary readings follow it. `end` is None while the change lasts. None when the
    readings don't show the change near filing.
    """
    level = _incident_level(direction, before, after)
    if level is None or not points:
        return None
    stamps = [ts for ts, _ in points]
    values = _smooth([value for _, value in points], metric)

    def changed(value: float | None) -> bool:
        return value is not None and (value > level if direction == "up" else value < level)

    def near_filing(values: list[float | None]) -> int | None:
        for index in range(len(points) - 1, -1, -1):
            if stamps[index] > filed_at:
                continue
            if stamps[index] < filed_at - INCIDENT_NEAR_S:
                break
            if changed(values[index]):
                return index
        return None

    anchor = near_filing(values)
    if anchor is None and metric == "mean_ms":
        # A latency spike can last a minute or two, which the median smooths away.
        values = [value for _, value in points]
        anchor = near_filing(values)
    if anchor is None:
        return None
    if direction == "up" and before == 0:
        # A new query: it began with its first reading.
        start = next(index for index in range(anchor + 1) if changed(values[index]))
    else:
        start, ordinary = anchor, 0
        for index in range(anchor - 1, -1, -1):
            if values[index] is None:
                continue
            if changed(values[index]):
                start, ordinary = index, 0
            else:
                ordinary += 1
                if ordinary > INCIDENT_GAP:
                    break
    end, last, ordinary = None, anchor, 0
    for index in range(anchor + 1, len(points)):
        if values[index] is None:
            continue
        if changed(values[index]):
            last, ordinary = index, 0
        else:
            ordinary += 1
            if ordinary >= INCIDENT_QUIET:
                end = last
                break
    # Smoothing blurs the edges by a bucket or two; the raw readings place them.
    raw = [value for _, value in points]
    smoothed_start = start
    while start < anchor and not changed(raw[start]):
        start += 1
    while start > max(0, smoothed_start - 2) and changed(raw[start - 1]):
        start -= 1
    if end is not None:
        smoothed_end = end
        while end > start and not changed(raw[end]):
            end -= 1
        while end + 1 < min(len(raw), smoothed_end + 3) and changed(raw[end + 1]):
            end += 1
        end = stamps[end] + step
    change = None
    if before:
        measured = _measured_change(points, stamps[start], end or filed_at + step, step,
                                    metric)
        if measured is not None and abs(measured) < INCIDENT_MIN_CHANGE:
            # The hour before the onset may already be changed: a noisy (sparse) query's
            # run breaks up minute by minute. Its broader trend places the onset.
            earlier = _earlier_onset(points, start, metric, changed)
            if earlier is not None:
                # The trend blurs the edge; the first changed raw reading places it.
                while earlier < start and not changed(raw[earlier]):
                    earlier += 1
                start = earlier
                measured = _measured_change(points, stamps[start], end or filed_at + step,
                                            step, metric)
        change = measured if measured is not None else after / before - 1
        if abs(change) < INCIDENT_MIN_CHANGE:
            # Too small to be the change the item is about: the readings don't show it.
            return None
    return {"start": stamps[start], "end": end, "change": change}


def first_changed_at(points: list[tuple[int, float | None]], *, metric: str, direction: str,
                     before: float | None, after: float | None, begin: int,
                     until: int) -> int | None:
    """The first bucket in [begin, until] whose smoothed reading shows the change: where a
    member that joined at an unrecorded time is anchored."""
    level = _incident_level(direction, before, after)
    if level is None:
        return None
    raw = [value for _, value in points]

    def first(values: list[float | None]) -> int | None:
        return next((ts for (ts, _), value in zip(points, values) if begin <= ts <= until and
                     value is not None and (value > level if direction == "up"
                                            else value < level)), None)

    found = first(_smooth(raw, metric))
    # A latency spike can last a minute or two, which the median smooths away.
    return first(raw) if found is None and metric == "mean_ms" else found


# The broader trend a noisy run is judged by, in buckets either side.
INCIDENT_TREND_RADIUS = 7


def _earlier_onset(points: list[tuple[int, float | None]], start: int, metric: str,
                   changed: Callable[[float | None], bool]) -> int | None:
    """An earlier onset index when the hour before `start` reads as changed: the first
    bucket of the run of changed readings, on the smoothed trend, that reaches `start`."""
    stamps = [ts for ts, _ in points]
    prior = [value for ts, value in points
             if stamps[start] - 3600 <= ts < stamps[start] and value is not None]
    level = (sum(prior) / len(prior) if metric == "qps" else _middle(prior)) if prior else None
    if not changed(level):
        return None
    trend = _smooth([value for _, value in points], metric, INCIDENT_TREND_RADIUS)
    earliest, ordinary = start, 0
    for index in range(start - 1, -1, -1):
        if trend[index] is None:
            continue
        if changed(trend[index]):
            earliest, ordinary = index, 0
        else:
            ordinary += 1
            if ordinary > INCIDENT_GAP:
                break
    return earliest if earliest < start else None


def _middle(values: list[float]) -> float | None:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else None


def _measured_change(points: list[tuple[int, float | None]], start: int, end: int,
                     step: int, metric: str) -> float | None:
    """The change as the readings show it: the level during the incident over the level
    in the hour before it. Call rates average (sparse queries have many empty minutes);
    latency takes the median. None when the hour before holds too few calls to say."""
    def level(values: list[float]) -> float | None:
        if metric == "qps":
            return sum(values) / len(values) if values else None
        return _middle(values)

    prior = [value for ts, value in points if start - 3600 <= ts < start and value is not None]
    if metric == "qps" and sum(prior) * step < INCIDENT_MIN_CALLS:
        return None
    if metric != "qps" and len(prior) < INCIDENT_MIN_CALLS // 3:
        return None
    before = level(prior)
    during = level([value for ts, value in points
                    if start <= ts < max(end, start + step) and value is not None])
    if not before or during is None:
        return None
    return during / before - 1


# Report presentation (brief 5.9)

HEALTH_LABELS = {"cache_hit_ratio": "Cache hit ratio", "deadlocks": "Deadlocks",
                 "rollbacks": "Rollbacks", "connections": "Connections",
                 "vacuum_bloat": "Vacuum and bloat", "vacuum bloat": "Vacuum and bloat",
                 "index_health": "Index health", "index health": "Index health",
                 "config_audit": "Configuration", "config audit": "Configuration",
                 "replication": "Replication", "index_churn": "Index churn"}
_STATUS = {"ok": "ok", "healthy": "ok", "good": "ok", "pass": "ok", "passed": "ok",
           "warn": "warn", "warning": "warn", "attention": "warn", "degraded": "warn",
           "bad": "bad", "critical": "bad", "error": "bad", "unhealthy": "bad", "fail": "bad",
           "failed": "bad"}


def health_label(check: str) -> str:
    raw = check.strip()
    key = raw.lower()
    if key in HEALTH_LABELS:
        return HEALTH_LABELS[key]
    if key.startswith("config:"):
        return "Configuration: " + raw.split(":", 1)[1].strip()
    words = raw.replace("_", " ").strip()
    return words[:1].upper() + words[1:].lower() if words else "Check"


def health_status(status: str) -> str:
    return _STATUS.get(status.strip().lower(), "info")


def health_value(check: str, computed: dict[str, Any]) -> str:
    database = ((computed.get("deltas") or {}).get("24h") or {}).get("database") or {}
    key = check.strip().lower()
    metric = database.get(key) if isinstance(database, dict) else None
    current = metric.get("current") if isinstance(metric, dict) else None
    if key == "cache_hit_ratio" and isinstance(current, (int, float)):
        # Round down so a ratio just under 1 never reads as a perfect 100%.
        return f"{math.floor(current * 10000 + 1e-6) / 100:.2f}%"
    if key in ("deadlocks", "xact_rollback", "rollbacks") and isinstance(current, (int, float)):
        return f"{current:.0f} in 24 h"
    if key == "connections":
        collector = (computed.get("health_collectors") or {}).get("connections") or {}
        total, limit = collector.get("total_connections"), collector.get("max_connections")
        if total is not None and limit:
            return f"{total} of {limit}"
    return ""


_DANGLING = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on", "or",
             "the", "to", "with"}


def fix_title(action: str) -> str:
    """Fallback title: the first clause of the action, at most eight words."""
    clause = re.split(r"[.;:,(]| - ", collapse(action), maxsplit=1)[0].strip()
    words = clause.split()[:8]
    while words and words[-1].lower() in _DANGLING:
        words.pop()
    title = " ".join(words) if words else "Review this query"
    return title[:1].upper() + title[1:]


def query_refs(keys: set[str], texts: dict[str, str],
               names: dict[str, str] | None = None) -> dict[str, dict[str, str]]:
    names = names or {}
    return {key: {"display_name": names.get(key) or display_name(texts.get(key, "")),
                  "text_snippet": snippet(texts.get(key, ""))}
            for key in sorted(keys) if key in texts or key in names}


def mentioned_keys(value: Any, known: set[str]) -> set[str]:
    """Twelve-hex query keys in any string of a report, including {{q:key}} tokens."""
    found: set[str] = set()
    if isinstance(value, str):
        found.update(key for key in HEX_KEY.findall(value) if key in known)
    elif isinstance(value, dict):
        for item in value.values():
            found |= mentioned_keys(item, known)
    elif isinstance(value, list):
        for item in value:
            found |= mentioned_keys(item, known)
    return found

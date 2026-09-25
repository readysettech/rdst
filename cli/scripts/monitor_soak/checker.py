"""Poll monitor evidence and assess each soak incident against deadlines."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

from common import INDEX, LEDGER, LOG_DIR, ROOT, heavy_sql

LOG = logging.getLogger(__name__)
STOP = False
REPORT = ROOT / "docs/plans/continuous_analysis/soak_report.md"
SUMMARY = ROOT / "docs/plans/continuous_analysis/soak_summary.json"
EVIDENCE = LOG_DIR / "checker_evidence.json"
ARCHIVE = LOG_DIR / "incidents.archive.jsonl"
MATCH = {
    "index_regression": ("from orders where total", "order by total"),
    "new_heavy_query": ("join orders o on o.customer_id", "group by c.country"),
    "traffic_spike": ("from orders where total", "order by total"),
    "query_stopped_running": ("from customers where country",),
    "stuck_query": ("pg_sleep",),
    "table_slowdown": ("from orders where total",),
}
EXPECTED = {
    "index_regression": {"latency_regression", "plan_change"},
    "new_heavy_query": {"new_heavy_query"},
    "traffic_spike": {"traffic_spike"},
    "query_stopped_running": {"query_stopped_running"},
    "stuck_query": {"stuck_query"},
    "table_slowdown": {"table_slowdown", "latency_regression", "plan_change",
                       "many_queries_changed"},
}
# Delivery levels each incident may reach (NOTIFICATION_MODEL.md). A regression or a new
# top-10 query is visible (notify when Jev says so), a traffic spike is worth a look but
# never pushes, and a stopped query stays in the digest unless it carried load.
DELIVERY = {
    "index_regression": {"notify", "inbox"},
    "new_heavy_query": {"notify", "inbox"},
    "traffic_spike": {"inbox"},
    "query_stopped_running": {"inbox", "digest"},
    "stuck_query": {"notify", "inbox"},
    "table_slowdown": {"notify", "inbox"},
}
# The inbox no longer carries index attribution or report-only findings.
RETIRED_TYPES = {"fix_available", "fix_confirmed", "fix_no_effect", "one_fix_several_queries"}
DATABASE_TYPES = {"rollback_or_deadlock_spike", "cache_hit_ratio_drop"}
DEFAULT_PUSH_CAP = 3
DETECT_DEADLINE = 30 * 60
RECOVER_DEADLINE = 40 * 60


def read_ledger(path: Path = LEDGER, *, include_archive: bool = False) -> list[dict]:
    lines = []
    for source in ([ARCHIVE, path] if include_archive else [path]):
        try:
            lines.extend(source.read_text(encoding="ascii").splitlines())
        except OSError:
            continue
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except ValueError:
            LOG.warning("Ignoring partial ledger line")
    return events


def incidents(events: list[dict], *, current_run_only: bool = False) -> list[dict]:
    if current_run_only:
        starts = [event for event in events if event.get("phase") == "start"]
        if starts:
            run_id = max(starts, key=lambda event: event["ts"]).get("run_id")
            events = [event for event in events if event.get("run_id") == run_id]
    by_id = {}
    for event in events:
        key = (event.get("run_id"), event.get("cycle"), event.get("kind"))
        if event.get("phase") == "start":
            by_id[key] = {"run_id": key[0], "cycle": key[1], "kind": key[2],
                          "start": event["ts"], "end": None}
        elif event.get("phase") == "end" and key in by_id:
            by_id[key]["end"] = event["ts"]
    return sorted(by_id.values(), key=lambda item: item["start"])


def sql_matches(kind: str, sql: str) -> bool:
    normalized = " ".join(sql.lower().split())
    return all(fragment in normalized for fragment in MATCH[kind])


def matching_keys(kind: str, snapshot: dict, start: int, end: int,
                  *, cycle: int | None = None, run_id: str | None = None) -> set[str]:
    heavy_select = None
    if kind == "new_heavy_query" and cycle is not None and run_id is not None:
        heavy_select = "".join(heavy_sql(cycle, run_id).split(" FROM customers c ", 1)[0]
                               .lower().split())

    def matches(sql: str) -> bool:
        normalized = " ".join(sql.lower().split())
        return (sql_matches(kind, sql) and
                (heavy_select is None or "".join(normalized.split()).startswith(
                    heavy_select + "fromcustomersc")))

    keys = {str(row["key"]) for row in snapshot["queries"]
            if matches(row.get("text", ""))}
    for run in snapshot["runs"]:
        request = run.get("request") or {}
        for row in request.get("queries", []):
            if matches(row.get("normalized_sql", "")):
                keys.add(str(row["query_key"]))
    if kind == "stuck_query":
        keys.update(str(row["query_key"]) for row in snapshot.get("stuck_samples", [])
                    if start <= row["ts"] <= end and row.get("query_key"))
    return keys


def low_impact_ignore(kind: str, run: dict, keys: set[str]) -> bool:
    """Accept an explicit Jev ignore only when the measured DB-time impact is small."""
    if kind not in {"traffic_spike", "query_stopped_running"}:
        return False
    request = run.get("request") or {}
    verdict = run.get("verdict") or {}
    ignored = {row.get("query_key") for row in verdict.get("queries", [])
               if row.get("attention") == "ignore"}
    for query in request.get("queries", []):
        if query.get("query_key") not in keys & ignored:
            continue
        current = query.get("current_5m") or {}
        baseline = (query.get("baseline") or {}).get("1h") or {}
        if kind == "traffic_spike":
            if (baseline.get("qps", 0) > 0 and
                    current.get("qps", 0) > baseline["qps"] * 1.2 and
                    query.get("extra_db_time_ms_per_5m_vs_1h", float("inf")) <= 1000):
                return True
        elif (current.get("calls") == 0 and baseline.get("qps", 0) >= 1 / 60 and
              baseline.get("qps", 0) * baseline.get("mean_ms", float("inf")) * 300 <= 1000):
            return True
    return False


def gated_by_model(kind: str, runs: list[dict], keys: set[str]) -> str | None:
    """Why the attention gate kept this incident from Jev, when it did: "small" (the query
    carries too little load), "minor" (it moved less than the model asks) or "recurring"
    (the database's usual schedule). Each is the notification model working, not a miss.
    A stuck query is never gated."""
    if kind == "stuck_query":
        return None
    for run in runs:
        for blob in (run.get("request"), run.get("verdict")):
            gate = (blob or {}).get("gate") if isinstance(blob, dict) else None
            if not isinstance(gate, dict):
                continue
            for reason, field in (("recurring", "recurring"), ("small", "smaller"),
                                  ("minor", "minor"), ("intermittent", "intermittent")):
                if keys & set(gate.get(field) or []):
                    return reason
    return None


def assess(incident: dict, snapshot: dict, now: int,
           *, judge_interval_s: int = 300) -> dict:
    """Pure assertion logic; missing evidence stays pending until its deadline."""
    kind, start, end = incident["kind"], incident["start"], incident["end"]
    keys = matching_keys(kind, snapshot, start, end or now,
                         cycle=incident.get("cycle"), run_id=incident.get("run_id"))
    def expected_for(key: str) -> set[str]:
        return EXPECTED[kind] - {"many_queries_changed"}

    detection_due = start + DETECT_DEADLINE
    if kind == "query_stopped_running":
        # The 30-minute absence is only eligible at the next judge pass.
        detection_due += judge_interval_s + 60
    relevant_runs = [run for run in snapshot["runs"]
                     if start <= run["ts"] <= min(now, detection_due)]
    shortlisted = any(str(row.get("query_key")) in keys for run in relevant_runs
                      for row in run.get("shortlist", []))
    if kind == "table_slowdown":
        shortlisted = any(len({str(row.get("query_key")) for row in run.get("shortlist", [])}
                              & keys) >= 2 for run in relevant_runs)
    matching_items = [item for item in snapshot["items"] if item.get("target") == "localdev"
                      and item.get("subject") in keys
                      and item.get("type") in expected_for(item["subject"])]
    item_by_id = {item["id"]: item for item in matching_items}
    group_ids = {item["id"] for item in snapshot["items"]
                 if item.get("target") == "localdev" and
                 item.get("type") == "many_queries_changed"}
    group_events = sorted((event for event in snapshot.get("item_events", [])
                           if event["item_id"] in group_ids), key=lambda event: event["ts"])
    def group_keys(event: dict) -> set[str]:
        detail = event.get("detail_json") or "{}"
        try:
            return set(json.loads(detail).get("query_keys", []))
        except (TypeError, ValueError):
            return set()

    def retired_group_keys(event: dict) -> set[str]:
        try:
            return set(json.loads(event.get("detail_json") or "{}").get(
                "recovered_queries", {}))
        except (TypeError, ValueError):
            return set()

    def qualifying_verdict_at(ts: int, grouped_keys: set[str]) -> bool:
        for run in relevant_runs:
            if run["ts"] != ts:
                continue
            answers = (run.get("verdict") or {}).get("queries", [])
            qualifying = {str(answer.get("query_key")) for answer in answers
                          if answer.get("attention") in ("inbox", "notify") and
                          answer.get("kind") != "stuck_query"}
            affected = {str(answer.get("query_key")) for answer in answers
                        if str(answer.get("query_key")) in keys & grouped_keys and
                        answer.get("kind") in expected_for(str(answer.get("query_key"))) and
                        answer.get("attention") in ("inbox", "notify")}
            if len(qualifying) >= 2 and len(affected) >= (2 if kind == "table_slowdown" else 1):
                return True
        return False

    group_filing = next((event for event in group_events
                         if start <= event["ts"] <= min(now, detection_due) and
                         event["kind"] in ("created", "updated", "worse", "reopened") and
                         len(keys & group_keys(event)) >= (2 if kind == "table_slowdown" else 1) and
                         qualifying_verdict_at(event["ts"], group_keys(event))), None)
    filed = any(start <= event["ts"] <= min(now, detection_due) and
                event["item_id"] in item_by_id and
                event["kind"] in ("created", "updated", "worse", "reopened") and
                not (event["kind"] == "updated" and
                     item_by_id[event["item_id"]].get("state") == "resolved" and
                     (item_by_id[event["item_id"]].get("resolved_at") or 0) < event["ts"])
                for event in snapshot.get("item_events", []))
    if "item_events" not in snapshot:
        filed = any(start <= item.get("created_at", 0) <= min(now, detection_due) or
                    (start <= item.get("updated_at", 0) <= min(now, detection_due) and
                     not (item.get("state") == "resolved" and
                          (item.get("resolved_at") or 0) < item.get("updated_at", 0)))
                    for item in matching_items)
    filed = filed or group_filing is not None
    recovered = bool(end and (not filed or any(item.get("target") == "localdev" and
                                 item.get("subject") in keys and
                                 item.get("type") in expected_for(item["subject"]) and
                                 item.get("state") == "resolved" and
                                 (item.get("resolved_at") or 0) >=
                                 (end - 60 if kind == "stuck_query" else end) and
                                 (item.get("resolved_at") or 0) <= end + RECOVER_DEADLINE
                                 for item in matching_items + snapshot.get("resolutions", []))))
    if end and group_filing is not None and not recovered:
        # A database-wide episode can stay open after this incident's members retire.
        episode_keys = keys & group_keys(group_filing)
        recovered = any(end <= event["ts"] <= end + RECOVER_DEADLINE and
                        event["ts"] > group_filing["ts"] and
                        event["item_id"] == group_filing["item_id"] and
                        (event["kind"] == "resolved" or
                         event["kind"] in ("updated", "worse") and
                         episode_keys <= retired_group_keys(event))
                        for event in group_events)
    verdict = any(any(str(row.get("query_key")) in keys and
                      row.get("kind") in expected_for(str(row.get("query_key")))
                      and row.get("attention") in ("inbox", "notify")
                      for row in (run.get("verdict") or {}).get("queries", []))
                  for run in relevant_runs)
    if kind == "table_slowdown":
        verdict = any(len({str(row.get("query_key")) for row in
                           (run.get("verdict") or {}).get("queries", [])
                           if str(row.get("query_key")) in keys and
                           row.get("kind") in expected_for(str(row.get("query_key"))) and
                           row.get("attention") in ("inbox", "notify")}) >= 2
                      for run in relevant_runs)
        filed_subjects = {item_by_id[event["item_id"]]["subject"]
                          for event in snapshot.get("item_events", [])
                          if event["item_id"] in item_by_id and
                          start <= event["ts"] <= min(now, detection_due) and
                          event["kind"] in ("created", "updated", "worse", "reopened")}
        filed = group_filing is not None or len(filed_subjects) >= 2
        if end and not filed:
            recovered = True
        if end and filed and group_filing is None:
            recovered = all(any(item.get("subject") == key and
                                item.get("state") == "resolved" and
                                end <= (item.get("resolved_at") or 0) <= end + RECOVER_DEADLINE
                                for item in matching_items + snapshot.get("resolutions", []))
                            for key in filed_subjects)
    ignored_low_impact = any(low_impact_ignore(kind, run, keys) for run in relevant_runs)
    gated = None if filed or shortlisted else gated_by_model(kind, relevant_runs, keys)
    if end and gated and not filed:
        # Nothing was filed, so nothing has to close.
        recovered = True
    dry_run = any(run.get("dry_run") and run.get("outcome") == "dry_run"
                  for run in relevant_runs)
    if end and dry_run and verdict and not filed:
        # Dry runs do not create an item that could later close.
        recovered = True
    login_error = any(run.get("outcome") == "error" and
                      (run.get("verdict") or {}).get("code") == "LOGIN_REQUIRED" and
                      any(str(row.get("query_key")) in keys for row in run.get("shortlist", []))
                      for run in relevant_runs)
    changes = snapshot["changes"]
    index_seen = True
    if kind in ("index_regression", "table_slowdown"):
        dropped = any(c["index_name"] == INDEX and c["kind"] == "dropped" and
                      start <= c["ts"] <= now for c in changes)
        added = bool(end and any(c["index_name"] == INDEX and c["kind"] == "added" and
                                 end <= c["ts"] <= now for c in changes))
        index_seen = dropped and (not end or added)
    filed_items = [item for item in matching_items
                   if start <= item.get("updated_at", 0) and item.get("state") != "dismissed"]
    group_items = [item for item in snapshot["items"]
                   if group_filing is not None and item.get("id") == group_filing["item_id"]]
    allowed = DELIVERY[kind] | ({"notify"} if group_items else set())
    delivered = all(item.get("delivery", "inbox") in allowed
                    for item in filed_items + group_items)
    duplicate_keys = [key for key, count in Counter(
        (item.get("target"), item.get("type"), item.get("subject"))
        for item in snapshot["items"]).items() if count > 1]
    # A dry run still needs a shortlist and a recorded fixed-choice verdict.
    observed = filed or shortlisted and (verdict and dry_run or ignored_low_impact) or bool(gated)
    checks = {"index_change": index_seen, "shortlist": shortlisted or bool(gated),
              "inbox_or_dry_run_verdict": observed, "delivery": delivered,
              "recovered": recovered, "no_duplicates": not duplicate_keys}
    if kind == "table_slowdown":
        # A full five-minute series must be available at a judge cycle regardless
        # of where the incident starts relative to that cycle and the collector.
        checks["judge_coverage"] = end is None or end - start >= judge_interval_s + 300 + 60
        checks["multi_query"] = len(keys) >= 2
        if gated:
            checks["inbox_or_dry_run_verdict"] = True
    if kind == "stuck_query":
        checks.pop("shortlist")
        checks["stuck_sample"] = any(start <= row["ts"] <= (end or now)
                                     and str(row.get("query_key")) in keys
                                     for row in snapshot.get("stuck_samples", []))
        checks["stuck_item"] = filed
    recovery_due = (end + RECOVER_DEADLINE) if end else None
    failed = (now >= detection_due and not all(checks[name] for name in checks if name != "recovered"))
    failed |= bool(recovery_due and now >= recovery_due and not recovered and filed)
    if (kind != "stuck_query" and login_error and shortlisted and index_seen and
            not duplicate_keys and not filed and not verdict):
        status = "auth_required"
    elif failed:
        status = "fail"
    elif all(checks.values()) and end:
        status = "pass"
    else:
        status = "pending"
    return {**incident, "status": status, "checks": checks,
            "query_keys": sorted(keys), "login_required": login_error,
            "gated": gated,
            "ignored_low_impact": ignored_low_impact and not filed,
            "detection_deadline": detection_due, "recovery_deadline": recovery_due}


def model_violations(items: list[dict], since: int) -> list[str]:
    """Items filed since `since` that the notification model forbids."""
    found = []
    for item in items:
        if item.get("created_at", 0) < since:
            continue
        label = f"item {item.get('id')} {item.get('type')}"
        if item.get("type") in RETIRED_TYPES:
            found.append(f"{label}: index attribution or report-only finding")
        elif item.get("type") in DATABASE_TYPES:
            try:
                numbers = json.loads(item.get("body_json") or "{}").get("numbers") or {}
            except ValueError:
                numbers = {}
            if item.get("subject") != "database":
                found.append(f"{label}: database-wide kind filed against one query")
            elif (item["type"] == "rollback_or_deadlock_spike" and
                  numbers.get("rollbacks_per_min", 0) < 1 and
                  not numbers.get("deadlocks_per_min")):
                found.append(f"{label}: no measured rollback or deadlock rate")
    return found


def notification_count(data_dir: Path, now: int) -> int | None:
    path = data_dir / "monitor.db"
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as connection:
            return connection.execute(
                "SELECT count(*) FROM inbox_event e JOIN inbox_item i ON i.id=e.item_id "
                "WHERE i.target='localdev' AND e.kind='notified' AND e.ts>=? AND e.ts<?",
                (now // 86400 * 86400, now // 86400 * 86400 + 86400)).fetchone()[0]
    except (OSError, sqlite3.Error) as exc:
        LOG.warning("Notification count unavailable: %s", exc)
        return None


def fetch(base: str, data_dir: Path) -> dict:
    paths = {"items": "/api/monitor/inbox?target=localdev&include_digest=1",
             "runs": "/api/monitor/targets/localdev/judge-runs?limit=100",
             "changes": "/api/monitor/targets/localdev/index-changes",
             "jobs": "/api/monitor/targets/localdev/analyze-jobs",
             "queries": "/api/monitor/targets/localdev/queries?window=24h"}
    keys = {"items": "items", "runs": "runs", "changes": "changes",
            "jobs": "jobs", "queries": "queries"}
    snapshot = {}
    for name, path in paths.items():
        with urllib.request.urlopen(base.rstrip("/") + path, timeout=8) as response:
            snapshot[name] = json.load(response)[keys[name]]
    with urllib.request.urlopen(base.rstrip("/") + "/api/monitor/targets/localdev/settings",
                                timeout=8) as response:
        snapshot["push_cap"] = json.load(response).get("push_daily_cap", DEFAULT_PUSH_CAP)
    with sqlite3.connect(f"file:{data_dir / 'monitor.db'}?mode=ro", uri=True, timeout=2) as conn:
        conn.row_factory = sqlite3.Row
        snapshot["stuck_samples"] = [dict(row) for row in conn.execute(
            "SELECT ts,query_key FROM stuck_sample WHERE target='localdev' AND query_key IS NOT NULL")]
        snapshot["query_first_seen"] = {row["query_key"]: row["first_seen"]
                                        for row in conn.execute(
                                            "SELECT query_key,first_seen FROM query_text "
                                            "WHERE target='localdev'")}
        snapshot["item_events"] = [dict(row) for row in conn.execute(
            "SELECT e.item_id,e.ts,e.kind,e.detail_json FROM inbox_event e "
            "JOIN inbox_item i ON i.id=e.item_id WHERE i.target='localdev'")]
    return snapshot


def merge_evidence(previous: dict | None, current: dict) -> dict:
    """Keep older judge runs and resolved states beyond the API's 100-run page."""
    old = previous or {}
    merged = dict(current)
    merged["runs"] = list({str(row["id"]): row for row in
                           old.get("runs", []) + current["runs"]}.values())
    merged["changes"] = list({str(row["id"]): row for row in
                              old.get("changes", []) + current["changes"]}.values())
    merged["queries"] = list({str(row["key"]): {"key": row["key"], "text": row.get("text", "")}
                              for row in old.get("queries", []) + current["queries"]}.values())
    merged["stuck_samples"] = list({(row["ts"], row.get("query_key")): row for row in
                                    old.get("stuck_samples", []) +
                                    current.get("stuck_samples", [])}.values())
    merged["query_first_seen"] = {**old.get("query_first_seen", {}),
                                  **current.get("query_first_seen", {})}
    merged["item_events"] = list({(row["item_id"], row["ts"], row["kind"]): row for row in
                                  old.get("item_events", []) +
                                  current.get("item_events", [])}.values())
    resolved = old.get("resolutions", []) + [item for item in current["items"]
                                              if item.get("state") == "resolved"]
    merged["resolutions"] = list({(str(item.get("id")), str(item.get("resolved_at"))): item
                                   for item in resolved}.values())
    return merged


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".{os.getpid()}.tmp")
    temp.write_text(content, encoding="ascii")
    os.replace(temp, path)


def report(results: list[dict], now: int, notifications: int | None,
           jobs: list[dict], error: str | None = None,
           duplicate_count: int = 0, judge_interval_s: int = 300,
           push_cap: int = DEFAULT_PUSH_CAP, violations: list[str] | None = None) -> None:
    run_ids = {row.get("run_id") for row in results}
    run_label = next(iter(run_ids)) if len(run_ids) == 1 else "multiple" if run_ids else "none"
    violations = violations or []
    cap_ok = notifications is not None and notifications <= push_cap
    failed = (notifications is not None and not cap_ok or duplicate_count > 0 or
              bool(violations) or any(row["status"] == "fail" for row in results))
    incomplete = (not results or error is not None or notifications is None or
                  any(row["status"] in ("pending", "auth_required") for row in results))
    overall = "fail" if failed else "incomplete" if incomplete else "pass"
    summary = {"checked_at": now, "api_error": error, "run_id": run_label,
               "judge_interval_s": judge_interval_s,
               "overall_status": overall,
               "duplicate_inbox_items": duplicate_count,
               "notification_count_today": notifications, "notification_cap_ok": cap_ok,
               "push_daily_cap": push_cap, "model_violations": violations,
               "analyze_jobs": len(jobs), "incidents": results}
    atomic_text(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    lines = ["# Continuous Analysis soak report", "",
             f"Updated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(now))}", "",
             f"Run: {run_label}", "",
             f"Judge interval: {judge_interval_s} seconds", "",
             f"API: {error or 'available'}", "",
             f"Overall: {overall}", "",
             f"Notifications today: {notifications if notifications is not None else 'unavailable'}"
             f" / {push_cap}",
             f"Duplicate inbox items: {duplicate_count}",
             f"Notification model violations: {len(violations)}",
             *(f"- {line}" for line in violations[:20]),
             f"Analyze jobs observed: {len(jobs)}", "",
             "| Cycle | Incident | Status | Start UTC | End UTC | Missing checks |",
             "| --- | --- | --- | --- | --- | --- |"]
    def stamp(ts):
        return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts)) if ts else "active"

    for row in results[-100:]:
        missing = ", ".join(key for key, value in row["checks"].items() if not value) or "none"
        if row.get("gated"):
            missing += f" (kept from Jev: {row['gated']})"
        lines.append(f"| {row['cycle']} | {row['kind']} | {row['status']} | "
                     f"{stamp(row['start'])} | {stamp(row['end'])} | {missing} |")
    lines += ["", "A sign-in error is reported as auth_required; it is not a judge verdict.",
              "Low-impact ignores require an explicit Jev ignore and at most 1 second of DB time per 5 minutes.",
              "An incident the attention gate kept from Jev (a small query, a minor move or a "
              "recurring burst) passes without an item; the report names the reason.",
              "Delivery: regressions and new top-10 queries are visible, traffic spikes never push, "
              "stopped queries may stay in the digest. No index attribution items.",
              "The notification count uses the local monitor.db in read-only mode.", ""]
    atomic_text(REPORT, "\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8797")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--judge-interval-s", type=int, default=int(
        os.environ.get("RDST_MONITOR_JUDGE_INTERVAL_S", "300")))
    parser.add_argument("--all-runs", action="store_true",
                        help="Include incidents from older soak runs in the report")
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("RDST_DATA_DIR", "~/.rdst-monitor-dev")).expanduser())
    args = parser.parse_args()
    if args.judge_interval_s < 1:
        parser.error("judge interval must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    global STOP
    signal.signal(signal.SIGTERM, lambda *_: globals().__setitem__("STOP", True))
    signal.signal(signal.SIGINT, lambda *_: globals().__setitem__("STOP", True))
    try:
        evidence = json.loads(EVIDENCE.read_text(encoding="ascii"))
    except (OSError, ValueError):
        evidence = None
    try:
        previous_results = json.loads(SUMMARY.read_text(encoding="ascii")).get("incidents", [])
    except (OSError, ValueError):
        previous_results = []
    last_results = previous_results
    while not STOP:
        now = int(time.time())
        error = None
        try:
            evidence = merge_evidence(evidence, fetch(args.api, args.data_dir))
            atomic_text(EVIDENCE, json.dumps(evidence, sort_keys=True) + "\n")
            last_results = [assess(item, evidence, now,
                                   judge_interval_s=args.judge_interval_s) for item in incidents(
                read_ledger(include_archive=args.all_runs), current_run_only=not args.all_runs)]
            previous_results = last_results
        except (OSError, ValueError, KeyError, urllib.error.HTTPError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            LOG.warning("API unavailable: %s", error)
        duplicates = sum(count - 1 for count in Counter(
            (item.get("target"), item.get("type"), item.get("subject"))
            for item in evidence["items"]).values() if count > 1) if evidence else 0
        since = min((row["start"] for row in last_results), default=now)
        report(last_results, now, notification_count(args.data_dir, now),
               evidence["jobs"] if evidence else [], error, duplicates,
               args.judge_interval_s,
               push_cap=(evidence or {}).get("push_cap", DEFAULT_PUSH_CAP),
               violations=model_violations(evidence["items"], since) if evidence else [])
        for _ in range(max(1, args.interval)):
            if STOP:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()

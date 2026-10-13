"""Actions: the few things to change on a database, each closing on something RDST can measure.

`candidates` turns the index plan, tested rewrites and the checkup's database actions into
action rows with fixed wording per type. `reconcile` merges them with what is stored: new
candidates open, open actions whose fix shows up in the database close as applied, and ones
whose evidence faded retire. A user's dismiss or snooze holds until it lapses.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache
from typing import Any

from features.monitor.analysis_digest import latest_done_job, load_analysis
from features.monitor.index_plan import MIN_ADD_BENEFIT, _columns, _norm, _prefix
from features.monitor.presentation import format_size
from shared.monitor.monitor_store import MonitorStore

TYPES = ("add_index", "drop_index", "rewrite_query", "change_setting", "run_maintenance",
         "try_caching")
SHOWN = 5
MIN_REWRITE_GAIN_PCT = 20.0
MIN_REWRITE_SHARE = 0.02
# Database actions carry a severity rather than a measured share of time.
SEVERITY_SCORE = {"bad": 0.15, "warn": 0.05, "info": 0.01}
SETTING_IDS = {"connections", "idle_in_transaction", "cache_hit"}
MAINTENANCE_IDS = {"vacuum", "analyze", "purge_lag", "wraparound", "inactive_slots"}
REWRITE_FADED = 0.1
OPEN_STATES = ("open", "snoozed")


def _pct(share: float) -> str:
    value = share * 100
    return f"{value:.0f}%" if value >= 10 else f"{value:.1f}%"


def _queries_phrase(count: int) -> str:
    return "1 query" if count == 1 else f"{count} queries"


def _rate(per_s: float) -> str:
    return f"{per_s:,.0f}" if per_s >= 10 else f"{per_s:.1f}"


def _add_index(add: dict[str, Any], window: dict[str, Any]) -> dict[str, Any]:
    columns = ", ".join(add["columns"])
    share = sum(helped["share"] for helped in add["helps"])
    count = len(add["helps"])
    summary = (f"Should speed up {_queries_phrase(count)} that {'takes' if count == 1 else 'take'} "
               f"{_pct(share)} of database time")
    cost = add["write_cost"]
    if add["label"] == "worth_it":
        note = ("UPDATEs change an indexed column, so they'll stop skipping index maintenance"
                if add["label_reason"] == "hot_updates" else
                f"{add['table']} takes {_rate(cost['writes_per_s'])} writes/s, so every write "
                "maintains one more index" if add["label_reason"] == "write_heavy" else
                f"{add['table']} already has {cost['existing_indexes']} indexes")
    else:
        note = None
    if add.get("replaces"):
        names = " and ".join(add["replaces"])
        replaced = (f"Once it's built, {names} {'is' if len(add['replaces']) == 1 else 'are'} "
                    "redundant and can be dropped")
        note = f"{note}. {replaced}" if note else replaced
    return {"key": add["id"], "type": "add_index", "score": add["benefit"],
            "payload": {"title": f"Add an index on {add['table']}({columns})", "summary": summary,
                        "note": note, "apply": "run_sql", "sql": add["sql"],
                        "table": add["table"], "columns": add["columns"],
                        "queries": [helped["key"] for helped in add["helps"]],
                        "confidence": "verified" if add["verified"] else "estimate",
                        "label": add["label"], "detail": add, "window": window}}


def _drop_index(drop: dict[str, Any], window: dict[str, Any]) -> dict[str, Any]:
    name = drop["index_name"]
    if drop["reason"] == "duplicate":
        title = f"Drop the duplicate index {name}"
        summary = f"{drop['covered_by']} already covers it"
    else:
        title = f"Drop the unused index {name}"
        summary = ("Not read in the time RDST can see" if drop["reason"] == "unused"
                   else "Not read in over 30 days")
    slows = drop.get("slows") or []
    if slows:
        share = sum(writer["share"] for writer in slows)
        summary = (f"Speeds up {'1 write' if len(slows) == 1 else f'{len(slows)} writes'} "
                   f"that take {_pct(share)} of database time. {summary}")
    if drop["table_writes_per_s"] >= 1:
        summary += f", and {drop['table']} takes {_rate(drop['table_writes_per_s'])} writes/s"
    if drop["size_bytes"]:
        summary += f"; frees {format_size(drop['size_bytes'])}"
    writes = drop["table_writes_per_s"]
    score = 0.02 if drop["reason"] == "duplicate" else 0.04 if writes >= 20 else 0.01
    if drop["confidence"] == "low":
        score /= 4
    return {"key": drop["id"], "type": "drop_index", "score": score,
            "payload": {"title": title, "summary": summary,
                        "note": "Replicas can read indexes the primary doesn't, so check them "
                                "before dropping" if drop["replica_caveat"] else None,
                        "apply": "run_sql", "sql": drop["sql"], "table": drop["table"],
                        "index_name": name,
                        "queries": [writer["key"] for writer in slows],
                        "confidence": drop["confidence"],
                        "detail": drop, "window": window}}


def _query_shape(sql: str) -> str:
    """A query with its values gone: literals, bound parameters and functions of a value."""
    from shared.query_registry.sql_normalizer import normalize_and_extract

    try:
        text = normalize_and_extract(sql)[0]
    except Exception:
        text = sql
    text = re.sub(r":p\d+|\$\d+|\?", "?", text)
    text = re.sub(r"\b\w+\(\s*\?\s*\)", "?", text)
    return " ".join(text.upper().split())


TRIVIAL_DROP_BYTES = 1024 * 1024
WRITE_HEAVY_DROP = 20.0


def _drop_worth_doing(drop: dict[str, Any]) -> bool:
    """A drop worth an action: confident, or on a table writes are hammering, and not trivial.

    The rest stay in the index plan, where a young statistics window says to check again."""
    writes = drop["table_writes_per_s"]
    if drop["confidence"] == "low" and writes < WRITE_HEAVY_DROP:
        return False
    return drop["size_bytes"] >= TRIVIAL_DROP_BYTES or writes >= 1


@lru_cache(maxsize=512)
def _tested_rewrite(target: str, key: str, ref: str) -> dict[str, Any] | None:
    analysis = load_analysis({"state": "done", "analysis_ref": ref, "target": target,
                              "query_key": key})
    display = analysis.display_payload if analysis is not None and \
        isinstance(analysis.display_payload, dict) else {}
    testing = display.get("rewrite_testing") if isinstance(display.get("rewrite_testing"), dict) else {}
    best = testing.get("best_rewrite") if testing.get("tested") else None
    if not isinstance(best, dict):
        return None
    improvement = best.get("improvement") if isinstance(best.get("improvement"), dict) else {}
    overall = improvement.get("overall") or {}
    if not overall.get("is_better") or not overall.get("significant"):
        return None
    # Measured run time when the rewrite ran; the planner's estimate when it was only planned.
    timed = (improvement.get("execution_time") or {}).get("improvement_pct")
    planned = (improvement.get("cost_estimate") or {}).get("improvement_pct")
    measured = isinstance(timed, (int, float))
    # A rewrite that ran no faster never shows, whatever the planner thought of it.
    pct = timed if measured else planned
    sql = best.get("rewritten_sql") or best.get("sql")
    if not isinstance(pct, (int, float)) or pct <= 0 or not sql:
        return None
    return {"sql": str(sql).strip(), "improvement_pct": float(pct), "measured": measured,
            "explanation": best.get("explanation")}


def _rewrites(store: MonitorStore, target: str, queries: list[dict[str, Any]],
              window: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    for query in queries:
        if query["share"] < MIN_REWRITE_SHARE or query.get("kind") != "select":
            continue
        job = latest_done_job(store, target, query["key"])
        tested = _tested_rewrite(target, query["key"], job["analysis_ref"]) if job else None
        if not tested or tested["improvement_pct"] < MIN_REWRITE_GAIN_PCT:
            continue
        text = store.read("SELECT text FROM query_text WHERE target=? AND query_key=?",
                          (target, query["key"]))
        if text and _query_shape(text[0]["text"]) == _query_shape(tested["sql"]):
            # The same query with values filled in: its speed-up is noise, not a rewrite.
            continue
        gain = min(tested["improvement_pct"], 99.0) / 100
        faster = 1 / (1 - gain)
        name = query.get("name") or "this query"
        result = (f"A tested rewrite ran {faster:.1f}x faster" if tested["measured"] else
                  f"The planner estimates a rewrite does {tested['improvement_pct']:.0f}% less work")
        found.append({"key": f"rewrite:{query['key']}", "type": "rewrite_query",
                      "score": query["share"] * gain,
                      "payload": {"title": f"Rewrite {name}",
                                  "summary": f"{result}; the query takes "
                                             f"{_pct(query['share'])} of database time",
                                  "note": "Check that it returns the same rows before you ship it",
                                  "apply": "app_change", "sql": tested["sql"],
                                  "queries": [query["key"]],
                                  "confidence": "measured" if tested["measured"] else "estimate",
                                  "detail": {**tested,
                                             "original": text[0]["text"] if text else None},
                                  "window": window}})
    return found


SPILL_SETTINGS = {"work_mem", "tmp_table_size", "max_heap_table_size"}
# Warn on a buffer setting means a low cache hit ratio was measured alongside it.
BUFFER_SETTINGS = {"shared_buffers", "innodb_buffer_pool_size"}
MEASURED_IDS = {"connections", "idle_in_transaction", "cache_hit"}
# A day's spills that make a spill setting worth doing first.
HEAVY_SPILL_BYTES = 100 * 1024 * 1024
HEAVY_DISK_TABLES = 1000


def _spills(store: MonitorStore, target: str, now: int) -> dict[str, Any] | None:
    """What the last day spilled to disk: Postgres temp blocks, MySQL on-disk temp tables."""
    meta = store.read("SELECT engine FROM stats_meta WHERE target=?", (target,))
    rows = store.read("SELECT query_key,SUM(temp_blks) blks FROM stmt_series WHERE target=? "
                      "AND res=60 AND bucket_start>=? GROUP BY query_key HAVING blks>0 "
                      "ORDER BY blks DESC", (target, now - 86400))
    if not rows or not meta:
        return None
    total = sum(row["blks"] for row in rows)
    keys = [row["query_key"] for row in rows[:3]]
    if meta[0]["engine"] == "mysql":
        return {"summary": f"Queries built {total:,} temporary tables on disk in the last day",
                "heavy": total >= HEAVY_DISK_TABLES, "queries": keys}
    size = total * 8192
    return {"summary": f"Sorts and hashes wrote {format_size(size)} to disk in the last day",
            "heavy": size >= HEAVY_SPILL_BYTES, "queries": keys}


def files_action(action: dict[str, Any], spills: dict[str, Any] | None = None) -> bool:
    """Whether a database finding becomes an action: maintenance always, a setting only when
    the database shows the problem it fixes. One merely off a rule of thumb stays in the
    report's settings audit."""
    identity = str(action.get("id") or "")
    if identity in MAINTENANCE_IDS:
        return True
    if not (identity.startswith("setting_") or identity in SETTING_IDS):
        return False
    name = (action.get("setting") or {}).get("name")
    return (action.get("severity") == "bad" or identity in MEASURED_IDS
            or (name in SPILL_SETTINGS and spills is not None)
            or (name in BUFFER_SETTINGS and action.get("severity") == "warn"))


def _database(actions: list[dict[str, Any]], window: dict[str, Any],
              spills: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    found = []
    for action in actions:
        if not files_action(action, spills):
            continue
        identity = str(action.get("id") or "")
        kind = "run_maintenance" if identity in MAINTENANCE_IDS else "change_setting"
        setting = action.get("setting") or {}
        score = SEVERITY_SCORE.get(action.get("severity"), 0.01)
        summary, queries = action.get("finding"), action.get("query_keys") or []
        if spills and setting.get("name") in SPILL_SETTINGS:
            # Measured spills make the case better than the rule of thumb does.
            summary, queries = spills["summary"], spills["queries"]
            score = max(score, SEVERITY_SCORE["bad"] if spills["heavy"] else score)
        found.append({"key": f"db:{identity}", "type": kind, "score": score,
                      "payload": {"title": action.get("title"), "summary": summary,
                                  "note": action.get("caveat"),
                                  "apply": "run_sql" if kind == "run_maintenance" else
                                  "config_restart" if action.get("restart") else "setting",
                                  "sql": action.get("sql"), "setting": setting or None,
                                  "restart": bool(action.get("restart")), "steps": action.get("steps"),
                                  "why": action.get("why"), "evidence": action.get("evidence") or [],
                                  "rows": action.get("rows") or [], "queries": queries,
                                  "confidence": "measured", "detail": None, "window": window}})
    return found


def latest_database_actions(store: MonitorStore, target: str) -> list[dict[str, Any]]:
    """The database actions (settings, maintenance) from the newer of the last checkup and
    the last database checks."""
    rows = store.read("SELECT report_json,COALESCE(finished_at,ts) at FROM report_run "
                      "WHERE target=? AND state='done' AND report_json IS NOT NULL "
                      "ORDER BY ts DESC,id DESC LIMIT 1", (target,))
    check = store.database_check(target)
    if check is not None and (not rows or int(check["at"]) >= int(rows[0]["at"] or 0)):
        return list(check.get("actions") or [])
    try:
        report = json.loads(rows[0]["report_json"]) if rows else {}
    except ValueError:
        return []
    return list((report.get("snapshot") or {}).get("actions") or [])


def _measuring(store: MonitorStore, target: str) -> bool:
    """Only the database check at add time has run, before there are rates to judge."""
    check, enabled = store.database_check(target), store.enabled_at(target)
    return bool(check and enabled and int(check["at"]) - enabled < FIRST_DATABASE_CHECK_S)


def database_checked_at(store: MonitorStore, target: str, checkup_at: int | None) -> int | None:
    """When settings and maintenance were last checked: a checkup or the database checks."""
    check = store.database_check(target)
    times = [at for at in (checkup_at, check and int(check["at"])) if at]
    return max(times) if times else None


def planned_adds(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """The indexes the plan offers as actions. Adds past the plan's first few stay actions, so
    a reshuffle in rank never retires them; one too small to be worth it stays among the
    smaller wins, and one whose writes outweigh it is never offered."""
    return [add for add in plan["add"] + plan.get("later", [])
            if add["benefit"] >= MIN_ADD_BENEFIT and add.get("label") != "skip"]


def candidates(store: MonitorStore, target: str, plan: dict[str, Any],
               queries: list[dict[str, Any]], database: list[dict[str, Any]]) -> list[dict[str, Any]]:
    window = {"observed_s": plan["observed_s"], "counter_age_s": plan["counter_age_s"]}
    found = [_add_index(add, window) for add in planned_adds(plan)]
    found += [_drop_index(drop, window) for drop in plan["drop"] if _drop_worth_doing(drop)]
    found += _rewrites(store, target, queries, window)
    found += _database(database, window, _spills(store, target, plan["computed_at"]))
    return found


def _index_present(snapshot: list[dict[str, Any]], table: str, columns: list[str]) -> str | None:
    from features.monitor.indexes import index_shape

    wanted = _columns(columns)
    for row in snapshot:
        shape = index_shape(row["definition"])
        if _norm(row["table"]) == table and not shape["partial"] \
                and _prefix(wanted, _columns(shape["columns"])):
            return row["index_name"]
    return None


def _moved_as_asked(title: str, old: str, new: str) -> bool:
    """A raise needs a larger value and a lower a smaller one; any change answers the rest."""
    try:
        before, after = float(old), float(new)
    except ValueError:
        return old != new
    word = title.split(" ", 1)[0].lower()
    if word in ("raise", "give"):
        return after > before
    if word == "lower":
        return after < before
    return after != before


def _applied(store: MonitorStore, target: str, row: dict[str, Any], now: int,
             snapshot: list[dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    """Why an open action's fix is now visible in the database, if it is."""
    payload = row["payload"]
    if row["type"] == "add_index":
        name = _index_present(snapshot, payload["table"], payload["columns"])
        return ("index_added", {"index_name": name}) if name else None
    if row["type"] == "drop_index":
        present = any(item["index_name"] == payload["index_name"]
                      and _norm(item["table"]) == payload["table"] for item in snapshot)
        return None if present else ("index_dropped", {"index_name": payload["index_name"]})
    if row["type"] == "change_setting" and payload.get("setting"):
        name = payload["setting"].get("name")
        changes = store.read("SELECT * FROM setting_change WHERE target=? AND name=? AND ts>=? "
                             "ORDER BY ts", (target, name, row["opened_at"]))
        if changes and _moved_as_asked(payload.get("title") or "", changes[0]["old_value"],
                                       changes[-1]["new_value"]):
            return "setting_changed", {"name": name, "value": changes[-1]["new_value"]}
        return None
    if row["type"] == "rewrite_query" and payload.get("queries"):
        key = payload["queries"][0]
        recent = store.read("SELECT COALESCE(SUM(calls),0) calls FROM stmt_series WHERE target=? "
                            "AND query_key=? AND res=60 AND bucket_start>=?", (target, key, now - 3600))
        before = store.read("SELECT COALESCE(SUM(calls),0) calls FROM stmt_series WHERE target=? "
                            "AND query_key=? AND res=60 AND bucket_start>=? AND bucket_start<?",
                            (target, key, row["opened_at"] - 3600, row["opened_at"]))
        if before[0]["calls"] and recent[0]["calls"] <= before[0]["calls"] * REWRITE_FADED \
                and now - row["opened_at"] >= 3600:
            return "query_replaced", {"query_key": key}
    return None


JEV_RETIRE_HOLD_S = 86400
REOPEN_GROWTH = 1.5
# A change the user says they made holds while its evidence (a week of readings) catches up.
USER_APPLIED_HOLD_S = 7 * 86400
# States only a user leaves: they hold whatever the plan lists, until Bring back.
USER_CLOSED = ("dismissed", "undone")


def _stays_closed(row: dict[str, Any], candidate: dict[str, Any], now: int,
                  database_refreshed: bool) -> bool:
    """Whether a closed action stays closed although its candidate is listed again."""
    if row.get("closed_reason") == "user" and row["state"] != "retired":
        return now - (row.get("closed_at") or now) < USER_APPLIED_HOLD_S
    if row["type"] in ("change_setting", "run_maintenance") and not database_refreshed:
        # Between checkups the database actions are the last checkup's, from before the fix.
        return row["state"] != "retired"
    if row.get("closed_reason") == "jev_stale":
        # Jev judged it stale: only new evidence or a day's wait brings it back.
        return (now - (row.get("closed_at") or now) < JEV_RETIRE_HOLD_S
                and candidate["score"] < row["score"] * REOPEN_GROWTH)
    return False


def reconcile(store: MonitorStore, target: str, now: int, found: list[dict[str, Any]], *,
              database_refreshed: bool = True) -> list[dict[str, Any]]:
    """Merge candidates with stored actions and write the changes; returns the open actions.

    `database_refreshed` says whether `found` carries a fresh read of the database actions;
    between checkups those stay as they were rather than retiring.
    """
    stored = {row["key"]: row for row in store.actions(target)}
    was_open = any(row["state"] == "open" for row in stored.values())
    snapshot = store.read("SELECT * FROM index_snapshot WHERE target=?", (target,))
    writes = []
    seen = set()
    for candidate in found:
        seen.add(candidate["key"])
        row = stored.get(candidate["key"])
        if row is None:
            writes.append({**candidate, "state": "open", "opened_at": now,
                           "events": [("opened", {"score": candidate["score"]})]})
            continue
        state, closed = row["state"], {}
        if state == "snoozed" and (row["snoozed_until"] or 0) <= now:
            state = "open"
        if state in ("applied", "confirmed", "no_effect", "retired"):
            if _stays_closed(row, candidate, now, database_refreshed):
                continue
            # The same problem came back after it closed.
            state, closed = "open", {"events": [("reopened", {"was": row["state"]})]}
        # The checkup's order and Jev's read outlive the recompute that rebuilt the payload.
        kept = {name: row["payload"][name] for name in ("priority", "jev") if name in row["payload"]}
        candidate = {**candidate, "payload": {**candidate["payload"], **kept}}
        held = ({"closed_at": row["closed_at"], "closed_reason": row["closed_reason"]}
                if state in USER_CLOSED else {})
        writes.append({**candidate, "state": state, "opened_at": row["opened_at"],
                       "snoozed_until": row["snoozed_until"] if state == "snoozed" else None,
                       "dismiss_reason": row["dismiss_reason"], **held, **closed})
    for key, row in stored.items():
        # A dismissal holds whatever the database does, until the user reopens it.
        if key in seen or row["state"] not in OPEN_STATES:
            continue
        applied = _applied(store, target, row, now, snapshot)
        if applied is not None:
            reason, detail = applied
            writes.append({**row, "state": "applied", "closed_at": now, "closed_reason": reason,
                           "events": [("applied", detail)]})
        elif row["type"] in ("change_setting", "run_maintenance") and not database_refreshed:
            continue
        elif row["type"] == "run_maintenance" and _maintenance_done(store, target, row):
            # The checkup no longer finds the bloat or stale statistics: the work got done.
            writes.append({**row, "state": "applied", "closed_at": now, "closed_reason": "cleared",
                           "events": [("applied", {"cleared": True})]})
        else:
            writes.append({**row, "state": "retired", "closed_at": now,
                           "closed_reason": "evidence_faded", "events": [("retired", {})]})
    # A fix that shows up while its candidate is still listed (the plan lags a cycle) closes too.
    for write in writes:
        if write["state"] in OPEN_STATES and write["type"] in ("add_index", "drop_index", "change_setting"):
            current = stored.get(write["key"])
            probe = {**write, "opened_at": current["opened_at"] if current else now}
            applied = _applied(store, target, probe, now, snapshot)
            if applied is not None:
                reason, detail = applied
                write.update(state="applied", closed_at=now, closed_reason=reason,
                             events=[*(write.get("events") or []), ("applied", detail)])
    if writes:
        store.write_actions(target, now, writes)
    opened = store.actions(target, ("open",))
    file_changes_ready(store, target, now, opened, was_open=was_open)
    return opened


def _maintenance_done(store: MonitorStore, target: str, row: dict[str, Any]) -> bool:
    """The maintenance a card named really ran, so it can show as done.

    A card can also leave because its finding no longer holds (a statistics reset, a rule that
    stopped flagging the table), and that's no reason to show it as done. PostgreSQL records
    when each table was last vacuumed and analyzed; elsewhere a vacuum's dead rows must fall."""
    stamp = {"db:vacuum": "vacuumed_at", "db:analyze": "analyzed_at"}.get(row["key"])
    if stamp is None:
        return True
    current = {state["table"]: state for state in store.read(
        'SELECT "table",live_rows,dead_rows,vacuumed_at,analyzed_at FROM table_state '
        "WHERE target=?", (target,))}
    for item in row["payload"].get("rows") or []:
        state = current.get(str(item.get("name")).split(".")[-1])
        if state is None:
            return False
        if state[stamp] is not None:
            if state[stamp] < row["opened_at"]:
                return False
            continue
        match = re.match(r"([\d.]+)% dead", str(item.get("value") or ""))
        if row["key"] != "db:vacuum" or not match:
            continue
        total = (state["live_rows"] or 0) + (state["dead_rows"] or 0)
        now_pct = 100 * (state["dead_rows"] or 0) / total if total else 100.0
        if now_pct >= VACUUM_CLEAR_PCT and now_pct > float(match.group(1)) / 2:
            return False
    return True


VACUUM_CLEAR_PCT = 10.0


def refresh(store: MonitorStore, target: str, now: int, *,
            database: list[dict[str, Any]] | None = None) -> list[dict[str, Any]] | None:
    """Recompute the index plan and the actions from the latest readings; None before any.

    A checkup passes its fresh `database` actions; between checkups the last stored ones hold.
    """
    from features.monitor import plan_inputs
    from features.monitor.index_plan import build_plan
    from features.monitor.naming import target_names

    meta = store.read("SELECT engine FROM stats_meta WHERE target=?", (target,))
    if not meta:
        return None
    names = target_names(store, target)
    inputs = plan_inputs.gather(store, target, now, meta[0]["engine"], names)
    plan = build_plan(inputs)
    previous = store.index_plan(target)
    if previous and previous.get("explanation") and _changes(previous) == _changes(plan):
        # The checkup's explanation holds while the plan's changes stay the same.
        plan["explanation"] = previous["explanation"]
    store.save_index_plan(target, plan)
    found = candidates(store, target, plan, inputs["queries"],
                       database if database is not None else latest_database_actions(store, target))
    opened = reconcile(store, target, now, found, database_refreshed=database is not None)
    verify(store, target, now)
    return opened


VERIFY_AFTER_S = 15 * 60
VERIFY_GIVE_UP_S = 2 * 3600
VERIFY_BEFORE_S = 7 * 86400
VERIFY_MIN_CALLS = 20
VERIFIED_GAIN = 0.2
DROP_VERIFIED_GAIN = 0.05
# The measurement starts a few minutes after the change, once the old plans have cleared.
VERIFY_SETTLE_S = 300


def verify(store: MonitorStore, target: str, now: int) -> None:
    """Measure an applied index or rewrite: the queries it served over the 7 days before the
    fix against at least 20 calls after it.

    Faster by a fifth or more confirms it; no gain by two hours after the fix says it didn't
    help. Each outcome files one closed Inbox event: Fix confirmed or No change yet. Changes
    without queries to measure stay applied."""
    from features.monitor.api.views import stmt_totals

    writes = []
    for row in store.actions(target, ("applied",)):
        keys = row["payload"].get("queries") or []
        closed = int(row.get("closed_at") or 0)
        if row["type"] not in ("add_index", "rewrite_query", "drop_index") or not keys or \
                now - closed < VERIFY_AFTER_S:
            continue

        def mean(start: int, end: int) -> tuple[float, int] | None:
            totals = stmt_totals(store, target, start, end, keys).values()
            calls = sum(int(total["calls"] or 0) for total in totals)
            spent = sum(float(total["total_ms"] or 0) for total in totals)
            return (spent / calls, calls) if calls >= VERIFY_MIN_CALLS else None

        before = mean(closed - VERIFY_BEFORE_S, closed)
        after = mean(closed + VERIFY_SETTLE_S, now)
        if before is None or after is None:
            continue
        result = {"before_ms": round(before[0], 3), "after_ms": round(after[0], 3),
                  "calls": after[1], "at": now}
        # One index less is a smaller gain on each write than a new index is on a read.
        gain = DROP_VERIFIED_GAIN if row["type"] == "drop_index" else VERIFIED_GAIN
        if after[0] <= before[0] * (1 - gain):
            state = "confirmed"
        elif now - closed >= VERIFY_GIVE_UP_S:
            state = "no_effect"
        else:
            continue
        writes.append({**row, "state": state, "payload": {**row["payload"], "result": result},
                       "events": [(state, result)]})
    if writes:
        store.write_actions(target, now, writes)
        for write in writes:
            _file_verification(store, target, now, write)


def _file_verification(store: MonitorStore, target: str, now: int,
                       row: dict[str, Any]) -> dict[str, Any] | None:
    """One closed Inbox event per verified change: Fix confirmed (positive) or No change yet
    (neutral). Neither pushes."""
    from features.monitor.naming import target_names

    stored = next((item for item in store.actions(target) if item["key"] == row["key"]), None)
    if stored is None:
        return None
    payload, result = stored["payload"], stored["payload"].get("result") or {}
    keys = list(payload.get("queries") or [])
    names = target_names(store, target)
    subject = names.get(keys[0]) if keys else None
    columns = list(payload.get("columns") or [])
    table = payload.get("table")
    confirmed = stored["state"] == "confirmed"
    body = {"action_id": stored["id"], "action_key": stored["key"], "action_type": stored["type"],
            "action_title": payload.get("title"), "query_keys": keys,
            "query_key": keys[0] if keys else None, "table": table, "columns": columns,
            "index_name": payload.get("index_name") or _applied_index(store, stored["id"]),
            "index_label": f"{table} ({', '.join(columns)})" if table and columns else
            payload.get("title"),
            "detected_at": int(stored.get("closed_at") or now),
            "before_ms": result.get("before_ms"), "after_ms": result.get("after_ms"),
            "calls": result.get("calls")}
    if confirmed:
        body["resolution"] = {"before": {"mean_ms": result.get("before_ms")},
                              "after": {"mean_ms": result.get("after_ms")}}
    kind = "fix_confirmed" if confirmed else "no_change_yet"
    title = f"{'Fix confirmed' if confirmed else 'No change yet'}: " \
            f"{subject or payload.get('title') or 'a change'}"
    item = store.upsert_item(target, kind, f"action:{stored['id']}", 0.5 if confirmed else 0.3,
                             title, body, now, delivery="inbox", reopen_resolved=True)
    return store.action_item(item["id"], "resolve", now, resolved_by="code",
                             measured_recovery=confirmed)


def _applied_index(store: MonitorStore, action_id: int) -> str | None:
    """The index the database showed when the action closed as applied."""
    for event in reversed(store.action_events(action_id)):
        if event["kind"] == "applied" and event["detail"].get("index_name"):
            return str(event["detail"]["index_name"])
    return None


def _changes(plan: dict[str, Any]) -> set[str]:
    return {item["id"] for item in plan.get("add", []) + plan.get("drop", [])}


def _named(text: str | None, names: dict[str, str]) -> str | None:
    """The model's {{q:key}} references as the query names the rest of the page uses."""
    if not text:
        return text
    return re.sub(r"\{\{q:([0-9a-f]+)\}\}", lambda m: names.get(m.group(1), "a query"), text)


def apply_explanations(store: MonitorStore, target: str, now: int,
                       narrative: dict[str, Any]) -> None:
    """A why per explained action, and the plan's explanation. The order stays measured impact
    and Jev's read."""
    from features.monitor.naming import target_names

    names = target_names(store, target)
    picks = narrative.get("action_picks") or []
    rows = {f"a{row['id']}": row for row in store.actions(target, OPEN_STATES)}
    writes = [{**rows[pick["action_id"]], "why": _named(pick.get("why"), names)}
              for pick in picks if pick["action_id"] in rows and pick.get("why")]
    if writes:
        store.write_actions(target, now, writes)
    plan = store.index_plan(target)
    if plan is not None and narrative.get("index_plan_explanation"):
        store.save_index_plan(target, {**plan, "explanation": _named(
            narrative["index_plan_explanation"], names)})


VERBS = ("set_aside", "snooze", "dismiss", "reopen", "bring_back", "did_it", "undid_it")
# Changes a user can say they undid: the ones applied, measured or not.
UNDOABLE = ("applied", "confirmed", "no_effect")


def set_state(store: MonitorStore, target: str, action_id: int, verb: str, now: int, *,
              until: int | None = None, reason: str | None = None) -> dict[str, Any] | None:
    """A user's verb on one action. Set aside (snooze is its older name) holds it until a date
    with a reason; dismiss closes it with a reason; did_it marks it applied by the user, so
    measuring starts; undid_it marks an applied change undone; bring_back (or reopen) opens
    any of them again. None when the action doesn't exist."""
    rows = [row for row in store.actions(target) if row["id"] == action_id]
    if not rows:
        return None
    row = rows[0]
    if verb in ("snooze", "set_aside"):
        update = {"state": "snoozed", "snoozed_until": until or now + 7 * 86400,
                  "dismiss_reason": reason}
    elif verb == "dismiss":
        update = {"state": "dismissed", "dismiss_reason": reason, "closed_at": now,
                  "closed_reason": "dismissed"}
    elif verb in ("reopen", "bring_back"):
        update = {"state": "open", "snoozed_until": None, "dismiss_reason": None,
                  "closed_at": None, "closed_reason": None}
    elif verb == "did_it":
        if row["state"] in UNDOABLE:
            return row
        update = {"state": "applied", "snoozed_until": None, "closed_at": now,
                  "closed_reason": "user"}
    elif verb == "undid_it":
        if row["state"] not in UNDOABLE:
            raise ValueError("Only an applied change can be undone")
        update = {"state": "undone", "dismiss_reason": reason, "closed_at": now,
                  "closed_reason": "undone"}
    else:
        raise ValueError(f"Unknown action verb {verb}")
    store.write_actions(target, now, [{**row, **update,
                                       "events": [(verb, {"until": until, "reason": reason})]}])
    return next(item for item in store.actions(target) if item["id"] == action_id)


INDEX_TYPES = ("add_index", "drop_index")
MORE = 5


def _card(row: dict[str, Any], names: dict[str, str], minute_ms: float = 0.0) -> dict[str, Any]:
    payload = row["payload"]
    return {"id": row["id"], "key": row["key"], "type": row["type"], "state": row["state"],
            "risk": risk(row), "effort": EFFORT.get(row["type"], "code"),
            "saves_ms_per_min": saves_ms_per_min(row["score"], minute_ms), "tier": None,
            "score": row["score"], "title": payload["title"], "summary": payload["summary"],
            "note": payload.get("note"), "why": row.get("why"), "apply": payload["apply"],
            "sql": payload.get("sql"), "restart": bool(payload.get("restart")),
            "confidence": payload.get("confidence"),
            "queries": [{"key": key, "name": names.get(key) or key}
                        for key in payload.get("queries") or []],
            "opened_at": row["opened_at"], "updated_at": row["updated_at"],
            "snoozed_until": row.get("snoozed_until"), "children": [],
            "detail": payload.get("detail"), "window": payload.get("window"),
            "steps": payload.get("steps"), "evidence": payload.get("evidence") or [],
            "rows": payload.get("rows") or [],
            "priority": payload.get("priority"),
            # Jev's read of urgency, 0 to 1; neutral until it has judged.
            "jev": float((payload.get("jev") or {}).get("priority", 0.5)),
            "severity": _severity(row["score"]), "category": CATEGORY[row["type"]],
            "result": payload.get("result")}


SEVERITIES = ("low", "medium", "high")
CATEGORY = {"add_index": "indexes", "drop_index": "indexes", "index_plan": "indexes",
            "change_setting": "settings", "settings": "settings",
            "run_maintenance": "maintenance", "maintenance": "maintenance",
            "rewrite_query": "rewrites", "try_caching": "caching"}


def _severity(score: float) -> str:
    """One scale for every kind: a bad database finding scores 0.15, a warning 0.05, an index
    its share of database time times its gain."""
    return "high" if score >= 0.1 else "medium" if score >= 0.03 else "low"


def _order(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Measured impact weighted by Jev's latest read.
    return sorted(cards, key=lambda card: (-card["score"] * (0.5 + card["jev"]), card["key"]))


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text[1:2].islower() else text


def _group_words(kind: str, cards: list[dict[str, Any]]) -> tuple[str, str]:
    if kind == "index_plan":
        adds = [card for card in cards if card["type"] == "add_index"]
        drops = [card for card in cards if card["type"] == "drop_index"]
        parts = ([f"add {len(adds)}"] if adds else []) + ([f"drop {len(drops)}"] if drops else [])
        shares = {child["key"]: child["share"] for card in adds for child in card["detail"]["helps"]}
        dropped = (f"{len(drops)} {'index' if len(drops) == 1 else 'indexes'} that cost writes "
                   "without serving reads")
        if adds:
            summary = (f"Should speed up {_queries_phrase(len(shares))} that "
                       f"{'takes' if len(shares) == 1 else 'take'} {_pct(sum(shares.values()))} "
                       "of database time")
            summary += f", and drops {dropped}" if drops else ""
        else:
            summary = f"Drops {dropped}"
        return f"Update your indexes: {', '.join(parts)}", summary
    first, rest = cards[0]["title"], len(cards) - 1
    if kind == "settings":
        settings = all(card["key"].startswith("db:setting_") for card in cards)
        return (f"Tune {len(cards)} server settings" if settings else
                f"Make {len(cards)} database fixes",
                f"Start with: {_lower_first(first)}, then {rest} more")
    return (f"Run {len(cards)} maintenance tasks",
            ", then ".join([first] + [_lower_first(card["title"]) for card in cards[1:]]))


CARD_CHILDREN = 5

GROUPS = {"index_plan": INDEX_TYPES, "settings": ("change_setting",),
          "maintenance": ("run_maintenance",)}


def _group(kind: str, cards: list[dict[str, Any]]) -> dict[str, Any]:
    cards = _order(cards)
    title, summary = _group_words(kind, cards)
    if kind == "index_plan" and len(cards) > CARD_CHILDREN:
        # The card lists the best few; the index plan holds them all.
        rest = len(cards) - CARD_CHILDREN
        summary += f" ({rest} more in the index plan)"
        cards = cards[:CARD_CHILDREN]
    sql = "\n".join(card["sql"] for card in cards if card["sql"])
    return {"id": None, "key": kind, "type": kind, "state": "open",
            "risk": max((card["risk"] for card in cards), key=RISKS.index),
            "effort": max((card["effort"] for card in cards), key=EFFORTS.index),
            "saves_ms_per_min": round(sum(card["saves_ms_per_min"] for card in cards), 1),
            "tier": min((card["tier"] for card in cards if card["tier"]), key=TIERS.index,
                        default=None),
            "score": sum(card["score"] for card in cards), "title": title, "summary": summary,
            "note": None, "why": next((card["why"] for card in cards if card["why"]), None),
            "apply": "setting" if kind == "settings" else "run_sql", "sql": sql or None,
            "restart": any(card["restart"] for card in cards),
            "confidence": "verified" if any(card["confidence"] == "verified" for card in cards)
            else None,
            "queries": list({query["key"]: query for card in cards
                             for query in card["queries"]}.values()),
            "opened_at": min(card["opened_at"] for card in cards),
            "updated_at": max(card["updated_at"] for card in cards), "snoozed_until": None,
            "children": cards, "detail": None, "window": cards[0]["window"],
            "severity": max((card["severity"] for card in cards), key=SEVERITIES.index),
            "category": CATEGORY[kind],
            "steps": None, "evidence": [], "rows": [],
            "priority": min((card["priority"] for card in cards if card["priority"] is not None),
                            default=None),
            "jev": max(card["jev"] for card in cards)}


# Why the first pass's analyses wait while signed in, by AI-off reason.
AI_PAUSED_TEXT = {
    "limit": "Your account's AI slots are all in use, so the first analyses wait for a free one.",
    "quota": "You've run out of AI usage for now, so the first analyses wait until it's raised.",
}


def _checkup_state(store: MonitorStore, target: str, now: int) -> dict[str, Any]:
    """Where the checkups stand: the first pass of analyses, then the latest checkup. While
    the first pass's analyses wait for a sign-in or for AI to come back, `held` says so, and
    `ai_reason` says why AI is off."""
    from features.monitor import database_limit
    from features.monitor.analyze_queue import SIGN_IN_FOR_MONITORING
    from features.monitor.report import first_pass

    progress = first_pass(store, target, now)
    if progress is not None and not progress["due"]:
        held, held_text, ai_reason = None, None, None
        if not database_limit._signed_in():
            held, held_text = "sign_in", SIGN_IN_FOR_MONITORING
        elif database_limit.paused(store, target, now):
            quota = database_limit.ai_state(store, target) != "local" and \
                database_limit.allowance_paused(store, "hosted", now) and \
                not database_limit.own_key()
            ai_reason = "quota" if quota else "limit"
            held, held_text = "ai_paused", AI_PAUSED_TEXT[ai_reason]
        return {"state": "first_pass", "analyzed": progress["analyzed"], "total": progress["total"],
                "due_by": progress["due_by"], "report_id": None, "finished_at": None,
                "held": held, "held_text": held_text, "ai_reason": ai_reason}
    rows = store.read("SELECT id,state,finished_at FROM report_run WHERE target=? AND sample=0 "
                      "ORDER BY id DESC LIMIT 1", (target,))
    latest = rows[0] if rows else None
    if latest is None:
        return {"state": "none", "analyzed": 0, "total": 0, "due_by": None, "report_id": None,
                "finished_at": None}
    # A checkup runs in the background; the page never waits on it.
    state = "running" if latest["state"] == "running" else "done"
    last_done = store.read("SELECT id,finished_at FROM report_run WHERE target=? AND sample=0 "
                           "AND state='done' ORDER BY id DESC LIMIT 1", (target,))
    return {"state": state, "analyzed": 0, "total": 0, "due_by": None, "report_id": latest["id"],
            "finished_at": last_done[0]["finished_at"] if last_done else None}


COVERED = ("indexes", "settings", "maintenance", "rewrites")


# The monitor rereads statistics, and with them the index plan and rewrites, this often;
# the database checks behind settings and maintenance run every 15 minutes.
STATS_EVERY_S = 300
DATABASE_CHECK_S = 15 * 60
FIRST_DATABASE_CHECK_S = 2 * 60


def _coverage(checkup: dict[str, Any], plan: dict[str, Any] | None,
              by_kind: dict[str, list[dict[str, Any]]],
              database_at: int | None, measuring: bool = False) -> dict[str, dict[str, Any]]:
    """What RDST has checked for each kind of change, when, and when it checks next."""
    first = checkup["state"] == "first_pass"
    running = checkup["state"] == "running"
    checked_at = checkup.get("finished_at")
    plan_at = (plan or {}).get("computed_at")
    load = (plan or {}).get("load") or {}
    notes = {
        "indexes": (f"Your {load.get('indexes', 0)} indexes fit your {load['queries']} busiest "
                    "queries" if load.get("queries") else "Your indexes fit your busiest queries"),
        "settings": "Memory, sorts and connections look right for your load",
        "maintenance": "No bloated tables, stale statistics or long-open transactions",
        "rewrites": "None of your busiest queries has a faster rewrite",
    }
    every = {"indexes": (plan_at, STATS_EVERY_S), "rewrites": (plan_at, STATS_EVERY_S),
             "settings": (database_at, DATABASE_CHECK_S),
             "maintenance": (database_at, DATABASE_CHECK_S)}
    coverage = {}
    for kind in COVERED:
        note = None
        at, interval = every[kind]
        at = at or checked_at
        if kind in by_kind:
            state = "found"
        elif kind in ("settings", "maintenance") and measuring:
            # Only the check at add time has run: rates need a couple of minutes of traffic.
            state = "checking"
            note = "Measuring two minutes of traffic first. Findings show up here after that."
        elif kind in ("settings", "maintenance") and database_at:
            state = "checked"
        elif kind in ("indexes", "rewrites") and plan is not None and not first:
            # Both come from the analyses; the background checkup only writes the why.
            state = "checked"
        elif first and kind == "rewrites" and checkup["state"] == "first_pass":
            state, at = "waiting", None
        elif first or (running and checked_at is None):
            state, at = "checking", None
        else:
            state = "checked"
        coverage[kind] = {"state": state, "checked_at": at, "note": notes[kind],
                          "waiting_on": note,
                          "next_at": at + interval if at else None}
    return coverage


def present(store: MonitorStore, target: str, now: int) -> dict[str, Any]:
    """The Actions tab: up to five cards (index changes as one), then a few more."""
    from features.monitor.naming import target_names

    names = target_names(store, target)
    rows = store.actions(target)
    minute_ms = database_minute(store, target, now)[0]
    cards = [_card(row, names, minute_ms) for row in rows if row["state"] == "open"]
    _tiers(cards, "tier")
    # One card per kind of change, so the page stays a short list however much there is to do.
    shown = []
    for kind, types in GROUPS.items():
        members = [card for card in cards if card["type"] in types]
        shown += [_group(kind, members)] if len(members) >= 2 else members
    shown += [card for card in cards
              if not any(card["type"] in types for types in GROUPS.values())]
    others = _order(shown)
    shown_count = SHOWN
    closed = [_card(row, names) for row in rows if row["state"] in ("applied", "confirmed", "no_effect")
              and (row.get("closed_at") or 0) >= now - 7 * 86400]
    plan = store.index_plan(target)
    # Each section's explanation; Indexes reads the whole plan, the rest their open changes.
    from features.monitor.action_stories import index_story, section_story

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        by_kind.setdefault(card["category"], []).append(card)
    sections = {kind: section_story(kind, members) for kind, members in by_kind.items()
                if kind != "indexes"}
    shown_plan = present_plan(store, target)
    if shown_plan is not None:
        sections["indexes"] = index_story(shown_plan)
    checkup = _checkup_state(store, target, now)
    return {"target": target, "computed_at": plan["computed_at"] if plan else None,
            "sections": sections,
            "card_count": len(others[:shown_count]),
            # Every change on the page: each index the plan adds or drops, then the rest.
            "change_count": (len(shown_plan["add"]) + len(shown_plan["drop"]) if shown_plan
                             else len(by_kind.get("indexes", [])))
            + sum(len(members) for kind, members in by_kind.items() if kind != "indexes"),
            "checkup": checkup,
            "coverage": _coverage(checkup, shown_plan, by_kind,
                                  database_checked_at(store, target, checkup.get("finished_at")),
                                  _measuring(store, target)),
            "actions": others[:shown_count], "more": others[shown_count:shown_count + MORE],
            "open_count": len(cards), "recently_done": closed[:SHOWN],
            "snoozed": sum(1 for row in rows if row["state"] == "snoozed"),
            "dismissed": sum(1 for row in rows if row["state"] == "dismissed"),
            "set_aside": [_card(row, names) for row in rows
                          if row["state"] in ("snoozed", "dismissed")][:SET_ASIDE],
            "window": {"observed_s": plan["observed_s"], "counter_age_s": plan["counter_age_s"]}
            if plan else None}


# Dismissed and snoozed changes the page lists, so any can be brought back.
SET_ASIDE = 20
JEV_STALE_RETIRE = 0.9
JEV_LIMIT = 20


def _measures(row: dict[str, Any], now: int) -> dict[str, float]:
    payload = row["payload"]
    detail = payload.get("detail") or {}
    measures = {"score": float(row["score"]), "age_s": float(max(0, now - row["opened_at"]))}
    if row["type"] == "add_index":
        measures["share_helped"] = float(sum(helped["share"] for helped in detail.get("helps") or []))
        measures["table_writes_per_s"] = float((detail.get("write_cost") or {}).get("writes_per_s") or 0)
    elif row["type"] == "drop_index":
        measures["table_writes_per_s"] = float(detail.get("table_writes_per_s") or 0)
        measures["size_bytes"] = float(detail.get("size_bytes") or 0)
    elif row["type"] == "rewrite_query":
        measures["improvement_pct"] = float(detail.get("improvement_pct") or 0)
    return measures


def jev_rank(store: MonitorStore, target: str, now: int, client: Any = None) -> bool:
    """Ask Jev how urgent each open action is now; retire ones it's sure have gone stale."""
    import uuid

    from shared.llm_manager.jev_client import JevClient

    rows = store.actions(target, ("open",))[:JEV_LIMIT]
    if len(rows) < 2:
        return False
    db = store.read("SELECT * FROM db_series WHERE target=? AND res=60 ORDER BY bucket_start DESC "
                    "LIMIT 1", (target,))
    latest = db[0] if db else {}
    numbers = {"total_qps": float(latest.get("calls") or 0) / 60,
               "connections": float(latest.get("active_conns") or 0),
               "cache_hit_ratio": min(1.0, max(0.0, float(latest.get("cache_hit_ratio") or 0)))}
    changes = [f"index {row['index_name']} on {row['table']} {row['kind']}" for row in store.read(
        'SELECT kind,"table",index_name FROM index_change WHERE target=? AND ts>=? ORDER BY ts DESC '
        "LIMIT 25", (target, now - 86400))]
    changes += [f"{row['name']} changed from {row['old_value']} to {row['new_value']}"
                for row in store.read("SELECT name,old_value,new_value FROM setting_change WHERE "
                                      "target=? AND ts>=? ORDER BY ts DESC LIMIT 25",
                                      (target, now - 86400))]
    request = [{"id": f"a{row['id']}", "type": row["type"], "title": row["payload"]["title"][:200],
                "summary": (row["payload"]["summary"] or row["payload"]["title"])[:400],
                "measures": _measures(row, now)} for row in rows]
    result = (client or JevClient()).action_priority(
        request_id=str(uuid.uuid4()), target_numbers=numbers, actions=request,
        recent_changes=[change[:200] for change in changes])
    answers = {answer["id"]: answer for answer in result["actions"]}
    writes = []
    for row in rows:
        answer = answers[f"a{row['id']}"]
        judged = {"priority": answer["priority"], "relevance": answer["relevance"], "at": now}
        update = {**row, "payload": {**row["payload"], "jev": judged}}
        if answer["relevance"] == "stale" and answer["stale_probability"] >= JEV_STALE_RETIRE:
            update.update(state="retired", closed_at=now, closed_reason="jev_stale",
                          events=[("retired", {"by": "jev"})])
        writes.append(update)
    store.write_actions(target, now, writes)
    return True


def present_plan(store: MonitorStore, target: str) -> dict[str, Any] | None:
    """The index plan with the changes a user dismissed or snoozed set aside."""
    plan = store.index_plan(target)
    if plan is None:
        return None
    held = {row["key"] for row in store.actions(target, ("dismissed", "snoozed"))}
    adds = [add for add in plan["add"] if add["id"] not in held]
    # The plan shows the drops that are actions; small or uncertain ones wait for more evidence.
    drops = [drop for drop in plan["drop"] if drop["id"] not in held and _drop_worth_doing(drop)]
    watch = [drop for drop in plan["drop"] if drop["id"] not in held and not _drop_worth_doing(drop)]
    set_aside = [item["id"] for item in plan["add"] + plan["drop"] if item["id"] in held]
    rollback = [drop["restore_sql"] for drop in drops if drop.get("restore_sql")] + \
        [add["undo_sql"] for add in adds if add.get("undo_sql")]
    return {**plan, "add": adds, "drop": drops, "watch": watch, "set_aside": set_aside,
            "sql": "\n".join([add["sql"] for add in adds] + [drop["sql"] for drop in drops]),
            "rollback_sql": "\n".join(rollback) or None}


# The plan across databases (ca-actions-plan.md decision 6): every open action ranked by the
# database time it saves a minute, in three tiers, with what applying it risks and costs.

DO_FIRST_SCORE = 0.1
DO_FIRST_MAX = 3
THEN_MIN_MS_PER_MIN = 1.0
# A change saving less than this share of what the best one saves is a small win: worth
# listing, not worth a quiet hour.
LATER_SHARE_OF_TOP = 0.2
TIERS = ("first", "then", "later")
# Lowest first: ties in time saved go to the change that risks and costs less.
RISKS = ("none", "writes", "locks", "restart")
EFFORTS = ("copy_sql", "setting", "code")
EFFORT = {"add_index": "copy_sql", "drop_index": "copy_sql", "run_maintenance": "copy_sql",
          "change_setting": "setting", "rewrite_query": "code", "try_caching": "code"}
KIND = {"add_index": "index", "drop_index": "index", "change_setting": "setting",
        "run_maintenance": "maintenance", "rewrite_query": "rewrite", "try_caching": "caching"}
KINDS = ("index", "setting", "maintenance", "rewrite")
PLAN_WINDOW_S = 7 * 86400
APPLIED_WINDOW_S = 30 * 86400
APPLIED_LIMIT = 100
APPLIED_STATES = ("applied", "confirmed", "no_effect")
APPLIED_STATUS = {"applied": "measuring", "confirmed": "verified", "no_effect": "no_change",
                  "undone": "undone"}
CHANGES_SUBJECT = "plan"


def _locks(sql: str | None) -> bool:
    """A statement that blocks reads or writes while it runs: an index built or dropped
    without CONCURRENTLY on PostgreSQL, VACUUM FULL, CLUSTER, REINDEX, OPTIMIZE TABLE."""
    for statement in (sql or "").split(";"):
        text = " ".join(statement.upper().split())
        if text.startswith(("CREATE INDEX", "CREATE UNIQUE INDEX", "DROP INDEX", "REINDEX")) \
                and "CONCURRENTLY" not in text:
            return True
        if text.startswith(("CLUSTER", "LOCK ", "OPTIMIZE TABLE")) or \
                (text.startswith("VACUUM") and re.search(r"\bFULL\b", text)):
            return True
    return False


def risk(row: dict[str, Any]) -> str:
    """What applying a change risks besides its own work: `restart` for a setting that only
    changes on a restart, `writes` for an index on a write-heavy table (every write maintains
    it), `locks` for a drop or maintenance statement that blocks the table, else `none`."""
    from features.monitor.index_plan import WRITE_HEAVY

    payload = row["payload"]
    if row["type"] == "change_setting":
        restart = payload.get("restart") or payload.get("apply") == "config_restart"
        return "restart" if restart else "none"
    if row["type"] == "add_index":
        cost = (payload.get("detail") or {}).get("write_cost") or {}
        writes = max(float(cost.get("writes_per_s") or 0),
                     float(cost.get("peak_writes_per_s") or 0) / 2)
        return "writes" if writes >= WRITE_HEAVY or cost.get("hot_risk") else "none"
    if row["type"] in ("drop_index", "run_maintenance") and _locks(payload.get("sql")):
        return "locks"
    return "none"


def saves_ms_per_min(score: float, minute_ms: float) -> float:
    """Database time a change saves a minute: the time its queries spend each minute, times
    the reduction expected of the change.

    An action's score is that reduction as a share of all database time. For an index it is
    the plan's benefit, the sum over the queries it speeds up of share x expected gain; for a
    rewrite, its query's share x the tested gain; settings, maintenance and drops carry a
    fixed share per severity on the same scale. So saves_ms_per_min = score x minute_ms, where
    minute_ms is the database time all queries spend a minute over the plan's window."""
    return round(float(score) * minute_ms, 1)


def database_minute(store: MonitorStore, target: str, now: int
                    ) -> tuple[float, dict[str, dict[str, Any]]]:
    """The database time queries spent a minute over the plan's window (the last 7 days, or
    since readings began), and each query's totals in that window."""
    from features.monitor.api.views import stmt_totals

    start = now - PLAN_WINDOW_S
    # The finest readings date the start; a rollup bucket starts before its first reading.
    first = store.read("SELECT MIN(bucket_start) first FROM stmt_series WHERE target=? "
                       "AND bucket_start>=? GROUP BY res ORDER BY res LIMIT 1", (target, start))
    if not first or first[0]["first"] is None:
        return 0.0, {}
    meta = store.read("SELECT observed_since FROM stats_meta WHERE target=?", (target,))
    since = max(start, int(meta[0]["observed_since"])) if meta else int(first[0]["first"])
    totals = stmt_totals(store, target, start, now + 1)
    spent = sum(float(row.get("total_ms") or 0) for row in totals.values())
    return spent / max(60, now + 60 - since) * 60, totals


def _rank_key(card: dict[str, Any]) -> tuple:
    return (-card["saves_ms_per_min"], -card["score"], RISKS.index(card["risk"]),
            EFFORTS.index(card["effort"]), card.get("database") or "", card["key"])


def _tiers(cards: list[dict[str, Any]], field: str) -> None:
    """Do first: open actions scoring 0.1 or more, at most three per database, by time saved
    a minute, ties by risk then effort; a database whose best change scores at least half
    that still has one thing to do first. Then: the rest saving at least a fifth of what the
    best change saves. Later: the rest. Writes each card's tier to `field`; the list keeps
    its order."""
    taken: dict[str, int] = {}
    top = max((card["saves_ms_per_min"] for card in cards), default=0.0)
    floor = max(THEN_MIN_MS_PER_MIN, LATER_SHARE_OF_TOP * top)
    for card in sorted(cards, key=_rank_key):
        database = card.get("database") or ""
        bar = DO_FIRST_SCORE / 2 if database not in taken else DO_FIRST_SCORE
        if card["score"] >= bar and taken.get(database, 0) < DO_FIRST_MAX:
            taken[database] = taken.get(database, 0) + 1
            card[field] = "first"
        else:
            card[field] = "then" if card["saves_ms_per_min"] >= floor else "later"


def do_first_keys(rows: list[dict[str, Any]]) -> list[str]:
    """The keys in one database's Do first tier, from its open action rows. On one database
    time saved orders as the score does."""
    cards = [{"key": row["key"], "score": row["score"], "risk": risk(row),
              "effort": EFFORT.get(row["type"], "code"), "saves_ms_per_min": 0.0}
             for row in rows]
    _tiers(cards, "tier")
    return [card["key"] for card in sorted(cards, key=lambda card: (-card["score"], card["key"]))
            if card["tier"] == "first"]


def file_changes_ready(store: MonitorStore, target: str, now: int,
                       opened: list[dict[str, Any]], *, was_open: bool = True
                       ) -> dict[str, Any] | None:
    """Keep the database's one Changes ready event in step with its plan (decision 2).

    It files when the plan first becomes non-empty or gains a Do first change, and pushes
    only for a Do first change; while open it keeps the count current. It closes by code
    when the plan empties, or when Actions is opened for the database (`mark_opened`)."""
    rows = store.read("SELECT * FROM inbox_item WHERE target=? AND type='changes_ready' "
                      "AND subject=?", (target, CHANGES_SUBJECT))
    item = rows[0] if rows else None
    is_open = item is not None and item["state"] not in ("resolved", "dismissed")
    if not opened:
        if is_open:
            store.action_item(item["id"], "resolve", now, resolved_by="code")
        return None
    previous = json.loads(item["body_json"]) if item else {}
    first = do_first_keys(opened)
    gained = sorted(set(first) - set(previous.get("do_first_keys") or []))
    if not is_open and item is not None and was_open and not gained:
        return None
    ranked = sorted(opened, key=lambda row: (row["key"] not in first, -row["score"], row["key"]))
    count = len(opened)
    body = {"db": target, "count": count, "do_first": len(first), "do_first_keys": first,
            "titles": [row["payload"].get("title") or row["key"] for row in ranked[:3]],
            "action_ids": [row["id"] for row in ranked[:3]],
            "filed_at": previous.get("filed_at", now) if is_open else now}
    title = f"{count} {'change' if count == 1 else 'changes'} ready on {target}"
    return store.upsert_item(target, "changes_ready", CHANGES_SUBJECT, 0.6, title, body, now,
                             delivery="notify" if gained else "inbox", reopen_resolved=True)


def mark_opened(store: MonitorStore, target: str, now: int) -> bool:
    """The user opened Actions for this database: its Changes ready event closes."""
    rows = store.read("SELECT id FROM inbox_item WHERE target=? AND type='changes_ready' "
                      "AND subject=? AND state NOT IN ('resolved','dismissed')",
                      (target, CHANGES_SUBJECT))
    for row in rows:
        store.action_item(row["id"], "resolve", now, resolved_by="code")
    return bool(rows)


def _engine_of(store: MonitorStore, target: str, plan: dict[str, Any] | None) -> str:
    if plan and plan.get("engine"):
        return str(plan["engine"])
    meta = store.read("SELECT engine FROM stats_meta WHERE target=?", (target,))
    return str(meta[0]["engine"]) if meta else "postgresql"


def rollback_sql(row: dict[str, Any], engine: str) -> str | None:
    """The SQL that reverses a change: drop an added index, rebuild a dropped one, set a
    setting back to the value it had."""
    payload = row["payload"]
    detail = payload.get("detail") or {}
    if row["type"] == "add_index":
        return detail.get("undo_sql")
    if row["type"] == "drop_index":
        return detail.get("restore_sql")
    setting = payload.get("setting") or {}
    if row["type"] == "change_setting" and setting.get("name") and setting.get("current"):
        from features.monitor.db_actions import setting_sql

        return setting_sql(str(setting["name"]), str(setting["current"]), engine)
    return None


def _gains(row: dict[str, Any]) -> dict[str, float]:
    """The expected share of each query's time a change saves, where the planner measured it.
    A gain guessed from the analysis's impact label ranks the plan but is never shown."""
    detail = row["payload"].get("detail") or {}
    if row["type"] == "add_index":
        return {helped["key"]: float(helped.get("gain") or 0)
                for helped in detail.get("helps") or [] if helped.get("gain_pct") is not None}
    if row["type"] == "rewrite_query" and detail.get("improvement_pct"):
        gain = min(float(detail["improvement_pct"]), 99.0) / 100
        return {key: gain for key in row["payload"].get("queries") or []}
    return {}


def plan_card(row: dict[str, Any], target: str, names: dict[str, str], minute_ms: float,
              totals: dict[str, dict[str, Any]], engine: str) -> dict[str, Any]:
    """One action as the global Actions page reads it."""
    payload = row["payload"]
    all_ms = sum(float(total.get("total_ms") or 0) for total in totals.values())
    gains = _gains(row)
    queries = []
    for key in payload.get("queries") or []:
        total = totals.get(key) or {}
        calls, spent = float(total.get("calls") or 0), float(total.get("total_ms") or 0)
        mean = spent / calls if calls else None
        gain = gains.get(key)
        queries.append({"key": key, "name": names.get(key) or key,
                        "share": spent / all_ms if all_ms else 0.0, "mean_ms": mean,
                        "expected_ms": mean * (1 - gain) if mean is not None and gain is not None
                        else None})
    result = payload.get("result") or {}
    detail = payload.get("detail") or {}
    return {"id": row["id"], "key": row["key"], "database": target, "type": row["type"],
            "kind": KIND.get(row["type"], "index"), "state": row["state"], "priority": None,
            "rank": None, "risk": risk(row), "effort": EFFORT.get(row["type"], "code"),
            "saves_ms_per_min": saves_ms_per_min(row["score"], minute_ms),
            "score": float(row["score"]), "title": payload.get("title") or row["key"],
            "summary": payload.get("summary") or "", "note": payload.get("note"),
            "why": row.get("why"), "apply": payload.get("apply") or "run_sql",
            "sql": payload.get("sql"), "rollback_sql": rollback_sql(row, engine),
            "restart": bool(payload.get("restart")), "confidence": payload.get("confidence"),
            "table": payload.get("table"), "columns": list(payload.get("columns") or []),
            "index_name": payload.get("index_name"), "setting": payload.get("setting") or None,
            "queries": queries, "share": sum(query["share"] for query in queries),
            "before_ms": result.get("before_ms"), "after_ms": result.get("after_ms"),
            "write_cost": detail.get("write_cost") if row["type"] == "add_index" else None,
            "steps": payload.get("steps"), "evidence": payload.get("evidence") or [],
            "rows": payload.get("rows") or [],
            "original": detail.get("original") if row["type"] == "rewrite_query" else None,
            "opened_at": row["opened_at"],
            "updated_at": row["updated_at"],
            "set_aside_until": row.get("snoozed_until") if row["state"] == "snoozed" else None,
            "closed_at": row.get("closed_at"), "reason": row.get("dismiss_reason"),
            "checkup_rank": payload.get("priority")}


def plan_cards(store: MonitorStore, target: str, now: int) -> list[dict[str, Any]]:
    """Every action on one database as a plan card, the open ones tiered."""
    from features.monitor.naming import target_names

    rows = store.actions(target)
    if not rows:
        return []
    names = target_names(store, target)
    minute_ms, totals = database_minute(store, target, now)
    engine = _engine_of(store, target, store.index_plan(target))
    cards = [plan_card(row, target, names, minute_ms, totals, engine) for row in rows]
    _tiers([card for card in cards if card["state"] == "open"], "priority")
    return cards


def _ranked(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(cards, key=lambda card: (TIERS.index(card["priority"]), *_rank_key(card)))


def database_summary(store: MonitorStore, target: str, now: int,
                     cards: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """One database's plan in numbers: changes to do, how many to do first, and the queries
    an index or a rewrite would speed up with their share of database time."""
    open_cards = [card for card in (plan_cards(store, target, now) if cards is None else cards)
                  if card["state"] == "open"]
    helped = {query["key"]: query["share"] for card in open_cards
              if card["type"] in ("add_index", "rewrite_query") for query in card["queries"]}
    return {"to_do": len(open_cards),
            "do_first": sum(1 for card in open_cards if card["priority"] == "first"),
            "fixes_ready": len(helped), "fixes_share": sum(helped.values()) if helped else None}


def report_checks(store: MonitorStore, target: str) -> dict[str, Any] | None:
    """The latest report's health checks counted as the report counts them, with the ones
    needing you that have no change here, so Actions and the report agree on what it flags."""
    from features.monitor.report import day_snapshot
    from features.monitor.report_v2 import health_rows

    rows = store.read("SELECT * FROM report_run WHERE target=? AND state='done' AND sample=0 "
                      "AND report_json IS NOT NULL ORDER BY COALESCE(period_end,ts) DESC,id DESC "
                      "LIMIT 1", (target,))
    try:
        report = json.loads(rows[0]["report_json"]) if rows else None
    except ValueError:
        return None
    day = day_snapshot(report, rows[0]) if isinstance(report, dict) else None
    if not day:
        return None
    health, fine = health_rows(day, [action for action in day.get("actions") or []
                                     if isinstance(action, dict)], {})
    needing = [row for row in health if row["needs_you"]]
    return {"report_id": rows[0]["id"], "checks": len(health) + len(fine),
            "needs_you": len(needing),
            "not_in_actions": [{"check": row["check"], "label": row["label"],
                                "finding": row["finding"]}
                               for row in needing if not row["in_actions"]]}


def global_page(store: MonitorStore, targets: list[str], now: int, *, db: str | None = None,
                unmonitored: list[str] | None = None) -> dict[str, Any]:
    """GET /actions: the open actions of every database in one ranked list (Do first, Then,
    Later), narrowed to `db` when given, with the counts each filter choice would show."""
    by_target = {target: plan_cards(store, target, now) for target in targets}
    every = [card for target in targets for card in by_target[target]]
    open_cards = _ranked([card for card in every if card["state"] == "open"])
    scoped = [card for card in open_cards if db is None or card["database"] == db]
    for rank, card in enumerate(scoped, 1):
        card["rank"] = rank
    in_scope = [target for target in targets if db is None or target == db]
    plans = {target: store.index_plan(target) for target in in_scope}
    computed = [plan["computed_at"] for plan in plans.values() if plan]
    observed = [min(int(plan.get("observed_s") or 0), PLAN_WINDOW_S)
                for plan in plans.values() if plan]
    databases = [{"database": target, "monitored": target not in (unmonitored or []),
                  "to_do": sum(1 for card in open_cards if card["database"] == target),
                  "do_first": sum(1 for card in open_cards if card["database"] == target
                                  and card["priority"] == "first"),
                  "checkup": _checkup_state(store, target, now)} for target in targets]
    databases += [{"database": name, "monitored": False, "to_do": 0, "do_first": 0,
                   "checkup": None} for name in unmonitored or [] if name not in targets]
    kinds = list(KINDS) + sorted({card["kind"] for card in scoped} - set(KINDS))
    scoped_all = [card for card in every if db is None or card["database"] == db]
    return {"db": db, "databases": databases, "unmonitored": list(unmonitored or []),
            "updated_at": max(computed) if computed else None,
            "observed_s": max(observed) if observed else None,
            "counts": {"by_database": {row["database"]: row["to_do"] for row in databases},
                       "by_kind": {kind: sum(1 for card in scoped if card["kind"] == kind)
                                   for kind in kinds},
                       **{tier: sum(1 for card in scoped if card["priority"] == tier)
                          for tier in TIERS},
                       "total": len(scoped)},
            "cards": scoped,
            "set_aside": [card for card in scoped_all if card["state"] == "snoozed"],
            "dismissed": [card for card in scoped_all if card["state"] == "dismissed"],
            "applied_count": sum(1 for card in scoped_all if card["state"] in APPLIED_STATES
                                 and (card["closed_at"] or 0) >= now - APPLIED_WINDOW_S),
            "keep": {target: len(plan.get("keep") or []) for target, plan in plans.items()
                     if plan},
            "left_out": {target: len(plan.get("skipped") or []) for target, plan in plans.items()
                         if plan},
            "report_checks": {target: checks for target in in_scope
                              if (checks := report_checks(store, target)) is not None}}


def _applied_at(store: MonitorStore, row: dict[str, Any]) -> int:
    """When an undone change had been applied: its last applied event."""
    for event in reversed(store.action_events(row["id"])):
        if event["kind"] in ("applied", "did_it"):
            return int(event["ts"])
    return int(row.get("closed_at") or row["opened_at"])


def _applied_row(store: MonitorStore, row: dict[str, Any], target: str, names: dict[str, str],
                 engine: str, now: int, next_check_at: int | None) -> dict[str, Any]:
    from features.monitor.api.views import stmt_totals

    payload = row["payload"]
    keys = list(payload.get("queries") or [])
    undone = row["state"] == "undone"
    applied_at = _applied_at(store, row) if undone else int(row.get("closed_at") or now)
    settled = applied_at + VERIFY_SETTLE_S
    before = stmt_totals(store, target, applied_at - VERIFY_BEFORE_S, applied_at, keys) \
        if keys else {}
    after = stmt_totals(store, target, settled, (row.get("closed_at") if undone else now) + 1,
                        keys) if keys and now > settled else {}

    def mean(totals: dict[str, Any]) -> float | None:
        calls = float(totals.get("calls") or 0)
        return float(totals.get("total_ms") or 0) / calls if calls else None

    queries = [{"key": key, "name": names.get(key) or key,
                "before_ms": mean(before.get(key) or {}), "after_ms": mean(after.get(key) or {}),
                "calls_after": int((after.get(key) or {}).get("calls") or 0)} for key in keys]
    result = payload.get("result") or {}
    calls_after = sum(query["calls_after"] for query in queries)
    before_ms = result.get("before_ms", mean({
        "calls": sum(float(total.get("calls") or 0) for total in before.values()),
        "total_ms": sum(float(total.get("total_ms") or 0) for total in before.values())}))
    after_ms = result.get("after_ms", mean({
        "calls": calls_after,
        "total_ms": sum(float(total.get("total_ms") or 0) for total in after.values())}))
    status = APPLIED_STATUS[row["state"]]
    minutes = max(1.0, (now - settled) / 60)
    saved = ((before_ms - after_ms) * calls_after / minutes
             if status == "verified" and before_ms is not None and after_ms is not None
             and calls_after else None)
    return {"id": row["id"], "key": row["key"], "database": target, "type": row["type"],
            "kind": KIND.get(row["type"], "index"), "title": payload.get("title") or row["key"],
            "applied_at": applied_at, "by": "user" if row.get("closed_reason") == "user"
            or _did_it(store, row) else "detected",
            "source": row.get("closed_reason"), "status": status,
            "queries": queries, "before_ms": before_ms, "after_ms": after_ms,
            "calls_after": calls_after, "verified_at": result.get("at"),
            "next_check_at": next_check_at if status == "measuring" and keys else None,
            "rollback_sql": rollback_sql(row, engine), "saved_ms_per_min":
            round(saved, 1) if saved is not None else None,
            "undone_at": row.get("closed_at") if undone else None,
            "reason": row.get("dismiss_reason") if undone else None}


def _did_it(store: MonitorStore, row: dict[str, Any]) -> bool:
    return any(event["kind"] == "did_it" for event in store.action_events(row["id"]))


def applied_page(store: MonitorStore, targets: list[str], now: int, *,
                 db: str | None = None) -> dict[str, Any]:
    """GET /actions/applied: the changes applied in the last 30 days by day, newest first,
    each measured against the 7 days before it; then the undone and dismissed ones with
    their reasons, and the week's totals."""
    from features.monitor.naming import target_names
    from shared.monitor.monitor_store import local_day_bounds

    rows, undone, dismissed = [], [], []
    minutes, saved = 0.0, 0.0
    for target in targets:
        if db is not None and target != db:
            continue
        actions = store.actions(target)
        if not actions:
            continue
        names = target_names(store, target)
        plan = store.index_plan(target)
        engine = _engine_of(store, target, plan)
        minute_ms = database_minute(store, target, now)[0]
        minutes += minute_ms
        for row in actions:
            closed = int(row.get("closed_at") or 0)
            if closed < now - APPLIED_WINDOW_S:
                continue
            if row["state"] == "dismissed":
                dismissed.append({"id": row["id"], "database": target, "type": row["type"],
                                  "kind": KIND.get(row["type"], "index"),
                                  "title": row["payload"].get("title") or row["key"],
                                  "dismissed_at": closed, "reason": row.get("dismiss_reason")})
                continue
            if row["state"] not in (*APPLIED_STATES, "undone"):
                continue
            check = max(closed + VERIFY_AFTER_S, int((plan or {}).get("computed_at") or now)
                        + STATS_EVERY_S, now)
            entry = _applied_row(store, row, target, names, engine, now, check)
            (undone if row["state"] == "undone" else rows).append(entry)
            if entry["saved_ms_per_min"] and entry["applied_at"] >= now - 7 * 86400:
                saved += entry["saved_ms_per_min"]
    rows.sort(key=lambda entry: (-entry["applied_at"], -entry["id"]))
    rows = rows[:APPLIED_LIMIT]
    days: list[dict[str, Any]] = []
    for entry in rows:
        start = local_day_bounds(entry["applied_at"])[0]
        if not days or days[-1]["start"] != start:
            days.append({"start": start, "day": datetime.fromtimestamp(start).date().isoformat(),
                         "rows": []})
        days[-1]["rows"].append(entry)
    week = [entry for entry in rows if entry["applied_at"] >= now - 7 * 86400]
    return {"db": db, "since": min((entry["applied_at"] for entry in rows), default=None),
            "days": days,
            "undone": sorted(undone, key=lambda entry: -(entry["undone_at"] or 0)),
            "dismissed": sorted(dismissed, key=lambda entry: -entry["dismissed_at"]),
            "totals": {"changes_this_week": len(week),
                       "verified": sum(1 for entry in week if entry["status"] == "verified"),
                       "measuring": sum(1 for entry in week if entry["status"] == "measuring"),
                       "no_change": sum(1 for entry in week if entry["status"] == "no_change"),
                       "saved_ms_per_min": round(saved, 1),
                       "saved_share": min(1.0, saved / minutes) if minutes else None}}

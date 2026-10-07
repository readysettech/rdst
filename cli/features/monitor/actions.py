"""Actions: the few things to change on a database, each closing on something RDST can measure.

`candidates` turns the index plan, tested rewrites and the checkup's database actions into
action rows with fixed wording per type. `reconcile` merges them with what is stored: new
candidates open, open actions whose fix shows up in the database close as applied, and ones
whose evidence faded retire. A user's dismiss or snooze holds until it lapses.
"""

from __future__ import annotations

import json
import re
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


def _database(actions: list[dict[str, Any]], window: dict[str, Any],
              spills: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    found = []
    for action in actions:
        identity = str(action.get("id") or "")
        if identity.startswith("setting_") or identity in SETTING_IDS:
            kind = "change_setting"
        elif identity in MAINTENANCE_IDS:
            kind = "run_maintenance"
        else:
            continue
        setting = action.get("setting") or {}
        name = setting.get("name")
        # A setting is an action only when the database shows the problem it fixes; one
        # that's merely off a rule of thumb stays in the Reports health checks.
        measured = (action.get("severity") == "bad" or identity in MEASURED_IDS
                    or (name in SPILL_SETTINGS and spills is not None)
                    or (name in BUFFER_SETTINGS and action.get("severity") == "warn"))
        if kind == "change_setting" and not measured:
            continue
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


def candidates(store: MonitorStore, target: str, plan: dict[str, Any],
               queries: list[dict[str, Any]], database: list[dict[str, Any]]) -> list[dict[str, Any]]:
    window = {"observed_s": plan["observed_s"], "counter_age_s": plan["counter_age_s"]}
    # Adds past the plan's first few stay actions, so a reshuffle in rank never retires them;
    # one too small to be worth it stays among the smaller wins.
    found = [_add_index(add, window) for add in plan["add"] + plan.get("later", [])
             if add["benefit"] >= MIN_ADD_BENEFIT]
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


def _stays_closed(row: dict[str, Any], candidate: dict[str, Any], now: int,
                  database_refreshed: bool) -> bool:
    """Whether a closed action stays closed although its candidate is listed again."""
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
        writes.append({**candidate, "state": state, "opened_at": row["opened_at"],
                       "snoozed_until": row["snoozed_until"] if state == "snoozed" else None,
                       "dismiss_reason": row["dismiss_reason"], **closed})
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
    return store.actions(target, ("open",))


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
VERIFY_MIN_CALLS = 20
VERIFIED_GAIN = 0.2
DROP_VERIFIED_GAIN = 0.05


def verify(store: MonitorStore, target: str, now: int) -> None:
    """Measure an applied index or rewrite: the queries it served, before and after the fix.

    Faster by a fifth or more confirms it; no gain by two hours after the fix says it didn't
    help. Changes without queries to measure stay applied."""
    from features.monitor.api.views import stmt_totals

    writes = []
    for row in store.actions(target, ("applied",)):
        keys = row["payload"].get("queries") or []
        closed = int(row.get("closed_at") or 0)
        if row["type"] not in ("add_index", "rewrite_query", "drop_index") or not keys or \
                now - closed < VERIFY_AFTER_S:
            continue

        def mean(start: int, end: int) -> float | None:
            totals = stmt_totals(store, target, start, end, keys).values()
            calls = sum(int(total["calls"] or 0) for total in totals)
            spent = sum(float(total["total_ms"] or 0) for total in totals)
            return spent / calls if calls >= VERIFY_MIN_CALLS else None

        before, after = mean(closed - 3600, closed), mean(closed + 300, now)
        if before is None or after is None:
            continue
        result = {"before_ms": round(before, 3), "after_ms": round(after, 3)}
        # One index less is a smaller gain on each write than a new index is on a read.
        gain = DROP_VERIFIED_GAIN if row["type"] == "drop_index" else VERIFIED_GAIN
        if after <= before * (1 - gain):
            state = "confirmed"
        elif now - closed >= VERIFY_GIVE_UP_S:
            state = "no_effect"
        else:
            continue
        writes.append({**row, "state": state, "payload": {**row["payload"], "result": result},
                       "events": [(state, result)]})
    if writes:
        store.write_actions(target, now, writes)


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


def set_state(store: MonitorStore, target: str, action_id: int, verb: str, now: int, *,
              until: int | None = None, reason: str | None = None) -> dict[str, Any] | None:
    """A user's snooze, dismiss or reopen."""
    rows = [row for row in store.actions(target) if row["id"] == action_id]
    if not rows:
        return None
    row = rows[0]
    if verb == "snooze":
        update = {"state": "snoozed", "snoozed_until": until or now + 7 * 86400}
    elif verb == "dismiss":
        update = {"state": "dismissed", "dismiss_reason": reason, "closed_at": now,
                  "closed_reason": "dismissed"}
    elif verb == "reopen":
        update = {"state": "open", "snoozed_until": None, "dismiss_reason": None,
                  "closed_at": None, "closed_reason": None}
    else:
        raise ValueError(f"Unknown action verb {verb}")
    store.write_actions(target, now, [{**row, **update,
                                       "events": [(verb, {"until": until, "reason": reason})]}])
    return next(item for item in store.actions(target) if item["id"] == action_id)


INDEX_TYPES = ("add_index", "drop_index")
MORE = 5


def _card(row: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    payload = row["payload"]
    return {"id": row["id"], "key": row["key"], "type": row["type"], "state": row["state"],
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


def _checkup_state(store: MonitorStore, target: str, now: int) -> dict[str, Any]:
    """Where the checkups stand: the first pass of analyses, then the latest checkup."""
    from features.monitor.report import first_pass

    progress = first_pass(store, target, now)
    if progress is not None and not progress["due"]:
        return {"state": "first_pass", "analyzed": progress["analyzed"], "total": progress["total"],
                "due_by": progress["due_by"], "report_id": None, "finished_at": None}
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
    cards = [_card(row, names) for row in rows if row["state"] == "open"]
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

"""The why behind each change on Actions: one short model call for the changes that lack one.

Each change is explained once, when it first appears, and keeps its why. A call covers every
change waiting for one, so a database with nothing new makes no call. Calls are capped per
database per day; changes found after the cap wait for the next day's first call.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from features.monitor import actions, database_limit, report_prompt
from shared.agent_activity import recorder
from shared.monitor.monitor_store import MonitorStore

logger = logging.getLogger(__name__)

CALLS_PER_DAY = 3
# Changes that appear close together share one call.
MIN_GAP_S = 15 * 60
MAX_ACTIONS = 8
STATE_KEY = "action_explain"
TRIED_KEPT = 200
# A change the model leaves out is asked about once more before it stays without a why.
ATTEMPTS = 2

SYSTEM_PROMPT = """\
You are a senior database engineer explaining proposed changes to the person who owns one \
database. RDST has already measured the workload and chosen each change; the facts you get \
are computed from its readings. Your job is to say, for each change, why it matters for this \
workload.

Rules:""" + report_prompt.SYSTEM_PROMPT.split("Rules:", 1)[1]

TASK = """\
Answer with one JSON object and nothing else:
{"actions": [{"action_id": "...", "why": "..."}], "index_plan_explanation": "..."}

actions: one entry per item in open_actions, by action_id. why is one sentence tying the \
change to this database's evidence: the queries it touches, their share of database time, \
the writes it costs or saves, or the measured gain. Use only ids from open_actions.

index_plan_explanation: two or three sentences on why index_plan's changes make sense \
together, covering both the reads they speed up and the writes they cost or save. Empty when \
index_plan is null or has no changes."""


def _state(store: MonitorStore, target: str) -> dict[str, Any]:
    rows = store.read("SELECT value FROM monitor_setting WHERE target=? AND key=?",
                      (target, STATE_KEY))
    try:
        state = json.loads(rows[0]["value"]) if rows else {}
    except ValueError:
        state = {}
    return state if isinstance(state, dict) else {}


def _day(now: int) -> str:
    return datetime.fromtimestamp(now).strftime("%Y-%m-%d")


def calls_today(store: MonitorStore, target: str, now: int) -> int:
    state = _state(store, target)
    return int(state.get("calls") or 0) if state.get("day") == _day(now) else 0


def _plan_waiting(plan: dict[str, Any] | None) -> bool:
    return bool(plan and (plan.get("add") or plan.get("drop")) and not plan.get("explanation"))


def waiting(store: MonitorStore, target: str) -> tuple[list[dict[str, Any]], dict | None]:
    """The open changes with no why that no call has tried yet, and the plan if it needs one."""
    tried = _state(store, target).get("tried") or []
    rows = sorted((row for row in store.actions(target, ("open",))
                   if not row.get("why") and tried.count(row["id"]) < ATTEMPTS),
                  key=lambda row: -float(row.get("score") or 0))[:MAX_ACTIONS]
    plan = store.index_plan(target)
    return rows, plan if _plan_waiting(plan) else None


def due(store: MonitorStore, target: str, now: int) -> bool:
    """Something to explain, the day's calls not used up and the last call long enough ago."""
    from features.monitor.report import first_pass

    # The first call waits for the first pass, so it explains everything that pass found.
    progress = first_pass(store, target, now)
    if (progress is not None and not progress["due"]) or database_limit.paused(store, target, now):
        return False
    state = _state(store, target)
    if calls_today(store, target, now) >= CALLS_PER_DAY or \
            now - int(state.get("last_at") or 0) < MIN_GAP_S:
        return False
    rows, plan = waiting(store, target)
    return bool(rows or plan)


def _record(store: MonitorStore, target: str, now: int, *, call: bool = False,
            tried: list[int] = ()) -> None:
    state = _state(store, target)
    calls = calls_today(store, target, now) + (1 if call else 0)
    kept = [*(state.get("tried") or []), *tried][-TRIED_KEPT:]
    store.write_setting(target, STATE_KEY, json.dumps(
        {"day": _day(now), "calls": calls, "last_at": now if call else state.get("last_at"),
         "tried": kept}))


def model_input(rows: list[dict[str, Any]], plan: dict[str, Any] | None) -> dict[str, Any]:
    data = report_prompt.checkup_input(rows, plan, [])
    return {"open_actions": data["open_actions"], "index_plan": data["index_plan"]}


def parse_answer(raw: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """Keep the whys that use only the data's numbers and queries."""
    allowed = report_prompt._allowed(data)
    keys = set(report_prompt._KEY.findall(json.dumps(data)))
    ids = {item["action_id"] for item in data["open_actions"]}
    picks, seen = [], set()
    for item in raw.get("actions") or []:
        if not isinstance(item, dict) or item.get("action_id") not in ids - seen:
            continue
        seen.add(item["action_id"])
        why = report_prompt._clean(item.get("why"), allowed, keys)
        if why:
            picks.append({"action_id": item["action_id"], "why": why})
    plan = data.get("index_plan") or {}
    explanation = report_prompt._clean(raw.get("index_plan_explanation"), allowed, keys) \
        if plan.get("add") or plan.get("drop") else None
    return {"action_picks": picks, "index_plan_explanation": explanation}


def _ask(data: dict[str, Any]) -> dict[str, Any]:
    from shared.llm_manager import LLMManager

    result = LLMManager().generate_response(
        TASK + "\n\nDATA:\n" + json.dumps(data, default=str, ensure_ascii=True),
        purpose="monitor_explain", system_message=SYSTEM_PROMPT, max_tokens=4096,
        temperature=0.0)
    raw = result["response"]
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        match = re.search(r"\{.*\}", text, re.S)
        raw = json.loads(match.group(0) if match else text)
    return raw if isinstance(raw, dict) else {}


def explain(store: MonitorStore, target: str, now: int) -> int:
    """One call for the changes waiting for a why; returns how many got one."""
    from shared.llm_manager.inference_attribution import (
        count_billable_calls, inference_workflow, monitoring_database,
    )

    rows, plan = waiting(store, target)
    if not rows and plan is None:
        return 0
    data = model_input(rows, plan)
    # A call counts against the day's cap whether or not it succeeds, so a failing model
    # can't be retried all day.
    _record(store, target, now, call=True)
    fingerprint = database_limit.target_fingerprint(target)
    with recorder.action("explain_actions", target, subject=_subject(len(rows))) as activity:
        try:
            with count_billable_calls(), monitoring_database(fingerprint), inference_workflow(
                    "monitor", "web", operation="monitor_explain"):
                answer = parse_answer(_ask(data), data)
        except Exception as exc:
            logger.warning("Action explanations unavailable for %s: %s", target, exc)
            if database_limit.is_limit_error(exc):
                database_limit.file_refusal(store, target, now)
            elif allowance := database_limit.is_allowance_error(exc):
                database_limit.file_allowance(store, target, allowance, now)
            activity.outcome = "failed"
            activity.summary = str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__
            return 0
        if answer["action_picks"]:
            # An answer with nothing usable doesn't count against the changes it was given.
            explained = {int(pick["action_id"][1:]) for pick in answer["action_picks"]}
            _record(store, target, now,
                    tried=[row["id"] for row in rows if row["id"] not in explained])
        actions.apply_explanations(store, target, now, answer)
        activity.summary = _subject(len(answer["action_picks"]))
    return len(answer["action_picks"])


def _subject(count: int) -> str:
    return "1 change" if count == 1 else f"{count} changes"

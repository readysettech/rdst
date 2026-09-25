"""Turn measured changes and fixed Jev choices into attention inbox items."""

from __future__ import annotations

import json
import math
import os
import statistics
import time
import uuid
from typing import Any, Callable

from features.monitor import database_limit
from features.monitor.attention import group_many
from features.monitor.delivery import (
    DIGEST_KINDS,
    Relevance,
    change_delivery,
    discovery_until,
    group_delivery,
    is_learning,
    relevance,
)
from features.monitor.presentation import group_title
from features.schema.assessment_context import referenced_tables
from shared import agent_activity
from shared.agent_activity.recorder import judge_summary
from shared.llm_manager.jev_client import ATTENTION_KINDS, JevClient, JevClientError
from shared.llm_manager.inference_attribution import count_billable_calls, monitoring_database
from shared.monitor.monitor_store import MonitorStore, item_query_keys

TITLES = {
    "latency_regression": "Query latency increased",
    "plan_change": "Query plan may have changed",
    "new_heavy_query": "New heavy query",
    "traffic_spike": "Query traffic increased",
    "query_stopped_running": "Query stopped running",
    "table_slowdown": "Queries on one table slowed",
    "stuck_query": "Stuck query",
    "rollback_or_deadlock_spike": "Rollbacks or deadlocks increased",
    "cache_hit_ratio_drop": "Cache hit ratio dropped",
    "slow_creep": "Query latency has crept up",
    "query_improved": "Query improved",
}
MEASURABLE_RECOVERY = {"latency_regression", "plan_change", "table_slowdown",
                       "traffic_spike", "query_stopped_running", "new_heavy_query",
                       "query_improved", "slow_creep"}
CODE_OWNED = {"stuck_query", "connections_near_max", "lock_contention", "monitoring_issue",
              "cache_candidate", "health_item", "health_finding", "daily_summary",
              "rollback_or_deadlock_spike", "cache_hit_ratio_drop"} | MEASURABLE_RECOVERY
RECOVERY_CYCLES = 3
BASELINE_BAND = 0.20
HEAVY_SHARE = 0.05
NEW_QUERY_DAYS = 1
LATENCY_RECOVERY = {"latency_regression", "plan_change", "table_slowdown", "slow_creep"}
GROUP_RECOVERY = LATENCY_RECOVERY | {"traffic_spike", "query_stopped_running",
                                     "new_heavy_query", "query_improved", "slow_creep"}
DATABASE_KINDS = {"rollback_or_deadlock_spike", "cache_hit_ratio_drop"}
# One burst, one item: an open many-queries item takes new members while its last member
# joined within BURST_GAP_S and it started within BURST_SPAN_S; later changes start a new item.
BURST_GAP_S = 11 * 60
BURST_SPAN_S = 30 * 60
SMALLER_KEYS_CAP = 500
# Repeats: a burst led by the same query (most database time at filing) as an earlier burst of
# the last 26 hours, sharing at least half of the smaller of the two query sets, is another
# occurrence of that item. Without load figures, a 70% overlap of the whole sets decides. A repeating item closes once its latest occurrence is back
# to normal and none has started for twice the usual gap between them (at least an hour).
REPEAT_OVERLAP = 0.7
REPEAT_SHARED = 0.5
REPEAT_WINDOW_S = 26 * 3600
REPEAT_QUIET_MIN_S = 3600
OCCURRENCES_CAP = 60
LEGACY_CHANGE_KINDS = {"latency_regression", "plan_change", "table_slowdown", "traffic_spike",
                       "query_stopped_running", "new_heavy_query", "slow_creep",
                       "query_improved", "many_queries_changed"}


def _code_owned_item(item: dict[str, Any]) -> bool:
    return item["type"] in CODE_OWNED or (item["type"] == "many_queries_changed" and
            bool(json.loads(item["body_json"]).get("recovery_queries")))


def recovery_ceiling(normal: float, filed: float | None) -> float:
    """The level a reading must be back under to count as normal: within 20% of its normal
    level, or below the geometric middle of normal and the level at filing (the line the
    incident window draws between changed and ordinary). A query with no traffic before
    has a normal level of zero, so only its quiet rate counts."""
    ceiling = normal * (1 + BASELINE_BAND)
    if normal > 0 and filed and filed > normal:
        ceiling = max(ceiling, math.sqrt(normal * filed))
    return ceiling


def _overlap(first: set[str], second: set[str]) -> float:
    return len(first & second) / max(len(first), len(second)) if first and second else 0.0


def leading_query(filings: dict[str, Any], keys: list[str] | set[str] | None = None) -> str | None:
    """The member with the most database time when it joined (calls a second x latency)."""
    def load(filing: dict[str, Any]) -> float:
        numbers = filing.get("at_filing") or filing.get("before") or {}
        return float(numbers.get("qps") or 0) * float(numbers.get("mean_ms") or 0)

    candidates = {key: filing for key, filing in filings.items()
                  if keys is None or key in keys}
    if not candidates or not any(load(filing) for filing in candidates.values()):
        return None
    return max(sorted(candidates), key=lambda key: load(candidates[key]))


def occurrence_size(filings: dict[str, Any]) -> float | None:
    """How big an occurrence was: the median member's level at filing over its normal level
    (latency for slower members, calls for busier ones)."""
    ratios = []
    for filing in filings.values():
        numbers = filing.get("at_filing") or filing.get("before") or {}
        if filing.get("kind") in LATENCY_RECOVERY | {"slow_creep"}:
            normal, level = filing.get("baseline_mean_ms"), numbers.get("mean_ms")
        else:
            normal, level = filing.get("baseline_qps"), numbers.get("qps")
        if normal and level:
            ratios.append(level / normal)
    return round(statistics.median(ratios), 3) if ratios else None


def occurrences(body: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    """An item's occurrences, oldest first; a burst filed before it repeated is one."""
    if body.get("occurrences"):
        return [dict(entry) for entry in body["occurrences"]]
    ended = [value.get("retired_at") for value in body.get("recovered_queries", {}).values()
             if value.get("retired_at")]
    end = item.get("resolved_at") or (max(ended) if ended and not body.get(
        "recovery_queries") else None)
    filings = {**body.get("recovered_queries", {}), **body.get("recovery_queries", {})}
    return [{"start": body.get("burst_start") or item["created_at"], "end": end,
             "keys": sorted(body.get("episode_query_keys", body.get("query_keys", []))),
             "top": leading_query(filings),
             "size": occurrence_size({**body.get("recovered_queries", {}),
                                      **body.get("recovery_queries", {})}),
             "quiet": False, "verdict": None}]


def repeat_quiet_s(entries: list[dict[str, Any]]) -> int:
    """How long a repeating item waits after its latest occurrence before it closes."""
    starts = [entry["start"] for entry in entries]
    gaps = [later - earlier for earlier, later in zip(starts, starts[1:])]
    return max(REPEAT_QUIET_MIN_S, int(2 * statistics.median(gaps))) if gaps else 0


def _routine(starts: list[int]) -> tuple[int, int] | None:
    """(period, repeats) once occurrences keep a steady schedule (the recurring rule)."""
    from features.monitor.recurring import _steady

    return _steady([later - earlier for earlier, later in zip(starts, starts[1:])])


def _same_pattern(keys: set[str], filings: dict[str, Any] | None,
                  entry: dict[str, Any]) -> bool:
    """Whether a burst of `keys` repeats an earlier occurrence. The lead is compared among the
    queries both share, so a query that wasn't part of the earlier burst (a new query, or an
    unrelated one changing at the same time) can't hide the repeat by outweighing the rest."""
    other = set(entry.get("keys") or [])
    shared = keys & other
    top = leading_query(filings, shared) if filings and shared else None
    if top and entry.get("top"):
        return (top == entry["top"] and
                len(shared) >= REPEAT_SHARED * min(len(keys), len(other)))
    return _overlap(keys, other) >= REPEAT_OVERLAP


def _repeat_of(bursts: list[dict[str, Any]], keys: set[str], filings: dict[str, Any],
               now: int, exclude: dict[str, Any] | None) -> dict[str, Any] | None:
    """The earlier burst item this burst repeats, if any: the same leading query and half the
    queries, lately."""
    found = None
    for burst in bursts:
        if burst is exclude or burst["state"] == "dismissed":
            continue
        last = occurrences(burst["body"], burst)[-1]
        if now - last["start"] <= REPEAT_WINDOW_S and _same_pattern(keys, filings, last):
            found = burst
    return found


def _joinable(body: dict[str, Any], now: int) -> bool:
    """Whether a many-queries item is still the burst new members join."""
    start, last = body.get("burst_start"), body.get("burst_last_join")
    return bool(start and last and now - last <= BURST_GAP_S and now - start <= BURST_SPAN_S)


def _gate_keys(gated: dict[str, dict[str, Any]] | None, reason: str) -> list[str]:
    return sorted(key for key, value in (gated or {}).items() if value.get("reason") == reason)


def gate_evidence(gated: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    """What the shortlist gate kept from Jev this cycle, for the judge run's record."""
    gated = gated or {}
    return {"smaller": _gate_keys(gated, "small")[:SMALLER_KEYS_CAP],
            "minor": _gate_keys(gated, "minor")[:SMALLER_KEYS_CAP],
            "intermittent": _gate_keys(gated, "intermittent")[:SMALLER_KEYS_CAP],
            "recurring": {key: value.get("pattern") for key, value in sorted(gated.items())
                          if value.get("reason") == "recurring"}}


def _note_gate(body: dict[str, Any], gated: dict[str, dict[str, Any]] | None,
               recurring: dict[str, dict[str, Any]] | None, members: list[str]) -> None:
    """Carry this cycle's gate outcome into an item: smaller queries that also moved, members
    whose burst is bigger or longer than their usual pattern, and bursts kept quiet because
    they recur."""
    smaller = set(body.get("smaller_query_keys", [])) | set(_gate_keys(gated, "small"))
    smaller -= set(members)
    if smaller:
        body["smaller_query_keys"] = sorted(smaller)[:SMALLER_KEYS_CAP]
    notes = {key: value for key, value in (recurring or {}).items() if key in members}
    if notes:
        body["recurring"] = {**body.get("recurring", {}), **notes}
    suppressed = {key: value.get("pattern") for key, value in (gated or {}).items()
                  if value.get("reason") == "recurring"}
    if suppressed:
        body["recurring_suppressed"] = {**body.get("recurring_suppressed", {}), **suppressed}


def reconcile_recovery(store: MonitorStore, target: str, now: int,
                       measured: list[dict[str, Any]],
                       flagged: set[str] | None = None) -> set[tuple[str, str]]:
    """Close measurable findings from consecutive observed cycles, without Jev.

    `flagged` holds the queries the measurements shortlist this cycle; an episode without
    per-query baselines closes after three cycles in which none of its queries is flagged.
    """
    by_key = {row["query_key"]: row for row in measured}
    first_seen = {row["query_key"]: row["first_seen"] for row in store.read(
        "SELECT query_key,first_seen FROM query_text WHERE target=?", (target,))}
    settle_s = max(1800, int(os.environ.get("RDST_MONITOR_ATTENTION_SETTLE_S", "86400")))
    collecting = bool(store.read(
        "SELECT 1 FROM db_series WHERE target=? AND res=60 AND bucket_start>=? "
        "AND bucket_start<? LIMIT 1", (target, now - 300, now)))
    closed = set()
    for item in store.inbox(target=target):
        kind = item["type"]
        query_key = json.loads(item["body_json"]).get("query_key")
        if kind != "many_queries_changed" and isinstance(query_key, str):
            item = {**item, "subject": query_key}
        if item["state"] == "resolved":
            body = json.loads(item["body_json"])
            current = by_key.get(item["subject"], {})
            if (kind == "query_stopped_running" and body.get("settled") and
                    current.get("calls")):
                body["settled"] = False
                body["resumed_at"] = now
                body["resolution"] = {"before": body.get("at_filing", {}),
                                      "after": _numbers(current)}
                if body.get("occurrences"):
                    body["occurrences"][-1]["end"] = now
                store.update_item_body(item["id"], body, include_resolved=True)
            if item["resolved_by"] != "code":
                body = json.loads(item["body_json"])
                if not body.get("condition_cleared"):
                    changed = False
                    for key, prior in body.get("recovered_queries", {}).items():
                        if (prior.get("kind") == "query_stopped_running" and
                                not prior.get("resumed_at") and
                                by_key.get(key, {}).get("calls", 0)):
                            prior["resumed_at"] = now
                            changed = True
                    group = body.get("recovery_queries") if kind == "many_queries_changed" else None
                    if group:
                        normal = all(_normal_condition(filing["kind"],
                                     by_key.get(key), filing, collecting=collecting)
                                     for key, filing in group.items())
                    elif kind in MEASURABLE_RECOVERY:
                        baseline = body.get("baseline", {}).get("1h") or {}
                        normal = _normal_condition(kind, by_key.get(item["subject"]),
                                   {"baseline_mean_ms": body.get("baseline_mean_ms",
                                                                 baseline.get("mean_ms")),
                                    "baseline_qps": baseline.get("qps")},
                                   collecting=collecting)
                    else:
                        normal = False
                    streak = int(body.get("clear_streak", 0)) + 1 if normal else 0
                    if streak != body.get("clear_streak", 0):
                        body["clear_streak"] = streak
                        if streak >= RECOVERY_CYCLES:
                            body["condition_cleared"] = True
                            body["condition_cleared_at"] = now
                        changed = True
                    if changed:
                        store.update_item_body(item["id"], body, include_resolved=True)
            continue
        if kind not in MEASURABLE_RECOVERY and kind != "many_queries_changed":
            continue
        body = json.loads(item["body_json"])
        if (kind in {"query_stopped_running", "query_improved"} and
                now - body.get("filed_at", item["created_at"]) >=
                max(1800, int(os.environ.get("RDST_MONITOR_ATTENTION_SETTLE_S", "86400")))):
            current = _numbers(by_key.get(item["subject"], {}))
            body["resolution"] = {"before": body.get("at_filing", {}), "after": current}
            body["numbers"] = current
            # Closed because the settle period ran out, not because a measurement recovered.
            body["settled"] = not (item["type"] == "query_stopped_running" and current["qps"])
            store.update_item_body(item["id"], body)
            store.action_item(item["id"], "resolve", now, resolved_by="code",
                              measured_recovery=True)
            closed.add((kind, item["subject"]))
            continue
        group = body.get("recovery_queries") if kind == "many_queries_changed" else None
        if kind == "many_queries_changed" and group is None:
            if flagged is None:
                continue
            quiet = not set(body.get("query_keys", [])) & flagged
            streak = int(body.get("recovery_streak", 0)) + 1 if quiet else 0
            if streak == body.get("recovery_streak", 0):
                continue
            body["recovery_streak"] = streak
            if streak >= RECOVERY_CYCLES:
                current = {key: _numbers(by_key.get(key, {})) for key in body.get("query_keys", [])}
                body["resolution"] = {"before": body.get("reported_numbers", {}),
                                      "after": current}
                store.update_item_body(item["id"], body)
                store.action_item(item["id"], "resolve", now, resolved_by="code",
                                  measured_recovery=True)
                closed.add((kind, item["subject"]))
            else:
                store.update_item_body(item["id"], body)
            continue
        if (kind == "many_queries_changed" and not group and group is not None and
                len(body.get("occurrences", [])) >= 2):
            if _repeat_waits(store, item, body, now, flagged):
                continue
        if kind == "many_queries_changed" and not group:
            if group is not None:
                retired = body.get("recovered_queries", {})
                body["resolution"] = {
                    "before": {key: value["before"] for key, value in retired.items()},
                    "after": {key: value["after"] for key, value in retired.items()}}
                body["settled"] = any(value.get("settled") for value in retired.values())
                body["numbers"] = body["resolution"]["after"]
                store.update_item_body(item["id"], body)
                store.action_item(item["id"], "resolve", now, resolved_by="code",
                                  measured_recovery=not body["settled"])
                closed.add((kind, item["subject"]))
            continue
        if now < body.get("recovery_last_ts", 0) + 300:
            continue
        row = by_key.get(item["subject"])
        if group:
            rows = {key: by_key.get(key) for key in group}
            retired = body.setdefault("recovered_queries", {})
            changed = False
            for key, prior in retired.items():
                if (prior.get("kind") == "query_stopped_running" and
                        not prior.get("resumed_at") and by_key.get(key, {}).get("calls", 0)):
                    prior["resumed_at"] = now
                    changed = True
            for key, filing in list(group.items()):
                value = rows[key]
                normal_ms = filing.get("baseline_mean_ms") or 0
                normal_qps = filing.get("baseline_qps") or 0
                member_kind = filing.get("kind", "latency_regression")
                filed_at_numbers = filing.get("at_filing") or {}
                if member_kind in LATENCY_RECOVERY | {"slow_creep"}:
                    # No calls while RDST is collecting: its slow calls have ended.
                    recovered = bool((collecting and not (value or {}).get("calls")) or (
                        value and value["calls"] and normal_ms and
                        value["mean_ms"] <= recovery_ceiling(
                            normal_ms, filed_at_numbers.get("mean_ms"))))
                elif member_kind == "query_improved":
                    recovered = bool(value and value["calls"] and normal_ms and
                                     value["mean_ms"] >= normal_ms * (1 - BASELINE_BAND))
                elif member_kind == "traffic_spike":
                    recovered = bool((value or collecting) and
                                     (value or {}).get("qps", 0) <= recovery_ceiling(
                                         normal_qps, filed_at_numbers.get("qps")))
                elif member_kind == "query_stopped_running":
                    recovered = bool(value and value["calls"])
                else:
                    recovered = bool((collecting and (not value or not value["calls"])) or
                                     (value and value["calls"] and
                                      (now - first_seen.get(key, now) >= NEW_QUERY_DAYS * 86400 or
                                       value["db_time_share"] < HEAVY_SHARE)))
                streak = int(filing.get("recovery_streak", 0)) + 1 if recovered else 0
                if streak != filing.get("recovery_streak", 0):
                    filing["recovery_streak"] = streak
                    changed = True
                filed_at = filing.get("filed_at", item["created_at"])
                settled = (member_kind == "query_stopped_running" and not recovered
                           and now - filed_at >= settle_s)
                if member_kind == "query_improved" and now - filed_at >= settle_s:
                    settled = True
                if (member_kind != "query_stopped_running" and not settled
                        and collecting and not (value or {}).get("calls")):
                    observations = store.read(
                        "SELECT res,MAX(bucket_start + res) AS seen FROM stmt_series "
                        "WHERE target=? AND query_key=? AND calls>0 "
                        "GROUP BY res ORDER BY res LIMIT 1", (target, key))
                    last_calls = observations[0]["seen"] if observations else None
                    settled = now - (last_calls or filed_at) >= settle_s
                if streak >= RECOVERY_CYCLES or settled:
                    retired[key] = {"kind": member_kind, "before": filing["at_filing"],
                                    "after": _numbers(value or {}), "retired_at": now,
                                    "filed_at": filed_at,
                                    "settled": (member_kind == "query_stopped_running"
                                                and settled and not recovered),
                                    "baseline_mean_ms": normal_ms,
                                    "baseline_qps": normal_qps}
                    del group[key]
                    changed = True
            if not changed:
                continue
            body["recovery_last_ts"] = now
            body["query_keys"] = list(group)
            if group:
                if (item["state"] != "dismissed" and
                        len(retired) != len(json.loads(item["body_json"]).get("recovered_queries", {}))):
                    store.upsert_item(target, kind, item["subject"], item["severity"],
                                      item["title"], body, now, notify=False)
                else:
                    store.update_item_body(item["id"], body)
                continue
            body.pop("still_happening", None)
            if len(body.get("occurrences", [])) >= 2 and _repeat_waits(
                    store, item, body, now, flagged):
                continue
            body["resolution"] = {
                "before": {key: value["before"] for key, value in retired.items()},
                "after": {key: value["after"] for key, value in retired.items()}}
            body["settled"] = any(value.get("settled") for value in retired.values())
            body["numbers"] = body["resolution"]["after"]
            store.update_item_body(item["id"], body)
            store.action_item(item["id"], "resolve", now, resolved_by="code",
                              measured_recovery=not body["settled"])
            closed.add((kind, item["subject"]))
            continue
        else:
            observed = row is not None or (kind in {"new_heavy_query", "traffic_spike"}
                                           and collecting)
        if not observed:
            if body.get("recovery_streak"):
                body["recovery_streak"] = 0
                body["recovery_last_ts"] = now
                store.update_item_body(item["id"], body)
            continue
        baseline = body.get("baseline", {}).get("1h") or {}
        current = _numbers(row or {})
        filed_numbers = body.get("at_filing") or {}
        if kind in LATENCY_RECOVERY:
            normal = baseline.get("mean_ms") or 0
            recovered = bool(row["calls"] and normal and current["mean_ms"] <= recovery_ceiling(
                normal, filed_numbers.get("mean_ms")))
        elif kind == "traffic_spike":
            normal = baseline.get("qps") or 0
            recovered = current["qps"] <= recovery_ceiling(normal, filed_numbers.get("qps"))
        elif kind == "query_stopped_running":
            recovered = row["calls"] > 0
        elif kind == "query_improved":
            normal = body.get("baseline_mean_ms", baseline.get("mean_ms")) or 0
            recovered = bool(row["calls"] and normal and
                             current["mean_ms"] >= normal * (1 - BASELINE_BAND))
        else:
            age = now - first_seen.get(item["subject"], now)
            recovered = bool((collecting and not (row or {}).get("calls")) or
                             (row and row["calls"] and
                              (age >= NEW_QUERY_DAYS * 86400 or
                               current["db_time_share"] < HEAVY_SHARE)))
        streak = int(body.get("recovery_streak", 0)) + 1 if recovered else 0
        if streak == body.get("recovery_streak", 0) and not recovered:
            continue
        body["recovery_streak"] = streak
        body["recovery_last_ts"] = now
        if recovered and (streak >= RECOVERY_CYCLES or
                          kind == "query_stopped_running" or
                          (kind == "new_heavy_query" and age >= NEW_QUERY_DAYS * 86400)):
            body.pop("still_happening", None)
            if kind == "query_stopped_running":
                body["resumed_at"] = now
                if body.get("occurrences"):
                    body["occurrences"][-1]["end"] = now
            before = body.get("at_filing", {})
            body["resolution"] = {"before": before, "after": current}
            body["numbers"] = current
            store.update_item_body(item["id"], body)
            store.action_item(item["id"], "resolve", now, resolved_by="code",
                              measured_recovery=True)
            closed.add((kind, item["subject"]))
        else:
            store.update_item_body(item["id"], body)
    return closed


def _repeat_waits(store: MonitorStore, item: dict[str, Any], body: dict[str, Any], now: int,
                  flagged: set[str] | None) -> bool:
    """For a repeating item whose latest occurrence has no member still changed: note when it
    ended, and keep the item open (True) until the quiet period has passed."""
    entries = body["occurrences"]
    last = entries[-1]
    if last.get("end") is None:
        if flagged is not None and set(last.get("keys", [])) & flagged:
            store.update_item_body(item["id"], body)
            return True
        last["end"] = now
    body["repeat_quiet_s"] = repeat_quiet_s(entries)
    if now - last["start"] >= body["repeat_quiet_s"]:
        return False
    store.update_item_body(item["id"], body)
    return True


def reconcile_delivery(store: MonitorStore, target: str, now: int, rel: Relevance, *,
                       learning: bool) -> None:
    """Per-cycle code rules that need no Jev call.

    Database-wide items close after three cycles whose measurements no longer show the
    change. A new query's digest item moves up once it has stayed in the top 10 by load
    across two consecutive checks.
    """
    rates = None
    reports = sorted((item for item in store.inbox(target=target)
                      if item["type"] == "daily_summary" and
                      item["state"] not in ("resolved", "dismissed")),
                     key=lambda item: (item["created_at"], item["id"]))
    if len(reports) > 1:
        from features.monitor.report import close_older_reports

        close_older_reports(store, target, reports[-1]["id"], now)
    for item in store.inbox(target=target):
        if item["state"] in ("resolved", "dismissed"):
            continue
        kind = item["type"]
        body = json.loads(item["body_json"])
        if kind in DATABASE_KINDS and item["subject"] == "database":
            rates = rates if rates is not None else (database_rates(store, target, now) or {})
            if not rates:
                continue
            current = _database_numbers(kind, rates)
            if database_change(kind, rates):
                legacy = not set(current) <= set(body.get("numbers") or {})
                if body.get("recovery_streak") or legacy:
                    body["recovery_streak"] = 0
                    if legacy:
                        # Filed before rates were recorded: show the measured change.
                        body["numbers"] = current
                    store.update_item_body(item["id"], body)
                continue
            body["recovery_streak"] = int(body.get("recovery_streak", 0)) + 1
            if body["recovery_streak"] >= RECOVERY_CYCLES:
                body["resolution"] = {"before": body.get("at_filing", {}), "after": current}
                body["numbers"] = current
                store.update_item_body(item["id"], body)
                store.action_item(item["id"], "resolve", now, resolved_by="code",
                                  measured_recovery=True)
            else:
                store.update_item_body(item["id"], body)
        elif kind == "many_queries_changed" and not body.get("burst_start"):
            # One item for every burst: the older single database-wide episode closes, and the
            # changes still measured file as bursts of their own.
            body["retired"] = "burst_split"
            store.update_item_body(item["id"], body)
            store.demote_to_digest(item["id"])
            store.action_item(item["id"], "resolve", now, resolved_by="code")
        elif "relevance_rule" not in body and item["delivery"] != "digest" and (
                kind in LEGACY_CHANGE_KINDS):
            # Filed before the notification model: put it through the relevance gate once.
            keys = (body.get("query_keys") or [] if kind == "many_queries_changed"
                    else [item["subject"]])
            rules = [rel.rule(key) for key in keys]
            body["relevance_rule"] = next((rule for rule in rules if rule), None)
            if kind != "many_queries_changed":
                body.update(rel.evidence(item["subject"]))
            store.update_item_body(item["id"], body)
            quiet = kind in {"slow_creep", "query_improved"} or not any(rules)
            if quiet:
                store.demote_to_digest(item["id"])
        elif kind == "new_heavy_query" and item["delivery"] == "digest" and not learning:
            key = item["subject"]
            _count_top_check(body, key, rel, now)
            level = change_delivery(kind, body.get("attention", "inbox"), key, rel,
                                    learning=False, top_checks=body["top_checks"])
            store.update_item_body(item["id"], body)
            if level != "digest":
                store.promote_item(item["id"], level, now)


def _numbers(row: dict[str, Any]) -> dict[str, float]:
    return {name: float(row.get(name) or 0) for name in ("mean_ms", "qps", "db_time_share")}


def _baseline_mean(row: dict[str, Any], kind: str) -> float:
    baselines = row.get("baseline", {})
    if kind == "query_improved":
        return max((value.get("mean_ms") or 0 for value in baselines.values() if value),
                   default=0)
    return (baselines.get("1h") or {}).get("mean_ms") or 0


def _recoverable_member(answer: dict[str, Any], row: dict[str, Any]) -> bool:
    """Whether code can tell when this member is back to normal. A query with no traffic in
    the hour before is back to normal once it goes quiet again."""
    kind = answer["kind"]
    return kind in GROUP_RECOVERY and (
        kind != "query_improved" or
        (row.get("baseline", {}).get("1h") or {}).get("mean_ms", 0) > 0)


def _member_filing(answer: dict[str, Any], row: dict[str, Any], now: int) -> dict[str, Any]:
    """What a group member measured when it joined: its kind, its normal level and now."""
    return {"kind": answer["kind"], "baseline_mean_ms": _baseline_mean(row, answer["kind"]),
            "baseline_qps": (row.get("baseline", {}).get("1h") or {}).get("qps", 0),
            "at_filing": _numbers(row), "filed_at": now}


def _new_episode(answer: dict[str, Any], row: dict[str, Any], closed: dict[str, Any],
                 now: int) -> bool:
    """After a burst closed, its members return only as a new episode: a different kind of
    change, a measured recurrence after recovering, or a further 2x."""
    key, kind = answer["query_key"], answer["kind"]
    if key not in set(closed.get("episode_query_keys", closed.get("query_keys", []))):
        return True
    reported = closed.get("reported_numbers", {})
    if (kind in LATENCY_RECOVERY | {"traffic_spike"} and key in reported and
            _material_worsening(_numbers(row), reported[key], kind)):
        return True
    prior = {**closed.get("recovered_queries", {}),
             **closed.get("recovery_queries", {})}.get(key)
    if not prior:
        return False
    if prior.get("kind") and prior["kind"] != kind:
        return True
    return (key in closed.get("recovered_queries", {}) and
            _recoverable_member(answer, row) and
            _recurred_condition(kind, row, prior, _member_filing(answer, row, now)))


WORSE_FACTOR = 2.0


def _material_worsening(current: dict[str, float], previous: dict[str, float],
                        kind: str = "latency_regression") -> bool:
    """A new episode: a further 2x on top of the level the episode was last reported at.

    Latency kinds compare mean latency, a traffic spike compares calls a second, and other
    kinds compare the query's work (calls a second times mean latency).
    """
    if kind in LATENCY_RECOVERY:
        old, new = previous.get("mean_ms", 0), current["mean_ms"]
    elif kind == "traffic_spike":
        old, new = previous.get("qps", 0), current["qps"]
    else:
        old = previous.get("qps", 0) * previous.get("mean_ms", 0)
        new = current["qps"] * current["mean_ms"]
    return bool(old > 0 and new >= old * WORSE_FACTOR)


def _recurred_condition(kind: str, row: dict[str, Any],
                        retired: dict[str, Any], filing: dict[str, Any]) -> bool:
    """A retired group member needs a measured new condition to rejoin."""
    after = retired["after"]
    if kind in LATENCY_RECOVERY | {"slow_creep"}:
        baseline = (retired.get("baseline_mean_ms") or filing.get("baseline_mean_ms") or
                    after.get("mean_ms", 0))
        return bool(row["calls"] and baseline and row["mean_ms"] > baseline * 1.2)
    if kind == "traffic_spike":
        baseline = (retired.get("baseline_qps") or filing.get("baseline_qps") or
                    after.get("qps", 0))
        return bool(row["calls"] and baseline and row["qps"] > baseline * 1.2)
    if kind == "query_stopped_running":
        return not row["calls"] and (after.get("qps", 0) > 0 or
                                    bool(retired.get("resumed_at")))
    if kind == "query_improved":
        return bool(row["calls"] and row["mean_ms"] < after.get("mean_ms", 0) * 0.8)
    if kind == "new_heavy_query":
        return bool(row["calls"] and row["db_time_share"] >= HEAVY_SHARE)
    return True


def _normal_condition(kind: str, row: dict[str, Any] | None,
                      filing: dict[str, Any], *, collecting: bool = False) -> bool:
    if not row:
        return collecting and kind in {"traffic_spike", "new_heavy_query"}
    mean = filing.get("baseline_mean_ms") or 0
    qps = filing.get("baseline_qps") or 0
    if kind in LATENCY_RECOVERY | {"slow_creep"}:
        return bool(row["calls"] and mean and row["mean_ms"] <= mean * (1 + BASELINE_BAND))
    if kind == "query_improved":
        return bool(row["calls"] and mean and row["mean_ms"] >= mean * (1 - BASELINE_BAND))
    if kind == "query_stopped_running":
        return bool(row["calls"])
    if kind == "traffic_spike":
        return row["qps"] <= qps * (1 + BASELINE_BAND)
    if kind == "new_heavy_query":
        return not row["calls"] or row["db_time_share"] < HEAVY_SHARE
    return False


def _within_filed_latency_band(row: dict[str, Any], filing: dict[str, Any]) -> bool:
    mean = filing.get("baseline_mean_ms") or 0
    return bool(row["calls"] and mean and
                mean * (1 - BASELINE_BAND) <= row["mean_ms"] <=
                mean * (1 + BASELINE_BAND))


def _supports_answer(kind: str, row: dict[str, Any]) -> bool:
    """Ignore a stale verdict when current measurements cannot show its condition."""
    if kind == "query_stopped_running":
        return not row["calls"]
    if kind not in GROUP_RECOVERY:
        return True
    if not row["calls"]:
        return False
    baselines = [value for value in row.get("baseline", {}).values() if value]
    if kind in LATENCY_RECOVERY:
        return any(value.get("mean_ms", 0) > 0 and
                   row["mean_ms"] > value["mean_ms"] * (1 + BASELINE_BAND)
                   for value in baselines)
    if kind == "traffic_spike":
        return any(value.get("qps", 0) > 0 and
                   row["qps"] > value["qps"] * (1 + BASELINE_BAND)
                   for value in baselines)
    if kind == "new_heavy_query":
        return row["db_time_share"] >= HEAVY_SHARE
    return True


def database_rates(store: MonitorStore, target: str, now: int) -> dict[str, Any] | None:
    """Per-minute rollbacks and deadlocks, and the cache hit ratio, for the last five minutes
    and the hour before, from the recorded database series. None without enough readings."""
    rows = store.read(
        "SELECT bucket_start,xact_rollback,deadlocks,cache_hit_ratio FROM db_series "
        "WHERE target=? AND res=60 AND bucket_start>=? AND bucket_start<?",
        (target, now - 3900, now))
    recent = [row for row in rows if row["bucket_start"] >= now - 300]
    prior = [row for row in rows if row["bucket_start"] < now - 300]
    if not recent or len(prior) < 5:
        return None

    def mean(samples: list[dict[str, Any]], metric: str) -> float | None:
        values = [row[metric] for row in samples if row[metric] is not None]
        return sum(values) / len(values) if values else None

    return {"rollbacks_per_min": mean(recent, "xact_rollback") or 0.0,
            "rollbacks_per_min_before": mean(prior, "xact_rollback") or 0.0,
            "deadlocks_per_min": mean(recent, "deadlocks") or 0.0,
            "deadlocks_per_min_before": mean(prior, "deadlocks") or 0.0,
            "cache_hit_ratio": mean(recent, "cache_hit_ratio"),
            "cache_hit_ratio_before": mean(prior, "cache_hit_ratio")}


def database_change(kind: str, rates: dict[str, Any] | None) -> bool:
    """Whether the measurements show the database-wide change, not just a query mover.

    Rollbacks must at least double and add one a minute; any deadlocks must at least double
    from their prior rate; the cache hit ratio must fall 5% below its prior hour.
    """
    if not rates:
        return False
    if kind == "rollback_or_deadlock_spike":
        rollbacks, before = rates["rollbacks_per_min"], rates["rollbacks_per_min_before"]
        deadlocks, deadlocks_before = rates["deadlocks_per_min"], rates["deadlocks_per_min_before"]
        return ((rollbacks >= 1 and rollbacks >= max(before * 2, before + 1)) or
                (deadlocks > 0 and deadlocks >= deadlocks_before * 2))
    ratio, baseline = rates["cache_hit_ratio"], rates["cache_hit_ratio_before"]
    return bool(ratio is not None and baseline and ratio < baseline * 0.95)


def _supports_database_answer(store: MonitorStore, target: str, now: int,
                              kind: str) -> bool:
    """Database-wide changes need database measurements, not a query mover."""
    return database_change(kind, database_rates(store, target, now))


def _database_numbers(kind: str, rates: dict[str, Any]) -> dict[str, float]:
    fields = (("rollbacks_per_min", "rollbacks_per_min_before", "deadlocks_per_min",
               "deadlocks_per_min_before") if kind == "rollback_or_deadlock_spike" else
              ("cache_hit_ratio", "cache_hit_ratio_before"))
    return {name: float(rates[name] or 0) for name in fields}


def _count_top_check(body: dict[str, Any], key: str, rel: Relevance, now: int) -> None:
    """Count consecutive checks with a new query in the top 10 by last-hour load."""
    if body.get("top_check_ts") == now:
        return
    body["top_checks"] = int(body.get("top_checks", 0)) + 1 if rel.hour.top(key) else 0
    body["top_check_ts"] = now


def _body_numbers(body: dict[str, Any]) -> dict[str, float]:
    return {key: float(value) for key, value in body.items()
            if isinstance(key, str) and len(key) <= 64 and type(value) in (int, float)
            and math.isfinite(value) and value >= 0}


def _finite(numbers: dict[str, Any]) -> dict[str, float]:
    """Only the finite numbers of a snapshot; an item filed with a missing reading keeps the rest."""
    return {key: float(value) for key, value in (numbers or {}).items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0}


def _request(store: MonitorStore, target: str, now: int, chosen: list[dict[str, Any]],
             context: dict[str, Any]) -> dict[str, Any]:
    eligible = store.full_detail_keys(target)
    chosen = [row for row in chosen if row["query_key"] in eligible]
    texts = {row["query_key"]: row["text"] for row in store.read(
        "SELECT query_key,text FROM query_text WHERE target=? "
        "AND query_key IN (SELECT value FROM json_each(?))",
        (target, json.dumps([row["query_key"] for row in chosen])))}
    queries = []
    measured = {row["query_key"]: row for row in context.get("measured", chosen)}
    for row in chosen:
        sql = texts.get(row["query_key"]) or "Query text unavailable"
        try:
            tables = referenced_tables(sql)
        except Exception:
            tables = []
        baselines = row.get("baseline", {})
        one_hour = baselines.get("1h") or {}
        current = {name: row.get(name, 0) for name in
                   ("calls", "total_ms", "mean_ms", "qps", "db_time_share", "temp_blks")}
        queries.append({
            "query_key": row["query_key"], "normalized_sql": sql[:8192],
            "tables": tables[:32], "db_time_share": row["db_time_share"],
            "current_5m": current,
            "baseline": {window: {name: value.get(name, 0) for name in
                                  ("calls", "total_ms", "mean_ms", "qps", "db_time_share", "temp_blks")}
                         if (value := baselines.get(window)) else None
                         for window in ("1h", "1d", "7d")},
            "extra_db_time_ms_per_5m_vs_1h": max(
                0, current["total_ms"] - one_hour.get("qps", 0) * 300 *
                one_hour.get("mean_ms", 0)),
            # Index changes stay out of every decision; the timeline shows them as facts.
            "recent_index_changes": [],
            "ratios": {window: {"mean_latency": ratios["mean_ms"],
                                "qps": ratios["qps"], "db_time_share": ratios["db_time_share"]}
                       for window, ratios in row["ratios"].items()},
            "flags": {"new": row["reason"] == "new_heavy", "stuck": row["reason"] == "stuck",
                      "stopped": row["reason"] == "stopped"},
        })
    db = context.get("database") or {}
    locks = store.read("SELECT MAX(lock_waits) peak FROM db_series WHERE target=? "
                       "AND res=60 AND bucket_start>? AND bucket_start<=?",
                       (target, now - 300, now))
    target_numbers = {
        # Unreadable lock waits count as none: the Key Service accepts only finite numbers.
        "lock_waits": float(db.get("lock_waits") or 0),
        "lock_waits_max_5m": float(locks[0]["peak"] or 0),
        "total_qps": float(db.get("calls") or 0) / 60,
        "connections": float(db.get("active_conns") or 0),
        "max_connections": float(db.get("max_conns") or 0),
        "cache_hit_ratio": min(1.0, max(0.0, float(db.get("cache_hit_ratio") or 0))),
        "rollbacks": float(db.get("xact_rollback") or 0),
        "deadlocks": float(db.get("deadlocks") or 0),
    }
    open_items = []
    candidates = [item for item in context["inbox"] if item["state"] != "resolved"
                  and item["type"] != "health_finding"
                  and set(item_query_keys(item["subject"], json.loads(item["body_json"]))) <= eligible]
    for item in sorted(candidates, key=_code_owned_item)[:30]:
        body = json.loads(item["body_json"])
        filing = body.get("at_filing", body.get("numbers", _body_numbers(body)))
        if "at_filing" not in body:
            created = store.read(
                "SELECT detail_json FROM inbox_event WHERE item_id=? AND kind='created' "
                "ORDER BY id LIMIT 1", (item["id"],))
            if created:
                original = json.loads(created[0]["detail_json"])
                filing = original.get("numbers", _body_numbers(original))
        current = measured.get(item["subject"])
        if current:
            now_numbers = _numbers(current)
        elif item["subject"] == "database":
            now_numbers = {key: float(db.get(key) or target_numbers.get(key) or 0)
                           for key in filing}
        else:
            now_numbers = {key: float(target_numbers.get(key, db.get(key, 0)) or 0)
                           for key in filing}
        open_items.append({"id": str(item["id"]), "type": item["type"],
                           "subject": item["subject"], "state": item["state"],
                           "at_filing": _finite(filing), "now": _finite(now_numbers)})
    return {"request_id": str(uuid.uuid4()), "target_numbers": target_numbers,
            "index_changes": [], "open_items": open_items[:30], "queries": queries}


def _check_outcome(store: MonitorStore, target: str, after_id: int) -> dict[str, Any]:
    """How the check that just ran for `target` ended, from its judge_run row."""
    rows = store.read("SELECT ts,outcome,dry_run FROM judge_run WHERE target=? AND id>? "
                      "ORDER BY id DESC LIMIT 1", (target, after_id))
    if not rows:
        return {}
    filed = 0
    if rows[0]["outcome"] == "filed":
        filed = store.read(
            "SELECT COUNT(*) AS n FROM inbox_event e JOIN inbox_item i ON i.id=e.item_id "
            "WHERE i.target=? AND e.ts>=? AND e.kind IN ('created','reopened','worse')",
            (target, rows[0]["ts"]))[0]["n"]
    outcome, summary = judge_summary(rows[0]["outcome"], filed, bool(rows[0]["dry_run"]))
    return {"outcome": outcome, "summary": summary, "items_filed": filed,
            "judge_outcome": rows[0]["outcome"]}


class AttentionJudge:
    def __init__(self, store: MonitorStore, *, client: Any = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.client = client or JevClient()
        self.clock = clock

    def __call__(self, chosen: list[dict[str, Any]], context: dict[str, Any]) -> None:
        """One attention check, recorded as one agent activity entry."""
        target = context["target"]
        with agent_activity.action("attention_check", target, keep=True,
                                   shortlisted=len(chosen)) as act:
            last = self.store.read("SELECT COALESCE(MAX(id), 0) AS id FROM judge_run")[0]["id"]
            self._judge(chosen, context)
            act.describe(**_check_outcome(self.store, target, last))

    def _judge(self, chosen: list[dict[str, Any]], context: dict[str, Any]) -> None:
        target = context["target"]
        now = int(self.clock())
        eligible = self.store.full_detail_keys(target)
        chosen = [row for row in chosen if row["query_key"] in eligible]
        just_closed = set()
        rel = context.get("relevance") or relevance(self.store, target, now)
        learning = is_learning(self.store, target, now)
        if os.environ.get("RDST_MONITOR_JUDGE_DRY_RUN") != "1" and "measured" in context:
            just_closed = reconcile_recovery(
                self.store, target, now, context["measured"],
                {row["query_key"] for row in chosen} |
                set(_gate_keys(context.get("gated"), "recurring")))
            self.note_routine_occurrences(target, now, context.get("gated"))
            reconcile_delivery(self.store, target, now, rel, learning=learning)
        context = {**context, "inbox": self.store.inbox(target=target),
                   "just_closed": just_closed, "relevance": rel, "learning": learning}
        context["inbox"] = [item for item in context["inbox"] if
            set(item_query_keys(item["subject"], json.loads(item["body_json"]))) <= eligible]
        # Jev reassesses every cycle that has a shortlist or an open item; an empty cycle
        # costs nothing. The daily cap is a safety net.
        reassess = any(item["state"] != "resolved" for item in context["inbox"])
        gate = gate_evidence(context.get("gated"))
        if not chosen and not reassess:
            self.store.record_judge_run(target, now, chosen, outcome="empty",
                                        response={"gate": gate} if context.get("gated") else None)
            return
        if not database_limit._signed_in():
            database_limit.hold_signed_out(self.store, target, now)
            self.store.record_judge_run(target, now, chosen, outcome="signed_out")
            return
        # The account was full when this database was turned on: local monitoring only.
        if database_limit.ai_state(self.store, target) == "local":
            self.store.record_judge_run(target, now, chosen, outcome="local_only")
            return
        # The account's Jev allowance is used up: one check retries now and then.
        if database_limit.allowance_paused(self.store, "jev", now):
            self.store.record_judge_run(target, now, chosen, outcome="allowance")
            return
        attempted = self.store.read(
            "SELECT COUNT(*) AS n FROM judge_run WHERE target=? AND ts>=? AND ts<? "
            "AND request_json IS NOT NULL", (target, now // 86400 * 86400,
                                                now // 86400 * 86400 + 86400))[0]["n"]
        cap = max(0, int(os.environ.get("RDST_MONITOR_JEV_DAILY_CAP", "300")))
        if attempted >= cap:
            self.store.record_judge_run(target, now, chosen, outcome="daily_cap")
            return
        last_failure = self.store.read(
            "SELECT ts,response_json,outcome FROM judge_run WHERE target=? "
            "AND request_json IS NOT NULL "
            "ORDER BY ts DESC,id DESC LIMIT 1", (target,))
        if last_failure and last_failure[0]["outcome"] == "error":
            data = json.loads(last_failure[0]["response_json"] or "{}")
            if now < last_failure[0]["ts"] + data.get("retry_after", 0):
                self.store.record_judge_run(target, now, chosen, outcome="backoff")
                return
        request = _request(self.store, target, now, chosen, context)
        dry_run = os.environ.get("RDST_MONITOR_JUDGE_DRY_RUN") == "1"
        with count_billable_calls() as calls, monitoring_database(
                database_limit.target_fingerprint(target)):
            try:
                verdict = self.client.attention(**request)
                # The gate is RDST's own record; Jev never sees it.
                request = {**request, "gate": gate}
            except JevClientError as exc:
                allowance = database_limit.is_allowance_error(exc)
                delay = database_limit.ALLOWANCE_RETRY_S if allowance else (
                    exc.retry_after if exc.retry_after is not None else
                    86400 - now % 86400 if exc.code == "JEV_BUDGET_EXHAUSTED" else
                    database_limit.RETRY_S if exc.code == database_limit.LIMIT_CODE else
                    300 if exc.code == "LOGIN_REQUIRED" else 60)
                self.store.record_judge_run(target, now, chosen, request=request,
                                            response={"code": exc.code, "retry_after": delay},
                                            dry_run=dry_run, outcome="error",
                                            billable_calls=calls.count)
                if exc.code == database_limit.LIMIT_CODE:
                    database_limit.file_refusal(self.store, target, now)
                if allowance:
                    database_limit.file_allowance(self.store, target, allowance, now)
                # One refused call can be a token refresh race; two in a row is a real
                # sign-in problem.
                if exc.code == "LOGIN_REQUIRED" and last_failure and (
                        last_failure[0]["outcome"] == "error" and
                        json.loads(last_failure[0]["response_json"] or "{}").get("code") ==
                        "LOGIN_REQUIRED"):
                    self.store.upsert_item(target, "monitoring_issue", "jev_login", 1.0,
                                           "Sign in to resume alerts", {"target": target}, now)
                return
            except (OSError, ConnectionError, TimeoutError) as exc:
                self.store.record_judge_run(target, now, chosen, request=request,
                                            response={"code": type(exc).__name__, "retry_after": 60},
                                            dry_run=dry_run, outcome="error",
                                            billable_calls=calls.count)
                return
        self.store.action_item_for_subject(target, "monitoring_issue", "jev_login", now)
        database_limit.clear(self.store, target, now)
        database_limit.clear_allowance(self.store, "jev", now)
        if not dry_run:
            try:
                self._file(target, now, chosen, request, verdict,
                           just_closed=context["just_closed"], rel=rel, learning=learning,
                           gated=context.get("gated"), recurring=context.get("recurring"))
            except Exception:
                self.store.record_judge_run(target, now, chosen, request=request,
                                            response=verdict, dry_run=False,
                                            outcome="filing_error", billable_calls=calls.count)
                raise
        self.store.record_judge_run(target, now, chosen, request=request,
                                    response=verdict, dry_run=dry_run,
                                    outcome="dry_run" if dry_run else "filed",
                                    billable_calls=calls.count)

    def _file_repeat(self, target: str, now: int, repeat: dict[str, Any],
                     fresh: dict[str, Any], filings: dict[str, Any], delivery: str,
                     kinds: list[str]) -> None:
        """Add this burst to the item it repeats, as a new occurrence.

        While the occurrences keep a steady schedule it is routine: it lists quietly (no unread
        mark, no push). One bigger than any earlier occurrence (1.5x) resurfaces as worse and
        says so; before the schedule is steady, each occurrence marks the item unread.
        """
        body = json.loads(json.dumps(repeat["body"]))
        entries = occurrences(body, repeat)
        starts = [entry["start"] for entry in entries] + [now]
        routine = _routine(starts)
        size = occurrence_size(filings)
        prior = [entry["size"] for entry in entries if entry.get("size")]
        verdict = None
        if routine:
            verdict = ("bigger" if size and prior and size > max(prior) * 1.5 else "usual")
        entries.append({"start": now, "end": None, "keys": sorted(fresh["query_keys"]),
                        "top": leading_query(filings), "size": size,
                        "quiet": verdict == "usual", "verdict": verdict})
        for field in ("resolution", "condition_cleared", "clear_streak", "still_happening",
                      "recovery_streak"):
            body.pop(field, None)
        retired = body.get("recovered_queries", {})
        for key in filings:
            retired.pop(key, None)
        body.update({"query_keys": fresh["query_keys"], "recovery_queries": filings,
                     "episode_query_keys": sorted(set(body.get("episode_query_keys", [])) |
                                                  set(fresh["query_keys"])),
                     "burst_start": now, "burst_last_join": now,
                     "reported_numbers": fresh.get("reported_numbers", {}),
                     "relevance_rule": fresh.get("relevance_rule"),
                     "occurrences": entries[-OCCURRENCES_CAP:]})
        for field in ("smaller_query_keys", "recurring_suppressed"):
            if fresh.get(field):
                body[field] = fresh[field]
        if verdict == "bigger" and routine:
            top = entries[-1]["keys"][0] if entries[-1]["keys"] else repeat["subject"]
            body["recurring"] = {top: {"kind": "period", "period_s": routine[0],
                                       "repeats": routine[1], "last_onset": now,
                                       "verdict": "bigger"}}
        else:
            body.pop("recurring", None)
        quiet = verdict == "usual"
        row = self.store.upsert_item(target, "many_queries_changed", repeat["subject"], 0.9,
                                     group_title(kinds), body, now, reopen_resolved=True,
                                     materially_worse=verdict == "bigger",
                                     delivery="inbox" if quiet else delivery)
        if quiet and row["state"] == "new":
            self.store.action_item(row["id"], "seen", now)
        elif not quiet and row["state"] == "seen":
            self.store.action_item(row["id"], "reopen", now)

    def note_routine_occurrences(self, target: str, now: int,
                                 gated: dict[str, dict[str, Any]] | None) -> None:
        """Bursts the gate kept from Jev as routine still count as occurrences of their open
        repeating item, added without any notice."""
        keys = set(_gate_keys(gated, "recurring"))
        if not keys:
            return
        for row in self.store.read(
                "SELECT * FROM inbox_item WHERE target=? AND type='many_queries_changed' "
                "AND state NOT IN ('resolved','dismissed')", (target,)):
            body = json.loads(row["body_json"])
            if len(body.get("occurrences", [])) < 2 or body.get("recovery_queries"):
                continue
            entries = body["occurrences"]
            last = entries[-1]
            if now - last["start"] <= BURST_SPAN_S or not _same_pattern(
                    keys, None, last):
                continue
            entries.append({"start": now, "end": None, "keys": sorted(keys & set(
                body.get("episode_query_keys", [])) or keys), "top": last.get("top"),
                "size": None, "quiet": True, "verdict": "usual"})
            body["occurrences"] = entries[-OCCURRENCES_CAP:]
            self.store.update_item_body(row["id"], body)

    def _file(self, target: str, now: int, chosen: list[dict[str, Any]],
              request: dict[str, Any], verdict: dict[str, Any],
              *, just_closed: set[tuple[str, str]] | None = None,
              rel: Relevance | None = None, learning: bool = False,
              gated: dict[str, dict[str, Any]] | None = None,
              recurring: dict[str, dict[str, Any]] | None = None) -> None:
        by_key = {row["query_key"]: row for row in chosen}
        rel = rel or relevance(self.store, target, now)
        bursts = [{**row, "body": json.loads(row["body_json"])} for row in self.store.read(
            "SELECT id,subject,state,resolved_by,created_at,body_json FROM inbox_item "
            "WHERE target=? AND type=? ORDER BY created_at,id", (target, "many_queries_changed"))]
        latest = bursts[-1] if bursts else None
        joinable = (latest if latest and latest["state"] != "resolved" and
                    _joinable(latest["body"], now) else None)
        group_body = joinable["body"] if joinable else {}
        # Members still changed in another open burst belong to that burst.
        elsewhere: dict[str, str | None] = {}
        for burst in bursts:
            if burst["state"] == "resolved" or burst is joinable:
                continue
            active = burst["body"].get("recovery_queries")
            if active is None:
                elsewhere.update({key: None for key in burst["body"].get("query_keys", [])})
            else:
                elsewhere.update({key: filing.get("kind") for key, filing in active.items()})
        group_filing = {}
        for burst in [burst for burst in bursts if burst["state"] != "resolved"] + (
                [latest] if latest else []):
            group_filing.update({**burst["body"].get("recovered_queries", {}),
                                 **burst["body"].get("recovery_queries", {})})
        qualified = []
        database_answers = {}
        first_seen = {row["query_key"]: row["first_seen"] for row in self.store.read(
            "SELECT query_key,first_seen FROM query_text WHERE target=?", (target,))}
        discovered_until = discovery_until(self.store, target)
        for answer in verdict["queries"]:
            key = answer["query_key"]
            if key not in by_key or answer["kind"] not in ATTENTION_KINDS:
                continue
            if (answer["kind"], key) in (just_closed or ()):
                continue
            if answer["attention"] not in ("inbox", "notify") or answer["kind"] == "stuck_query":
                continue
            # Being heavy is not a change: only a query first seen in the last day is new.
            if (answer["kind"] == "new_heavy_query" and
                    now - first_seen.get(key, now) >= NEW_QUERY_DAYS * 86400):
                continue
            # On a first run every query is new: only one found after the first hour is.
            if (answer["kind"] == "new_heavy_query" and discovered_until is not None and
                    first_seen.get(key, now) < discovered_until):
                continue
            row = by_key[key]
            if not _supports_answer(answer["kind"], row):
                continue
            prior = group_filing.get(key)
            if prior and prior.get("kind") in LATENCY_RECOVERY:
                if (answer["kind"] in LATENCY_RECOVERY and
                        _normal_condition(prior["kind"], row, prior)):
                    continue
                if (answer["kind"] == "query_improved" and
                        _within_filed_latency_band(row, prior)):
                    continue
            if answer["kind"] in LATENCY_RECOVERY and not prior:
                existing = self.store.read(
                    "SELECT body_json FROM inbox_item WHERE target=? AND type=? AND subject=?",
                    (target, answer["kind"], key))
                if existing:
                    body = json.loads(existing[0]["body_json"])
                    baseline = body.get("baseline_mean_ms") or (
                        body.get("baseline", {}).get("1h") or {}).get("mean_ms")
                    if _normal_condition(answer["kind"], row,
                                         {"baseline_mean_ms": baseline}):
                        continue
            if ((answer["kind"] == "query_improved" and
                 (not row["calls"] or not _baseline_mean(row, "query_improved") or
                  row["mean_ms"] >= _baseline_mean(row, "query_improved") * 0.8)) or
                    (answer["kind"] == "query_stopped_running" and row["calls"])):
                continue
            base = row.get("baseline", {}).get("1h") or {}
            # A rare query's large latency ratio alone does not merit an item.
            if (answer["kind"] == "latency_regression" and row["qps"] < 1 / 3600
                    and (base.get("qps") or 0) < 1 / 3600):
                continue
            if answer["kind"] in DATABASE_KINDS:
                if not _supports_database_answer(self.store, target, now, answer["kind"]):
                    continue
                previous = database_answers.get(answer["kind"])
                if previous:
                    answer = {**answer,
                              "attention": "notify" if "notify" in (
                                  answer["attention"], previous["attention"]) else "inbox",
                              "severity": max(answer["severity"], previous["severity"])}
                database_answers[answer["kind"]] = answer
                continue
            qualified.append(answer)
        rates = database_rates(self.store, target, now) if database_answers else None
        for kind, answer in database_answers.items():
            existing = self.store.read(
                "SELECT state,body_json FROM inbox_item WHERE target=? AND type=? AND subject=?",
                (target, kind, "database"))
            open_body = (json.loads(existing[0]["body_json"])
                         if existing and existing[0]["state"] not in ("resolved", "dismissed")
                         else {})
            numbers = _database_numbers(kind, rates or {})
            prior = open_body.get("numbers", {})
            if kind == "rollback_or_deadlock_spike":
                worse = bool(prior) and any(
                    numbers.get(metric, 0) > max(prior.get(metric, 0) * 1.5,
                                                 prior.get(metric, 0) + 1)
                    for metric in ("rollbacks_per_min", "deadlocks_per_min"))
            else:
                worse = bool(prior.get("cache_hit_ratio") and
                             numbers.get("cache_hit_ratio", 1) <
                             prior["cache_hit_ratio"] * 0.8)
            body = (open_body if open_body and not worse else
                    {**open_body, "at_filing": open_body.get("at_filing", numbers),
                     "numbers": numbers, "kind": kind, "filed_at": open_body.get("filed_at", now)})
            body.pop("recovery_streak", None)
            self.store.upsert_item(target, kind, "database", answer["severity"],
                                   TITLES[kind], body, now,
                                   delivery=change_delivery(kind, answer["attention"], None, rel,
                                                            learning=learning),
                                   materially_worse=worse)
        # Good news and slow creep are digest findings of their own; they never make or join a
        # burst.
        quiet = [item for item in qualified if item["kind"] in DIGEST_KINDS]
        qualified = [item for item in qualified if item["kind"] not in DIGEST_KINDS]
        scope = verdict["event_scope"]
        changed = [item for item in qualified if item.get("attention") in ("inbox", "notify")]
        if (scope != "database_wide" and not joinable and
                len({item["query_key"] for item in changed}) >= 2 and _repeat_of(
                    bursts, {item["query_key"] for item in changed},
                    {item["query_key"]: _member_filing(item, by_key[item["query_key"]], now)
                     for item in changed}, now, None)):
            # These queries repeat a burst seen before (its lead and half its queries): they
            # changed together, whatever this one check's view of them on its own.
            scope = "database_wide"
        if group_body.get("recovery_queries"):
            measurable = [item for item in qualified
                          if _recoverable_member(item, by_key[item["query_key"]])]
            separate = [item for item in qualified
                        if not _recoverable_member(item, by_key[item["query_key"]])]
            grouped = group_many(measurable, scope) + separate
        else:
            grouped = group_many(qualified, scope)
        grouped += quiet
        # A burst left with a single new member files it as a single item; the list grows
        # while it is walked.
        for answer in grouped:
            if answer.get("type") == "many_queries_changed":
                queries = [item for item in answer["queries"]
                           if not (item["query_key"] in elsewhere and
                                   elsewhere[item["query_key"]] in (None, item["kind"]))]
                if (latest and latest["state"] == "resolved" and
                        not latest["body"].get("condition_cleared")):
                    queries = [item for item in queries if _new_episode(
                        item, by_key[item["query_key"]], latest["body"], now)]
                if len(queries) < 2:
                    grouped.extend(queries)
                    continue
                answer = {**answer, "queries": queries}
                keys = [item["query_key"] for item in answer["queries"]]
                recoverable = all(_recoverable_member(item, by_key[item["query_key"]])
                                  for item in answer["queries"])
                recovery_queries = ({item["query_key"]: _member_filing(
                    item, by_key[item["query_key"]], now) for item in answer["queries"]}
                                    if recoverable else {})
                old_body = group_body
                if old_body.get("recovery_queries") and recoverable:
                    body = json.loads(json.dumps(old_body))
                    active = body["recovery_queries"]
                    members = set(body.get("episode_query_keys", body.get("query_keys", [])))
                    for key, filing in recovery_queries.items():
                        retired = body.get("recovered_queries", {}).get(key)
                        if (key not in active and retired and
                                retired.get("kind") == filing["kind"] and
                                not _recurred_condition(filing["kind"], by_key[key],
                                                        retired, filing)):
                            continue
                        if key not in active or active[key]["kind"] != filing["kind"]:
                            if retired and retired.get("kind") == filing["kind"]:
                                filing["baseline_mean_ms"] = retired.get(
                                    "baseline_mean_ms") or filing["baseline_mean_ms"]
                                filing["baseline_qps"] = retired.get(
                                    "baseline_qps") or filing["baseline_qps"]
                            active[key] = filing
                            body.get("recovered_queries", {}).pop(key, None)
                            members.add(key)
                    body["query_keys"] = [key for key in
                                          dict.fromkeys(old_body["query_keys"] + keys)
                                          if key in active]
                    body["episode_query_keys"] = sorted(members)
                elif old_body and not recoverable:
                    # Keep Jev-owned episodes stable, adding only new members.
                    body = json.loads(json.dumps(old_body))
                    if not body.get("recovery_queries"):
                        members = set(body.get("episode_query_keys", body.get("query_keys", [])))
                        for key in keys:
                            if key not in members:
                                body["query_keys"].append(key)
                                members.add(key)
                        body["episode_query_keys"] = sorted(members)
                else:
                    # An old group without recovery metadata cannot carry a new
                    # measurable episode's stale membership forward.
                    body = {"query_keys": keys, "episode_query_keys": sorted(keys),
                            "at_filing": request["target_numbers"],
                            "numbers": request["target_numbers"]}
                    if recovery_queries:
                        # A member back from an earlier burst keeps the normal level it
                        # recovered to; the rolling hour still holds that burst.
                        for key, filing in recovery_queries.items():
                            retired = group_filing.get(key)
                            if retired and retired.get("kind") == filing["kind"]:
                                filing["baseline_mean_ms"] = retired.get(
                                    "baseline_mean_ms") or filing["baseline_mean_ms"]
                                filing["baseline_qps"] = retired.get(
                                    "baseline_qps") or filing["baseline_qps"]
                        body["recovery_queries"] = recovery_queries
                current_numbers = {key: _numbers(by_key[key]) for key in body["query_keys"]
                                   if key in by_key}
                reported = old_body.get("reported_numbers", {})
                adverse = {item["query_key"]: item["kind"] for item in answer["queries"]
                           if item["kind"] in LATENCY_RECOVERY | {"traffic_spike"}}
                # A member joining the episode is not a new episode; a further 2x is.
                worse = bool(reported and any(
                    _material_worsening(value, reported[key], adverse[key])
                    for key, value in current_numbers.items()
                    if key in adverse and key in reported))
                if not old_body or worse:
                    body["reported_numbers"] = {**reported, **current_numbers}
                body["relevance_rule"] = next(
                    (rule for key in body["query_keys"] if (rule := rel.rule(key))), None)
                if not old_body:
                    body["burst_start"] = body["burst_last_join"] = now
                elif set(body["episode_query_keys"]) - set(old_body.get("episode_query_keys", [])):
                    body["burst_last_join"] = now
                _note_gate(body, gated, recurring, body["episode_query_keys"])
                if body == old_body:
                    continue
                subject = joinable["subject"] if joinable and old_body else f"burst:{now}"
                delivery = group_delivery(answer["queries"], rel, learning=learning)
                repeat = None if old_body else _repeat_of(
                    bursts, set(keys), recovery_queries, now, joinable)
                if repeat:
                    self._file_repeat(target, now, repeat, body, recovery_queries, delivery,
                                      [item["kind"] for item in answer["queries"]])
                    continue
                self.store.upsert_item(target, "many_queries_changed", subject, 0.9,
                                       group_title([item["kind"] for item in answer["queries"]]),
                                       body, now,
                                       reopen_resolved=True, materially_worse=worse,
                                       delivery=delivery)
                continue
            row = by_key[answer["query_key"]]
            kind = answer["kind"]
            numbers = _numbers(row)
            existing = self.store.read(
                "SELECT state,resolved_by,severity,body_json FROM inbox_item "
                "WHERE target=? AND type=? AND (subject=? OR "
                "json_extract(body_json,'$.query_key')=?) "
                "ORDER BY state IN ('new','seen','snoozed') DESC,created_at,id LIMIT 1",
                (target, kind, row["query_key"], row["query_key"]))
            previous_body = json.loads(existing[0]["body_json"]) if existing else {}
            reported = previous_body.get("reported_numbers") or previous_body.get("numbers")
            worse = bool(reported and kind in LATENCY_RECOVERY | {"traffic_spike"}
                         and _material_worsening(numbers, reported, kind))
            # A closed episode reopens only as a new one: cleared and recurred, or 2x worse.
            if (existing and existing[0]["state"] == "resolved" and
                    existing[0]["resolved_by"] != "code" and
                    not previous_body.get("condition_cleared") and not worse):
                continue
            old_body = (previous_body if existing and existing[0]["state"] != "resolved"
                        else {})
            body = {**old_body, "query_key": row["query_key"], "numbers": numbers,
                    "at_filing": old_body.get("at_filing", numbers),
                    "filed_at": old_body.get("filed_at", now),
                    "baseline": old_body.get("baseline", row.get("baseline", {})),
                    "kind": kind, "event_scope": verdict["event_scope"]}
            if kind == "query_improved":
                body["baseline_mean_ms"] = old_body.get("baseline_mean_ms",
                                                        _baseline_mean(row, kind))
            if not old_body or worse:
                body["reported_numbers"] = numbers
                body.pop("smaller_query_keys", None)
                _note_gate(body, gated, recurring, [row["query_key"]])
            body.pop("resolution", None)
            body["attention"] = answer["attention"]
            body.update(rel.evidence(row["query_key"]))
            if kind == "new_heavy_query":
                _count_top_check(body, row["query_key"], rel, now)
            self.store.upsert_item(target, kind, row["query_key"], answer["severity"],
                                   TITLES[kind], body, now,
                                   delivery=change_delivery(
                                       kind, answer["attention"], row["query_key"], rel,
                                       learning=learning,
                                       top_checks=body.get("top_checks", 0)),
                                   reopen_resolved=kind in MEASURABLE_RECOVERY,
                                   materially_worse=worse)
        episodes = []
        for answer in verdict["queries"]:
            if answer["query_key"] not in by_key or answer["kind"] not in {
                    "query_stopped_running", "query_improved"}:
                continue
            row = by_key[answer["query_key"]]
            if ((answer["kind"] == "query_improved" and
                 (not row["calls"] or not _baseline_mean(row, "query_improved") or
                  row["mean_ms"] >= _baseline_mean(row, "query_improved") * 0.8)) or
                    (answer["kind"] == "query_stopped_running" and row["calls"])):
                continue
            mean = _baseline_mean(row, answer["kind"])
            episodes.append((target, answer["query_key"], answer["kind"], mean, now,
                             row["qps"]))
        if episodes:
            self.store.record_attention_episodes(episodes)

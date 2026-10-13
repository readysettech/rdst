"""The daily snapshot (DESIGN_V5 section 5): one report per local calendar day.

Everything here is computed from recorded history when the report is written and stored
with it, so a past day reads the same forever: the day's facts, its per-query traffic
chart with event markers, its numbers against the day before, what happened, the queries
worth a look (each with its stored deep analysis), health problems with their values, and
every query that ran. The narrative model may only rephrase the facts.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Iterable

from features.monitor.analysis_digest import digest_for_ref
from features.monitor.db_actions import database_actions, health_overview
from features.monitor.naming import target_names
from features.monitor.presentation import display_name, format_ms, format_pct, format_size, plain
from shared.monitor.monitor_store import EVERYTHING_ELSE, RETIRED_TYPES, MonitorStore, item_query_keys, \
    local_day_bounds

SNAPSHOT_VERSION = 3
CHART_TOP = 8
LOOK_AT_LIMIT = 10
ALL_QUERIES_LIMIT = 300
EVENT_LIMIT = 60
OPEN_STATES = ("new", "seen", "snoozed")
# The first status that applies wins (DESIGN_V5 section 1).
STATUS_RANK = {"needs_attention": 0, "watch": 1, "fix_ready": 2, "healthy": 3,
               "not_analyzed": 4}
LOOK_AT = ("needs_attention", "watch", "fix_ready")
STATUS_WORDS = {"needs_attention": "Needs attention", "watch": "Watch",
                "fix_ready": "Fix suggested", "healthy": "Healthy",
                "not_analyzed": "Not analyzed yet"}
TIMELINE_KINDS = ("created", "worse", "reopened")
EVENT_WORDS = {
    "latency_regression": "Got slower", "plan_change": "Plan changed",
    "table_slowdown": "Table got slower", "slow_creep": "Slowly getting slower",
    "traffic_spike": "Traffic jumped", "stuck_query": "Stuck", "new_heavy_query": "New heavy query",
    "query_stopped_running": "Stopped running", "query_improved": "Got faster",
    "many_queries_changed": "Several queries changed", "cache_hit_ratio_drop": "Cache hit dropped",
    "connections_near_max": "Connections near the limit",
    "rollback_or_deadlock_spike": "Rollbacks jumped", "monitoring_issue": "Monitoring issue",
}
# What an event looked like when it happened, whatever became of it later.
EVENT_TONES = {
    "latency_regression": "negative", "plan_change": "negative", "table_slowdown": "negative",
    "stuck_query": "negative", "rollback_or_deadlock_spike": "negative",
    "connections_near_max": "negative", "cache_hit_ratio_drop": "negative",
    "query_stopped_running": "warning", "traffic_spike": "warning",
    "new_heavy_query": "warning", "slow_creep": "warning", "many_queries_changed": "warning",
    "query_improved": "positive", "monitoring_issue": "neutral",
}
HEALTH_ACTIONS = {
    "vacuum_bloat": "Run VACUUM (ANALYZE) on these tables and check that autovacuum keeps up "
                    "with their writes.",
    "unused_indexes": "Drop indexes nothing reads; each one slows every write.",
    "duplicate_indexes": "Drop the redundant index; the covering one serves the same reads.",
    "connections": "Lower the application's pool size or add a connection pooler.",
    "idle_in_transaction": "Find the application code that leaves transactions open and "
                           "commit or roll back sooner.",
    "config_audit": "Review these settings against the server's memory and workload.",
    "replication": "Check the replica's lag and that its replication slots are still read.",
    "cache_hit": "Look for new large scans in the query list; if the working set grew, give "
                 "the database more memory.",
}
_SQL = re.compile(r"\b(SELECT|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+"
                  r"(UNIQUE\s+)?INDEX|ALTER\s+TABLE|DROP\s+INDEX|WHERE|ORDER\s+BY)\b|->>|"
                  r"\b\w+\s?\((\s?\w+\s?,)+\s?\w+\s?\)")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_TOKEN = re.compile(r"\{\{q:([0-9a-f]{6,64})\}\}")


def report_period(trigger: str, now: int) -> tuple[int, int, bool]:
    """(start, end, partial): the nightly run covers the local day that ended; a run by
    hand covers today so far."""
    today, _ = local_day_bounds(now)
    if trigger == "nightly":
        return local_day_bounds(today - 1)[0], today, False
    return today, max(today + 60, now), True


def prior_period(start: int, end: int) -> tuple[int, int]:
    """The previous calendar day, cut to the same length when today is partial."""
    previous, _ = local_day_bounds(start - 1)
    if end >= local_day_bounds(start)[1]:
        return previous, start
    return previous, previous + (end - start)


def grade(score: float | None) -> str | None:
    if not isinstance(score, (int, float)):
        return None
    # The same floors as the query rows' grade tiles.
    return "A" if score >= 90 else "B" if score >= 70 else "C" if score >= 55 else \
        "D" if score >= 40 else "F"


def _clock(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0")


def _weekday(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%A")


def _rate(qps: float) -> str:
    if qps >= 100:
        return f"{qps:,.0f}"
    if qps >= 10:
        return f"{qps:.0f}"
    if qps >= 1:
        return f"{qps:.1f}"
    return f"{qps:.2f}"


# Numbers

# A rollup coarser than an hour starts at a UTC boundary, so it can straddle a local day's
# edge. Only rows that fit inside the day count toward it.
LOCAL_RES = 3600


def local_rows(rows: Iterable[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    return [row for row in rows if row["res"] <= LOCAL_RES
            or (row["ts"] >= start and row["ts"] + row["res"] <= end)]


def day_totals(store: MonitorStore, target: str, start: int, end: int
               ) -> dict[str, dict[str, Any]]:
    """Calls and execution time per query over one local day (or part of one)."""
    from features.monitor.api import views

    rows = views.stmt_rows(store, target, start, end, step=LOCAL_RES)
    return views.totals_by_key(local_rows(rows, start, end))


def _window_numbers(store: MonitorStore, target: str, start: int, end: int) -> dict[str, Any]:
    from features.monitor.api import views

    step = 300 if end - start > 3 * 3600 else 60
    rows = local_rows(views.db_rows(store, target, start, end, step), start, end)
    if not rows:
        return {}
    first = max(start, min(row["ts"] for row in rows))
    last = min(end, max(row["ts"] + row["res"] for row in rows))
    seconds = max(60, last - first)
    buckets = views.spread(rows, step)
    points = sorted((views._health_point(bucket, parts, step, end)
                     for bucket, parts in buckets.items()), key=lambda point: point["ts"])
    period = views._period(rows, seconds, points)
    busiest = views.peak_rate(store, target, start, end, period["qps"])
    ratios = [point["cache_hit_ratio"] for point in points
              if point["cache_hit_ratio"] is not None]
    limits = [point["max_conns"] for point in points if point["max_conns"] is not None]
    hits = sum(row.get("blks_hit") or 0 for row in rows)
    reads = sum(row.get("blks_read") or 0 for row in rows)
    return {**period, "observed_seconds": seconds, "first_ts": first, "last_ts": last,
            # Weighted by reads, so a quiet minute's few misses don't read as a memory problem.
            "cache_hit_weighted": hits / (hits + reads) if hits + reads else None,
            "disk_reads_per_s": reads / seconds,
            "peak_qps": busiest["qps"] if busiest else None,
            "peak_qps_at": busiest["ts"] if busiest else None,
            "cache_hit_ratio_avg": sum(ratios) / len(ratios) if ratios else None,
            "max_conns": max(limits) if limits else None,
            "vitals": {"res": step, "xs": [point["ts"] for point in points],
                       **{field: [_round(point[field]) for point in points]
                          for field in VITALS}}}


VITALS = ("qps", "mean_ms", "active_conns", "cache_hit_ratio", "xact_rollback", "deadlocks")


def _round(value: Any) -> Any:
    return round(value, 4) if isinstance(value, float) else value


NUMBERS = (("qps", "Queries per second", "qps", None),
           ("mean_ms", "Average latency", "ms", "down"),
           ("p95_ms", "p95 latency", "ms", "down"),
           ("active_conns_peak", "Peak connections", "count", "down"),
           ("cache_hit_ratio_min", "Lowest cache hit", "ratio", "up"),
           ("xact_rollback", "Rollbacks", "count", "down"),
           ("deadlocks", "Deadlocks", "count", "down"))


def day_numbers(current: dict[str, Any], prior: dict[str, Any]) -> list[dict[str, Any]]:
    """Each number with the previous day's value; `better` says which way is good."""
    result = []
    for field, label, unit, better in NUMBERS:
        value, before = current.get(field), prior.get(field)
        change = value / before - 1 if isinstance(value, (int, float)) and \
            isinstance(before, (int, float)) and before else None
        result.append({"metric": field, "label": label, "unit": unit, "value": value,
                       "prior": before, "change": change, "better": better})
    return result


# Chart

def day_chart(store: MonitorStore, target: str, start: int, end: int, now: int,
              markers: list[dict[str, Any]], names: dict[str, str]) -> dict[str, Any] | None:
    """Queries per second by query over the day: the busiest queries, then everything else."""
    from features.monitor.api import views

    stop = min(end, now)
    if stop - start < 60:
        return None
    history = views.history_span(store, target, now)
    res = views.window_res(stop - start, start, now)
    rows = local_rows(views.stmt_rows(store, target, start, stop, step=res), start, stop)
    totals = views.totals_by_key(rows)
    texts = views.query_texts(store, target)[0]
    user = views.workload_keys(store, target, totals, texts)
    population = user | ({EVERYTHING_ELSE} & set(totals))
    rows = [row for row in rows if row["query_key"] in population]
    leaders = sorted((key for key in user if totals[key]["calls"]),
                     key=lambda key: (-totals[key]["calls"], key))[:CHART_TOP]
    if not any(totals[key]["calls"] for key in population):
        return None
    index = {key: position for position, key in enumerate(leaders)}
    rest = population - set(leaders)
    other = any(totals[key]["calls"] for key in rest)
    width = len(leaders) + (1 if other else 0)
    buckets = views.spread(rows, res)
    covered = set(views.spread(local_rows(views.db_rows(store, target, start, stop, res),
                                          start, stop), res))
    xs: list[int] = []
    series: list[list[float | None]] = [[] for _ in range(width)]
    spent: list[list[float | None]] = [[] for _ in range(width)]
    latency: list[list[float | None]] = [[] for _ in range(width)]
    overall: list[float | None] = []
    # Every query's calls and time per bucket, for spike attribution; not stored.
    by_key: dict[str, tuple[list[float], list[float]]] = {}
    for bucket in views.timeline(start, stop, res, history, buckets):
        if bucket < start or bucket >= stop:
            continue
        parts = buckets.get(bucket, [])
        xs.append(bucket)
        if not parts and bucket not in covered:
            for values in (*series, *spent, *latency):
                values.append(None)
            overall.append(None)
            continue
        calls = [0.0] * width
        ms = [0.0] * width
        slot = len(xs) - 1
        for row, share in parts:
            position = index.get(row["query_key"], width - 1 if other else None)
            if position is not None:
                calls[position] += row["calls"] * share
                ms[position] += row["total_ms"] * share
            per = by_key.setdefault(row["query_key"], ([], []))
            while len(per[0]) <= slot:
                per[0].append(0.0)
                per[1].append(0.0)
            per[0][slot] += row["calls"] * share
            per[1][slot] += row["total_ms"] * share
        seconds = max(60, min(res, stop - bucket))
        for position in range(width):
            series[position].append(round(calls[position] / seconds, 4))
            spent[position].append(round(ms[position], 3))
            latency[position].append(round(ms[position] / calls[position], 4)
                                     if calls[position] else None)
        overall.append(round(sum(ms) / sum(calls), 4) if sum(calls) else None)
    observed = [x for x, *values in zip(xs, *series) if any(v is not None for v in values)]
    seconds = max(60, (observed[-1] + res - observed[0]) if observed else stop - start)
    all_ms = (sum(totals[key]["total_ms"] for key in user)
              + totals.get(EVERYTHING_ELSE, {}).get("total_ms", 0.0))

    def band(key: str | None, keys: Iterable[str], position: int | None) -> dict[str, Any]:
        calls = sum(totals[item]["calls"] for item in keys)
        total = sum(totals[item]["total_ms"] for item in keys)
        return {"key": key or "other", "kind": "query" if key else "other",
                "display_name": (names.get(key) or display_name(texts.get(key, "")))
                if key else "Everything else", "color_index": position,
                "share": total / all_ms if all_ms else 0.0, "calls": calls,
                "qps": calls / seconds, "mean_ms": total / calls if calls else None}

    bands = [band(key, [key], position) for position, key in enumerate(leaders)]
    if other:
        bands.append(band(None, rest, None))
    return {"res": res, "start": start, "end": end, "xs": xs, "bands": bands, "qps": series,
            "ms": spent, "mean_ms": latency, "overall_mean_ms": overall,
            "markers": [marker for marker in markers if start <= marker["ts"] < end],
            "_by_key": {key: (calls + [0.0] * (len(xs) - len(calls)),
                              ms + [0.0] * (len(xs) - len(ms)))
                        for key, (calls, ms) in by_key.items() if key != EVERYTHING_ELSE}}


# Spikes

SPIKE_LIMIT = 5


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    """Index ranges [first, last] of flagged buckets, bridging one quiet bucket."""
    runs: list[tuple[int, int]] = []
    for position, flagged in enumerate(flags):
        if not flagged:
            continue
        if runs and position - runs[-1][1] <= 2:
            runs[-1] = (runs[-1][0], position)
        else:
            runs.append((position, position))
    return runs


def day_spikes(chart: dict[str, Any] | None, events: list[dict[str, Any]],
               by_key: dict[str, tuple[list[float], list[float]]] | None = None
               ) -> list[dict[str, Any]]:
    """Stretches where traffic or latency stood well above the day's usual level: when, how
    big against the day's median, and the queries that moved most."""
    if not chart or len(chart.get("xs") or []) < 12:
        return []
    xs, res = chart["xs"], chart["res"]
    qps = chart.get("qps") or []
    totals = [None if all(values[i] is None for values in qps) else
              sum(values[i] or 0 for values in qps) for i in range(len(xs))]
    found: list[dict[str, Any]] = []
    for metric, line, floor in (("traffic", totals, 0.1),
                                ("latency", chart.get("overall_mean_ms") or [], 0.5)):
        measured = [value for value in line if value is not None]
        if len(measured) < 12:
            continue
        usual = _median(measured)
        spread = _median([abs(value - usual) for value in measured]) * 1.4826
        ratio_floor = 1.6 if metric == "traffic" else 2.0
        threshold = max(usual * ratio_floor, usual + 4 * spread, usual + floor)
        for first, last in _runs([value is not None and value >= threshold for value in line]):
            window = [(xs[i], line[i]) for i in range(first, last + 1) if line[i] is not None]
            peak_ts, peak = max(window, key=lambda point: point[1])
            movers = []
            seconds = max(60, res)
            for key, (calls, spent) in (by_key or {}).items():
                during = range(first, last + 1)
                if metric == "traffic":
                    day_values = [value / seconds for value in calls]
                    before = _median(day_values)
                    now_value = sum(calls[i] for i in during) / len(during) / seconds
                    gain = now_value - before
                    shown_before, shown_during = before, now_value
                else:
                    # Latency rises with the time a query adds, so rank by time spent and
                    # show the query's own latency before and during.
                    gain = (sum(spent[i] for i in during) / len(during)) - _median(spent)
                    day_calls, day_ms = sum(calls), sum(spent)
                    during_calls = sum(calls[i] for i in during)
                    if not day_calls or not during_calls:
                        continue
                    outside = day_calls - during_calls
                    shown_before = ((day_ms - sum(spent[i] for i in during)) / outside
                                    if outside else day_ms / day_calls)
                    shown_during = sum(spent[i] for i in during) / during_calls
                    if shown_during < shown_before * 1.3 and gain <= 0:
                        continue
                if gain <= 0 or (shown_before and shown_during / shown_before < 1.3):
                    continue
                movers.append({"query_key": key, "before": round(shown_before, 4),
                               "during": round(shown_during, 4),
                               "ratio": round(shown_during / shown_before, 2)
                               if shown_before else None, "gain": gain})
            movers.sort(key=lambda mover: -mover["gain"])
            start, end = xs[first], xs[last] + res
            nearby, seen = [], set()
            for event in events:
                if event["kind"] == "index" or not start - res <= event["ts"] < end + res \
                        or (event["what"], event["ts"] // 60) in seen:
                    continue
                seen.add((event["what"], event["ts"] // 60))
                nearby.append({"ts": event["ts"], "what": event["what"],
                               "title": event["title"]})
            found.append({"metric": metric, "start": start, "end": end, "peak_ts": peak_ts,
                          "peak": round(peak, 4), "usual": round(usual, 4),
                          "ratio": round(peak / usual, 2) if usual else None,
                          "queries": [{key: value for key, value in mover.items()
                                       if key != "gain"} for mover in movers[:3]],
                          "events": nearby[:4]})
    found.sort(key=lambda spike: -(spike["ratio"] or 0))
    found = sorted(found[:SPIKE_LIMIT], key=lambda spike: spike["start"])
    for number, spike in enumerate(found, 1):
        spike["id"] = f"S{number}"
        spike["summary"] = spike_words(spike)
    return found


def spike_words(spike: dict[str, Any]) -> str:
    """One plain line: when, how big against the usual level."""
    when = _clock(spike["start"])
    if spike["end"] - spike["start"] > 600:
        when = f"{when} to {_clock(spike['end'])}"
    if spike["metric"] == "traffic":
        size = (f"{_rate(spike['peak'])} queries a second at the peak, against a usual "
                f"{_rate(spike['usual'])}")
        return f"Traffic spiked {when}: {size}."
    return (f"Latency spiked {when}: {format_ms(spike['peak'])} a query at the peak, against "
            f"a usual {format_ms(spike['usual'])}.")


# Events

def day_events(store: MonitorStore, target: str, start: int, end: int
               ) -> list[dict[str, Any]]:
    """What happened: one line per filed, worsened or reopened Inbox item, plus earlier
    items that went back to normal during the day, and index changes."""
    from features.monitor.api.views import inbox_items

    marks = store.read(
        "SELECT e.item_id,e.ts,e.kind FROM inbox_event e JOIN inbox_item i ON i.id=e.item_id "
        "WHERE i.target=? AND e.ts>=? AND e.ts<? AND e.kind IN "
        "('created','worse','reopened','resolved') ORDER BY e.ts,e.id", (target, start, end))
    ids = sorted({mark["item_id"] for mark in marks})
    if ids:
        placeholders = ",".join("?" for _ in ids)
        rows = store.read(f"SELECT * FROM inbox_item WHERE id IN ({placeholders})", tuple(ids))
    else:
        rows = []
    rows = [row for row in rows if row["type"] not in RETIRED_TYPES
            and row["type"] != "daily_summary" and not row.get("muted_by")]
    items = {item["id"]: item for item in inbox_items(store, rows)} if rows else {}
    lines: list[dict[str, Any]] = []
    seen: set[int] = set()
    for mark in marks:
        item = items.get(mark["item_id"])
        if item is None:
            continue
        kind = mark["kind"]
        if kind == "resolved":
            # Items filed today carry their outcome on their own line.
            if item["created_at"] >= start or item["id"] in seen:
                continue
        elif kind == "created":
            seen.add(item["id"])
        state = ("resolved" if item["state"] == "resolved" else
                 "dismissed" if item["state"] == "dismissed" else "open")
        measurement = item.get("measurement") or {}
        lines.append({
            "ts": mark["ts"], "kind": kind, "item_id": item["id"], "type": item["type"],
            "what": ("Back to normal" if kind == "resolved" else
                     f"{EVENT_WORDS.get(item['type'], item['title'])} again"
                     if kind == "reopened" else
                     "Got worse" if kind == "worse" else
                     EVENT_WORDS.get(item["type"], item["title"])),
            "title": item["title"], "detail": plain(measurement.get("text") or item["summary"]),
            "query_keys": list(item["query_keys"]), "state": state,
            "resolved_at": item.get("resolved_at"),
            "tone": "positive" if kind == "resolved" else EVENT_TONES.get(item["type"], "warning"),
            "delivery": item.get("delivery")})
    for change in store.read('SELECT ts,kind,"table",index_name,definition FROM index_change '
                             "WHERE target=? AND ts>=? AND ts<? ORDER BY ts,id",
                             (target, start, end)):
        added = change["kind"] == "added"
        lines.append({"ts": change["ts"], "kind": "index", "item_id": None,
                      "type": "index_added" if added else "index_dropped",
                      "what": "Index added" if added else "Index dropped",
                      "title": f"{change['index_name']} on {change['table']}",
                      "detail": "", "definition": change["definition"], "query_keys": [],
                      "state": "done", "resolved_at": None,
                      "tone": "positive" if added else "neutral", "delivery": None})
    lines.sort(key=lambda line: (line["ts"], line["item_id"] or 0))
    return lines[-EVENT_LIMIT:]


def event_markers(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"ts": event["ts"], "kind": "index" if event["kind"] == "index" else "event",
             "label": f"{event['what']}: {event['title']}" if event["kind"] == "index"
             else event["what"], "query_keys": event["query_keys"][:3],
             "tone": event.get("tone")} for event in events]


# Queries

def _open_items(store: MonitorStore, target: str) -> dict[str, dict[str, Any]]:
    """The open Inbox item that sets each query's status, Act now before Watch."""
    found: dict[str, dict[str, Any]] = {}
    rows = store.read("SELECT id,type,subject,title,body_json,delivery,severity,created_at "
                      "FROM inbox_item WHERE target=? AND state IN ('new','seen','snoozed') "
                      "AND muted_by IS NULL ORDER BY severity DESC,id DESC", (target,))
    for row in rows:
        # A group alert is about the database; its members keep their own status.
        if row["type"] in RETIRED_TYPES or row["type"] in ("daily_summary", "many_queries_changed"):
            continue
        try:
            body = json.loads(row["body_json"] or "{}")
        except ValueError:
            body = {}
        status = "needs_attention" if row["delivery"] == "notify" else "watch"
        for key in item_query_keys(row["subject"], body if isinstance(body, dict) else {}):
            current = found.get(key)
            if current is None or STATUS_RANK[status] < STATUS_RANK[current["status"]]:
                found[key] = {"status": status, "reason": row["title"], "item_id": row["id"],
                              "since": row["created_at"]}
    return found


def _status(key: str, open_items: dict[str, dict[str, Any]],
            digest: dict[str, Any] | None) -> tuple[str, str | None]:
    if key in open_items:
        return open_items[key]["status"], open_items[key]["reason"]
    if digest is not None and digest.get("fix_ready"):
        return "fix_ready", None
    if digest is not None:
        return "healthy", None
    return "not_analyzed", None


def _digests(store: MonitorStore, target: str, keys: Iterable[str], now: int,
             refs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The analysis each query had when the report was written: the run's own first, else
    the latest one finished by then."""
    done = {row["query_key"]: row for row in store.read(
        "SELECT j.* FROM analyze_job j WHERE j.target=? AND j.state='done' "
        "AND j.analysis_ref IS NOT NULL AND j.finished_at<=? AND j.id=(SELECT MAX(id) "
        "FROM analyze_job WHERE target=j.target AND query_key=j.query_key AND state='done' "
        "AND analysis_ref IS NOT NULL AND finished_at<=?)", (target, now, now))}
    found: dict[str, dict[str, Any]] = {}
    for key in keys:
        ref = refs.get(key) or done.get(key)
        if not ref or not ref.get("analysis_ref"):
            continue
        try:
            digest = digest_for_ref(target, key, ref["analysis_ref"], ref.get("mode"),
                                    ref.get("finished_at"))
        except Exception:
            digest = None
        if digest is not None:
            found[key] = digest
    return found


def day_queries(store: MonitorStore, target: str, start: int, end: int, now: int,
                run_analyses: list[dict[str, Any]] | None = None,
                provisional: Iterable[str] = ()) -> tuple[list[dict[str, Any]],
                                                          dict[str, str]]:
    """Every application query that ran in the day with its numbers, previous-day latency,
    grade and status; and the display names."""
    from features.monitor.api import views

    prior_start, prior_end = prior_period(start, end)
    totals = day_totals(store, target, start, end)
    before = day_totals(store, target, prior_start, prior_end)
    # "New" only means something when the day before was recorded.
    prior_recorded = bool(before)
    texts = views.query_texts(store, target)[0]
    user = views.workload_keys(store, target, totals, texts)
    keys = sorted((key for key in user if totals[key]["calls"]),
                  key=lambda key: (-totals[key]["total_ms"], key))[:ALL_QUERIES_LIMIT]
    names = target_names(store, target, {key: texts.get(key, "") for key in user | set(keys)})
    all_ms = (sum(totals[key]["total_ms"] for key in user)
              + totals.get(EVERYTHING_ELSE, {}).get("total_ms", 0.0))
    numbers = _window_numbers(store, target, start, end)
    seconds = numbers.get("observed_seconds") or max(60, min(end, now) - start)
    refs = {item["query_key"]: item for item in run_analyses or []
            if isinstance(item, dict) and item.get("analysis_ref")}
    digests = _digests(store, target, keys, now, refs)
    open_items = _open_items(store, target)
    provisional = set(provisional)
    result = []
    for key in keys:
        entry, prior = totals[key], before.get(key) or {}
        mean = entry["total_ms"] / entry["calls"] if entry["calls"] else None
        prior_mean = prior["total_ms"] / prior["calls"] if prior.get("calls") else None
        digest = digests.get(key)
        status, reason = _status(key, open_items, digest)
        fix = (digest or {}).get("fix")
        result.append({
            "query_key": key, "display_name": names.get(key) or display_name(texts.get(key, "")),
            "calls": entry["calls"], "qps": entry["calls"] / seconds,
            "total_ms": entry["total_ms"], "mean_ms": mean,
            "share": entry["total_ms"] / all_ms if all_ms else 0.0,
            "prior_calls": prior.get("calls") or 0, "prior_mean_ms": prior_mean,
            "latency_change": mean / prior_mean - 1 if mean and prior_mean else None,
            "status": status, "status_reason": reason,
            "score": (digest or {}).get("efficiency_score"),
            "grade": grade((digest or {}).get("efficiency_score")),
            "fix": {"kind": fix["kind"], "title": fix["title"], "sql": fix.get("sql")}
            if fix else None,
            "analysis_ref": (digest or {}).get("analysis_ref"),
            "analysis_mode": (digest or {}).get("mode"),
            "analyzed_at": (digest or {}).get("analyzed_at"),
            "headline": (digest or {}).get("headline"),
            "findings": [plain(text) for text in (digest or {}).get("findings") or []],
            "provisional": key in provisional,
            "new": prior_recorded and not prior.get("calls"),
            "text": texts.get(key, "")})
    return result, names


def look_at(queries: list[dict[str, Any]], texts: dict[str, str]) -> list[dict[str, Any]]:
    """Queries with an open alert or a fix ready, most urgent first, then by load."""
    picked = [row for row in queries if row["status"] in LOOK_AT]
    picked.sort(key=lambda row: (STATUS_RANK[row["status"]], -row["share"]))
    return [{**row, "text": row.get("text") or texts.get(row["query_key"], "")}
            for row in picked[:LOOK_AT_LIMIT]]


# Health

def health_section(collectors: dict[str, Any] | None,
                   numbers: dict[str, Any] | None = None) -> dict[str, Any]:
    """Only the problems, each with its values and what to do; the rest is one line."""
    collectors = collectors or {}
    if collectors.get("collection_error"):
        return {"problems": [], "fine": [], "unavailable": [
            {"check": "health", "label": "Health checks",
             "reason": plain(collectors["collection_error"])}]}
    errors = collectors.get("section_errors") or {}
    problems: list[dict[str, Any]] = []
    fine: list[str] = []
    unavailable = [{"check": name, "label": label, "reason": plain(errors[name])}
                   for name, label in (("vacuum_bloat", "Vacuum and bloat"),
                                       ("index_health", "Indexes"),
                                       ("connections", "Connections"),
                                       ("config_audit", "Settings"),
                                       ("replication", "Replication"),
                                       ("sequential_scans", "Full table scans")) if name in errors]

    from features.monitor.db_actions import MYSQL_FREE_WARN_BYTES, VACUUM_DEAD_PCT, mysql_table

    mysql = str(collectors.get("engine") or "").lower() == "mysql"
    vacuum = collectors.get("vacuum_bloat")
    if isinstance(vacuum, dict) and "vacuum_bloat" not in errors:
        tables = [table for table in vacuum.get("tables") or []
                  if isinstance(table, dict) and table.get("status") in ("warn", "crit")
                  and (not mysql or (table.get("free_bytes") or 0) >= MYSQL_FREE_WARN_BYTES)
                  and (mysql or (table.get("dead_ratio_pct") or 0) >= VACUUM_DEAD_PCT)]
        tables.sort(key=lambda table: -(table.get("dead_ratio_pct") or 0))
        if tables and mysql:
            problems.append({
                "check": "vacuum_bloat", "label": "Tables with free space to reclaim",
                "status": "bad" if any(t.get("status") == "crit" for t in tables) else "warn",
                "summary": f"{len(tables)} {'table has' if len(tables) == 1 else 'tables have'} "
                           "a lot of free space",
                "rows": [{"name": f"{table.get('table')}",
                          "value": f"{table.get('dead_ratio_pct') or 0:.1f}% free",
                          "detail": f"{format_size(table.get('free_bytes'))} of "
                                    f"{format_size((table.get('size_bytes') or 0) + (table.get('free_bytes') or 0))}"}
                         for table in tables[:10]],
                "action": "Rebuild these tables with OPTIMIZE TABLE during a quiet hour.",
                "sql": "\n".join(f"OPTIMIZE TABLE {mysql_table(table.get('schema'), table.get('table'))};"
                                 for table in tables[:10])})
        elif tables:
            problems.append({
                "check": "vacuum_bloat", "label": "Tables that need a vacuum",
                "status": "bad" if any(t.get("status") == "crit" for t in tables) else "warn",
                "summary": f"{len(tables)} {'table has' if len(tables) == 1 else 'tables have'} "
                           "a lot of dead rows",
                "rows": [{"name": f"{table.get('table')}",
                          "value": f"{table.get('dead_ratio_pct') or 0:.1f}% dead",
                          "detail": f"{int(table.get('dead_tuples') or 0):,} dead of "
                                    f"{int(table.get('live_tuples') or 0) + int(table.get('dead_tuples') or 0):,} rows"}
                         for table in tables[:10]],
                "action": HEALTH_ACTIONS["vacuum_bloat"],
                "sql": "\n".join(f"VACUUM (ANALYZE) {table.get('schema', 'public')}."
                                 f"{table.get('table')};" for table in tables[:10])})
        pct = vacuum.get("txid_age_pct")
        if isinstance(pct, (int, float)) and pct >= 50:
            problems.append({"check": "wraparound", "label": "Transaction ID age",
                             "status": "bad" if pct >= 75 else "warn",
                             "summary": f"{pct:.0f}% of the way to wraparound",
                             "rows": [], "action": "Run VACUUM (FREEZE) on the oldest tables.",
                             "sql": None})
        from features.monitor.db_actions import MYSQL_HISTORY_WARN

        history = vacuum.get("history_list_length")
        purge_lag = isinstance(history, (int, float)) and history >= MYSQL_HISTORY_WARN
        if purge_lag:
            problems.append({"check": "purge_lag", "label": "Purge backlog",
                             "status": "bad" if history >= 10 * MYSQL_HISTORY_WARN else "warn",
                             "summary": f"{int(history):,} undo records waiting to be purged",
                             "rows": [], "action": "End the oldest open transactions so InnoDB "
                                                   "can purge old row versions.",
                             "sql": None})
        if not tables and not (isinstance(pct, (int, float)) and pct >= 50) and not purge_lag:
            fine.append("Free space and purge" if mysql else "Vacuum and bloat")

    indexes = collectors.get("index_health")
    if isinstance(indexes, dict) and "index_health" not in errors:
        unused = [row for row in indexes.get("unused_indexes") or [] if isinstance(row, dict)]
        duplicates = [row for row in indexes.get("duplicates") or [] if isinstance(row, dict)]
        if unused:
            total = sum(float(row.get("size_bytes") or 0) for row in unused)
            problems.append({
                "check": "unused_indexes", "label": "Unused indexes", "status": "warn",
                "summary": f"{len(unused)} {'index' if len(unused) == 1 else 'indexes'} never "
                           f"read, {format_size(total)} in all",
                "rows": [{"name": f"{row.get('index')}", "value": format_size(row.get("size_bytes")),
                          "detail": f"on {row.get('table')}"} for row in unused[:10]],
                "action": HEALTH_ACTIONS["unused_indexes"],
                "sql": "\n".join(
                    f"ALTER TABLE {mysql_table(row.get('schema'), row.get('table'))} "
                    f"DROP INDEX `{row.get('index')}`;" if mysql else
                    f"DROP INDEX {row.get('schema', 'public')}.{row.get('index')};"
                    for row in unused[:10])})
        if duplicates:
            problems.append({
                "check": "duplicate_indexes", "label": "Duplicate indexes", "status": "warn",
                "summary": f"{len(duplicates)} {'index repeats' if len(duplicates) == 1 else 'indexes repeat'} "
                           "another one",
                "rows": [{"name": f"{row.get('redundant_index')}",
                          "value": format_size(row.get("wasted_bytes")),
                          "detail": f"covered by {row.get('covered_by')}"}
                         for row in duplicates[:10]],
                "action": HEALTH_ACTIONS["duplicate_indexes"],
                "sql": "\n".join(
                    f"ALTER TABLE {mysql_table(row.get('schema'), row.get('table'))} "
                    f"DROP INDEX `{row.get('redundant_index')}`;" if mysql else
                    f"DROP INDEX {row.get('schema', 'public')}.{row.get('redundant_index')};"
                    for row in duplicates[:10])})
        if not unused and not duplicates:
            fine.append("Indexes")

    connections = collectors.get("connections")
    if isinstance(connections, dict) and "connections" not in errors:
        total, limit = connections.get("total_connections") or 0, \
            connections.get("max_connections") or 0
        idle = int(connections.get("idle_in_transaction_count") or 0)
        found = False
        if limit and total / limit >= 0.8:
            found = True
            problems.append({"check": "connections", "label": "Connections",
                             "status": "bad" if total / limit >= 0.95 else "warn",
                             "summary": f"{total} of {limit} in use",
                             "rows": [], "action": HEALTH_ACTIONS["connections"], "sql": None})
        if idle:
            found = True
            sessions = [row for row in connections.get("long_running_idle_in_tx") or []
                        if isinstance(row, dict)]
            problems.append({
                "check": "idle_in_transaction", "label": "Idle in transaction",
                "status": "warn",
                "summary": f"{idle} {'session' if idle == 1 else 'sessions'} left a "
                           "transaction open",
                "rows": [{"name": str(row.get("application_name") or f"pid {row.get('pid')}"),
                          "value": f"{float(row.get('duration_seconds') or 0) / 60:.0f} min",
                          "detail": plain(row.get("query_preview") or "")}
                         for row in sessions[:10]],
                "action": HEALTH_ACTIONS["idle_in_transaction"], "sql": None})
        if not found:
            fine.append("Connections")

    config = collectors.get("config_audit")
    if isinstance(config, dict) and "config_audit" not in errors:
        off = [row for row in config.get("settings") or []
               if isinstance(row, dict) and row.get("status") in ("warn", "crit")]
        if off:
            problems.append({
                "check": "config_audit", "label": "Settings off recommendation",
                "status": "bad" if any(row.get("status") == "crit" for row in off) else "warn",
                "summary": f"{len(off)} {'setting differs' if len(off) == 1 else 'settings differ'} "
                           "from the recommendation",
                "rows": [{"name": str(row.get("parameter")),
                          "value": f"{plain(row.get('current'))}, recommended "
                                   f"{plain(row.get('recommended'))}",
                          "detail": plain(row.get("note"))} for row in off],
                "action": HEALTH_ACTIONS["config_audit"], "sql": None})
        else:
            fine.append("Settings")

    replication = collectors.get("replication")
    if isinstance(replication, dict) and "replication" not in errors:
        lag = replication.get("upstream_lag_seconds")
        inactive = int(replication.get("inactive_slots") or 0)
        rows = []
        if isinstance(lag, (int, float)) and lag >= 30:
            rows.append({"name": "Replica lag", "value": f"{lag:.0f} s", "detail": ""})
        if inactive:
            rows.append({"name": "Inactive slots", "value": str(inactive),
                         "detail": "They keep WAL on disk until they're read or dropped"})
        if rows:
            problems.append({"check": "replication", "label": "Replication", "status": "warn",
                             "summary": "Replication needs a look", "rows": rows,
                             "action": HEALTH_ACTIONS["replication"], "sql": None})
        else:
            fine.append("Replication")

    scans = collectors.get("sequential_scans")
    if isinstance(scans, dict) and "sequential_scans" not in errors:
        for action in database_actions({"sequential_scans": scans}, {}):
            problems.append({"check": "sequential_scans", "label": action["title"],
                             "status": action["severity"], "summary": action["finding"],
                             "rows": action["rows"], "action": action["steps"], "sql": None})
        if scans.get("compared") and not any(p["check"] == "sequential_scans" for p in problems):
            fine.append("Full table scans")

    lowest = (numbers or {}).get("cache_hit_ratio_min")
    if isinstance(lowest, (int, float)) and lowest < 0.9:
        problems.append({"check": "cache_hit", "label": "Cache hit", "status":
                         "bad" if lowest < 0.8 else "warn",
                         "summary": f"Dropped to {lowest * 100:.1f}% at its lowest",
                         "rows": [], "action": HEALTH_ACTIONS["cache_hit"], "sql": None})
    elif isinstance(lowest, (int, float)):
        fine.append("Cache hit")
    problems.sort(key=lambda problem: 0 if problem["status"] == "bad" else 1)
    return {"problems": problems, "fine": fine, "unavailable": unavailable}


# The day in brief

def _change_words(value: float | None, before: float | None, *, noun: str) -> str:
    if not isinstance(value, (int, float)) or not isinstance(before, (int, float)) or not before:
        return ""
    change = value / before - 1
    if abs(change) < 0.05:
        return f", about the same as {noun}"
    if value / before >= 2:
        return f", {value / before:.1f}x {noun}"
    return f", {'up' if change > 0 else 'down'} {abs(change) * 100:.0f}% on {noun}"


# What a finding means for the application, one plain line each. Health findings use their
# database action's line; the others their topic's.
ACTION_WHY = {
    "cache_hit": "More reads go to disk: queries wait on storage instead of memory, so they "
                 "slow down under load.",
    "setting_shared_buffers": "More reads go to disk: queries wait on storage instead of "
                              "memory, so they slow down under load.",
    "setting_innodb_buffer_pool_size": "More reads go to disk: queries wait on storage "
                                       "instead of memory, so they slow down under load.",
    "setting_work_mem": "Sorts and groupings spill to disk, so reporting queries take longer "
                        "than they need to.",
    "vacuum": "Dead rows make every scan read more pages, so queries on those tables get "
              "slower and push other data out of memory.",
    "analyze": "Stale statistics lead the planner to bad plans, so queries can suddenly get "
               "slower.",
    "wraparound": "If it reaches the limit, the database stops taking writes until a vacuum "
                  "catches up.",
    "rollbacks": "Each rollback is work thrown away, and a jump usually means the app is "
                 "hitting errors.",
    "deadlocks": "The database cancels one side of each deadlock, so the app sees an error "
                 "and has to retry.",
    "connections": "Near the limit, new connections get refused and the app starts failing "
                   "requests.",
    "idle_in_transaction": "An open transaction holds its locks and blocks cleanup, so other "
                           "writes wait and tables bloat.",
    "unused_indexes": "Every insert and update still writes these indexes, so writes pay for "
                      "reads that never happen.",
    "duplicate_indexes": "Every insert and update writes both copies, so writes pay twice for "
                         "the same reads.",
    "replica_lag": "Reads from the replica return old data, and a failover would lose recent "
                   "writes.",
    "inactive_slots": "An unread slot keeps WAL on disk, and the disk can fill up.",
    "setting_autovacuum": "Without it dead rows pile up, so tables bloat and queries slow "
                          "down over time.",
}
# Words that tie a sentence about the database to one action, checked in order.
ACTION_WORDS = (
    (("cache hit", "shared_buffers", "buffer pool", "from disk", "to disk", "memory"),
     ("cache_hit", "setting_shared_buffers", "setting_innodb_buffer_pool_size")),
    (("dead row", "bloat", "vacuum"), ("vacuum",)),
    (("rollback", "rolled back"), ("rollbacks",)),
    (("deadlock",), ("deadlocks",)),
    (("connection",), ("connections", "idle_in_transaction")),
    (("work_mem", "sort"), ("setting_work_mem",)),
    (("unused ind",), ("unused_indexes",)),
    (("duplicate ind",), ("duplicate_indexes",)),
    (("replica", "replication"), ("replica_lag", "inactive_slots")),
)
TOPIC_WHY = {
    "traffic": "Traffic sets how hard the database works, so it's the baseline for every "
               "other number here.",
    "traffic_up": "More queries a second means more work for the same server, so latency and "
                  "connections tend to follow.",
    "latency": "Latency is how long the app waits on each query, so it shows up as page and "
               "API speed.",
    "latency_up": "Every query waited longer on average, so pages and API calls that run them "
                  "got slower.",
    "latency_down": "Queries answered faster on average, so the app waited less on the "
                    "database.",
    "spike_traffic": "A burst like this queues queries behind each other and slows everything "
                     "running at the time.",
    "spike_latency": "During a latency spike every request that runs these queries waits "
                     "longer, which users notice as slow pages.",
    "queries": "When one query carries this much database time, fixing it does the most good "
               "for the whole database.",
    "events_open": "An open event is a change RDST saw that hasn't gone back to normal, so "
                   "it may still be slowing the app.",
    "events_closed": "Everything that changed went back to normal, so nothing from this day "
                     "is still affecting the app.",
    "events_none": "RDST saw no regressions, stuck queries or jumps in traffic.",
    "index": "An index change alters the plans the database can pick, so query speed can "
             "move after it.",
    "health": "A problem with the database itself slows every query, not just one.",
    "todo": "A fix that's ready is the quickest way to take load off the database.",
}


def action_why(action: dict[str, Any]) -> str:
    return ACTION_WHY.get(action["id"]) or action.get("why") or TOPIC_WHY["health"]


def topic_why(topic: str | None, text: str, actions: list[dict[str, Any]]) -> str | None:
    """The line under a brief item that came without one: from its words and topic."""
    lower = text.lower()
    if topic == "health" or topic is None:
        for words, ids in ACTION_WORDS:
            if any(word in lower for word in words):
                action = next((action for action in actions if action["id"] in ids), None)
                if action is not None:
                    return action_why(action)
                return ACTION_WHY[ids[0]] if ids[0] in ACTION_WHY else None
        if topic == "health":
            flagged = [action for action in actions if action["severity"] != "info"]
            return action_why(flagged[0]) if flagged else TOPIC_WHY["health"]
        return None
    if topic == "spike":
        return TOPIC_WHY["spike_traffic" if "traffic" in lower and "latency" not in lower
                         else "spike_latency"]
    if topic == "latency":
        faster = any(word in lower for word in ("faster", "fell", "down ", "dropped"))
        return TOPIC_WHY["latency_down" if faster else "latency_up"]
    if topic == "traffic":
        return TOPIC_WHY["traffic_up" if any(word in lower for word in ("rose", "up ", "x "))
                         else "traffic"]
    if topic == "events":
        return TOPIC_WHY["events_open" if "open" in lower else "events_closed"]
    return TOPIC_WHY.get(topic)


def brief_facts(snapshot: dict[str, Any], target: str) -> list[dict[str, Any]]:
    """The day's facts as sentences with {{q:key}} tokens. Each carries what it means for
    the application (`why`) and how much it matters today (`weight`, higher first)."""
    facts: list[dict[str, Any]] = []
    numbers = {row["metric"]: row for row in snapshot.get("numbers") or []}
    period = snapshot["period"]
    noun = _weekday(period["prior_start"]) if period.get("prior_start") else "the day before"
    qps, latency = numbers.get("qps") or {}, numbers.get("mean_ms") or {}
    if isinstance(qps.get("value"), (int, float)):
        text = (f"{target} averaged {_rate(qps['value'])} queries a second"
                f"{_change_words(qps['value'], qps.get('prior'), noun=noun)}")
        peak = snapshot.get("peak") or {}
        if peak.get("qps") and peak.get("ts"):
            text += f", busiest at {_clock(peak['ts'])} with {_rate(peak['qps'])} a second"
        change = qps.get("change")
        up = isinstance(change, (int, float)) and change >= 0.25
        facts.append({"kind": "traffic", "text": text + ".", "keys": [],
                      "why": TOPIC_WHY["traffic_up" if up else "traffic"],
                      "weight": 50 if isinstance(change, (int, float)) and abs(change) >= 0.5
                      else 20})
    if isinstance(latency.get("value"), (int, float)) and latency["value"]:
        before = latency.get("prior")
        text = f"Average latency was {format_ms(latency['value'])}"
        change = latency["value"] / before - 1 if isinstance(before, (int, float)) and before \
            else None
        if change is not None:
            text += (f", about the same as {noun}" if abs(change) < 0.05
                     else f", against {format_ms(before)} on {noun}")
        why = TOPIC_WHY["latency" if change is None or abs(change) < 0.05 else
                        "latency_up" if change > 0 else "latency_down"]
        facts.append({"kind": "latency", "text": text + ".", "keys": [], "why": why,
                      "weight": 75 if change is not None and change >= 0.2 else
                      40 if change is not None and change <= -0.2 else 15})
    queries = snapshot.get("queries") or []
    if queries:
        busiest = max(queries, key=lambda row: row["calls"])
        heaviest = max(queries, key=lambda row: row["total_ms"])
        if busiest["query_key"] == heaviest["query_key"]:
            text = (f"{{{{q:{busiest['query_key']}}}}} ran most often and took "
                    f"{format_pct(heaviest['share'])} of database time.")
            keys = [busiest["query_key"]]
        else:
            text = (f"{{{{q:{busiest['query_key']}}}}} ran most often, and "
                    f"{{{{q:{heaviest['query_key']}}}}} took the most database time "
                    f"({format_pct(heaviest['share'])}).")
            keys = [busiest["query_key"], heaviest["query_key"]]
        facts.append({"kind": "queries", "text": text, "keys": keys,
                      "why": TOPIC_WHY["queries"],
                      "weight": 55 if heaviest["share"] >= 0.3 else 30})
    events = [event for event in snapshot.get("events") or [] if event["kind"] != "index"]
    if events:
        still = [event for event in events if event["state"] == "open"
                 and event["kind"] != "resolved"]
        text = (f"RDST noted {len(events)} {'event' if len(events) == 1 else 'events'}; "
                + (f"{len(still)} {'is' if len(still) == 1 else 'are'} still open."
                   if still else "it went back to normal." if len(events) == 1 else
                   "all of them went back to normal."))
        keys: list[str] = []
        lead = next((event for event in still if event["query_keys"]), None)
        if lead is not None:
            keys = lead["query_keys"][:1]
            text += (f" Open since {_clock(lead['ts'])}: {{{{q:{keys[0]}}}}} "
                     f"({lead['what'].lower()}).")
        harmful = any(event.get("tone") == "negative" for event in still)
        facts.append({"kind": "events", "text": text, "keys": keys,
                      "why": TOPIC_WHY["events_open" if still else "events_closed"],
                      "weight": 85 if harmful else 65 if still else 35})
    else:
        facts.append({"kind": "events", "text": "Nothing unusual happened.", "keys": [],
                      "why": TOPIC_WHY["events_none"], "weight": 5})
    indexes = [event for event in snapshot.get("events") or [] if event["kind"] == "index"]
    if indexes:
        first = indexes[0]
        facts.append({"kind": "index", "text": f"{first['what']}: {first['title']} at "
                                               f"{_clock(first['ts'])}.", "keys": [],
                      "why": TOPIC_WHY["index"], "weight": 30})
    ready = [row for row in snapshot.get("look_at") or [] if row["status"] == "fix_ready"]
    actions = [action for action in snapshot.get("actions") or []
               if action["severity"] != "info"]
    if actions:
        # Health leads the page with the finding; the brief leads with what to do about it.
        finding = actions[0]["finding"]
        if finding[:2].istitle():
            finding = finding[0].lower() + finding[1:]
        facts.append({"kind": "health", "text": f"{actions[0]['title']}: {finding}", "keys": [],
                      "why": action_why(actions[0]),
                      "weight": 90 if actions[0]["severity"] == "bad" else 70})
    # What to change is counted on Actions, which stays current; the day names its fixes.
    if ready:
        facts.append({"kind": "todo", "text": f"{len(ready)} "
                      f"{'query has' if len(ready) == 1 else 'queries have'} a suggested fix.",
                      "keys": [], "why": TOPIC_WHY["todo"], "weight": 45})
    spikes = snapshot.get("spikes") or []
    if spikes:
        biggest = max(spikes, key=lambda spike: spike["ratio"] or 0)
        position = next((i for i, fact in enumerate(facts) if fact["kind"] == "latency"), 0) + 1
        facts.insert(position, {"kind": "spike", "text": biggest["summary"],
                                "keys": [row["query_key"] for row in biggest["queries"][:1]],
                                "why": TOPIC_WHY[f"spike_{biggest['metric']}"],
                                "weight": 60 + min(10.0, biggest["ratio"] or 0) * 2})
    return facts


BRIEF_LIMIT = 3
BRIEF_TOPICS = ("traffic", "latency", "spike", "queries", "events", "health", "index", "todo")


def fallback_brief(facts: list[dict[str, Any]], limit: int = BRIEF_LIMIT
                   ) -> list[dict[str, Any]]:
    """The day's top priorities from the facts, most important first."""
    ranked = sorted(enumerate(facts), key=lambda pair: (-(pair[1].get("weight") or 0), pair[0]))
    return [fact for _, fact in ranked[:limit]]


def allowed_numbers(*texts: str) -> set[str]:
    return set(_NUMBER.findall(_TOKEN.sub("", " ".join(texts))))


_MOVE = re.compile(r"\b(fell|dropped|decreased|shrank|rose|climbed|increased|grew|jumped)"
                   r"\s+from\s+([\d.,]+)\s*(ms|s)?\b[^.]*?\bto\s+([\d.,]+)\s*(ms|s)?\b",
                   re.IGNORECASE)
_DOWN_WORDS = {"fell", "dropped", "decreased", "shrank"}


def _amount(number: str, unit: str | None) -> float:
    value = float(number.replace(",", ""))
    return value * 1000 if (unit or "").lower() == "s" else value


def _direction_matches(text: str) -> bool:
    """'fell from 1.2 s to 6.7 s' names the wrong direction; so does 'rose from 9 to 3'."""
    for verb, before, before_unit, after, after_unit in _MOVE.findall(text):
        try:
            start, end = _amount(before, before_unit), _amount(after, after_unit or before_unit)
        except ValueError:
            continue
        if start != end and (end < start) != (verb.lower() in _DOWN_WORDS):
            return False
    return True


# Phrasing that reads as machine-written filler; a sentence carrying it falls back to the facts.
_SLOP = re.compile(
    r"\b(?:quietly|simply|somehow|seamless(?:ly)?|crucial(?:ly)?|here's|here is|"
    r"the whole (?:plan|point|thing)|the only thing|that's it|at the end of the day|"
    r"the reality is)\b|\bnot\b[^.]{0,60}\b(?:but|it's|it is)\b|;", re.IGNORECASE)


def clean_sentence(text: str, allowed: set[str], keys: set[str]) -> bool:
    """A sentence the report may show: no SQL, no dashes, no filler phrasing, only known
    queries and numbers, and every "rose/fell from X to Y" pointing the way the numbers move."""
    stripped = _TOKEN.sub("", text)
    return bool(stripped.strip()) and _direction_matches(stripped) and not (
        _SLOP.search(stripped) or
        _SQL.search(stripped) or re.search("[–—]", text) or
        any(number not in allowed for number in _NUMBER.findall(stripped)) or
        any(key not in keys for key in _TOKEN.findall(text)))


def brief_sentences(summary: str | None, facts: list[dict[str, Any]]) -> tuple[list[str], str]:
    """The model's phrasing when it keeps to the facts, else the facts themselves.

    It must name queries only by token, carry no SQL, and use no number the facts don't.
    """
    fallback = [fact["text"] for fact in fallback_brief(facts)]
    text = " ".join(str(summary or "").split())
    if not text:
        return fallback, "facts"
    sentences = [part for part in re.split(r"(?<=[.!?])\s+", text) if part]
    allowed = allowed_numbers(*(fact["text"] for fact in facts))
    keys = {key for fact in facts for key in fact["keys"]}
    if not 2 <= len(sentences) <= 6 or not clean_sentence(text, allowed, keys):
        return fallback, "facts"
    return sentences[:BRIEF_LIMIT], "model"


# Assembly

def database_check(store: MonitorStore, target: str, now: int,
                   collectors: dict[str, Any]) -> list[dict[str, Any]]:
    """Today's database findings from fresh checks, measured as the checkup measures them."""
    start, end, _ = report_period("user", now)
    prior_start, prior_end = prior_period(start, end)
    return database_actions(collectors, _window_numbers(store, target, start, end),
                            _window_numbers(store, target, prior_start, prior_end))


def build(store: MonitorStore, target: str, start: int, end: int, now: int, *,
          partial: bool, run_analyses: list[dict[str, Any]] | None = None,
          provisional: Iterable[str] = (), collectors: dict[str, Any] | None = None
          ) -> dict[str, Any]:
    """The frozen record of one day (or today so far)."""
    from features.monitor.api import views

    prior_start, prior_end = prior_period(start, end)
    current = _window_numbers(store, target, start, end)
    before = _window_numbers(store, target, prior_start, prior_end)
    # The comparison day is the previous local day's own recorded readings; a day before
    # recording began has none, and then there's no earlier day to compare with.
    compared = bool(before)
    prior_from = before.get("first_ts") if compared else None
    queries, names = day_queries(store, target, start, end, now, run_analyses, provisional)
    texts = views.query_texts(store, target)[0]
    events = day_events(store, target, start, end)
    chart = day_chart(store, target, start, end, now, event_markers(events), names)
    by_key = chart.pop("_by_key", None) if chart else None
    actions = database_actions(collectors, current, before)
    snapshot = {
        "version": SNAPSHOT_VERSION,
        "period": {"start": start, "end": end, "partial": partial,
                   "day": datetime.fromtimestamp(start).date().isoformat(),
                   "prior_start": prior_start if compared else None,
                   "prior_end": prior_end if compared else None,
                   "prior_observed_from": prior_from
                   if prior_from and prior_from >= prior_start + 3600 else None,
                   "observed_from": current.get("first_ts"),
                   "observed_to": current.get("last_ts")},
        "numbers": day_numbers(current, before),
        "peak": {"qps": current.get("peak_qps"), "ts": current.get("peak_qps_at")},
        "chart": chart, "events": events, "queries": queries,
        "look_at": look_at(queries, texts),
        "health": health_section(collectors, current),
        "overview": health_overview(collectors, current, actions,
                                    sum(1 for event in events if event["state"] == "open"
                                        and event.get("tone") == "negative")),
        "actions": actions,
        "vitals": current.get("vitals"),
        "spikes": day_spikes(chart, events, by_key),
        "engine": str((collectors or {}).get("engine") or "postgresql"),
        "names": {key: names.get(key) or display_name(texts.get(key, ""))
                  for key in {row["query_key"] for row in queries}
                  | {key for event in events for key in event["query_keys"]}},
    }
    snapshot["facts"] = brief_facts(snapshot, target)
    return snapshot


def with_brief(snapshot: dict[str, Any], summary: str | None,
               narrative: dict[str, Any] | None = None) -> dict[str, Any]:
    """The day in brief, and the narrative's picks merged onto the computed sections.

    `narrative` holds what the report stored after checking it against the facts: brief
    items with their topics, ranked database recommendations and per-query notes.
    """
    narrative = narrative or {}
    facts = snapshot.get("facts") or []
    computed = snapshot.get("actions") or []
    items = [item for item in narrative.get("brief_items") or [] if isinstance(item, dict)]
    if len(items) >= 2:
        items, source = items[:BRIEF_LIMIT], "model"
    else:
        sentences, source = brief_sentences(summary, facts)
        if source == "facts":
            items = [{"topic": fact["kind"], "text": fact["text"], "why": fact.get("why")}
                     for fact in fallback_brief(facts)]
        else:
            items = [{"topic": None, "text": sentence} for sentence in sentences]
    # Every priority says what it means for the application; older answers came without.
    items = [{**item, "why": item.get("why") or
              topic_why(item.get("topic"), item["text"], computed)} for item in items]
    sentences = [item["text"] for item in items]
    picks = {pick["action_id"]: (rank, pick) for rank, pick in enumerate(
        pick for pick in narrative.get("database_recommendations") or []
        if isinstance(pick, dict))}
    actions = []
    for action in snapshot.get("actions") or []:
        rank, pick = picks.get(action["id"], (None, {}))
        actions.append({**action, "pick": rank, "model_why": pick.get("why")})
    actions.sort(key=lambda action: (action["pick"] is None,
                                     action["pick"] if action["pick"] is not None else 0))
    notes = {note["query_key"]: note for note in narrative.get("query_notes") or []
             if isinstance(note, dict)}
    look = [{**row, "note": notes.get(row["query_key"])} for row in snapshot.get("look_at") or []]
    return {**snapshot, "brief": sentences, "brief_source": source, "brief_items": items,
            "actions": actions, "look_at": look}


def legacy(report: dict[str, Any], row: dict[str, Any]) -> dict[str, Any] | None:
    """A readable snapshot for a report written before snapshots, from what it stored.

    It has no chart (the minute readings are gone); numbers come from its stored 24 h
    deltas, queries from its top queries and analyses, health from its collectors.
    """
    computed = report.get("computed") or {}
    if not computed:
        return None
    end = int(row.get("ts") or 0)
    start = end - 86400
    database = ((computed.get("deltas") or {}).get("24h") or {}).get("database") or {}

    def pair(field: str) -> tuple[Any, Any]:
        value = database.get(field) or {}
        return value.get("current"), value.get("prior")

    calls, prior_calls = pair("calls")
    mean, prior_mean = pair("mean_ms")
    current = {"qps": calls / 86400 if isinstance(calls, (int, float)) else None,
               "mean_ms": mean or None,
               "active_conns_peak": pair("active_conns")[0],
               "cache_hit_ratio_min": pair("cache_hit_ratio")[0],
               "xact_rollback": pair("xact_rollback")[0], "deadlocks": pair("deadlocks")[0]}
    prior = {"qps": prior_calls / 86400 if isinstance(prior_calls, (int, float)) and prior_calls
             else None, "mean_ms": prior_mean or None,
             "active_conns_peak": pair("active_conns")[1],
             "cache_hit_ratio_min": pair("cache_hit_ratio")[1],
             "xact_rollback": pair("xact_rollback")[1] or None,
             "deadlocks": pair("deadlocks")[1] or None}
    target = row.get("target") or ""
    refs = {item["query_key"]: item for item in computed.get("analyses") or []
            if isinstance(item, dict) and item.get("analysis_ref")}
    provisional = set(report.get("provisional_query_keys") or [])
    queries = []
    for item in computed.get("top_queries") or []:
        if not isinstance(item, dict):
            continue
        key = item["query_key"]
        ref = refs.get(key)
        digest = None
        if ref:
            try:
                digest = digest_for_ref(target, key, ref["analysis_ref"], ref.get("mode"))
            except Exception:
                digest = None
        fix = (digest or {}).get("fix")
        status = "fix_ready" if digest and digest.get("fix_ready") else \
            "healthy" if digest else "not_analyzed"
        queries.append({"query_key": key, "display_name": item.get("display_name") or
                        display_name(item.get("text") or ""),
                        "calls": None, "qps": None, "total_ms": item.get("total_ms") or 0.0,
                        "mean_ms": None, "share": item.get("db_time_share") or 0.0,
                        "prior_calls": None, "prior_mean_ms": None, "latency_change": None,
                        "status": status, "status_reason": (fix or {}).get("title"),
                        "score": (digest or {}).get("efficiency_score"),
                        "grade": grade((digest or {}).get("efficiency_score")),
                        "fix": {"kind": fix["kind"], "title": fix["title"],
                                "sql": fix.get("sql")} if fix else None,
                        "analysis_ref": (digest or {}).get("analysis_ref"),
                        "analysis_mode": (digest or {}).get("mode"),
                        "analyzed_at": (digest or {}).get("analyzed_at"),
                        "headline": (digest or {}).get("headline"),
                        "findings": [plain(text) for text in (digest or {}).get("findings") or []],
                        "provisional": key in provisional, "new": False,
                        "text": item.get("text") or ""})
    texts = {item["query_key"]: item.get("text") or "" for item in computed.get("top_queries") or []
             if isinstance(item, dict)}
    events = [{"ts": item.get("ts"), "kind": "created", "item_id": item.get("id"),
               "type": item.get("type"), "what": EVENT_WORDS.get(item.get("type"),
                                                                 item.get("title") or ""),
               "title": item.get("title") or "", "detail": plain(item.get("summary")),
               "query_keys": list(item.get("query_keys") or []), "state": "unknown",
               "resolved_at": None, "tone": None, "delivery": "digest"}
              for item in report.get("digest") or [] if isinstance(item, dict)]
    snapshot = {"version": SNAPSHOT_VERSION, "legacy": True,
                "period": {"start": start, "end": end, "partial": False, "rolling": True,
                           "day": datetime.fromtimestamp(end).date().isoformat(),
                           "prior_start": start - 86400, "prior_end": start,
                           "observed_from": None, "observed_to": None},
                "numbers": day_numbers(current, prior), "peak": {"qps": None, "ts": None},
                "chart": None, "events": events, "queries": queries,
                "look_at": look_at(queries, texts),
                "health": health_section(computed.get("health_collectors"), current),
                "names": {row["query_key"]: row["display_name"] for row in queries}}
    collectors = computed.get("health_collectors")
    actions = database_actions(collectors, current, prior)
    snapshot.update(actions=actions, overview=health_overview(collectors, current, actions),
                    vitals=None, spikes=[],
                    engine=str((collectors or {}).get("engine") or "postgresql"))
    snapshot["facts"] = brief_facts(snapshot, target)
    return with_brief(snapshot, None)

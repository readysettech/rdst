"""Read models for the monitor API in the UI contract shape (UI brief sections 4.3 and 5).

The number of SQLite reads a reader issues never depends on how many queries a target
has, so list pages stay flat as the workload grows.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Iterable

from features.monitor.analysis_digest import digest_for_ref, library_analysis_jobs, summary_for_job
from features.monitor.connection import ANSWERED
from features.monitor.db_actions import concurrent_index_sql
from features.monitor.naming import target_names
from features.monitor.ranking import PRIOR_CALLS, blended_ms
from features.monitor.presentation import (
    INCIDENT_LOOKAHEAD_S,
    INCIDENT_LOOKBACK_S,
    INCIDENT_NEAR_S,
    analysis_error_kind,
    analysis_reason,
    clock_text,
    database_fix,
    display_name,
    first_changed_at,
    format_pct,
    group_is_latency,
    incident_levels,
    incident_signal,
    incident_span,
    item_view,
    latency_text,
    member_signal,
    present_inbox_item,
    settle_status,
    trend_metric,
)
from shared.monitor.statements import alert_member, analyzability
from shared.monitor.monitor_store import EVERYTHING_ELSE, RESOLUTIONS, RETENTION, MonitorStore, item_query_keys, rule_matches

logger = logging.getLogger(__name__)
RANGE_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "24h": 86400, "7d": 7 * 86400,
                 "30d": 30 * 86400, "90d": 90 * 86400}
RANGE_RES = {"5m": 60, "15m": 60, "1h": 60, "24h": 60, "7d": 300, "30d": 3600, "90d": 3600}
# The load chart stacks one band per top query, so longer ranges use coarser buckets.
LOAD_RES = {"5m": 60, "15m": 60, "1h": 60, "24h": 300, "7d": 3600, "30d": 3600, "90d": 86400}
RANGES = (*RANGE_SECONDS, "all")
LOAD_TOP = 8
MAX_POINTS = 1000
MINI_POINTS = 60
MINI_STEPS = (60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400,
              172800, 345600, 604800)
ATTENTION_TYPES = {"latency_regression", "plan_change", "table_slowdown", "slow_creep",
                   "traffic_spike", "stuck_query", "new_heavy_query",
                   "query_stopped_running", "many_queries_changed"}
SIGNIFICANT_CHANGE = 0.2
SIGNIFICANT_CALLS = 30
REGISTRY_TTL_SECONDS = 30
_registry_cache: dict[str, Any] = {"at": 0.0, "entries": {}}
_PLACEHOLDER_RE = re.compile(r"\$\d+|\?|(?<![:\w]):[A-Za-z_]\w*")


def _param_source(source: str | None, text: str | None) -> str | None:
    """Jobs recorded `none` for a query without placeholders; it had no values to find."""
    if source == "none" and text and not _PLACEHOLDER_RE.search(text):
        return None
    return source


# History span and ranges (brief 4.3, 5.1)

def history_span(store: MonitorStore, target: str, now: int) -> dict[str, Any]:
    firsts: dict[int, int] = {}
    lasts: dict[int, int] = {}
    for table in ("db_series", "stmt_series"):
        for row in store.read(f"SELECT res,MIN(bucket_start) first,MAX(bucket_start) last "
                              f"FROM {table} WHERE target=? GROUP BY res", (target,)):
            res = row["res"]
            firsts[res] = min(firsts.get(res, row["first"]), row["first"])
            lasts[res] = max(lasts.get(res, row["last"]), row["last"])
    first = None
    # Rollup buckets start on their own boundary, before the first reading they hold.
    # The finest resolution whose oldest row is inside its retention is exact.
    for res in RESOLUTIONS:
        if res in firsts:
            first = firsts[res] if first is None else min(first, firsts[res])
            retention = RETENTION.get(res)
            if retention is None or firsts[res] > now - retention + res:
                break
    seen = store.read("SELECT MIN(first_seen) first FROM query_text WHERE target=?",
                      (target,))[0]["first"]
    if first is not None and seen is not None and first < seen and first != firsts.get(60):
        first = min(seen, firsts.get(60, seen))
    last = lasts.get(60, max(lasts.values()) if lasts else None)
    span = last - first + 60 if first is not None and last is not None else 0
    return {"first_ts": first, "last_ts": last, "span_seconds": max(0, span),
            "resolutions": {str(res): {"first_ts": firsts.get(res)} for res in RESOLUTIONS}}


def resolve_range(range: str | None, window: str | None) -> str:
    value = range or window or "24h"
    if value not in RANGES:
        raise ValueError("Unsupported range")
    return value


def range_bounds(range: str, now: int, history: dict[str, Any]) -> tuple[int, int, int]:
    """Return (start, seconds, res): the window a range covers and its chart resolution."""
    if range == "all":
        first = history["first_ts"] if history["first_ts"] is not None else now - 3600
        seconds = max(60, now - first)
        res = next((res for res in RESOLUTIONS if seconds / res <= MAX_POINTS), 86400)
        return first, seconds, res
    seconds = RANGE_SECONDS[range]
    return now - seconds, seconds, RANGE_RES[range]


def mini_step(seconds: int) -> int:
    return next((step for step in MINI_STEPS if seconds / step <= MINI_POINTS), MINI_STEPS[-1])


def timeline(start: int, now: int, step: int, history: dict[str, Any],
             buckets: Iterable[int] = ()) -> list[int]:
    """Buckets from the later of the range start and the first reading, through now.

    A rolled-up row can begin before the first exact reading; its buckets are kept.
    """
    first = min([value for value in (history["first_ts"], min(buckets, default=None))
                 if value is not None], default=None)
    begin = max(start, first) if first is not None else now
    return list(range(begin // step * step, now // step * step + 1, step))


# Complete, non-overlapping rows. Up to the caller's bucket size a closed coarser bucket
# replaces the finer rows it covers, so sums read as few rows as possible. Above it, a closed
# bucket gives way to the finer rows inside it, so a chart never spreads a coarse row evenly
# where finer history is kept; it stays when they are gone (demoted for the storage budget,
# or past retention), and then also where it straddles the window's edge. Rows of a
# resolution count only from the first boundary after their retention edge, so finer rows
# that replace a bucket cover all of it. Finer rows count where the same identity has no
# covering retained parent, including partial window edges and tiers without daily rows.
# SQLite picks the rows and sums them, so reads return few rows.

def fine_from(res: int, now: int) -> int:
    """The first bucket start from which rows of `res` are complete."""
    retention = RETENTION.get(res)
    if retention is None:
        return 0
    align = RESOLUTIONS[-1] if res == RESOLUTIONS[-2] else RESOLUTIONS[-2]
    return -(-(now - retention) // align) * align


def _complete(table: str, fields: str, store: MonitorStore, target: str, start: int, end: int,
              keys: list[str] | None, step: int | None) -> tuple[str, tuple[Any, ...]]:
    """The complete-rows SELECT, one branch per resolution, and its parameters."""
    start, end = int(start), int(end)
    top = max((res for res in RESOLUTIONS if step is None or res <= step), default=RESOLUTIONS[0])
    rolled = {int(row["key"].rsplit("_", 1)[1]): int(row["value"]) for row in store.read(
        "SELECT key,value FROM monitor_setting WHERE target=? AND key LIKE 'rollup_until_%'",
        (target,))}
    # Retention runs with the rollup, which ran less than one bucket after its minute mark.
    minute_mark = rolled.get(RESOLUTIONS[0])
    edge = {res: fine_from(res, minute_mark + RESOLUTIONS[1]) if minute_mark is not None and res != 60 else 0
            for res in RESOLUTIONS}
    keyed = table == "stmt_series"
    key_filter = " AND query_key IN (SELECT value FROM json_each(?))" if \
        keyed and keys is not None else ""
    same_key = " AND x.query_key=s.query_key" if keyed else ""
    head = (target, *((json.dumps(keys),) if key_filter else ()))
    branches = []
    for res in RESOLUTIONS:
        where = [f"res={res}", f"bucket_start>={edge[res]}", f"bucket_start<{end}"]
        # Whether any of the finer rows this bucket summed are kept.
        kept = " OR ".join(
            f"EXISTS (SELECT 1 FROM {table} x WHERE x.target=s.target{same_key} "
            f"AND x.res={child} AND x.bucket_start>=s.bucket_start "
            f"AND x.bucket_start<s.bucket_start+{res} AND x.bucket_start>={edge[child]})"
            for child in RESOLUTIONS if child < res)
        if res == RESOLUTIONS[0]:
            where.append(f"bucket_start>={start}")
        else:
            where.append(f"bucket_start+{res}>{start}")
        if RESOLUTIONS[0] < res <= top:
            # A bucket straddling an edge of the window counts when none of its finer rows
            # are kept (older history holds only the rollups); else those inside count.
            where.append(f"((bucket_start>={start} AND bucket_start+{res}<={end}) "
                         f"OR NOT ({kept}))")
        if res < top:
            where += [f"NOT EXISTS (SELECT 1 FROM {table} x WHERE x.target=s.target{same_key} "
                      f"AND x.res={cover} AND x.bucket_start=s.bucket_start/{cover}*{cover} "
                      f"AND x.bucket_start>={max(start, edge[cover])} "
                      f"AND x.bucket_start+{cover}<={end})"
                      for cover in RESOLUTIONS if res < cover <= top]
        elif res > top:
            where.append(f"NOT ({kept})")
        branches.append(f"SELECT {fields} FROM {table} s WHERE target=?{key_filter} AND "
                        + " AND ".join(where))
    return " UNION ALL ".join(branches), head * len(branches)


def complete_rows(store: MonitorStore, table: str, target: str, start: int, end: int,
                  fields: str, keys: list[str] | None = None,
                  step: int | None = None) -> list[dict[str, Any]]:
    """Complete rows in [start, end); `step` is the finest bucket the caller needs."""
    sql, params = _complete(table, fields, store, target, start, end, keys, step)
    return store.read(f"{sql} ORDER BY ts", params)


def _minute_chart_rows(store: MonitorStore, table: str, target: str, start: int, end: int,
                       keys: list[str] | None = None,
                       leaders: list[str] | None = None) -> list[dict[str, Any]]:
    """Use exact minutes and retained rollups, aggregating chart bands inside SQLite."""
    keyed = table == "stmt_series"
    key_filter = " AND query_key IN (SELECT value FROM json_each(?))" if keyed and keys is not None else ""
    same_key = " AND f.query_key=s.query_key" if keyed else ""
    marks = store.read("SELECT CAST(value AS INTEGER) ts FROM monitor_setting WHERE target=? "
                       "AND key='rollup_until_60'", (target,))
    reference = marks[0]["ts"] if marks else int(store.clock())
    params = (start // 60 * 60, end, target, start // 86400 * 86400, end, start,
              *((json.dumps(keys),) if key_filter else ()), max(end, reference) - 48 * 3600)
    identity = "query_key," if keyed else ""
    counters = ["calls", "total_ms"]
    if keyed:
        counters.append("temp_blks")
    values = []
    if not keyed:
        counters += ["blks_hit", "blks_read", "xact_commit", "xact_rollback", "deadlocks"]
        values = ["active_conns", "max_conns", "cache_hit_ratio", "lock_waits"]
    fields = [f"{field}*60.0/res {field}" for field in counters] + values
    sql = (
        "WITH RECURSIVE ticks(ts) AS (SELECT ? UNION ALL SELECT ts+60 FROM ticks WHERE ts+60<?),"
        f"source AS (SELECT * FROM {table} s WHERE target=? AND bucket_start>=? "
        f"AND bucket_start<? AND bucket_start+res>?{key_filter} "
        f"AND (res=60 OR bucket_start+res<=? OR NOT EXISTS (SELECT 1 FROM {table} f "
        f"WHERE f.target=s.target{same_key} AND f.res=60 "
        "AND f.bucket_start>=s.bucket_start AND f.bucket_start<s.bucket_start+s.res))),"
        f"expanded AS (SELECT {identity}bucket_start ts,res," + ",".join(counters + values) +
        f" FROM source WHERE res=60 UNION ALL SELECT {identity}ts,res," +
        ",".join(counters + values) +
        " FROM source JOIN ticks ON ts>=bucket_start AND ts<bucket_start+res WHERE res>60),"
        f"ranked AS (SELECT *,ROW_NUMBER() OVER (PARTITION BY {identity}ts ORDER BY "
        "CASE res WHEN 60 THEN 0 WHEN 300 THEN 1 WHEN 3600 THEN 2 ELSE 3 END) priority "
        "FROM expanded),"
        f"used AS (SELECT {identity}ts,60 res,res source_res," + ",".join(fields) +
        " FROM ranked WHERE priority=1) ")
    if leaders is not None:
        sql += ("SELECT CASE WHEN query_key IN (SELECT value FROM json_each(?)) "
                "THEN query_key ELSE 'other' END query_key,ts,res,MAX(source_res) source_res,"
                "SUM(calls) calls,SUM(total_ms) total_ms,SUM(temp_blks) temp_blks FROM used GROUP BY 1,2 ORDER BY ts")
        params += (json.dumps(leaders),)
    else:
        sql += "SELECT * FROM used ORDER BY ts"
    return store.read(sql, params)


def _minute_detail_note(rows: Iterable[dict[str, Any]], res: int) -> str | None:
    if res == 60 and any(row.get("source_res", row["res"]) > 60 for row in rows):
        return "Minute detail is kept for 48 hours. This chart uses stored averages where minute detail is unavailable."
    return None


def db_rows(store: MonitorStore, target: str, start: int, end: int,
            step: int | None = None) -> list[dict[str, Any]]:
    if step == 60:
        return _minute_chart_rows(store, "db_series", target, start, end)
    return complete_rows(store, "db_series", target, start, end, "bucket_start ts,*",
                         step=step)


_STMT_FIELDS = "query_key,bucket_start ts,res,calls,total_ms,temp_blks"


def stmt_rows(store: MonitorStore, target: str, start: int, end: int,
              key: str | list[str] | None = None,
              step: int | None = None, *, chart: bool = False) -> list[dict[str, Any]]:
    """Complete statement rows; with `step`, rows no coarser than it are pre-summed per bucket."""
    keys = [key] if isinstance(key, str) else key
    if step == 60 and chart:
        return _minute_chart_rows(store, "stmt_series", target, start, end, keys)
    if step is None:
        return complete_rows(store, "stmt_series", target, start, end, _STMT_FIELDS, keys)
    return _stepped(store, target, start, end, keys, step, "query_key", ())


def band_rows(store: MonitorStore, target: str, start: int, end: int, step: int,
              leaders: list[str], others: Iterable[str]) -> list[dict[str, Any]]:
    """Stepped rows of `leaders`, with every query in `others` summed under the key "other"."""
    if step == 60:
        return _minute_chart_rows(store, "stmt_series", target, start, end,
                                  [*leaders, *others], leaders)
    return _stepped(store, target, start, end, [*leaders, *others], step,
                    "CASE WHEN query_key IN (SELECT value FROM json_each(?)) "
                    "THEN query_key ELSE 'other' END", (json.dumps(leaders),))


def _stepped(store: MonitorStore, target: str, start: int, end: int, keys: list[str] | None,
             step: int, key_sql: str, key_params: tuple[Any, ...]) -> list[dict[str, Any]]:
    sql, params = _complete("stmt_series", _STMT_FIELDS, store, target, start, end, keys, step)
    step = int(step)
    return store.read(
        f"WITH used AS ({sql}) SELECT {key_sql} query_key,"
        f"CASE WHEN res<={step} THEN ts/{step}*{step} ELSE ts END ts,"
        f"CASE WHEN res<={step} THEN {step} ELSE res END res,"
        "SUM(calls) calls,SUM(total_ms) total_ms,SUM(temp_blks) temp_blks FROM used GROUP BY 1,2,3 ORDER BY 2,1",
        (*params, *key_params))


def stmt_totals(store: MonitorStore, target: str, start: int, end: int,
                keys: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Recorded calls and execution time per query over [start, end), summed in SQLite."""
    sql, params = _complete("stmt_series", _STMT_FIELDS, store, target, start, end, keys, None)
    rows = store.read(f"WITH used AS ({sql}) SELECT query_key,SUM(calls) calls,"
                      "SUM(total_ms) total_ms FROM used GROUP BY query_key", params)
    return {row["query_key"]: row for row in rows}


def totals_by_key(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = totals.setdefault(row["query_key"], {"query_key": row["query_key"], "calls": 0,
                                                     "total_ms": 0.0})
        entry["calls"] += row["calls"]
        entry["total_ms"] += row["total_ms"]
    return totals


def spread(rows: Iterable[dict[str, Any]], step: int) -> dict[int, list[tuple[dict, float]]]:
    """Assign rows to step-sized buckets; a coarser row is spread evenly across its span."""
    buckets: dict[int, list[tuple[dict, float]]] = defaultdict(list)
    for row in rows:
        res = row["res"]
        if res <= step:
            buckets[row["ts"] // step * step].append((row, 1.0))
        else:
            parts = max(1, res // step)
            for index in range(parts):
                buckets[(row["ts"] + index * step) // step * step].append((row, 1 / parts))
    return buckets


def _seconds_in(bucket: int, step: int, now: int) -> int:
    return max(60, min(step, now - bucket))


# Markers (brief 4.4, 5.4)

def _index_markers(store: MonitorStore, target: str, start: int, end: int,
                   tables: set[str] | None) -> list[dict[str, Any]]:
    """Index changes as timeline facts. They carry no claim about any query's latency."""
    markers = []
    for change in store.read('SELECT ts,kind,"table",index_name,definition FROM index_change '
                             "WHERE target=? AND ts>=? AND ts<? ORDER BY ts,id",
                             (target, start, end)):
        if tables is not None and change["table"].lower() not in tables:
            continue
        added = change["kind"] == "added"
        markers.append({"ts": change["ts"], "kind": "index_added" if added else "index_dropped",
                        "label": f"{change['index_name']} {'added' if added else 'dropped'}",
                        "index_name": change["index_name"], "table": change["table"],
                        "matches_recommendation": False})
    return markers


def _inbox_markers(items: Iterable[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    return [{"ts": item["created_at"], "kind": "inbox", "label": f"{item['title']} filed",
             "inbox_id": item["id"], "severity": item["severity"]}
            for item in items if start <= item["created_at"] < end and not item.get("muted_by")
            and item.get("delivery", "inbox") != "digest"]


# 5.1 Health

def _health_point(bucket: int, parts: list[tuple[dict, float]], step: int,
                  now: int) -> dict[str, Any]:
    if not parts:
        return {"ts": bucket, "qps": None, "mean_ms": None, "active_conns": None,
                "max_conns": None, "cache_hit_ratio": None, "xact_rollback": None,
                "deadlocks": None, "lock_waits": None}
    calls = sum((row["calls"] or 0) * share for row, share in parts)
    total = sum((row["total_ms"] or 0) * share for row, share in parts)
    hits = sum((row["blks_hit"] or 0) * share for row, share in parts)
    reads = sum((row["blks_read"] or 0) * share for row, share in parts)
    ratios = [row["cache_hit_ratio"] for row, _ in parts if row["cache_hit_ratio"] is not None]
    active = [row["active_conns"] for row, _ in parts if row["active_conns"] is not None]
    limits = [row["max_conns"] for row, _ in parts if row["max_conns"] is not None]
    rollbacks = [(row["xact_rollback"], share) for row, share in parts
                 if row["xact_rollback"] is not None]
    deadlocks = [(row["deadlocks"], share) for row, share in parts if row["deadlocks"] is not None]
    return {"ts": bucket, "qps": calls / _seconds_in(bucket, step, now),
            "mean_ms": total / calls if calls else None,
            "active_conns": max(active) if active else None,
            "lock_waits": max((row["lock_waits"] for row, _ in parts if row.get("lock_waits") is not None), default=None),
            "max_conns": max(limits) if limits else None,
            "cache_hit_ratio": (hits / (hits + reads) if hits + reads else
                                sum(ratios) / len(ratios) if ratios else None),
            "xact_rollback": round(sum(v * s for v, s in rollbacks)) if rollbacks else None,
            "deadlocks": round(sum(v * s for v, s in deadlocks)) if deadlocks else None}


def p95_of_buckets(rows: Iterable[dict[str, Any]]) -> float | None:
    """The 95th percentile of the per-bucket mean latency: the latency a busy reading
    reaches. Readings carry no per-call distribution, so this is the tail RDST can see."""
    means = sorted(row["total_ms"] / row["calls"] for row in rows
                   if row.get("calls") and row.get("total_ms") is not None)
    if not means:
        return None
    return means[max(0, math.ceil(0.95 * len(means)) - 1)]


def _period(rows: list[dict[str, Any]], seconds: int,
            points: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    calls = sum(row["calls"] or 0 for row in rows)
    total = sum(row["total_ms"] or 0 for row in rows)
    active = [row["active_conns"] for row in rows if row["active_conns"] is not None]
    ratios = [point["cache_hit_ratio"] for point in (points or [])
              if point["cache_hit_ratio"] is not None]
    if points is None:
        ratios = [row["cache_hit_ratio"] for row in rows if row["cache_hit_ratio"] is not None]
    return {"qps": calls / max(60, seconds), "mean_ms": total / calls if calls else 0.0,
            "p95_ms": p95_of_buckets(rows),
            "db_time_ms": total, "active_conns_peak": max(active) if active else None,
            "lock_waits_peak": max((row["lock_waits"] for row in rows if row.get("lock_waits") is not None), default=None),
            "cache_hit_ratio_min": min(ratios) if ratios else None,
            # Whole counts, so every page rounds them the same way.
            "xact_rollback": round(sum(row["xact_rollback"] or 0 for row in rows)),
            "deadlocks": round(sum(row["deadlocks"] or 0 for row in rows))}


def peak_rate(store: MonitorStore, target: str, start: int, end: int,
              average: float | None) -> dict[str, Any] | None:
    """The busiest whole minute in [start, end) in queries a second: the one peak Home, the
    Database page and the report show. Past minute retention, a rollup's average stands in
    for its minutes. The peak never reads below the window's average rate."""
    rows = store.read("SELECT bucket_start ts,calls*1.0/res qps FROM db_series WHERE target=? "
                      "AND res<=3600 AND bucket_start>=? AND bucket_start+res<=? AND calls>0 "
                      "ORDER BY qps DESC,res,bucket_start LIMIT 1", (target, start, end))
    if not rows:
        return None
    return {"qps": max(float(rows[0]["qps"]), average or 0.0), "ts": int(rows[0]["ts"])}


def health(store: MonitorStore, target: str, range: str, now: int,
           window: tuple[int, int] | None = None) -> dict[str, Any]:
    history = history_span(store, target, now)
    start, seconds, res, now = bounds(range, now, history, window, chart=False)
    rows = db_rows(store, target, start, now + 1, res)
    buckets = spread(rows, res)
    points = [_health_point(bucket, buckets.get(bucket, []), res, now)
              for bucket in timeline(start, now, res, history, buckets)]
    observed = now - max(start, history["first_ts"] or now)
    prior = None
    if range != "all" and history["first_ts"] is not None and history["first_ts"] <= start - seconds:
        prior = _period(db_rows(store, target, start - seconds, start), seconds)
    live = store.read("SELECT * FROM db_series WHERE target=? AND res=60 "
                      "ORDER BY bucket_start DESC LIMIT 1", (target,))
    latest = live[0] if live else None
    now_tile = {"ts": latest["bucket_start"] if latest else now,
                "qps": (latest["calls"] / 60) if latest else 0.0,
                "mean_ms": (latest["total_ms"] / latest["calls"]
                            if latest and latest["calls"] else 0.0),
                "active_conns": latest["active_conns"] if latest else None,
                "lock_waits": latest["lock_waits"] if latest else None,
                "max_conns": latest["max_conns"] if latest else None,
                "cache_hit_ratio": latest["cache_hit_ratio"] if latest else None,
                "rollbacks_per_min": latest["xact_rollback"] if latest else None}
    items = store.read("SELECT id,title,severity,created_at,muted_by,delivery FROM inbox_item "
                       "WHERE target=? AND created_at>=? AND created_at<?",
                       (target, start, now + 1))
    markers = (_index_markers(store, target, start, now + 1, None)
               + _inbox_markers(items, start, now + 1)
               + [{"ts": row["finished_at"] or row["ts"], "kind": "report", "label": "Report written",
                   "report_id": row["id"]}
                  for row in store.read("SELECT id,ts,finished_at FROM report_run WHERE target=? "
                                        "AND state='done' AND sample=0 AND ts>=?",
                                        (target, start))])
    markers.sort(key=lambda marker: marker["ts"])
    period = _period(rows, observed, points)
    peak = peak_rate(store, target, start, now + 1, period["qps"])
    period.update(qps_peak=peak["qps"] if peak else None, qps_peak_at=peak["ts"] if peak else None)
    return {"target": target, "engine": _engine(target), "engine_version": None,
            "range": range, "res": res, "history": history, "now": now_tile,
            "period": period, "prior": prior, "series": points,
            "markers": markers, "detail_note": _minute_detail_note(rows, res)}


def _engine(target: str) -> str:
    try:
        from shared.config.targets import TargetsConfig

        config = TargetsConfig()
        config.load()
        engine = (config.get(target) or {}).get("engine", "postgresql")
    except Exception:
        engine = "postgresql"
    return "mysql" if str(engine).lower() == "mysql" else "postgresql"


# Registry lookups (text fallback, tags, compare outcomes)

def registry_entries(keys: list[str] | None = None) -> dict[str, Any]:
    if keys is not None:
        try:
            from shared.query_registry import QueryRegistry
            from shared.query_registry.query_registry import QueryEntry

            store = QueryRegistry().library_store
            return {key: QueryEntry.from_dict(value) for key, value in
                    store.load_keys(keys).items()} if store is not None else {}
        except Exception:
            logger.debug("Query registry page unavailable", exc_info=True)
            return {}
    now = time.monotonic()
    if now - _registry_cache["at"] < REGISTRY_TTL_SECONDS:
        return _registry_cache["entries"]
    try:
        from shared.query_registry import QueryRegistry

        registry = QueryRegistry()
        registry.load()
        entries = {entry.hash: entry for entry in registry.list_queries(limit=None)}
    except Exception:
        logger.debug("Query registry unavailable for monitor text", exc_info=True)
        entries = {}
    _registry_cache.update(at=now, entries=entries)
    return entries


def forget_registry() -> None:
    _registry_cache.update(at=0.0, entries={})


def _last_compare(entry: Any, target: str) -> dict[str, Any] | None:
    from datetime import datetime

    lifecycle = entry.lifecycle_for(target) if entry is not None else None
    outcome = getattr(lifecycle, "last_compare", None) or {}
    if not outcome.get("status"):
        return None
    try:
        ts = int(datetime.fromisoformat(str(outcome.get("at")).replace("Z", "+00:00")).timestamp())
    except ValueError:
        ts = 0
    readyset, origin = outcome.get("readyset_ms"), outcome.get("origin_ms")
    speedup = (origin / readyset if isinstance(readyset, (int, float)) and readyset > 0
               and isinstance(origin, (int, float)) else None)
    label = None
    if speedup:
        label = f"{speedup:.1f}x faster" if speedup >= 1 else f"{1 / speedup:.1f}x slower"
    return {"ts": ts, "verdict": str(outcome["status"]), "speedup": speedup,
            "speedup_label": label,
            "origin_ms": origin if isinstance(origin, (int, float)) else None,
            "readyset_ms": readyset if isinstance(readyset, (int, float)) else None}


TEXT_UNAVAILABLE = ("The database didn't report this statement's text. RDST asks again on "
                    "every collection.")


def query_texts(store: MonitorStore, target: str, keys: list[str] | None = None) -> tuple[dict[str, str], dict[str, Any]]:
    """Text per query key: monitor history, then the Query Library, then a live sample."""
    selected = " AND query_key IN (SELECT value FROM json_each(?))" if keys is not None else ""
    params = (target, *((json.dumps(keys),) if keys is not None else ()))
    texts = {row["query_key"]: row["text"] for row in store.read(
        "SELECT query_key,text FROM query_text WHERE target=?" + selected, params) if row["text"]}
    for row in store.read("SELECT s.query_key,s.text FROM query_sample s WHERE s.target=? "
                          "AND s.id=(SELECT MAX(id) FROM query_sample l WHERE l.target=s.target "
                          "AND l.query_key=s.query_key)" + selected, params):
        texts.setdefault(row["query_key"], row["text"])
    entries = registry_entries(keys)
    for key, entry in entries.items():
        if key not in texts and (entry.original_sql or entry.sql):
            texts[key] = entry.original_sql or entry.sql
    return texts, entries


# Per-query Jev assessments (the Query Library's quick rating)

def assessments(target: str, keys: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Latest quick assessment per query key for the target's current identity, one read."""
    try:
        from features.schema.assessment_context import target_identity
        from shared.config.targets import TargetsConfig
        from shared.query_registry import QueryRegistry

        config = TargetsConfig()
        config.load()
        target_config = config.get(target)
        store = QueryRegistry().library_store
        if not isinstance(target_config, dict) or store is None:
            return {}
        rows = (store.assessments_for_target(target, target_identity(dict(target_config)), keys=keys)
                if keys is not None else store.assessments_for_target(
                    target, target_identity(dict(target_config))))
    except Exception:
        logger.debug("Quick assessments unavailable for %s", target, exc_info=True)
        return {}
    result = {}
    for key, row in rows.items():
        findings = (row.get("result") or {}).get("findings") if isinstance(
            row.get("result"), dict) else []
        result[key] = {
            "status": row["status"], "band": row["band"] or None,
            "priority_score": row["priority_score"], "confidence": row["confidence"],
            "findings": [{"id": str(item.get("id", "")), "label": str(item.get("label", "")),
                          "verdict": str(item.get("verdict", "")),
                          "confidence": item.get("confidence")}
                         for item in findings or [] if isinstance(item, dict)],
            "model": row["model"] or None, "rubric_version": row["rubric_version"] or None,
            "assessed_at": row["assessed_at"] or None, "error_code": row["error_code"] or None,
            "waiting_reason": ASSESSMENT_WAITING.get(row["status"]),
            "schema_coverage": row.get("schema_coverage") or None,
            "schema_collected_at": row.get("schema_collected_at") or None}
    return result


# What an assessment that isn't complete is waiting for, as the row says it.
ASSESSMENT_WAITING = {
    "pending": "Queued. RDST assesses new queries in the background within a few minutes.",
    "running": "RDST is classifying it now.",
    "retry_wait": "The last attempt failed. RDST retries shortly.",
    "waiting_auth": "Waiting for sign-in. Sign in to RDST to resume assessments.",
    "waiting_connection": "Waiting to read the schema. RDST retries when it can connect.",
    "waiting_schema": "Waiting to read the schema. RDST retries when it can connect.",
    "paused": "Paused after an unclear answer. It resumes on the next change.",
    "unsupported": "RDST can't classify this statement.",
}


# 5.3 Queries

def _state(key: str, items: list[dict[str, Any]], latency_change: float | None,
           significant: bool, stuck: bool, muted: bool, new: bool) -> list[str]:
    types = {item["type"] for item in items}
    state = []
    if types & ATTENTION_TYPES or (significant and (latency_change or 0) >= SIGNIFICANT_CHANGE):
        state.append("attention")
    if new or "new_heavy_query" in types:
        state.append("new")
    if stuck or "stuck_query" in types:
        state.append("stuck")
    if muted:
        state.append("muted")
    return state


def _query_page(store: MonitorStore, target: str, start: int, end: int, seconds: int,
                prior_covered: bool, sort: str, filter: str, q: str, limit: int,
                offset: int, only: str | None, keys: list[str] | None = None,
                locate: str | None = None, active_order: list[str] | None = None
                ) -> tuple[list[str], int, dict, list, float, dict, int | None]:
    current_sql, cp = _complete("stmt_series", _STMT_FIELDS, store, target, start, end, None, None)
    prior_sql, pp = _complete("stmt_series", _STMT_FIELDS, store, target,
                              start - seconds, start, None, None)
    sql = ("WITH current AS (SELECT query_key,SUM(calls) calls,SUM(total_ms) ms FROM (" +
           current_sql + ") GROUP BY query_key),prior AS (SELECT query_key,SUM(calls) calls,"
           "SUM(total_ms) ms FROM (" + prior_sql + ") GROUP BY query_key),"
           "catalog AS (SELECT query_key FROM query_text WHERE target=? UNION "
           "SELECT query_key FROM current UNION SELECT query_key FROM stuck_sample "
           "WHERE target=? AND ts>=?),"
           "facts AS (SELECT k.query_key,COALESCE(c.calls,0) calls,COALESCE(c.ms,0) ms,"
           # Impact ranks by live calls at a cost per call steadied by the engine's long-run
           # average (features.monitor.ranking.blended_ms); a query not running ranks last.
           "CASE WHEN COALESCE(c.calls,0)>0 THEN c.calls*(c.ms+?*COALESCE("
           "pr.total_ms/NULLIF(pr.calls,0),c.ms/c.calls))/(c.calls+?) ELSE 0 END impact,"
           "COALESCE(c.ms/NULLIF(c.calls,0),0) mean,"
           "c.ms/NULLIF(c.calls,0)/(p.ms/NULLIF(p.calls,0))-1 latency,"
           "(? AND c.calls>=30 AND p.calls>=30 AND "
           "ABS(c.ms/c.calls/(p.ms/p.calls)-1)>=0.2) significant,"
           "(? AND c.calls>0 AND COALESCE(p.calls,0)=0) fresh,"
           "ui.efficiency_score score,COALESCE(ui.fix_ready,0) fix_ready,"
           "COALESCE(ui.analyzed,0) analyzed,COALESCE(ui.assessed,0) assessed,COALESCE(ui.findings,0) findings,"
           "EXISTS (SELECT 1 FROM inbox_item i WHERE i.target=t.target AND i.subject=k.query_key "
           "AND i.state IN ('new','seen') AND i.delivery='digest') watch,"
           "EXISTS (SELECT 1 FROM inbox_item i WHERE i.target=? AND i.subject=k.query_key "
           "AND i.state NOT IN ('resolved','dismissed') AND i.delivery!='digest') attention,"
           "EXISTS (SELECT 1 FROM mute_rule m WHERE m.query_key=k.query_key "
           "AND (m.target IS NULL OR m.target=?) AND (m.until IS NULL OR m.until>?)) muted "
           "FROM catalog k LEFT JOIN current c USING(query_key) LEFT JOIN prior p USING(query_key) "
           "LEFT JOIN query_prior pr ON pr.target=? AND pr.query_key=k.query_key "
           "LEFT JOIN query_text t ON t.target=? AND t.query_key=k.query_key "
           "LEFT JOIN query_ui ui ON ui.target=t.target AND ui.query_key=k.query_key "
           "WHERE k.query_key!=? AND COALESCE(ui.workload,1)=1 "
           "AND NOT EXISTS (SELECT 1 FROM system_query x "
           "WHERE x.target=? AND x.query_key=k.query_key)),"
           "selected AS (SELECT * FROM facts WHERE " )
    conditions = ["1"]
    parameters = (*cp, *pp, target, target, start, PRIOR_CALLS, PRIOR_CALLS,
                  int(prior_covered), int(prior_covered),
                  target, target, end, target, target, EVERYTHING_ELSE, target)
    if only is not None:
        conditions.append("query_key=?")
        parameters += (only,)
    if keys is not None:
        conditions.append("query_key IN (SELECT value FROM json_each(?))")
        parameters += (json.dumps(keys),)
    for word in q.split():
        if word.lower() in {"on", "by"}:
            continue
        conditions.append("(instr(lower(COALESCE((SELECT text FROM query_text t "
                          "WHERE t.target=? AND t.query_key=facts.query_key),'')),lower(?))>0 "
                          "OR instr(lower(query_key),lower(?))>0)")
        parameters += (target, word, word)
    if filter == "changed":
        conditions.append("ABS(latency)>=0.2")
    elif filter == "attention":
        conditions.append("(attention OR (significant AND latency>=0.2))")
    elif filter == "new":
        conditions.append("fresh")
    elif filter == "fixed":
        conditions.append("0")
    elif filter == "watch":
        conditions.append("watch AND NOT attention")
    elif filter == "fix_ready":
        conditions.append("fix_ready AND NOT attention AND NOT watch")
    elif filter == "healthy":
        conditions.append("analyzed AND NOT fix_ready AND NOT attention AND NOT watch")
    elif filter == "not_analyzed":
        conditions.append("NOT analyzed AND NOT attention AND NOT watch")
    elif filter == "analyzed":
        conditions.append("analyzed")
    elif filter == "unassessed":
        conditions.append("NOT assessed")
    elif filter == "none":
        conditions.append("assessed AND findings=0")
    elif filter in {"access_expression_risk", "index_coverage", "join_growth", "repeated_work", "broad_work"}:
        from features.query_registry.assessment_rubric import FINDING_BITS

        conditions.append(f"assessed AND (findings & {1 << FINDING_BITS.index(filter)})!=0")
    sql += " AND ".join(conditions) + ") "
    counts = store.read(sql + "SELECT COUNT(*) 'all',SUM(attention OR (significant AND latency>=0.2)) "
                        "attention,SUM(fresh) new,0 fixed,SUM(muted) muted,SUM(ms)+COALESCE("
                        "(SELECT ms FROM current WHERE query_key=?),0) db_ms,"
                        "(SELECT COUNT(*) FROM selected) total FROM facts", (*parameters, EVERYTHING_ELSE))[0]
    metric = {"qps": "calls", "mean_ms": "mean", "db_time_share": "impact", "change": "ABS(latency)", "score_low": "-score", "score_high": "score"}[sort]
    order = f"{metric} DESC,ms DESC,query_key"
    order_params = parameters
    if active_order:
        order = ("COALESCE((SELECT CAST(key AS INTEGER) FROM json_each(?) "
                 "WHERE value=selected.query_key),2147483647)," + order)
        order_params += (json.dumps(active_order),)
    page = store.read(sql + f"SELECT query_key FROM selected ORDER BY {order} "
                      "LIMIT ? OFFSET ?", (*order_params, limit, offset))
    located = None
    if locate is not None:
        found = store.read(sql + "SELECT n FROM (SELECT query_key,ROW_NUMBER() OVER ("
                           f"ORDER BY {order})-1 n FROM selected) "
                           "WHERE query_key=?", (*order_params, locate))
        located = found[0]["n"] if found else None
    leaders = store.read(sql + "SELECT query_key,ms FROM facts ORDER BY ms DESC,query_key LIMIT 8", parameters)
    db_ms = counts.pop("db_ms") or 0.0
    total = counts.pop("total")
    counts = {key: value or 0 for key, value in counts.items()}
    composition = [{"key": row["query_key"], "db_time_ms": row["ms"],
                    "share": row["ms"] / db_ms if db_ms else 0.0} for row in leaders[:6] if row["ms"]]
    rest = db_ms - sum(row["db_time_ms"] for row in composition)
    if rest > 0:
        composition.append({"key": "other", "db_time_ms": rest, "share": rest / db_ms})
    colors = {row["query_key"]: i for i, row in enumerate(leaders) if row["ms"]}
    return [row["query_key"] for row in page], total, counts, composition, db_ms, colors, located


def queries(store: MonitorStore, target: str, range: str, now: int, *, sort: str,
            filter: str, q: str, limit: int, offset: int,
            window: tuple[int, int] | None = None, only: str | None = None,
            keys: list[str] | None = None, locate: str | None = None) -> dict[str, Any]:
    """The query list; `window` re-scopes it to a brushed [from, to], `only` keeps one key,
    and `locate` reports where one key sits in the whole filtered, sorted list."""
    from features.monitor.api.routes import _tables
    from features.monitor.analyze_queue import (analysis_retry, current_phase, queue_plan,
                                                retry_plan, waiting_reason)

    history = history_span(store, target, now)
    start, seconds, _, now = bounds(range, now, history, window, chart=False)
    end = now + 1
    step = mini_step(seconds)
    # Rates cover the recorded part of the range: an hour's range with two minutes of
    # history is two minutes of calls.
    recorded = seconds
    if window is None and history["first_ts"] is not None and history["first_ts"] > start:
        recorded = max(60, now - history["first_ts"])
    compare = window is not None or range != "all"
    prior_covered = (compare and history["first_ts"] is not None
                     and history["first_ts"] <= start - seconds)
    plan = queue_plan(store, target, now)
    # The queue analyzes by the same impact the list sorts by, so its rows sit where they rank.
    active_order = None
    keys, total, counts, composition, db_ms, colors, located = _query_page(
        store, target, start, end, seconds, prior_covered, sort, filter, q, limit, offset, only,
        keys, locate, active_order)
    mini_rows = stmt_rows(store, target, start, end, keys, step=step) if keys else []
    current = totals_by_key(mini_rows)
    prior = stmt_totals(store, target, start - seconds, start, keys) if compare and keys else {}
    stuck_now = {row["query_key"] for row in store.read(
        "SELECT DISTINCT query_key FROM stuck_sample WHERE target=? AND ts>=? "
        "AND query_key IN (SELECT value FROM json_each(?))", (target, start, json.dumps(keys)))}
    texts, entries = query_texts(store, target, keys)
    allowed = workload_keys(store, target, keys, texts)
    keys = [key for key in keys if key in allowed]
    names = target_names(store, target, texts)
    bases = {key: display_name(texts[key]) if texts.get(key) else f"Query {key}" for key in keys}
    family_sizes: dict[str, int] = defaultdict(int)
    for base in bases.values():
        family_sizes[base] += 1
    execute = bool(store.levers(target)["execute"])
    first_seen = {row["query_key"]: row["first_seen"] for row in store.read(
        "SELECT query_key,first_seen FROM query_text WHERE target=? "
        "AND query_key IN (SELECT value FROM json_each(?))", (target, json.dumps(keys)))}
    open_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in store.read("SELECT id,type,subject,body_json,muted_by FROM inbox_item "
                           "WHERE target=? AND state NOT IN ('dismissed','resolved') "
                           "AND delivery!='digest'", (target,)):
        for key in item_query_keys(item["subject"], json.loads(item["body_json"])):
            open_items[key].append(item)
    rules = store.read("SELECT query_key FROM mute_rule WHERE query_key IS NOT NULL "
                       "AND (target IS NULL OR target=?) AND (until IS NULL OR until>?)",
                       (target, now))
    muted_keys = {row["query_key"] for row in rules}
    jobs = {row["query_key"]: row for row in store.read(
        "SELECT j.* FROM analyze_job j WHERE j.target=? "
        "AND j.query_key IN (SELECT value FROM json_each(?)) AND j.id=("
        "SELECT MAX(id) FROM analyze_job WHERE target=j.target AND query_key=j.query_key "
        "AND NOT (state='skipped' AND error='cancelled'))",
        (target, json.dumps(keys)))}
    done = {row["query_key"]: row for row in store.read(
        "SELECT j.* FROM analyze_job j WHERE j.target=? AND j.id=(SELECT MAX(id) FROM analyze_job "
        "WHERE target=j.target AND query_key=j.query_key AND state='done' "
        "AND analysis_ref IS NOT NULL) AND j.query_key IN (SELECT value FROM json_each(?))",
        (target, json.dumps(keys)))}
    saved = library_analysis_jobs(target, [key for key in keys if key not in done])
    for key, job in saved.items():
        done.setdefault(key, job)
        jobs.setdefault(key, job)
    retries = retry_plan(store, target, now)
    assessed = assessments(target, keys)
    mini = spread(mini_rows, step)
    covered = set(spread(db_rows(store, target, start, end, step), step))
    per_key: dict[str, dict[int, list[tuple[dict, float]]]] = defaultdict(dict)
    for bucket, parts in mini.items():
        for row, share in parts:
            per_key[row["query_key"]].setdefault(bucket, []).append((row, share))
    buckets = timeline(start, now, step, history, mini)
    day_totals = stmt_totals(store, target, now - 86400, end, keys) if keys else {}
    before_totals = stmt_totals(store, target, now - 2 * 86400, now - 86400, keys) if keys else {}
    trends_24h = hourly_means(store, target, keys, now) if keys else {}
    engine = _engine(target)
    rows = []
    store_full = store.full_detail_keys(target)
    priors = store.priors(target)
    for key in keys:
        totals = current.get(key, {"calls": 0, "total_ms": 0.0})
        old = prior.get(key, {"calls": 0, "total_ms": 0.0})
        calls, total_ms = totals["calls"], totals["total_ms"]
        mean = total_ms / calls if calls else 0.0
        old_mean = old["total_ms"] / old["calls"] if old["calls"] else 0.0
        latency = mean / old_mean - 1 if old_mean and calls else None
        qps_change = calls / old["calls"] - 1 if old["calls"] else None
        significant = bool(prior_covered and latency is not None and calls >= SIGNIFICANT_CALLS
                           and old["calls"] >= SIGNIFICANT_CALLS
                           and abs(latency) >= SIGNIFICANT_CHANGE)
        is_new = bool(prior_covered and not old["calls"] and calls)
        verdict = None
        if prior_covered:
            verdict = ("new" if is_new else "regressed" if significant and latency > 0
                       else "improved" if significant else "steady")
        text = texts.get(key, "")
        entry = entries.get(key)
        job = jobs.get(key)
        queue_fact = plan["jobs"].get(job["id"], {}) if job and job["state"] == "queued" else {}
        items = open_items.get(key, [])
        finished = done.get(key)
        digest = (digest_for_ref(target, key, finished["analysis_ref"], finished.get("mode"),
                                 finished.get("finished_at"))
                  if finished and finished.get("analysis_ref") else None)
        ready_fix = digest["fix"] if digest and digest.get("fix_ready") and digest.get("fix") \
            else None
        today_row, before_row = day_totals.get(key) or {}, before_totals.get(key) or {}
        today_mean = (today_row["total_ms"] / today_row["calls"]
                      if today_row.get("calls") else None)
        before_mean = (before_row["total_ms"] / before_row["calls"]
                       if before_row.get("calls") else None)
        series = []
        for bucket in buckets:
            parts = per_key[key].get(bucket, [])
            point_calls = sum(row["calls"] * share for row, share in parts)
            point_ms = sum(row["total_ms"] * share for row, share in parts)
            gap = not parts and (bucket not in covered or key not in store_full)
            series.append({"ts": bucket,
                           "qps": None if gap else point_calls / _seconds_in(bucket, step, now),
                           "mean_ms": point_ms / point_calls if point_calls else None})
        rows.append({
            "key": key, "text": text,
            "text_status": ("unavailable" if not text else
                            "truncated" if len(text.encode("utf-8")) >= 8190 else "ok"),
            "text_reason": None if text else TEXT_UNAVAILABLE,
            "display_name": names.get(key) or (display_name(text) if text else f"Query {key}"),
            "family": ({"name": bases[key], "size": family_sizes[bases[key]]}
                       if key in bases and family_sizes[bases[key]] > 1 else None),
            **analyzability(text, mean_ms=mean, execute=execute),
            "color_index": colors.get(key),
            "tables": _tables(text), "qps": calls / recorded, "mean_ms": mean,
            "db_time_ms": total_ms, "db_time_share": total_ms / db_ms if db_ms else 0.0,
            "impact_ms": blended_ms(calls, total_ms, priors.get(key)),
            "change": {"latency": latency, "qps": qps_change, "significant": significant,
                       "verdict": verdict},
            "series": series,
            "state": _state(key, items, latency, significant, key in stuck_now,
                            key in muted_keys or any(item["muted_by"] for item in items),
                            is_new),
            "fix": None,
            "last_analysis": ({"state": job["state"], "finished_at": job["finished_at"],
                               "analysis_ref": job["analysis_ref"], "mode": job["mode"] or "",
                               "mode_label": {"explain": "Plan only (EXPLAIN)",
                                              "analyze": "Ran the query (EXPLAIN ANALYZE)"
                                              }.get(job["mode"] or ""),
                               "param_source": _param_source(job["param_source"], text),
                               "reason": (waiting_reason(plan, job["id"])
                                          if job["state"] == "queued"
                                          else analysis_reason(job["state"], job["error"])),
                               "error_kind": analysis_error_kind(job["state"], job["error"]),
                               "queue_state": ("running" if job["state"] == "running"
                                               else queue_fact.get("queue_state")),
                               "held_reason": queue_fact.get("held_reason"),
                               "queue_position": queue_fact.get("queue_position"),
                               "phase": ((current_phase(target, key) or "plan")
                                         if job["state"] == "running" else None),
                               # The latest finished analysis, even if a later run failed.
                               "summary": summary_for_job(done.get(key)),
                               **analysis_retry(job, retries.get(key))}
                              if job else None),
            "last_compare": _last_compare(entry, target),
            "assessment": assessed.get(key),
            # Pre-contract fields read by the classic Database page.
            "sparkline": [{"ts": point["ts"], "qps": point["qps"] or 0.0} for point in series],
            "change_vs_prior": {"latency": latency, "qps": qps_change},
            "badges": [],
            "plan_score": (digest or {}).get("efficiency_score"),
            "latency_delta_24h": (today_mean / before_mean - 1
                                  if today_mean is not None and before_mean else None),
            "trend_24h": trends_24h.get(key, []),
            "suggested_fix": ({"sql": concurrent_index_sql(ready_fix["sql"], engine) or "",
                               "label": fix_label(ready_fix)}
                              if ready_fix and ready_fix.get("sql") else None),
            "status": query_status(key in stuck_now, job, digest, ready_fix),
            "_first_seen": first_seen.get(key),
            "_attention": bool(items) or (latency or 0) >= SIGNIFICANT_CHANGE,
        })
    page = [{field: value for field, value in row.items() if not field.startswith("_")}
            for row in rows]
    label = "custom" if window else range
    seen_total = store.read(
        "SELECT COUNT(*) n FROM query_text t WHERE t.target=? AND NOT EXISTS (SELECT 1 FROM "
        "system_query x WHERE x.target=t.target AND x.query_key=t.query_key) AND COALESCE("
        "(SELECT workload FROM query_ui u WHERE u.target=t.target AND u.query_key=t.query_key),1)=1",
        (target,))[0]["n"]
    return {"target": target, "range": label, "window": label, "queries": page,
            "total": total, "seen_total": int(seen_total), "limit": limit, "offset": offset,
            "counts": counts,
            "composition": composition, "history": history,
            **({"located": located} if locate is not None else {})}


# Load breakdown (DESIGN_V4 section 2)

def recorded_label(span_seconds: int) -> str:
    if span_seconds <= 0:
        return "No readings yet"
    if span_seconds < 3600:
        minutes = max(1, round(span_seconds / 60))
        return f"{minutes} minute{'s' if minutes != 1 else ''} recorded"
    if span_seconds < 48 * 3600:
        hours = round(span_seconds / 3600, 1)
        return f"{hours:g} hour{'s' if hours != 1 else ''} recorded"
    return f"{span_seconds / 86400:.1f} days recorded"


def load_bounds(range: str, now: int, history: dict[str, Any]) -> tuple[int, int, int]:
    start, seconds, res = range_bounds(range, now, history)
    return start, seconds, res if range == "all" else LOAD_RES[range]


WINDOW_MAX_POINTS = 800


def window_res(seconds: int, start: int, now: int, *, max_points: int = WINDOW_MAX_POINTS) -> int:
    """Use at most 800 points, with minute detail throughout incident retention."""
    for res in RESOLUTIONS:
        if start < (now - 30 * 86400 if res == 60 else fine_from(res, now)):
            continue
        if seconds / res <= max_points:
            return res
    return RESOLUTIONS[-1]


def bounds(range: str, now: int, history: dict[str, Any], window: tuple[int, int] | None,
           *, chart: bool) -> tuple[int, int, int, int]:
    """(start, seconds, res, end) for a named range or an explicit [from, to] window.

    `end` is the window's last second; readers treat it as "now" for the window.
    """
    if window is not None:
        start, end = window
        seconds = max(60, end - start)
        return start, seconds, window_res(seconds, start, now), end
    start, seconds, res = load_bounds(range, now, history) if chart else \
        range_bounds(range, now, history)
    return start, seconds, res, now


def range_availability(history: dict[str, Any], now: int) -> list[dict[str, Any]]:
    """A range is on when history is longer than the next shorter range, so it shows more."""
    span = history["span_seconds"] if history["first_ts"] is not None else 0
    label = recorded_label(span)
    result, previous = [], 0
    for name in RANGES:
        if name == "all":
            enabled = span > RANGE_SECONDS["1h"]
        else:
            enabled = span > previous
            previous = RANGE_SECONDS[name]
        result.append({"range": name, "enabled": enabled,
                       "res": load_bounds(name, now, history)[2],
                       "reason": None if enabled else label})
    return result


def workload_keys(store: MonitorStore, target: str, keys: Iterable[str],
                  texts: dict[str, str] | None = None) -> set[str]:
    """Keys that are the application's statements: not RDST's own, not catalog reads."""
    from shared.monitor.statements import is_workload

    system = store.system_queries(target)
    texts = texts if texts is not None else query_texts(store, target)[0]
    return {key for key in keys if key != EVERYTHING_ELSE and key not in system and is_workload(texts.get(key, ""))}


def top_keys(totals: dict[str, dict[str, Any]], keys: Iterable[str], count: int,
             by: str = "total_ms") -> list[str]:
    """The load ranking shared by the chart bands, row swatches and the report: by recorded
    execution time, or by calls for the calls chart."""
    ranked = sorted((key for key in keys if (totals.get(key) or {}).get(by)),
                    key=lambda key: (-totals[key][by], key))
    return ranked[:count]


def load(store: MonitorStore, target: str, range: str, now: int,
         top: int = LOAD_TOP, window: tuple[int, int] | None = None,
         rank: str = "time") -> dict[str, Any]:
    """The bands rank by execution time, or by calls when `rank` is "calls"; a band keeps the
    color its row has in the time ranking, so the swatches agree across the page."""
    by = "calls" if rank == "calls" else "total_ms"
    history = history_span(store, target, now)
    ranges = range_availability(history, now)
    start, seconds, res, now = bounds(range, now, history, window, chart=True)
    end = now + 1
    totals = stmt_totals(store, target, start, end)
    excluded = store.system_queries(target) | {row["query_key"] for row in store.read(
        "SELECT query_key FROM query_ui WHERE target=? AND workload=0", (target,))}
    user = set(totals) - excluded - {EVERYTHING_ELSE}
    candidates = list(dict.fromkeys(top_keys(totals, user, top * 4) +
                                    top_keys(totals, user, top * 4, by)))
    texts = query_texts(store, target, candidates)[0]
    allowed = workload_keys(store, target, candidates, texts)
    user.difference_update(set(candidates) - allowed)
    by_time = top_keys(totals, user, top)
    leaders = top_keys(totals, user, top, by)
    colors = {key: index for index, key in enumerate(by_time)}
    taken = {colors[key] for key in leaders if key in colors}
    free = iter(index for index, _ in enumerate(by_time) if index not in taken)
    colors.update({key: next(free) for key in leaders if key not in colors})
    all_ms = sum(totals[key]["total_ms"] for key in user)
    all_calls = sum(totals[key]["calls"] for key in user)
    band_index = {key: index for index, key in enumerate(leaders)}
    rest = user - set(leaders)
    if EVERYTHING_ELSE in totals:
        rest.add(EVERYTHING_ELSE)
        all_ms += totals[EVERYTHING_ELSE]["total_ms"]
        all_calls += totals[EVERYTHING_ELSE]["calls"]
    rest_ms = sum(totals[key]["total_ms"] for key in rest)
    rest_calls = sum(totals[key]["calls"] for key in rest)
    names = target_names(store, target, texts)
    names.update({key: f"Query {key}" for key in leaders if key not in names})
    bands = [{"key": key, "kind": "query", "display_name": names[key],
              "color_index": colors[key],
              "share": totals[key]["total_ms"] / all_ms if all_ms else 0.0,
              "total_ms": totals[key]["total_ms"], "calls": totals[key]["calls"],
              "mean_ms": (totals[key]["total_ms"] / totals[key]["calls"]
                          if totals[key]["calls"] else None)}
             for key in leaders]
    other = bool(rest_ms or rest_calls)
    if other:
        bands.append({"key": "other", "kind": "other", "display_name": "Everything else",
                      "color_index": None, "share": rest_ms / all_ms if all_ms else 0.0,
                      "total_ms": rest_ms, "calls": rest_calls,
                      "mean_ms": rest_ms / rest_calls if rest_calls else None})
    width = len(bands)
    rows = band_rows(store, target, start, end, res, leaders, rest)
    buckets = spread(rows, res)
    covered = set(spread(db_rows(store, target, start, end, res), res))
    points = []
    for bucket in timeline(start, now, res, history, buckets):
        parts = buckets.get(bucket, [])
        if not parts and bucket not in covered:
            points.append({"ts": bucket, "seconds": _seconds_in(bucket, res, now),
                           "total_ms": None, "calls": None, "mean_ms": None})
            continue
        ms, calls = [0.0] * width, [0.0] * width
        for row, share in parts:
            index = band_index.get(row["query_key"], width - 1 if other else None)
            if index is None:
                continue
            ms[index] += row["total_ms"] * share
            calls[index] += row["calls"] * share
        total_calls = sum(calls)
        points.append({"ts": bucket, "seconds": _seconds_in(bucket, res, now),
                       "total_ms": ms, "calls": calls,
                       "mean_ms": sum(ms) / total_calls if total_calls else None})
    return {"target": target, "range": "custom" if window else range, "res": res,
            "start": max(start, history["first_ts"] or start), "end": now, "history": history,
            "recorded_label": recorded_label(
                history["span_seconds"] if history["first_ts"] is not None else 0),
            "ranges": ranges, "bands": bands, "points": points,
            "total_ms": all_ms, "calls": all_calls, "detail_note": _minute_detail_note(rows, res)}


def load_latency(store: MonitorStore, target: str, range: str, now: int,
                 keys: list[str], window: tuple[int, int] | None = None) -> dict[str, Any]:
    history = history_span(store, target, now)
    start, _, res, now = bounds(range, now, history, window, chart=True)
    end = now + 1
    wanted = list(dict.fromkeys(keys))
    buckets = spread(stmt_rows(store, target, start, end, wanted, step=res, chart=True), res) if wanted else {}
    everything = spread(db_rows(store, target, start, end, res), res)
    index = {key: position for position, key in enumerate(wanted)}
    points = []
    for bucket in timeline(start, now, res, history, buckets):
        ms, calls = [0.0] * len(wanted), [0.0] * len(wanted)
        for row, share in buckets.get(bucket, []):
            ms[index[row["query_key"]]] += row["total_ms"] * share
            calls[index[row["query_key"]]] += row["calls"] * share
        overall = everything.get(bucket, [])
        all_calls = sum((row["calls"] or 0) * share for row, share in overall)
        all_ms = sum((row["total_ms"] or 0) * share for row, share in overall)
        points.append({"ts": bucket,
                       "mean_ms": [value / count if count else None
                                   for value, count in zip(ms, calls)],
                       "overall_mean_ms": all_ms / all_calls if all_calls else None})
    return {"target": target, "range": "custom" if window else range, "res": res,
            "keys": wanted, "points": points}


# 5.4 Query series

def series(store: MonitorStore, target: str, key: str, range: str, now: int,
           window: tuple[int, int] | None = None) -> dict[str, Any]:
    from features.monitor.api.routes import _tables

    reference = now
    history = history_span(store, target, now)
    start, seconds, res, now = bounds(range, now, history, window, chart=False)
    if window is not None:
        res = window_res(seconds, start, reference, max_points=RANGE_SECONDS["24h"] // 60 + 1)
    end = now + 1
    rows = stmt_rows(store, target, start, end, key, step=res, chart=True)
    buckets = spread(rows, res)
    covered = set(spread(db_rows(store, target, start, end, res), res))
    full_detail = key in store.full_detail_keys(target)
    points = []
    for bucket in timeline(start, now, res, history, buckets):
        parts = buckets.get(bucket, [])
        if not parts and (bucket not in covered or not full_detail):
            points.append({"ts": bucket, "res": res, "calls": None, "qps": None,
                           "mean_ms": None, "total_ms": None, "temp_blks": None})
            continue
        calls = sum(row["calls"] * share for row, share in parts)
        total = sum(row["total_ms"] * share for row, share in parts)
        points.append({"ts": bucket, "res": res, "calls": calls,
                       "qps": calls / _seconds_in(bucket, res, now),
                       "mean_ms": total / calls if calls else None, "total_ms": total,
                       "temp_blks": sum(row.get("temp_blks", 0) * share for row, share in parts)})
    text = query_texts(store, target, [key])[0].get(key, "")
    tables = {table.lower() for table in _tables(text)} or None
    items = [item for item in store.read(
        "SELECT id,type,subject,title,severity,created_at,body_json,muted_by,delivery "
        "FROM inbox_item "
        "WHERE target=? AND created_at>=?", (target, start))
        if key in item_query_keys(item["subject"], json.loads(item["body_json"]))]
    markers = (_index_markers(store, target, start, end, tables)
               + [{"ts": job["finished_at"], "kind": "analysis",
                   "label": "Analyzed by running the query (EXPLAIN ANALYZE)"
                   if job["mode"] == "analyze" else "Analyzed from the plan only (EXPLAIN)", "analysis_ref": job["analysis_ref"]}
                  for job in store.read("SELECT finished_at,mode,analysis_ref FROM analyze_job "
                                        "WHERE target=? AND query_key=? AND state='done' "
                                        "AND finished_at>=?", (target, key, start))]
               + _inbox_markers(items, start, end))
    markers.sort(key=lambda marker: marker["ts"])
    return {"target": target, "key": key, "range": range, "resolution": res, "res": res,
            "history": history, "points": points, "markers": markers,
            "fix": None, "detail_note": (_minute_detail_note(rows, res) if full_detail else
                "Detail is kept for the top 1,000 queries by load")}


# 5.7 Inbox

def inbox_items(store: MonitorStore, rows: list[dict[str, Any]], *,
                incidents: bool = False) -> list[dict[str, Any]]:
    """Add presentation fields; one trend read per target, not per item."""
    now = int(store.clock())
    rules = store.read("SELECT * FROM mute_rule WHERE until IS NULL OR until>? ORDER BY id", (now,))
    live_rules = {rule["id"] for rule in rules}
    rows = [{**row, "muted_by": next((rule["id"] for rule in rules if rule_matches(
        rule, row["target"], row["type"], item_query_keys(row["subject"], _body(row)), now)), None)}
        for row in rows]
    by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_target[row["target"]].append(row)
    trends: dict[int, list[dict[str, Any]]] = {}
    names: dict[str, dict[str, str]] = {}
    texts: dict[str, dict[str, str]] = {}
    engines: dict[str, str] = {}
    excluded: dict[str, set[str]] = {}
    stats: dict[int, dict[str, dict[str, Any]]] = {}
    for target, items in by_target.items():
        # Monitor history first, then the Query Library, like the query list.
        keys = list(dict.fromkeys(key for item in items for key in
                    item_query_keys(item["subject"], _body(item))))
        texts[target] = query_texts(store, target, keys)[0]
        names[target] = target_names(store, target, texts[target])
        trends.update(_trends(store, target, items))
        engines[target] = _engine(target)
        groups = [item for item in items if item["type"] == "many_queries_changed"]
        if groups:
            system = store.system_queries(target)
            keys = {key for item in groups for key in item_query_keys(
                item["subject"], _body(item))}
            excluded[target] = {key for key in keys if key in system or
                                not alert_member(texts[target].get(key, ""))}
            for item in groups:
                stats[item["id"]] = member_stats(store, target, item)
    notified = store.notified_at() if rows else {}
    presented = [{**present_inbox_item(row, names[row["target"]], trends.get(row["id"], []), live_rules,
                                       engine=engines[row["target"]],
                                       texts=texts[row["target"]],
                                       excluded=excluded.get(row["target"]),
                                       member_stats=stats.get(row["id"])),
                  "notified_at": notified.get(row["id"])}
                 for row in rows]
    events = store.item_events([row["id"] for row in rows])
    presented = [{**item, **item_view(item, _body(item), names[item["target"]],
                                      item["query_keys"], events.get(item["id"], []),
                                      engines[item["target"]])}
                 for item in presented]
    # Open items are always measured, so every view reads the same status and band; closed
    # ones only when the caller shows their windows.
    windows: dict[int, dict[str, Any] | None] = {}
    for target in by_target:
        measure = [item for item in presented if item["target"] == target and (
            incidents or item["state"] not in ("resolved", "dismissed"))]
        if measure:
            windows.update(incident_windows(store, target, measure, stats))
    settled = [settle_status(item, _body(item), windows.get(item["id"])) for item in presented]
    fixes = {target: store.actions(target, ("open",)) for target in by_target}
    return [{**(item if incidents else {**item, "incident": None}),
             "action": likely_fix(item, fixes[item["target"]])} for item in settled]


def likely_fix(item: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The best-scored open action covering one of an open item's queries; for a
    database-wide item, the action its subject maps to (presentation.LIKELY_FIX)."""
    if item["state"] in ("resolved", "dismissed"):
        return None
    keys = set(item.get("query_keys") or [])
    for action in actions if keys else []:
        payload = action["payload"]
        if payload.get("title") and keys.intersection(payload.get("queries") or []):
            return {"id": action["id"], "title": payload["title"], "type": action["type"]}
    return database_fix(str(item.get("type") or ""), _body(item) if "body_json" in item else {},
                        actions)


def _body(item: dict[str, Any]) -> dict[str, Any]:
    body = item["body_json"]
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return {}
    return body if isinstance(body, dict) else {}


MEMBER_AFTER_S = 900
MEMBER_BEFORE_S = 3600


def member_stats(store: MonitorStore, target: str,
                 item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Each group member's latency and calls in the 15 minutes before filing and the hour
    before that, measured from recorded history."""
    keys = item_query_keys(item["subject"], _body(item))
    if not keys:
        return {}
    filed = item["created_at"]
    after = stmt_totals(store, target, filed - MEMBER_AFTER_S, filed + 60, keys)
    before = stmt_totals(store, target, filed - MEMBER_AFTER_S - MEMBER_BEFORE_S,
                         filed - MEMBER_AFTER_S, keys)
    result = {}
    for key in keys:
        now, old = after.get(key) or {}, before.get(key) or {}
        after_ms = now["total_ms"] / now["calls"] if now.get("calls") else None
        before_ms = old["total_ms"] / old["calls"] if old.get("calls") else None
        after_qps = (now.get("calls") or 0) / (MEMBER_AFTER_S + 60)
        before_qps = (old.get("calls") or 0) / MEMBER_BEFORE_S
        result[key] = {"before_ms": before_ms, "after_ms": after_ms, "before_qps": before_qps,
                       "after_qps": after_qps,
                       "latency_change": after_ms / before_ms - 1 if after_ms and before_ms
                       else None,
                       "calls_change": after_qps / before_qps - 1 if before_qps else None}
    return result


# Incident windows (DESIGN_V5 section 3)

INCIDENT_STEP = 60
SPARK_STEP = 300
SPARK_MARGIN_S = 1800
_INCIDENT_CACHE: dict[tuple[Any, ...], dict[str, Any] | None] = {}
_INCIDENT_CACHE_MAX = 2000
INCIDENT_OPEN_CACHE_S = 300


def _fallback_incident(item: dict[str, Any], body: dict[str, Any]) -> dict[str, Any] | None:
    """The window from the item's own times, for items without a per-query reading."""
    if item["type"] == "monitoring_issue" and item["subject"] == "lost_connection" and \
            body.get("gap_from"):
        # The gap in readings: from the last reading to when monitoring resumed.
        end = body.get("gap_to")
        return {"start": int(body["gap_from"]), "end": int(end) if end else None,
                "ongoing": not end, "measured": False, "metric": None, "change": None,
                "queries": []}
    if item["type"] in ("daily_summary", "monitoring_issue"):
        return None
    start = item["created_at"]
    duration = body.get("duration_s") or (body.get("numbers") or {}).get("duration_s")
    if item["type"] == "stuck_query" and isinstance(duration, (int, float)):
        start = int(item["created_at"] - duration)
    end = item["resolved_at"] if item.get("resolved_by") == "code" else None
    return {"start": start, "end": end,
            "ongoing": end is None and item["state"] not in ("resolved", "dismissed"),
            "measured": False,
            "metric": None, "change": (item.get("measurement") or {}).get("change"),
            "queries": []}


def _minute_series(store: MonitorStore, target: str, windows: dict[str, tuple[int, int]]
                   ) -> tuple[dict[str, dict[int, list[float]]], set[int]]:
    """Calls and time per query and minute over each query's own [begin, finish), and the
    minutes monitoring read. Each five minutes come from minute rows when they are kept,
    else from the five-minute row, spread evenly. One read serves every query."""
    rows = store.read(
        "SELECT s.query_key,s.res,s.bucket_start ts,s.calls,s.total_ms "
        "FROM json_each(?) w CROSS JOIN stmt_series s ON s.target=? AND s.query_key=w.key "
        "AND s.res IN (60,300) AND s.bucket_start>=json_extract(w.value,'$[0]') "
        "AND s.bucket_start<json_extract(w.value,'$[1]') WHERE s.res=60 OR NOT EXISTS ("
        "SELECT 1 FROM stmt_series f WHERE f.target=s.target AND f.query_key=s.query_key "
        "AND f.res=60 AND f.bucket_start>=s.bucket_start AND f.bucket_start<s.bucket_start+300)",
        (json.dumps(windows), target))
    begin = min(start for start, _ in windows.values())
    finish = max(end for _, end in windows.values())
    series: dict[str, dict[int, list[float]]] = defaultdict(dict)
    for row in rows:
        for minute in range(row["ts"], row["ts"] + row["res"], INCIDENT_STEP):
            entry = series[row["query_key"]].setdefault(minute, [0.0, 0.0])
            entry[0] += (row["calls"] or 0) * INCIDENT_STEP / row["res"]
            entry[1] += (row["total_ms"] or 0) * INCIDENT_STEP / row["res"]
    read: set[int] = set()
    for row in store.read("SELECT res,bucket_start ts FROM db_series WHERE target=? "
                          "AND res IN (60,300) AND bucket_start>=? AND bucket_start<?",
                          (target, begin, finish)):
        read.update(range(row["ts"], row["ts"] + row["res"], INCIDENT_STEP))
    return series, read


def _points(minutes: dict[int, list[float]], read: set[int], metric: str, begin: int,
            finish: int, step: int = INCIDENT_STEP) -> list[tuple[int, float | None]]:
    """Per-step values: calls a second (0 when monitoring read and the query didn't run)
    or mean latency (None without calls)."""
    points: list[tuple[int, float | None]] = []
    for bucket in range(begin // step * step, finish, step):
        calls = total = 0.0
        seen = False
        for minute in range(bucket, bucket + step, INCIDENT_STEP):
            if minute in minutes:
                calls += minutes[minute][0]
                total += minutes[minute][1]
                seen = True
            seen = seen or minute in read
        if not seen:
            points.append((bucket, None))
        elif metric == "qps":
            points.append((bucket, calls / step))
        else:
            points.append((bucket, total / calls if calls else None))
    return points


def _member_filed(body: dict[str, Any], key: str, created_at: int) -> tuple[int, int | None]:
    """When a group member joined its episode (members can join long after filing), and,
    when that wasn't recorded for a recovered member, the time it recovered: it joined
    somewhere between filing and then."""
    for field in ("recovery_queries", "recovered_queries"):
        entry = (body.get(field) or {}).get(key) or {}
        if isinstance(entry.get("filed_at"), (int, float)):
            return int(entry["filed_at"]), None
        retired = entry.get("retired_at")
        if isinstance(retired, (int, float)) and retired > created_at:
            return created_at, int(retired)
    return created_at, None


def incident_windows(store: MonitorStore, target: str, items: list[dict[str, Any]],
              stats: dict[int, dict[str, dict[str, Any]]]) -> dict[int, dict[str, Any] | None]:
    """Each presented item's incident window: its onset and end measured from the
    per-query minute series around filing, per member (around when it joined) for group
    items. Closed items don't change, so their windows are kept; open ones for a check
    cycle."""
    now = int(store.clock())
    result: dict[int, dict[str, Any] | None] = {}
    for item in items:
        closed = item["state"] in ("resolved", "dismissed")
        # Open items update every cycle, so they're cached per check cycle, not per update.
        cache_key = (str(store.path), item["id"], item["state"],
                     item["updated_at"] if closed else now // INCIDENT_OPEN_CACHE_S)
        if cache_key in _INCIDENT_CACHE:
            result[item["id"]] = _INCIDENT_CACHE[cache_key]
            continue
        body = _body(item)
        keys = item["query_keys"]
        group = item["type"] == "many_queries_changed" and bool(keys)
        member = (stats.get(item["id"]) or {})
        if group:
            metric = "mean_ms" if group_is_latency(item) else "qps"
            signals = {key: member_signal(key, body, member.get(key) or {}, metric)
                       for key in keys}
            joined = {key: _member_filed(body, key, item["created_at"]) for key in keys}
        else:
            signal = incident_signal(item["type"])
            if signal is None or len(keys) != 1:
                result[item["id"]] = _fallback_incident(item, body)
                continue
            metric = signal[0]
            signals = {keys[0]: signal}
            joined = {keys[0]: (item["created_at"], None)}
        windows = {key: (filed - INCIDENT_LOOKBACK_S,
                         min(now, (until or filed) + INCIDENT_LOOKAHEAD_S) + INCIDENT_STEP)
                   for key, (filed, until) in joined.items()}
        # One read covers every member's span around its own anchor.
        series, read = _minute_series(store, target, windows)
        spans: dict[str, dict[str, Any] | None] = {}
        sparks: dict[str, list[dict[str, Any]]] = {}
        levels: dict[str, tuple[str, float | None, float | None]] = {}
        for key, (filed, until) in joined.items():
            begin, finish = windows[key]
            own, direction = signals[key]
            before, after = incident_levels(item["type"], body, key, own,
                                            member.get(key) or {})
            levels[key] = (own, before, after)
            points = _points(series.get(key, {}), read, own, begin, finish)
            if until is not None:
                filed = first_changed_at(
                    points, metric=own, direction=direction, before=before, after=after,
                    begin=filed - INCIDENT_NEAR_S, until=until) or filed
            span = spans[key] = incident_span(
                points, filed,
                metric=own, direction=direction, before=before, after=after,
                step=INCIDENT_STEP)
            if group:
                sparks[key] = _spark(series.get(key, {}), read, own, filed, span)
        window = _incident(item, body, metric, keys, spans, sparks, now, levels)
        result[item["id"]] = window
        if len(_INCIDENT_CACHE) >= _INCIDENT_CACHE_MAX:
            _INCIDENT_CACHE.clear()
        _INCIDENT_CACHE[cache_key] = window
    return result


def _spark(minutes: dict[int, list[float]], read: set[int], metric: str, filed: int,
           span: dict[str, Any] | None) -> list[dict[str, Any]]:
    """A member's sparkline: its own window with half an hour either side, at most 36
    points."""
    start = min(span["start"], filed) if span else filed - 3600
    end = max(span["end"] or filed, filed) if span else filed
    begin, finish = start - SPARK_MARGIN_S, end + SPARK_MARGIN_S
    if read:
        finish = min(finish, max(read) + INCIDENT_STEP)
    step = SPARK_STEP * max(1, -(-(finish - begin) // (SPARK_STEP * 36)))
    return [{"ts": ts, "value": value}
            for ts, value in _points(minutes, read, metric, begin, finish, step)
            if value is not None]


def _incident(item: dict[str, Any], body: dict[str, Any], metric: str, keys: list[str],
              spans: dict[str, dict[str, Any] | None], sparks: dict[str, list[dict[str, Any]]],
              now: int, levels: dict[str, tuple[str, float | None, float | None]]
              ) -> dict[str, Any] | None:
    measured = [span for span in spans.values() if span]
    fallback = _fallback_incident(item, body)
    if fallback is None:
        return None
    code_end = item["resolved_at"] if item.get("resolved_by") == "code" else None
    if measured:
        start = min(span["start"] for span in measured)
        ends = [span["end"] for span in measured]
        end = max(ends) if all(value is not None for value in ends) else code_end
    else:
        start, end = fallback["start"], fallback["end"]
    group = item["type"] == "many_queries_changed"
    change = (item.get("median_change") if group else
              (measured[0]["change"] if measured else fallback["change"]))
    queries = [{"query_key": key, "start": span["start"] if span else None,
                "end": span["end"] if span else None,
                "change": span["change"] if span else None,
                "metric": "latency" if own == "mean_ms" else "calls",
                "before": before, "after": after, "spark": sparks.get(key, [])}
               for key in keys for span in [spans.get(key)]
               for own, before, after in [levels.get(key, (metric, None, None))]] if group else []
    # Open items without an end are still happening; so are closed ones whose readings
    # still show the change. Past the look-ahead, a closed item's state is unknown.
    open_item = item["state"] not in ("resolved", "dismissed")
    ongoing = end is None and (open_item or (
        bool(measured) and now < item["created_at"] + INCIDENT_LOOKAHEAD_S))
    return {"start": start, "end": end, "ongoing": ongoing, "measured": bool(measured),
            "metric": "latency" if metric == "mean_ms" else "calls", "change": change,
            "queries": queries}


TREND_STEP = 300
TREND_POINTS = 24


def _trends(store: MonitorStore, target: str,
            items: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    if not items:
        return {}
    windows = {item["id"]: (item["created_at"] // TREND_STEP * TREND_STEP - 16 * TREND_STEP)
               for item in items}
    start = max(windows.values()) - 8 * 86400
    # Read only the spans around filings, merged where they overlap.
    spans: list[list[int]] = []
    for begin in sorted(value for value in windows.values() if value >= start):
        finish = begin + TREND_POINTS * TREND_STEP
        if spans and begin <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], finish)
        else:
            spans.append([begin, finish])
    wanted = sorted({keys[0] for item in items
                     if len(keys := item_query_keys(item["subject"], json.loads(
                         item["body_json"]) if isinstance(item["body_json"], str) else {})) == 1})
    query_rows: list[dict[str, Any]] = []
    database_rows: list[dict[str, Any]] = []
    for begin, finish in spans:
        if wanted:
            query_rows += stmt_rows(store, target, begin, finish, wanted, TREND_STEP)
        database_rows += db_rows(store, target, begin, finish, TREND_STEP)
    query_buckets = spread(query_rows, TREND_STEP)
    db_buckets = spread(database_rows, TREND_STEP)
    result = {}
    for item in items:
        body = json.loads(item["body_json"]) if isinstance(item["body_json"], str) else {}
        keys = item_query_keys(item["subject"], body)
        metric = trend_metric(item["type"])
        key = keys[0] if len(keys) == 1 else None
        points = []
        for index in range(TREND_POINTS):
            bucket = windows[item["id"]] + index * TREND_STEP
            if bucket < start:
                continue
            if key is not None:
                parts = [(row, share) for row, share in query_buckets.get(bucket, [])
                         if row["query_key"] == key]
            else:
                parts = db_buckets.get(bucket, [])
            if not parts:
                # Monitoring was reading, the query just didn't run: zero calls, no latency.
                if key is not None and metric == "qps" and bucket in db_buckets:
                    points.append({"ts": bucket, "value": 0.0})
                continue
            calls = sum((row["calls"] or 0) * share for row, share in parts)
            total = sum((row["total_ms"] or 0) * share for row, share in parts)
            if key is None and metric != "mean_ms":
                point = _health_point(bucket, parts, TREND_STEP, bucket + TREND_STEP)
                rollbacks = point["xact_rollback"]
                value = {"qps": point["qps"],
                         "rollbacks": rollbacks * 60 / TREND_STEP if rollbacks is not None
                         else None,
                         "cache_hit_ratio": point["cache_hit_ratio"],
                         "active_conns": point["active_conns"]}.get(metric)
            elif metric == "qps":
                value = calls / TREND_STEP
            else:
                value = total / calls if calls else None
            if value is not None:
                points.append({"ts": bucket, "value": value})
        result[item["id"]] = points
    return result


# The redesign's query row fields, the Full analysis page and Home (plan section 4.2, W0).

HOME_LOG_LIMIT = 6
QUIET_CYCLES = 3
OPEN_STATES = ("new", "seen", "snoozed")
ENGINE_WORDS = {"postgresql": "PostgreSQL", "mysql": "MySQL"}
_FIX_TITLE = re.compile(r"^Add an index on (\w+)\((.*)\)$")


def fix_label(fix: dict[str, Any]) -> str:
    """"Add index posts (creationdate, owneruserid, score)" from the digest's fix title."""
    title = str(fix.get("title") or "")
    match = _FIX_TITLE.match(title) if fix.get("kind") == "index" else None
    if match:
        return f"Add index {match.group(1)} ({match.group(2)})"
    return title or "Fix suggested"


def query_status(stuck: bool, job: dict[str, Any] | None, digest: dict[str, Any] | None,
                 fix: dict[str, Any] | None) -> str:
    if stuck:
        return "stuck"
    if job and job.get("state") in ("queued", "running"):
        return "analyzing"
    if fix:
        return "fix_suggested"
    return "healthy" if digest else "not_analyzed"


def hourly_means(store: MonitorStore, target: str, keys: list[str],
                 now: int) -> dict[str, list[dict[str, Any]]]:
    """Each query's mean latency per hour over the last day, for the trend column."""
    sums: dict[str, dict[int, list[float]]] = defaultdict(dict)
    for bucket, parts in spread(stmt_rows(store, target, now - 86400, now + 1, keys,
                                          step=3600), 3600).items():
        for row, share in parts:
            acc = sums[row["query_key"]].setdefault(bucket, [0.0, 0.0])
            acc[0] += (row["calls"] or 0) * share
            acc[1] += (row["total_ms"] or 0) * share
    return {key: [{"ts": bucket, "mean_ms": ms / calls if calls else None}
                  for bucket, (calls, ms) in sorted(buckets.items())]
            for key, buckets in sums.items()}


def checks_ran_at(store: MonitorStore, target: str) -> int | None:
    """When an attention check last ran to completion (a shortlist judged, or nothing to
    judge); null when the checks are held by sign-in, a limit or an error."""
    rows = store.read("SELECT ts FROM judge_run WHERE target=? AND outcome IN "
                      "('filed','empty','dry_run','noop') ORDER BY ts DESC,id DESC LIMIT 1",
                      (target,))
    return int(rows[0]["ts"]) if rows else None


def checks_held(store: MonitorStore, target: str) -> bool:
    """Whether the latest attention check stopped short: held by sign-in, a limit or an
    error, so no next check is due until that clears."""
    rows = store.read("SELECT outcome FROM judge_run WHERE target=? ORDER BY ts DESC,id DESC "
                      "LIMIT 1", (target,))
    return bool(rows) and rows[0]["outcome"] not in ("filed", "empty", "dry_run", "noop")


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        else None


def analysis_view(store: MonitorStore, target: str, key: str, digest: dict[str, Any],
                  now: int) -> dict[str, Any]:
    """The digest plus what the Full analysis page shows, read from the stored analysis.
    Facts the stored record lacks stay null."""
    from features.monitor.analysis_digest import as_tested, index_why, load_analysis, tested_index
    from shared.monitor.monitor_store import local_day_bounds

    analysis = load_analysis({"state": "done", "analysis_ref": digest.get("analysis_ref"),
                              "target": target, "query_key": key, "mode": digest.get("mode")}) \
        if digest.get("analysis_ref") else None
    llm = getattr(analysis, "llm_analysis", None) if analysis else None
    llm = llm if isinstance(llm, dict) else {}
    display = getattr(analysis, "display_payload", None) if analysis else None
    display = display if isinstance(display, dict) else {}
    explain = getattr(analysis, "explain_plan", None) if analysis else None
    explain = explain if isinstance(explain, dict) else {}
    engine = _engine(target)
    fix = digest.get("fix") if digest.get("fix_ready") and digest.get("fix") else None
    testing = display.get("index_testing") if isinstance(display.get("index_testing"), dict) \
        else {}
    cost_before = cost_after = None
    tested = tested_index(fix.get("sql"), testing) if fix else None
    # A cost the planner reached without the index says nothing about the fix.
    if tested and tested.get("planner_used_index"):
        cost_before = _num(tested.get("original_cost") or tested.get("cost_before"))
        cost_after = _num(tested.get("new_cost") or tested.get("cost_after"))
    plan = digest.get("plan_summary") or {}
    root = explain.get("Plan") if isinstance(explain.get("Plan"), dict) else {}
    findings = [{"title": text, "detail": None, "value": None}
                for text in digest.get("findings") or []]
    rewrites = [{"name": " ".join(str(item.get("explanation") or "Rewrite").split())[:80],
                 "before_ms": None, "after_ms": None, "runs": None, "outcome": "untested",
                 "sql": str(item["rewritten_sql"]).strip()}
                for item in llm.get("rewrite_suggestions") or []
                if isinstance(item, dict) and item.get("rewritten_sql")]
    # Every index recommendation besides the one the fix leads with, with its statement.
    lead_sql = str((digest.get("fix") or {}).get("sql") or "").strip().lower()
    indexes = []
    for item in llm.get("index_recommendations") or []:
        sql, tested = as_tested(item, testing) if isinstance(item, dict) else ("", None)
        if not sql or sql.lower() == lead_sql:
            continue
        table = str(item.get("table") or "").split(".")[-1]
        columns = ", ".join(str(column) for column in
                            (tested or {}).get("columns") or item.get("columns") or [])
        indexes.append({"title": f"Add an index on {table}({columns})" if table and columns
                        else "Add an index",
                        "detail": index_why(item.get("rationale")) or None, "sql": sql})
    also = indexes + [
        {"title": " ".join(str(item["description"]).split())[:120], "detail": None}
        for item in llm.get("optimization_opportunities") or []
        if isinstance(item, dict) and item.get("description") and not (
            str(item.get("type") or "").lower() == "configuration" and
            str(item.get("priority") or "").lower() == "high")]
    calls = (stmt_totals(store, target, local_day_bounds(now)[0], now + 1, [key]).get(key)
             or {}).get("calls")
    sampled = display.get("parameters") or display.get("sampled_parameters") or {}
    if isinstance(sampled, dict):
        params = [{"name": str(name), "value": str(value)} for name, value in sampled.items()]
    elif isinstance(sampled, list):
        # A monitor analysis records each placeholder with the SQL literal it ran with.
        params = [{"name": str(value["name"]), "value": str(value["value"])}
                  if isinstance(value, dict) and {"name", "value"} <= value.keys()
                  else {"name": f"${index + 1}", "value": str(value)}
                  for index, value in enumerate(sampled)]
    else:
        params = []
    headline = str(digest.get("headline") or "").rstrip(".")
    rest = str((fix or {}).get("why") or (findings[0]["title"] if findings else "")).strip()
    ref = str(digest.get("analysis_ref") or "")
    tokens = _num(llm.get("tokens") or getattr(analysis, "tokens_used", None))
    return {**digest, "score": digest.get("efficiency_score"),
            "verdict": {"lead": f"{headline}.", "rest": rest} if headline else None,
            "fix_detail": {"sql": concurrent_index_sql(fix["sql"], engine) or fix["sql"],
                           "serves": str(fix.get("why") or "") or None,
                           "cost_before": cost_before, "cost_after": cost_after,
                           "size": None, "write_cost": None}
            if fix and fix.get("sql") else None,
            "plan_findings": findings, "rewrites": rewrites, "also": also,
            "readyset": {"blockers": [], "verified": False}, "benchmark": None,
            "run": {"model": str(llm.get("model") or getattr(analysis, "model", "") or "") or None,
                    "tokens": int(tokens) if tokens else None,
                    "seconds": _num(getattr(analysis, "duration_s", None)),
                    "id": ref.rstrip("/").rsplit("/", 1)[-1] or None},
            "rows_read": plan.get("rows_examined"), "rows_returned": plan.get("rows_returned"),
            "planner_cost": _num(root.get("Total Cost")),
            "calls_today": int(calls) if calls else None, "params": params,
            "param_source": str(display.get("param_source") or "") or None,
            "explain_text": json.dumps(explain, indent=1)[:20000] if explain else None}


def _listed_fix_digest(store: MonitorStore, target: str, key: str) -> dict[str, Any]:
    """The digest of the analysis whose ready fix the query list shows; {} without one."""
    from features.monitor.analysis_digest import latest_done_job

    job = latest_done_job(store, target, key) or library_analysis_jobs(target, [key]).get(key)
    digest = (digest_for_ref(target, key, job["analysis_ref"], job.get("mode"),
                             job.get("finished_at")) if job else None) or {}
    return digest if digest.get("fix_ready") and digest.get("fix") else {}


def benchmark_fixes(store: MonitorStore, target: str, keys: list[str], runs: int,
                    now: int) -> dict[str, Any]:
    """Each query with its fix: before and after as Actions shows them, the mean over the
    plan's readings and the planner's measured estimate for the change. A query no open
    change covers falls back to the estimate of the analysis whose fix the list shows.
    Nothing runs on the database and no scratch schema is built; `scratch_schema` says so."""
    from features.monitor.actions import database_minute, plan_cards
    from features.monitor.judge import ready_fixes

    fixes = {fix["query_key"]: fix for fix in ready_fixes(store, target, now)}
    totals = database_minute(store, target, now)[1]
    # Actions' estimate for each query an open change speeds up; None where it has none.
    planned: dict[str, float | None] = {}
    planned_kind: dict[str, str] = {}
    for card in plan_cards(store, target, now):
        if card["state"] == "open" and card["type"] in ("add_index", "rewrite_query"):
            for query in card["queries"]:
                if planned.get(query["key"]) is None:
                    planned[query["key"]] = query["expected_ms"]
                    planned_kind[query["key"]] = \
                        "index" if card["type"] == "add_index" else "rewrite"
    names = target_names(store, target)
    rows = []
    kinds: set[str] = set()
    for key in keys:
        fix, total, digest = fixes.get(key), totals.get(key) or {}, {}
        calls = float(total.get("calls") or 0)
        before = float(total.get("total_ms") or 0) / calls if calls else None
        if key in planned:
            after = planned[key] if before is not None else None
        else:
            digest = ((digest_for_ref(target, key, fix["analysis_ref"], None) or {}) if fix
                      else _listed_fix_digest(store, target, key))
            pct = ((digest.get("fix") or {}).get("expected_gain") or {}).get("cost_reduction_pct")
            after = (before * (1 - min(100.0, max(0.0, float(pct))) / 100)
                     if before is not None and isinstance(pct, (int, float)) else None)
        if after is not None:
            kinds.add(planned_kind.get(key) or str((digest.get("fix") or {}).get("kind")))
        reason = None
        if after is None:
            reason = ("No fix is ready for this query."
                      if fix is None and key not in planned and not digest.get("fix")
                      else "No calls recorded to compare with." if before is None else
                      "The analysis has no planner estimate for this index."
                      if (digest.get("fix") or {}).get("kind") == "index" else
                      "The analysis gave no estimate for this fix.")
        rows.append({"key": key, "name": names.get(key) or f"Query {key}", "before_ms": before,
                     "after_ms": after,
                     "change_pct": round((after / before - 1) * 100)
                     if after is not None and before else None, "estimated": True,
                     "reason": reason})
    return {"target": target, "ran_at": now, "runs": runs, "scratch_schema": False, "rows": rows,
            "share": round(sum(fixes[key]["share"] for key in keys if key in fixes), 4),
            "method": _benchmark_method(target, kinds)}


def _benchmark_method(target: str, kinds: set[str]) -> str:
    """How the estimates were made, naming only the kinds of fix the benchmark estimated."""
    parts = [text for kind, text in (
        ("index", "index fixes tested as hypothetical indexes by the planner"),
        ("rewrite", "rewrites from their test runs or planner cost")) if kind in kinds]
    if not parts:
        return f"No fix here has an estimate on {target} yet."
    return f"Estimated on {target}: {'; '.join(parts)}. Actions shows the same estimates."


def _connection_label(entry: dict[str, Any]) -> str:
    engine = ENGINE_WORDS.get(str(entry.get("engine") or "postgresql").lower(), "PostgreSQL")
    host, port = entry.get("host"), entry.get("port")
    if host and port:
        return f"{engine} at {host}:{port}"
    return f"{engine} at {host}" if host else engine


def _next_report_at(levers: dict[str, Any], now: int) -> int | None:
    if not levers.get("nightly_report"):
        return None
    due = datetime.fromtimestamp(now).replace(hour=int(levers["nightly_hour"]), minute=0,
                                              second=0, microsecond=0)
    if due.timestamp() <= now:
        due += timedelta(days=1)
    return int(due.timestamp())


def collect_timing(store: MonitorStore, target: str) -> tuple[int | None, int]:
    """The last good reading and the seconds between readings (60 until two exist)."""
    rows = store.read("SELECT ts FROM monitor_pulse WHERE target=? AND kind='collect' AND ok=1 "
                      "AND detail!=? ORDER BY id DESC LIMIT 2", (target, ANSWERED))
    every = max(30, min(600, rows[0]["ts"] - rows[1]["ts"])) if len(rows) == 2 else 60
    return (int(rows[0]["ts"]) if rows else None), int(every)


def failing_since(store: MonitorStore, target: str) -> int | None:
    """When the current run of failed collections began; None while the latest one succeeded."""
    rows = store.read("SELECT MIN(ts) ts FROM monitor_pulse WHERE target=? AND kind='collect' "
                      "AND ok=0 AND id > COALESCE((SELECT MAX(id) FROM monitor_pulse WHERE "
                      "target=? AND kind='collect' AND ok=1), 0)", (target, target))
    return int(rows[0]["ts"]) if rows and rows[0]["ts"] is not None else None


def ai_paused_since(store: MonitorStore, target: str, issues: dict[str, dict[str, Any]]
                    ) -> int | None:
    """When attention checks stopped reaching the AI: the earlier of the first held check since
    the AI last judged one and when the open sign-in or limit item was filed."""
    rows = store.read("SELECT MIN(ts) ts FROM judge_run WHERE target=? AND outcome NOT IN "
                      "('filed','empty','dry_run','noop') AND id > COALESCE((SELECT MAX(id) "
                      "FROM judge_run WHERE target=? AND outcome IN ('filed','dry_run')), 0)",
                      (target, target))
    began = [int(row["created_at"]) for subject, row in issues.items()
             if subject in ("jev_login", "database_limit")]
    if rows and rows[0]["ts"] is not None:
        began.append(int(rows[0]["ts"]))
    return min(began) if began else store.enabled_at(target)


def running_work(store: MonitorStore, targets: list[str]
                 ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """The deep analysis and the report running now, per database, in one read each."""
    if not targets:
        return {}, {}
    marks = ",".join("?" for _ in targets)
    jobs = {row["target"]: row for row in store.read(
        f"SELECT target,query_key FROM analyze_job WHERE state='running' AND target IN ({marks}) "
        "ORDER BY started_at", tuple(targets))}
    reports = {row["target"]: row for row in store.read(
        f"SELECT target,id FROM report_run WHERE state='running' AND target IN ({marks})",
        tuple(targets))}
    return jobs, reports


def first_hour_progress(store: MonitorStore, target: str, now: int) -> tuple[int, int] | None:
    """The first hour's analyses: (finished, scheduled); None outside it or before any."""
    from shared.monitor.monitor_store import FIRST_HOUR_JOB, FIRST_HOUR_S

    enabled_at = store.enabled_at(target)
    if not enabled_at or not enabled_at <= now < enabled_at + FIRST_HOUR_S:
        return None
    start, end = store.first_hour(target)
    if not start or not start <= now < end:
        return None
    row = store.read("SELECT COUNT(*) AS total,COALESCE(SUM(state IN ('done','failed')),0) "
                     "AS done FROM analyze_job WHERE target=? AND state!='skipped' AND "
                     f"{FIRST_HOUR_JOB}", (target, start, end))[0]
    return (int(row["done"]), int(row["total"])) if row["total"] else None


def activity(store: MonitorStore, target: str, now: int, *, enabled: bool,
             last_reading_at: int | None, every_s: int, checks_ran_at: int | None,
             check_every_s: int, running: dict[str, Any] | None = None,
             report: dict[str, Any] | None = None) -> dict[str, Any]:
    """What monitoring is doing on this database right now, for the radar and its tooltip.
    `done`/`total` count the first hour's analyses while it lasts."""
    from features.monitor.loop import cycling

    progress = first_hour_progress(store, target, now) if enabled else None
    if not enabled:
        kind, label = "idle", "Monitoring is off"
    elif report:
        kind, label = "report", "Writing the report"
    elif running:
        name = target_names(store, target).get(running["query_key"]) or \
            f"Query {running['query_key']}"
        kind, label = "analyzing", f"Analyzing {name}"
    elif cycling(target):
        kind, label = "checking", "Checking queries for changes"
    else:
        kind, label = "reading", "Reading query statistics"
    return {"kind": kind, "label": label,
            "done": progress[0] if progress else None,
            "total": progress[1] if progress else None,
            "last_reading_at": last_reading_at, "every_s": every_s,
            "next_check_at": checks_ran_at + check_every_s
            if enabled and checks_ran_at and not checks_held(store, target) else None}


def _month_day(ts: int) -> str:
    moment = datetime.fromtimestamp(ts)
    return f"{moment.strftime('%B')} {moment.day}"


def home_headline(item: dict[str, Any]) -> str:
    """"a stuck query since 9:55 AM": what needs you and since when."""
    type, subject = item["type"], item["subject"]
    what = {"stuck_query": "a stuck query", "lock_contention": "queries waiting on locks",
            "connections_near_max": "connections near the limit"}.get(type)
    if type == "monitoring_issue":
        what = ("the database can't be reached"
                if subject in ("lost_connection", "password_missing") else "a monitoring issue")
    if what is None:
        what = item["title"][:1].lower() + item["title"][1:]
    return f"{what} since {clock_text(item['created_at'])}"


def _cache_reason(report: dict[str, Any] | None, cache: float | None) -> str | None:
    if cache is None or cache >= 0.9 or not report:
        return None
    actions = ((report.get("snapshot") or {}).get("actions") or [])
    buffers = next((action for action in actions if isinstance(action, dict) and action.get("id")
                    in ("setting_shared_buffers", "setting_innodb_buffer_pool_size", "cache_hit")),
                   None)
    finding = str((buffers or {}).get("finding") or "")
    if not finding:
        return None
    return "shared_buffers is the default" if "default" in finding.lower() else finding.rstrip(".")


def _day_card(store: MonitorStore, target: str, now: int, midnight: int,
              database: dict[str, Any]) -> dict[str, Any] | None:
    from features.monitor.snapshot import _window_numbers

    today = _window_numbers(store, target, midnight, now + 1)
    if not today:
        return None
    span = now + 1 - midnight
    yesterday = _window_numbers(store, target, midnight - 86400, midnight - 86400 + span)
    vitals = today.get("vitals") or {}
    latest = store.read("SELECT cache_hit_ratio FROM db_series WHERE target=? AND res=60 "
                        "ORDER BY bucket_start DESC LIMIT 1", (target,))
    cache = latest[0]["cache_hit_ratio"] if latest else today.get("cache_hit_ratio_min")
    reports = store.read("SELECT id,ts,period_start,report_json FROM report_run WHERE target=? "
                         "AND state='done' AND sample=0 ORDER BY COALESCE(period_end,ts) DESC,"
                         "id DESC LIMIT 1", (target,))
    report = json.loads(reports[0]["report_json"] or "null") if reports else None
    mean, prior = today.get("mean_ms"), yesterday.get("mean_ms")
    return {"target": target, "qps": today.get("qps"), "qps_peak": today.get("peak_qps"),
            "qps_peak_at": today.get("peak_qps_at"), "mean_ms": mean,
            "mean_ms_delta": mean / prior - 1 if mean is not None and prior else None,
            "fixes_ready": database["fixes_ready"], "fixes_share": database["_fixes_share"],
            "fixes_item_id": database["fixes_item_id"], "cache_hit_ratio": cache,
            "cache_hit_reason": _cache_reason(report if isinstance(report, dict) else None, cache),
            "series": [{"ts": ts, "qps": qps} for ts, qps in zip(vitals.get("xs") or [],
                                                                 vitals.get("qps") or [])],
            "report_id": reports[0]["id"] if reports else None,
            "report_day": (datetime.fromtimestamp(int(reports[0]["period_start"] or reports[0]["ts"]))
                           .date().isoformat() if reports else None)}


def _log_entry(item: dict[str, Any], kind: str, ts: int) -> dict[str, Any] | None:
    type, target = item["type"], item["target"]
    body = _body(item)
    inbox = {"kind": "inbox", "target": target, "label": "Inbox", "item_id": item["id"]}
    keys = item.get("query_keys") or []
    created = kind == "created"
    if type in ("daily_summary", "report_ready"):
        return None
    if type == "stuck_query":
        if created:
            return {"ts": ts, "text": "Stuck query filed", "detail": None, "link": inbox,
                    "band": "act_now"}
        duration = body.get("duration_s")
        minutes = max(1, int(round(float(duration) / 60))) if duration else None
        return {"ts": ts, "text": "Stuck query ended, item closed",
                "detail": f"ran {minutes} {'minute' if minutes == 1 else 'minutes'}"
                if minutes else None, "link": inbox, "band": None}
    if type == "fixes_ready":
        if not created:
            return None
        count = int(body.get("count") or 0)
        return {"ts": ts, "text": f"Report ran: {count} index {'fix' if count == 1 else 'fixes'} ready",
                "detail": f"{format_pct(float(body.get('share') or 0))} of database time",
                "link": inbox, "band": None}
    if type == "changes_ready":
        if not created:
            return None
        count, first = int(body.get("count") or 0), int(body.get("do_first") or 0)
        return {"ts": ts, "text": f"{count} {'change' if count == 1 else 'changes'} ready",
                "detail": f"{first} to do first" if first else None,
                "link": {"kind": "actions", "target": target, "label": "Actions",
                         "item_id": item["id"]}, "band": None}
    if type == "no_change_yet":
        return None
    if type == "fix_confirmed":
        if not created:
            return None
        before, after = body.get("before_ms"), body.get("after_ms")
        return {"ts": ts, "text": f"Fix confirmed on {body.get('index_label') or body.get('index_name') or target}",
                "detail": f"{latency_text(before)} to {latency_text(after)}"
                if isinstance(before, (int, float)) and isinstance(after, (int, float)) else None,
                "link": {"kind": "query", "target": target, "label": "Query",
                         "query_key": keys[0] if keys else None, "item_id": item["id"]},
                "band": None}
    if type == "traffic_spike":
        measurement = item.get("measurement") or {}
        change = measurement.get("change")
        size = f"{change + 1:.1f}x" if isinstance(change, (int, float)) else None
        link = {"kind": "chart", "target": target, "label": "Chart", "item_id": item["id"]}
        if created:
            return {"ts": ts, "text": "Traffic spike filed", "detail": size, "link": link,
                    "band": "watch"}
        minutes = max(1, int(round(((item.get("resolved_at") or ts) - item["created_at"]) / 60)))
        return {"ts": ts, "text": "Traffic spike ended",
                "detail": f"{size} for {minutes} minutes" if size else f"{minutes} minutes",
                "link": link, "band": None}
    title = item["title"]
    if created:
        return {"ts": ts, "text": f"{title} filed", "detail": item.get("summary") or None,
                "link": inbox, "band": "act_now" if item["severity_band"] == "act_now" else "watch"}
    return {"ts": ts, "text": f"{title} closed", "detail": None, "link": inbox, "band": None}


def home_log(store: MonitorStore, targets: list[str], since: int, limit: int,
             fixes_items: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The agent's own log, newest first: items filed and closed, reports run, analyses."""
    if not targets:
        return []
    placeholders = ",".join("?" for _ in targets)
    marks = store.read(f"SELECT e.ts,e.kind,e.item_id FROM inbox_event e JOIN inbox_item i "
                       f"ON i.id=e.item_id WHERE i.target IN ({placeholders}) AND e.ts>=? "
                       "AND e.kind IN ('created','resolved') AND i.delivery!='digest' "
                       "ORDER BY e.ts DESC,e.id DESC LIMIT 200", (*targets, since))
    ids = sorted({mark["item_id"] for mark in marks})
    items: dict[int, dict[str, Any]] = {}
    if ids:
        rows = store.read(f"SELECT * FROM inbox_item WHERE id IN ({','.join('?' for _ in ids)})",
                          tuple(ids))
        items = {item["id"]: item for item in inbox_items(store, rows)}
    entries = []
    for mark in marks:
        item = items.get(mark["item_id"])
        entry = _log_entry(item, mark["kind"], mark["ts"]) if item else None
        if entry:
            entries.append(entry)
    for run in store.read(f"SELECT id,target,trigger,ts,finished_at,period_start FROM report_run "
                          f"WHERE target IN ({placeholders}) AND state='done' AND sample=0 "
                          "AND COALESCE(finished_at,ts)>=? ORDER BY ts DESC LIMIT 20",
                          (*targets, since)):
        link = {"kind": "report", "target": run["target"], "label": "Report", "report_id": run["id"]}
        fixes = fixes_items.get(run["target"])
        fixes_body = _body(fixes) if fixes else {}
        if run["trigger"] == "nightly":
            entries.append({"ts": run["finished_at"] or run["ts"],
                            "text": f"Daily report for {_month_day(int(run['period_start'] or run['ts'] - 86400))}",
                            "detail": None, "link": link, "band": None})
        elif fixes and fixes_body.get("report_id") == run["id"]:
            continue
        else:
            entries.append({"ts": run["finished_at"] or run["ts"], "text": "Report ran",
                            "detail": None, "link": link, "band": None})
    jobs = store.read(f"SELECT target,query_key,finished_at FROM analyze_job WHERE target IN "
                      f"({placeholders}) AND state='done' AND finished_at>=? AND reason!='user' "
                      "ORDER BY finished_at DESC LIMIT 100", (*targets, since))
    ready = {(row["target"], row["query_key"]) for row in store.read(
        f"SELECT target,query_key FROM query_ui WHERE target IN ({placeholders}) AND fix_ready=1",
        tuple(targets))}
    group: list[dict[str, Any]] = []
    for job in [*jobs, None]:
        if group and (job is None or job["target"] != group[0]["target"] or
                      group[-1]["finished_at"] - job["finished_at"] > 600):
            count = len(group)
            fixes = sum((row["target"], row["query_key"]) in ready for row in group)
            detail = ("both healthy" if count == 2 and not fixes else "all healthy" if not fixes
                      else f"{fixes} with a fix")
            entries.append({"ts": group[0]["finished_at"],
                            "text": f"{count} new {'query' if count == 1 else 'queries'} analyzed",
                            "detail": detail if count > 1 else ("fix suggested" if fixes else "healthy"),
                            "link": {"kind": "queries", "target": group[0]["target"],
                                     "label": "Queries"}, "band": None})
            group = []
        if job is not None:
            group.append(job)
    entries.sort(key=lambda entry: -entry["ts"])
    return entries[:limit]


def _building_first_plan(store: MonitorStore, target: str, now: int) -> bool:
    """Whether the first pass of analyses is still building this database's first plan."""
    from features.monitor.report import first_pass

    progress = first_pass(store, target, now)
    return progress is not None and not progress["due"]


def home(store: MonitorStore, now: int, *, configured: list[dict[str, Any]] | None = None,
         selected: str | None = None, limit: int = HOME_LOG_LIMIT) -> dict[str, Any]:
    """Home: one sentence from data, the selected database's day, every database sorted by
    attention with its strip of counts, the day's log and what you analyzed. Act now (Inbox)
    and Do first (Actions) count separately."""
    from features.monitor import actions, database_limit
    from shared.monitor.monitor_store import local_day_bounds

    entries = configured or [{"name": name} for name in sorted(store.monitored_targets())]
    analyzing, reporting = running_work(store, [str(entry.get("name") or "") for entry in entries])
    midnight = local_day_bounds(now)[0]
    databases: list[dict[str, Any]] = []
    act_items: list[dict[str, Any]] = []
    fixes_items: dict[str, dict[str, Any]] = {}
    enabled_names: list[str] = []
    checks: list[int | None] = []
    readings: list[int | None] = []
    reports_at: list[int | None] = []
    for entry in entries:
        name = str(entry.get("name") or "")
        if not name:
            continue
        levers = store.levers(name)
        enabled = bool(levers["enabled"])
        rows = [row for row in store.inbox(target=name) if row["state"] in OPEN_STATES
                and row["delivery"] != "digest" and not row.get("muted_by")]
        items = inbox_items(store, rows) if rows else []
        act = [item for item in items if item["severity_band"] == "act_now"]
        fixes = next((item for item in items if item["type"] == "changes_ready"), None)
        plan = actions.database_summary(store, name, now)
        issues = {row["subject"]: row for row in rows if row["type"] == "monitoring_issue"}
        ran = checks_ran_at(store, name) if enabled else None
        if not enabled:
            state, since = "not_monitored", None
        elif issues.keys() & {"lost_connection", "password_missing"}:
            issue = issues.get("lost_connection") or issues["password_missing"]
            state = "cant_reach"
            since = int(_body(issue).get("failed_since") or issue["created_at"])
        elif (failing := failing_since(store, name)) is not None:
            # The latest check failed: the database is down before the outage is filed.
            state, since = "cant_reach", failing
        elif database_limit.paused(store, name, now) or "jev_login" in issues:
            state, since = "ai_paused", ai_paused_since(store, name, issues)
        else:
            state, since = "watching", None
        latest = store.read("SELECT calls FROM db_series WHERE target=? AND res=60 "
                            "ORDER BY bucket_start DESC LIMIT 1", (name,))
        last_reading, every = collect_timing(store, name) if enabled else (None, 60)
        doing = activity(store, name, now, enabled=enabled, last_reading_at=last_reading,
                         every_s=every, checks_ran_at=ran,
                         check_every_s=levers["attention_every_min"] * 60,
                         running=analyzing.get(name), report=reporting.get(name))
        databases.append({"target": name, "connection": _connection_label(entry), "state": state,
                          "state_since": since,
                          "qps": round(latest[0]["calls"] / 60, 3) if latest and enabled else None,
                          "open_items": len(items), "act_now": len(act),
                          "fixes_ready": plan["fixes_ready"],
                          "fixes_item_id": fixes["id"] if fixes else None,
                          "to_do": plan["to_do"], "do_first": plan["do_first"],
                          "_fixes_share": plan["fixes_share"], "_activity": doing,
                          # Only while its analyses can run: never signed out, with AI off
                          # or deep analysis switched off.
                          "_building": enabled and state != "ai_paused" and
                          bool(levers["deep_analysis"]) and
                          _building_first_plan(store, name, now)})
        if fixes:
            fixes_items[name] = fixes
        if enabled:
            enabled_names.append(name)
            checks.append(ran)
            readings.append(last_reading)
            reports_at.append(_next_report_at(levers, now))
            act_items += act
    order = {"cant_reach": 1, "watching": 2, "ai_paused": 3, "not_monitored": 4}
    databases.sort(key=lambda row: (0 if row["act_now"] else order[row["state"]],
                                    -row["fixes_ready"], row["target"]))
    act_items.sort(key=lambda item: -item["created_at"])
    lead = act_items[0] if act_items else None
    by_name = {row["target"]: row for row in databases}
    unreachable = [name for name in enabled_names if by_name[name]["state"] == "cant_reach"]
    down = selected if selected in unreachable else unreachable[0] if unreachable else None
    target = (lead["target"] if lead else down if down else selected if selected in enabled_names
              else enabled_names[0] if enabled_names else
              next((str(entry["name"]) for entry in entries if entry.get("name")), None))
    ran_at = max((ts for ts in checks if ts), default=None)
    interval = max((store.levers(name)["attention_every_min"] for name in enabled_names),
                   default=5) * 60
    since: int | None = None
    if not enabled_names:
        state = "off"
    elif lead:
        state, since = "needs_you", lead["created_at"]
    elif down:
        state, since = "cant_reach", by_name[down]["state_since"]
    elif (target and by_name[target]["state"] == "ai_paused") or ran_at is None or \
            now - ran_at > QUIET_CYCLES * interval:
        state = "paused"
        since = (by_name[target]["state_since"] if target else None) or ran_at
    else:
        state = "quiet"
        closed = store.read("SELECT MAX(resolved_at) ts FROM inbox_item WHERE target=? "
                            "AND state IN ('resolved','dismissed') AND delivery!='digest'",
                            (target,))
        since = (closed[0]["ts"] if closed and closed[0]["ts"] else None) or \
            store.enabled_at(target) or ran_at
    status = {"state": state, "target": target,
              "needs_you": len(act_items),
              "to_watch": sum(row["open_items"] for row in databases) - len(act_items),
              "headline": home_headline(lead) if lead else None,
              "link": {"kind": "inbox", "target": lead["target"], "label": "Inbox",
                       "item_id": lead["id"]} if lead else None,
              "checks_ran_at": ran_at, "since": since,
              "last_reading_at": max((ts for ts in readings if ts), default=None),
              "next_report_at": min((ts for ts in reports_at if ts), default=None),
              "watching": len(enabled_names),
              "act_now": len(act_items),
              "do_first": sum(row["do_first"] for row in databases
                              if row["target"] in enabled_names)}
    # The day card follows the database the user chose; the sentence keeps the one that
    # needs them most.
    day_target = selected if selected in enabled_names else target
    day = (_day_card(store, day_target, now, midnight, by_name[day_target])
           if day_target in enabled_names else None)
    placeholders = ",".join("?" for _ in enabled_names)
    opened_closed = fixes_confirmed = 0
    if enabled_names:
        opened_closed = store.read(
            f"SELECT COUNT(*) n FROM inbox_item WHERE target IN ({placeholders}) "
            "AND created_at>=? AND state IN ('resolved','dismissed') AND delivery!='digest'",
            (*enabled_names, midnight - 86400))[0]["n"]
        fixes_confirmed = store.read(
            f"SELECT COUNT(*) n FROM inbox_item WHERE target IN ({placeholders}) "
            "AND type='fix_confirmed' AND created_at>=?", (*enabled_names, midnight - 86400))[0]["n"]
    analyzed = []
    names_by_target: dict[str, dict[str, str]] = {}
    if enabled_names:
        for job in store.read(f"SELECT target,query_key,finished_at,analysis_ref FROM analyze_job "
                              f"WHERE target IN ({placeholders}) AND reason='user' AND state='done' "
                              "ORDER BY finished_at DESC,id DESC LIMIT 3", tuple(enabled_names)):
            names = names_by_target.setdefault(job["target"], target_names(store, job["target"]))
            ready = store.read("SELECT fix_ready FROM query_ui WHERE target=? AND query_key=?",
                               (job["target"], job["query_key"]))
            analyzed.append({"target": job["target"], "query_key": job["query_key"],
                             "display_name": names.get(job["query_key"]) or f"Query {job['query_key']}",
                             "analyzed_at": int(job["finished_at"] or 0),
                             "analysis_ref": job["analysis_ref"],
                             "outcome": "fix_suggested" if ready and ready[0]["fix_ready"]
                             else "healthy"})
    return {"status": status, "day": day,
            "databases": [{key: value for key, value in row.items() if not key.startswith("_")}
                          for row in databases],
            "databases_strip": [{"target": row["target"], "state": row["state"],
                                 "act_now": row["act_now"], "to_do": row["to_do"],
                                 "do_first": row["do_first"], "fixes_ready": row["fixes_ready"],
                                 "activity": row["_activity"],
                                 "building": row["_building"]}
                                for row in databases],
            "not_monitored": sum(row["state"] == "not_monitored" for row in databases),
            "today": home_log(store, enabled_names, midnight, limit, fixes_items),
            "since_yesterday": {"opened_closed": int(opened_closed),
                                "fixes_confirmed": int(fixes_confirmed)},
            "recently_analyzed": analyzed, "generated_at": now}

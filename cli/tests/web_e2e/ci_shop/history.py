"""Two days of backdated ci-shop history, judged and reported by the real monitor code.

The history projects the live run's measured latencies back over two days at each shape's
catalogue rate, through the production MonitorStore tables and rollup. It holds:
- a report-group burst 20 hours and 2 hours ago, which the real MonitorLoop and AttentionJudge
  judge at those times (through an injected clock and the real keyservice), so the live burst is
  a third occurrence of the same repeating item;
- a steady query that stopped 32 minutes before the run;
- yesterday as a full day, whose nightly report the real report code builds under a clock set
  just after today's midnight.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from typing import Any

from shared.monitor.monitor_store import MonitorStore, local_day_bounds
from shared.query_registry.query_registry import hash_sql
from tests.web_e2e.ci_shop import catalogue

logger = logging.getLogger(__name__)

HISTORY_DAYS = 2
BURST_AGO_S = (20 * 3600, 2 * 3600)
BURST_MINUTES = 3
STOPPED_AGO_S = 32 * 60
RECOVERY_PASS_MINUTES = (8, 13, 18)
JEV_CAP = 80
HISTORY_BATCH = 20_000

_reports: set[asyncio.Task] = set()
# MySQL keys a statement by its DIGEST_TEXT, not by the text a client sends; the setup fills
# this with each shape's digest text (STATEMENT_DIGEST_TEXT), so a shape the collector hasn't
# seen yet gets the key it will have once it runs. Empty on PostgreSQL, whose collector keys
# the statement text itself.
DIGEST_TEXT: dict[str, str] = {}
# Mean latency, measured on this engine at setup, of shapes the live load hasn't run yet.
MEASURED_MS: dict[str, float] = {}


def template(shape: catalogue.Shape, engine: str) -> str:
    """The statement as the engine's statistics show it: placeholders, never values."""
    if engine == "mysql":
        return shape.sql.replace("%s", "?")
    parts = shape.sql.split("%s")
    return "".join(part + (f"${index}" if index < len(parts) else "")
                   for index, part in enumerate(parts, start=1))


def observed_keys(store: MonitorStore, target: str) -> dict[str, dict[str, Any]]:
    """Catalogue name -> the key and text the collector recorded for it."""
    found: dict[str, dict[str, Any]] = {}
    for row in store.read("SELECT query_key,text,first_seen FROM query_text WHERE target=?",
                          (target,)):
        name = catalogue.shape_for(row["text"])
        if name and name not in found:
            found[name] = dict(row)
    return found


def catalogue_keys(store: MonitorStore, target: str) -> dict[str, dict[str, Any]]:
    """Every catalogue shape with its key: the collector's where it has seen the shape, else the
    registry hash of its text (the identity the collector derives from the same statement)."""
    seen = observed_keys(store, target)
    calls = {row["query_key"]: row["calls"] for row in store.read(
        "SELECT query_key,SUM(calls) calls FROM stmt_series WHERE target=? AND res=60 "
        "GROUP BY query_key", (target,))}
    rng = random.Random(7)
    shapes = {}
    for shape in catalogue.ALL:
        computed = hash_sql(DIGEST_TEXT.get(shape.name) or catalogue.render(shape, rng))
        entry = seen.get(shape.name)
        shapes[shape.name] = {
            "name": shape.name, "role": shape.role, "sql": shape.sql,
            "key": entry["query_key"] if entry else computed,
            "observed": bool(entry), "computed_key": computed,
            "calls": calls.get(entry["query_key"] if entry else computed, 0),
            "regression": shape.name == catalogue.REGRESSION,
            "noise": shape.name == catalogue.NOISE}
    return shapes


def _measured(store: MonitorStore, target: str) -> tuple[dict[str, float], int, dict[str, Any]]:
    rows = store.read("SELECT query_key,SUM(calls) calls,SUM(total_ms) total_ms,"
                      "MIN(bucket_start) first FROM stmt_series WHERE target=? AND res=60 "
                      "GROUP BY query_key", (target,))
    means = {row["query_key"]: row["total_ms"] / row["calls"] for row in rows if row["calls"]}
    firsts = [row["first"] for row in rows]
    db = store.read("SELECT MIN(bucket_start) first,MAX(max_conns) max_conns,"
                    "AVG(active_conns) active FROM db_series WHERE target=? AND res=60",
                    (target,))[0]
    live_start = min([value for value in [*firsts, db["first"]] if value] or [int(time.time())])
    return means, live_start // 60 * 60, db


def write_history(store: MonitorStore, target: str, now: int) -> dict[str, Any]:
    shapes = catalogue_keys(store, target)
    means, live_start, db = _measured(store, target)
    start = local_day_bounds(now)[0] - HISTORY_DAYS * 86400
    bursts = [(live_start - ago) // 60 * 60 for ago in BURST_AGO_S]
    stopped_at = (live_start - STOPPED_AGO_S) // 60 * 60
    rng = random.Random(20260930)
    max_conns = db["max_conns"] or 100
    stmt_rows: list[tuple] = []
    db_rows: list[tuple] = []
    mean_ms = {}
    for shape in catalogue.ALL:
        key = shapes[shape.name]["key"]
        mean_ms[shape.name] = means.get(key) or MEASURED_MS.get(shape.name) or shape.default_ms
    history_shapes = [shape for shape in catalogue.ALL if shape.role != "new_heavy"]
    for minute in range(start, live_start, 60):
        in_burst = any(first <= minute < first + BURST_MINUTES * 60 for first in bursts)
        calls_total, ms_total = 0, 0.0
        for shape in history_shapes:
            if shape.role in ("burst", "burst_only"):
                rate = shape.burst_rate if in_burst else shape.rate
            elif shape.role == "stopped":
                rate = shape.rate if minute < stopped_at else 0.0
            else:
                rate = shape.rate
            calls = round(rate * 60 * rng.uniform(0.9, 1.1))
            if calls <= 0:
                continue
            total = calls * mean_ms[shape.name] * rng.uniform(0.95, 1.05)
            stmt_rows.append((target, shapes[shape.name]["key"], 60, minute, calls, total, calls,
                              calls * 12, 0))
            calls_total += calls
            ms_total += total
        db_rows.append((target, 60, minute, calls_total, ms_total, 6 if in_burst else 4,
                        max_conns, 0.999, calls_total, 0, 0, calls_total * 12, calls_total // 100))
    texts = []
    engine = os.environ.get("RDST_CIM_ENGINE", "postgresql")
    for name in (catalogue.BURST_ONLY.name, catalogue.STOPPED.name):
        shape = catalogue.BY_NAME[name]
        if not shapes[name]["observed"]:
            last = stopped_at if name == catalogue.STOPPED.name else bursts[-1] + BURST_MINUTES * 60
            texts.append((target, shapes[name]["key"], template(shape, engine), start, last))
    observed = [shapes[name]["key"] for name in shapes if shapes[name]["observed"]]

    insert = ("INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,"
              "blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?) "
              "ON CONFLICT(target,query_key,res,bucket_start) DO NOTHING")
    # Short transactions, so the live monitor's own reads and writes keep going meanwhile.
    # Minute rows past the 48-hour retention go in with the rollup that keeps them, since
    # a live collection in between would prune them.
    retained = now - 47 * 3600
    recent = [row for row in stmt_rows if row[3] >= retained]
    oldest = [row for row in stmt_rows if row[3] < retained]
    for first in range(0, len(recent), HISTORY_BATCH):
        batch = recent[first:first + HISTORY_BATCH]
        store._write(lambda conn, batch=batch: conn.executemany(insert, batch))

    def write(conn):
        conn.executemany(insert, oldest)
        conn.executemany("INSERT INTO db_series (target,res,bucket_start,calls,total_ms,"
                         "active_conns,max_conns,cache_hit_ratio,xact_commit,xact_rollback,"
                         "deadlocks,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                         "ON CONFLICT(target,res,bucket_start) DO NOTHING", db_rows)
        conn.executemany("INSERT OR IGNORE INTO query_text VALUES (?,?,?,?,?)", texts)
        conn.executemany("UPDATE query_text SET first_seen=? WHERE target=? AND query_key=?",
                         [(start, target, key) for key in observed])
        # Rebuild every rollup from the minute rows, the backdated ones included.
        for table in ("stmt_series", "db_series"):
            conn.execute(f"DELETE FROM {table} WHERE target=? AND res>60", (target,))
        conn.execute("DELETE FROM dirty_rollup WHERE target=?", (target,))
        conn.execute("DELETE FROM monitor_setting WHERE target=? AND key LIKE 'rollup_until_%'",
                     (target,))
        store._rollup(conn, target, now)

    store._write(write)
    return {"shapes": shapes, "start": start, "live_start": live_start, "bursts": bursts,
            "stopped_at": stopped_at, "minute_rows": len(stmt_rows), "db_rows": len(db_rows),
            "measured": {name: round(mean_ms[name], 3) for name in mean_ms}}


class _IdleSampler:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


def replay_judge(store: MonitorStore, target: str, at: int) -> dict[str, Any]:
    """One real monitor pass (code rules, gate and Jev) as the monitor would have run it `at`."""
    from features.monitor.loop import MonitorLoop

    loop = MonitorLoop(store, clock=lambda: at, sampler=_IdleSampler(),
                       assessments=lambda targets: None)
    loop.cycle(target)
    run = store.read("SELECT id,outcome,response_json FROM judge_run WHERE target=? AND ts=? "
                     "ORDER BY id DESC LIMIT 1", (target, at))
    if not run:
        return {"at": at, "outcome": None}
    response = json.loads(run[0]["response_json"] or "{}")
    return {"at": at, "id": run[0]["id"], "outcome": run[0]["outcome"],
            "event_scope": response.get("event_scope"),
            "queries": [{key: answer.get(key) for key in ("query_key", "kind", "attention")}
                        for answer in response.get("queries", [])]}


def _step_seen(store: MonitorStore, run_id: int, step: str, states: tuple[str, ...]) -> bool:
    return any(event["data"].get("type") == "step" and event["data"].get("step") == step
               and event["data"].get("state") in states for event in _events(store, run_id))


async def run_nightly(store: MonitorStore, target: str, analysis_cap: int) -> dict[str, Any]:
    """Yesterday's nightly report from the real report code, under a clock set just after
    today's midnight that then advances in real time. Returns once the report is finished.

    The report queues Deep Analyze runs of yesterday's heaviest queries, which the queue starts
    ahead of automatic ones. With automatic analysis off meanwhile, only those runs can start.
    Once they are queued, the cap drops to zero, so the report reads their plans with EXPLAIN
    instead of waiting on a day of analyses. The cap and automatic analysis then return, and
    the queued runs go first.
    """
    from features.monitor import report

    now = int(time.time())
    offset = now - (local_day_bounds(now)[0] + 150)
    store.set_settings(target, {"analysis_daily_cap": analysis_cap, "deep_analysis": False})
    task = asyncio.create_task(report.run_report(target, "nightly",
                                                 clock=lambda: time.time() - offset))
    _reports.add(task)
    task.add_done_callback(_reports.discard)
    period_start = local_day_bounds(now - 86400)[0]
    run_id = None
    queued = False
    try:
        deadline = time.time() + 240
        while time.time() < deadline and not task.done():
            if run_id is None:
                rows = store.read("SELECT id FROM report_run WHERE target=? AND "
                                  "trigger='nightly' AND period_start=? ORDER BY id DESC LIMIT 1",
                                  (target, period_start))
                run_id = rows[0]["id"] if rows else None
            if run_id is not None and not queued and _step_seen(store, run_id, "analyses",
                                                                ("running", "done")):
                queued = True
                store.set_settings(target, {"analysis_daily_cap": 0})
            await asyncio.sleep(0.5)
    finally:
        store.set_settings(target, {"analysis_daily_cap": analysis_cap, "deep_analysis": True})
    if not task.done():
        raise RuntimeError(f"The nightly report {run_id} didn't finish in four minutes")
    if task.exception() is not None:
        raise task.exception()
    state = store.read("SELECT state FROM report_run WHERE id=?", (run_id,))[0]["state"]
    jobs = store.read("SELECT COUNT(*) n FROM analyze_job WHERE target=? AND reason='report' "
                      "AND state IN ('queued','running')", (target,))[0]["n"]
    return {"report_id": run_id, "state": state, "report_jobs": jobs,
            "analyses_step_done": _step_seen(store, run_id, "analyses", ("done",)),
            "period_start": period_start}


def _events(store: MonitorStore, run_id: int) -> list[dict[str, Any]]:
    rows = store.read("SELECT data_json FROM report_event WHERE report_id=? ORDER BY seq",
                      (run_id,))
    return [{"data": json.loads(row["data_json"])} for row in rows]


async def inject(target: str, *, analysis_cap: int) -> dict[str, Any]:
    store = MonitorStore()
    now = int(time.time())
    started = time.monotonic()
    written = await asyncio.to_thread(write_history, store, target, now)
    written["write_s"] = round(time.monotonic() - started, 2)
    replays = []
    for first in written["bursts"]:
        # Replays and every later pass share one generous daily ceiling on attention calls.
        os.environ["RDST_MONITOR_JEV_DAILY_CAP"] = str(JEV_CAP)
        replays.append(await asyncio.to_thread(replay_judge, store, target,
                                               first + BURST_MINUTES * 60))
        # The passes after a burst settle its recovery, which code owns: with the attention
        # budget at zero they run the recovery checks without asking Jev again.
        os.environ["RDST_MONITOR_JEV_DAILY_CAP"] = "0"
        for minutes in RECOVERY_PASS_MINUTES:
            await asyncio.to_thread(replay_judge, store, target, first + minutes * 60)
    os.environ["RDST_MONITOR_JEV_DAILY_CAP"] = str(JEV_CAP)
    written["replays"] = replays
    written["burst_items"] = [
        {"id": row["id"], "state": row["state"],
         "occurrences": len(json.loads(row["body_json"]).get("occurrences") or [])}
        for row in store.read("SELECT id,state,body_json FROM inbox_item WHERE target=? "
                              "AND type='many_queries_changed'", (target,))]
    written["nightly"] = await run_nightly(store, target, analysis_cap)
    written["elapsed_s"] = round(time.monotonic() - started, 2)
    return written

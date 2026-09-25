"""Append-only audit log of what the RDST agent did.

One row per agent action (a deep analysis, an Ask, a classification, an attention
check, a daily snapshot, a benchmark), with child rows for every statement RDST ran on
the user's database and every AI call it made. Rows are written once, when the action
finishes, and removed only by retention. Result rows and sampled parameter values are
never stored: statement text keeps its placeholders.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

import shared.constants as shared_constants
from shared.query_registry.observation_store import (
    _apply_journal_mode,
    _check_sqlite_runtime,
    _connection_pragmas,
    _refuse_network_path,
)

SCHEMA_VERSION = 1
RETENTION_SECONDS = 90 * 86400
PRUNE_EVERY_SECONDS = 3600
ACTIONS = ("deep_analysis", "ask", "classification", "attention_check", "daily_snapshot",
           "benchmark")
# How the list folds: frequent, low-stakes actions become one line per database and day (or
# hour). A deep analysis RDST chose not to run folds the same way.
GROUPS = ("day", "hour", "none")
_BUCKET_SECONDS = {"day": 86400, "hour": 3600}
_FOLD = ("CASE WHEN action IN ('attention_check','classification') THEN action "
         "WHEN action='deep_analysis' AND outcome='skipped' THEN 'skipped_analysis' END")
OUTCOMES = ("done", "failed", "skipped", "cancelled")
PAGE_LIMIT_MAX = 200

_SCHEMA = """
CREATE TABLE activity_entry (id INTEGER PRIMARY KEY, ts INTEGER NOT NULL,
 finished_at INTEGER, target TEXT NOT NULL DEFAULT '', action TEXT NOT NULL,
 subject TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
 decision TEXT, details_json TEXT NOT NULL DEFAULT '{}', run_ref TEXT,
 source TEXT NOT NULL DEFAULT 'live' CHECK (source IN ('live','backfill')),
 statement_count INTEGER NOT NULL DEFAULT 0, ai_call_count INTEGER NOT NULL DEFAULT 0,
 tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
 cost_usd REAL NOT NULL DEFAULT 0, items_filed INTEGER NOT NULL DEFAULT 0);
CREATE INDEX activity_entry_ts ON activity_entry(ts, id);
CREATE INDEX activity_entry_target_ts ON activity_entry(target, ts);
CREATE INDEX activity_entry_action_ts ON activity_entry(action, ts);
CREATE UNIQUE INDEX activity_entry_backfill ON activity_entry(run_ref) WHERE source='backfill';
CREATE TABLE activity_statement (id INTEGER PRIMARY KEY,
 entry_id INTEGER NOT NULL REFERENCES activity_entry(id) ON DELETE CASCADE,
 seq INTEGER NOT NULL, sql TEXT NOT NULL, kind TEXT NOT NULL,
 executions INTEGER NOT NULL DEFAULT 1, duration_ms REAL, max_ms REAL, timeout_ms INTEGER,
 error TEXT);
CREATE INDEX activity_statement_entry ON activity_statement(entry_id, seq);
CREATE TABLE activity_ai_call (id INTEGER PRIMARY KEY,
 entry_id INTEGER NOT NULL REFERENCES activity_entry(id) ON DELETE CASCADE,
 seq INTEGER NOT NULL, ts INTEGER, provider TEXT NOT NULL, model TEXT NOT NULL,
 operation TEXT NOT NULL, purpose TEXT NOT NULL, data_kinds TEXT NOT NULL DEFAULT '[]',
 tokens_in INTEGER, tokens_out INTEGER, cost_usd REAL,
 cost_estimated INTEGER NOT NULL DEFAULT 0, duration_ms REAL, request_id TEXT, error TEXT);
CREATE INDEX activity_ai_call_entry ON activity_ai_call(entry_id, seq);
CREATE TABLE activity_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_OPENED: set[Path] = set()
_LAST_PRUNE: dict[Path, float] = {}


def default_agent_activity_path() -> Path:
    return shared_constants.rdst_data_dir() / "agent_activity.db"


def encode_cursor(ts: int, entry_id: int) -> str:
    return f"{int(ts)}.{int(entry_id)}"


def decode_cursor(cursor: str | None) -> tuple[int, int] | None:
    if not cursor:
        return None
    try:
        ts, entry_id = cursor.split(".", 1)
        return int(ts), int(entry_id)
    except ValueError:
        raise ValueError("Invalid cursor") from None


class AgentActivityStore:
    def __init__(self, db_path: Path | None = None, *,
                 clock: Callable[[], float] = time.time,
                 monitor_db_path: Path | None = None) -> None:
        self.path = Path(os.path.realpath(db_path or default_agent_activity_path()))
        self.clock = clock
        # The backfill source: monitor.db beside this file unless a test says otherwise.
        self.monitor_db_path = monitor_db_path or self.path.with_name("monitor.db")
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(self.path, threading.RLock())
        self._opened = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        for pragma in _connection_pragmas():
            conn.execute(pragma)
        return conn

    def ensure_open(self, *, backfill_before: int | None = None) -> None:
        """Create or migrate the file; the first open backfills history older than
        `backfill_before` (default now), so an action being written isn't copied twice."""
        if self._opened or self.path in _OPENED and self.path.exists():
            self._opened = True
            return
        with self._lock:
            if self._opened:
                return
            _check_sqlite_runtime()
            _refuse_network_path(str(self.path))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = self._connect()
            try:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"agent_activity.db schema {version} is newer than this build")
                _apply_journal_mode(conn, self.path)
                if version < SCHEMA_VERSION:
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        if version == 0:
                            for statement in _SCHEMA.split(";"):
                                if statement.strip():
                                    conn.execute(statement)
                        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                        conn.execute("COMMIT")
                    except BaseException:
                        conn.execute("ROLLBACK")
                        raise
            finally:
                conn.close()
            self._opened = True
            _OPENED.add(self.path)
        self._backfill_once(backfill_before)

    def _backfill_once(self, before: int | None) -> None:
        if self.meta("backfilled"):
            return
        from shared.agent_activity.backfill import backfill_rows

        now = int(self.clock())
        try:
            rows = backfill_rows(self.monitor_db_path, now - RETENTION_SECONDS,
                                 now if before is None else before)
        except Exception:
            rows = []

        def write(conn: sqlite3.Connection) -> None:
            if conn.execute("SELECT 1 FROM activity_meta WHERE key='backfilled'").fetchone():
                return
            for entry, statements, ai_calls in rows:
                self._insert(conn, {**entry, "source": "backfill"}, statements, ai_calls)
            conn.execute("INSERT INTO activity_meta (key,value) VALUES ('backfilled',?)",
                         (str(now),))

        self._write(write)

    def meta(self, key: str) -> str | None:
        rows = self.read("SELECT value FROM activity_meta WHERE key=?", (key,))
        return rows[0]["value"] if rows else None

    def read(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.ensure_open()
        conn = self._connect()
        try:
            return [dict(row) for row in conn.execute(sql, params)]
        finally:
            conn.close()

    def _write(self, action: Callable[[sqlite3.Connection], Any]) -> Any:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                result = action(conn)
                conn.execute("COMMIT")
                return result
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    @staticmethod
    def _insert(conn: sqlite3.Connection, entry: dict[str, Any],
                statements: list[dict[str, Any]], ai_calls: list[dict[str, Any]]) -> int | None:
        tokens_in = sum(int(call.get("tokens_in") or 0) for call in ai_calls)
        tokens_out = sum(int(call.get("tokens_out") or 0) for call in ai_calls)
        cost = sum(float(call.get("cost_usd") or 0) for call in ai_calls)
        details = dict(entry.get("details") or {})
        cursor = conn.execute(
            "INSERT OR IGNORE INTO activity_entry (ts,finished_at,target,action,subject,outcome,"
            "summary,decision,details_json,run_ref,source,statement_count,ai_call_count,"
            "tokens_in,tokens_out,cost_usd,items_filed) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(entry["ts"]), entry.get("finished_at"), entry.get("target") or "",
             entry["action"], entry.get("subject") or "", entry.get("outcome") or "done",
             entry.get("summary") or "", entry.get("decision"),
             json.dumps(details, default=str, sort_keys=True), entry.get("run_ref"),
             entry.get("source") or "live",
             int(entry.get("statement_count",
                           sum(int(s.get("executions") or 1) for s in statements))),
             int(entry.get("ai_call_count", len(ai_calls))), tokens_in, tokens_out, cost,
             int(entry.get("items_filed") or 0)))
        if not cursor.rowcount:
            return None
        entry_id = int(cursor.lastrowid)
        conn.executemany(
            "INSERT INTO activity_statement (entry_id,seq,sql,kind,executions,duration_ms,"
            "max_ms,timeout_ms,error) VALUES (?,?,?,?,?,?,?,?,?)",
            [(entry_id, seq, s["sql"], s.get("kind") or "read", int(s.get("executions") or 1),
              s.get("duration_ms"), s.get("max_ms"), s.get("timeout_ms"), s.get("error"))
             for seq, s in enumerate(statements)])
        conn.executemany(
            "INSERT INTO activity_ai_call (entry_id,seq,ts,provider,model,operation,purpose,"
            "data_kinds,tokens_in,tokens_out,cost_usd,cost_estimated,duration_ms,request_id,"
            "error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(entry_id, seq, c.get("ts"), c.get("provider") or "unknown",
              c.get("model") or "unknown", c.get("operation") or "other",
              c.get("purpose") or "other", json.dumps(list(c.get("data_kinds") or [])),
              c.get("tokens_in"), c.get("tokens_out"), c.get("cost_usd"),
              int(bool(c.get("cost_estimated"))), c.get("duration_ms"), c.get("request_id"),
              c.get("error"))
             for seq, c in enumerate(ai_calls)])
        return entry_id

    def append(self, entry: dict[str, Any], statements: list[dict[str, Any]] | None = None,
               ai_calls: list[dict[str, Any]] | None = None) -> int | None:
        """Write one finished action. Entries are never updated afterwards."""
        if entry.get("action") not in ACTIONS:
            raise ValueError(f"Unknown agent action: {entry.get('action')}")
        self.ensure_open(backfill_before=int(entry["ts"]))
        now = self.clock()
        prune = now - _LAST_PRUNE.get(self.path, 0) >= PRUNE_EVERY_SECONDS

        def write(conn: sqlite3.Connection) -> int | None:
            entry_id = self._insert(conn, entry, statements or [], ai_calls or [])
            if prune:
                self._prune(conn, int(now))
            return entry_id

        entry_id = self._write(write)
        if prune:
            _LAST_PRUNE[self.path] = now
        return entry_id

    @staticmethod
    def _prune(conn: sqlite3.Connection, now: int) -> int:
        cutoff = now - RETENTION_SECONDS
        old = "SELECT id FROM activity_entry WHERE ts<?"
        conn.execute(f"DELETE FROM activity_statement WHERE entry_id IN ({old})", (cutoff,))
        conn.execute(f"DELETE FROM activity_ai_call WHERE entry_id IN ({old})", (cutoff,))
        return conn.execute("DELETE FROM activity_entry WHERE ts<?", (cutoff,)).rowcount

    def prune(self) -> int:
        self.ensure_open()
        return self._write(lambda conn: self._prune(conn, int(self.clock())))

    @staticmethod
    def _filters(target: str | None, action: str | None, since: int | None,
                 until: int | None, outcome: str | None = None) -> tuple[str, list[Any]]:
        clauses, params = ["1=1"], []
        for column, value in (("target", target), ("action", action), ("outcome", outcome)):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        if since is not None:
            clauses.append("ts>=?")
            params.append(int(since))
        if until is not None:
            clauses.append("ts<?")
            params.append(int(until))
        return " AND ".join(clauses), params

    def page(self, *, target: str | None = None, action: str | None = None,
             outcome: str | None = None, since: int | None = None, until: int | None = None,
             cursor: str | None = None, limit: int = 50, group: str = "day",
             utc_offset: int = 0) -> dict[str, Any]:
        """Newest first. With `group` 'day' or 'hour', attention checks, classifications
        and skipped deep analyses fold into one line per database and local day or hour
        (`utc_offset` minutes east of UTC); a folded line is never split across pages.
        'none' lists every entry."""
        if group not in GROUPS:
            raise ValueError(f"Unknown group: {group}")
        limit = max(1, min(int(limit), PAGE_LIMIT_MAX))
        offset = int(utc_offset) * 60
        size = _BUCKET_SECONDS.get(group, 86400)
        position = decode_cursor(cursor)
        # Nothing newer than the bucket holding the cursor can belong on this page.
        top = until
        if position is not None:
            bucket_end = ((position[0] + offset) // size + 1) * size - offset
            top = bucket_end if top is None else min(top, bucket_end)
        bounds = self.read("SELECT MIN(ts) AS oldest, MAX(ts) AS newest FROM activity_entry")[0]
        if bounds["oldest"] is None:
            return {"items": [], "next_cursor": None}
        floor = max(int(since), bounds["oldest"]) if since is not None else bounds["oldest"]
        # Read a window of whole buckets back from the top, widening it until it holds a
        # full page, so a long log never folds more than the page needs.
        newest = top if top is not None else bounds["newest"] + 1
        span = 2 * 86400
        while True:
            low = ((newest - span + offset) // size) * size - offset
            rows = self._page_rows(target, action, outcome, max(low, floor), top, position,
                                   limit, group, size, offset)
            if len(rows) > limit or low <= floor:
                break
            span *= 4
        more = len(rows) > limit
        rows = rows[:limit]
        items = [self._list_item(row, group, size, offset) for row in rows]
        self._fill_report_periods(items)
        next_cursor = encode_cursor(rows[-1]["ts"], rows[-1]["id"]) if more and rows else None
        return {"items": items, "next_cursor": next_cursor}

    def _page_rows(self, target: str | None, action: str | None, outcome: str | None,
                   since: int | None, until: int | None, position: tuple[int, int] | None,
                   limit: int, group: str, size: int, offset: int) -> list[dict[str, Any]]:
        where, params = self._filters(target, action, since, until, outcome)
        fold = _FOLD if group != "none" else "NULL"
        base = f"SELECT *, {fold} AS fold FROM activity_entry WHERE {where}"
        before, before_params = "", []
        if position is not None:
            before = "AND (ts<? OR (ts=? AND id<?))"
            before_params = [position[0], position[0], position[1]]
        sums = ("SUM(statement_count) AS statement_count, SUM(ai_call_count) AS ai_call_count, "
                "SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out, "
                "SUM(cost_usd) AS cost_usd, SUM(items_filed) AS items_filed")
        single = (
            "SELECT 'entry' AS kind, id, ts, ts AS first_ts, 1 AS count, "
            "outcome='done' AS done, outcome='failed' AS failed, outcome='skipped' AS skipped, "
            "NULL AS fold, NULL AS bucket, target, action, subject, outcome, summary, source, "
            "statement_count, ai_call_count, tokens_in, tokens_out, cost_usd, items_filed, "
            f"decision, details_json FROM ({base}) WHERE fold IS NULL {before}")
        having = ("HAVING MAX(ts)<? OR (MAX(ts)=? AND MAX(id)<?)" if position is not None
                  else "")
        folded = (
            "SELECT 'rollup' AS kind, MAX(id) AS id, MAX(ts) AS ts, MIN(ts) AS first_ts, "
            "COUNT(*) AS count, SUM(outcome='done') AS done, SUM(outcome='failed') AS failed, "
            "SUM(outcome='skipped') AS skipped, fold, (ts+?)/? AS bucket, target, "
            "MIN(action) AS action, "
            "CASE WHEN COUNT(*)=1 THEN MAX(subject) ELSE '' END AS subject, "
            "CASE WHEN SUM(outcome='failed')=COUNT(*) THEN 'failed' "
            "WHEN SUM(outcome='done') THEN 'done' ELSE MIN(outcome) END AS outcome, "
            "CASE WHEN COUNT(*)=1 THEN MAX(summary) ELSE '' END AS summary, "
            f"MIN(source) AS source, {sums}, NULL AS decision, NULL AS details_json "
            f"FROM ({base}) WHERE fold IS NOT NULL GROUP BY target, fold, bucket {having}")
        return self.read(
            f"SELECT * FROM ({single} UNION ALL {folded}) ORDER BY ts DESC, id DESC LIMIT ?",
            tuple(params + before_params + [offset, size] + params + before_params
                  + [limit + 1]))

    def _fill_report_periods(self, items: list[dict[str, Any]]) -> None:
        """The day each daily report covers, from monitor.db for entries written before
        the period was recorded with them."""
        missing = {item["report_id"]: item for item in items
                   if item["action"] == "daily_snapshot" and item.get("period_start") is None
                   and isinstance(item.get("report_id"), int)}
        if not missing or not self.monitor_db_path.exists():
            return
        try:
            conn = sqlite3.connect(f"file:{self.monitor_db_path}?mode=ro", uri=True)
            try:
                marks = ",".join("?" for _ in missing)
                rows = conn.execute(
                    f"SELECT id, period_start, period_end FROM report_run WHERE id IN ({marks}) "
                    "AND period_start IS NOT NULL AND period_end IS NOT NULL",
                    tuple(missing)).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return
        for report_id, start, end in rows:
            missing[report_id].update({"period_start": start, "period_end": end})

    @staticmethod
    def _list_item(row: dict[str, Any], group: str, size: int, offset: int) -> dict[str, Any]:
        item = dict(row)
        details = json.loads(item.pop("details_json") or "{}")
        bucket = item.pop("bucket")
        if item["kind"] == "rollup":
            start = int(bucket) * size - offset
            item.update({"group": group, "bucket_start": start, "bucket_end": start + size,
                         "key": f"{item['fold']}:{item['target']}:{start}"})
            return item
        item["key"] = f"entry:{item['id']}"
        for name in ("question", "query_key", "analysis_ref", "report_id", "period_start",
                     "period_end", "parameters", "note"):
            if details.get(name) is not None:
                item[name] = details[name]
        if item["action"] == "deep_analysis" and item["outcome"] == "done":
            from shared.agent_activity.findings import finding_for_ref

            item["finding"] = finding_for_ref(details.get("analysis_ref"))
        return item

    def targets(self) -> list[str]:
        return [row["target"] for row in self.read(
            "SELECT DISTINCT target FROM activity_entry WHERE target<>'' ORDER BY target")]

    def entry(self, entry_id: int) -> dict[str, Any] | None:
        rows = self.read("SELECT * FROM activity_entry WHERE id=?", (int(entry_id),))
        if not rows:
            return None
        entry = rows[0]
        entry["details"] = json.loads(entry.pop("details_json") or "{}")
        entry["statements"] = self.read(
            "SELECT sql,kind,executions,duration_ms,max_ms,timeout_ms,error "
            "FROM activity_statement WHERE entry_id=? ORDER BY seq", (int(entry_id),))
        calls = self.read(
            "SELECT ts,provider,model,operation,purpose,data_kinds,tokens_in,tokens_out,cost_usd,"
            "cost_estimated,duration_ms,request_id,error FROM activity_ai_call "
            "WHERE entry_id=? ORDER BY seq", (int(entry_id),))
        for call in calls:
            call["data_kinds"] = json.loads(call["data_kinds"] or "[]")
            call["cost_estimated"] = bool(call["cost_estimated"])
        entry["ai_calls"] = calls
        return entry

    def benchmarks_for_query(self, target: str, query_hash: str,
                             limit: int = 20) -> list[dict[str, Any]]:
        """Benchmarks that measured this query on this database, newest first: the run's
        settings plus this query's own numbers."""
        rows = self.read(
            "SELECT id, ts, outcome, details_json FROM activity_entry "
            "WHERE action='benchmark' AND target=? AND EXISTS (SELECT 1 FROM "
            "json_each(activity_entry.details_json, '$.queries') AS q "
            "WHERE json_extract(q.value, '$.hash')=?) ORDER BY ts DESC, id DESC LIMIT ?",
            (target, query_hash, max(1, min(int(limit), PAGE_LIMIT_MAX))))
        runs = []
        for row in rows:
            details = json.loads(row.pop("details_json") or "{}")
            query = next(item for item in details.get("queries") or []
                         if item.get("hash") == query_hash)
            runs.append({**row, **{key: details.get(key) for key in (
                "mode", "lanes", "duration_seconds", "concurrency", "interval_ms",
                "elapsed_seconds", "readyset_setup")}, "query": query})
        return runs

    def export_rows(self, *, target: str | None = None, action: str | None = None,
                    since: int | None = None, until: int | None = None,
                    limit: int = 100_000) -> list[dict[str, Any]]:
        """Every entry in [since, until), newest first, with its question or finding."""
        where, params = self._filters(target, action, since, until)
        rows = self.read(
            "SELECT id,ts,target,action,subject,outcome,summary,decision,source,"
            "statement_count,ai_call_count,tokens_in,tokens_out,cost_usd,items_filed,"
            f"details_json FROM activity_entry WHERE {where} ORDER BY ts DESC, id DESC LIMIT ?",
            tuple(params + [int(limit)]))
        from shared.agent_activity.findings import finding_for_ref

        for row in rows:
            details = json.loads(row.pop("details_json") or "{}")
            row["question"] = details.get("question")
            row["query_key"] = details.get("query_key")
            row["finding"] = (finding_for_ref(details.get("analysis_ref"))
                              if row["action"] == "deep_analysis" and row["outcome"] == "done"
                              else None)
        return rows

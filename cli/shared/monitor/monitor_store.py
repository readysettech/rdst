"""Precious local history for continuous monitoring."""

from __future__ import annotations

import heapq
import json
import math
import os
import random
import re
import socket
import sqlite3
import threading
import time
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

import psutil

import shared.constants as shared_constants
from shared import startup_status
from shared.analysis_lane import PLAN_ANALYSIS_CONCURRENCY
from shared.persistence import replace_with_retry
from shared.persistence import staging_file as _staging_file
from shared.query_registry.observation_store import (
    StorageError,
    _apply_journal_mode,
    _check_journal_headers,
    _check_sqlite_runtime,
    _connection_pragmas,
    _refuse_network_path,
    _storage_guard,
)

# Each collector process establishes a fresh rate baseline against persisted history.
COLLECTOR_PROCESS = uuid.uuid4().hex
OWNER_HOST = socket.gethostname()


# Allow five seconds of scheduling jitter around the normal one-minute cadence.
COLLECTION_GAP_SECONDS = 65
SCHEMA_VERSION = 32
SAMPLES_PER_QUERY = 5
RESOLUTIONS = (60, 300, 3600, 86400)
# Full detail follows the same retention for every query in the first tier.
RETENTION = {60: 48 * 3600, 300: 7 * 86400, 3600: 90 * 86400, 86400: 2 * 365 * 86400}
FINE_RETENTION_SECONDS = RETENTION[60]
FULL_DETAIL_QUERIES = 1000
SERIES_QUERIES = 10_000
RECENT_LOAD_SECONDS = 86400
TAIL_RETENTION = {300: 48 * 3600, 3600: 30 * 86400}
EVERYTHING_ELSE = '__everything_else__'
INCIDENT_RETENTION_SECONDS = 30 * 86400
INCIDENT_PAD_SECONDS = 3600
# Incident onsets are measured within three hours before filing; keep that evidence too.
INCIDENT_LOOKBACK_SECONDS = 3 * 3600
REPORT_BODY_RETENTION_SECONDS = 90 * 86400
DEFAULT_INSTALL_BUDGET_BYTES = 1024 * 1024 * 1024
EVENT_RETENTION_SECONDS = 30 * 86400
DEFAULT_BUDGET_BYTES = 250 * 1024 * 1024
PULSE_RETENTION_SECONDS = 48 * 3600
# In the rollback journal a commit waits for every reader, and new readers wait for that
# commit. A writer that cannot start or commit within WRITE_BUSY_MS rolls back, so readers
# go ahead, and runs its transaction again until its retry window closes.
WRITE_BUSY_MS = 1000
WRITE_RETRY_S = 10.0
# Reads wait out a long writer for about as long as a write does before giving up.
READ_RETRY_S = 10.0
# A collector minute keeps retrying for most of the minute before the collector requeues it.
RECORD_RETRY_S = 45.0
# Budget maintenance prunes in short transactions of at most this many rows, pausing between
# them so collectors and readers take the lock.
MAINTENANCE_BATCH_ROWS = 500
SAMPLE_BATCH_ROWS = 100
MAINTENANCE_PAUSE_S = 0.02
VACUUM_BATCH_PAGES = 256
# Per-target levers. Environment variables keep their meaning as process-wide defaults;
# the fallbacks suit a free tier.
BOOL_SETTINGS = {"enabled": False, "deep_analysis": False, "execute": False,
                 "auto_analyze_new": True, "nightly_report": True}
ATTENTION_INTERVALS_MIN = (5, 15, 60)
DEFAULT_ANALYSIS_DAILY_CAP = 15
DEFAULT_NEWCOMER_DAILY_CAP = 100
DEFAULT_ATTENTION_EVERY_MIN = 5
DEFAULT_NIGHTLY_HOUR = 0
DEFAULT_PUSH_DAILY_CAP = 3
# Dev/demo lever: a per-database learning period in seconds, overriding RDST_MONITOR_LEARNING_S.
DEV_LEARNING_PERIOD_MAX_S = 86400
# Delivery levels, lowest first (NOTIFICATION_MODEL.md): digest items only reach the daily
# report, inbox items are listed without a push, notify items also push.
DELIVERY_LEVELS = ("digest", "inbox", "notify")
# Types the inbox no longer carries: per-query index attribution and report-only findings.
# A confirmed fix is filed again as one closed item per applied action (actions.verify).
RETIRED_TYPES = ("fix_available", "fix_no_effect", "one_fix_several_queries")
# What notifies, per database (`notify_events` setting). An event that's off still files its
# item; it just never pushes. Database unreachable never counts toward or waits on the daily cap.
NOTIFY_EVENTS = {"database_unreachable": True, "database_recovered": False,
                 "monitoring_setup": True, "stuck_query": True, "database_events": True,
                 "query_changes": True, "daily_report": True}
# Pushes are held between these local hours (`quiet_hours` setting); the Inbox still fills.
# Equal hours mean no quiet hours. Database unreachable is never held.
DEFAULT_QUIET_HOURS = {"from_hour": 23, "to_hour": 7}
# Minute-detail readings (the 5-minute rollup) are kept this many days unless the
# `keep_readings_days` preference says otherwise.
DEFAULT_KEEP_READINGS_DAYS = 7
KEEP_READINGS_CHOICES = (7, 30, 90)
DATABASE_EVENT_TYPES = ("connections_near_max", "lock_contention", "rollback_or_deadlock_spike",
                        "cache_hit_ratio_drop")
UNCAPPED_PUSH_SQL = "NOT (i.type='monitoring_issue' AND i.subject='lost_connection')"
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
# Files this process has opened: checked, migrated and set to its journal mode.
_OPENED: set[Path] = set()
_STATEMENT_GUARD = threading.local()
_DUPLICATE_COLUMN = re.compile(r"(.+):\d+")


def _process_start(pid: int) -> float | None:
    try:
        process = psutil.Process(pid)
        return None if process.status() == psutil.STATUS_ZOMBIE else process.create_time()
    except psutil.NoSuchProcess:
        return None


def _dead_analysis_owner(row: Any) -> bool:
    if row["owner_host"] != OWNER_HOST or row["owner_pid"] is None or row["owner_start"] is None:
        return False
    try:
        return _process_start(row["owner_pid"]) != row["owner_start"]
    except psutil.AccessDenied:
        return False



def is_locked(exc: BaseException) -> bool:
    """Whether a SQLite error is lock contention that a later attempt can clear."""
    return isinstance(exc, sqlite3.OperationalError) and (
        "locked" in str(exc) or "busy" in str(exc))


@contextmanager
def guard_statements(guard: Any):
    previous = getattr(_STATEMENT_GUARD, "value", None)
    _STATEMENT_GUARD.value = guard
    try:
        yield
    finally:
        _STATEMENT_GUARD.value = previous


class _GuardedConnection:
    def __init__(self, conn: sqlite3.Connection, guard: Any) -> None:
        self._conn = conn
        self._guard = guard

    def execute(self, *args: Any) -> sqlite3.Cursor:
        self._guard.check()
        return self._conn.execute(*args)

    def executemany(self, *args: Any) -> sqlite3.Cursor:
        self._guard.check()
        return self._conn.executemany(*args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

_SCHEMA = """
CREATE TABLE query_text (target TEXT NOT NULL, query_key TEXT NOT NULL, text TEXT NOT NULL,
 first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,
 PRIMARY KEY (target, query_key));
CREATE TABLE stmt_series (target TEXT NOT NULL, query_key TEXT NOT NULL, res INTEGER NOT NULL
 CHECK (res IN (60,300,3600,86400)), bucket_start INTEGER NOT NULL,
 calls INTEGER NOT NULL, total_ms REAL NOT NULL, rows INTEGER NOT NULL,
 blks_hit INTEGER NOT NULL, blks_read INTEGER NOT NULL,
 PRIMARY KEY (target,query_key,res,bucket_start));
CREATE INDEX stmt_series_window ON stmt_series(target,res,bucket_start);
CREATE TABLE db_series (target TEXT NOT NULL, res INTEGER NOT NULL
 CHECK (res IN (60,300,3600,86400)), bucket_start INTEGER NOT NULL,
 calls INTEGER NOT NULL, total_ms REAL NOT NULL, active_conns INTEGER,
 max_conns INTEGER, cache_hit_ratio REAL, xact_commit INTEGER,
 xact_rollback INTEGER, deadlocks INTEGER,
 PRIMARY KEY (target,res,bucket_start));
CREATE TABLE stuck_sample (target TEXT NOT NULL, ts INTEGER NOT NULL, pid INTEGER NOT NULL,
 query_key TEXT, state TEXT, duration_s REAL NOT NULL,
 PRIMARY KEY (target,ts,pid));
CREATE TABLE index_snapshot (target TEXT NOT NULL, schema TEXT NOT NULL, "table" TEXT NOT NULL,
 index_name TEXT NOT NULL, definition TEXT NOT NULL, captured_at INTEGER NOT NULL,
 PRIMARY KEY (target,schema,"table",index_name));
CREATE TABLE index_change (id INTEGER PRIMARY KEY, target TEXT NOT NULL, ts INTEGER NOT NULL,
 kind TEXT NOT NULL CHECK (kind IN ('added','dropped')), schema TEXT NOT NULL,
 "table" TEXT NOT NULL, index_name TEXT NOT NULL, definition TEXT NOT NULL);
CREATE INDEX index_change_target_ts ON index_change(target,ts);
CREATE TABLE inbox_item (id INTEGER PRIMARY KEY, target TEXT NOT NULL, type TEXT NOT NULL,
 subject TEXT NOT NULL, state TEXT NOT NULL
 CHECK (state IN ('new','seen','snoozed','dismissed','resolved')),
 severity REAL NOT NULL, title TEXT NOT NULL, body_json TEXT NOT NULL,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, resolved_at INTEGER,
 resolved_by TEXT CHECK (resolved_by IN ('user','code','jev')),
 seen_count INTEGER NOT NULL DEFAULT 0, dismissed_at INTEGER,
 reopened_count INTEGER NOT NULL DEFAULT 0, UNIQUE (target,type,subject));
CREATE TABLE inbox_event (id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES inbox_item(id),
 ts INTEGER NOT NULL, kind TEXT NOT NULL
 CHECK (kind IN ('created','updated','worse','reopened','resolved','dismissed','notified')),
 detail_json TEXT NOT NULL);
CREATE TABLE judge_run (id INTEGER PRIMARY KEY, target TEXT NOT NULL, ts INTEGER NOT NULL,
 shortlist_json TEXT NOT NULL, request_json TEXT, response_json TEXT,
 dry_run INTEGER NOT NULL, outcome TEXT NOT NULL);
CREATE TABLE analyze_job (id INTEGER PRIMARY KEY, target TEXT NOT NULL, query_key TEXT NOT NULL,
 reason TEXT NOT NULL CHECK (reason IN ('new','top15_stale','user','report')),
 state TEXT NOT NULL CHECK (state IN ('queued','running','done','failed','skipped')),
 priority REAL NOT NULL, param_source TEXT CHECK (param_source IN ('sampled','table_sampled','none','estimated')),
 mode TEXT CHECK (mode IN ('analyze','explain')), enqueued_at INTEGER NOT NULL,
 started_at INTEGER, finished_at INTEGER, error TEXT, analysis_ref TEXT);
CREATE TABLE report_run (id INTEGER PRIMARY KEY, target TEXT NOT NULL, ts INTEGER NOT NULL,
 trigger TEXT NOT NULL CHECK (trigger IN ('nightly','user')), state TEXT NOT NULL,
 top_json TEXT, report_json TEXT, provisional INTEGER NOT NULL DEFAULT 0);
CREATE TABLE monitor_setting (target TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
 PRIMARY KEY (target,key));
CREATE TABLE attention_episode (target TEXT NOT NULL, query_key TEXT NOT NULL,
 kind TEXT NOT NULL, baseline_mean_ms REAL NOT NULL, settled_at INTEGER NOT NULL,
 judged_qps REAL NOT NULL DEFAULT 0,
 PRIMARY KEY (target,query_key,kind));
CREATE TABLE dirty_rollup (target TEXT NOT NULL, source_res INTEGER NOT NULL,
 bucket_start INTEGER NOT NULL, PRIMARY KEY (target,source_res,bucket_start));
"""

_SCHEMA_V14 = """
CREATE TABLE IF NOT EXISTS mute_rule (id INTEGER PRIMARY KEY, target TEXT, query_key TEXT,
 type TEXT, until INTEGER, created_at INTEGER NOT NULL, note TEXT,
 CHECK (query_key IS NOT NULL OR type IS NOT NULL));
CREATE TABLE IF NOT EXISTS fix_verdict (target TEXT NOT NULL, query_key TEXT NOT NULL,
 index_change_id INTEGER NOT NULL, index_name TEXT NOT NULL, "table" TEXT NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('measuring','confirmed','no_effect','regressed','reverted')),
 detected_at INTEGER NOT NULL, before_mean_ms REAL NOT NULL, before_qps REAL NOT NULL,
 after_mean_ms REAL, after_qps REAL, after_calls INTEGER NOT NULL DEFAULT 0,
 updated_at INTEGER NOT NULL, PRIMARY KEY (target,query_key,index_change_id));
CREATE TABLE IF NOT EXISTS monitor_pulse (id INTEGER PRIMARY KEY, target TEXT NOT NULL,
 ts INTEGER NOT NULL, kind TEXT NOT NULL
 CHECK (kind IN ('collect','index_scan','attention','analyze','report')),
 ok INTEGER NOT NULL, detail TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS monitor_pulse_target ON monitor_pulse(target,id);
CREATE TABLE IF NOT EXISTS report_event (report_id INTEGER NOT NULL, seq INTEGER NOT NULL,
 ts INTEGER NOT NULL, data_json TEXT NOT NULL, PRIMARY KEY (report_id,seq));
CREATE TABLE IF NOT EXISTS system_query (target TEXT NOT NULL, query_key TEXT NOT NULL,
 PRIMARY KEY (target,query_key))
"""


# Table, index and setting statistics: the latest cumulative counters, hourly activity deltas
# between readings, and the settings an action can name. Actions: what to change, one row each.
_SCHEMA_V28 = """
CREATE TABLE IF NOT EXISTS stats_meta (target TEXT PRIMARY KEY, engine TEXT NOT NULL,
 counter_since INTEGER NOT NULL, observed_since INTEGER NOT NULL, taken_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS table_state (target TEXT NOT NULL, schema TEXT NOT NULL,
 "table" TEXT NOT NULL, ins INTEGER NOT NULL, upd INTEGER NOT NULL, del INTEGER NOT NULL,
 hot_upd INTEGER NOT NULL, seq_scan INTEGER NOT NULL, idx_scan INTEGER NOT NULL,
 scan_rows INTEGER NOT NULL, live_rows INTEGER NOT NULL, dead_rows INTEGER NOT NULL,
 size_bytes INTEGER NOT NULL, updated_at INTEGER NOT NULL, vacuumed_at INTEGER,
 analyzed_at INTEGER, PRIMARY KEY (target,schema,"table"));
CREATE TABLE IF NOT EXISTS table_activity (target TEXT NOT NULL, schema TEXT NOT NULL,
 "table" TEXT NOT NULL, bucket_start INTEGER NOT NULL, ins INTEGER NOT NULL, upd INTEGER NOT NULL,
 del INTEGER NOT NULL, hot_upd INTEGER NOT NULL, seq_scan INTEGER NOT NULL,
 idx_scan INTEGER NOT NULL, scan_rows INTEGER NOT NULL, seconds INTEGER NOT NULL,
 PRIMARY KEY (target,schema,"table",bucket_start));
CREATE TABLE IF NOT EXISTS index_state (target TEXT NOT NULL, schema TEXT NOT NULL,
 "table" TEXT NOT NULL, index_name TEXT NOT NULL, scans INTEGER NOT NULL,
 size_bytes INTEGER NOT NULL, is_unique INTEGER NOT NULL, is_primary INTEGER NOT NULL,
 last_scan_at INTEGER, first_seen INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 PRIMARY KEY (target,schema,"table",index_name));
CREATE TABLE IF NOT EXISTS index_activity (target TEXT NOT NULL, schema TEXT NOT NULL,
 "table" TEXT NOT NULL, index_name TEXT NOT NULL, bucket_start INTEGER NOT NULL,
 scans INTEGER NOT NULL, PRIMARY KEY (target,schema,"table",index_name,bucket_start));
CREATE TABLE IF NOT EXISTS setting_state (target TEXT NOT NULL, name TEXT NOT NULL,
 value TEXT NOT NULL, unit TEXT, pending_restart INTEGER NOT NULL, changed_at INTEGER,
 read_at INTEGER NOT NULL, PRIMARY KEY (target,name));
CREATE TABLE IF NOT EXISTS setting_change (id INTEGER PRIMARY KEY, target TEXT NOT NULL,
 ts INTEGER NOT NULL, name TEXT NOT NULL, old_value TEXT NOT NULL, new_value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS setting_change_target_ts ON setting_change(target,ts);
CREATE TABLE IF NOT EXISTS action (id INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT NOT NULL,
 key TEXT NOT NULL, type TEXT NOT NULL CHECK (type IN ('add_index','drop_index','rewrite_query',
 'change_setting','run_maintenance','try_caching')),
 state TEXT NOT NULL CHECK (state IN ('open','snoozed','dismissed','applied','confirmed',
 'no_effect','retired','undone')),
 score REAL NOT NULL, payload_json TEXT NOT NULL, why TEXT, opened_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL, closed_at INTEGER, closed_reason TEXT, snoozed_until INTEGER,
 dismiss_reason TEXT, UNIQUE (target,key));
CREATE TABLE IF NOT EXISTS action_event (id INTEGER PRIMARY KEY AUTOINCREMENT,
 action_id INTEGER NOT NULL REFERENCES action(id), ts INTEGER NOT NULL, kind TEXT NOT NULL,
 detail_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS action_event_action ON action_event(action_id,id);
CREATE TABLE IF NOT EXISTS index_plan (target TEXT PRIMARY KEY, computed_at INTEGER NOT NULL,
 plan_json TEXT NOT NULL)
"""
STATS_ACTIVITY_RETENTION_SECONDS = 8 * 86400

def _tail_period_sql(alias: str, *, span: int | None = None) -> str:
    begin = f"{alias}.bucket_start" if span is None else f"{alias}.bucket_start/{span}*{span}"
    width = f"{alias}.res" if span is None else str(span)
    return (f"EXISTS (SELECT 1 FROM query_tier_period p WHERE p.target={alias}.target "
            f"AND p.query_key={alias}.query_key AND p.tier=2 "
            f"AND p.start<{begin}+{width} "
            f"AND (p.end IS NULL OR p.end>{begin})) AND NOT EXISTS "
            f"(SELECT 1 FROM query_tier_period p WHERE p.target={alias}.target "
            f"AND p.query_key={alias}.query_key AND p.tier=1 "
            f"AND p.start<{begin}+{width} "
            f"AND (p.end IS NULL OR p.end>{begin}))")


def default_monitor_db_path() -> Path:
    return shared_constants.rdst_data_dir() / "monitor.db"


def _env_int(name: str) -> int | None:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return None


def local_day_bounds(now: int) -> tuple[int, int]:
    """The machine's local calendar day holding `now`, the day the nightly report uses."""
    start = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    end = datetime.combine(start.date() + timedelta(days=1), datetime.min.time())
    return int(start.timestamp()), int(end.timestamp())


# The order claim_analysis starts queued jobs in; queue positions follow it too.
ANALYSIS_CLAIM_ORDER = ("CASE reason WHEN 'user' THEN 0 WHEN 'report' THEN 1 ELSE 2 END,"
                        "priority DESC,enqueued_at,id")


# Automatic analyses spend one of two daily allowances: queries entering the top N for the
# first time spend the newcomer allowance; re-analyses and report analyses spend the
# analysis_daily_cap allowance.
NEWCOMER_REASONS = ("new",)
REANALYSIS_REASONS = ("top15_stale", "report")


# A started automatic analysis spends the daily cap unless it was skipped or failed
# without a usable AI answer: before any AI call (a timed-out run still ran the query),
# or with an answer the analyzer couldn't parse.
ANALYSIS_SPENDS_CAP = (
    "reason!='user' AND NOT (state='skipped' OR state='failed' AND ((COALESCE(billable_calls,0)=0 "
    "AND lower(COALESCE(error,'')) NOT LIKE '%timed out%') "
    "OR COALESCE(error,'') LIKE '%Failed to parse LLM response%'))")


# A database's first hour of monitoring has its own analysis allowance: automatic jobs queued
# in it don't spend the daily cap.
FIRST_HOUR_S = 3600
FIRST_HOUR_JOB = ("reason IN ('new','top15_stale') AND enqueued_at>=? AND enqueued_at<?")


# A turn-on is a first run only when nothing was recorded before it (the rule
# delivery.discovery_until uses): turning monitoring back on for a database with history
# starts no first hour.
# A bucket is earlier history only when it ends before the turn-on's minute: a rollup bucket
# that merely starts before it holds the first run's own readings.
HISTORY_BEFORE = "SELECT 1 FROM db_series WHERE target=? AND bucket_start+res<? LIMIT 1"


def first_hour_window(conn: sqlite3.Connection, target: str) -> tuple[int, int]:
    """[start, end) of the target's first monitored hour; empty when it has none on record."""
    row = conn.execute("SELECT value FROM monitor_setting WHERE target=? AND key='enabled_at'",
                       (target,)).fetchone()
    try:
        start = int(row[0]) if row else 0
    except ValueError:
        start = 0
    if not start or conn.execute(HISTORY_BEFORE, (target, start)).fetchone():
        return (0, 0)
    return (start, start + FIRST_HOUR_S)


def _started_in_day(conn: sqlite3.Connection, target: str, now: int,
                    reasons: tuple[str, ...] | None = None) -> int:
    """Analyses started in the local day that spend an allowance; `reasons` picks one."""
    start, end = local_day_bounds(now)
    return conn.execute("SELECT COUNT(*) FROM analyze_job WHERE target=? AND started_at>=? "
                        f"AND started_at<? AND {ANALYSIS_SPENDS_CAP} "
                        f"AND NOT ({FIRST_HOUR_JOB}) "
                        "AND (? IS NULL OR reason IN (SELECT value FROM json_each(?)))",
                        (target, start, end, *first_hour_window(conn, target),
                         None if reasons is None else 1,
                         json.dumps(list(reasons or ())))).fetchone()[0]


def allowance_reasons(reason: str) -> tuple[str, ...]:
    """The reasons sharing `reason`'s daily allowance."""
    return NEWCOMER_REASONS if reason in NEWCOMER_REASONS else REANALYSIS_REASONS


def default_analysis_daily_cap() -> int:
    value = _env_int("RDST_MONITOR_ANALYZE_DAILY_CAP")
    return max(0, value) if value is not None else DEFAULT_ANALYSIS_DAILY_CAP


def default_newcomer_daily_cap() -> int:
    value = _env_int("RDST_MONITOR_NEWCOMER_DAILY_CAP")
    return max(0, value) if value is not None else DEFAULT_NEWCOMER_DAILY_CAP


def default_attention_every_min() -> int:
    """The nearest offered interval to RDST_MONITOR_JUDGE_INTERVAL_S, when that is set."""
    seconds = _env_int("RDST_MONITOR_JUDGE_INTERVAL_S")
    if seconds is None:
        return DEFAULT_ATTENTION_EVERY_MIN
    return min(ATTENTION_INTERVALS_MIN, key=lambda minutes: abs(minutes * 60 - seconds))


def default_push_daily_cap() -> int:
    value = _env_int("RDST_MONITOR_PUSH_DAILY_CAP")
    return max(0, value) if value is not None else DEFAULT_PUSH_DAILY_CAP


def _push_cap(conn: sqlite3.Connection, target: str) -> int:
    row = conn.execute("SELECT value FROM monitor_setting WHERE target=? AND key='push_daily_cap'",
                       (target,)).fetchone()
    try:
        return int(row[0]) if row else default_push_daily_cap()
    except ValueError:
        return default_push_daily_cap()


def notify_event(type: str, subject: str) -> str:
    """The `notify_events` switch an item's push belongs to."""
    if type == "monitoring_issue":
        return "database_unreachable" if subject == "lost_connection" else "monitoring_setup"
    if type == "stuck_query":
        return "stuck_query"
    if type in ("daily_summary", "report_ready"):
        return "daily_report"
    if type in DATABASE_EVENT_TYPES:
        return "database_events"
    return "query_changes"


def in_quiet_hours(hours: dict[str, Any] | None, ts: int) -> bool:
    """Whether local time `ts` falls in the quiet window [from_hour, to_hour), which may
    wrap past midnight; equal hours mean no quiet window."""
    if not isinstance(hours, dict):
        return False
    try:
        start, end = int(hours.get("from_hour", 0)), int(hours.get("to_hour", 0))
    except (TypeError, ValueError):
        return False
    if start == end:
        return False
    hour = datetime.fromtimestamp(ts).hour
    return start <= hour < end if start < end else hour >= start or hour < end


def _quiet_hours(conn: sqlite3.Connection, target: str) -> dict[str, Any]:
    row = conn.execute("SELECT value FROM monitor_setting WHERE target=? AND key='quiet_hours'",
                       (target,)).fetchone()
    try:
        saved = _valid_quiet_hours(json.loads(row[0])) if row else None
    except ValueError:
        saved = None
    return saved if saved is not None else dict(DEFAULT_QUIET_HOURS)


def _valid_quiet_hours(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict) or set(value) != {"from_hour", "to_hour"}:
        return None
    hours = {}
    for key in ("from_hour", "to_hour"):
        hour = value[key]
        if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
            return None
        hours[key] = hour
    return hours


_PREFERENCES_CACHE: dict[str, Any] = {"at": 0.0, "value": {}}


def read_preferences(path: Path | None = None, *, now: float | None = None) -> dict[str, Any]:
    """The `[preferences]` section of the RDST config file (theme, font size, how long to
    keep readings), read at most once a minute."""
    moment = time.time() if now is None else now
    if path is None and moment - _PREFERENCES_CACHE["at"] < 60:
        return dict(_PREFERENCES_CACHE["value"])
    config = path or shared_constants.rdst_data_dir() / "config.toml"
    found: dict[str, Any] = {}
    try:
        import toml

        data = toml.load(config) if Path(config).exists() else {}
        section = data.get("preferences")
        found = dict(section) if isinstance(section, dict) else {}
    except Exception:
        found = {}
    if path is None:
        _PREFERENCES_CACHE.update(at=moment, value=dict(found))
    return found


def keep_readings_days(preferences: dict[str, Any] | None = None) -> int:
    value = (preferences if preferences is not None else read_preferences()).get("keep_readings_days")
    return int(value) if value in KEEP_READINGS_CHOICES else DEFAULT_KEEP_READINGS_DAYS


def reading_retention(preferences: dict[str, Any] | None = None) -> dict[int, int]:
    """RETENTION with the 5-minute rollup kept as long as the preference says."""
    return {**RETENTION, 300: keep_readings_days(preferences) * 86400}


def _notify_events(conn: sqlite3.Connection, target: str) -> dict[str, bool]:
    row = conn.execute("SELECT value FROM monitor_setting WHERE target=? AND key='notify_events'",
                       (target,)).fetchone()
    events = dict(NOTIFY_EVENTS)
    try:
        saved = json.loads(row[0]) if row else {}
    except ValueError:
        saved = {}
    if isinstance(saved, dict):
        events.update({key: bool(value) for key, value in saved.items() if key in NOTIFY_EVENTS})
    return events


def jev_daily_cap() -> int:
    value = _env_int("RDST_MONITOR_JEV_DAILY_CAP")
    return max(0, value) if value is not None else 300


_QUERY_KEY = re.compile(r"[0-9a-f]{12}")


def item_query_keys(subject: str, body: dict[str, Any]) -> list[str]:
    """Query identities an inbox item is about, most specific first."""
    keys: list[str] = []
    for candidate in (body.get("query_key"), subject, subject.split(":", 1)[0]):
        if isinstance(candidate, str) and _QUERY_KEY.fullmatch(candidate):
            keys.append(candidate)
    for field in ("query_keys", "episode_query_keys"):
        values = body.get(field)
        if isinstance(values, list):
            keys.extend(value for value in values
                        if isinstance(value, str) and _QUERY_KEY.fullmatch(value))
    return list(dict.fromkeys(keys))


def _pin_item(conn: sqlite3.Connection, item: Any) -> None:
    """Keep windows independently of the bounded occurrence list in the item body."""
    body = json.loads(item["body_json"])
    keys = item_query_keys(item["subject"], body)
    closed_at = (item["resolved_at"] or item["dismissed_at"] or item["updated_at"]) if \
        item["state"] in ("resolved", "dismissed") else None
    reopened = conn.execute("SELECT MAX(ts) FROM inbox_event WHERE item_id=? AND kind='reopened'",
                            (item["id"],)).fetchone()[0]
    anchor = body.get("burst_start") or reopened or item["created_at"]
    entries = body.get("occurrences") or [{"start": anchor, "end": closed_at, "keys": keys}]
    windows: dict[tuple[str, int], tuple[int, int | None]] = {}

    def add(key: str, start: int, end: int | None, onset: int | None = None) -> None:
        begin = min(start - INCIDENT_LOOKBACK_SECONDS, onset or start) - INCIDENT_PAD_SECONDS
        finish = int(end if end is not None else max(start, item["updated_at"])) + INCIDENT_PAD_SECONDS
        identity = (key, start)
        if identity in windows:
            old_start, old_end = windows[identity]
            begin = min(begin, old_start)
            finish = max(old_end, finish) if old_end is not None and finish is not None else None
        windows[identity] = (begin, finish)

    for entry in entries:
        start = int(entry["start"])
        end = entry.get("end") or closed_at
        incident = body.get("incident") or {}
        for key in ["", *(entry.get("keys") or keys)]:
            add(key, start, end, incident.get("start"))
    for field in ("recovery_queries", "recovered_queries"):
        for key, member in (body.get(field) or {}).items():
            start = int(member.get("filed_at") or item["created_at"])
            end = member.get("retired_at") or closed_at
            for own in ("", key):
                add(own, start, end)
    # Close a prior occurrence when its bounded body entry has rolled out of the list.
    oldest = min(int(entry["start"]) for entry in entries)
    conn.execute("UPDATE incident_pin SET end=? WHERE item_id=? AND end IS NULL "
                 "AND incident_start<?", (oldest + INCIDENT_PAD_SECONDS, item["id"], oldest))
    conn.executemany(
        "INSERT INTO incident_pin VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(item_id,query_key,incident_start) DO UPDATE SET "
        "start=MIN(start,excluded.start),end=excluded.end "
        "WHERE start>excluded.start OR end IS NOT excluded.end",
        [(item["id"], item["target"], key, start, begin, finish)
         for (key, start), (begin, finish) in windows.items()])


def _unpinned_sql(table: str, now: int) -> str:
    key = f"AND p.query_key={table}.query_key " if table == "stmt_series" else "AND p.query_key='' "
    return ("NOT EXISTS (SELECT 1 FROM incident_pin p JOIN inbox_item i ON i.id=p.item_id "
            f"WHERE p.target={table}.target {key}"
            f"AND {table}.bucket_start>=p.start "
            f"AND (p.end IS NULL OR {table}.bucket_start<p.end))")


def rule_matches(rule: dict[str, Any], target: str, type: str, keys: list[str],
                 ts: int) -> bool:
    return ((rule["target"] is None or rule["target"] == target) and
            (rule["type"] is None or rule["type"] == type) and
            (rule["query_key"] is None or rule["query_key"] in keys) and
            (rule["until"] is None or rule["until"] > ts))


def _matching_rule(conn: sqlite3.Connection, target: str, type: str, keys: list[str],
                   ts: int) -> int | None:
    for row in conn.execute("SELECT * FROM mute_rule WHERE (target IS NULL OR target=?) "
                            "AND (type IS NULL OR type=?) ORDER BY id", (target, type)):
        if rule_matches(dict(row), target, type, keys, ts):
            return row["id"]
    return None


def _retire_legacy_items(conn: sqlite3.Connection, ts: int) -> None:
    """Close items the notification model no longer files, keeping them for history.

    Index attribution (fix confirmed, index didn't help, fix available) and report-only
    findings move to the digest and close. Database-wide kinds filed against one query, and
    database items whose numbers never showed a change, close the same way.
    """
    rows = conn.execute("SELECT id,type,subject,state,body_json FROM inbox_item").fetchall()
    for row in rows:
        try:
            body = json.loads(row["body_json"])
        except ValueError:
            body = {}
        numbers = body.get("numbers") if isinstance(body, dict) else None
        empty_database = (row["type"] == "rollback_or_deadlock_spike" and
                          not (body.get("rollbacks_per_min") if isinstance(body, dict) else 0) and
                          isinstance(numbers, dict) and
                          not (numbers.get("rollbacks") or numbers.get("deadlocks")))
        # Index attribution's fix_confirmed items carried no detected index; the redesign's do.
        legacy_fix = (row["type"] == "fix_confirmed" and
                      not (isinstance(body, dict) and body.get("detected_at")))
        retire = (row["type"] in RETIRED_TYPES or legacy_fix or
                  (row["type"] in ("rollback_or_deadlock_spike", "cache_hit_ratio_drop") and
                   row["subject"] != "database") or empty_database)
        if not retire:
            continue
        conn.execute("UPDATE inbox_item SET delivery='digest' WHERE id=?", (row["id"],))
        if row["state"] not in ("resolved", "dismissed"):
            conn.execute("UPDATE inbox_item SET state='resolved',resolved_at=?,resolved_by='code',"
                         "updated_at=? WHERE id=?", (ts, ts, row["id"]))
            conn.execute("INSERT INTO inbox_event (item_id,ts,kind,detail_json) VALUES (?,?,?,?)",
                         (row["id"], ts, "resolved",
                          json.dumps({"action": "resolve", "retired": True})))


def _allow_undone_actions(conn: sqlite3.Connection) -> None:
    """Schema 31: an action a user undid keeps its own state. SQLite can't change a CHECK in
    place, so the table is rebuilt with the same rows and ids; events keep pointing at them."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='action'"
                       ).fetchone()
    if sql is None or "'undone'" in sql[0]:
        return
    create = re.sub(r"CREATE TABLE\s+(IF NOT EXISTS\s+)?\"?action\"?", "CREATE TABLE action_v31",
                    sql[0], count=1, flags=re.IGNORECASE)
    create = create.replace("'no_effect','retired')", "'no_effect','retired','undone')", 1)
    conn.execute(create)
    conn.execute("INSERT INTO action_v31 SELECT * FROM action")
    conn.execute("DROP TABLE action")
    conn.execute("ALTER TABLE action_v31 RENAME TO action")


def _retire_fix_items(conn: sqlite3.Connection, ts: int) -> None:
    """Schema 31: fixes live in Actions. The open fixes item closes, and so do confirmations
    still measuring, since the actions check files the outcome now."""
    rows = conn.execute("SELECT id FROM inbox_item WHERE type IN ('fixes_ready','fix_confirmed') "
                        "AND state NOT IN ('resolved','dismissed')").fetchall()
    for row in rows:
        conn.execute("UPDATE inbox_item SET state='resolved',resolved_at=?,resolved_by='code',"
                     "updated_at=? WHERE id=?", (ts, ts, row[0]))
        conn.execute("INSERT INTO inbox_event (item_id,ts,kind,detail_json) VALUES (?,?,?,?)",
                     (row[0], ts, "resolved", json.dumps({"action": "resolve", "retired": True,
                                                          "schema": 31})))


def _close_legacy_items(conn: sqlite3.Connection, ts: int) -> None:
    """Schema 16: close items the current rules would not keep open.

    Group items filed before direction-aware titles and member rules ("Many queries changed at
    once" episodes that counted DDL and RDST's own statements) close so the judge files a new
    episode if the change is still measured. Items about statements that never alert (DDL,
    maintenance, RDST's own reads) close too. Both move to the digest, kept for history.
    """
    from shared.monitor.statements import alert_member

    texts = {(row["target"], row["query_key"]): row["text"]
             for row in conn.execute("SELECT target,query_key,text FROM query_text")}
    system = {(row["target"], row["query_key"])
              for row in conn.execute("SELECT target,query_key FROM system_query")}
    rows = conn.execute("SELECT id,target,type,subject,body_json FROM inbox_item "
                        "WHERE state NOT IN ('resolved','dismissed')").fetchall()
    for row in rows:
        # A stuck statement matters whatever it is: a long DDL can hold locks.
        if row["type"] in ("monitoring_issue", "daily_summary", "stuck_query") or (
                row["subject"] == "database" and row["type"] != "many_queries_changed"):
            continue
        try:
            body = json.loads(row["body_json"])
        except ValueError:
            body = {}
        keys = item_query_keys(row["subject"], body if isinstance(body, dict) else {})
        quiet = bool(keys) and all((row["target"], key) in system or not alert_member(
            texts.get((row["target"], key), "")) for key in keys)
        if row["type"] != "many_queries_changed" and not quiet:
            continue
        conn.execute("UPDATE inbox_item SET delivery='digest',state='resolved',resolved_at=?,"
                     "resolved_by='code',updated_at=? WHERE id=?", (ts, ts, row["id"]))
        conn.execute("INSERT INTO inbox_event (item_id,ts,kind,detail_json) VALUES (?,?,?,?)",
                     (row["id"], ts, "resolved", json.dumps({"action": "resolve",
                                                             "retired": True, "schema": 16})))


def identity_moves(texts: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Old key -> new key for each (key, text) whose key the pre-identity-text hash minted.

    The query library's schema 16 rule: a key moves only when it equals the previous
    derivation of its own text and the identity-text hash differs, so keys of any other
    origin stay where they are.
    """
    from shared.query_registry.query_registry import _sql_digest, hash_sql, normalize_sql
    from shared.query_registry.sql_normalizer import canonicalize_placeholder_style

    moves: dict[str, str] = {}
    for key, text in texts:
        try:
            previous = _sql_digest(canonicalize_placeholder_style(normalize_sql(text)))
            current = hash_sql(text)
        except Exception:
            continue
        if key == previous and current != previous:
            moves[key] = current
    return moves


def _swap_keys(value: Any, moves: dict[str, str]) -> Any:
    if isinstance(value, str):
        return moves.get(value, value)
    if isinstance(value, list):
        return [_swap_keys(item, moves) for item in value]
    if isinstance(value, dict):
        return {moves.get(key, key): _swap_keys(item, moves) for key, item in value.items()}
    return value


def _rekey_identities(conn: sqlite3.Connection) -> None:
    """Schema 19: follow the query library's identity-text re-key (library schema 16).

    History, samples, jobs, episodes, rules and alerts move to the new key. Where the new key
    already has a row, minute series add up, text keeps the widest first/last seen, and
    one-per-key rows (episodes, verdicts, alert subjects) keep the row already there.
    """
    by_target: dict[str, dict[str, str]] = {}
    for row in conn.execute("SELECT target,query_key,text FROM query_text").fetchall():
        moves = identity_moves([(row[1], row[2])])
        if moves:
            by_target.setdefault(row[0], {}).update(moves)
    for target, moves in by_target.items():
        for old, new in moves.items():
            args = {"t": target, "old": old, "new": new}
            conn.execute("INSERT INTO query_text SELECT target,:new,text,first_seen,last_seen "
                         "FROM query_text WHERE target=:t AND query_key=:old "
                         "ON CONFLICT(target,query_key) DO UPDATE SET "
                         "first_seen=MIN(first_seen,excluded.first_seen),"
                         "last_seen=MAX(last_seen,excluded.last_seen)", args)
            conn.execute("INSERT INTO stmt_series SELECT target,:new,res,bucket_start,calls,"
                         "total_ms,rows,blks_hit,blks_read,temp_blks FROM stmt_series "
                         "WHERE target=:t AND query_key=:old "
                         "ON CONFLICT(target,query_key,res,bucket_start) DO UPDATE SET "
                         "calls=calls+excluded.calls,total_ms=total_ms+excluded.total_ms,"
                         "rows=rows+excluded.rows,blks_hit=blks_hit+excluded.blks_hit,"
                         "blks_read=blks_read+excluded.blks_read,temp_blks=temp_blks+excluded.temp_blks", args)
            for table in ("query_text", "stmt_series"):
                conn.execute(f"DELETE FROM {table} WHERE target=:t AND query_key=:old", args)
            for table in ("query_sample", "stuck_sample", "mute_rule"):
                conn.execute(f"UPDATE {table} SET query_key=:new "
                             "WHERE target=:t AND query_key=:old", args)
            conn.execute("UPDATE analyze_job SET query_key=:new, analysis_ref=replace("
                         "analysis_ref,'/query-registry/'||:old||'/','/query-registry/'||:new||'/') "
                         "WHERE target=:t AND query_key=:old", args)
            for table in ("attention_episode", "fix_verdict", "system_query"):
                conn.execute(f"UPDATE OR IGNORE {table} SET query_key=:new "
                             "WHERE target=:t AND query_key=:old", args)
            conn.execute("DELETE FROM attention_episode WHERE target=:t AND query_key=:old", args)
            conn.execute("DELETE FROM system_query WHERE target=:t AND query_key=:old", args)
            conn.execute("UPDATE OR IGNORE inbox_item SET subject=:new "
                         "WHERE target=:t AND subject=:old", args)
        for row in conn.execute("SELECT id,body_json FROM inbox_item WHERE target=?",
                                (target,)).fetchall():
            try:
                body = json.loads(row[1])
            except ValueError:
                continue
            moved = _swap_keys(body, moves)
            if moved != body:
                conn.execute("UPDATE inbox_item SET body_json=? WHERE id=?",
                             (json.dumps(moved), row[0]))


def _repair_open_items(conn: sqlite3.Connection) -> None:
    """Fold open single-query aliases into the earliest item, preserving their history."""
    groups: dict[tuple[str, str, str], list[Any]] = {}
    for row in conn.execute("SELECT * FROM inbox_item WHERE state IN ('new','seen','snoozed') "
                            "ORDER BY created_at,id").fetchall():
        body = json.loads(row["body_json"])
        key = body.get("query_key")
        if not isinstance(key, str) or body.get("query_keys") or body.get("recovery_queries"):
            continue
        groups.setdefault((row["target"], row["type"], key), []).append(row)
    for (_, _, key), rows in groups.items():
        if len(rows) < 2:
            continue
        keeper = rows[0]
        body = json.loads(max(rows, key=lambda row: (row["updated_at"], row["id"]))["body_json"])
        entries = []
        for row in rows:
            own = json.loads(row["body_json"])
            entries.extend(own.get("occurrences") or [
                {"start": own.get("filed_at", row["created_at"]), "end": None,
                 "keys": [key]}])
            if row["id"] == keeper["id"]:
                continue
            conn.execute("UPDATE inbox_event SET item_id=? WHERE item_id=?",
                         (keeper["id"], row["id"]))
            conn.execute("INSERT OR IGNORE INTO incident_pin SELECT ?,target,query_key,"
                         "incident_start,start,end FROM incident_pin WHERE item_id=?",
                         (keeper["id"], row["id"]))
            conn.execute("DELETE FROM incident_pin WHERE item_id=?", (row["id"],))
            conn.execute("DELETE FROM inbox_item WHERE id=?", (row["id"],))
        body["occurrences"] = sorted(entries, key=lambda entry: entry["start"])[-60:]
        body["filed_at"] = body.get("filed_at", rows[-1]["created_at"])
        conn.execute("UPDATE OR IGNORE inbox_item SET subject=? WHERE id=?", (key, keeper["id"]))
        conn.execute("UPDATE inbox_item SET body_json=?,updated_at=?,severity=?,delivery=? WHERE id=?",
                     (json.dumps(body, sort_keys=True), max(row["updated_at"] for row in rows),
                      max(row["severity"] for row in rows),
                      max((row["delivery"] for row in rows), key=DELIVERY_LEVELS.index), keeper["id"]))
        _pin_item(conn, conn.execute("SELECT * FROM inbox_item WHERE id=?",
                                    (keeper["id"],)).fetchone())


def _target_tables(conn: sqlite3.Connection) -> list[str]:
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    return [table for table in tables
            if "target" in {row[1] for row in conn.execute(f"PRAGMA table_info('{table}')")}]


def _forget(conn: sqlite3.Connection, target: str) -> None:
    conn.execute("DELETE FROM inbox_event WHERE item_id IN "
                 "(SELECT id FROM inbox_item WHERE target=?)", (target,))
    conn.execute("DELETE FROM action_event WHERE action_id IN "
                 "(SELECT id FROM action WHERE target=?)", (target,))
    for table in _target_tables(conn):
        conn.execute(f'DELETE FROM "{table}" WHERE target=?', (target,))


def _insert_pulse(conn: sqlite3.Connection, target: str, ts: int, kind: str, ok: bool,
                  detail: str) -> None:
    conn.execute("INSERT INTO monitor_pulse (target,ts,kind,ok,detail) VALUES (?,?,?,?,?)",
                 (target, ts, kind, int(ok), detail))


_STORAGE_ERRORS: dict[Path, tuple[float, str]] = {}
STORAGE_RETRY_S = 5.0


class LocalMonitorLimitError(ValueError):
    """Enabling a database would exceed the local collection limit."""


class DatabaseBusyError(RuntimeError):
    """An analysis or a report is running for the database."""


class MonitorStore:
    def __init__(
        self,
        db_path: Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
        budget_bytes: int = DEFAULT_BUDGET_BYTES,
        install_budget_bytes: int | None = None,
    ) -> None:
        self.path = Path(os.path.realpath(db_path or default_monitor_db_path()))
        self.clock = clock
        self.budget_bytes = budget_bytes
        self.install_budget_bytes = (install_budget_bytes if install_budget_bytes is not None else
                                     _env_int("RDST_MONITOR_INSTALL_BUDGET_BYTES") or
                                     DEFAULT_INSTALL_BUDGET_BYTES)
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(self.path, threading.RLock())
        self._opened = False

    def _connect(self) -> sqlite3.Connection:
        guard = getattr(_STATEMENT_GUARD, "value", None)
        if guard is not None:
            guard.check()
        conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            for pragma in _connection_pragmas():
                if guard is not None:
                    guard.check()
                conn.execute(pragma)
        except BaseException:
            conn.close()
            raise
        return _GuardedConnection(conn, guard) if guard is not None else conn

    @_storage_guard
    def ensure_open(self) -> None:
        # Routes build a store per request. The integrity check reads every page under a
        # shared lock, so it runs once per file per process, not once per request.
        if self._opened or (self.path in _OPENED and self._current_schema()):
            self._opened = True
            return
        with self._lock:
            if self._opened:
                return
            _check_sqlite_runtime()
            _refuse_network_path(str(self.path))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _check_journal_headers(self.path)
            conn = self._connect()
            migration = None
            try:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise RuntimeError(f"monitor.db schema {version} is newer than this build")
                if version == 0:
                    conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
                if version:
                    check = conn.execute("PRAGMA quick_check").fetchone()[0]
                    if check != "ok":
                        raise StorageError(f"Local storage damaged at {self.path}: {check}. "
                                           "Restore this file from a backup.")
                _apply_journal_mode(conn, self.path)
                if version < SCHEMA_VERSION:
                    if version:
                        migration = startup_status.begin("monitor.db")
                        backup = self.path.with_name(f"monitor.db.pre-v{version}.bak")
                        if not backup.exists():
                            staging = _staging_file(backup)
                            try:
                                dest = sqlite3.connect(staging)
                                try:
                                    conn.backup(dest)
                                finally:
                                    dest.close()
                                if not backup.exists():
                                    replace_with_retry(str(staging), backup)
                            finally:
                                staging.unlink(missing_ok=True)
                    # Table rebuilds drop a parent while its rows are referenced; the keys are
                    # checked as a whole before the commit instead.
                    conn.execute("PRAGMA foreign_keys = OFF")
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        if version == 0:
                            for statement in _SCHEMA.split(";"):
                                if statement.strip():
                                    conn.execute(statement)
                        # Identity migration and tier trimming carry all series counters.
                        if version < 26:
                            columns = {row[1] for row in conn.execute("PRAGMA table_info(stmt_series)")}
                            if "temp_blks" not in columns:
                                conn.execute("ALTER TABLE stmt_series ADD COLUMN temp_blks INTEGER NOT NULL DEFAULT 0")
                            columns = {row[1] for row in conn.execute("PRAGMA table_info(db_series)")}
                            if "lock_waits" not in columns:
                                conn.execute("ALTER TABLE db_series ADD COLUMN lock_waits INTEGER")
                        if version < 27:
                            # Each query's running totals from the engine, for its long-run
                            # cost per call while the live readings are still few.
                            conn.execute("CREATE TABLE IF NOT EXISTS query_prior (target TEXT NOT NULL, "
                                         "query_key TEXT NOT NULL, calls INTEGER NOT NULL, "
                                         "total_ms REAL NOT NULL, PRIMARY KEY(target,query_key))")
                        if version < 28:
                            for statement in _SCHEMA_V28.split(";"):
                                if statement.strip():
                                    conn.execute(statement)
                            columns = {row[1] for row in conn.execute("PRAGMA table_info(report_run)")}
                            if "origin" not in columns:
                                # 'first' marks the checkup that follows monitoring's first pass.
                                conn.execute("ALTER TABLE report_run ADD COLUMN origin TEXT")
                        if version < 29:
                            # A PostgreSQL index left invalid by a failed concurrent build.
                            columns = {row[1] for row in conn.execute("PRAGMA table_info(index_state)")}
                            if "is_valid" not in columns:
                                conn.execute("ALTER TABLE index_state ADD COLUMN is_valid INTEGER "
                                             "NOT NULL DEFAULT 1")
                        if version < 30:
                            # When PostgreSQL last vacuumed and analyzed each table, by anyone.
                            columns = {row[1] for row in conn.execute("PRAGMA table_info(table_state)")}
                            for column in ("vacuumed_at", "analyzed_at"):
                                if column not in columns:
                                    conn.execute(f"ALTER TABLE table_state ADD COLUMN {column} INTEGER")
                        if version < 31:
                            _allow_undone_actions(conn)
                            _retire_fix_items(conn, int(time.time()))
                        if version < 2:
                            conn.execute("ALTER TABLE inbox_item ADD COLUMN snoozed_until INTEGER")
                        if version < 3:
                            conn.execute("ALTER TABLE report_run ADD COLUMN progress_json TEXT")
                            conn.execute("UPDATE report_run SET state='failed' WHERE state='running' "
                                         "AND id NOT IN (SELECT MAX(id) FROM report_run "
                                         "WHERE state='running' GROUP BY target)")
                            conn.execute("CREATE UNIQUE INDEX report_one_active ON report_run(target) WHERE state='running'")
                        if version < 4:
                            conn.execute("ALTER TABLE report_run ADD COLUMN finished_at INTEGER")
                        if version < 5:
                            conn.execute("ALTER TABLE report_run ADD COLUMN heartbeat_at INTEGER")
                        if version < 6:
                            conn.execute("ALTER TABLE db_series ADD COLUMN blks_hit INTEGER")
                            conn.execute("ALTER TABLE db_series ADD COLUMN blks_read INTEGER")
                        if version < 7:
                            conn.execute("CREATE INDEX judge_run_target_ts ON judge_run(target,ts)")
                            conn.execute("CREATE INDEX inbox_event_ts ON inbox_event(ts)")
                        if version < 8:
                            conn.execute("CREATE INDEX IF NOT EXISTS analyze_job_target_key_id "
                                         "ON analyze_job(target,query_key,id)")
                            conn.execute("CREATE INDEX IF NOT EXISTS inbox_item_target_subject "
                                         "ON inbox_item(target,subject)")
                        if version < 9:
                            conn.execute("CREATE TABLE IF NOT EXISTS dirty_rollup ("
                                         "target TEXT NOT NULL, source_res INTEGER NOT NULL, "
                                         "bucket_start INTEGER NOT NULL, "
                                         "PRIMARY KEY (target,source_res,bucket_start))")
                            columns = {row[1] for row in conn.execute("PRAGMA table_info(inbox_item)")}
                            if "dismissed_returned" not in columns:
                                conn.execute("ALTER TABLE inbox_item ADD COLUMN "
                                             "dismissed_returned INTEGER NOT NULL DEFAULT 0")
                            conn.execute("UPDATE inbox_item SET dismissed_returned=1 WHERE EXISTS ("
                                         "SELECT 1 FROM inbox_event e WHERE e.item_id=inbox_item.id "
                                         "AND e.kind='reopened' AND "
                                         "json_extract(e.detail_json,'$.action') IS NOT 'reopen' "
                                         "AND (SELECT p.kind FROM inbox_event p WHERE p.item_id=e.item_id "
                                         "AND p.id<e.id AND p.kind IN ('dismissed','reopened','resolved') "
                                         "ORDER BY p.id DESC LIMIT 1)='dismissed' "
                                         "AND NOT EXISTS (SELECT 1 FROM inbox_event m WHERE m.item_id=e.item_id "
                                         "AND m.id>e.id AND m.kind='reopened' AND "
                                         "json_extract(m.detail_json,'$.action')='reopen'))")
                        if version < 10:
                            conn.execute("CREATE TABLE IF NOT EXISTS query_sample (id INTEGER PRIMARY KEY, "
                                         "target TEXT NOT NULL, query_key TEXT NOT NULL, "
                                         "text TEXT NOT NULL, observed_at INTEGER NOT NULL)")
                            conn.execute("CREATE INDEX IF NOT EXISTS query_sample_latest ON "
                                         "query_sample(target,query_key,id DESC)")
                        if version < 11:
                            for table in ("judge_run", "analyze_job", "report_run"):
                                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                                if "billable_calls" not in columns:
                                    conn.execute(f"ALTER TABLE {table} ADD COLUMN billable_calls "
                                                 "INTEGER")
                        if version < 12:
                            conn.execute("CREATE TABLE IF NOT EXISTS attention_episode ("
                                         "target TEXT NOT NULL, query_key TEXT NOT NULL, "
                                         "kind TEXT NOT NULL, baseline_mean_ms REAL NOT NULL, "
                                         "settled_at INTEGER NOT NULL, "
                                         "PRIMARY KEY (target,query_key,kind))")
                        if version < 13:
                            columns = {row[1] for row in conn.execute(
                                "PRAGMA table_info(attention_episode)")}
                            if "judged_qps" not in columns:
                                conn.execute("ALTER TABLE attention_episode ADD COLUMN "
                                             "judged_qps REAL NOT NULL DEFAULT 0")
                            conn.execute("UPDATE attention_episode SET judged_qps=COALESCE(("
                                         "SELECT SUM(s.calls) / 300.0 FROM stmt_series s "
                                         "WHERE s.target=attention_episode.target "
                                         "AND s.query_key=attention_episode.query_key "
                                         "AND s.res=60 AND s.bucket_start>=settled_at-300 "
                                         "AND s.bucket_start<settled_at), 0)")
                        if version < 14:
                            for statement in _SCHEMA_V14.split(";"):
                                if statement.strip():
                                    conn.execute(statement)
                            columns = {row[1] for row in conn.execute("PRAGMA table_info(inbox_item)")}
                            if "muted_by" not in columns:
                                conn.execute("ALTER TABLE inbox_item ADD COLUMN muted_by INTEGER")
                        if version < 15:
                            columns = {row[1] for row in conn.execute("PRAGMA table_info(inbox_item)")}
                            if "delivery" not in columns:
                                conn.execute("ALTER TABLE inbox_item ADD COLUMN delivery TEXT NOT NULL "
                                             "DEFAULT 'inbox' CHECK (delivery IN "
                                             "('digest','inbox','notify'))")
                            _retire_legacy_items(conn, int(time.time()))
                        if version < 16:
                            _close_legacy_items(conn, int(time.time()))
                        if version < 17:
                            conn.execute("CREATE INDEX IF NOT EXISTS inbox_event_kind_item "
                                         "ON inbox_event(kind,item_id,ts)")
                        if version < 18:
                            # A report covers one local day; sample days are demo history.
                            columns = {row[1] for row in conn.execute("PRAGMA table_info(report_run)")}
                            for column, kind in (("period_start", "INTEGER"),
                                                 ("period_end", "INTEGER"),
                                                 ("sample", "INTEGER NOT NULL DEFAULT 0")):
                                if column not in columns:
                                    conn.execute(f"ALTER TABLE report_run ADD COLUMN {column} {kind}")
                        if version < 19:
                            _rekey_identities(conn)
                        if version < 20:
                            conn.execute("CREATE TABLE IF NOT EXISTS incident_pin (item_id INTEGER NOT NULL, "
                                         "target TEXT NOT NULL, query_key TEXT NOT NULL, "
                                         "incident_start INTEGER NOT NULL, start INTEGER NOT NULL, "
                                         "end INTEGER, PRIMARY KEY(item_id,query_key,incident_start))")
                            conn.execute("CREATE INDEX IF NOT EXISTS incident_pin_window "
                                         "ON incident_pin(target,query_key,start,end)")
                            for item in conn.execute("SELECT * FROM inbox_item").fetchall():
                                _pin_item(conn, item)
                        if version < 21:
                            conn.execute("CREATE TABLE IF NOT EXISTS query_load (target TEXT NOT NULL, "
                                         "query_key TEXT NOT NULL, total_ms REAL NOT NULL, "
                                         "updated_at INTEGER NOT NULL, PRIMARY KEY(target,query_key))")
                            conn.execute("CREATE TABLE IF NOT EXISTS query_tier (target TEXT NOT NULL, "
                                         "query_key TEXT NOT NULL, rank INTEGER NOT NULL, "
                                         "PRIMARY KEY(target,query_key))")
                            conn.execute("CREATE INDEX IF NOT EXISTS query_tier_rank ON query_tier(target,rank)")
                            conn.execute("CREATE TABLE IF NOT EXISTS query_tier_period (target TEXT NOT NULL, "
                                         "query_key TEXT NOT NULL, start INTEGER NOT NULL, "
                                         "end INTEGER, tier INTEGER NOT NULL, "
                                         "PRIMARY KEY(target,query_key,start,tier))")
                            conn.execute("CREATE INDEX IF NOT EXISTS query_tier_period_open "
                                         "ON query_tier_period(target,query_key) WHERE end IS NULL")
                            conn.execute("CREATE TABLE IF NOT EXISTS query_ui (target TEXT NOT NULL, "
                                         "query_key TEXT NOT NULL, efficiency_score REAL, "
                                         "fix_ready INTEGER NOT NULL DEFAULT 0, "
                                         "analyzed INTEGER NOT NULL DEFAULT 0, "
                                         "assessed INTEGER NOT NULL DEFAULT 0, "
                                         "findings INTEGER NOT NULL DEFAULT 0, workload INTEGER NOT NULL DEFAULT 1, "
                                         "PRIMARY KEY(target,query_key))")
                        if version < 22:
                            _repair_open_items(conn)
                        if version < 23:
                            targets = conn.execute("SELECT target FROM query_text UNION "
                                                   "SELECT target FROM query_load UNION "
                                                   "SELECT target FROM query_sample UNION SELECT target FROM stuck_sample "
                                                   "WHERE query_key IS NOT NULL").fetchall()
                            for row in targets:
                                self._trim_tracked_queries(conn, row[0])
                        if version < 24:
                            for table in ("inbox_event", "monitor_pulse"):
                                sql = conn.execute("SELECT sql FROM sqlite_master WHERE name=?",
                                                   (table,)).fetchone()[0]
                                if "AUTOINCREMENT" not in sql.upper():
                                    indexes = conn.execute(
                                        "SELECT sql FROM sqlite_master WHERE type='index' "
                                        "AND tbl_name=? AND sql IS NOT NULL", (table,)).fetchall()
                                    create = re.sub(r"CREATE TABLE\s+\S+", f"CREATE TABLE {table}_cursor",
                                                    sql, count=1, flags=re.IGNORECASE)
                                    create = create.replace("INTEGER PRIMARY KEY",
                                                            "INTEGER PRIMARY KEY AUTOINCREMENT", 1)
                                    conn.execute(create)
                                    conn.execute(f"INSERT INTO {table}_cursor SELECT * FROM {table}")
                                    conn.execute(f"DROP TABLE {table}")
                                    conn.execute(f"ALTER TABLE {table}_cursor RENAME TO {table}")
                                    for index in indexes:
                                        conn.execute(index[0])
                            columns = {row[1] for row in conn.execute("PRAGMA table_info(analyze_job)")}
                            if "owner" not in columns:
                                conn.execute("ALTER TABLE analyze_job ADD COLUMN owner TEXT")
                                conn.execute("ALTER TABLE analyze_job ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0")
                                conn.execute("UPDATE analyze_job SET state='queued',started_at=NULL "
                                             "WHERE state='running'")
                            for item in conn.execute("SELECT * FROM inbox_item").fetchall():
                                _pin_item(conn, item)
                        columns = {row[1] for row in conn.execute("PRAGMA table_info(analyze_job)")}
                        for name, kind in (("owner_host", "TEXT"), ("owner_pid", "INTEGER"),
                                           ("owner_start", "REAL")):
                            if name not in columns:
                                conn.execute(f"ALTER TABLE analyze_job ADD COLUMN {name} {kind}")
                        if version < 32:
                            sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='analyze_job'").fetchone()[0]
                            indexes = conn.execute(
                                "SELECT sql FROM sqlite_master WHERE type='index' "
                                "AND tbl_name='analyze_job' AND sql IS NOT NULL").fetchall()
                            create = re.sub(r"CREATE TABLE\s+\S+", "CREATE TABLE analyze_job_estimated",
                                            sql, count=1, flags=re.IGNORECASE)
                            create = create.replace("'sampled','table_sampled','none')",
                                                    "'sampled','table_sampled','none','estimated')")
                            conn.execute(create)
                            conn.execute("INSERT INTO analyze_job_estimated SELECT * FROM analyze_job")
                            conn.execute("DROP TABLE analyze_job")
                            conn.execute("ALTER TABLE analyze_job_estimated RENAME TO analyze_job")
                            for index in indexes:
                                conn.execute(index[0])
                        broken = conn.execute("PRAGMA foreign_key_check").fetchall()
                        if broken:
                            raise StorageError(f"monitor.db migration left {len(broken)} rows "
                                               f"without their parent in {broken[0][0]}")
                        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                        conn.execute("COMMIT")
                    except BaseException:
                        if conn.in_transaction:
                            (conn._conn if isinstance(conn, _GuardedConnection) else conn).execute("ROLLBACK")
                        raise
            finally:
                conn.close()
                if migration:
                    startup_status.end(migration)
            self._opened = True
            _OPENED.add(self.path)

    def _current_schema(self) -> bool:
        if not self.path.exists():
            return False
        conn = self._connect()
        try:
            return conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        finally:
            conn.close()

    def read(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Rows as dicts. A read that meets a writer's lock past busy_timeout retries until
        READ_RETRY_S has passed; a read changes nothing, so running it again is safe."""
        deadline = time.monotonic() + READ_RETRY_S
        delay = 0.05
        while True:
            try:
                return self._read_once(sql, params)
            except sqlite3.OperationalError as exc:
                if not is_locked(exc) or time.monotonic() + delay > deadline:
                    raise
            time.sleep(delay * (1 + random.random()))
            delay = min(delay * 2, 1.0)

    @_storage_guard
    def _read_once(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        self.ensure_open()
        conn = self._connect()
        try:
            if sql.split(None, 1)[0].upper() not in ("SELECT", "WITH"):
                return [dict(row) for row in conn.execute(sql, params)]
            # SQLite fills a private temp table in one step, so the shared lock on the file
            # ends with the query. Stepping rows from Python would hold it while this thread
            # waits for the GIL once per row, which blocks every commit meanwhile.
            conn.execute(f"CREATE TEMP TABLE _read AS {sql}", params)
            names = [column[0] for column in
                     conn.execute("SELECT * FROM temp._read LIMIT 0").description]
            # The rows leave SQLite as one JSON text, one step. Stepping them one by one
            # releases and retakes the GIL per row, and while other threads run Python each
            # retake waits a switch interval: concurrent requests then take seconds.
            columns = ",".join('"' + name.replace('"', '""') + '"' for name in names)
            try:
                rows = json.loads(conn.execute(
                    f"SELECT json_group_array(json_array({columns})) "
                    "FROM (SELECT * FROM temp._read ORDER BY rowid)").fetchone()[0])
            except sqlite3.OperationalError:
                # JSON can't hold BLOB values; those results step row by row.
                rows = conn.execute("SELECT * FROM temp._read ORDER BY rowid").fetchall()
        finally:
            conn.close()
        # A repeated result column comes back as "name:N"; the first one keeps the name.
        keep = [index for index, name in enumerate(names)
                if not ((match := _DUPLICATE_COLUMN.fullmatch(name)) and match.group(1) in names)]
        return [{names[index]: row[index] for index in keep} for row in rows]

    def _write(self, action: Callable[[sqlite3.Connection], Any], *,
               retry_s: float = WRITE_RETRY_S) -> Any:
        """Run `action` in one short transaction, retrying it while the file is locked."""
        self.ensure_open()
        failure = _STORAGE_ERRORS.get(self.path)
        if failure and time.monotonic() < failure[0]:
            raise StorageError(failure[1])
        deadline = time.monotonic() + retry_s
        delay = 0.02
        while True:
            try:
                result = self._write_once(action)
                _STORAGE_ERRORS.pop(self.path, None)
                return result
            except StorageError as exc:
                if len(_STORAGE_ERRORS) >= 128:
                    _STORAGE_ERRORS.pop(next(iter(_STORAGE_ERRORS)))
                _STORAGE_ERRORS[self.path] = (time.monotonic() + STORAGE_RETRY_S, str(exc))
                raise
            except sqlite3.OperationalError as exc:
                if not is_locked(exc) or time.monotonic() + delay > deadline:
                    raise
            time.sleep(delay * (1 + random.random()))
            delay = min(delay * 2, 0.5)

    @_storage_guard
    def _write_once(self, action: Callable[[sqlite3.Connection], Any]) -> Any:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(f"PRAGMA busy_timeout = {WRITE_BUSY_MS}")
                conn.execute("BEGIN IMMEDIATE")
                try:
                    result = action(conn)
                    conn.execute("COMMIT")
                    return result
                except BaseException:
                    if conn.in_transaction:
                        (conn._conn if isinstance(conn, _GuardedConnection) else conn).execute(
                            "ROLLBACK")
                    raise
            finally:
                conn.close()

    def levers(self, target: str) -> dict[str, Any]:
        """Per-target switches and cost levers, without the storage size scan."""
        values: dict[str, Any] = dict(BOOL_SETTINGS)
        values["analysis_daily_cap"] = default_analysis_daily_cap()
        values["newcomer_daily_cap"] = default_newcomer_daily_cap()
        values["attention_every_min"] = default_attention_every_min()
        values["nightly_hour"] = DEFAULT_NIGHTLY_HOUR
        values["push_daily_cap"] = default_push_daily_cap()
        values["dev_learning_period_s"] = None
        values["notify_events"] = dict(NOTIFY_EVENTS)
        values["quiet_hours"] = dict(DEFAULT_QUIET_HOURS)
        for row in self.read("SELECT key,value FROM monitor_setting WHERE target=?", (target,)):
            key, value = row["key"], row["value"]
            if key == "quiet_hours":
                try:
                    saved = _valid_quiet_hours(json.loads(value))
                except ValueError:
                    saved = None
                if saved is not None:
                    values[key] = saved
            elif key == "notify_events":
                try:
                    saved = json.loads(value)
                except ValueError:
                    continue
                if isinstance(saved, dict):
                    values[key].update({name: bool(on) for name, on in saved.items()
                                        if name in NOTIFY_EVENTS})
            elif key in BOOL_SETTINGS:
                values[key] = value == "1"
            elif key in ("analysis_daily_cap", "newcomer_daily_cap", "attention_every_min",
                         "nightly_hour",
                         "push_daily_cap", "dev_learning_period_s"):
                try:
                    values[key] = int(value)
                except ValueError:
                    pass
        return values

    def lever_is_set(self, target: str, key: str) -> bool:
        return bool(self.read("SELECT 1 FROM monitor_setting WHERE target=? AND key=?",
                              (target, key)))

    def settings(self, target: str) -> dict[str, Any]:
        values = self.levers(target)
        values["configured"] = any(self.lever_is_set(target, key)
                                   for key in ("enabled", "deep_analysis", "execute"))
        values["analysis_mode"] = "execute" if values["execute"] else "plans_only"
        values["enabled_at"] = self.enabled_at(target)
        values["first_hour_started_at"] = self.first_hour(target)[0] or None
        values["size_bytes"] = self.target_size_bytes(target)
        values["budget_bytes"] = self.budget_bytes
        return values

    def forget_target(self, target: str) -> None:
        """Drop everything stored for a removed target, so a database added again under the
        same name starts fresh."""
        self._write(lambda conn: _forget(conn, target))

    def rename_target(self, old: str, new: str) -> None:
        """Move everything stored for a database to its new name in one transaction. Rows
        left under the new name by an earlier database are dropped first. The old name keeps
        monitoring off, so a cycle queued under it before the rename stops at its first read.
        An analysis or report running under the old name refuses the rename: its result would
        land under a name that no longer exists."""
        def write(conn: sqlite3.Connection) -> None:
            if conn.execute("SELECT 1 FROM analyze_job WHERE target=? AND state='running' "
                            "UNION ALL SELECT 1 FROM report_run WHERE target=? "
                            "AND state='running' LIMIT 1", (old, old)).fetchone():
                raise DatabaseBusyError(
                    f"{old} is running an analysis or a report. Rename it when that finishes.")
            _forget(conn, new)
            for table in _target_tables(conn):
                conn.execute(f'UPDATE "{table}" SET target=? WHERE target=?', (new, old))
            conn.execute("INSERT INTO monitor_setting (target,key,value) VALUES (?,'enabled','0')",
                         (old,))
        self._write(write)

    def busy(self, target: str) -> bool:
        """Whether an analysis or a report is running for the database."""
        return bool(self.read(
            "SELECT 1 FROM analyze_job WHERE target=? AND state='running' UNION ALL "
            "SELECT 1 FROM report_run WHERE target=? AND state='running' LIMIT 1",
            (target, target)))

    def set_settings(self, target: str, values: dict[str, Any]) -> dict[str, Any]:
        stored: dict[str, str] = {}
        for key, value in values.items():
            if key in BOOL_SETTINGS:
                stored[key] = "1" if value else "0"
            elif key in ("analysis_daily_cap", "newcomer_daily_cap"):
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000:
                    raise ValueError(f"{key} must be between 0 and 1000")
                stored[key] = str(value)
            elif key == "attention_every_min":
                if value not in ATTENTION_INTERVALS_MIN:
                    raise ValueError("attention_every_min must be 5, 15 or 60")
                stored[key] = str(value)
            elif key == "nightly_hour":
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 23:
                    raise ValueError("nightly_hour must be between 0 and 23")
                stored[key] = str(value)
            elif key == "push_daily_cap":
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 20:
                    raise ValueError("push_daily_cap must be between 0 and 20")
                stored[key] = str(value)
            elif key == "notify_events":
                if not isinstance(value, dict) or any(
                        name not in NOTIFY_EVENTS or not isinstance(on, bool)
                        for name, on in value.items()):
                    raise ValueError("notify_events takes on/off switches for: " +
                                     ", ".join(NOTIFY_EVENTS))
                stored[key] = json.dumps({**self.levers(target)["notify_events"], **value},
                                         sort_keys=True)
            elif key == "quiet_hours":
                hours = _valid_quiet_hours(value)
                if hours is None:
                    raise ValueError("quiet_hours takes from_hour and to_hour between 0 and 23")
                stored[key] = json.dumps(hours, sort_keys=True)
            elif key == "dev_learning_period_s":
                if (isinstance(value, bool) or not isinstance(value, int)
                        or not 0 <= value <= DEV_LEARNING_PERIOD_MAX_S):
                    raise ValueError(f"dev_learning_period_s must be between 0 and {DEV_LEARNING_PERIOD_MAX_S}")
                stored[key] = str(value)
            else:
                raise ValueError("Unknown monitor setting")
        def write(conn: sqlite3.Connection) -> None:
            if stored.get("enabled") == "1":
                enabled = conn.execute("SELECT 1 FROM monitor_setting WHERE target=? "
                                       "AND key='enabled' AND value='1'", (target,)).fetchone()
                count = conn.execute("SELECT COUNT(*) FROM monitor_setting WHERE key='enabled' "
                                     "AND value='1'").fetchone()[0]
                limit = _env_int("RDST_MONITOR_LOCAL_LIMIT")
                limit = max(1, limit) if limit is not None else 5
                if not enabled and count >= limit:
                    raise LocalMonitorLimitError(
                        f"This install already monitors {count} databases. The local limit is {limit}. "
                        "Turn off monitoring for a database before turning on another.")
            for key, value in stored.items():
                conn.execute(
                    "INSERT INTO monitor_setting VALUES (?,?,?) "
                    "ON CONFLICT(target,key) DO UPDATE SET value=excluded.value",
                    (target, key, value),
                )
        self._write(write)
        return self.settings(target)

    def write_setting(self, target: str, key: str, value: str) -> None:
        """An internal per-target marker that isn't a user setting."""
        self._write(lambda conn: conn.execute(
            "INSERT INTO monitor_setting VALUES (?,?,?) "
            "ON CONFLICT(target,key) DO UPDATE SET value=excluded.value", (target, key, value)))

    def start_first_hour(self, target: str, ts: int) -> None:
        """Record the latest turn-on; a database without earlier history gets a first hour."""
        self.write_setting(target, "enabled_at", str(ts))

    def enabled_at(self, target: str) -> int | None:
        """When monitoring was last turned on, including databases with earlier history."""
        rows = self.read("SELECT value FROM monitor_setting WHERE target=? AND key='enabled_at'",
                         (target,))
        return int(rows[0]["value"]) if rows and str(rows[0]["value"]).isdigit() else None

    def first_hour(self, target: str) -> tuple[int, int]:
        start = self.enabled_at(target)
        if not start or self.read(HISTORY_BEFORE, (target, start)):
            return (0, 0)
        return (start, start + FIRST_HOUR_S)

    def save_database_check(self, target: str, at: int, actions: list[dict[str, Any]]) -> None:
        """The latest database checks' findings, kept between checkups."""
        value = json.dumps({"at": at, "actions": actions})
        self._write(lambda conn: conn.execute(
            "INSERT OR REPLACE INTO monitor_setting (target,key,value) VALUES (?,'database_check',?)",
            (target, value)))

    def database_check(self, target: str) -> dict[str, Any] | None:
        rows = self.read("SELECT value FROM monitor_setting WHERE target=? AND key='database_check'",
                         (target,))
        try:
            return json.loads(rows[0]["value"]) if rows else None
        except ValueError:
            return None

    def monitored_targets(self) -> set[str]:
        return {row["target"] for row in self.read(
            "SELECT target FROM monitor_setting WHERE key='enabled' AND value='1'"
        )}

    def claim_report(self, target: str, trigger: str, now: int,
                     period_start: int | None = None) -> tuple[dict[str, Any], bool]:
        """Start a report run, or return the one already running or just finished.

        A finished run is reused only when it covers `period_start` (when given): just after
        midnight, yesterday's nightly report is no answer to a request for today.
        """
        if trigger not in {"nightly", "user"}:
            raise ValueError("Unknown report trigger")
        def write(conn: sqlite3.Connection) -> tuple[dict[str, Any], bool]:
            row = conn.execute("SELECT * FROM report_run WHERE target=? AND state='running' "
                               "ORDER BY id DESC LIMIT 1", (target,)).fetchone()
            if row:
                return dict(row), False
            row = conn.execute("SELECT * FROM report_run WHERE target=? AND state='done' "
                               "AND sample=0 AND COALESCE(finished_at,ts)>? "
                               "AND (? IS NULL OR period_start=?) "
                               "ORDER BY id DESC LIMIT 1",
                               (target, now - 900, period_start, period_start)).fetchone()
            if row:
                return dict(row), False
            cursor = conn.execute("INSERT INTO report_run (target,ts,trigger,state,progress_json,heartbeat_at,"
                                  "billable_calls,period_start) VALUES (?,?,?,?,?,?,0,?)",
                                  (target, now, trigger, "running",
                                   json.dumps({"message": "Selecting top queries", "done": 0,
                                               "total": 0}), now, period_start))
            return dict(conn.execute("SELECT * FROM report_run WHERE id=?",
                                     (cursor.lastrowid,)).fetchone()), True
        return self._write(write)

    def update_report(self, report_id: int, *, progress: dict[str, Any] | None = None,
                      top: list[str] | None = None, report: dict[str, Any] | None = None,
                      provisional: bool = False, state: str | None = None,
                      finished_at: int | None = None, billable_calls: int = 0,
                      period: tuple[int, int] | None = None) -> None:
        def write(conn: sqlite3.Connection) -> None:
            row = conn.execute("SELECT * FROM report_run WHERE id=?", (report_id,)).fetchone()
            if row is None:
                raise ValueError("Report run not found")
            if period is not None:
                conn.execute("UPDATE report_run SET period_start=?,period_end=? WHERE id=?",
                             (period[0], period[1], report_id))
            conn.execute("UPDATE report_run SET state=?,top_json=?,report_json=?,provisional=?,"
                         "progress_json=?,finished_at=?,heartbeat_at=?,"
                         "billable_calls=COALESCE(billable_calls,0)+? "
                         "WHERE id=?", (state or row["state"],
                         json.dumps(top) if top is not None else row["top_json"],
                         json.dumps(report) if report is not None else row["report_json"],
                         int(provisional) if report is not None else row["provisional"],
                         json.dumps(progress) if progress is not None else row["progress_json"],
                         finished_at if finished_at is not None else row["finished_at"],
                         int(time.time()), billable_calls,
                         report_id))
        self._write(write)

    def put_sample_report(self, target: str, ts: int, period: tuple[int, int],
                          report: dict[str, Any], top: list[str]) -> int:
        """Store one synthetic past day (sample=1), replacing a sample for the same day."""
        def write(conn: sqlite3.Connection) -> int:
            conn.execute("DELETE FROM report_run WHERE target=? AND sample=1 AND period_start=?",
                         (target, period[0]))
            cursor = conn.execute(
                "INSERT INTO report_run (target,ts,trigger,state,top_json,report_json,provisional,"
                "progress_json,finished_at,heartbeat_at,billable_calls,period_start,period_end,"
                "sample) VALUES (?,?,'nightly','done',?,?,0,?,?,?,0,?,?,1)",
                (target, ts, json.dumps(top), json.dumps(report),
                 json.dumps({"message": "Report ready", "done": len(top), "total": len(top)}),
                 ts + 90, ts + 90, period[0], period[1]))
            return int(cursor.lastrowid)
        return self._write(write)

    def delete_sample_reports(self, target: str) -> int:
        def write(conn: sqlite3.Connection) -> int:
            return conn.execute("DELETE FROM report_run WHERE target=? AND sample=1",
                                (target,)).rowcount
        return self._write(write)

    def touch_report(self, report_id: int, now: int) -> bool:
        """Keep a claimed run alive while it waits; False once it's no longer running."""
        return self._write(lambda conn: conn.execute(
            "UPDATE report_run SET heartbeat_at=? WHERE id=? AND state='running'",
            (now, report_id)).rowcount) > 0

    def recover_reports(self, now: int, stale_seconds: int = 900) -> None:
        self._write(lambda conn: conn.execute(
            "UPDATE report_run SET state='failed' WHERE state='running' "
            "AND COALESCE(heartbeat_at,ts)<?", (now - stale_seconds,)))

    def previous_health_collectors(self, target: str, ts: int) -> dict[str, Any]:
        """The previous night's completed report retains the cumulative health counters."""
        rows = self.read("SELECT report_json FROM report_run WHERE target=? AND trigger='nightly' "
                         "AND state='done' AND sample=0 AND ts<? AND ts>=? ORDER BY ts DESC,id DESC LIMIT 1",
                         (target, ts, ts - 36 * 3600))
        report = json.loads(rows[0]["report_json"] or "{}") if rows else {}
        return (report.get("computed") or {}).get("health_collectors") or {}

    def monitored_table_queries(self, target: str) -> list[dict[str, Any]]:
        """Recent Jev requests retain query-to-table relationships and measured load."""
        rows = self.read("SELECT request_json FROM judge_run WHERE target=? AND request_json IS NOT NULL "
                         "ORDER BY ts DESC,id DESC LIMIT 30", (target,))
        queries = {}
        for row in rows:
            for query in json.loads(row["request_json"]).get("queries", []):
                queries.setdefault(query["query_key"], query)
        return sorted(queries.values(), key=lambda query: -float(query.get("db_time_share") or 0))

    def report(self, report_id: int) -> dict[str, Any] | None:
        rows = self.read("SELECT * FROM report_run WHERE id=?", (report_id,))
        return rows[0] if rows else None

    def analyze_jobs(self, target: str, state: str | None = None) -> list[dict[str, Any]]:
        if state is not None and state not in {"queued", "running", "done", "failed", "skipped"}:
            raise ValueError("Unknown analyze job state")
        return self.read(
            "SELECT * FROM analyze_job WHERE target=? AND (? IS NULL OR state=?) "
            "ORDER BY id DESC", (target, state, state)
        )

    def latest_analysis_jobs(self, target: str) -> dict[str, dict[str, Any]]:
        rows = self.read(
            "SELECT j.* FROM analyze_job j WHERE j.target=? AND j.id=("
            "SELECT MAX(id) FROM analyze_job WHERE target=j.target AND query_key=j.query_key)",
            (target,),
        )
        return {row["query_key"]: row for row in rows}

    def analyses_started_today(self, target: str, now: int,
                               reasons: tuple[str, ...] | None = None) -> int:
        """Automatic analyses spending the local day's allowances (or the one `reasons`
        picks), including running jobs."""
        self.ensure_open()
        with closing(self._connect()) as conn:
            return _started_in_day(conn, target, now, reasons)

    def enqueue_analysis(self, target: str, key: str, reason: str, priority: float,
                         now: int, *, cap: int | None = 100) -> dict[str, Any] | None:
        if reason not in {"new", "top15_stale", "user", "report"}:
            raise ValueError("Unknown analyze reason")
        def write(conn: sqlite3.Connection) -> dict[str, Any] | None:
            existing = conn.execute("SELECT * FROM analyze_job WHERE target=? AND query_key=? "
                                    "AND state IN ('queued','running') ORDER BY id DESC LIMIT 1",
                                    (target, key)).fetchone()
            if existing:
                if existing["state"] == "queued" and reason in ("user", "report"):
                    conn.execute("UPDATE analyze_job SET reason=?,priority=? WHERE id=?",
                                 (reason, priority, existing["id"]))
                    return dict(conn.execute("SELECT * FROM analyze_job WHERE id=?",
                                             (existing["id"],)).fetchone())
                return None
            if reason != "user" and cap is not None and _started_in_day(
                    conn, target, now, allowance_reasons(reason)) >= cap:
                return None
            cursor = conn.execute(
                "INSERT INTO analyze_job (target,query_key,reason,state,priority,enqueued_at,"
                "billable_calls) VALUES (?,?,?,?,?,?,0)",
                (target, key, reason, "queued", priority, now))
            return dict(conn.execute("SELECT * FROM analyze_job WHERE id=?",
                                     (cursor.lastrowid,)).fetchone())
        return self._write(write)

    def skip_analysis_once(self, target: str, key: str, reason: str, now: int,
                           explanation: str) -> dict[str, Any]:
        """Persist an automatic skip once per query identity, without spending the daily cap."""
        def write(conn: sqlite3.Connection) -> dict[str, Any]:
            row = conn.execute("SELECT * FROM analyze_job WHERE target=? AND query_key=? "
                               "AND state='skipped' ORDER BY id DESC LIMIT 1", (target, key)).fetchone()
            if row:
                return dict(row)
            row = conn.execute("SELECT * FROM analyze_job WHERE target=? AND query_key=? "
                               "AND state IN ('queued','running') ORDER BY id DESC LIMIT 1",
                               (target, key)).fetchone()
            if row:
                if row["reason"] == "user":
                    return dict(row)
                if row["state"] == "queued":
                    conn.execute("UPDATE analyze_job SET state='skipped',finished_at=?,error=? "
                                 "WHERE id=?", (now, explanation, row["id"]))
                    return dict(conn.execute("SELECT * FROM analyze_job WHERE id=?",
                                             (row["id"],)).fetchone())
                return dict(row)
            cursor = conn.execute(
                "INSERT INTO analyze_job (target,query_key,reason,state,priority,enqueued_at,"
                "finished_at,error,billable_calls) VALUES (?,?,?,'skipped',0,?,?,?,0)",
                (target, key, reason, now, now, explanation))
            return dict(conn.execute("SELECT * FROM analyze_job WHERE id=?",
                                     (cursor.lastrowid,)).fetchone())
        return self._write(write)

    def hold_analysis(self, job_id: int, *, owner: str = COLLECTOR_PROCESS,
                      attempt: int = 1) -> bool:
        """Keep a claimed automatic job pending for the next signed-in queue tick."""
        return self._write(lambda conn: conn.execute(
            "UPDATE analyze_job SET state='queued',started_at=NULL,error='signed out',owner=NULL "
            "WHERE id=? AND state='running' AND owner=? AND attempt=?",
            (job_id, owner, attempt)).rowcount == 1)

    def analysis_owned(self, job: dict[str, Any]) -> bool:
        """Check the claim before starting the next stage of paid work."""
        return bool(self.read("SELECT 1 FROM analyze_job WHERE id=? AND state='running' "
                              "AND owner=? AND attempt=?",
                              (job["id"], job["owner"], job["attempt"])))

    def retry_analysis(self, job: dict[str, Any]) -> bool:
        """Show an immediate retry while retaining the fenced running claim."""
        return self._write(lambda conn: conn.execute(
            "UPDATE analyze_job SET error=CASE WHEN error IN ('recovered_once','retrying_after_recovery') "
            "THEN 'retrying_after_recovery' ELSE 'retrying' END WHERE id=? AND state='running' "
            "AND owner=? AND attempt=?",
            (job["id"], job["owner"], job["attempt"])).rowcount == 1)

    def claim_analysis(self, target: str, now: int, *, cap: int = 100, reserve: int = 0,
                       newcomer_cap: int | None = None,
                       allow_automatic: bool = True, allow_report: bool = True, allow_user: bool = True, eligible_keys: list[str] | None = None) -> dict[str, Any] | None:
        """Start the next job: user clicks first, then report jobs, then the heaviest load.

        Re-analyses stop `reserve` short of the local day's cap, reserving capacity for
        report analyses. Queries new to the top spend `newcomer_cap` (the default newcomer
        allowance when None). User jobs run independently of both. Automatic jobs queued in
        the first monitored hour have their own allowance.
        """
        newcomer_cap = default_newcomer_daily_cap() if newcomer_cap is None else newcomer_cap
        def write(conn: sqlite3.Connection) -> dict[str, Any] | None:
            for running in conn.execute("SELECT * FROM analyze_job WHERE target=? AND state='running'",
                                        (target,)).fetchall():
                if _dead_analysis_owner(running):
                    conn.execute("UPDATE analyze_job SET state='queued',started_at=NULL,owner=NULL,"
                                 "attempt=attempt+1 WHERE id=?", (running["id"],))
            setting = conn.execute("SELECT value FROM monitor_setting WHERE target=? AND key='execute'",
                                   (target,)).fetchone()
            execute = setting is not None and setting[0] == "1"
            running = conn.execute("SELECT mode FROM analyze_job WHERE target=? AND state='running'",
                                   (target,)).fetchall()
            # Queries run one at a time under EXPLAIN ANALYZE, except the first monitored
            # hour's pass, which gets the first actions out within minutes.
            hour_start, hour_end = first_hour_window(conn, target)
            first_pass = hour_start <= now < hour_end
            limit = 1 if execute and not first_pass else PLAN_ANALYSIS_CONCURRENCY
            if len(running) >= limit or (not first_pass and
                                         any(row["mode"] != "explain" for row in running)):
                return None
            used = _started_in_day(conn, target, now, REANALYSIS_REASONS)
            newcomers = _started_in_day(conn, target, now, NEWCOMER_REASONS)
            row = conn.execute("SELECT * FROM analyze_job WHERE target=? AND state='queued' "
                               "AND (reason='user' OR ? IS NULL OR query_key IN (SELECT value FROM json_each(?))) "
                               "AND (reason!='report' OR ?) "
                               "AND (reason='user' AND ? OR reason='report' AND ? OR "
                               "reason='top15_stale' AND ? OR reason='new' AND ? "
                               f"OR ? AND {FIRST_HOUR_JOB}) "
                               f"ORDER BY {ANALYSIS_CLAIM_ORDER} LIMIT 1",
                               (target, json.dumps(eligible_keys) if eligible_keys is not None else None,
                                json.dumps(eligible_keys), int(allow_report), int(allow_user), int(allow_report and used < cap), int(allow_automatic and used < cap - reserve),
                                int(allow_automatic and newcomers < newcomer_cap),
                                int(allow_automatic), *first_hour_window(conn, target))).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE analyze_job SET state='running',started_at=?,owner=?,attempt=attempt+1,mode=?,"
                         "owner_host=?,owner_pid=?,owner_start=? WHERE id=?",
                         (now, COLLECTOR_PROCESS, "analyze" if execute else "explain",
                          OWNER_HOST, os.getpid(), _process_start(os.getpid()), row["id"]))
            return dict(conn.execute("SELECT * FROM analyze_job WHERE id=?", (row["id"],)).fetchone())
        return self._write(write)

    def update_analysis_priorities(self, target: str, weights: dict[str, float]) -> None:
        def write(conn: sqlite3.Connection) -> None:
            keys = conn.execute("SELECT id,query_key FROM analyze_job WHERE target=? "
                                "AND state='queued'", (target,)).fetchall()
            conn.executemany("UPDATE analyze_job SET priority=? WHERE id=?",
                             [(weights.get(row["query_key"], 0.0), row["id"]) for row in keys])
        self._write(write)

    def trim_automatic_analyses(self, target: str, keep: int,
                                reasons: tuple[str, ...] = ("new", "top15_stale")) -> int:
        """Drop queued automatic jobs of `reasons` beyond the `keep` heaviest; they never
        started. Jobs queued in the first monitored hour have their own allowance and stay."""
        def write(conn: sqlite3.Connection) -> int:
            rows = conn.execute("SELECT id FROM analyze_job WHERE target=? AND state='queued' "
                                "AND reason IN (SELECT value FROM json_each(?)) "
                                f"AND NOT ({FIRST_HOUR_JOB}) "
                                "ORDER BY priority DESC,enqueued_at,id LIMIT -1 OFFSET ?",
                                (target, json.dumps(list(reasons)),
                                 *first_hour_window(conn, target), max(0, keep))).fetchall()
            conn.executemany("DELETE FROM analyze_job WHERE id=?", [(row["id"],) for row in rows])
            return len(rows)
        return self._write(write)

    def priors(self, target: str) -> dict[str, tuple[int, float]]:
        """Each query's running (calls, ms) from the engine, as the last reading saw them."""
        return {row["query_key"]: (row["calls"], row["total_ms"]) for row in self.read(
            "SELECT query_key,calls,total_ms FROM query_prior WHERE target=?", (target,))}

    def reprioritize_queued(self, target: str, weights: dict[str, float]) -> int:
        """Re-rank waiting automatic jobs by their current weight, heaviest first."""
        def write(conn: sqlite3.Connection) -> int:
            return sum(conn.execute(
                "UPDATE analyze_job SET priority=? WHERE target=? AND state='queued' "
                "AND reason!='user' AND query_key=?", (weight, target, key)).rowcount
                for key, weight in weights.items())
        return self._write(write)

    def drop_queued_analyses(self, target: str, keys: list[str],
                             reasons: tuple[str, ...] = ("new", "top15_stale")) -> int:
        """Drop queued (never started) jobs of `reasons` for `keys`."""
        def write(conn: sqlite3.Connection) -> int:
            return conn.execute("DELETE FROM analyze_job WHERE target=? AND state='queued' "
                                "AND reason IN (SELECT value FROM json_each(?)) "
                                "AND query_key IN (SELECT value FROM json_each(?))",
                                (target, json.dumps(list(reasons)), json.dumps(keys))).rowcount
        return self._write(write)

    def finish_analysis(self, job_id: int, state: str, now: int, *, error: str | None = None,
                        analysis_ref: str | None = None, mode: str | None = None,
                        param_source: str | None = None, billable_calls: int = 0,
                        owner: str = COLLECTOR_PROCESS, attempt: int = 1) -> bool:
        if state not in {"done", "failed", "skipped"}:
            raise ValueError("Invalid terminal job state")
        return self._write(lambda conn: conn.execute(
            "UPDATE analyze_job SET state=?,finished_at=?,error=?,analysis_ref=?,mode=?,"
            "param_source=?,billable_calls=COALESCE(billable_calls,0)+? "
            "WHERE id=? AND state='running' AND owner=? AND attempt=?",
            (state, now, error, analysis_ref, mode, param_source, billable_calls,
             job_id, owner, attempt)).rowcount == 1)

    def cancel_analysis(self, job_id: int, now: int) -> dict[str, Any] | None:
        """Stop a queued or running job: it ends skipped with the error `cancelled`, so a
        late finish from its worker no longer applies and nothing reads it as the query's
        latest analysis. Returns the row, or None when it had already ended."""
        def write(conn: sqlite3.Connection) -> dict[str, Any] | None:
            changed = conn.execute(
                "UPDATE analyze_job SET state='skipped',error='cancelled',finished_at=? "
                "WHERE id=? AND state IN ('queued','running')", (now, job_id)).rowcount
            row = conn.execute("SELECT * FROM analyze_job WHERE id=?", (job_id,)).fetchone()
            if row is None or not changed:
                return None
            _insert_pulse(conn, row["target"], now, "analyze", True,
                          f"Analysis cancelled for {row['query_key']}")
            return dict(row)
        return self._write(write)

    def record_user_analysis(self, target: str, key: str, text: str, now: int, *,
                             analysis_ref: str | None, mode: str | None) -> dict[str, Any]:
        """A query analyzed from pasted SQL: keep its text and its finished analysis.

        The job has no start time, so it never counts against the daily analysis budget.
        """
        if mode not in (None, "analyze", "explain"):
            raise ValueError("mode must be analyze or explain")
        def write(conn: sqlite3.Connection) -> dict[str, Any]:
            if not conn.execute("SELECT 1 FROM query_text WHERE target=? AND query_key=? UNION "
                                "SELECT 1 FROM query_load WHERE target=? AND query_key=? UNION "
                                "SELECT 1 FROM query_sample WHERE target=? AND query_key=? UNION "
                                "SELECT 1 FROM stuck_sample WHERE target=? AND query_key=?",
                                (target, key) * 4).fetchone():
                count = conn.execute("SELECT COUNT(*) FROM (SELECT query_key FROM query_text WHERE target=? "
                                     "UNION SELECT query_key FROM query_load WHERE target=? UNION "
                                     "SELECT query_key FROM query_sample WHERE target=? UNION "
                                     "SELECT query_key FROM stuck_sample WHERE target=? AND query_key IS NOT NULL)",
                                     (target,) * 4).fetchone()[0]
                if count >= SERIES_QUERIES:
                    return {"job": None}
            conn.execute("INSERT INTO query_text VALUES (?,?,?,?,?) ON CONFLICT(target,query_key) "
                         "DO NOTHING", (target, key, text, now, now))
            if analysis_ref is None:
                return {"job": None}
            cursor = conn.execute(
                "INSERT INTO analyze_job (target,query_key,reason,state,priority,mode,"
                "enqueued_at,finished_at,analysis_ref,billable_calls) "
                "VALUES (?,?,'user','done',0,?,?,?,?,0)",
                (target, key, mode, now, now, analysis_ref))
            return {"job": dict(conn.execute("SELECT * FROM analyze_job WHERE id=?",
                                             (cursor.lastrowid,)).fetchone())}
        return self._write(write)

    def recover_analysis(self, now: int, timeout_s: int) -> None:
        def write(conn: sqlite3.Connection) -> None:
            rows = conn.execute("SELECT * FROM analyze_job WHERE state='running'").fetchall()
            for row in rows:
                if not _dead_analysis_owner(row) and row["started_at"] > now - timeout_s:
                    continue
                if row["error"] in {"recovered_once", "retrying_after_recovery"}:
                    conn.execute("UPDATE analyze_job SET state='failed',finished_at=?,"
                                 "error='Analysis timed out after restart recovery',owner=NULL,attempt=attempt+1 WHERE id=?",
                                 (now, row["id"]))
                else:
                    conn.execute("UPDATE analyze_job SET state='queued',started_at=NULL,"
                                 "error='recovered_once',owner=NULL,attempt=attempt+1 WHERE id=?", (row["id"],))
        self._write(write)

    def sync_indexes(self, target: str, indexes: list[dict[str, str]], ts: int) -> list[dict[str, Any]]:
        """Atomically replace a target's snapshot; an initial empty snapshot is valid."""
        def write(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            old = {(r["schema"], r["table"], r["index_name"]): r["definition"]
                   for r in conn.execute("SELECT * FROM index_snapshot WHERE target=?", (target,))}
            new = {(r["schema"], r["table"], r["index_name"]): r["definition"]
                   for r in indexes}
            seeded = conn.execute("SELECT 1 FROM monitor_setting WHERE target=? AND key='index_seeded'",
                                  (target,)).fetchone()
            changes = []
            if seeded:
                changed = {key for key in new.keys() & old.keys() if new[key] != old[key]}
                for kind, keys, source in (("dropped", (old.keys() - new.keys()) | changed, old),
                                           ("added", (new.keys() - old.keys()) | changed, new)):
                    for schema, table, name in sorted(keys):
                        definition = source[(schema, table, name)]
                        cursor = conn.execute(
                            'INSERT INTO index_change (target,ts,kind,schema,"table",index_name,definition) '
                            'VALUES (?,?,?,?,?,?,?)',
                            (target, ts, kind, schema, table, name, definition))
                        changes.append({"id": cursor.lastrowid, "target": target, "ts": ts,
                                        "kind": kind, "schema": schema, "table": table,
                                        "index_name": name, "definition": definition})
            conn.execute("DELETE FROM index_snapshot WHERE target=?", (target,))
            conn.executemany('INSERT INTO index_snapshot VALUES (?,?,?,?,?,?)',
                             [(target, *key, value, ts) for key, value in new.items()])
            conn.execute("INSERT OR IGNORE INTO monitor_setting VALUES (?,?,?)",
                         (target, "index_seeded", "1"))
            return changes
        return self._write(write)

    def record_stats(self, target: str, reading: dict[str, Any]) -> list[dict[str, Any]]:
        """Store one statistics reading (features.monitor.table_stats) and return setting changes.

        Activity between this reading and the last lands in the hour of this reading. A counter
        below its last value means the engine reset its statistics; the new value is then the
        activity since that reset.
        """
        ts = int(reading["taken_at"])
        bucket = ts // 3600 * 3600

        def delta(new: int, old: int | None) -> int:
            return 0 if old is None else new if new < old else new - old

        def write(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            meta = conn.execute("SELECT * FROM stats_meta WHERE target=?", (target,)).fetchone()
            seconds = max(0, ts - meta["taken_at"]) if meta else 0
            since = int(reading["counter_since"])
            # MySQL's counter age comes from its uptime, which drifts a second between readings.
            reset = bool(meta) and since > meta["counter_since"] + 120
            if meta and not reset:
                since = meta["counter_since"]
            counters = ("ins", "upd", "del", "hot_upd", "seq_scan", "idx_scan", "scan_rows")
            old_tables = {(row["schema"], row["table"]): row for row in conn.execute(
                "SELECT * FROM table_state WHERE target=?", (target,))}
            conn.execute("DELETE FROM table_state WHERE target=?", (target,))
            for table in reading["tables"]:
                key = (table["schema"], table["table"])
                old = old_tables.get(key)
                # After a reset the counters hold the activity since it.
                moved = ({name: table[name] for name in counters} if reset and old else
                         {name: delta(table[name], old[name] if old else None) for name in counters})
                if old is not None and any(old[name] > table[name] for name in counters):
                    moved = {name: table[name] for name in counters}
                    since = max(since, meta["taken_at"] if meta else ts)
                conn.execute('INSERT INTO table_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (target, *key, *(table[name] for name in counters),
                              table["live_rows"], table["dead_rows"], table["size_bytes"], ts,
                              table.get("vacuumed_at"), table.get("analyzed_at")))
                if seconds and any(moved.values()):
                    conn.execute(
                        'INSERT INTO table_activity VALUES (?,?,?,?,?,?,?,?,?,?,?,?) '
                        'ON CONFLICT(target,schema,"table",bucket_start) DO UPDATE SET '
                        'ins=ins+excluded.ins,upd=upd+excluded.upd,del=del+excluded.del,'
                        'hot_upd=hot_upd+excluded.hot_upd,seq_scan=seq_scan+excluded.seq_scan,'
                        'idx_scan=idx_scan+excluded.idx_scan,scan_rows=scan_rows+excluded.scan_rows,'
                        'seconds=seconds+excluded.seconds',
                        (target, *key, bucket, *(moved[name] for name in counters), seconds))
            old_indexes = {(row["schema"], row["table"], row["index_name"]): row
                           for row in conn.execute("SELECT * FROM index_state WHERE target=?",
                                                   (target,))}
            conn.execute("DELETE FROM index_state WHERE target=?", (target,))
            for index in reading["indexes"]:
                key = (index["schema"], index["table"], index["index_name"])
                old = old_indexes.get(key)
                scans = index["scans"] if reset and old else delta(index["scans"], old["scans"] if old else None)
                first_seen = old_indexes[key]["first_seen"] if key in old_indexes else ts
                conn.execute('INSERT INTO index_state (target,schema,"table",index_name,scans,'
                             'size_bytes,is_unique,is_primary,last_scan_at,first_seen,updated_at,'
                             'is_valid) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                             (target, *key, index["scans"], index["size_bytes"],
                              int(index["unique"]), int(index["primary"]),
                              index.get("last_scan_at"), first_seen, ts,
                              int(index.get("valid", True))))
                if seconds and scans:
                    conn.execute('INSERT INTO index_activity VALUES (?,?,?,?,?,?) '
                                 'ON CONFLICT(target,schema,"table",index_name,bucket_start) '
                                 'DO UPDATE SET scans=scans+excluded.scans',
                                 (target, *key, bucket, scans))
            changes = []
            old_settings = {row["name"]: row for row in conn.execute(
                "SELECT * FROM setting_state WHERE target=?", (target,))}
            for name, setting in reading["settings"].items():
                old = old_settings.get(name)
                changed_at = old["changed_at"] if old else None
                if old is not None and old["value"] != setting["value"]:
                    changed_at = ts
                    conn.execute("INSERT INTO setting_change (target,ts,name,old_value,new_value) "
                                 "VALUES (?,?,?,?,?)", (target, ts, name, old["value"], setting["value"]))
                    changes.append({"name": name, "old": old["value"], "new": setting["value"],
                                    "ts": ts})
                conn.execute("INSERT OR REPLACE INTO setting_state VALUES (?,?,?,?,?,?,?)",
                             (target, name, setting["value"], setting.get("unit"),
                              int(bool(setting.get("pending_restart"))), changed_at, ts))
            conn.execute("INSERT INTO stats_meta VALUES (?,?,?,?,?) ON CONFLICT(target) DO UPDATE "
                         "SET engine=excluded.engine,counter_since=excluded.counter_since,"
                         "taken_at=excluded.taken_at",
                         (target, reading["engine"], since, ts, ts))
            return changes
        return self._write(write)

    def actions(self, target: str, states: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        """A target's actions, best first, with their payloads decoded."""
        rows = self.read("SELECT * FROM action WHERE target=? AND (? IS NULL OR state IN "
                         "(SELECT value FROM json_each(?))) ORDER BY score DESC,id",
                         (target, None if states is None else 1, json.dumps(list(states or ()))))
        for row in rows:
            row["payload"] = json.loads(row.pop("payload_json"))
        return rows

    def write_actions(self, target: str, now: int, rows: list[dict[str, Any]]) -> None:
        """Insert or update actions by key in one transaction; each row may carry `events`."""
        def write(conn: sqlite3.Connection) -> None:
            for row in rows:
                existing = conn.execute("SELECT id FROM action WHERE target=? AND key=?",
                                        (target, row["key"])).fetchone()
                values = (row["type"], row["state"], float(row["score"]),
                          json.dumps(row["payload"], sort_keys=True), row.get("why"), now,
                          row.get("closed_at"), row.get("closed_reason"), row.get("snoozed_until"),
                          row.get("dismiss_reason"))
                if existing:
                    action_id = existing[0]
                    conn.execute("UPDATE action SET type=?,state=?,score=?,payload_json=?,"
                                 "why=COALESCE(?,why),updated_at=?,closed_at=?,closed_reason=?,"
                                 "snoozed_until=?,dismiss_reason=? WHERE id=?", (*values, action_id))
                else:
                    action_id = conn.execute(
                        "INSERT INTO action (target,key,type,state,score,payload_json,why,updated_at,"
                        "closed_at,closed_reason,snoozed_until,dismiss_reason,opened_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (target, row["key"], *values, row.get("opened_at", now))).lastrowid
                for kind, detail in row.get("events") or []:
                    conn.execute("INSERT INTO action_event (action_id,ts,kind,detail_json) "
                                 "VALUES (?,?,?,?)", (action_id, now, kind, json.dumps(detail)))
        self._write(write)

    def save_index_plan(self, target: str, plan: dict[str, Any]) -> None:
        self._write(lambda conn: conn.execute(
            "INSERT OR REPLACE INTO index_plan VALUES (?,?,?)",
            (target, int(plan["computed_at"]), json.dumps(plan, sort_keys=True))))

    def index_plan(self, target: str) -> dict[str, Any] | None:
        rows = self.read("SELECT plan_json FROM index_plan WHERE target=?", (target,))
        return json.loads(rows[0]["plan_json"]) if rows else None

    def action_events(self, action_id: int) -> list[dict[str, Any]]:
        rows = self.read("SELECT * FROM action_event WHERE action_id=? ORDER BY id", (action_id,))
        for row in rows:
            row["detail"] = json.loads(row.pop("detail_json"))
        return rows

    def inbox(self, state: str | None = None, target: str | None = None) -> list[dict[str, Any]]:
        if state and state not in {"new", "seen", "snoozed", "dismissed", "resolved"}:
            raise ValueError("Unknown inbox state")
        return self.read(
            "SELECT * FROM inbox_item WHERE (? IS NULL OR state=?) "
            "AND (? IS NULL OR target=?) ORDER BY severity DESC,updated_at DESC,id DESC",
            (state, state, target, target))

    def item_events(self, item_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        """Each item's own events, oldest first, in one read."""
        if not item_ids:
            return {}
        placeholders = ",".join("?" for _ in item_ids)
        found: dict[int, list[dict[str, Any]]] = {item_id: [] for item_id in item_ids}
        for row in self.read(f"SELECT item_id,ts,kind,detail_json FROM inbox_event "
                             f"WHERE item_id IN ({placeholders}) ORDER BY ts,id", tuple(item_ids)):
            found.setdefault(row["item_id"], []).append(row)
        return found

    def notified_at(self) -> dict[int, int]:
        """When each item last pushed, from its latest 'notified' event."""
        return {row["item_id"]: row["ts"] for row in self.read(
            "SELECT item_id,MAX(ts) AS ts FROM inbox_event WHERE kind='notified' "
            "GROUP BY item_id")}

    def inbox_events(self, after: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        return self.read("SELECT e.*,i.target,i.type,i.subject,i.state,i.severity,i.title,i.body_json "
                         "FROM inbox_event e JOIN inbox_item i ON i.id=e.item_id "
                         "WHERE e.id>? ORDER BY e.id LIMIT ?", (after, limit))

    def upsert_item(self, target: str, type: str, subject: str, severity: float,
                    title: str, body: dict[str, Any], ts: int, *, notify_cap: int | None = None,
                    notify: bool = True, reopen_resolved: bool = False,
                    materially_worse: bool = False,
                    notify_on_update: bool = False,
                    delivery: str | None = None) -> dict[str, Any]:
        """File or update the one item for (target, type, subject).

        `delivery` is the level this filing asks for (digest, inbox, notify); without it
        `notify` picks notify or inbox. An open item only moves up a level, and a promotion
        into the inbox or to notify counts like a new filing. Pushes stop at the target's
        daily budget; a filing over budget records `push_skipped` and rolls into the digest.
        """
        level = delivery or ("notify" if notify else "inbox")
        if level not in DELIVERY_LEVELS:
            raise ValueError("Unknown delivery level")
        if (isinstance(body.get("query_key"), str) and not body.get("query_keys") and
                not body.get("recovery_queries")):
            subject = body["query_key"]
        filed: list[str] = []
        def write(conn: sqlite3.Connection) -> dict[str, Any]:
            filed.clear()
            row = conn.execute(
                "SELECT * FROM inbox_item WHERE target=? AND type=? AND (subject=? OR "
                "json_extract(body_json,'$.query_key')=?) "
                "ORDER BY state IN ('new','seen','snoozed') DESC,created_at,id LIMIT 1",
                (target, type, subject, subject)).fetchone()
            if row is not None:
                muted_by = _matching_rule(conn, target, type,
                                          item_query_keys(subject, body), ts)
                if row["muted_by"] != muted_by:
                    conn.execute("UPDATE inbox_item SET muted_by=? WHERE id=?", (muted_by, row["id"]))
                    row = conn.execute("SELECT * FROM inbox_item WHERE id=?", (row["id"],)).fetchone()
            filing = body
            if type == "query_stopped_running" and row is None:
                filing = {**body, "occurrences": [{"start": body.get("filed_at", ts),
                           "end": None, "keys": [subject]}]}
            if type == "query_stopped_running" and row is not None:
                prior = json.loads(row["body_json"])
                recovered_at = prior.get("resumed_at") or row["resolved_at"] or row["updated_at"]
                if row["state"] == "resolved" and (
                        body.get("filed_at", ts) <= recovered_at or prior.get("settled")):
                    return dict(row)
                entries = prior.get("occurrences") or [
                    {"start": prior.get("filed_at", row["created_at"]),
                     "end": None, "keys": [subject]}]
                if row["state"] == "resolved":
                    entries = [*entries[:-1], {**entries[-1],
                               "end": prior.get("resumed_at", row["resolved_at"])}]
                    entries.append({"start": body.get("filed_at", ts), "end": None,
                                    "keys": [subject]})
                filing = {**body, "occurrences": entries[-60:]}
                if row["state"] != "resolved":
                    filing["filed_at"] = prior.get("filed_at", row["created_at"])
            detail = json.dumps(filing, sort_keys=True)
            kind = "updated"
            wants_push = False
            final = level
            if row is None:
                cursor = conn.execute(
                    "INSERT INTO inbox_item (target,type,subject,state,severity,title,body_json,"
                    "created_at,updated_at,delivery) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (target, type, subject, "new", severity, title, detail, ts, ts, level))
                item_id = cursor.lastrowid
                kind, wants_push = "created", True
            else:
                item_id = row["id"]
                stored = row["delivery"]
                if row["state"] != "dismissed" and json.loads(row["body_json"]).get("still_happening"):
                    detail = json.dumps({**filing, "still_happening": True}, sort_keys=True)
                # Episodes (NOTIFICATION_MODEL.md): only a measured further 2x counts as worse.
                # A dismissed episode stays silent until it gets that much worse; a cleared
                # and recurred condition arrives as a reopen of a resolved item instead.
                worse = materially_worse
                snooze_expired = row["state"] == "snoozed" and row["snoozed_until"] is not None and ts >= row["snoozed_until"]
                dismissed_return = row["state"] == "dismissed" and worse
                reopen = (row["state"] == "resolved" and
                          (row["resolved_by"] == "code" or reopen_resolved or worse)) or snooze_expired or dismissed_return
                if not reopen:
                    final = max(stored, level, key=DELIVERY_LEVELS.index)
                promoted = final != stored and not reopen
                if reopen:
                    kind, wants_push = "reopened", True
                    if dismissed_return:
                        detail = json.dumps({**filing, "still_happening": True}, sort_keys=True)
                elif promoted and row["state"] not in ("dismissed", "resolved"):
                    wants_push = final == "notify"
                elif worse and row["state"] not in ("dismissed", "snoozed", "resolved"):
                    kind, wants_push = "worse", True
                elif notify_on_update and row["state"] in ("new", "seen"):
                    wants_push = True
                if row["state"] in ("resolved", "dismissed") and not reopen:
                    return dict(row)
                if (detail == row["body_json"] and title == row["title"] and
                        severity == row["severity"] and not reopen and not worse and
                        not promoted):
                    return dict(row)
                conn.execute(
                    "UPDATE inbox_item SET state=?,severity=?,title=?,body_json=?,updated_at=?,"
                    "delivery=?,"
                    "resolved_at=CASE WHEN ? THEN NULL ELSE resolved_at END,"
                    "resolved_by=CASE WHEN ? THEN NULL ELSE resolved_by END,"
                    "snoozed_until=CASE WHEN ? THEN NULL ELSE snoozed_until END,"
                    "reopened_count=reopened_count+?,"
                    "dismissed_returned=CASE WHEN ? THEN 0 ELSE dismissed_returned+? END "
                    "WHERE id=?",
                    ("new" if reopen or (promoted and stored == "digest") else row["state"],
                     severity, title, detail, ts, final,
                     int(reopen), int(reopen), int(reopen), int(reopen),
                     int(reopen and row["state"] == "resolved"), int(dismissed_return), item_id))
            conn.execute("INSERT INTO inbox_event (item_id,ts,kind,detail_json) VALUES (?,?,?,?)",
                         (item_id, ts, kind, detail))
            _pin_item(conn, conn.execute("SELECT * FROM inbox_item WHERE id=?",
                                        (item_id,)).fetchone())
            filed.append(kind)
            # Muted items are still filed for history, but they never notify.
            muted_by = _matching_rule(conn, target, type, item_query_keys(subject, body), ts)
            conn.execute("UPDATE inbox_item SET muted_by=? WHERE id=?", (muted_by, item_id))
            if wants_push and final == "notify" and muted_by is None:
                event = notify_event(type, subject)
                cap = _push_cap(conn, target) if notify_cap is None else notify_cap
                day_start, day_end = local_day_bounds(ts)
                pushed = conn.execute(
                    "SELECT COUNT(*) FROM inbox_event e JOIN inbox_item i ON i.id=e.item_id "
                    "WHERE i.target=? AND e.kind='notified' AND e.ts>=? AND e.ts<? AND " +
                    UNCAPPED_PUSH_SQL, (target, day_start, day_end)).fetchone()[0]
                if not _notify_events(conn, target)[event]:
                    conn.execute("INSERT INTO inbox_event (item_id,ts,kind,detail_json) "
                                 "VALUES (?,?,?,?)", (item_id, ts, "updated",
                                                      json.dumps({"push_skipped": "event_off"})))
                elif event != "database_unreachable" and \
                        in_quiet_hours(_quiet_hours(conn, target), ts):
                    conn.execute("INSERT INTO inbox_event (item_id,ts,kind,detail_json) "
                                 "VALUES (?,?,?,?)", (item_id, ts, "updated",
                                                      json.dumps({"push_skipped": "quiet_hours"})))
                elif event == "database_unreachable" or pushed < cap:
                    conn.execute("INSERT INTO inbox_event (item_id,ts,kind,detail_json) "
                                 "VALUES (?,?,?,?)", (item_id, ts, "notified", "{}"))
                else:
                    conn.execute("INSERT INTO inbox_event (item_id,ts,kind,detail_json) "
                                 "VALUES (?,?,?,?)", (item_id, ts, "updated",
                                                      json.dumps({"push_skipped": "daily_limit"})))
            return dict(conn.execute("SELECT * FROM inbox_item WHERE id=?", (item_id,)).fetchone())
        item = self._write(write)
        if filed and filed[-1] in ("created", "reopened", "worse"):
            from shared import product_telemetry

            product_telemetry.inbox_item_filed(item, filed[-1], store=self)
        return item

    def action_item(self, item_id: int, action: str, ts: int, *, snooze_until: int | None = None,
                    resolved_by: str = "user", measured_recovery: bool = False) -> dict[str, Any] | None:
        if action not in {"seen", "snooze", "dismiss", "resolve", "reopen"}:
            raise ValueError("Unknown inbox action")
        def write(conn: sqlite3.Connection) -> dict[str, Any] | None:
            row = conn.execute("SELECT * FROM inbox_item WHERE id=?", (item_id,)).fetchone()
            if row is None:
                return None
            if (action == "resolve" and resolved_by != "user" and
                    row["state"] == "dismissed" and row["dismissed_returned"] and
                    not (measured_recovery and resolved_by == "code")):
                return dict(row)
            body = json.loads(row["body_json"])
            if action == "seen" and row["state"] in ("resolved", "dismissed"):
                # Seeing a closed item acknowledges its recovery; the state stays closed.
                if body.get("recovery_seen_at"):
                    return dict(row)
                body["recovery_seen_at"] = ts
                conn.execute("UPDATE inbox_item SET body_json=?,seen_count=seen_count+1 WHERE id=?",
                             (json.dumps(body, sort_keys=True), item_id))
                conn.execute("INSERT INTO inbox_event (item_id,ts,kind,detail_json) VALUES (?,?,?,?)",
                             (item_id, ts, "updated", json.dumps({"action": "seen"})))
                return dict(conn.execute("SELECT * FROM inbox_item WHERE id=?", (item_id,)).fetchone())
            state = {"snooze": "snoozed", "dismiss": "dismissed",
                     "resolve": "resolved", "reopen": "new"}.get(action, "seen")
            if state == row["state"]:
                return dict(row)
            if action == "resolve" and resolved_by == "jev":
                body["resolution_label"] = "Resolved by RDST"
            elif action == "reopen":
                body.pop("resolution_label", None)
                body.pop("still_happening", None)
            conn.execute("UPDATE inbox_item SET state=?,updated_at=?,resolved_at=?,resolved_by=?,body_json=?,"
                         "dismissed_at=?,snoozed_until=?,seen_count=seen_count+?,"
                         "reopened_count=reopened_count+?,"
                         "dismissed_returned=CASE WHEN ? THEN 0 ELSE dismissed_returned END WHERE id=?",
                         (state, ts, ts if action == "resolve" else None,
                          resolved_by if action == "resolve" else None,
                          json.dumps(body, sort_keys=True),
                          ts if action == "dismiss" else row["dismissed_at"],
                          snooze_until if action == "snooze" else None,
                          int(action == "seen"), int(action == "reopen"),
                          int(action == "reopen"), item_id))
            kind = {"dismiss": "dismissed", "resolve": "resolved", "reopen": "reopened"}.get(action, "updated")
            conn.execute("INSERT INTO inbox_event (item_id,ts,kind,detail_json) VALUES (?,?,?,?)",
                         (item_id, ts, kind, json.dumps({"action": action, "until": snooze_until,
                                                         **({"by": resolved_by}
                                                            if action == "resolve" else {})})))
            _pin_item(conn, conn.execute("SELECT * FROM inbox_item WHERE id=?",
                                        (item_id,)).fetchone())
            return dict(conn.execute("SELECT * FROM inbox_item WHERE id=?", (item_id,)).fetchone())
        return self._write(write)

    def demote_to_digest(self, item_id: int) -> None:
        """Move an item filed before the notification model out of the inbox, silently."""
        self._write(lambda conn: conn.execute(
            "UPDATE inbox_item SET delivery='digest' WHERE id=?", (item_id,)))

    def promote_item(self, item_id: int, delivery: str, ts: int) -> dict[str, Any] | None:
        """Raise an open item to a higher delivery level; a promotion to notify pushes."""
        rows = self.read("SELECT * FROM inbox_item WHERE id=?", (item_id,))
        if not rows or rows[0]["delivery"] == delivery:
            return rows[0] if rows else None
        row = rows[0]
        return self.upsert_item(row["target"], row["type"], row["subject"], row["severity"],
                                row["title"], json.loads(row["body_json"]), ts,
                                delivery=delivery)

    def notify_recovery(self, item_id: int, ts: int) -> bool:
        """Push a closed outage item's recovery, when its database has that switched on."""
        def write(conn: sqlite3.Connection) -> bool:
            row = conn.execute("SELECT * FROM inbox_item WHERE id=?", (item_id,)).fetchone()
            if (row is None or row["muted_by"] is not None or
                    not _notify_events(conn, row["target"])["database_recovered"]):
                return False
            conn.execute("INSERT INTO inbox_event (item_id,ts,kind,detail_json) VALUES (?,?,?,?)",
                         (item_id, ts, "notified", json.dumps({"recovered": True})))
            return True
        return self._write(write)

    def update_item_body(self, item_id: int, body: dict[str, Any],
                         *, include_resolved: bool = False) -> None:
        """Persist code-side observations without creating a new inbox event."""
        def write(conn: sqlite3.Connection) -> None:
            conn.execute("UPDATE inbox_item SET body_json=? WHERE id=? AND (? OR state!='resolved')",
                         (json.dumps(body, sort_keys=True), item_id, int(include_resolved)))
            item = conn.execute("SELECT * FROM inbox_item WHERE id=?", (item_id,)).fetchone()
            if item is not None:
                _pin_item(conn, item)
        self._write(write)

    # Mute rules (UI brief 5.6)

    def mute_rules(self, target: str | None = None) -> list[dict[str, Any]]:
        return self.read(
            "SELECT r.*,(SELECT COUNT(*) FROM inbox_item i WHERE i.muted_by=r.id) matched_count "
            "FROM mute_rule r WHERE (? IS NULL OR r.target IS NULL OR r.target=?) "
            "ORDER BY r.id DESC", (target, target))

    def create_mute_rule(self, *, target: str | None, query_key: str | None, type: str | None,
                         until: int | None, note: str | None, now: int) -> dict[str, Any]:
        if query_key is None and type is None:
            raise ValueError("A mute rule needs a query or an alert type")
        def write(conn: sqlite3.Connection) -> int:
            rule_id = conn.execute(
                "INSERT INTO mute_rule (target,query_key,type,until,created_at,note) "
                "VALUES (?,?,?,?,?,?)", (target, query_key, type, until, now, note)).lastrowid
            rule = dict(conn.execute("SELECT * FROM mute_rule WHERE id=?", (rule_id,)).fetchone())
            # Open items that the new rule covers move to the Muted tab right away.
            for item in conn.execute("SELECT id,target,type,subject,body_json FROM inbox_item "
                                     "WHERE muted_by IS NULL AND state NOT IN ('resolved','dismissed') "
                                     "AND (? IS NULL OR target=?) AND (? IS NULL OR type=?)",
                                     (target, target, type, type)).fetchall():
                keys = item_query_keys(item["subject"], json.loads(item["body_json"]))
                if rule_matches(rule, item["target"], item["type"], keys, now):
                    conn.execute("UPDATE inbox_item SET muted_by=? WHERE id=?", (rule_id, item["id"]))
            return rule_id
        rule_id = self._write(write)
        return next(rule for rule in self.mute_rules() if rule["id"] == rule_id)

    def delete_mute_rule(self, rule_id: int) -> bool:
        def write(conn: sqlite3.Connection) -> bool:
            deleted = conn.execute("DELETE FROM mute_rule WHERE id=?", (rule_id,)).rowcount
            if deleted:
                conn.execute("UPDATE inbox_item SET muted_by=NULL WHERE muted_by=?", (rule_id,))
            return bool(deleted)
        return self._write(write)

    # Pulses (UI brief 5.2)

    def record_pulse(self, target: str, ts: int, kind: str, ok: bool, detail: str) -> None:
        self._write(lambda conn: _insert_pulse(conn, target, ts, kind, ok, detail))

    def pulses(self, target: str, limit: int = 5, after: int = 0) -> list[dict[str, Any]]:
        """Newest first, or oldest first when reading forward from `after`."""
        failure = _STORAGE_ERRORS.get(self.path)
        if failure:
            return [{"id": 0, "target": target, "ts": int(self.clock()), "kind": "collect",
                     "ok": 0, "detail": failure[1]}]
        if after:
            return self.read("SELECT * FROM monitor_pulse WHERE target=? AND id>? "
                             "ORDER BY id LIMIT ?", (target, after, limit))
        return self.read("SELECT * FROM monitor_pulse WHERE target=? ORDER BY id DESC LIMIT ?",
                         (target, limit))

    # Report progress events (UI brief 5.8)

    def append_report_event(self, report_id: int, event: dict[str, Any], ts: int) -> int:
        def write(conn: sqlite3.Connection) -> int:
            seq = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM report_event WHERE report_id=?",
                               (report_id,)).fetchone()[0]
            conn.execute("INSERT INTO report_event VALUES (?,?,?,?)",
                         (report_id, seq, ts, json.dumps(event, sort_keys=True)))
            return seq
        return self._write(write)

    def report_events(self, report_id: int, after: int = -1) -> list[dict[str, Any]]:
        return [{"seq": row["seq"], "event": json.loads(row["data_json"])} for row in self.read(
            "SELECT seq,data_json FROM report_event WHERE report_id=? AND seq>? ORDER BY seq",
            (report_id, after))]

    def cancel_report(self, report_id: int, now: int) -> dict[str, Any] | None:
        def write(conn: sqlite3.Connection) -> dict[str, Any] | None:
            row = conn.execute("SELECT * FROM report_run WHERE id=?", (report_id,)).fetchone()
            if row is None:
                return None
            if row["state"] == "running":
                conn.execute("UPDATE report_run SET state='cancelled',finished_at=?,"
                             "progress_json=? WHERE id=?",
                             (now, json.dumps({"message": "Report cancelled", "done": 0,
                                               "total": 0}), report_id))
            return dict(conn.execute("SELECT * FROM report_run WHERE id=?",
                                     (report_id,)).fetchone())
        return self._write(write)

    # Fix verdicts (UI brief 5.5)

    def save_fix_verdict(self, verdict: dict[str, Any]) -> None:
        columns = ("target", "query_key", "index_change_id", "index_name", "table", "status",
                   "detected_at", "before_mean_ms", "before_qps", "after_mean_ms", "after_qps",
                   "after_calls", "updated_at")
        self._write(lambda conn: conn.execute(
            "INSERT INTO fix_verdict (" + ",".join(f'"{c}"' for c in columns) + ") VALUES (" +
            ",".join("?" for _ in columns) + ") ON CONFLICT(target,query_key,index_change_id) "
            "DO UPDATE SET status=excluded.status,after_mean_ms=excluded.after_mean_ms,"
            "after_qps=excluded.after_qps,after_calls=excluded.after_calls,"
            "updated_at=excluded.updated_at", tuple(verdict[c] for c in columns)))

    def system_queries(self, target: str) -> set[str]:
        return {row["query_key"] for row in self.read(
            "SELECT query_key FROM system_query WHERE target=?", (target,))}

    def record_attention_episodes(self, rows: list[tuple[str, str, str, float, int, float]]) -> None:
        self._write(lambda conn: conn.executemany(
            "INSERT OR IGNORE INTO attention_episode "
            "(target,query_key,kind,baseline_mean_ms,settled_at,judged_qps) "
            "VALUES (?,?,?,?,?,?)", rows))

    def clear_attention_episodes(self, target: str,
                                 rows: list[tuple[str, str]]) -> None:
        self._write(lambda conn: conn.executemany(
            "DELETE FROM attention_episode WHERE target=? AND query_key=? AND kind=?",
            [(target, key, kind) for key, kind in rows]))

    def record_judge_run(self, target: str, ts: int, shortlist: list[dict[str, Any]],
                         *, request: dict[str, Any] | None = None,
                         response: dict[str, Any] | None = None,
                         dry_run: bool = True, outcome: str = "noop",
                         billable_calls: int = 0) -> None:
        self._write(lambda conn: conn.execute(
            "INSERT INTO judge_run (target,ts,shortlist_json,request_json,response_json,dry_run,outcome,"
            "billable_calls) VALUES (?,?,?,?,?,?,?,?)",
            (target, ts, json.dumps(shortlist), json.dumps(request) if request is not None else None,
             json.dumps(response) if response is not None else None, int(dry_run), outcome,
             billable_calls)))

    def action_item_for_subject(self, target: str, type: str, subject: str, ts: int) -> None:
        rows = self.read("SELECT id,state FROM inbox_item WHERE target=? AND type=? AND subject=?",
                         (target, type, subject))
        if rows and rows[0]["state"] != "resolved":
            self.action_item(rows[0]["id"], "resolve", ts, resolved_by="code")

    def resolve_sign_in_items(self, ts: int) -> int:
        """Resolve every open sign-in item once the account session works again."""
        rows = self.read("SELECT id FROM inbox_item WHERE type='monitoring_issue' AND "
                         "subject IN ('jev_login','llm_login') AND state!='resolved'")
        for row in rows:
            self.action_item(row["id"], "resolve", ts, resolved_by="code")
        return len(rows)

    def sync_stuck_items(self, target: str, ts: int, stuck: list[dict[str, Any]]) -> None:
        """Reconcile the code-only stuck rule after a successful activity sample.

        The item carries the statement's backend pid and how long it has run, so the
        Inbox shows the real pid in its steps."""
        active: dict[str, dict[str, Any]] = {}
        for row in stuck:
            key = row.get("query_key")
            if key and (key not in active or float(row.get("duration_s") or 0) >
                        float(active[key].get("duration_s") or 0)):
                active[key] = row
        for key, row in active.items():
            body: dict[str, Any] = {"query_key": key}
            if row.get("pid") is not None:
                body["pid"] = int(row["pid"])
            if row.get("duration_s") is not None:
                body["duration_s"] = float(row["duration_s"])
            self.upsert_item(target, "stuck_query", key, 0.9, "Stuck query", body, ts)
        for item in self.inbox(target=target):
            if (item["type"] == "stuck_query" and item["state"] != "resolved"
                    and item["subject"] not in active):
                self.action_item(item["id"], "resolve", ts, resolved_by="code")

    @staticmethod
    def _trim_tracked_queries(conn: sqlite3.Connection, target: str,
                              retained: set[str] | None = None) -> None:
        keys = ("SELECT query_key FROM query_text WHERE target=? UNION "
                "SELECT query_key FROM query_load WHERE target=? UNION "
                "SELECT query_key FROM query_sample WHERE target=? UNION "
                "SELECT query_key FROM stuck_sample WHERE target=? AND query_key IS NOT NULL")
        if retained is None and conn.execute(
                f"SELECT COUNT(*) FROM ({keys})", (target,) * 4).fetchone()[0] <= SERIES_QUERIES:
            return
        conn.execute("CREATE TEMP TABLE retained_query (query_key TEXT PRIMARY KEY)")
        if retained is None:
            conn.execute(f"INSERT INTO retained_query SELECT query_key FROM ({keys}) k ORDER BY "
                         "COALESCE((SELECT total_ms FROM query_load l WHERE l.target=? "
                         "AND l.query_key=k.query_key),0) DESC,query_key LIMIT ?",
                         (target, target, target, target, target, SERIES_QUERIES))
        else:
            conn.executemany("INSERT INTO retained_query VALUES (?)", ((key,) for key in retained))
        outside = "query_key NOT IN (SELECT query_key FROM retained_query)"
        # Fill missing parent buckets before folding identities with different retained tiers.
        for child, parent in zip(RESOLUTIONS, RESOLUTIONS[1:]):
            conn.execute("INSERT INTO stmt_series SELECT target,query_key,?,"
                         "bucket_start / ? * ?,SUM(calls),SUM(total_ms),SUM(rows),"
                         "SUM(blks_hit),SUM(blks_read),SUM(temp_blks) FROM stmt_series s WHERE target=? "
                         f"AND query_key!=? AND {outside} AND res=? AND NOT EXISTS ("
                         "SELECT 1 FROM stmt_series p WHERE p.target=s.target "
                         "AND p.query_key=s.query_key AND p.res=? "
                         "AND p.bucket_start=s.bucket_start / ? * ?) AND EXISTS ("
                         "SELECT 1 FROM stmt_series a WHERE a.target=s.target "
                         "AND a.query_key=? AND a.res=? "
                         "AND a.bucket_start=s.bucket_start / ? * ?) "
                         "GROUP BY target,query_key,bucket_start / ?",
                         (parent, parent, parent, target, EVERYTHING_ELSE, child,
                          parent, parent, parent, EVERYTHING_ELSE, parent, parent, parent, parent))
        conn.execute("INSERT INTO stmt_series SELECT target,?,res,bucket_start,SUM(calls),"
                     "SUM(total_ms),SUM(rows),SUM(blks_hit),SUM(blks_read),SUM(temp_blks) FROM stmt_series "
                     f"WHERE target=? AND query_key!=? AND {outside} GROUP BY res,bucket_start "
                     "ON CONFLICT(target,query_key,res,bucket_start) DO UPDATE SET "
                     "calls=calls+excluded.calls,total_ms=total_ms+excluded.total_ms,"
                     "rows=rows+excluded.rows,blks_hit=blks_hit+excluded.blks_hit,"
                     "blks_read=blks_read+excluded.blks_read,temp_blks=temp_blks+excluded.temp_blks", (EVERYTHING_ELSE, target, EVERYTHING_ELSE))
        conn.execute(f"DELETE FROM stmt_series WHERE target=? AND query_key!=? AND {outside}",
                     (target, EVERYTHING_ELSE))
        for table in ("query_text", "query_load", "query_sample", "query_tier", "query_tier_period", "query_ui"):
            conn.execute(f"DELETE FROM {table} WHERE target=? AND {outside}", (target,))
        conn.execute(f"UPDATE stuck_sample SET query_key=NULL WHERE target=? AND {outside}", (target,))
        conn.execute("DROP TABLE retained_query")

    def record(
        self,
        target: str,
        ts: int,
        statements: list[dict[str, Any]],
        texts: dict[str, str],
        stuck: list[dict[str, Any]],
        db: dict[str, Any] | None = None,
        samples: list[dict[str, str]] | None = None,
        system_keys: set[str] | None = None,
        queries_read: int | None = None,
        workload_checked: bool = False,
        untracked: tuple[int, float] | None = None,
        priors: dict[str, tuple[int, float]] | None = None,
    ) -> None:
        """One collector reading. `priors` are the engine's running (calls, ms) per query. `queries_read` counts the queries it saw when that differs
        from the statements recorded (a baseline reading records none). `untracked` is the
        (calls, ms) of statements the reading didn't fetch, kept under everything else."""
        bucket = ts // 60 * 60
        def write(conn: sqlite3.Connection) -> None:
            outside = untracked
            enabled = conn.execute("SELECT value FROM monitor_setting WHERE target=? AND key='enabled'",
                                   (target,)).fetchone()
            if enabled is not None and enabled[0] == "0":
                return
            rows = statements
            counters = db
            if workload_checked:
                previous = conn.execute(
                    "SELECT value FROM monitor_setting WHERE target=? AND key='collector_baseline'",
                    (target,)).fetchone()
                stamp = f"{COLLECTOR_PROCESS}:{ts}"
                process, _, reading = (previous[0] if previous else "").partition(":")
                gap = bool(reading) and (ts < int(reading) or
                                         ts - int(reading) > COLLECTION_GAP_SECONDS)
                history = previous is not None or conn.execute(
                    "SELECT 1 FROM monitor_pulse WHERE target=? AND kind='collect' LIMIT 1",
                    (target,)).fetchone() is not None
                if (history and process != COLLECTOR_PROCESS) or gap:
                    rows, counters, outside = [], None, None
                conn.execute("INSERT INTO monitor_setting(target,key,value) VALUES (?,?,?) "
                             "ON CONFLICT(target,key) DO UPDATE SET value=excluded.value",
                             (target, "collector_baseline", stamp))
            for key in system_keys or ():
                # RDST's own and relation-free statements (BEGIN, SET, catalog reads) are
                # not workload. Forget any history recorded before they were recognized.
                if conn.execute("INSERT OR IGNORE INTO system_query VALUES (?,?)",
                                (target, key)).rowcount:
                    for table in ("stmt_series", "query_text", "query_sample", "query_load", "query_tier", "query_tier_period", "query_ui"):
                        conn.execute(f"DELETE FROM {table} WHERE target=? AND query_key=?",
                                     (target, key))
            if rows or counters is not None:
                watermark = conn.execute(
                    "SELECT value FROM monitor_setting WHERE target=? AND key='rollup_until_60'",
                    (target,)).fetchone()
                if watermark and bucket // 300 * 300 < int(watermark[0]):
                    conn.execute("INSERT OR IGNORE INTO dirty_rollup VALUES (?,?,?)",
                                 (target, 60, bucket // 300 * 300))
            tracked = {row[0] for row in conn.execute(
                "SELECT query_key FROM query_text WHERE target=? UNION "
                "SELECT query_key FROM query_load WHERE target=? UNION "
                "SELECT query_key FROM query_sample WHERE target=? UNION "
                "SELECT query_key FROM stuck_sample WHERE target=? AND query_key IS NOT NULL",
                (target, target, target, target))}
            loads = {row["query_key"]: row["total_ms"] for row in rows}
            candidates = set(texts) | set(loads) | {sample["query_key"] for sample in samples or []}
            candidates.update(row["query_key"] for row in stuck if row.get("query_key"))
            saved = conn.execute("SELECT value FROM monitor_setting WHERE target=? "
                                 "AND key='candidate_loads'", (target,)).fetchone()
            pending = json.loads(saved[0]) if saved else {}
            previous = {row[0]: (row[1], row[2]) for row in conn.execute(
                "SELECT query_key,total_ms,updated_at FROM query_load WHERE target=?", (target,))}

            def weight(key: str) -> float:
                value, updated = previous.get(key, pending.get(key, (0.0, ts)))
                if ts < updated - RECENT_LOAD_SECONDS:
                    return value
                age = max(0, ts - updated)
                return (value / (1.0 + age / RECENT_LOAD_SECONDS)
                        if age < RECENT_LOAD_SECONDS else 0.0) + loads.get(key, 0.0)

            # Identity slots and excluded load counters each share the single configured cap.
            pool = tracked | candidates | set(pending)
            selected = set(heapq.nsmallest(SERIES_QUERIES, pool,
                                          key=lambda key: (-weight(key), key not in tracked, key)))
            displaced = tracked - selected
            entrants = selected - tracked
            if displaced:
                self._trim_tracked_queries(conn, target, selected)
            candidate_counts = {key: ((weight(key), ts) if key in loads else
                                      previous.get(key, pending.get(key))) for key in heapq.nsmallest(
                SERIES_QUERIES, pool - selected, key=lambda key: (-weight(key), key))
                if weight(key) > 0}
            conn.execute("INSERT INTO monitor_setting(target,key,value) VALUES (?,?,?) "
                         "ON CONFLICT(target,key) DO UPDATE SET value=excluded.value",
                         (target, "candidate_loads", json.dumps(candidate_counts)))
            # Entrants' prior traffic already belongs to the aggregate; only their load
            # counter carries forward for ranking.
            conn.executemany("INSERT INTO query_load VALUES (?,?,?,?)",
                             ((target, key, weight(key) - loads.get(key, 0.0), ts)
                              for key in entrants))
            tracked = selected
            retained_texts = {key: value for key, value in texts.items() if key in tracked}
            conn.executemany(
                "INSERT INTO query_text VALUES (?,?,?,COALESCE((SELECT MIN(bucket_start) "
                "FROM stmt_series WHERE target=? AND query_key=?),?),?) "
                "ON CONFLICT(target,query_key) DO UPDATE SET "
                "text=excluded.text,last_seen=excluded.last_seen",
                ((target, key, value.encode("utf-8")[:8192].decode("utf-8", "ignore"),
                  target, key, ts, ts) for key, value in retained_texts.items()))
            if retained_texts and not workload_checked:
                from shared.monitor.statements import is_workload

                conn.executemany(
                    "INSERT INTO query_ui(target,query_key,workload) VALUES (?,?,0) "
                    "ON CONFLICT(target,query_key) DO UPDATE SET workload=0",
                    ((target, key) for key, text in retained_texts.items() if not is_workload(text)))
            conn.executemany(
                "INSERT INTO query_load VALUES (?,?,?,?) ON CONFLICT(target,query_key) "
                "DO UPDATE SET total_ms=CASE WHEN excluded.updated_at<updated_at-86400 THEN total_ms "
                "WHEN excluded.updated_at-updated_at>=86400 THEN excluded.total_ms ELSE "
                "total_ms/(1.0+MAX(0,excluded.updated_at-updated_at)/86400.0)+excluded.total_ms END,"
                "updated_at=MAX(updated_at,excluded.updated_at)",
                ((target, row["query_key"], row["total_ms"], ts) for row in rows if row["query_key"] in tracked))
            ranked = conn.execute("SELECT 1 FROM monitor_setting WHERE target=? "
                                  "AND key='tiers_ranked_at'", (target,)).fetchone()
            if not ranked:
                self._rank_queries(conn, target, ts)
            ranks = {row["query_key"]: row["rank"] for row in conn.execute(
                "SELECT query_key,rank FROM query_tier WHERE target=?", (target,))}
            if len(ranks) < SERIES_QUERIES and (
                    any(row["query_key"] not in ranks for row in rows) or conn.execute(
                        "SELECT 1 FROM query_text q WHERE target=? AND NOT EXISTS "
                        "(SELECT 1 FROM query_tier t WHERE t.target=q.target "
                        "AND t.query_key=q.query_key) AND NOT EXISTS (SELECT 1 FROM query_ui u "
                        "WHERE u.target=q.target AND u.query_key=q.query_key AND u.workload=0) "
                        "LIMIT 1", (target,)).fetchone()):
                self._rank_queries(conn, target, ts)
                ranks = {row["query_key"]: row["rank"] for row in conn.execute(
                    "SELECT query_key,rank FROM query_tier WHERE target=?", (target,))}
            excluded = {row["query_key"] for row in conn.execute(
                "SELECT query_key FROM query_ui WHERE target=? AND workload=0", (target,))}
            detailed = []
            other = {"query_key": EVERYTHING_ELSE, "calls": 0, "total_ms": 0.0,
                     "rows": 0, "blks_hit": 0, "blks_read": 0, "temp_blks": 0}
            for row in rows:
                if row["query_key"] in excluded:
                    continue
                if row["query_key"] in ranks:
                    detailed.append(row)
                else:
                    other["calls"] += row["calls"]
                    other["total_ms"] += row["total_ms"]
                    other["temp_blks"] += row.get("temp_blks", 0)
            if outside is not None:
                other["calls"] += outside[0]
                other["total_ms"] += outside[1]
            if other["calls"] or other["total_ms"]:
                detailed.append(other)
            for sample in samples or []:
                if sample["query_key"] not in tracked:
                    continue
                self._insert_sample(conn, target, ts, sample["query_key"], sample["text"])
            if priors:
                conn.executemany(
                    "INSERT INTO query_prior VALUES (?,?,?,?) ON CONFLICT(target,query_key) "
                    "DO UPDATE SET calls=excluded.calls,total_ms=excluded.total_ms",
                    ((target, key, calls, ms) for key, (calls, ms) in priors.items() if calls > 0))
            conn.executemany(
                "INSERT INTO stmt_series VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(target,query_key,res,bucket_start) DO UPDATE SET "
                "calls=calls+excluded.calls,total_ms=total_ms+excluded.total_ms,"
                "rows=rows+excluded.rows,blks_hit=blks_hit+excluded.blks_hit,"
                "blks_read=blks_read+excluded.blks_read,temp_blks=temp_blks+excluded.temp_blks",
                ((target, row["query_key"], 60, bucket, row["calls"], row["total_ms"],
                  row.get("rows", 0), row.get("blks_hit", 0), row.get("blks_read", 0), row.get("temp_blks", 0))
                 for row in detailed))
            for row in stuck:
                conn.execute(
                    "INSERT OR REPLACE INTO stuck_sample VALUES (?,?,?,?,?,?)",
                    (target, ts, row["pid"], row.get("query_key") if row.get("query_key") in tracked else None,
                     row.get("state"), row["duration_s"]),
                )
            if counters is not None:
                conn.execute(
                    "INSERT INTO db_series (target,res,bucket_start,calls,total_ms,active_conns,"
                    "max_conns,cache_hit_ratio,xact_commit,xact_rollback,deadlocks,blks_hit,blks_read,lock_waits) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(target,res,bucket_start) DO UPDATE SET "
                    "calls=calls+excluded.calls,total_ms=total_ms+excluded.total_ms,"
                    "active_conns=excluded.active_conns,max_conns=excluded.max_conns,"
                    "cache_hit_ratio=excluded.cache_hit_ratio,"
                    "xact_commit=xact_commit+excluded.xact_commit,"
                    "xact_rollback=xact_rollback+excluded.xact_rollback,"
                    "deadlocks=deadlocks+excluded.deadlocks,"
                    "blks_hit=COALESCE(blks_hit,0)+COALESCE(excluded.blks_hit,0),"
                    "blks_read=COALESCE(blks_read,0)+COALESCE(excluded.blks_read,0),"
                    "lock_waits=excluded.lock_waits",
                    (target, 60, bucket, counters["calls"], counters["total_ms"],
                     counters.get("active_conns"), counters.get("max_conns"),
                     counters.get("cache_hit_ratio"), counters.get("xact_commit", 0),
                     counters.get("xact_rollback", 0), counters.get("deadlocks", 0),
                     counters.get("blks_hit"), counters.get("blks_read"), counters.get("lock_waits")),
                )
            self._rollup(conn, target, ts)
            count = len(rows) if queries_read is None else queries_read
            _insert_pulse(conn, target, ts, "collect", True,
                          f"Read {count} {'query' if count == 1 else 'queries'}")
        self._write(write, retry_s=RECORD_RETRY_S)

    def record_sample(self, target: str, query_key: str, text: str, ts: int) -> None:
        """Retain a live instance without running the collector's rollup."""
        self._write(lambda conn: self._insert_sample(conn, target, ts, query_key, text))

    @staticmethod
    def _insert_sample(conn: sqlite3.Connection, target: str, ts: int,
                       query_key: str, text: str) -> None:
        enabled = conn.execute("SELECT value FROM monitor_setting WHERE target=? AND key='enabled'",
                               (target,)).fetchone()
        if enabled is not None and enabled[0] == "0":
            return
        if not conn.execute("SELECT 1 FROM query_text WHERE target=? AND query_key=? UNION "
                            "SELECT 1 FROM query_load WHERE target=? AND query_key=? UNION "
                            "SELECT 1 FROM query_sample WHERE target=? AND query_key=? UNION "
                            "SELECT 1 FROM stuck_sample WHERE target=? AND query_key=? LIMIT 1",
                            (target, query_key) * 4).fetchone():
            count = conn.execute("SELECT COUNT(*) FROM (SELECT query_key FROM query_text "
                                 "WHERE target=? UNION SELECT query_key FROM query_load WHERE target=? "
                                 "UNION SELECT query_key FROM query_sample WHERE target=? UNION "
                                 "SELECT query_key FROM stuck_sample WHERE target=? AND query_key IS NOT NULL)",
                                 (target,) * 4).fetchone()[0]
            if count >= SERIES_QUERIES:
                return
        value = text.encode("utf-8")[:8192].decode("utf-8", "ignore")
        if not value:
            return
        conn.execute("DELETE FROM query_sample WHERE target=? AND query_key=? AND text=?",
                     (target, query_key, value))
        conn.execute("INSERT INTO query_sample(target,query_key,text,observed_at) "
                     "VALUES (?,?,?,?)", (target, query_key, value, ts))
        conn.execute("DELETE FROM query_sample WHERE target=? AND query_key=? AND id NOT IN "
                     "(SELECT id FROM query_sample WHERE target=? AND query_key=? "
                     "ORDER BY id DESC LIMIT ?)",
                     (target, query_key, target, query_key, SAMPLES_PER_QUERY))

    def rollup(self, target: str, now: int | None = None) -> None:
        self._write(lambda conn: self._rollup(conn, target, int(self.clock()) if now is None else now))

    def _rollup(self, conn: sqlite3.Connection, target: str, now: int) -> None:
        conn.execute("DELETE FROM incident_pin WHERE "
                     "NOT EXISTS (SELECT 1 FROM inbox_item WHERE id=incident_pin.item_id)")
        watermarks = {row["key"]: int(row["value"]) for row in conn.execute(
            "SELECT key,value FROM monitor_setting WHERE target=? AND key IN "
            "('rollup_until_60','rollup_until_300','rollup_until_3600')", (target,))}
        dirty_pending = conn.execute(
            "SELECT 1 FROM dirty_rollup WHERE target=? LIMIT 1", (target,)).fetchone()
        for source, dest in zip(RESOLUTIONS, RESOLUTIONS[1:]):
            closed = now // dest * dest
            setting = f"rollup_until_{source}"
            beginning = watermarks.get(setting, 0)
            if closed > beginning:
                conn.execute(
                    "INSERT OR IGNORE INTO stmt_series "
                    "SELECT target,query_key,?,bucket_start / ? * ?,"
                    "SUM(calls),SUM(total_ms),SUM(rows),SUM(blks_hit),SUM(blks_read),SUM(temp_blks) "
                    "FROM stmt_series s WHERE target=? AND res=? AND bucket_start>=? AND bucket_start<? "
                    + ("AND NOT (" + _tail_period_sql("s") + ") " if dest == 86400 else "") +
                    "GROUP BY target,query_key,bucket_start / ?",
                    (dest, dest, dest, target, source, beginning, closed, dest),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO db_series (target,res,bucket_start,calls,total_ms,"
                    "active_conns,max_conns,cache_hit_ratio,xact_commit,xact_rollback,deadlocks,"
                    "blks_hit,blks_read,lock_waits) "
                    "SELECT target,?,bucket_start / ? * ?,SUM(calls),SUM(total_ms),"
                    "MAX(active_conns),MAX(max_conns),"
                    "CASE WHEN COALESCE(SUM(blks_hit),0)+COALESCE(SUM(blks_read),0)>0 "
                    "THEN CAST(SUM(blks_hit) AS REAL)/(SUM(blks_hit)+SUM(blks_read)) "
                    "ELSE AVG(cache_hit_ratio) END,"
                    "SUM(xact_commit),SUM(xact_rollback),SUM(deadlocks),"
                    "SUM(blks_hit),SUM(blks_read),MAX(lock_waits) "
                    "FROM db_series WHERE target=? AND res=? AND bucket_start>=? AND bucket_start<? "
                    "GROUP BY target,bucket_start / ?",
                    (dest, dest, dest, target, source, beginning, closed, dest),
                )
                conn.execute("INSERT INTO monitor_setting VALUES (?,?,?) "
                             "ON CONFLICT(target,key) DO UPDATE SET value=excluded.value",
                             (target, setting, str(closed)))
            dirty = (conn.execute(
                "SELECT bucket_start FROM dirty_rollup WHERE target=? AND source_res=? "
                "AND bucket_start<? ORDER BY bucket_start", (target, source, closed)).fetchall()
                if dirty_pending else ())
            for row in dirty:
                start = row["bucket_start"]
                tail_add = (_tail_period_sql("stmt_series") +
                    " AND NOT EXISTS (SELECT 1 FROM incident_pin p WHERE p.target=stmt_series.target "
                    "AND p.query_key=stmt_series.query_key AND p.start<stmt_series.bucket_start+300 "
                    "AND (p.end IS NULL OR p.end>stmt_series.bucket_start))") if dest == 300 else "0"
                conn.execute(
                    "INSERT INTO stmt_series "
                    "SELECT target,query_key,?, ?,SUM(calls),SUM(total_ms),SUM(rows),"
                    "SUM(blks_hit),SUM(blks_read),SUM(temp_blks) FROM stmt_series s "
                    "WHERE target=? AND res=? AND bucket_start>=? AND bucket_start<? "
                    + ("AND NOT (" + _tail_period_sql("s") + ") " if dest == 86400 else "") +
                    "GROUP BY target,query_key ON CONFLICT(target,query_key,res,bucket_start) "
                    "DO UPDATE SET " + ",".join(
                        f"{field}=CASE WHEN {tail_add} THEN {field}+excluded.{field} "
                        f"ELSE excluded.{field} END" for field in
                        ("calls", "total_ms", "rows", "blks_hit", "blks_read", "temp_blks")),
                    (dest, start, target, source, start, start + dest))
                conn.execute(
                    "INSERT INTO db_series (target,res,bucket_start,calls,total_ms,"
                    "active_conns,max_conns,cache_hit_ratio,xact_commit,xact_rollback,"
                    "deadlocks,blks_hit,blks_read,lock_waits) "
                    "SELECT target,?, ?,SUM(calls),SUM(total_ms),MAX(active_conns),"
                    "MAX(max_conns),CASE WHEN COALESCE(SUM(blks_hit),0)+"
                    "COALESCE(SUM(blks_read),0)>0 THEN CAST(SUM(blks_hit) AS REAL)/"
                    "(SUM(blks_hit)+SUM(blks_read)) ELSE AVG(cache_hit_ratio) END,"
                    "SUM(xact_commit),SUM(xact_rollback),SUM(deadlocks),"
                    "SUM(blks_hit),SUM(blks_read),MAX(lock_waits) FROM db_series "
                    "WHERE target=? AND res=? AND bucket_start>=? AND bucket_start<? "
                    "GROUP BY target ON CONFLICT(target,res,bucket_start) "
                    "DO UPDATE SET calls=excluded.calls,total_ms=excluded.total_ms,"
                    "active_conns=excluded.active_conns,max_conns=excluded.max_conns,"
                    "cache_hit_ratio=excluded.cache_hit_ratio,"
                    "xact_commit=excluded.xact_commit,xact_rollback=excluded.xact_rollback,"
                    "deadlocks=excluded.deadlocks,blks_hit=excluded.blks_hit,"
                    "blks_read=excluded.blks_read,lock_waits=excluded.lock_waits",
                    (dest, start, target, source, start, start + dest))
                conn.execute("DELETE FROM dirty_rollup WHERE target=? AND source_res=? "
                             "AND bucket_start=?", (target, source, start))
                if dest != RESOLUTIONS[-1]:
                    parent = RESOLUTIONS[RESOLUTIONS.index(dest) + 1]
                    conn.execute("INSERT OR IGNORE INTO dirty_rollup VALUES (?,?,?)",
                                 (target, dest, start // parent * parent))
                    dirty_pending = True
        # Middle-tier minutes are only rollup input. Pins keep incident evidence.
        conn.execute("DELETE FROM stmt_series WHERE target=? AND res=60 "
                     "AND bucket_start<? AND " + _tail_period_sql("stmt_series", span=300) +
                     " AND " + _unpinned_sql("stmt_series", now), (target, now // 300 * 300))
        for res, age in reading_retention().items():
            cutoff = (now - age) // res * res
            for table in ("stmt_series", "db_series"):
                conn.execute(
                    f"DELETE FROM {table} WHERE target=? AND res=? AND bucket_start<?"
                    + (f" AND {_unpinned_sql(table, now)}" if res == 60 else ""),
                    (target, res, cutoff),
                )
        kept = "json_object('summary',json_extract(report_json,'$.summary'))"
        conn.execute("UPDATE report_run SET progress_json=NULL,report_json=" + kept +
                     " WHERE target=? AND ts<? AND state!='running' "
                     "AND (progress_json IS NOT NULL OR (report_json IS NOT NULL "
                     "AND report_json!=" + kept + "))",
                     (target, now - REPORT_BODY_RETENTION_SECONDS))
        for table in ("table_activity", "index_activity"):
            conn.execute(f"DELETE FROM {table} WHERE target=? AND bucket_start<?",
                         (target, now - STATS_ACTIVITY_RETENTION_SECONDS))
        conn.execute("DELETE FROM setting_change WHERE target=? AND ts<?",
                     (target, now - EVENT_RETENTION_SECONDS))
        conn.execute("DELETE FROM stuck_sample WHERE target=? AND ts<?",
                     (target, now - 48 * 3600))
        conn.execute("DELETE FROM query_sample WHERE target=? AND observed_at<?",
                     (target, now - 48 * 3600))
        conn.execute("DELETE FROM judge_run WHERE target=? AND ts<?",
                     (target, now - EVENT_RETENTION_SECONDS))
        conn.execute("DELETE FROM monitor_pulse WHERE target=? AND ts<?",
                     (target, now - PULSE_RETENTION_SECONDS))
        conn.execute("DELETE FROM report_event WHERE report_id IN (SELECT id FROM report_run "
                     "WHERE target=? AND ts<?)", (target, now - EVENT_RETENTION_SECONDS))
        conn.execute("DELETE FROM inbox_event WHERE ts<? AND item_id IN "
                     "(SELECT id FROM inbox_item WHERE target=? AND "
                     "(inbox_event.kind!='created' OR (state IN ('resolved','dismissed') "
                     "AND updated_at<?)))",
                     (now - EVENT_RETENTION_SECONDS, target, now - EVENT_RETENTION_SECONDS))

    # Storage budget. Maintenance runs between writes, never inside one: each prune is a
    # bounded batch in its own short transaction. The order of preference is fixed: oldest
    # query samples, fine rows already rolled up (lightest queries first), old report
    # detail, old analysis jobs, old hourly rows under a daily row; then one inbox item.

    @staticmethod
    def _slim_report_sql() -> str:
        return ("json_patch(json_object('summary',json_extract(report_json,'$.summary')),"
                "json_object('snapshot',json_extract(report_json,'$.snapshot')))")

    def _prune_backups(self) -> None:
        """Keep the newest migration backup of each local store."""
        groups: dict[str, list[Path]] = {}
        for path in self.path.parent.iterdir():
            match = re.fullmatch(r"(.+\.db)\.pre-v([0-9]+)\.bak", path.name)
            if match and path.is_file() and not path.is_symlink():
                groups.setdefault(match[1], []).append(path)
        for paths in groups.values():
            paths.sort(key=lambda path: (path.stat().st_mtime_ns,
                                         int(path.name.rsplit("pre-v", 1)[1][:-4])), reverse=True)
            for path in paths[1:]:
                os.remove(path)

    def maintain(self, *, pause_s: float = MAINTENANCE_PAUSE_S) -> None:
        """Apply age and size limits to local monitoring history and reclaim freed pages."""
        self.ensure_open()
        now = int(self.clock())
        self._prune_backups()
        self._write(lambda conn: conn.execute(
            "DELETE FROM incident_pin WHERE NOT EXISTS "
            "(SELECT 1 FROM inbox_item WHERE id=incident_pin.item_id)"))
        thinned_reports = False
        kept = "json_object('summary',json_extract(report_json,'$.summary'))"
        while True:
            count = self._write(lambda conn: conn.execute(
                "UPDATE report_run SET progress_json=NULL,report_json=" + kept +
                " WHERE rowid IN (SELECT rowid FROM report_run WHERE ts<? AND state!='running' "
                "AND (progress_json IS NOT NULL OR (report_json IS NOT NULL AND report_json!=" +
                kept + ")) ORDER BY ts,id LIMIT ?)",
                (now - REPORT_BODY_RETENTION_SECONDS, MAINTENANCE_BATCH_ROWS)).rowcount)
            if not count:
                break
            thinned_reports = True
            time.sleep(pause_s)
        thinned = thinned_reports
        for row in self.read("SELECT target FROM monitor_setting WHERE key='rollup_until_60'"):
            thinned = self._thin_fine(row["target"], pause_s=pause_s) or thinned
            self.rank_queries(row["target"])
        if (self._file_bytes() > self.budget_bytes or
                self._install_size_bytes() > self.install_budget_bytes):
            targets = self.read(
                "SELECT target FROM monitor_setting UNION SELECT target FROM query_text "
                "UNION SELECT target FROM report_run UNION SELECT target FROM inbox_item "
                "UNION SELECT target FROM analyze_job UNION SELECT target FROM stmt_series "
                "UNION SELECT target FROM db_series UNION SELECT target FROM index_snapshot "
                "UNION SELECT target FROM index_change UNION SELECT target FROM judge_run "
                "UNION SELECT target FROM stuck_sample UNION SELECT target FROM monitor_pulse")
            for row in targets:
                self._enforce_budget(row["target"], pause_s=pause_s)
            self._enforce_install_budget(pause_s=pause_s)
            thinned = True
        if thinned:
            self._vacuum(pause_s)

    def _used_bytes(self) -> int:
        conn = self._connect()
        try:
            return ((conn.execute("PRAGMA page_count").fetchone()[0] -
                     conn.execute("PRAGMA freelist_count").fetchone()[0]) *
                    conn.execute("PRAGMA page_size").fetchone()[0])
        finally:
            conn.close()

    def _install_size_bytes(self) -> int:
        """Monitoring pages plus the install's other SQLite files and migration backups."""
        size = self._used_bytes()
        for path in self.path.parent.iterdir():
            if path == self.path or not path.is_file() or path.is_symlink():
                continue
            if re.fullmatch(r".+\.db(?:-(?:wal|shm)|\.pre-v[0-9]+\.bak)?", path.name):
                size += path.stat().st_size
        return size

    def _enforce_install_budget(self, *, pause_s: float) -> None:
        """Prune eligible history oldest first across targets, in bounded transactions."""
        now = int(self.clock())
        cutoff = now - EVENT_RETENTION_SECONDS
        choices = [("query_sample", "1", "observed_at", "DELETE")]
        for res, parent in ((60, 300), (300, 3600), (3600, 86400)):
            for table in ("stmt_series", "db_series"):
                key = f"AND p.query_key={table}.query_key " if table == "stmt_series" else ""
                condition = (f"res={res} AND EXISTS (SELECT 1 FROM {table} p "
                             f"WHERE p.target={table}.target {key}AND p.res={parent} "
                             f"AND p.bucket_start={table}.bucket_start/{parent}*{parent})")
                if res == 60:
                    condition += " AND " + _unpinned_sql(table, now)
                if res == 3600:
                    condition += f" AND bucket_start<{cutoff}"
                choices.append((table, condition, "bucket_start", "DELETE"))
        kept = self._slim_report_sql()
        choices.extend([
            ("report_run", f"ts<{cutoff} AND state!='running' AND report_json IS NOT NULL "
             f"AND report_json!={kept}", "ts", f"UPDATE report_run SET report_json={kept}"),
            ("analyze_job", f"enqueued_at<{cutoff} AND state!='running'", "enqueued_at", "DELETE"),
        ])
        while self._install_size_bytes() > self.install_budget_bytes:
            candidates = []
            for index, (table, condition, stamp, _) in enumerate(choices):
                rows = self.read(f"SELECT MIN({stamp}) ts FROM {table} WHERE {condition}")
                if rows[0]["ts"] is not None:
                    candidates.append((rows[0]["ts"], index))
            if not candidates:
                targets = self.read("SELECT target FROM monitor_setting UNION SELECT target "
                                    "FROM stmt_series UNION SELECT target FROM report_run LIMIT 1")
                if targets:
                    self.upsert_item(
                        targets[0]["target"], "monitoring_issue", "install_storage_budget", 1.0,
                        "Local monitoring storage budget exceeded",
                        {"budget_bytes": self.install_budget_bytes, "scope": "install"}, now,
                        notify=False)
                return
            candidates.sort()
            _, index = candidates[0]
            table, condition, stamp, action = choices[index]
            if len(candidates) > 1:
                condition += f" AND {stamp}<={candidates[1][0]}"
            def prune(conn: sqlite3.Connection) -> None:
                change = f"DELETE FROM {table}" if action == "DELETE" else action
                conn.execute(change + f" WHERE rowid IN (SELECT rowid FROM {table} "
                             f"WHERE {condition} ORDER BY {stamp},rowid LIMIT ?)",
                             (MAINTENANCE_BATCH_ROWS,))
            self._write(prune)
            time.sleep(pause_s)

    def _vacuum(self, pause_s: float) -> None:
        free = None
        while True:
            remaining = self._write(lambda conn: (
                conn.execute(f"PRAGMA incremental_vacuum({VACUUM_BATCH_PAGES})").fetchall(),
                conn.execute("PRAGMA freelist_count").fetchone()[0])[1])
            if not remaining or (free is not None and remaining >= free):
                return
            free = remaining
            time.sleep(pause_s)

    @staticmethod
    def _rank_queries(conn: sqlite3.Connection, target: str, now: int) -> None:
        # One decaying counter per digest bounds ranking storage independently of history.
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS next_tier "
                     "(query_key TEXT PRIMARY KEY, rank INTEGER NOT NULL)")
        conn.execute("DELETE FROM next_tier")
        conn.execute("INSERT INTO next_tier SELECT query_key,ROW_NUMBER() OVER (ORDER BY "
                     "CASE WHEN updated_at<? THEN 0 ELSE "
                     "total_ms/(1.0+MAX(0,?-updated_at)/86400.0) END DESC,query_key) "
                     "FROM query_load l WHERE target=? AND NOT EXISTS (SELECT 1 FROM query_ui u "
                     "WHERE u.target=l.target AND u.query_key=l.query_key AND u.workload=0) "
                     "ORDER BY 2 LIMIT ?", (now - RECENT_LOAD_SECONDS, now, target, SERIES_QUERIES))
        # Existing stores seed their initial ranking from retained hourly history.
        if not conn.execute("SELECT 1 FROM next_tier LIMIT 1").fetchone():
            conn.execute("INSERT INTO next_tier SELECT query_key,ROW_NUMBER() OVER "
                         "(ORDER BY SUM(total_ms) DESC,query_key) FROM stmt_series "
                         "WHERE target=? AND res=3600 AND bucket_start>=? AND query_key!=? "
                         "GROUP BY query_key ORDER BY 2 LIMIT ?",
                         (target, now - RECENT_LOAD_SECONDS, EVERYTHING_ELSE, SERIES_QUERIES))
        conn.execute("INSERT INTO next_tier SELECT q.query_key,"
                     "(SELECT COALESCE(MAX(rank),0) FROM next_tier)+ROW_NUMBER() OVER "
                     "(ORDER BY q.last_seen DESC,q.query_key) FROM query_text q "
                     "WHERE target=? AND NOT EXISTS (SELECT 1 FROM next_tier n "
                     "WHERE n.query_key=q.query_key) AND NOT EXISTS (SELECT 1 FROM query_ui u "
                     "WHERE u.target=q.target AND u.query_key=q.query_key AND u.workload=0) "
                     "ORDER BY 2 LIMIT MAX(0,?-(SELECT COUNT(*) FROM next_tier))",
                     (target, SERIES_QUERIES))
        conn.execute("UPDATE query_tier_period SET end=? WHERE target=? AND end IS NULL "
                     "AND tier!=COALESCE((SELECT CASE WHEN rank<=? THEN 1 ELSE 2 END "
                     "FROM next_tier n WHERE n.query_key=query_tier_period.query_key),3)",
                     (now, target, FULL_DETAIL_QUERIES))
        conn.execute("INSERT OR IGNORE INTO query_tier_period SELECT ?,query_key,?,NULL,"
                     "CASE WHEN rank<=? THEN 1 ELSE 2 END FROM next_tier n WHERE NOT EXISTS "
                     "(SELECT 1 FROM query_tier_period p WHERE p.target=? "
                     "AND p.query_key=n.query_key AND p.end IS NULL) "
                     "ON CONFLICT(target,query_key,start,tier) DO UPDATE SET end=NULL",
                     (target, now, FULL_DETAIL_QUERIES, target))
        conn.execute("DELETE FROM query_tier WHERE target=? AND NOT EXISTS ("
                     "SELECT 1 FROM next_tier n WHERE n.query_key=query_tier.query_key)", (target,))
        conn.execute("INSERT INTO query_tier SELECT ?,query_key,rank FROM next_tier n "
                     "WHERE NOT EXISTS (SELECT 1 FROM query_tier t WHERE t.target=? "
                     "AND t.query_key=n.query_key AND t.rank=n.rank) "
                     "ON CONFLICT(target,query_key) DO UPDATE SET rank=excluded.rank", (target, target))
        conn.execute("INSERT INTO monitor_setting VALUES (?,'tiers_ranked_at',?) "
                     "ON CONFLICT(target,key) DO UPDATE SET value=excluded.value", (target, str(now)))
        conn.execute("DELETE FROM query_tier_period WHERE target=? AND end<?",
                     (target, now - RETENTION[86400]))

    def update_query_ui(self, target: str, key: str, *, summary: dict | None = None,
                        findings: int | None = None) -> None:
        def write(conn: sqlite3.Connection) -> None:
            conn.execute("INSERT OR IGNORE INTO query_ui(target,query_key) VALUES (?,?)", (target, key))
            if summary is not None:
                conn.execute("UPDATE query_ui SET efficiency_score=?,fix_ready=?,analyzed=1 "
                             "WHERE target=? AND query_key=?",
                             (summary.get("efficiency_score"), int(bool(summary.get("fix_ready"))), target, key))
            if findings is not None:
                conn.execute("UPDATE query_ui SET assessed=1,findings=? WHERE target=? AND query_key=?",
                             (findings, target, key))
        self._write(write)

    def rank_queries(self, target: str, now: int | None = None) -> None:
        self._write(lambda conn: self._rank_queries(
            conn, target, int(self.clock()) if now is None else now))

    def sync_query_ui(self, target: str, *, summaries: dict[str, dict] | None = None,
                      assessments: dict[str, int] | None = None) -> None:
        """Batch existing analysis and classification projections for SQL list selection."""
        def write(conn: sqlite3.Connection) -> None:
            conn.executemany(
                "INSERT INTO query_ui(target,query_key,efficiency_score,fix_ready,analyzed) "
                "VALUES (?,?,?,?,1) ON CONFLICT(target,query_key) DO UPDATE SET "
                "efficiency_score=excluded.efficiency_score,fix_ready=excluded.fix_ready,analyzed=1",
                ((target, key, summary.get("efficiency_score"), int(bool(summary.get("fix_ready"))))
                 for key, summary in (summaries or {}).items()))
            conn.executemany(
                "INSERT INTO query_ui(target,query_key,assessed,findings) VALUES (?,?,1,?) "
                "ON CONFLICT(target,query_key) DO UPDATE SET assessed=1,findings=excluded.findings",
                ((target, key, mask) for key, mask in (assessments or {}).items()))
        self._write(write)

    def full_detail_keys(self, target: str) -> set[str]:
        marks = self.read("SELECT 1 FROM monitor_setting WHERE target=? "
                          "AND key='tiers_ranked_at'", (target,))
        if not marks:
            # Bounded startup selection also serves stores populated by older collectors.
            return {row["query_key"] for row in self.read(
                "WITH weights AS (SELECT query_key,SUM(total_ms) ms FROM stmt_series "
                "WHERE target=? AND query_key!=? GROUP BY query_key), "
                "known AS (SELECT query_key FROM weights UNION "
                "SELECT query_key FROM query_text WHERE target=?) "
                "SELECT query_key FROM known LEFT JOIN weights USING(query_key) "
                "ORDER BY COALESCE(ms,0) DESC,query_key LIMIT ?",
                (target, EVERYTHING_ELSE, target, FULL_DETAIL_QUERIES))}
        return {row["query_key"] for row in self.read(
            "SELECT query_key FROM query_tier WHERE target=? AND rank<=?",
            (target, FULL_DETAIL_QUERIES))}

    def _thin_fine(self, target: str, *, pause_s: float = MAINTENANCE_PAUSE_S) -> bool:
        now = int(self.clock())
        changed = False
        for res, age in TAIL_RETENTION.items():
            while True:
                count = self._write(lambda conn: conn.execute(
                    "DELETE FROM stmt_series WHERE rowid IN (SELECT s.rowid FROM stmt_series s "
                    "WHERE target=? AND res=? AND bucket_start<? AND " + _tail_period_sql("s") +
                    " ORDER BY bucket_start LIMIT ?)",
                    (target, res, (now - age) // res * res, MAINTENANCE_BATCH_ROWS)).rowcount)
                if not count:
                    break
                changed = True
                time.sleep(pause_s)
        return changed

    def _file_bytes(self) -> int:
        conn = self._connect()
        try:
            return (conn.execute("PRAGMA page_count").fetchone()[0] *
                    conn.execute("PRAGMA page_size").fetchone()[0])
        finally:
            conn.close()

    def _enforce_budget(self, target: str, *, pause_s: float = MAINTENANCE_PAUSE_S) -> None:
        # The size is measured once, then lowered by what each batch frees. Credit is
        # generous (twice the rows' share of their table's pages), so the estimate stays at or
        # below the true size; it is measured again whenever it reaches the budget.
        estimate = {"size": self.target_size_bytes(target), "measured": True}
        page_share: dict[str, float] = {}

        def over() -> bool:
            if estimate["size"] <= self.budget_bytes and not estimate["measured"]:
                estimate.update(size=self.target_size_bytes(target), measured=True)
            return estimate["size"] > self.budget_bytes

        def prune(table: str, pick: str, params: tuple[Any, ...], change: str,
                  limit: int = MAINTENANCE_BATCH_ROWS) -> int:
            """Change one batch of rows chosen by `pick` (a rowid query) and credit it."""
            def write(conn: sqlite3.Connection) -> tuple[int, int]:
                ids = [row[0] for row in conn.execute(pick + " LIMIT ?", (*params, limit))]
                if not ids:
                    return 0, 0
                marks = ",".join("?" for _ in ids)
                measure = (f"SELECT COALESCE(SUM({self._weight_sql(conn, table)}),0) "
                           f"FROM {table} WHERE rowid IN ({marks})")
                before = conn.execute(measure, ids).fetchone()[0]
                conn.execute(change.format(ids=marks), ids)
                return len(ids), before - conn.execute(measure, ids).fetchone()[0]
            count, freed = self._write(write)
            if count:
                if table not in page_share:
                    page_share[table] = self._page_share(table)
                estimate["size"] -= math.ceil(2 * freed * page_share[table])
                estimate["measured"] = False
                time.sleep(pause_s)
            return count

        def prune_all(table: str, pick: str, params: tuple[Any, ...], change: str) -> None:
            while prune(table, pick, params, change):
                pass

        delete = "DELETE FROM {table} WHERE rowid IN ({{ids}})"
        if not over():
            return
        while prune("query_sample", "SELECT rowid FROM query_sample WHERE target=? "
                    "ORDER BY observed_at,id", (target,),
                    delete.format(table="query_sample"), SAMPLE_BATCH_ROWS):
            if not over():
                return
        for res in (60, 300):
            parent = 300 if res == 60 else 3600
            keys = self.read(
                "SELECT s.query_key,SUM(s.total_ms) AS weight FROM stmt_series s "
                "WHERE s.target=? AND s.res=? AND EXISTS ("
                "SELECT 1 FROM stmt_series p WHERE p.target=s.target "
                "AND p.query_key=s.query_key AND p.res=? "
                "AND p.bucket_start=s.bucket_start / ? * ?) "
                "GROUP BY s.query_key ORDER BY weight,s.query_key",
                (target, res, parent, parent, parent),
            )
            for key in keys:
                prune_all("stmt_series",
                          "SELECT rowid FROM stmt_series WHERE target=? AND res=? "
                          "AND query_key=? AND EXISTS (SELECT 1 FROM stmt_series p "
                          "WHERE p.target=stmt_series.target "
                          "AND p.query_key=stmt_series.query_key AND p.res=? "
                          "AND p.bucket_start=stmt_series.bucket_start / ? * ?) AND "
                          + (_unpinned_sql("stmt_series", int(self.clock())) if res == 60 else "1"),
                          (target, res, key["query_key"], parent, parent, parent),
                          delete.format(table="stmt_series"))
                if not over():
                    return
        cutoff = int(self.clock()) - EVENT_RETENTION_SECONDS
        # The day's snapshot is the record of that day, so it stays; the inputs go.
        kept = ("json_patch(json_object('summary',json_extract(report_json,'$.summary')),"
                "json_object('snapshot',json_extract(report_json,'$.snapshot')))")
        prune_all("report_run", "SELECT rowid FROM report_run WHERE target=? AND ts<? "
                  "AND state!='running' AND report_json IS NOT NULL "
                  f"AND report_json != {kept} ORDER BY id", (target, cutoff),
                  f"UPDATE report_run SET report_json={kept} WHERE rowid IN ({{ids}})")
        if not over():
            return
        prune_all("analyze_job", "SELECT rowid FROM analyze_job WHERE target=? "
                  "AND enqueued_at<? AND state!='running' ORDER BY id", (target, cutoff),
                  delete.format(table="analyze_job"))
        if not over():
            return
        keys = self.read(
            "SELECT s.query_key,SUM(s.total_ms) weight FROM stmt_series s "
            "WHERE s.target=? AND s.res=3600 AND s.bucket_start<? AND EXISTS ("
            "SELECT 1 FROM stmt_series p WHERE p.target=s.target AND p.query_key=s.query_key "
            "AND p.res=86400 AND p.bucket_start=s.bucket_start/86400*86400) "
            "GROUP BY s.query_key ORDER BY weight,s.query_key", (target, cutoff))
        for key in keys:
            prune_all("stmt_series", "SELECT rowid FROM stmt_series WHERE target=? AND res=3600 "
                      "AND bucket_start<? AND query_key=? AND EXISTS ("
                      "SELECT 1 FROM stmt_series p WHERE p.target=stmt_series.target "
                      "AND p.query_key=stmt_series.query_key AND p.res=86400 "
                      "AND p.bucket_start=stmt_series.bucket_start/86400*86400)",
                      (target, cutoff, key["query_key"]), delete.format(table="stmt_series"))
            if not over():
                return
        # Retain query identities and the daily history within its age limit. File one issue.
        def file_issue(conn: sqlite3.Connection) -> None:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO inbox_item "
                "(target,type,subject,state,severity,title,body_json,created_at,updated_at) "
                "VALUES (?,'monitoring_issue','storage_budget','new',1.0,"
                "'Monitoring storage budget exceeded',?,?,?)",
                (target, json.dumps({"budget_bytes": self.budget_bytes}),
                 int(self.clock()), int(self.clock())))
            if cursor.rowcount:
                conn.execute("INSERT INTO inbox_event (item_id,ts,kind,detail_json) "
                             "VALUES (?,?,'created','{}')", (cursor.lastrowid, int(self.clock())))
                _pin_item(conn, conn.execute("SELECT * FROM inbox_item WHERE id=?",
                                             (cursor.lastrowid,)).fetchone())
        self._write(file_issue)

    def _page_share(self, table: str) -> float:
        """File bytes per stored payload byte in `table`, its indexes included."""
        conn = self._connect()
        try:
            names = [table] + [row["name"] for row in conn.execute(f"PRAGMA index_list({table})")]
            try:
                pages = sum(conn.execute("SELECT COALESCE(SUM(pgsize),0) FROM dbstat WHERE name=?",
                                         (name,)).fetchone()[0] for name in names)
            except sqlite3.OperationalError:
                pages = ((conn.execute("PRAGMA page_count").fetchone()[0] -
                          conn.execute("PRAGMA freelist_count").fetchone()[0]) *
                         conn.execute("PRAGMA page_size").fetchone()[0])
            total = conn.execute(f"SELECT COALESCE(SUM({self._weight_sql(conn, table)}),0) "
                                 f"FROM {table}").fetchone()[0]
            return pages / total if total else 0.0
        finally:
            conn.close()

    @staticmethod
    def _weight_sql(conn: sqlite3.Connection, table: str, prefix: str = "") -> str:
        """A row's stored payload: its column bytes plus a fixed per-row overhead."""
        columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
        return "64+" + "+".join(f"COALESCE(LENGTH(CAST({prefix}\"{col}\" AS BLOB)),0)"
                                for col in columns)

    def target_size_bytes(self, target: str) -> int:
        self.ensure_open()
        conn = self._connect()
        try:
            return self._target_size(conn, target)
        finally:
            conn.close()

    @staticmethod
    def _target_size(conn: sqlite3.Connection, target: str) -> int:
        """Attribute SQLite table and index pages by stored payload share."""
        tables = ("query_text", "query_sample", "stmt_series", "db_series", "stuck_sample",
                  "index_snapshot", "index_change", "inbox_item", "inbox_event",
                  "judge_run", "analyze_job", "report_run", "monitor_setting",
                  "dirty_rollup", "monitor_pulse", "fix_verdict", "system_query", "incident_pin",
                  "query_load", "query_tier", "query_tier_period", "query_ui")
        try:
            pages = {row["name"]: row["bytes"] for row in conn.execute(
                "SELECT name,SUM(pgsize) bytes FROM dbstat GROUP BY name")}
        except sqlite3.OperationalError:
            pages = {}
        page_bytes = {table: pages.get(table, 0) for table in tables}
        for table in tables:
            for row in conn.execute(f"PRAGMA index_list({table})"):
                page_bytes[table] += pages.get(row["name"], 0)
        size = 0
        own_total = 0
        all_total = 0
        occupied_pages = 0
        for table in tables:
            source = ("inbox_event e JOIN inbox_item i ON i.id=e.item_id"
                      if table == "inbox_event" else table)
            condition = "i.target=?" if table == "inbox_event" else "target=?"
            if pages:
                target_column = "i.target" if table == "inbox_event" else "target"
                first = conn.execute(f"SELECT {target_column} FROM {source} "
                                     f"ORDER BY {target_column} LIMIT 1").fetchone()
                if first is None:
                    continue
                occupied_pages += page_bytes[table]
                last = conn.execute(f"SELECT {target_column} FROM {source} "
                                    f"ORDER BY {target_column} DESC LIMIT 1").fetchone()
                if first[0] == last[0]:
                    if first[0] == target:
                        size += page_bytes[table]
                    continue
            weight = MonitorStore._weight_sql(conn, table,
                                              "e." if table == "inbox_event" else "")
            own = conn.execute(f"SELECT COALESCE(SUM({weight}),0) FROM {source} WHERE {condition}",
                               (target,)).fetchone()[0]
            if not own:
                continue
            total = conn.execute(f"SELECT COALESCE(SUM({weight}),0) FROM {source}").fetchone()[0]
            own_total += own
            all_total += total
            if page_bytes[table]:
                size += math.ceil(page_bytes[table] * own / total)
        if not pages and all_total:
            # Calibrate the fallback to SQLite's actual allocated page count.
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            used_pages = (conn.execute("PRAGMA page_count").fetchone()[0] -
                          conn.execute("PRAGMA freelist_count").fetchone()[0])
            size = math.ceil(used_pages * page_size * own_total / all_total)
        # Schema and pointer-map pages are real file bytes too.
        targets = conn.execute("SELECT COUNT(DISTINCT target) FROM monitor_setting").fetchone()[0]
        if pages:
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            used_pages = (conn.execute("PRAGMA page_count").fetchone()[0] -
                          conn.execute("PRAGMA freelist_count").fetchone()[0])
            unmapped = max(0, used_pages * page_size - occupied_pages)
            size += math.ceil(unmapped / max(targets, 1))
        return size

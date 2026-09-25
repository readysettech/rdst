import json
import sqlite3
from dataclasses import replace

import pytest

from features.monitor import loop, presentation
from features.monitor.api import models, views
from features.monitor.attention import baselines
from features.monitor.judge import _request
from features.query_registry.discovery import MYSQL_MONITOR_LOCK_SQL, PG_MONITOR_DB_SQL, QueryDiscoveryCollector, mysql_monitor_sample
from features.query_registry.statement_stats import MysqlDigestSource, PgStatStatementsSource
from shared.monitor.monitor_store import MonitorStore, SCHEMA_VERSION
from shared.query_registry.delta_processor import COMPLETE, CounterRow, DeltaResult
from tests.unit.test_statement_stats import mysql_digest_row, mysql_executor, pg_executor, pg_stat_row


def record(store, ts, waits=5, spills=2):
    store.record("db", ts, [{"query_key": "q", "calls": 1, "total_ms": 10, "temp_blks": spills}],
                 {"q": "SELECT * FROM events"}, [],
                 {"calls": 1, "total_ms": 10, "active_conns": 10, "lock_waits": waits})


def test_old_schema_migration_preserves_history(tmp_path):
    path = tmp_path / "monitor.db"
    store = MonitorStore(path)
    record(store, 60)
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE stmt_series DROP COLUMN temp_blks")
        conn.execute("ALTER TABLE db_series DROP COLUMN lock_waits")
        conn.execute("PRAGMA user_version=25")
    migrated = MonitorStore(path)
    assert migrated.read("SELECT calls,temp_blks FROM stmt_series") == [{"calls": 1, "temp_blks": 0}]
    assert migrated.read("SELECT calls,lock_waits FROM db_series") == [{"calls": 1, "lock_waits": None}]
    assert migrated.read("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
    assert path.with_name("monitor.db.pre-v25.bak").exists()


@pytest.mark.parametrize("version", [130000, 150000, 170000])
def test_postgres_collects_temp_spills(version):
    raw = list(pg_stat_row(version))
    offset = 11 if version >= 140000 else 10
    raw[offset:offset] = [20, 4, 9]
    execute = pg_executor(version, [raw])
    assert PgStatStatementsSource().collect(execute).rows[0].temp_blks == 9
    assert "temp_blks_written" in execute.sql_containing("pg_stat_statements(false)")


@pytest.mark.parametrize("sample", [False, True])
def test_mysql_collects_disk_temp_tables(sample):
    raw = mysql_digest_row() + (12,)
    if sample:
        raw += ("SELECT * FROM events WHERE id=7",)
    execute = mysql_executor([raw], sample_capable=sample)
    result = MysqlDigestSource().collect(execute)
    assert result.rows[0].temp_blks == 12
    assert "SUM_CREATED_TMP_DISK_TABLES" in execute.sql_containing("events_statements_summary_by_digest WHERE")
    if sample:
        assert list(result.sample_texts.values()) == [raw[-1]]


@pytest.mark.parametrize("unavailable", [False, True])
def test_mysql_lock_waits_best_effort(unavailable):
    def execute(sql, params):
        if sql == MYSQL_MONITOR_LOCK_SQL:
            if unavailable:
                raise RuntimeError("Table isn't available")
            return [(7,)]
        if "SHOW GLOBAL STATUS" in sql:
            return [("Threads_connected", 10)]
        if "max_connections" in sql:
            return [("max_connections", 100)]
        return [(0,)]
    sample = mysql_monitor_sample(execute, "epoch")
    assert sample[8] == (None if unavailable else 7)
    assert sample[5:8] == (10, 100, "epoch")


@pytest.mark.parametrize("engine", ["postgresql", "mysql"])
def test_minute_collection_diffs_spills_and_keeps_lock_gauge(tmp_path, engine):
    store = MonitorStore(tmp_path / "monitor.db")
    collector = QueryDiscoveryCollector("db", two_phase=True)
    collector._monitor = store
    collector._engine = engine
    collector._alias_map = {"key": "q"}
    previous = CounterRow("key", 2, 0, 2, 1, 1, 1, 60, "epoch", temp_blks=10)
    collector._baseline = {"key": replace(previous, calls=4, captured_at=120, temp_blks=17)}
    from tests.unit.test_monitor_mysql_parity import Cursor

    cursor = Cursor([
        ("pg_stat_database", [(0, 0, 0, 0, 0, 10, 100, "epoch", 5)]),
        ("SHOW GLOBAL STATUS", [("Threads_connected", 10)]),
        ("max_connections", [("max_connections", 100)]),
        ("INNODB_METRICS", [(0,)]), ("SUM_ERRORS", [(0,)]),
        ("data_lock_waits", [(5,)]),
    ])
    def execute(sql, params):
        cursor.execute(sql, params)
        return cursor.fetchall()
    collector._monitor_db_sample = (execute(PG_MONITOR_DB_SQL, None)[0] if engine == "postgresql"
                                    else mysql_monitor_sample(execute, "epoch"))
    delta = DeltaResult("key", 60, 120, COMPLETE, 2, 2, 2 / 60, 4)
    collector._record_monitor_cycle(120, [delta], None, [], prior_rows={"key": previous},
                                    observed_texts={"key": "SELECT * FROM events"})
    assert store.read("SELECT temp_blks FROM stmt_series")[0]["temp_blks"] == 7
    assert store.read("SELECT lock_waits FROM db_series")[0]["lock_waits"] == 5
    assert "wait_event_type='Lock'" in PG_MONITOR_DB_SQL and "NOT LIKE 'rdst/%'" in PG_MONITOR_DB_SQL


def test_rollups_sum_spills_and_max_locks_including_late_samples(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    for ts, waits in [(60, 3), (120, 8), (180, None), (240, 4), (300, 1)]:
        record(store, ts, waits)
    assert store.read("SELECT temp_blks FROM stmt_series WHERE res=300 AND bucket_start=0")[0]["temp_blks"] == 8
    assert store.read("SELECT lock_waits FROM db_series WHERE res=300 AND bucket_start=0")[0]["lock_waits"] == 8
    record(store, 120, 12, 3)
    record(store, 3600, 0, 0)
    record(store, 86400, 0, 0)
    for res in (300, 3600, 86400):
        assert store.read("SELECT temp_blks FROM stmt_series WHERE res=? AND bucket_start=0", (res,))[0]["temp_blks"] == (11 if res == 300 else 13)
        assert store.read("SELECT lock_waits FROM db_series WHERE res=? AND bucket_start=0", (res,))[0]["lock_waits"] == 12


def test_jev_payload_and_api_include_metrics(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    for ts, waits in [(60, 9), (120, 4), (180, 3)]:
        record(store, ts, waits)
    current = loop._window_rows(store, "db", 0, 181, 60)
    assert sum(row["temp_blks"] for row in current) == 6
    rows = baselines(current,
                     {name: [{"query_key": "q", "calls": 2, "total_ms": 20, "temp_blks": 11}]
                      for name in ("1h", "1d", "7d")})
    rows[0]["reason"] = "mover"
    db = store.read("SELECT * FROM db_series WHERE res=60 ORDER BY bucket_start DESC")[0]
    request = _request(store, "db", 180, rows, {"database": db, "inbox": []})
    assert request["queries"][0]["current_5m"]["temp_blks"] == 6
    assert all(v["temp_blks"] == 11 for v in request["queries"][0]["baseline"].values())
    assert request["target_numbers"]["lock_waits"] == 3
    assert request["target_numbers"]["lock_waits_max_5m"] == 9
    health = models.MonitorHealth.model_validate(views.health(store, "db", "5m", 180))
    assert health.now.lock_waits == 3 and health.period.lock_waits_peak == 9
    assert max(p.lock_waits for p in health.series if p.lock_waits is not None) == 9
    series = models.QuerySeries.model_validate(views.series(store, "db", "q", "5m", 180))
    assert any(p.temp_blks == 2 for p in series.points)


def test_lock_contention_open_sustain_recover_and_no_flap(tmp_path, monkeypatch):
    store = MonitorStore(tmp_path / "monitor.db")
    monitor = loop.MonitorLoop(store=store, sampler=object())
    captured = []
    monkeypatch.setattr(loop, "_top_blocker", lambda target: captured.append(target) or {
        "query": "UPDATE events SET value = ?", "state": "idle in transaction", "xact_age_s": 300})
    def sample(ts, waits):
        record(store, ts, waits)
        monitor._lock_contention("db", ts)
    for ts in (60, 120, 180, 240):
        sample(ts, 3)
    assert not store.inbox()
    sample(300, 5)
    item = store.inbox()[0]
    assert item["type"] == "lock_contention" and item["severity"] == 0.9
    assert item["title"] == "Queries waiting on locks"
    assert presentation.severity_band(item["type"], item["severity"]) == "act_now"
    assert "5 sessions waiting on locks for 5 minutes." in presentation.inbox_summary(item, json.loads(item["body_json"]), {})
    for ts in (360, 420):
        sample(ts, 5)
    assert captured == ["db"]
    assert json.loads(store.inbox()[0]["body_json"])["minutes"] == 7
    for ts, waits in [(480, 0), (540, 3), (600, 0), (660, None), (720, 0), (780, 0)]:
        sample(ts, waits)
    assert store.inbox()[0]["state"] != "resolved"
    sample(840, 0)
    item = store.read("SELECT * FROM inbox_item")[0]
    assert item["state"] == "resolved" and item["resolved_by"] == "code"
    assert presentation.recovered(item, json.loads(item["body_json"]))
    assert "Lock waits back to normal" in presentation.inbox_summary(item, json.loads(item["body_json"]), {})
    assert len(store.read("SELECT * FROM inbox_event WHERE kind='notified'")) == 1
    for ts in (900, 1020, 1080, 1140, 1200):
        sample(ts, 3)
    assert store.read("SELECT state FROM inbox_item")[0]["state"] == "resolved"
    sample(1260, 3)
    assert store.read("SELECT state FROM inbox_item")[0]["state"] == "new"


def test_lock_contention_respects_push_cap(tmp_path, monkeypatch):
    store = MonitorStore(tmp_path / "monitor.db")
    monitor = loop.MonitorLoop(store=store, sampler=object())
    monkeypatch.setattr(loop, "_top_blocker", lambda target: None)
    store.upsert_item("db", "connections_near_max", "database", 0.9, "Connections", {}, 60)
    store.set_settings("db", {"push_daily_cap": 1})
    for ts in (60, 120, 180, 240, 300):
        record(store, ts)
    monitor._lock_contention("db", 300)
    assert len(store.read("SELECT * FROM inbox_event WHERE kind='notified'")) == 1


@pytest.mark.parametrize("unavailable", [False, True])
def test_postgres_blocker_capture_normalizes_text_and_closes_connection(monkeypatch, unavailable):
    from decimal import Decimal

    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def execute(self, sql):
            if "pg_blocking_pids" in sql:
                assert "ORDER BY w.query_start LIMIT 1" in sql
                if unavailable:
                    raise RuntimeError("Activity isn't available")
        def fetchone(self):
            return ("UPDATE events SET value = 'secret' WHERE id = 42", "idle in transaction", Decimal("320.5"))
    class Connection:
        closed = False
        def cursor(self):
            return Cursor()
        def close(self):
            self.closed = True
    connection = Connection()
    monkeypatch.setattr(loop, "_default_connection_factory", lambda target: (connection, "postgresql"))
    blocker = loop._top_blocker("db")
    assert connection.closed
    if unavailable:
        assert blocker is None
    else:
        assert "secret" not in blocker["query"] and "42" not in blocker["query"]
        assert blocker["state"] == "idle in transaction" and blocker["xact_age_s"] == 320.5
        json.dumps(blocker)


def test_unknown_lock_waits_stay_null_in_rollups(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    for ts in (60, 120, 300, 3600, 86400):
        record(store, ts, None)
    assert {row["res"] for row in store.read("SELECT DISTINCT res FROM db_series")} == {60, 300, 3600, 86400}
    assert all(row["lock_waits"] is None for row in store.read("SELECT lock_waits FROM db_series"))


def test_jev_request_carries_only_finite_numbers_without_lock_readings(tmp_path):
    import math

    store = MonitorStore(tmp_path / "monitor.db")
    # A database whose lock waits can't be read, and an item filed with that gap.
    db = {"calls": 60, "active_conns": 3, "max_conns": 100, "cache_hit_ratio": 0.99,
          "xact_rollback": 0, "deadlocks": 0, "lock_waits": None}
    store.upsert_item("db", "cache_hit_ratio_drop", "database", 0.6, "Cache hit ratio dropped",
                      {"at_filing": {"cache_hit_ratio": 0.9, "lock_waits_max_5m": None}}, 100)
    request = _request(store, "db", 180, [], {"database": db, "inbox": store.inbox()})
    numbers = [request["target_numbers"]] + [
        part for item in request["open_items"] for part in (item["at_filing"], item["now"])]
    assert request["open_items"]
    for group in numbers:
        assert all(isinstance(v, (int, float)) and math.isfinite(v) for v in group.values())
    assert request["target_numbers"]["lock_waits_max_5m"] == 0


def test_a_resolved_item_never_stays_in_act_now(tmp_path):
    from features.monitor.presentation import present_inbox_item

    store = MonitorStore(tmp_path / "monitor.db")
    item = store.upsert_item("db", "stuck_query", "q1", 0.9, "Stuck query", {}, 100)
    store.action_item(item["id"], "resolve", 200, resolved_by="code")
    resolved = store.read("SELECT * FROM inbox_item WHERE id=?", (item["id"],))[0]
    assert present_inbox_item(resolved, {}, [], set())["severity_band"] == "recovered"


def test_a_held_lock_postpones_the_index_read_without_an_outage(tmp_path):
    from unittest.mock import MagicMock

    from features.monitor.indexes import IndexesBusy, read_indexes

    class Blocked(Exception):
        pgcode = "55P03"

    connection = MagicMock()
    cursor = connection.cursor.return_value
    cursor.execute.side_effect = lambda sql, *a: (_ for _ in ()).throw(Blocked()) \
        if "pg_indexes" in sql else None
    with pytest.raises(IndexesBusy):
        read_indexes(connection, "postgresql")
    assert any("ROLLBACK" in call.args[0] for call in cursor.execute.call_args_list)

    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    active = loop.MonitorLoop(store, clock=lambda: 1000,
                              index_reader=lambda target: (_ for _ in ()).throw(IndexesBusy()),
                              judge=lambda chosen, context: None)
    active.cycle("db")
    assert not [item for item in store.inbox() if item["subject"] == "lost_connection"]
@pytest.mark.parametrize("testing", [False, True])
def test_lock_threshold_env_overrides_are_test_only(tmp_path, monkeypatch, testing):
    monkeypatch.setenv("RDST_TESTING", "1" if testing else "0")
    monkeypatch.setenv("RDST_MONITOR_LOCK_WAIT_OPEN_MINUTES", "2")
    monkeypatch.setenv("RDST_MONITOR_LOCK_WAIT_RECOVERY_MINUTES", "1")
    monkeypatch.setattr(loop, "_top_blocker", lambda target: None)
    store = MonitorStore(tmp_path / "monitor.db")
    monitor = loop.MonitorLoop(store=store, sampler=object())
    for ts in (60, 120):
        record(store, ts, 4)
        monitor._lock_contention("db", ts)
    assert bool(store.inbox()) == testing
    if not testing:
        for ts in (180, 240, 300):
            record(store, ts, 4)
            monitor._lock_contention("db", ts)
    record(store, 360, 0)
    monitor._lock_contention("db", 360)
    assert (store.read("SELECT state FROM inbox_item")[0]["state"] == "resolved") == testing

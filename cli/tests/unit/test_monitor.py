from __future__ import annotations

import sqlite3
import selectors
import pytest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.monitor.api import routes
from features.query_registry.discovery import (
    PG_MONITOR_DB_SQL, QueryDiscoveryCollector, mysql_monitor_sample,
)
from features.query_registry.statement_stats import MysqlDigestSource, MYSQL_ACTIVITY_SAMPLE_SQL
from features.query_registry.scheduler import ACTIVE_INTERVAL_SECONDS, ObservationScheduler
from shared.constants import rdst_data_dir
from shared.monitor.monitor_store import (
    FINE_RETENTION_SECONDS, RETENTION, MonitorStore, SCHEMA_VERSION)
from shared.query_registry import hash_sql
from shared.query_registry.delta_processor import COMPLETE, COUNTER_REGRESSION, CounterRow, DeltaResult


def test_data_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path / "monitor"))
    assert rdst_data_dir() == tmp_path / "monitor"
    monkeypatch.setenv("RDST_DATA_DIR", "~/rdst-test")
    assert rdst_data_dir().is_absolute()
    assert PG_MONITOR_DB_SQL.startswith("/*rdst:observe*/")


def test_mysql_monitor_status_and_buffer_ratio(tmp_path):
    def execute(sql, _params):
        if "SHOW GLOBAL STATUS" in sql:
            return [("Com_commit", "10"), ("Com_rollback", "3"),
                    ("Innodb_buffer_pool_read_requests", "1000"),
                    ("Innodb_pages_read", "100"), ("Threads_connected", "4")]
        if "INNODB_METRICS" in sql:
            return [("2",)]
        if "SUM_ERRORS" in sql:
            return [(4,)]
        return [("max_connections", "100")]

    # Rollbacks: 3 ROLLBACK statements plus 4 failed statements, like xact_rollback.
    first = mysql_monitor_sample(execute, "epoch")
    assert first == (10, 7, 1000, 100, 2, 4, 100, "epoch", None)
    store = MonitorStore(tmp_path / "monitor.db")
    collector = QueryDiscoveryCollector("db", two_phase=True)
    collector._monitor = store
    collector._engine = "mysql"
    collector._alias_map = {}
    collector._monitor_db_sample = first
    registry = SimpleNamespace(get_query=lambda _: None)
    collector._record_monitor_cycle(60, [], registry, [])
    collector._monitor_db_sample = (15, 9, 1100, 110, 3, 5, 100, "epoch")
    collector._record_monitor_cycle(120, [], registry, [])
    row = store.read("SELECT * FROM db_series WHERE bucket_start=120")[0]
    assert (row["active_conns"], row["max_conns"]) == (5, 100)
    assert (row["xact_commit"], row["xact_rollback"], row["deadlocks"]) == (5, 2, 1)
    assert row["cache_hit_ratio"] == 0.9
    collector._monitor_db_sample = (2, 0, 2, 1, 0, 5, 100, "new-epoch")
    collector._record_monitor_cycle(180, [], registry, [])
    assert store.read("SELECT xact_rollback FROM db_series WHERE bucket_start=180")[0]["xact_rollback"] == 0


def test_mysql_processlist_maps_to_stuck_sample(tmp_path):
    assert "performance_schema.threads" in MYSQL_ACTIVITY_SAMPLE_SQL
    source = MysqlDigestSource()
    texts = source.activity_sample(lambda *_: [("SELECT SLEEP(70)", 42, "executing", 70)])
    assert texts == ["SELECT SLEEP(70)"]
    store = MonitorStore(tmp_path / "monitor.db")
    collector = QueryDiscoveryCollector("db", two_phase=True)
    collector._monitor = store
    collector._engine = "mysql"
    collector._alias_map = {}
    collector._monitor_db_sample = None
    collector._record_monitor_cycle(120, [], SimpleNamespace(get_query=lambda _: None),
                                    source.last_activity_details, activity_sampled=True)
    assert store.read("SELECT pid,duration_s FROM stuck_sample") == [
        {"pid": 42, "duration_s": 70.0}]


def test_mysql_digest_delta_feeds_db_qps(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    collector = QueryDiscoveryCollector("db", two_phase=True)
    collector._monitor = store
    collector._engine = "mysql"
    collector._alias_map = {"shopdb:digest": "key"}
    collector._monitor_db_sample = (0, 0, 100, 1, 0, 2, 100, "epoch")
    collector._baseline = {"shopdb:digest": CounterRow(
        "shopdb:digest", 60, 10, 600, 10, 10, 10, 120, "epoch")}
    registry = SimpleNamespace(get_query=lambda _: SimpleNamespace(
        original_sql="SELECT id FROM orders WHERE total >= ?"))
    delta = DeltaResult("shopdb:digest", 60, 120, COMPLETE, 60, 600.0, 1.0, 60)
    collector._record_monitor_cycle(120, [delta], registry, [])
    assert store.read("SELECT calls FROM db_series")[0]["calls"] == 60
    assert store.read("SELECT calls FROM stmt_series")[0]["calls"] == 60


def test_mysql_history_text_is_the_masked_sample_not_the_digest(tmp_path):
    """DIGEST_TEXT (`COUNT ( * )`, `EVENTS`) doesn't run back on MySQL; the stored text is
    the sampled statement with its values masked."""
    store = MonitorStore(tmp_path / "monitor.db")
    collector = QueryDiscoveryCollector("db", two_phase=True)
    collector._monitor = store
    collector._engine = "mysql"
    collector._alias_map = {"shop:d1": "key"}
    collector._monitor_db_sample = None
    current = CounterRow("shop:d1", 60, 10, 600, 10, 10, 10, 120, "epoch")
    collector._baseline = {"shop:d1": current}
    delta = DeltaResult("shop:d1", 60, 120, COMPLETE, 60, 600.0, 1.0, 60)
    collector._record_monitor_cycle(
        120, [delta], SimpleNamespace(get_query=lambda _: None), [], [current],
        sample_texts={"shop:d1": "SELECT event_type, COUNT(*) FROM events "
                                 "WHERE customer_id = 42 GROUP BY event_type"},
        observed_texts={"shop:d1": "SELECT `event_type` , COUNT ( * ) FROM EVENTS "
                                   "WHERE `customer_id` = ? GROUP BY `event_type`"})
    assert store.read("SELECT text FROM query_text")[0]["text"] == (
        "SELECT event_type, COUNT(*) FROM events WHERE customer_id = ? GROUP BY event_type")


def test_monitor_keeps_text_for_identity_outside_library_admission(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    collector = QueryDiscoveryCollector("db", two_phase=True)
    collector._monitor = store
    collector._engine = "postgresql"
    engine_key = "1:2:3:t"
    collector._alias_map = {engine_key: "key"}
    current = CounterRow(engine_key, 60, 10, 600, 10, 10, 10, 120, "epoch")
    collector._baseline = {engine_key: current}
    delta = DeltaResult(engine_key, 60, 120, COMPLETE, 10, 600.0, 1.0, 10)
    registry = SimpleNamespace(get_query=lambda _: None)

    collector._record_monitor_cycle(
        120, [delta], registry, [], [current], observed_texts={
            engine_key: "SELECT count(*) FROM stress_items WHERE id >= $1"})

    assert store.read("SELECT query_key,text FROM query_text WHERE target='db'") == [
        {"query_key": "key", "text": "SELECT count(*) FROM stress_items WHERE id >= $1"}]
    assert store.read("SELECT calls FROM stmt_series WHERE target='db'")[0]["calls"] == 10


def test_monitor_text_backfill_keeps_first_series_time(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.record("db", 120, [{"query_key": "key", "calls": 3, "total_ms": 6}], {}, [])
    store.record("db", 300, [], {"key": "SELECT * FROM stress_items"}, [])

    assert store.read("SELECT first_seen,last_seen FROM query_text WHERE target='db'") == [
        {"first_seen": 120, "last_seen": 300}]


def test_pg_scoped_counter_reset_rebaselines_mass_regressions():
    collector = QueryDiscoveryCollector("db", two_phase=True)
    collector._engine = "postgresql"
    collector._baseline_captured_at = 60
    collector._baseline = {
        str(i): CounterRow(str(i), 60, 10, 600, 10, 10, 10, 60, "epoch")
        for i in range(150)}
    reset_rows = [CounterRow(str(i), 2, 1, 20, 10, 10, 10, 120, "epoch")
                  for i in range(30)]
    eviction_rows = [CounterRow(str(i), 61, 11, 610, 10, 10, 10, 120, "epoch")
                     for i in range(140)]

    assert collector._pg_target_reset(reset_rows)
    assert not collector._pg_target_reset(eviction_rows)


def test_schema_and_settings(tmp_path):
    path = tmp_path / "monitor.db"
    store = MonitorStore(path)
    settings = store.settings("db")
    assert {key: settings[key] for key in ("enabled", "deep_analysis", "execute")} == {
        "enabled": False, "deep_analysis": False, "execute": False
    }
    assert settings["size_bytes"] > 0
    assert settings["budget_bytes"] == store.budget_bytes
    assert store.set_settings("db", {"enabled": True})["enabled"]
    assert store.monitored_targets() == {"db"}
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {
        "query_text", "stmt_series", "db_series", "stuck_sample", "index_snapshot",
        "index_change", "inbox_item", "inbox_event", "judge_run", "analyze_job",
        "report_run", "monitor_setting",
    } <= tables
    store.ensure_open()
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    newer = MonitorStore(path)
    try:
        newer.ensure_open()
    except RuntimeError as exc:
        assert "newer" in str(exc)
    else:
        assert False, "newer schema was accepted"


def test_query_text_is_capped_at_eight_kib(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.record("db", 60, [], {"q": "x" * 9000}, [], None)
    assert len(store.read("SELECT text FROM query_text")[0]["text"].encode()) == 8192


def test_rollup_purge_and_budget(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", budget_bytes=10_000, clock=lambda: 86400)
    for minute in range(5):
        store.record("db", minute * 60, [
            {"query_key": "heavy", "calls": 2, "total_ms": 20, "rows": 1},
            {"query_key": "light", "calls": 1, "total_ms": 1},
        ], {"heavy": "SELECT * FROM t", "light": "SELECT * FROM u"}, [], {
            "calls": 3, "total_ms": 21, "active_conns": 2, "max_conns": 10,
            "cache_hit_ratio": 0.9, "xact_commit": 2, "xact_rollback": 0,
            "deadlocks": 0,
        })
    store.rollup("db", 300)
    assert store.read(
        "SELECT calls FROM stmt_series WHERE target='db' AND query_key='heavy' "
        "AND res=300 AND bucket_start=0"
    )[0]["calls"] == 10
    store.rollup("db", 3600)
    assert store.read(
        "SELECT calls FROM db_series WHERE target='db' AND res=3600"
    )[0]["calls"] == 15
    store.rollup("db", 86400)
    assert store.read(
        "SELECT calls FROM stmt_series WHERE target='db' AND res=86400 "
        "AND query_key='light'"
    )[0]["calls"] == 5
    store.budget_bytes = 250
    store.rollup("db", 86400)
    store.maintain()
    assert store.read("SELECT * FROM stmt_series WHERE res IN (60,300)") == []
    assert store.read("SELECT * FROM stmt_series WHERE res=86400")
    assert len([row for row in store.inbox(target="db")
                if row["subject"] == "storage_budget"]) == 1
    store.rollup("db", 86400)
    store.maintain()
    assert len(store.read("SELECT e.id FROM inbox_event e JOIN inbox_item i ON i.id=e.item_id "
                          "WHERE i.subject='storage_budget' AND e.kind='created'")) == 1


def test_long_tail_demotion_keeps_heavy_minute_rows(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", budget_bytes=10_000)
    for minute in range(5):
        store.record("db", minute * 60, [
            {"query_key": "heavy", "calls": 10, "total_ms": 100},
            {"query_key": "light", "calls": 1, "total_ms": 1},
        ], {}, [], None)
    store.rollup("db", 300)
    # This is below SQLite's minimum schema footprint, so all eligible fine rows go.
    store.budget_bytes = 1100
    store.rollup("db", 300)
    store.maintain()
    assert not store.read("SELECT * FROM stmt_series WHERE res=60 AND query_key='light'")
    assert not store.read("SELECT * FROM stmt_series WHERE res=60 AND query_key='heavy'")
    assert store.read("SELECT * FROM stmt_series WHERE res=300 AND query_key='light'")


@pytest.mark.parametrize("stage", ["1m", "5m", "report", "analysis", "1h"])
def test_budget_policy_stops_at_each_stage(monkeypatch, tmp_path, stage):
    now = 100 * 86400
    old = now - 40 * 86400
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: now)
    store.ensure_open()

    def seed(conn):
        conn.execute("INSERT INTO query_text VALUES ('db','light','SELECT 1',0,?)", (now,))
        conn.execute("INSERT INTO query_text VALUES ('db','heavy','SELECT 2',0,?)", (now,))
        if stage in ("1m", "5m"):
            res, parent = (60, 300) if stage == "1m" else (300, 3600)
            for key, weight in (("light", 1), ("heavy", 100)):
                for resolution in (res, parent):
                    conn.execute("INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
                                 ("db", key, resolution, now - 3600, 1, weight, 0, 0, 0))
        if stage == "report":
            conn.execute("INSERT INTO report_run (target,ts,trigger,state,report_json) "
                         "VALUES ('db',?,'nightly','done',?)",
                         (old, '{"summary":"keep","detail":"' + 'x' * 1000 + '"}'))
        if stage == "analysis":
            conn.execute("INSERT INTO analyze_job (target,query_key,reason,state,priority,enqueued_at) "
                         "VALUES ('db','light','new','done',0,?)", (old,))
        if stage == "1h":
            for resolution in (3600, 86400):
                conn.execute("INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
                             ("db", "light", resolution, old // resolution * resolution,
                              1, 1, 0, 0, 0))
    store._write(seed)

    def size(conn, _target):
        if stage in ("1m", "5m", "1h"):
            res = {"1m": 60, "5m": 300, "1h": 3600}[stage]
            remaining = conn.execute("SELECT 1 FROM stmt_series WHERE res=? AND query_key='light'",
                                     (res,)).fetchone()
        elif stage == "report":
            remaining = conn.execute("SELECT 1 FROM report_run WHERE report_json LIKE '%detail%'").fetchone()
        else:
            remaining = conn.execute("SELECT 1 FROM analyze_job").fetchone()
        return 2 if remaining else 0

    monkeypatch.setattr(MonitorStore, "_target_size", staticmethod(size))
    store.budget_bytes = 1
    store._enforce_budget("db")
    with store._connect() as conn:
        assert size(conn, "db") == 0
    assert len(store.read("SELECT * FROM query_text WHERE target='db'")) == 2
    if stage in ("1m", "5m"):
        res = 60 if stage == "1m" else 300
        assert store.read("SELECT 1 FROM stmt_series WHERE res=? AND query_key='heavy'", (res,))
    if stage == "report":
        assert store.read("SELECT report_json FROM report_run")[0]["report_json"] == '{"summary":"keep"}'
    if stage == "1h":
        assert store.read("SELECT 1 FROM stmt_series WHERE res=86400")


def test_budget_size_counts_report_analysis_and_inbox_pages(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    initial = store.settings("db")["size_bytes"]
    report, _ = store.claim_report("db", "user", 100)
    store.update_report(report["id"], report={"summary": "x" * 16000})
    report_size = store.settings("db")["size_bytes"]
    assert report_size > initial
    store.enqueue_analysis("db", "q", "new", 1, 100)
    store.upsert_item("db", "monitoring_issue", "large", 1, "Issue",
                      {"detail": "x" * 16000}, 100)
    assert store.settings("db")["size_bytes"] > report_size
    with sqlite3.connect(store.path) as conn:
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    assert store.settings("db")["size_bytes"] == (page_count - freelist) * page_size


def test_budget_reclaims_file_pages_when_history_can_fit(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", budget_bytes=10**9,
                         clock=lambda: 100_000)
    store.ensure_open()

    def seed(conn):
        conn.executemany("INSERT INTO query_text VALUES (?,?,?,?,?)",
                         [("db", f"q{i}", "SELECT 1", 0, 100_000) for i in range(1000)])
        conn.executemany("INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
                         [("db", f"q{i}", res,
                           99_600 + minute * 60 if res == 60 else 99_600,
                           1, 1, 0, 0, 0)
                          for i in range(1000) for res in (60, 300)
                          for minute in (range(5) if res == 60 else range(1))])
    store._write(seed)
    store.budget_bytes = 420_000
    store.maintain()
    with sqlite3.connect(store.path) as conn:
        physical = (conn.execute("PRAGMA page_count").fetchone()[0] *
                    conn.execute("PRAGMA page_size").fetchone()[0])
    assert physical <= store.budget_bytes
    assert not [item for item in store.inbox(target="db")
                if item["subject"] == "storage_budget"]


def test_retention_keeps_exact_boundary(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    for ts in (0, 60):
        store.record("db", ts, [
            {"query_key": "q", "calls": 1, "total_ms": 1}
        ], {}, [], None)
    store.rollup("db", RETENTION[60] + 60)
    assert store.read(
        "SELECT bucket_start FROM stmt_series WHERE res=60 ORDER BY bucket_start"
    ) == [{"bucket_start": 60}]
    store.budget_bytes = 10_000
    store.record("db", RETENTION[60] + 200_000, [], {}, [], None)
    assert store.read("SELECT * FROM stmt_series WHERE res=60") == []
    assert store.read("SELECT * FROM stmt_series WHERE res=86400")


def test_minute_rows_last_48_hours_for_every_query(tmp_path):
    clock = {"now": 0}
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: clock["now"])
    loads = {"heavy": 100, "middle": 10, "light": 1}
    for hour in range(4):
        for minute in range(0, 3600, 900):
            store.record("db", hour * 3600 + minute, [
                {"query_key": key, "calls": 1, "total_ms": ms} for key, ms in loads.items()
            ], {}, [], None)
    clock["now"] = 3 * 86400
    store.record("db", clock["now"], [], {}, [], None)
    before = store.read("SELECT query_key,res,SUM(calls) calls FROM stmt_series "
                        "WHERE res IN (300,3600) GROUP BY query_key,res ORDER BY 1,2")

    store.maintain(pause_s=0)

    minutes = {row["query_key"]: row["n"] for row in store.read(
        "SELECT query_key,COUNT(*) n FROM stmt_series WHERE res=60 AND bucket_start<? "
        "GROUP BY query_key", (clock["now"] - FINE_RETENTION_SECONDS,))}
    assert minutes == {}
    # The rollups still hold every query, so charts read 5-minute rows where minutes are gone.
    assert store.read("SELECT query_key,res,SUM(calls) calls FROM stmt_series "
                      "WHERE res IN (300,3600) GROUP BY query_key,res ORDER BY 1,2") == before
    store.maintain(pause_s=0)

    clock["now"] = RETENTION[60] + 3600
    store.record("db", clock["now"], [], {}, [], None)
    assert not store.read("SELECT 1 FROM stmt_series WHERE res=60 AND bucket_start<3600")


def test_window_resolution_uses_minutes_for_incident_retention(tmp_path):
    from features.monitor.api.views import window_res

    now = 30 * 86400
    assert window_res(3600, now - 5 * 86400, now) == 60
    assert window_res(3600, now - 8 * 86400, now) == 60


def test_sink_skips_reset_and_attributes_gap_to_current_bucket(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    collector = QueryDiscoveryCollector("db", two_phase=True)
    collector._monitor = store
    collector._alias_map = {"engine": "key"}
    collector._baseline = {}
    collector._monitor_db_sample = (10, 1, 90, 10, 0, 3, 20)
    registry = SimpleNamespace(get_query=lambda key: SimpleNamespace(original_sql="SELECT * FROM t"))
    current = CounterRow(
        "engine", 6, 2, 120, 20, 20, 20, 180, "epoch", blks_hit=10, blks_read=2
    )
    collector._baseline = {"engine": current}
    collector._record_monitor_cycle(180, [], registry, [], [current])
    assert store.read("SELECT text FROM query_text")[0]["text"] == "SELECT * FROM t"
    collector._baseline = {
        "engine": CounterRow(
            "engine", 12, 8, 240, 20, 20, 20, 240, "epoch",
            blks_hit=15, blks_read=3,
        )
    }
    gap = DeltaResult("engine", 60, 240, COMPLETE, 6, 120.0, 6 / 180, 6)
    reset = DeltaResult("engine", 240, 300, COUNTER_REGRESSION, None, None, None, 2)
    collector._record_monitor_cycle(240, [gap, reset], registry, [
        {"query": "SELECT * FROM t", "pid": 7, "state": "active", "duration_s": 90}
    ])
    assert store.read(
        "SELECT bucket_start,calls,total_ms,rows,blks_hit,blks_read FROM stmt_series"
    ) == [
        {"bucket_start": 240, "calls": 6, "total_ms": 120.0,
         "rows": 6, "blks_hit": 5, "blks_read": 1}
    ]
    assert store.read("SELECT pid FROM stuck_sample")[0]["pid"] == 7
    collector._monitor_db_sample = (2, 0, 3, 1, 0, 2, 20)
    collector._record_monitor_cycle(300, [reset], registry, [])
    assert store.read("SELECT * FROM stmt_series WHERE bucket_start=300") == []
    assert store.read("SELECT xact_commit FROM db_series WHERE bucket_start=300")[0]["xact_commit"] == 0
    collector._monitor_db_previous = (2, 0, 3, 1, 0, 2, 20, 1)
    collector._monitor_db_sample = (20, 5, 110, 15, 1, 2, 20, 2)
    collector._record_monitor_cycle(360, [], registry, [])
    assert store.read("SELECT xact_commit FROM db_series WHERE bucket_start=360")[0]["xact_commit"] == 0


def test_monitor_sink_matches_activity_query_id_to_statement_identity(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    collector = QueryDiscoveryCollector("db", two_phase=True)
    collector._monitor = store
    collector._engine = "postgresql"
    collector._alias_map = {"10:20:-987:t": "key"}
    registry = SimpleNamespace(get_query=lambda key: SimpleNamespace(
        original_sql="SELECT * FROM t WHERE id=$1"))
    current = CounterRow("10:20:-987:t", 1, 1, 10, 10, 10, 10, 180, "epoch")
    collector._record_monitor_cycle(180, [], registry, [
        {"query": "SELECT * FROM t WHERE id=7", "pid": 1, "state": "active",
         "query_id": -987, "duration_s": 0.09},
        {"query": "SELECT * FROM other WHERE id=8", "pid": 2, "state": "active",
         "query_id": 123, "duration_s": 0.09},
    ], [current])
    assert store.read("SELECT query_key,text FROM query_sample") == [
        {"query_key": "key", "text": "SELECT * FROM t WHERE id=7"}]


def test_stuck_query_is_filed_and_closed_on_collector_cadence(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    store = MonitorStore()
    collector = QueryDiscoveryCollector("db", two_phase=True)
    collector._monitor = store
    collector._engine = "postgresql"
    collector._alias_map = {}
    collector._monitor_db_sample = None
    sql = "SELECT pg_sleep(180) FROM orders WHERE id = 1"
    key = hash_sql(sql)
    registry = SimpleNamespace(get_query=lambda _key: None)
    collector._record_monitor_cycle(180, [], registry, [
        {"query": sql, "pid": 7, "state": "active", "duration_s": 75}
    ], activity_sampled=True)
    assert store.read("SELECT query_key,text FROM query_text") == [
        {"query_key": key, "text": sql}]
    assert store.inbox("new", "db")[0]["subject"] == key
    monkeypatch.setattr(routes, "_now", lambda: 180)
    assert any(row["key"] == key and row["text"] == sql
               for row in routes.queries("db")["queries"])
    collector._record_monitor_cycle(240, [], registry, [], activity_sampled=False)
    assert store.inbox("new", "db")[0]["subject"] == key
    collector._record_monitor_cycle(300, [], registry, [], activity_sampled=True)
    assert store.inbox("resolved", "db")[0]["resolved_by"] == "code"


def test_monitored_cadence_without_subscriber():
    class Coordinator:
        def has_subscribers(self, target):
            return False
    scheduler = ObservationScheduler(
        Coordinator(), targets_provider=lambda: ["db"], monitored_provider=lambda: {"db"},
        clock=lambda: 1_000,
    )
    scheduler._refresh_targets(1_000)
    assert scheduler._base_interval("db") == ACTIVE_INTERVAL_SECONDS
    assert scheduler._schedules["db"].next_due - 1_000 <= ACTIVE_INTERVAL_SECONDS
    assert scheduler._next_interval(ACTIVE_INTERVAL_SECONDS, 6) == 120


def test_api_queries_health_and_series(monkeypatch, tmp_path):
    # The restricted CI sandbox denies socketpair wakeups used by TestClient's
    # background event loop. Polling lets that loop receive cross-thread work.
    original_select = selectors.EpollSelector.select
    monkeypatch.setattr(
        selectors.EpollSelector, "select",
        lambda self, timeout=None: original_select(
            self, 0.01 if timeout is None else min(timeout, 0.01)
        ),
    )
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "_now", lambda: 100_000)
    store = MonitorStore()
    store.record("db", 13_540, [
        {"query_key": "key", "calls": 30, "total_ms": 600},
    ], {"key": "SELECT * FROM t"}, [], None)
    store.record("db", 99_960, [
        {"query_key": "key", "calls": 60, "total_ms": 600},
    ], {"key": "SELECT * FROM t"}, [], {
        "calls": 60, "total_ms": 600, "active_conns": 2, "max_conns": 10,
        "cache_hit_ratio": 0.9, "xact_commit": 50, "xact_rollback": 1,
        "deadlocks": 0,
    })
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "INSERT INTO index_change (target,ts,kind,schema,\"table\",index_name,definition) "
            "VALUES (?,?,?,?,?,?,?)",
            ("db", 99_970, "added", "public", "t", "idx_t", "CREATE INDEX idx_t ON t(id)"),
        )
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/monitor")
    client = TestClient(app)
    settings = client.put("/api/monitor/targets/db/settings", json={"enabled": True})
    assert settings.status_code == 200
    assert client.get("/api/monitor/targets/db/settings").json()["enabled"]
    health = client.get("/api/monitor/targets/db/health").json()
    assert health["tiles"]["qps"] == 1
    assert health["tiles"]["qps_now"] == 1
    queries = client.get("/api/monitor/targets/db/queries").json()["queries"]
    assert queries[0]["mean_ms"] == 10
    assert queries[0]["tables"] == ["t"]
    assert queries[0]["db_time_share"] == 1
    assert queries[0]["change_vs_prior"] == {"latency": -0.5, "qps": 1.0}
    series = client.get("/api/monitor/targets/db/queries/key/series").json()
    assert series["markers"][0]["index_name"] == "idx_t"
    assert [point["calls"] for point in series["points"] if point["calls"]] == [60]
    assert client.get(
        "/api/monitor/targets/db/queries/key/series?range=all"
    ).json()["points"]


def test_api_reads_demoted_query_history(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "_now", lambda: 100_000)
    store = MonitorStore(clock=lambda: 100_000)
    store.record("db", 95_000, [
        {"query_key": "tail", "calls": 5, "total_ms": 25},
    ], {"tail": "SELECT * FROM t"}, [], None)
    store.budget_bytes = 0
    store.rollup("db", 100_000)
    store.maintain()
    assert not store.read("SELECT * FROM stmt_series WHERE res IN (60,300)")
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/monitor")
    # TestClient's portal needs polling in this sandbox.
    original_select = selectors.EpollSelector.select
    monkeypatch.setattr(
        selectors.EpollSelector, "select",
        lambda self, timeout=None: original_select(
            self, 0.01 if timeout is None else min(timeout, 0.01)
        ),
    )
    client = TestClient(app)
    row = client.get("/api/monitor/targets/db/queries").json()["queries"][0]
    assert row["key"] == "tail"
    # Rates cover the recorded part of the range, from the first reading.
    assert row["qps"] == pytest.approx(5 / 5000)
    assert row["sparkline"]
    points = client.get("/api/monitor/targets/db/queries/tail/series").json()["points"]
    # The demoted hourly row is spread across the minute buckets it covers.
    assert sum(point["calls"] or 0 for point in points) == pytest.approx(5)


async def test_stream_emits_tick_after_collector_pass(monkeypatch):
    class Collector:
        async def subscribe(self):
            yield SimpleNamespace(event="discovery_update", data={"state": "watching"})
    monkeypatch.setattr(routes.query_discovery, "collector_for", lambda target: Collector())
    response = await routes.stream("db")
    event = await anext(response.body_iterator)
    assert event["event"] == "tick"
    assert '"target": "db"' in event["data"]


def test_a_baseline_reading_records_no_rates_on_mysql(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    collector = QueryDiscoveryCollector("db", two_phase=True)
    collector._monitor = store
    collector._engine = "mysql"
    collector._alias_map = {"shopdb:digest": "key"}
    collector._baseline = {"shopdb:digest": CounterRow(
        "shopdb:digest", 60, 10, 600, 10, 10, 10, 120, "epoch")}
    registry = SimpleNamespace(get_query=lambda _: SimpleNamespace(
        original_sql="SELECT id FROM orders WHERE total >= ?"))
    collector._monitor_db_sample = (10, 2, 100, 1, 0, 2, 100, "epoch")
    stale = DeltaResult("shopdb:digest", 0, 120, COMPLETE, 600, 6000.0, 5.0, 600)
    collector._record_monitor_cycle(120, [stale], registry, [], baseline=True)
    assert store.read("SELECT COUNT(*) n FROM stmt_series")[0]["n"] == 0
    assert store.read("SELECT COUNT(*) n FROM db_series")[0]["n"] == 0
    assert store.read("SELECT detail FROM monitor_pulse")[0]["detail"] == "Read 1 query"
    # The next reading rates from the baseline's counters.
    collector._monitor_db_sample = (15, 3, 110, 1, 0, 2, 100, "epoch")
    delta = DeltaResult("shopdb:digest", 120, 180, COMPLETE, 60, 600.0, 1.0, 60)
    collector._record_monitor_cycle(180, [delta], registry, [])
    row = store.read("SELECT calls,xact_commit,xact_rollback FROM db_series")[0]
    assert (row["calls"], row["xact_commit"], row["xact_rollback"]) == (60, 5, 1)

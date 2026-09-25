"""Retention keeps incident evidence while bounding local monitoring history."""

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.monitor.api import routes, views
from shared.monitor.monitor_store import (
    FINE_RETENTION_SECONDS,
    LocalMonitorLimitError, MonitorStore, RETENTION,
)

NOW = 100 * 86400
KEY = "0123456789ab"
OTHER = "abcdef012345"


def seed_series(store, target, keys, times, resolutions=(60,)):
    def write(conn):
        for res in resolutions:
            for ts in times:
                bucket = ts // res * res
                for index, key in enumerate(keys):
                    conn.execute("INSERT OR IGNORE INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
                                 (target, key, res, bucket, 60, index + 1, 0, 0, 0))
                conn.execute("INSERT OR IGNORE INTO db_series "
                             "(target,res,bucket_start,calls,total_ms) VALUES (?,?,?,?,?)",
                             (target, res, bucket, 60, 600))
    store._write(write)


def test_top_thousand_share_one_retention_rule(tmp_path):
    assert RETENTION[60] == 48 * 3600
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    keys = [f"{index:012x}" for index in range(1000)]
    seed_series(store, "db", keys, [NOW - 3 * 86400, NOW - 40 * 86400], (60, 300, 3600))
    store.rollup("db", NOW)
    store.maintain(pause_s=0)
    assert not store.read("SELECT query_key FROM stmt_series WHERE res=60")
    old_hourly = store.read("SELECT query_key FROM stmt_series WHERE res=3600 AND bucket_start<?",
                           (NOW - 30 * 86400,))
    assert {row["query_key"] for row in old_hourly} == set(keys)


@pytest.mark.parametrize("state", ["new", "resolved", "dismissed"])
def test_pinned_query_and_database_minutes_survive_age_and_budgets(tmp_path, state):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW, budget_bytes=1,
                         install_budget_bytes=1)
    onset = NOW - 10 * 86400
    item = store.upsert_item("db", "latency_regression", KEY, 1, "Query slowed down",
                             {"query_key": KEY}, onset, notify=False)
    end = onset + 600
    if state != "new":
        store.action_item(item["id"], "resolve" if state == "resolved" else "dismiss", end)
    points = [onset - 3600, onset, end + 3540, end + 3660]
    seed_series(store, "db", [KEY, OTHER], points, (60, 300, 3600))
    store.rollup("db", NOW)
    store.maintain(pause_s=0)
    kept = {row["bucket_start"] for row in store.read(
        "SELECT bucket_start FROM stmt_series WHERE res=60 AND query_key=?", (KEY,))}
    expected = points[:2] if state == "new" else points[:3]
    assert set(expected) <= kept
    assert not store.read("SELECT 1 FROM stmt_series WHERE res=60 AND query_key=?", (OTHER,))
    assert set(expected) <= {row["bucket_start"] for row in store.read(
        "SELECT bucket_start FROM db_series WHERE res=60")}
    if state != "new":
        assert points[3] not in kept


def test_repeat_windows_remain_pinned_when_body_list_is_truncated(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    first, second = NOW - 12 * 86400, NOW - 5 * 86400
    occurrences = [{"start": first, "end": first + 600, "keys": [KEY]},
                   {"start": second, "end": second + 600, "keys": [OTHER]}]
    item = store.upsert_item("db", "many_queries_changed", "burst", 1, "Queries changed",
                             {"occurrences": occurrences}, second, notify=False)
    store.update_item_body(item["id"], {"occurrences": occurrences[1:]})
    seed_series(store, "db", [KEY, OTHER], [first, second], (60, 300, 3600))
    store.rollup("db", NOW)
    store.maintain(pause_s=0)
    assert store.read("SELECT 1 FROM stmt_series WHERE res=60 AND query_key=? AND bucket_start=?",
                      (KEY, first))
    assert store.read("SELECT 1 FROM stmt_series WHERE res=60 AND query_key=? AND bucket_start=?",
                      (OTHER, second))
    assert len(store.read("SELECT 1 FROM db_series WHERE res=60")) == 2


@pytest.mark.parametrize("delete_item", [False, True])
def test_pin_survives_30_days_until_item_deletion(tmp_path, delete_item):
    now = [NOW]
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: now[0])
    onset = NOW - 10 * 86400
    item = store.upsert_item("db", "latency_regression", KEY, 1, "Query slowed down", {}, onset)
    seed_series(store, "db", [KEY], [onset], (60, 300, 3600))
    if delete_item:
        def delete(conn):
            conn.execute("DELETE FROM inbox_event WHERE item_id=?", (item["id"],))
            conn.execute("DELETE FROM inbox_item WHERE id=?", (item["id"],))
        store._write(delete)
    else:
        now[0] = onset + 31 * 86400
    store.rollup("db", now[0])
    store.maintain(pause_s=0)
    assert bool(store.read("SELECT 1 FROM stmt_series WHERE res=60")) is not delete_item
    assert bool(store.read("SELECT 1 FROM db_series WHERE res=60")) is not delete_item


def test_daily_retention_is_two_years(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    edge = (NOW - RETENTION[86400]) // 86400 * 86400
    seed_series(store, "db", [KEY], [edge - 86400, edge], (86400,))
    store.rollup("db", NOW)
    assert [row["bucket_start"] for row in store.read("SELECT bucket_start FROM stmt_series")] == [edge]


def test_report_bodies_expire_at_90_days_and_keep_summaries(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    for ts, state in [(NOW - 90 * 86400 - 1, "done"), (NOW - 90 * 86400, "done"),
                      (NOW - 91 * 86400, "running")]:
        store._write(lambda conn, ts=ts, state=state: conn.execute(
            "INSERT INTO report_run(target,ts,trigger,state,report_json,progress_json) "
            "VALUES (?,?,'user',?,?,?)", (str(ts), ts, state,
            json.dumps({"summary": "Keep this", "snapshot": {"big": "x" * 1000}, "body": "x" * 1000}), '{}')))
    store.maintain(pause_s=0)
    rows = store.read("SELECT ts,report_json,progress_json FROM report_run ORDER BY ts")
    assert len(rows) == 3
    assert json.loads(rows[1]["report_json"]) == {"summary": "Keep this"}
    assert rows[1]["progress_json"] is None
    assert "body" in json.loads(rows[0]["report_json"])
    assert "body" in json.loads(rows[2]["report_json"])


def test_backups_keep_newest_per_store_and_ignore_other_files(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    for name in ("monitor.db", "library.db"):
        for version in (9, 10):
            path = tmp_path / f"{name}.pre-v{version}.bak"
            path.write_text("backup")
            os.utime(path, (version, version))
    other = tmp_path / "monitor.db.pre-v11.bak.tmp"
    other.write_text("staging")
    store.maintain(pause_s=0)
    assert sorted(path.name for path in tmp_path.iterdir() if path.name.endswith('.bak')) == [
        "library.db.pre-v10.bak", "monitor.db.pre-v10.bak"]
    assert other.exists()


def test_install_budget_prunes_oldest_across_targets(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", budget_bytes=10**9, install_budget_bytes=1)
    store._write(lambda conn: conn.executemany(
        "INSERT INTO query_sample(target,query_key,observed_at,text) VALUES (?,?,?,?)",
        [("new", KEY, 200, "x"), ("old", KEY, 100, "x")]))
    monkeypatch.setattr("shared.monitor.monitor_store.MAINTENANCE_BATCH_ROWS", 1)
    monkeypatch.setattr(store, "_used_bytes", lambda: 2 if len(store.read("SELECT 1 FROM query_sample")) > 1 else 1)
    store._enforce_install_budget(pause_s=0)
    assert store.read("SELECT target FROM query_sample") == [{"target": "new"}]


def test_local_cap_is_atomic_and_override_can_raise_it(monkeypatch, tmp_path):
    monkeypatch.delenv("RDST_MONITOR_LOCAL_LIMIT", raising=False)
    store = MonitorStore(tmp_path / "monitor.db")
    for index in range(4):
        store.set_settings(str(index), {"enabled": True})
    def enable(target):
        try:
            store.set_settings(target, {"enabled": True})
            return True
        except LocalMonitorLimitError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(enable, ["fifth", "sixth"])) == [False, True]
    enabled = store.monitored_targets()
    store.set_settings(next(iter(enabled)), {"enabled": True, "execute": False})
    assert len(store.monitored_targets()) == 5
    monkeypatch.setenv("RDST_MONITOR_LOCAL_LIMIT", "6")
    assert enable("extra")
    store.set_settings("extra", {"enabled": False})
    monkeypatch.delenv("RDST_MONITOR_LOCAL_LIMIT")
    store.set_settings("0", {"enabled": False})
    assert enable("replacement")


def test_sixth_turn_on_returns_clear_message_before_ai_claim(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    for index in range(5):
        store.set_settings(str(index), {"enabled": True})
    monkeypatch.setattr(routes, "_store", lambda: store)
    app = FastAPI()
    app.include_router(routes.router)
    response = TestClient(app).put('/targets/sixth/settings', json={"enabled": True})
    assert response.status_code == 409
    assert "local limit is 5" in response.json()["detail"]
    assert "Turn off monitoring" in response.json()["detail"]
    assert "sixth" not in store.monitored_targets()


def test_old_zoom_and_query_series_use_hourly_for_missing_minutes(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    hour = NOW - 5 * 86400
    seed_series(store, "db", [KEY], [hour], (3600,))
    seed_series(store, "db", [KEY], [hour + 1200], (60,))
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_now", lambda: NOW)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    suffix = f'?range=7d&from={hour}&to={hour + 3540}'
    query = client.get(f'/targets/db/queries/{KEY}/series{suffix}')
    assert query.status_code == 200, query.text
    data = query.json()
    assert data["res"] == 60
    assert all(point["calls"] is not None for point in data["points"])
    assert data["points"][20]["calls"] == 60
    assert data["points"][0]["calls"] == 1
    assert "Minute detail is kept for 48 hours" in data["detail_note"]
    health = client.get(f'/targets/db/health{suffix}')
    assert health.status_code == 200, health.text
    assert all(point["qps"] is not None for point in health.json()["series"])
    assert health.json()["detail_note"]
    load = views.load(store, "db", "7d", NOW, window=(hour, hour + 3540))
    assert load["res"] == 60
    assert all(point["calls"] is not None for point in load["points"])
    assert load["detail_note"]


def test_whole_install_budget_counts_other_stores_and_retained_backup(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.ensure_open()
    initial = store._install_size_bytes()
    (tmp_path / "library.db").write_bytes(b"x" * 1024)
    (tmp_path / "monitor.db.pre-v19.bak").write_bytes(b"x" * 2048)
    (tmp_path / "cache.db-wal").write_bytes(b"x" * 512)
    (tmp_path / "unrelated.txt").write_bytes(b"x" * 4096)
    assert store._install_size_bytes() == initial + 3584


def test_reopened_single_query_pins_each_episode_without_the_gap(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    first, second = NOW - 12 * 86400, NOW - 5 * 86400
    item = store.upsert_item("db", "latency_regression", KEY, 1, "Query slowed down", {}, first)
    store.action_item(item["id"], "resolve", first + 600)
    store.action_item(item["id"], "reopen", second)
    seed_series(store, "db", [KEY], [first, first + 2 * 86400, second], (60, 300, 3600))
    store.rollup("db", NOW)
    store.budget_bytes = 1
    store.maintain(pause_s=0)
    kept = {row["bucket_start"] for row in store.read("SELECT bucket_start FROM stmt_series WHERE res=60")}
    assert kept == {first, second}


def test_v19_migration_backfills_incident_pins(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    item = store.upsert_item("db", "latency_regression", KEY, 1, "Query slowed down", {}, NOW - 5 * 86400)
    def downgrade(conn):
        conn.execute("DROP TABLE incident_pin")
        conn.execute("PRAGMA user_version=19")
    store._write(downgrade)
    migrated = MonitorStore(store.path, clock=lambda: NOW)
    pins = migrated.read("SELECT item_id,query_key FROM incident_pin ORDER BY query_key")
    assert pins == [{"item_id": item["id"], "query_key": ""},
                    {"item_id": item["id"], "query_key": KEY}]


def test_created_events_expire_for_old_closed_items_and_keep_open_items(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    old = NOW - 40 * 86400
    closed = store.upsert_item("db", "monitoring_issue", "closed", 1, "Old issue", {}, old)
    opened = store.upsert_item("db", "monitoring_issue", "open", 1, "Open issue", {}, old)
    store.action_item(closed["id"], "resolve", old + 600)
    store.rollup("db", NOW)
    assert store.read("SELECT item_id FROM inbox_event WHERE kind='created'") == [
        {"item_id": opened["id"]}]
    assert len(store.inbox(target="db")) == 2


def test_query_detail_keeps_minute_resolution_for_a_full_day_window(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    data = views.series(store, "db", KEY, "24h", NOW, window=(NOW - 86400, NOW))
    assert data["res"] == 60


def test_forget_target_drops_only_that_targets_data(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    for target in ("gone", "kept"):
        store.set_settings(target, {"enabled": True})
        store.upsert_item(target, "monitoring_issue", "password_missing", 1.0, "Password", {}, 1000)
    store.forget_target("gone")
    assert store.monitored_targets() == {"kept"}
    assert [item["target"] for item in store.inbox()] == ["kept"]
    assert store.read("SELECT COUNT(*) n FROM inbox_event e JOIN inbox_item i ON i.id=e.item_id "
                      "WHERE i.target='gone'")[0]["n"] == 0

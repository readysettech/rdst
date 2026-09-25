"""Restart and collection gaps establish a baseline; re-enable wakes ordinary work."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.monitor.api import routes
from features.monitor import database_limit
from shared.monitor import monitor_store
from shared.monitor.monitor_store import MonitorStore


@pytest.mark.parametrize("gap", [60, 66, 600])
def test_collection_gap_keeps_readings_and_leaves_rate_gap(tmp_path, gap):
    store = MonitorStore(tmp_path / "monitor.db")
    def reading(ts, calls, *, baseline=False):
        store.record("db", ts, [] if baseline else [{"query_key": "q", "calls": calls, "total_ms": calls * 2}],
                     {"q": "SELECT * FROM orders"}, [], None if baseline else {"calls": calls, "total_ms": calls * 2},
                     workload_checked=True, queries_read=1)
    reading(600, 10000, baseline=True)
    reading(660, 600)
    reading(660 + gap, 7200)
    rows = store.read("SELECT bucket_start,calls FROM db_series WHERE res=60 ORDER BY bucket_start")
    expected = [{"bucket_start": 660, "calls": 600}]
    if gap == 60:
        expected.append({"bucket_start": 720, "calls": 7200})
    assert rows == expected
    reading(720 + gap, 600)
    assert store.read("SELECT calls FROM db_series WHERE bucket_start=?",
                      ((720 + gap) // 60 * 60,)) == [{"calls": 600}]
    assert len(store.read("SELECT * FROM monitor_pulse WHERE kind='collect'")) == 4
    assert store.read("SELECT text FROM query_text") == [{"text": "SELECT * FROM orders"}]


def test_restart_rebaselines_even_inside_normal_interval(tmp_path, monkeypatch):
    path = tmp_path / "monitor.db"
    def reading(store, ts, *, baseline=False):
        store.record("db", ts, [] if baseline else [{"query_key": "q", "calls": 600, "total_ms": 1200}],
                     {"q": "SELECT * FROM orders"}, [], None if baseline else {"calls": 600, "total_ms": 1200},
                     workload_checked=True, queries_read=1)
    store = MonitorStore(path)
    reading(store, 600, baseline=True)
    reading(store, 660)
    monkeypatch.setattr(monitor_store, "COLLECTOR_PROCESS", "restarted-unit-process")
    restarted = MonitorStore(path)
    reading(restarted, 720)
    assert restarted.read("SELECT bucket_start FROM db_series") == [{"bucket_start": 660}]
    reading(restarted, 780)
    assert restarted.read("SELECT bucket_start FROM db_series ORDER BY bucket_start") == [
        {"bucket_start": 660}, {"bucket_start": 780}]


def test_reenable_requests_collection_and_wakes_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    store = MonitorStore()
    store.set_settings("db", {"enabled": True})
    store.record("db", 9000, [{"query_key": "q", "calls": 600, "total_ms": 1200}],
                 {"q": "SELECT * FROM orders"}, [], {"calls": 600, "total_ms": 1200})
    store.set_settings("db", {"enabled": False})
    monkeypatch.setattr(routes, "_now", lambda: 10000)
    monkeypatch.setattr(database_limit, "claim_target", lambda *a, **kw: "on")
    monkeypatch.setattr(routes, "_start_first_run", Mock())
    from features.query_registry.scheduler import ObservationScheduler, ACTIVE_INTERVAL_SECONDS
    from features.monitor.analyze_queue import AnalyzeQueue
    from tests.unit.test_monitor_analyze_queue import FakeService

    coordinator = SimpleNamespace(has_subscribers=lambda target: False)
    scheduler = ObservationScheduler(coordinator, targets_provider=lambda: ["db"],
                                     monitored_provider=store.monitored_targets, clock=lambda: 10000)
    scheduler._refresh_targets(10000)
    assert scheduler.next_reading_at("db") > 10000
    queue = AnalyzeQueue(store, service=FakeService(), clock=lambda: 10000)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/monitor")
    app.state.monitor_scheduler = scheduler
    app.state.analyze_queue = queue
    with TestClient(app) as http:
        response = http.put("/api/monitor/targets/db/settings", json={"enabled": True, "deep_analysis": True})
    assert response.status_code == 200
    assert scheduler.next_reading_at("db") == 10000
    assert scheduler._base_interval("db") == ACTIVE_INTERVAL_SECONDS
    assert scheduler._schedules["db"].fast_until == 0
    assert queue._wake.is_set()
    queue._prepare(10000)
    assert store.analyze_jobs("db")[0]["state"] == "queued"
    assert store.monitored_targets() == {"db"}

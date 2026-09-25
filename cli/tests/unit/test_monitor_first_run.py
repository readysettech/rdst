"""Turning monitoring on starts a fast first run; pasted queries land in the list."""

from __future__ import annotations

import selectors
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.monitor.api import routes
from shared.monitor.monitor_store import MonitorStore
from shared.query_registry import hash_sql


@pytest.fixture
def client(monkeypatch, tmp_path):
    original_select = selectors.EpollSelector.select
    monkeypatch.setattr(selectors.EpollSelector, "select",
                        lambda self, timeout=None: original_select(
                            self, 0.01 if timeout is None else min(timeout, 0.01)))
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "_now", lambda: 10_000)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get",
                        lambda self, target: {"engine": "postgresql"} if target == "db" else None)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/monitor")
    return app, TestClient(app)


class RecordingScheduler:
    def __init__(self) -> None:
        self.fast: list[str] = []
        self.listener = None

    def _refresh_targets(self, now) -> None:
        pass

    def _clock(self) -> float:
        return 0.0

    def start_fast_phase(self, name, on_collected=None) -> bool:
        self.fast.append(name)
        self.listener = on_collected
        return True


class RecordingLoop:
    def __init__(self) -> None:
        self.wakes = 0

    def wake(self) -> None:
        self.wakes += 1


@pytest.mark.parametrize("with_history", [False, True])
def test_turning_monitoring_on_starts_the_first_run_once(client, monkeypatch, with_history):
    app, http = client
    scheduler, loop = RecordingScheduler(), RecordingLoop()
    app.state.monitor_scheduler = scheduler
    app.state.monitor_loop = loop
    app.state.analyze_queue = SimpleNamespace(stop=None)
    woke = []
    monkeypatch.setattr("features.monitor.analyze_queue.wake_active_queue",
                        lambda: woke.append(True) or True)

    if with_history:
        store = MonitorStore()
        store.start_first_hour("db", 1000)
        store.record("db", 2000, [], {}, [], {"calls": 1, "total_ms": 10})
        store.set_settings("db", {"enabled": False})

    turned_on = http.put("/api/monitor/targets/db/settings", json={"enabled": True})
    assert turned_on.status_code == 200 and turned_on.json()["enabled_at"] == 10_000
    expected_start = None if with_history else 10_000
    assert turned_on.json()["first_hour_started_at"] == expected_start
    assert http.get("/api/monitor/targets/db/settings").json()["first_hour_started_at"] == expected_start
    assert scheduler.fast == ["db"]
    # The first-run analysis ramp counts from here.
    assert http.get("/api/monitor/targets/db/settings").json()["enabled_at"] == 10_000
    assert loop.wakes == 1
    # A first collection queues analyses and runs the monitor pass; later ones only queue.
    scheduler.listener("db", True)
    scheduler.listener("db", False)
    assert len(woke) == 2 and loop.wakes == 2

    # Saving other settings on a monitored database doesn't restart the fast phase.
    http.put("/api/monitor/targets/db/settings", json={"enabled": True, "execute": True})
    assert scheduler.fast == ["db"]


def test_a_click_on_a_waiting_query_says_where_it_stands(client):
    _, http = client
    store = MonitorStore()
    store.set_settings("db", {"enabled": True, "deep_analysis": True})
    store.start_first_hour("db", 9_990)
    for key, weight in (("a", 300), ("b", 200), ("c", 100)):
        store.record("db", 9_995, [{"query_key": key, "calls": 5, "total_ms": weight}],
                     {key: f"SELECT * FROM {key}"}, [], None)
    from features.monitor.analyze_queue import enqueue_first_hour
    enqueue_first_hour("db", store=store, now=10_000)
    body = http.post("/api/monitor/targets/db/queries/c/analyze").json()
    assert body["already_queued"] is True and body["queue_position"] == 3
    queue = http.get("/api/monitor/targets/db/analyze-jobs").json()["queue"]
    assert [entry["query_key"] for entry in queue["next"]] == ["a", "b", "c"]
    assert queue["first_hour"]["top"] == 10 and queue["first_hour"]["analyzed"] == 0
    assert queue["first_hour"]["scheduled"] == 3 and queue["first_hour"]["failed"] == 0


def test_pasted_query_lands_in_the_list_with_its_analysis(client):
    _, http = client
    sql = "SELECT id, total FROM orders WHERE customer_id = $1;"
    response = http.post("/api/monitor/targets/db/queries", json={
        "sql": sql, "query_hash": "abc123def456", "analysis_id": "an-1", "mode": "analyze"})
    assert response.status_code == 200
    body = response.json()
    key = hash_sql("SELECT id, total FROM orders WHERE customer_id = $1")
    assert body["key"] == key and body["created"] is True
    assert body["job"]["state"] == "done" and body["job"]["started_at"] is None
    assert body["job"]["analysis_ref"] == "/api/query-registry/abc123def456/analysis/an-1"

    listed = http.get("/api/monitor/targets/db/queries?range=1h").json()["queries"]
    (row,) = [row for row in listed if row["key"] == key]
    assert row["last_analysis"]["state"] == "done"
    assert row["last_analysis"]["mode"] == "analyze"
    # Pasting it again keeps one row.
    again = http.post("/api/monitor/targets/db/queries", json={"sql": sql}).json()
    assert again == {"key": key, "created": False, "job": None}
    assert MonitorStore().read("SELECT COUNT(*) AS n FROM query_text WHERE target='db'")[0]["n"] == 1


def test_pasted_query_is_refused_for_bad_input(client):
    _, http = client
    base = "/api/monitor/targets/db/queries"
    assert http.post("/api/monitor/targets/nope/queries", json={"sql": "SELECT 1 FROM t"}
                     ).status_code == 404
    assert http.post(base, json={"sql": "  ;"}).status_code == 422
    assert http.post(base, json={"sql": "SELECT * FROM pg_stat_activity"}).status_code == 422
    assert http.post(base, json={"sql": "SELECT 1 FROM t", "query_hash": "abc"}).status_code == 422

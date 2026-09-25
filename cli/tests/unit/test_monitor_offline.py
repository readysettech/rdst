"""WAN faults leave database collection and code alerts running in a real store."""

import os
import socket
from types import SimpleNamespace

import pytest
import requests

from features.monitor import database_limit, loop as monitor_loop
from features.monitor.judge import AttentionJudge
from shared.llm_manager.jev_client import JevClient, REQUEST_TIMEOUT_SECONDS
from shared.monitor.monitor_store import MonitorStore
from tests.unit.monitor_replay_fixtures import NOW, series
from tests.unit.test_monitor_replay import Clock, ScriptedJev


class OfflineSession:
    def __init__(self, fault):
        self.fault = fault
        self.offline = True
        self.calls = []
        self.success = ScriptedJev({"q": ("notify", "latency_regression", .8)})

    def post(self, url, *, json, headers, timeout):
        self.calls.append((headers["Authorization"], timeout))
        assert timeout == REQUEST_TIMEOUT_SECONDS
        if self.offline:
            if self.fault == "dns":
                raise requests.ConnectionError(socket.gaierror("DNS unavailable"))
            if self.fault == "timeout":
                raise requests.Timeout("Provider timed out")
            if self.fault == "disconnect":
                raise requests.exceptions.ChunkedEncodingError("Response disconnected")
            status = int(self.fault)
            body = {"code": "UNAUTHORIZED" if status == 401 else "JEV_SERVICE_UNAVAILABLE"}
            response = SimpleNamespace(status_code=status, json=lambda: body,
                                       headers={"Retry-After": "120"} if status == 429 else {})
            return response
        return SimpleNamespace(status_code=200, headers={},
                               json=lambda: self.success.attention(
                                   **{key: value for key, value in json.items()
                                      if key in ("request_id", "queries", "open_items",
                                                 "target_numbers", "index_changes")}))


@pytest.mark.parametrize("fault", ["dns", "timeout", "404", "401", "429", "503", "disconnect"])
def test_wan_offline_keeps_local_alerts_and_recovers(monkeypatch, tmp_path, fault, offsets=None):
    monkeypatch.setenv("RDST_MONITOR_LEARNING_S", "0")
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(database_limit, "_signed_in", lambda: True)
    monkeypatch.setattr(database_limit, "target_fingerprint", lambda _: None)
    refreshes = []

    def token(*, force_refresh=False):
        refreshes.append(force_refresh)
        return "unit-refreshed" if force_refresh else "unit-token"

    monkeypatch.setattr(JevClient, "_session_token", staticmethod(token))
    monkeypatch.setattr(monitor_loop, "signed_in", lambda: False)
    monkeypatch.setattr(monitor_loop.query_discovery, "collector_for",
                        lambda _: SimpleNamespace(_snapshot={}))
    clock = Clock()
    store = MonitorStore(tmp_path / "monitor.db")
    session = OfflineSession(fault)
    judge = AttentionJudge(store, client=JevClient(session), clock=clock)
    indexes = []
    loop = monitor_loop.MonitorLoop(store, clock=clock, judge=judge,
                                    index_reader=lambda target: indexes.append(target) or [])
    delay = 300 if fault == "401" else 120 if fault == "429" else 60
    offsets = offsets or (0, 1, delay - 1, delay, delay + 1)
    attempts, next_retry = 0, 0
    for offset in offsets:
        if offset >= next_retry:
            attempts += 1
            next_retry = offset + delay
        clock.now = NOW + offset
        series(store, "q", now=clock.now, before_ms=10, after_ms=30)
        store.record("db", clock.now, [], {}, [],
                     {"calls": 60, "total_ms": 1800, "active_conns": 19, "max_conns": 20})
        loop.cycle("db")
        assert any(item["type"] == "connections_near_max" for item in store.inbox("new"))
    multiplier = 2 if fault == "401" else 1
    assert len(session.calls) == attempts * multiplier
    assert store.read("SELECT COUNT(*) n FROM monitor_pulse WHERE kind='collect'")[0]["n"] >= len(offsets)
    assert indexes == ["db"] * len(offsets)
    assert len(store.read("SELECT * FROM inbox_item WHERE subject='jev_login'")) == (1 if fault == "401" else 0)
    assert not store.read("SELECT * FROM inbox_item WHERE subject='database_limit'")
    assert all(item["subject"] != "lost_connection" for item in store.inbox())
    if fault == "401":
        assert refreshes.count(True) == attempts
        assert [auth for auth, _ in session.calls] == [
            "Bearer unit-token", "Bearer unit-refreshed"] * attempts
    session.offline = False
    clock.now = NOW + max(offsets) + delay
    series(store, "q", now=clock.now, before_ms=10, after_ms=30)
    loop.cycle("db")
    assert len(session.calls) == attempts * multiplier + 1
    assert store.read("SELECT outcome FROM judge_run ORDER BY id DESC LIMIT 1") == [{"outcome": "filed"}]
    assert any(item["type"] == "latency_regression" for item in store.inbox("new"))
    assert all(item["state"] == "resolved" for item in store.read(
        "SELECT state FROM inbox_item WHERE subject='jev_login'"))
    assert len(store.read("SELECT * FROM inbox_item WHERE type='connections_near_max'")) == 1


@pytest.mark.slow
@pytest.mark.skipif(os.getenv("RDST_RUN_MONITOR_SLOW") != "1",
                    reason="set RDST_RUN_MONITOR_SLOW=1 for a simulated offline hour")
@pytest.mark.parametrize("fault", ["dns", "timeout", "404", "401", "429", "503", "disconnect"])
def test_hour_offline_recovers_without_restart(monkeypatch, tmp_path, fault):
    test_wan_offline_keeps_local_alerts_and_recovers(
        monkeypatch, tmp_path, fault, offsets=tuple(range(0, 3601, 60)))

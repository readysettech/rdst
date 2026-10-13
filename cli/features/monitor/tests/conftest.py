"""Shared seeding for the redesign's contract tests: one target with a day of readings."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.monitor.api import routes, views
from shared.monitor.monitor_store import MonitorStore

# Thursday 2026-10-08 09:56:30 local, the canonical timeline's "now".
NOW = int(datetime(2026, 10, 8, 9, 56, 30).timestamp())
KEYS = [f"ca00000000{index:02d}" for index in range(1, 6)]
TEXTS = {KEYS[0]: "SELECT id, title FROM posts WHERE owneruserid = $1 ORDER BY creationdate DESC",
         KEYS[1]: "SELECT id, votetypeid FROM votes WHERE postid = $1",
         KEYS[2]: "SELECT count(*) FROM comments WHERE postid = $1",
         KEYS[3]: "SELECT id, displayname FROM users WHERE id = $1",
         KEYS[4]: "SELECT count(*), pg_sleep(600) FROM posts"}


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "_now", lambda: NOW)
    monkeypatch.setattr(views, "registry_entries", lambda keys=None: {})
    monkeypatch.setattr(views, "assessments", lambda target, keys=None: {})
    monkeypatch.setattr(views, "_engine", lambda target: "postgresql")
    monitor = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    monkeypatch.setattr(routes, "_store", lambda: monitor)
    return monitor


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/monitor")
    return TestClient(app)


def seed(store: MonitorStore, *, minutes: int = 120, target: str = "db",
         means: dict[str, float] | None = None, start: int | None = None) -> None:
    """Minute readings for the keys, each query at its own mean latency."""
    means = means or {}
    begin = NOW - minutes * 60 if start is None else start
    for minute in range(minutes):
        ts = begin + minute * 60
        rows = [{"query_key": key, "calls": 60 - index * 10,
                 "total_ms": (60 - index * 10) * means.get(key, 50.0 - index * 10)}
                for index, key in enumerate(KEYS[:4])]
        calls = sum(row["calls"] for row in rows)
        store.record(target, ts, rows, TEXTS, [],
                     {"calls": calls, "total_ms": sum(row["total_ms"] for row in rows),
                      "active_conns": 8, "max_conns": 100, "cache_hit_ratio": 0.125,
                      "xact_commit": 100, "xact_rollback": 1, "deadlocks": 0})
        store.record_pulse(target, ts + 5, "collect", True, "Read 4 queries")

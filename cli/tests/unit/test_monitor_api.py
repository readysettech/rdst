from types import SimpleNamespace
import selectors

from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.monitor.api import routes
from shared.monitor.monitor_store import MonitorStore


def test_inbox_actions_and_index_changes(monkeypatch, tmp_path):
    original_select = selectors.EpollSelector.select
    monkeypatch.setattr(selectors.EpollSelector, "select",
                        lambda self, timeout=None: original_select(
                            self, 0.01 if timeout is None else min(timeout, 0.01)))
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "_now", lambda: 1000)
    store = MonitorStore()
    first = store.upsert_item("db", "stuck_query", "a", 0.9, "Stuck", {}, 900)
    store.upsert_item("db", "stuck_query", "b", 0.8, "Stuck", {}, 900)
    store.sync_indexes("db", [], 900)
    store.sync_indexes("db", [{"schema": "public", "table": "orders",
                               "index_name": "idx", "definition": "CREATE INDEX idx ON orders (id)"}], 950)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/monitor")
    client = TestClient(app)
    assert len(client.get("/api/monitor/inbox?state=new&target=db").json()["items"]) == 2
    assert client.post(f"/api/monitor/inbox/{first['id']}/snooze",
                       json={"until": 1100}).json()["state"] == "snoozed"
    assert client.post(f"/api/monitor/inbox/{first['id']}/dismiss").json()["state"] == "dismissed"
    assert client.post(f"/api/monitor/inbox/{first['id']}/resolve").json()["state"] == "resolved"
    assert client.post(f"/api/monitor/inbox/{first['id']}/reopen").json()["state"] == "new"
    assert client.post(f"/api/monitor/inbox/{first['id']}/seen").json()["state"] == "seen"
    assert len(client.get("/api/monitor/inbox?state=seen").json()["items"]) == 1
    assert client.post("/api/monitor/inbox/mark-all-seen?target=db").status_code in (404, 405)
    assert client.get("/api/monitor/targets/db/index-changes").json()["changes"][0]["kind"] == "added"
    assert client.post("/api/monitor/inbox/999/resolve").status_code == 404
    assert client.post(f"/api/monitor/inbox/{first['id']}/snooze",
                       json={"until": 900}).status_code == 422
    assert client.get("/api/monitor/inbox?state=bad").status_code == 422


def test_aliased_join_tables():
    sql = "SELECT * FROM customers c JOIN orders o ON c.id=o.customer_id " \
          "JOIN order_items AS oi ON oi.order_id=o.id"
    assert routes._tables(sql) == ["customers", "order_items", "orders"]


def test_query_page_has_constant_sqlite_read_count(monkeypatch, tmp_path):
    original_select = selectors.EpollSelector.select
    monkeypatch.setattr(selectors.EpollSelector, "select",
                        lambda self, timeout=None: original_select(
                            self, 0.01 if timeout is None else min(timeout, 0.01)))
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "_now", lambda: 1000)
    store = MonitorStore()
    store.ensure_open()
    reads = []
    original = store.read

    def counted(sql, params=()):
        reads.append(sql)
        return original(sql, params)

    monkeypatch.setattr(store, "read", counted)
    monkeypatch.setattr(routes, "_store", lambda: store)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/monitor")
    client = TestClient(app)
    counts = []
    for n in (10, 2000):
        def seed(conn):
            conn.execute("DELETE FROM stmt_series")
            conn.execute("DELETE FROM query_text")
            conn.executemany("INSERT INTO query_text VALUES (?,?,?,?,?)",
                             [("db", f"q{i:04d}", f"SELECT {i} FROM t", 0, 900)
                              for i in range(n)])
            conn.executemany("INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
                             [("db", f"q{i:04d}", 60, 900, 1, float(i), 0, 0, 0)
                              for i in range(n)])
        store._write(seed)
        reads.clear()
        response = client.get("/api/monitor/targets/db/queries?limit=10&sort=qps")
        assert response.status_code == 200, response.text
        assert response.json()["total"] == n
        assert len(response.json()["queries"]) == 10
        counts.append(len(reads))
    assert counts[0] == counts[1]


def test_query_page_filters_and_sorts_before_pagination(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.ensure_open()
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_now", lambda: 100_000)

    def seed(conn):
        for key, name, total in (("a", "SELECT needle_slow FROM t", 30),
                                 ("b", "SELECT needle_fast FROM t", 10),
                                 ("c", "SELECT other FROM t", 100)):
            conn.execute("INSERT INTO query_text VALUES (?,?,?,?,?)",
                         ("db", key, name, 0, 100_000))
            conn.execute("INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
                         ("db", key, 60, 99_960, 1, total, 0, 0, 0))
    store._write(seed)
    first = routes.queries("db", q="needle", sort="mean_ms", limit=1)
    assert first["total"] == 2
    assert [row["key"] for row in first["queries"]] == ["a"]
    second = routes.queries("db", q="needle", sort="mean_ms", limit=1, offset=1)
    assert [row["key"] for row in second["queries"]] == ["b"]


def test_judge_runs_returns_latest_and_validates_limit(monkeypatch, tmp_path):
    original_select = selectors.EpollSelector.select
    monkeypatch.setattr(selectors.EpollSelector, "select",
                        lambda self, timeout=None: original_select(
                            self, 0.01 if timeout is None else min(timeout, 0.01)))
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    store = MonitorStore()
    store.record_judge_run("db", 10, [{"query_key": "old"}], outcome="empty")
    store.record_judge_run("db", 20, [{"query_key": "q"}],
                           request={"queries": [{"query_key": "q"}]},
                           response={"event_scope": "single"}, dry_run=False,
                           outcome="filed")
    store.record_judge_run("other", 30, [], outcome="empty")
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/monitor")
    client = TestClient(app)
    response = client.get("/api/monitor/targets/db/judge-runs?limit=1")
    assert response.status_code == 200
    assert response.json()["runs"] == [{
        "id": 2, "target": "db", "ts": 20, "dry_run": False,
        "outcome": "filed", "shortlist": [{"query_key": "q"}],
        "request": {"queries": [{"query_key": "q"}]},
        "verdict": {"event_scope": "single"}}]
    assert client.get("/api/monitor/targets/db/judge-runs?limit=101").status_code == 422


async def test_inbox_stream_emits_upsert_and_notified(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    store = MonitorStore()
    store.set_settings("db", {"quiet_hours": {"from_hour": 0, "to_hour": 0}})
    store.upsert_item("db", "stuck_query", "old", 0.9, "Stuck", {}, 100)
    class Request:
        headers = {}

        async def is_disconnected(self):
            return False
    response = await routes.inbox_stream(Request())
    store.upsert_item("db", "stuck_query", "q", 0.9, "Stuck", {}, 101)
    resync = await anext(response.body_iterator)
    first = await anext(response.body_iterator)
    second = await anext(response.body_iterator)
    assert resync == {"id": "2", "event": "upsert", "data": "{}"}
    assert first["event"] == "upsert"
    assert second["event"] == "notified"
    assert '"subject": "q"' in first["data"]
    await response.body_iterator.aclose()


async def test_inbox_stream_resumes_after_last_event_id(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    store = MonitorStore()
    store.upsert_item("db", "stuck_query", "old", 0.9, "Stuck", {}, 100)
    store.upsert_item("db", "stuck_query", "new", 0.9, "Stuck", {}, 101)
    class Request:
        headers = {"last-event-id": "2"}

        async def is_disconnected(self):
            return False
    response = await routes.inbox_stream(Request())
    assert await anext(response.body_iterator) == {"id": "2", "event": "upsert", "data": "{}"}
    first = await anext(response.body_iterator)
    assert first["id"] == "3"
    assert '"subject": "new"' in first["data"]
    await response.body_iterator.aclose()


async def test_inbox_stream_replays_events_since_client_start(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    store = MonitorStore()
    store.upsert_item("db", "stuck_query", "old", 0.9, "Stuck", {}, 99)
    store.upsert_item("db", "stuck_query", "new", 0.9, "Stuck", {}, 100)
    class Request:
        headers = {}
        query_params = {"since": "100"}

        async def is_disconnected(self):
            return False
    response = await routes.inbox_stream(Request())
    assert (await anext(response.body_iterator))["id"] == "2"
    assert (await anext(response.body_iterator))["id"] == "3"
    await response.body_iterator.aclose()


def test_readings_name_a_setup_blocker_instead_of_a_delay(tmp_path):
    from features.monitor.api import routes as monitor_routes

    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    scheduler = SimpleNamespace(next_reading_at=lambda target: None,
                                collection_status=lambda target: {
                                    "delay_reason": "Waiting for the database to answer"})
    assert monitor_routes._readings(store, "db", scheduler).get("setup_blocked") is None
    store.upsert_item("db", "monitoring_issue", "pg_stat_statements_missing", 1.0,
                      "pg_stat_statements isn't available", {}, 1000)
    readings = monitor_routes._readings(store, "db", scheduler)
    assert readings["setup_blocked"] is True
    assert readings["delay_reason"].startswith("pg_stat_statements isn't set up")


def test_turning_monitoring_off_drops_its_queued_analyses(tmp_path, monkeypatch):
    from features.monitor import database_limit

    store = MonitorStore(tmp_path / "monitor.db")
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(database_limit, "release", lambda config: True)
    store.record("db", 1000, [{"query_key": "q1", "calls": 1, "total_ms": 5}],
                 {"q1": "SELECT 1"}, [], None)
    store.enqueue_analysis("db", "q1", "new", 5.0, 1000)
    routes._release_monitoring_slot("db")
    assert not store.read("SELECT 1 FROM analyze_job WHERE target='db' AND state='queued'")


def test_aurora_readers_cannot_turn_on_monitoring(tmp_path, monkeypatch):
    store = MonitorStore(tmp_path / "monitor.db")
    monkeypatch.setattr(routes, "_store", lambda: store)
    tags = {"reader": ["aurora", "reader", "role:reader"], "writer": ["aurora", "role:writer"]}
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get",
                        lambda self, name: {"tags": tags[name]})
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/monitor")
    client = TestClient(app)
    refused = client.put("/api/monitor/targets/reader/settings", json={"enabled": True})
    assert refused.status_code == 409
    assert "through its writer" in refused.json()["detail"]
    assert store.levers("reader")["enabled"] is False
    assert routes._monitor_unavailable("writer") is None

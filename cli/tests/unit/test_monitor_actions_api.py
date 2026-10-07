from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.monitor import actions
from features.monitor.api import models, routes
from features.monitor.index_plan import build_plan
from shared.monitor.monitor_store import MonitorStore

NOW = 1_000_000


def _client(tmp_path, monkeypatch):
    store = MonitorStore(tmp_path / "monitor.db")
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/monitor")
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_now", lambda: NOW)
    return store, TestClient(app)


def _seed(store):
    store.sync_indexes("db", [{"schema": "public", "table": "events", "index_name": "events_kind",
                               "definition": "CREATE INDEX events_kind ON public.events USING btree (kind)"}],
                       NOW - 600)
    existing = [{"schema": "public", "table": "events", "index_name": "events_kind",
                 "columns": ["kind"], "scans": 0, "window_scans": 0, "size_bytes": 4096}]
    plan = build_plan({"engine": "postgresql", "now": NOW, "observed_s": 3600,
                       "counter_age_s": 30 * 86400, "existing": existing,
                       "tables": {"events": {"writes_per_s": 300}},
                       "queries": [{"key": "q1", "name": "SELECT on orders", "share": 0.4,
                                    "kind": "select", "suggestions": [
                                        {"table": "orders", "columns": ["customer_id"],
                                         "gain_pct": 80, "verified": True}]}]})
    store.save_index_plan("db", plan)
    database = [{"id": "setting_work_mem", "title": "Raise work_mem so sorts stay in memory",
                 "severity": "bad", "finding": "work_mem is 4MB.", "sql": "ALTER SYSTEM ...",
                 "setting": {"name": "work_mem"}, "restart": False}]
    actions.reconcile(store, "db", NOW, actions.candidates(store, "db", plan, [], database))
    return plan


def test_actions_fold_index_changes_into_one_card(tmp_path, monkeypatch):
    store, client = _client(tmp_path, monkeypatch)
    _seed(store)
    page = client.get("/api/monitor/targets/db/actions").json()
    models.ActionsPage.model_validate(page)
    assert [card["type"] for card in page["actions"]] == ["index_plan", "change_setting"]
    plan_card = page["actions"][0]
    assert plan_card["title"] == "Update your indexes: add 1, drop 1"
    assert [child["type"] for child in plan_card["children"]] == ["add_index", "drop_index"]
    assert plan_card["sql"].count(";") == 2
    assert page["open_count"] == 3 and page["window"]["observed_s"] == 3600


def test_dismissing_an_action_takes_it_off_the_list(tmp_path, monkeypatch):
    store, client = _client(tmp_path, monkeypatch)
    _seed(store)
    setting = next(row for row in store.actions("db") if row["type"] == "change_setting")
    page = client.post(f"/api/monitor/targets/db/actions/{setting['id']}/dismiss",
                       json={"reason": "managed by ops"}).json()
    assert [card["type"] for card in page["actions"]] == ["index_plan"]
    assert page["dismissed"] == 1
    assert client.post("/api/monitor/targets/db/actions/999/dismiss").status_code == 404


def test_the_index_plan_is_served_as_computed(tmp_path, monkeypatch):
    store, client = _client(tmp_path, monkeypatch)
    assert client.get("/api/monitor/targets/db/index-plan").json() is None
    plan = _seed(store)
    served = client.get("/api/monitor/targets/db/index-plan").json()
    models.IndexPlan.model_validate(served)
    assert served["add"][0]["table"] == "orders" and served["sql"] == plan["sql"]


def test_the_reports_tab_shows_only_nightly_reports(tmp_path, monkeypatch):
    store, client = _client(tmp_path, monkeypatch)
    first, _ = store.claim_report("db", "user", 1_000)
    store.update_report(first["id"], report={"summary": "checkup"}, state="done", finished_at=1_001)
    assert client.get("/api/monitor/targets/db/report/latest").json()["id"] == first["id"]
    assert client.get("/api/monitor/targets/db/report/latest?daily=true").json() is None
    assert client.get("/api/monitor/targets/db/report/history?daily=true").json()["reports"] == []
    # A checkup someone ran feeds Actions too; the tab waits for the nightly report.
    asked, _ = store.claim_report("db", "user", 2_000)
    store.update_report(asked["id"], report={"summary": "today"}, state="done", finished_at=2_001)
    assert client.get("/api/monitor/targets/db/report/latest?daily=true").json() is None
    nightly, _ = store.claim_report("db", "nightly", 3_000)
    store.update_report(nightly["id"], report={"summary": "day"}, state="done", finished_at=3_001)
    assert client.get("/api/monitor/targets/db/report/latest?daily=true").json()["id"] == nightly["id"]

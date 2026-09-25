"""The monitor API in the UI contract shape (UI brief section 5)."""

from __future__ import annotations

import asyncio
import json
import selectors
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.monitor import presentation, report as reports
from features.monitor.analyze_queue import daily_cap, enqueue_new_queries
from features.monitor.api import routes, views
from features.query_registry.discovery import QueryDiscoveryCollector
from shared.monitor.monitor_store import MonitorStore
from shared.query_registry.delta_processor import COMPLETE, DeltaResult

NOW = 1_000_800  # hour aligned, so minute and hour buckets start on round offsets
KEY_A = "aaaaaaaaaaa1"
KEY_B = "bbbbbbbbbbb2"


@pytest.fixture
def store(monkeypatch, tmp_path):
    original_select = selectors.EpollSelector.select
    monkeypatch.setattr(selectors.EpollSelector, "select",
                        lambda self, timeout=None: original_select(
                            self, 0.01 if timeout is None else min(timeout, 0.01)))
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("RDST_MONITOR_ANALYZE_DAILY_CAP", raising=False)
    monkeypatch.delenv("RDST_MONITOR_JUDGE_INTERVAL_S", raising=False)
    monkeypatch.setattr(routes, "_now", lambda: NOW)
    monkeypatch.setattr(views, "registry_entries", lambda keys=None: {})
    monitor = MonitorStore(clock=lambda: NOW)
    monkeypatch.setattr(routes, "_store", lambda: monitor)
    return monitor


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/monitor")
    return TestClient(app)


def _db(calls=60, **extra):
    return {"calls": calls, "total_ms": calls * 2.0, "active_conns": 3, "max_conns": 100,
            "cache_hit_ratio": 0.99, "xact_commit": 10, "xact_rollback": 1, "deadlocks": 0,
            **extra}


def _minutes(store, start, count, key=KEY_A, calls=60, ms=2.0, text="SELECT * FROM orders"):
    for minute in range(count):
        ts = start + minute * 60
        store.record("db", ts, [{"query_key": key, "calls": calls, "total_ms": calls * ms}],
                     {key: text}, [], _db(calls))


# 5.1 Health

def test_health_serves_history_span_series_gaps_and_connection_metrics(client, store):
    _minutes(store, NOW - 1800, 10)
    _minutes(store, NOW - 600, 10)
    body = client.get("/api/monitor/targets/db/health?range=1h").json()
    assert body["range"] == "1h" and body["res"] == 60
    assert body["history"]["first_ts"] == NOW - 1800
    assert body["history"]["span_seconds"] == 1800
    # The chart starts at the first reading, not an hour ago, and gaps stay null.
    assert body["series"][0]["ts"] == NOW - 1800
    gap = [point for point in body["series"] if NOW - 1200 <= point["ts"] < NOW - 600]
    assert gap and all(point["qps"] is None and point["active_conns"] is None for point in gap)
    point = body["series"][-2]
    assert (point["qps"], point["mean_ms"], point["active_conns"], point["max_conns"],
            point["cache_hit_ratio"], point["xact_rollback"], point["deadlocks"]) == (
        1.0, 2.0, 3, 100, 0.99, 1, 0)
    assert body["now"]["rollbacks_per_min"] == 1 and body["now"]["qps"] == 1.0
    assert body["period"]["xact_rollback"] == 20 and body["period"]["active_conns_peak"] == 3
    # One hour of history cannot cover a prior hour.
    assert body["prior"] is None
    # The classic page still reads its tiles.
    assert "tiles" in body and body["window"] == "1h"


def test_health_prior_and_window_alias(client, store):
    _minutes(store, NOW - 7300, 122)
    body = client.get("/api/monitor/targets/db/health?window=1h").json()
    assert body["range"] == "1h" and body["prior"]["qps"] > 0
    assert client.get("/api/monitor/targets/db/health?range=2h").status_code == 422


def test_health_markers_cover_indexes_inbox_and_reports(client, store):
    _minutes(store, NOW - 600, 5)
    store.sync_indexes("db", [], NOW - 900)
    store.sync_indexes("db", [{"schema": "public", "table": "orders", "index_name": "idx_o",
                               "definition": "CREATE INDEX idx_o ON orders (total)"}], NOW - 300)
    store.upsert_item("db", "traffic_spike", KEY_A, 0.7, "Query traffic increased", {}, NOW - 200)
    run, _ = store.claim_report("db", "user", NOW - 100)
    store.update_report(run["id"], state="done", report={"summary": "ok"}, finished_at=NOW - 50)
    markers = client.get("/api/monitor/targets/db/health?range=1h").json()["markers"]
    assert [(m["kind"], m["label"]) for m in markers] == [
        ("index_added", "idx_o added"), ("inbox", "Query traffic increased filed"),
        ("report", "Report written")]


def test_history_span_uses_exact_first_reading_after_minute_rows_expire(store):
    now = 20 * 86400
    store.record("db", now - 8 * 86400 + 125, [{"query_key": KEY_A, "calls": 1, "total_ms": 1}],
                 {KEY_A: "SELECT 1 FROM t"}, [], None)
    store.rollup("db", now)
    span = views.history_span(store, "db", now)
    assert span["resolutions"]["60"]["first_ts"] is None
    # query_text.first_seen keeps the exact second of the first reading.
    assert span["first_ts"] == now - 8 * 86400 + 125


def test_all_range_picks_resolution_that_keeps_points_bounded(store):
    history = {"first_ts": NOW - 3 * 86400}
    assert views.range_bounds("all", NOW, history)[2] == 300
    assert views.range_bounds("7d", NOW, history) == (NOW - 7 * 86400, 7 * 86400, 300)


# 5.3 Queries

def test_queries_recover_text_and_exclude_system_statements(client, store):
    store.record("db", NOW - 120, [
        {"query_key": KEY_A, "calls": 10, "total_ms": 20},
        {"query_key": KEY_B, "calls": 5, "total_ms": 5},
        {"query_key": "ccccccccccc3", "calls": 3, "total_ms": 3},
        {"query_key": "ddddddddddd4", "calls": 3, "total_ms": 3},
    ], {KEY_A: "SELECT count(*) FROM orders WHERE status = $1"}, [], _db(21),
        samples=[{"query_key": KEY_B, "text": "SELECT id FROM customers WHERE id = 7"}])
    store.record("db", NOW - 60, [], {}, [], None, system_keys={"ddddddddddd4"})
    body = client.get("/api/monitor/targets/db/queries?range=1h").json()
    rows = {row["key"]: row for row in body["queries"]}
    assert set(rows) == {KEY_A, KEY_B, "ccccccccccc3"}
    assert (rows[KEY_A]["display_name"], rows[KEY_A]["text_status"]) == (
        "COUNT on orders by status", "ok")
    assert rows[KEY_B]["text"] == "SELECT id FROM customers WHERE id = 7"
    assert rows[KEY_B]["display_name"] == "SELECT on customers by id"
    missing = rows["ccccccccccc3"]
    assert missing["text_status"] == "unavailable" and missing["text_reason"]
    assert missing["display_name"] == "Query ccccccccccc3"
    assert body["counts"]["all"] == 3
    assert body["composition"][0] == {"key": KEY_A, "db_time_ms": 20.0, "share": 20 / 28}
    assert body["history"]["first_ts"] == NOW - 120


def test_query_change_is_gated_by_significance_and_prior_coverage(client, store):
    _minutes(store, NOW - 7200, 60, ms=1.0)
    _minutes(store, NOW - 3600, 60, ms=1.5)
    _minutes(store, NOW - 3600, 1, key=KEY_B, calls=2, ms=9.0, text="SELECT 1 FROM t")
    rows = {row["key"]: row for row in
            client.get("/api/monitor/targets/db/queries?range=1h").json()["queries"]}
    change = rows[KEY_A]["change"]
    assert change["verdict"] == "regressed" and change["significant"]
    assert change["latency"] == pytest.approx(0.5)
    assert "attention" in rows[KEY_A]["state"]
    assert rows[KEY_B]["change"]["verdict"] == "new" and "new" in rows[KEY_B]["state"]
    # MiniTrend points carry qps and latency for the whole range.
    assert rows[KEY_A]["series"][-2]["mean_ms"] == pytest.approx(1.5)
    no_prior = client.get("/api/monitor/targets/db/queries?range=24h").json()["queries"]
    assert all(row["change"]["verdict"] is None for row in no_prior)


def test_query_state_reports_muted_and_stuck_without_index_verdicts(client, store):
    _minutes(store, NOW - 300, 3)
    store.record("db", NOW - 60, [], {}, [{"pid": 1, "query_key": KEY_B, "state": "active",
                                          "duration_s": 90}], None)
    store.create_mute_rule(target="db", query_key=KEY_A, type=None, until=None, note=None,
                           now=NOW)
    store.save_fix_verdict({"target": "db", "query_key": KEY_A, "index_change_id": 1,
                            "index_name": "idx_o", "table": "orders", "status": "no_effect",
                            "detected_at": NOW - 7200, "before_mean_ms": 0.49,
                            "before_qps": 2.0, "after_mean_ms": 0.5, "after_qps": 2.0,
                            "after_calls": 400, "updated_at": NOW - 3600})
    body = client.get("/api/monitor/targets/db/queries?range=1h").json()
    rows = {row["key"]: row for row in body["queries"]}
    # A stored index verdict no longer marks the query: no index attribution anywhere.
    assert "muted" in rows[KEY_A]["state"] and "fix_no_effect" not in rows[KEY_A]["state"]
    assert rows[KEY_A]["fix"] is None
    assert "stuck" in rows[KEY_B]["state"]
    assert body["counts"]["muted"] == 1


def test_query_last_analysis_carries_a_human_reason(client, store):
    _minutes(store, NOW - 120, 1)
    store.skip_analysis_once("db", KEY_A, "new", NOW - 60,
                             "Read-only safety validation rejected the statement: "
                             "Dangerous keyword found: CREATE; query must begin with SELECT")
    row = client.get("/api/monitor/targets/db/queries?range=1h").json()["queries"][0]
    assert row["last_analysis"]["reason"] == (
        "RDST only analyzes read-only queries. This statement runs CREATE.")
    jobs = client.get(f"/api/monitor/targets/db/queries/{KEY_A}/analyze-jobs").json()["jobs"]
    assert jobs[0]["reason_text"] == row["last_analysis"]["reason"]


# 5.4 Series

def test_query_series_has_gaps_and_neutral_index_markers(client, store):
    _minutes(store, NOW - 1800, 5)
    _minutes(store, NOW - 600, 5)
    store.sync_indexes("db", [], NOW - 2000)
    store.sync_indexes("db", [{"schema": "public", "table": "customers", "index_name": "idx_c",
                               "definition": "CREATE INDEX idx_c ON customers (id)"},
                              {"schema": "public", "table": "orders", "index_name": "idx_o",
                               "definition": "CREATE INDEX idx_o ON orders (total)"}], NOW - 900)
    store.save_fix_verdict({"target": "db", "query_key": KEY_A, "index_change_id": 2,
                            "index_name": "idx_o", "table": "orders", "status": "confirmed",
                            "detected_at": NOW - 900, "before_mean_ms": 312.0,
                            "before_qps": 1.0, "after_mean_ms": 4.1, "after_qps": 1.0,
                            "after_calls": 300, "updated_at": NOW - 100})
    body = client.get(f"/api/monitor/targets/db/queries/{KEY_A}/series?range=1h").json()
    assert body["res"] == 60 and body["history"]["first_ts"] == NOW - 1800
    gap = [p for p in body["points"] if NOW - 1500 <= p["ts"] < NOW - 600]
    assert gap and all(p["calls"] is None and p["qps"] is None for p in gap)
    assert [(m["kind"], m.get("index_name"), m["label"]) for m in body["markers"]] == [
        ("index_added", "idx_o", "idx_o added")]
    assert body["fix"] is None


# 5.2 Pulse

def test_pulses_follow_collector_ticks_and_report_live_state(client, store):
    store.set_settings("db", {"enabled": True})
    _minutes(store, NOW - 120, 2)
    pulses = client.get("/api/monitor/targets/db/pulse?limit=2").json()["pulses"]
    assert [(p["kind"], p["detail"], p["ok"]) for p in pulses] == [
        ("collect", "Read 1 query", True), ("collect", "Read 1 query", True)]
    assert pulses[0]["state"] == "live" and pulses[0]["next_collect_at"] == NOW
    store.upsert_item("db", "monitoring_issue", "lost_connection", 1.0, "Lost", {}, NOW)
    assert client.get("/api/monitor/targets/db/pulse").json()["pulses"][0]["state"] == \
        "connection_lost"
    store.set_settings("db", {"enabled": False})
    assert client.get("/api/monitor/targets/db/pulse").json()["pulses"][0]["state"] == "paused"


def test_pulse_state_is_late_when_collection_stops(client, store):
    store.set_settings("db", {"enabled": True})
    _minutes(store, NOW - 600, 1)
    assert client.get("/api/monitor/targets/db/pulse").json()["pulses"][0]["state"] == "late"


async def test_stream_emits_pulse_after_each_collector_pass(monkeypatch, store):
    class Collector:
        async def subscribe(self):
            await asyncio.sleep(3600)
            yield None

    monkeypatch.setattr(routes.query_discovery, "collector_for", lambda target: Collector())
    monkeypatch.setattr(routes, "PULSE_POLL_SECONDS", 0.01)
    store.set_settings("db", {"enabled": True})
    async def connected():
        return False
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(monitor_scheduler=SimpleNamespace(
            next_reading_at=lambda target: NOW + 7.5))), is_disconnected=connected)
    response = await routes.stream("db", request)
    events = response.body_iterator
    pending = asyncio.ensure_future(anext(events))
    await asyncio.sleep(0.05)
    store.record_pulse("db", NOW, "index_scan", True, "No index changes")
    event = await asyncio.wait_for(pending, 2)
    assert event["event"] == "pulse"
    assert json.loads(event["data"])["detail"] == "No index changes"
    assert json.loads(event["data"])["next_reading_at"] == NOW + 7.5
    await events.aclose()


# 5.6 Mute rules and 5.7 inbox presentation

def test_mute_rules_round_trip_and_move_open_items(client, store):
    item = store.upsert_item("db", "traffic_spike", KEY_A, 0.7, "Traffic", {"query_key": KEY_A},
                             NOW - 60)
    assert client.post("/api/monitor/mute-rules", json={"target": "db"}).status_code == 422
    rule = client.post("/api/monitor/mute-rules",
                       json={"target": "db", "query_key": KEY_A, "note": "noisy"}).json()
    assert rule["matched_count"] == 1 and rule["note"] == "noisy"
    assert [r["id"] for r in client.get("/api/monitor/mute-rules?target=db").json()["rules"]] \
        == [rule["id"]]
    assert client.get("/api/monitor/mute-rules?target=other").json()["rules"] == []
    muted = client.get("/api/monitor/inbox?state=muted").json()["items"]
    assert [(i["id"], i["muted_by"]) for i in muted] == [(item["id"], rule["id"])]
    assert client.get("/api/monitor/inbox?state=open").json()["items"] == []
    assert len(client.get("/api/monitor/inbox?state=open&include_muted=1").json()["items"]) == 1
    assert client.delete(f"/api/monitor/mute-rules/{rule['id']}").json() == {"deleted": rule["id"]}
    assert client.delete(f"/api/monitor/mute-rules/{rule['id']}").status_code == 404
    assert client.get("/api/monitor/inbox?state=open").json()["items"][0]["muted_by"] is None


@pytest.mark.parametrize("scope", [
    {"target": "db", "query_key": KEY_A, "type": None},
    {"target": "db", "query_key": None, "type": "latency_regression"},
    {"target": None, "query_key": None, "type": "latency_regression"},
])
def test_muted_filings_are_kept_but_never_notify(store, scope):
    rule = store.create_mute_rule(**scope, until=None, note=None, now=NOW)
    item = store.upsert_item("db", "latency_regression", KEY_A, 0.9, "Slower",
                             {"query_key": KEY_A}, NOW)
    assert item["muted_by"] == rule["id"]
    kinds = [row["kind"] for row in store.read("SELECT kind FROM inbox_event")]
    assert kinds == ["created"]
    other = store.upsert_item("db", "traffic_spike", KEY_B, 0.9, "Traffic", {}, NOW)
    assert other["muted_by"] is None


def test_expired_mute_rule_no_longer_applies(store):
    store.create_mute_rule(target=None, query_key=None, type="stuck_query", until=NOW - 1,
                           note=None, now=NOW - 100)
    assert store.upsert_item("db", "stuck_query", KEY_A, 0.9, "Stuck", {}, NOW)["muted_by"] is None


def test_inbox_items_carry_presentation_fields(client, store):
    _minutes(store, NOW - 7200, 120, ms=1.0)
    body = {"query_key": KEY_A, "kind": "latency_regression",
            "baseline": {"1h": {"mean_ms": 0.08}},
            "reported_numbers": {"mean_ms": 0.58, "db_time_share": 0.25, "qps": 1.0}}
    store.upsert_item("db", "latency_regression", KEY_A, 0.7, "Query latency increased", body,
                      NOW - 600)
    store.upsert_item("db", "stuck_query", KEY_B, 0.9, "Stuck query", {"query_key": KEY_B}, NOW)
    spike = store.upsert_item("db", "traffic_spike", KEY_B, 0.7, "Query traffic increased",
                              {"query_key": KEY_B, "baseline": {"1h": {"qps": 1.0}},
                               "at_filing": {"qps": 4.0}}, NOW - 900)
    store.update_item_body(spike["id"], {
        "query_key": KEY_B, "baseline": {"1h": {"qps": 1.0}}, "at_filing": {"qps": 4.0},
        "resolution": {"before": {"qps": 4.0}, "after": {"qps": 1.1}}})
    store.action_item(spike["id"], "resolve", NOW - 60, resolved_by="code", measured_recovery=True)
    store.upsert_item("db", "query_improved", KEY_B, 0.4, "Query improved",
                      {"query_key": KEY_B}, NOW, delivery="digest")
    items = {i["type"]: i for i in client.get("/api/monitor/inbox?state=open").json()["items"]}
    # Digest-only findings never reach the inbox.
    assert "query_improved" not in items
    latency = items["latency_regression"]
    assert (latency["severity_band"], latency["tone"]) == ("watch", "warning")
    assert latency["summary"] == "Latency 0.08 ms -> 0.58 ms, 25% of database time."
    assert latency["link"] == {"kind": "query", "target": "db", "query_key": KEY_A,
                               "tab": "performance", "label": "Open query"}
    assert latency["advice"]["steps"][0] == "Refresh them: ANALYZE orders;"
    assert latency["query_keys"] == [KEY_A] and latency["subject_label"] == "SELECT on orders"
    assert latency["group_key"] == f"latency_regression:db:{(NOW - 600) // 21600}"
    # 24 five-minute points around filing, 16 before it; none past the latest reading.
    trend = latency["trend"]
    assert trend[0]["ts"] == (NOW - 600) // 300 * 300 - 16 * 300
    assert len(trend) == 18 and {point["value"] for point in trend} == {1.0}
    assert (items["stuck_query"]["severity_band"], items["stuck_query"]["tone"]) == (
        "act_now", "negative")
    assert items["stuck_query"]["advice"]["steps"][1] == "Cancel it: SELECT pg_cancel_backend(<pid>);"
    # A measured recovery stays in Open, green, until someone sees it.
    recovered = items["traffic_spike"]
    assert (recovered["state"], recovered["severity_band"], recovered["tone"]) == (
        "resolved", "recovered", "positive")
    assert recovered["summary"] == "Calls back to normal: 1.1 calls a second."
    assert recovered["advice"] is None
    client.post(f"/api/monitor/inbox/{spike['id']}/seen")
    assert "traffic_spike" not in {
        i["type"] for i in client.get("/api/monitor/inbox?state=open").json()["items"]}
    assert store.inbox("resolved")[0]["id"] == spike["id"]
    # Tabs are newest first; the classic raw-state filter still works.
    assert [i["type"] for i in client.get("/api/monitor/inbox?state=open").json()["items"]][-1] \
        == "latency_regression"
    assert len(client.get("/api/monitor/inbox?state=new").json()["items"]) == 2
    assert len(client.get("/api/monitor/inbox?state=new&include_digest=1").json()["items"]) == 3


def test_inbox_action_returns_presented_item(client, store):
    item = store.upsert_item("db", "stuck_query", KEY_B, 0.9, "Stuck query", {}, NOW)
    acted = client.post(f"/api/monitor/inbox/{item['id']}/resolve").json()
    assert acted["state"] == "resolved" and acted["severity_band"] == "recovered"
    assert client.get("/api/monitor/inbox?state=done").json()["items"][0]["id"] == item["id"]


# 5.8 Report progress, 5.9 presentation

def _events(client, run_id, last=None):
    headers = {"Last-Event-ID": str(last)} if last is not None else {}
    with client.stream("GET", f"/api/monitor/targets/db/report/{run_id}/events",
                       headers=headers) as response:
        frames = []
        for line in response.iter_lines():
            if line.startswith("id:"):
                frames.append({"id": int(line[3:].strip())})
            elif line.startswith("data:"):
                frames[-1]["data"] = json.loads(line[5:].strip())
            elif line.startswith("event:"):
                raise AssertionError("progress frames are unnamed")
        return frames


async def test_report_run_streams_progress_and_replays(client, store, monkeypatch):
    _minutes(store, NOW - 300, 2)
    store.skip_analysis_once("db", KEY_A, "report", NOW - 10, "PG_SLEEP() reaches outside the "
                             "queried tables")
    store.set_settings("db", {"enabled": True})
    monkeypatch.setattr(reports, "USER_WAIT_SECONDS", 0)
    result = await reports.run_report(
        "db", "user", store=store, clock=lambda: NOW,
        explain=lambda target, sql: {"plan": ["Seq Scan"]},
        collect=lambda target: ({"section_errors": {"replication": "no access"}}, {}),
        narrate=lambda context: {"summary": "Load is steady. {{q:" + KEY_A + "}} leads.",
                                 "fix_first": [{"query_key": KEY_A, "why": "w",
                                                "action": "Add an index on orders(total). Then"
                                                          " check again"}],
                                 "one_fix_several_queries": [],
                                 "health": [{"check": "cache_hit_ratio", "status": "healthy",
                                             "note": "fine"}],
                                 "changed_since_yesterday": []})
    frames = _events(client, result["id"])
    assert [f["id"] for f in frames] == list(range(len(frames)))
    events = [f["data"] for f in frames]
    assert events[0] == {"type": "run", "state": "running", "started_at": NOW, "elapsed_s": 0}
    assert events[-1]["type"] == "run" and events[-1]["state"] == "done"
    assert {"type": "selected", "queries": [
        {"key": KEY_A, "display_name": "SELECT on orders", "db_time_share": 1.0}]} in events
    skipped = [e for e in events if e["type"] == "analysis" and e["state"] == "skipped"]
    assert skipped[0]["reason"] == "This query calls pg_sleep, which RDST won't run."
    checks = {e["check"]: e["state"] for e in events if e["type"] == "health_check"}
    assert checks["replication"] == "failed" and checks["vacuum_bloat"] == "done"
    assert [e["state"] for e in events if e["type"] == "section"].count("ready") == 5
    replay = _events(client, result["id"], last=len(frames) - 3)
    assert [f["id"] for f in replay] == [len(frames) - 2, len(frames) - 1]
    presented = client.get(f"/api/monitor/targets/db/report/{result['id']}").json()
    assert "computed" not in presented["report_json"]
    assert presented["report_json"]["fix_first"][0]["title"] == "Add an index on orders"
    assert presented["report_json"]["health"][0] == {
        "check": "cache_hit_ratio", "label": "Cache hit ratio", "value": "99.00%",
        "status": "ok", "note": "fine", "action": None, "matters": False}
    assert presented["refs"][KEY_A] == {"display_name": "SELECT on orders",
                                        "text_snippet": "SELECT * FROM orders"}
    assert client.get("/api/monitor/targets/db/report/latest").json()["id"] == result["id"]


async def test_cancel_stops_a_running_report(client, store, monkeypatch):
    _minutes(store, NOW - 300, 2)
    store.set_settings("db", {"enabled": True})
    blocked = asyncio.Event()

    async def wait(seconds):
        blocked.set()
        await asyncio.sleep(3600)

    task = asyncio.create_task(reports.run_report(
        "db", "user", store=store, clock=lambda: NOW, sleep=wait))
    await asyncio.wait_for(blocked.wait(), 5)
    run_id = store.read("SELECT id FROM report_run")[0]["id"]
    cancelled = await asyncio.to_thread(
        lambda: client.post(f"/api/monitor/targets/db/report/{run_id}/cancel").json())
    assert cancelled["state"] == "cancelled"
    result = await asyncio.wait_for(task, 5)
    assert result["state"] == "cancelled"
    events = [e["event"] for e in store.report_events(run_id)]
    assert events[-1]["type"] == "run" and events[-1]["state"] == "cancelled"
    # A cancelled run frees the target for the next one.
    assert store.claim_report("db", "user", NOW)[1]


def test_report_without_events_replays_its_outcome(client, store):
    run, _ = store.claim_report("db", "user", NOW - 100)
    store.update_report(run["id"], state="done", report={"summary": "old"}, finished_at=NOW - 40)
    store._write(lambda conn: conn.execute("DELETE FROM report_event"))
    assert [f["data"] for f in _events(client, run["id"])] == [
        {"type": "run", "state": "done", "started_at": NOW - 100, "elapsed_s": 60}]
    assert client.get("/api/monitor/targets/other/report/1/events").status_code == 404


# 5.10 Settings and usage

def test_settings_levers_default_for_a_free_tier_and_validate(client, store):
    settings = client.get("/api/monitor/targets/db/settings").json()
    assert {key: settings[key] for key in (
        "deep_analysis", "auto_analyze_new", "analysis_daily_cap", "attention_every_min",
        "nightly_report", "nightly_hour")} == {
        "deep_analysis": False, "auto_analyze_new": True, "analysis_daily_cap": 10,
        "attention_every_min": 5, "nightly_report": True, "nightly_hour": 0}
    updated = client.put("/api/monitor/targets/db/settings", json={
        "analysis_daily_cap": 3, "attention_every_min": 60, "nightly_report": False,
        "nightly_hour": 5}).json()
    assert (updated["analysis_daily_cap"], updated["attention_every_min"],
            updated["nightly_report"], updated["nightly_hour"]) == (3, 60, False, 5)
    assert daily_cap("db", store) == 3
    assert client.put("/api/monitor/targets/db/settings",
                      json={"attention_every_min": 7}).status_code == 422
    assert client.put("/api/monitor/targets/db/settings",
                      json={"nightly_hour": 24}).status_code == 422


def test_auto_analyze_new_lever_stops_automatic_jobs(store):
    _minutes(store, NOW - 120, 1)
    store.set_settings("db", {"deep_analysis": True, "auto_analyze_new": False})
    assert enqueue_new_queries("db", store=store, now=NOW) == []
    store.set_settings("db", {"auto_analyze_new": True})
    assert len(enqueue_new_queries("db", store=store, now=NOW)) == 1


def test_usage_counts_billable_work_per_day(client, store):
    store.record_judge_run("db", NOW - 10, [], request={"q": 1}, outcome="filed",
                           billable_calls=1)
    store.record_judge_run("db", NOW - 10, [], outcome="unchanged")
    job = store.enqueue_analysis("db", KEY_A, "user", 1.0, NOW - 5)
    store.claim_analysis("db", NOW - 5)
    store.finish_analysis(job["id"], "done", NOW - 1, billable_calls=3)
    run, _ = store.claim_report("db", "user", NOW - 2)
    store.update_report(run["id"], state="done", billable_calls=1)
    body = client.get("/api/monitor/targets/db/usage?days=2").json()
    assert len(body["days"]) == 2
    assert {k: body["days"][-1][k] for k in ("jev_calls", "analyses", "reports",
                                             "billable_calls")} == {
        "jev_calls": 1, "analyses": 1, "reports": 1, "billable_calls": 5}
    assert body["caps"] == {"jev_daily": 300, "analysis_daily": 10, "push_daily": 3}
    assert client.get("/api/monitor/targets/db/usage?days=0").status_code == 422


# 5.11 Reasons

@pytest.mark.parametrize("error,expected", [
    ("LOGIN_REQUIRED: Sign in to run Deep Analyze", "Sign in to run deep analysis."),
    ("Workflow failed: Refusing to analyze this query: Dangerous keyword found: INSERT; query",
     "RDST only analyzes read-only queries. This statement runs INSERT."),
    ('PostgreSQL EXPLAIN failed: relation "x.orders" does not exist',
     "A table this query reads no longer exists."),
    ("Only SELECT statements are eligible for automatic Deep Analyze",
     "RDST only analyzes SELECT statements on its own. Run it from the query to analyze it."),
])
def test_analysis_reasons_are_sentences(error, expected):
    assert presentation.analysis_reason("failed", error) == expected


def test_queued_job_past_todays_allowance_says_so(client, store):
    store.set_settings("db", {"enabled": True, "deep_analysis": True, "newcomer_daily_cap": 1})
    for key in (KEY_A, KEY_B):
        _minutes(store, NOW - 60, 1, key=key)
        store.enqueue_analysis("db", key, "new", 1.0, NOW, cap=5)
    store.claim_analysis("db", NOW, cap=5, newcomer_cap=1)
    jobs = client.get("/api/monitor/targets/db/analyze-jobs?state=queued").json()["jobs"]
    assert jobs[0]["reason_text"] == ("Today's automatic analyses are done. RDST picks this "
                                      "query up tomorrow if it's still among the busiest, or "
                                      "you can analyze it now.")
    assert jobs[0]["queue_state"] == "held"


# Collector: RDST's own statements never become monitored queries

def test_collector_recognizes_system_statements_after_restart(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    collector = QueryDiscoveryCollector("db", two_phase=True)
    collector._monitor = store
    collector._engine = "postgresql"
    collector._alias_map = {"e1": KEY_A, "e2": KEY_B}
    collector._baseline = {}
    store.record("db", 60, [{"query_key": KEY_B, "calls": 4, "total_ms": 1}], {}, [])
    deltas = [DeltaResult("e1", 60, 120, COMPLETE, 5, 10.0, 5 / 60, 5),
              DeltaResult("e2", 60, 120, COMPLETE, 7, 1.0, 7 / 60, 7)]
    collector._record_monitor_cycle(
        120, deltas, None, [],
        observed_texts={"e1": "SELECT * FROM orders WHERE id = $1", "e2": "BEGIN"})
    assert store.read("SELECT query_key FROM stmt_series") == []
    assert store.system_queries("db") == {KEY_B}
    assert KEY_B in collector._system_identities
    collector._record_monitor_cycle(180, deltas, None, [])
    assert {row["query_key"] for row in store.read("SELECT query_key FROM stmt_series")} == {KEY_A}


def test_compare_outcome_admits_a_monitor_only_query(monkeypatch, tmp_path, store):
    from features.query_registry.api import routes as registry_routes
    from shared.query_registry import QueryRegistry, hash_sql

    sql = "SELECT id FROM orders WHERE total > $1"
    key = hash_sql(sql)
    store.record("db", NOW - 60, [{"query_key": key, "calls": 1, "total_ms": 1}], {key: sql}, [])
    registry = QueryRegistry()
    entry = registry_routes._admit_monitored_query(registry, key, "db")
    assert entry is not None and entry.hash == key
    assert registry_routes._admit_monitored_query(QueryRegistry(), "0123456789ab", "db") is None


def test_display_names_use_one_scheme_for_every_statement():
    names = {
        "select count(*) from public.orders": "COUNT on orders",
        "SELECT id, total FROM orders WHERE total >= $1 ORDER BY total LIMIT $2":
            "SELECT on orders by total",
        "SELECT count(*) FROM orders WHERE status = $1": "COUNT on orders by status",
        "SELECT c.country, sum(o.total) FROM customers c JOIN orders o ON o.customer_id = c.id "
        "WHERE o.total > $1 GROUP BY 1": "SUM on customers and orders by total",
        "WITH t AS (SELECT * FROM orders) SELECT count(*) FROM t": "COUNT on orders",
        "UPDATE orders SET x=1": "UPDATE on orders",
        'DROP INDEX IF EXISTS "s".idx_orders_total': "DROP INDEX idx_orders_total",
        "CREATE INDEX idx_x ON orders (total)": "CREATE INDEX on orders",
        'ANALYZE "s".orders': "ANALYZE orders",
        "BEGIN": "BEGIN",
    }
    for sql, name in names.items():
        assert presentation.display_name(sql) == name, sql
    # Library tags don't rename monitored queries: every page reads the same way.
    assert presentation.display_name("SELECT 1 FROM orders", "tagged") == "SELECT on orders"
    assert presentation.display_name("") == "Untitled query"
    assert presentation.fix_title("Identify the client issuing pg_sleep(180) and stop it") == \
        "Identify the client issuing pg_sleep"


def test_attention_lever_holds_shortlists_between_ai_checks(monkeypatch, store):
    from features.monitor import loop as monitor_loop

    class Config:
        def load(self):
            pass

        def get(self, target):
            return {}

    monkeypatch.setattr("shared.config.targets.TargetsConfig", Config)
    monkeypatch.setattr(monitor_loop.query_discovery, "collector_for",
                        lambda target: SimpleNamespace(_snapshot={}))
    calls = []
    clock = SimpleNamespace(now=NOW)
    loop = monitor_loop.MonitorLoop(
        store, clock=lambda: clock.now, index_reader=lambda target: [], interval=300,
        judge=lambda chosen, context: calls.append([row["query_key"] for row in chosen]))
    store.set_settings("db", {"attention_every_min": 15})
    loop.cycle("db")
    clock.now += 300
    loop.cycle("db")
    assert len(calls) == 1
    pulses = store.pulses("db", 5)
    assert pulses[0]["kind"] == "attention" and "next AI check at" in pulses[0]["detail"]
    assert pulses[1] == {**pulses[1], "kind": "index_scan", "detail": "No index changes"}
    clock.now += 600
    loop.cycle("db")
    assert len(calls) == 2
    assert store.pulses("db", 1)[0]["detail"] == "Checked 0 queries, nothing new"


def test_fix_title_fallback_never_ends_on_a_connective():
    assert presentation.fix_title(
        "Identify the process dropping and re-adding idx_orders_total and stop it") == \
        "Identify the process dropping and re-adding idx_orders_total"
    assert presentation.health_value("cache_hit_ratio", {"deltas": {"24h": {"database": {
        "cache_hit_ratio": {"current": 0.99999}}}}}) == "99.99%"


def test_library_entry_endpoint_admits_monitored_queries(client, store):
    from shared.query_registry import QueryRegistry, hash_sql

    sql = "SELECT id FROM orders WHERE customer_id = $1"
    key = hash_sql(sql)
    store.record("db", NOW - 60, [{"query_key": key, "calls": 1, "total_ms": 1}], {key: sql}, [])
    assert client.post(f"/api/monitor/targets/db/queries/{key}/library").json() == {
        "hash": key, "created": True}
    registry = QueryRegistry()
    registry.load()
    assert registry.get_query(key) is not None
    assert client.post(f"/api/monitor/targets/db/queries/{key}/library").json()["created"] is False
    assert client.post("/api/monitor/targets/db/queries/0123456789ab/library").status_code == 404


def test_events_of_a_run_stopped_by_a_restart_end_with_its_state(client, store):
    run, _ = store.claim_report("db", "user", NOW - 100)
    store.append_report_event(run["id"], {"type": "run", "state": "running",
                                          "started_at": NOW - 100, "elapsed_s": 0}, NOW - 100)
    store.recover_reports(NOW + 10_000)
    frames = _events(client, run["id"])
    assert [f["id"] for f in frames] == [0, 1]
    assert frames[-1]["data"]["state"] == "failed"


def test_inbox_request_skips_the_integrity_check_after_first_open(monkeypatch, store):
    """Routes build a store per request; only the first open of a file scans every page."""
    statements = []
    connect = MonitorStore._connect

    def traced(self):
        conn = connect(self)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(MonitorStore, "_connect", traced)
    monkeypatch.setattr(routes, "_store", lambda: MonitorStore(store.path, clock=lambda: NOW))
    for index in range(3):
        store.upsert_item("db", "latency_regression", f"{index:012x}", 0.6, "Slower",
                          {"query_key": f"{index:012x}"}, NOW - 60)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/monitor")
    statements.clear()
    for _ in range(3):
        assert len(TestClient(app).get("/api/monitor/inbox?target=db&state=open")
                   .json()["items"]) == 3
    assert not [sql for sql in statements if "quick_check" in sql]
    connects = [sql for sql in statements if sql.startswith("PRAGMA busy_timeout")]
    assert len(connects) <= 3 * 12


def test_inbox_items_carry_when_they_last_pushed(client, store):
    pushed = store.upsert_item("db", "latency_regression", KEY_A, 0.8, "Slower",
                               {"query_key": KEY_A}, NOW - 300, delivery="notify")
    store.upsert_item("db", "latency_regression", KEY_A, 0.9, "Slower",
                      {"query_key": KEY_A}, NOW - 60, delivery="notify",
                      reopen_resolved=True, materially_worse=True)
    skipped = store.upsert_item("db", "traffic_spike", KEY_B, 0.8, "Traffic",
                                {"query_key": KEY_B}, NOW - 30, delivery="notify",
                                notify_cap=0)
    listed = store.upsert_item("db", "new_heavy_query", "cccccccccc3c", 0.4, "New",
                               {"query_key": "cccccccccc3c"}, NOW - 20, delivery="inbox")
    items = {item["id"]: item for item in client.get("/api/monitor/inbox?target=db").json()[
        "items"]}
    notified = store.read("SELECT MAX(ts) AS ts FROM inbox_event WHERE item_id=? "
                          "AND kind='notified'", (pushed["id"],))[0]["ts"]
    assert items[pushed["id"]]["notified_at"] == notified
    assert items[skipped["id"]]["delivery"] == "notify"
    assert items[skipped["id"]]["notified_at"] is None
    assert items[listed["id"]]["notified_at"] is None


def test_usage_counts_analyses_in_the_queue_local_day(monkeypatch, client, store):
    monkeypatch.setenv("TZ", "America/Denver")
    time.tzset()
    try:
        # 19:00 local on a day whose UTC date has already rolled over.
        now = int(datetime(2026, 9, 28, 19, 0).timestamp())
        monkeypatch.setattr(routes, "_now", lambda: now)
        for started in (now - 2 * 3600, now - 22 * 3600):  # 17:00 today, 21:00 yesterday
            job = store.enqueue_analysis("db", KEY_A, "new", 1.0, started)
            store.claim_analysis("db", started)
            store.finish_analysis(job["id"], "done", started + 60)
        days = client.get("/api/monitor/targets/db/usage?days=2").json()["days"]
        assert [day["date"] for day in days] == ["2026-09-27", "2026-09-28"]
        assert [day["analyses"] for day in days] == [1, 1]
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_a_report_just_finished_for_yesterday_is_no_answer_for_today(store):
    from shared.monitor.monitor_store import local_day_bounds

    today = local_day_bounds(NOW)[0]
    yesterday = local_day_bounds(today - 1)[0]
    nightly, _ = store.claim_report("db", "nightly", today + 150, yesterday)
    store.update_report(nightly["id"], state="done", period=(yesterday, today),
                        finished_at=today + 250)
    run, claimed = store.claim_report("db", "user", today + 300, today)
    assert claimed and run["id"] != nightly["id"]
    store.update_report(run["id"], state="done", period=(today, today + 300),
                        finished_at=today + 320)
    # A second request for the same day within the reuse window gets that run.
    again, claimed = store.claim_report("db", "user", today + 400, today)
    assert not claimed and again["id"] == run["id"]


def test_usage_counts_every_analysis_that_ran(client, store):
    # First-hour analyses and clicks spend no daily allowance, but Usage still counts them.
    _minutes(store, NOW - 60, 1)
    store.set_settings("db", {"enabled": True})
    store.start_first_hour("db", NOW - 60)
    for key, reason in ((KEY_A, "new"), (KEY_B, "user")):
        job = store.enqueue_analysis("db", key, reason, 1, NOW - 30)
        store.claim_analysis("db", NOW - 30)
        store.finish_analysis(job["id"], "done", NOW - 20, billable_calls=1)
    queue = client.get("/api/monitor/targets/db/analyze-jobs").json()["queue"]
    usage = client.get("/api/monitor/targets/db/usage?days=1").json()["days"][0]
    assert usage["analyses"] == 2 and queue["used"] == 0
    assert usage["billable_calls"] == 2


def test_signed_out_queue_preserves_job_reason(client, store, monkeypatch):
    from features.monitor import database_limit

    monkeypatch.setattr(database_limit, "_signed_in", lambda: False)
    _minutes(store, NOW - 60, 1)
    store.set_settings("db", {"enabled": True})
    store.enqueue_analysis("db", KEY_A, "new", 1, NOW)
    job = client.get("/api/monitor/targets/db/analyze-jobs").json()["queue"]["next"][0]
    assert job["reason"] == "new" and job["queue_state"] == "held"
    assert job["reason_text"] == "Sign in to turn on AI monitoring"


@pytest.mark.parametrize("own_key", [False, True])
def test_signed_out_manual_admission(client, store, monkeypatch, own_key):
    from features.monitor import database_limit

    monkeypatch.setattr(database_limit, "_signed_in", lambda: False)
    monkeypatch.setattr(database_limit, "own_key", lambda: own_key)
    _minutes(store, NOW - 60, 1)
    client.app.state.analyze_queue = type("Queue", (), {"wake": lambda self: None})()
    response = client.post(f"/api/monitor/targets/db/queries/{KEY_A}/analyze")
    assert response.status_code == (200 if own_key else 401)
    if own_key:
        assert response.json()["queue_state"] == "today"
    else:
        assert store.analyze_jobs("db") == []

"""W0b: quiet hours and the daily report switch, p95 latency, the seen-so-far count and
the benchmark-with-fixes estimate."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from features.monitor import delivery, snapshot
from features.monitor.api import views
from features.monitor.tests.conftest import KEYS, NOW, seed
from shared.monitor.monitor_store import in_quiet_hours, notify_event

NIGHT = int(datetime(2026, 10, 8, 2, 30).timestamp())
MORNING = int(datetime(2026, 10, 8, 9, 30).timestamp())


def _events(store, item_id: int) -> list[dict]:
    """Each event's kind, with a skipped push's reason."""
    found = []
    for event in store.inbox_events():
        if event["item_id"] != item_id:
            continue
        detail = json.loads(event["detail_json"] or "{}")
        skipped = detail.get("push_skipped") if isinstance(detail, dict) else None
        found.append({"kind": event["kind"], **({"push_skipped": skipped} if skipped else {})})
    return found


def test_quiet_hours_wrap_midnight_and_equal_hours_mean_off():
    hours = {"from_hour": 23, "to_hour": 7}
    assert in_quiet_hours(hours, NIGHT) and not in_quiet_hours(hours, MORNING)
    assert in_quiet_hours(hours, int(datetime(2026, 10, 7, 23, 5).timestamp()))
    assert not in_quiet_hours(hours, int(datetime(2026, 10, 8, 7, 0).timestamp()))
    assert in_quiet_hours({"from_hour": 9, "to_hour": 17}, MORNING)
    assert not in_quiet_hours({"from_hour": 0, "to_hour": 0}, NIGHT)
    assert not in_quiet_hours(None, NIGHT)
    assert notify_event("daily_summary", "2026-10-07") == "daily_report"
    assert notify_event("report_ready", "run:3") == "daily_report"


def test_pushes_are_held_in_quiet_hours_while_the_inbox_fills(store):
    store.set_settings("db", {"enabled": True})
    assert delivery.push_held_by_quiet_hours(store, "db", NIGHT)
    assert not delivery.push_held_by_quiet_hours(store, "db", MORNING)
    held = store.upsert_item("db", "stuck_query", KEYS[0], .9, "Stuck query",
                             {"query_key": KEYS[0]}, NIGHT, delivery="notify")
    assert held["state"] == "new" and held["delivery"] == "notify"
    assert _events(store, held["id"]) == [{"kind": "created"},
                                          {"kind": "updated", "push_skipped": "quiet_hours"}]
    # An outage is never held.
    outage = store.upsert_item("db", "monitoring_issue", "lost_connection", 1.0, "Lost",
                               {"target": "db"}, NIGHT, delivery="notify")
    assert [event["kind"] for event in _events(store, outage["id"])] == ["created", "notified"]
    # Off: equal hours.
    store.set_settings("db", {"quiet_hours": {"from_hour": 0, "to_hour": 0}})
    assert not delivery.push_held_by_quiet_hours(store, "db", NIGHT)
    pushed = store.upsert_item("db", "stuck_query", KEYS[1], .9, "Stuck query",
                               {"query_key": KEYS[1]}, NIGHT, delivery="notify")
    assert [event["kind"] for event in _events(store, pushed["id"])] == ["created", "notified"]


def test_settings_carry_the_daily_report_switch_and_quiet_hours(client, store):
    page = client.get("/api/monitor/targets/db/settings").json()
    assert page["notify_events"]["daily_report"] is True
    assert page["quiet_hours"] == {"from_hour": 23, "to_hour": 7}
    page = client.put("/api/monitor/targets/db/settings", json={
        "notify_events": {"daily_report": False},
        "quiet_hours": {"from_hour": 22, "to_hour": 6}}).json()
    assert page["notify_events"]["daily_report"] is False
    assert page["notify_events"]["stuck_query"] is True
    assert page["quiet_hours"] == {"from_hour": 22, "to_hour": 6}
    assert client.put("/api/monitor/targets/db/settings",
                      json={"quiet_hours": {"from_hour": 25, "to_hour": 6}}).status_code == 422
    assert client.put("/api/monitor/targets/db/settings",
                      json={"quiet_hours": {"from_hour": 1}}).status_code == 422
    # The daily report item honours its own switch.
    store.set_settings("db", {"enabled": True})
    item = store.upsert_item("db", "daily_summary", "2026-10-07", .4, "Daily report", {},
                             MORNING, delivery="notify")
    assert _events(store, item["id"]) == [{"kind": "created"},
                                          {"kind": "updated", "push_skipped": "event_off"}]


def test_p95_is_the_tail_of_the_readings_and_reaches_health_snapshot_and_report(client, store):
    assert views.p95_of_buckets([]) is None
    rows = [{"calls": 10, "total_ms": 10.0 * mean} for mean in range(1, 21)]
    assert views.p95_of_buckets(rows) == 19.0
    seed(store, minutes=30, means={KEYS[0]: 50.0, KEYS[1]: 40.0, KEYS[2]: 30.0, KEYS[3]: 20.0})
    health = client.get("/api/monitor/targets/db/health?range=1h").json()
    assert health["period"]["p95_ms"] == pytest.approx(health["period"]["mean_ms"], rel=1e-6)
    numbers = {row["metric"]: row for row in snapshot.day_numbers(
        {"p95_ms": 69.7, "mean_ms": 15.9}, {"p95_ms": 60.0, "mean_ms": 15.9})}
    assert numbers["p95_ms"]["label"] == "p95 latency" and numbers["p95_ms"]["unit"] == "ms"
    assert numbers["p95_ms"]["better"] == "down"


def test_the_list_counts_every_query_seen_so_far(client, store):
    seed(store, minutes=3)
    page = client.get("/api/monitor/targets/db/queries?range=1h&limit=2").json()
    # The seed names five queries; the page lists two of them.
    assert page["seen_total"] == 5 and len(page["queries"]) == 2 and page["total"] == 5


def test_benchmark_fixes_estimates_after_from_the_planner(client, store, monkeypatch):
    seed(store)
    gain = {"measured": False, "text": "Planner estimate: cost down 95% with a hypothetical "
                                       "index", "before_ms": None, "after_ms": None,
            "cost_reduction_pct": 95.0}
    digest = {"query_key": KEYS[0], "analysis_ref": "/a/1", "analyzed_at": NOW, "mode": "explain",
              "mode_label": "Plan only (EXPLAIN)", "rating": "fair", "efficiency_score": 42,
              "plan_summary": None, "findings": [], "headline": "Needs an index",
              "fix_ready": True, "other_fixes": [],
              "fix": {"kind": "index", "title": "Add an index on posts(owneruserid)",
                      "sql": "CREATE INDEX i ON posts (owneruserid);", "why": "w",
                      "expected_gain": gain}}
    monkeypatch.setattr("features.monitor.analysis_digest.digest_for_ref",
                        lambda target, key, ref, mode, finished_at=None:
                        digest if key == KEYS[0] else None)
    monkeypatch.setattr(views, "digest_for_ref",
                        lambda target, key, ref, mode, finished_at=None:
                        digest if key == KEYS[0] else None)
    store._write(lambda conn: conn.execute(
        "INSERT INTO analyze_job (target,query_key,reason,state,priority,enqueued_at,finished_at,"
        "analysis_ref) VALUES ('db',?,'new','done',1.0,?,?,'/a/1')", (KEYS[0], NOW - 70, NOW - 60)))
    store.sync_indexes("db", [], NOW - 3600)
    result = client.post("/api/monitor/targets/db/benchmark-fixes",
                         json={"query_keys": [KEYS[0], KEYS[1]], "runs": 20}).json()
    assert result["scratch_schema"] is False and result["runs"] == 20
    assert result["method"].startswith("Estimated on db: index fixes tested as hypothetical")
    first, second = result["rows"]
    assert first["before_ms"] == pytest.approx(50.0) and first["after_ms"] == pytest.approx(2.5)
    assert first["change_pct"] == -95 and first["estimated"] is True
    assert second["before_ms"] == pytest.approx(40.0) and second["after_ms"] is None
    assert second["change_pct"] is None
    assert first["reason"] is None and second["reason"] == "No fix is ready for this query."
    assert 0 < result["share"] < 1
    # The run is listed on the activity log as a benchmark of this database.
    from shared.agent_activity.store import AgentActivityStore

    [entry] = AgentActivityStore().page(action="benchmark", group="none")["items"]
    assert (entry["target"], entry["outcome"]) == ("db", "done")
    assert entry["summary"] == "1 of 1 faster with their fixes, estimated"
    assert client.post("/api/monitor/targets/db/benchmark-fixes",
                       json={"query_keys": []}).status_code == 422
    assert client.post("/api/monitor/targets/db/benchmark-fixes",
                       json={"query_keys": [KEYS[0]], "runs": 0}).status_code == 422

"""Nightly health inbox items, combined delivery, and full-scan comparisons."""

import json
from unittest.mock import Mock

import pytest

from features.audit.health import collect_health_report
from features.audit.health.sequential_scans import collect_sequential_scans, scan_deltas
from features.monitor import judge, report
from features.monitor.api.models import InboxItem
from features.monitor.db_actions import database_actions
from features.monitor.presentation import present_inbox_item
from features.monitor.snapshot import health_section
from shared.monitor.monitor_store import MonitorStore

NOW = 2_000_000
DAY = 86400


@pytest.fixture
def store(tmp_path):
    return MonitorStore(tmp_path / "monitor.db")


def action(id="setting_shared_buffers", severity="bad", check="settings"):
    return {"id": id, "severity": severity, "check": check, "title": f"Fix {id}",
            "finding": f"{id} needs attention.", "evidence": [{"label": "Size", "value": "128 MB"}],
            "why": "Queries read more from disk.", "steps": "Give the cache more memory.",
            "sql": "SELECT 1"}


def file_report(store, actions=(), ts=NOW, collectors=None, trigger="nightly"):
    row, created = store.claim_report("db", trigger, ts, period_start=ts - DAY)
    assert created
    collectors = {"engine": "postgresql", "config_audit": {}, "replication": {}} if collectors is None else collectors
    store.update_report(row["id"], report={"computed": {"health_collectors": collectors},
                        "snapshot": {"actions": list(actions)}}, state="done", finished_at=ts)
    report._file_report_items(store, "db", row["id"], ts, {"summary": "The report's ready."}, trigger)
    return row["id"]


def items(store, kind="health_finding"):
    return [item for item in store.inbox(target="db") if item["type"] == kind]


def pushes(store):
    return [event for event in store.inbox_events() if event["kind"] == "notified"]


@pytest.mark.parametrize("severities,count", [([], 0), (["info"], 0), (["warn"], 0),
                                                (["bad"], 1), (["bad", "bad", "warn"], 1)])
def test_combined_push(store, severities, count):
    actions = [action(f"setting_{i}", severity) for i, severity in enumerate(severities)]
    run = file_report(store, actions)
    assert len(pushes(store)) == count
    assert len(items(store)) == sum(severity != "info" for severity in severities)
    for item in items(store):
        body = json.loads(item["body_json"])
        assert body["report_id"] == run
        assert body["evidence"] and body["why"] and body["steps"] and body["sql"]
        assert item["delivery"] == "inbox"
        assert item["severity"] == (.75 if body["severity"] == "bad" else .6)
        assert present_inbox_item(item, {}, [], set())["severity_band"] == "watch"
    daily = present_inbox_item(items(store, "daily_summary")[0], {}, [], set())
    if count:
        assert pushes(store)[0]["item_id"] == daily["id"]
        assert daily["summary"] == actions[0]["finding"] + (
            " Plus 1 more in today's report." if severities.count("bad") > 1 else "")
        assert daily["title"] == ("2 things to fix on db" if severities.count("bad") > 1 else actions[0]["title"])


def test_updates_escalation_and_no_repeat(store):
    file_report(store, [action(severity="warn")])
    original = items(store)[0]
    assert not pushes(store)
    file_report(store, [action()], NOW + DAY)
    assert items(store)[0]["id"] == original["id"]
    assert len(pushes(store)) == 1
    file_report(store, [action()], NOW + 2 * DAY)
    file_report(store, [action(severity="warn")], NOW + 3 * DAY)
    file_report(store, [action()], NOW + 4 * DAY)
    assert len(pushes(store)) == 1
    assert len(items(store)) == 1
    assert len(store.read("SELECT * FROM inbox_event WHERE item_id=? AND kind='updated'",
                          (original["id"],))) == 4


def test_recovery_and_recurrence(store):
    file_report(store, [action()])
    original = items(store)[0]
    file_report(store, [], NOW + DAY)
    resolved = items(store)[0]
    assert resolved["state"] == "resolved"
    assert resolved["resolved_by"] == "code"
    assert resolved["resolved_at"] == NOW + DAY
    presented = present_inbox_item(resolved, {}, [], set())
    assert presented["severity_band"] == "recovered"
    assert presented["summary"] == "The latest report no longer flags this."
    InboxItem.model_validate(presented)
    file_report(store, [action()], NOW + 2 * DAY)
    reopened = items(store)[0]
    assert reopened["id"] == original["id"]
    assert reopened["state"] == "new" and reopened["resolved_by"] is None
    assert "resolution" not in json.loads(reopened["body_json"])
    # Back within a week of clearing: a flap, so no second push.
    assert len(pushes(store)) == 1
    file_report(store, [], NOW + 3 * DAY)
    file_report(store, [action()], NOW + 11 * DAY)
    assert len(pushes(store)) == 2


@pytest.mark.parametrize("collectors", [{}, {"engine": "postgresql"}, {"collection_error": "offline", "config_audit": {}},
                                        {"config_audit": {}, "section_errors": {"config_audit": "denied"}},
                                        {"replication": {}}])
def test_failed_or_missing_collectors_preserve_items(store, collectors):
    file_report(store, [action()])
    original = items(store)[0]
    file_report(store, [], NOW + DAY, collectors)
    assert items(store)[0] == original
    assert len(pushes(store)) == 1


def test_partial_collection_resolves_only_checked_items(store):
    file_report(store, [action(), action("replica_lag", check="replication")])
    file_report(store, [], NOW + DAY,
                {"config_audit": {}, "replication": {}, "section_errors": {"replication": "denied"}})
    assert {item["subject"]: item["state"] for item in items(store)} == {
        "setting_shared_buffers": "resolved", "replica_lag": "new"}


def test_user_reports_leave_health_untouched(store):
    file_report(store, [action()])
    before = items(store)
    file_report(store, [], NOW + DAY, trigger="user")
    assert items(store) == before
    assert len(items(store, "daily_summary")) == 1


def test_retry_does_not_push_or_lose_headline(store):
    run = file_report(store, [action()])
    before = items(store, "daily_summary")[0]
    report._file_report_items(store, "db", run, NOW, {"summary": "New prose."}, "nightly")
    assert len(pushes(store)) == 1
    assert present_inbox_item(items(store, "daily_summary")[0], {}, [], set())["summary"] == action()["finding"]
    assert items(store, "daily_summary")[0]["id"] == before["id"]


def test_dismissed_finding_stays_silent(store):
    file_report(store, [action()])
    store.action_item(items(store)[0]["id"], "dismiss", NOW + 1)
    file_report(store, [action()], NOW + DAY)
    assert len(pushes(store)) == 1
    assert items(store)[0]["state"] == "dismissed"


@pytest.mark.parametrize("first_kind", ["latency_regression", "daily_summary"])
def test_combined_push_counts_toward_daily_cap(store, first_kind):
    store.set_settings("db", {"push_daily_cap": 1})
    if first_kind == "latency_regression":
        store.upsert_item("db", first_kind, "q", .7, "Query slowed", {}, NOW - 1, delivery="notify")
    file_report(store, [action()])
    if first_kind == "daily_summary":
        store.upsert_item("db", "latency_regression", "q", .7, "Query slowed", {}, NOW + 1, delivery="notify")
    assert len(pushes(store)) == 1
    assert store.read("SELECT * FROM inbox_event WHERE json_extract(detail_json,'$.push_skipped')='daily_limit'")
    assert all(item["delivery"] == "inbox" for item in items(store))


def test_jev_excludes_health_findings_even_with_query_keys(store):
    file_report(store, [{**action(), "query_keys": []}])
    store.upsert_item("db", "health_finding", "database", .6, "Review scans", {}, NOW, delivery="inbox")
    request = judge._request(store, "db", NOW, [], {"inbox": items(store), "database": {}})
    assert request["open_items"] == []


@pytest.mark.parametrize("engine,row", [("postgresql", ("public", "orders", 8000, 1_000_000, 100_000)),
                                        ("mysql", ("app", "orders", 800_000_000, 100_000))])
def test_scan_collector_and_day_delta(engine, row):
    conn = Mock()
    conn.cursor.return_value.fetchall.return_value = [row]
    current = collect_sequential_scans(conn, engine)
    assert conn.cursor.return_value.close.called
    sql = conn.cursor.return_value.execute.call_args.args[0]
    assert ("pg_stat_user_tables" if engine == "postgresql" else "INDEX_NAME IS NULL") in sql
    assert not database_actions({"sequential_scans": scan_deltas(current, None)}, {})
    prior = {"tables": [{**current["tables"][0], "scans": 7000, "tuples_read": 900_000}
                        if engine == "postgresql" else
                        {**current["tables"][0], "tuples_read": 700_000_000}]}
    measured = scan_deltas(current, prior)
    actions = database_actions({"sequential_scans": measured}, {})
    assert actions[0]["severity"] == "warn"
    assert "1,000 full scans" in actions[0]["finding"]
    assert measured["deltas"][0]["scans_day"] == 1000
    reset = scan_deltas(prior, current)
    assert not reset["deltas"] and reset["unchecked_tables"]


@pytest.mark.parametrize("rows,scans,severity", [(99_999, 10_000, None), (100_000, 999, None),
                                                (100_000, 1000, "warn"), (1_000_000, 10_000, "bad"),
                                                (999_999, 10_000, "warn")])
def test_scan_thresholds(rows, scans, severity):
    section = {"compared": True, "deltas": [{"schema": "public", "table": "orders", "rows": rows, "scans_day": scans}]}
    actions = database_actions({"sequential_scans": section}, {})
    assert (actions[0]["severity"] if actions else None) == severity
    if actions:
        assert health_section({"sequential_scans": section})["problems"][0]["check"] == "sequential_scans"


def test_previous_night_and_related_queries(store):
    baseline = {"tables": [{"schema": "public", "table": "orders", "rows": 100_000, "scans": 7000}]}
    file_report(store, [], collectors={"sequential_scans": baseline})
    store.record_judge_run("db", NOW, [], request={"queries": [
        {"query_key": "q1", "tables": ["public.orders"], "normalized_sql": "SELECT * FROM orders", "db_time_share": .8},
        {"query_key": "q2", "tables": ["elsewhere"], "normalized_sql": "SELECT * FROM elsewhere", "db_time_share": .9}]})
    health = {"sequential_scans": {"tables": [{**baseline["tables"][0], "scans": 8000}]}}
    report._scan_health(store, "db", NOW + DAY, health)
    actions = database_actions(health, {})
    assert actions[0]["query_keys"] == ["q1"]
    assert actions[0]["rows"][0]["name"]
    file_report(store, actions, NOW + DAY, health)
    assert len(items(store)) == 1
    missing = {"sequential_scans": baseline}
    report._scan_health(store, "db", NOW + 4 * DAY, missing)
    assert not missing["sequential_scans"]["compared"]
    file_report(store, [], NOW + 4 * DAY, missing)
    assert items(store)[0]["state"] == "new"


def test_scan_collector_error_is_recorded(monkeypatch):
    conn = Mock()
    monkeypatch.setattr("shared.db_connection.create_direct_connection", lambda tc: conn)
    monkeypatch.setattr("features.audit.health.sequential_scans.collect_sequential_scans",
                        Mock(side_effect=RuntimeError("permission denied")))
    health = collect_health_report({}, "postgresql")
    assert health.section_errors["sequential_scans"] == "permission denied"
    assert health.sequential_scans is None


@pytest.mark.parametrize("section,errors", [(None, {}), ({"tables": []}, {"sequential_scans": "denied"}),
                                           ({"compared": False}, {}),
                                           ({"compared": True, "unchecked_tables": ["public.orders"]}, {})])
def test_scan_recovery_needs_a_comparison(store, section, errors):
    finding = action("sequential_scans_public.orders", "warn", "sequential_scans")
    file_report(store, [finding], collectors={"sequential_scans": {"compared": True}})
    before = items(store)[0]
    file_report(store, [], NOW + DAY, {"sequential_scans": section, "section_errors": errors})
    assert items(store)[0] == before


def test_scan_recovery_after_successful_comparison(store):
    finding = action("sequential_scans_public.orders", "warn", "sequential_scans")
    file_report(store, [finding], collectors={"sequential_scans": {"compared": True}})
    file_report(store, [], NOW + DAY, {"sequential_scans": {"compared": True, "deltas": []}})
    assert items(store)[0]["state"] == "resolved"


@pytest.mark.asyncio
async def test_nightly_scan_flows_through_report_and_inbox(store, monkeypatch):
    baseline = {"tables": [{"schema": "public", "table": "orders", "rows": 100_000, "scans": 7000}]}
    file_report(store, [], collectors={"sequential_scans": baseline})
    current = {"engine": "postgresql", "sequential_scans": {
        "tables": [{**baseline["tables"][0], "scans": 8000}]}}

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    async def inline_target(_target, function, *args):
        return function(*args)

    monkeypatch.setattr(report.asyncio, "to_thread", inline)
    monkeypatch.setattr(report, "_to_thread", inline_target)
    monkeypatch.setattr(report.database_limit, "_signed_in", lambda: True)
    result = await report.run_report(
        "db", "nightly", store=store, clock=lambda: NOW + DAY,
        collect=lambda target: (current, {}),
        narrate=lambda context: {"summary": "The report's ready.", "fix_first": [],
                                 "one_fix_several_queries": [],
                                 "health": [{"check": "Settings", "status": "ok", "note": "Settings look good."}],
                                 "changed_since_yesterday": []})
    assert result["state"] == "done"
    saved = json.loads(store.report(result["id"])["report_json"])
    assert saved["snapshot"]["actions"][0]["id"] == "sequential_scans_public.orders"
    assert any(check["check"] == "Full table scans" and check["status"] == "warn"
               for check in saved["health"])
    assert items(store)[0]["subject"] == "sequential_scans_public.orders"
    assert not pushes(store)

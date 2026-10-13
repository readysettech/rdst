"""Nightly checkup notices, headline pushes, and full-scan comparisons."""

import json
from unittest.mock import Mock

import pytest

from features.audit.health import collect_health_report
from features.audit.health.sequential_scans import collect_sequential_scans, scan_deltas
from features.monitor import judge, report
from features.monitor.db_actions import database_actions
from features.monitor.presentation import present_inbox_item
from features.monitor.snapshot import health_section
from shared.monitor.monitor_store import MonitorStore

NOW = 2_000_000
DAY = 86400


@pytest.fixture
def store(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    # NOW falls inside the default quiet hours in some time zones.
    store.set_settings("db", {"quiet_hours": {"from_hour": 0, "to_hour": 0}})
    return store


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


def open_actions(store, findings, ts):
    """The checkup's reconcile opens an action per database finding before filing."""
    store.write_actions("db", ts, [{"key": f"db:{finding['id']}", "type": "change_setting",
                                    "state": "open", "score": 0.1, "payload": {"title": finding["title"]},
                                    "opened_at": ts} for finding in findings])


@pytest.mark.parametrize("severities,count", [([], 0), (["warn"], 0), (["bad"], 1),
                                                (["bad", "bad", "warn"], 1)])
def test_findings_stay_off_the_inbox_and_new_bad_ones_push_once(store, severities, count):
    findings = [action(f"setting_{i}", severity) for i, severity in enumerate(severities)]
    open_actions(store, findings, NOW)
    file_report(store, findings)
    assert items(store) == []
    assert len(pushes(store)) == count
    daily = present_inbox_item(items(store, "daily_summary")[0], {}, [], set())
    assert daily["title"] == f"Daily report for {report._month_day(NOW)} is ready"
    if count:
        assert pushes(store)[0]["item_id"] == daily["id"]
        more = " Plus 1 more in today's report." if severities.count("bad") > 1 else ""
        assert daily["summary"] == f"Fix setting_0. setting_0 needs attention.{more}"


def test_a_finding_whose_action_was_already_open_does_not_push_again(store):
    finding = action()
    open_actions(store, [finding], NOW - DAY)
    file_report(store, [finding])
    assert not pushes(store)


def test_finding_items_filed_before_actions_close(store):
    store.upsert_item("db", "health_finding", "setting_old", .6, "Old", {"check": "settings"}, NOW - DAY,
                      delivery="inbox")
    file_report(store, [])
    assert items(store)[0]["state"] == "resolved"


def test_user_reports_leave_health_untouched(store):
    file_report(store, [action()])
    before = items(store)
    file_report(store, [], NOW + DAY, trigger="user")
    assert items(store) == before
    assert len(items(store, "daily_summary")) == 1


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
    missing = {"sequential_scans": baseline}
    report._scan_health(store, "db", NOW + 4 * DAY, missing)
    assert not missing["sequential_scans"]["compared"]


def test_scan_collector_error_is_recorded(monkeypatch):
    conn = Mock()
    monkeypatch.setattr("shared.db_connection.create_direct_connection", lambda tc, **kw: conn)
    monkeypatch.setattr("features.audit.health.sequential_scans.collect_sequential_scans",
                        Mock(side_effect=RuntimeError("permission denied")))
    health = collect_health_report({}, "postgresql")
    assert health.section_errors["sequential_scans"] == "permission denied"
    assert health.sequential_scans is None


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
    # The finding is an action now; the Inbox carries only the checkup's notice.
    assert items(store) == [] and items(store, "daily_summary")
    assert not pushes(store)

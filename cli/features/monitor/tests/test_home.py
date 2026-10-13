"""GET /home: one sentence from data, the day card, every database, the day's log."""

from __future__ import annotations

from datetime import datetime

import pytest

from features.monitor import actions, database_limit
from features.monitor.api import routes, views
from features.monitor.index_plan import build_plan
from features.monitor.presentation import clock_text
from features.monitor.tests.conftest import KEYS, NOW, seed

CONFIGURED = [{"name": "db", "engine": "postgresql", "host": "127.0.0.1", "port": 15432},
              {"name": "other", "engine": "postgresql", "host": "localhost", "port": 5432}]


def _watching(store, monkeypatch) -> None:
    seed(store, minutes=60)
    store.set_settings("db", {"enabled": True})
    store.record_judge_run("db", NOW - 60, [], outcome="filed")
    monkeypatch.setattr(database_limit, "paused", lambda store, target, now: False)


def test_home_says_what_needs_you_and_lists_every_database_by_attention(store, monkeypatch):
    _watching(store, monkeypatch)
    store.sync_stuck_items("db", NOW - 90, [{"pid": 48213, "query_key": KEYS[4], "duration_s": 240}])
    home = views.home(store, NOW, configured=CONFIGURED)
    status = home["status"]
    assert (status["state"], status["needs_you"], status["to_watch"], status["target"],
            status["watching"]) == ("needs_you", 1, 0, "db", 1)
    assert status["headline"] == f"a stuck query since {clock_text(NOW - 90)}"
    item = store.inbox(target="db")[0]
    assert status["link"] == {"kind": "inbox", "target": "db", "label": "Inbox", "item_id": item["id"]}
    assert status["checks_ran_at"] == NOW - 60 and status["last_reading_at"] == NOW - 55
    assert status["next_report_at"] == int(datetime(2026, 10, 9).timestamp())
    assert [(row["target"], row["state"]) for row in home["databases"]] == \
        [("db", "watching"), ("other", "not_monitored")]
    assert home["databases"][0]["connection"] == "PostgreSQL at 127.0.0.1:15432"
    assert (home["databases"][0]["act_now"], home["databases"][0]["open_items"]) == (1, 1)
    assert home["databases"][0]["qps"] > 0 and home["databases"][1]["qps"] is None
    assert home["not_monitored"] == 1
    day = home["day"]
    assert day["target"] == "db" and day["qps"] > 0 and day["cache_hit_ratio"] == 0.125
    assert day["qps_peak"] >= day["qps"] and len(day["series"]) > 0
    assert home["today"][0] == {"ts": NOW - 90, "text": "Stuck query filed", "detail": None,
                                "link": status["link"], "band": "act_now"}
    assert home["since_yesterday"] == {"opened_closed": 0, "fixes_confirmed": 0}
    assert home["recently_analyzed"] == [] and home["generated_at"] == NOW
    # A chosen database gets its own day card (none yet for one without readings); the
    # sentence still names the one that needs you.
    store.set_settings("other", {"enabled": True})
    chosen = views.home(store, NOW, configured=CONFIGURED, selected="other")
    assert chosen["status"]["target"] == "db" and chosen["day"] is None
    assert views.home(store, NOW, configured=CONFIGURED, selected="db")["day"]["target"] == "db"


def test_home_is_quiet_once_the_item_closes_and_paused_when_checks_stop(store, monkeypatch):
    _watching(store, monkeypatch)
    store.sync_stuck_items("db", NOW - 300, [{"pid": 1, "query_key": KEYS[4], "duration_s": 240}])
    store.sync_stuck_items("db", NOW - 30, [])
    home = views.home(store, NOW, configured=CONFIGURED)
    assert (home["status"]["state"], home["status"]["since"], home["status"]["headline"]) == \
        ("quiet", NOW - 30, None)
    assert home["today"][0]["text"] == "Stuck query ended, item closed"
    assert home["today"][0]["detail"] == "ran 4 minutes"
    assert home["since_yesterday"]["opened_closed"] == 1
    later = views.home(store, NOW + 3600, configured=CONFIGURED)
    assert later["status"]["state"] == "paused" and later["status"]["since"] == NOW - 60
    off = views.home(store, NOW, configured=[CONFIGURED[1]])
    assert (off["status"]["state"], off["status"]["watching"], off["day"]) == ("off", 0, None)
    # Nothing monitored: the status names the first configured database.
    assert off["status"]["target"] == "other"
    assert off["databases"][0]["state"] == "not_monitored"


def _plan(store, at: int) -> None:
    """Two index adds, both Do first: posts for the busiest query, votes for the next."""
    plan = build_plan({"engine": "postgresql", "now": at, "observed_s": 3600,
                       "counter_age_s": 30 * 86400, "existing": [], "tables": {}, "queries": [
                           {"key": key, "name": name, "share": share, "kind": "select",
                            "suggestions": [{"table": table, "columns": [column],
                                             "gain_pct": 80, "verified": True}]}
                           for key, name, share, table, column in (
                               (KEYS[0], "SELECT on posts", 0.4, "posts", "owneruserid"),
                               (KEYS[1], "SELECT on votes", 0.2, "votes", "postid"))]})
    store.save_index_plan("db", plan)
    actions.reconcile(store, "db", at, actions.candidates(store, "db", plan, [], []))


def test_home_log_lists_reports_fixes_and_analyses(store, monkeypatch):
    _watching(store, monkeypatch)
    midnight = int(datetime(2026, 10, 8).timestamp())
    run, _ = store.claim_report("db", "nightly", midnight + 60, midnight - 86400)
    store.update_report(run["id"], state="done", finished_at=midnight + 120, report={})
    _plan(store, NOW - 300)
    fixes = next(row for row in store.inbox(target="db") if row["type"] == "changes_ready")
    confirmed = store.upsert_item("db", "fix_confirmed", KEYS[3], .5, "Fix confirmed: users",
                                  {"query_key": KEYS[3], "index_label": "users (id)",
                                   "detected_at": NOW - 700, "before_ms": 27.6, "after_ms": 1.9,
                                   "calls": 40}, NOW - 700, delivery="inbox")
    store.action_item(confirmed["id"], "resolve", NOW - 600, resolved_by="code")
    store._write(lambda conn: conn.executemany(
        "INSERT INTO analyze_job (target,query_key,reason,state,priority,enqueued_at,finished_at,"
        "analysis_ref) VALUES ('db',?,'new','done',1.0,?,?,?)",
        [(KEYS[0], NOW - 1000, NOW - 960, "/a/1"), (KEYS[1], NOW - 1000, NOW - 950, "/a/2")]))
    store._write(lambda conn: conn.executemany(
        "INSERT INTO analyze_job (target,query_key,reason,state,priority,enqueued_at,finished_at,"
        "analysis_ref) VALUES ('db',?,'user','done',1.0,?,?,?)", [(KEYS[2], NOW - 200, NOW - 150, "/a/3")]))
    store.sync_query_ui("db", summaries={KEYS[2]: {"efficiency_score": 42, "fix_ready": True}})
    home = views.home(store, NOW, configured=CONFIGURED[:1])
    log = [(entry["text"], entry["detail"], entry["link"]["label"]) for entry in home["today"]]
    assert log == [
        ("2 changes ready", "2 to do first", "Actions"),
        ("Fix confirmed on users (id)", "27.6 ms to 1.9 ms", "Query"),
        ("2 new queries analyzed", "both healthy", "Queries"),
        ("Daily report for October 7", None, "Report")]
    assert home["today"][0]["link"]["item_id"] == fixes["id"]
    assert home["since_yesterday"] == {"opened_closed": 1, "fixes_confirmed": 1}
    # Fixes ready counts the queries the open actions speed up, by their share of the week.
    assert home["day"]["fixes_ready"] == 2
    assert home["day"]["fixes_share"] == pytest.approx(5000 / 6800)
    assert home["day"]["fixes_item_id"] == fixes["id"]
    assert (home["status"]["act_now"], home["status"]["do_first"]) == (0, 2)
    assert [{**row, "activity": None} for row in home["databases_strip"]] == [
        {"target": "db", "state": "watching", "act_now": 0, "to_do": 2, "do_first": 2,
         "fixes_ready": 2, "activity": None, "building": False}]
    assert (home["day"]["report_id"], home["day"]["report_day"]) == (run["id"], "2026-10-07")
    assert home["recently_analyzed"] == [{
        "target": "db", "query_key": KEYS[2], "display_name": "COUNT on comments by postid",
        "analyzed_at": NOW - 150, "analysis_ref": "/a/3", "outcome": "fix_suggested"}]
    assert home["status"]["to_watch"] == 1


def test_home_route_serves_the_model_and_bounds_its_limit(client, store, monkeypatch):
    _watching(store, monkeypatch)
    monkeypatch.setattr(routes, "_configured_targets", lambda: CONFIGURED)
    page = client.get("/api/monitor/home").json()
    assert page["status"]["state"] == "quiet" and len(page["databases"]) == 2
    assert client.get("/api/monitor/home?limit=0").status_code == 422


def test_strip_rows_say_what_monitoring_is_doing_on_each_database(store, monkeypatch):
    from types import SimpleNamespace

    from features.monitor import loop
    from features.monitor.naming import target_names

    _watching(store, monkeypatch)
    strip = {row["target"]: row["activity"]
             for row in views.home(store, NOW, configured=CONFIGURED)["databases_strip"]}
    # The seed writes its own collect pulse beside the store's, so the cadence isn't checked.
    assert {**strip["db"], "every_s": 60} == {
        "kind": "reading", "label": "Reading query statistics", "done": None, "total": None,
        "last_reading_at": NOW - 55, "every_s": 60, "next_check_at": NOW - 60 + 5 * 60}
    assert strip["other"] == {"kind": "idle", "label": "Monitoring is off", "done": None,
                              "total": None, "last_reading_at": None, "every_s": 60,
                              "next_check_at": None}
    monkeypatch.setattr(loop, "_active_loop", SimpleNamespace(_cycling={"db": object()}))
    assert views.home(store, NOW, configured=CONFIGURED)["databases_strip"][0]["activity"][
        "label"] == "Checking queries for changes"
    store.claim_report("db", "user", NOW)
    assert views.home(store, NOW, configured=CONFIGURED)["databases_strip"][0]["activity"][
        "kind"] == "report"
    # A database in its first hour: the running analysis by name, and the hour's progress.
    store.set_settings("other", {"enabled": True})
    store.start_first_hour("other", NOW)
    seed(store, minutes=2, target="other", start=NOW)
    for key in KEYS[:3]:
        store.enqueue_analysis("other", key, "new", 1.0, NOW + 1)
    first = store.claim_analysis("other", NOW + 2)
    store.finish_analysis(first["id"], "done", NOW + 60)
    running = store.claim_analysis("other", NOW + 61)
    row = next(row for row in views.home(store, NOW + 120, configured=CONFIGURED)[
        "databases_strip"] if row["target"] == "other")
    name = target_names(store, "other")[running["query_key"]]
    assert row["activity"]["kind"] == "analyzing"
    assert row["activity"]["label"] == f"Analyzing {name}"
    assert (row["activity"]["done"], row["activity"]["total"]) == (1, 3)
    # Checks held by sign-in have no next check, however long ago the last one ran.
    store.record_judge_run("db", NOW + 100, [], outcome="signed_out")
    assert views.home(store, NOW + 120, configured=CONFIGURED)["databases_strip"][0][
        "activity"]["next_check_at"] is None


def test_pulse_carries_the_activity(client, store, monkeypatch):
    _watching(store, monkeypatch)
    page = client.get("/api/monitor/targets/db/pulse").json()
    assert page["activity"]["kind"] == "reading"
    assert page["activity"]["last_reading_at"] == NOW - 55
    assert page["pulses"][0]["activity"] == page["activity"]

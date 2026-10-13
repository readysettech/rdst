"""The fresh-user tour's backend fixes: one peak and one rollbacks figure on every page, the
first plan held for sign-in, and wording that says only what is true."""

from __future__ import annotations

from datetime import datetime

import pytest

from features.monitor import actions, database_limit, snapshot
from features.monitor.analysis_digest import index_why
from features.monitor.api import models, views
from features.monitor.presentation import first_change, inbox_link
from features.monitor.tests.conftest import KEYS, NOW, TEXTS, seed

MIDNIGHT = int(datetime(2026, 10, 8).timestamp())
DATABASE = {"fixes_ready": 0, "_fixes_share": None, "fixes_item_id": None}


def _minutes(store, start: int, calls: list[int]) -> None:
    for index, count in enumerate(calls):
        store.record("db", start + index * 60,
                     [{"query_key": KEYS[0], "calls": count, "total_ms": count * 2.0}], TEXTS, [],
                     {"calls": count, "total_ms": count * 2.0, "active_conns": 4,
                      "max_conns": 100, "cache_hit_ratio": 0.99, "xact_commit": 10,
                      "xact_rollback": 3, "deadlocks": 0})


def test_home_the_database_page_and_the_report_show_one_peak(store):
    start = NOW // 60 * 60 - 40 * 60
    _minutes(store, start, [100] * 10 + [600] + [100] * 29)
    health = views.health(store, "db", "24h", NOW)
    models.MonitorHealth.model_validate(health)
    period = health["period"]
    assert (period["qps_peak"], period["qps_peak_at"]) == (pytest.approx(10.0), start + 600)
    assert period["qps_peak"] >= period["qps"]
    # The report's day and Home's day card read the same busiest minute.
    day = snapshot._window_numbers(store, "db", NOW - 86400, NOW + 1)
    card = views._day_card(store, "db", NOW, MIDNIGHT, DATABASE)
    assert (day["peak_qps"], day["peak_qps_at"]) == (period["qps_peak"], period["qps_peak_at"])
    assert (card["qps_peak"], card["qps_peak_at"]) == (period["qps_peak"], period["qps_peak_at"])


def test_the_peak_is_the_busiest_whole_minute_and_never_below_the_average(store):
    # The first minutes, inside five-minute buckets that are still filling: the peak is a
    # whole minute's rate, so it holds as the buckets fill.
    _minutes(store, NOW // 60 * 60 - 180, [120, 180, 60])
    card = views._day_card(store, "db", NOW, MIDNIGHT, DATABASE)
    assert (card["qps_peak"], card["qps_peak_at"]) == (pytest.approx(3.0), NOW // 60 * 60 - 120)
    assert card["qps_peak"] >= card["qps"]
    assert views.peak_rate(store, "db", MIDNIGHT, NOW + 1, 5.0)["qps"] == 5.0
    assert views.peak_rate(store, "db", MIDNIGHT, MIDNIGHT + 60, None) is None


def test_rollbacks_are_one_whole_number_for_the_same_window(store):
    _minutes(store, NOW // 60 * 60 - 40 * 60, [100] * 40)
    health = views.health(store, "db", "24h", NOW)
    day = snapshot._window_numbers(store, "db", NOW - 86400, NOW + 1)
    assert health["period"]["xact_rollback"] == day["xact_rollback"] == 120
    assert isinstance(day["xact_rollback"], int)
    # The live minute chart's per-minute rows are fractions of rollups; the sum stays whole.
    hour = views.health(store, "db", "1h", NOW)
    assert hour["period"]["xact_rollback"] == 120 and isinstance(
        hour["period"]["xact_rollback"], int)


def test_the_first_plan_says_it_waits_for_sign_in(store, monkeypatch):
    store.set_settings("db", {"enabled": True})
    store.start_first_hour("db", NOW - 120)
    monkeypatch.setattr(database_limit, "_signed_in", lambda: False)
    held = actions._checkup_state(store, "db", NOW)
    models.CheckupState.model_validate(held)
    assert (held["state"], held["held"], held["held_text"]) == \
        ("first_pass", "sign_in", "Sign in to turn on AI monitoring")
    monkeypatch.setattr(database_limit, "_signed_in", lambda: True)
    assert actions._checkup_state(store, "db", NOW)["held"] is None


def test_held_analyses_carry_the_reason_on_the_analyze_jobs_payload(client, store, monkeypatch):
    store.set_settings("db", {"enabled": True, "deep_analysis": True})
    store._write(lambda conn: conn.execute(
        "INSERT INTO analyze_job (target,query_key,reason,state,priority,enqueued_at) "
        "VALUES ('db',?,'new','queued',1.0,?)", (KEYS[0], NOW - 60)))
    monkeypatch.setattr(database_limit, "_signed_in", lambda: False)
    page = client.get("/api/monitor/targets/db/analyze-jobs").json()
    models.AnalyzeJobPage.model_validate(page)
    [job] = page["jobs"]
    assert (job["queue_state"], job["held_reason"], job["reason_text"]) == \
        ("held", "sign_in", "Sign in to turn on AI monitoring")


def test_turning_monitoring_off_and_on_reconnects_its_readers(client, store, monkeypatch):
    changed = []
    monkeypatch.setattr("features.monitor.loop.connection_changed",
                        lambda target, scheduler=None: changed.append(target))

    class Queue:
        async def stop(self) -> None:
            pass

        def wake(self) -> None:
            pass

    client.app.state.analyze_queue = Queue()
    assert client.put("/api/monitor/targets/db/settings", json={"enabled": True}).status_code \
        == 200
    client.put("/api/monitor/targets/db/settings", json={"deep_analysis": False})
    client.put("/api/monitor/targets/db/settings", json={"enabled": False})
    assert changed == ["db", "db"]


def test_the_changes_ready_summary_names_the_first_change():
    assert first_change("Add an index on orders(total)") == "an index on orders(total)"
    assert first_change("Raise work_mem so sorts stay in memory") == \
        "raising work_mem so sorts stay in memory"
    assert first_change("Drop 2 unused indexes") == "dropping 2 unused indexes"
    assert first_change("Rewrite SELECT on orders") == "rewriting SELECT on orders"
    assert first_change("Turn on slow query logging") == "turning on slow query logging"
    assert first_change("db:vacuum") == "db:vacuum"


def test_a_report_ready_item_opens_its_report():
    item = {"type": "report_ready", "target": "db", "subject": "run:4", "state": "new"}
    assert inbox_link(item, {"report_id": 4}, []) == {
        "kind": "report", "target": "db", "report_id": 4, "label": "Open report"}


def test_the_report_inbox_and_actions_count_the_same_checks(client, store, monkeypatch):
    from features.monitor.api import routes
    from features.monitor.tests.test_report_view import _action, _check, _run, _stored

    monkeypatch.setattr(database_limit, "_signed_in", lambda: True)
    monkeypatch.setattr(routes, "_configured_targets", lambda: [{"name": "db"}])
    store.set_settings("db", {"enabled": True})
    stored = _stored()
    day = stored["snapshot"]
    # The tour's report: an index with no reads and a setting off a rule of thumb, both
    # "Needs a look", neither a change Actions files.
    day["overview"]["checks"] = [
        _check(name, label, "warn" if name in ("indexes", "settings") else "ok", "")
        for name, label in (("cache", "Cache hit"), ("connections", "Connections"),
                            ("vacuum", "Vacuum and bloat"), ("indexes", "Indexes"),
                            ("settings", "Settings"), ("replication", "Replication"),
                            ("transactions", "Rollbacks and deadlocks"),
                            ("sequential_scans", "Full table scans"))]
    day["actions"] = [
        _action(id="unused_indexes", check="indexes", title="Drop 1 unused index",
                severity="warn", finding="1 index has no reads, 41 MB in all.",
                sql="DROP INDEX CONCURRENTLY public.votes_creationdate_idx;"),
        _action(id="setting_random_page_cost", check="settings", severity="warn",
                title="Lower random_page_cost", finding="random_page_cost is 4.",
                setting={"name": "random_page_cost", "current": "4", "target": "1.1"})]
    run_id = _run(store, stored)

    report = client.get(f"/api/monitor/targets/db/report/{run_id}").json()
    health = report["health"]
    # The rows the report shows as "Needs a look" are the rows that need you.
    looks = [row["check"] for row in health if row["state"] in ("warn", "bad")]
    assert looks == [row["check"] for row in health if row["needs_you"]] == ["indexes", "settings"]
    assert "2 of 8 checks need you" in report["verdict"]["rest"]
    assert not any(row["in_actions"] for row in health)

    page = client.get("/api/monitor/actions").json()
    models.GlobalActionsPage.model_validate(page)
    assert page["counts"]["total"] == 0
    # Actions counts what the report counts and names what only the report lists.
    assert page["report_checks"]["db"] == {
        "report_id": run_id, "checks": 8, "needs_you": 2,
        "not_in_actions": [
            {"check": "indexes", "label": "Indexes",
             "finding": "1 index has no reads, 41 MB in all"},
            {"check": "settings", "label": "Settings", "finding": "random_page_cost is 4"}]}


def test_the_benchmark_and_actions_read_one_estimate_and_claim_none_unmeasured(client, store):
    from features.monitor.index_plan import build_plan

    seed(store, minutes=120)
    # The planner measured the posts index; the votes index has only the analysis's "high".
    plan = build_plan({"engine": "postgresql", "now": NOW, "observed_s": 7200,
                       "counter_age_s": 30 * 86400, "existing": [], "tables": {},
                       "queries": [{"key": key, "name": f"SELECT on {table}", "share": 0.3,
                                    "kind": "select", "suggestions": [
                                        {"table": table, "columns": [column], "gain_pct": pct,
                                         "verified": pct is not None, "impact": "high"}]}
                                   for key, table, column, pct in (
                                       (KEYS[0], "posts", "owneruserid", 60.0),
                                       (KEYS[1], "votes", "postid", None))]})
    store.save_index_plan("db", plan)
    actions.reconcile(store, "db", NOW, actions.candidates(store, "db", plan, [], []))
    cards = {card["queries"][0]["key"]: card["queries"][0]
             for card in actions.plan_cards(store, "db", NOW)}
    assert cards[KEYS[0]]["expected_ms"] == pytest.approx(cards[KEYS[0]]["mean_ms"] * 0.4)
    assert cards[KEYS[1]]["expected_ms"] is None

    result = client.post("/api/monitor/targets/db/benchmark-fixes",
                         json={"query_keys": [KEYS[0], KEYS[1]], "runs": 20}).json()
    posts, votes = result["rows"]
    assert (posts["before_ms"], posts["after_ms"]) == \
        (pytest.approx(cards[KEYS[0]]["mean_ms"]), pytest.approx(cards[KEYS[0]]["expected_ms"]))
    assert posts["change_pct"] == -60 and posts["reason"] is None
    assert (votes["after_ms"], votes["reason"]) == \
        (None, "The analysis gave no estimate for this fix.")


def test_an_index_fix_never_claims_what_only_a_covering_index_does():
    why = ("Queries filter on customer_id with no index. An index on customer_id turns the "
           "scan into a lookup. The id column is already available from the index entry "
           "itself, so this is an index-only scan. INCLUDE (device, duration_s) covers the "
           "select list.")
    assert index_why(why) == ("Queries filter on customer_id with no index. An index on "
                              "customer_id turns the scan into a lookup.")
    assert index_why("") == ""


def test_the_first_plan_says_it_waits_while_ai_is_paused(store, monkeypatch):
    store.set_settings("db", {"enabled": True})
    store.start_first_hour("db", NOW - 120)
    monkeypatch.setattr(database_limit, "_signed_in", lambda: True)
    database_limit.set_ai_state(store, "db", "local", "limit", NOW)
    held = actions._checkup_state(store, "db", NOW)
    models.CheckupState.model_validate(held)
    assert (held["state"], held["held"], held["ai_reason"]) == \
        ("first_pass", "ai_paused", "limit")
    assert held["held_text"] == actions.AI_PAUSED_TEXT["limit"]
    database_limit.set_ai_state(store, "db", "on", None, NOW)
    assert actions._checkup_state(store, "db", NOW)["held"] is None


def test_the_global_plan_lists_each_databases_checkup(store, monkeypatch):
    store.set_settings("db", {"enabled": True})
    store.start_first_hour("db", NOW - 120)
    monkeypatch.setattr(database_limit, "_signed_in", lambda: True)
    page = actions.global_page(store, ["db"], NOW, unmonitored=["shop"])
    models.GlobalActionsPage.model_validate(page)
    rows = {row["database"]: row for row in page["databases"]}
    assert rows["db"]["checkup"]["state"] == "first_pass"
    assert rows["shop"]["checkup"] is None


def test_home_marks_a_database_building_its_first_plan(store, monkeypatch):
    store.set_settings("db", {"enabled": True})
    store.start_first_hour("db", NOW - 120)
    monkeypatch.setattr(database_limit, "_signed_in", lambda: True)
    home = views.home(store, NOW, configured=[{"name": "db"}])
    models.HomeModel.model_validate(home)
    assert home["databases_strip"][0]["building"] is True


def test_a_rewrite_card_carries_the_query_it_replaces(store):
    row = {"id": 1, "key": "rewrite:k", "type": "rewrite_query", "state": "open", "score": 0.1,
           "payload": {"title": "Rewrite it", "sql": "SELECT 1", "queries": [],
                       "detail": {"original": "SELECT * FROM t"}},
           "opened_at": NOW, "updated_at": NOW}
    card = actions.plan_card(row, "db", {}, 1000.0, {}, "postgresql")
    assert card["original"] == "SELECT * FROM t"
    index = {**row, "type": "add_index", "key": "add:k"}
    assert actions.plan_card(index, "db", {}, 1000.0, {}, "postgresql")["original"] is None

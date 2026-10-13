"""The redesign's filing rules: index fixes from the analyses, confirmed fixes from the
actions check, report items, outages, the closed window, checks_ran_at, the stuck query's
pid and the analysis defaults."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from features.monitor import actions, connection, judge, report
from features.monitor.api import views
from features.monitor.index_plan import build_plan
from features.monitor.analysis_digest import digest_for_ref
from features.monitor.db_actions import concurrent_index_sql, index_target
from features.monitor.presentation import clock_text
from features.monitor.tests.conftest import KEYS, NOW, seed

POSTS_SQL = "CREATE INDEX idx_posts_owneruserid ON posts (owneruserid, creationdate DESC);"
VOTES_SQL = "CREATE INDEX idx_votes_postid ON votes (postid);"


def _digest(key: str, sql: str | None) -> dict:
    fix = {"kind": "index", "title": "Add an index on posts(owneruserid, creationdate)",
           "sql": sql, "why": "No index on owneruserid.", "expected_gain": None} if sql else None
    return {"query_key": key, "analysis_ref": f"/api/query-registry/{key}/analysis/a_{key}",
            "analyzed_at": NOW - 600, "mode": "explain", "mode_label": "Plan only (EXPLAIN)",
            "rating": "fair" if sql else "good", "efficiency_score": 42 if sql else 95,
            "plan_summary": None, "findings": ["Sequential scan on posts."] if sql else [],
            "fix": fix, "other_fixes": [], "headline": "Needs an index" if sql else "Already efficient",
            "fix_ready": bool(sql)}


def _finished_job(store, key: str, ts: int = NOW - 600) -> None:
    store._write(lambda conn: conn.execute(
        "INSERT INTO analyze_job (target,query_key,reason,state,priority,enqueued_at,started_at,"
        "finished_at,analysis_ref) VALUES ('db',?,'new','done',1.0,?,?,?,?)",
        (key, ts - 60, ts - 30, ts, f"/api/query-registry/{key}/analysis/a_{key}")))


@pytest.fixture
def digests(monkeypatch):
    found = {KEYS[0]: _digest(KEYS[0], POSTS_SQL), KEYS[1]: _digest(KEYS[1], VOTES_SQL),
             KEYS[2]: _digest(KEYS[2], POSTS_SQL), KEYS[3]: _digest(KEYS[3], None)}

    def fake(target, key, ref, mode, finished_at=None):
        return found.get(key)

    monkeypatch.setattr("features.monitor.analysis_digest.digest_for_ref", fake)
    monkeypatch.setattr(views, "digest_for_ref", fake)
    return found


def _events(store, item_id: int) -> list[str]:
    return [event["kind"] for event in store.inbox_events() if event["item_id"] == item_id]


def test_ready_fixes_rank_index_fixes_by_share_with_concurrent_sql(store, digests):
    seed(store)
    for key in KEYS[:4]:
        _finished_job(store, key)
    store.sync_indexes("db", [], NOW - 3600)
    fixes = judge.ready_fixes(store, "db", NOW)
    assert [fix["rank"] for fix in fixes] == [1, 2, 3]
    assert [fix["query_key"] for fix in fixes] == KEYS[:3]
    assert all(fix["sql"].startswith("CREATE INDEX CONCURRENTLY") for fix in fixes)
    assert fixes[0]["group"] == "posts (owneruserid, creationdate)"
    assert not hasattr(judge, "file_fixes_ready") and not hasattr(judge, "confirm_fixes")


def _add(table: str, columns: list[str], helps: list[str], label: str = "safe") -> dict:
    return {"table": table, "columns": columns, "label": label, "benefit": 0.2,
            "helps": [{"key": key} for key in helps]}


def _fix_keys(store, adds=(), later=(), skipped=()) -> list[str]:
    for key in KEYS[:4]:
        _finished_job(store, key)
    store.sync_indexes("db", [], NOW - 3600)
    store.save_index_plan("db", {"computed_at": NOW, "add": list(adds), "later": list(later),
                                 "skipped": list(skipped)})
    return sorted(fix["query_key"] for fix in judge.ready_fixes(store, "db", NOW))


def test_fixes_follow_the_index_plan_adds(store, digests):
    seed(store)
    # The votes index is not in the plan, so its fix waits.
    assert _fix_keys(store, [_add("posts", ["owneruserid", "creationdate"], KEYS[:1])]) == \
        sorted([KEYS[0], KEYS[2]])


def test_fixes_count_a_wider_or_covering_add_for_every_query_it_speeds_up(store, digests):
    seed(store)
    # Posts fixes merged into a wider add; the votes query is sped up by another votes index.
    adds = [_add("posts", ["owneruserid", "creationdate", "score"], []),
            _add("votes", ["votetypeid"], [KEYS[1]])]
    assert _fix_keys(store, later=adds) == sorted(KEYS[:3])


def test_fixes_leave_out_adds_the_plan_skips(store, digests):
    seed(store)
    posts = _add("posts", ["owneruserid", "creationdate"], [KEYS[0], KEYS[2]], label="skip")
    assert _fix_keys(store, [posts], skipped=[_add("votes", ["postid"], [KEYS[1]], "skip")]) == []


def test_fixes_without_a_plan_keep_every_index_fix(store, digests):
    seed(store)
    for key in KEYS[:4]:
        _finished_job(store, key)
    assert store.index_plan("db") is None
    assert sorted(fix["query_key"] for fix in judge.ready_fixes(store, "db", NOW)) == \
        sorted(KEYS[:3])


def test_an_applied_index_files_one_closed_fix_confirmed_from_the_actions_check(store):
    seed(store)
    plan = build_plan({"engine": "postgresql", "now": NOW, "observed_s": 7200,
                       "counter_age_s": 30 * 86400, "existing": [], "tables": {},
                       "queries": [{"key": KEYS[0], "name": "SELECT on posts", "share": 0.4,
                                    "kind": "select", "suggestions": [
                                        {"table": "posts", "columns": ["owneruserid",
                                                                       "creationdate"],
                                         "gain_pct": 80, "verified": True}]}]})
    store.save_index_plan("db", plan)
    store.sync_indexes("db", [], NOW - 3600)
    actions.reconcile(store, "db", NOW, actions.candidates(store, "db", plan, [], []))
    store.sync_indexes("db", [{
        "schema": "public", "table": "posts", "index_name": "idx_posts_owneruserid",
        "definition": "CREATE INDEX idx_posts_owneruserid ON public.posts USING btree "
                      "(owneruserid, creationdate DESC)"}], NOW + 120)
    actions.reconcile(store, "db", NOW + 120, [])
    [row] = store.actions("db")
    assert (row["state"], row["closed_reason"]) == ("applied", "index_added")
    # Too soon, then too few calls: nothing files.
    actions.verify(store, "db", NOW + 600)
    actions.verify(store, "db", NOW + 1200)
    assert not [item for item in store.inbox(target="db") if item["type"] == "fix_confirmed"]
    seed(store, minutes=20, start=NOW + 420, means={KEYS[0]: 2.0})
    actions.verify(store, "db", NOW + 1700)
    assert store.actions("db")[0]["state"] == "confirmed"
    [item] = [item for item in store.inbox(target="db") if item["type"] == "fix_confirmed"]
    assert (item["state"], item["resolved_by"], item["delivery"]) == ("resolved", "code", "inbox")
    assert _events(store, item["id"]) == ["created", "resolved"]
    presented = views.inbox_items(store, [item])[0]
    assert (presented["kind"], presented["band"], presented["severity_band"]) == \
        ("fix_confirmed", "closed", "recovered")
    assert presented["title"].startswith("Fix confirmed: ")
    assert presented["before_ms"] == pytest.approx(50.0)
    assert presented["after_ms"] == pytest.approx(2.0)
    assert presented["calls"] >= actions.VERIFY_MIN_CALLS
    assert presented["index_name"] == "posts (owneruserid, creationdate)"
    assert presented["action_id"] == row["id"] and presented["link"]["kind"] == "actions"
    assert presented["summary"] == "50.0 ms to 2.0 ms."
    texts = [entry["text"] for entry in presented["history"]]
    assert texts[0] == "Item closed by RDST" and texts[1].startswith("Verified:")
    assert texts[-1] == "Measured against the 7 days before the change"
    assert presented["related"][0]["link"]["query_key"] == KEYS[0]
    assert presented["related"][1]["link"]["kind"] == "actions"
    # The measurement decided once: a later check files nothing more.
    actions.verify(store, "db", NOW + 3600)
    assert len([item for item in store.inbox(target="db") if item["type"] == "fix_confirmed"]) == 1


def _done_report(store, trigger: str, ts: int, period_start: int) -> dict:
    run, _ = store.claim_report("db", trigger, ts, period_start)
    store.update_report(run["id"], state="done", finished_at=ts + 30, report={
        "summary": "A summary.", "fix_first": [], "one_fix_several_queries": [], "health": [],
        "changed_since_yesterday": [],
        "computed": {"top_queries": [], "analyses": [
            {"query_key": KEYS[0]}, {"query_key": KEYS[1], "provisional": True}], "deltas": {}},
        "snapshot": {"period": {"start": period_start, "end": ts, "partial": True}}})
    return run


def test_on_request_report_files_report_ready_in_the_inbox_and_the_first_report_pushes_once(
        store):
    midnight = int(datetime(2026, 10, 8).timestamp())
    run = _done_report(store, "user", NOW - 60, midnight)
    report._file_report_items(store, "db", run["id"], NOW, {"summary": "A summary."}, "user")
    items = [row for row in store.inbox(target="db") if row["type"] == "report_ready"]
    assert len(items) == 1 and items[0]["delivery"] == "inbox"
    assert items[0]["title"] == "Report for October 8 is ready"
    assert json.loads(items[0]["body_json"])["analyses"] == 1
    assert _events(store, items[0]["id"]) == ["created"]
    later = _done_report(store, "user", NOW + 1000, midnight)
    report._file_report_items(store, "db", later["id"], NOW + 1030, {"summary": "A summary."},
                              "user", first=True)
    items = {row["subject"]: row for row in store.inbox(target="db")
             if row["type"] == "report_ready"}
    assert items[f"run:{run['id']}"]["state"] == "resolved"
    first = items[f"run:{later['id']}"]
    assert first["delivery"] == "notify" and _events(store, first["id"]) == ["created", "notified"]
    assert first["title"] == "First report for October 8 is ready"
    presented = next(row for row in views.inbox_items(store, [first]))
    assert (presented["kind"], presented["band"], presented["source_report_id"]) == \
        ("report_ready", "watch", later["id"])
    assert [entry["text"] for entry in presented["history"]] == \
        ["Pushed once to this computer", "Report ran, 1 analysis"]


def test_nightly_report_item_names_its_day_and_files_no_fixes_item(store, digests):
    seed(store)
    _finished_job(store, KEYS[1])
    store.sync_indexes("db", [], NOW - 3600)
    yesterday = int(datetime(2026, 10, 7).timestamp())
    run = _done_report(store, "nightly", int(datetime(2026, 10, 8, 0, 1).timestamp()), yesterday)
    report._file_report_items(store, "db", run["id"], NOW, {"summary": "A summary."}, "nightly")
    types = {row["type"]: row for row in store.inbox(target="db")}
    assert types["daily_summary"]["title"] == "Daily report for October 7 is ready"
    assert "fixes_ready" not in types
    assert views.inbox_items(store, [types["daily_summary"]])[0]["kind"] == "report_ready"


def test_outage_files_after_three_failed_cycles_pushes_after_an_hour_and_closes_on_recovery(
        store):
    store.set_settings("db", {"enabled": True})
    clock = [NOW]
    watch = connection.ConnectionWatch(store, clock=lambda: clock[0])
    for step in range(2):
        clock[0] = NOW + step * 60
        watch.collection("db", False, clock[0], "connection refused")
    assert not [row for row in store.inbox() if row["subject"] == "lost_connection"]
    clock[0] = NOW + 120
    watch.collection("db", False, clock[0], "connection refused")
    item = next(row for row in store.inbox() if row["subject"] == "lost_connection")
    assert item["delivery"] == "inbox" and _events(store, item["id"]) == ["created"]
    clock[0] = NOW + 1800
    watch.tick()
    assert "notified" not in _events(store, item["id"])
    clock[0] = NOW + 3700
    watch.tick()
    item = store.read("SELECT * FROM inbox_item WHERE id=?", (item["id"],))[0]
    assert item["delivery"] == "notify" and "notified" in _events(store, item["id"])
    clock[0] = NOW + 3800
    watch.collection("db", True, clock[0])
    item = store.read("SELECT * FROM inbox_item WHERE id=?", (item["id"],))[0]
    assert (item["state"], item["resolved_by"]) == ("resolved", "code")
    presented = views.inbox_items(store, [item])[0]
    assert (presented["kind"], presented["band"]) == ("monitoring_issue", "closed")


def test_closed_items_are_served_for_seven_days(client, store):
    seed(store, minutes=3)
    old = store.upsert_item("db", "traffic_spike", KEYS[0], .7, "Traffic jumped", {}, NOW - 9 * 86400)
    store.action_item(old["id"], "resolve", NOW - 8 * 86400, resolved_by="code")
    recent = store.upsert_item("db", "traffic_spike", KEYS[1], .7, "Traffic jumped", {}, NOW - 7200)
    store.action_item(recent["id"], "resolve", NOW - 3600, resolved_by="code")
    done = client.get("/api/monitor/inbox?state=done").json()["items"]
    assert [item["id"] for item in done] == [recent["id"]]
    assert done[0]["band"] == "closed"


def test_checks_ran_at_counts_completed_attention_checks_on_pulse_and_health(client, store):
    seed(store, minutes=3)
    store.set_settings("db", {"enabled": True})
    assert views.checks_ran_at(store, "db") is None
    store.record_judge_run("db", NOW - 100, [], outcome="filed")
    store.record_judge_run("db", NOW - 50, [], outcome="signed_out")
    assert views.checks_ran_at(store, "db") == NOW - 100
    assert client.get("/api/monitor/targets/db/health?range=1h").json()["checks_ran_at"] == NOW - 100
    pulse = client.get("/api/monitor/targets/db/pulse").json()
    assert pulse["checks_ran_at"] == NOW - 100 and pulse["pulses"][0]["checks_ran_at"] == NOW - 100


def test_stuck_query_steps_carry_the_real_pid(store):
    seed(store, minutes=3)
    store.sync_stuck_items("db", NOW, [{"pid": 48213, "query_key": KEYS[4], "duration_s": 240}])
    item = views.inbox_items(store, store.inbox(target="db"))[0]
    assert (item["kind"], item["band"], item["pid"], item["running_s"]) == \
        ("stuck_query", "act_now", 48213, 240.0)
    assert [step["title"] for step in item["steps"]] == \
        ["Find it and what it waits on", "Cancel it", "If it won't cancel"]
    assert all("48213" in step["sql"] for step in item["steps"])
    assert item["advice"]["steps"][1] == "Cancel it: SELECT pg_cancel_backend(48213);"
    assert [entry["text"] for entry in item["history"]] == \
        ["Pushed to this computer", "Filed", "Statement passed the 60 s limit"]
    assert [row["link"]["label"] for row in item["related"]] == ["Queries", "Live"]
    # Without a sampled pid the steps keep their placeholder.
    item = views.inbox_items(store, [store.upsert_item(
        "db", "stuck_query", KEYS[3], .9, "Stuck query", {"query_key": KEYS[3]}, NOW)])[0]
    assert item["steps"][1]["sql"] == "SELECT pg_cancel_backend(<pid>);"
    assert clock_text(NOW) == "9:56 AM"


def test_defaults_are_top_15_and_100_new_a_day(store):
    levers = store.levers("db")
    assert (levers["analysis_daily_cap"], levers["newcomer_daily_cap"]) == (15, 100)


def test_index_statements_build_concurrently_on_postgres():
    assert concurrent_index_sql("CREATE INDEX i ON t (c)") == "CREATE INDEX CONCURRENTLY i ON t (c)"
    assert concurrent_index_sql("create unique index i on t (c)") == \
        "create unique index CONCURRENTLY i on t (c)"
    assert concurrent_index_sql("CREATE INDEX CONCURRENTLY i ON t (c)") == \
        "CREATE INDEX CONCURRENTLY i ON t (c)"
    assert concurrent_index_sql("ALTER TABLE t ADD INDEX i (c)", "mysql") == \
        "ALTER TABLE t ADD INDEX i (c)"
    assert index_target(POSTS_SQL) == ("posts", ["owneruserid", "creationdate"])
    assert index_target('CREATE INDEX i ON "public"."Orders" (total) INCLUDE (id)') == \
        ("orders", ["total"])
    assert index_target("not an index") == (None, [])
    assert digest_for_ref.cache_info().maxsize == 1024

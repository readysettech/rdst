"""The notification model: relevance gate, delivery levels, learning period, budget."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from features.monitor import loop as monitor_loop
from features.monitor.delivery import (
    Relevance,
    LoadWindow,
    change_delivery,
    group_delivery,
    is_learning,
    relevance,
)
from features.monitor.judge import AttentionJudge, reconcile_delivery
from shared.monitor.monitor_store import SCHEMA_VERSION, MonitorStore

NOW = 3_000_000


def minutes(store, key, start, count, *, calls=10, ms=1.0):
    store._write(lambda conn: conn.executemany(
        "INSERT OR REPLACE INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
        [("db", key, 60, start + index * 60, calls, calls * ms, 0, 0, 0)
         for index in range(count)]))


def database_minutes(store, start, count, *, rollbacks=0, deadlocks=0, ratio=0.99):
    store._write(lambda conn: conn.executemany(
        "INSERT OR REPLACE INTO db_series (target,res,bucket_start,calls,total_ms,"
        "cache_hit_ratio,xact_rollback,deadlocks) VALUES (?,?,?,?,?,?,?,?)",
        [("db", 60, start + index * 60, 10, 10, ratio, rollbacks, deadlocks)
         for index in range(count)]))


@pytest.fixture
def store(tmp_path):
    return MonitorStore(tmp_path / "monitor.db")


# Relevance gate

def test_relevance_rules_use_recorded_24h_and_1h_load(store):
    # Twelve steady queries over the last day; "hour_heavy" only ran in the last hour.
    for index in range(12):
        minutes(store, f"steady_{index:02d}", NOW - 20 * 3600, 60, ms=100 - index)
    minutes(store, "hour_heavy", NOW - 1800, 30, ms=50)
    # Heavy two days ago: history before the window never counts.
    minutes(store, "old_giant", NOW - 50 * 3600, 60, ms=100_000)
    minutes(store, "trickle", NOW - 20 * 3600, 1, calls=1, ms=0.01)
    rel = relevance(store, "db", NOW)
    assert rel.rule("steady_00") == "top_10_24h"
    assert rel.rule("hour_heavy") == "top_10_1h"
    # Ranked 11th and 12th over the day, but each is more than 2% of it.
    assert rel.rule("steady_11") == "share_24h"
    assert rel.rule("old_giant") is None and rel.rule("trickle") is None
    evidence = rel.evidence("hour_heavy")
    assert evidence["relevance_rule"] == "top_10_1h" and evidence["load_rank_1h"] == 1


def test_share_rule_applies_to_the_last_hour():
    day = LoadWindow.of({f"q{index}": 100.0 for index in range(20)})
    hour = LoadWindow.of({**{f"q{index}": 100.0 for index in range(10)}, "late": 60.0})
    rel = Relevance(day, hour)
    assert rel.rule("late") == "share_1h"


def test_system_queries_never_rank(store):
    minutes(store, "rdst_own", NOW - 600, 10, ms=1000)
    minutes(store, "app", NOW - 600, 10, ms=1)
    store._write(lambda conn: conn.execute("INSERT INTO system_query VALUES ('db','rdst_own')"))
    rel = relevance(store, "db", NOW)
    assert rel.day.rank == {"app": 1}


# Delivery levels

class Everything(Relevance):
    def rule(self, key):
        return "top_10_24h"


@pytest.mark.parametrize("kind,attention,level", [
    ("latency_regression", "notify", "notify"),
    ("plan_change", "inbox", "inbox"),
    ("traffic_spike", "notify", "inbox"),
    ("query_stopped_running", "notify", "inbox"),
    ("slow_creep", "notify", "digest"),
    ("query_improved", "inbox", "digest"),
])
def test_delivery_levels_for_relevant_queries(kind, attention, level):
    assert change_delivery(kind, attention, "q", Everything(), learning=False) == level


def test_irrelevant_queries_and_the_learning_period_go_to_the_digest():
    nothing = Relevance()
    assert change_delivery("latency_regression", "notify", "q", nothing,
                           learning=False) == "digest"
    assert change_delivery("latency_regression", "notify", "q", Everything(),
                           learning=True) == "digest"
    assert change_delivery("rollback_or_deadlock_spike", "notify", None, nothing,
                           learning=True) == "digest"
    assert change_delivery("rollback_or_deadlock_spike", "notify", None, nothing,
                           learning=False) == "notify"


def test_new_query_notifies_only_after_two_top_10_checks():
    rel = Everything()
    assert change_delivery("new_heavy_query", "notify", "q", rel, learning=False,
                           top_checks=1) == "digest"
    assert change_delivery("new_heavy_query", "notify", "q", rel, learning=False,
                           top_checks=2) == "notify"
    members = [{"query_key": "a", "kind": "new_heavy_query", "attention": "notify"},
               {"query_key": "b", "kind": "query_stopped_running", "attention": "notify"}]
    assert group_delivery(members, rel, learning=False) == "inbox"


def test_reconcile_promotes_a_new_query_that_stays_in_the_top_10(store):
    minutes(store, "fresh", NOW - 600, 10, ms=500)
    item = store.upsert_item("db", "new_heavy_query", "fresh", .8, "New heavy query",
                             {"query_key": "fresh", "attention": "notify", "top_checks": 1,
                              "top_check_ts": NOW - 300}, NOW - 300, delivery="digest")
    assert not [event for event in store.inbox_events() if event["kind"] == "notified"]
    reconcile_delivery(store, "db", NOW, relevance(store, "db", NOW), learning=False)
    promoted = store.inbox()[0]
    assert (promoted["id"], promoted["delivery"]) == (item["id"], "notify")
    assert [event["kind"] for event in store.inbox_events()][-1] == "notified"


def test_reconcile_resets_the_count_when_a_new_query_drops_out(store):
    store.upsert_item("db", "new_heavy_query", "gone", .8, "New heavy query",
                      {"query_key": "gone", "attention": "notify", "top_checks": 1,
                       "top_check_ts": NOW - 300}, NOW - 300, delivery="digest")
    reconcile_delivery(store, "db", NOW, Relevance(), learning=False)
    item = store.inbox()[0]
    assert item["delivery"] == "digest"
    assert json.loads(item["body_json"])["top_checks"] == 0


# Learning period

def test_learning_period_runs_a_day_from_the_first_reading(store, monkeypatch):
    monkeypatch.delenv("RDST_MONITOR_LEARNING_S", raising=False)
    assert not is_learning(store, "db", NOW)
    database_minutes(store, NOW - 3600, 5)
    assert is_learning(store, "db", NOW)
    assert not is_learning(store, "db", NOW - 3600 + 86400)


def test_dev_learning_period_lever_shortens_learning_for_one_database(store, monkeypatch):
    monkeypatch.delenv("RDST_MONITOR_LEARNING_S", raising=False)
    database_minutes(store, NOW - 3600, 5)
    assert store.settings("db")["dev_learning_period_s"] is None
    store.set_settings("db", {"dev_learning_period_s": 1800})
    assert store.settings("db")["dev_learning_period_s"] == 1800
    assert not is_learning(store, "db", NOW)
    assert is_learning(store, "db", NOW - 3600 + 1799)
    store._write(lambda conn: conn.execute(
        "INSERT INTO db_series (target,res,bucket_start,calls,total_ms) VALUES ('other',60,?,10,10)",
        (NOW - 3600,)))
    assert is_learning(store, "other", NOW)
    with pytest.raises(ValueError):
        store.set_settings("db", {"dev_learning_period_s": 86401})


def test_first_scan_files_no_change_items_in_the_inbox(store, monkeypatch):
    monkeypatch.delenv("RDST_MONITOR_LEARNING_S", raising=False)
    database_minutes(store, NOW - 3900, 65)
    minutes(store, "q", NOW - 3900, 65, ms=1)
    minutes(store, "q", NOW - 300, 5, ms=9)

    class Jev:
        def attention(self, **request):
            return {"request_id": request["request_id"], "event_scope": "separate",
                    "queries": [{"query_key": query["query_key"], "attention": "notify",
                                 "kind": "latency_regression", "severity": .9}
                                for query in request["queries"]],
                    "open_items": []}

    row = {"query_key": "q", "calls": 50, "total_ms": 450, "mean_ms": 9, "qps": 1 / 6,
           "db_time_share": 1.0, "reason": "mover",
           "baseline": {"1h": {"calls": 600, "total_ms": 600, "mean_ms": 1, "qps": 1 / 6,
                               "db_time_share": 1.0}, "1d": None, "7d": None},
           "ratios": {"1h": {"mean_ms": 9, "qps": 1, "db_time_share": 1}}}
    AttentionJudge(store, client=Jev(), clock=lambda: NOW)(
        [row], {"target": "db", "measured": [row], "database": {}})
    item = store.inbox()[0]
    assert (item["type"], item["delivery"]) == ("latency_regression", "digest")
    assert not [event for event in store.inbox_events() if event["kind"] == "notified"]


# Database-wide items

def test_rollback_item_needs_a_measured_spike_and_closes_on_measurements(store, monkeypatch):
    monkeypatch.setenv("RDST_MONITOR_LEARNING_S", "0")
    judge = AttentionJudge(store, client=None, clock=lambda: NOW)
    verdict = {"event_scope": "database_wide", "open_items": [], "queries": [
        {"query_key": "q", "kind": "rollback_or_deadlock_spike", "attention": "notify",
         "severity": .8}]}
    rows = [{"query_key": "q", "calls": 10, "mean_ms": 1, "qps": 1, "db_time_share": .5,
             "baseline": {"1h": {"mean_ms": 1, "qps": 1}}}]
    # A flat zero rate is no spike: nothing is filed.
    database_minutes(store, NOW - 3900, 65, rollbacks=0)
    judge._file("db", NOW, rows, {"target_numbers": {"rollbacks": 0}}, verdict)
    assert store.inbox() == []
    # 0.5 a minute for an hour, then 6 a minute: filed against the database with the rates.
    database_minutes(store, NOW - 3900, 60, rollbacks=0)
    store._write(lambda conn: conn.execute(
        "UPDATE db_series SET xact_rollback=CASE WHEN bucket_start % 120 = 0 THEN 1 ELSE 0 END "
        "WHERE bucket_start<?", (NOW - 300,)))
    database_minutes(store, NOW - 300, 5, rollbacks=6)
    judge._file("db", NOW, rows, {"target_numbers": {"rollbacks": 6}}, verdict)
    item = store.inbox()[0]
    assert (item["type"], item["subject"], item["delivery"]) == (
        "rollback_or_deadlock_spike", "database", "notify")
    numbers = json.loads(item["body_json"])["numbers"]
    assert numbers["rollbacks_per_min"] == 6 and numbers["rollbacks_per_min_before"] == 0.5
    from features.monitor.presentation import inbox_summary
    assert inbox_summary(item, json.loads(item["body_json"]), {}) == (
        "Rollbacks 0.5 -> 6.0 a minute.")
    # Three cycles back at the old rate close it, with the measurements as the reason.
    for cycle in range(1, 4):
        ts = NOW + cycle * 300
        database_minutes(store, ts - 300, 5, rollbacks=0)
        reconcile_delivery(store, "db", ts, Relevance(), learning=False)
    closed = store.inbox()[0]
    assert (closed["state"], closed["resolved_by"]) == ("resolved", "code")
    assert json.loads(closed["body_json"])["resolution"]["after"]["rollbacks_per_min"] == 0


# Push budget

def test_push_budget_defaults_to_three_and_is_a_lever(store):
    for index in range(5):
        store.upsert_item("db", "stuck_query", f"q{index}", .9, "Stuck query", {}, NOW + index)
    events = store.inbox_events()
    assert [event["kind"] for event in events].count("notified") == 3
    assert [json.loads(event["detail_json"]) for event in events
            if event["kind"] == "updated"] == [{"push_skipped": "daily_limit"}] * 2
    assert store.settings("db")["push_daily_cap"] == 3
    store.set_settings("db", {"push_daily_cap": 5})
    store.upsert_item("db", "stuck_query", "q9", .9, "Stuck query", {}, NOW + 9)
    assert [event["kind"] for event in store.inbox_events()].count("notified") == 4
    with pytest.raises(ValueError):
        store.set_settings("db", {"push_daily_cap": 21})


def test_digest_and_inbox_levels_never_push(store):
    store.upsert_item("db", "traffic_spike", "q", .7, "Traffic", {}, NOW, delivery="inbox")
    store.upsert_item("db", "slow_creep", "q", .7, "Creep", {}, NOW, delivery="digest")
    assert not [event for event in store.inbox_events() if event["kind"] == "notified"]


def test_an_open_item_only_moves_up(store):
    store.upsert_item("db", "traffic_spike", "q", .7, "Traffic", {"n": 1}, NOW, delivery="inbox")
    store.upsert_item("db", "traffic_spike", "q", .7, "Traffic", {"n": 2}, NOW + 1,
                      delivery="digest")
    assert store.inbox()[0]["delivery"] == "inbox"


# Monitoring issues

def test_sign_in_issue_clears_once_the_session_works(store, monkeypatch):
    class Config:
        def load(self):
            pass

        def get(self, target):
            return {}

    monkeypatch.setattr("shared.config.targets.TargetsConfig", Config)
    monkeypatch.setattr(monitor_loop.query_discovery, "collector_for",
                        lambda target: SimpleNamespace(_snapshot={}))
    store.upsert_item("db", "monitoring_issue", "jev_login", 1.0, "Sign in to resume alerts",
                      {"target": "db"}, NOW - 3 * 86400)
    active = monitor_loop.MonitorLoop(store, clock=lambda: NOW, index_reader=lambda target: [])
    monkeypatch.setattr(monitor_loop, "signed_in", lambda: False)
    active._monitoring_issues("db", NOW, False)
    assert store.inbox()[0]["state"] == "new"
    monkeypatch.setattr(monitor_loop, "signed_in", lambda: True)
    active._monitoring_issues("db", NOW + 60, False)
    item = store.inbox()[0]
    assert (item["state"], item["resolved_by"]) == ("resolved", "code")


# Migration

def test_schema_15_retires_index_attribution_and_empty_database_items(tmp_path):
    path = tmp_path / "monitor.db"
    store = MonitorStore(path)
    store.ensure_open()
    for type, subject, body in (
            ("fix_confirmed", "q:idx", {"query_key": "q"}),
            ("fix_no_effect", "q:idx2", {"query_key": "q"}),
            ("one_fix_several_queries", "[]", {}),
            ("rollback_or_deadlock_spike", "081043271dee",
             {"numbers": {"rollbacks": 0, "deadlocks": 0}}),
            ("rollback_or_deadlock_spike", "database",
             {"numbers": {"rollbacks": 0, "deadlocks": 0}}),
            ("latency_regression", "q", {"query_key": "q"})):
        store.upsert_item("db", type, subject, .8, type, body, NOW)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE inbox_item SET delivery='inbox'")
        conn.execute("PRAGMA user_version=14")
    migrated = MonitorStore(path)
    rows = {(row["type"], row["subject"]): row for row in migrated.inbox()}
    assert rows[("latency_regression", "q")]["state"] == "new"
    retired = [row for key, row in rows.items() if key != ("latency_regression", "q")]
    assert {(row["state"], row["resolved_by"], row["delivery"]) for row in retired} == {
        ("resolved", "code", "digest")}
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


# Heavy is not a change

def test_a_chronically_heavy_query_is_never_filed_as_new(store, monkeypatch):
    monkeypatch.setenv("RDST_MONITOR_LEARNING_S", "0")
    store._write(lambda conn: conn.executemany(
        "INSERT INTO query_text VALUES (?,?,?,?,?)",
        [("db", "old_heavy", "SELECT * FROM orders", NOW - 3 * 86400, NOW),
         ("db", "brand_new", "SELECT * FROM carts", NOW - 600, NOW)]))
    rows = [{"query_key": key, "calls": 300, "mean_ms": 50, "qps": 1, "db_time_share": .4,
             "baseline": {"1h": None, "1d": None, "7d": None}}
            for key in ("old_heavy", "brand_new")]
    verdict = {"event_scope": "separate", "open_items": [], "queries": [
        {"query_key": key, "kind": "new_heavy_query", "attention": "notify", "severity": .8}
        for key in ("old_heavy", "brand_new")]}
    AttentionJudge(store, client=None, clock=lambda: NOW)._file(
        "db", NOW, rows, {"target_numbers": {}}, verdict, rel=Everything(), learning=False)
    assert [item["subject"] for item in store.inbox()] == ["brand_new"]


def test_legacy_items_go_through_the_relevance_gate_once(store):
    minutes(store, "heavy", NOW - 600, 10, ms=500)
    stopped = store.upsert_item("db", "query_stopped_running", "trickle", .7, "Stopped",
                                {"query_key": "trickle"}, NOW - 900)
    slow = store.upsert_item("db", "latency_regression", "heavy", .8, "Slower",
                             {"query_key": "heavy"}, NOW - 900)
    reconcile_delivery(store, "db", NOW, relevance(store, "db", NOW), learning=False)
    rows = {row["subject"]: row for row in store.inbox()}
    assert rows["trickle"]["delivery"] == "digest"
    assert rows["heavy"]["delivery"] == "notify"
    assert json.loads(rows["heavy"]["body_json"])["relevance_rule"] == "top_10_24h"
    assert {stopped["id"], slow["id"]} == {row["id"] for row in rows.values()}


def test_only_the_latest_daily_report_stays_open(store):
    from features.monitor import report as reports

    for day, ts in (("2026-09-26", NOW - 2 * 86400), ("2026-09-27", NOW - 86400)):
        store.upsert_item("db", "daily_summary", day, .4, "Daily summary",
                          {"summary": "x"}, ts, delivery="inbox")
    reconcile_delivery(store, "db", NOW, Relevance(), learning=False)
    assert [item["subject"] for item in store.inbox("new")] == ["2026-09-27"]
    reports._file_report_items(store, "db", 9, NOW, {"summary": "y"}, "nightly")
    open_items = [item for item in store.inbox() if item["state"] == "new"]
    assert len(open_items) == 1 and json.loads(open_items[0]["body_json"])["report_id"] == 9


def test_summaries_lead_with_the_finding():
    from features.monitor.presentation import inbox_summary, lead_with_finding

    text = "On target 'localdev' (PostgreSQL), measured load is dominated by two queries."
    assert lead_with_finding(text) == "Measured load is dominated by two queries."
    assert lead_with_finding("Two queries carry the load.") == "Two queries carry the load."
    item = {"type": "daily_summary", "subject": "d", "title": "Checkup ready",
            "state": "new"}
    assert inbox_summary(item, {"summary": text}, {}).startswith("Measured load")


# A first run's queries are the existing workload, not new

def _new_heavy_verdict(keys):
    return {"event_scope": "separate", "open_items": [], "queries": [
        {"query_key": key, "kind": "new_heavy_query", "attention": "notify", "severity": .8}
        for key in keys]}


def _heavy_rows(keys):
    return [{"query_key": key, "calls": 300, "mean_ms": 50, "qps": 1, "db_time_share": .4,
             "baseline": {"1h": None, "1d": None, "7d": None}} for key in keys]


def _seen(store, *entries):
    store._write(lambda conn: conn.executemany(
        "INSERT INTO query_text VALUES (?,?,?,?,?)",
        [("db", key, f"SELECT * FROM t{index}", at, at)
         for index, (key, at) in enumerate(entries)]))


@pytest.mark.parametrize("learning", [True, False])
def test_queries_found_in_the_first_hour_never_file_as_new(store, learning):
    enabled = NOW - 60
    store.start_first_hour("db", enabled)
    _seen(store, *[(f"q{index}", enabled + 10) for index in range(11)])
    keys = [f"q{index}" for index in range(11)]
    AttentionJudge(store, client=None, clock=lambda: NOW)._file(
        "db", NOW, _heavy_rows(keys), {"target_numbers": {}}, _new_heavy_verdict(keys),
        rel=Everything(), learning=learning)
    assert store.inbox() == []


def test_a_query_found_after_the_first_hour_still_files_as_new(store):
    enabled = NOW - 2 * 3600
    store.start_first_hour("db", enabled)
    _seen(store, ("existing", enabled + 10), ("late", enabled + 1800), ("fresh", NOW - 300))
    keys = ["existing", "late", "fresh"]
    AttentionJudge(store, client=None, clock=lambda: NOW)._file(
        "db", NOW, _heavy_rows(keys), {"target_numbers": {}}, _new_heavy_verdict(keys),
        rel=Everything(), learning=False)
    assert [item["subject"] for item in store.inbox()] == ["fresh"]


def test_a_database_with_earlier_history_files_new_queries_in_the_first_hour(store):
    enabled = NOW - 600
    store._write(lambda conn: conn.execute(
        "INSERT INTO db_series (target,res,bucket_start,calls,total_ms) VALUES ('db',60,?,10,10)",
        (enabled - 2 * 86400,)))
    store.start_first_hour("db", enabled)
    _seen(store, ("fresh", NOW - 120))
    AttentionJudge(store, client=None, clock=lambda: NOW)._file(
        "db", NOW, _heavy_rows(["fresh"]), {"target_numbers": {}}, _new_heavy_verdict(["fresh"]),
        rel=Everything(), learning=False)
    assert [item["subject"] for item in store.inbox()] == ["fresh"]


def test_a_lost_connection_files_during_the_first_hour(store):
    from features.monitor.connection import ConnectionWatch

    store.set_settings("db", {"enabled": True})
    store.start_first_hour("db", NOW - 120)
    watch = ConnectionWatch(store, clock=lambda: NOW)
    watch.collection("db", False, NOW - 60, "connection refused")
    watch.collection("db", False, NOW, "connection refused")
    filed = [item for item in store.inbox() if item["subject"] == "lost_connection"]
    assert len(filed) == 1 and filed[0]["state"] == "new"

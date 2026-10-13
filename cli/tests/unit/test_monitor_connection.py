"""Lost connection: debounced over three cycles, filed in the Inbox, pushed once it has
lasted an hour, never capped, closed with the gap it left."""

from __future__ import annotations

import json
import time

import pytest

from features.monitor.api import routes
from features.monitor.connection import (
    LOST_AFTER_SECONDS,
    PUSH_AFTER_SECONDS,
    ConnectionWatch,
    resumed_message,
    span_text,
)
from shared.monitor.monitor_store import MonitorStore, notify_event


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


# 2026-09-30 09:55 UTC
T0 = 1_790_762_100


def item(store: MonitorStore) -> dict:
    rows = store.read("SELECT * FROM inbox_item WHERE type='monitoring_issue' "
                      "AND subject='lost_connection'")
    return rows[0] if rows else {}


def pushes(store: MonitorStore, item_id: int) -> int:
    return store.read("SELECT COUNT(*) n FROM inbox_event WHERE item_id=? AND kind='notified'",
                      (item_id,))[0]["n"]


def monitored(tmp_path) -> MonitorStore:
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    store.record("db", T0 - 60, [], {}, [], {"calls": 1, "total_ms": 1})
    return store


def test_two_failures_are_a_blip_and_the_third_files_an_inbox_item_that_pushes_uncapped(tmp_path):
    store = monitored(tmp_path)
    store.set_settings("db", {"push_daily_cap": 0})
    now = [T0]
    watch = ConnectionWatch(store, clock=lambda: now[0])
    watch.collection("db", False, T0, "connection refused")
    watch.collection("db", False, T0 + 60, "connection refused")
    assert item(store) == {}
    watch.collection("db", False, T0 + 120, "connection refused")
    filed = item(store)
    assert filed["state"] == "new" and filed["delivery"] == "inbox"
    assert pushes(store, filed["id"]) == 0
    body = json.loads(filed["body_json"])
    assert body["gap_from"] == T0 - 60 and body["failed_since"] == T0
    assert body["message"] == ("RDST can't reach db since 9:54 AM. Monitoring is paused until it's "
                               "back. The server refused the connection.")
    # Further failures update nothing; the push comes once the outage is an hour old, and
    # the daily budget never holds it back.
    watch.collection("db", False, T0 + 180)
    assert pushes(store, filed["id"]) == 0
    now[0] = T0 + PUSH_AFTER_SECONDS
    watch.tick()
    assert item(store)["delivery"] == "notify" and pushes(store, filed["id"]) == 1
    watch.collection("db", False, T0 + PUSH_AFTER_SECONDS + 60)
    assert pushes(store, filed["id"]) == 1
    assert store.read("SELECT COUNT(*) n FROM monitor_pulse WHERE kind='collect' AND ok=0"
                      )[0]["n"] == 5


def test_a_single_old_failure_files_after_three_minutes_and_pushes_after_an_hour(tmp_path):
    store = monitored(tmp_path)
    now = [T0]
    watch = ConnectionWatch(store, clock=lambda: now[0])
    watch.collection("db", False)
    now[0] += LOST_AFTER_SECONDS - 1
    watch.tick()
    assert item(store) == {}
    now[0] += 1
    watch.tick()
    assert item(store)["state"] == "new" and item(store)["delivery"] == "inbox"
    assert pushes(store, item(store)["id"]) == 0
    now[0] = T0 + PUSH_AFTER_SECONDS - 1
    watch.tick()
    assert pushes(store, item(store)["id"]) == 0
    now[0] += 1
    watch.tick()
    assert pushes(store, item(store)["id"]) == 1


@pytest.mark.parametrize("recovery_after", [30, 119])
def test_recovery_before_deadline_cancels_filing(tmp_path, recovery_after):
    store = monitored(tmp_path)
    now = [T0]
    watch = ConnectionWatch(store, clock=lambda: now[0])
    watch.collection("db", False)
    now[0] += recovery_after
    watch.collection("db", True)
    now[0] = T0 + LOST_AFTER_SECONDS
    watch.tick()
    # A delayed index result and a pending filing both belong to the recovered streak.
    watch.observe("db", False, T0)
    watch._file("db", now[0], T0, "connection refused")
    assert item(store) == {}


def test_recovery_after_filing_only_delivers_resumed_on_replay(tmp_path):
    store = monitored(tmp_path)
    store.set_settings("db", {"notify_events": {"database_recovered": True}})
    now = [T0]
    watch = ConnectionWatch(store, clock=lambda: now[0])
    watch.collection("db", False)
    for _ in range(2):
        now[0] += 60
        watch.collection("db", False)
    filed = item(store)
    assert filed["state"] == "new" and pushes(store, filed["id"]) == 0
    now[0] += 10
    watch.collection("db", True)
    watch.observe("db", False, T0 + 60)
    watch._file("db", now[0], T0, None)
    now[0] += PUSH_AFTER_SECONDS
    watch.tick()
    # The only push is the recovery's; a replayed failure neither re-files nor pushes.
    assert pushes(store, filed["id"]) == 1
    events = [row for row in store.inbox_events() if row["kind"] == "notified"]
    assert len(events) == 1
    assert all(row["state"] == "resolved" for row in events)
    assert all(json.loads(row["body_json"])["message"].startswith("Monitoring resumed")
               for row in events)


def test_disabled_outage_does_not_file_at_deadline(tmp_path):
    store = monitored(tmp_path)
    now = [T0]
    watch = ConnectionWatch(store, clock=lambda: now[0])
    watch.collection("db", False)
    store.set_settings("db", {"enabled": False})
    now[0] += LOST_AFTER_SECONDS
    watch.tick()
    assert item(store) == {}


def test_recovery_closes_with_the_gap_and_a_new_loss_pushes_after_an_hour(tmp_path):
    store = monitored(tmp_path)
    woken = []
    now = [T0]
    watch = ConnectionWatch(store, clock=lambda: now[0], on_recovered=woken.append)
    for ts in (T0, T0 + 60, T0 + 120):
        watch.collection("db", False, ts)
    resumed = T0 + 80 * 60  # 11:15 AM
    watch.collection("db", True, resumed)
    closed = item(store)
    assert closed["state"] == "resolved" and closed["resolved_by"] == "code"
    body = json.loads(closed["body_json"])
    assert body["message"] == "Monitoring resumed at 11:15 AM. No readings from 9:54 to 11:15 AM."
    assert (body["gap_from"], body["gap_to"]) == (T0 - 60, resumed)
    assert pushes(store, closed["id"]) == 0  # recovery pushes only when switched on
    assert woken == ["db"]
    from features.monitor.api.views import inbox_items
    presented = inbox_items(store, [closed])[0]
    assert presented["severity_band"] == "recovered"
    assert presented["summary"].startswith("Monitoring resumed at 11:15 AM.")
    assert presented["link"]["kind"] == "database"
    assert routes._outages(store, "db") == [
        {"start": T0 - 60, "end": resumed, "cause": "unreachable", "item_id": closed["id"]}]

    store.set_settings("db", {"notify_events": {"database_recovered": True}})
    for ts in (resumed + 600, resumed + 660, resumed + 720):
        watch.collection("db", False, ts)
    reopened = item(store)
    assert reopened["state"] == "new" and pushes(store, reopened["id"]) == 0
    assert routes._outages(store, "db")[-1]["end"] is None
    now[0] = resumed + 600 + PUSH_AFTER_SECONDS
    watch.tick()
    assert pushes(store, reopened["id"]) == 1
    watch.collection("db", True, now[0] + 60)
    assert pushes(store, reopened["id"]) == 2


def test_unmonitored_targets_never_alert(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    watch = ConnectionWatch(store)
    for ts in (T0, T0 + 60, T0 + 300):
        watch.collection("db", False, ts)
    assert item(store) == {}


def test_outages_do_not_use_the_daily_push_budget(tmp_path):
    store = monitored(tmp_path)
    store.set_settings("db", {"push_daily_cap": 1})
    now = [T0]
    watch = ConnectionWatch(store, clock=lambda: now[0])
    for ts in (T0, T0 + 60, T0 + 120):
        watch.collection("db", False, ts)
    now[0] = T0 + PUSH_AFTER_SECONDS
    watch.tick()
    assert pushes(store, item(store)["id"]) == 1
    regression = store.upsert_item("db", "latency_regression", "q1", 0.9, "Slower", {}, T0 + 90)
    assert pushes(store, regression["id"]) == 1
    second = store.upsert_item("db", "latency_regression", "q2", 0.9, "Slower", {}, T0 + 95)
    assert pushes(store, second["id"]) == 0


def test_switched_off_events_still_file_but_never_push(tmp_path):
    store = monitored(tmp_path)
    settings = store.set_settings("db", {"notify_events": {"query_changes": False}})
    assert settings["notify_events"]["query_changes"] is False
    assert settings["notify_events"]["database_unreachable"] is True
    filed = store.upsert_item("db", "latency_regression", "q1", 0.9, "Slower", {}, T0)
    assert filed["state"] == "new" and pushes(store, filed["id"]) == 0
    skipped = store.read("SELECT detail_json FROM inbox_event WHERE item_id=? AND kind='updated'",
                         (filed["id"],))
    assert json.loads(skipped[-1]["detail_json"]) == {"push_skipped": "event_off"}
    # A partial update keeps the other switches.
    settings = store.set_settings("db", {"notify_events": {"stuck_query": False}})
    assert settings["notify_events"]["query_changes"] is False
    with pytest.raises(ValueError):
        store.set_settings("db", {"notify_events": {"bogus": True}})


def test_event_groups():
    assert notify_event("monitoring_issue", "lost_connection") == "database_unreachable"
    assert notify_event("monitoring_issue", "jev_login") == "monitoring_setup"
    assert notify_event("stuck_query", "q") == "stuck_query"
    assert notify_event("connections_near_max", "database") == "database_events"
    assert notify_event("many_queries_changed", "x") == "query_changes"


def test_gap_wording():
    assert span_text(T0, T0 + 3600) == "9:55 to 10:55 AM"
    assert span_text(T0 + 2 * 3600, T0 + 3 * 3600) == "11:55 AM to 12:55 PM"
    assert resumed_message(T0, T0 + 600) == (
        "Monitoring resumed at 10:05 AM. No readings from 9:55 to 10:05 AM.")
    assert span_text(T0, T0 + 2 * 86400) == "Wed 9:55 AM to Fri 9:55 AM"


def test_a_missing_password_is_never_a_lost_connection(tmp_path, monkeypatch):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get",
                        lambda self, name: {"password_env": "RDST_DB_PASSWORD"})
    monkeypatch.delenv("RDST_DB_PASSWORD", raising=False)
    watch = ConnectionWatch(store, clock=lambda: T0)
    for offset in (0, 60, 180):
        watch.observe("db", False, T0 + offset, "password authentication failed")
    watch.tick()
    assert not store.inbox()


def test_a_connection_problem_says_why_and_what_to_check():
    from features.monitor.connection import connection_problem

    assert connection_problem('FATAL: password authentication failed for user "x"') == {
        "reason": "The database rejected the password.",
        "fix": "Update the password for this database in Settings."}
    refused = connection_problem("connection to server at 127.0.0.1 failed: Connection refused")
    assert refused["fix"] == "Check that the database is running and listening on that port."
    assert connection_problem("something odd")["reason"] == "RDST couldn't connect."


def test_after_a_connection_change_the_first_failure_files_at_once(tmp_path):
    store = monitored(tmp_path)
    watch = ConnectionWatch(store, clock=lambda: T0)
    # Readings with the old settings were fine; the new port refuses.
    watch.collection("db", True, T0 - 60)
    watch.settings_changed("db")
    watch.collection("db", False, T0, "connection refused")
    filed = item(store)
    assert filed["state"] == "new"
    assert json.loads(filed["body_json"])["failed_since"] == T0
    # A good reading with the new settings closes it, and later blips are debounced again.
    watch.collection("db", True, T0 + 60)
    assert item(store)["state"] == "resolved"
    watch.collection("db", False, T0 + 120, "connection refused")
    assert item(store)["state"] == "resolved"


def test_without_a_change_a_single_failure_is_still_a_blip(tmp_path):
    store = monitored(tmp_path)
    watch = ConnectionWatch(store, clock=lambda: T0)
    watch.collection("db", True, T0 - 60)
    watch.collection("db", False, T0, "connection refused")
    assert item(store) == {}


def test_a_connection_change_reaches_every_reader(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from features.monitor import loop as monitor_loop

    store = monitored(tmp_path)
    calls = []
    sampler = SimpleNamespace(reconnect=lambda target: calls.append(("sampler", target)))
    scheduler = SimpleNamespace(collect_soon=lambda target: calls.append(("scheduler", target)))
    active = monitor_loop.MonitorLoop(store, clock=lambda: T0, index_reader=lambda target: [],
                                      sampler=sampler, interval=300)
    monkeypatch.setattr(monitor_loop, "_active_loop", active)
    monkeypatch.setattr(monitor_loop.query_discovery, "reconnect",
                        lambda target: calls.append(("collector", target)))
    active._setup_failure["db"] = {"id": "pg_stat_statements"}
    active.watch.collection("db", True, T0 - 60)

    monitor_loop.connection_changed("db", scheduler)

    assert calls == [("collector", "db"), ("sampler", "db"), ("scheduler", "db")]
    assert "db" not in active._setup_failure and active._wake.is_set()
    active.watch.collection("db", False, T0, "connection refused")
    assert item(store)["state"] == "new"

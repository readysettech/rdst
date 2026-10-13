import json

from shared.monitor.monitor_store import MonitorStore


def test_inbox_state_machine_cap_and_dismissed_return(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"quiet_hours": {"from_hour": 0, "to_hour": 0}})
    ts = 1000
    item = store.upsert_item("db", "stuck_query", "q", 0.5, "Stuck", {"n": 1}, ts,
                             notify_cap=1)
    assert item["state"] == "new"
    assert [e["kind"] for e in store.inbox_events()] == ["created", "notified"]
    store.action_item(item["id"], "seen", ts + 1)
    assert store.inbox("seen")[0]["seen_count"] == 1
    store.action_item(item["id"], "snooze", ts + 2, snooze_until=ts + 50)
    assert store.inbox("snoozed")[0]["snoozed_until"] == ts + 50
    store.upsert_item("db", "stuck_query", "q", 0.5, "Stuck", {"n": 2}, ts + 40, notify_cap=1)
    assert store.inbox("snoozed")
    store.upsert_item("db", "stuck_query", "q", 0.5, "Stuck", {"n": 3}, ts + 50, notify_cap=1)
    assert store.inbox("new")[0]["reopened_count"] == 1
    store.action_item(item["id"], "dismiss", ts + 60)
    # Dismissed and still happening: silent, whatever the severity says.
    store.upsert_item("db", "stuck_query", "q", 0.9, "Stuck", {"n": 4}, ts + 70, notify_cap=1)
    assert store.inbox("dismissed")
    # A measured further 2x is a new episode.
    store.upsert_item("db", "stuck_query", "q", 0.8, "Stuck", {"n": 5}, ts + 80, notify_cap=1,
                      materially_worse=True)
    assert store.inbox("new")[0]["reopened_count"] == 2
    store.action_item(item["id"], "dismiss", ts + 90)
    store.upsert_item("db", "stuck_query", "q", 0.8, "Stuck", {"n": 6}, ts + 91, notify_cap=1)
    assert store.inbox("dismissed")
    # A week later the same episode is still silent.
    store.upsert_item("db", "stuck_query", "q", 0.8, "Stuck", {"n": 7}, ts + 90 + 7 * 86400, notify_cap=1)
    assert store.inbox("dismissed")[0]["reopened_count"] == 2
    store.action_item(item["id"], "resolve", ts + 7 * 86400 + 100, resolved_by="code")
    assert store.inbox("dismissed")
    assert len([e for e in store.inbox_events() if e["kind"] == "notified"]) == 1


def test_notification_cap_per_target_per_day(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"quiet_hours": {"from_hour": 0, "to_hour": 0}})
    for i in range(3):
        store.upsert_item("db", "monitoring_issue", str(i), 1, "Issue", {}, 100 + i,
                          notify_cap=2)
    assert len([e for e in store.inbox_events() if e["kind"] == "notified"]) == 2


def test_material_group_update_notifies_within_cap(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"quiet_hours": {"from_hour": 0, "to_hour": 0}})
    item = store.upsert_item("db", "many_queries_changed", "database", .9,
                             "Many queries changed", {"query_keys": ["a", "b"]},
                             100, notify_cap=2)
    store.upsert_item("db", "many_queries_changed", "database", .9,
                      "Many queries changed", {"query_keys": ["a", "b", "c"]},
                      200, notify_cap=2, notify_on_update=True)
    store.upsert_item("db", "many_queries_changed", "database", .9,
                      "Many queries changed", {"query_keys": ["a", "b", "c", "d"]},
                      300, notify_cap=2, notify_on_update=True)
    events = [event for event in store.inbox_events() if event["item_id"] == item["id"]]
    # Over the cap the push is skipped and recorded, so the digest can list it.
    assert [event["kind"] for event in events] == [
        "created", "notified", "updated", "notified", "updated", "updated"]
    assert json.loads(events[-1]["detail_json"]) == {"push_skipped": "daily_limit"}


def test_user_resolved_item_stays_resolved_until_worse(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    item = store.upsert_item("db", "stuck_query", "q", 0.5, "Stuck", {}, 100)
    store.action_item(item["id"], "resolve", 110)
    store.upsert_item("db", "stuck_query", "q", 0.5, "Stuck", {"detail": 1}, 120)
    resolved = store.inbox("resolved")[0]
    assert resolved["resolved_at"] == 110
    assert resolved["resolved_by"] == "user"
    store.upsert_item("db", "stuck_query", "q", 0.8, "Stuck", {"detail": 2}, 130)
    assert store.inbox("resolved")
    store.upsert_item("db", "stuck_query", "q", 0.8, "Stuck", {"detail": 3}, 140,
                      materially_worse=True)
    assert store.inbox("new")[0]["reopened_count"] == 1


def test_monitor_event_logs_prune_old_entries_but_keep_item_origin(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    item = store.upsert_item("db", "stuck_query", "q", 0.5, "Stuck", {}, 100)
    store.record_judge_run("db", 100, [], outcome="empty")
    later = 100 + 31 * 86400
    store.upsert_item("db", "stuck_query", "q", 0.8, "Stuck", {"new": True}, later)
    store.record_judge_run("db", later, [], outcome="empty")
    store.rollup("db", later)
    assert [row["ts"] for row in store.read("SELECT ts FROM judge_run ORDER BY ts")] == [later]
    events = store.read("SELECT ts,kind FROM inbox_event WHERE item_id=? ORDER BY id", (item["id"],))
    assert events[0] == {"ts": 100, "kind": "created"}
    assert all(row["ts"] == later for row in events[1:])


def test_sign_in_resolves_sign_in_items_for_every_target(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.upsert_item("a", "monitoring_issue", "jev_login", 1.0, "Sign in to resume alerts",
                      {"target": "a"}, 100)
    store.upsert_item("b", "monitoring_issue", "llm_login", .6, "Sign in", {"target": "b"}, 100)
    store.upsert_item("a", "monitoring_issue", "password", 1.0, "Password", {"target": "a"}, 100)

    assert store.resolve_sign_in_items(200) == 2
    states = {item["subject"]: item["state"] for t in ("a", "b") for item in store.inbox(target=t)}
    assert states == {"jev_login": "resolved", "llm_login": "resolved", "password": "new"}
    assert store.resolve_sign_in_items(300) == 0


def test_history_keeps_who_closed_an_item_across_a_reopen(tmp_path):
    from features.monitor.presentation import item_history

    store = MonitorStore(tmp_path / "monitor.db")
    item = store.upsert_item("db", "monitoring_issue", "q", 0.5, "Issue", {}, 100, notify=False)
    store.action_item(item["id"], "resolve", 110, resolved_by="code")
    store.action_item(item["id"], "reopen", 120)
    row = store.inbox(target="db")[0]
    events = [event for event in store.inbox_events() if event["item_id"] == item["id"]]
    texts = [entry["text"] for entry in item_history(row, {}, events)]
    assert texts[:2] == ["Reopened", "Item closed by RDST"]

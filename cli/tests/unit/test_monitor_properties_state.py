"""Seeded state-machine and index-history checks using temporary SQLite files."""

import json
import random

import pytest

from shared.monitor.monitor_store import MonitorStore


@pytest.mark.parametrize("seed", range(8))
def test_index_change_log_replays_snapshot(seed, tmp_path):
    rng = random.Random(seed)
    store = MonitorStore(tmp_path / "monitor.db")
    store.sync_indexes("db", [], 100)
    expected = {}
    indexes = [{"schema": "public", "table": "t", "index_name": f"idx{i}",
                "definition": f"CREATE INDEX idx{i} ON t (c{i})"} for i in range(8)]
    for step in range(25):
        index = rng.choice(indexes)
        key = (index["schema"], index["table"], index["index_name"])
        if key in expected:
            del expected[key]
        else:
            expected[key] = index["definition"]
        store.sync_indexes("db", [dict(schema=s, table=t, index_name=n, definition=d)
                                  for (s, t, n), d in expected.items()], 101 + step)
    replay = {}
    for change in store.read("SELECT kind,schema,\"table\",index_name,definition "
                             "FROM index_change WHERE target='db' ORDER BY id"):
        key = (change["schema"], change["table"], change["index_name"])
        if change["kind"] == "added":
            replay[key] = change["definition"]
        else:
            assert replay.pop(key) == change["definition"]
    snapshot = {(row["schema"], row["table"], row["index_name"]): row["definition"]
                for row in store.read('SELECT * FROM index_snapshot WHERE target="db"')}
    assert replay == snapshot == expected


@pytest.mark.parametrize("seed", range(8))
def test_inbox_actions_are_logged_unique_and_capped(seed, tmp_path):
    rng = random.Random(seed)
    store = MonitorStore(tmp_path / "monitor.db")
    cap = rng.randrange(1, 5)
    for step in range(30):
        ts = 86400 + step * 60
        subject = f"q{rng.randrange(5)}"
        before = store.read("SELECT id,state FROM inbox_item WHERE target='db' "
                            "AND type='regression' AND subject=?", (subject,))
        events_before = store.read("SELECT COUNT(*) n FROM inbox_event")[0]["n"]
        if before and rng.randrange(2):
            action = rng.choice(("seen", "snooze", "dismiss", "resolve", "reopen"))
            after = store.action_item(before[0]["id"], action, ts,
                                      snooze_until=ts + 3600)
        else:
            after = store.upsert_item("db", "regression", subject,
                                      rng.randrange(1, 10) / 10, "Changed",
                                      {"step": step}, ts, notify_cap=cap)
        events_after = store.read("SELECT COUNT(*) n FROM inbox_event")[0]["n"]
        if not before or after["state"] != before[0]["state"]:
            assert events_after > events_before
        open_rows = store.read("SELECT target,type,subject FROM inbox_item "
                               "WHERE state IN ('new','seen','snoozed')")
        keys = [(row["target"], row["type"], row["subject"]) for row in open_rows]
        assert len(keys) == len(set(keys))
        notified = store.read("SELECT COUNT(*) n FROM inbox_event "
                              "WHERE kind='notified' AND ts>=86400 AND ts<172800")[0]["n"]
        assert notified <= cap


def test_dismissed_episode_stays_silent_while_it_continues(tmp_path):
    """Dismissed + still happening = no notification, at any severity, for any time."""
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"quiet_hours": {"from_hour": 0, "to_hour": 0}})
    item = store.upsert_item("db", "regression", "q", 0.2, "Changed", {}, 100)
    store.action_item(item["id"], "dismiss", 101)
    for ts, severity in ((102, 0.5), (104, 0.8), (101 + 7 * 86400, 1.0)):
        assert store.upsert_item("db", "regression", "q", severity, "Changed",
                                 {"ts": ts}, ts)["state"] == "dismissed"
    assert [row["kind"] for row in store.read(
        "SELECT kind FROM inbox_event WHERE item_id=? ORDER BY id", (item["id"],))] == [
        "created", "notified", "dismissed"]


def test_dismissed_episode_returns_as_a_new_episode_when_2x_worse(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"quiet_hours": {"from_hour": 0, "to_hour": 0}})
    item = store.upsert_item("db", "regression", "q", 0.2, "Changed", {}, 100)
    store.action_item(item["id"], "dismiss", 101)
    returned = store.upsert_item("db", "regression", "q", 0.5, "Changed", {}, 102,
                                 materially_worse=True)
    assert returned["state"] == "new"
    assert json.loads(returned["body_json"])["still_happening"]
    assert store.read("SELECT COUNT(*) n FROM inbox_event WHERE item_id=? AND kind='notified'",
                      (item["id"],))[0]["n"] == 2
    store.action_item(item["id"], "dismiss", 103)
    assert store.upsert_item("db", "regression", "q", 0.8, "Changed", {},
                             104)["state"] == "dismissed"


def test_resolved_by_rdst_then_recurred_is_a_new_episode(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"quiet_hours": {"from_hour": 0, "to_hour": 0}})
    item = store.upsert_item("db", "regression", "q", 0.5, "Changed", {}, 100)
    store.action_item(item["id"], "resolve", 200, resolved_by="code", measured_recovery=True)
    assert store.upsert_item("db", "regression", "q", 0.5, "Changed", {"again": 1},
                             300)["state"] == "new"
    assert store.read("SELECT COUNT(*) n FROM inbox_event WHERE item_id=? AND kind='notified'",
                      (item["id"],))[0]["n"] == 2


def test_muted_episode_never_notifies(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.create_mute_rule(target=None, query_key=None, type="regression", until=None,
                           note=None, now=50)
    item = store.upsert_item("db", "regression", "q", 0.5, "Changed", {}, 100)
    store.action_item(item["id"], "resolve", 200, resolved_by="code", measured_recovery=True)
    store.upsert_item("db", "regression", "q", 0.5, "Changed", {"again": 1}, 300,
                      materially_worse=True)
    assert not store.read("SELECT 1 FROM inbox_event WHERE kind='notified'")

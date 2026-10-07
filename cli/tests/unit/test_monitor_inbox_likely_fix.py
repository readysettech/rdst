"""An Inbox item names the open action that covers one of its queries."""

from __future__ import annotations

from features.monitor.api import views
from features.monitor.api.models import InboxItem
from shared.monitor.monitor_store import MonitorStore

NOW = 2_000_000


def action(key, state, score, title, queries, type="add_index"):
    return {"key": key, "type": type, "state": state, "score": score,
            "payload": {"title": title, "summary": "", "queries": queries}}


def items(store):
    return {item["subject"]: item for item in views.inbox_items(store, store.inbox(target="db"))}


def test_item_shows_the_best_open_action_for_its_queries(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.upsert_item("db", "many_queries_changed", "burst:1", 0.9, "2 queries got slower",
                      {"query_keys": ["aaaaaaaaaaa1", "aaaaaaaaaaa2"]}, NOW - 600)
    store.upsert_item("db", "many_queries_changed", "burst:2", 0.9, "1 query got slower",
                      {"query_keys": ["bbbbbbbbbbb1"]}, NOW - 600)
    store.write_actions("db", NOW, [
        action("idx:low", "open", 0.2, "Add an index on orders(id)", ["aaaaaaaaaaa2"]),
        action("idx:high", "open", 0.8, "Add an index on orders(customer_id)",
               ["aaaaaaaaaaa1", "ccccccccccc1"]),
        action("idx:done", "applied", 0.9, "Add an index on users(email)", ["bbbbbbbbbbb1"]),
        action("idx:snoozed", "snoozed", 0.9, "Rewrite COUNT on users", ["bbbbbbbbbbb1"],
               type="rewrite_query"),
    ])
    high = next(row["id"] for row in store.actions("db") if row["key"] == "idx:high")

    found = items(store)
    assert found["burst:1"]["action"] == {
        "id": high, "title": "Add an index on orders(customer_id)", "type": "add_index"}
    # Only open actions count.
    assert found["burst:2"]["action"] is None
    InboxItem.model_validate(found["burst:1"])


def test_closed_items_and_items_without_queries_have_no_fix(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.upsert_item("db", "many_queries_changed", "burst:1", 0.9, "2 queries got slower",
                      {"query_keys": ["aaaaaaaaaaa1"]}, NOW - 600)
    store.write_actions("db", NOW, [
        action("idx", "open", 0.5, "Add an index on orders(id)", ["aaaaaaaaaaa1"])])
    [row] = store.inbox(target="db")
    closed = views.inbox_items(store, [{**row, "state": "resolved"}])
    assert closed[0]["action"] is None
    assert views.likely_fix({"state": "new", "query_keys": []}, store.actions("db")) is None

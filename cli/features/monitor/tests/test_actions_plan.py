"""The plan across databases (ca-actions-plan.md decisions 2-7): Do first, Then and Later
with risk, effort and time saved; the global Actions and Applied pages; the new verbs; the
Changes ready event; the actions check filing Fix confirmed or No change yet; and the
database-wide Likely fix."""

from __future__ import annotations

import json
import sqlite3

import pytest

from features.monitor import actions
from features.monitor.api import models, routes, views
from features.monitor.index_plan import build_plan
from features.monitor.tests.conftest import KEYS, NOW, seed
from shared.monitor.monitor_store import SCHEMA_VERSION, MonitorStore

CONFIGURED = [{"name": "db"}, {"name": "other"}, {"name": "staging"}]
SHARED_BUFFERS = {"id": "setting_shared_buffers", "title": "Raise shared_buffers to 2 GB",
                  "severity": "bad", "finding": "shared_buffers is 128 MB.",
                  "sql": "ALTER SYSTEM SET shared_buffers = '2GB';", "restart": True,
                  "setting": {"name": "shared_buffers", "current": "128MB", "target": "2GB"}}


def _plan(store, target: str, specs: list[tuple[str, float, str, str]], *,
          tables: dict | None = None, database: list | None = None, at: int = NOW) -> list:
    """Index adds from (query key, share, table, column), and the database actions given."""
    plan = build_plan({"engine": "postgresql", "now": at, "observed_s": 7200,
                       "counter_age_s": 30 * 86400, "existing": [], "tables": tables or {},
                       "queries": [{"key": key, "name": f"SELECT on {table}", "share": share,
                                    "kind": "select", "suggestions": [
                                        {"table": table, "columns": [column], "gain_pct": 80,
                                         "verified": True}]}
                                   for key, share, table, column in specs]})
    store.save_index_plan(target, plan)
    found = actions.candidates(store, target, plan, [], database or [])
    actions.reconcile(store, target, at, found)
    return found


DB_SPECS = [(KEYS[0], 0.4, "posts", "owneruserid"), (KEYS[1], 0.3, "votes", "postid"),
            (KEYS[2], 0.2, "comments", "postid"), (KEYS[3], 0.15, "users", "location"),
            (KEYS[4], 0.05, "badges", "userid")]


def _two_databases(store, monkeypatch) -> None:
    seed(store, minutes=240)
    seed(store, minutes=240, target="other")
    for target in ("db", "other"):
        store.set_settings(target, {"enabled": True})
    monkeypatch.setattr(routes, "_configured_targets", lambda: CONFIGURED)
    _plan(store, "db", DB_SPECS, tables={"votes": {"writes_per_s": 40}},
          database=[SHARED_BUFFERS])
    _plan(store, "other", [(KEYS[0], 0.5, "events", "account_id")])


def _row(store, target: str, title_part: str) -> dict:
    return next(row for row in store.actions(target) if title_part in row["payload"]["title"])


def test_cards_carry_risk_effort_and_time_saved_by_one_formula(store):
    seed(store, minutes=240)
    _plan(store, "db", DB_SPECS, tables={"votes": {"writes_per_s": 40}},
          database=[SHARED_BUFFERS, {"id": "vacuum", "title": "Vacuum 1 bloated table",
                                     "severity": "warn", "finding": "orders is 18% dead.",
                                     "sql": "VACUUM (ANALYZE) public.orders;"}])
    minute_ms, _ = actions.database_minute(store, "db", NOW)
    # Four queries share every minute; the busiest spends 3,000 ms of 6,800.
    assert minute_ms == pytest.approx(6800, rel=0.01)
    cards = {card["title"]: card for card in actions.plan_cards(store, "db", NOW)}
    posts = cards["Add an index on posts(owneruserid)"]
    assert (posts["risk"], posts["effort"], posts["kind"]) == ("none", "copy_sql", "index")
    assert posts["saves_ms_per_min"] == pytest.approx(0.32 * minute_ms, abs=0.1)
    assert posts["queries"][0]["share"] == pytest.approx(3000 / 6800, rel=0.01)
    assert posts["queries"][0]["expected_ms"] == pytest.approx(posts["queries"][0]["mean_ms"] * 0.2)
    assert posts["rollback_sql"].startswith("DROP INDEX CONCURRENTLY")
    assert cards["Add an index on votes(postid)"]["risk"] == "writes"
    buffers = cards["Raise shared_buffers to 2 GB"]
    assert (buffers["risk"], buffers["effort"], buffers["kind"]) == ("restart", "setting", "setting")
    assert buffers["rollback_sql"] == "ALTER SYSTEM SET shared_buffers = '128MB';"
    vacuum = cards["Vacuum 1 bloated table"]
    assert (vacuum["risk"], vacuum["effort"]) == ("none", "copy_sql")
    assert [card["priority"] for card in sorted(cards.values(), key=lambda card: -card["score"])
            ][:4] == ["first", "first", "first", "then"]
    assert actions._locks("VACUUM FULL public.orders;") and \
        actions._locks("DROP INDEX public.idx_old;") and \
        not actions._locks("DROP INDEX CONCURRENTLY public.idx_old;") and \
        not actions._locks("ALTER TABLE `t` DROP INDEX `i`;")
    assert actions.EFFORT["rewrite_query"] == "code"
    # The per-database page carries the same fields; a folded card takes the worst of them.
    page = actions.present(store, "db", NOW)
    models.ActionsPage.model_validate(page)
    plan_card = next(card for card in page["actions"] if card["type"] == "index_plan")
    assert plan_card["risk"] == "writes" and plan_card["tier"] == "first"


def test_tiers_cap_do_first_at_three_per_database_and_break_ties_by_risk():
    def card(key, database, score, saves, risk="none"):
        return {"key": key, "database": database, "score": score, "saves_ms_per_min": saves,
                "risk": risk, "effort": "copy_sql"}

    cards = [card("restart", "a", 0.3, 100.0, "restart"), card("plain", "a", 0.3, 100.0),
             card("third", "a", 0.2, 50.0), card("fourth", "a", 0.15, 40.0),
             card("tiny", "a", 0.05, 0.5), card("other", "b", 0.12, 5.0),
             # A spread-out database: its best change scores under 0.1 but still leads.
             card("spread", "c", 0.07, 45.0), card("spread2", "c", 0.06, 38.0),
             card("faint", "d", 0.04, 20.0)]
    actions._tiers(cards, "priority")
    tiers = {item["key"]: item["priority"] for item in cards}
    assert tiers == {"restart": "first", "plain": "first", "third": "first", "fourth": "then",
                     "tiny": "later", "other": "first", "spread": "first", "spread2": "then",
                     "faint": "then"}
    ranked = [item["key"] for item in actions._ranked(cards)]
    assert ranked[:2] == ["plain", "restart"] and ranked[-1] == "tiny"


def test_global_actions_rank_every_database_and_count_each_filter(client, store, monkeypatch):
    _two_databases(store, monkeypatch)
    page = client.get("/api/monitor/actions").json()
    models.GlobalActionsPage.model_validate(page)
    assert [card["rank"] for card in page["cards"]] == list(range(1, 8))
    assert page["cards"][0]["database"] == "other"
    assert [card["priority"] for card in page["cards"]][:5] == ["first"] * 4 + ["then"]
    assert page["counts"]["by_database"] == {"db": 6, "other": 1, "staging": 0}
    assert page["counts"]["by_kind"] == {"index": 6, "setting": 1, "maintenance": 0,
                                         "rewrite": 0}
    assert (page["counts"]["first"], page["counts"]["total"]) == (4, 7)
    assert page["unmonitored"] == ["staging"]
    assert [row["database"] for row in page["databases"]] == ["db", "other", "staging"]
    assert page["updated_at"] == NOW and page["observed_s"] == 7200
    narrowed = client.get("/api/monitor/actions?db=db").json()
    assert {card["database"] for card in narrowed["cards"]} == {"db"}
    assert [card["rank"] for card in narrowed["cards"]] == list(range(1, 7))
    assert narrowed["counts"]["by_kind"]["index"] == 5
    assert narrowed["counts"]["by_database"]["other"] == 1


def test_verbs_set_aside_dismiss_did_it_undid_it_and_bring_back(client, store, monkeypatch):
    _two_databases(store, monkeypatch)

    def unreachable(store, target, now):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(routes, "_read_database", unreachable)
    badges = _row(store, "db", "badges")["id"]
    buffers = _row(store, "db", "shared_buffers")["id"]
    posts = _row(store, "db", "posts")["id"]
    base = "/api/monitor/targets/db/actions"
    assert client.post(f"{base}/{badges}/set_aside",
                       json={"until": NOW - 1}).status_code == 422
    client.post(f"{base}/{badges}/set_aside", json={"until": NOW + 86400,
                                                    "reason": "after the release"})
    # Dismiss without a body still works; snooze stays an alias of set aside.
    assert client.post(f"{base}/{buffers}/dismiss").status_code == 200
    page = client.get("/api/monitor/actions?db=db").json()
    assert [(card["id"], card["reason"], card["set_aside_until"]) for card in page["set_aside"]] \
        == [(badges, "after the release", NOW + 86400)]
    assert [card["id"] for card in page["dismissed"]] == [buffers]
    assert len(page["cards"]) == 4
    assert client.post(f"{base}/{posts}/undid_it").status_code == 409
    client.post(f"{base}/{posts}/did_it")
    row = _row(store, "db", "posts")
    assert (row["state"], row["closed_reason"], row["closed_at"]) == ("applied", "user", NOW)
    # The plan still lists it while its readings catch up; the user's word holds.
    _plan(store, "db", DB_SPECS, tables={"votes": {"writes_per_s": 40}}, at=NOW + 300)
    assert _row(store, "db", "posts")["state"] == "applied"
    applied = client.get("/api/monitor/actions/applied?db=db").json()
    models.AppliedPage.model_validate(applied)
    [entry] = applied["days"][0]["rows"]
    assert (entry["id"], entry["by"], entry["status"], entry["source"]) == \
        (posts, "user", "measuring", "user")
    assert entry["next_check_at"] >= NOW + actions.VERIFY_AFTER_S
    assert entry["rollback_sql"].startswith("DROP INDEX CONCURRENTLY")
    client.post(f"{base}/{posts}/undid_it", json={"reason": "slowed the nightly import"})
    assert _row(store, "db", "posts")["state"] == "undone"
    _plan(store, "db", DB_SPECS, tables={"votes": {"writes_per_s": 40}}, at=NOW + 600)
    assert _row(store, "db", "posts")["state"] == "undone"
    applied = client.get("/api/monitor/actions/applied").json()
    assert applied["days"] == []
    assert [(entry["id"], entry["reason"], entry["status"]) for entry in applied["undone"]] == \
        [(posts, "slowed the nightly import", "undone")]
    assert [entry["id"] for entry in applied["dismissed"]] == [buffers]
    client.post(f"{base}/{buffers}/bring_back")
    client.post(f"{base}/{posts}/bring_back")
    client.post(f"{base}/{badges}/snooze", json={"until": NOW + 3600})
    assert {row["id"]: row["state"] for row in store.actions("db")}[buffers] == "open"
    assert _row(store, "db", "posts")["state"] == "open"
    assert _row(store, "db", "badges")["state"] == "snoozed"
    verbs = [event["kind"] for event in store.action_events(posts)]
    assert verbs[-3:] == ["did_it", "undid_it", "bring_back"]


def test_applied_page_measures_each_change_against_the_week_before(client, store, monkeypatch):
    seed(store, minutes=240)
    monkeypatch.setattr(routes, "_configured_targets", lambda: [{"name": "db"}])
    store.write_actions("db", NOW, [{
        "key": "add:posts", "type": "add_index", "state": "confirmed", "score": 0.3,
        "opened_at": NOW - 86400, "closed_at": NOW - 3600, "closed_reason": "index_added",
        "payload": {"title": "Add an index on posts(owneruserid)", "summary": "",
                    "apply": "run_sql", "queries": [KEYS[0]], "table": "posts",
                    "columns": ["owneruserid"],
                    "detail": {"undo_sql": "DROP INDEX CONCURRENTLY public.idx_posts_owneruserid;"},
                    "result": {"before_ms": 80.0, "after_ms": 50.0, "calls": 300,
                               "at": NOW - 600}}}])
    page = client.get("/api/monitor/actions/applied").json()
    models.AppliedPage.model_validate(page)
    [entry] = page["days"][0]["rows"]
    assert (entry["status"], entry["by"], entry["before_ms"], entry["after_ms"]) == \
        ("verified", "detected", 80.0, 50.0)
    assert entry["queries"][0]["before_ms"] == pytest.approx(50.0)
    assert entry["queries"][0]["calls_after"] > 0 and entry["verified_at"] == NOW - 600
    assert entry["next_check_at"] is None and entry["saved_ms_per_min"] > 0
    totals = page["totals"]
    assert (totals["changes_this_week"], totals["verified"], totals["measuring"]) == (1, 1, 0)
    assert totals["saved_ms_per_min"] == entry["saved_ms_per_min"]
    assert 0 < totals["saved_share"] <= 1 and page["since"] == NOW - 3600


def test_changes_ready_files_per_database_and_closes_when_actions_opens(client, store):
    seed(store, minutes=240)
    found = _plan(store, "db", DB_SPECS, database=[SHARED_BUFFERS])
    [item] = [row for row in store.inbox(target="db") if row["type"] == "changes_ready"]
    body = json.loads(item["body_json"])
    assert item["title"] == "6 changes ready on db"
    assert (body["count"], body["do_first"], body["db"]) == (6, 3, "db")
    assert body["titles"][0] == "Add an index on posts(owneruserid)"
    events = [event["kind"] for event in store.inbox_events() if event["item_id"] == item["id"]]
    assert events == ["created", "notified"]
    presented = views.inbox_items(store, [item])[0]
    models.InboxItem.model_validate(presented)
    assert (presented["kind"], presented["band"], presented["link"]["kind"]) == \
        ("changes_ready", "watch", "actions")
    assert presented["summary"] == "3 to do first, starting with an index on posts(owneruserid)."
    assert presented["changes"]["count"] == 6 and len(presented["changes"]["action_ids"]) == 3
    assert presented["related"][0]["link"] == {"kind": "actions", "target": "db",
                                               "label": "Actions"}
    assert client.post("/api/monitor/targets/db/actions/opened").json() == \
        {"target": "db", "closed": True}
    closed = store.read("SELECT * FROM inbox_item WHERE id=?", (item["id"],))[0]
    assert (closed["state"], closed["resolved_by"]) == ("resolved", "code")
    # The same plan files nothing new; a new Do first change files it again.
    actions.reconcile(store, "db", NOW + 60, found)
    assert store.read("SELECT state FROM inbox_item WHERE id=?", (item["id"],))[0]["state"] == \
        "resolved"
    _plan(store, "db", [(KEYS[0], 0.6, "tags", "tagname"), *DB_SPECS], database=[SHARED_BUFFERS],
          at=NOW + 120)
    again = store.read("SELECT * FROM inbox_item WHERE id=?", (item["id"],))[0]
    assert again["state"] == "new" and again["title"] == "7 changes ready on db"
    # An empty plan closes it by code.
    actions.reconcile(store, "db", NOW + 180, [])
    assert store.read("SELECT state FROM inbox_item WHERE id=?", (item["id"],))[0]["state"] == \
        "resolved"
    assert client.post("/api/monitor/targets/db/actions/opened").json()["closed"] is False


def test_a_change_that_did_nothing_files_a_closed_neutral_no_change_yet(store):
    seed(store, minutes=240)
    store.write_actions("db", NOW, [{
        "key": "add:posts", "type": "add_index", "state": "applied", "score": 0.3,
        "opened_at": NOW - 86400, "closed_at": NOW - 3 * 3600, "closed_reason": "index_added",
        "payload": {"title": "Add an index on posts(owneruserid)", "summary": "",
                    "apply": "run_sql", "queries": [KEYS[0]], "table": "posts",
                    "columns": ["owneruserid"]}}])
    actions.verify(store, "db", NOW)
    assert store.actions("db")[0]["state"] == "no_effect"
    [item] = [row for row in store.inbox(target="db") if row["type"] == "no_change_yet"]
    assert (item["state"], item["resolved_by"], item["delivery"]) == ("resolved", "code", "inbox")
    presented = views.inbox_items(store, [item])[0]
    models.InboxItem.model_validate(presented)
    assert (presented["kind"], presented["band"], presented["tone"]) == \
        ("no_change_yet", "closed", "neutral")
    assert presented["title"].startswith("No change yet: ")
    assert presented["summary"].startswith("50.0 ms before, 50.0 ms after over ")
    assert presented["link"] == {"kind": "actions", "target": "db", "label": "Open Applied"}
    assert not [event for event in store.inbox_events() if event["kind"] == "notified"]


def test_database_wide_items_name_their_likely_fix(store):
    def setting(key, name, title):
        return {"key": key, "type": "change_setting", "state": "open", "score": 0.15,
                "payload": {"title": title, "summary": "", "apply": "setting",
                            "setting": {"name": name} if name else None, "queries": []}}

    store.write_actions("db", NOW, [
        setting("db:setting_max_connections", "max_connections", "Raise max_connections"),
        setting("db:setting_shared_buffers", "shared_buffers", "Raise shared_buffers to 2 GB"),
        setting("db:idle_in_transaction", None, "End transactions left idle")])
    for type in ("connections_near_max", "cache_hit_ratio_drop", "lock_contention"):
        store.upsert_item("db", type, "database", 0.9, type, {"active_conns": 95,
                                                              "max_conns": 100}, NOW)
    items = {item["type"]: item for item in views.inbox_items(store, store.inbox(target="db"))}
    assert items["connections_near_max"]["action"]["title"] == "Raise max_connections"
    assert items["cache_hit_ratio_drop"]["action"]["title"] == "Raise shared_buffers to 2 GB"
    assert items["lock_contention"]["action"]["title"] == "End transactions left idle"


def test_schema_31_allows_undone_and_closes_the_old_fixes_item(tmp_path):
    path = tmp_path / "monitor.db"
    store = MonitorStore(path)
    item = store.upsert_item("db", "fixes_ready", "fixes", 0.6, "3 queries on db have an index fix",
                             {"count": 3}, NOW)
    store.write_actions("db", NOW, [{"key": "add:x", "type": "add_index", "state": "applied",
                                     "score": 0.2, "payload": {"title": "t"}}])
    with sqlite3.connect(path) as conn:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='action'").fetchone()[0]
        conn.execute(sql.replace("'retired','undone')", "'retired')").replace(
            "action (", "action_old (", 1))
        conn.execute("INSERT INTO action_old SELECT * FROM action")
        conn.execute("DROP TABLE action")
        conn.execute("ALTER TABLE action_old RENAME TO action")
        conn.execute("PRAGMA user_version=30")
    migrated = MonitorStore(path)
    assert migrated.read("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
    [row] = migrated.actions("db")
    actions.set_state(migrated, "db", row["id"], "undid_it", NOW + 60, reason="rolled back")
    assert migrated.actions("db")[0]["state"] == "undone"
    fixes = migrated.read("SELECT state,resolved_by FROM inbox_item WHERE id=?", (item["id"],))[0]
    assert fixes == {"state": "resolved", "resolved_by": "code"}

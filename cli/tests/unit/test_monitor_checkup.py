import asyncio

import pytest

from features.monitor import actions, report_prompt
from features.monitor.index_plan import build_plan
from shared.monitor.monitor_store import MonitorStore

NOW = 1_000_000


def _store_with_actions(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.sync_indexes("db", [{"schema": "public", "table": "events", "index_name": "events_kind",
                               "definition": "CREATE INDEX events_kind ON public.events USING btree (kind)"}],
                       NOW - 600)
    plan = build_plan({
        "engine": "postgresql", "now": NOW, "observed_s": 3 * 3600, "counter_age_s": 40 * 86400,
        "existing": [{"schema": "public", "table": "events", "index_name": "events_kind",
                      "columns": ["kind"], "scans": 0, "window_scans": 0}],
        "tables": {"events": {"writes_per_s": 250}},
        "queries": [{"key": "aaaaaaaaaaaa", "name": "SELECT on orders", "share": 0.4, "kind": "select",
                     "suggestions": [{"table": "orders", "columns": ["customer_id"], "gain_pct": 80,
                                      "verified": True}]}]})
    store.save_index_plan("db", plan)
    database = [{"id": "setting_work_mem", "title": "Raise work_mem so sorts stay in memory",
                 "severity": "bad", "finding": "work_mem is 4MB.", "setting": {"name": "work_mem"}}]
    open_actions = actions.reconcile(store, "db", NOW, actions.candidates(store, "db", plan, [], database))
    return store, plan, open_actions


def test_checkup_input_names_actions_by_id_and_summarizes_the_plan(tmp_path):
    store, plan, open_actions = _store_with_actions(tmp_path)
    data = report_prompt.checkup_input(open_actions, plan, [])
    ids = [row["action_id"] for row in data["open_actions"]]
    assert ids == [f"a{row['id']}" for row in open_actions]
    assert data["index_plan"]["based_on"] == "3 hours of RDST's readings and 40 days of the database's own counters"
    assert data["index_plan"]["add"][0]["helps"] == ["{{q:aaaaaaaaaaaa}}"]
    assert data["index_plan"]["drop"][0]["table_writes_a_second"] == "250.0"


def test_parse_answer_keeps_only_known_actions_and_grounded_words(tmp_path):
    store, plan, open_actions = _store_with_actions(tmp_path)
    data = {"queries": [{"query_key": "aaaaaaaaaaaa"}], **report_prompt.checkup_input(open_actions, plan, [])}
    first, second, third = data["open_actions"]
    answer = report_prompt.parse_answer({
        "actions": [{"action_id": second["action_id"], "why": "It touches the busiest query."},
                    {"action_id": "a999", "why": "Made up."},
                    {"action_id": first["action_id"], "why": "It saves 77% of the time."}],
        "index_plan_explanation": "One index speeds up the busiest read, and dropping an unused "
                                  "index on a write heavy table saves work on every insert.",
        "overlaps": [{"action_ids": [first["action_id"], third["action_id"]], "note": "Do the index first."},
                     {"action_ids": ["a999", first["action_id"]], "note": "Unknown."}]}, data)
    assert [pick["action_id"] for pick in answer["action_picks"]] == [second["action_id"], first["action_id"]]
    # 77% isn't in the data, so that why is dropped while the pick stays.
    assert answer["action_picks"][1]["why"] is None
    assert answer["index_plan_explanation"].startswith("One index speeds up")
    assert [overlap["note"] for overlap in answer["overlaps"]] == ["Do the index first."]


def test_explanations_keep_the_order(tmp_path):
    store, plan, open_actions = _store_with_actions(tmp_path)
    before = [card["key"] for card in actions.present(store, "db", NOW)["actions"]]
    setting = next(row for row in open_actions if row["type"] == "change_setting")
    actions.apply_explanations(store, "db", NOW, {
        "action_picks": [{"action_id": f"a{setting['id']}", "why": "Sorts spill to disk."}],
        "index_plan_explanation": "Why the plan."})
    page = actions.present(store, "db", NOW)
    # The order stays measured impact and Jev's read; the explanation adds its why.
    assert [card["key"] for card in page["actions"]] == before
    picked = next(card for card in page["actions"] if card["type"] == "change_setting")
    assert picked["why"] == "Sorts spill to disk."
    assert store.index_plan("db")["explanation"] == "Why the plan."


def test_a_why_survives_a_recompute(tmp_path):
    store, plan, open_actions = _store_with_actions(tmp_path)
    setting = next(row for row in open_actions if row["type"] == "change_setting")
    actions.apply_explanations(store, "db", NOW, {
        "action_picks": [{"action_id": f"a{setting['id']}", "why": "Sorts spill to disk."}]})
    database = [{"id": "setting_work_mem", "title": "Raise work_mem so sorts stay in memory",
                 "severity": "bad", "finding": "work_mem is 4MB.", "setting": {"name": "work_mem"}}]
    actions.reconcile(store, "db", NOW + 300, actions.candidates(store, "db", plan, [], database))
    row = next(row for row in store.actions("db") if row["type"] == "change_setting")
    assert row["why"] == "Sorts spill to disk."


def test_a_why_names_queries_the_way_the_page_does(tmp_path):
    from features.monitor.naming import target_names

    store, plan, open_actions = _store_with_actions(tmp_path)
    store.record("db", NOW - 60, [{"query_key": "aaaaaaaaaaaa", "calls": 1, "total_ms": 5}],
                 {"aaaaaaaaaaaa": "SELECT * FROM orders WHERE customer_id = 1"}, [])
    setting = next(row for row in open_actions if row["type"] == "change_setting")
    actions.apply_explanations(store, "db", NOW, {
        "action_picks": [{"action_id": f"a{setting['id']}",
                          "why": "{{q:aaaaaaaaaaaa}} and {{q:bbbbbbbbbbbb}} dominate."}],
        "index_plan_explanation": "It speeds up {{q:aaaaaaaaaaaa}}."})
    name = target_names(store, "db")["aaaaaaaaaaaa"]
    picked = next(card for card in actions.present(store, "db", NOW)["actions"]
                  if card["type"] == "change_setting")
    assert picked["why"] == f"{name} and a query dominate."
    assert store.index_plan("db")["explanation"] == f"It speeds up {name}."


def test_a_group_with_more_than_settings_is_not_called_settings():
    cards = [{"key": "db:idle_in_transaction", "title": "Close transactions the app leaves open"},
             {"key": "db:setting_shared_buffers", "title": "Give PostgreSQL more memory"}]
    assert actions._group_words("settings", cards)[0] == "Make 2 database fixes"
    assert actions._group_words("settings", cards[1:] * 2)[0] == "Tune 2 server settings"


@pytest.mark.asyncio
async def test_a_checkup_waiting_for_a_slot_keeps_its_claim(tmp_path, monkeypatch):
    from features.monitor import report as report_module

    store = MonitorStore(tmp_path / "monitor.db")
    run, claimed = store.claim_report("db", "nightly", 1000)
    assert claimed
    clock = [1000]
    scheduler = report_module.ReportScheduler(store, clock=lambda: clock[0])
    monkeypatch.setattr(report_module, "SLOT_HEARTBEAT_S", 0.05)
    for _ in range(report_module.CHECKUP_SLOTS):
        await scheduler._slots.acquire()
    ran = []

    async def work():
        ran.append(True)

    waiting = asyncio.create_task(scheduler._in_slot(work(), run["id"]))
    clock[0] = 2000
    await asyncio.sleep(0.2)
    store.recover_reports(2000)
    assert store.report(run["id"])["state"] == "running"
    scheduler._slots.release()
    await waiting
    assert ran == [True]

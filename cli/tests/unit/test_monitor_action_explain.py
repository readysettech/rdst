import pytest

from features.monitor import action_explain, actions
from features.monitor.index_plan import build_plan
from shared.monitor.monitor_store import MonitorStore

NOW = 1_000_000


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = MonitorStore(tmp_path / "monitor.db")
    plan = build_plan({
        "engine": "postgresql", "now": NOW, "observed_s": 3 * 3600, "counter_age_s": 40 * 86400,
        "existing": [{"schema": "public", "table": "events", "index_name": "events_kind",
                      "columns": ["kind"], "scans": 0, "window_scans": 0}],
        "tables": {"events": {"writes_per_s": 250}},
        "queries": [{"key": "aaaaaaaaaaaa", "name": "SELECT on orders", "share": 0.4,
                     "kind": "select", "suggestions": [{"table": "orders", "columns": ["customer_id"],
                                                        "gain_pct": 80, "verified": True}]}]})
    store.save_index_plan("db", plan)
    database = [{"id": "setting_work_mem", "title": "Raise work_mem so sorts stay in memory",
                 "severity": "bad", "finding": "work_mem is 4MB.", "setting": {"name": "work_mem"}}]
    actions.reconcile(store, "db", NOW, actions.candidates(store, "db", plan, [], database))
    monkeypatch.setattr(action_explain.database_limit, "paused", lambda store, target, now: False)
    monkeypatch.setattr(action_explain.database_limit, "target_fingerprint", lambda target: None)
    return store


def _answer_every(calls):
    def ask(data):
        calls.append(data)
        return {"actions": [{"action_id": item["action_id"], "why": "It touches the busiest query."}
                            for item in data["open_actions"]],
                "index_plan_explanation": "One index speeds up the busiest read."}
    return ask


def test_one_call_explains_every_new_change_and_the_plan(store, monkeypatch):
    calls = []
    monkeypatch.setattr(action_explain, "_ask", _answer_every(calls))
    rows, _ = action_explain.waiting(store, "db")
    assert action_explain.due(store, "db", NOW) and len(rows) >= 2
    assert action_explain.explain(store, "db", NOW) == len(rows)
    assert len(calls) == 1 and len(calls[0]["open_actions"]) == len(rows)
    assert all(row["why"] for row in store.actions("db", ("open",)))
    assert store.index_plan("db")["explanation"] == "One index speeds up the busiest read."
    # Nothing new: no call, however long it's been.
    assert not action_explain.due(store, "db", NOW + 86400)


def test_a_change_the_model_left_out_gets_one_more_try(store, monkeypatch):
    def ask(data):
        first = data["open_actions"][0]["action_id"]
        return {"actions": [{"action_id": first, "why": "It touches the busiest query."},
                            {"action_id": "a999", "why": "Made up."}]}

    monkeypatch.setattr(action_explain, "_ask", ask)
    assert action_explain.explain(store, "db", NOW) == 1
    # One more try in a later call, then it stays without a why.
    rows, _ = action_explain.waiting(store, "db")
    assert len(rows) == 1
    assert action_explain.explain(store, "db", NOW + action_explain.MIN_GAP_S) == 1
    rows, _ = action_explain.waiting(store, "db")
    assert rows == []


def test_calls_are_capped_per_day_and_spaced(store, monkeypatch):
    calls = []
    monkeypatch.setattr(action_explain, "_ask", lambda data: calls.append(data) or {})
    gap = action_explain.MIN_GAP_S
    for index in range(action_explain.CALLS_PER_DAY):
        # A failed or empty answer leaves the changes waiting for the next call.
        assert action_explain.due(store, "db", NOW + index * gap)
        action_explain.explain(store, "db", NOW + index * gap)
        assert not action_explain.due(store, "db", NOW + index * gap + 60)
    last = NOW + (action_explain.CALLS_PER_DAY - 1) * gap
    assert not action_explain.due(store, "db", last + gap)
    assert action_explain.calls_today(store, "db", last) == action_explain.CALLS_PER_DAY
    assert action_explain.due(store, "db", NOW + 86400)


def test_a_failed_call_counts_and_leaves_the_changes_waiting(store, monkeypatch):
    def fail(data):
        raise RuntimeError("UPSTREAM_ERROR: provider unavailable")

    monkeypatch.setattr(action_explain, "_ask", fail)
    before, _ = action_explain.waiting(store, "db")
    assert action_explain.explain(store, "db", NOW) == 0
    assert action_explain.calls_today(store, "db", NOW) == 1
    rows, plan = action_explain.waiting(store, "db")
    assert rows == before and plan is not None


def test_numbers_the_data_lacks_are_dropped(store):
    rows, plan = action_explain.waiting(store, "db")
    data = action_explain.model_input(rows, plan)
    first = data["open_actions"][0]["action_id"]
    answer = action_explain.parse_answer(
        {"actions": [{"action_id": first, "why": "It saves 77% of the time."}]}, data)
    assert answer["action_picks"] == []


def test_it_waits_for_the_first_pass(store, monkeypatch):
    from features.monitor import report

    monkeypatch.setattr(report, "first_pass", lambda store, target, now: {"due": False})
    assert not action_explain.due(store, "db", NOW)
    monkeypatch.setattr(report, "first_pass", lambda store, target, now: {"due": True})
    assert action_explain.due(store, "db", NOW)


def test_a_paused_database_makes_no_call(store, monkeypatch):
    monkeypatch.setattr(action_explain.database_limit, "paused", lambda store, target, now: True)
    assert not action_explain.due(store, "db", NOW)

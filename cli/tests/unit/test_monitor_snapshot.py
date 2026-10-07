"""The daily snapshot report: its day, its frozen payload, sample days and the migration."""

import json
import re
import sqlite3
from datetime import datetime

import pytest

from features.monitor import report as reports
from features.monitor import snapshot
from features.monitor.api import models
from shared.monitor.monitor_store import SCHEMA_VERSION, MonitorStore, local_day_bounds

DAY_START = int(datetime(2026, 9, 28).timestamp())
NIGHTLY_AT = int(datetime(2026, 9, 29, 0, 1).timestamp())
KEY_A, KEY_B = "aaaaaaaaaaaa", "bbbbbbbbbbbb"


def _store(tmp_path):
    return MonitorStore(tmp_path / "monitor.db")


def _day(store, start, *, calls=60, slow=1.0):
    texts = {KEY_A: "SELECT id FROM orders WHERE customer_id = $1",
             KEY_B: "SELECT count(*) FROM events WHERE type = $1"}
    for minute in range(0, 180, 5):
        ts = start + 9 * 3600 + minute * 60
        store.record("db", ts, [
            {"query_key": KEY_A, "calls": calls, "total_ms": calls * 2.0 * slow, "rows": calls,
             "blks_hit": 100, "blks_read": 0},
            {"query_key": KEY_B, "calls": 5, "total_ms": 10.0, "rows": 5, "blks_hit": 10,
             "blks_read": 1}], texts, [],
            {"calls": calls + 5, "total_ms": calls * 2.0 * slow + 10, "active_conns": 4,
             "max_conns": 100, "cache_hit_ratio": 0.99, "xact_commit": calls,
             "xact_rollback": 1, "deadlocks": 0, "blks_hit": 110, "blks_read": 1})


def test_nightly_covers_the_day_that_ended_and_a_run_by_hand_today_so_far():
    start, end, partial = snapshot.report_period("nightly", NIGHTLY_AT)
    assert (start, end, partial) == (DAY_START, local_day_bounds(NIGHTLY_AT)[0], False)
    noon = int(datetime(2026, 9, 29, 12).timestamp())
    start, end, partial = snapshot.report_period("user", noon)
    assert (start, end, partial) == (local_day_bounds(noon)[0], noon, True)
    # The day before, cut to the same length when today is partial.
    assert snapshot.prior_period(DAY_START, local_day_bounds(DAY_START)[1]) == (
        local_day_bounds(DAY_START - 1)[0], DAY_START)
    today = local_day_bounds(noon)[0]
    assert snapshot.prior_period(today, noon) == (
        local_day_bounds(today - 1)[0], local_day_bounds(today - 1)[0] + noon - today)


def test_snapshot_payload_is_frozen_numbers_chart_and_queries(tmp_path):
    store = _store(tmp_path)
    _day(store, DAY_START - 86400, calls=30)
    _day(store, DAY_START, calls=60, slow=3.0)
    item = store.upsert_item("db", "latency_regression", KEY_A, 0.9, "Query latency increased",
                             {"query_key": KEY_A}, DAY_START + 10 * 3600, delivery="notify")
    end = local_day_bounds(DAY_START)[1]
    day = snapshot.build(store, "db", DAY_START, end, NIGHTLY_AT, partial=False,
                         collectors={"config_audit": {"settings": [
                             {"parameter": "work_mem", "current": "4 MB",
                              "recommended": "16–32 MB", "status": "warn",
                              "note": "Low — causes disk sorts"}]}})
    day = snapshot.with_brief(day, None)
    models.DaySnapshot.model_validate(day)
    assert day["period"]["day"] == "2026-09-28" and day["period"]["partial"] is False
    numbers = {row["metric"]: row for row in day["numbers"]}
    assert numbers["qps"]["value"] > numbers["qps"]["prior"]
    assert numbers["mean_ms"]["better"] == "down" and numbers["mean_ms"]["change"] > 0
    chart = day["chart"]
    assert chart["start"] == DAY_START and chart["end"] == end
    assert [band["key"] for band in chart["bands"]] == [KEY_A, KEY_B]
    assert len(chart["qps"]) == 2 and len(chart["qps"][0]) == len(chart["xs"])
    assert max(value or 0 for value in chart["qps"][0]) == pytest.approx(60 / 300)
    assert [event["what"] for event in day["events"]] == ["Got slower"]
    assert day["events"][0]["state"] == "open" and day["events"][0]["item_id"] == item["id"]
    assert chart["markers"][0]["ts"] == day["events"][0]["ts"]
    rows = {row["query_key"]: row for row in day["queries"]}
    assert rows[KEY_A]["status"] == "needs_attention"
    assert rows[KEY_A]["latency_change"] == pytest.approx(2.0)
    assert day["look_at"][0]["query_key"] == KEY_A and day["look_at"][0]["text"]
    problem = day["health"]["problems"][0]
    assert problem["rows"][0]["value"] == "4 MB, recommended 16 to 32 MB"
    assert "—" not in problem["rows"][0]["detail"]
    # The day in brief is the facts, with queries as tokens and no SQL.
    assert day["brief_source"] == "facts"
    assert any(f"{{{{q:{KEY_A}}}}}" in sentence for sentence in day["brief"])
    assert not any("SELECT" in sentence for sentence in day["brief"])


def test_brief_keeps_model_wording_only_when_it_keeps_to_the_facts():
    facts = [{"kind": "traffic", "text": "db averaged 12 queries a second.", "keys": []},
             {"kind": "queries", "text": f"{{{{q:{KEY_A}}}}} ran most often.", "keys": [KEY_A]}]
    good = f"The database ran 12 queries a second. Most of them were {{{{q:{KEY_A}}}}}."
    assert snapshot.brief_sentences(good, facts) == (
        ["The database ran 12 queries a second.", f"Most of them were {{{{q:{KEY_A}}}}}."],
        "model")
    for bad in (f"It ran 40 queries a second. {{{{q:{KEY_A}}}}} led.",
                f"{{{{q:{KEY_A}}}}} needs an index on events(type, created_at). Add it.",
                "SELECT id FROM orders was slow. It ran 12 queries a second."):
        assert snapshot.brief_sentences(bad, facts)[1] == "facts"


def test_sample_days_are_labeled_and_never_count_as_real(tmp_path, monkeypatch):
    from features.monitor.api import routes

    store = _store(tmp_path)
    monkeypatch.setattr(routes, "_store", lambda: store)
    end = local_day_bounds(DAY_START)[1]
    record = {"version": 3, "period": {"start": DAY_START, "end": end, "partial": False,
                                       "day": "2026-09-28"},
              "numbers": [], "peak": {"qps": None, "ts": None}, "chart": None,
              "events": [], "queries": [], "look_at": [],
              "health": {"problems": [], "fine": [], "unavailable": []}, "names": {},
              "facts": [{"kind": "events", "text": "Nothing unusual happened.", "keys": []}]}
    sample_id = store.put_sample_report("db", end + 60, (DAY_START, end),
                                        {"summary": "", "snapshot": record}, [])
    # Replacing a sample day keeps one row for it.
    sample_id = store.put_sample_report("db", end + 60, (DAY_START, end),
                                        {"summary": "", "snapshot": record}, [])
    assert len(store.read("SELECT id FROM report_run WHERE sample=1")) == 1
    presented = routes.get_report("db", sample_id)
    assert presented["sample"] is True and presented["period_start"] == DAY_START
    assert presented["report_json"]["snapshot"]["brief"] == ["Nothing unusual happened."]
    models.DatabaseReport.model_validate(presented)
    history = routes.report_history("db")["reports"]
    assert history[0]["sample"] is True and history[0]["day"] == "2026-09-28"
    # A real run is always the latest, and claiming one ignores sample rows.
    run, claimed = store.claim_report("db", "user", end + 120)
    assert claimed
    store.update_report(run["id"], state="done", report={"summary": "real"}, finished_at=end + 130)
    assert routes.latest_report("db")["id"] == run["id"]
    assert routes.usage("db", days=7)["days"][-1]["reports"] <= 1
    assert store.delete_sample_reports("db") == 1


def test_run_stores_its_day_and_snapshot(tmp_path, monkeypatch):
    import asyncio

    store = _store(tmp_path)
    _day(store, DAY_START)

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(reports.asyncio, "to_thread", inline)
    # No analysis can start, so the nightly run finishes with plans only.
    monkeypatch.setenv("RDST_MONITOR_ANALYZE_DAILY_CAP", "0")
    result = asyncio.run(reports.run_report(
        "db", "nightly", store=store, clock=lambda: NIGHTLY_AT,
        explain=lambda target, sql: {"plan": ["Seq Scan"]},
        collect=lambda target: ({"section_errors": {}}, {}),
        narrate=lambda context: {"summary": "No wording.", "fix_first": [],
                                 "one_fix_several_queries": [], "health": [],
                                 "changed_since_yesterday": []}))
    row = store.report(result["id"])
    assert (row["period_start"], row["period_end"]) == (DAY_START, local_day_bounds(DAY_START)[1])
    stored = json.loads(row["report_json"])
    assert stored["version"] == 3 and stored["snapshot"]["period"]["day"] == "2026-09-28"
    assert stored["snapshot"]["queries"][0]["query_key"] == KEY_A


def test_migration_adds_period_and_sample_columns(tmp_path):
    path = tmp_path / "monitor.db"
    MonitorStore(path).ensure_open()
    conn = sqlite3.connect(path)
    for column in ("period_start", "period_end", "sample"):
        conn.execute(f"ALTER TABLE report_run DROP COLUMN {column}")
    conn.execute("INSERT INTO report_run (target,ts,trigger,state) VALUES ('db',1,'user','done')")
    conn.execute("PRAGMA user_version = 17")
    conn.commit()
    conn.close()
    store = MonitorStore(path)
    store.ensure_open()
    row = store.read("SELECT * FROM report_run")[0]
    assert row["sample"] == 0 and row["period_start"] is None
    assert store.read("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION


# Round 3 (FEEDBACK_ROUND3 B): health first, database actions, spikes, CSV data, the prompt.

from features.monitor import db_actions, report_prompt  # noqa: E402

COLLECTORS = {
    "engine": "postgresql",
    "config_audit": {"settings": [
        {"parameter": "shared_buffers", "current": "128 MB", "recommended": "25% of RAM",
         "status": "ok", "note": "25% of RAM is standard"},
        {"parameter": "work_mem", "current": "4 MB", "recommended": "16–32 MB",
         "status": "warn", "note": "Low — causes disk sorts"},
        {"parameter": "random_page_cost", "current": "4", "recommended": "1.1",
         "status": "warn", "note": "Assumes spinning disk"}]},
    "index_health": {"total_indexes": 9, "unused_indexes": [
        {"schema": "public", "table": "orders", "index": "idx_old", "size_bytes": 8 * 1024 ** 2,
         "scans": 0},
        {"schema": "public", "table": "orders", "index": "orders_pkey", "is_primary": True,
         "size_bytes": 1024, "scans": 0}], "duplicates": []},
    "vacuum_bloat": {"tables": [
        {"schema": "public", "table": "orders", "live_tuples": 8200, "dead_tuples": 1800,
         "dead_ratio_pct": 18.0, "status": "warn", "n_mod_since_analyze": 5000,
         "size_bytes": 64 * 1024 * 1024}],
        "txid_age_pct": 1.0, "autovacuum_enabled": True},
    "connections": {"total_connections": 12, "max_connections": 100,
                    "idle_in_transaction_count": 1, "long_running_idle_in_tx": [
                        {"pid": 7, "application_name": "worker", "duration_seconds": 600}]},
    "replication": {"upstream_lag_seconds": None, "inactive_slots": 0, "slots": []},
    "section_errors": {},
}


def test_database_actions_carry_evidence_and_sql():
    numbers = {"cache_hit_ratio_min": 0.72, "cache_hit_ratio_avg": 0.9,
               "active_conns_peak": 9, "max_conns": 100, "xact_rollback": 50, "deadlocks": 2}
    actions = {action["id"]: action for action in db_actions.database_actions(
        COLLECTORS, numbers, {"xact_rollback": 40})}
    # Worst first; the low cache hit folds into the default shared_buffers.
    assert list(actions)[0] == "setting_shared_buffers"
    assert "cache_hit" not in actions
    buffers = actions["setting_shared_buffers"]
    assert buffers["severity"] == "bad" and buffers["restart"] is True
    assert {"label": "Lowest cache hit", "value": "72.0%"} in buffers["evidence"]
    assert actions["setting_work_mem"]["sql"] == (
        "ALTER SYSTEM SET work_mem = '16MB';\nSELECT pg_reload_conf();")
    assert actions["setting_random_page_cost"]["sql"].startswith(
        "ALTER SYSTEM SET random_page_cost = 1.1;")
    # A primary key is never offered for dropping.
    assert actions["unused_indexes"]["sql"] == "DROP INDEX CONCURRENTLY public.idx_old;"
    assert actions["vacuum"]["sql"] == "VACUUM (ANALYZE) public.orders;"
    assert actions["analyze"]["sql"] == "ANALYZE public.orders;"
    assert "idle_in_transaction_session_timeout" in actions["idle_in_transaction"]["sql"]
    assert actions["deadlocks"]["evidence"] == [{"label": "Deadlocks", "value": "2"}]
    # Rollbacks only when they jump.
    assert "rollbacks" not in actions
    for action in actions.values():
        assert not any(dash in action["finding"] + action["why"] + action["steps"]
                       for dash in "–—")
    mysql = db_actions.setting_sql("innodb_buffer_pool_size", "1 GB", "mysql")
    assert mysql == "SET PERSIST innodb_buffer_pool_size = 1073741824;"
    assert db_actions.setting_value("25% of RAM") is None


def test_health_overview_leads_with_the_worst_check():
    numbers = {"cache_hit_ratio_min": 0.72, "active_conns_peak": 9, "max_conns": 100,
               "xact_rollback": 0, "deadlocks": 0}
    actions = db_actions.database_actions(COLLECTORS, numbers, {})
    overview = db_actions.health_overview(COLLECTORS, numbers, actions)
    checks = {check["check"]: check for check in overview["checks"]}
    assert overview["status"] == "bad" and overview["label"] == "Needs attention"
    # The cache check shows the day's reading even though the setting carries the action.
    assert checks["cache"] == {"check": "cache", "label": "Cache hit", "status": "bad",
                               "value": "Fell to 72.0%"}
    assert checks["replication"]["status"] == "ok"
    assert overview["headline"].startswith("Cache hit fell to 72.0%")
    quiet = db_actions.health_overview({"section_errors": {}}, {"cache_hit_ratio_min": 0.99}, [])
    assert quiet["status"] == "ok" and quiet["headline"] == "Every check passed."


def _spiky_day(store, start):
    texts = {KEY_A: "SELECT id FROM orders WHERE customer_id = $1",
             KEY_B: "SELECT count(*) FROM events WHERE type = $1"}
    for minute in range(0, 600, 5):
        ts = start + 9 * 3600 + minute * 60
        burst = 300 <= minute < 315
        calls_b = 60 if burst else 5
        store.record("db", ts, [
            {"query_key": KEY_A, "calls": 60, "total_ms": 120.0, "rows": 60},
            {"query_key": KEY_B, "calls": calls_b, "total_ms": calls_b * 2.0, "rows": 5}],
            texts, [], {"calls": 60 + calls_b, "total_ms": 120.0 + calls_b * 2.0,
                        "active_conns": 4, "max_conns": 100, "cache_hit_ratio": 0.99,
                        "xact_commit": 60, "xact_rollback": 0, "deadlocks": 0,
                        "blks_hit": 100, "blks_read": 1})


def test_spikes_say_when_how_big_and_which_queries(tmp_path):
    store = _store(tmp_path)
    _spiky_day(store, DAY_START)
    end = local_day_bounds(DAY_START)[1]
    day = snapshot.with_brief(snapshot.build(store, "db", DAY_START, end, NIGHTLY_AT,
                                             partial=False, collectors=COLLECTORS), None)
    models.DaySnapshot.model_validate(day)
    spikes = day["spikes"]
    assert [spike["metric"] for spike in spikes] == ["traffic"]
    spike = spikes[0]
    assert spike["start"] == DAY_START + 14 * 3600 and spike["ratio"] > 1.6
    assert spike["queries"][0]["query_key"] == KEY_B
    assert spike["queries"][0]["during"] > spike["queries"][0]["before"]
    assert spike["summary"].startswith("Traffic spiked 2:00 PM")
    assert "_by_key" not in day["chart"]
    # The chart can switch metrics: time spent and latency per band, and the vitals.
    chart = day["chart"]
    assert len(chart["ms"]) == len(chart["mean_ms"]) == len(chart["bands"])
    assert len(chart["overall_mean_ms"]) == len(chart["xs"])
    assert len(day["vitals"]["xs"]) == len(day["vitals"]["cache_hit_ratio"])
    # Health leads with the overview; actions and a spike fact reach the brief.
    assert day["overview"]["checks"][0]["check"] == "cache"
    assert day["actions"][0]["id"] == "setting_work_mem"
    assert any(fact["kind"] == "spike" for fact in day["facts"])
    assert len(day["brief"]) <= 5
    # Every query row carries what the CSV exports: its SQL and its findings.
    assert all(row["text"] and "findings" in row for row in day["queries"])


def test_prompt_answer_keeps_only_what_the_facts_support(tmp_path):
    store = _store(tmp_path)
    _spiky_day(store, DAY_START)
    end = local_day_bounds(DAY_START)[1]
    day = snapshot.build(store, "db", DAY_START, end, NIGHTLY_AT, partial=False,
                         collectors=COLLECTORS)
    data = report_prompt.model_input(day, "db", [])
    assert "SELECT" not in json.dumps(data)
    assert {action["id"] for action in data["candidate_actions"]} >= {"setting_work_mem"}
    token = f"{{{{q:{KEY_B}}}}}"
    answer = report_prompt.parse_answer({
        "brief": [{"topic": "spike", "text": f"Traffic spiked at 2:00 PM, led by {token}."},
                  {"topic": "health", "text": "work_mem is 4 MB, below the 16 to 32 MB the check wants."},
                  {"topic": "traffic", "text": "Traffic was 9000 queries a second."},
                  {"topic": "todo", "text": "Run CREATE INDEX ON orders (id) now."}],
        "database_recommendations": [
            {"action_id": "setting_work_mem", "why": "Sorts spill at 4 MB."},
            {"action_id": "made_up", "why": "x"},
            {"action_id": "vacuum", "why": "orders is 99% dead."}],
        "query_notes": [{"query_key": KEY_B, "title": "Look at the event count",
                         "why": "It led the spike.", "action": "Check the caller."},
                        {"query_key": "ffffffffffff", "title": "Nope", "why": "Nope"}],
        "one_fix_several_queries": []}, data)
    # Invented numbers and SQL drop out; unknown ids and queries too.
    assert [item["topic"] for item in answer["brief_items"]] == ["spike", "health"]
    assert answer["database_recommendations"] == [
        {"action_id": "setting_work_mem", "why": "Sorts spill at 4 MB."},
        {"action_id": "vacuum", "why": None}]
    assert [note["query_key"] for note in answer["query_notes"]] == [KEY_B]
    assert answer["fix_first"][0]["title"] == "Look at the event count"
    narrative = reports.validate_narrative(answer)
    merged = snapshot.with_brief(day, narrative["summary"], narrative)
    assert merged["brief_source"] == "model" and merged["brief_items"][0]["topic"] == "spike"
    assert merged["actions"][0]["id"] == "setting_work_mem"
    assert merged["actions"][0]["pick"] == 0 and merged["actions"][1]["pick"] == 1
    models.DaySnapshot.model_validate(merged)
    # Temperature 0 and the prompt's rules travel with every call.
    assert "Never compute, round" in report_prompt.SYSTEM_PROMPT
    assert report_prompt.prompt(data).split("\nDATA:\n", 1)[0].startswith("Write the report for")


@pytest.fixture
def denver():
    """A local day that isn't UTC-aligned, so daily rollups straddle its edges."""
    import os
    import time

    before = os.environ.get("TZ")
    os.environ["TZ"] = "America/Denver"
    time.tzset()
    yield
    if before is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = before
    time.tzset()


def _rows_from(store, start, stop):
    """Readings every 5 minutes over [start, stop), as a database that began recording at
    `start` would have them."""
    texts = {KEY_A: "SELECT id FROM orders WHERE customer_id = $1"}
    for ts in range(start, stop, 300):
        store.record("db", ts, [{"query_key": KEY_A, "calls": 30, "total_ms": 60.0,
                                 "rows": 30}], texts, [],
                     {"calls": 30, "total_ms": 60.0, "active_conns": 3, "max_conns": 100,
                      "cache_hit_ratio": 0.99, "xact_commit": 30, "xact_rollback": 2,
                      "deadlocks": 0, "blks_hit": 100, "blks_read": 1})


def test_first_day_has_no_earlier_day_and_the_next_compares_with_its_real_part(
        tmp_path, denver):
    store = _store(tmp_path)
    monday = int(datetime(2026, 9, 28).timestamp())
    tuesday, wednesday = local_day_bounds(monday)[1], local_day_bounds(monday + 86400)[1]
    began = monday + 16 * 3600 + 45 * 60
    _rows_from(store, began, tuesday)
    _rows_from(store, tuesday, tuesday + 6 * 3600)
    # A daily rollup whose finer rows are gone, from the UTC day that holds most of Sunday.
    sunday_utc = (monday - 86400) // 86400 * 86400
    assert sunday_utc < monday - 86400 < sunday_utc + 86400 < monday

    def lone_day(conn):
        conn.execute("INSERT INTO db_series (target,res,bucket_start,calls,total_ms,"
                     "active_conns,max_conns,cache_hit_ratio,xact_commit,xact_rollback,"
                     "deadlocks) VALUES ('db',86400,?,900000,1.8e6,40,100,0.5,900000,5000,3)",
                     (sunday_utc,))
        conn.execute("INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,"
                     "total_ms,rows,blks_hit,blks_read) VALUES "
                     "('db',?,86400,?,900000,1.8e6,900000,0,0)", (KEY_A, sunday_utc))
    store._write(lone_day)

    first = snapshot.with_brief(snapshot.build(store, "db", monday, tuesday, tuesday + 60,
                                               partial=False), None)
    models.DaySnapshot.model_validate(first)
    assert first["period"]["prior_start"] is None and first["period"]["prior_end"] is None
    assert all(row["prior"] is None for row in first["numbers"])
    assert not any(row["new"] for row in first["queries"])
    assert not any("Sunday" in fact["text"] for fact in first["facts"])
    assert report_prompt.model_input(first, "db")["database"]["compared_with"] is None
    # Only rows inside the local day count: the straddling daily row stays out of Monday.
    assert first["period"]["observed_from"] == began
    assert {row["metric"]: row["value"] for row in first["numbers"]}["deadlocks"] == 0

    second = snapshot.build(store, "db", tuesday, wednesday, wednesday + 60, partial=False)
    period = second["period"]
    assert period["prior_start"] == monday and period["prior_end"] == tuesday
    assert period["prior_observed_from"] == began
    numbers = {row["metric"]: row for row in second["numbers"]}
    assert numbers["qps"]["prior"] == pytest.approx(30 / 300)
    assert numbers["xact_rollback"]["prior"] == 2 * (tuesday - began) // 300
    assert report_prompt.model_input(second, "db")["database"]["compared_with"] == (
        "Monday, recorded from 4:45 PM")


def test_brief_is_the_top_three_priorities_each_with_what_it_means(tmp_path):
    store = _store(tmp_path)
    _spiky_day(store, DAY_START)
    end = local_day_bounds(DAY_START)[1]
    collectors = {**COLLECTORS, "vacuum_bloat": {"tables": [
        {"table": "orders", "status": "crit", "dead_ratio_pct": 60.0, "dead_tuples": 600,
         "live_tuples": 400}]}}
    day = snapshot.build(store, "db", DAY_START, end, NIGHTLY_AT, partial=False,
                         collectors=collectors)
    assert all(fact["why"] and fact["weight"] is not None for fact in day["facts"])
    brief = snapshot.with_brief(day, None)
    items = brief["brief_items"]
    assert brief["brief_source"] == "facts" and len(items) == 3
    assert brief["brief"] == [item["text"] for item in items]
    # A database problem outranks the routine traffic line, and every item explains itself.
    assert items[0]["topic"] == "health"
    assert "traffic" not in [item["topic"] for item in items]
    assert all(item["why"] and not re.search(r"\d", item["why"]) for item in items)
    models.DaySnapshot.model_validate(brief)

    data = report_prompt.model_input(day, "db", [])
    token = f"{{{{q:{KEY_B}}}}}"
    answer = report_prompt.parse_answer({"brief": [
        {"topic": "health", "text": "Cache hit fell and work_mem is 4 MB.",
         "why": "More reads go to disk: queries wait on storage instead of memory, so they "
                "slow down under load."},
        {"topic": "spike", "text": f"Traffic spiked at 2:00 PM, led by {token}.",
         "why": "It ran 9000 times a second."},
        {"topic": "events", "text": "Nothing stayed open.", "why": "Nothing to do."},
        {"topic": "todo", "text": "Raise work_mem first."}], "database_recommendations": [],
        "query_notes": [], "one_fix_several_queries": []}, data)
    kept = answer["brief_items"]
    # Three at most; a why with a number (even one from the data) gives way to the report's.
    assert [item["topic"] for item in kept] == ["health", "spike", "events"]
    assert kept[0]["why"].startswith("More reads go to disk") and kept[1]["why"] is None
    merged = snapshot.with_brief(day, answer["summary"], reports.validate_narrative(answer))
    shown = merged["brief_items"]
    assert merged["brief_source"] == "model" and len(shown) == 3
    assert shown[1]["why"] == snapshot.TOPIC_WHY["spike_traffic"]
    assert shown[2]["why"] == "Nothing to do."
    models.DaySnapshot.model_validate(merged)
    # An older stored answer of five items shows its top three, each explained.
    older = snapshot.with_brief(day, None, {"brief_items": [
        {"topic": "health", "text": "Rollbacks jumped."}, *(
            {"topic": "traffic", "text": f"Traffic line {n}."} for n in range(4))]})
    assert len(older["brief_items"]) == 3
    assert older["brief_items"][0]["why"] == snapshot.ACTION_WHY["rollbacks"]


def test_a_sentence_must_move_the_way_its_numbers_do():
    from features.monitor.snapshot import clean_sentence
    allowed = {"1.2", "6.7", "9", "3"}
    assert not clean_sentence("Latency fell from 1.2 s to 6.7 s a query.", allowed, set())
    assert clean_sentence("Latency rose from 1.2 s to 6.7 s a query.", allowed, set())
    assert clean_sentence("Calls fell from 9 to 3 a minute.", allowed, set())
    assert not clean_sentence("Calls rose from 9 to 3 a minute.", allowed, set())


def test_a_quiet_database_with_a_few_misses_is_not_short_of_memory():
    config = {"settings": [{"parameter": "shared_buffers", "current": "128 MB",
                            "recommended": "4 GB", "status": "ok"}]}
    quiet = {"cache_hit_ratio_min": 0.82, "cache_hit_weighted": 0.93, "disk_reads_per_s": 3}
    busy = {**quiet, "disk_reads_per_s": 2000, "cache_hit_weighted": 0.4}
    found = {mode: {action["id"]: action for action in db_actions.database_actions(
        {"engine": "postgresql", "config_audit": config}, numbers)}
        for mode, numbers in (("quiet", quiet), ("busy", busy))}
    assert found["quiet"]["setting_shared_buffers"]["severity"] == "info"
    assert "cache_hit" not in found["quiet"]
    assert found["busy"]["setting_shared_buffers"]["severity"] == "warn"


def test_a_transaction_caught_between_statements_is_not_an_action():
    passing = {"total_connections": 12, "max_connections": 100, "idle_in_transaction_count": 1,
               "long_running_idle_in_tx": []}
    forgotten = {**passing, "long_running_idle_in_tx": [
        {"pid": 7, "duration_seconds": 420, "application_name": "web", "query_preview": "BEGIN"}]}
    ids = {name: {action["id"] for action in db_actions.database_actions(
        {"engine": "postgresql", "connections": connections}, {})}
        for name, connections in (("passing", passing), ("forgotten", forgotten))}
    assert "idle_in_transaction" not in ids["passing"]
    assert "idle_in_transaction" in ids["forgotten"]


def test_a_relative_setting_comes_with_sql_to_size_and_numbered_steps():
    sql = db_actions.setting_sql("shared_buffers", "25% of RAM", "postgresql")
    assert sql.splitlines() == [
        "-- 25% of the server's RAM: 4GB on a 16 GB server. Change it to fit yours.",
        "ALTER SYSTEM SET shared_buffers = '4GB';"]
    steps = db_actions.setting_steps("shared_buffers", "25% of RAM", "postgresql", True).splitlines()
    assert steps[0] == "Pick a value near 25% of the server's RAM."
    assert "parameter group" in steps[1] and steps[2].startswith("Restart PostgreSQL")

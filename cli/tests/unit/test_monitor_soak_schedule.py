"""Offline checks for the soak schedule and checker assertions."""

from __future__ import annotations

import json
import sqlite3
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/monitor_soak"))

from checker import (assess, incidents, low_impact_ignore, matching_keys,
                     merge_evidence, notification_count, read_ledger)  # noqa: E402
from common import HOT_COUNT_SQL, HOT_SQL, INDEX, heavy_sql  # noqa: E402
from incidents import (Runner, Step, request_stop, schedule,
                       table_slowdown_minutes)  # noqa: E402


def snapshot(kind: str = "index_regression") -> dict:
    sql = "SELECT id, total FROM orders WHERE total >= $1 AND total < $2 ORDER BY total LIMIT $3"
    key = "hot-key"
    return {
        "queries": [{"key": key, "text": sql}],
        "runs": [{"ts": 1020, "shortlist": [{"query_key": key}], "dry_run": False,
                  "outcome": "filed", "verdict": {"queries": [
                      {"query_key": key, "kind": "latency_regression",
                       "attention": "notify"}]}}],
        "items": [{"id": 1, "target": "localdev", "type": "latency_regression",
                   "subject": key, "created_at": 1025, "updated_at": 1400,
                   "state": "resolved", "resolved_at": 1400}],
        "changes": [{"ts": 1010, "kind": "dropped", "index_name": INDEX},
                    {"ts": 1310, "kind": "added", "index_name": INDEX}],
        "jobs": [],
    }


def test_schedule_is_repeatable_and_reversible():
    steps = schedule()
    assert len(steps) == 12
    assert [step.minute for step in steps] == sorted(step.minute for step in steps)
    assert max(step.minute for step in steps) < 180
    assert [(step.kind, step.phase) for step in steps] == [
        (kind, phase) for kind in (
            "index_regression", "new_heavy_query", "traffic_spike",
            "query_stopped_running", "stuck_query", "table_slowdown")
        for phase in ("start", "end")]
    starts = {step.kind: step.minute for step in steps if step.phase == "start"}
    ends = {step.kind: step.minute for step in steps if step.phase == "end"}
    assert ends["traffic_spike"] - starts["traffic_spike"] == 20
    assert ends["query_stopped_running"] - starts["query_stopped_running"] == 45
    assert ends["stuck_query"] - starts["stuck_query"] == 3
    assert ends["table_slowdown"] - starts["table_slowdown"] == 11


def test_table_slowdown_covers_dev_judge_and_collector_phase():
    assert table_slowdown_minutes(600) == 16
    steps = schedule(judge_interval_s=600)
    times = {step.phase: step.minute for step in steps if step.kind == "table_slowdown"}
    assert times == {"start": 160, "end": 176}
    with pytest.raises(ValueError, match="too short"):
        schedule(judge_interval_s=900)


def test_table_slowdown_uses_two_stable_indexed_order_queries(monkeypatch):
    assert HOT_SQL != HOT_COUNT_SQL
    assert "FROM orders WHERE total >=" in HOT_SQL
    assert "FROM orders WHERE total >=" in HOT_COUNT_SQL
    ddl = []
    monkeypatch.setattr("incidents.ledger_event", lambda *args, **kwargs: {})
    runner = Runner(judge_interval_s=600)
    monkeypatch.setattr(runner, "ddl", ddl.append)
    runner.apply(Step(160, "table_slowdown", "start"), 1)
    runner.apply(Step(176, "table_slowdown", "end"), 1)
    assert ddl == [f"DROP INDEX IF EXISTS public.{INDEX}",
                   f"CREATE INDEX IF NOT EXISTS {INDEX} ON public.orders (total)"]


def test_heavy_sql_has_a_distinct_normalized_shape_each_cycle_and_run():
    run_id = "0123456789abcdef" * 2
    shapes = [heavy_sql(cycle, run_id) for cycle in range(1, 9)]
    assert len(set(shapes)) == len(shapes)
    assert all("JOIN orders o ON o.customer_id = c.id" in sql and
               "GROUP BY c.country ORDER BY sum(o.total) DESC" in sql
               for sql in shapes)
    assert [sql.count("max(o.total)") for sql in shapes] == [65, 65, 66, 65, 66, 66, 67, 65]
    assert heavy_sql(1, "f" * 32) != heavy_sql(1, "0" * 32)
    assert len(heavy_sql(2 ** 60, run_id)) < 3000


def test_heavy_phase_passes_cycle_and_run_to_load(monkeypatch):
    controls = []
    monkeypatch.setattr("incidents.write_control", lambda **values: controls.append(values))
    monkeypatch.setattr("incidents.ledger_event", lambda *args, **kwargs: {})
    runner = Runner()
    runner.apply(Step(50, "new_heavy_query", "start"), 2)
    runner.apply(Step(70, "new_heavy_query", "end"), 2)
    assert controls == [
        {"heavy_enabled": True, "heavy_cycle": 2, "heavy_run_id": runner.run_id},
        {"heavy_enabled": False, "heavy_cycle": 2, "heavy_run_id": runner.run_id},
    ]


def test_stopped_query_runner_restores_steady_load(monkeypatch):
    controls = []
    monkeypatch.setattr("incidents.write_control", lambda **values: controls.append(values))
    monkeypatch.setattr("incidents.ledger_event", lambda *args, **kwargs: {})
    runner = Runner()
    runner.apply(Step(100, "query_stopped_running", "start"), 1)
    assert "query_stopped_running" in runner.active
    runner.apply(Step(145, "query_stopped_running", "end"), 1)
    assert "query_stopped_running" not in runner.active
    assert controls == [{"steady_enabled": False}, {"steady_enabled": True}]


def test_signal_unwinds_sleep_and_restores(monkeypatch):
    calls = []
    runner = Runner()
    monkeypatch.setattr(runner, "prepare", lambda: calls.append("prepare"))
    monkeypatch.setattr(runner, "restore", lambda cycle: calls.append(("restore", cycle)))
    monkeypatch.setattr("incidents.time.sleep", lambda _: request_stop(None, None))
    monkeypatch.setattr("incidents.signal.signal", lambda *_: None)
    runner.run()
    assert calls == ["prepare", ("restore", 1)]


def test_signal_mid_incident_restores(monkeypatch):
    calls = []
    runner = Runner()
    monkeypatch.setattr(runner, "prepare", lambda: calls.append("prepare"))
    monkeypatch.setattr(runner, "restore", lambda cycle: calls.append(("restore", cycle)))
    monkeypatch.setattr("incidents.time.sleep", lambda _: None)
    monkeypatch.setattr(runner, "apply", lambda *_: request_stop(None, None))
    monkeypatch.setattr("incidents.signal.signal", lambda *_: None)
    runner.run()
    assert calls == ["prepare", ("restore", 1)]


def test_runner_restores_original_index_and_controls_after_interruption(monkeypatch, tmp_path):
    calls = []
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"index_existed": false}\n', encoding="ascii")
    monkeypatch.setattr("incidents.BASELINE", baseline)
    monkeypatch.setattr("incidents.write_control", lambda **values: calls.append(values))
    monkeypatch.setattr("incidents.ledger_event", lambda *args, **kwargs: {})
    runner = Runner()
    runner.index_existed = False
    runner.active = {"index_regression", "traffic_spike"}
    monkeypatch.setattr(runner, "ddl", lambda sql: calls.append(sql))
    runner.restore(1)
    assert not baseline.exists()
    assert any("CREATE INDEX IF NOT EXISTS" in value for value in calls if isinstance(value, str))
    assert any("DROP INDEX IF EXISTS" in value for value in calls if isinstance(value, str))
    assert calls[-3] == {"rate_multiplier": 1.0, "heavy_enabled": False,
                         "steady_enabled": True}


def test_start_refuses_when_only_one_soak_process_is_alive(tmp_path):
    here = tmp_path / "scripts" / "monitor_soak"
    here.mkdir(parents=True)
    wrapper = here / "run_soak.sh"
    shutil.copyfile(Path(__file__).resolve().parents[2] / "scripts/monitor_soak/run_soak.sh", wrapper)
    logs = tmp_path / "docs/plans/continuous_analysis/logs/soak"
    logs.mkdir(parents=True)
    (logs / "load.pid").write_text(f"{os.getpid()}\n", encoding="ascii")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("kill", "exit 0\n"),
                       ("ps", 'echo "$TEST_SOAK_SCRIPT_DIR/load.py"\n')):
        executable = bin_dir / name
        executable.write_text("#!/bin/sh\n" + body, encoding="ascii")
        executable.chmod(0o755)
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
           "TEST_SOAK_SCRIPT_DIR": str(here)}
    result = subprocess.run(["bash", str(wrapper), "start"], env=env,
                            capture_output=True, text=True, timeout=5, check=False)
    assert result.returncode == 1
    assert "running load process" in result.stderr
    assert not (logs / "incidents.pid").exists()


def test_stop_waits_then_reports_sigkill_for_unresponsive_process(tmp_path):
    here = tmp_path / "scripts" / "monitor_soak"
    here.mkdir(parents=True)
    wrapper = here / "run_soak.sh"
    shutil.copyfile(Path(__file__).resolve().parents[2] / "scripts/monitor_soak/run_soak.sh", wrapper)
    logs = tmp_path / "docs/plans/continuous_analysis/logs/soak"
    logs.mkdir(parents=True)
    (logs / "load.pid").write_text(f"{os.getpid()}\n", encoding="ascii")
    sleep_log = tmp_path / "sleeps"
    setup = tmp_path / "bash_env"
    setup.write_text(
        'kill() { if [[ "$1" == "-KILL" ]]; then KILLED=1; fi; return 0; }\n'
        'ps() { case "$*" in *args=*) echo "$TEST_SOAK_SCRIPT_DIR/load.py";; '
        '*pgid=*) echo 4242;; *stat=*) if [[ "${KILLED:-0}" == 0 ]]; then echo S; fi;; esac; }\n'
        'sleep() { echo 1 >> "$TEST_SLEEP_LOG"; }\n', encoding="ascii")
    env = {**os.environ, "BASH_ENV": str(setup), "TEST_SOAK_SCRIPT_DIR": str(here),
           "TEST_SLEEP_LOG": str(sleep_log)}
    result = subprocess.run(["bash", str(wrapper), "stop"], env=env,
                            capture_output=True, text=True, timeout=5, check=False)
    assert result.returncode == 0
    assert len(sleep_log.read_text(encoding="ascii").splitlines()) == 30
    assert "load exceeded 30s shutdown grace; sending SIGKILL" in result.stderr
    assert not (logs / "load.pid").exists()


def test_start_refuses_orphan_without_pid_file(tmp_path):
    here = tmp_path / "scripts" / "monitor_soak"
    here.mkdir(parents=True)
    wrapper = here / "run_soak.sh"
    shutil.copyfile(Path(__file__).resolve().parents[2] / "scripts/monitor_soak/run_soak.sh", wrapper)
    setup = tmp_path / "bash_env"
    setup.write_text('pgrep() { echo 123; }\n', encoding="ascii")
    result = subprocess.run(["bash", str(wrapper), "start"],
                            env={**os.environ, "BASH_ENV": str(setup)},
                            capture_output=True, text=True, timeout=5, check=False)
    assert result.returncode == 1
    assert "running load process" in result.stderr


def test_stuck_checker_uses_collector_sample_without_judge_shortlist():
    data = snapshot()
    data["queries"] = []
    data["runs"] = []
    data["items"] = [{"id": 2, "target": "localdev", "type": "stuck_query",
                      "subject": "stuck-key", "created_at": 1100, "updated_at": 1400,
                      "state": "resolved", "resolved_at": 1400}]
    data["stuck_samples"] = [{"ts": 1100, "query_key": "stuck-key"}]
    result = assess({"cycle": 1, "kind": "stuck_query", "start": 1000, "end": 1300},
                    data, 1500)
    assert result["status"] == "pass"
    assert "shortlist" not in result["checks"]
    assert result["checks"]["stuck_sample"]
    data["items"][0]["resolved_at"] = 1270
    assert assess({"cycle": 1, "kind": "stuck_query", "start": 1000,
                   "end": 1300}, data, 1500)["status"] == "pass"


def test_unfiled_incident_has_no_recovery_obligation():
    data = snapshot()
    data["items"] = []
    result = assess({"cycle": 1, "kind": "index_regression", "start": 1000,
                     "end": 1300}, data, 1500)
    assert result["checks"]["recovered"]
    assert not result["checks"]["inbox_or_dry_run_verdict"]


def test_later_cycle_item_does_not_satisfy_earlier_detection():
    data = snapshot()
    data["runs"][0]["ts"] = 5000
    data["items"][0].update(created_at=5000, updated_at=5000,
                            resolved_at=None, state="new")
    data["item_events"] = [{"item_id": 1, "ts": 5000, "kind": "created"}]
    result = assess({"cycle": 1, "kind": "index_regression", "start": 1000,
                     "end": 1300}, data, 6000)
    assert not result["checks"]["shortlist"]
    assert not result["checks"]["inbox_or_dry_run_verdict"]
    assert result["checks"]["recovered"]


def test_login_error_is_auth_required_only_for_jev_incident():
    data = snapshot()
    data["items"] = []
    data["runs"][0].update(outcome="error", verdict={"code": "LOGIN_REQUIRED"})
    incident = {"cycle": 1, "kind": "index_regression", "start": 1000,
                "end": 1300}
    assert assess(incident, data, 5000)["status"] == "auth_required"
    stuck = {**incident, "kind": "stuck_query"}
    assert assess(stuck, data, 5000)["status"] == "fail"


def test_ledger_pairing_and_successful_recovery():
    pairs = incidents([{"cycle": 2, "kind": "index_regression", "phase": "start", "ts": 1000},
                       {"cycle": 2, "kind": "index_regression", "phase": "end", "ts": 1300}])
    assert pairs == [{"run_id": None, "cycle": 2, "kind": "index_regression", "start": 1000, "end": 1300}]
    result = assess(pairs[0], snapshot(), 1500)
    assert result["status"] == "pass"
    assert all(result["checks"].values())


def test_current_run_excludes_older_ledger_entries():
    events = [
        {"run_id": "old", "cycle": 1, "kind": "index_regression", "phase": "start", "ts": 1000},
        {"run_id": "new", "cycle": 1, "kind": "index_regression", "phase": "start", "ts": 2000},
        {"run_id": "new", "cycle": 1, "kind": "index_regression", "phase": "end", "ts": 2300},
    ]
    assert [item["start"] for item in incidents(events, current_run_only=True)] == [2000]
    assert [item["start"] for item in incidents(events)] == [1000, 2000]


def test_archived_ledger_is_only_read_on_request(monkeypatch, tmp_path):
    active = tmp_path / "incidents.jsonl"
    archive = tmp_path / "incidents.archive.jsonl"
    active.write_text(json.dumps({"run_id": "new", "phase": "start", "ts": 2}) + "\n",
                      encoding="ascii")
    archive.write_text(json.dumps({"run_id": "old", "phase": "start", "ts": 1}) + "\n",
                       encoding="ascii")
    monkeypatch.setattr("checker.ARCHIVE", archive)
    assert [event["run_id"] for event in read_ledger(active)] == ["new"]
    assert [event["run_id"] for event in read_ledger(active, include_archive=True)] == ["old", "new"]


def test_database_wide_filing_tracks_affected_query_at_event_time():
    data = snapshot()
    data["items"] = [{"id": 15, "target": "localdev", "type": "many_queries_changed",
                      "subject": "database", "state": "new"}]
    data["runs"][0]["verdict"]["queries"][0]["attention"] = "notify"
    data["runs"][0]["verdict"]["queries"].append(
        {"query_key": "other-key", "kind": "query_improved", "attention": "inbox"})
    data["runs"][0]["verdict"]["event_scope"] = "database_wide"
    data["item_events"] = [
        {"item_id": 15, "ts": 1020, "kind": "updated",
         "detail_json": json.dumps({"query_keys": ["hot-key", "other-key"]})},
        {"item_id": 15, "ts": 1400, "kind": "updated",
         "detail_json": json.dumps({"query_keys": ["other-key"],
                                    "recovered_queries": {"hot-key": {}}})},
    ]
    incident = {"cycle": 1, "kind": "index_regression", "start": 1000, "end": 1300}
    result = assess(incident, data, 1500)
    assert result["status"] == "pass"
    assert result["checks"]["inbox_or_dry_run_verdict"]
    assert result["checks"]["recovered"]
    data["item_events"][1] = {"item_id": 15, "ts": 1400, "kind": "reopened",
                              "detail_json": json.dumps({"query_keys": ["other-key"]})}
    assert not assess(incident, data, 3000)["checks"]["recovered"]
    data["item_events"][0]["detail_json"] = json.dumps({"query_keys": ["hot-key"]})
    assert assess(incident, data, 3000)["checks"]["inbox_or_dry_run_verdict"]
    data["runs"][0]["verdict"]["queries"].pop()
    assert not assess(incident, data, 3000)["checks"]["inbox_or_dry_run_verdict"]
    data["runs"][0]["verdict"]["queries"].append(
        {"query_key": "other-key", "kind": "query_improved", "attention": "inbox"})
    data["item_events"][0]["detail_json"] = json.dumps({"query_keys": ["other-key"]})
    result = assess(incident, data, 3000)
    assert result["status"] == "fail"
    assert not result["checks"]["inbox_or_dry_run_verdict"]


def test_new_heavy_checker_uses_current_cycle_shape_and_kind():
    data = snapshot()
    run_id = "0123456789abcdef" * 2
    data["queries"] = [
        {"key": "old-key", "text": heavy_sql(1, run_id)},
        {"key": "new-key", "text": heavy_sql(2, run_id).replace(", ", " , ")},
    ]
    data["runs"][0]["shortlist"] = [{"query_key": "new-key"}]
    data["runs"][0]["verdict"]["queries"] = [
        {"query_key": "old-key", "kind": "traffic_spike", "attention": "notify"},
        {"query_key": "new-key", "kind": "traffic_spike", "attention": "notify"}]
    data["items"][0].update(type="traffic_spike", subject="new-key")
    data["item_events"] = [{"item_id": 1, "ts": 1025, "kind": "created"}]
    incident = {"run_id": run_id, "cycle": 2, "kind": "new_heavy_query",
                "start": 1000, "end": 1300}
    assert matching_keys("new_heavy_query", data, 1000, 1300,
                         cycle=2, run_id=run_id) == {"new-key"}
    assert not assess(incident, data, 3000)["checks"]["inbox_or_dry_run_verdict"]
    data["runs"][0]["verdict"]["queries"][1]["kind"] = "new_heavy_query"
    data["items"][0]["type"] = "new_heavy_query"
    assert assess(incident, data, 1500)["status"] == "pass"


def test_traffic_spike_still_expects_reused_query_kind():
    data = snapshot()
    data["runs"][0]["verdict"]["queries"][0]["kind"] = "traffic_spike"
    data["items"][0]["type"] = "traffic_spike"
    data["item_events"] = [{"item_id": 1, "ts": 1025, "kind": "created"}]
    assert assess({"cycle": 2, "kind": "traffic_spike", "start": 1000,
                   "end": 1300}, data, 1500)["status"] == "pass"


def test_stopped_query_deadline_allows_next_judge_after_thirty_minutes():
    data = snapshot()
    data["queries"] = [{"key": "steady", "text":
                        "SELECT count(*) FROM customers WHERE country = $1"}]
    data["runs"][0].update(ts=2802, shortlist=[{"query_key": "steady"}],
                           verdict={"queries": [{"query_key": "steady",
                                                  "kind": "query_stopped_running",
                                                  "attention": "inbox"},
                                                 {"query_key": "other",
                                                  "kind": "query_improved",
                                                  "attention": "inbox"}]})
    data["items"] = [{"id": 15, "target": "localdev", "type": "many_queries_changed",
                      "subject": "database", "state": "new"}]
    data["item_events"] = [{"item_id": 15, "ts": 2802, "kind": "updated",
                            "detail_json": json.dumps({"query_keys": ["other"],
                                                       "recovered_queries": {
                                                           "steady": {"kind": "traffic_spike"}}})}]
    incident = {"cycle": 1, "kind": "query_stopped_running", "start": 1000,
                "end": None}
    checks = assess(incident, data, 3500, judge_interval_s=600)["checks"]
    assert checks["shortlist"] and not checks["inbox_or_dry_run_verdict"]
    data["item_events"].append({"item_id": 15, "ts": 2802, "kind": "updated",
                                "detail_json": json.dumps({"query_keys": ["steady", "preexisting"],
                                                           "recovery_queries": {
                                                               "steady": {"kind":
                                                                          "query_stopped_running"}}})})
    checks = assess(incident, data, 3500, judge_interval_s=600)["checks"]
    assert checks["shortlist"] and checks["inbox_or_dry_run_verdict"]


def test_missing_index_change_and_duplicate_fail_at_deadline():
    data = snapshot()
    data["changes"] = []
    data["items"].append(dict(data["items"][0], id=2))
    result = assess({"cycle": 1, "kind": "index_regression", "start": 1000, "end": 1300},
                    data, 3000)
    assert result["status"] == "fail"
    assert result["checks"]["index_change"] is False
    assert result["checks"]["no_duplicates"] is False


def test_dry_run_needs_matching_verdict_and_shortlist():
    data = snapshot()
    data["items"] = []
    data["runs"][0]["dry_run"] = True
    data["runs"][0]["outcome"] = "dry_run"
    active = {"cycle": 1, "kind": "index_regression", "start": 1000, "end": None}
    assert assess(active, data, 1200)["checks"]["inbox_or_dry_run_verdict"]
    data["runs"][0]["verdict"] = None
    assert not assess(active, data, 1200)["checks"]["inbox_or_dry_run_verdict"]


def test_dry_run_has_no_inbox_item_to_close():
    data = snapshot()
    data["items"] = []
    data["runs"][0]["dry_run"] = True
    data["runs"][0]["outcome"] = "dry_run"
    incident = {"cycle": 1, "kind": "index_regression", "start": 1000, "end": 1300}
    assert assess(incident, data, 1500)["status"] == "pass"


def test_table_slowdown_checker_requires_judge_coverage():
    data = snapshot()
    data["queries"] = [{"key": "hot-key", "text": HOT_SQL},
                       {"key": "count-key", "text": HOT_COUNT_SQL}]
    data["runs"][0]["shortlist"] = [{"query_key": "hot-key"},
                                    {"query_key": "count-key"}]
    data["runs"][0]["verdict"]["queries"] = [
        {"query_key": key, "kind": "table_slowdown", "attention": "notify"}
        for key in ("hot-key", "count-key")]
    data["runs"][0].update(dry_run=True, outcome="dry_run")
    data["items"] = []
    data["changes"][1]["ts"] = 1970
    incident = {"cycle": 1, "kind": "table_slowdown", "start": 1000, "end": 1300}
    short = assess(incident, data, 3000, judge_interval_s=600)
    assert short["status"] == "fail"
    assert short["checks"]["inbox_or_dry_run_verdict"]
    assert not short["checks"]["judge_coverage"]
    long = assess({**incident, "end": 1960}, data, 3000, judge_interval_s=600)
    assert long["checks"]["judge_coverage"]
    assert long["status"] == "pass"
    data["runs"][0]["verdict"]["queries"].pop()
    assert not assess({**incident, "end": 1960}, data, 3000,
                      judge_interval_s=600)["checks"]["inbox_or_dry_run_verdict"]


def test_low_impact_ignore_needs_explicit_verdict_and_small_db_time():
    run = {"request": {"queries": [{"query_key": "q",
            "current_5m": {"qps": 4, "calls": 1200},
            "baseline": {"1h": {"qps": 2, "mean_ms": .2}},
            "extra_db_time_ms_per_5m_vs_1h": 0}]},
           "verdict": {"queries": [{"query_key": "q", "attention": "ignore"}]}}
    assert low_impact_ignore("traffic_spike", run, {"q"})
    run["request"]["queries"][0]["extra_db_time_ms_per_5m_vs_1h"] = 1001
    assert not low_impact_ignore("traffic_spike", run, {"q"})
    run["request"]["queries"][0]["current_5m"]["calls"] = 0
    assert low_impact_ignore("query_stopped_running", run, {"q"})
    run["request"]["queries"][0]["baseline"]["1h"]["mean_ms"] = 2
    assert not low_impact_ignore("query_stopped_running", run, {"q"})
    run["verdict"]["queries"][0]["attention"] = "notify"
    assert not low_impact_ignore("query_stopped_running", run, {"q"})


def test_update_to_already_resolved_item_is_not_a_filed_incident():
    data = snapshot()
    data["queries"][0]["text"] = (
        "SELECT c.country FROM customers c JOIN orders o ON o.customer_id = c.id "
        "GROUP BY c.country")
    data["runs"][0]["verdict"]["queries"][0]["kind"] = "new_heavy_query"
    data["items"][0].update(type="new_heavy_query", state="resolved",
                            resolved_at=900, created_at=800, updated_at=1100)
    data["item_events"] = [{"item_id": 1, "ts": 1100, "kind": "updated"}]
    result = assess({"cycle": 2, "kind": "new_heavy_query", "start": 1000,
                     "end": 1300}, data, 3000)
    assert not result["checks"]["inbox_or_dry_run_verdict"]
    assert result["status"] == "fail"


def test_login_error_is_visible_but_not_a_verdict():
    data = snapshot()
    data["items"] = []
    data["runs"][0].update(outcome="error", verdict={"code": "LOGIN_REQUIRED"})
    result = assess({"cycle": 1, "kind": "index_regression", "start": 1000, "end": None},
                    data, 1200)
    assert result["status"] == "auth_required"
    assert not result["checks"]["inbox_or_dry_run_verdict"]


def test_notification_count_reads_only_current_day(tmp_path):
    db = tmp_path / "monitor.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("CREATE TABLE inbox_item (id INTEGER, target TEXT);"
                           "CREATE TABLE inbox_event (item_id INTEGER, ts INTEGER, kind TEXT);")
        conn.execute("INSERT INTO inbox_item VALUES (1, 'localdev')")
        conn.executemany("INSERT INTO inbox_event VALUES (1, ?, 'notified')",
                         [(10,), (86410,), (86411,)])
    assert notification_count(tmp_path, 86420) == 2


def test_evidence_retains_old_runs_and_resolved_state():
    first = snapshot()
    first["runs"][0]["id"] = 1
    first["changes"][0]["id"] = 1
    first["changes"][1]["id"] = 2
    first["stuck_samples"] = [{"ts": 1100, "query_key": "stuck-key"}]
    second = {"runs": [], "changes": [], "queries": [], "items": [], "jobs": []}
    merged = merge_evidence(merge_evidence(None, first), second)
    assert len(merged["runs"]) == 1
    assert len(merged["changes"]) == 2
    assert len(merged["resolutions"]) == 1
    assert merged["stuck_samples"] == first["stuck_samples"]


@pytest.mark.parametrize("field,reason", [("smaller", "small"), ("minor", "minor")])
def test_spike_on_a_query_the_gate_keeps_from_jev_passes_without_an_item(field, reason):
    data = snapshot()
    data["runs"] = [{"ts": 1020, "shortlist": [], "dry_run": False, "outcome": "empty",
                     "request": None,
                     "verdict": {"gate": {"smaller": [], "minor": [], "recurring": {},
                                          field: ["hot-key"]}}}]
    data["items"] = []
    result = assess({"cycle": 1, "kind": "traffic_spike", "start": 1000, "end": 1300},
                    data, 4000)
    assert result["status"] == "pass" and result["gated"] == reason


def test_recurring_burst_kept_quiet_passes_and_says_why():
    data = snapshot()
    pattern = {"metric": "calls", "kind": "period", "repeats": 3, "period_s": 10800,
               "last_onset": 1000, "usual_peak": 400.0, "usual_duration_s": 1200,
               "verdict": "usual"}
    data["runs"] = [{"ts": 1020, "shortlist": [], "dry_run": False, "outcome": "filed",
                     "request": {"queries": [], "gate": {"smaller": [], "minor": [],
                                                         "recurring": {"hot-key": pattern}}},
                     "verdict": {"queries": []}}]
    data["items"] = []
    result = assess({"cycle": 5, "kind": "traffic_spike", "start": 1000, "end": 1300},
                    data, 4000)
    assert result["status"] == "pass" and result["gated"] == "recurring"


def test_stuck_query_is_never_excused_by_the_gate():
    data = snapshot()
    data["runs"] = [{"ts": 1020, "shortlist": [], "outcome": "empty", "request": None,
                     "verdict": {"gate": {"smaller": ["hot-key"]}}}]
    data["items"] = []
    data["stuck_samples"] = []
    result = assess({"cycle": 1, "kind": "stuck_query", "start": 1000, "end": 1300},
                    data, 4000)
    assert result["status"] == "fail" and result["gated"] is None


def test_jittered_cycles_shuffle_incidents_and_never_overlap():
    from incidents import PHASE_MINUTES, jittered_schedule

    first, length = jittered_schedule("run:1", 600)
    assert jittered_schedule("run:1", 600) == (first, length)
    starts = {step.kind: step.minute for step in first if step.phase == "start"}
    assert set(starts) == set(PHASE_MINUTES) | {"table_slowdown"}
    ordered = sorted(first, key=lambda step: (step.minute, step.phase == "start"))
    active = set()
    for step in ordered:
        if step.phase == "start":
            assert not active, "incidents never overlap"
            active.add(step.kind)
        else:
            active.discard(step.kind)
    assert length > max(step.minute for step in first)
    # Different cycles put the same incident at different times.
    offsets = {jittered_schedule(f"run:{cycle}", 600)[0][0].kind for cycle in range(12)}
    minutes = {next(step.minute for step in jittered_schedule(f"run:{cycle}", 600)[0]
                    if step.kind == "index_regression") for cycle in range(12)}
    assert len(offsets) > 1 and len(minutes) > 6

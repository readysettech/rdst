"""Report selection, persistence, and fallback contracts."""

import asyncio
import json
import selectors
import threading
from datetime import datetime

import pytest
from pydantic import ValidationError

from features.monitor import report as reports
from shared.monitor.monitor_store import MonitorStore
from shared.llm_manager.inference_attribution import record_billable_call

NOW = 2_000_000


@pytest.fixture(autouse=True)
def _responsive_selector(monkeypatch):
    original_select = selectors.EpollSelector.select
    monkeypatch.setattr(selectors.EpollSelector, "select",
                        lambda self, timeout=None: original_select(
                            self, 0.01 if timeout is None else min(timeout, 0.01)))


async def test_report_thread_shutdown_serializes_restart():
    entered, release, restarted = threading.Event(), threading.Event(), threading.Event()

    def blocked():
        entered.set()
        release.wait(5)

    old = asyncio.create_task(reports._to_thread("db", blocked))
    assert await asyncio.to_thread(entered.wait, 2)
    old.cancel()
    try:
        await old
    except asyncio.CancelledError:
        pass
    stopping = asyncio.create_task(reports.stop_report_threads())
    await asyncio.sleep(0.05)
    reports._work("db", restart=True)
    new = asyncio.create_task(reports._to_thread("db", restarted.set))
    await asyncio.sleep(0.05)
    assert not restarted.is_set()
    release.set()
    await stopping
    await new
    assert restarted.is_set()


async def test_model_thread_skips_the_target_lock_but_honours_stop():
    from features.monitor import run_guard

    def lock_is_free():
        lock = run_guard._locks.setdefault("db", threading.Lock())
        if not lock.acquire(blocking=False):
            return False
        lock.release()
        return True

    assert await reports._model_thread("db", lock_is_free)
    reports._work("db", restart=True).guard.stopped.set()
    calls = []
    with pytest.raises(asyncio.CancelledError):
        await reports._model_thread("db", calls.append, 1)
    assert calls == []
    await reports.stop_report_threads()


class Clock:
    def __init__(self, value=NOW):
        self.value = value

    def __call__(self):
        return self.value

    async def advance(self, seconds):
        self.value += max(60, seconds)
        await asyncio.sleep(0)


def _store(tmp_path):
    return MonitorStore(tmp_path / "monitor.db")


def _seed(store, count=1, at=NOW - 60):
    for index in range(count):
        key = f"q{index:02d}"
        store.record("db", at,
                     [{"query_key": key, "calls": 10,
                       "total_ms": (index + 1) * 100}],
                     {key: f"SELECT {index} FROM orders"}, [])


def _narrative(context):
    return {"summary": "Review orders", "fix_first": [],
            "one_fix_several_queries": [{"table": "orders", "columns": ["customer_id"],
                                         "query_keys": ["q00", "q01"],
                                         "action": "Add an index"}],
            "health": [], "changed_since_yesterday": ["Traffic changed"]}


async def _run(store, clock, **kwargs):
    return await reports.run_report("db", "user", store=store, clock=clock,
                                    sleep=clock.advance,
                                    explain=lambda target, sql: {"plan": ["scan"]},
                                    collect=lambda target: ({"engine": "postgresql"}, {"tables": {}}),
                                    narrate=_narrative, **kwargs)


@pytest.mark.asyncio
async def test_top_15_and_provisional_fallback_after_one_budget(tmp_path):
    store = _store(tmp_path)
    _seed(store, 17)
    clock = Clock()
    result = await _run(store, clock)
    assert result["top_json"] == [f"q{i:02d}" for i in range(16, 1, -1)]
    assert result["provisional"] is True
    assert result["progress"] == {"message": "Report ready", "done": 15, "total": 15}
    assert result["report_json"]["provisional_query_keys"] == result["top_json"]
    assert clock.value == NOW + 60
    assert all(item["provisional"] and item["explain"]["plan"] == ["scan"]
               for item in result["report_json"]["computed"]["analyses"])


@pytest.mark.asyncio
async def test_reuse_window_and_one_active_run(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    active, claimed = store.claim_report("db", "user", NOW)
    assert claimed
    other, claimed = MonitorStore(store.path).claim_report("db", "user", NOW + 1)
    assert not claimed and other["id"] == active["id"]
    store.update_report(active["id"], state="done", report={"summary": "ready"})
    reused, claimed = store.claim_report("db", "user", NOW + 899)
    assert not claimed and reused["id"] == active["id"]
    fresh, claimed = store.claim_report("db", "user", NOW + 901)
    assert claimed and fresh["id"] != active["id"]


@pytest.mark.asyncio
async def test_cancelled_report_releases_active_run(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _seed(store)
    entered = asyncio.Event()

    async def inline_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(reports.asyncio, "to_thread", inline_thread)

    async def wait_for_cancel(_seconds):
        entered.set()
        await asyncio.Future()

    task = asyncio.create_task(reports.run_report(
        "db", "user", store=store, clock=lambda: NOW, sleep=wait_for_cancel))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    old = store.read("SELECT id,state FROM report_run")[0]
    assert old["state"] == "failed"
    new, claimed = store.claim_report("db", "user", NOW + 1)
    assert claimed and new["id"] != old["id"]


@pytest.mark.asyncio
async def test_nightly_report_falls_back_when_analysis_cap_is_exhausted(tmp_path, monkeypatch):
    from shared.monitor.monitor_store import local_day_bounds

    store = _store(tmp_path)
    # The nightly report covers the day that ended.
    _seed(store, at=local_day_bounds(NOW)[0] - 3600)
    store.enqueue_analysis("db", "q00", "new", 100, NOW - 1)
    monkeypatch.setenv("RDST_MONITOR_ANALYZE_DAILY_CAP", "0")

    async def inline_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    async def unexpected_sleep(_seconds):
        raise AssertionError("Nightly report waited on a job blocked by the daily cap")

    monkeypatch.setattr(reports.asyncio, "to_thread", inline_thread)
    result = await reports.run_report(
        "db", "nightly", store=store, clock=lambda: NOW, sleep=unexpected_sleep,
        explain=lambda target, sql: {"plan": ["scan"]},
        collect=lambda target: ({"engine": "postgresql"}, {"tables": {}}),
        narrate=_narrative)
    assert result["state"] == "done"
    assert result["provisional"] is True


@pytest.mark.asyncio
async def test_nightly_report_stops_waiting_at_its_deadline(tmp_path, monkeypatch):
    """Analyses that never finish don't keep the nightly run going: past its deadline it
    finishes with plans only for them."""
    from shared.monitor.monitor_store import local_day_bounds

    store = _store(tmp_path)
    _seed(store, 2, at=local_day_bounds(NOW)[0] - 3600)
    monkeypatch.setenv("RDST_MONITOR_ANALYZE_DAILY_CAP", "50")
    clock = Clock()
    result = await reports.run_report(
        "db", "nightly", store=store, clock=clock, sleep=clock.advance,
        explain=lambda target, sql: {"plan": ["scan"]},
        collect=lambda target: ({"engine": "postgresql"}, {"tables": {}}),
        narrate=_narrative)
    assert result["state"] == "done"
    assert result["provisional"] is True
    assert clock.value == NOW + reports.NIGHTLY_WAIT_SECONDS
    assert all(item["provisional"] and item["explain"]["plan"] == ["scan"]
               for item in result["report_json"]["computed"]["analyses"])


def test_rolling_delta_math_and_zero_baseline(tmp_path):
    store = _store(tmp_path)
    store.record("db", NOW - 60, [{"query_key": "q", "calls": 20, "total_ms": 200}],
                 {"q": "SELECT * FROM orders"}, [])
    store.record("db", NOW - 86400 - 60,
                 [{"query_key": "q", "calls": 10, "total_ms": 50}],
                 {"q": "SELECT * FROM orders"}, [])
    deltas = reports.rolling_deltas(store, "db", NOW)
    q = deltas["24h"]["queries"]["q"]
    assert q["calls"] == {"current": 20, "prior": 10, "absolute": 10, "ratio": 1.0}
    assert q["mean_ms"]["ratio"] == 1.0
    assert deltas["24h"]["database"]["total_ms"]["ratio"] == 3.0
    assert deltas["7d"]["database"]["calls"]["ratio"] is None


def test_plain_explain_has_connection_timeout_and_self_marker(monkeypatch):
    calls = []

    class Cursor:
        def execute(self, sql):
            calls.append(sql)

        def fetchall(self):
            return [("Seq Scan",)]

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def rollback(self):
            pass

        def close(self):
            pass

    class Config:
        def load(self):
            pass

        def get(self, _target):
            return {"engine": "postgresql"}

    def connect(_config, **kwargs):
        assert kwargs == {"lane": "rdst/analyze", "query_timeout_seconds": 5}
        return Connection()

    monkeypatch.setattr("shared.config.targets.TargetsConfig", Config)
    monkeypatch.setattr("shared.db_connection.create_direct_connection", connect)
    assert reports.plain_explain("db", "SELECT 1") == {"plan": ["Seq Scan"]}
    assert calls == ["/*rdst:analyze*/ EXPLAIN SELECT 1"]


@pytest.mark.asyncio
async def test_llm_failure_ships_computed_sections_and_unique_summary(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    clock = Clock()
    result = await reports.run_report(
        "db", "user", store=store, clock=clock, sleep=clock.advance,
        explain=lambda target, sql: {"plan": ["scan"]},
        collect=lambda target: ({"engine": "postgresql"}, {"tables": {}}),
        narrate=lambda context: (_ for _ in ()).throw(RuntimeError("LLM down")))
    assert result["state"] == "done"
    assert result["report_json"]["narrative_error"] == "LLM down"
    assert result["report_json"]["computed"]["deltas"]["24h"]["database"]["calls"]["current"] == 10
    assert result["report_json"]["narrative_retry_at"] == NOW + 900
    # Only the nightly report files the daily report item; a run on request files its own
    # report_ready item in the Inbox, without a push.
    items = store.inbox(target="db")
    assert [(item["type"], item["delivery"]) for item in items] == [("report_ready", "inbox")]
    assert not [event for event in store.inbox_events() if event["kind"] == "notified"]


@pytest.mark.asyncio
async def test_report_findings_stay_in_the_report(tmp_path):
    store = _store(tmp_path)
    _seed(store, 2)
    result = await _run(store, Clock())
    assert result["report_json"]["one_fix_several_queries"][0]["table"] == "orders"
    assert [item["type"] for item in store.inbox(target="db")] == ["report_ready"]


def test_nightly_report_files_one_daily_report_item_without_a_push(tmp_path):
    store = _store(tmp_path)
    for run_id in (7, 8):
        reports._file_report_items(store, "db", run_id, NOW, {"summary": "All steady."},
                                   "nightly")
    items = store.inbox(target="db")
    when = datetime.fromtimestamp(NOW)
    assert [(item["type"], item["title"], item["delivery"]) for item in items] == [
        ("daily_summary", f"Daily report for {when.strftime('%B')} {when.day} is ready", "inbox")]
    assert json.loads(items[0]["body_json"])["report_id"] == 8
    assert not [event for event in store.inbox_events() if event["kind"] == "notified"]


def test_digest_lists_quiet_findings_and_pushes_over_budget(tmp_path):
    store = _store(tmp_path)
    store.set_settings("db", {"push_daily_cap": 1, "quiet_hours": {"from_hour": 0, "to_hour": 0}})
    store.upsert_item("db", "query_improved", "q00", .4, "Query improved",
                      {"query_key": "q00"}, NOW - 100, delivery="digest")
    store.upsert_item("db", "latency_regression", "q01", .8, "Query latency increased",
                      {"query_key": "q01"}, NOW - 90, delivery="notify")
    store.upsert_item("db", "latency_regression", "q02", .8, "Query latency increased",
                      {"query_key": "q02"}, NOW - 80, delivery="notify")
    entries = reports.digest(store, "db", NOW)
    assert [(entry["type"], entry["reason"]) for entry in entries] == [
        ("latency_regression", "push_limit"), ("query_improved", "digest")]


def test_narrative_schema_rejects_missing_and_extra_fields():
    valid = _narrative({})
    assert reports.validate_narrative(json.dumps(valid))["summary"] == "Review orders"
    with pytest.raises(ValidationError):
        reports.validate_narrative({**valid, "extra": 1})
    with pytest.raises(ValidationError):
        reports.validate_narrative({"summary": "incomplete"})


def test_narrative_discards_unidentified_fix_without_losing_valid_findings():
    narrative = {**_narrative({}), "fix_first": [
        {"query_key": "q00", "why": "Measured regression", "action": "Review plan"},
        {"query_key": None, "why": "Unknown query", "action": "Add an index"},
    ]}
    result = reports.validate_narrative(narrative)
    assert result["fix_first"] == narrative["fix_first"][:1]
    assert result["summary"] == "Review orders"


@pytest.mark.asyncio
async def test_fresh_analysis_avoids_explain_and_wait(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _seed(store)
    store.enqueue_analysis("db", "q00", "report", 100, NOW - 10)
    job = store.claim_analysis("db", NOW - 9)
    store.finish_analysis(job["id"], "done", NOW - 1,
                          analysis_ref="/api/query-registry/q00/analysis/123", mode="analyze")
    monkeypatch.setattr(reports, "_analysis_for", lambda job, key: {"query_key": key,
                                                              "analysis_ref": job["analysis_ref"]})
    clock = Clock()
    result = await _run(store, clock)
    assert clock.value == NOW
    assert not result["provisional"]
    assert result["report_json"]["computed"]["analyses"][0]["analysis_ref"].endswith("123")


@pytest.mark.asyncio
async def test_nightly_scheduler_once_per_local_day(tmp_path, monkeypatch):
    from datetime import datetime

    store = _store(tmp_path)
    store.set_settings("db", {"enabled": True})
    now = int(datetime(2026, 9, 25, 2, 5).timestamp())
    # Readings from the day that ended at midnight, the day the nightly report covers.
    day = int(datetime(2026, 9, 24, 23, 0).timestamp())
    store.record("db", day - 120, [{"query_key": "q", "calls": 1, "total_ms": 2}],
                 {"q": "SELECT * FROM orders"}, [])
    store.record("db", day - 60, [], {"q": "SELECT * FROM orders"}, [])
    calls = []

    async def fake_run(target, trigger, **kwargs):
        calls.append((target, trigger))
        store.update_report(kwargs["claimed_run"]["id"], state="done", report={"summary": "done"},
                            finished_at=now,
                            period=reports.snapshot.report_period(trigger, now)[:2])

    monkeypatch.setattr(reports, "run_report", fake_run)
    scheduler = reports.ReportScheduler(store, clock=lambda: now)
    scheduler.tick()
    await asyncio.gather(*scheduler._runs)
    scheduler.tick()
    assert calls == [("db", "nightly")]


@pytest.mark.asyncio
async def test_nightly_waits_for_observed_span_and_positive_top_query(tmp_path, monkeypatch):
    from datetime import datetime

    store = _store(tmp_path)
    store.set_settings("db", {"enabled": True})
    now = int(datetime(2026, 9, 25, 2, 5).timestamp())
    calls = []

    async def fake_run(target, trigger, **kwargs):
        calls.append((target, trigger))
        store.update_report(kwargs["claimed_run"]["id"], state="done",
                            report={"summary": "done"}, finished_at=now)

    monkeypatch.setattr(reports, "run_report", fake_run)
    day = int(datetime(2026, 9, 24, 23, 0).timestamp())
    scheduler = reports.ReportScheduler(store, clock=lambda: now)
    scheduler.tick()
    store.record("db", day - 120, [], {"q": "SELECT * FROM orders"}, [])
    store.record("db", day - 60, [], {"q": "SELECT * FROM orders"}, [])
    scheduler.tick()
    store.record("db", day - 60, [{"query_key": "q", "calls": 1, "total_ms": 2}],
                 {"q": "SELECT * FROM orders"}, [])
    scheduler.tick()
    await asyncio.gather(*scheduler._runs)
    assert calls == [("db", "nightly")]
    assert len(store.read("SELECT id FROM report_run WHERE target='db'")) == 1


@pytest.mark.asyncio
async def test_failed_nightly_report_is_not_restarted_each_minute(tmp_path, monkeypatch):
    from datetime import datetime

    store = _store(tmp_path)
    store.set_settings("db", {"enabled": True})
    now = int(datetime(2026, 9, 25, 2, 5).timestamp())
    day = int(datetime(2026, 9, 24, 23, 0).timestamp())
    store.record("db", day - 120, [{"query_key": "q", "calls": 1, "total_ms": 2}],
                 {"q": "SELECT * FROM orders"}, [])
    store.record("db", day - 60, [], {"q": "SELECT * FROM orders"}, [])
    calls = []

    async def fake_run(target, trigger, **kwargs):
        calls.append((target, trigger))
        store.update_report(kwargs["claimed_run"]["id"], state="failed", finished_at=now,
                            period=reports.snapshot.report_period(trigger, now)[:2])

    monkeypatch.setattr(reports, "run_report", fake_run)
    scheduler = reports.ReportScheduler(store, clock=lambda: now)
    scheduler.tick()
    await asyncio.gather(*scheduler._runs)
    scheduler.tick()
    await asyncio.gather(*scheduler._runs)
    assert calls == [("db", "nightly")]


def test_history_lists_every_completed_report_as_a_summary(tmp_path, monkeypatch):
    from features.monitor.api import routes

    store = _store(tmp_path)
    for stamp in (NOW - 7200, NOW - 3600):
        run, claimed = store.claim_report("db", "user", stamp)
        assert claimed
        store.update_report(run["id"], state="done", finished_at=stamp + 45, report={
            "summary": f"Load is steady. Report {stamp}.",
            "fix_first": [{"query_key": "q00", "why": "w", "action": "a"}],
            "one_fix_several_queries": [],
            "health": [{"check": "deadlocks", "status": "healthy", "note": ""},
                       {"check": "config: work_mem", "status": "warn", "note": ""}],
            "provisional_query_keys": ["q01"]})
    failed, _ = store.claim_report("db", "user", NOW)
    store.update_report(failed["id"], state="failed", finished_at=NOW)
    monkeypatch.setattr(routes, "_store", lambda: store)
    reports_ = routes.report_history("db")["reports"]
    assert [row["ts"] for row in reports_] == [NOW - 3600, NOW - 7200]
    assert reports_[0] == {
        "id": 2, "ts": NOW - 3600, "trigger": "user", "duration_s": 45,
        "headline": "Load is steady.", "verdict": "",
        "counts": {"fix_first": 1, "one_fix": 0, "health_warn": 1, "health_ok": 1},
        "provisional_count": 1, "period_start": NOW - 3600 - 86400, "period_end": NOW - 3600,
        "period_partial": False, "sample": False, "day": reports._legacy_day(
            {"ts": NOW - 3600, "trigger": "user"}),
        "events": None, "open_events": None, "look_at": None, "health_problems": None,
        "qps": None}
    assert len(routes.report_history("db", limit=1)["reports"]) == 1


def test_hosted_report_llm_called_once_with_purpose(monkeypatch):
    from shared import llm_manager

    calls = []

    class FakeLLM:
        def generate_response(self, prompt, **kwargs):
            calls.append((prompt, kwargs))
            return {"response": json.dumps(_narrative({}))}

    monkeypatch.setattr(llm_manager, "LLMManager", FakeLLM)
    result = reports._narrate({"target": "db", "top_queries": []})
    assert result["summary"] == "Review orders"
    assert len(calls) == 1
    # Auto routing: the user's own key when set, else the hosted service.
    assert "provider" not in calls[0][1]
    assert calls[0][1]["purpose"] == "monitor_report"
    assert calls[0][1]["temperature"] == 0.0
    assert calls[0][1]["system_message"] == reports.report_prompt.SYSTEM_PROMPT
    assert json.loads(calls[0][0].split("\nDATA:\n", 1)[1])["database"]["name"] == "db"


@pytest.mark.asyncio
async def test_report_row_counts_billable_narrative_call(tmp_path):
    store = _store(tmp_path)
    _seed(store)

    def narrate(context):
        record_billable_call()
        return _narrative(context)

    clock = Clock()
    result = await reports.run_report(
        "db", "user", store=store, clock=clock, sleep=clock.advance,
        explain=lambda target, sql: {"plan": ["scan"]},
        collect=lambda target: ({"engine": "postgresql"}, {"tables": {}}),
        narrate=narrate)
    assert result["billable_calls"] == 1


@pytest.mark.asyncio
async def test_report_counts_call_even_when_narrative_fails(tmp_path):
    store = _store(tmp_path)
    _seed(store)

    def narrate(context):
        record_billable_call()
        raise ValueError("Invalid model answer")

    clock = Clock()
    result = await reports.run_report(
        "db", "user", store=store, clock=clock, sleep=clock.advance,
        explain=lambda target, sql: {"plan": ["scan"]},
        collect=lambda target: ({"engine": "postgresql"}, {"tables": {}}),
        narrate=narrate)
    assert result["report_json"]["narrative_error"] == "Invalid model answer"
    assert result["billable_calls"] == 1


@pytest.mark.asyncio
async def test_narrative_runs_outside_the_target_lock(tmp_path):
    from features.monitor.run_guard import RunGuard

    store = _store(tmp_path)
    _seed(store)
    ran = threading.Event()
    other_job_ran = []

    def narrate(context):
        # A monitor pass for the same database runs while the model is still answering.
        threading.Thread(target=RunGuard().run, args=("db", ran.set), daemon=True).start()
        other_job_ran.append(ran.wait(2))
        return _narrative(context)

    clock = Clock()
    result = await reports.run_report(
        "db", "user", store=store, clock=clock, sleep=clock.advance,
        explain=lambda target, sql: {"plan": ["scan"]},
        collect=lambda target: ({"engine": "postgresql"}, {"tables": {}}),
        narrate=narrate)
    assert other_job_ran == [True]
    assert result["state"] == "done"
    assert result["report_json"]["summary"] == "Review orders"


@pytest.mark.asyncio
async def test_narrative_timeout_ships_the_computed_narrative(tmp_path, monkeypatch, caplog):
    import requests
    from shared.llm_manager.base import ProviderRequest
    from shared.llm_manager.hosted_provider import HostedProvider
    from shared.llm_manager.inference_attribution import attribution_for

    store = _store(tmp_path)
    _seed(store)
    timeouts = []

    def post(*_args, timeout, **_kwargs):
        timeouts.append(timeout)
        raise requests.Timeout("read timed out")

    monkeypatch.setattr("shared.llm_manager.hosted_provider._HTTP_SESSION.post", post)

    def narrate(context):
        request = ProviderRequest(model="", messages=[{"role": "user", "content": "report"}],
                                  extra={"_rdst_attribution": attribution_for("monitor_report")})
        return HostedProvider().complete(request, api_key="token")

    clock = Clock()
    result = await reports.run_report(
        "db", "user", store=store, clock=clock, sleep=clock.advance,
        explain=lambda target, sql: {"plan": ["scan"]},
        collect=lambda target: ({"engine": "postgresql"}, {"tables": {}}),
        narrate=narrate)
    assert timeouts == [120]
    assert result["state"] == "done"
    report = result["report_json"]
    assert "read timed out" in report["narrative_error"]
    assert report["summary"] == reports._computed_narrative(report["computed"])["summary"]
    assert len([record for record in caplog.records
                if record.getMessage().startswith("Report narrative unavailable")]) == 1


@pytest.mark.asyncio
async def test_report_enqueues_top_queries_with_queue_hook(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _seed(store, 3)
    enqueued = []
    monkeypatch.setattr(reports, "enqueue_for_report",
                        lambda target, keys, **kwargs: enqueued.extend(keys))
    result = await _run(store, Clock())
    assert enqueued == result["top_json"]
    assert result["provisional"]


@pytest.mark.asyncio
async def test_report_api_start_latest_and_progress(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from features.monitor.api import routes

    store = _store(tmp_path)
    store.set_settings("db", {"enabled": True})
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_now", lambda: NOW)

    async def fake_run(target, trigger, **kwargs):
        run = kwargs["claimed_run"]
        store.update_report(run["id"], progress={"message": "Analyzing top queries, 0 of 1",
                                                   "done": 0, "total": 1})
        await asyncio.sleep(0)
        store.update_report(run["id"], state="done", report={"summary": "Ready"},
                            finished_at=NOW)

    monkeypatch.setattr(reports, "run_report", fake_run)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    started = await routes.start_report("db", request)
    assert started["state"] == "running"
    await asyncio.sleep(0)
    progress = routes.get_report("db", started["id"])
    assert progress["progress"]["message"] == "Analyzing top queries, 0 of 1"
    await asyncio.gather(*request.app.state.report_tasks)
    assert routes.latest_report("db")["report_json"]["summary"] == "Ready"
    reused = await routes.start_report("db", request)
    assert reused["id"] == started["id"]


@pytest.mark.asyncio
async def test_failed_narrative_retries_without_new_report_run(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _seed(store, 2)
    clock = Clock()
    first = await reports.run_report(
        "db", "user", store=store, clock=clock, sleep=clock.advance,
        explain=lambda target, sql: {"plan": ["scan"]},
        collect=lambda target: ({"engine": "postgresql"}, {"tables": {}}),
        narrate=lambda context: (_ for _ in ()).throw(RuntimeError("temporary")))
    assert first["report_json"]["narrative_error"] == "temporary"
    def recovered_narrative(context):
        record_billable_call()
        return _narrative(context)

    monkeypatch.setattr(reports, "_narrate", recovered_narrative)
    scheduler = reports.ReportScheduler(store, clock=lambda: NOW + 901)
    scheduler.tick()
    await asyncio.gather(*scheduler._runs)
    updated = reports._present(store.report(first["id"]))
    assert updated["report_json"]["summary"] == "Review orders"
    assert updated["report_json"]["narrative_error"] is None
    assert updated["billable_calls"] == 1
    assert len(store.read("SELECT id FROM report_run WHERE target=?", ("db",))) == 1
    assert not [item for item in store.inbox(target="db")
                if item["type"] == "one_fix_several_queries"]


@pytest.mark.asyncio
async def test_narrative_retry_stops_for_old_or_exhausted_reports(tmp_path, monkeypatch):
    store = _store(tmp_path)
    old, _ = store.claim_report("db", "user", NOW - 2 * 86400)
    store.update_report(old["id"], state="done", report={
        "narrative_error": "down", "narrative_retry_at": NOW - 86400,
        "narrative_retry_count": 0, "computed": {}}, finished_at=NOW - 2 * 86400)
    capped, _ = store.claim_report("db", "user", NOW)
    store.update_report(capped["id"], state="done", report={
        "narrative_error": "down", "narrative_retry_at": NOW - 1,
        "narrative_retry_count": 4, "computed": {}}, finished_at=NOW)
    calls = []

    async def retry(_scheduler, report_id, _target, _report):
        calls.append(report_id)

    monkeypatch.setattr(reports.ReportScheduler, "_retry_narrative", retry)
    scheduler = reports.ReportScheduler(store, clock=lambda: NOW)
    scheduler.tick()
    await asyncio.gather(*scheduler._runs)
    assert calls == []


def test_database_deltas_use_database_series_counters(tmp_path):
    store = _store(tmp_path)
    store.record("db", NOW - 60, [], {}, [],
                 {"calls": 20, "total_ms": 200, "active_conns": 8,
                  "max_conns": 100, "cache_hit_ratio": .95,
                  "xact_commit": 20, "xact_rollback": 4, "deadlocks": 2})
    store.record("db", NOW - 86400 - 60, [], {}, [],
                 {"calls": 10, "total_ms": 100, "active_conns": 4,
                  "max_conns": 100, "cache_hit_ratio": .99,
                  "xact_commit": 10, "xact_rollback": 1, "deadlocks": 0})
    database = reports.rolling_deltas(store, "db", NOW)["24h"]["database"]
    assert database["xact_rollback"]["absolute"] == 3
    assert database["deadlocks"]["current"] == 2
    assert database["active_conns"]["ratio"] == 1
    assert database["cache_hit_ratio"]["absolute"] == pytest.approx(-.04)


def test_cache_hit_rollup_uses_block_counts_not_mean_of_ratios(tmp_path):
    store = _store(tmp_path)
    for ts, hits, reads in ((0, 1, 1), (60, 999, 1)):
        store.record("db", ts, [], {}, [], {
            "calls": 1, "total_ms": 1, "active_conns": 1,
            "max_conns": 10, "cache_hit_ratio": hits / (hits + reads),
            "blks_hit": hits, "blks_read": reads})
    store.rollup("db", 300)
    totals = reports._database_window(store, "db", 0, 300)
    assert totals["cache_hit_ratio"] == pytest.approx(1000 / 1002)


def test_stale_report_recovery_preserves_live_run(tmp_path):
    store = _store(tmp_path)
    first, claimed = store.claim_report("db", "user", NOW)
    assert claimed
    store.recover_reports(NOW + 899)
    assert store.report(first["id"])["state"] == "running"
    store.recover_reports(NOW + 901)
    assert store.report(first["id"])["state"] == "failed"
    second, claimed = store.claim_report("db", "user", NOW + 902)
    assert claimed and second["id"] != first["id"]


def test_existing_index_is_a_fact_not_a_verdict(tmp_path):
    store = _store(tmp_path)
    store.sync_indexes("db", [], NOW - 100)
    store.sync_indexes("db", [{"schema": "public", "table": "orders",
                               "index_name": "idx_customer",
                               "definition": "CREATE INDEX idx_customer ON orders (customer_id)"}], NOW - 50)
    facts = reports._index_facts(store, "db")
    assert {fact["fact"] for fact in facts} == {"index_change"}
    context = {"top_queries": [{"query_key": "q00"}, {"query_key": "q01"}],
               "fix_applied": facts}
    narrative = reports._bound_narrative({
        "fix_first": [{"query_key": "q00", "why": "x", "action": "Add an index"}],
        "one_fix_several_queries": [{"table": "orders", "columns": ["customer_id"],
                                     "query_keys": ["q00", "q01"], "action": "Add an index"}]},
        context)
    # No verdict hides advice; only an index that already exists is not asked for again.
    assert [finding["query_key"] for finding in narrative["fix_first"]] == ["q00"]
    assert narrative["one_fix_several_queries"] == []

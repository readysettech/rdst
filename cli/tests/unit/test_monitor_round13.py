"""Round 13: the deep analysis policy, queue state on rows, classification context, locate."""

from types import SimpleNamespace

import pytest

from features.monitor import analyze_queue
from features.monitor.analyze_queue import (OVER_ALLOWANCE, AnalyzeQueue, _phase_for, budget,
                                            enqueue_new_queries, enqueue_stale_top, queue_plan)
from features.monitor.api import views
from shared.monitor.monitor_store import MonitorStore

NOW = 1_800_000_000


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(views, "registry_entries", lambda keys=None: {})
    monkeypatch.setattr(views, "assessments", lambda target, keys=None: {})
    result = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    result.set_settings("db", {"enabled": True, "deep_analysis": True})
    return result


def add(store, key, weight, *, at=NOW - 60, sql=None):
    store.record("db", at, [{"query_key": key, "calls": 10, "total_ms": weight}],
                 {key: sql or f"SELECT * FROM t_{key} WHERE id = 1"}, [], None)


def rows(store, **kwargs):
    page = views.queries(store, "db", "1h", NOW, sort=kwargs.pop("sort", "db_time_share"),
                         filter=kwargs.pop("filter", "all"), q="", limit=50, offset=0, **kwargs)
    return page, {row["key"]: row for row in page["queries"]}


def test_newcomer_setting_defaults_to_25_and_is_validated(store):
    assert store.settings("db")["newcomer_daily_cap"] == 25
    assert store.set_settings("db", {"newcomer_daily_cap": 3})["newcomer_daily_cap"] == 3
    for bad in (-1, 1001, True, "5"):
        with pytest.raises(ValueError):
            store.set_settings("db", {"newcomer_daily_cap": bad})


def test_newcomer_default_follows_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("RDST_MONITOR_NEWCOMER_DAILY_CAP", "7")
    assert MonitorStore(tmp_path / "other.db").levers("db")["newcomer_daily_cap"] == 7


def test_only_queries_in_the_top_n_are_newcomers(store):
    store.set_settings("db", {"analysis_daily_cap": 3})
    for index in range(6):
        add(store, f"q{index}", 1000 - index)
    # Writes never take a place in the top.
    add(store, "w", 5000, sql="UPDATE t SET x = 1")
    jobs = enqueue_new_queries("db", store=store, now=NOW)
    assert [job["query_key"] for job in jobs] == ["q0", "q1", "q2"]
    assert all(job["reason"] == "new" for job in jobs)


def test_newcomers_spend_their_own_allowance_and_nothing_waits_for_tomorrow(store):
    store.set_settings("db", {"analysis_daily_cap": 10, "newcomer_daily_cap": 2})
    for index in range(5):
        add(store, f"q{index}", 1000 - index)
    assert len(enqueue_new_queries("db", store=store, now=NOW)) == 2
    for _ in range(2):
        job = store.claim_analysis("db", NOW, cap=10, newcomer_cap=2)
        store.finish_analysis(job["id"], "done", NOW + 1, billable_calls=1)
    facts = budget(store, "db", NOW + 2)
    assert facts["newcomer_left"] == 0 and facts["used"] == 0
    # The ceiling is reached: the rest aren't queued at all.
    assert enqueue_new_queries("db", store=store, now=NOW + 2) == []
    assert store.analyze_jobs("db", "queued") == []
    # The next day they're picked up while they're still in the top.
    tomorrow = NOW + 86400
    for index in range(5):
        add(store, f"q{index}", 1000 - index, at=tomorrow - 60)
    assert [job["query_key"] for job in enqueue_new_queries("db", store=store, now=tomorrow)] \
        == ["q2", "q3"]


def test_re_analysis_keeps_the_daily_cap(store):
    store.set_settings("db", {"analysis_daily_cap": 5, "newcomer_daily_cap": 0})
    for index in range(3):
        add(store, f"q{index}", 1000 - index, at=NOW - 2 * 86400)
        job = store.enqueue_analysis("db", f"q{index}", "new", 1, NOW - 2 * 86400, cap=None)
        store.claim_analysis("db", NOW - 2 * 86400, newcomer_cap=10)
        store.finish_analysis(job["id"], "done", NOW - 2 * 86400 + 10)
        add(store, f"q{index}", 1000 - index)
    jobs = enqueue_stale_top("db", store=store, now=NOW)
    # Cap 5 keeps 1 for reports: 4 automatic re-analyses, here all 3.
    assert {job["query_key"] for job in jobs} == {"q0", "q1", "q2"}
    assert {job["reason"] for job in jobs} == {"top15_stale"}


def test_a_job_over_the_allowance_is_held_then_dropped(store):
    store.set_settings("db", {"newcomer_daily_cap": 1})
    for index in range(3):
        add(store, f"q{index}", 1000 - index)
        store.enqueue_analysis("db", f"q{index}", "new", 1000 - index, NOW, cap=None)
    plan = queue_plan(store, "db", NOW)
    held = [job for job in plan["queued"] if plan["jobs"][job["id"]]["queue_state"] == "held"]
    assert len(held) == 2 and all(plan["jobs"][job["id"]]["reason_text"] == OVER_ALLOWANCE
                                  for job in held)
    AnalyzeQueue(store, clock=lambda: NOW)._prepare(NOW)
    assert [job["query_key"] for job in store.analyze_jobs("db", "queued")] == ["q0"]


def test_rows_carry_queue_place_and_the_running_step(store, monkeypatch):
    for index in range(3):
        add(store, f"q{index}", 1000 - index)
        store.enqueue_analysis("db", f"q{index}", "top15_stale", 1000 - index, NOW, cap=None)
    running = store.claim_analysis("db", NOW)
    monkeypatch.setitem(analyze_queue._phases, ("db", running["query_key"]), "sample")
    _, by_key = rows(store)
    assert by_key["q0"]["last_analysis"]["queue_state"] == "running"
    assert by_key["q0"]["last_analysis"]["phase"] == "sample"
    assert (by_key["q1"]["last_analysis"]["queue_state"],
            by_key["q1"]["last_analysis"]["queue_position"]) == ("today", 1)
    assert by_key["q2"]["last_analysis"]["queue_position"] == 2
    assert by_key["q2"]["last_analysis"]["phase"] is None
    # An immediate retry while still running reads as retrying, softly.
    store.retry_analysis(running)
    _, by_key = rows(store)
    assert by_key["q0"]["last_analysis"]["error_kind"] == "retrying"
    from features.monitor.api.models import MonitorQueryPage
    MonitorQueryPage.model_validate(rows(store)[0])


@pytest.mark.parametrize(("stage", "mode", "phase"), [
    ("executing_explain", "explain", "plan"), ("executing_explain", "analyze", "run"),
    ("collecting_schema", "analyze", "schema"), ("analyzing_llm", "explain", "advise"),
    ("testing_rewrites", "explain", "verify"), ("testing_indexes", "explain", "verify"),
    ("checking_readyset", "explain", "verify"), ("storing_results", "explain", "advise"),
])
def test_progress_stages_map_to_row_steps(stage, mode, phase):
    assert _phase_for(stage, mode) == phase


def test_locate_finds_a_query_under_the_current_filter_and_sort(store):
    for index in range(30):
        add(store, f"q{index:02}", 1000 - index)
    page, _ = rows(store, locate="q17")
    assert page["located"] == 17 and len(page["queries"]) == 30
    # Sorted by calls, every query ties on calls and falls back to load.
    page, _ = rows(store, sort="qps", locate="q03")
    assert page["located"] == 3
    page, _ = rows(store, filter="new", locate="q03")
    assert page["located"] is None
    assert "located" not in rows(store)[0]


def test_classification_rows_say_what_schema_they_read(monkeypatch):
    from features.monitor.api import views as fresh_views

    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get",
                        lambda self, target: {"engine": "postgresql", "host": "h"})
    monkeypatch.setattr("features.schema.assessment_context.target_identity", lambda config: "id")
    row = {"status": "complete", "band": "High", "priority_score": 80, "confidence": 0.9,
           "result": {"findings": []}, "model": "jev", "rubric_version": "1",
           "assessed_at": "2026-10-02T10:00:00Z", "error_code": None,
           "schema_coverage": "relevant tables (2 tables, 1 index)",
           "schema_collected_at": "2026-10-02T09:59:00Z"}
    library = SimpleNamespace(assessments_for_target=lambda *args, **kwargs: {"k": row})
    monkeypatch.setattr("shared.query_registry.QueryRegistry",
                        lambda: SimpleNamespace(library_store=library))
    result = fresh_views.assessments("db", ["k"])["k"]
    assert result["schema_coverage"] == "relevant tables (2 tables, 1 index)"
    assert result["schema_collected_at"] == "2026-10-02T09:59:00Z"
    from features.monitor.api.models import QueryAssessment
    QueryAssessment.model_validate(result)


def test_a_report_queues_only_what_today_can_start(store):
    from features.monitor.analyze_queue import enqueue_for_report

    store.set_settings("db", {"analysis_daily_cap": 3})
    keys = [f"q{index}" for index in range(6)]
    for index, key in enumerate(keys):
        add(store, key, 1000 - index)
    jobs = enqueue_for_report("db", keys, store=store, now=NOW)
    assert [job["query_key"] for job in jobs] == ["q0", "q1", "q2"]
    # Asking again doesn't stack more on top of what's already waiting.
    assert [job["query_key"] for job in enqueue_for_report("db", keys, store=store, now=NOW)] \
        == ["q0", "q1", "q2"]
    assert len(store.analyze_jobs("db", "queued")) == 3

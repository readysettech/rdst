"""Database page v4 backend: load breakdown, analyzability, the Analyze database run,
report v2, alert wording, readings and background quick assessments."""

from __future__ import annotations

import asyncio
import json
import selectors
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.monitor import presentation, report as reports, report_v2
from features.monitor.api import routes, views
from shared.monitor.monitor_store import MonitorStore
from shared.monitor.statements import alert_member, analyzability, is_workload, statement_kind

NOW = 1_000_800
KEYS = [f"{index:012x}" for index in range(1, 12)]
DROP_KEY = "dddddddddd01"
SELF_KEY = "eeeeeeeeee01"


@pytest.fixture
def store(monkeypatch, tmp_path):
    original_select = selectors.EpollSelector.select
    monkeypatch.setattr(selectors.EpollSelector, "select",
                        lambda self, timeout=None: original_select(
                            self, 0.01 if timeout is None else min(timeout, 0.01)))
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "_now", lambda: NOW)
    monkeypatch.setattr(views, "registry_entries", lambda keys=None: {})
    monkeypatch.setattr(views, "assessments", lambda target, keys=None: {})
    monitor = MonitorStore(clock=lambda: NOW)
    monkeypatch.setattr(routes, "_store", lambda: monitor)
    return monitor


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/monitor")
    return TestClient(app)


def _db(calls: float) -> dict:
    return {"calls": calls, "total_ms": calls * 2.0, "active_conns": 3, "max_conns": 100,
            "cache_hit_ratio": 0.99, "xact_commit": 10, "xact_rollback": 1, "deadlocks": 0}


def _seed(store: MonitorStore, minutes: int = 30, *, start: int | None = None) -> None:
    """Eleven queries with falling load, plus a DROP and one of RDST's own reads."""
    texts = {key: f"SELECT count(*) FROM t{index} WHERE c{index} = $1"
             for index, key in enumerate(KEYS)}
    texts[DROP_KEY] = "DROP INDEX IF EXISTS s.idx_orders_total"
    texts[SELF_KEY] = 'SELECT count(*) FROM "public".pg_stat_statements WHERE query = $1'
    begin = NOW - minutes * 60 if start is None else start
    for minute in range(minutes):
        ts = begin + minute * 60
        rows = [{"query_key": key, "calls": 10, "total_ms": 10.0 * (len(KEYS) - index)}
                for index, key in enumerate(KEYS)]
        rows += [{"query_key": DROP_KEY, "calls": 1, "total_ms": 5.0},
                 {"query_key": SELF_KEY, "calls": 1, "total_ms": 500.0}]
        store.record("db", ts, rows, texts, [], _db(sum(row["calls"] for row in rows)))


# Statements: kinds, names, analyzability, RDST's own reads

def test_statement_rules_follow_the_analyzer_safety_rules():
    assert statement_kind("DROP INDEX x") == "ddl"
    assert statement_kind("ANALYZE orders") == "maintenance"
    assert statement_kind("INSERT INTO t VALUES (1)") == "write"
    assert statement_kind("WITH x AS (SELECT 1) SELECT * FROM x") == "select"
    drop = analyzability("DROP TABLE orders")
    assert drop == {"analyzable": False, "analyzable_code": "ddl",
                    "not_analyzable_reason": "DROP changes the schema, so RDST doesn't run it.",
                    "analysis_note": None}
    assert analyzability("SELECT pg_sleep(1) FROM orders")["not_analyzable_reason"] == \
        "This query calls pg_sleep, which RDST won't run."
    assert analyzability("UPDATE orders SET x = 1")["not_analyzable_reason"] == \
        "UPDATE changes data, so RDST doesn't run it."
    assert analyzability("ANALYZE orders")["analyzable_code"] == "maintenance"
    assert analyzability("")["analyzable_code"] == "no_text"
    slow = analyzability("SELECT * FROM orders", mean_ms=5000, execute=True)
    assert slow["analyzable"] and "plan without running it" in slow["analysis_note"]
    assert analyzability("SELECT * FROM orders", mean_ms=5000)["analysis_note"] is None


def test_extension_statistics_views_are_rdst_reads_in_any_schema():
    from shared.query_registry.sql_normalizer import references_user_relations

    assert not references_user_relations(
        'SELECT count(*) FROM "public".pg_stat_statements WHERE query = $1')
    assert not references_user_relations("SELECT * FROM monitoring.pg_stat_statements_info")
    assert references_user_relations("SELECT * FROM public.orders")
    assert not is_workload('SELECT calls FROM "public".pg_stat_statements')
    assert not alert_member("CREATE INDEX i ON orders (total)")
    assert alert_member("SELECT * FROM orders") and alert_member("UPDATE orders SET x = 1")


# Load breakdown

def test_load_breakdown_bands_top_queries_and_matches_row_colors(client, store):
    _seed(store)
    body = client.get("/api/monitor/targets/db/load?range=1h").json()
    assert body["res"] == 60 and body["range"] == "1h"
    bands = body["bands"]
    assert [band["key"] for band in bands[:8]] == KEYS[:8]
    assert [band["color_index"] for band in bands[:8]] == list(range(8))
    assert bands[-1]["key"] == "other" and bands[-1]["color_index"] is None
    # RDST's own statement is not load; the DROP is the application's and lands in "other".
    assert SELF_KEY not in {band["key"] for band in bands}
    total = sum(band["total_ms"] for band in bands)
    assert total == pytest.approx(body["total_ms"])
    assert sum(band["share"] for band in bands) == pytest.approx(1.0)
    point = body["points"][-2]
    assert len(point["total_ms"]) == len(bands) and point["seconds"] == 60
    assert point["total_ms"][0] == pytest.approx(110.0)
    assert point["total_ms"][-1] == pytest.approx(10 + 20 + 30 + 5)
    # Before the first reading there is no bucket; nothing is invented.
    assert body["points"][0]["ts"] == NOW - 1800
    rows = {row["key"]: row for row in client.get(
        "/api/monitor/targets/db/queries?range=1h").json()["queries"]}
    assert all(rows[key]["color_index"] == index for index, key in enumerate(KEYS[:8]))
    assert rows[KEYS[9]]["color_index"] is None
    assert SELF_KEY not in rows


def test_load_ranked_by_calls_keeps_the_time_ranking_colors(client, store):
    # The last query by time is the busiest by calls; the rest fall with time.
    texts = {key: f"SELECT count(*) FROM t{index} WHERE c{index} = $1"
             for index, key in enumerate(KEYS)}
    for minute in range(30):
        rows = [{"query_key": key, "calls": 1000 if index == 10 else 100 - index * 5,
                 "total_ms": 10.0 * (len(KEYS) - index)}
                for index, key in enumerate(KEYS)]
        store.record("db", NOW - (30 - minute) * 60, rows, texts, [],
                     _db(sum(row["calls"] for row in rows)))
    bands = client.get("/api/monitor/targets/db/load?range=1h&rank=calls").json()["bands"]
    assert [band["key"] for band in bands[:8]] == [KEYS[10], *KEYS[:7]]
    # The seven shared with the time ranking keep its colors; the newcomer takes the free one.
    assert [band["color_index"] for band in bands[:8]] == [7, 0, 1, 2, 3, 4, 5, 6]
    assert bands[-1]["key"] == "other"
    assert client.get("/api/monitor/targets/db/load?rank=size").status_code == 422


def test_load_ranges_resolution_and_availability(client, store):
    _seed(store, minutes=150)
    body = client.get("/api/monitor/targets/db/load?range=5m").json()
    assert body["res"] == 60 and len(body["points"]) in (5, 6)
    assert client.get("/api/monitor/targets/db/load?range=24h").json()["res"] == 300
    ranges = {entry["range"]: entry for entry in body["ranges"]}
    assert ranges["1h"]["enabled"] and ranges["24h"]["enabled"]
    assert not ranges["7d"]["enabled"] and ranges["7d"]["reason"] == "2.5 hours recorded"
    assert ranges["all"]["enabled"]
    assert body["recorded_label"] == "2.5 hours recorded"


def test_load_gap_buckets_are_null(client, store):
    _seed(store, minutes=10, start=NOW - 3600)
    _seed(store, minutes=10, start=NOW - 900)
    points = client.get("/api/monitor/targets/db/load?range=1h").json()["points"]
    gap = [point for point in points if NOW - 3000 <= point["ts"] < NOW - 900]
    assert gap and all(point["total_ms"] is None for point in gap)


def test_brushed_window_rescopes_list_and_breakdown(client, store):
    _seed(store, minutes=60)
    start, end = NOW - 1800, NOW - 600
    body = client.get(f"/api/monitor/targets/db/load?from={start}&to={end}").json()
    assert body["range"] == "custom" and body["res"] == 60
    assert body["points"][0]["ts"] == start and body["points"][-1]["ts"] == end
    page = client.get(f"/api/monitor/targets/db/queries?from={start}&to={end}").json()
    assert page["range"] == "custom" and page["queries"][0]["key"] == KEYS[0]
    assert client.get(f"/api/monitor/targets/db/load?from={end}&to={start}").status_code == 422
    assert client.get(f"/api/monitor/targets/db/load?from={start}").status_code == 422
    assert views.window_res(7 * 86400, NOW - 7 * 86400, NOW) == 3600
    assert views.window_res(6 * 3600, NOW - 6 * 3600, NOW) == 60
    # Incident windows can retain minute rows for up to 30 days.
    assert views.window_res(3600, NOW - 3 * 86400, NOW) == 60
    assert views.window_res(3600, NOW - 8 * 86400, NOW) == 60


def test_latency_per_query(client, store):
    _seed(store)
    body = client.get(f"/api/monitor/targets/db/load/latency?range=1h&keys={KEYS[0]},{KEYS[1]}"
                      ).json()
    point = body["points"][-2]
    assert body["keys"] == KEYS[:2]
    assert point["mean_ms"] == [pytest.approx(11.0), pytest.approx(10.0)]
    assert point["overall_mean_ms"] == pytest.approx(2.0)
    assert client.get("/api/monitor/targets/db/load/latency?keys=").status_code == 422


def test_complete_rows_match_the_overlap_rule(store):
    _seed(store, minutes=20, start=NOW - 7200)
    store.rollup("db", NOW)
    rows = views.stmt_rows(store, "db", NOW - 7200, NOW + 1)
    seen = {(row["query_key"], row["ts"], row["res"]) for row in rows}
    for key, ts, res in seen:
        for parent in (300, 3600, 86400):
            if parent > res:
                assert (key, ts // parent * parent, parent) not in seen or \
                    ts // parent * parent + parent > NOW + 1


# Query rows, single query, analyze refusals

def test_query_rows_carry_analyzability_and_single_row(client, store):
    _seed(store)
    rows = {row["key"]: row for row in client.get(
        "/api/monitor/targets/db/queries?range=1h&limit=50").json()["queries"]}
    assert rows[DROP_KEY]["analyzable"] is False
    assert rows[DROP_KEY]["not_analyzable_reason"] == \
        "DROP changes the schema, so RDST doesn't run it."
    assert rows[DROP_KEY]["display_name"] == "DROP INDEX idx_orders_total"
    assert rows[KEYS[0]]["analyzable"] is True and rows[KEYS[0]]["analyzable_code"] == "ok"
    one = client.get(f"/api/monitor/targets/db/queries/{KEYS[3]}?range=1h").json()
    assert one["key"] == KEYS[3] and one["color_index"] == 3
    assert one["db_time_share"] == pytest.approx(rows[KEYS[3]]["db_time_share"])
    assert client.get(f"/api/monitor/targets/db/queries/{SELF_KEY}").status_code == 404
    assert client.get("/api/monitor/targets/db/queries/0123456789ab").status_code == 404
    assert client.get("/api/monitor/targets/db/queries?range=1h&q=t3").json()["total"] == 1


def test_analyze_refuses_a_drop_with_its_reason(client, store):
    _seed(store)
    response = client.post(f"/api/monitor/targets/db/queries/{DROP_KEY}/analyze")
    assert response.status_code == 422
    assert response.json() == {"detail": "DROP changes the schema, so RDST doesn't run it."}
    assert store.read("SELECT * FROM analyze_job") == []
    assert client.post(f"/api/monitor/targets/db/queries/{KEYS[0]}/analyze").status_code == 200


# Analyze failures

@pytest.mark.parametrize("error,kind,reason", [
    ("UPSTREAM_ERROR: The included AI could not process the request", "ai_unavailable",
     "The AI service didn't return an answer for this query. RDST tries again later."),
    ("HOSTED_UPSTREAM_BUSY: The included AI is temporarily busy", "ai_unavailable",
     "The AI service was busy. RDST tries again later."),
    ("Failed to parse LLM response as JSON", "ai_bad_answer",
     "The AI's answer for this query was cut off. RDST tries again later."),
    ("LOGIN_REQUIRED: Sign in to run Deep Analyze", "sign_in", "Sign in to run deep analysis."),
    ('PostgreSQL EXPLAIN failed: syntax error at or near "NULL"', "plan_failed",
     "The database couldn't plan this statement with sample values."),
    ("Read-only safety validation rejected the statement: Dangerous keyword found: DROP",
     "not_analyzable", "RDST only analyzes read-only queries. This statement runs DROP."),
])
def test_analysis_failures_are_classified(error, kind, reason):
    assert presentation.analysis_error_kind("failed", error) == kind
    assert presentation.analysis_reason("failed", error) == reason


def test_analysis_budget_leaves_room_for_reasoning(monkeypatch):
    from features.analyze.functions import llm_analysis

    manager = MagicMock()
    manager.generate_response.return_value = {"response": json.dumps({})}
    monkeypatch.setattr(llm_analysis, "LLMManager", lambda: manager)
    llm_analysis.analyze_with_llm({"database_engine": "postgresql", "success": True}, {},
                                  "SELECT id FROM items", hosted_only=True)
    assert manager.generate_response.call_args.kwargs["max_tokens"] == 8192


async def test_queue_keeps_the_hosts_error_detail(store, monkeypatch):
    from features.monitor import analyze_queue

    class Service:
        async def suggest_parameter_values(self, *args):
            return {"placeholders": []}

        async def analyze(self, input, options):
            yield type("Event", (), {"type": "progress", "stage": "analyzing_llm"})()
            yield type("Event", (), {
                "type": "complete", "llm_analysis": {
                    "success": False, "error_code": "UPSTREAM_ERROR",
                    "error": "LLM analysis failed: The included AI could not process the request"},
                "explain_results": {}, "query_hash": "x", "stored_analysis_id": "y"})()

    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get", lambda self, name: {})
    store.record("db", NOW - 60, [{"query_key": KEYS[0], "calls": 1, "total_ms": 1}],
                 {KEYS[0]: "SELECT * FROM orders"}, [])
    job = store.enqueue_analysis("db", KEYS[0], "user", 1, NOW)
    claimed = store.claim_analysis("db", NOW)
    queue = analyze_queue.AnalyzeQueue(store, service=Service(), clock=lambda: NOW)
    await queue._process(claimed)
    row = store.read("SELECT state,error FROM analyze_job WHERE id=?", (job["id"],))[0]
    assert row["state"] == "failed" and row["error"].startswith("UPSTREAM_ERROR: ")
    assert analyze_queue.current_phase("db", KEYS[0]) is None


def test_queued_analysis_stores_the_same_sections_as_an_interactive_one(monkeypatch, tmp_path):
    """Owner rule: background analyses are stored complete, like an interactive run."""
    from features.analyze.models import AnalyzeInput
    from features.analyze.service import AnalyzeService
    from shared.query_registry.analysis_results import (
        AnalysisResultsRegistry,
        create_analysis_result,
    )

    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    registry = AnalysisResultsRegistry()
    stored = {}
    for source in ("web", "monitor"):
        analysis_id = registry.store_analysis_result("hash_1", create_analysis_result(
            query_hash="hash_1", target="db", performance_metrics={"rows_examined": 5},
            llm_analysis={"success": True, "analysis_results": {"a": 1}},
            explain_plan={"Plan": {"Node Type": "Seq Scan"}}, query_metrics={}))
        workflow_result = {"success": True, "result": {
            "explain_results": {"success": True, "execution_time_ms": 10},
            "llm_analysis": {"success": True, "rewrite_suggestions": [{"rewritten_sql": "x"}]},
            "rewrite_test_results": {"tested": False, "skipped_reason": "background_analysis"}
            if source == "monitor" else {"tested": True},
            "index_test_results": {"tested": False},
            "readyset_cacheability": {"checked": True, "cacheable": True},
            "storage_result": {"analysis_id": "a", "query_hash": "hash_1",
                               "stored_analysis_id": analysis_id},
            "FormatFinalResults": {"summary": "done"}}}

        async def run():
            return [event async for event in AnalyzeService()._process_results(
                workflow_result=workflow_result, readyset_result=None,
                input=AnalyzeInput(sql="SELECT 1 FROM t", normalized_sql="SELECT 1 FROM t",
                                   source=source, hash="hash_1"))]

        asyncio.run(run())
        stored[source] = registry.get_analysis_by_id("hash_1", analysis_id)
    web, monitor = stored["web"].to_dict(), stored["monitor"].to_dict()
    assert set(web) == set(monitor)
    assert set(web["display_payload"]) == set(monitor["display_payload"]) == {
        "explain_results", "llm_analysis", "rewrite_testing", "index_testing",
        "readyset_cacheability", "formatted"}
    assert monitor["display_payload"]["llm_analysis"] == web["display_payload"]["llm_analysis"]
    assert monitor["explain_plan"] == web["explain_plan"]


# Analyze database run and report v2

def _events(client, run_id):
    with client.stream("GET", f"/api/monitor/targets/db/report/{run_id}/events") as response:
        return [json.loads(line[5:]) for line in response.iter_lines()
                if line.startswith("data:")]


def _narrate(context):
    return {"summary": "Load is steady. {{q:" + KEYS[0] + "}} leads.",
            "fix_first": [{"query_key": KEYS[0], "title": "Add an index on t0", "why": "w",
                           "action": "Add it"}],
            "one_fix_several_queries": [{"table": "t0", "columns": ["c0"],
                                         "query_keys": KEYS[:2], "action": "Add one index"}],
            "health": [{"check": "vacuum_bloat", "status": "warn", "note": "bloat"},
                       {"check": "connections", "status": "ok", "note": "fine"}],
            "changed_since_yesterday": ["old wording"]}


async def test_run_streams_real_stages_and_writes_report_v2(client, store, monkeypatch):
    _seed(store)
    store.set_settings("db", {"enabled": True})
    store.skip_analysis_once("db", KEYS[1], "report", NOW - 10,
                             "UPSTREAM_ERROR: The included AI could not process the request")
    monkeypatch.setattr(reports, "USER_WAIT_SECONDS", 0)
    result = await reports.run_report(
        "db", "user", store=store, clock=lambda: NOW,
        explain=lambda target, sql: {"plan": ["Seq Scan"]},
        collect=lambda target: ({"section_errors": {}}, {}), narrate=_narrate)
    events = _events(client, result["id"])
    steps = [event["step"] for event in events if event["type"] == "step"]
    assert steps[0] == "history" and steps.index("select") > steps.index("history")
    history = next(event for event in events if event["type"] == "step"
                   and event["step"] == "history" and event["state"] == "done")
    assert history["detail"] == "12 queries, 30 readings" and history["total"] == 30
    selected = next(event for event in events if event["type"] == "selected")["queries"]
    assert SELF_KEY not in {query["key"] for query in selected}
    analyses = [event for event in events if event["type"] == "analysis"]
    assert all("display_name" in event and "error_kind" in event for event in analyses)
    failed = next(event for event in analyses if event["key"] == KEYS[1])
    assert failed["error_kind"] == "ai_unavailable"
    narrative = [event for event in events if event["type"] == "narrative"]
    assert len(narrative) == 1 and narrative[0]["final"] is True
    assert "{{q:" not in narrative[0]["delta"] and "leads." in narrative[0]["delta"]
    body = client.get(f"/api/monitor/targets/db/report/{result['id']}").json()["report_json"]
    assert body["version"] == 3
    verdict = body["verdict"]
    assert verdict["query_key"] == KEYS[0] and not verdict["sentence"].startswith("On ")
    assert verdict["sentence"].endswith(".")
    card = body["fix_first"][0]
    assert card["display_name"] == "COUNT on t0 by c0"
    assert card["share"] == pytest.approx(110 / 665, rel=1e-3)
    assert card["calls"] == 300 and card["mean_ms"] == pytest.approx(11.0)
    assert body["one_fix_several_queries"][0]["share"] == pytest.approx(210 / 665, rel=1e-3)
    health = {item["check"]: item for item in body["health"]}
    assert health["vacuum_bloat"]["matters"] and "VACUUM" in health["vacuum_bloat"]["action"]
    assert health["connections"]["action"] is None
    # Readings began half an hour ago: nothing moved against a day that has none.
    assert body["since_yesterday"] == []
    history_rows = client.get("/api/monitor/targets/db/report/history").json()["reports"]
    # A daily snapshot is listed by its day, not by a verdict sentence.
    assert history_rows[0]["verdict"] == "" and history_rows[0]["period_partial"] is True
    assert history_rows[0]["sample"] is False and history_rows[0]["look_at"] is not None


def test_verdict_leads_with_the_finding():
    top = [{"query_key": "a", "text": "SELECT count(*) FROM orders", "db_time_share": 0.71},
           {"query_key": "b", "text": "SELECT * FROM users", "db_time_share": 0.1}]
    assert report_v2.verdict({"top_queries": top}, {}, [])["sentence"] == \
        "One query is 71% of your load: COUNT on orders."
    top[0]["db_time_share"] = 0.35
    top[1]["db_time_share"] = 0.3
    assert report_v2.verdict({"top_queries": top}, {}, [])["sentence"] == \
        "Two queries are 65% of your load: COUNT on orders and SELECT on users."
    digest = {"a": {"fix": {"kind": "index", "title": "Add an index on orders(total)"},
                    "headline": "Needs an index on orders(total)"}}
    top[1]["db_time_share"] = 0.05
    verdict = report_v2.verdict({"top_queries": top}, digest, [])
    assert verdict["sentence"] == ("COUNT on orders is the biggest share of your load at 35%. "
                                   "An index on orders(total) is the fix.")
    assert verdict["tone"] == "negative"


def test_old_reports_are_presented_in_v2(client, store):
    run, _ = store.claim_report("db", "nightly", NOW - 3600)
    store.update_report(run["id"], state="done", finished_at=NOW - 3500, report={
        "summary": "On target 'db' (PostgreSQL), one query dominates.",
        "fix_first": [{"query_key": KEYS[0], "why": "w", "action": "Add an index"}],
        "one_fix_several_queries": [], "health": [], "changed_since_yesterday": [],
        "computed": {"top_queries": [{"query_key": KEYS[0], "text": "SELECT * FROM t0",
                                      "total_ms": 100.0, "db_time_share": 0.8}],
                     "analyses": [], "deltas": {"24h": {"database": {
                         "total_ms": {"current": 100.0, "prior": 50.0}}, "queries": {}}}}})
    body = client.get(f"/api/monitor/targets/db/report/{run['id']}").json()["report_json"]
    assert body["summary"] == "One query dominates."
    assert body["verdict"]["sentence"] == "One query is 80% of your load: SELECT on t0."
    assert body["fix_first"][0]["share"] == 0.8 and body["fix_first"][0]["analysis"] is None
    assert body["since_yesterday"] == [{"label": "Database load", "query_key": None,
                                        "metric": "load", "before": 50.0, "after": 100.0,
                                        "change": 1.0, "unit": "ms"}]
    assert body["changed_since_yesterday"] == ["Database load: 50 ms -> 100 ms (+100%)."]


def test_digest_is_a_header_over_the_complete_stored_analysis(store, monkeypatch, tmp_path):
    from features.monitor.analysis_digest import latest_digest
    from shared.query_registry.analysis_results import (
        AnalysisResultsRegistry,
        create_analysis_result,
    )

    rationale = "Composite index " + "word " * 80
    analysis_id = AnalysisResultsRegistry().store_analysis_result(KEYS[0], create_analysis_result(
        query_hash=KEYS[0], target="db",
        performance_metrics={"rows_examined": 38069, "rows_returned": 8},
        llm_analysis={"performance_assessment": {"overall_rating": "fair",
                                                 "efficiency_score": 60,
                                                 "primary_concerns": ["Seq scan on orders"]},
                      "index_recommendations": [{
                          "sql": "CREATE INDEX i ON orders (total)", "table": "public.orders",
                          "columns": ["total"], "rationale": rationale,
                          "estimated_impact": "high"}]},
        explain_plan={"Execution Time": 4.7, "Plan": {
            "Node Type": "Sort", "Plans": [{"Node Type": "Seq Scan", "Relation Name": "orders",
                                            "Actual Total Time": 4.0}]}},
        query_metrics={}))
    ref = f"/api/query-registry/{KEYS[0]}/analysis/{analysis_id}"
    store.record("db", NOW - 60, [{"query_key": KEYS[0], "calls": 1, "total_ms": 1}],
                 {KEYS[0]: "SELECT * FROM orders WHERE total > $1"}, [])
    job = store.enqueue_analysis("db", KEYS[0], "user", 1, NOW)
    store.claim_analysis("db", NOW)
    store.finish_analysis(job["id"], "done", NOW, analysis_ref=ref, mode="analyze")
    digest = latest_digest(store, "db", KEYS[0])
    assert digest["analysis_ref"] == ref
    assert digest["headline"] == "Needs an index on orders(total)"
    assert digest["fix"]["sql"] == "CREATE INDEX i ON orders (total)"
    assert digest["fix"]["why"] == " ".join(rationale.split())
    assert digest["fix"]["expected_gain"] == {"measured": False, "text": "Estimated impact: high",
                                              "before_ms": None, "after_ms": None,
                                              "cost_reduction_pct": None}
    assert digest["plan_summary"]["text"] == \
        "Sort, mostly Seq Scan on orders; took 4.70 ms; read 38,069 rows to return 8"
    assert digest["findings"] == ["Seq scan on orders"]


# Alerts

def _group(store, kinds):
    recovery = {key: {"kind": kind, "baseline_mean_ms": 2.0, "baseline_qps": 1.0,
                      "at_filing": {"mean_ms": 2.0 * (index + 2) if kind != "traffic_spike"
                                    else 2.0, "qps": 1.0 if kind != "traffic_spike"
                                    else 3.0 + index, "db_time_share": 0.1},
                      "filed_at": NOW}
                for index, (key, kind) in enumerate(zip(KEYS, kinds))}
    return store.upsert_item("db", "many_queries_changed", "database", 0.9, "x",
                             {"query_keys": KEYS[:len(kinds)], "recovery_queries": recovery},
                             NOW)


@pytest.mark.parametrize("kinds,title", [
    (["latency_regression"] * 6, "6 queries got slower at the same time"),
    (["query_improved"] * 3, "3 queries got faster at the same time"),
    (["traffic_spike"] * 4, "Traffic jumped on 4 queries"),
    (["latency_regression", "traffic_spike", "query_improved"],
     "3 queries changed at the same time"),
])
def test_group_titles_say_which_way(kinds, title):
    assert presentation.group_title(kinds) == title


def test_group_alert_body_has_median_and_top_three(client, store):
    _seed(store, minutes=5)
    _group(store, ["latency_regression"] * 5)
    item = client.get("/api/monitor/inbox?state=open").json()["items"][0]
    assert item["title"] == "5 queries got slower at the same time"
    assert item["median_change"] == pytest.approx(3.0)
    assert [member["query_key"] for member in item["members"]] == [KEYS[4], KEYS[3], KEYS[2]]
    assert item["measurement"]["text"] == "Median latency up 4.0x across 5 queries"
    assert item["summary"].startswith("Median latency up 4.0x. Most impact: COUNT on t4 by c4 "
                                      "2.0 ms -> 12 ms")


def test_a_group_alert_is_urgent_only_when_it_costs_real_database_time(store):
    def band(before_ms, after_ms, qps):
        recovery = {key: {"kind": "latency_regression", "baseline_mean_ms": before_ms,
                          "baseline_qps": qps, "at_filing": {"mean_ms": after_ms, "qps": qps},
                          "filed_at": NOW} for key in KEYS[:2]}
        item = store.upsert_item("db", "many_queries_changed", f"database-{after_ms}", 0.9, "x",
                                 {"query_keys": KEYS[:2], "recovery_queries": recovery}, NOW)
        return presentation.present_inbox_item(item, {}, [], set())["severity_band"]

    # Sub-millisecond moves on busy inserts are worth watching, not dropping everything for.
    assert band(0.04, 0.29, 5) == "watch"
    assert band(10, 60, 5) == "act_now"


def test_group_members_exclude_ddl_and_rdst_statements(client, store):
    _seed(store, minutes=5)
    store.upsert_item("db", "many_queries_changed", "database", 0.9, "x",
                      {"query_keys": [KEYS[0], KEYS[1], DROP_KEY]}, NOW)
    item = client.get("/api/monitor/inbox?state=open").json()["items"][0]
    assert item["query_keys"] == [KEYS[0], KEYS[1]]
    assert item["title"].startswith("2 queries")


def test_single_alerts_state_metric_direction_and_size():
    latency = presentation.alert_measurement(
        {"type": "latency_regression"},
        {"numbers": {"mean_ms": 13.0}, "baseline": {"1h": {"mean_ms": 4.0}}})
    assert latency["text"] == "Latency up 3.2x: 4.0 ms -> 13 ms"
    assert (latency["metric"], latency["direction"]) == ("latency", "up")
    traffic = presentation.alert_measurement(
        {"type": "traffic_spike"}, {"numbers": {"qps": 1.5}, "baseline": {"1h": {"qps": 1.2}}})
    assert traffic["text"] == "Calls up 25%: 1.2 a second -> 1.5 a second"
    stopped = presentation.alert_measurement(
        {"type": "query_stopped_running"}, {"baseline": {"1h": {"qps": 0.1}}})
    assert stopped["direction"] == "down" and stopped["after"] == 0.0


def test_schema_16_closes_legacy_group_and_rdst_items(tmp_path):
    import sqlite3

    path = tmp_path / "monitor.db"
    store = MonitorStore(path)
    store.record("db", NOW, [], {SELF_KEY: 'SELECT 1 FROM "public".pg_stat_statements',
                                 DROP_KEY: "DROP INDEX x", KEYS[0]: "SELECT * FROM t"}, [])
    group = store.upsert_item("db", "many_queries_changed", "database", 0.9,
                              "Many queries changed at once", {"query_keys": [KEYS[0]]}, NOW)
    own = store.upsert_item("db", "new_heavy_query", SELF_KEY, 0.3, "New heavy query",
                            {"query_key": SELF_KEY}, NOW)
    ddl = store.upsert_item("db", "latency_regression", DROP_KEY, 0.6, "Slower",
                            {"query_key": DROP_KEY}, NOW)
    kept = store.upsert_item("db", "latency_regression", KEYS[0], 0.6, "Slower",
                             {"query_key": KEYS[0]}, NOW)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=15")
    migrated = MonitorStore(path)
    rows = {row["id"]: row for row in migrated.read("SELECT * FROM inbox_item")}
    for closed in (group, own, ddl):
        assert rows[closed["id"]]["state"] == "resolved"
        assert rows[closed["id"]]["delivery"] == "digest"
    assert rows[kept["id"]]["state"] == "new"


# Readings

def test_health_and_pulse_give_last_and_next_reading(client, store):
    store.set_settings("db", {"enabled": True})
    store.record_pulse("db", NOW - 120, "collect", True, "ok")
    store.record_pulse("db", NOW - 60, "collect", True, "ok")
    health = client.get("/api/monitor/targets/db/health?range=1h").json()
    assert (health["last_reading_at"], health["next_reading_at"]) == (NOW - 60, NOW)
    pulse = client.get("/api/monitor/targets/db/pulse").json()["pulses"][0]
    assert (pulse["last_reading_at"], pulse["next_reading_at"]) == (NOW - 60, NOW)
    store.set_settings("db", {"enabled": False})
    assert client.get("/api/monitor/targets/db/health").json()["next_reading_at"] is None


# Background quick assessments

def test_assessment_worker_admits_monitored_targets_without_a_browser():
    from features.query_registry.assessment import AssessmentWorker

    worker = AssessmentWorker()
    worker.set_background_targets({"b", "a"})
    assert worker.active_target == "a" and worker.background
    worker._next_background()
    assert worker.active_target == "b"
    worker.select_target("c")
    assert worker.active_target == "c" and not worker.background
    worker.release_target("c")
    assert worker.active_target in {"a", "b"} and worker.background
    worker.set_background_targets(set())
    assert worker.active_target is None


async def test_monitor_loop_feeds_assessments(store):
    from features.monitor.loop import MonitorLoop

    fed = []
    loop = MonitorLoop(store, judge=lambda *args: None, assessments=fed.append, interval=3600)
    loop.cycle = lambda target: None
    store.set_settings("db", {"enabled": True})
    await loop.start()
    await asyncio.sleep(0.05)
    await loop.stop()
    assert fed[0] == {"db"} and fed[-1] == set()


def test_query_rows_carry_the_latest_assessment(client, store, monkeypatch):
    _seed(store, minutes=3)
    monkeypatch.setattr(views, "assessments", lambda target, keys=None: {KEYS[0]: {
        "status": "complete", "band": "Low", "priority_score": 35, "confidence": 0.26,
        "findings": [{"id": "index_coverage", "label": "Possible index gap",
                      "verdict": "strong_concern", "confidence": 0.26}],
        "model": "jev-1", "rubric_version": "v2", "assessed_at": "2026-09-26T04:36:16Z",
        "error_code": None}})
    rows = {row["key"]: row for row in client.get(
        "/api/monitor/targets/db/queries?range=1h").json()["queries"]}
    assert rows[KEYS[0]]["assessment"]["band"] == "Low"
    assert rows[KEYS[1]]["assessment"] is None


# Report consistency (coordinator review)

def test_report_numbers_come_from_one_window_and_cards_rank_by_share(client, store):
    top = [{"query_key": KEYS[0], "text": "SELECT id FROM orders WHERE total >= $1",
            "total_ms": 600.0, "db_time_share": 0.326},
           {"query_key": KEYS[1], "text": "SELECT pg_sleep(180) FROM orders WHERE id = 1",
            "total_ms": 540.0, "db_time_share": 0.292},
           {"query_key": KEYS[2], "text": "SELECT id FROM products WHERE category = $1 LIMIT $2",
            "total_ms": 9.0, "db_time_share": 0.005}]
    run, _ = store.claim_report("db", "user", NOW - 60)
    store.update_report(run["id"], state="done", finished_at=NOW, report={
        "summary": "Load is led by {{q:" + KEYS[0] + "}} (32.6%) and another query (20.9%). "
                   "Eight aggregates can share one rewrite.",
        "fix_first": [
            {"query_key": KEYS[2], "title": "Add ORDER BY before LIMIT", "why": "w",
             "action": "Rewrite it; an index on products(category) was also suggested."},
            {"query_key": KEYS[1], "title": "Find and stop the pg_sleep query", "why": "w",
             "action": "Stop it"},
            {"query_key": KEYS[0], "title": "Keep the index", "why": "w", "action": "a"}],
        "one_fix_several_queries": [], "health": [], "changed_since_yesterday": [],
        "computed": {"top_queries": top, "analyses": [], "deltas": {}}})
    body = client.get(f"/api/monitor/targets/db/report/{run['id']}").json()["report_json"]
    shares = {row["query_key"]: row["db_time_share"] for row in top}
    assert body["verdict"]["sentence"] == ("Two queries are 62% of your load: SELECT on orders "
                                           "by total and SELECT pg_sleep on orders by id.")
    # The narrative's own percentages never reach the page; shares come from code.
    assert body["summary"] == "Eight aggregates can share one rewrite."
    assert [card["query_key"] for card in body["fix_first"]] == [KEYS[0], KEYS[1], KEYS[2]]
    assert all(card["share"] == shares[card["query_key"]] for card in body["fix_first"])
    assert body["fix_first"][1]["display_name"] == "SELECT pg_sleep on orders by id"


def test_a_card_only_shows_the_fix_for_its_own_finding():
    digest = {"fix": {"kind": "index", "title": "Add an index on products(category)",
                      "sql": "CREATE INDEX i ON products (category)", "why": "w",
                      "expected_gain": None},
              "other_fixes": [{"kind": "rewrite", "title": "Rewrite the query",
                               "sql": "SELECT ... ORDER BY id LIMIT 25", "why": "w",
                               "expected_gain": None}]}
    order = {"title": "Add ORDER BY before LIMIT",
             "action": "Rewrite it; an index on products(category) was also suggested."}
    assert report_v2.paired_fix(order, digest)["kind"] == "rewrite"
    index = {"title": "Index products by category", "action": "Add it"}
    assert report_v2.paired_fix(index, digest)["sql"] == "CREATE INDEX i ON products (category)"
    assert report_v2.paired_fix({"title": "Stop the index add/drop churn", "action": ""},
                                digest) is None
    assert report_v2.paired_fix({"title": "Add ORDER BY before LIMIT", "action": ""},
                                {"fix": digest["fix"], "other_fixes": []}) is None


def _stored_analysis(store, key, llm, mode="analyze"):
    from shared.query_registry.analysis_results import (
        AnalysisResultsRegistry,
        create_analysis_result,
    )

    analysis_id = AnalysisResultsRegistry().store_analysis_result(key, create_analysis_result(
        query_hash=key, target="db", performance_metrics={}, llm_analysis=llm,
        explain_plan={"Plan": {"Node Type": "Index Scan"}}, query_metrics={}))
    job = store.enqueue_analysis("db", key, "user", 1, NOW)
    store.claim_analysis("db", NOW)
    ref = f"/api/query-registry/{key}/analysis/{analysis_id}"
    store.finish_analysis(job["id"], "done", NOW, analysis_ref=ref, mode=mode)
    return ref


def test_fix_ready_chip_row_digest_and_report_card_agree(client, store):
    from features.monitor.analysis_digest import digest_for_ref

    _seed(store, minutes=3)
    efficient = _stored_analysis(store, KEYS[0], {
        "performance_assessment": {"overall_rating": "excellent", "primary_concerns": []},
        "optimization_opportunities": [{"type": "configuration", "priority": "high",
                                        "description": "No confident change"}]})
    indexed = _stored_analysis(store, KEYS[1], {
        "performance_assessment": {"overall_rating": "fair"},
        "index_recommendations": [{"sql": "CREATE INDEX i ON t1 (c1)", "table": "t1",
                                   "columns": ["c1"], "rationale": "r",
                                   "estimated_impact": "high"}]})
    rows = {row["key"]: row for row in client.get(
        "/api/monitor/targets/db/queries?range=1h").json()["queries"]}
    assert rows[KEYS[0]]["last_analysis"]["summary"]["fix_ready"] is False
    assert rows[KEYS[1]]["last_analysis"]["summary"]["fix_ready"] is True
    digest_for_ref.cache_clear()
    assert client.get(f"/api/monitor/targets/db/queries/{KEYS[0]}/analysis").json()[
        "fix_ready"] is False
    cards = report_v2.fix_first(
        [{"query_key": KEYS[0], "title": "Change a setting", "why": "w", "action": "a"},
         {"query_key": KEYS[1], "title": "Add an index on t1", "why": "w", "action": "a"}],
        {"top_queries": [{"query_key": KEYS[0], "db_time_share": 0.5},
                         {"query_key": KEYS[1], "db_time_share": 0.4}]},
        {KEYS[0]: digest_for_ref("db", KEYS[0], efficient, "analyze"),
         KEYS[1]: digest_for_ref("db", KEYS[1], indexed, "analyze")})
    assert cards[0]["fix_sql"] is None
    assert cards[1]["fix_sql"] == "CREATE INDEX i ON t1 (c1)"


def test_incomplete_assessments_say_what_they_wait_for(monkeypatch):
    rows = {"a": {"status": "waiting_auth", "band": "", "priority_score": None,
                  "confidence": None, "result": {}, "model": "", "rubric_version": "",
                  "assessed_at": "", "error_code": "login_required"}}

    class Store:
        def assessments_for_target(self, target, identity):
            return rows

    class Registry:
        library_store = Store()

    monkeypatch.setattr("shared.query_registry.QueryRegistry", lambda: Registry())
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get",
                        lambda self, name: {"host": "h", "database": "d"})
    result = views.assessments("db")
    assert result["a"]["waiting_reason"].startswith("Waiting for sign-in")


def test_queries_sharing_a_name_are_told_apart_and_grouped(client, store):
    texts = {KEYS[0]: "SELECT count(o.total) FROM customers c JOIN orders o "
                      "ON o.customer_id = c.id WHERE o.total > $1 GROUP BY 1",
             KEYS[1]: "SELECT count(o.total), sum(o.total) FROM customers c "
                      "JOIN orders o ON o.customer_id = c.id WHERE o.total > $1 GROUP BY 1"}
    store.record("db", NOW - 60, [{"query_key": key, "calls": 5, "total_ms": 5.0}
                                  for key in texts], texts, [])
    rows = {row["key"]: row for row in client.get(
        "/api/monitor/targets/db/queries?range=1h").json()["queries"]}
    assert rows[KEYS[0]]["display_name"] == "COUNT on customers and orders by total"
    assert rows[KEYS[1]]["display_name"] == "COUNT on customers and orders by total, variant 2"
    assert rows[KEYS[0]]["family"] == {"name": "COUNT on customers and orders by total",
                                       "size": 2}


def test_shared_fix_title_comes_from_its_own_action():
    items = report_v2.one_fix_several([
        {"table": "orders", "columns": ["total"], "query_keys": KEYS[:8],
         "action": "Deduplicate the aggregate expression list in the application."},
        {"table": "orders", "columns": ["total"], "query_keys": KEYS[:2],
         "action": "Add an index on orders(total)."}], {"top_queries": []})
    assert (items[0]["kind"], items[0]["title"]) == ("rewrite", "One rewrite fixes 8 queries")
    assert items[1]["title"] == "One index on orders(total) helps 2 queries"


def test_row_summary_carries_the_efficiency_score(client, store):
    _seed(store, minutes=3)
    _stored_analysis(store, KEYS[0], {"performance_assessment": {
        "overall_rating": "excellent", "efficiency_score": 98}})
    row = client.get(f"/api/monitor/targets/db/queries/{KEYS[0]}?range=1h").json()
    assert row["last_analysis"]["summary"]["efficiency_score"] == 98


def test_a_zoomed_window_reads_kept_minutes_not_a_spread_hour(client, store):
    """Closed hourly rows exist for the whole window, yet a one-minute chart shows each
    minute as recorded, and the sums match the minute rows."""
    begin = NOW - 3 * 3600
    for minute in range(3 * 60):
        calls = 60 if minute % 2 else 0
        store.record("db", begin + minute * 60,
                     [{"query_key": KEYS[0], "calls": calls, "total_ms": calls * 2.0}],
                     {KEYS[0]: "SELECT * FROM t WHERE c = $1"}, [], _db(calls))
    store.rollup("db", NOW)
    assert store.read("SELECT COUNT(*) n FROM stmt_series WHERE res=3600")[0]["n"] >= 2
    start, end = begin, begin + 2 * 3600
    body = client.get(f"/api/monitor/targets/db/load?from={start}&to={end}").json()
    assert body["res"] == 60
    calls = [sum(point["calls"]) for point in body["points"] if point["calls"] is not None]
    assert calls[:4] == [0, 60, 0, 60]
    totals = views.stmt_totals(store, "db", start, end)
    assert totals[KEYS[0]]["calls"] == 60 * 60
    # A coarse bucket size sums the hourly rows it covers to the same totals.
    rows = views.stmt_rows(store, "db", start, end, step=3600)
    assert {row["res"] for row in rows} == {3600}
    assert sum(row["calls"] for row in rows) == 60 * 60


def test_reads_leave_sqlite_in_one_piece(store):
    """Rows keep their order, types and names (the first of a repeated name wins)."""
    store.ensure_open()
    rows = store.read("SELECT 2 AS n, 'x' AS s, 1.5 AS f, NULL AS z, 3 AS n "
                      "UNION ALL SELECT 1, 'y', 2.0, NULL, 4")
    assert rows == [{"n": 2, "s": "x", "f": 1.5, "z": None},
                    {"n": 1, "s": "y", "f": 2.0, "z": None}]
    assert isinstance(rows[1]["f"], float) and isinstance(rows[0]["n"], int)
    assert store.read("SELECT x'00ff' AS b") == [{"b": b"\x00\xff"}]
    assert store.read("SELECT 1 AS a WHERE 0") == []


def test_an_unaligned_window_over_rollups_counts_the_buckets_it_overlaps(client, store,
                                                                         monkeypatch):
    """Four days back a query outside the top keeps only five-minute rollups; a window with
    ragged edges still reads the buckets it overlaps."""
    begin = (NOW - 4 * 86400) // 3600 * 3600
    for minute in range(60):
        store.record("db", begin + minute * 60,
                     [{"query_key": KEYS[0], "calls": minute + 1, "total_ms": 1.0}],
                     {KEYS[0]: "SELECT * FROM t WHERE c = $1"}, [], _db(minute + 1))
    store.rollup("db", NOW)
    store.maintain(pause_s=0)
    assert not store.read("SELECT 1 FROM stmt_series WHERE res=60")
    start, end = begin + 130, begin + 1970
    body = client.get(f"/api/monitor/targets/db/load?from={start}&to={end}").json()
    assert body["res"] == 60
    # Buckets starting 0, 300, ..., 1800 overlap [130, 1970): minutes 1 to 35.
    assert body["calls"] == sum(range(1, 36))
    page = client.get(f"/api/monitor/targets/db/queries?from={start}&to={end}").json()
    assert page["queries"][0]["key"] == KEYS[0]
    assert page["queries"][0]["db_time_ms"] == pytest.approx(35.0)


@pytest.mark.parametrize("mode", ["explain", None])
def test_plan_only_digest_labels_estimates_and_omits_execution(mode):
    from types import SimpleNamespace
    from features.monitor.analysis_digest import digest_from_analysis

    analysis = SimpleNamespace(
        timestamp="2026-10-01T00:00:00Z", index_suggestions=[], rewrite_suggestions=[],
        explain_plan={"Execution Time": 49, "Plan": {"Node Type": "Seq Scan"}},
        performance_metrics={"rows_examined": 1000, "rows_returned": 10},
        llm_analysis={"index_recommendations": [{"sql": "CREATE INDEX i ON t(id)"}]},
        display_payload={"explain_results": {"plan_format": "explain"},
                         "index_testing": {"tested": True, "results": [{
                             "index_sql": "CREATE INDEX i ON t(id)",
                             "planner_used_index": True, "cost_reduction_pct": 50}]}})
    digest = digest_from_analysis("q", {"mode": mode}, analysis)
    assert digest["mode"] == "explain"
    assert digest["plan_summary"]["execution_ms"] is None
    assert "estimated 1,000 rows scanned and 10 returned" in digest["plan_summary"]["text"]
    assert "took" not in digest["plan_summary"]["text"]
    assert digest["fix"]["expected_gain"]["measured"] is False
    assert digest["fix"]["expected_gain"]["text"].startswith("Planner estimate:")


def _rewrite_tested(sql, execution, cost, better=True):
    improvement = {"overall": {"improvement_pct": 40.0, "is_better": better,
                               "significant": True},
                   "cost_estimate": {"improvement_pct": cost}}
    if execution is not None:
        improvement["execution_time"] = {"improvement_pct": execution, "baseline_ms": 80.0,
                                         "rewrite_ms": 20.0}
    return {"success": True, "sql": sql, "improvement": improvement}


@pytest.mark.parametrize("tested,gain", [
    (_rewrite_tested("SELECT  2;", 75.0, 30.0),
     {"measured": True, "text": "Tested: 75% faster", "before_ms": 80.0, "after_ms": 20.0,
      "cost_reduction_pct": 75.0}),
    (_rewrite_tested("select 2", None, 30.0),
     {"measured": False, "text": "Planner estimate: cost down 30%", "before_ms": None,
      "after_ms": None, "cost_reduction_pct": 30.0}),
    (_rewrite_tested("SELECT 2", -10.0, 30.0, better=False),
     {"measured": False, "text": "Tested: no faster than the original", "before_ms": None,
      "after_ms": None, "cost_reduction_pct": 0.0}),
    (_rewrite_tested("SELECT 3", 75.0, 30.0),
     {"measured": False, "text": "Estimated: 2x faster", "before_ms": None, "after_ms": None,
      "cost_reduction_pct": None}),
])
def test_rewrite_fix_gain_comes_from_its_tested_rewrite(tested, gain):
    from types import SimpleNamespace
    from features.monitor.analysis_digest import digest_from_analysis

    analysis = SimpleNamespace(
        timestamp="2026-10-01T00:00:00Z", index_suggestions=[], rewrite_suggestions=[],
        explain_plan={}, performance_metrics={},
        llm_analysis={"rewrite_suggestions": [{"rewritten_sql": "SELECT 2",
                                               "expected_improvement": "2x faster"}]},
        display_payload={"rewrite_testing": {"tested": True, "rewrite_results": [tested]}})
    digest = digest_from_analysis("q", {"mode": "analyze"}, analysis)
    assert digest["fix"]["kind"] == "rewrite"
    assert digest["fix"]["expected_gain"] == gain


def test_rewrite_gain_never_borrows_a_test_whose_literals_differ():
    from features.monitor.analysis_digest import _same_sql

    assert _same_sql("SELECT * FROM t WHERE s = 'active';", "select *  from T where s = 'active'")
    assert not _same_sql("SELECT * FROM t WHERE s = 'active'", "SELECT * FROM t WHERE s = 'Active'")
    assert not _same_sql('SELECT "Name" FROM t', 'select "name" from t')
    assert _same_sql("SELECT 'it''s' FROM t", "select 'it''s' from T;")


def test_plan_only_mode_reaches_query_rows_and_report_digests(client, store):
    _seed(store, minutes=3)
    ref = _stored_analysis(store, KEYS[0], {}, mode="explain")
    rows = {row["key"]: row for row in client.get(
        "/api/monitor/targets/db/queries?range=1h").json()["queries"]}
    assert rows[KEYS[0]]["last_analysis"]["mode"] == "explain"
    assert rows[KEYS[0]]["last_analysis"]["summary"]["mode"] == "explain"
    digest = report_v2.digests("db", {"analyses": [{"query_key": KEYS[0],
                                                 "analysis_ref": ref, "mode": "explain"}]})[KEYS[0]]
    assert digest["mode"] == "explain" and digest["plan_summary"]["execution_ms"] is None

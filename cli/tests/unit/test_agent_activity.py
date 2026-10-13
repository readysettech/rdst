"""The agent activity log: store, action grouping, recording at the choke points, backfill
and the API."""

from __future__ import annotations

import csv
import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.agent_activity.api import routes
from features.monitor.analyze_queue import analysis_outcome, parameter_note
from shared import agent_activity
from shared.agent_activity import recorder
from shared.agent_activity.store import RETENTION_SECONDS, AgentActivityStore
from shared.llm_manager.base import LLMError, ProviderRequest, ProviderResponse
from shared.llm_manager.hosted_provider import HostedProvider
from shared.llm_manager.jev_client import JevClient
from shared.llm_manager.key_resolution import HOSTED_MODEL, KeyResolution
from shared.llm_manager.llm_manager import LLMManager
from shared.monitor.monitor_store import MonitorStore
from shared.telemetry import telemetry

HOUR = 1_790_000_000 // 3600 * 3600


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _entry(ts, action="deep_analysis", target="db", **fields):
    return {"ts": ts, "action": action, "target": target, "subject": "SELECT on orders",
            "outcome": "done", **fields}


def test_append_and_read_one_entry(data_dir):
    store = AgentActivityStore()
    entry_id = store.append(
        _entry(HOUR + 10, decision="Ran EXPLAIN only", details={"query_key": "abc"}),
        [{"sql": "EXPLAIN SELECT * FROM orders WHERE id = ?", "kind": "explain",
          "executions": 1, "duration_ms": 3.5}],
        [{"provider": "readyset", "model": "glm", "operation": "monitor_analyze",
          "purpose": "monitor_analyze", "data_kinds": ["SQL text"], "tokens_in": 100,
          "tokens_out": 20, "cost_usd": 0.001}])
    entry = store.entry(entry_id)
    assert entry["decision"] == "Ran EXPLAIN only"
    assert entry["details"] == {"query_key": "abc"}
    assert entry["statements"][0]["kind"] == "explain"
    assert entry["ai_calls"][0]["data_kinds"] == ["SQL text"]
    assert (entry["statement_count"], entry["ai_call_count"], entry["tokens_in"]) == (1, 1, 100)
    assert store.entry(entry_id + 1) is None
    with pytest.raises(ValueError):
        store.append(_entry(HOUR, action="format_disk"))


def test_page_lists_decisions_and_folds_routine_work_per_day(data_dir):
    store = AgentActivityStore()
    for minute in (0, 5, 70):
        store.append(_entry(HOUR + minute * 60, action="attention_check", subject="",
                            items_filed=1 if minute == 5 else 0), [],
                     [{"provider": "readyset-jev", "model": "jev", "tokens_in": 10}])
    store.append(_entry(HOUR + 7 * 60, details={"analysis_ref": "gone", "query_key": "k1"}))
    store.append(_entry(HOUR + 8 * 60, outcome="skipped", summary="INSERT changes data"))
    store.append(_entry(HOUR + 9 * 60, outcome="skipped", summary="UPDATE changes data"))
    store.append(_entry(HOUR + 10 * 60, action="classification", decision="Priority High"),
                 [], [{"provider": "readyset-jev", "model": "jev"}])
    store.append(_entry(HOUR + 11 * 60, action="classification", outcome="skipped"),
                 [{"sql": "SELECT version()", "kind": "session"}])
    store.append(_entry(HOUR + 12 * 60, action="ask", subject="orders",
                        details={"question": "how many orders?"}))
    items = store.page()["items"]
    by_key = {item["key"].split(":")[0]: item for item in items if item["kind"] == "rollup"}
    assert [item["kind"] for item in items].count("rollup") == 3
    checks = by_key["attention_check"]
    assert (checks["count"], checks["items_filed"], checks["ai_call_count"]) == (3, 1, 3)
    assert checks["group"] == "day"
    assert checks["bucket_end"] - checks["bucket_start"] == 86400
    assert checks["bucket_start"] <= HOUR < checks["bucket_end"]
    classified = by_key["classification"]
    assert (classified["count"], classified["done"], classified["skipped"]) == (2, 1, 1)
    skipped = by_key["skipped_analysis"]
    assert (skipped["action"], skipped["count"], skipped["outcome"]) == (
        "deep_analysis", 2, "skipped")
    ask = next(item for item in items if item["action"] == "ask")
    assert ask["question"] == "how many orders?" and ask["subject"] == "orders"
    analysis = next(item for item in items
                    if item["kind"] == "entry" and item["action"] == "deep_analysis")
    assert analysis["query_key"] == "k1" and analysis["finding"] is None
    # Expanding a day lists its hours; the hour lists its checks.
    hours = store.page(action="attention_check", group="hour",
                       since=checks["bucket_start"], until=checks["bucket_end"])["items"]
    assert [(item["count"], item["bucket_start"]) for item in hours] == [
        (1, HOUR + 3600), (2, HOUR)]
    assert len(store.page(group="none", since=HOUR, until=HOUR + 3600,
                          action="attention_check")["items"]) == 2
    assert [item["summary"] for item in store.page(group="none", action="deep_analysis",
                                                   outcome="skipped")["items"]] == [
        "UPDATE changes data", "INSERT changes data"]
    assert len(store.page(group="none")["items"]) == 9
    assert store.page(target="other")["items"] == []
    with pytest.raises(ValueError):
        store.page(group="week")


def test_local_days_follow_the_utc_offset(data_dir):
    store = AgentActivityStore()
    midnight = 1_790_000_000 // 86400 * 86400
    # 23:30 and 00:30 UTC: two UTC days, one day seven hours west of UTC.
    for ts in (midnight - 1800, midnight + 1800):
        store.append(_entry(ts, action="attention_check"))
    assert [item["count"] for item in store.page()["items"]] == [1, 1]
    west = store.page(utc_offset=-420)["items"]
    assert [item["count"] for item in west] == [2]
    assert west[0]["bucket_start"] == midnight - 86400 + 7 * 3600


def test_paging_never_splits_a_folded_day(data_dir):
    store = AgentActivityStore()
    day = HOUR // 86400 * 86400
    for minute in range(12):
        store.append(_entry(day + 3600 + minute * 60, action="attention_check"))
    for n in range(3):
        store.append(_entry(day + 7200 + n * 60))
    # Older days, far enough back that the page reads more than one window.
    for n in range(1, 4):
        store.append(_entry(day - n * 30 * 86400, action="attention_check"))
    seen, cursor = [], None
    while True:
        page = store.page(limit=2, cursor=cursor)
        seen += page["items"]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert [item["count"] for item in seen] == [1, 1, 1, 12, 1, 1, 1]
    assert len({item["key"] for item in seen}) == 7
    with pytest.raises(ValueError):
        store.page(cursor="nonsense")


def test_retention_prunes_entries_and_their_children(data_dir):
    now = [HOUR]
    store = AgentActivityStore(clock=lambda: now[0])
    old = store.append(_entry(HOUR), [{"sql": "SELECT 1", "kind": "session"}])
    now[0] = HOUR + RETENTION_SECONDS + 10
    # Writing prunes at most hourly; the next write is more than an hour later.
    store.append(_entry(now[0]))
    assert store.entry(old) is None
    assert store.prune() == 0
    assert store.read("SELECT COUNT(*) AS n FROM activity_statement")[0]["n"] == 0


def test_action_groups_calls_and_statements_into_one_entry(data_dir):
    with agent_activity.action("ask", "db", question="how many orders?") as act:
        agent_activity.record_ai_call(provider="readyset", model="glm", operation="sql_generation",
                                      purpose="sql_generation", tokens_in=5)
        act.statement("/*rdst:ask*/ SELECT count(*) FROM orders WHERE status = 'paid'",
                      duration_ms=2.0)
        act.statement("SELECT count(*) FROM orders WHERE status = 'shipped'", duration_ms=4.0)
        agent_activity.describe(subject="orders", decision="Ran the generated query")
    assert agent_activity.current() is None
    entry = AgentActivityStore().entry(AgentActivityStore().page()["items"][0]["id"])
    assert entry["subject"] == "orders"
    assert entry["details"]["question"] == "how many orders?"
    assert len(entry["ai_calls"]) == 1
    # Literals never reach the log; the same shape run twice is one row with two runs.
    (statement,) = entry["statements"]
    assert statement["sql"] == "SELECT count(*) FROM orders WHERE status = ?"
    assert (statement["executions"], statement["duration_ms"]) == (2, 6.0)


def test_empty_actions_are_dropped_and_failures_recorded(data_dir):
    with agent_activity.action("classification", "db"):
        pass
    with pytest.raises(RuntimeError):
        with agent_activity.action("deep_analysis", "db", keep=True):
            raise RuntimeError("connection refused\nmore detail")
    with agent_activity.action("classification", "db", default_outcome="skipped") as act:
        act.ai_call({"provider": "readyset-jev", "model": "jev", "error": "LOGIN_REQUIRED"})
    items = AgentActivityStore().page(group="none")["items"]
    assert [(item["action"], item["outcome"]) for item in items] == [
        ("classification", "failed"), ("deep_analysis", "failed")]
    assert items[1]["summary"] == "connection refused"


def test_hosted_calls_record_the_charged_cost_only_inside_an_action(data_dir, monkeypatch):
    monkeypatch.setattr(
        HostedProvider, "_complete_serialized",
        lambda self, request, **kwargs: ProviderResponse(
            text="ok", usage={"prompt_tokens": 1200, "completion_tokens": 80},
            raw={"usage": {"cost_microusd": 2500}}))
    manager = LLMManager()
    monkeypatch.setattr(manager, "_safe_load_key_for_query",
                        lambda provider: KeyResolution(api_key="t", is_trial=False,
                                                       provider="readyset"))
    manager.query(system_message="s", user_query="q", purpose="monitor_analyze")
    assert not AgentActivityStore().page()["items"]
    with agent_activity.action("deep_analysis", "db"):
        manager.query(system_message="s", user_query="q", purpose="monitor_analyze")
    entry = AgentActivityStore().entry(AgentActivityStore().page()["items"][0]["id"])
    (call,) = entry["ai_calls"]
    assert (call["provider"], call["model"], call["operation"]) == (
        "readyset", HOSTED_MODEL, "monitor_analyze")
    assert (call["tokens_in"], call["tokens_out"], call["cost_usd"]) == (1200, 80, 0.0025)
    assert call["cost_estimated"] is False
    assert "SQL text with sampled parameter values" in call["data_kinds"]


def test_hosted_failures_and_claude_usage_are_recorded(data_dir, monkeypatch):
    def refuse(self, request, **kwargs):
        raise LLMError("Sign in", code="LOGIN_REQUIRED")

    monkeypatch.setattr(HostedProvider, "_complete_serialized", refuse)
    with agent_activity.action("daily_snapshot", "db"):
        with pytest.raises(LLMError):
            HostedProvider().complete(ProviderRequest(model="m", messages=[]), api_key="t")
        # Claude BYOK calls reach the audit through the LLM usage telemetry choke point.
        telemetry.track_llm_usage(provider="claude", model="claude-sonnet-4-6",
                                  tokens_in=1_000_000, tokens_out=0, duration_ms=900,
                                  purpose="monitor_report")
    items = AgentActivityStore().page()["items"]
    entry = AgentActivityStore().entry(items[0]["id"])
    failed, claude = entry["ai_calls"]
    assert (failed["error"], failed["tokens_in"]) == ("LOGIN_REQUIRED", None)
    assert (claude["cost_usd"], claude["cost_estimated"]) == (3.0, True)
    # One call failed and one succeeded, so the snapshot itself isn't marked failed.
    assert items[0]["outcome"] == "done"


def test_jev_calls_record_tokens_and_the_keyservice_price(data_dir, monkeypatch):
    monkeypatch.setattr(JevClient, "_send", lambda self, path, payload: {
        "model": "jev-1.13.0", "usage": {"input_tokens": 50_000, "output_tokens": 12}})
    with agent_activity.action("attention_check", "db", keep=True):
        JevClient()._post("/v1/jev/attention", {
            "request_id": "r1", "attribution": {"operation": "monitor_attention"}})
    entry = AgentActivityStore().entry(AgentActivityStore().page()["items"][0]["id"])
    (call,) = entry["ai_calls"]
    assert (call["model"], call["purpose"], call["request_id"]) == (
        "jev-1.13.0", "attention_check", "r1")
    assert call["cost_usd"] == pytest.approx(0.0021)
    assert call["cost_estimated"] is True


class _FakeCursor:
    def __init__(self, fail=False):
        self.fail = fail

    def execute(self, query, args=None):
        if self.fail and query.lstrip().upper().startswith("EXPLAIN ANALYZE"):
            raise RuntimeError("canceling statement due to statement timeout")
        return 1


class _FakeMysqlConnection:
    def __init__(self, fail=False):
        self.fail = fail

    def cursor(self, *args):
        return _FakeCursor(self.fail)


def test_statements_record_placeholders_timeouts_and_errors(data_dir):
    assert agent_activity.instrument_mysql(conn := _FakeMysqlConnection()) is conn
    assert "_rdst_activity" not in vars(conn)
    with agent_activity.action("deep_analysis", "db", keep=True):
        conn = agent_activity.instrument_mysql(_FakeMysqlConnection(fail=True))
        cursor = conn.cursor()
        cursor.execute("SET SESSION max_execution_time = %s", (1500,))
        cursor.execute("/*rdst:analyze*/ EXPLAIN FORMAT=JSON SELECT * FROM events "
                       "WHERE props = 'secret@example.com' AND n > 42")
        with pytest.raises(RuntimeError):
            cursor.execute("EXPLAIN ANALYZE SELECT * FROM events WHERE props = 'x'")
        cursor.execute("SELECT DISTINCT `props` FROM `events` WHERE `props` IS NOT NULL LIMIT 5")
    entry = AgentActivityStore().entry(AgentActivityStore().page()["items"][0]["id"])
    statements = {row["kind"]: row for row in entry["statements"]}
    assert statements["session"]["sql"] == "SET SESSION max_execution_time = %s"
    assert statements["explain"]["sql"] == (
        "EXPLAIN FORMAT=JSON SELECT * FROM events WHERE props = ? AND n > ?")
    assert statements["explain"]["timeout_ms"] == 1500
    assert statements["explain_analyze"]["error"] == (
        "canceling statement due to statement timeout")
    assert statements["sample"]["sql"].endswith("LIMIT ?")
    assert "secret" not in json.dumps(entry)


def test_postgres_kwargs_carry_the_action_and_connection_timeout(data_dir):
    kwargs = {"host": "h", "options": "-c statement_timeout=5000"}
    assert agent_activity.pg_connection_kwargs(kwargs) is kwargs
    with agent_activity.action("daily_snapshot", "db") as act:
        wrapped = agent_activity.pg_connection_kwargs(kwargs)
        assert callable(wrapped["connection_factory"])
        assert agent_activity.pg_connection_kwargs(wrapped) is wrapped
        act.statement("SET LOCAL statement_timeout = %s", timeout_ms=1500)
    assert act.statements[0]["sql"] == "SET LOCAL statement_timeout = 1500"


def test_statement_kinds():
    kind = recorder.statement_kind
    assert kind("EXPLAIN (ANALYZE true, VERBOSE true) SELECT 1") == "explain_analyze"
    assert kind("EXPLAIN ANALYZE SELECT 1") == "explain_analyze"
    assert kind("EXPLAIN (GENERIC_PLAN true, VERBOSE true) SELECT 1") == "explain"
    assert kind("SELECT query FROM pg_stat_activity WHERE query_id = ANY(%s)") == "sample"
    assert kind("SELECT indexname FROM hypopg_create_index(%s)") == "hypothetical_index"
    assert kind("SELECT relname FROM pg_class") == "catalog"
    assert kind("SET LOCAL statement_timeout = %s") == "session"
    assert kind("SELECT id FROM orders") == "read"


def test_judge_and_analysis_outcomes():
    assert recorder.judge_summary("filed", 2, False) == ("done", "2 items filed")
    assert recorder.judge_summary("filed", 0, False) == ("done", "Nothing new to file")
    assert recorder.judge_summary("error", 0, False)[0] == "failed"
    assert parameter_note({"placeholders": [{}, {}, {}]}, "sampled") == (
        "3 parameters sampled from live sessions")
    assert parameter_note({"placeholders": []}, "none") is None
    outcome = analysis_outcome(
        {"state": "done", "mode": "analyze", "param_source": "sampled", "error": None},
        [{"kind": "explain_analyze", "timeout_ms": 1500}])
    assert outcome["decision"] == "Ran EXPLAIN ANALYZE with a 1.5 s timeout"
    skipped = analysis_outcome({"state": "skipped", "error": "Only SELECT statements"}, [])
    assert (skipped["outcome"], skipped["decision"]) == (
        "skipped", "Not run: Only SELECT statements")
    assert analysis_outcome({"state": "running"}, [])["outcome"] == "cancelled"


def test_backfill_runs_once_from_monitor_history(data_dir):
    monitor = MonitorStore()
    monitor.ensure_open()
    now = int(__import__("time").time())

    def seed(conn):
        conn.execute("INSERT INTO query_text VALUES ('db','k1','SELECT * FROM orders WHERE id = $1',"
                     "?,?)", (now, now))
        conn.execute("INSERT INTO analyze_job (target,query_key,reason,state,priority,mode,"
                     "param_source,enqueued_at,started_at,finished_at,billable_calls) VALUES "
                     "('db','k1','new','done',1,'analyze','sampled',?,?,?,2)",
                     (now - 60, now - 60, now - 30))
        conn.execute("INSERT INTO analyze_job (target,query_key,reason,state,priority,"
                     "enqueued_at) VALUES ('db','k1','new','queued',1,?)", (now,))
        conn.execute("INSERT INTO judge_run (target,ts,shortlist_json,request_json,response_json,"
                     "dry_run,outcome) VALUES ('db',?,'[]',?,?,0,'filed')",
                     (now - 300, json.dumps({"request_id": "r9"}), json.dumps(
                         {"model": "jev-1.13.0", "usage": {"input_tokens": 900,
                                                          "output_tokens": 30}})))
        conn.execute("INSERT INTO report_run (target,ts,trigger,state,billable_calls) VALUES "
                     "('db',?,'nightly','done',1)", (now - 3600,))
        # Started by the live check below: not history yet.
        conn.execute("INSERT INTO judge_run (target,ts,shortlist_json,dry_run,outcome) "
                     "VALUES ('db',?,'[]',1,'empty')", (now,))

    monitor._write(seed)
    # The live entry that opens the store is written once, never also backfilled.
    with agent_activity.action("attention_check", "db", keep=True) as act:
        act.started = now
    store = AgentActivityStore()
    items = [item for item in store.page(group="none")["items"] if item["source"] == "backfill"]
    assert len(store.page(group="none")["items"]) == 4
    assert sorted(item["action"] for item in items) == [
        "attention_check", "daily_snapshot", "deep_analysis"]
    assert {item["source"] for item in items} == {"backfill"}
    analysis = next(item for item in items if item["action"] == "deep_analysis")
    assert analysis["subject"] == "SELECT on orders by id"
    detail = store.entry(analysis["id"])
    assert detail["decision"] == "Ran EXPLAIN ANALYZE with parameters sampled from live sessions"
    check = store.entry(next(item["id"] for item in items if item["action"] == "attention_check"))
    assert check["ai_calls"][0]["tokens_in"] == 900
    # A second store instance (a restart) never backfills again.
    from shared.agent_activity import store as store_module

    store_module._OPENED.clear()
    AgentActivityStore().ensure_open()
    assert len(AgentActivityStore().page(group="none")["items"]) == 4


def test_api_pages_details_and_csv(data_dir):
    store = AgentActivityStore()
    for minute in range(3):
        store.append(_entry(HOUR + minute * 60, action="attention_check"))
    first = store.append(_entry(HOUR - 86400, summary='Timed out, "retry"'),
                         [{"sql": "EXPLAIN SELECT 1", "kind": "explain"}])
    store.append(_entry(HOUR - 3 * 86400, action="ask", details={"question": "top orders?"}))
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    client = TestClient(app)
    page = client.get("/api/agent-activity?limit=1").json()
    assert page["items"][0]["kind"] == "rollup" and page["targets"] == ["db"]
    rest = client.get(f"/api/agent-activity?limit=1&cursor={page['next_cursor']}").json()
    assert rest["items"][0]["id"] == first and rest["next_cursor"]
    hour = client.get(f"/api/agent-activity?group=none&since={HOUR}&until={HOUR + 3600}"
                      "&action=attention_check").json()
    assert len(hour["items"]) == 3
    assert client.get(f"/api/agent-activity/{first}").json()["statements"][0]["sql"] == (
        "EXPLAIN SELECT 1")
    assert client.get("/api/agent-activity/999").status_code == 404
    for bad in ("action=format_disk", "cursor=bad", "group=week", "utc_offset=5000",
                "outcome=maybe"):
        assert client.get(f"/api/agent-activity?{bad}").status_code == 422
    response = client.get("/api/agent-activity/export.csv?action=deep_analysis")
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [row["summary"] for row in rows] == ['Timed out, "retry"']
    assert rows[0]["time_utc"].endswith("Z")
    # A range export holds every entry in [since, until), checks included.
    since, until = HOUR - 2 * 86400, HOUR + 3600
    response = client.get(f"/api/agent-activity/export.csv?since={since}&until={until}")
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [row["action"] for row in rows] == ["attention_check"] * 3 + ["deep_analysis"]
    assert "rdst-agent-activity-from-" in response.headers["content-disposition"]
    everything = client.get("/api/agent-activity/export.csv").text
    assert "top orders?" in everything
    assert client.get(f"/api/agent-activity/export.csv?since={until}&until={since}"
                      ).status_code == 422


def test_deep_analysis_findings_read_the_stored_result(data_dir, monkeypatch):
    from shared.agent_activity import findings

    assert findings.analysis_finding({"index_recommendations": [
        {"table": "public.events", "columns": ["event_type", "created_at"]},
        {"table": "orders", "columns": ["id"]}]}) == (
        "suggested an index on events(event_type, created_at) and 1 more")
    assert findings.analysis_finding({"rewrite_suggestions": [{}, {}]}) == (
        "suggested 2 rewrites")
    assert findings.analysis_finding({"performance_assessment": {
        "overall_rating": "good"}}) == "rated it good, no index or rewrite needed"
    assert findings.analysis_finding({"performance_assessment": {
        "overall_rating": "poor"}}).startswith("rated it poor")
    assert findings.analysis_finding(None) is None

    calls = []

    class Registry:
        def get_stored_payload(self, query_hash, analysis_id):
            calls.append((query_hash, analysis_id))
            return {"llm_analysis": {"index_recommendations": [
                {"table": "orders", "columns": ["customer_id"]}]}}

    import shared.query_registry as query_registry

    monkeypatch.setattr(query_registry, "AnalysisResultsRegistry", Registry)
    monkeypatch.setattr(findings, "_CACHE", type(findings._CACHE)())
    ref = "/api/query-registry/abc/analysis/20260929_1"
    store = AgentActivityStore()
    store.append(_entry(HOUR, details={"analysis_ref": ref}))
    store.append(_entry(HOUR + 60, outcome="failed", details={"analysis_ref": ref}))
    items = store.page(group="none")["items"]
    assert [item.get("finding") for item in items] == [
        None, "suggested an index on orders(customer_id)"]
    store.page(group="none")
    assert calls == [("abc", "20260929_1")]
    assert store.export_rows()[1]["finding"] == "suggested an index on orders(customer_id)"
    assert findings.finding_for_ref("not a ref") is None


def test_daily_reports_carry_the_day_they_cover(data_dir):
    import asyncio

    @recorder.recorded_snapshot
    async def run_report(target, trigger):
        agent_activity.record_ai_call(provider="readyset", model="glm",
                                      operation="monitor_report")
        return {"id": 7, "state": "done", "period_start": HOUR - 86400,
                "period_end": HOUR, "report_json": {}}

    asyncio.run(run_report("db", "nightly"))
    monitor = MonitorStore()
    monitor.ensure_open()
    monitor._write(lambda conn: conn.execute(
        "INSERT INTO report_run (id,target,ts,trigger,state,period_start,period_end) VALUES "
        "(3,'db',?,'nightly','done',?,?)", (HOUR, HOUR - 2 * 86400, HOUR - 86400)))
    store = AgentActivityStore()
    # Written before the period was recorded with the entry: read from monitor.db.
    store.append(_entry(HOUR, action="daily_snapshot", subject="Nightly",
                        details={"report_id": 3}))
    reports = {item["report_id"]: item for item in store.page(action="daily_snapshot")["items"]}
    assert (reports[7]["period_start"], reports[7]["period_end"]) == (HOUR - 86400, HOUR)
    assert reports[3]["period_start"] == HOUR - 2 * 86400


def test_folded_lines_name_their_database_and_the_page_states_one_retention(data_dir):
    store = AgentActivityStore()
    for target in ("shop", "billing"):
        for minute in range(3):
            store.append(_entry(HOUR + minute * 60, action="attention_check", target=target,
                                subject=""))
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    page = TestClient(app).get("/api/agent-activity").json()
    rollups = [item for item in page["items"] if item["kind"] == "rollup"]
    assert sorted((item["target"], item["count"]) for item in rollups) == [
        ("billing", 3), ("shop", 3)]
    assert page["targets"] == ["billing", "shop"]
    assert page["retention_days"] * 86400 == RETENTION_SECONDS

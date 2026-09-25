"""Durable queue behavior without a database server, LLM, or terminal."""

import builtins
import asyncio
import selectors
import threading
from types import SimpleNamespace

import pytest

from features.monitor.analyze_queue import (
    AnalyzeQueue, analysis_mode, apply_suggestions, automatic_skip_reason,
    enqueue_for_report, enqueue_new_queries,
    enqueue_stale_top, enqueue_user, timeout_ms,
)
from shared.monitor.monitor_store import MonitorStore
from shared.llm_manager.inference_attribution import attribution_for, record_billable_call
from shared import analysis_lane

NOW = 1_800_000_000


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("RDST_MONITOR_ANALYZE_DAILY_CAP", "100")
    result = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    result.set_settings("db", {"enabled": True, "deep_analysis": True, "execute": True})
    return result


def add_query(store, key, weight, *, now=NOW):
    store.record("db", now, [{"query_key": key, "calls": 10, "total_ms": weight}],
                 {key: f"SELECT * FROM {key}"}, [], None)


def test_heaviest_first_and_auto_enqueue(store):
    add_query(store, "light", 20)
    add_query(store, "heavy", 200)
    jobs = enqueue_new_queries("db", store=store, now=NOW)
    assert {job["query_key"] for job in jobs} == {"heavy", "light"}
    assert store.claim_analysis("db", NOW)["query_key"] == "heavy"
    assert store.claim_analysis("db", NOW) is None
    assert enqueue_new_queries("db", store=store, now=NOW) == []


def test_daily_cap_and_user_override_of_automatic_switch(store, monkeypatch):
    add_query(store, "one", 10)
    add_query(store, "two", 10)
    store.set_settings("db", {"deep_analysis": False})
    assert enqueue_new_queries("db", store=store, now=NOW) == []
    monkeypatch.setenv("RDST_MONITOR_ANALYZE_DAILY_CAP", "1")
    job = enqueue_user("db", "one", store=store, now=NOW)
    assert job["reason"] == "user"
    assert store.claim_analysis("db", NOW, cap=1)["id"] == job["id"]
    store.finish_analysis(job["id"], "done", NOW)
    assert store.claim_analysis("db", NOW, cap=1) is None
    second = enqueue_user("db", "two", store=store, now=NOW)
    assert store.claim_analysis("db", NOW, cap=0)["id"] == second["id"]
    from features.monitor.analyze_queue import started_today
    assert started_today(store, "db", NOW) == 0


@pytest.mark.parametrize(("error", "billable", "spends"), [
    ("Failed to connect to MySQL: refused", 0, False),
    ("LOGIN_REQUIRED: Sign in to run Deep Analyze", 0, False),
    ("Failed to parse LLM response as JSON", 2, False),
    ("EXPLAIN ANALYZE timed out; retry tomorrow", 0, True),
    ("Deep Analyze did not store a result", 1, True),
    (None, 0, False),
])
def test_only_a_failure_with_a_usable_answer_spends_the_cap(store, error, billable, spends):
    from features.monitor.analyze_queue import budget

    store.set_settings("db", {"newcomer_daily_cap": 1})
    add_query(store, "one", 10)
    add_query(store, "two", 10)
    enqueue_new_queries("db", store=store, now=NOW)
    first = store.claim_analysis("db", NOW, cap=1, newcomer_cap=1)
    # No error: the run was skipped once it started (no real parameter values).
    store.finish_analysis(first["id"], "failed" if error else "skipped", NOW + 5, error=error,
                          billable_calls=billable)
    assert store.analyses_started_today("db", NOW + 10) == int(spends)
    assert budget(store, "db", NOW + 10)["newcomer_left"] == 1 - int(spends)
    # Only what today's allowance can start is queued.
    enqueue_new_queries("db", store=store, now=NOW + 10)
    second = store.claim_analysis("db", NOW + 10, cap=1, newcomer_cap=1)
    assert (second is None) == spends


def test_report_enqueue_works_when_automatic_analysis_is_off(store):
    add_query(store, "one", 10)
    store.set_settings("db", {"deep_analysis": False})
    jobs = enqueue_for_report("db", ["one"], store=store, now=NOW)
    assert len(jobs) == 1 and jobs[0]["reason"] == "report"
    store.claim_analysis("db", NOW)
    store.finish_analysis(jobs[0]["id"], "done", NOW, analysis_ref="saved")
    assert enqueue_for_report("db", ["one"], store=store, now=NOW + 60) == []


def test_turning_off_automatic_analysis_holds_queued_automatic_jobs(store):
    add_query(store, "one", 10)
    add_query(store, "two", 10)
    enqueue_new_queries("db", store=store, now=NOW)
    store.set_settings("db", {"deep_analysis": False})
    assert store.claim_analysis("db", NOW, allow_automatic=False) is None
    user = enqueue_user("db", "two", store=store, now=NOW)
    assert user["reason"] == "user"
    assert store.claim_analysis("db", NOW, allow_automatic=False)["id"] == user["id"]
    report = enqueue_for_report("db", ["one"], store=store, now=NOW)
    assert report[0]["reason"] == "report"


def test_restart_recovery_only_once(store):
    add_query(store, "one", 10)
    job = enqueue_user("db", "one", store=store, now=NOW)
    store.claim_analysis("db", NOW)
    reopened = MonitorStore(store.path, clock=lambda: NOW + 601)
    reopened.recover_analysis(NOW + 601, 600)
    assert reopened.analyze_jobs("db")[0]["state"] == "queued"
    reopened.claim_analysis("db", NOW + 601)
    reopened.recover_analysis(NOW + 1202, 600)
    row = reopened.analyze_jobs("db")[0]
    assert row["state"] == "failed"
    assert "timed out" in row["error"]
    assert row["id"] == job["id"]


def test_safety_and_timeout_decisions():
    from shared.query_safety import is_plain_explainable_write

    assert analysis_mode("SELECT * FROM t", 200, True) == "analyze"
    assert analysis_mode("UPDATE t SET x=1", 200, True) == "explain"
    assert is_plain_explainable_write("UPDATE t SET x=1")
    assert not is_plain_explainable_write("UPDATE t SET x=1; DROP TABLE t")
    assert analysis_mode("SELECT * FROM t", 3001, True) == "explain"
    assert analysis_mode("SELECT * FROM t", 200, False) == "explain"
    assert analysis_mode("SELECT pg_sleep(5)", 200, True) == "explain"
    assert analysis_mode("SELECT * INTO new_table FROM t", 200, True) == "explain"
    assert (timeout_ms(0), timeout_ms(2000), timeout_ms(100000)) == (1000, 10000, 30000)


@pytest.mark.parametrize("sql", [
    "INSERT INTO t SELECT 1", "UPDATE t SET x=1", "DELETE FROM t", "CREATE TABLE x(id int)",
    "DROP TABLE x", "VACUUM t", "SET work_mem='1MB'", "SHOW work_mem", "BEGIN",
    "COMMIT", "SELECT pg_sleep(1)", "SELECT * FROM t; DELETE FROM t",
    "SELECT * INTO new_table FROM t", "SELECT nextval('order_seq')",
])
def test_automatic_queue_rejects_unsupported_statements(sql):
    assert automatic_skip_reason(sql)


def test_automatic_queue_accepts_read_only_selects():
    assert automatic_skip_reason("SELECT * FROM t WHERE id=$1") is None
    assert automatic_skip_reason("WITH x AS (SELECT 1) SELECT * FROM x") is None
    assert automatic_skip_reason(
        "SELECT a FROM t WHERE c > now() - interval $2 ORDER BY c") is None
    assert automatic_skip_reason(
        "SELECT id, name FROM products WHERE attributes @> $1 AND active LIMIT $2") is None


def test_automatic_queue_reports_unparseable_statement():
    assert automatic_skip_reason("SELECT 'unterminated") == "RDST couldn't parse this statement"
    assert automatic_skip_reason("SELECT * FROM t FOR UPDATE")
    assert automatic_skip_reason("SELECT * FROM t FOR SHARE")
    assert automatic_skip_reason("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x")


def test_read_only_fallback_ignores_string_literals(monkeypatch):
    from features.monitor import analyze_queue

    monkeypatch.setattr(analyze_queue, "parse", lambda *args, **kwargs: (_ for _ in ()).throw(
        ValueError("unsupported syntax")))
    assert analyze_queue._read_only_select("SELECT 'DROP' FROM t WHERE x @> $1") is True
    assert analyze_queue._read_only_select("SELECT * FROM t; DELETE FROM t") is False
    assert analyze_queue._read_only_select("SELECT * FROM t FOR SHARE") is False


def test_stale_top_only_after_day(store):
    add_query(store, "one", 10)
    job = enqueue_user("db", "one", store=store, now=NOW)
    store.claim_analysis("db", NOW)
    store.finish_analysis(job["id"], "done", NOW, analysis_ref="a")
    assert enqueue_stale_top("db", store=store, now=NOW + 86399) == []
    assert enqueue_stale_top("db", store=store, now=NOW + 86401) == []  # no 24h traffic
    add_query(store, "one", 10, now=NOW + 86401)
    assert enqueue_stale_top("db", store=store, now=NOW + 86401)[0]["query_key"] == "one"


class FakeService:
    def __init__(self, *, error=None, llm_error=None):
        self.error = error
        self.llm_error = llm_error
        self.calls = []

    async def suggest_parameter_values(self, sql, target, config, key):
        return {"placeholders": []}

    async def analyze(self, input, options):
        self.calls.append((input, options))
        if self.error:
            yield SimpleNamespace(type="error", message=self.error)
        else:
            yield SimpleNamespace(type="complete", analysis_id="hash-1",
                                  query_hash="hash-1", stored_analysis_id="analysis-1",
                                  llm_analysis={"success": False, "error": "Sign in to Readyset",
                                                "error_code": self.llm_error}
                                  if self.llm_error else {"success": True},
                                  explain_results={"success": True})


@pytest.mark.asyncio
async def test_worker_uses_service_and_stores_result(store, monkeypatch):
    add_query(store, "one", 100)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get", lambda self, target: {})
    service = FakeService()
    queue = AnalyzeQueue(store, service=service, clock=lambda: NOW)
    await queue.tick()
    job = store.analyze_jobs("db")[0]
    assert job["state"] == "done" and job["analysis_ref"] == "/api/query-registry/hash-1/analysis/analysis-1"
    assert job["mode"] == "analyze" and job["param_source"] is None
    assert job["billable_calls"] == 0
    assert service.calls[0][1].non_interactive and not service.calls[0][1].hosted_only
    assert service.calls[0][1].statement_timeout_ms == 1000


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,operation", [
    ("new", "monitor_analyze"), ("user", "analyze_query"),
    ("report", "monitor_analyze"),
])
async def test_queue_attribution_and_billable_calls(store, monkeypatch, reason, operation):
    add_query(store, "one", 100)
    monkeypatch.delenv("RDST_DESKTOP", raising=False)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get", lambda self, target: {})

    class AttributedService(FakeService):
        async def analyze(self, input, options):
            self.attribution = attribution_for("analyze_query")
            record_billable_call()
            async for event in super().analyze(input, options):
                yield event

    if reason == "user":
        enqueue_user("db", "one", store=store, now=NOW)
    elif reason == "report":
        enqueue_for_report("db", ["one"], store=store, now=NOW)
    service = AttributedService()
    await AnalyzeQueue(store, service=service, clock=lambda: NOW).tick()
    job = store.analyze_jobs("db")[0]
    assert service.attribution["operation"] == operation
    assert service.attribution["feature"] == ("analyze" if reason == "user" else "monitor")
    assert service.attribution["surface"] == ("web" if reason == "user" else "cli")
    assert job["billable_calls"] == 1


@pytest.mark.asyncio
async def test_failed_analysis_keeps_completed_billable_call(store, monkeypatch):
    add_query(store, "one", 100)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get", lambda self, target: {})

    class FailedAfterInference(FakeService):
        async def analyze(self, input, options):
            record_billable_call()
            yield SimpleNamespace(type="error", message="Could not parse analysis")

    await AnalyzeQueue(store, service=FailedAfterInference(), clock=lambda: NOW).tick()
    job = store.analyze_jobs("db")[0]
    assert job["state"] == "failed"
    assert job["billable_calls"] == 1


@pytest.mark.asyncio
async def test_job_mode_uses_saved_plan_format(store, monkeypatch):
    store.record("db", NOW, [{"query_key": "q", "calls": 1, "total_ms": 10}],
                 {"q": "SELECT * FROM t WHERE id=$1"}, [])
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get", lambda self, target: {})

    class ActualAnalyze(FakeService):
        async def suggest_parameter_values(self, *args):
            return {"placeholders": [{"placeholder": "$1", "suggestions": []}]}

        async def analyze(self, input, options):
            assert options.fast  # No values requested plain EXPLAIN.
            yield SimpleNamespace(type="complete", query_hash="q", stored_analysis_id="one",
                                  llm_analysis={"success": True},
                                  explain_results={"success": True, "plan_format": "analyze",
                                                   "execution_time_ms": 9, "rows_examined": 10})

    await AnalyzeQueue(store, service=ActualAnalyze(), clock=lambda: NOW).tick()
    assert store.analyze_jobs("db")[0]["mode"] == "analyze"


@pytest.mark.asyncio
async def test_login_required_files_single_issue(store, monkeypatch):
    add_query(store, "one", 100)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get", lambda self, target: {})
    service = FakeService(llm_error="LOGIN_REQUIRED")
    queue = AnalyzeQueue(store, service=service, clock=lambda: NOW)
    await queue.tick()
    assert store.analyze_jobs("db")[0]["state"] == "failed"
    assert store.analyze_jobs("db")[0]["billable_calls"] == 0
    assert "Sign in" in store.analyze_jobs("db")[0]["error"]
    assert len(store.inbox(target="db")) == 1
    assert store.inbox(target="db")[0]["subject"] == "llm_login"
    assert store.inbox(target="db")[0]["type"] == "monitoring_issue"


@pytest.mark.asyncio
async def test_shutdown_discards_blocked_parameter_read(store, monkeypatch):
    original_select = selectors.EpollSelector.select
    monkeypatch.setattr(selectors.EpollSelector, "select",
                        lambda self, timeout=None: original_select(
                            self, 0.01 if timeout is None else min(timeout, 0.01)))
    add_query(store, "one", 100)
    job = store.enqueue_analysis("db", "one", "user", 1, NOW)
    job = store.claim_analysis("db", NOW)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get", lambda self, target: {})
    entered, release = threading.Event(), threading.Event()

    class BlockedService(FakeService):
        async def suggest_parameter_values(self, *args):
            def read():
                entered.set()
                release.wait(5)
                return {}
            return await asyncio.to_thread(read)

    queue = AnalyzeQueue(store, service=BlockedService(), clock=lambda: NOW)
    queue._task = asyncio.create_task(queue._process(job))
    assert await asyncio.to_thread(entered.wait, 2)
    stopping = asyncio.create_task(queue.stop())
    await asyncio.sleep(0.05)
    release.set()
    await stopping
    assert store.analyze_jobs("db")[0]["state"] == "running"
    assert not store.inbox(target="db")


def test_analysis_lane_cancels_driver_connections(monkeypatch):
    cancelled = []
    class PG:
        def cancel(self):
            cancelled.append("pg")
    run = analysis_lane.begin("db")
    conn = PG()
    analysis_lane.register("db", conn, "postgresql")
    analysis_lane.stop(run)
    assert cancelled == ["pg"]
    run = analysis_lane.begin("db")
    monkeypatch.setattr("shared.db_connection.cancel_query",
                        lambda conn, engine, config: cancelled.append(engine))
    analysis_lane.register("db", object(), "mysql", {"engine": "mysql"})
    analysis_lane.stop(run)
    assert cancelled[-1] == "mysql"


def test_analysis_lane_rejects_statement_after_stop():
    statements = []
    class Cursor:
        def execute(self, sql):
            statements.append(sql)
    class Connection:
        def cursor(self):
            return Cursor()
        def cancel(self):
            pass
    run = analysis_lane.begin("db")
    conn = Connection()
    analysis_lane.register("db", conn, "postgresql")
    guarded = analysis_lane.StatementConnection(conn, run)
    analysis_lane.stop(run)
    with pytest.raises(RuntimeError, match="stopped"):
        guarded.cursor().execute("SELECT 1")
    assert statements == []


def test_parameter_source_prefers_activity():
    sql, source = apply_suggestions("SELECT * FROM t WHERE id=$1", {
        "placeholders": [{"placeholder": "$1", "suggestions": [
            {"value": "42", "provenance": "pg_stat_activity (running now)"},
            {"value": "4", "provenance": "pg_stats"}]}]})
    assert sql == "SELECT * FROM t WHERE id=42" and source == "sampled"
    sql, source = apply_suggestions("SELECT * FROM t WHERE id=$1", {
        "placeholders": [{"placeholder": "$1", "suggestions": []}]})
    assert sql == "SELECT * FROM t WHERE id=$1" and source == "none"


LIVE = "pg_stat_activity (running now)"
TABLE = "Common value in t.status (pg_stats)"


@pytest.mark.parametrize("sql,suggestions,expected_sql,expected_source", [
    # Every value from live traffic.
    ("SELECT * FROM t WHERE a=$1 AND b=$2",
     [[{"value": "1", "provenance": LIVE}], [{"value": "x", "provenance": "monitor activity sample"}]],
     "SELECT * FROM t WHERE a=1 AND b='x'", "sampled"),
    # One value from the table's own data labels the whole run as table-sampled.
    ("SELECT * FROM t WHERE a=$1 AND status=$2",
     [[{"value": "1", "provenance": LIVE}], [{"value": "paid", "provenance": TABLE,
                                             "column_type": "text"}]],
     "SELECT * FROM t WHERE a=1 AND status='paid'", "table_sampled"),
    # A query-shape hint is not a real value: never substituted, the run is plan only.
    ("SELECT * FROM t WHERE status=$1 LIMIT $2",
     [[{"value": "paid", "provenance": TABLE, "column_type": "text"}],
      [{"value": "10", "provenance": "Query shape (LIMIT)"}]],
     "SELECT * FROM t WHERE status='paid' LIMIT $2", "none"),
    # A typed literal keeps its placeholder in a form a generic plan can read.
    ("SELECT * FROM t WHERE created > now() - interval $1",
     [[{"value": "7 days", "provenance": "Query shape (INTERVAL)"}]],
     "SELECT * FROM t WHERE created > now() - $1::interval", "none"),
    ("SELECT * FROM t WHERE d = DATE $1", [[]], "SELECT * FROM t WHERE d = $1::DATE", "none"),
    # MySQL digests: unfilled `?` stay `?`; no NULL or 'a' stands in for a value.
    ("SELECT * FROM t WHERE a = ? AND b LIKE ?",
     [[{"value": "3", "provenance": "performance_schema digest sample", "literal_sql": "3"}],
      [{"value": "%", "provenance": "Query shape (LIKE)"}]],
     "SELECT * FROM t WHERE a = 3 AND b LIKE ?", "none"),
    ("SELECT * FROM t WHERE a = ?",
     [[{"value": "3", "provenance": "performance_schema statement history"}]],
     "SELECT * FROM t WHERE a = 3", "sampled"),
    # No placeholders: nothing to look for.
    ("SELECT * FROM t WHERE a = 1", [], "SELECT * FROM t WHERE a = 1", None),
])
def test_values_policy(sql, suggestions, expected_sql, expected_source):
    from features.analyze.parameter_suggestions import enumerate_placeholders

    data = {"placeholders": [{"placeholder": p["placeholder"], "suggestions": options}
                             for p, options in zip(enumerate_placeholders(sql), suggestions)]}
    assert apply_suggestions(sql, data) == (expected_sql, expected_source)


@pytest.mark.asyncio
async def test_worker_plans_only_without_real_values(store, monkeypatch):
    store.record("db", NOW, [{"query_key": "one", "calls": 10, "total_ms": 100}],
                 {"one": "SELECT * FROM one WHERE id=$1"}, [], None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get",
                        lambda self, target: {"engine": "postgresql"})

    class NoValues(FakeService):
        async def suggest_parameter_values(self, sql, target, config, key):
            return {"placeholders": [{"placeholder": "$1", "suggestions": [
                {"value": "1", "provenance": "Query shape (LIMIT)"}]}]}

    service = NoValues()
    await AnalyzeQueue(store, service=service, clock=lambda: NOW).tick()
    job = store.analyze_jobs("db")[0]
    sent, options = service.calls[0]
    assert sent.sql == "SELECT * FROM one WHERE id=$1" and options.fast
    assert job["param_source"] == "none" and job["mode"] == "explain"


@pytest.mark.asyncio
async def test_mysql_without_real_values_is_skipped(store, monkeypatch):
    from features.monitor.analyze_queue import NO_VALUES_SKIP

    store.record("db", NOW, [{"query_key": "one", "calls": 10, "total_ms": 100}],
                 {"one": "SELECT * FROM one WHERE id = ?"}, [], None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get",
                        lambda self, target: {"engine": "mysql"})

    class NoValues(FakeService):
        async def suggest_parameter_values(self, sql, target, config, key):
            return {"placeholders": [{"placeholder": "?", "suggestions": []}]}

    service = NoValues()
    await AnalyzeQueue(store, service=service, clock=lambda: NOW).tick()
    job = store.analyze_jobs("db")[0]
    assert service.calls == []
    assert job["state"] == "skipped" and job["error"] == NO_VALUES_SKIP
    assert job["param_source"] == "none"


def test_postgres_16_plain_explain_uses_generic_plan(monkeypatch):
    from features.analyze.functions import explain_analysis

    statements = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, *args):
            statements.append(sql)

        def fetchone(self):
            return ([{"Plan": {}}],)

    class Connection:
        server_version = 160014

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(explain_analysis, "resolve_connection_params", lambda **kw: {"password": ""})
    monkeypatch.setattr(explain_analysis, "postgres_connection_kwargs", lambda *args, **kw: {})
    monkeypatch.setattr(explain_analysis, "_postgres_connection", lambda *args: Connection())
    result = explain_analysis._execute_postgres_explain_analyze(
        "SELECT 1 LIMIT NULL", {}, fast_mode=True)
    assert result["success"]
    assert any("EXPLAIN (GENERIC_PLAN true," in sql for sql in statements)


@pytest.mark.parametrize("version,planned", [(160014, True), (150004, False)])
def test_plan_only_keeps_placeholders_for_a_generic_plan(monkeypatch, version, planned):
    from features.analyze.functions import explain_analysis

    statements = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, *args):
            statements.append(sql)

        def fetchone(self):
            return ([{"Plan": {}}],)

    class Connection:
        server_version = version

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(explain_analysis, "resolve_connection_params", lambda **kw: {"password": ""})
    monkeypatch.setattr(explain_analysis, "postgres_connection_kwargs", lambda *args, **kw: {})
    monkeypatch.setattr(explain_analysis, "_postgres_connection", lambda *args: Connection())
    result = explain_analysis._execute_postgres_explain_analyze(
        "SELECT * FROM t WHERE id = $1", {}, fast_mode=True)
    assert result["success"] is planned
    if planned:
        assert any("GENERIC_PLAN true" in sql and "id = $1" in sql for sql in statements)
    else:
        assert "No real values" in result["error"] and statements == []
    # Measured runs never take placeholders.
    assert not explain_analysis._execute_postgres_explain_analyze(
        "SELECT * FROM t WHERE id = $1", {}, fast_mode=False)["success"]


@pytest.mark.asyncio
async def test_monitor_api_exposes_job_and_last_analysis(store, monkeypatch):
    from features.monitor.api import routes

    add_query(store, "one", 100)
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_now", lambda: NOW)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(analyze_queue=SimpleNamespace(wake=lambda: None))))
    job = await routes.enqueue_analysis("db", "one", request)
    assert job["state"] == "queued"
    assert routes.analyze_jobs("db", "queued")["jobs"][0]["id"] == job["id"]
    assert routes.queries("db")["queries"][0]["last_analysis"]["state"] == "queued"


@pytest.mark.asyncio
async def test_automatic_analysis_skips_writes_and_a_user_write_is_planned(store, monkeypatch):
    store.record("db", NOW, [{"query_key": "write", "calls": 1, "total_ms": 100}],
                 {"write": "UPDATE t SET x=1"}, [], None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get", lambda self, target: {})
    service = FakeService()
    # Writes never take a place in the top N, so automatic analysis never queues them.
    await AnalyzeQueue(store, service=service, clock=lambda: NOW).tick()
    await AnalyzeQueue(store, service=service, clock=lambda: NOW + 86400).tick()
    assert store.analyze_jobs("db") == [] and service.calls == []
    user = enqueue_user("db", "write", store=store, now=NOW + 86400)
    assert user["reason"] == "user"
    await AnalyzeQueue(store, service=service, clock=lambda: NOW + 86400).tick()
    assert store.analyze_jobs("db")[0]["mode"] == "explain"
    assert service.calls[0][1].fast


def test_automatic_skip_does_not_replace_queued_user_write(store):
    store.record("db", NOW, [{"query_key": "write", "calls": 1, "total_ms": 5}],
                 {"write": "UPDATE t SET x=1"}, [])
    user = enqueue_user("db", "write", store=store, now=NOW)
    assert user.pop("already_queued") is False
    enqueue_for_report("db", ["write"], store=store, now=NOW)
    assert store.analyze_jobs("db") == [user]


@pytest.mark.asyncio
async def test_timeout_keeps_retry_reason(store, monkeypatch):
    add_query(store, "one", 100)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get", lambda self, target: {})

    class TimeoutService(FakeService):
        async def analyze(self, input, options):
            yield SimpleNamespace(type="complete", analysis_id="partial",
                                  query_hash="hash-1", stored_analysis_id="partial",
                                  llm_analysis={"success": True},
                                  explain_results={"explain_analyze_timeout": True})

    await AnalyzeQueue(store, service=TimeoutService(), clock=lambda: NOW).tick()
    job = store.analyze_jobs("db")[0]
    assert job["state"] == "failed" and "retry" in job["error"]
    assert enqueue_new_queries("db", store=store, now=NOW + 3600) == []
    assert enqueue_new_queries("db", store=store, now=NOW + 86400)


def test_noninteractive_path_does_not_open_tty(monkeypatch):
    from features.analyze.functions import explain_analysis

    class Cursor:
        last = ""
        statements = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, *args):
            self.last = sql
            self.statements.append((sql, args))

        def fetchone(self):
            return (1,) if "pg_backend_pid" in self.last else ([{"Plan": {}}],)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def cursor(self):
            return Cursor()

        def close(self):
            pass

    def forbidden_open(path, *args, **kwargs):
        if path == "/dev/tty":
            raise AssertionError("terminal access")
        return original_open(path, *args, **kwargs)

    original_open = builtins.open
    monkeypatch.setattr(builtins, "open", forbidden_open)
    monkeypatch.setattr(explain_analysis, "psycopg2", SimpleNamespace(connect=lambda **kw: Connection()))
    monkeypatch.setattr(explain_analysis, "resolve_connection_params", lambda **kw: {"password": ""})
    monkeypatch.setattr(explain_analysis, "postgres_connection_kwargs", lambda *args, **kw: {})
    monkeypatch.setattr(explain_analysis, "_postgres_connection", lambda *args: Connection())
    monkeypatch.setattr(explain_analysis.signal, "signal", lambda *args: (_ for _ in ()).throw(
        AssertionError("signal handler installed")))
    result = explain_analysis._execute_postgres_explain_analyze(
        "SELECT 1", {}, non_interactive=True, statement_timeout_ms=1000)
    assert result["success"]
    assert sum(sql.startswith("SET LOCAL statement_timeout") for sql, _ in Cursor.statements) == 2


# The daily budget: local day, report reserve, a queue that only holds what can run

@pytest.fixture
def denver(monkeypatch):
    import time as time_module
    monkeypatch.setenv("TZ", "America/Denver")
    time_module.tzset()
    yield
    monkeypatch.undo()
    time_module.tzset()


def test_cap_follows_the_local_day(store, denver):
    from datetime import datetime
    from features.monitor.analyze_queue import started_today

    evening = int(datetime(2027, 1, 15, 19, 0).timestamp())  # 02:00 UTC the next day
    add_query(store, "one", 10, now=evening)
    add_query(store, "two", 10, now=evening)
    for key in ("one", "two"):
        store.enqueue_analysis("db", key, "top15_stale", 1, evening, cap=None)
    first = store.claim_analysis("db", evening, cap=1)
    store.finish_analysis(first["id"], "done", evening + 60)
    late = int(datetime(2027, 1, 15, 23, 30).timestamp())
    assert started_today(store, "db", late) == 1
    assert store.claim_analysis("db", late, cap=1) is None
    after_midnight = int(datetime(2027, 1, 16, 0, 5).timestamp())
    assert store.claim_analysis("db", after_midnight, cap=1)["query_key"] == "two"


def test_automatic_jobs_leave_the_report_reserve(store):
    from features.monitor.analyze_queue import report_reserve

    assert (report_reserve(27), report_reserve(10), report_reserve(4)) == (5, 2, 0)
    for key in ("a", "b", "c", "d"):
        add_query(store, key, 10)
        store.enqueue_analysis("db", key, "top15_stale", 10, NOW, cap=None)
    ran = store.claim_analysis("db", NOW, cap=2, reserve=1)
    store.finish_analysis(ran["id"], "done", NOW)
    assert store.claim_analysis("db", NOW, cap=2, reserve=1) is None
    report = enqueue_for_report("db", ["d"], store=store, now=NOW)[0]
    assert store.claim_analysis("db", NOW, cap=2, reserve=1)["id"] == report["id"]


def test_user_click_jumps_the_line_without_spending_the_cap(store):
    add_query(store, "heavy", 5000)
    enqueue_new_queries("db", store=store, now=NOW)
    add_query(store, "light", 1)
    user = enqueue_user("db", "light", store=store, now=NOW)
    assert user["already_queued"] is False
    assert store.claim_analysis("db", NOW, cap=5)["id"] == user["id"]
    from features.monitor.analyze_queue import started_today
    assert started_today(store, "db", NOW) == 0


def test_click_on_a_query_already_waiting_keeps_its_place(store):
    from features.monitor.analyze_queue import queue_plan

    add_query(store, "heavy", 5000)
    add_query(store, "light", 1)
    enqueue_new_queries("db", store=store, now=NOW)
    again = enqueue_user("db", "light", store=store, now=NOW)
    assert again["already_queued"] is True and again["reason"] == "new"
    assert queue_plan(store, "db", NOW)["jobs"][again["id"]]["queue_position"] == 2
    running = store.claim_analysis("db", NOW)
    assert enqueue_user("db", running["query_key"], store=store, now=NOW)["already_queued"]


def test_click_moves_a_held_job_to_the_front(store):
    from features.monitor.analyze_queue import OVER_ALLOWANCE, queue_plan, waiting_reason

    store.set_settings("db", {"newcomer_daily_cap": 1})
    for index in range(4):
        add_query(store, f"q{index}", 100 - index)
        store.enqueue_analysis("db", f"q{index}", "new", 100 - index, NOW, cap=None)
    plan = queue_plan(store, "db", NOW)
    states = [plan["jobs"][job["id"]]["queue_state"] for job in plan["queued"]]
    assert states.count("today") == 1 and states.count("held") == 3
    held = next(job for job in plan["queued"] if plan["jobs"][job["id"]]["queue_state"] == "held")
    assert waiting_reason(plan, held["id"]) == OVER_ALLOWANCE
    assert "midnight" not in OVER_ALLOWANCE
    moved = enqueue_user("db", held["query_key"], store=store, now=NOW)
    assert moved["already_queued"] is False and moved["reason"] == "user"
    assert queue_plan(store, "db", NOW)["jobs"][moved["id"]]["queue_position"] == 1


def test_queue_only_holds_what_today_can_start(store, monkeypatch):
    # The top 20, with room for 4 newcomers today: nothing waits for a later day.
    store.set_settings("db", {"analysis_daily_cap": 20, "newcomer_daily_cap": 4})
    for index in range(20):
        add_query(store, f"q{index:02}", 100 + index)
    jobs = enqueue_new_queries("db", store=store, now=NOW)
    assert len(jobs) == 4
    assert {job["query_key"] for job in jobs} == {f"q{index:02}" for index in range(16, 20)}
    # A heavier newcomer displaces the lightest waiting job.
    add_query(store, "big", 10_000)
    assert [job["query_key"] for job in enqueue_new_queries("db", store=store, now=NOW)] == ["big"]
    user = enqueue_user("db", "q00", store=store, now=NOW)
    queue = AnalyzeQueue(store, clock=lambda: NOW)
    queue._prepare(NOW)
    queued = store.analyze_jobs("db", "queued")
    automatic = [job for job in queued if job["reason"] != "user"]
    assert len(automatic) == 4 and "q16" not in {j["query_key"] for j in automatic}
    assert user["id"] in {job["id"] for job in queued}
    # Dropped queries come back once they rank again.
    assert all(job["state"] != "skipped" for job in store.analyze_jobs("db"))


def test_trim_keeps_user_and_report_jobs(store):
    for index in range(10):
        add_query(store, f"q{index}", 10 + index)
        store.enqueue_analysis("db", f"q{index}", "new", 10 + index, NOW)
    report = enqueue_for_report("db", ["q0"], store=store, now=NOW)[0]
    assert store.trim_automatic_analyses("db", 2) == 7
    left = {job["query_key"] for job in store.analyze_jobs("db", "queued")}
    assert left == {"q9", "q8", "q0"} and report["reason"] == "report"


def test_priority_follows_current_load_at_dispatch(store, monkeypatch):
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    add_query(store, "early", 500)
    add_query(store, "later", 10)
    enqueue_new_queries("db", store=store, now=NOW)
    add_query(store, "later", 50_000, now=NOW + 60)
    queue = AnalyzeQueue(store, clock=lambda: NOW + 60)
    assert queue._claim("db")["query_key"] == "later"


def test_queue_plan_positions(store):
    from features.monitor.analyze_queue import queue_plan, waiting_reason

    store.set_settings("db", {"analysis_daily_cap": 5})
    for index in range(5):
        add_query(store, f"q{index}", 100 - index)
        store.enqueue_analysis("db", f"q{index}", "top15_stale", 100 - index, NOW)
    for index, key in enumerate(("q0", "q1")):
        job = store.claim_analysis("db", NOW + index * 100, cap=5, reserve=1)
        store.finish_analysis(job["id"], "done", NOW + index * 100 + 60)
    running = store.claim_analysis("db", NOW + 300, cap=5, reserve=1)
    user = enqueue_user("db", "q4", store=store, now=NOW + 300)
    plan = queue_plan(store, "db", NOW + 320)
    assert plan["average_run_s"] == 60 and plan["used"] == 3 and plan["automatic_left"] == 1
    assert plan["running"]["id"] == running["id"]
    assert plan["jobs"][user["id"]] == {"queue_state": "today", "queue_position": 1,
                                        "estimated_start_at": NOW + 360}
    q3 = next(job for job in plan["queued"] if job["query_key"] == "q3")
    assert plan["jobs"][q3["id"]]["queue_state"] == "today"
    assert plan["jobs"][q3["id"]]["queue_position"] == 2
    assert waiting_reason(plan, q3["id"]) is None


def test_analyze_jobs_endpoint_reports_the_queue(store, monkeypatch):
    from features.monitor.api import routes

    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_now", lambda: NOW)
    store.set_settings("db", {"analysis_daily_cap": 10})
    for index in range(3):
        add_query(store, f"q{index}", 100 - index)
        store.enqueue_analysis("db", f"q{index}", "top15_stale", 100 - index, NOW)
    done = store.claim_analysis("db", NOW, cap=10)
    store.finish_analysis(done["id"], "failed", NOW + 30, error="LOGIN_REQUIRED: sign in")
    store.claim_analysis("db", NOW + 30, cap=10)
    page = routes.analyze_jobs("db")
    queue = page["queue"]
    # The sign-in failure never reached the AI, so only the running job spends the cap.
    assert (queue["cap"], queue["used"], queue["left"], queue["reserved"]) == (10, 1, 9, 2)
    assert queue["running"]["display_name"] and queue["running"]["queue_state"] == "running"
    assert [job["query_key"] for job in queue["next"]] == ["q2"]
    assert queue["next"][0]["queue_position"] == 1
    assert queue["recent"][0]["reason_text"] == "Sign in to run deep analysis."
    waiting = next(job for job in page["jobs"] if job["state"] == "queued")
    assert waiting["queue_state"] == "today" and waiting["estimated_start_at"] >= NOW
    from features.monitor.api.models import AnalyzeJobPage
    AnalyzeJobPage.model_validate(page)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["user", "new"])
async def test_runs_the_user_asked_for_test_rewrites(store, monkeypatch, reason):
    """A run the user asked for tests the suggested rewrites under its own timeout;
    automatic runs only read the original statement."""
    add_query(store, "one", 100)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get", lambda self, target: {})
    if reason == "user":
        enqueue_user("db", "one", store=store, now=NOW)
    service = FakeService()
    await AnalyzeQueue(store, service=service, clock=lambda: NOW).tick()
    options = service.calls[0][1]
    assert options.test_rewrites is (reason == "user")
    assert options.non_interactive and options.statement_timeout_ms == 1000


def test_api_reports_no_param_source_for_queries_without_parameters():
    from features.monitor.api.views import _param_source

    assert _param_source("none", "SELECT * FROM t WHERE id = 1") is None
    assert _param_source("none", "SELECT * FROM t WHERE id = $1") == "none"
    assert _param_source("none", "SELECT * FROM t WHERE id = ?") == "none"
    assert _param_source("sampled", "SELECT * FROM t WHERE id = 1") == "sampled"


def test_an_ai_service_failure_retries_with_backoff_then_gives_up(store):
    from features.monitor.analyze_queue import (RETRY_BACKOFF_S, analysis_retry,
                                                enqueue_transient_retries, retry_plan)

    add_query(store, "one", 10)
    enqueue_new_queries("db", store=store, now=NOW)
    now = NOW
    for attempt, wait in enumerate(RETRY_BACKOFF_S, start=1):
        job = store.claim_analysis("db", now)
        assert job["query_key"] == "one"
        store.finish_analysis(job["id"], "failed", now, error="UPSTREAM_ERROR: no answer")
        plan = retry_plan(store, "db", now)["one"]
        assert (plan["attempts"], plan["retry_at"]) == (attempt, now + wait)
        row = analysis_retry(store.analyze_jobs("db")[0], plan)
        assert row["error_kind"] == "retrying" and "tries again" in row["reason"]
        # Not before its backoff is up, then queued again with its original reason.
        assert enqueue_transient_retries("db", store=store, now=now + wait - 1) == []
        now += wait
        retried = enqueue_transient_retries("db", store=store, now=now)
        assert [job["reason"] for job in retried] == ["new"]
    job = store.claim_analysis("db", now)
    store.finish_analysis(job["id"], "failed", now, error="UPSTREAM_ERROR: no answer")
    plan = retry_plan(store, "db", now)["one"]
    assert plan["retry_at"] is None and plan["attempts"] == len(RETRY_BACKOFF_S) + 1
    assert enqueue_transient_retries("db", store=store, now=now + 86_000) == []
    row = analysis_retry(store.analyze_jobs("db")[0], plan)
    assert "error_kind" not in row and "tries again tomorrow" in row["reason"]


def test_a_query_failure_is_not_retried_as_transient(store):
    from features.monitor.analyze_queue import enqueue_transient_retries, retry_plan

    add_query(store, "one", 10)
    enqueue_new_queries("db", store=store, now=NOW)
    job = store.claim_analysis("db", NOW)
    store.finish_analysis(job["id"], "failed", NOW, error="relation \"one\" does not exist")
    assert retry_plan(store, "db", NOW + 60) == {}
    assert enqueue_transient_retries("db", store=store, now=NOW + 3600) == []


def test_work_lists_running_queued_and_done_today_across_databases(store, monkeypatch):
    from features.monitor.api import routes

    store.set_settings("other", {"enabled": True, "deep_analysis": True})
    add_query(store, "one", 300)
    add_query(store, "two", 200)
    add_query(store, "three", 100)
    store.record("other", NOW, [{"query_key": "x", "calls": 5, "total_ms": 50}],
                 {"x": "SELECT * FROM x"}, [], None)
    enqueue_new_queries("db", store=store, now=NOW)
    enqueue_new_queries("other", store=store, now=NOW)
    done = store.claim_analysis("db", NOW)
    store.finish_analysis(done["id"], "done", NOW + 10)
    store.claim_analysis("db", NOW + 20)
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_now", lambda: NOW + 30)
    work = routes.monitor_work()
    assert [(job["target"], job["query_key"]) for job in work["running"]] == [("db", "two")]
    assert {(job["target"], job["query_key"], job["queue_position"])
            for job in work["queued"]} == {("db", "three", 1), ("other", "x", 1)}
    assert all(job["source"] == "monitoring" for job in work["queued"])
    assert [job["query_key"] for job in work["done_today"]] == ["one"]
    assert work["done_today_count"] == 1 and work["reports"] == []


@pytest.mark.asyncio
async def test_waiting_work_that_cannot_start_is_looked_at_again_within_a_minute(store,
                                                                                 monkeypatch):
    from features.monitor import analyze_queue

    add_query(store, "one", 100)
    monkeypatch.setattr(AnalyzeQueue, "_claim", lambda *args: None)
    queue = AnalyzeQueue(store, service=FakeService(), clock=lambda: NOW)
    await queue.tick()
    assert store.analyze_jobs("db")[0]["state"] == "queued"
    assert queue._retry_at == NOW + analyze_queue.WAITING_RECHECK_S


@pytest.mark.asyncio
async def test_manual_click_after_cap_starts_worker(store, monkeypatch):
    from unittest.mock import AsyncMock, Mock
    from features.monitor import analyze_queue
    from features.monitor.api import routes

    for key in ("automatic", "manual", "waiting"):
        add_query(store, key, 100)
    store.set_settings("db", {"analysis_daily_cap": 1})
    automatic = store.enqueue_analysis("db", "automatic", "top15_stale", 1, NOW)
    store.claim_analysis("db", NOW, cap=1)
    store.finish_analysis(automatic["id"], "done", NOW + 1)
    store.enqueue_analysis("db", "waiting", "top15_stale", 1, NOW, cap=None)
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_now", lambda: NOW + 2)
    queue = SimpleNamespace(wake=Mock())
    start = AsyncMock(return_value=queue)
    monkeypatch.setattr(analyze_queue, "start_analyze_queue", start)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(analyze_queue=None)))
    job = await routes.enqueue_analysis("db", "manual", request)
    start.assert_awaited_once()
    queue.wake.assert_called_once()
    assert job["queue_state"] == "today" and job["queue_position"] == 1
    assert store.claim_analysis("db", NOW + 2, cap=1)["id"] == job["id"]
    store.finish_analysis(job["id"], "done", NOW + 3)
    assert analyze_queue.started_today(store, "db", NOW + 3) == 1
    assert store.claim_analysis("db", NOW + 3, cap=1) is None


@pytest.mark.asyncio
async def test_manual_job_runs_without_monitoring(store, monkeypatch):
    add_query(store, "manual", 1)
    store.set_settings("db", {"enabled": False, "analysis_daily_cap": 0})
    job = enqueue_user("db", "manual", store=store, now=NOW)
    service = FakeService()
    queue = AnalyzeQueue(store, service=service, clock=lambda: NOW)
    await queue.tick()
    assert store.read("SELECT state FROM analyze_job WHERE id=?", (job["id"],))[0]["state"] == "done"


@pytest.mark.asyncio
async def test_transient_retry_is_claimed_when_due(store, monkeypatch):
    from features.monitor import analyze_queue

    add_query(store, "one", 1)
    job = store.enqueue_analysis("db", "one", "new", 1, NOW)
    store.claim_analysis("db", NOW)
    store.finish_analysis(job["id"], "failed", NOW, error="HOSTED_INFERENCE_UNAVAILABLE")
    service = FakeService()
    current = [NOW + 1]
    queue = AnalyzeQueue(store, service=service, clock=lambda: current[0])
    await queue.tick()
    assert not service.calls and queue._retry_at == NOW + 60
    current[0] = NOW + 60
    await queue.tick()
    assert len(service.calls) == 1
    assert store.latest_analysis_jobs("db")["one"]["state"] == "done"


@pytest.mark.asyncio
async def test_background_and_manual_work_run_during_database_load(store):
    from features.monitor import analyze_queue

    store.record("db", NOW, [], {}, [], {"calls": 10, "total_ms": 1000,
                                         "active_conns": 10, "max_conns": 10})
    for key in ("automatic", "manual"):
        add_query(store, key, 100)
    store.enqueue_analysis("db", "automatic", "new", 100, NOW)
    enqueue_user("db", "manual", store=store, now=NOW)
    service = FakeService()
    queue = AnalyzeQueue(store, service=service, clock=lambda: NOW)
    plan = analyze_queue.queue_plan(store, "db", NOW)
    assert {job["queue_state"] for job in plan["jobs"].values()} == {"today"}
    await queue.tick()
    assert len(service.calls) == 2
    assert all(job["state"] == "done" for job in store.latest_analysis_jobs("db").values())


def test_retry_signed_out_shows_hold_until_signin(store, monkeypatch):
    from features.monitor import analyze_queue, database_limit

    add_query(store, "one", 1)
    job = store.enqueue_analysis("db", "one", "new", 1, NOW)
    store.claim_analysis("db", NOW)
    store.finish_analysis(job["id"], "failed", NOW, error="HOSTED_INFERENCE_UNAVAILABLE")
    failed = store.latest_analysis_jobs("db")["one"]
    retry = analyze_queue.retry_plan(store, "db", NOW)["one"]
    monkeypatch.setattr(database_limit, "_signed_in", lambda: False)
    facts = analyze_queue.analysis_retry(failed, retry)
    assert facts["retry_at"] is None and facts["reason"] == "Sign in to turn on AI monitoring"
    monkeypatch.setattr(database_limit, "_signed_in", lambda: True)
    assert analyze_queue.analysis_retry(failed, retry)["retry_at"] == NOW + 60


@pytest.mark.asyncio
async def test_restart_resumes_manual_queue_without_monitoring(store, monkeypatch):
    from unittest.mock import AsyncMock
    from features.monitor import analyze_queue
    from shared.api.app import lifespan

    monkeypatch.setattr("features.query_registry.assessment.query_assessment_worker",
                        SimpleNamespace(start=AsyncMock(), stop=AsyncMock(),
                                        set_background_targets=lambda targets: None))
    monkeypatch.setenv("RDST_DATA_DIR", str(store.path.parent))
    add_query(store, "manual", 1)
    store.set_settings("db", {"enabled": False})
    enqueue_user("db", "manual", store=store, now=NOW)
    monkeypatch.setattr("features.query_registry.scheduler.monitored_targets", lambda: set())
    monkeypatch.setattr("features.query_registry.scheduler.observation_scheduler_enabled", lambda: False)
    queue = SimpleNamespace(stop=AsyncMock())
    start = AsyncMock(return_value=queue)
    monkeypatch.setattr(analyze_queue, "start_analyze_queue", start)
    async with lifespan(None):
        start.assert_awaited_once()
    queue.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_over_todays_allowance_is_not_queued_for_later(store):
    add_query(store, "one", 1)
    store.set_settings("db", {"newcomer_daily_cap": 1})
    job = store.enqueue_analysis("db", "one", "new", 1, NOW)
    store.claim_analysis("db", NOW)
    store.finish_analysis(job["id"], "failed", NOW, error="HOSTED_INFERENCE_UNAVAILABLE",
                          billable_calls=1)
    queue = AnalyzeQueue(store, service=FakeService(), clock=lambda: NOW + 60)
    await queue.tick()
    # The failed run spent today's only newcomer analysis, so the retry isn't kept waiting.
    assert store.latest_analysis_jobs("db")["one"]["state"] == "failed"
    assert store.analyze_jobs("db", "queued") == []


@pytest.mark.asyncio
async def test_stale_retry_time_uses_regular_queue_interval(store, monkeypatch):
    from features.monitor import analyze_queue

    queue = AnalyzeQueue(store, service=FakeService(), clock=lambda: NOW)
    async def tick():
        queue._retry_at = NOW - 1
    timeouts = []
    async def wait(awaitable, *, timeout):
        awaitable.close()
        timeouts.append(timeout)
        raise asyncio.CancelledError
    monkeypatch.setattr(queue, "tick", tick)
    monkeypatch.setattr(analyze_queue.asyncio, "wait_for", wait)
    with pytest.raises(asyncio.CancelledError):
        await queue._run()
    assert timeouts == [queue.interval_s]
    assert queue._retry_at is None


@pytest.mark.parametrize('execute,limit', [(False, 3), (True, 1)])
def test_atomic_concurrent_claims_and_mode_changes(store, execute, limit):
    from concurrent.futures import ThreadPoolExecutor

    store.set_settings('db', {'execute': execute})
    for index in range(6):
        add_query(store, f'q{index}', 100 - index)
    enqueue_new_queries('db', store=store, now=NOW)
    add_query(store, 'clicked', 1)
    user = enqueue_user('db', 'clicked', store=store, now=NOW)
    first = store.claim_analysis('db', NOW)
    assert first['id'] == user['id']
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: store.claim_analysis('db', NOW), range(5)))
    claimed = [first] + [job for job in claims if job]
    assert len(claimed) == limit
    assert len({job['id'] for job in claimed}) == limit
    assert all(job['mode'] == ('analyze' if execute else 'explain') for job in claimed)
    store.set_settings('db', {'execute': not execute})
    assert store.claim_analysis('db', NOW) is None
    for job in claimed:
        assert not store.finish_analysis(job['id'], 'done', NOW, owner='stale', attempt=job['attempt'])
        assert store.finish_analysis(job['id'], 'done', NOW, owner=job['owner'], attempt=job['attempt'])
    assert store.claim_analysis('db', NOW) is not None


def test_parallel_claims_reserve_newcomer_allowance(store):
    store.set_settings('db', {'execute': False})
    for index in range(5):
        add_query(store, f'q{index}', 100 - index)
    later = NOW + 7200
    enqueue_new_queries('db', store=store, now=later)
    claimed = [store.claim_analysis('db', later, newcomer_cap=2) for _ in range(3)]
    assert sum(job is not None for job in claimed) == 2
    assert store.analyses_started_today('db', later, ('new',)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('execute,limit', [(False, 3), (True, 1)])
async def test_dispatch_fills_database_slots(store, monkeypatch, execute, limit):
    store.set_settings('db', {'execute': execute})
    for index in range(5):
        add_query(store, f'q{index}', 100 - index)
    enqueue_new_queries('db', store=store, now=NOW)
    queue = AnalyzeQueue(store, clock=lambda: NOW)
    monkeypatch.setattr(queue, '_prepare', lambda now: ['db'])
    release = asyncio.Event()
    loop = asyncio.get_running_loop()

    def claim(target):
        loop.call_soon_threadsafe(release.clear)
        job = store.claim_analysis(target, NOW)
        if job is None:
            loop.call_soon_threadsafe(release.set)
        return job

    monkeypatch.setattr(queue, '_claim', claim)
    active = 0
    peak = 0
    started = []

    async def process(job):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.append(job['query_key'])
        await release.wait()
        store.finish_analysis(job['id'], 'done', NOW, owner=job['owner'], attempt=job['attempt'])
        active -= 1

    monkeypatch.setattr(queue, '_process_recorded', process)
    await queue.tick()
    assert peak == limit
    assert started == [f'q{index}' for index in range(5)]


def test_concurrent_recovery_fences_every_attempt(store):
    store.set_settings('db', {'execute': False})
    for index in range(3):
        add_query(store, f'q{index}', 100 - index)
    enqueue_new_queries('db', store=store, now=NOW)
    old = [store.claim_analysis('db', NOW) for _ in range(3)]
    store.recover_analysis(NOW + 100, 60)
    fresh = [store.claim_analysis('db', NOW + 100) for _ in range(3)]
    assert {job['id'] for job in old} == {job['id'] for job in fresh}
    for job in old:
        assert not store.analysis_owned(job)
        assert not store.retry_analysis(job)
        assert not store.finish_analysis(job['id'], 'done', NOW + 100,
                                         owner=job['owner'], attempt=job['attempt'])
    assert all(store.analysis_owned(job) for job in fresh)


def test_analysis_connections_share_three_slots_and_cancel_separately(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context

    first = analysis_lane.begin('budget-test', execute=False)
    first_context = copy_context()
    second = analysis_lane.begin('budget-test', execute=False)
    cancelled = []
    monkeypatch.setattr('shared.db_connection.cancel_query',
                        lambda conn, engine, config: cancelled.append(conn))
    first_context.run(analysis_lane.register, 'budget-test', 'first', 'postgresql')
    analysis_lane.register('budget-test', 'second', 'postgresql')
    analysis_lane.stop(first)
    assert cancelled == ['first']
    assert not second.stopped.is_set()
    analysis_lane.finish(first)
    analysis_lane.finish(second)
    release = threading.Event()
    full = threading.Event()
    lock = threading.Lock()
    active = peak = 0

    def sample():
        nonlocal active, peak
        run = analysis_lane.begin('budget-test', execute=False)
        try:
            with analysis_lane.connection_budget():
                with lock:
                    active += 1
                    peak = max(peak, active)
                    if active == 3:
                        full.set()
                assert release.wait(5)
                with lock:
                    active -= 1
        finally:
            analysis_lane.finish(run)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(sample) for _ in range(8)]
        assert full.wait(5)
        release.set()
        for future in futures:
            future.result()
    assert peak == analysis_lane.PLAN_ANALYSIS_CONCURRENCY


def test_startup_page_uses_pending_dispatch_order_before_range_load(store, monkeypatch):
    from features.monitor.api.views import _query_page

    store.set_settings('db', {'execute': False})
    for index in range(4):
        add_query(store, f'q{index}', 100 - index)
    enqueue_new_queries('db', store=store, now=NOW)
    # A queue snapshot can rank a query ahead of the selected range's heavier rows.
    order = ['q3', 'q2', 'q1']
    page = _query_page(store, 'db', NOW - 60, NOW + 1, 60, False,
                       'db_time_share', '', '', 2, 0, None, locate='q1', active_order=order)
    assert page[0] == ['q3', 'q2']
    assert page[-1] == 2


@pytest.mark.parametrize("reserve,expected", [(0, 2), (1, 1)])
def test_parallel_claims_reserve_daily_allowance(store, reserve, expected):
    from concurrent.futures import ThreadPoolExecutor

    store.set_settings("db", {"execute": False})
    later = NOW + 7200
    for index in range(5):
        key = f"q{index}"
        add_query(store, key, 100 - index)
        store.enqueue_analysis("db", key, "top15_stale", 100 - index, later)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: store.claim_analysis("db", later, cap=2, reserve=reserve),
                               range(4)))
    assert sum(job is not None for job in claims) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("detail,expected", [
    ("Registry is read-only", "Registry is read-only"),
    (None, "missing stored_analysis_id"),
])
async def test_worker_missing_stored_result_keeps_detail(store, monkeypatch, detail, expected):
    add_query(store, "one", 100)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get", lambda self, target: {})

    class MissingResult(FakeService):
        async def analyze(self, input, options):
            yield SimpleNamespace(
                type="complete", query_hash="hash-1", stored_analysis_id=None,
                llm_analysis={"success": True}, explain_results={"success": True},
                formatted={"error": detail} if detail else {},
            )

    await AnalyzeQueue(store, service=MissingResult(), clock=lambda: NOW).tick()
    job = store.analyze_jobs("db")[0]
    assert job["state"] == "failed"
    assert job["error"] == f"Deep Analyze did not store a result: {expected}"

"""Monitoring prerequisite checks per engine and provider, on scripted connections."""

from __future__ import annotations

import json
from typing import Any, Callable

import pytest

from features.configure.prerequisites import (
    MYSQL_PROBE_SQL,
    POSTGRES_PROBE_SQL,
    Context,
    check_mysql,
    check_postgres,
    check_target,
    detect_provider,
)


class ScriptedCursor:
    def __init__(self, conn: "ScriptedConnection"):
        self.conn = conn
        self.rows: list[tuple] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.conn.log.append(sql)
        for needle, answer in self.conn.rules:
            if needle in sql:
                value = answer(sql, params) if callable(answer) else answer
                if isinstance(value, Exception):
                    raise value
                self.rows = list(value)
                return
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchall(self) -> list[tuple]:
        return self.rows

    def close(self) -> None:
        pass


class ScriptedConnection:
    """Answers each statement from the first rule whose needle appears in it."""

    def __init__(self, rules: list[tuple[str, Any]]):
        self.rules = rules
        self.log: list[str] = []
        self.closed = False

    def cursor(self) -> ScriptedCursor:
        return ScriptedCursor(self)

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class ProbeCounter:
    """pg_stat_statements / digest counter that the probe session bumps."""

    def __init__(self, visible: bool = True):
        self.calls = 0
        self.visible = visible
        self.probes: list[str] = []

    def read(self, _sql: str, _params: Any) -> list[tuple]:
        return [(self.calls,)]

    def open_probe(self) -> ScriptedConnection:
        def run(sql: str, _params: Any) -> list[tuple]:
            self.probes.append(sql)
            if self.visible:
                self.calls += 1
            return [("x",)]

        return ScriptedConnection([("rdst_visibility_probe", run)])


def by_id(checks) -> dict[str, Any]:
    return {check.id: check for check in checks}


def pg_rules(
    *,
    roles: list[str] | None = None,
    aurora: bool = False,
    available: bool = True,
    schema: str | None = "public",
    preload: Any = "pg_stat_statements",
    view_error: Exception | None = None,
    superuser: bool = True,
    member: bool = False,
    hidden: int = 0,
    query_size: str = "4kB",
    track: str = "top",
    counter: ProbeCounter | None = None,
) -> list[tuple[str, Any]]:
    counter = counter or ProbeCounter()
    return [
        ("FROM pg_catalog.pg_roles WHERE rolname IN", [(role,) for role in roles or []]),
        ("proname = 'aurora_version'", [(1 if aurora else 0,)]),
        ("pg_available_extensions", [(1 if available else 0,)]),
        ("e.extname = 'pg_stat_statements'", [(schema,)] if schema else []),
        ("current_setting('shared_preload_libraries')",
         preload if isinstance(preload, Exception) else [(preload,)]),
        ("SELECT 1 FROM", view_error if view_error else [(1,)]),
        ("rolsuper", [(superuser,)]),
        ("pg_has_role", [(member,)]),
        ("pg_stat_activity WHERE query", [(hidden,)]),
        ("pg_stat_statements WHERE query = %s", [(0,)]),
        ("SHOW track_activity_query_size", [(query_size,)]),
        ("pg_stat_statements.track", [(track,)]),
        ("sum(calls)", counter.read),
    ]


def pg_ctx(provider: str = "self_hosted", user: str = "rdst", database: str = "app") -> Context:
    return Context(engine="postgresql", provider=provider, user=user, database=database)


def run_pg(rules, ctx=None, counter=None):
    counter = counter or ProbeCounter()
    conn = ScriptedConnection(rules)
    return check_postgres(conn, ctx or pg_ctx(), counter.open_probe, sleep=lambda _s: None), counter


# PostgreSQL ----------------------------------------------------------------


def test_postgres_ready_when_everything_is_in_place_and_the_probe_is_seen():
    counter = ProbeCounter()
    checks, _ = run_pg(pg_rules(counter=counter), counter=counter)
    result = by_id(checks)
    assert [check.status for check in checks] == ["ok"] * 5
    assert result["visibility_probe"].detail.startswith("A test statement from a second session")
    assert counter.probes == [POSTGRES_PROBE_SQL]
    # The probe is tagged so monitoring excludes RDST's own statement.
    assert POSTGRES_PROBE_SQL.startswith("/*rdst:")


def test_postgres_missing_extension_waits_on_the_probe_and_gives_create_sql():
    checks, counter = run_pg(pg_rules(schema=None, preload=RuntimeError("permission denied"),
                                      superuser=False, member=True))
    result = by_id(checks)
    assert result["pg_stat_statements_preload"].status == "waiting"
    assert result["pg_stat_statements_preload"].fix is None
    extension = result["pg_stat_statements"]
    assert extension.status == "missing" and extension.blocking
    assert extension.fix.sql == ["CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"]
    assert result["visibility_probe"].status == "waiting"
    assert counter.probes == []


def test_postgres_not_preloaded_keeps_existing_libraries_in_the_fix():
    error = RuntimeError("pg_stat_statements must be loaded via shared_preload_libraries")
    checks, _ = run_pg(pg_rules(preload="auto_explain", view_error=error))
    preload = by_id(checks)["pg_stat_statements_preload"]
    assert preload.status == "missing"
    assert preload.fix.sql == [
        "ALTER SYSTEM SET shared_preload_libraries = 'auto_explain, pg_stat_statements';"
    ]
    assert preload.fix.restart is True
    assert by_id(checks)["visibility_probe"].status == "waiting"


def test_postgres_hidden_statements_need_the_grant_but_do_not_block():
    checks, _ = run_pg(pg_rules(superuser=False, member=False, hidden=3))
    grant = by_id(checks)["read_all_stats"]
    assert grant.status == "missing"
    assert grant.blocking is False and grant.recommended is False
    assert "3 statements from other users" in grant.detail
    assert grant.fix.sql == ["GRANT pg_read_all_stats TO rdst;"]


def test_postgres_no_other_users_yet_is_a_warning():
    checks, _ = run_pg(pg_rules(superuser=False, member=False, hidden=0))
    grant = by_id(checks)["read_all_stats"]
    assert grant.status == "warn" and grant.blocking is False


@pytest.mark.parametrize(
    "provider,expected_sql,who",
    [
        ("neon", "GRANT pg_monitor TO app_user;", "the role Neon created for you"),
        ("cloudsql", "GRANT pg_monitor TO app_user;", "the postgres user"),
        ("digitalocean", "GRANT pg_read_all_stats TO app_user;", "doadmin"),
        ("rds", "GRANT pg_read_all_stats TO app_user;", "the master user"),
    ],
)
def test_postgres_grant_fix_per_provider(provider, expected_sql, who):
    checks, _ = run_pg(pg_rules(superuser=False, member=False, hidden=1),
                       ctx=pg_ctx(provider, user="app_user"))
    fix = by_id(checks)["read_all_stats"].fix
    assert fix.sql == [expected_sql]
    assert who in fix.steps[0]


def test_postgres_quotes_unusual_user_names_in_the_grant():
    checks, _ = run_pg(pg_rules(superuser=False, member=False, hidden=1),
                       ctx=pg_ctx(user="App-User"))
    assert by_id(checks)["read_all_stats"].fix.sql == ['GRANT pg_read_all_stats TO "App-User";']


def test_postgres_create_fix_for_supabase_and_azure():
    checks, _ = run_pg(pg_rules(schema=None), ctx=pg_ctx("supabase"))
    supabase = by_id(checks)["pg_stat_statements"].fix
    assert supabase.sql == ["create extension if not exists pg_stat_statements with schema extensions;"]
    assert "Database > Extensions" in supabase.steps[0]
    assert supabase.docs_url.startswith("https://supabase.com/")

    checks, _ = run_pg(pg_rules(schema=None), ctx=pg_ctx("azure"))
    azure = by_id(checks)["pg_stat_statements"].fix
    assert any("azure.extensions" in step for step in azure.steps)
    assert azure.sql == ["CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"]


def test_postgres_short_query_text_is_a_recommendation_with_provider_steps():
    checks, _ = run_pg(pg_rules(query_size="1kB"))
    size = by_id(checks)["query_text_size"]
    assert size.status == "warn" and size.recommended and not size.blocking
    assert size.fix.sql == ["ALTER SYSTEM SET track_activity_query_size = 4096;"]
    # A recommendation that isn't met reads as what's wrong right now.
    assert size.as_dict()["problem"] == "Long queries aren't captured in full right now"

    checks, _ = run_pg(pg_rules(query_size="1kB"), ctx=pg_ctx("cloudsql"))
    cloudsql = by_id(checks)["query_text_size"].fix
    assert cloudsql.commands == [
        "gcloud sql instances patch <instance> --database-flags=track_activity_query_size=4096"
    ]
    assert cloudsql.restart is True


def test_postgres_probe_not_seen_with_tracking_off_names_the_setting():
    counter = ProbeCounter(visible=False)
    checks, _ = run_pg(pg_rules(track="none", counter=counter), counter=counter)
    probe = by_id(checks)["visibility_probe"]
    assert probe.status == "missing"
    assert "pg_stat_statements.track is none" in probe.detail
    assert probe.fix.sql[0] == "ALTER SYSTEM SET pg_stat_statements.track = 'top';"


# MySQL ---------------------------------------------------------------------


class Denied(Exception):
    pass


def mysql_rules(
    *,
    ps: str = "1",
    readable: Any = True,
    consumers: dict[str, str] | None = None,
    digest_length: int = 4096,
    variables: list[tuple[str, str]] | None = None,
    counter: ProbeCounter | None = None,
) -> list[tuple[str, Any]]:
    counter = counter or ProbeCounter()
    all_on = {name: "YES" for name in (
        "global_instrumentation", "thread_instrumentation", "statements_digest",
        "events_statements_current", "events_statements_history",
        "events_statements_history_long")}
    return [
        ("SHOW GLOBAL VARIABLES", variables or []),
        ("mysql.rds_version", Denied("no such function")),
        ("@@GLOBAL.performance_schema_max_digest_length", [(digest_length,)]),
        ("@@GLOBAL.performance_schema", [(ps,)]),
        ("SUM(COUNT_STAR)", counter.read),
        ("SELECT 1 FROM performance_schema.events_statements_summary_by_digest",
         [(1,)] if readable is True else readable),
        ("setup_consumers", list((consumers or all_on).items())),
    ]


def run_mysql(rules, provider="self_hosted", counter=None):
    counter = counter or ProbeCounter()
    ctx = Context(engine="mysql", provider=provider, user="rdst", database="app")
    conn = ScriptedConnection(rules)
    return check_mysql(conn, ctx, counter.open_probe, sleep=lambda _s: None), counter


def test_mysql_ready_when_digests_are_collected_and_the_probe_is_seen():
    counter = ProbeCounter()
    checks, _ = run_mysql(mysql_rules(counter=counter), counter=counter)
    assert {check.id: check.status for check in checks} == {
        "performance_schema": "ok",
        "read_all_stats": "ok",
        "statement_consumers": "ok",
        "statement_history": "ok",
        "query_text_size": "ok",
        "visibility_probe": "ok",
    }
    assert counter.probes == [MYSQL_PROBE_SQL]


def test_mysql_performance_schema_off_waits_on_everything_else():
    checks, counter = run_mysql(mysql_rules(ps="0"), provider="rds")
    result = by_id(checks)
    assert result["performance_schema"].status == "missing"
    assert any("performance_schema to 1" in step for step in result["performance_schema"].fix.steps)
    assert result["read_all_stats"].status == "waiting"
    assert result["visibility_probe"].status == "waiting"
    assert counter.probes == []


def test_mysql_denied_select_asks_for_the_grant():
    denied = Denied(1142, "SELECT command denied to user 'rdst'@'%'")
    checks, _ = run_mysql(mysql_rules(readable=denied))
    grant = by_id(checks)["read_all_stats"]
    assert grant.status == "missing"
    assert grant.fix.sql == ["GRANT SELECT ON performance_schema.* TO 'rdst'@'%';"]
    assert by_id(checks)["statement_consumers"].status == "waiting"


def test_mysql_consumers_off_give_runtime_sql_and_the_persistent_setting():
    consumers = {"global_instrumentation": "YES", "statements_digest": "NO",
                 "events_statements_history_long": "NO"}
    checks, counter = run_mysql(mysql_rules(consumers=consumers), provider="rds")
    result = by_id(checks)
    digest = result["statement_consumers"]
    assert digest.status == "missing"
    assert digest.fix.sql == [
        "UPDATE performance_schema.setup_consumers SET ENABLED = 'YES' "
        "WHERE NAME IN ('statements_digest');"
    ]
    assert any("performance_schema_consumer_statements_digest" in step for step in digest.fix.after)
    history = result["statement_history"]
    assert history.status == "warn" and history.recommended
    assert result["visibility_probe"].status == "waiting"
    assert counter.probes == []


def test_mysql_self_hosted_consumer_fix_lists_my_cnf_options():
    checks, _ = run_mysql(mysql_rules(consumers={"global_instrumentation": "YES",
                                                 "statements_digest": "NO"}))
    after = by_id(checks)["statement_consumers"].fix.after
    assert "performance-schema-consumer-statements-digest=ON" in after


def test_mysql_probe_not_seen_points_at_instruments():
    counter = ProbeCounter(visible=False)
    checks, _ = run_mysql(mysql_rules(counter=counter), counter=counter)
    probe = by_id(checks)["visibility_probe"]
    assert probe.status == "missing"
    assert "setup_instruments" in probe.fix.sql[0]


# Provider detection --------------------------------------------------------


@pytest.mark.parametrize(
    "host,expected",
    [
        ("db-1.abc.us-east-1.rds.amazonaws.com", "rds"),
        ("app.cluster-abc.us-east-1.rds.amazonaws.com", "aurora"),
        ("db.abcd.supabase.co", "supabase"),
        ("aws-0-us-east-1.pooler.supabase.com", "supabase"),
        ("ep-cool-1.us-east-2.aws.neon.tech", "neon"),
        ("db-1-do-user-1.b.db.ondigitalocean.com", "digitalocean"),
        ("server.postgres.database.azure.com", "azure"),
        ("10.0.0.5", "self_hosted"),
    ],
)
def test_detect_provider_from_host(host, expected):
    conn = ScriptedConnection(pg_rules())
    assert detect_provider(conn, "postgresql", {"host": host}) == expected


def test_detect_provider_from_postgres_roles():
    assert detect_provider(ScriptedConnection(pg_rules(roles=["rds_superuser"], aurora=True)),
                           "postgresql", {"host": "db.internal"}) == "aurora"
    assert detect_provider(ScriptedConnection(pg_rules(roles=["rds_superuser"])),
                           "postgresql", {"host": "db.internal"}) == "rds"
    assert detect_provider(ScriptedConnection(pg_rules(roles=["cloudsqlsuperuser"])),
                           "postgresql", {"host": "10.1.2.3"}) == "cloudsql"
    # A provider tag from discovery counts when the host says nothing.
    assert detect_provider(ScriptedConnection(pg_rules()), "postgresql",
                           {"host": "10.1.2.3", "tags": ["provider:neon"]}) == "neon"


def test_detect_provider_from_mysql_variables():
    rules = lambda variables: mysql_rules(variables=variables)  # noqa: E731
    assert detect_provider(ScriptedConnection(rules([("aurora_version", "3.05")])),
                           "mysql", {"host": "db"}) == "aurora"
    assert detect_provider(ScriptedConnection(rules([("basedir", "/rdsdbbin/mysql/")])),
                           "mysql", {"host": "db"}) == "rds"
    assert detect_provider(ScriptedConnection(rules([("cloudsql_iam_authentication", "OFF")])),
                           "mysql", {"host": "10.0.0.1"}) == "cloudsql"
    assert detect_provider(ScriptedConnection(rules([("azure_server_name", "x")])),
                           "mysql", {"host": "10.0.0.1"}) == "azure"
    assert detect_provider(ScriptedConnection(rules([])), "mysql", {"host": "10.0.0.1"}) == "self_hosted"


# check_target --------------------------------------------------------------


def test_check_target_reports_connection_errors_without_secrets():
    def connect(*_args, **_kwargs):
        raise RuntimeError("could not connect: host=db password=hunter2 timeout")

    result = check_target("orders", {"engine": "postgresql", "host": "db"}, connect=connect)
    assert result["ready"] is False
    assert result["checks"] == []
    assert "hunter2" not in result["connection_error"]
    assert "password=***" in result["connection_error"]


def test_check_target_runs_postgres_checks_and_summarizes():
    counter = ProbeCounter()
    opened: list[ScriptedConnection] = []

    def connect(config, **kwargs: Any):
        assert kwargs["lane"] == "rdst/prereq"
        conn = ScriptedConnection(pg_rules(counter=counter, query_size="1kB")) if not opened \
            else counter.open_probe()
        opened.append(conn)
        return conn

    result = check_target("orders", {"engine": "postgres", "host": "10.0.0.1",
                                     "database": "app", "user": "rdst"}, connect=connect,
                          clock=lambda: 1000.0)
    assert result["engine"] == "postgresql"
    assert result["provider"] == "self_hosted"
    assert result["provider_name"] == "Self-hosted PostgreSQL"
    assert result["ready"] is True
    assert result["blocking"] == 0 and result["warnings"] == 1 and result["attention"] == 0
    assert result["checked_at"] == 1000
    assert result["database"] == "app"
    size = next(check for check in result["checks"] if check["id"] == "query_text_size")
    assert size["recommended"] is True
    assert size["problem"] == "Long queries aren't captured in full right now"
    assert "ALTER SYSTEM SET track_activity_query_size" in size["remediation"]
    assert all(conn.closed for conn in opened)


def test_check_target_rejects_unknown_engines():
    result = check_target("x", {"engine": "sqlite"}, connect=lambda *a, **k: None)
    assert result["connection_error"] == "Unsupported engine: sqlite"


# API route -----------------------------------------------------------------


def test_prerequisites_route_checks_the_saved_target(monkeypatch):
    import asyncio

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    import features.configure.prerequisites as prerequisites
    import shared.api.target_guard as target_guard
    from features.configure.api import routes
    from shared.api.target_guard import TargetGuard

    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        target_guard, "ensure_target_password",
        lambda name: TargetGuard(name, {"engine": "postgresql", "host": "db"}, "postgresql"),
    )

    def fake_check(name, config):
        seen["args"] = (name, config)
        return {"target": name, "ready": True, "checks": []}

    monkeypatch.setattr(prerequisites, "check_target", fake_check)
    monkeypatch.setattr(routes, "require_local_request", lambda _request: None)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")

    async def request():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/api/configure/targets/orders/prerequisites")

    response = asyncio.run(request())
    assert response.status_code == 200
    assert response.json() == {"target": "orders", "ready": True, "checks": []}
    assert seen["args"] == ("orders", {"engine": "postgresql", "host": "db"})


# CLI flow ------------------------------------------------------------------


class RecordingConsole:
    def __init__(self):
        self.printed: list[Any] = []

    def print(self, *items: Any) -> None:
        self.printed.extend(items)

    def text(self) -> str:
        from io import StringIO

        from rich.console import Console

        buffer = StringIO()
        console = Console(file=buffer, width=120, color_system=None)
        for item in self.printed:
            console.print(item)
        return buffer.getvalue()


def cli_result(name: str, *, ready: bool, attention: int = 0) -> dict[str, Any]:
    fix = {"summary": f"Create it in {name}.", "sql": ["CREATE EXTENSION pg_stat_statements;"],
           "steps": [], "commands": [], "after": [], "restart": False, "note": None,
           "docs_url": None}
    return {
        "target": name, "engine": "postgresql", "provider": "rds", "provider_name": "Amazon RDS",
        "ready": ready, "blocking": 0 if ready else 1, "attention": attention, "warnings": 0,
        "connection_error": None,
        "checks": [{"id": "pg_stat_statements", "label": "pg_stat_statements is created",
                    "status": "ok" if ready else "missing", "detail": "", "blocking": True,
                    "fix": None if ready else fix}],
    }


def test_cli_flow_rechecks_until_green_then_prints_ready_line():
    from features.configure.cli.prerequisites_renderer import run_prerequisite_flow

    answers = iter([False, True])
    calls: list[str] = []

    def check(name, _config):
        calls.append(name)
        return cli_result(name, ready=next(answers))

    console = RecordingConsole()
    asked: list[int] = []
    results = run_prerequisite_flow(console, [("orders", {})], interactive=True, check=check,
                                    ask=lambda: asked.append(1) or True)
    assert calls == ["orders", "orders"]
    assert len(asked) == 1
    assert results[0]["ready"] is True
    output = console.text()
    assert "CREATE EXTENSION pg_stat_statements;" in output
    assert "Ready to monitor: orders is set up" in output


def test_cli_flow_skip_and_non_interactive_never_block():
    from features.configure.cli.prerequisites_renderer import run_prerequisite_flow

    def check(name, _config):
        return cli_result(name, ready=False)

    def never_ask():
        raise AssertionError("prompted")

    console = RecordingConsole()
    run_prerequisite_flow(console, [("orders", {})], interactive=False, check=check, ask=never_ask)
    assert "Ready to monitor" not in console.text()

    skipped = RecordingConsole()
    run_prerequisite_flow(skipped, [("orders", {})], interactive=True, check=check,
                          ask=lambda: False)
    assert "Monitoring setup for orders" in skipped.text()

    quiet = RecordingConsole()
    results = run_prerequisite_flow(quiet, [("orders", {})], json_mode=True, interactive=True,
                                    check=check, ask=never_ask)
    assert quiet.printed == [] and results[0]["ready"] is False


def test_cli_flow_fleet_lists_one_line_per_target_and_details_only_failures():
    from features.configure.cli.prerequisites_renderer import (
        prerequisites_data,
        run_prerequisite_flow,
    )

    state = {"orders": False, "users": True}
    rechecked: list[str] = []

    def check(name, _config):
        if name == "orders" and rechecked:
            state["orders"] = True
        rechecked.append(name)
        return cli_result(name, ready=state[name])

    console = RecordingConsole()
    results = run_prerequisite_flow(console, [("orders", {}), ("users", {})], interactive=True,
                                    check=check, ask=lambda: True)
    output = console.text()
    assert "Monitoring setup: 1 of 2 ready" in output
    assert output.count("Monitoring setup for orders") == 1
    assert "Monitoring setup for users" not in output
    # Only the failing target is checked again.
    assert rechecked == ["orders", "users", "orders"]
    assert "Ready to monitor: All 2 targets are set up" in output
    assert prerequisites_data(results)["ready"] is True


def test_fleet_import_checks_prerequisites_for_imported_targets(monkeypatch, tmp_path):
    import argparse

    from features.fleet.cli import command as fleet_command
    from features.configure.cli import prerequisites_renderer

    class Event:
        type = "import_complete"
        imported, skipped, errors = 2, 0, 0
        target_names = ["a", "b"]

    class Service:
        async def import_fleet(self, **_kwargs):
            yield Event()

    class Config:
        def load(self):
            pass

        def get(self, name):
            return {"engine": "postgresql", "host": name}

    seen: dict[str, Any] = {}

    def flow(_console, targets, json_mode=False, **_kwargs):
        seen["targets"] = [name for name, _ in targets]
        return [cli_result(name, ready=True) for name, _ in targets]

    monkeypatch.setattr(fleet_command, "FleetService", Service)
    monkeypatch.setattr(fleet_command, "TargetsConfig", Config)
    monkeypatch.setattr(prerequisites_renderer, "run_prerequisite_flow", flow)
    args = argparse.Namespace(csv_file="fleet.csv", password_env=None, group=None, tags=None,
                              dry_run=False)
    result = fleet_command.FleetCommand()._handle_import(args)
    assert seen["targets"] == ["a", "b"]
    assert result.data["prerequisites"]["ready"] is True


# Monitor: naming the failed check ------------------------------------------


def test_monitor_names_the_failed_setup_check_once_per_outage(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from features.monitor import loop as monitor_loop
    from features.monitor.presentation import inbox_advice, inbox_link, inbox_summary
    from shared.monitor.monitor_store import MonitorStore

    class Config:
        def load(self):
            pass

        def get(self, _target):
            return {"engine": "postgresql", "host": "db"}

    monkeypatch.setattr("shared.config.targets.TargetsConfig", Config)
    collector = SimpleNamespace(_snapshot={"capture_warning": {"source": "pg_stat_statements"}})
    monkeypatch.setattr(monitor_loop.query_discovery, "collector_for", lambda _t: collector)
    checks: list[str] = []

    def checker(target, _config):
        checks.append(target)
        return {"checks": [
            {"id": "pg_stat_statements_preload", "status": "ok", "blocking": True},
            {"id": "visibility_probe", "label": "RDST sees new statements as they run",
             "status": "missing", "blocking": True,
             "detail": "pg_stat_statements.track is none, so no statements are recorded.",
             "fix": {"summary": "Turn statement tracking back on.", "steps": [],
                     "sql": ["ALTER SYSTEM SET pg_stat_statements.track = 'top';"],
                     "commands": [], "after": []}},
        ]}

    store = MonitorStore(tmp_path / "monitor.db")
    active = monitor_loop.MonitorLoop(store, clock=lambda: 1000, index_reader=lambda _t: [],
                                      judge=lambda *_a: None, setup_checker=checker)
    active._monitoring_issues("db", 1000, False)
    active._monitoring_issues("db", 1060, False)
    assert checks == ["db"]
    item = next(i for i in store.inbox("new") if i["subject"] == "pg_stat_statements_missing")
    body = item["body"] if "body" in item else json.loads(item["body_json"])
    assert body["failed_check"]["id"] == "visibility_probe"
    assert inbox_summary(item, body, {}).startswith(
        "Setup check failed: RDST sees new statements as they run.")
    assert inbox_link(item, body, [])["kind"] == "setup"
    advice = inbox_advice(item, body, "postgresql", {}, [])
    assert advice["summary"] == "Turn statement tracking back on."
    assert "Run: ALTER SYSTEM SET pg_stat_statements.track = 'top';" in advice["steps"]

    collector._snapshot = {}
    active._monitoring_issues("db", 1120, False)
    collector._snapshot = {"capture_warning": {"source": "pg_stat_statements"}}
    active._monitoring_issues("db", 1180, False)
    assert checks == ["db", "db"]

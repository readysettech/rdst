"""Continuous-analysis, desktop and app_error telemetry: safe properties, dedupe and gating."""

from __future__ import annotations

import json
import logging
import re

import pytest

from features.monitor import telemetry as monitor_telemetry
from features.monitor.connection import ConnectionWatch
from shared import product_telemetry
from shared.api.routes import telemetry as telemetry_route
from shared.monitor.monitor_store import MonitorStore

TARGET = "secret-orders-db"
HOST = "orders.prod.internal.example.com"
SQL = "SELECT card_number FROM payments WHERE email = 'a@b.com'"
T0 = 1_790_762_100


class FakeTelemetry:
    POSTHOG_API_KEY = "phc_test"
    device_id = "device-1"

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.stats: dict = {}
        self.analyzes: list[dict] = []

    def is_enabled(self) -> bool:
        return True

    def track(self, event: str, properties: dict | None = None) -> None:
        self.events.append((event, dict(properties or {})))

    def _get_auth_type(self) -> str:
        return "none"

    def _get_stats(self) -> dict:
        return self.stats

    def track_installation(self, install_method: str = "unknown") -> None:
        self.stats["desktop_installations"] = 1
        self.track("installation", {"install_method": install_method})

    def track_analyze(self, **kwargs) -> None:
        self.analyzes.append(kwargs)

    def named(self, event: str) -> list[dict]:
        return [props for name, props in self.events if name == event]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    telemetry = FakeTelemetry()
    monkeypatch.setattr(product_telemetry, "_telemetry", lambda: telemetry)
    monkeypatch.setattr(product_telemetry, "own_key", lambda: False)
    monkeypatch.setattr(product_telemetry, "engine_of", lambda target: "postgresql")
    monkeypatch.setattr(product_telemetry, "_gate", product_telemetry._ErrorGate())
    product_telemetry._ids.clear()
    return telemetry


def assert_private(telemetry: FakeTelemetry) -> None:
    """No event carries a target name, host, SQL or email."""
    for _event, props in telemetry.events:
        text = json.dumps(props)
        for secret in (TARGET, HOST, "card_number", "a@b.com", "10.1.2.3"):
            assert secret not in text, (secret, props)


def test_scrub_removes_sql_hosts_addresses_paths_and_quoted_values():
    text = product_telemetry.scrub(
        f'could not connect to "{HOST}" (10.1.2.3) port=5432 near {SQL} '
        "reading /home/me/.rdst/monitor.db")
    for secret in (HOST, "10.1.2.3", "card_number", "a@b.com", "/home/me", "5432"):
        assert secret not in text
    assert product_telemetry.scrub("relation \"orders\" does not exist") == \
        "relation [value] does not exist"
    assert product_telemetry.scrub("x" * 500).endswith("...")


def test_safe_properties_drop_identifying_keys_and_keep_versions():
    props = product_telemetry.safe_properties({
        "target": TARGET, "host": HOST, "sql": SQL, "email": "a@b.com", "count": 3,
        "os_version": "6.6.87.2-microsoft", "note": f"at {HOST}"})
    assert props == {"count": 3, "os_version": "6.6.87.2-microsoft", "note": "at [host]"}


def test_database_id_is_salted_short_and_never_the_name(fake):
    config = {"engine": "postgresql", "host": HOST, "port": 5432, "database": "orders"}
    first = product_telemetry.database_id(config=config)
    assert re.fullmatch(r"[0-9a-f]{16}", first)
    fake.device_id = "device-2"
    assert product_telemetry.database_id(config=config) != first
    assert re.fullmatch(r"[0-9a-f]{16}", product_telemetry.database_id(TARGET))


def test_events_go_through_the_telemetry_gate(monkeypatch, tmp_path):
    from shared.telemetry_manager import TelemetryManager

    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    sent: list[str] = []
    for env, expected in (({"RDST_TELEMETRY": "0"}, False), ({"RDST_TESTING": "true"}, False),
                          ({}, True)):
        for name in ("RDST_TELEMETRY", "RDST_TESTING"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        manager = TelemetryManager()
        monkeypatch.setattr(manager, "track", lambda event, props=None: sent.append(event))
        monkeypatch.setattr(product_telemetry, "_telemetry", lambda: manager)
        sent.clear()
        product_telemetry.capture("monitoring_disabled", {"engine": "mysql"})
        product_telemetry.report_error("monitor_loop", RuntimeError("boom"))
        assert bool(sent) is expected, env


def test_capture_adds_surface_properties(fake, monkeypatch):
    monkeypatch.setenv("RDST_DESKTOP", "1")
    monkeypatch.setenv("RDST_DESKTOP_VERSION", "1.2.3")
    product_telemetry.capture("monitoring_disabled", {"engine": "mysql"})
    props = fake.named("monitoring_disabled")[0]
    assert props["surface"] == "desktop" and props["is_desktop"] is True
    assert props["desktop_version"] == "1.2.3" and props["app_version"]


def test_app_errors_are_scrubbed_rate_limited_and_deduped(fake):
    for _ in range(3):
        product_telemetry.report_error("collector", ConnectionError(f"can't reach {HOST}"))
    errors = fake.named("app_error")
    assert len(errors) == 1
    assert errors[0]["component"] == "collector"
    assert errors[0]["error_class"] == "ConnectionError"
    assert errors[0]["message"] == "can't reach [host]"
    for index in range(10):
        product_telemetry.report_error("collector", ValueError(f"distinct {index}"))
    assert len(fake.named("app_error")) == product_telemetry.APP_ERROR_PER_MINUTE
    assert_private(fake)


def test_error_gate_reports_suppressed_repeats_after_the_window():
    gate = product_telemetry._ErrorGate(per_minute=5, dedupe_s=3600)
    assert gate.admit(("a",), now=0) == 0
    assert gate.admit(("a",), now=10) is None
    assert gate.admit(("a",), now=3601) == 1


def test_logged_errors_report_the_format_string_not_the_arguments(fake):
    handler = product_telemetry.AppErrorLogHandler()
    log = logging.getLogger("features.monitor.analyze_queue")
    log.addHandler(handler)
    try:
        log.info("Analyzed %s", TARGET)
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            log.warning("Monitor cycle failed for %s on %s", TARGET, HOST, exc_info=True)
        log.error("Report for %s failed: %s", TARGET, SQL)
    finally:
        log.removeHandler(handler)
    errors = fake.named("app_error")
    assert [error["component"] for error in errors] == ["analyze_queue", "analyze_queue"]
    assert errors[0]["error_class"] == "RuntimeError"
    assert errors[0]["message"] == "Monitor cycle failed for %s on %s: boom"
    assert errors[1]["error_class"] == "LoggedError"
    assert_private(fake)


def test_quota_refusals_send_one_limit_event_a_day(fake, monkeypatch):
    from shared import account_session

    monkeypatch.setattr(account_session, "account_metadata",
                        lambda: {"user_id": "usr_42", "email": "a@b.com"})
    product_telemetry.refusal("HOSTED_CAP_REACHED", feature="hosted")
    product_telemetry.refusal("HOSTED_CAP_REACHED", feature="hosted")
    product_telemetry.refusal("HOSTED_GLOBAL_CAP_REACHED", feature="jev")
    product_telemetry.refusal("LOGIN_REQUIRED", feature="jev")
    # Database limit refusals are reported by database_limit.limit_reached instead.
    product_telemetry.refusal("MONITOR_DATABASE_LIMIT", feature="hosted")
    events = fake.named("monitoring_limit_reached")
    assert [event["reason"] for event in events] == ["monthly_quota", "global_cap"]
    assert events[0]["account_user_id"] == "usr_42"
    assert events[0]["admin_user_url"].endswith("/admin#/users/usr_42")
    assert events[0]["admin_base_url"].endswith("/admin")
    assert events[0]["account_kind"] in ("signed_in", "device_only")
    assert "email" not in events[0]


def test_database_limit_reached_names_the_database_by_id(fake, monkeypatch):
    from features.monitor import database_limit

    monkeypatch.setattr(product_telemetry, "_account_admin", lambda: {})
    database_limit.limit_reached(TARGET, where="enable", active=3, limit=3)
    event = fake.named("monitoring_limit_reached")[0]
    assert event["reason"] == "database_limit" and event["feature"] == "enable"
    assert event["monitored_count"] == 3 and event["limit"] == 3
    assert re.fullmatch(r"[0-9a-f]{16}", event["database_id"])
    assert_private(fake)


def test_monitoring_enabled_marks_the_first_for_the_account(fake):
    product_telemetry.monitoring_enabled(TARGET, monitored_count=1, ai_monitoring="on")
    product_telemetry.monitoring_enabled("other", monitored_count=2, ai_monitoring="local_only")
    first, second = fake.named("monitoring_enabled")
    assert first["first_for_account"] is True and second["first_for_account"] is False
    assert second["ai_monitoring"] == "local_only" and first["own_key"] is False
    assert first["engine"] == "postgresql"
    assert_private(fake)


@pytest.mark.parametrize("with_history", [False, True])
def test_inbox_filing_sends_item_events_and_one_first_inbox_item(fake, tmp_path, with_history):
    store = MonitorStore(tmp_path / "monitor.db")
    if with_history:
        store.start_first_hour(TARGET, T0 - 86400)
        store.record(TARGET, T0 - 3600, [], {}, [], {"calls": 1, "total_ms": 10})
        store.set_settings(TARGET, {"enabled": False})
    store.set_settings(TARGET, {"enabled": True})
    store.start_first_hour(TARGET, T0 - 600)
    assert bool(store.first_hour(TARGET)[0]) is not with_history
    store.upsert_item(TARGET, "query_slower", "abc123def456", 0.9, f"Slower: {SQL}",
                      {"sql": SQL}, T0)
    store.upsert_item(TARGET, "query_slower", "abc123def456", 0.9, "Slower", {"n": 2}, T0 + 60)
    store.upsert_item(TARGET, "stuck_query", "fedcba987654", 0.7, "Stuck", {}, T0 + 120)
    filed = fake.named("inbox_item_filed")
    assert [(item["kind"], item["band"], item["change"]) for item in filed] == [
        ("query_slower", "act_now", "created"), ("stuck_query", "act_now", "created")]
    first = fake.named("first_inbox_item")
    assert len(first) == 1 and first[0]["minutes_since_enable"] == 10
    assert_private(fake)


def test_connection_lost_and_restored_carry_the_downtime(fake, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings(TARGET, {"enabled": True})
    watch = ConnectionWatch(store, clock=lambda: T0)
    watch.collection(TARGET, False, T0, f"could not connect to {HOST}")
    watch.collection(TARGET, False, T0 + 60, f"could not connect to {HOST}")
    watch.collection(TARGET, False, T0 + 120, f"could not connect to {HOST}")
    watch.collection(TARGET, True, T0 + 600)
    assert len(fake.named("connection_lost")) == 1
    restored = fake.named("connection_restored")
    assert len(restored) == 1 and restored[0]["downtime_s"] == 600
    assert_private(fake)


@pytest.mark.parametrize("with_history", [False, True])
def test_daily_summary_counts_a_day_once_per_database(fake, tmp_path, with_history):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings(TARGET, {"enabled": True})
    if with_history:
        store.start_first_hour(TARGET, T0 - 86400)
        store.record(TARGET, T0 - 3600, [], {}, [], {"calls": 1, "total_ms": 10})
        store.set_settings(TARGET, {"enabled": False})
        store.set_settings(TARGET, {"enabled": True})
    store.start_first_hour(TARGET, T0)
    assert bool(store.first_hour(TARGET)[0]) is not with_history
    assert monitor_telemetry.send_due(store, now=T0) == 0
    day = T0 + 86_400
    store.record(TARGET, day - 3600, [{"query_key": "k1", "text": SQL, "calls": 5,
                                       "total_ms": 50.0, "rows": 5, "blks_hit": 0,
                                       "blks_read": 0}],
                 {"k1": SQL}, [], {"calls": 5, "total_ms": 50.0})
    store.upsert_item(TARGET, "query_slower", "k1", 0.7, "Slower", {}, day - 60)
    fake.events.clear()
    assert monitor_telemetry.send_due(store, now=day) == 1
    assert monitor_telemetry.send_due(store, now=day + 3600) == 0
    summary = fake.named("monitoring_daily_summary")[0]
    assert summary["queries_seen"] >= 1 and summary["new_queries"] >= 1
    assert summary["inbox_items"] == 1 and summary["days_monitored"] == 1.0
    for key in ("analyses_done", "ai_calls", "ai_cost_usd", "collection_errors", "uptime_pct"):
        assert key in summary
    assert_private(fake)


def test_background_analysis_counts_as_an_analyze_run_and_completes(fake):
    job = {"target": TARGET, "query_key": "abc123", "reason": "new"}
    monitor_telemetry.analysis_finished(
        job, {"state": "failed", "error": f"LOGIN_REQUIRED: at {HOST}", "mode": "explain"}, 2.5)
    monitor_telemetry.analysis_finished(job, {"state": "skipped"}, 0.1)
    event = fake.named("deep_analysis_completed")
    assert len(event) == 1
    assert event[0]["ok"] is False and event[0]["error_class"] == "LOGIN_REQUIRED"
    assert event[0]["duration_s"] == 2.5 and event[0]["trigger"] == "new"
    assert fake.analyzes[0]["source"] == "monitor" and fake.analyzes[0]["success"] is False
    assert_private(fake)


def test_report_generated_names_the_trigger_and_outcome(fake):
    product_telemetry._report_generated(TARGET, "nightly", False, 12.0, None, "RuntimeError", False)
    event = fake.named("report_generated")[0]
    assert event["trigger"] == "nightly" and event["ok"] is False
    assert event["error_class"] == "RuntimeError"
    assert_private(fake)


def test_desktop_events_are_allowlisted(fake):
    request = telemetry_route.ClientEventsRequest(events=[
        {"event": "tray_quit", "properties": {"uptime_hours": 3.5, "target": TARGET}},
        {"event": "drop_database", "properties": {}},
        {"event": "app_error", "properties": {"component": "main", "error_class": "TypeError",
                                              "message": f"bad {HOST}", "handled": False}},
        {"event": "desktop_installed", "properties": {"os": "win32", "install_kind": "fresh"}},
        {"event": "desktop_installed", "properties": {"os": "win32", "install_kind": "fresh"}},
    ])
    accepted, rejected = telemetry_route._send("desktop", request.events)
    assert accepted == 4 and rejected == ["drop_database"]
    assert fake.named("tray_quit") == [{"uptime_hours": 3.5, "source": "desktop",
                                        **product_telemetry.surface_properties()}]
    assert fake.named("app_error")[0]["message"] == "bad [host]"
    assert fake.named("app_error")[0]["source"] == "desktop"
    # The desktop's first run also sends the existing installation event, once.
    assert [props["install_method"] for props in fake.named("installation")] == ["desktop"]
    assert len(fake.named("desktop_installed")) == 2
    assert_private(fake)


def test_installed_capture_reports_each_logged_error_once(fake, monkeypatch):
    monkeypatch.setattr(product_telemetry, "_installed", False)
    added: list[str] = []
    real_get_logger = logging.getLogger
    monkeypatch.setattr(product_telemetry.logging, "getLogger",
                        lambda name=None: added.append(name) or real_get_logger(name))
    import sys
    import threading

    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    product_telemetry.install_error_capture()
    try:
        assert "features.monitor" in added and "features.monitor.report" not in added
        logging.getLogger("features.monitor.report").error("Report failed for %s", TARGET)
        assert [e["component"] for e in fake.named("app_error")] == ["report"]
        assert fake.named("app_error")[0]["suppressed_repeats"] == 0
    finally:
        for name in added:
            real_get_logger(name).removeHandler(product_telemetry._handler)


def test_person_identity_is_shared_by_events_and_daily_summaries(monkeypatch, tmp_path):
    from shared import account_session, telemetry_manager

    metadata = {}
    monkeypatch.setattr(account_session, "is_signed_in_locally", lambda: bool(metadata))
    monkeypatch.setattr(account_session, "account_metadata", lambda: metadata)
    events = []

    class Posthog:
        def capture(self, **kwargs):
            events.append(kwargs)

        def identify(self, distinct_id, properties):
            events.append({"distinct_id": distinct_id, "event": "$identify",
                           "properties": {"$set": properties}})

    monkeypatch.setattr(telemetry_manager, "_get_posthog", lambda: Posthog())
    manager = telemetry_manager.TelemetryManager()
    manager._enabled = True
    manager._initialized = True
    manager._device_id = "install-1"
    manager.POSTHOG_API_KEY = "preview-key"
    monkeypatch.setattr(manager, "_enqueue_posthog_send", lambda send: send(True))
    monkeypatch.setattr(product_telemetry, "_telemetry", lambda: manager)
    product_telemetry.capture("desktop_installed")
    assert events[-1]["distinct_id"] == "install-1"
    metadata.update(user_id="usr_42", email="person@example.com")
    product_telemetry.capture("monitoring_enabled")
    product_telemetry.capture("monitoring_daily_summary", {"database_id": "0123456789abcdef"})
    product_telemetry.report_error("api", ValueError("bad request"))
    signed_in = events[1:]
    assert all(event["distinct_id"] == "usr_42" for event in signed_in)
    identify = signed_in[0]
    assert identify["event"] == "$identify"
    assert identify["properties"]["$anon_distinct_id"] == "install-1"
    assert identify["properties"]["$set"] == {"email": "person@example.com", "account_kind": "signed_in"}
    assert all("person@example.com" not in json.dumps(event) for event in signed_in[1:])
    metadata.clear()
    product_telemetry.capture("monitoring_disabled")
    assert events[-1]["distinct_id"] == "install-1"
    assert events[-1]["properties"]["account_kind"] == "device_only"


def test_stale_account_metadata_keeps_install_identity(monkeypatch):
    from shared import account_session

    monkeypatch.setattr(account_session, "is_signed_in_locally", lambda: False)
    monkeypatch.setattr(account_session, "account_metadata",
                        lambda: {"user_id": "stale", "email": "old@example.com"})
    assert product_telemetry.identity("install-1") == {
        "distinct_id": "install-1", "device_id": "install-1", "account_kind": "device_only", "email": ""}


@pytest.mark.asyncio
async def test_local_identity_route_respects_opt_out_and_reports_internal_users(fake, monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request

    request = Request({"type": "http", "method": "GET", "path": "/api/telemetry/identity",
                       "headers": [], "client": ("127.0.0.1", 1234), "server": ("localhost", 80),
                       "scheme": "http"})
    monkeypatch.setattr(fake, "_detect_internal_user", lambda: True, raising=False)
    monkeypatch.setattr(product_telemetry, "identity", lambda device_id: {
        "distinct_id": "usr_42", "device_id": device_id, "account_kind": "signed_in", "email": "person@example.com"})
    result = await telemetry_route.get_telemetry_identity(request)
    assert result["distinct_id"] == "usr_42" and result["internal_user"] is True
    monkeypatch.setattr(product_telemetry, "enabled", lambda: False)
    assert await telemetry_route.get_telemetry_identity(request) == {"telemetry_enabled": False}
    remote = Request({**request.scope, "client": ("203.0.113.1", 1234)})
    with pytest.raises(HTTPException):
        await telemetry_route.get_telemetry_identity(remote)

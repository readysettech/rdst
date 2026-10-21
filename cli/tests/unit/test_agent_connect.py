"""Agent connect: the local-only /mcp endpoint, the prompt and its attempt, the progress feed,
the discovery file, and the local-only guard on monitor writes."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from features.agent_connect import discovery, session, tools
from features.agent_connect.prompt import render_prompt
from features.configure.events import ConfigureConnectionTestEvent

LOOPBACK = ("127.0.0.1", 50000)
BASE = "http://127.0.0.1:8787"
ACCEPT = "application/json, text/event-stream"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def app(data_dir, monkeypatch):
    from features.account.api import routes as account
    from features.monitor import database_limit
    from features.monitor.api import routes as monitor
    from shared.api.app import create_app

    async def account_status(request):
        return {"signed_in": True, "detail": None}

    async def stop():
        return None

    monkeypatch.setattr(account, "account_status", account_status)
    monkeypatch.setattr(tools, "_signed_in", lambda: True)
    monkeypatch.setattr(database_limit, "claim_target", lambda *args, **kwargs: "local")
    monkeypatch.setattr(monitor, "_release_monitoring_slot", lambda target: None)
    application = create_app()
    application.state.analyze_queue = SimpleNamespace(wake=lambda: None, stop=stop)
    return application


def _prereq_result(engine="postgresql", missing=()):
    """A prerequisite result as check_target returns it, with ``missing`` checks failing."""
    from features.configure import prerequisites as prereq
    from features.configure.prerequisite_fixes import provider_name

    ctx = prereq.Context(engine=engine, provider="self_hosted", user="app", database="shop")
    ids = (("pg_stat_statements_preload", "pg_stat_statements", "visibility_probe")
           if engine == "postgresql" else
           ("performance_schema", "read_all_stats", "statement_consumers", "visibility_probe"))
    checks = []
    for check_id in ids:
        if check_id in missing:
            status = prereq.MISSING
        elif missing and check_id == "visibility_probe":
            status = prereq.WAITING
        else:
            status = prereq.OK
        checks.append(prereq.Check(
            check_id, f"{check_id} label", status, f"{check_id} is {status}.",
            fix=prereq.fix_for(check_id, ctx) if status != prereq.OK else None))
    return {"target": "shop", "engine": engine, "database": "shop", "checked_at": 1,
            "provider": "self_hosted", "provider_name": provider_name("self_hosted", engine),
            **prereq.summarize(checks), "checks": [check.as_dict() for check in checks],
            "connection_error": None}


@pytest.fixture
def prereqs(monkeypatch):
    """The prerequisite check's answer for every target; set ``.result`` to change it."""
    from features.configure import prerequisites as prereq

    holder = SimpleNamespace(result=_prereq_result(), calls=0)

    def check_target(name, config, **kwargs):
        holder.calls += 1
        return holder.result

    monkeypatch.setattr(prereq, "check_target", check_target)
    return holder


@pytest.fixture
def sent(monkeypatch):
    """Product telemetry events captured instead of sent."""
    from shared import product_telemetry

    events = []
    monkeypatch.setattr(product_telemetry, "enabled", lambda: True)
    monkeypatch.setattr(product_telemetry, "capture",
                        lambda event, props=None, **kwargs: events.append((event, props)))
    product_telemetry._agent_seen.clear()
    return events


def run(coro):
    return asyncio.run(coro)


async def _request(app, method, path, client=LOOPBACK, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app, client=client),
                           base_url=BASE) as http:
        return await http.request(method, path, **kwargs)


def _rpc(app, method, params=None, **kwargs):
    headers = {"Accept": ACCEPT, **kwargs.pop("headers", {})}
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return run(_request(app, "POST", "/mcp", json=body, headers=headers, **kwargs))


def _tool(app, tool_name, **arguments):
    response = _rpc(app, "tools/call", {"name": tool_name, "arguments": arguments})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    text = result["content"][0]["text"]
    return result["isError"], (text if result["isError"] else json.loads(text))


def test_mcp_answers_loopback_agents_without_authorization(app):
    response = _rpc(app, "initialize", {"protocolVersion": "2025-03-26"})
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["serverInfo"]["name"] == "rdst"
    assert "OS keyring" in result["instructions"]
    # Agents registered with a bearer header keep working: the header is ignored.
    stale = _rpc(app, "tools/list", headers={"Authorization": "Bearer " + "0" * 64})
    assert stale.status_code == 200


@pytest.mark.parametrize("origin, status", [
    ("https://evil.example", 403), ("null", 403), ("http://127.0.0.1:5173", 200),
    ("http://localhost:8787", 200)])
def test_mcp_refuses_foreign_browser_origins(app, origin, status):
    assert _rpc(app, "tools/list", headers={"Origin": origin}).status_code == status


@pytest.mark.parametrize("host, status", [
    ("evil.example", 403), ("evil.example:8787", 403), ("127.0.0.1.nip.io", 403),
    ("localhost:8787", 200), ("[::1]:8787", 200), ("127.0.0.1", 200)])
def test_mcp_refuses_rebound_host_names(app, host, status):
    assert _rpc(app, "tools/list", headers={"Host": host}).status_code == status


def test_mcp_refuses_other_machines(app):
    assert _rpc(app, "tools/list", client=("192.168.1.20", 40000)).status_code == 403


def test_mcp_answers_the_wsl_listener_at_its_address(app):
    app.state.agent_connect = {"url": BASE, "wsl_url": "http://172.20.0.1:8787"}

    async def via_wsl(host, server_host):
        body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        transport = ASGITransport(app=app, client=("172.20.5.9", 40000))
        async with AsyncClient(transport=transport, base_url=f"http://{server_host}:8787") as http:
            return await http.post("/mcp", json=body, headers={"Host": host, "Accept": ACCEPT})

    assert run(via_wsl("172.20.0.1:8787", "172.20.0.1")).status_code == 200
    assert run(via_wsl("evil.example", "172.20.0.1")).status_code == 403
    # A WSL client reaching another listener with the WSL Host header is still refused.
    assert run(via_wsl("172.20.0.1:8787", "10.0.0.4")).status_code == 403


def test_mcp_notifications_get_202_and_lists_every_tool(app):
    note = run(_request(app, "POST", "/mcp", json={
        "jsonrpc": "2.0", "method": "notifications/initialized"}, headers={"Accept": ACCEPT}))
    assert note.status_code == 202
    listed = _rpc(app, "tools/list").json()["result"]["tools"]
    assert {tool["name"] for tool in listed} == set(tools.TOOLS)
    for tool in listed:
        assert "RDST is Readyset's database monitoring app" in tool["description"]
        assert "OS keyring" in tool["description"]
    assert _rpc(app, "nope").json()["error"]["code"] == -32601


def test_prompt_carries_the_mcp_url_and_no_secret(app):
    response = run(_request(app, "POST", "/api/agent-connect/prompt"))
    assert response.status_code == 200
    body = response.json()
    assert body["mcp_url"] == f"{BASE}/mcp?a={body['attempt']}"
    assert "claude mcp add" not in body["prompt"]
    assert f'curl -s "{body["mcp_url"]}" -H "Content-Type: application/json"' in body["prompt"]
    for word in ("token", "Authorization", "Bearer", "pair", "expire"):
        assert word not in body["prompt"]
    assert "token" not in body
    cross_site = run(_request(app, "POST", "/api/agent-connect/prompt",
                              headers={"Origin": "https://evil.example"}))
    assert cross_site.status_code == 403


def test_prompt_mentions_the_wsl_address_only_when_there_is_one():
    plain = render_prompt("http://127.0.0.1:9000", "abc")
    assert "isn't reachable" not in plain
    wsl = render_prompt("http://127.0.0.1:9000", "abc", "http://172.20.0.1:9000")
    assert "http://172.20.0.1:9000" in wsl
    for text in (plain, wsl):
        assert all(ord(char) < 128 for char in text)


def test_first_mcp_request_after_a_prompt_reports_the_agent_connected(app):
    async def scenario():
        subscriber, _ = session.events.subscribe()
        try:
            prompt = (await _request(app, "POST", "/api/agent-connect/prompt")).json()
            headers = {"Accept": ACCEPT}
            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            path = prompt["mcp_url"].removeprefix(BASE)
            refused = await _request(app, "POST", path, json=body,
                                     headers={"Origin": "https://evil.example"})
            assert refused.status_code == 403
            for _ in range(2):
                assert (await _request(app, "POST", path, json=body,
                                       headers=headers)).status_code == 200
            steps = []
            while not subscriber.queue.empty():
                steps.append(subscriber.queue.get_nowait()["step"])
            return steps
        finally:
            session.events.unsubscribe(subscriber)

    assert run(scenario()) == ["connected"]


def test_unknown_query_parameters_and_saved_agents_still_reach_mcp(app):
    run(_request(app, "POST", "/api/agent-connect/prompt"))
    assert _rpc(app, "tools/list").status_code == 200
    response = run(_request(app, "POST", "/mcp?a=nope&x=1", json={
        "jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"Accept": ACCEPT}))
    assert response.status_code == 200 and response.json()["result"] == {}


def test_attempts_join_the_prompt_they_name_and_expire():
    from shared import product_telemetry

    first = session.issue_attempt(now=1000.0)
    second = session.issue_attempt(now=1001.0)
    assert session.agent_request(first, now=1002.0)
    assert session.current_attempt(now=1002.0) == product_telemetry.agent_attempt_id(first)
    assert not session.agent_request(second, now=1003.0)
    assert session.current_attempt(now=1003.0) == product_telemetry.agent_attempt_id(second)
    late = 1001.0 + session.ATTEMPT_WINDOW_SECONDS + 1
    assert session.current_attempt(now=late) is None
    assert not session.agent_request(second, now=late)


def test_mcp_config_gives_the_claude_command(app):
    config = run(_request(app, "GET", "/api/agent-connect/mcp-config"))
    assert config.status_code == 200
    body = config.json()
    assert body["mcp_url"] == f"{BASE}/mcp" and "token" not in body
    assert body["command"] == f'claude mcp add --transport http rdst "{BASE}/mcp"'
    cross_site = run(_request(app, "GET", "/api/agent-connect/mcp-config",
                              headers={"Origin": "https://evil.example"}))
    assert cross_site.status_code == 403


def test_token_and_pairing_routes_are_gone(app):
    for path in ("/api/agent-connect/rotate-token", "/api/agent-connect/pair"):
        assert run(_request(app, "POST", path, json={"code": "x"})).status_code in (404, 405)


def test_events_stream_replays_progress_since_the_prompt():
    from features.agent_connect.api.routes import agent_connect_events

    async def scenario():
        start = session.events.publish("connected")["ts"]
        session.events.publish("database_added", target="shop")

        class FakeRequest:
            headers = {"host": "127.0.0.1:8787"}
            client = SimpleNamespace(host="127.0.0.1")
            url = SimpleNamespace(scheme="http")
            calls = 0

            async def is_disconnected(self):
                FakeRequest.calls += 1
                return FakeRequest.calls > 2

        response = await agent_connect_events(FakeRequest(), since=start)
        frames = []
        async for frame in response.body_iterator:
            frames.append(json.loads(frame["data"]))
        return frames

    frames = run(scenario())
    assert [frame["step"] for frame in frames][-2:] == ["connected", "database_added"]
    assert frames[-1]["target"] == "shop"


@pytest.mark.parametrize("headers,client,status", [
    ({"Origin": "https://evil.example"}, ("127.0.0.1", 50000), 403),
    ({}, ("203.0.113.9", 50000), 403),
])
def test_events_stream_refuses_foreign_pages_and_remote_callers(app, headers, client, status):
    response = run(_request(app, "GET", "/api/agent-connect/events", client=client,
                            headers=headers))
    assert response.status_code == status


def test_event_bus_delivers_to_live_subscribers():
    async def scenario():
        subscriber, backlog = session.events.subscribe()
        session.events.publish("tested", target="shop")
        event = await asyncio.wait_for(subscriber.queue.get(), timeout=1)
        session.events.unsubscribe(subscriber)
        return backlog, event

    backlog, event = run(scenario())
    assert backlog == []
    assert event["step"] == "tested" and event["target"] == "shop"
    with pytest.raises(ValueError):
        session.events.publish("bogus")


def _add_shop(app):
    return _tool(app, "rdst_add_database", name="shop",
                 url="postgresql+psycopg2://app:s3cret@localhost:5433/shop")


def test_status_and_add_database_store_the_password_in_the_keyring(app, _isolated_keyring):
    from shared.config.targets import TargetsConfig

    failed, status = _tool(app, "rdst_status")
    assert not failed
    assert status["targets"] == []
    assert status["ai_access"] == {"signed_in": True, "detail": None}
    failed, added = _add_shop(app)
    assert not failed, added
    assert added == {"target": "shop", "added": True, "persisted": True,
                     "message": added["message"]}
    config = TargetsConfig()
    config.load()
    saved = config.get("shop")
    assert (saved["engine"], saved["host"], saved["port"], saved["database"], saved["user"]) == \
        ("postgresql", "localhost", 5433, "shop", "app")
    assert "s3cret" not in (config.path.read_text())
    assert "s3cret" in {value for value in _isolated_keyring._store.values()}
    failed, status = _tool(app, "rdst_status")
    assert status["targets"][0]["name"] == "shop"
    assert status["targets"][0]["has_password"] is True
    assert status["targets"][0]["monitoring"] is False


def test_add_database_reads_the_url_from_an_env_file(app, tmp_path):
    env_file = tmp_path / "project.env"
    env_file.write_text('OTHER=1\nexport DATABASE_URL="mysql://root:pw@127.0.0.1/app"\n')
    failed, added = _tool(app, "rdst_add_database", name="app", env_file=str(env_file),
                          env_var="DATABASE_URL")
    assert not failed, added
    from shared.config.targets import TargetsConfig

    config = TargetsConfig()
    config.load()
    assert config.get("app")["engine"] == "mysql"
    assert config.get("app")["port"] == 3306


def test_add_database_reports_session_only_passwords(app, monkeypatch):
    from shared.secret_store_service import SecretStoreService

    monkeypatch.setattr(SecretStoreService, "is_available", lambda self: False)
    failed, added = _add_shop(app)
    assert not failed
    assert added["persisted"] is False
    assert "until it restarts" in added["warning"]


def test_add_database_errors_never_echo_the_url(app):
    failed, text = _tool(app, "rdst_add_database", name="bad", url="oracle://u:topsecret@h/db")
    assert failed
    assert "topsecret" not in text
    _add_shop(app)
    failed, text = _add_shop(app)
    assert failed and "already exists" in text


def test_test_and_monitoring_tools_follow_the_routes(app, monkeypatch, prereqs):
    from features.configure.service import ConfigureService

    async def connected(self, name, target_config=None):
        yield ConfigureConnectionTestEvent(type="connection_test", target_name=name,
                                           status="success", message="Connected",
                                           server_version="16.2")

    monkeypatch.setattr(ConfigureService, "test_connection", connected)
    _add_shop(app)

    async def collect():
        subscriber, _ = session.events.subscribe()
        await asyncio.to_thread(_tool, app, "rdst_test_database", name="shop")
        await asyncio.to_thread(_tool, app, "rdst_enable_monitoring", name="shop",
                                analysis_mode="plans_only")
        steps = []
        while not subscriber.queue.empty():
            steps.append(subscriber.queue.get_nowait()["step"])
        session.events.unsubscribe(subscriber)
        return steps

    assert run(collect()) == ["tested", "awaiting_user"]
    _turn_on_in_app(app, deep_analysis=True, analysis_mode="plans_only")
    failed, settings = _tool(app, "rdst_get_monitor_settings", name="shop")
    assert not failed and settings["enabled"] is True and settings["execute"] is False
    assert settings["deep_analysis"] is True
    failed, updated = _tool(app, "rdst_update_monitor_settings", name="shop",
                            settings={"nightly_report": False})
    assert not failed and updated["nightly_report"] is False
    failed, status = _tool(app, "rdst_status")
    assert status["targets"][0]["monitoring"] is True
    failed, disabled = _tool(app, "rdst_disable_monitoring", name="shop")
    assert not failed and disabled["enabled"] is False


def _turn_on_in_app(app, **settings):
    """The user clicks Turn on monitoring in the app's dialog."""
    response = run(_request(app, "PUT", "/api/monitor/targets/shop/settings",
                            json={"enabled": True, **settings}))
    assert response.status_code == 200, response.text
    return response.json()


def _monitoring(app) -> bool:
    return bool(_tool(app, "rdst_get_monitor_settings", name="shop")[1]["enabled"])


def test_the_user_turns_monitoring_on_the_first_time(app, prereqs):
    _add_shop(app)
    (failed, waiting), events = _events_from(
        lambda: _tool(app, "rdst_enable_monitoring", name="shop"))
    assert not failed
    assert waiting["target"] == "shop" and waiting["waiting_for_user"] is True
    assert waiting["enabled"] is False
    assert waiting["message"].startswith(
        "RDST is showing the Turn on monitoring screen. Ask the user to read it and click "
        "Turn on monitoring in the RDST app.")
    assert "rdst_status" in waiting["message"]
    assert [(event["step"], event["target"]) for event in events] == [("awaiting_user", "shop")]
    assert "Turn on monitoring" in events[0]["message"]
    assert not _monitoring(app)

    # The settings tool can't turn it on either, and changes nothing while it waits.
    (failed, waiting), events = _events_from(lambda: _tool(
        app, "rdst_update_monitor_settings", name="shop",
        settings={"enabled": True, "nightly_report": False}))
    assert not failed and waiting["waiting_for_user"] is True
    assert [event["step"] for event in events] == ["awaiting_user"]
    settings = _tool(app, "rdst_get_monitor_settings", name="shop")[1]
    assert settings["enabled"] is False and settings["nightly_report"] is True

    # An in-process call marked as the agent's never counts as the user's click.
    from features.monitor.api import routes as monitor

    request = tools.internal_request(app)
    run(monitor.put_settings("shop", monitor.MonitorSettingsUpdate(enabled=False), request))
    assert not monitor.monitoring_approved("shop")

    _turn_on_in_app(app)
    assert monitor.monitoring_approved("shop") and _monitoring(app)


def test_signed_out_the_user_signs_in_and_turns_monitoring_on_in_the_app(
        app, prereqs, monkeypatch):
    _add_shop(app)
    monkeypatch.setattr(tools, "_signed_in", lambda: False)
    (failed, waiting), events = _events_from(
        lambda: _tool(app, "rdst_enable_monitoring", name="shop"))
    assert not failed and waiting["waiting_for_user"] is True
    assert waiting["signed_in"] is False
    assert "sign in" in waiting["message"] and "free with a Readyset account" in waiting["message"]
    assert [(event["step"], event["target"]) for event in events] == [("awaiting_user", "shop")]
    assert "sign in" in events[0]["message"]
    assert not _monitoring(app)


def test_a_turn_on_left_to_the_user_counts_as_waiting_not_done():
    waiting = {"target": "shop", "enabled": False, "waiting_for_user": True}
    assert tools._outcome({**waiting, "signed_in": True}) == (False, "awaiting_user")
    assert tools._outcome({**waiting, "signed_in": False}) == (False, "signin_required")
    assert tools._outcome({"target": "shop", "enabled": True}) == (True, None)


def test_after_the_user_turned_it_on_the_agent_may_toggle_monitoring(app, prereqs, monkeypatch):
    from features.monitor import analyze_queue

    async def start_analyze_queue():
        return app.state.analyze_queue or SimpleNamespace(wake=lambda: None, stop=_stopped)

    async def _stopped():
        return None

    # Each tool call runs on its own event loop here; a real queue would outlive its loop.
    monkeypatch.setattr(analyze_queue, "start_analyze_queue", start_analyze_queue)
    _add_shop(app)
    _turn_on_in_app(app)
    failed, disabled = _tool(app, "rdst_disable_monitoring", name="shop")
    assert not failed and disabled["enabled"] is False
    (failed, enabled), events = _events_from(
        lambda: _tool(app, "rdst_enable_monitoring", name="shop"))
    assert not failed and enabled["enabled"] is True and "waiting_for_user" not in enabled
    assert [event["step"] for event in events] == ["monitoring_enabled"]
    failed, updated = _tool(app, "rdst_update_monitor_settings", name="shop",
                            settings={"enabled": False})
    assert not failed and updated["enabled"] is False
    failed, updated = _tool(app, "rdst_update_monitor_settings", name="shop",
                            settings={"enabled": True})
    assert not failed and updated["enabled"] is True

    # Prerequisites still come first.
    _tool(app, "rdst_disable_monitoring", name="shop")
    prereqs.result = _prereq_result(missing=("pg_stat_statements",))
    failed, text = _tool(app, "rdst_enable_monitoring", name="shop")
    assert failed and text.startswith("Monitoring is NOT on for shop.")
    assert not _monitoring(app)


def test_a_database_added_again_needs_the_user_again(app, prereqs):
    from features.monitor.api import routes as monitor

    _add_shop(app)
    _turn_on_in_app(app)
    monitor._store().forget_target("shop")
    failed, waiting = _tool(app, "rdst_enable_monitoring", name="shop")
    assert not failed and waiting["waiting_for_user"] is True


def test_test_database_reports_failure_as_a_result(app, monkeypatch):
    from features.configure.service import ConfigureService

    async def refused(self, name, target_config=None):
        yield ConfigureConnectionTestEvent(type="connection_test", target_name=name,
                                           status="failed", message="Connection refused",
                                           category="host_unreachable")

    monkeypatch.setattr(ConfigureService, "test_connection", refused)
    _add_shop(app)
    failed, result = _tool(app, "rdst_test_database", name="shop")
    assert not failed
    assert result["ok"] is False and result["category"] == "host_unreachable"
    failed, text = _tool(app, "rdst_test_database", name="missing")
    assert failed and "no database named missing" in text


def test_read_tools_answer_for_a_monitored_database(app):
    from shared.monitor.monitor_store import MonitorStore

    _add_shop(app)
    store = MonitorStore()
    item = store.upsert_item("shop", "stuck_query", "a", 0.9, "Stuck", {}, 900)
    store.record_user_analysis("shop", "k1", "SELECT * FROM orders WHERE id = $1", 900,
                               analysis_ref=None, mode=None)

    failed, inbox = _tool(app, "rdst_list_inbox", target="shop")
    assert not failed and inbox["total"] == 1
    item_id = inbox["items"][0]["id"]
    assert item_id == item["id"]
    failed, one = _tool(app, "rdst_get_inbox_item", id=item_id)
    assert not failed and one["title"] == "Stuck"
    failed, acted = _tool(app, "rdst_inbox_action", id=item_id, verb="dismiss")
    assert not failed and acted["state"] == "dismissed"
    for name, arguments in (("rdst_list_actions", {}), ("rdst_get_index_plan", {}),
                            ("rdst_target_health", {"range": "24h"}),
                            ("rdst_top_queries", {}), ("rdst_latest_report", {})):
        failed, result = _tool(app, name, target="shop", **arguments)
        assert not failed, (name, result)
    failed, health = _tool(app, "rdst_target_health", target="shop")
    assert "sparkline" not in health
    failed, detail = _tool(app, "rdst_query_detail", target="shop", key="k1")
    assert not failed and detail["key"] == "k1"
    failed, series = _tool(app, "rdst_query_timeseries", target="shop", key="k1",
                           range="24h")
    assert not failed, series
    assert series["key"] == "k1" and isinstance(series["points"], list)
    assert series["resolution_s"] and "mean_ms" in series["units"]


def test_action_tool_closes_an_action_right_away(app, monkeypatch):
    from features.monitor import actions
    from features.monitor.api import routes as monitor
    from features.monitor.index_plan import build_plan
    from shared.monitor.monitor_store import MonitorStore

    _add_shop(app)
    store = MonitorStore()
    settings = [{"id": f"setting_{name}", "title": f"Raise {name}", "severity": "bad",
                 "finding": f"{name} is low.", "sql": "ALTER SYSTEM ...",
                 "setting": {"name": name}, "restart": False}
                for name in ("work_mem", "shared_buffers")]
    plan = build_plan({"engine": "postgresql", "now": 1000, "observed_s": 3600,
                       "counter_age_s": 86400, "existing": [], "tables": {}, "queries": []})
    actions.reconcile(store, "shop", 1000, actions.candidates(store, "shop", plan, [], settings))
    applied, dropped = (row["id"] for row in store.actions("shop"))
    reads = []
    monkeypatch.setattr(monitor, "_read_database", lambda *args: reads.append(args[1]))

    failed, page = _tool(app, "rdst_action", target="shop", id=applied, verb="did_it")
    assert not failed and reads == ["shop"]
    states = {row["id"]: row["state"] for row in store.actions("shop")}
    assert states[applied] == "applied" and states[dropped] == "open"
    failed, _ = _tool(app, "rdst_action", target="shop", id=applied, verb="undid_it")
    states = {row["id"]: row["state"] for row in store.actions("shop")}
    assert not failed and states[applied] != "applied"
    failed, page = _tool(app, "rdst_action", target="shop", id=dropped, verb="dismiss",
                         reason="managed by ops")
    assert not failed and page["dismissed"] == 1
    failed, text = _tool(app, "rdst_action", target="shop", id=dropped, verb="check")
    assert failed and "did_it, dismiss or undid_it" in text
    failed, text = _tool(app, "rdst_action", target="shop", id=999, verb="dismiss")
    assert failed and "not found" in text.lower()
    for name in ("rdst_list_actions", "rdst_get_index_plan"):
        assert "rdst_action with did_it" in tools.TOOLS[name][0]


def test_discovery_file_is_owner_only_and_removed_by_its_owner(data_dir):
    path = discovery.write_discovery({"url": "http://127.0.0.1:1", "port": 1,
                                      "pid": os.getpid(), "started_at": 1})
    assert path == data_dir / "desktop-server.json"
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert discovery.read_discovery()["url"] == "http://127.0.0.1:1"
    discovery.remove_discovery(pid=os.getpid() + 1)
    assert path.exists()
    discovery.remove_discovery()
    assert not path.exists()


def test_discovery_ignores_a_dead_server(data_dir):
    import psutil

    dead = max(psutil.pids()) + 10_000
    discovery.write_discovery({"url": "http://127.0.0.1:1", "pid": dead})
    assert discovery.read_discovery() is None


def test_runtime_advertises_the_server_and_cleans_up(data_dir, monkeypatch):
    monkeypatch.setenv("RDST_WEB_HOST", "0.0.0.0")
    monkeypatch.setenv("RDST_WEB_PORT", "8899")
    app = SimpleNamespace(state=SimpleNamespace())
    runtime = discovery.AgentConnectRuntime()
    run(runtime.start(app))
    info = json.loads((data_dir / "desktop-server.json").read_text())
    assert info["url"] == "http://127.0.0.1:8899" and info["port"] == 8899
    assert info["pid"] == os.getpid() and "token" not in info
    assert "wsl_url" not in info
    assert not (data_dir / "mcp-token").exists()
    run(runtime.stop())
    assert not (data_dir / "desktop-server.json").exists()


def test_wsl_listener_serves_only_mcp():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["path"])

    async def scenario():
        sent = []

        async def send(message):
            sent.append(message)

        narrowed = discovery.wsl_only(inner)
        for path in ("/mcp", "/api/agent-connect/pair", "/api/configure/targets",
                     "/api/agent-connect/prompt"):
            await narrowed({"type": "http", "path": path}, None, send)
        return sent

    sent = run(scenario())
    assert seen == ["/mcp"]
    assert [message.get("status") for message in sent
            if "status" in message] == [404, 404, 404]


def test_wsl_listener_follows_the_adapter_after_startup(data_dir, monkeypatch):
    """WSL started after RDST, then took a new address: the listener and file keep up."""
    started, stopped = [], []

    class Listener:
        def __init__(self, app, host, port):
            self.host, self.url = host, discovery.local_url(host, port)

        async def start(self):
            started.append(self.host)
            return True

        async def stop(self):
            stopped.append(self.host)

    adapter = {"ip": None}
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(discovery, "wsl_adapter_ip", lambda: adapter["ip"])
    monkeypatch.setattr(discovery, "WslListener", Listener)
    monkeypatch.setattr(discovery, "WSL_PROBE_SECONDS", 0.01)
    monkeypatch.setenv("RDST_WEB_PORT", "8899")
    path = data_dir / "desktop-server.json"

    async def advertised(expected):
        for _ in range(500):
            if json.loads(path.read_text()).get("wsl_url") == expected:
                return expected
            await asyncio.sleep(0.01)
        return json.loads(path.read_text()).get("wsl_url")

    async def scenario():
        runtime = discovery.AgentConnectRuntime()
        await runtime.start(SimpleNamespace(state=SimpleNamespace()))
        seen = [await advertised(None)]
        adapter["ip"] = "172.20.0.1"
        seen.append(await advertised("http://172.20.0.1:8899"))
        adapter["ip"] = "172.21.0.1"
        seen.append(await advertised("http://172.21.0.1:8899"))
        await runtime.stop()
        return seen

    assert run(scenario()) == [None, "http://172.20.0.1:8899", "http://172.21.0.1:8899"]
    assert started == ["172.20.0.1", "172.21.0.1"]
    assert stopped == ["172.20.0.1", "172.21.0.1"]
    assert not path.exists()


def test_wsl_adapter_is_windows_only(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert discovery.wsl_adapter_ip() is None


def _monitor_writes() -> list[tuple[str, str]]:
    """Every state-changing /api/monitor route, with sample path parameters."""
    import re

    from features.monitor.api import routes as monitor

    samples = {"target": "db", "key": "k", "job_id": "1", "rule_id": "1", "item_id": "1",
               "action": "seen", "action_id": "1", "verb": "dismiss", "report_id": "1"}
    return sorted({(method, "/api/monitor" + re.sub(
        r"{(\w+)}", lambda match: samples[match.group(1)], route.path))
        for route in monitor.router.routes
        for method in route.methods if method not in ("GET", "HEAD")})


def test_cross_site_page_cannot_drive_any_monitor_write(app):
    writes = _monitor_writes()
    # The settings switch, inbox verbs and report runs are among them.
    assert ("PUT", "/api/monitor/targets/db/settings") in writes
    assert len(writes) >= 17
    for method, path in writes:
        response = run(_request(app, method, path, json={}, headers={
            "Origin": "https://evil.example"}))
        assert response.status_code == 403, (method, path, response.text)


def test_monitor_reads_and_local_writes_pass_the_guard(app):
    read = run(_request(app, "GET", "/api/monitor/targets/db/settings",
                        headers={"Origin": "https://evil.example"}))
    assert read.status_code == 200
    local = run(_request(app, "PUT", "/api/monitor/targets/db/settings",
                         json={"nightly_report": False}))
    assert local.status_code == 200


def test_claude_add_registers_the_running_app_over_http(data_dir, monkeypatch):
    import argparse
    import shutil
    import subprocess

    import rdst as rdst_module
    from shared.cli import RdstCLI

    discovery.write_discovery({"url": "http://127.0.0.1:4100", "port": 4100,
                               "pid": os.getpid(), "started_at": 1})
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        failed = len(calls) == 1
        return SimpleNamespace(returncode=1 if failed else 0,
                               stderr="MCP server rdst already exists" if failed else "")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude" if name == "claude"
                        else None)
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = rdst_module.execute_command(
        RdstCLI(), argparse.Namespace(command="claude", action="add", config=None))
    assert result.ok, result.message
    assert "http://127.0.0.1:4100/mcp" in result.message
    assert calls[0] == ["/usr/bin/claude", "mcp", "add", "--transport", "http", "rdst",
                        "http://127.0.0.1:4100/mcp"]
    assert calls[1] == ["/usr/bin/claude", "mcp", "remove", "rdst"]
    assert calls[2] == calls[0]


def _events_from(scenario):
    async def collect():
        subscriber, _ = session.events.subscribe()
        result = await asyncio.to_thread(scenario)
        events = []
        while not subscriber.queue.empty():
            events.append(subscriber.queue.get_nowait())
        session.events.unsubscribe(subscriber)
        return result, events

    return run(collect())


def _connected(monkeypatch):
    from features.configure.service import ConfigureService

    async def connected(self, name, target_config=None):
        yield ConfigureConnectionTestEvent(
            type="connection_test", target_name=name, status="success", message="Connected",
            server_version="16.2", query_capture={"available": True})

    monkeypatch.setattr(ConfigureService, "test_connection", connected)


def test_missing_pg_stat_statements_blocks_monitoring_until_fixed(app, monkeypatch, prereqs):
    _connected(monkeypatch)
    _add_shop(app)
    prereqs.result = _prereq_result(missing=("pg_stat_statements",))

    (failed, tested), events = _events_from(
        lambda: _tool(app, "rdst_test_database", name="shop"))
    assert not failed
    assert tested["ok"] is True and tested["ready_to_monitor"] is False
    assert tested["next_step"] == "enable_query_stats"
    assert tested["blocking_reason"].startswith("Query statistics aren't available")
    assert tested["sql"] == ["CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"]
    assert tested["needs_restart"] is False
    assert "SQL: CREATE EXTENSION IF NOT EXISTS pg_stat_statements;" in tested["instructions"]
    checks = {check["id"]: check for check in tested["prerequisites"]["checks"]}
    assert checks["pg_stat_statements"]["fix_kind"] == "in_database"
    assert checks["pg_stat_statements_preload"]["fix_kind"] is None
    assert [event["step"] for event in events] == ["needs_setup"]
    setup = events[0]
    assert setup["target"] == "shop" and setup["check"] == "query_stats"
    assert setup["ready_to_monitor"] is False and setup["instructions"] == tested["instructions"]
    assert setup["prerequisites"]["checks"][1]["id"] == "pg_stat_statements"

    (failed, text), events = _events_from(
        lambda: _tool(app, "rdst_enable_monitoring", name="shop"))
    assert failed and text.startswith("Monitoring is NOT on for shop.")
    assert "CREATE EXTENSION" in text and "rdst_check_prerequisites" in text
    refusal = json.loads(text[text.index("\n\n{") + 2:])
    assert refusal["ready_to_monitor"] is False and refusal["checks"]
    assert [event["step"] for event in events] == ["needs_setup"]
    assert _tool(app, "rdst_get_monitor_settings", name="shop")[1]["enabled"] is False

    prereqs.result = _prereq_result()
    (failed, checked), events = _events_from(
        lambda: _tool(app, "rdst_check_prerequisites", name="shop"))
    assert not failed and checked["ready_to_monitor"] is True
    assert checked["blocking_reason"] is None and checked["next_step"] == "enable_monitoring"
    assert [event["step"] for event in events] == ["tested"]
    failed, enabled = _tool(app, "rdst_enable_monitoring", name="shop")
    assert not failed and enabled["waiting_for_user"] is True


def test_agents_cannot_turn_monitoring_on_before_setup(app, monkeypatch, prereqs):
    _add_shop(app)
    prereqs.result = _prereq_result(missing=("pg_stat_statements",))
    assert "force" not in tools.TOOLS["rdst_enable_monitoring"][1]["properties"]
    failed, _ = _tool(app, "rdst_enable_monitoring", name="shop", force=True)
    assert failed
    failed, _ = _tool(app, "rdst_update_monitor_settings", name="shop",
                      settings={"enabled": True})
    assert failed
    failed, settings = _tool(app, "rdst_get_monitor_settings", name="shop")
    assert not failed and not settings.get("enabled")


def test_mysql_without_performance_schema_needs_a_server_change(app, prereqs):
    _tool(app, "rdst_add_database", name="shop", url="mysql://app:pw@127.0.0.1:3306/shop")
    prereqs.result = _prereq_result("mysql", missing=("performance_schema",))
    failed, checked = _tool(app, "rdst_check_prerequisites", name="shop")
    assert not failed
    assert checked["ready_to_monitor"] is False and checked["source"] == "performance_schema"
    assert checked["needs_restart"] is True and checked["sql"] == []
    assert checked["failed"] == ["performance_schema"]
    by_id = {check["id"]: check for check in checked["checks"]}
    assert by_id["performance_schema"]["fix_kind"] == "server_config"
    failed, text = _tool(app, "rdst_enable_monitoring", name="shop")
    assert failed and "performance_schema" in text


def test_unreachable_check_blocks_like_the_dialog(app, prereqs):
    _add_shop(app)
    prereqs.result = {"target": "shop", "engine": "postgresql", "ready": False, "checks": [],
                      "connection_error": "connection refused"}
    failed, checked = _tool(app, "rdst_check_prerequisites", name="shop")
    assert checked["ready_to_monitor"] is False and checked["next_step"] == "fix_connection"
    assert "couldn't connect" in checked["blocking_reason"]
    failed, text = _tool(app, "rdst_enable_monitoring", name="shop")
    assert failed and "NOT on" in text


def test_recheck_route_publishes_the_current_step(app, prereqs):
    _add_shop(app)
    prereqs.result = _prereq_result(missing=("pg_stat_statements",))
    cross_site = run(_request(app, "POST", "/api/agent-connect/recheck",
                              json={"target": "shop"}, headers={"Origin": "https://evil.example"}))
    assert cross_site.status_code == 403
    response, events = _events_from(lambda: run(_request(
        app, "POST", "/api/agent-connect/recheck", json={"target": "shop"})))
    assert response.status_code == 200 and response.json()["ready_to_monitor"] is False
    assert [event["step"] for event in events] == ["needs_setup"]
    prereqs.result = _prereq_result()
    response, events = _events_from(lambda: run(_request(
        app, "POST", "/api/agent-connect/recheck", json={"target": "shop"})))
    assert response.json()["ready_to_monitor"] is True
    assert [event["step"] for event in events] == ["tested"]
    missing = run(_request(app, "POST", "/api/agent-connect/recheck", json={"target": "nope"}))
    assert missing.status_code == 404


def test_needs_setup_frames_reach_the_events_stream():
    from features.agent_connect.api.routes import agent_connect_events

    async def scenario():
        start = session.events.publish("connected")["ts"]
        session.events.publish("needs_setup", target="shop", check="query_stats",
                               instructions=["Create pg_stat_statements in shop."],
                               ready_to_monitor=False, blocking_reason=None)

        class FakeRequest:
            headers = {"host": "127.0.0.1:8787"}
            client = SimpleNamespace(host="127.0.0.1")
            url = SimpleNamespace(scheme="http")

            async def is_disconnected(self):
                return True

        response = await agent_connect_events(FakeRequest(), since=start)
        return [json.loads(frame["data"]) async for frame in response.body_iterator]

    setup = run(scenario())[-1]
    assert setup["step"] == "needs_setup" and setup["check"] == "query_stats"
    assert setup["instructions"] == ["Create pg_stat_statements in shop."]
    assert setup["ready_to_monitor"] is False and "blocking_reason" not in setup


def test_agent_telemetry_ties_a_prompt_to_its_connection_and_tool_calls(app, prereqs, sent):
    from shared import product_telemetry

    prompt = run(_request(app, "POST", "/api/agent-connect/prompt")).json()
    code = prompt["attempt"]
    attempt = product_telemetry.agent_attempt_id(code)
    assert run(_request(app, "POST", prompt["mcp_url"].removeprefix(BASE), json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={"Accept": ACCEPT})).status_code == 200
    _add_shop(app)
    prereqs.result = _prereq_result(missing=("pg_stat_statements",))
    for _ in range(3):
        _tool(app, "rdst_check_prerequisites", name="shop")
    _tool(app, "rdst_enable_monitoring", name="shop")
    prereqs.result = _prereq_result()
    _tool(app, "rdst_check_prerequisites", name="shop")

    steps = [props for event, props in sent if event == "agent_connect_step"]
    assert [(p["step"], p.get("tool"), p["ok"]) for p in steps] == [
        ("prompt_issued", None, None), ("connected", None, True),
        ("tool_called", "rdst_add_database", True),
        ("tool_called", "rdst_check_prerequisites", False),
        ("tool_called", "rdst_enable_monitoring", False),
        ("tool_called", "rdst_check_prerequisites", True)]
    assert {p["attempt_id"] for p in steps} == {attempt}
    assert steps[3]["error_class"] == "needs_setup"
    prereq = [(p["check"], p["ok"], p["via"]) for event, p in sent
              if event == "database_add_prereq_result"]
    assert prereq == [("pg_stat_statements_preload", True, "mcp"),
                      ("pg_stat_statements", False, "mcp"),
                      ("visibility_probe", False, "mcp"),
                      ("pg_stat_statements", True, "mcp"),
                      ("visibility_probe", True, "mcp")]
    for event, props in sent:
        text = json.dumps(props)
        assert code not in text and "shop" not in text and "localhost" not in text


def test_prerequisites_route_reports_the_manual_path(app, prereqs, sent):
    _add_shop(app)
    prereqs.result = _prereq_result(missing=("pg_stat_statements",))
    for _ in range(2):
        response = run(_request(app, "GET", "/api/configure/targets/shop/prerequisites",
                                headers={"X-RDST-Via": "url"}))
        assert response.status_code == 200
    prereq = [(p["check"], p["ok"], p["via"]) for event, p in sent
              if event == "database_add_prereq_result"]
    assert prereq == [("pg_stat_statements_preload", True, "url"),
                      ("pg_stat_statements", False, "url"),
                      ("visibility_probe", False, "url")]


def test_prompt_works_in_a_fresh_agent_session():
    text = render_prompt("http://127.0.0.1:47787", "abc")
    assert "monitors" in text or "watches" in text
    assert "Which PostgreSQL or MySQL database do you want RDST to watch?" in text
    assert "rdst_check_prerequisites" in text and "ready_to_monitor" in text
    assert "only after I say yes" in text
    assert "Nothing needs installing or registering" in text
    assert 'curl -s "http://127.0.0.1:47787/mcp?a=abc"' in text
    assert "token" not in text and "Authorization" not in text
    # The prompt asks the agent to find the database without naming credential sources.
    for spelled_out in ("POSTGRES_PASSWORD", "MYSQL_ROOT_PASSWORD", "pgpass", "my.cnf",
                        "password", "secrets manager"):
        assert spelled_out not in text
    assert "the same way you would to run the app" in text
    assert "Is this the database you want RDST to watch?" in text
    assert "Keep connection details out of your replies." in text
    assert "Once I confirm, add it with rdst_add_database" in text
    assert "Turn on monitoring with rdst_enable_monitoring" not in text
    assert "waiting_for_user: true" in text
    assert "sign in to Readyset if it asks" in text
    assert "click Turn on monitoring in the RDST app" in text
    assert "check rdst_status" in text and "monitoring: true" in text
    assert "call rdst_action with did_it" in text
    assert "rdst_query_timeseries" in text
    assert "quietly" not in text and all(ord(char) < 128 for char in text)

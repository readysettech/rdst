"""Install-time databases from the Helm chart's targets list, and the server-mode prompt that
has a user's own coding agent connect a database in their cluster."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from features.agent_connect import tools
from features.agent_connect.api import routes as agent_routes
from features.agent_connect.prompt import AGENT_KEY_PLACEHOLDER, render_prompt
from features.configure import seed_targets
from shared import agent_key, server_mode
from shared.config.targets import TargetsConfig

SHOP = {"name": "shop-db", "engine": "postgresql", "host": "pg-rw.database.svc.cluster.local",
        "port": 5432, "database": "shop", "user": "shop",
        "passwordEnv": "RDST_SHOP_DB_PASSWORD", "monitoring": True}


@pytest.fixture(autouse=True)
def _server(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_SERVER", "1")
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RDST_ACCESS_TOKEN", "test-access-token")
    monkeypatch.delenv("RDST_AUTH", raising=False)
    monkeypatch.setattr(server_mode, "_token_cache", None)
    yield
    server_mode._token_cache = None


@pytest.fixture
def enabled(monkeypatch):
    calls: list[str] = []

    async def enable(app, name):
        calls.append(name)

    monkeypatch.setattr(seed_targets, "_enable", enable)
    return calls


def _seed(entries) -> list[str]:
    return asyncio.run(seed_targets.seed(FastAPI(), json.dumps(entries)))


def _target(name: str) -> dict | None:
    config = TargetsConfig()
    config.load()
    return config.get(name)


def test_a_target_is_added_pointing_at_its_environment_variable(monkeypatch, enabled):
    monkeypatch.setenv("RDST_SHOP_DB_PASSWORD", "s3cret-from-a-secret")
    assert _seed([SHOP]) == ["shop-db"]
    saved = _target("shop-db")
    assert saved["host"] == SHOP["host"] and saved["password_env"] == "RDST_SHOP_DB_PASSWORD"
    assert enabled == ["shop-db"]


def test_the_password_is_never_written_to_disk(monkeypatch, tmp_path, enabled):
    monkeypatch.setenv("RDST_SHOP_DB_PASSWORD", "s3cret-from-a-secret")
    _seed([SHOP])
    assert TargetsConfig().path.parent == tmp_path
    for path in [*tmp_path.rglob("*"), *Path.home().rglob("*")]:
        if path.is_file():
            assert b"s3cret-from-a-secret" not in path.read_bytes(), path


def test_seeding_again_changes_nothing_and_an_edit_updates_it(monkeypatch, enabled):
    _seed([SHOP])
    first = _target("shop-db")
    assert _seed([SHOP]) == ["shop-db"]
    assert _target("shop-db") == first
    _seed([{**SHOP, "port": 6432}])
    assert _target("shop-db")["port"] == 6432


def test_editing_the_endpoint_keeps_the_charts_password_variable(monkeypatch, enabled):
    monkeypatch.setenv("RDST_SHOP_DB_PASSWORD", "s3cret-from-a-secret")
    _seed([SHOP])
    _seed([{**SHOP, "port": 6432, "user": "shop_ro"}])
    saved = _target("shop-db")
    assert (saved["port"], saved["user"]) == (6432, "shop_ro")
    assert saved["password_env"] == "RDST_SHOP_DB_PASSWORD"


def test_monitoring_turns_on_once_and_a_users_off_switch_sticks(enabled):
    _seed([SHOP])
    _seed([SHOP])
    _seed([{**SHOP, "port": 6432}])
    assert enabled == ["shop-db"]


def test_monitoring_asked_for_later_turns_on_then(enabled):
    _seed([{**SHOP, "monitoring": False}])
    _seed([SHOP])
    assert enabled == ["shop-db"]


def test_a_database_added_in_the_ui_is_left_alone(enabled):
    config = TargetsConfig()
    config.load()
    config.upsert("shop-db", {"engine": "postgresql", "host": "mine.example.com", "port": 5432,
                              "database": "app", "user": "me",
                              "password_env": "RDST_SHOP_DB_PASSWORD"})
    config.save()
    assert _seed([SHOP]) == []
    assert _target("shop-db")["host"] == "mine.example.com"
    assert enabled == []


def test_monitoring_stays_off_unless_asked(enabled):
    _seed([{**SHOP, "monitoring": False}])
    assert _target("shop-db") is not None and enabled == []


def test_bad_entries_are_skipped_with_a_reason_and_the_good_one_still_lands(enabled, caplog):
    entries = [{**SHOP, "name": "bad name!"}, {**SHOP, "name": "x", "engine": "oracle"},
               {"name": "partial", "host": "h"}, "nope", SHOP, {**SHOP}]
    with caplog.at_level("WARNING", logger="uvicorn.error"):
        assert _seed(entries) == ["shop-db"]
    text = caplog.text
    assert "invalid name" in text and "oracle" in text and "missing database, user" in text
    assert "isn't an object" in text and "repeats a name" in text


def test_garbage_never_stops_startup(enabled, caplog):
    with caplog.at_level("WARNING", logger="uvicorn.error"):
        assert asyncio.run(seed_targets.seed(FastAPI(), "{not json")) == []
    assert "isn't valid JSON" in caplog.text


def test_the_default_password_variable_follows_rdsts_naming():
    entries, problems = seed_targets.parse(json.dumps([{**SHOP, "passwordEnv": None}]))
    assert not problems and entries[0]["password_env"] == "RDST_SHOP_DB_PASSWORD"


def test_the_server_prompt_finds_the_database_in_the_cluster():
    text = render_prompt("https://rdst.example.com", "att1", server=True)
    assert f"Authorization: Bearer {AGENT_KEY_PLACEHOLDER}" in text
    assert "https://rdst.example.com/mcp?a=att1" in text
    for phrase in ("svc.cluster.local", "never localhost or a port-forward address",
                   "kubectl get clusters.postgresql.cnpg.io -A", "<cluster>-app Secret",
                   "read-only or monitoring user", "Never show me the password",
                   "Ask me before you change anything in my cluster",
                   "kubectl exec into the database's pod"):
        assert phrase in text, phrase
    assert "127.0.0.1" not in text and "this computer" not in text
    assert render_prompt("http://127.0.0.1:8787", "att1") != text


def test_an_agent_with_the_agent_key_adds_a_database(monkeypatch):
    key = agent_key.create()

    async def connects(name, fields):
        return True, None, None

    monkeypatch.setattr(tools, "_try_connection", connects)
    app = FastAPI()
    server_mode.install(app)
    app.include_router(agent_routes.mcp_router)
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "rdst_add_database", "arguments": {
            "name": "orders", "engine": "postgresql", "port": 5432, "database": "orders",
            "host": "orders-rw.apps.svc.cluster.local", "user": "orders",
            "password": "from-the-secret"}}}

    async def run():
        transport = ASGITransport(app=app, client=("10.42.0.7", 51000))
        async with AsyncClient(transport=transport, base_url="http://rdst.example.com") as http:
            listed = await http.post("/mcp", headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json, text/event-stream"}, json={
                "jsonrpc": "2.0", "id": 0, "method": "tools/list", "params": {}})
            added = await http.post("/mcp", headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json, text/event-stream"}, json=call)
            return listed, added

    listed, added = asyncio.run(run())
    assert listed.status_code == 200 and added.status_code == 200, added.text
    description = listed.json()["result"]["tools"][0]["description"]
    assert "running as a server" in description and "this computer" not in description
    result = added.json()["result"]
    assert not result.get("isError"), result
    assert _target("orders")["host"] == "orders-rw.apps.svc.cluster.local"

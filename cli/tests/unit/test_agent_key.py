"""A server-mode RDST holds one agent key for coding agents on other machines. Creating a key
replaces the old one, only its hash is stored, and it opens /mcp and nothing else."""

from __future__ import annotations

import asyncio
import json
import os
import stat

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from features.agent_connect.api import routes as agent_routes
from shared import agent_key, server_mode

TOKEN = "test-access-token"
ORIGIN = "https://rdst.example.com"
INGRESS = {"x-forwarded-proto": "https", "x-forwarded-host": "rdst.example.com"}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
ACCEPT = {"Accept": "application/json, text/event-stream"}


@pytest.fixture(autouse=True)
def _server(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_SERVER", "1")
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RDST_ACCESS_TOKEN", TOKEN)
    monkeypatch.delenv("RDST_AUTH", raising=False)
    monkeypatch.setattr(server_mode, "_token_cache", None)
    yield
    server_mode._token_cache = None


def _app() -> FastAPI:
    app = FastAPI()
    server_mode.install(app)
    server_mode.install_agent_key(app)
    app.include_router(agent_routes.mcp_router)

    @app.get("/api/status")
    async def status():
        return {"ok": True}

    return app


def _call(method: str, path: str, headers: dict[str, str] | None = None,
          body: dict | None = None) -> Response:
    async def run() -> Response:
        transport = ASGITransport(app=_app(), client=("10.42.0.7", 51000))
        async with AsyncClient(transport=transport, base_url="http://rdst.example.com") as http:
            return await http.request(method, path, json=body,
                                      headers={**ACCEPT, **INGRESS, **(headers or {})})

    return asyncio.run(run())


def _ui(method: str) -> Response:
    """A request from the signed-in UI: the session cookie plus the page's own Origin."""
    cookie = f"{server_mode.SESSION_COOKIE}={server_mode._session_value(TOKEN)}"
    return _call(method, "/api/server/agent-key", {"Cookie": cookie, "Origin": ORIGIN})


def _mcp(key: str) -> Response:
    return _call("POST", "/mcp", {"Authorization": f"Bearer {key}"}, LIST_TOOLS)


def test_the_ui_creates_a_key_that_an_agent_uses_on_mcp():
    created = _ui("POST")
    assert created.status_code == 200, created.text
    key = created.json()["key"]
    assert key.startswith("rdst_ak_") and created.json()["exists"] is True
    response = _mcp(key)
    assert response.status_code == 200, response.text
    assert "rdst_list_actions" in {tool["name"] for tool in response.json()["result"]["tools"]}


def test_a_new_key_replaces_the_old_one():
    first = _ui("POST").json()["key"]
    second = _ui("POST").json()["key"]
    assert first != second
    assert _mcp(first).status_code == 401
    assert _mcp(second).status_code == 200


def test_revoking_the_key_shuts_the_agent_out():
    key = _ui("POST").json()["key"]
    revoked = _ui("DELETE")
    assert revoked.json() == {"revoked": True, "exists": False, "created_at": None,
                              "last_used_at": None}
    assert _mcp(key).status_code == 401


def test_only_the_hash_is_stored_in_a_private_file():
    key = _ui("POST").json()["key"]
    path = agent_key.key_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    text = path.read_text()
    assert key not in text
    assert set(json.loads(text)) == {"hash", "created_at", "last_used_at"}


def test_status_shows_when_the_key_was_made_and_last_used_never_the_key():
    assert _ui("GET").json() == {"exists": False, "created_at": None, "last_used_at": None}
    key = _ui("POST").json()["key"]
    assert _ui("GET").json()["last_used_at"] is None
    _mcp(key)
    shown = _ui("GET").json()
    assert shown["exists"] is True and shown["last_used_at"] is not None
    assert key not in json.dumps(shown)


def test_the_agent_key_opens_mcp_and_nothing_else():
    key = _ui("POST").json()["key"]
    bearer = {"Authorization": f"Bearer {key}"}
    assert _call("GET", "/api/status", bearer).status_code == 401
    assert _call("GET", "/api/server/agent-key", bearer).status_code == 401
    assert _call("POST", "/api/server/agent-key", bearer).status_code == 401
    assert _call("DELETE", "/api/server/agent-key", bearer).status_code == 401
    page = _call("GET", "/", bearer)
    assert page.status_code == 303 and page.headers["location"].startswith("/login")


def test_creating_a_key_needs_a_signed_in_ui_session():
    assert _call("POST", "/api/server/agent-key").status_code == 401
    other_site = _call("POST", "/api/server/agent-key", {
        "Cookie": f"{server_mode.SESSION_COOKIE}={server_mode._session_value(TOKEN)}",
        "Origin": "https://evil.example"})
    assert other_site.status_code == 403
    assert not agent_key.status()["exists"]


def test_a_web_page_cant_use_the_agent_key_on_mcp():
    key = _ui("POST").json()["key"]
    response = _call("POST", "/mcp", {"Authorization": f"Bearer {key}", "Origin": ORIGIN},
                     LIST_TOOLS)
    assert response.status_code == 403


def test_a_wrong_or_lookalike_key_is_refused():
    _ui("POST")
    assert _mcp("rdst_ak_not-the-key").status_code == 401
    assert _mcp("").status_code == 401


def test_the_access_token_still_works_on_mcp():
    assert _mcp(TOKEN).status_code == 200


def test_outside_server_mode_there_are_no_agent_key_routes(monkeypatch):
    monkeypatch.delenv("RDST_SERVER")
    app = FastAPI()
    server_mode.install_agent_key(app)
    assert not [route for route in app.routes if getattr(route, "path", "") ==
                "/api/server/agent-key"]

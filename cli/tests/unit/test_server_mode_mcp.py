"""A coding agent on the user's own machine reaches a server-mode RDST's /mcp over HTTPS with
the access token as a Bearer header. A browser session alone can't drive it, and the prompt
the database page copies carries the server's public address and the header."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from features.agent_connect.api import routes as agent_routes
from features.agent_connect.prompt import render_ask_prompt
from shared import server_mode

TOKEN = "test-access-token"
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


def _send(path: str, headers: dict[str, str] | None = None, cookies=None) -> Response:
    app = FastAPI()
    server_mode.install(app)
    app.include_router(agent_routes.mcp_router)

    async def run() -> Response:
        transport = ASGITransport(app=app, client=("10.42.0.7", 51000))
        async with AsyncClient(transport=transport, base_url="http://rdst.example.com",
                               cookies=cookies) as http:
            return await http.post(path, json=LIST_TOOLS,
                                   headers={**ACCEPT, **INGRESS, **(headers or {})})

    return asyncio.run(run())


def test_an_agent_on_another_machine_lists_tools_with_the_bearer_token():
    response = _send("/mcp", {"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200, response.text
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert "rdst_list_actions" in names


def test_the_prompts_attempt_parameter_works_alongside_the_token():
    response = _send("/mcp?a=abc123", {"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200, response.text


def test_without_the_token_mcp_answers_401_not_a_sign_in_page():
    response = _send("/mcp")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"
    wrong = _send("/mcp", {"Authorization": "Bearer nope"})
    assert wrong.status_code == 401


def test_a_browser_session_alone_cant_drive_mcp():
    cookies = {server_mode.SESSION_COOKIE: server_mode._session_value(TOKEN)}
    response = _send("/mcp", cookies=cookies)
    assert response.status_code == 401


def test_a_web_page_cant_drive_mcp_even_with_the_token():
    response = _send("/mcp", {"Authorization": f"Bearer {TOKEN}",
                              "Origin": "https://rdst.example.com"})
    assert response.status_code == 403


def test_the_server_prompt_names_the_public_address_and_the_header():
    prompt = render_ask_prompt("https://rdst.example.com", "shop", "abc", server=True)
    assert "runs as a server at https://rdst.example.com" in prompt
    assert ('curl -s "https://rdst.example.com/mcp?a=abc" '
            '-H "Authorization: Bearer RDST_AGENT_KEY_HERE"') in prompt
    assert "agent key as a Bearer header" in prompt
    assert "this computer" not in prompt and "127.0.0.1" not in prompt


def test_a_desktop_prompt_is_unchanged():
    prompt = render_ask_prompt("http://127.0.0.1:8787", "shop", "abc")
    assert "installed on this computer" in prompt and "Bearer" not in prompt

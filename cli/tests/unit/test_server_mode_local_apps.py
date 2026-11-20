"""A server-mode RDST never runs a coding agent, a terminal or an app in its own container,
or writes an agent's settings there, even for a request that reaches it from 127.0.0.1
through kubectl port-forward."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from features.agent_connect.api import routes as agent_routes
from shared import server_mode

TOKEN = "test-access-token"


@pytest.fixture(autouse=True)
def _server(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_SERVER", "1")
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RDST_ACCESS_TOKEN", TOKEN)
    monkeypatch.delenv("RDST_AUTH", raising=False)
    monkeypatch.setattr(server_mode, "_token_cache", None)
    yield
    server_mode._token_cache = None


def _post(path: str, body: dict) -> Response:
    app = FastAPI()
    server_mode.install(app)
    app.include_router(agent_routes.router, prefix="/api")

    async def run() -> Response:
        # A port-forwarded connection arrives on the pod's loopback interface.
        transport = ASGITransport(app=app, client=("127.0.0.1", 51000))
        async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8787") as http:
            return await http.post(path, json=body, headers={
                "Authorization": f"Bearer {TOKEN}", "Origin": "http://127.0.0.1:8787"})

    return asyncio.run(run())


@pytest.mark.parametrize("path, body", [
    ("/api/agent-connect/open-app", {"app": "claude", "attempt": "abc123"}),
    ("/api/agent-connect/connect", {"agent": "claude"}),
    ("/api/agent-connect/ask-open", {"app": "claude", "target": "shop"}),
    ("/api/agent-connect/discover", {"agent": "claude"}),
])
def test_port_forwarded_requests_cant_start_or_configure_local_apps(path, body):
    response = _post(path, body)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "unavailable_in_server_mode"


def test_desktop_rdst_still_reaches_the_local_app_check(monkeypatch):
    monkeypatch.delenv("RDST_SERVER")
    server_mode.require_local_apps()


def test_server_mode_hides_the_dev_keyring_reset():
    from shared.api.routes import dev as dev_routes

    app = FastAPI()
    server_mode.install(app)
    app.include_router(dev_routes.router, prefix="/api")

    async def run() -> Response:
        transport = ASGITransport(app=app, client=("127.0.0.1", 51000))
        async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8787") as http:
            return await http.post("/api/dev/clear-keyring", headers={
                "Authorization": f"Bearer {TOKEN}", "Origin": "http://127.0.0.1:8787"})

    assert asyncio.run(run()).status_code == 404


def test_agent_tools_refuse_server_file_reads_in_server_mode(monkeypatch, tmp_path):
    import pytest

    from features.agent_connect import discover, tools
    from shared import server_mode

    env = tmp_path / "app.env"
    env.write_text("DATABASE_URL=postgresql://db:5432/app\n")
    monkeypatch.setattr(discover, "rdst_path", lambda path: path)
    monkeypatch.setattr(server_mode, "enabled", lambda: True)
    with pytest.raises(tools.ToolError, match="runs as a server"):
        tools._read_env_file(str(env), "DATABASE_URL")
    monkeypatch.setattr(server_mode, "enabled", lambda: False)
    assert tools._read_env_file(str(env), "DATABASE_URL").startswith("postgresql://")

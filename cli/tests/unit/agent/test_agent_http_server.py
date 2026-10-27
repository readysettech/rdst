"""Security contract for `rdst agent serve`.

The agent server returns query rows, so a web page the user has open must not be able to
drive it: a Host allowlist against DNS rebinding, CORS only for --allow-origin origins, and
browser requests from other sites refused. Scripts and curl send no Origin and work as they
always did; a bearer token is required only when the user configures one.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from features.agent import http_server  # noqa: E402
from features.agent.config import AgentConfig  # noqa: E402

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class _FakeRuntime:
    def __init__(self, config):
        self.questions: list[str] = []

    def ask(self, question: str):
        self.questions.append(question)
        return SimpleNamespace(to_dict=lambda: {"success": True, "rows": [[1]]})

    def get_schema_summary(self):
        return {"tables": ["users"], "source": "database"}


@pytest.fixture
async def make_client(monkeypatch):
    monkeypatch.setattr(http_server, "AgentRuntime", _FakeRuntime)
    clients: list[TestClient] = []

    async def make(**kwargs) -> tuple[TestClient, http_server.AgentHTTPServer]:
        server = http_server.AgentHTTPServer(AgentConfig(name="sales", target="db"), **kwargs)
        client = TestClient(TestServer(server.app, host="127.0.0.1"))
        await client.start_server()
        clients.append(client)
        return client, server

    yield make
    for client in clients:
        await client.close()


async def test_curl_style_form_post_without_origin_works(make_client):
    """`curl -d '{...}'` sends application/x-www-form-urlencoded and no Origin."""
    client, server = await make_client()
    response = await client.post(
        "/ask", data='{"question": "How many users?"}',
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert response.status == 200
    assert (await response.json()) == {"success": True, "rows": [[1]]}
    assert server.runtime.questions == ["How many users?"]
    assert "Access-Control-Allow-Origin" not in response.headers


async def test_no_token_is_required_or_generated_by_default(make_client):
    client, server = await make_client()
    assert server.token is None
    assert (await client.get("/schema")).status == 200


async def test_cors_simple_cross_site_post_is_refused(make_client):
    """A page on another site can send text/plain without a preflight; it must not run."""
    client, server = await make_client()
    response = await client.post(
        "/ask", data='{"question": "q"}',
        headers={"Content-Type": "text/plain", "Origin": "https://evil.example"})
    assert response.status == 403
    assert "Access-Control-Allow-Origin" not in response.headers
    assert server.runtime.questions == []


@pytest.mark.parametrize("headers", [
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"},
    {"Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "cors"},
])
async def test_other_browser_requests_from_other_sites_are_refused(make_client, headers):
    client, server = await make_client()
    assert (await client.post("/ask", json={"question": "q"}, headers=headers)).status == 403
    assert (await client.get("/schema", headers=headers)).status == 403
    assert server.runtime.questions == []


async def test_browser_navigation_to_health_works(make_client):
    client, _ = await make_client()
    response = await client.get("/health", headers={
        "Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate"})
    assert response.status == 200


async def test_configured_token_is_enforced(make_client):
    client, server = await make_client(token=TOKEN)
    assert (await client.post("/ask", json={"question": "q"})).status == 401
    wrong = await client.post("/ask", json={"question": "q"},
                              headers={"Authorization": "Bearer nope"})
    assert wrong.status == 401
    ok = await client.post("/ask", data='{"question": "q"}', headers=AUTH)
    assert ok.status == 200
    assert server.runtime.questions == ["q"]
    assert (await client.get("/health")).status == 200


async def test_rebinding_host_is_rejected(make_client):
    client, server = await make_client()
    response = await client.post(
        "/ask", json={"question": "q"}, headers={"Host": "rebind.evil.example:8080"})
    assert response.status == 421
    health = await client.get("/health", headers={"Host": "rebind.evil.example:8080"})
    assert health.status == 421
    assert server.runtime.questions == []


@pytest.mark.parametrize("host", ["localhost:8080", "127.0.0.1:8080", "[::1]:8080"])
async def test_loopback_hosts_are_accepted(make_client, host):
    client, _ = await make_client()
    response = await client.get("/schema", headers={"Host": host})
    assert response.status == 200
    assert (await response.json())["tables"] == ["users"]


async def test_preflight_from_other_origins_is_refused(make_client):
    client, _ = await make_client()
    preflight = await client.options("/ask", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert preflight.status == 403
    assert "Access-Control-Allow-Origin" not in preflight.headers


async def test_configured_origin_gets_cors(make_client):
    client, _ = await make_client(token=TOKEN, allowed_origins=["https://app.example"])
    preflight = await client.options("/ask", headers={
        "Origin": "https://app.example", "Access-Control-Request-Method": "POST",
        "Sec-Fetch-Site": "cross-site"})
    assert preflight.status == 204
    assert preflight.headers["Access-Control-Allow-Origin"] == "https://app.example"
    assert "Authorization" in preflight.headers["Access-Control-Allow-Headers"]
    response = await client.post("/ask", json={"question": "q"}, headers={
        **AUTH, "Origin": "https://app.example", "Sec-Fetch-Site": "cross-site"})
    assert response.status == 200
    assert response.headers["Access-Control-Allow-Origin"] == "https://app.example"
    other = await client.post("/ask", json={"question": "q"},
                              headers={**AUTH, "Origin": "https://evil.example"})
    assert other.status == 403
    assert "Access-Control-Allow-Origin" not in other.headers


def test_startup_line_shows_bound_address(monkeypatch, capsys):
    monkeypatch.setattr(http_server, "AgentRuntime", _FakeRuntime)
    server = http_server.AgentHTTPServer(AgentConfig(name="sales", target="db"))
    server._print_startup("======== Running on http://127.0.0.1:8080 ========\n"
                          "(Press CTRL+C to quit)")
    assert capsys.readouterr().out == "Agent 'sales' running on http://127.0.0.1:8080\n"


@pytest.mark.parametrize("token", [None, "from-env"])
def test_serve_banner_matches_the_token_setting(monkeypatch, capsys, token):
    from features.agent.cli.command import AgentCommand

    ran = []
    monkeypatch.setattr(http_server, "AgentRuntime", _FakeRuntime)
    monkeypatch.setattr(http_server.AgentHTTPServer, "run",
                        lambda self, host, port: ran.append((host, port, self.token)))
    monkeypatch.setattr(AgentCommand, "_get_manager", lambda self: type(
        "M", (), {"get": staticmethod(lambda name: AgentConfig(name=name, target="db"))})())
    monkeypatch.setattr(AgentCommand, "_require_claude_access", lambda self: None)
    if token:
        monkeypatch.setenv(http_server.AGENT_TOKEN_ENV, token)
    else:
        monkeypatch.delenv(http_server.AGENT_TOKEN_ENV, raising=False)

    assert AgentCommand().execute("serve", name="sales", port=8099).ok
    out = capsys.readouterr().out
    assert ran == [("127.0.0.1", 8099, token)]
    assert "http://127.0.0.1:8099/ask" in out and "localhost" not in out
    assert "from-env" not in out
    if token:
        assert "require the configured bearer token" in out and "Bearer <token>" in out
    else:
        assert "No token required" in out and "Authorization" not in out

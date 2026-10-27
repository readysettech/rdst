"""DNS-rebinding and per-launch token protection for the RDST web API.

A rebinding page at http://rebind.evil.example:8787 resolves to 127.0.0.1 and sends Host and
Origin for its own domain from a loopback peer. The Host allowlist must turn it away before
any route, static file, or /mcp runs, while every legitimate spelling of the server's address
keeps working.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient, Response

from shared.api.guards import (
    TOKEN_EXEMPT_CALLBACKS,
    ApiTokenMiddleware,
    HostAllowlistMiddleware,
    host_name,
    is_allowed_host,
    normalize_allowed_hosts,
)

LOOPBACK_CLIENT = ("127.0.0.1", 54321)
REMOTE_CLIENT = ("203.0.113.10", 54321)
REBIND = "rebind.evil.example:8787"
# A path no router serves: 404 proves the request got past the middlewares.
UNROUTED = "/api/__unrouted__"


# Every variable the Host allowlist and token check read; a value left by another test (or a
# real Codespace) would change which names are allowed.
SERVER_ENV = ("RDST_WEB_HOST", "RDST_WEB_PORT", "RDST_WEB_ALLOWED_HOSTS", "RDST_API_TOKEN",
              "CODESPACE_NAME", "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")


@pytest.fixture
def web_env(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    for name in SERVER_ENV:
        # setenv first so teardown restores the prior state even when the name was unset.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    return monkeypatch


def _send(method: str, path: str, *, base_url: str = "http://127.0.0.1:8787",
          client=LOOPBACK_CLIENT, headers: dict | None = None, **kwargs) -> Response:
    from shared.api.app import create_app

    async def send() -> Response:
        transport = ASGITransport(app=create_app(), client=client)
        async with AsyncClient(transport=transport, base_url=base_url) as http:
            return await http.request(method, path, headers=headers, **kwargs)

    return asyncio.run(send())


@pytest.mark.parametrize(("value", "name"), [
    ("localhost:8787", "localhost"), ("LOCALHOST.", "localhost"), ("[::1]:8787", "::1"),
    ("127.0.0.1", "127.0.0.1"), ("rdst.internal:80", "rdst.internal"),
    ("[::1", None), ("a:b:c", None), ("", None),
])
def test_host_name_parsing(value, name):
    assert host_name(value) == name


def test_allowed_host_normalization():
    names = ["rdst.internal:8787", "::1", "[fe80::1]:80", "0.0.0.0", "", " Box.LAN "]
    assert normalize_allowed_hosts(names) == {"rdst.internal", "::1", "fe80::1", "box.lan"}


def test_is_allowed_host_rules():
    allowed = frozenset({"rdst.internal"})
    assert is_allowed_host("localhost:5173", allowed)
    assert is_allowed_host("127.0.0.2", allowed)
    assert is_allowed_host("rdst.internal:8787", allowed)
    assert is_allowed_host("192.168.1.5:8787", allowed, server_host="192.168.1.5")
    assert is_allowed_host(None, allowed)
    assert not is_allowed_host(REBIND, allowed, server_host="127.0.0.1")
    assert not is_allowed_host("127.0.0.1.nip.io", allowed, server_host="127.0.0.1")
    assert not is_allowed_host("localhost.evil.example", allowed)


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/api/query-registry"), ("POST", "/api/env/set"), ("POST", "/api/ask"),
    ("GET", "/api/configure/targets"), ("POST", "/mcp"), ("GET", "/health"), ("GET", "/"),
])
def test_rebinding_host_is_rejected_everywhere(web_env, method, path):
    """The reported attack: Host and Origin both name the attacker, the peer is loopback."""
    response = _send(method, path, json={},
                     headers={"host": REBIND, "origin": f"http://{REBIND}"})

    assert response.status_code == 421
    assert "--allowed-host rebind.evil.example" in response.text


@pytest.mark.parametrize("host", ["127.0.0.1:8787", "localhost:8787", "[::1]:8787",
                                  "localhost:3001", "localhost"])
def test_loopback_hosts_are_accepted(web_env, host):
    assert _send("GET", "/health", headers={"host": host}).status_code == 200
    assert _send("GET", UNROUTED, headers={"host": host}).status_code == 404


def test_cli_style_request_is_accepted(web_env):
    """`rdst tunnel` and readiness probes call http://127.0.0.1:<port> with no Origin."""
    response = _send("GET", "/api/tunnel/list")
    assert response.status_code != 421


def test_non_loopback_bind_accepts_bound_and_allowed_names(web_env):
    web_env.setenv("RDST_WEB_HOST", "0.0.0.0")
    web_env.setenv("RDST_WEB_ALLOWED_HOSTS", "rdst.internal,box.lan:8787")
    lan = {"base_url": "http://192.168.1.5:8787", "client": REMOTE_CLIENT}

    assert _send("GET", "/health", headers={"host": "rdst.internal:8787"}, **lan).status_code == 200
    assert _send("GET", "/health", headers={"host": "box.lan:8787"}, **lan).status_code == 200
    # The machine's IP, as typed into the browser, is the address the connection reached.
    assert _send("GET", "/health", **lan).status_code == 200
    assert _send("GET", "/health", headers={"host": REBIND}, **lan).status_code == 421


def test_bind_host_name_is_accepted(web_env):
    web_env.setenv("RDST_WEB_HOST", "box.lan")
    response = _send("GET", "/health", base_url="http://10.0.0.7:8787",
                     headers={"host": "box.lan:8787"})
    assert response.status_code == 200


def test_token_is_required_on_api_when_configured(web_env):
    web_env.setenv("RDST_API_TOKEN", "s3cret-token")

    missing = _send("GET", UNROUTED)
    wrong = _send("GET", UNROUTED, headers={"x-rdst-token": "nope"})
    right = _send("GET", UNROUTED, headers={"x-rdst-token": "s3cret-token"})

    assert missing.status_code == 401 and wrong.status_code == 401
    assert "s3cret-token" not in missing.text
    assert right.status_code == 404


def test_token_leaves_health_mcp_and_preflight_alone(web_env):
    web_env.setenv("RDST_API_TOKEN", "s3cret-token")

    assert _send("GET", "/health").status_code == 200
    assert _send("POST", "/mcp", json={}).status_code != 401
    preflight = _send("OPTIONS", "/api/ask", headers={
        "origin": "http://localhost:3001", "access-control-request-method": "POST",
        "access-control-request-headers": "x-rdst-token"})
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:3001"


def test_without_token_env_api_stays_open_to_loopback(web_env):
    assert _send("GET", UNROUTED).status_code == 404


def test_rebinding_check_runs_before_token(web_env):
    web_env.setenv("RDST_API_TOKEN", "s3cret-token")
    response = _send("GET", UNROUTED, headers={"host": REBIND, "x-rdst-token": "s3cret-token"})
    assert response.status_code == 421


@pytest.mark.parametrize(("middleware", "kwargs", "headers"), [
    (HostAllowlistMiddleware, {"allowed_hosts": frozenset()}, [(b"host", REBIND.encode())]),
    (ApiTokenMiddleware, {"token": "t"}, [(b"host", b"127.0.0.1")]),
])
def test_websockets_are_closed(middleware, kwargs, headers):
    sent: list[dict] = []

    async def inner(scope, receive, send):
        raise AssertionError("request reached the app")

    async def send(message):
        sent.append(message)

    scope = {"type": "websocket", "path": "/api/stream", "headers": headers,
             "server": ("127.0.0.1", 8787)}
    asyncio.run(middleware(inner, **kwargs)(scope, None, send))

    assert sent == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.parametrize("path", sorted(TOKEN_EXEMPT_CALLBACKS))
def test_browser_redirect_callbacks_skip_the_token(web_env, path):
    """An external browser returns to these by redirect and can't carry X-RDST-Token."""
    web_env.setenv("RDST_API_TOKEN", "s3cret-token")

    landed = _send("GET", path, params={"login_id": "unknown", "provider": "supabase"})
    posted = _send("POST", path, json={})
    rebound = _send("GET", path, headers={"host": REBIND})

    assert landed.status_code != 401
    assert posted.status_code == 401
    assert rebound.status_code == 421


def test_token_exemption_is_exact(web_env):
    web_env.setenv("RDST_API_TOKEN", "s3cret-token")
    for path in ("/api/account/oauth/callback/", "/api/account/oauth", "/api/providers/oauth"):
        assert _send("GET", path).status_code == 401, path


def test_callback_websocket_still_needs_the_token():
    sent: list[dict] = []

    async def inner(scope, receive, send):
        raise AssertionError("request reached the app")

    async def send(message):
        sent.append(message)

    scope = {"type": "websocket", "path": "/api/account/oauth/callback", "headers": []}
    asyncio.run(ApiTokenMiddleware(inner, token="t")(scope, None, send))

    assert sent == [{"type": "websocket.close", "code": 1008}]


def test_app_keeps_the_token_for_the_discovery_file(web_env):
    from shared.api.app import create_app

    assert create_app().state.api_token is None
    web_env.setenv("RDST_API_TOKEN", "s3cret-token")
    assert create_app().state.api_token == "s3cret-token"


def test_rejection_names_the_host_and_how_to_allow_it(web_env):
    response = _send("GET", "/", headers={"host": "rdst.example.com"})

    assert response.status_code == 421
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert '"rdst.example.com"' in response.text
    assert "rdst web --allowed-host rdst.example.com" in response.text
    assert "RDST_WEB_ALLOWED_HOSTS=rdst.example.com" in response.text


@pytest.mark.parametrize("host", ["<script>alert(1)</script>.evil", 'a"b.evil:80', "x y.evil",
                                  "a" * 300])
def test_rejection_never_reflects_unusual_host_text(web_env, host):
    from shared.api.guards import _printable_host

    assert _printable_host(host) is None
    response = _send("GET", "/", headers={"host": host})
    assert response.status_code == 421
    assert "--allowed-host NAME" in response.text
    assert "<" not in response.text and '"a' not in response.text and "aaaa" not in response.text


def test_malformed_host_gets_a_generic_message():
    from shared.api.guards import _printable_host, host_rejection_text

    assert _printable_host("[::1") is None
    assert "--allowed-host NAME" in host_rejection_text(None)


def test_each_rejected_host_is_logged_once(web_env, caplog):
    from shared.api.app import create_app

    async def send_all() -> None:
        transport = ASGITransport(app=create_app(), client=LOOPBACK_CLIENT)
        async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8787") as http:
            for host in ("rdst.example.com", "rdst.example.com:443", "other.example"):
                await http.get("/health", headers={"host": host})

    with caplog.at_level("WARNING", logger="uvicorn.error"):
        asyncio.run(send_all())

    logged = [r.getMessage() for r in caplog.records if r.name == "uvicorn.error"]
    assert len(logged) == 2
    assert "'rdst.example.com'" in logged[0] and "--allowed-host rdst.example.com" in logged[0]
    assert "RDST_WEB_ALLOWED_HOSTS" in logged[0]


def test_rejection_log_is_bounded(caplog):
    from shared.api import guards

    async def inner(scope, receive, send):
        raise AssertionError("request reached the app")

    async def send(message):
        pass

    middleware = HostAllowlistMiddleware(inner, allowed_hosts=frozenset())

    async def flood() -> None:
        for i in range(guards._MAX_LOGGED_HOSTS + 20):
            scope = {"type": "http", "headers": [(b"host", f"h{i}.evil".encode())],
                     "server": ("127.0.0.1", 8787)}
            await middleware(scope, None, send)

    with caplog.at_level("WARNING", logger="uvicorn.error"):
        asyncio.run(flood())
    assert len(caplog.records) == guards._MAX_LOGGED_HOSTS


def test_reverse_proxy_name_works_once_allowed(web_env):
    """systemd + Caddy/nginx: the proxy on this host forwards Host: rdst.example.com."""
    # The connection reaches 127.0.0.1; the test transport reports base_url's host as the
    # server address, so naming the proxy there would make the address rule accept it.
    proxied = {"base_url": "https://127.0.0.1", "headers": {
        "host": "rdst.example.com", "origin": "https://rdst.example.com"}}

    assert _send("GET", "/api/init/status", **proxied).status_code == 421
    web_env.setenv("RDST_WEB_ALLOWED_HOSTS", "rdst.example.com")
    assert _send("GET", "/health", **proxied).status_code == 200
    assert _send("GET", "/api/init/status", **proxied).status_code == 200


def test_codespaces_forwarded_host_is_allowed_automatically(web_env):
    web_env.setenv("CODESPACE_NAME", "fuzzy-train-5g7")
    web_env.setenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
    web_env.setenv("RDST_WEB_PORT", "8787")
    forwarded = "fuzzy-train-5g7-8787.app.github.dev"

    assert _send("GET", "/health", headers={"host": forwarded}).status_code == 200
    assert _send("GET", "/health", headers={
        "host": "fuzzy-train-5g7-9999.app.github.dev"}).status_code == 421
    assert _send("GET", "/health", headers={"host": "evil.app.github.dev"}).status_code == 421


@pytest.mark.parametrize("missing", ["CODESPACE_NAME", "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN",
                                     "RDST_WEB_PORT"])
def test_codespaces_host_needs_all_its_settings(web_env, missing):
    from shared.api.guards import codespaces_forwarded_host

    values = {"CODESPACE_NAME": "cs", "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev",
              "RDST_WEB_PORT": "8787"}
    for name, value in values.items():
        web_env.setenv(name, value)
    assert codespaces_forwarded_host() == "cs-8787.app.github.dev"
    web_env.delenv(missing)
    assert codespaces_forwarded_host() is None


def test_vscode_port_forward_on_another_local_port_works(web_env):
    """VS Code Remote forwards a local port; the browser sends Host: localhost:<that port>."""
    headers = {"host": "localhost:61234", "origin": "http://localhost:61234"}
    assert _send("GET", "/api/init/status", headers=headers).status_code == 200


def test_docker_port_mapping_passes_the_host_check(web_env):
    """`docker run -p 127.0.0.1:8787:8787`: Host is localhost, the peer is the bridge gateway."""
    web_env.setenv("RDST_WEB_HOST", "0.0.0.0")
    response = _send("GET", "/health", base_url="http://172.17.0.2:8787",
                     client=("172.17.0.1", 40000), headers={"host": "localhost:8787"})
    assert response.status_code == 200

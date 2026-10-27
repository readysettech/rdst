"""Provider sign-in starts: where the browser runs and where the broker sends it back.

The refusal for a browser on another machine follows the browser's Origin, not the TCP peer:
a Docker port mapping makes the peer the bridge gateway while the browser is local. The
completion URL keeps the exact loopback origin the browser used, IPv6 and port included.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from features.providers.api import routes

DOCKER_PEER = ("172.17.0.1", 40000)
LOOPBACK_PEER = ("127.0.0.1", 40000)
REMOTE_PEER = ("192.168.1.9", 40000)


def _request(peer, origin: str | None, host: str = "localhost:8787") -> Request:
    headers = [(b"host", host.encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    return Request({"type": "http", "method": "POST", "scheme": "http", "path": "/",
                    "headers": headers, "client": peer, "server": ("127.0.0.1", 8787)})


@pytest.mark.parametrize(("peer", "origin"), [
    (DOCKER_PEER, "http://localhost:8787"),
    (DOCKER_PEER, "http://127.0.0.1:8787"),
    (LOOPBACK_PEER, "http://[::1]:8787"),
    (LOOPBACK_PEER, "http://localhost:3001"),
    (LOOPBACK_PEER, None),
])
def test_local_browser_is_not_refused(peer, origin):
    assert routes._remote_browser_refusal(_request(peer, origin), "supabase") is None


@pytest.mark.parametrize(("peer", "origin"), [
    (REMOTE_PEER, "http://192.168.1.5:8787"),
    (LOOPBACK_PEER, "https://rdst.example.com"),
    (REMOTE_PEER, None),
    (LOOPBACK_PEER, "null"),
])
def test_browser_elsewhere_is_refused(peer, origin):
    response = routes._remote_browser_refusal(_request(peer, origin), "digitalocean")
    assert response is not None and response.status_code == 409


@pytest.mark.parametrize(("origin", "expected"), [
    ("http://[::1]:8787", "http://[::1]:8787"),
    ("http://127.0.0.1:8787", "http://127.0.0.1:8787"),
    ("http://localhost:8787", "http://localhost:8787"),
    ("http://localhost", "http://localhost"),
    ("https://localhost:9443", "https://localhost:9443"),
])
def test_completion_url_keeps_the_browser_origin(origin, expected):
    url = routes._provider_completion_url(_request(LOOPBACK_PEER, origin), "supabase")
    assert url == f"{expected}/api/providers/oauth-return?provider=supabase"


def test_completion_url_falls_back_to_the_request_address():
    request = _request(LOOPBACK_PEER, None, host="[::1]:8787")
    assert routes._local_origin(request) == "http://[::1]:8787"


@pytest.mark.parametrize("origin", ["http://user@localhost:8787", "http://localhost:99999"])
def test_unusual_origins_never_leak_into_the_completion_url(origin):
    url = routes._provider_completion_url(_request(LOOPBACK_PEER, origin), "supabase")
    assert "user@" not in url and "99999" not in url


@pytest.mark.parametrize(("peer", "base_url", "origin"), [
    (("::1", 40000), "http://[::1]:8787", "http://[::1]:8787"),
    (LOOPBACK_PEER, "http://127.0.0.1:8787", "http://127.0.0.1:8787"),
    (LOOPBACK_PEER, "http://localhost:8787", "http://localhost:8787"),
])
def test_start_route_sends_the_loopback_completion_url(monkeypatch, peer, base_url, origin):
    started = []

    async def start(provider, *args):
        started.append((provider, args[-1]))
        return {"login_id": "login-1"}

    monkeypatch.setattr(routes, "_start_provider_login", start)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")

    async def send():
        transport = ASGITransport(app=app, client=peer)
        async with AsyncClient(transport=transport, base_url=base_url) as http:
            return await http.post("/api/providers/digitalocean-login", headers={"origin": origin})

    response = asyncio.run(send())
    assert response.status_code == 200, response.text
    assert started == [("digitalocean",
                        f"{origin}/api/providers/oauth-return?provider=digitalocean")]

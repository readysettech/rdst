"""Contract tests for the local-or-same-origin guard on the RDST web API.

`rdst web` binds a loopback port with no authentication, so a page the user
happens to have open in the same browser can reach every route. The guard's
job is to let the RDST UI through on loopback or a configured remote host while
rejecting a request another web page made on the user's behalf.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient, Response

from shared.api.guards import require_local_request

LOOPBACK_CLIENT = ("127.0.0.1", 54321)
REMOTE_CLIENT = ("203.0.113.10", 54321)


def _app() -> FastAPI:
    app = FastAPI()

    @app.post("/api/guarded")
    async def guarded(request: Request) -> dict[str, bool]:
        require_local_request(request)
        return {"ok": True}

    return app


def _post(base_url: str, client: tuple[str, int], **kwargs) -> Response:
    async def send() -> Response:
        transport = ASGITransport(app=_app(), client=client)
        async with AsyncClient(transport=transport, base_url=base_url) as http:
            return await http.post("/api/guarded", **kwargs)

    return asyncio.run(send())


def test_loopback_client_without_origin_passes():
    """The CLI, curl, and the desktop readiness probe send no Origin."""
    response = _post("http://127.0.0.1:8787", LOOPBACK_CLIENT)

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_loopback_client_with_cross_site_origin_is_forbidden():
    """A page on the web driving the local API is the attack this stops."""
    response = _post(
        "http://127.0.0.1:8787",
        LOOPBACK_CLIENT,
        headers={"origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Origin/Referer host mismatch"


def test_loopback_client_with_cross_site_referer_is_forbidden():
    response = _post(
        "http://127.0.0.1:8787",
        LOOPBACK_CLIENT,
        headers={"referer": "https://evil.example/post/1"},
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("base_url", "origin"),
    [
        # Vite and desktop servers can serve the page on another local port.
        ("http://localhost:8787", "http://localhost:3001"),
        # Desktop shell: static server and Python API on different ports.
        ("http://127.0.0.1:51733", "http://127.0.0.1:51732"),
        ("http://localhost:8787", "http://localhost:8787"),
    ],
)
def test_loopback_origin_on_another_port_passes(base_url, origin):
    response = _post(base_url, LOOPBACK_CLIENT, headers={"origin": origin})

    assert response.status_code == 200


def test_loopback_origin_with_different_host_spelling_is_forbidden():
    response = _post(
        "http://127.0.0.1:8787",
        LOOPBACK_CLIENT,
        headers={"origin": "http://localhost:8787"},
    )

    assert response.status_code == 403


def test_ipv6_mapped_loopback_client_passes():
    response = _post("http://127.0.0.1:8787", ("::ffff:127.0.0.1", 54321))

    assert response.status_code == 200


def test_remote_client_without_browser_source_is_forbidden():
    """Headerless remote clients cannot drive the unauthenticated web API."""
    response = _post("http://192.168.56.10:8787", REMOTE_CLIENT)

    assert response.status_code == 403
    assert "same-origin Origin or Referer" in response.json()["detail"]


def test_remote_client_cannot_claim_loopback_origin():
    response = _post(
        "http://127.0.0.1:8787",
        REMOTE_CLIENT,
        headers={"origin": "http://127.0.0.1:8787"},
    )

    assert response.status_code == 403


@pytest.mark.parametrize("source_header", ["origin", "referer"])
def test_remote_same_origin_browser_passes(source_header):
    headers = {
        source_header: (
            "http://192.168.56.10:8787"
            if source_header == "origin"
            else "http://192.168.56.10:8787/settings"
        )
    }

    response = _post("http://192.168.56.10:8787", REMOTE_CLIENT, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://192.168.56.11:8787",
        "http://192.168.56.10:9999",
        "https://192.168.56.10:8787",
        "null",
    ],
)
def test_remote_cross_origin_browser_is_forbidden(origin):
    response = _post(
        "http://192.168.56.10:8787",
        REMOTE_CLIENT,
        headers={"origin": origin},
    )

    assert response.status_code == 403
    assert "does not match" in response.json()["detail"]


def test_non_loopback_host_header_with_loopback_origin_is_forbidden():
    """A DNS name resolving to loopback must not launder a foreign Host."""
    response = _post(
        "http://rdst.evil.example",
        LOOPBACK_CLIENT,
        headers={"origin": "http://localhost:3001"},
    )

    assert response.status_code == 403


# Routes that spend money, touch the database, or rewrite the semantic-layer
# YAML. Each must reject a request another page made, so this list is the
# contract: a new state-changing route on these routers belongs here.
GUARDED_WRITE_ROUTES = [
    ("POST", "/api/analyze"),
    ("POST", "/api/analyze/quick"),
    ("POST", "/api/semantic-layer/refresh"),
    ("POST", "/api/semantic-layer/profile"),
    ("POST", "/api/semantic-layer/init"),
    ("POST", "/api/semantic-layer/init/stream"),
    ("DELETE", "/api/semantic-layer"),
    ("POST", "/api/semantic-layer/table"),
    ("POST", "/api/semantic-layer/column"),
    ("POST", "/api/semantic-layer/enum"),
    ("POST", "/api/semantic-layer/terminology"),
    ("POST", "/api/semantic-layer/relationship"),
    ("POST", "/api/semantic-layer/metric"),
    ("POST", "/api/semantic-layer/annotate"),
    ("POST", "/api/semantic-layer/annotation-runs"),
]

# One body carrying every required field on these routers' request models.
# Pydantic ignores the extras, so a single payload clears validation
# everywhere and the guard is what the assertion sees.
WRITE_BODY = {
    "target": "imdb",
    "query": "SELECT 1",
    "table_name": "movies",
    "column_name": "title",
    "description": "d",
    "enum_values": {},
    "term": "t",
    "definition": "d",
    "sql_pattern": "p",
    "source_table": "movies",
    "target_table": "actors",
    "join_pattern": "j",
    "name": "m",
    "sql": "SELECT 1",
}


def _write_route_app() -> FastAPI:
    from features.analyze.api import routes as analyze_routes
    from features.schema.api import semantic_layer_routes
    from shared.api.target_guard import (
        TargetGuard,
        require_target,
        require_target_body,
    )

    app = FastAPI()
    app.include_router(analyze_routes.router, prefix="/api")
    app.include_router(semantic_layer_routes.router, prefix="/api")

    async def target_guard() -> TargetGuard:
        return TargetGuard("imdb", {"engine": "postgresql"}, "postgresql")

    app.dependency_overrides[require_target] = target_guard
    app.dependency_overrides[require_target_body] = target_guard
    return app


@pytest.mark.parametrize(("method", "path"), GUARDED_WRITE_ROUTES)
def test_cross_site_page_cannot_drive_write_routes(method, path):
    async def send() -> Response:
        transport = ASGITransport(app=_write_route_app(), client=LOOPBACK_CLIENT)
        async with AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8787"
        ) as http:
            return await http.request(
                method,
                path,
                json=WRITE_BODY,
                params={"target": "imdb"},
                headers={"origin": "https://evil.example"},
            )

    response = asyncio.run(send())

    assert response.status_code == 403, response.text

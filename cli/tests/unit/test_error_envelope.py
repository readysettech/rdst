"""Unit tests for the shared HTTP error envelope (B7/T24).

Every API failure must surface one ``{code, message, detail}`` shape, and an
unexpected server error must never echo ``str(exc)`` as the user-facing message.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient, Response
from pydantic import BaseModel

from shared.api.app import create_app, register_error_handlers


class _Client:
    def __init__(self, app: FastAPI):
        self.app = app

    def request(self, method: str, path: str, **kwargs) -> Response:
        async def send() -> Response:
            async with AsyncClient(
                transport=ASGITransport(
                    app=self.app, raise_app_exceptions=False
                ),
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def get(self, path: str, **kwargs) -> Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> Response:
        return self.request("POST", path, **kwargs)


def _app_with_handlers() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    class _Body(BaseModel):
        name: str

    @app.get("/boom")
    async def _boom() -> None:
        # A raw exception whose text must NOT reach the client. The marker is a
        # synthetic, obviously-fake canary (not a real secret) chosen so it does
        # not trip credential scanners on the test source.
        raise ValueError("synthetic-leak-canary-value leaked into str(e)")

    @app.get("/gone")
    async def _gone() -> None:
        raise HTTPException(status_code=404, detail="No such target")

    @app.post("/need-body")
    async def _need_body(body: _Body) -> dict:
        return {"name": body.name}

    return app


def test_unhandled_exception_returns_envelope_without_leaking_str_e() -> None:
    client = _Client(_app_with_handlers())
    response = client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert set(body) == {"code", "message", "detail"}
    assert body["code"] == "internal_error"
    # Neither the raw exception text nor the class name reaches the client —
    # several consumers render ``detail`` directly, so it must stay humane.
    assert "synthetic-leak-canary-value" not in response.text
    assert "ValueError" not in response.text
    assert body["message"] == "An unexpected server error occurred."
    assert body["detail"] == "An unexpected server error occurred."


def test_http_exception_maps_to_envelope() -> None:
    client = _Client(_app_with_handlers())
    response = client.get("/gone")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert body["message"] == "No such target"
    # ``detail`` is preserved for back-compat with existing consumers.
    assert body["detail"] == "No such target"


def test_validation_error_maps_to_envelope() -> None:
    client = _Client(_app_with_handlers())
    response = client.post("/need-body", json={})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid_request"
    assert isinstance(body["message"], str) and body["message"]
    # The field errors ride in ``detail`` (array), preserving the FastAPI shape.
    assert isinstance(body["detail"], list)


def test_create_app_wires_the_envelope_for_unknown_api_routes() -> None:
    client = _Client(create_app())
    response = client.get("/api/definitely-not-a-real-route")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert "message" in body and body["message"]

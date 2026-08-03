"""Fixtures for the fleet router tests.

These suites are synchronous: they drive the fleet router through a real ASGI
transport and block on each call, so the mocked boto boundaries stay easy to
assert on.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from features.providers.api.routes import router


def make_response(status_code: int = 200, payload: Any = None) -> MagicMock:
    """A stand-in for a `requests` response carrying a JSON payload."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.text = ""
    return response


def recording_set_secret(written: list[tuple[str, str]]) -> Callable:
    """A `SecretStoreService.set_secret` that records instead of persisting."""

    def fake_set_secret(self, name, value, persist=True):
        written.append((name, value))
        return {"persisted": True, "session_only": False, "message": "ok"}

    return fake_set_secret


class BrokerStub:
    """Stub for ``requests.post`` against a keyservice OAuth broker.

    The start/result/refresh flow lives in the shared ``provider_oauth`` base,
    so one stub shape serves every provider; callers pass the provider slug and
    the opaque token strings their assertions expect.
    """

    def __init__(
        self,
        provider: str,
        *,
        access: str,
        refresh: str,
        refreshed: str,
        next_refresh: str,
    ):
        base = f"https://keyservice.test/oauth/{provider}"
        self.START_URL = f"{base}/start"
        self.RESULT_URL = f"{base}/result"
        self.REFRESH_URL = f"{base}/refresh"
        self.calls: list[tuple[str, dict]] = []
        self.start_response = make_response(
            200,
            {
                "login_id": "login-1",
                "authorize_url": f"{base}/authorize?login_id=login-1",
                "expires_in": 600,
            },
        )
        self.result_status = "pending"
        self.result_detail = None
        self.tokens = {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": 3600,
        }
        self.refresh_response = make_response(
            200,
            {
                "access_token": refreshed,
                "refresh_token": next_refresh,
                "expires_in": 3600,
            },
        )

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs.get("json") or {}))
        if url == self.START_URL:
            return self.start_response
        if url == self.RESULT_URL:
            if self.result_status == "ready":
                return make_response(200, {"status": "ready", "tokens": self.tokens})
            body = {"status": self.result_status}
            if self.result_detail:
                body["detail"] = self.result_detail
            return make_response(200, body)
        if url == self.REFRESH_URL:
            return self.refresh_response
        raise AssertionError(f"unexpected broker URL: {url}")


class FleetRouterClient:
    """Sync-over-async client for one request at a time against the router."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app

    def request(self, method: str, url: str, **kwargs):
        async def _send():
            transport = ASGITransport(app=self.app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(_send())

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)


def make_fleet_router_client() -> FleetRouterClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return FleetRouterClient(app)


@pytest.fixture
def fleet_router_client() -> FleetRouterClient:
    return make_fleet_router_client()


@pytest.fixture(autouse=True)
def disable_background_provider_identity(monkeypatch):
    """Provider tests opt into identity calls explicitly; never leak HTTP."""
    monkeypatch.setattr(
        "features.providers.identity.capture_provider_identity_async",
        lambda provider, token: None,
    )
    monkeypatch.setattr(
        "features.providers.identity.capture_aws_identity_async",
        lambda arn: None,
    )

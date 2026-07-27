"""Fixtures for the fleet router tests.

These suites are synchronous: they drive the fleet router through a real ASGI
transport and block on each call, so the mocked boto boundaries stay easy to
assert on.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from features.fleet.api.routes import router


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

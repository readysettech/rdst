#!/usr/bin/env python3
"""Integration tests for target password lock API behavior."""

from unittest.mock import Mock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from lib.api.app import create_app
from lib.api.routes.target_guard import TARGET_PASSWORD_REQUIRED_CODE


@pytest.fixture
def app():
    return create_app()


def _mock_config():
    cfg = Mock()
    cfg.load.return_value = None
    cfg.get_default.return_value = "prod"
    cfg.get.side_effect = lambda name: {
        "prod": {
            "engine": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "app",
            "user": "app",
            "password_env": "PROD_DB_PASSWORD",
        }
    }.get(name)
    return cfg


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "url", "payload"),
    [
        ("POST", "/api/analyze", {"query": "select 1", "target": "prod"}),
        ("POST", "/api/ask", {"question": "count users", "target": "prod"}),
        ("GET", "/api/schema?target=prod", None),
        ("GET", "/api/top?target=prod", None),
        ("POST", "/api/readyset/setup", {"target": "prod"}),
        (
            "POST",
            "/api/query-registry/benchmark",
            {"queries": ["select 1"], "target": "prod"},
        ),
    ],
)
async def test_target_bound_endpoints_return_423_when_password_missing(
    app, method, url, payload, monkeypatch
):
    monkeypatch.delenv("PROD_DB_PASSWORD", raising=False)

    with patch("lib.api.routes.target_guard.TargetsConfig", return_value=_mock_config()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.request(method, url, json=payload)

    assert response.status_code == 423
    detail = response.json().get("detail", {})
    assert detail.get("code") == TARGET_PASSWORD_REQUIRED_CODE
    assert detail.get("target") == "prod"
    assert detail.get("password_env") == "PROD_DB_PASSWORD"

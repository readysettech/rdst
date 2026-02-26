#!/usr/bin/env python3
"""Integration tests for env API endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import Mock, patch

from lib.api.app import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.mark.asyncio
async def test_get_env_requirements_returns_contract(app):
    mock_service = Mock()
    mock_service.secret_store.is_available.return_value = True
    mock_service.get_requirements.return_value = [
        {
            "kind": "target_password",
            "accepted_names": ["PROD_DB_PASSWORD"],
            "target": "prod",
            "satisfied": False,
            "source": "missing",
        },
        {
            "kind": "anthropic_api_key",
            "accepted_names": ["ANTHROPIC_API_KEY", "RDST_TRIAL_TOKEN"],
            "target": None,
            "satisfied": True,
            "source": "process_env",
        },
    ]

    with patch("lib.api.routes.env.EnvRequirementsService", return_value=mock_service):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/env/requirements")

    assert response.status_code == 200
    payload = response.json()
    assert payload["keyring_available"] is True
    assert len(payload["requirements"]) == 2
    assert payload["requirements"][0]["kind"] == "target_password"


@pytest.mark.asyncio
async def test_set_env_secret_persisted_success(app):
    mock_service = Mock()
    mock_service.get_allowed_secret_names.return_value = ["PROD_DB_PASSWORD"]
    mock_service.secret_store.set_secret.return_value = {
        "persisted": True,
        "session_only": False,
        "message": "Saved",
    }

    with patch("lib.api.routes.env.EnvRequirementsService", return_value=mock_service):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/env/set",
                json={"name": "PROD_DB_PASSWORD", "value": "secret", "persist": True},
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["persisted"] is True
    assert payload["session_only"] is False


@pytest.mark.asyncio
async def test_set_env_secret_session_only_fallback(app):
    mock_service = Mock()
    mock_service.get_allowed_secret_names.return_value = ["PROD_DB_PASSWORD"]
    mock_service.secret_store.set_secret.return_value = {
        "persisted": False,
        "session_only": True,
        "message": "Session only",
    }

    with patch("lib.api.routes.env.EnvRequirementsService", return_value=mock_service):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/env/set",
                json={"name": "PROD_DB_PASSWORD", "value": "secret", "persist": True},
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["persisted"] is False
    assert payload["session_only"] is True


@pytest.mark.asyncio
async def test_set_env_secret_rejects_non_allowlisted_name(app):
    mock_service = Mock()
    mock_service.get_allowed_secret_names.return_value = ["PROD_DB_PASSWORD"]

    with patch("lib.api.routes.env.EnvRequirementsService", return_value=mock_service):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/env/set",
                json={"name": "NOT_ALLOWED", "value": "secret", "persist": True},
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert "not allowed" in (payload.get("message") or "").lower()


@pytest.mark.asyncio
async def test_set_env_secret_rejects_mismatched_origin(app):
    mock_service = Mock()
    mock_service.get_allowed_secret_names.return_value = ["PROD_DB_PASSWORD"]

    with patch("lib.api.routes.env.EnvRequirementsService", return_value=mock_service):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8787") as client:
            response = await client.post(
                "/api/env/set",
                headers={"origin": "http://localhost:8787"},
                json={"name": "PROD_DB_PASSWORD", "value": "secret", "persist": True},
            )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_env_routes_reject_non_loopback_client(app):
    transport = ASGITransport(app=app, client=("203.0.113.10", 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/env/requirements")

    assert response.status_code == 403

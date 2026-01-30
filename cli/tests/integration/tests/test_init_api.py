#!/usr/bin/env python3
"""Integration tests for Init API endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch

from lib.api.app import create_app
from lib.services.types import InitCompleteEvent, InitStatus, InitValidationResult


@pytest.fixture
def app():
    """Create FastAPI app for testing."""
    return create_app()


async def _events(*items):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_init_status_endpoint(app):
    """GET /api/init/status returns onboarding state."""
    with patch("lib.api.routes.init.InitService") as mock_service_class:
        mock_service = mock_service_class.return_value
        status = InitStatus(
            initialized=False,
            targets=[
                {
                    "name": "prod",
                    "engine": "postgresql",
                    "has_password": True,
                    "is_default": True,
                }
            ],
            default_target="prod",
            llm_configured=True,
        )
        mock_service.get_status_events.return_value = _events(
            InitCompleteEvent(type="complete", success=True, status=status)
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/init/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["initialized"] is False
    assert payload["default_target"] == "prod"
    assert payload["llm_configured"] is True
    assert len(payload["targets"]) == 1
    assert payload["targets"][0]["name"] == "prod"


@pytest.mark.asyncio
async def test_init_validate_endpoint_with_specific_targets(app):
    """POST /api/init/validate validates specific target subset."""
    with patch("lib.api.routes.init.InitService") as mock_service_class:
        mock_service = mock_service_class.return_value
        validation = InitValidationResult(
            target_results=[
                {
                    "name": "prod",
                    "success": True,
                    "message": "Connected",
                }
            ],
            llm_result={"success": True, "model": "claude-sonnet"},
        )
        mock_service.validate_all_events.return_value = _events(
            InitCompleteEvent(type="complete", success=True, validation=validation)
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/init/validate",
                json={"targets": ["prod"]},
            )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["target_results"]) == 1
    assert payload["target_results"][0]["name"] == "prod"
    assert payload["target_results"][0]["success"] is True
    assert payload["llm_result"]["success"] is True
    mock_service.validate_all_events.assert_called_once_with(["prod"])


@pytest.mark.asyncio
async def test_init_complete_endpoint(app):
    """POST /api/init/complete marks init completed."""
    with patch("lib.api.routes.init.InitService") as mock_service_class:
        mock_service = mock_service_class.return_value
        mock_service.mark_complete_events.return_value = _events(
            InitCompleteEvent(type="complete", success=True)
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/init/complete")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    mock_service.mark_complete_events.assert_called_once_with()


@pytest.mark.asyncio
async def test_init_validate_rejects_invalid_payload(app):
    """POST /api/init/validate enforces request validation."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/init/validate",
            json={"targets": "prod"},
        )

    assert response.status_code == 422

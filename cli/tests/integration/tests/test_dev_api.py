#!/usr/bin/env python3
"""Integration tests for dev API endpoints."""

from unittest.mock import Mock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from shared.api.app import create_app
from shared.config.targets import TargetsConfig
from shared.anthropic_env import get_anthropic_source


@pytest.fixture
def app():
    return create_app()


@pytest.mark.asyncio
async def test_clear_keyring_allows_loopback_same_host_and_clears_trial_config(app, tmp_path):
    env_service = Mock()
    env_service.get_allowed_secret_names.return_value = ["PROD_DB_PASSWORD", "RDST_TRIAL_TOKEN"]

    secret_store = Mock()
    secret_store.clear_required.return_value = {
        "cleared": ["RDST_TRIAL_TOKEN"],
        "missing": ["PROD_DB_PASSWORD"],
        "errors": [],
    }
    secret_store.get_secret.return_value = None

    config = TargetsConfig(path=str(tmp_path / "config.toml"))
    config.load()
    config.set_trial_config({
        "token": "trial-token",
        "email": "user@example.com",
        "status": "exhausted",
        "remaining_cents": 0,
        "limit_cents": 500,
    })
    config.save()

    with (
        patch(
            "shared.api.routes.dev.EnvRequirementsService",
            return_value=env_service,
        ),
        patch(
            "shared.api.routes.dev.SecretStoreService",
            return_value=secret_store,
        ),
        patch("shared.api.routes.dev.TargetsConfig", return_value=config),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8787"
        ) as client:
            response = await client.post(
                "/api/dev/clear-keyring",
                headers={"origin": "http://127.0.0.1:8787"},
            )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "cleared": ["RDST_TRIAL_TOKEN"],
        "missing": ["PROD_DB_PASSWORD"],
        "errors": [],
        "message": "Reset 1 secret(s) and local trial state.",
    }
    secret_store.clear_required.assert_called_once_with(["PROD_DB_PASSWORD", "RDST_TRIAL_TOKEN"])

    config.load()
    assert config.get_trial_config() == {}
    with patch.dict("os.environ", {}, clear=True):
        assert get_anthropic_source(secret_store=secret_store, cfg=config) == "missing"



@pytest.mark.asyncio
async def test_clear_keyring_rejects_mismatched_origin_host(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8787"
    ) as client:
        response = await client.post(
            "/api/dev/clear-keyring",
            headers={"origin": "http://localhost:8787"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "Origin/Referer host mismatch"}


@pytest.mark.asyncio
async def test_clear_keyring_rejects_non_loopback_client(app):
    transport = ASGITransport(app=app, client=("203.0.113.10", 50000))
    async with AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8787"
    ) as client:
        response = await client.post(
            "/api/dev/clear-keyring",
            headers={"origin": "http://127.0.0.1:8787"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}

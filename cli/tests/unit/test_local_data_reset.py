from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from shared.api.routes import settings as settings_mod
from shared.config.targets import TargetsConfig


class _SecretStore:
    SERVICE_NAME = "rdst-web"
    errors_by_service: dict[str, list[str]] = {}
    cleared: list[tuple[str, list[str], str]] = []

    def __init__(self, service_name: str | None = None):
        self.service_name = service_name or self.SERVICE_NAME

    def refresh_availability(self):
        pass

    def clear_required(
        self,
        names,
        *,
        clear_environment="all",
        update_inventory=True,
    ):
        type(self).cleared.append((self.service_name, list(names), clear_environment))
        return {
            "cleared": list(names),
            "missing": [],
            "errors": list(type(self).errors_by_service.get(self.service_name, [])),
        }


async def _reset(app: FastAPI):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://127.0.0.1",
    ) as client:
        return await client.post("/api/settings/reset-local-data")


@pytest.fixture
def reset_app(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(settings_mod, "require_local_request", lambda request: None)

    import features.qpdemo.service as demo_module
    import shared.deploy.sandbox_manager as sandbox_module
    import shared.secret_store_service as secret_store_module
    import shared.telemetry as telemetry_module

    monkeypatch.setattr(
        demo_module.DemoService,
        "reset_local_data",
        lambda self: {"success": True, "message": "removed"},
    )
    monkeypatch.setattr(
        sandbox_module.sandbox_manager,
        "reset_local_data",
        AsyncMock(),
    )
    monkeypatch.setattr(secret_store_module, "SecretStoreService", _SecretStore)
    telemetry = MagicMock()
    monkeypatch.setattr(telemetry_module, "telemetry", telemetry)
    _SecretStore.errors_by_service = {}
    _SecretStore.cleared = []

    app = FastAPI()
    app.include_router(settings_mod.router, prefix="/api")
    return app, tmp_path / ".rdst", telemetry


def _seed_config(data_dir: Path):
    cfg = TargetsConfig(path=str(data_dir / "config.toml"))
    cfg.load()
    cfg.upsert(
        "prod",
        {
            "engine": "postgresql",
            "host": "db.example.com",
            "port": 5432,
            "user": "rdst",
            "database": "app",
            "password_env": "RDST_PROD_PASSWORD",
        },
    )
    cfg.set_email("person@example.com")
    cfg.save()


def test_reset_removes_local_data_and_owned_credentials(reset_app):
    app, data_dir, telemetry = reset_app
    _seed_config(data_dir)
    (data_dir / "audits").mkdir()
    (data_dir / "audits" / "saved.json").write_text("{}", encoding="utf-8")

    response = asyncio.run(_reset(app))

    assert response.status_code == 200
    assert not data_dir.exists()
    assert any(
        service == "rdst-web" and "RDST_PROD_PASSWORD" in names
        for service, names, _ in _SecretStore.cleared
    )
    telemetry.reset_runtime_state.assert_called_once_with()


def test_credential_failure_preserves_local_files_for_retry(reset_app):
    app, data_dir, _ = reset_app
    _seed_config(data_dir)
    _SecretStore.errors_by_service = {"rdst-web": ["Secure keyring unavailable"]}

    response = asyncio.run(_reset(app))

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "local_data_reset_incomplete"
    assert (data_dir / "config.toml").exists()


def test_invalid_secret_inventory_does_not_block_the_reset(reset_app):
    app, data_dir, _ = reset_app
    _seed_config(data_dir)
    (data_dir / "secret-inventory.json").write_text("[]", encoding="utf-8")

    response = asyncio.run(_reset(app))

    assert response.status_code == 200
    assert not (data_dir / "config.toml").exists()


def test_filesystem_failure_is_reported(reset_app, monkeypatch):
    app, data_dir, _ = reset_app
    _seed_config(data_dir)
    monkeypatch.setattr(settings_mod.shutil, "rmtree", MagicMock(side_effect=OSError("busy")))

    response = asyncio.run(_reset(app))

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "local_data_reset_incomplete"
    assert data_dir.exists()


def test_a_named_instance_clears_only_its_own_keyring_services(reset_app, monkeypatch):
    app, _, _ = reset_app
    monkeypatch.setenv("RDST_INSTANCE", "cl")

    response = asyncio.run(_reset(app))

    assert response.status_code == 200
    services = {service for service, _, _ in _SecretStore.cleared}
    assert services and services <= {"rdst-web-cl", "rdst-slack-cl"}

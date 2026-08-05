"""Password persistence contracts for fleet and provider imports."""

from __future__ import annotations

from typing import Any

import pytest

from features.fleet.service import FleetService
from features.providers.service import ProvidersService
from shared.api.ssh_errors import connectivity_error_payload


class FakeConfig:
    def __init__(self) -> None:
        self.targets: dict[str, dict[str, Any]] = {}
        self.saved = False

    def get(self, name: str) -> dict[str, Any] | None:
        target = self.targets.get(name)
        return dict(target) if target else None

    def upsert(self, name: str, target: dict[str, Any]) -> None:
        self.targets[name] = dict(target)

    def save(self) -> None:
        self.saved = True

    def list_targets(self) -> list[str]:
        return list(self.targets)


class FakeSecretStore:
    def __init__(self) -> None:
        self.saved: list[tuple[str, str, bool]] = []

    def set_secret(self, name: str, value: str, persist: bool = True) -> dict:
        self.saved.append((name, value, persist))
        return {"persisted": True, "session_only": False}


async def _import_csv(
    tmp_path,
    csv_text: str,
    *,
    password_env: str | None = None,
    password: str | None = None,
) -> tuple[FakeConfig, FakeSecretStore]:
    csv_path = tmp_path / "fleet.csv"
    csv_path.write_text(csv_text, encoding="utf-8")
    config = FakeConfig()
    secret_store = FakeSecretStore()
    service = FleetService(config=config, secret_store=secret_store)

    async for _ in service.import_fleet(
        str(csv_path),
        password_env=password_env,
        password=password,
    ):
        pass

    return config, secret_store


@pytest.mark.asyncio
async def test_csv_import_with_password_derives_pointer_and_stores_secret(tmp_path):
    config, secret_store = await _import_csv(
        tmp_path,
        "name,host,engine,password\norders prod,db.example.com,postgresql,s3cret\n",
    )

    target = config.targets["orders prod"]
    assert target["password_env"] == "RDST_ORDERS_PROD_PASSWORD"
    assert "password" not in target
    assert secret_store.saved == [
        ("RDST_ORDERS_PROD_PASSWORD", "s3cret", True)
    ]


@pytest.mark.asyncio
async def test_csv_import_without_password_omits_pointer(tmp_path):
    config, secret_store = await _import_csv(
        tmp_path,
        "name,host,engine\norders,db.example.com,postgresql\n",
    )

    assert "password_env" not in config.targets["orders"]
    assert secret_store.saved == []


@pytest.mark.asyncio
async def test_csv_import_preserves_explicit_password_pointer(tmp_path):
    config, secret_store = await _import_csv(
        tmp_path,
        "name,host,engine\norders,db.example.com,postgresql\n",
        password_env="CUSTOM_DATABASE_PASSWORD",
        password="s3cret",
    )

    assert config.targets["orders"]["password_env"] == "CUSTOM_DATABASE_PASSWORD"
    assert secret_store.saved == [
        ("CUSTOM_DATABASE_PASSWORD", "s3cret", True)
    ]


def test_provider_bulk_add_with_password_uses_same_secret_store_seam():
    config = FakeConfig()
    secret_store = FakeSecretStore()

    result = ProvidersService(config=config, secret_store=secret_store).add_members(
        [
            {
                "name": "customer db",
                "engine": "postgresql",
                "host": "db.example.com",
                "port": 5432,
                "database": "app",
                "user": "readonly",
                "password": "provider-secret",
            }
        ]
    )

    assert result["target_names"] == ["customer db"]
    assert config.targets["customer db"]["password_env"] == (
        "RDST_CUSTOMER_DB_PASSWORD"
    )
    assert "password" not in config.targets["customer db"]
    assert secret_store.saved == [
        ("RDST_CUSTOMER_DB_PASSWORD", "provider-secret", True)
    ]


@pytest.mark.parametrize("tunneled", [False, True])
def test_missing_password_auth_failure_has_credential_category(tunneled):
    target = {
        "engine": "postgresql",
        "host": "db.example.com",
        "port": 5432,
        "database": "app",
        "user": "readonly",
    }
    if tunneled:
        target["ssh"] = {"host": "jump.example.com", "user": "deploy"}

    payload = connectivity_error_payload(
        RuntimeError('password authentication failed for user "readonly"'),
        "orders",
        target,
    )

    assert payload is not None
    assert payload["code"] == "TARGET_PASSWORD_REQUIRED"
    assert payload["category"] == "target_password_required"
    assert payload["message"].startswith(
        "No password is available for target 'orders'"
    )
    assert "SSH tunnel" not in payload["message"]


def test_tunnel_wording_remains_for_genuine_network_failure():
    payload = connectivity_error_payload(
        RuntimeError("connection refused"),
        "orders",
        {
            "host": "db.example.com",
            "ssh": {"host": "jump.example.com", "user": "deploy"},
        },
    )

    assert payload is not None
    assert payload["category"] == "database_connection_failed"
    assert payload["message"].startswith("The SSH tunnel opened")

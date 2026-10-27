"""A new password never lands on the pointer of a database renamed away from the same name."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest

from features.configure.cli.wizard import ConfigurationWizard
from features.fleet.service import FleetService
from features.providers.service import ProvidersService
from shared.config.targets import TargetsConfig
from shared.password_resolver import new_password_env
from shared.secret_store_service import SecretStoreService

RENAMED = {"engine": "postgresql", "host": "db.example.com", "port": 5432, "database": "app",
           "user": "reader", "password_env": "RDST_PROD_PASSWORD"}


class FakeConfig:
    def __init__(self, targets: dict[str, dict[str, Any]]) -> None:
        self.targets = {name: dict(target) for name, target in targets.items()}

    def get(self, name: str) -> dict[str, Any] | None:
        target = self.targets.get(name)
        return dict(target) if target else None

    def upsert(self, name: str, target: dict[str, Any]) -> None:
        self.targets[name] = dict(target)

    def save(self) -> None:
        pass

    def list_targets(self) -> list[str]:
        return list(self.targets)


class FakeSecretStore:
    def __init__(self) -> None:
        self.saved: dict[str, str] = {"RDST_PROD_PASSWORD": "prod2-secret"}

    def set_secret(self, name: str, value: str, persist: bool = True) -> dict:
        self.saved[name] = value
        return {"persisted": True, "session_only": False}


def _renamed_on_disk() -> TargetsConfig:
    """`prod` renamed to `prod2`: the config and keyring a rename leaves behind."""
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert("prod2", RENAMED)
    cfg.save()
    SecretStoreService().set_secret("RDST_PROD_PASSWORD", "prod2-secret", persist=True)
    return cfg


def _cli_add(cfg: TargetsConfig, **kwargs) -> dict:
    result = ConfigurationWizard(console=Mock())._add_edit_target(
        cfg, "add", {"name": "prod", "skip_verify": True, **kwargs})
    assert result.ok, result.message
    cfg.load()
    return cfg.get("prod")


def test_cli_add_with_a_password_gets_its_own_pointer(tmp_rdst_home):
    cfg = _renamed_on_disk()

    added = _cli_add(cfg, connection_string="postgresql://reader:n3w@db2.example.com/app")  # trufflehog:ignore

    assert added["password_env"] == "RDST_PROD_PASSWORD_2"
    store = SecretStoreService()
    assert store.get_secret("RDST_PROD_PASSWORD") == "prod2-secret"
    assert store.get_secret("RDST_PROD_PASSWORD_2") == "n3w"
    assert cfg.get("prod2")["password_env"] == "RDST_PROD_PASSWORD"


def test_cli_add_without_a_password_cannot_read_the_renamed_one(tmp_rdst_home):
    cfg = _renamed_on_disk()

    added = _cli_add(cfg, host="db2.example.com", user="reader", database="app")

    assert added["password_env"] == "RDST_PROD_PASSWORD_2"


def test_provider_import_gets_its_own_pointer():
    config, store = FakeConfig({"prod2": RENAMED}), FakeSecretStore()

    ProvidersService(config=config, secret_store=store).add_members([{
        "name": "prod", "engine": "postgresql", "host": "new.example.com", "port": 5432,
        "database": "app", "user": "reader", "password": "n3w"}])

    assert config.targets["prod"]["password_env"] == "RDST_PROD_PASSWORD_2"
    assert store.saved == {"RDST_PROD_PASSWORD": "prod2-secret", "RDST_PROD_PASSWORD_2": "n3w"}


@pytest.mark.asyncio
async def test_fleet_import_gets_its_own_pointer_and_numbers_within_a_batch(tmp_path):
    csv_path = tmp_path / "fleet.csv"
    csv_path.write_text("name,host,engine,password\n"
                        "prod,new.example.com,postgresql,n3w\n"
                        "prod-db,a.example.com,postgresql,one\n"
                        "prod_db,b.example.com,postgresql,two\n", encoding="utf-8")
    config, store = FakeConfig({"prod2": RENAMED}), FakeSecretStore()

    async for _ in FleetService(config=config, secret_store=store).import_fleet(str(csv_path)):
        pass

    assert config.targets["prod"]["password_env"] == "RDST_PROD_PASSWORD_2"
    # Two names that derive the same pointer in one import each keep their own password.
    assert {config.targets[name]["password_env"] for name in ("prod-db", "prod_db")} == {
        "RDST_PROD_DB_PASSWORD", "RDST_PROD_DB_PASSWORD_2"}
    assert store.saved["RDST_PROD_PASSWORD"] == "prod2-secret"
    assert store.saved["RDST_PROD_PASSWORD_2"] == "n3w"
    assert {store.saved["RDST_PROD_DB_PASSWORD"], store.saved["RDST_PROD_DB_PASSWORD_2"]} == {
        "one", "two"}


def test_a_targets_own_pointer_is_kept():
    config = FakeConfig({"prod2": RENAMED,
                         "prod": {**RENAMED, "password_env": "RDST_PROD_PASSWORD_2"},
                         "shop": {**RENAMED, "password_env": "RDST_SHOP_PASSWORD"}})

    assert new_password_env("prod", config) == "RDST_PROD_PASSWORD_2"
    assert new_password_env("shop", config) == "RDST_SHOP_PASSWORD"
    assert new_password_env("fresh", config) == "RDST_FRESH_PASSWORD"


def test_an_unreadable_config_falls_back_to_the_conventional_pointer(tmp_rdst_home):
    (tmp_rdst_home / "config.toml").write_text("[broken\n")

    assert new_password_env("prod") == "RDST_PROD_PASSWORD"


# A database with no password of its own (trust or IAM sign-in, or a Secrets Manager secret)
# doesn't hold its conventional pointer, so another name that derives it may use it.
WITHOUT_POINTER = [
    {k: v for k, v in RENAMED.items() if k != "password_env"},
    {**{k: v for k, v in RENAMED.items() if k != "password_env"},
     "password_secret_arn": "arn:aws:secretsmanager:us-east-1:1:secret:prod"},
]


def _saved_without_pointer(target: dict) -> TargetsConfig:
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert("prod-db", target)
    cfg.save()
    return cfg


async def _web_add(name: str, **target):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from features.configure.api import routes

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/configure/targets", json={"name": name, "target": {
            "engine": "postgresql", "host": "other.example.com", "port": 5432,
            "database": "app", "user": "reader", **target}})


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", WITHOUT_POINTER)
async def test_web_add_uses_a_pointer_nobody_holds(tmp_rdst_home, run_blocking_inline,
                                                    existing):
    _saved_without_pointer(existing)

    added = await _web_add("prod_db", password="n3w")

    assert added.json()["success"] is True, added.json()
    cfg = TargetsConfig()
    cfg.load()
    assert cfg.get("prod_db")["password_env"] == "RDST_PROD_DB_PASSWORD"
    assert "password_env" not in cfg.get("prod-db")


@pytest.mark.asyncio
async def test_web_add_skips_a_pointer_password_setup_already_saved(tmp_rdst_home,
                                                                     run_blocking_inline):
    # Password setup saves the secret under the implicit pointer before it records it.
    _saved_without_pointer(WITHOUT_POINTER[0])
    SecretStoreService().set_secret("RDST_PROD_DB_PASSWORD", "prod-db-secret", persist=True)

    added = await _web_add("prod_db", password="n3w")
    taken = await _web_add("prod.db", host="attacker.example",
                           password_env="RDST_PROD_DB_PASSWORD")

    cfg = TargetsConfig()
    cfg.load()
    assert cfg.get("prod_db")["password_env"] == "RDST_PROD_DB_PASSWORD_2"
    assert SecretStoreService().get_secret("RDST_PROD_DB_PASSWORD") == "prod-db-secret"
    assert added.json()["success"] is True
    assert taken.status_code == 422 and "prod-db" in taken.json()["detail"]


@pytest.mark.parametrize("existing", WITHOUT_POINTER)
def test_cli_add_uses_a_pointer_nobody_holds(tmp_rdst_home, existing):
    cfg = _saved_without_pointer(existing)

    result = ConfigurationWizard(console=Mock())._add_edit_target(cfg, "add", {
        "name": "prod_db", "skip_verify": True,
        "connection_string": "postgresql://reader:n3w@other.example.com/app"})  # trufflehog:ignore

    assert result.ok, result.message
    cfg.load()
    assert cfg.get("prod_db")["password_env"] == "RDST_PROD_DB_PASSWORD"
    assert SecretStoreService().get_secret("RDST_PROD_DB_PASSWORD") == "n3w"


@pytest.mark.parametrize("existing", WITHOUT_POINTER)
def test_provider_import_uses_a_pointer_nobody_holds(tmp_rdst_home, existing):
    config, store = FakeConfig({"prod-db": existing}), FakeSecretStore()

    ProvidersService(config=config, secret_store=store).add_members([{
        "name": "prod_db", "engine": "postgresql", "host": "other.example.com", "port": 5432,
        "database": "app", "user": "reader", "password": "n3w"}])

    assert config.targets["prod_db"]["password_env"] == "RDST_PROD_DB_PASSWORD"
    assert store.saved["RDST_PROD_DB_PASSWORD"] == "n3w"


def test_password_setup_and_new_pointers_agree_on_who_holds_what(tmp_rdst_home):
    from shared.env_requirements_service import EnvRequirementsService

    cfg = _saved_without_pointer(WITHOUT_POINTER[0])
    cfg.upsert("prod_db", {**RENAMED, "password_env": "RDST_PROD_DB_PASSWORD"})
    cfg.save()

    mapping = EnvRequirementsService()._target_env_mapping(cfg)

    # The sourceless database's setup pointer steps past the one prod_db stores.
    assert mapping["RDST_PROD_DB_PASSWORD_2"]["targets"] == ["prod-db"]
    assert mapping["RDST_PROD_DB_PASSWORD"]["targets"] == ["prod_db"]

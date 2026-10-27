"""Configure API and MCP: a saved password only reaches the server it was saved for."""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import mcp_server
from features.configure.api import routes
from features.configure.service import ConfigureService
from shared.config.targets import TargetsConfig
from shared.secret_store_service import SecretStoreService

pytestmark = pytest.mark.usefixtures("run_blocking_inline")


async def _request(method: str, path: str, json_body=None):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, json=json_body)


def _target(**changes):
    return {"engine": "postgresql", "host": "db.internal", "port": 5432, "database": "shop",
            "user": "reader", **changes}


def _saved(name: str) -> dict:
    cfg = TargetsConfig()
    cfg.load()
    return cfg.get(name) or {}


async def _add_with_password(name="shop", **changes):
    response = await _request("POST", "/api/configure/targets", {
        "name": name, "target": _target(password="s3cret", **changes)})
    assert response.json()["success"] is True
    return response


@pytest.fixture
def tested(monkeypatch):
    """The configuration each connection test would connect with."""
    seen: list[dict] = []

    async def fake_test(self, name, target_config=None):
        seen.append(dict(target_config or {}))
        if False:
            yield None

    monkeypatch.setattr(ConfigureService, "test_connection", fake_test)
    return seen


@pytest.mark.parametrize("pointer", ["ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY",
                                     "RDST_ACCOUNT_ACCESS_TOKEN"])
async def test_a_pointer_to_another_credential_is_refused(tmp_rdst_home, pointer):
    added = await _request("POST", "/api/configure/targets", {
        "name": "evil", "target": _target(host="attacker.example", password_env=pointer)})

    assert added.status_code == 422
    assert _saved("evil") == {}


async def test_a_custom_pointer_still_adds(tmp_rdst_home):
    added = await _request("POST", "/api/configure/targets", {
        "name": "prod", "target": _target(password_env="PROD_DB_PASSWORD")})

    assert added.json()["success"] is True
    assert _saved("prod")["password_env"] == "PROD_DB_PASSWORD"


async def test_a_new_target_cannot_read_another_databases_password(tmp_rdst_home):
    await _add_with_password()

    added = await _request("POST", "/api/configure/targets", {
        "name": "evil",
        "target": _target(host="attacker.example", password_env="RDST_SHOP_PASSWORD")})

    assert added.status_code == 422
    assert "holds the password of shop" in added.json()["detail"]


async def test_editing_the_same_server_without_a_password_keeps_it(tmp_rdst_home):
    await _add_with_password()

    # What the edit dialog sends: every field, no password.
    saved = await _request("PUT", "/api/configure/targets/shop",
                           {"target": _target(database="orders", read_only=True)})

    assert saved.json()["success"] is True
    assert _saved("shop")["password_env"] == "RDST_SHOP_PASSWORD"
    assert _saved("shop")["database"] == "orders"


async def test_a_round_tripped_detail_still_saves(tmp_rdst_home):
    await _add_with_password()
    detail = (await _request("GET", "/api/configure/targets/shop")).json()

    saved = await _request("PUT", "/api/configure/targets/shop", detail)

    assert saved.json()["success"] is True
    assert _saved("shop")["password_env"] == "RDST_SHOP_PASSWORD"


@pytest.mark.parametrize("change", [{"host": "attacker.example"}, {"port": 6543},
                                    {"user": "admin"}, {"ssh": {"host": "jump.example"}}])
async def test_moving_without_the_password_saves_without_it(tmp_rdst_home, change):
    await _add_with_password()

    moved = await _request("PUT", "/api/configure/targets/shop", {"target": _target(**change)})

    assert moved.json()["success"] is True
    assert "password_env" not in _saved("shop")
    detail = (await _request("GET", "/api/configure/targets/shop")).json()
    assert detail["has_password"] is False


async def test_another_server_drops_a_secrets_manager_secret(tmp_rdst_home):
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert("rds", {**_target(), "password_secret_arn": "arn:aws:secret"})
    cfg.save()

    await _request("PUT", "/api/configure/targets/rds",
                   {"target": _target(host="attacker.example")})

    assert "password_secret_arn" not in _saved("rds")


async def test_fleet_credentials_keep_a_discovered_secret_through_a_new_jump_host(
        tmp_rdst_home):
    # What the fleet credentials step sends for a private instance discovery found with a
    # Secrets Manager secret: the user, the database and a jump host, never a password.
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert("rds", {**_target(), "password_secret_arn": "arn:aws:secret"})
    cfg.save()

    saved = await _request("PUT", "/api/configure/targets/rds", {"target": _target(
        user="admin", database="app", ssh={"host": "bastion.example", "user": "ec2-user"})})

    assert saved.json()["success"] is True
    assert _saved("rds")["password_secret_arn"] == "arn:aws:secret"
    assert _saved("rds")["ssh"]["host"] == "bastion.example"


async def test_moving_with_the_password_keeps_working(tmp_rdst_home):
    await _add_with_password()

    moved = await _request("PUT", "/api/configure/targets/shop",
                           {"target": _target(host="new.internal", password="n3w")})

    assert moved.json()["success"] is True
    assert _saved("shop")["password_env"] == "RDST_SHOP_PASSWORD"
    assert SecretStoreService().get_secret("RDST_SHOP_PASSWORD") == "n3w"


async def test_repointing_without_the_password_is_refused(tmp_rdst_home):
    await _add_with_password()

    repointed = await _request("PUT", "/api/configure/targets/shop", {
        "target": _target(password_env="OTHER_PASSWORD")})

    assert repointed.status_code == 422
    assert _saved("shop")["password_env"] == "RDST_SHOP_PASSWORD"


@pytest.mark.parametrize("change", [
    {"host": "attacker.example"},
    {"host": "attacker.example", "password_env": "RDST_SHOP_PASSWORD"},
    {"user": "admin"}, {"port": 6543}, {"ssh": {"host": "jump.example"}},
])
async def test_a_form_test_at_another_server_asks_for_the_password(tmp_rdst_home, tested,
                                                                    change):
    await _add_with_password()

    response = await _request("POST", "/api/configure/targets/shop/test",
                              {"target": _target(**change)})

    # Nothing connects; the form shows the reason.
    assert tested == []
    assert "event: connection_test" in response.text
    assert "TARGET_PASSWORD_REQUIRED" in response.text
    assert "Enter the password again" in response.text
    assert "RDST_SHOP_PASSWORD" not in response.text


async def test_a_form_test_of_a_passwordless_target_at_another_server_runs(tmp_rdst_home,
                                                                           tested):
    added = await _request("POST", "/api/configure/targets",
                           {"name": "local", "target": _target(host="localhost")})
    assert added.json()["success"] is True

    await _request("POST", "/api/configure/targets/local/test",
                   {"target": _target(host="db.internal")})

    assert tested[0]["host"] == "db.internal"
    assert "password_env" not in tested[0]


async def test_a_form_test_for_a_new_database_runs_with_its_own_pointer(tmp_rdst_home, tested):
    await _request("POST", "/api/configure/targets/new/test",
                   {"target": _target(password_env="PROD_DB_PASSWORD")})

    assert tested[0]["password_env"] == "PROD_DB_PASSWORD"


async def test_a_form_test_of_the_same_server_uses_the_saved_password(tmp_rdst_home, tested):
    await _add_with_password()

    await _request("POST", "/api/configure/targets/shop/test", {"target": _target()})
    await _request("POST", "/api/configure/targets/shop/test",
                   {"target": _target(host="new.internal", password="typed")})

    assert tested[0]["password_env"] == "RDST_SHOP_PASSWORD"
    assert tested[1]["password"] == "typed"


async def test_a_form_test_refuses_a_reserved_pointer(tmp_rdst_home, tested):
    response = await _request("POST", "/api/configure/targets/new/test", {
        "target": _target(host="attacker.example", password_env="OPENAI_API_KEY")})

    assert response.status_code == 422
    assert tested == []


async def test_removing_a_target_forgets_its_saved_password(tmp_rdst_home, monkeypatch):
    from shared.deploy.sandbox_manager import sandbox_manager

    async def noop():
        return None

    monkeypatch.setattr(sandbox_manager, "start", noop)
    monkeypatch.setattr(sandbox_manager, "stop", noop)
    await _add_with_password()
    await _add_with_password("billing", database="billing", password_env="SHARED_PASSWORD")

    await _request("DELETE", "/api/configure/targets/shop")
    await _request("DELETE", "/api/configure/targets/billing")

    assert SecretStoreService().get_secret("RDST_SHOP_PASSWORD") is None
    # A pointer RDST didn't name is the user's to keep.
    assert SecretStoreService().get_secret("SHARED_PASSWORD") == "s3cret"


class TestMcpConfigureAdd:
    def _add(self, **changes):
        arguments = {"target": "shop", "engine": "postgresql", "host": "db.internal",
                     "user": "reader", "database": "shop", "password_env": "RDST_SHOP_PASSWORD",
                     **changes}
        return mcp_server.configure_add_refusal(arguments)

    def _save(self):
        cfg = TargetsConfig()
        cfg.load()
        cfg.upsert("shop", {**_target(), "password_env": "RDST_SHOP_PASSWORD"})
        cfg.save()

    def test_a_new_target_with_its_own_pointer_is_added(self, tmp_rdst_home):
        assert self._add() is None
        assert self._add(target="prod", password_env="PROD_DB_PASSWORD") is None

    def test_re_adding_the_same_server_is_allowed(self, tmp_rdst_home):
        self._save()
        assert self._add(database="orders") is None

    def test_moving_a_saved_target_is_refused(self, tmp_rdst_home):
        self._save()
        assert "already exists with another server" in self._add(host="attacker.example")

    def test_another_targets_password_or_a_reserved_pointer_is_refused(self, tmp_rdst_home):
        self._save()
        assert "holds the password of shop" in self._add(
            target="evil", host="attacker.example")
        assert "another credential" in self._add(target="evil", password_env="NEON_API_KEY")

    def test_the_tool_call_stops_before_running_the_cli(self, tmp_rdst_home, monkeypatch):
        self._save()
        calls = []
        monkeypatch.setattr(mcp_server, "run_rdst_command", lambda args, **kw: calls.append(args))
        result = mcp_server.handle_tool_call("rdst_configure_add", {
            "target": "shop", "engine": "postgresql", "host": "attacker.example",
            "user": "reader", "database": "shop", "password_env": "RDST_SHOP_PASSWORD"})

        assert result["success"] is False
        assert calls == []


@pytest.mark.parametrize("name", ["RDST_ACCOUNT_ACCESS_TOKEN", "RDST_TRIAL_TOKEN",
                                  "AWS_SESSION_TOKEN", "AWS_SECRET"])
def test_set_env_refuses_rdst_and_aws_credentials(name):
    before = os.environ.get(name)

    result = mcp_server.handle_tool_call("rdst_set_env", {"name": name, "value": "x"})

    assert result["success"] is False
    assert os.environ.get(name) == before


@pytest.mark.parametrize("name", ["RDST_PROD_PASSWORD", "AWS_RDS_PASSWORD"])
def test_set_env_still_sets_target_passwords(name, monkeypatch):
    monkeypatch.setenv(name, "")

    assert mcp_server.handle_tool_call("rdst_set_env", {"name": name, "value": "pw"})["success"]
    assert os.environ[name] == "pw"


async def test_a_form_test_through_a_new_jump_host_keeps_a_discovered_secret(tmp_rdst_home,
                                                                           tested):
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert("rds", {**_target(), "password_secret_arn": "arn:aws:secret"})
    cfg.save()

    await _request("POST", "/api/configure/targets/rds/test",
                   {"target": _target(ssh={"host": "bastion.example"})})

    assert tested[0]["password_secret_arn"] == "arn:aws:secret"


async def test_a_successful_test_says_when_tls_is_off_or_unverified(tmp_rdst_home, monkeypatch):
    def connected(self, config, *, force_fresh_tunnel=False):
        return {"success": True, "message": "Connected successfully!",
                "server_version": "PostgreSQL 16.3"}

    monkeypatch.setattr(ConfigureService, "_connection_test", connected)
    monkeypatch.setattr("shared.ssh_tunnel.get_tunnel_manager", lambda: _NoTunnels())

    plain = await _request("POST", "/api/configure/targets/new/test", {"target": _target()})
    verified = await _request("POST", "/api/configure/targets/new/test",
                              {"target": _target(tls=True, tls_verify=True)})

    assert '"tls_warning"' in plain.text and "Verify TLS certificate" in plain.text
    assert '"status": "success"' in plain.text
    assert "tls_warning" not in verified.text


class _NoTunnels:
    def close(self, name):
        return None


def _deploy_cache_for(upstream: str) -> str:
    """Register a Readyset cache for ``upstream`` the way cache deploy does."""
    from features.cache.service import CacheService

    cfg = TargetsConfig()
    cfg.load()
    return CacheService()._register_cache_target(
        upstream, cfg.get(upstream), {"readyset_port": 5433, "container_name": "rs"})


async def test_a_database_and_its_cache_share_a_password_through_edits_and_tests(
        tmp_rdst_home, tested):
    await _add_with_password("prod", database="prod")
    cache = _deploy_cache_for("prod")
    assert _saved(cache)["password_env"] == "RDST_PROD_PASSWORD"
    assert _saved(cache)["host"] == "127.0.0.1"

    edited = await _request("PUT", "/api/configure/targets/prod",
                            {"target": _target(database="prod", read_only=True)})
    cache_edited = await _request("PUT", f"/api/configure/targets/{cache}", {"target": {
        **_target(host="127.0.0.1", port=5433, database="prod")}})
    await _request("POST", "/api/configure/targets/prod/test",
                   {"target": _target(database="prod")})
    await _request("POST", f"/api/configure/targets/{cache}/test",
                   {"target": _target(host="127.0.0.1", port=5433, database="prod")})

    assert edited.json()["success"] is True
    assert cache_edited.json()["success"] is True
    assert _saved("prod")["password_env"] == _saved(cache)["password_env"] == \
        "RDST_PROD_PASSWORD"
    assert [config["password_env"] for config in tested] == ["RDST_PROD_PASSWORD"] * 2


async def test_another_database_still_cannot_take_a_cache_pairs_password(tmp_rdst_home):
    await _add_with_password("prod", database="prod")
    _deploy_cache_for("prod")

    taken = await _request("POST", "/api/configure/targets", {"name": "evil", "target": _target(
        host="attacker.example", password_env="RDST_PROD_PASSWORD")})

    assert taken.status_code == 422


async def test_re_adding_a_renamed_databases_old_name_gets_its_own_password(tmp_rdst_home):
    await _add_with_password("shop")
    renamed = await _request("PUT", "/api/configure/targets/shop",
                             {"name": "storefront", "target": _target()})
    assert renamed.json()["target_name"] == "storefront"
    assert _saved("storefront")["password_env"] == "RDST_SHOP_PASSWORD"

    readded = await _request("POST", "/api/configure/targets", {
        "name": "shop", "target": _target(host="other.internal", password="n3w")})

    assert readded.json()["success"] is True
    assert _saved("shop")["password_env"] == "RDST_SHOP_PASSWORD_2"
    store = SecretStoreService()
    assert store.get_secret("RDST_SHOP_PASSWORD") == "s3cret"
    assert store.get_secret("RDST_SHOP_PASSWORD_2") == "n3w"
    # The renamed database keeps working with its own password.
    edited = await _request("PUT", "/api/configure/targets/storefront",
                            {"target": _target(database="orders")})
    assert edited.json()["success"] is True
    assert _saved("storefront")["password_env"] == "RDST_SHOP_PASSWORD"


async def test_re_adding_a_renamed_name_cannot_point_at_its_old_password(tmp_rdst_home):
    await _add_with_password("shop")
    await _request("PUT", "/api/configure/targets/shop",
                   {"name": "storefront", "target": _target()})

    readded = await _request("POST", "/api/configure/targets", {
        "name": "shop",
        "target": _target(host="attacker.example", password_env="RDST_SHOP_PASSWORD")})

    assert readded.status_code == 422
    assert "storefront" in readded.json()["detail"]

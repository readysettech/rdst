"""Unit tests for unified password resolution."""

from unittest.mock import Mock

import pytest

from shared.password_resolver import (
    PasswordResolution,
    bind_target_credentials,
    check_password_env,
    derive_password_env,
    is_rdst_password_env,
    is_reserved_password_env,
    resolve_password,
    resolve_password_value,
    store_target_password,
    unique_password_env,
)


class FakeSecretStore:
    def __init__(self, values=None):
        self.values = values or {}

    def get_secret(self, name: str):
        return self.values.get(name)

    def is_available(self) -> bool:
        return True


def test_derive_password_env_uses_rdst_target_convention():
    assert derive_password_env("customer prod") == "RDST_CUSTOMER_PROD_PASSWORD"
    assert derive_password_env("--") == "RDST_TARGET_PASSWORD"

# -- Priority order tests --


def test_direct_password_returns_config():
    cfg = {"password": "s3cret", "password_env": "DB_PASS"}
    result = resolve_password(cfg, secret_store=FakeSecretStore())
    assert result == PasswordResolution(available=True, source="config")


def test_process_env_beats_secure_store(monkeypatch):
    monkeypatch.setenv("DB_PASS", "from-env")
    cfg = {"password_env": "DB_PASS"}
    store = FakeSecretStore({"DB_PASS": "from-keychain"})
    result = resolve_password(cfg, secret_store=store)
    assert result == PasswordResolution(available=True, source="process_env")


def test_secure_store_used_when_env_missing(monkeypatch):
    monkeypatch.delenv("DB_PASS", raising=False)
    cfg = {"password_env": "DB_PASS"}
    store = FakeSecretStore({"DB_PASS": "from-keychain"})
    result = resolve_password(cfg, secret_store=store)
    assert result == PasswordResolution(available=True, source="secure_store")


def test_missing_when_nothing_found(monkeypatch):
    monkeypatch.delenv("DB_PASS", raising=False)
    cfg = {"password_env": "DB_PASS"}
    result = resolve_password(cfg, secret_store=FakeSecretStore())
    assert result == PasswordResolution(available=False, source="missing")


# -- Edge cases --


def test_no_password_fields_at_all():
    cfg = {"host": "localhost"}
    result = resolve_password(cfg, secret_store=FakeSecretStore())
    assert result == PasswordResolution(available=False, source="missing")


def test_empty_password_treated_as_missing():
    cfg = {"password": "", "password_env": "DB_PASS"}
    result = resolve_password(cfg, secret_store=FakeSecretStore())
    assert result == PasswordResolution(available=False, source="missing")


def test_object_input_with_attrs():
    class TargetObj:
        password = "direct"
        password_env = "DB_PASS"

    result = resolve_password(TargetObj(), secret_store=FakeSecretStore())
    assert result == PasswordResolution(available=True, source="config")


def test_object_input_without_password():
    class TargetObj:
        password_env = "DB_PASS"

    store = FakeSecretStore({"DB_PASS": "keychain-val"})
    result = resolve_password(TargetObj(), secret_store=store)
    assert result == PasswordResolution(available=True, source="secure_store")


def test_default_secret_store_instantiated(monkeypatch):
    """When secret_store=None, resolve_password creates a SecretStoreService."""
    monkeypatch.delenv("DB_PASS", raising=False)
    cfg = {"password_env": "DB_PASS"}
    # Should not raise — just returns missing since keychain likely empty in test
    result = resolve_password(cfg)
    assert result.source in ("secure_store", "missing")


# -- resolve_password_value tests --


def test_resolve_value_direct_password():
    """Direct password in config is returned as-is."""
    cfg = {"password": "s3cret", "password_env": "DB_PASS"}
    result = resolve_password_value(cfg, secret_store=FakeSecretStore())
    assert result == "s3cret"


def test_resolve_value_from_env(monkeypatch):
    """Env var value is returned when no direct password."""
    monkeypatch.setenv("DB_PASS", "from-env")
    cfg = {"password_env": "DB_PASS"}
    store = FakeSecretStore({"DB_PASS": "from-keychain"})
    result = resolve_password_value(cfg, secret_store=store)
    assert result == "from-env"


def test_resolve_value_from_keyring(monkeypatch):
    """Keyring value is returned and injected into os.environ."""
    monkeypatch.delenv("DB_PASS", raising=False)
    cfg = {"password_env": "DB_PASS"}
    store = FakeSecretStore({"DB_PASS": "from-keychain"})
    result = resolve_password_value(cfg, secret_store=store)
    assert result == "from-keychain"


def test_resolve_value_missing(monkeypatch):
    """Empty string when nothing found."""
    monkeypatch.delenv("DB_PASS", raising=False)
    cfg = {"password_env": "DB_PASS"}
    result = resolve_password_value(cfg, secret_store=FakeSecretStore())
    assert result == ""


def test_resolve_value_env_injection(monkeypatch):
    """Keyring resolution injects into os.environ for subprocess inheritance."""
    import os
    monkeypatch.delenv("DB_PASS", raising=False)
    cfg = {"password_env": "DB_PASS"}
    store = FakeSecretStore({"DB_PASS": "keychain-val"})
    resolve_password_value(cfg, secret_store=store)
    assert os.environ.get("DB_PASS") == "keychain-val"
    # Clean up so this doesn't leak into other tests
    monkeypatch.delenv("DB_PASS", raising=False)


# -- Pointers that name other credentials --


@pytest.mark.parametrize("name", [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "aws_session_token",
    "RDST_ACCOUNT_ACCESS_TOKEN", "RDST_TRIAL_TOKEN", "SUPABASE_ACCESS_TOKEN", "NEON_API_KEY",
    "DIGITALOCEAN_TOKEN", "RDST_SUPABASE_OAUTH_CLIENT_SECRET", "T123:abc:bot-token",
])
def test_other_credentials_are_reserved(name):
    assert is_reserved_password_env(name)
    with pytest.raises(ValueError):
        check_password_env(name)


@pytest.mark.parametrize("name", [
    None, "", "PGPASSWORD", "PROD_DB_PASSWORD", "FLEET_DB_PASS", "TPCH_PASSWD",
    "RDST_PROD_PASSWORD", "RDST_DEMO_PASSWORD", "AWS_RDS_PASSWORD", "SHOP_PASSWORD",
])
def test_database_password_pointers_are_allowed(name):
    assert not is_reserved_password_env(name)
    check_password_env(name)


def test_a_reserved_pointer_is_never_read(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    cfg = {"password_env": "ANTHROPIC_API_KEY"}
    store = FakeSecretStore({"ANTHROPIC_API_KEY": "sk-ant-keyring"})

    assert resolve_password_value(cfg, secret_store=store) == ""
    assert resolve_password(cfg, secret_store=store).available is False


def test_a_reserved_pointer_is_never_written():
    with pytest.raises(ValueError):
        store_target_password("prod", "pw", "AWS_SECRET_ACCESS_KEY",
                              secret_store=Mock())


# -- Binding a saved password to its server --


SAVED = {"engine": "postgresql", "host": "db.internal", "port": 5432, "database": "shop",
         "user": "reader", "password_env": "RDST_SHOP_PASSWORD"}


def _bind(proposed, existing=SAVED, saved=None, *, password_submitted=False, name="shop"):
    return bind_target_credentials(name, proposed, existing,
                                   saved if saved is not None else {"shop": SAVED},
                                   password_submitted=password_submitted)


def test_an_unchanged_server_keeps_its_saved_password():
    assert _bind({**SAVED, "database": "other", "read_only": True})["password_env"] == \
        "RDST_SHOP_PASSWORD"
    # Case and the port's type don't make it another server.
    assert _bind({**SAVED, "host": "DB.internal ", "port": "5432"})["password_env"] == \
        "RDST_SHOP_PASSWORD"


WITH_SECRETS = {**SAVED, "password": "legacy", "password_secret_arn": "arn:aws:x",
                "password_secret_key": "pw"}


@pytest.mark.parametrize("change", [
    {"host": "attacker.example"}, {"port": 6432}, {"engine": "mysql"},
])
def test_another_server_without_the_password_drops_every_saved_credential(change):
    bound = _bind({**WITH_SECRETS, **change}, WITH_SECRETS)

    for field in ("password", "password_env", "password_secret_arn", "password_secret_key"):
        assert field not in bound
    assert bound["host"] == {**WITH_SECRETS, **change}["host"]


@pytest.mark.parametrize("change", [{"user": "admin"}, {"ssh": {"host": "jump.example.com"}}])
def test_another_user_or_jump_host_keeps_only_the_servers_own_secret(change):
    bound = _bind({**WITH_SECRETS, **change}, WITH_SECRETS)

    assert "password" not in bound and "password_env" not in bound
    # Discovery's Secrets Manager secret belongs to the database server, which didn't change.
    assert bound["password_secret_arn"] == "arn:aws:x"


def test_moving_with_the_password_keeps_the_pointer_and_drops_the_old_secret():
    existing = {**SAVED, "password_secret_arn": "arn:aws:x"}
    bound = _bind({**existing, "host": "new.internal"}, existing, password_submitted=True)

    assert bound["password_env"] == "RDST_SHOP_PASSWORD"
    assert "password_secret_arn" not in bound


def test_repointing_needs_the_password():
    with pytest.raises(ValueError, match="Enter the password"):
        _bind({**SAVED, "password_env": "PROD_DB_PASSWORD"})
    assert _bind({**SAVED, "password_env": "PROD_DB_PASSWORD"},
                 password_submitted=True)["password_env"] == "PROD_DB_PASSWORD"


def test_another_databases_saved_password_is_refused():
    saved = {"shop": SAVED, "billing": {**SAVED, "host": "billing.internal",
                                        "password_env": "RDST_BILLING_PASSWORD"}}
    for pointer in ("RDST_BILLING_PASSWORD", "RDST_SHOP_PASSWORD"):
        with pytest.raises(ValueError, match="holds the password of"):
            bind_target_credentials("new", {**SAVED, "host": "attacker.example",
                                             "password_env": pointer}, None, saved,
                                    password_submitted=False)


def test_a_new_target_may_share_a_custom_pointer_or_reuse_one_at_the_same_server():
    saved = {"shop": {**SAVED, "password_env": "SHOP_PASSWORD"}}
    bound = bind_target_credentials("shop-2", {**SAVED, "host": "other.internal",
                                               "password_env": "SHOP_PASSWORD"},
                                    None, saved, password_submitted=False)
    assert bound["password_env"] == "SHOP_PASSWORD"
    same = bind_target_credentials("shop-ro", {**SAVED, "database": "other"}, None,
                                   {"shop": SAVED}, password_submitted=False)
    assert same["password_env"] == "RDST_SHOP_PASSWORD"


def test_bind_refuses_a_reserved_pointer():
    with pytest.raises(ValueError):
        _bind({**SAVED, "password_env": "ANTHROPIC_API_KEY"}, password_submitted=True)


def test_numbered_pointers_rdst_generates_are_database_passwords(monkeypatch):
    from shared.env_requirements_service import EnvRequirementsService

    class Config:
        targets = {"prod": {"password_env": "RDST_PROD_PASSWORD"}, "PROD": {}}

        def list_targets(self):
            return sorted(self.targets)

        def get(self, name):
            return self.targets.get(name)

    generated = EnvRequirementsService(secret_store=FakeSecretStore())._target_env_mapping(
        Config())
    numbered = next(name for name in generated if name != "RDST_PROD_PASSWORD")
    assert numbered == "RDST_PROD_PASSWORD_2"
    assert not is_reserved_password_env(numbered)
    assert is_rdst_password_env(numbered)

    monkeypatch.setenv(numbered, "pw")
    assert resolve_password_value({"password_env": numbered},
                                  secret_store=FakeSecretStore()) == "pw"


@pytest.mark.parametrize("name", ["RDST_TRIAL_TOKEN_2", "RDST_ACCOUNT_ACCESS_TOKEN_1",
                                  "AWS_SESSION_TOKEN_3"])
def test_a_number_does_not_make_another_credential_a_password(name):
    assert is_reserved_password_env(name)


def test_unique_password_env_numbers_past_taken_pointers():
    assert unique_password_env("shop", set()) == "RDST_SHOP_PASSWORD"
    assert unique_password_env("shop", {"RDST_SHOP_PASSWORD"}) == "RDST_SHOP_PASSWORD_2"
    assert unique_password_env("shop", {"RDST_SHOP_PASSWORD", "RDST_SHOP_PASSWORD_2"}) == \
        "RDST_SHOP_PASSWORD_3"


def test_an_unchanged_shared_pointer_is_not_another_databases():
    cache = {**SAVED, "host": "127.0.0.1", "port": 5433}
    saved = {"shop": SAVED, "shop-cache": cache}

    assert bind_target_credentials("shop", dict(SAVED), SAVED, saved,
                                   password_submitted=False)["password_env"] == \
        "RDST_SHOP_PASSWORD"
    assert bind_target_credentials("shop-cache", dict(cache), cache, saved,
                                   password_submitted=False)["password_env"] == \
        "RDST_SHOP_PASSWORD"

"""Unit tests for unified password resolution."""

from unittest.mock import Mock

from shared.password_resolver import (
    PasswordResolution,
    derive_password_env,
    resolve_password,
    resolve_password_value,
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

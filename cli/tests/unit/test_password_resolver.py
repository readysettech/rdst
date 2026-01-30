"""Unit tests for unified password resolution."""

from unittest.mock import Mock

from lib.services.password_resolver import PasswordResolution, resolve_password


class FakeSecretStore:
    def __init__(self, values=None):
        self.values = values or {}

    def get_secret(self, name: str):
        return self.values.get(name)

    def is_available(self) -> bool:
        return True


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

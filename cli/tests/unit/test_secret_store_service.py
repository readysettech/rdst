"""Unit tests for secure secret storage service."""

import os
import threading
import time
from unittest.mock import Mock, patch

from shared import secret_store_service
from shared.secret_store_service import SecretStoreService


def _build_backend(class_name: str, module_name: str):
    backend_type = type(class_name, (), {})
    backend_type.__module__ = module_name
    return backend_type()


def test_keyring_timeout_does_not_wait_for_hung_backend(monkeypatch):
    release = threading.Event()
    service = SecretStoreService()
    monkeypatch.setattr(secret_store_service, "_KEYRING_TIMEOUT", 0.01)

    started = time.monotonic()
    result = service._keyring_call(lambda: release.wait(timeout=1))
    elapsed = time.monotonic() - started
    release.set()

    assert result is secret_store_service._TIMEOUT
    assert elapsed < 0.5


def test_set_secret_persists_when_keyring_available(monkeypatch):
    service = SecretStoreService()
    service._keyring = Mock()

    with patch.object(service, "is_available", return_value=True):
        result = service.set_secret("RDST_TEST_SECRET", "top-secret", persist=True)

    assert os.environ.get("RDST_TEST_SECRET") == "top-secret"
    assert result["persisted"] is True
    assert result["session_only"] is False
    service._keyring.set_password.assert_called_once_with(
        service.service_name,
        "RDST_TEST_SECRET",
        "top-secret",
    )
    monkeypatch.delenv("RDST_TEST_SECRET", raising=False)


def test_set_secret_is_session_only_when_persist_disabled(monkeypatch):
    service = SecretStoreService()
    result = service.set_secret("RDST_TEST_SECRET", "top-secret", persist=False)

    assert os.environ.get("RDST_TEST_SECRET") == "top-secret"
    assert result["persisted"] is False
    assert result["session_only"] is True
    monkeypatch.delenv("RDST_TEST_SECRET", raising=False)


def test_set_secret_falls_back_to_session_only_when_keyring_unavailable(monkeypatch):
    service = SecretStoreService()
    service._keyring = Mock()

    with patch.object(service, "is_available", return_value=False):
        result = service.set_secret("RDST_TEST_SECRET", "top-secret", persist=True)

    assert os.environ.get("RDST_TEST_SECRET") == "top-secret"
    assert result["persisted"] is False
    assert result["session_only"] is True
    monkeypatch.delenv("RDST_TEST_SECRET", raising=False)


def test_restore_required_restores_missing_envs(monkeypatch):
    service = SecretStoreService()

    monkeypatch.delenv("MISSING_SECRET", raising=False)
    monkeypatch.setenv("ALREADY_SET_SECRET", "present")

    with patch.object(
        service,
        "get_secret",
        side_effect=lambda name: "restored-value" if name == "MISSING_SECRET" else None,
    ):
        result = service.restore_required(
            ["MISSING_SECRET", "ALREADY_SET_SECRET", "OTHER_SECRET"]
        )

    assert "MISSING_SECRET" in result["restored"]
    assert "OTHER_SECRET" in result["missing"]
    assert "ALREADY_SET_SECRET" not in result["restored"]
    assert os.environ.get("MISSING_SECRET") == "restored-value"

    monkeypatch.delenv("MISSING_SECRET", raising=False)
    monkeypatch.delenv("ALREADY_SET_SECRET", raising=False)


def test_clear_required_clears_env_and_keyring_entry(monkeypatch):
    service = SecretStoreService()
    service._keyring = Mock()

    monkeypatch.setenv("RDST_TEST_SECRET", "present")

    with patch.object(service, "is_available", return_value=True):
        service._keyring.get_password.return_value = "persisted-value"
        result = service.clear_required(["RDST_TEST_SECRET"])

    assert result["cleared"] == ["RDST_TEST_SECRET"]
    assert result["missing"] == []
    assert result["errors"] == []
    assert os.environ.get("RDST_TEST_SECRET") is None
    service._keyring.delete_password.assert_called_once_with(
        service.service_name,
        "RDST_TEST_SECRET",
    )


def test_clear_required_marks_missing_when_not_present(monkeypatch):
    service = SecretStoreService()
    service._keyring = Mock()

    monkeypatch.delenv("RDST_UNKNOWN_SECRET", raising=False)

    with patch.object(service, "is_available", return_value=True):
        service._keyring.get_password.return_value = None
        result = service.clear_required(["RDST_UNKNOWN_SECRET"])

    assert result["cleared"] == []
    assert result["missing"] == ["RDST_UNKNOWN_SECRET"]
    assert result["errors"] == []


def test_is_available_accepts_macos_keyring_backend():
    SecretStoreService._probe_cache.clear()
    service = SecretStoreService(service_name="rdst-test-macos")
    service._keyring = Mock()
    service._keyring.get_keyring.return_value = _build_backend(
        "Keyring",
        "keyring.backends.macOS",
    )
    service._keyring_call = Mock(return_value=None)

    assert service._backend_looks_viable() is True
    assert service.is_available() is True
    service._keyring_call.assert_called_once_with(
        service._keyring.get_password,
        service.service_name,
        "__rdst_probe__",
    )


def test_is_available_rejects_fail_keyring_backend_without_probe():
    SecretStoreService._probe_cache.clear()
    service = SecretStoreService(service_name="rdst-test-fail")
    service._keyring = Mock()
    service._keyring.get_keyring.return_value = _build_backend(
        "Keyring",
        "keyring.backends.fail",
    )
    service._keyring_call = Mock(return_value=None)

    assert service._backend_looks_viable() is False
    assert service.is_available() is False
    service._keyring_call.assert_not_called()


def test_is_available_rejects_null_backend():
    SecretStoreService._probe_cache.clear()
    service = SecretStoreService(service_name="rdst-test-null")
    service._keyring = Mock()
    service._keyring.get_keyring.return_value = _build_backend(
        "NullKeyring",
        "keyring.backends.null",
    )
    service._keyring_call = Mock(return_value=None)

    assert service._backend_looks_viable() is False
    assert service.is_available() is False
    service._keyring_call.assert_not_called()


def test_is_available_caches_probe_result():
    SecretStoreService._probe_cache.clear()
    service = SecretStoreService(service_name="rdst-test-cache")
    service._keyring = Mock()
    service._keyring.get_keyring.return_value = _build_backend(
        "Keyring",
        "keyring.backends.macOS",
    )
    service._keyring_call = Mock(return_value=None)

    # A running server keeps the first probe result cached until restart.
    assert service.is_available() is True
    assert service.is_available() is True
    service._keyring_call.assert_called_once_with(
        service._keyring.get_password,
        service.service_name,
        "__rdst_probe__",
    )


class _WindowsLikeKeyring:
    """Credential Manager stand-in: an entry holds at most 1280 characters."""

    def __init__(self):
        self.entries = {}

    def get_password(self, service, name):
        return self.entries.get((service, name))

    def set_password(self, service, name, value):
        if len(value) > 1280:
            raise OSError(1783, "CredWrite", "The stub received bad data.")
        self.entries[(service, name)] = value

    def delete_password(self, service, name):
        if (service, name) not in self.entries:
            raise KeyError(name)
        del self.entries[(service, name)]


def _windows_like_service():
    service = SecretStoreService(service_name="rdst-test-chunks")
    service._keyring = _WindowsLikeKeyring()
    return service


def test_long_secret_round_trips_through_entry_size_limit(monkeypatch):
    service = _windows_like_service()
    token = "eyJ" + "a" * 2400
    os.environ.pop("RDST_TEST_LONG", None)

    with patch.object(service, "is_available", return_value=True):
        assert service.set_secret("RDST_TEST_LONG", token)["persisted"] is True
        assert all(len(value) <= 1280 for value in service._keyring.entries.values())
        assert service.get_secret("RDST_TEST_LONG") == token

        # A shorter value replaces it and leaves no chunks behind.
        assert service.set_secret("RDST_TEST_LONG", "short")["persisted"] is True
        assert service.get_secret("RDST_TEST_LONG") == "short"
        assert list(service._keyring.entries) == [("rdst-test-chunks", "RDST_TEST_LONG")]

        service.set_secret("RDST_TEST_LONG", token)
        assert service.clear_required(["RDST_TEST_LONG"])["cleared"] == ["RDST_TEST_LONG"]
        assert service._keyring.entries == {}
    os.environ.pop("RDST_TEST_LONG", None)


def test_failed_chunk_write_keeps_the_previous_value(monkeypatch):
    service = _windows_like_service()
    os.environ.pop("RDST_TEST_LONG", None)

    with patch.object(service, "is_available", return_value=True):
        service.set_secret("RDST_TEST_LONG", "previous")
        with patch.object(service, "_set", side_effect=[True, False]):
            result = service.set_secret("RDST_TEST_LONG", "x" * 2500)
        assert result["persisted"] is False
        assert service.get_secret("RDST_TEST_LONG") == "previous"
    os.environ.pop("RDST_TEST_LONG", None)


def test_timed_out_probe_is_retried_later(monkeypatch):
    SecretStoreService._probe_cache.clear()
    SecretStoreService._probe_retry_at.clear()
    service = SecretStoreService(service_name="rdst-test-retry")
    service._keyring = Mock()
    service._keyring.get_keyring.return_value = _build_backend("WinVaultKeyring", "keyring.backends.Windows")
    service._keyring_call = Mock(side_effect=[secret_store_service._TIMEOUT, None])
    clock = [100.0]
    monkeypatch.setattr(secret_store_service.time, "monotonic", lambda: clock[0])

    assert service.is_available() is False
    assert service.is_available() is False
    clock[0] += secret_store_service._PROBE_RETRY_SECONDS + 1
    assert service.is_available() is True
    assert service._keyring_call.call_count == 2


def test_clearing_deletes_secrets_even_when_the_keyring_reads_slowly(monkeypatch):
    # Signing out must not leave tokens behind for the next start to restore, even when
    # reading the secret first times out.
    service = SecretStoreService()
    service._keyring = Mock()
    service._keyring.get_password.side_effect = RuntimeError("slow Credential Manager")
    monkeypatch.setenv("RDST_TEST_TOKEN", "x")
    result = service.clear_required(["RDST_TEST_TOKEN"])
    deleted = [call.args[1] for call in service._keyring.delete_password.call_args_list]
    assert deleted[0] == "RDST_TEST_TOKEN"
    assert "RDST_TEST_TOKEN#1" in deleted and "RDST_TEST_TOKEN#8" in deleted
    assert result["cleared"] == ["RDST_TEST_TOKEN"]
    assert "RDST_TEST_TOKEN" not in os.environ

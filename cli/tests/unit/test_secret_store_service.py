"""Unit tests for secure secret storage service."""

import os
from unittest.mock import Mock, patch

from lib.services.secret_store_service import SecretStoreService


def _build_backend(class_name: str, module_name: str):
    backend_type = type(class_name, (), {})
    backend_type.__module__ = module_name
    return backend_type()


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

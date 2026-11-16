"""Unit tests for secure secret storage service."""

import os
import sys
import threading
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace
import time
from unittest.mock import Mock, patch

import pytest
from filelock import FileLock

from shared import secret_store_service
from shared.secret_store_service import SecretStoreService


@pytest.fixture(autouse=True)
def isolated_secret_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(
        secret_store_service,
        "_secret_inventory_path",
        lambda: tmp_path / "secret-inventory.json",
    )
    monkeypatch.setattr(secret_store_service, "_keyring_file_lock", nullcontext)
    SecretStoreService._environment_owners.clear()
    SecretStoreService._environment_originals.clear()


def _build_backend(class_name: str, module_name: str):
    backend_type = type(class_name, (), {})
    backend_type.__module__ = module_name
    return backend_type()


def test_windows_inventory_discovers_historical_service_entries(monkeypatch):
    fake = ModuleType("win32ctypes.pywin32")
    fake.win32cred = SimpleNamespace(
        CredEnumerate=lambda filter_value, flags: [
            {"TargetName": "rdst-slack", "UserName": "T:v2:bot-token"},
            {
                "TargetName": "T:v1:app-token@rdst-slack",
                "UserName": "T:v1:app-token",
            },
            {"TargetName": "other", "UserName": "ignored"},
        ]
    )
    monkeypatch.setattr(secret_store_service.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32ctypes.pywin32", fake)

    assert secret_store_service.platform_secret_names("rdst-slack") == [
        "T:v1:app-token",
        "T:v2:bot-token",
    ]


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


def test_keyring_mutation_is_not_detached_after_timeout(monkeypatch):
    release = threading.Event()
    started = threading.Event()
    service = SecretStoreService()
    monkeypatch.setattr(secret_store_service, "_KEYRING_TIMEOUT", 0.01)
    results = []

    def mutate():
        started.set()
        assert release.wait(timeout=1)
        return True

    thread = threading.Thread(
        target=lambda: results.append(service._keyring_call(mutate, mutation=True))
    )
    thread.start()
    assert started.wait(timeout=1)
    assert thread.is_alive()
    release.set()
    thread.join(timeout=1)

    assert results == [True]


def test_keyring_cleanup_waits_for_inflight_mutation(tmp_path, monkeypatch):
    release = threading.Event()
    started = threading.Event()
    values = {}
    backend = Mock()

    def set_password(service, name, value):
        del service
        started.set()
        assert release.wait(timeout=1)
        values[name] = value

    backend.set_password.side_effect = set_password
    backend.get_password.side_effect = lambda service, name: values.get(name)
    backend.delete_password.side_effect = lambda service, name: values.pop(name, None)
    monkeypatch.setattr(
        secret_store_service,
        "_keyring_file_lock",
        lambda: FileLock(str(tmp_path / ".keyring.lock"), timeout=1),
    )
    setter = SecretStoreService()
    clearer = SecretStoreService()
    setter._keyring = backend
    clearer._keyring = backend
    results = []

    with (
        patch.object(setter, "is_available", return_value=True),
        patch.object(clearer, "is_available", return_value=True),
    ):
        set_thread = threading.Thread(
            target=lambda: setter.set_secret(
                "RDST_RACING_SECRET",
                "value",
                apply_to_environment=False,
            )
        )
        set_thread.start()
        assert started.wait(timeout=1)
        clear_thread = threading.Thread(
            target=lambda: results.append(
                clearer.clear_required(
                    ["RDST_RACING_SECRET"],
                    clear_environment="none",
                )
            )
        )
        clear_thread.start()
        assert clear_thread.is_alive()
        release.set()
        set_thread.join(timeout=1)
        clear_thread.join(timeout=1)

    assert values == {}
    assert results[0]["errors"] == []


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


def test_set_secret_records_keyring_entry_before_persisting(monkeypatch):
    service = SecretStoreService(service_name="rdst-test-ledger")
    service._keyring = Mock()

    with patch.object(service, "is_available", return_value=True):
        result = service.set_secret("RDST_LEDGER_SECRET", "top-secret", persist=True)

    assert result["persisted"] is True
    assert secret_store_service.stored_secret_inventory() == {
        "rdst-test-ledger": ["RDST_LEDGER_SECRET"]
    }


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
        service._keyring.get_password.side_effect = ["persisted-value", None]
        result = service.clear_required(["RDST_TEST_SECRET"])

    assert result["cleared"] == ["RDST_TEST_SECRET"]
    assert result["missing"] == []
    assert result["errors"] == []
    assert os.environ.get("RDST_TEST_SECRET") is None
    service._keyring.delete_password.assert_called_once_with(
        service.service_name,
        "RDST_TEST_SECRET",
    )


def test_clear_required_removes_verified_entries_from_inventory(monkeypatch):
    service = SecretStoreService(service_name="rdst-test-ledger")
    service._keyring = Mock()

    with patch.object(service, "is_available", return_value=True):
        service.set_secret("RDST_LEDGER_SECRET", "top-secret", persist=True)
        service._keyring.get_password.side_effect = ["top-secret", None]
        result = service.clear_required(["RDST_LEDGER_SECRET"])

    assert result["errors"] == []
    assert secret_store_service.stored_secret_inventory() == {}


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


def test_clear_required_reports_a_keyring_it_cannot_read_or_delete(monkeypatch):
    service = SecretStoreService()
    service._keyring = Mock()
    service._keyring.get_password.side_effect = RuntimeError("locked")
    service._keyring.delete_password.side_effect = RuntimeError("locked")
    monkeypatch.delenv("RDST_UNKNOWN_SECRET", raising=False)

    result = service.clear_required(["RDST_UNKNOWN_SECRET"])

    assert result["cleared"] == []
    assert "RDST_UNKNOWN_SECRET" in result["errors"][0]


def test_clear_required_without_a_keyring_has_nothing_to_remove(monkeypatch):
    service = SecretStoreService()
    service._keyring = None
    monkeypatch.delenv("RDST_UNKNOWN_SECRET", raising=False)

    result = service.clear_required(["RDST_UNKNOWN_SECRET"])

    assert result == {"cleared": [], "missing": ["RDST_UNKNOWN_SECRET"], "errors": []}


def test_clearing_an_absent_secret_on_a_slow_keyring_is_not_an_error(monkeypatch):
    service = SecretStoreService()
    service._keyring = Mock()
    # The first read times out; the delete finds nothing; the re-read answers.
    service._keyring.get_password.side_effect = [RuntimeError("slow"), None]
    service._keyring.delete_password.side_effect = RuntimeError("not found")
    monkeypatch.delenv("RDST_UNKNOWN_SECRET", raising=False)

    result = service.clear_required(["RDST_UNKNOWN_SECRET"])

    assert result["errors"] == []
    assert result["missing"] == ["RDST_UNKNOWN_SECRET"]


def test_owned_environment_cleanup_preserves_inherited_values(monkeypatch):
    inherited = "RDST_INHERITED_SECRET"
    owned = "RDST_OWNED_SECRET"
    service = SecretStoreService()
    monkeypatch.setenv(inherited, "external")
    monkeypatch.delenv(owned, raising=False)

    with patch.object(service, "is_available", return_value=False):
        service.set_secret(inherited, "temporary-override", persist=False)
        service.set_secret(owned, "local", persist=False)
        result = service.clear_required(
            [inherited, owned],
            clear_environment="owned",
        )

    assert os.environ[inherited] == "external"
    assert owned not in os.environ
    assert owned in result["cleared"]


def test_a_lazily_loaded_secret_is_cleared_from_the_environment(monkeypatch):
    service = SecretStoreService()
    service._keyring = None
    monkeypatch.delenv("RDST_LAZY_SECRET", raising=False)

    service.apply_to_environment("RDST_LAZY_SECRET", "from-keyring")
    result = service.clear_required(["RDST_LAZY_SECRET"], clear_environment="owned")

    assert "RDST_LAZY_SECRET" not in os.environ
    assert result["cleared"] == ["RDST_LAZY_SECRET"]


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


def test_lacks_secret_only_when_the_store_answers_it_is_absent():
    service = SecretStoreService()
    service._keyring = Mock()

    with patch.object(service, "is_available", return_value=True):
        service._keyring.get_password.return_value = None
        assert service.lacks_secret("ANTHROPIC_API_KEY") is True
        service._keyring.get_password.return_value = "sk-ant-key"
        assert service.lacks_secret("ANTHROPIC_API_KEY") is False
        service._keyring.get_password.side_effect = RuntimeError("locked")
        assert service.lacks_secret("ANTHROPIC_API_KEY") is False
    with patch.object(service, "is_available", return_value=False):
        assert service.lacks_secret("ANTHROPIC_API_KEY") is False

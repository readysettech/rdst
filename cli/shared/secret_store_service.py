"""Secure secret storage for RDST environment variables."""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from filelock import FileLock

from shared.constants import rdst_data_dir, rdst_instance_suffix
from shared.persistence import update_json

_KEYRING_TIMEOUT = 2
# A probe that timed out (a busy disk at startup, a slow credential service) is tried again
# after this many seconds instead of leaving the keyring off for the life of the process.
_PROBE_RETRY_SECONDS = 30
_DEAD_BACKENDS = {"NullKeyring", "NoKeyring", "ChainerBackend"}
_TIMEOUT = object()
# Clearing waits longer than reads: a secret left behind restores a signed-out session.
_CLEAR_TIMEOUT = 10
# Chunks deleted when a secret's head can't be read to say how many it has.
_MAX_CLEAR_CHUNKS = 8
# Windows Credential Manager holds at most 2560 bytes (1280 UTF-16 characters) per entry,
# and a Readyset access token can be longer. Longer values go into "<name>#<n>" entries,
# and the entry under the name itself holds the chunk count behind this header.
_MAX_ENTRY_CHARS = 1000
_MAX_CHUNKS = 64
_CHUNK_HEADER = "rdst-chunked:"


def _chunk_count(head: Any) -> int:
    if not isinstance(head, str) or not head.startswith(_CHUNK_HEADER):
        return 0
    try:
        count = int(head[len(_CHUNK_HEADER):])
    except ValueError:
        return 0
    return count if 0 < count <= _MAX_CHUNKS else 0


def _chunk_name(name: str, index: int) -> str:
    return f"{name}#{index}"


_SECRET_INVENTORY_NAME = "secret-inventory.json"
_KEYRING_LOCK_NAME = ".keyring.lock"


def _secret_inventory_path() -> Path:
    return rdst_data_dir() / _SECRET_INVENTORY_NAME


_ERROR_NOT_FOUND = 1168


def platform_secret_names(service_name: str) -> list[str]:
    """Enumerate RDST keyring usernames where the platform supports it."""
    if sys.platform != "win32":
        return []
    try:
        try:
            from win32ctypes.pywin32 import win32cred
        except ImportError:
            import win32cred

        with _keyring_file_lock():
            credentials = win32cred.CredEnumerate(None, 0)
    except Exception as exc:
        # ERROR_NOT_FOUND: the user has no stored credentials at all.
        code = getattr(exc, "winerror", None) or (exc.args[0] if exc.args else None)
        if code == _ERROR_NOT_FOUND:
            return []
        raise RuntimeError(
            f"Failed to enumerate Windows credentials for {service_name}: {exc}"
        ) from exc

    names: set[str] = set()
    compound_suffix = f"@{service_name}"
    for credential in credentials:
        target = credential.get("TargetName")
        username = credential.get("UserName")
        if (
            isinstance(username, str)
            and isinstance(target, str)
            and (target == service_name or target.endswith(compound_suffix))
        ):
            names.add(username)
    return sorted(names)


def stored_secret_inventory() -> dict[str, list[str]]:
    """Return every keyring entry RDST has attempted to persist."""
    path = _secret_inventory_path()
    if not path.exists():
        return {}
    try:
        import json

        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to read RDST secret inventory: {exc}") from exc
    if not isinstance(value, dict) or any(
        not isinstance(service, str)
        or not isinstance(names, list)
        or any(not isinstance(name, str) for name in names)
        for service, names in value.items()
    ):
        raise RuntimeError("RDST secret inventory is invalid")
    return {service: sorted(set(names)) for service, names in value.items()}


def _record_secret(service_name: str, name: str) -> None:
    def update(value):
        if not isinstance(value, dict):
            value = {}
        names = value.setdefault(service_name, [])
        if name not in names:
            names.append(name)
            names.sort()
        return value

    update_json(_secret_inventory_path(), update)


def _forget_secrets(service_name: str, names: set[str]) -> None:
    path = _secret_inventory_path()
    if not path.exists():
        return

    def update(value):
        if not isinstance(value, dict):
            return {}
        remaining = [name for name in value.get(service_name, []) if name not in names]
        if remaining:
            value[service_name] = remaining
        else:
            value.pop(service_name, None)
        return value

    update_json(path, update)


def _keyring_file_lock() -> FileLock:
    path = rdst_data_dir() / _KEYRING_LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(path), timeout=_KEYRING_TIMEOUT)


class SecretStoreService:
    """Store and restore secrets using OS keychain when available."""

    SERVICE_NAME = "rdst-web"
    _probe_cache: dict[str, bool] = {}
    _probe_retry_at: dict[str, float] = {}
    _environment_owners: set[tuple[str, str]] = set()
    _environment_originals: dict[str, str | None] = {}
    _environment_owners_lock = threading.Lock()
    _keyring_operation_lock = threading.RLock()

    def __init__(self, service_name: str | None = None):
        self.service_name = service_name or f"{self.SERVICE_NAME}{rdst_instance_suffix()}"
        self._keyring = self._load_keyring()

    def _load_keyring(self):
        try:
            import keyring

            return keyring
        except Exception:
            return None

    def _keyring_call(self, fn, *args, timeout: float | None = None, mutation: bool = False):
        if mutation:
            try:
                return fn(*args)
            except Exception:
                return _TIMEOUT

        results: queue.Queue[Any] = queue.Queue(maxsize=1)

        def call() -> None:
            try:
                results.put(fn(*args))
            except Exception:
                results.put(_TIMEOUT)

        threading.Thread(target=call, daemon=True).start()
        try:
            return results.get(timeout=_KEYRING_TIMEOUT if timeout is None else timeout)
        except queue.Empty:
            return _TIMEOUT

    def _backend_looks_viable(self) -> bool:
        try:
            backend = self._keyring.get_keyring()
            cls_name = type(backend).__name__
            module = type(backend).__module__ or ""
            if cls_name in _DEAD_BACKENDS:
                return False
            if "fail" in module or "null" in module:
                return False
            return True
        except Exception:
            return False

    def _restore_owned_environment(self, name: str) -> bool:
        with self._environment_owners_lock:
            owner = (self.service_name, name)
            if owner not in self._environment_owners:
                return False
            self._environment_owners.remove(owner)
            still_owned = any(
                owned_name == name for _, owned_name in self._environment_owners
            )
            if still_owned:
                return False
            original = self._environment_originals.pop(name, None)
            had_env = os.environ.get(name) is not None
            if original is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original
            return had_env

    def refresh_availability(self) -> None:
        """Force the next availability check to probe the keyring again."""
        SecretStoreService._probe_cache.pop(self.service_name, None)

    def is_available(self) -> bool:
        if self.service_name in SecretStoreService._probe_cache:
            retry_at = SecretStoreService._probe_retry_at.get(self.service_name)
            if retry_at is None or time.monotonic() < retry_at:
                return SecretStoreService._probe_cache[self.service_name]

        if not self._keyring:
            SecretStoreService._probe_cache[self.service_name] = False
            return False

        if not self._backend_looks_viable():
            SecretStoreService._probe_cache[self.service_name] = False
            return False

        result = self._keyring_call(
            self._keyring.get_password, self.service_name, "__rdst_probe__"
        )
        available = result is not _TIMEOUT
        SecretStoreService._probe_cache[self.service_name] = available
        if available:
            SecretStoreService._probe_retry_at.pop(self.service_name, None)
        else:
            SecretStoreService._probe_retry_at[self.service_name] = (
                time.monotonic() + _PROBE_RETRY_SECONDS
            )
        return available

    def _get(self, name: str) -> Any:
        return self._keyring_call(self._keyring.get_password, self.service_name, name)

    def _set(self, name: str, value: str) -> bool:
        result = self._keyring_call(
            self._keyring.set_password, self.service_name, name, value, mutation=True
        )
        return result is not _TIMEOUT

    def _delete_chunks(self, name: str, first: int, last: int) -> None:
        for index in range(first, last + 1):
            self._keyring_call(
                self._keyring.delete_password,
                self.service_name,
                _chunk_name(name, index),
                mutation=True,
            )

    def _write(self, name: str, value: str) -> bool:
        """Store ``value`` under ``name``, split across entries when it's too long."""
        previous = _chunk_count(self._get(name))
        if len(value) <= _MAX_ENTRY_CHARS:
            if not self._set(name, value):
                return False
            self._delete_chunks(name, 1, previous)
            return True
        parts = [
            value[start:start + _MAX_ENTRY_CHARS]
            for start in range(0, len(value), _MAX_ENTRY_CHARS)
        ]
        if len(parts) > _MAX_CHUNKS:
            return False
        # Chunks first, header last: until the header changes, readers still get the
        # previous value whole.
        for index, part in enumerate(parts, start=1):
            if not self._set(_chunk_name(name, index), part):
                return False
        if not self._set(name, f"{_CHUNK_HEADER}{len(parts)}"):
            return False
        self._delete_chunks(name, len(parts) + 1, previous)
        return True

    def track_existing_secrets(self, names: List[str]) -> None:
        """Add previously persisted entries to the durable deletion ledger."""
        with self._keyring_operation_lock, _keyring_file_lock():
            for name in names:
                _record_secret(self.service_name, name)

    def apply_to_environment(self, name: str, value: str) -> None:
        apply_owned_environment(name, value, self.service_name)

    def set_secret(
        self,
        name: str,
        value: str,
        persist: bool = True,
        apply_to_environment: bool = True,
    ) -> Dict[str, Any]:
        if apply_to_environment:
            self.apply_to_environment(name, value)

        if not persist:
            return {
                "persisted": False,
                "session_only": True,
                "message": "Secret applied for this RDST web session only.",
            }

        if not self.is_available():
            return {
                "persisted": False,
                "session_only": True,
                "message": "Secure keychain unavailable. Secret applied for this session only.",
            }

        with self._keyring_operation_lock, _keyring_file_lock():
            try:
                _record_secret(self.service_name, name)
            except Exception:
                if apply_to_environment:
                    self._restore_owned_environment(name)
                return {
                    "persisted": False,
                    "session_only": True,
                    "message": "Failed to record the secure key entry. Secret was not saved or applied.",
                }

            persisted = self._write(name, value)
        if persisted:
            return {
                "persisted": True,
                "session_only": False,
                "message": "Secret saved securely and applied to this session.",
            }
        return {
            "persisted": False,
            "session_only": True,
            "message": "Failed to persist securely. Secret applied for this session only.",
        }

    def lacks_secret(self, name: str) -> bool:
        """Whether the store answered that ``name`` is absent, rather than being unavailable or
        too slow to say."""
        return self.is_available() and self._get(name) is None

    def get_secret(self, name: str) -> Optional[str]:
        if not self.is_available():
            return None
        head = self._get(name)
        if head is _TIMEOUT:
            return None
        count = _chunk_count(head)
        if not count:
            return head
        parts = [self._get(_chunk_name(name, index)) for index in range(1, count + 1)]
        if any(not isinstance(part, str) for part in parts):
            return None
        return "".join(parts)

    def restore_required(self, required_names: List[str]) -> Dict[str, List[str]]:
        restored: List[str] = []
        missing: List[str] = []
        errors: List[str] = []
        seen = set()

        for name in required_names:
            if not name or name in seen:
                continue
            seen.add(name)

            if os.environ.get(name):
                continue

            try:
                value = self.get_secret(name)
                if value:
                    self.apply_to_environment(name, value)
                    restored.append(name)
                else:
                    missing.append(name)
            except Exception as exc:
                missing.append(name)
                errors.append(f"{name}: {exc}")

        return {
            "restored": restored,
            "missing": missing,
            "errors": errors,
        }

    def clear_required(
        self,
        required_names: List[str],
        *,
        clear_environment: Literal["all", "owned", "none"] = "all",
        update_inventory: bool = True,
    ) -> Dict[str, List[str]]:
        with self._keyring_operation_lock, _keyring_file_lock():
            return self._clear_required_locked(
                required_names,
                clear_environment=clear_environment,
                update_inventory=update_inventory,
            )

    def _clear_required_locked(
        self,
        required_names: List[str],
        *,
        clear_environment: Literal["all", "owned", "none"],
        update_inventory: bool,
    ) -> Dict[str, List[str]]:
        cleared: List[str] = []
        missing: List[str] = []
        errors: List[str] = []
        seen = set()

        # Clearing a secret (signing out) must not depend on a quick probe: the first
        # Credential Manager call can be slow. Deletes run whenever a keyring is installed.
        keyring_available = self._keyring is not None

        for name in required_names:
            if not name or name in seen:
                continue
            seen.add(name)

            had_env = False
            if clear_environment == "all":
                had_env = os.environ.pop(name, None) is not None
                with self._environment_owners_lock:
                    self._environment_owners.discard((self.service_name, name))
                    if not any(
                        owned_name == name for _, owned_name in self._environment_owners
                    ):
                        self._environment_originals.pop(name, None)
            elif clear_environment == "owned":
                had_env = self._restore_owned_environment(name)

            had_keyring = False
            if keyring_available:
                existing = self._keyring_call(
                    self._keyring.get_password, self.service_name, name,
                    timeout=_CLEAR_TIMEOUT,
                )
                if existing is not None:
                    # An unreadable head (slow or failing keyring) still gets deleted, with
                    # every chunk a long secret can have, so nothing restores the session.
                    result = self._keyring_call(
                        self._keyring.delete_password,
                        self.service_name,
                        name,
                        mutation=True,
                    )
                    chunks = (_MAX_CLEAR_CHUNKS if existing is _TIMEOUT
                              else _chunk_count(existing))
                    self._delete_chunks(name, 1, chunks)
                    verified = self._keyring_call(
                        self._keyring.get_password,
                        self.service_name,
                        name,
                        timeout=_CLEAR_TIMEOUT,
                    )
                    if verified is not _TIMEOUT and verified is not None:
                        errors.append(f"{name}: keyring entry remains after deletion")
                        continue
                    if result is _TIMEOUT and verified is _TIMEOUT:
                        # Neither deleted nor readable: the keyring may still hold the secret.
                        errors.append(f"{name}: keyring delete failed or timed out")
                        continue
                    had_keyring = existing is not _TIMEOUT or result is not _TIMEOUT

            if had_env or had_keyring:
                cleared.append(name)
            else:
                missing.append(name)

        if update_inventory and not errors:
            try:
                _forget_secrets(self.service_name, seen)
            except Exception as exc:
                errors.append(f"secret inventory update failed: {exc}")

        return {
            "cleared": cleared,
            "missing": missing,
            "errors": errors,
        }


# The class itself, for module functions that share its ownership state even when a caller
# has replaced the name SecretStoreService.
_SecretStore = SecretStoreService


def apply_owned_environment(name: str, value: str, service_name: str | None = None) -> None:
    """Set ``name`` for this process as a value RDST owns, so clearing can take it back."""
    owner = (service_name or f"{_SecretStore.SERVICE_NAME}{rdst_instance_suffix()}", name)
    with _SecretStore._environment_owners_lock:
        if not any(owned == name for _, owned in _SecretStore._environment_owners):
            _SecretStore._environment_originals[name] = os.environ.get(name)
        _SecretStore._environment_owners.add(owner)
        os.environ[name] = value

"""Secure secret storage for RDST environment variables."""

from __future__ import annotations

import os
import queue
import threading
import time
from typing import Any, Dict, List, Optional

from shared.constants import rdst_instance_suffix

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


class SecretStoreService:
    """Store and restore secrets using OS keychain when available."""

    SERVICE_NAME = "rdst-web"
    _probe_cache: dict[str, bool] = {}
    _probe_retry_at: dict[str, float] = {}

    def __init__(self, service_name: str | None = None):
        self.service_name = service_name or f"{self.SERVICE_NAME}{rdst_instance_suffix()}"
        self._keyring = self._load_keyring()

    def _load_keyring(self):
        try:
            import keyring

            return keyring
        except Exception:
            return None

    def _keyring_call(self, fn, *args, timeout: float | None = None):
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
            self._keyring.set_password, self.service_name, name, value
        )
        return result is not _TIMEOUT

    def _delete_chunks(self, name: str, first: int, last: int) -> None:
        for index in range(first, last + 1):
            self._keyring_call(
                self._keyring.delete_password, self.service_name, _chunk_name(name, index)
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

    def set_secret(
        self,
        name: str,
        value: str,
        persist: bool = True,
        apply_to_environment: bool = True,
    ) -> Dict[str, Any]:
        if apply_to_environment:
            os.environ[name] = value

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

        if self._write(name, value):
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
                    os.environ[name] = value
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

    def clear_required(self, required_names: List[str]) -> Dict[str, List[str]]:
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

            had_env = os.environ.pop(name, None) is not None
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
                        self._keyring.delete_password, self.service_name, name,
                        timeout=_CLEAR_TIMEOUT,
                    )
                    chunks = (_MAX_CLEAR_CHUNKS if existing is _TIMEOUT
                              else _chunk_count(existing))
                    self._delete_chunks(name, 1, chunks)
                    if result is _TIMEOUT and existing is not _TIMEOUT:
                        errors.append(f"{name}: keyring delete timed out")
                        continue
                    had_keyring = existing is not _TIMEOUT or result is not _TIMEOUT

            if had_env or had_keyring:
                cleared.append(name)
            else:
                missing.append(name)

        return {
            "cleared": cleared,
            "missing": missing,
            "errors": errors,
        }


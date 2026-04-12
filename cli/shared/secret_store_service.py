"""Secure secret storage for RDST environment variables."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Dict, List, Optional

_KEYRING_TIMEOUT = 2
_DEAD_BACKENDS = {"NullKeyring", "NoKeyring", "ChainerBackend"}
_TIMEOUT = object()


class SecretStoreService:
    """Store and restore secrets using OS keychain when available."""

    SERVICE_NAME = "rdst-web"
    _probe_cache: dict[str, bool] = {}

    def __init__(self, service_name: str | None = None):
        self.service_name = service_name or self.SERVICE_NAME
        self._keyring = self._load_keyring()

    def _load_keyring(self):
        try:
            import keyring

            return keyring
        except Exception:
            return None

    def _keyring_call(self, fn, *args):
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(fn, *args)
                return future.result(timeout=_KEYRING_TIMEOUT)
        except (FuturesTimeoutError, Exception):
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
        return available

    def set_secret(self, name: str, value: str, persist: bool = True) -> Dict[str, Any]:
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

        result = self._keyring_call(
            self._keyring.set_password, self.service_name, name, value
        )
        if result is not _TIMEOUT:
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
        result = self._keyring_call(self._keyring.get_password, self.service_name, name)
        return None if result is _TIMEOUT else result

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

        keyring_available = self.is_available()

        for name in required_names:
            if not name or name in seen:
                continue
            seen.add(name)

            had_env = os.environ.pop(name, None) is not None
            had_keyring = False

            if keyring_available:
                existing = self._keyring_call(
                    self._keyring.get_password, self.service_name, name
                )
                if existing is not _TIMEOUT and existing is not None:
                    result = self._keyring_call(
                        self._keyring.delete_password, self.service_name, name
                    )
                    if result is _TIMEOUT:
                        errors.append(f"{name}: keyring delete timed out")
                        continue
                    had_keyring = True

            if had_env or had_keyring:
                cleared.append(name)
            else:
                missing.append(name)

        return {
            "cleared": cleared,
            "missing": missing,
            "errors": errors,
        }


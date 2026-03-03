"""Secure secret storage for RDST web environment variables."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Dict, List, Optional

# Safety-net timeout for keyring operations (seconds). Only reached if
# the backend type check passes but the actual call still hangs.
_KEYRING_TIMEOUT = 2

# Backend classes that are known to never work (no daemon, no storage).
# Do not include the generic "Keyring" class name here: the real macOS backend
# is also named "Keyring" but lives under keyring.backends.macOS.
_DEAD_BACKENDS = {"NullKeyring", "NoKeyring", "ChainerBackend"}

# Sentinel to distinguish "keyring returned None" from "timed out / error".
_TIMEOUT = object()


class SecretStoreService:
    """Store and restore secrets using OS keychain when available."""

    SERVICE_NAME = "rdst-web"
    # Class-level cache: probe result survives across instances within
    # the same process (helps the web server; CLI is one process per run).
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
        """Run a keyring operation with timeout protection.

        All keyring calls go through here so a hung daemon
        never blocks RDST for more than _KEYRING_TIMEOUT seconds.
        Returns _TIMEOUT sentinel on timeout or error.
        """
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(fn, *args)
                return future.result(timeout=_KEYRING_TIMEOUT)
        except (FuturesTimeoutError, Exception):
            return _TIMEOUT

    def _backend_looks_viable(self) -> bool:
        """Fast check: does the keyring backend look usable?

        keyring.get_keyring() is instant — it just returns the
        already-selected backend object. We check its class name
        against known-dead backends (fail.Keyring, null, chainer)
        to avoid even attempting a probe call.
        """
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
        """Return True when a usable keyring backend exists.

        Fast path: checks the backend type (instant). If the backend
        is a known-dead type, returns False immediately with no delay.
        Slow path: only if backend looks viable, does a real probe
        with a 2-second safety-net timeout.
        """
        if self.service_name in SecretStoreService._probe_cache:
            return SecretStoreService._probe_cache[self.service_name]

        if not self._keyring:
            SecretStoreService._probe_cache[self.service_name] = False
            return False

        # Fast reject: backend type tells us instantly
        if not self._backend_looks_viable():
            SecretStoreService._probe_cache[self.service_name] = False
            return False

        # Backend looks real (macOS Keychain, Windows Vault,
        # GNOME Keyring, etc.) — do an actual probe with timeout
        result = self._keyring_call(
            self._keyring.get_password, self.service_name, "__rdst_probe__"
        )
        available = result is not _TIMEOUT
        SecretStoreService._probe_cache[self.service_name] = available
        return available

    def set_secret(self, name: str, value: str, persist: bool = True) -> Dict[str, Any]:
        """Set process env immediately and optionally persist to keychain."""
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
        """Read secret from keychain (timeout-protected)."""
        if not self.is_available():
            return None
        result = self._keyring_call(
            self._keyring.get_password, self.service_name, name
        )
        return None if result is _TIMEOUT else result

    def restore_required(self, required_names: List[str]) -> Dict[str, List[str]]:
        """Restore missing required env vars from keychain."""
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
        """Clear required env vars from process env and secure store when available."""
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

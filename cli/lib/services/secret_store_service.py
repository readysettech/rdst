"""Secure secret storage for RDST web environment variables."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


class SecretStoreService:
    """Store and restore secrets using OS keychain when available."""

    SERVICE_NAME = "rdst-web"

    def __init__(self, service_name: str | None = None):
        self.service_name = service_name or self.SERVICE_NAME
        self._keyring = self._load_keyring()

    def _load_keyring(self):
        try:
            import keyring

            return keyring
        except Exception:
            return None

    def is_available(self) -> bool:
        """Return True when a usable keyring backend exists."""
        if not self._keyring:
            return False
        try:
            # No-op lookup validates backend availability without storing data.
            self._keyring.get_password(self.service_name, "__rdst_probe__")
            return True
        except Exception:
            return False

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

        try:
            self._keyring.set_password(self.service_name, name, value)
            return {
                "persisted": True,
                "session_only": False,
                "message": "Secret saved securely and applied to this session.",
            }
        except Exception:
            return {
                "persisted": False,
                "session_only": True,
                "message": "Failed to persist securely. Secret applied for this session only.",
            }

    def get_secret(self, name: str) -> Optional[str]:
        """Read secret from keychain."""
        if not self.is_available():
            return None
        try:
            return self._keyring.get_password(self.service_name, name)
        except Exception:
            return None

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
                try:
                    existing = self._keyring.get_password(self.service_name, name)
                    if existing is not None:
                        self._keyring.delete_password(self.service_name, name)
                        had_keyring = True
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
                    continue

            if had_env or had_keyring:
                cleared.append(name)
            else:
                missing.append(name)

        return {
            "cleared": cleared,
            "missing": missing,
            "errors": errors,
        }

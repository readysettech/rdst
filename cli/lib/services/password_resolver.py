"""Unified password resolution for RDST targets.

Single source of truth for determining whether a target has a usable
password and where it comes from.  Every call site that previously had
its own ``_check_password`` / ``_has_password`` helper should use
``resolve_password`` instead.
"""

from __future__ import annotations

import os
from typing import Any, NamedTuple, Union

from .secret_store_service import SecretStoreService


class PasswordResolution(NamedTuple):
    available: bool
    source: str  # "config" | "process_env" | "secure_store" | "missing"


def _extract_password_fields(
    target_config: Union[dict, Any],
) -> tuple[str | None, str | None]:
    """Extract password and password_env from a config dict or object."""
    if isinstance(target_config, dict):
        return target_config.get("password"), target_config.get("password_env")
    return getattr(target_config, "password", None), getattr(target_config, "password_env", None)


def resolve_password(
    target_config: Union[dict, Any],
    secret_store: SecretStoreService | None = None,
) -> PasswordResolution:
    """Resolve whether *target_config* has an accessible password.

    Resolution priority:
      1. Direct ``password`` field in config  -> ``"config"``
      2. ``password_env`` present in ``os.environ`` -> ``"process_env"``
      3. ``password_env`` found via *secret_store* (keychain) -> ``"secure_store"``
      4. Nothing found -> ``"missing"``

    Parameters
    ----------
    target_config:
        A dict or object with ``password`` / ``password_env`` fields.
    secret_store:
        Optional :class:`SecretStoreService`.  When *None* a throwaway
        instance is created (it's stateless, just wraps keyring).
    """
    password, password_env = _extract_password_fields(target_config)

    if password:
        return PasswordResolution(available=True, source="config")

    if password_env:
        if os.environ.get(password_env):
            return PasswordResolution(available=True, source="process_env")

        store = secret_store or SecretStoreService()
        if store.get_secret(password_env):
            return PasswordResolution(available=True, source="secure_store")

    return PasswordResolution(available=False, source="missing")


def resolve_password_value(
    target_config: Union[dict, Any],
    secret_store: SecretStoreService | None = None,
) -> str:
    """Return the actual password string for *target_config*.

    Same resolution priority as :func:`resolve_password`:
      1. Direct ``password`` field in config
      2. ``password_env`` present in ``os.environ``
      3. ``password_env`` found via *secret_store* (keychain)
      4. Empty string (no password available)

    When the value comes from the secure store it is also injected into
    ``os.environ`` so that child processes (e.g. ``rdst analyze`` subprocesses)
    inherit it without needing their own keyring access.
    """
    password, password_env = _extract_password_fields(target_config)

    if password:
        return password

    if password_env:
        env_val = os.environ.get(password_env)
        if env_val:
            return env_val

        store = secret_store or SecretStoreService()
        secret_val = store.get_secret(password_env)
        if secret_val:
            # Inject so subprocess.run children inherit it
            os.environ[password_env] = secret_val
            return secret_val

    return ""

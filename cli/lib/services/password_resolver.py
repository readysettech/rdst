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
    if isinstance(target_config, dict):
        password = target_config.get("password")
        password_env = target_config.get("password_env")
    else:
        password = getattr(target_config, "password", None)
        password_env = getattr(target_config, "password_env", None)

    if password:
        return PasswordResolution(available=True, source="config")

    if password_env:
        if os.environ.get(password_env):
            return PasswordResolution(available=True, source="process_env")

        store = secret_store or SecretStoreService()
        if store.get_secret(password_env):
            return PasswordResolution(available=True, source="secure_store")

    return PasswordResolution(available=False, source="missing")

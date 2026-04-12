"""Unified password resolution for RDST targets."""

from __future__ import annotations

import os
from typing import Any, NamedTuple, Union

from .aws_secrets import resolve_secret
from .secret_store_service import SecretStoreService


class PasswordResolution(NamedTuple):
    available: bool
    source: str


def _extract_password_fields(
    target_config: Union[dict, Any],
) -> tuple[str | None, str | None]:
    if isinstance(target_config, dict):
        return target_config.get("password"), target_config.get("password_env")
    return getattr(target_config, "password", None), getattr(
        target_config, "password_env", None
    )


def resolve_password(
    target_config: Union[dict, Any],
    secret_store: SecretStoreService | None = None,
) -> PasswordResolution:
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
    password, password_env = _extract_password_fields(target_config)

    if password:
        return password

    if password_env:
        env_val = os.environ.get(password_env)
        if env_val:
            return env_val

    secret_arn = (
        target_config.get("password_secret_arn")
        if isinstance(target_config, dict)
        else getattr(target_config, "password_secret_arn", None)
    )
    if secret_arn:
        try:
            secret_key = (
                target_config.get("password_secret_key", "password")
                if isinstance(target_config, dict)
                else getattr(target_config, "password_secret_key", "password")
            )
            secret_val = resolve_secret(secret_arn, secret_key=secret_key)
            if secret_val:
                if password_env:
                    os.environ[password_env] = secret_val
                return secret_val
        except Exception:
            pass

    if password_env:
        store = secret_store or SecretStoreService()
        secret_val = store.get_secret(password_env)
        if secret_val:
            os.environ[password_env] = secret_val
            return secret_val

    return ""

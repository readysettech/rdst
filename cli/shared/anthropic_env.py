"""Helpers for resolving Anthropic credentials across web and CLI flows."""

from __future__ import annotations

import os
from typing import Any

from shared.config.targets import TargetsConfig
from shared.secret_store_service import SecretStoreService

ANTHROPIC_API_KEY_NAMES = ("ANTHROPIC_API_KEY", "RDST_TRIAL_TOKEN")
def _load_trial_config(cfg: Any | None = None) -> dict[str, Any]:
    if cfg is None:
        try:
            cfg = TargetsConfig()
            cfg.load()
        except Exception:
            return {}

    try:
        trial_config = cfg.get_trial_config()
    except Exception:
        trial_config = None

    return trial_config if isinstance(trial_config, dict) else {}


def get_anthropic_source(
    secret_store: Any | None = None,
    cfg: Any | None = None,
) -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "process_env"
    if os.environ.get("RDST_TRIAL_TOKEN"):
        return "trial"

    trial_config = _load_trial_config(cfg)
    trial_token = trial_config.get("token")
    trial_status = trial_config.get("status")
    if trial_status == "exhausted" and trial_token:
        return "trial_exhausted"
    if trial_status == "active" and trial_token:
        return "trial"

    store = secret_store
    if store is None:
        try:
            store = SecretStoreService()
        except Exception:
            store = None

    if store is not None:
        direct_key = store.get_secret("ANTHROPIC_API_KEY")
        if direct_key:
            return "secure_store"

        trial_key = store.get_secret("RDST_TRIAL_TOKEN")
        if trial_key:
            return "trial"

    return "missing"


def get_anthropic_api_key(
    secret_store: Any | None = None,
    cfg: Any | None = None,
) -> str | None:
    direct_key = os.environ.get("ANTHROPIC_API_KEY")
    if direct_key:
        return direct_key

    trial_env = os.environ.get("RDST_TRIAL_TOKEN")
    if trial_env:
        return trial_env

    trial_config = _load_trial_config(cfg)
    trial_token = trial_config.get("token")
    if trial_token and trial_config.get("status") == "active":
        return trial_token

    store = secret_store
    if store is None:
        try:
            store = SecretStoreService()
        except Exception:
            store = None

    if store is None:
        return None

    for name in ANTHROPIC_API_KEY_NAMES:
        value = store.get_secret(name)
        if value:
            os.environ[name] = value
            return value

    return None


def has_anthropic_api_key(
    secret_store: Any | None = None,
    cfg: Any | None = None,
) -> bool:
    return get_anthropic_api_key(secret_store=secret_store, cfg=cfg) is not None


__all__ = [
    "get_anthropic_api_key",
    "get_anthropic_source",
    "has_anthropic_api_key",
]

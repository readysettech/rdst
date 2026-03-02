"""Helpers for resolving Anthropic API keys across web and CLI flows."""

from __future__ import annotations

import os

ANTHROPIC_API_KEY_NAMES = ("ANTHROPIC_API_KEY", "RDST_TRIAL_TOKEN")


def get_anthropic_api_key() -> str | None:
    """Return the first configured Anthropic API key.

    Resolution order: env vars → OS keyring (with timeout) → None.
    """
    # 1. Check environment variables
    for name in ANTHROPIC_API_KEY_NAMES:
        value = os.environ.get(name)
        if value:
            return value

    # 2. Check OS keyring (set via rdst web)
    try:
        from .secret_store_service import SecretStoreService

        store = SecretStoreService()
        for name in ANTHROPIC_API_KEY_NAMES:
            value = store.get_secret(name)
            if value:
                os.environ[name] = value  # cache in env for downstream use
                return value
    except Exception:
        pass

    return None


def _has_active_trial() -> bool:
    """Check if an active RDST trial token exists in config."""
    try:
        from lib.cli.rdst_cli import TargetsConfig
        cfg = TargetsConfig()
        cfg.load()
        return cfg.is_trial_active()
    except Exception:
        return False


def has_anthropic_api_key() -> bool:
    """Return True when an Anthropic API key or active trial is available."""
    return get_anthropic_api_key() is not None or _has_active_trial()

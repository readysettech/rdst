"""Helpers for resolving Anthropic API keys across web and CLI flows."""

from __future__ import annotations

import os

ANTHROPIC_API_KEY_NAMES = ("RDST_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")


def get_anthropic_api_key() -> str | None:
    """Return the first configured Anthropic API key, preferring RDST-specific env."""
    for name in ANTHROPIC_API_KEY_NAMES:
        value = os.environ.get(name)
        if value:
            return value
    return None


def has_anthropic_api_key() -> bool:
    """Return True when an Anthropic API key is available."""
    return get_anthropic_api_key() is not None

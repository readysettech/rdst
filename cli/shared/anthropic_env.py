"""Helpers for resolving Anthropic credentials across web and CLI flows."""

from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any

from shared.secret_store_service import SecretStoreService, apply_owned_environment

ANTHROPIC_API_KEY_NAMES = ("ANTHROPIC_API_KEY",)

# Anthropic keys are dash-separated URL-safe base64; any other character means a mistyped or
# mangled paste, and a non-ASCII one cannot even be sent as a request header.
_ANTHROPIC_KEY_ALPHABET = re.compile(r"[A-Za-z0-9_-]+")
MALFORMED_ANTHROPIC_KEY_MESSAGE = (
    "That isn't a valid Anthropic API key. Keys contain only letters, digits, "
    "hyphens, and underscores; copy the key from the Anthropic Console and paste it again."
)


def is_well_formed_anthropic_key(key: str) -> bool:
    return _ANTHROPIC_KEY_ALPHABET.fullmatch(key) is not None


# RDST copies a key saved in Settings into its own environment, so the key the environment
# supplied at startup is only known from this snapshot, taken before any stored key is restored.
# It takes precedence over the stored key again after every restart.
_STARTUP_ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY") or None


def startup_anthropic_key() -> str | None:
    return _STARTUP_ANTHROPIC_KEY


def get_anthropic_source(
    secret_store: Any | None = None,
    cfg: Any | None = None,
) -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "process_env"

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

    return "missing"


def get_anthropic_api_key(
    secret_store: Any | None = None,
    cfg: Any | None = None,
) -> str | None:
    direct_key = os.environ.get("ANTHROPIC_API_KEY")
    if direct_key:
        return direct_key

    store = secret_store
    if store is None:
        try:
            store = SecretStoreService()
        except Exception:
            store = None

    if store is None:
        return None

    value = store.get_secret("ANTHROPIC_API_KEY")
    if value:
        apply_owned_environment("ANTHROPIC_API_KEY", value)
        return value

    return None


def has_anthropic_api_key(
    secret_store: Any | None = None,
    cfg: Any | None = None,
) -> bool:
    return get_anthropic_api_key(secret_store=secret_store, cfg=cfg) is not None


_VALIDITY_TTL_SECONDS = 60.0
# Validity is cached by a fingerprint of the resolved key: a changed key
# re-validates immediately while repeated page loads reuse the last result
# instead of pinging Anthropic every time.
_validity_cache: dict[str, tuple[dict[str, Any], float]] = {}
# Anthropic never accepts a key again once it rejected it, so a rejection outlives the cache.
_rejected_keys: set[str] = set()


def clear_anthropic_validity_cache() -> None:
    """Drop cached validity results (used by tests)."""
    _validity_cache.clear()
    _rejected_keys.clear()


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def anthropic_key_rejected(
    secret_store: Any | None = None,
    cfg: Any | None = None,
) -> bool:
    """Whether the resolved key is known to be unusable, without asking Anthropic."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        store = secret_store
        if store is None:
            try:
                store = SecretStoreService()
            except Exception:
                return False
        key = store.get_secret("ANTHROPIC_API_KEY")
    if not key:
        return False
    return not is_well_formed_anthropic_key(key) or _fingerprint(key) in _rejected_keys


def check_anthropic_key(key: str) -> dict[str, Any]:
    """Ask Anthropic whether ``key`` authenticates, with one minimal request.

    Returns ``{"valid": bool, "reason": "ok" | "rejected" | "provider_error", "model": ...}``.
    A key with characters outside the key alphabet is ``rejected`` without a request. With AI
    off in a build that asks first, nothing is sent and the key reads as unchecked
    (``provider_error``). Results are cached per key. Blocking (network I/O): callers on the
    event loop must offload it.
    """
    if not is_well_formed_anthropic_key(key):
        return {"valid": False, "reason": "rejected", "model": None}
    from shared.ai_data_consent import ai_data_sharing_allowed

    if not ai_data_sharing_allowed():
        return {"valid": False, "reason": "provider_error", "model": None}

    fingerprint = _fingerprint(key)
    cached = _validity_cache.get(fingerprint)
    now = time.monotonic()
    if cached is not None and cached[1] > now:
        return dict(cached[0])

    from shared.llm_manager import LLMError, LLMManager
    from shared.llm_manager.claude_provider import AnthropicModel

    model = AnthropicModel.HAIKU_4_5.value
    try:
        LLMManager(enforce_data_consent=False).query(
            system_message="ping",
            user_query="ping",
            provider="claude",
            api_key=key,
            model=model,
            max_tokens=1,
            temperature=0,
            purpose="anthropic_key_validation",
        )
        result: dict[str, Any] = {"valid": True, "reason": "ok", "model": model}
    except LLMError as exc:
        rejected = exc.code == "ANTHROPIC_AUTH_INVALID"
        result = {
            "valid": False,
            "reason": "rejected" if rejected else "provider_error",
            "model": model,
        }
    except Exception:
        result = {"valid": False, "reason": "provider_error", "model": model}

    _validity_cache[fingerprint] = (result, now + _VALIDITY_TTL_SECONDS)
    if result["reason"] == "rejected":
        _rejected_keys.add(fingerprint)
    return dict(result)


def validate_anthropic_key(
    secret_store: Any | None = None,
    cfg: Any | None = None,
) -> dict[str, Any]:
    """Report whether the resolved Anthropic key actually authenticates.

    Presence (`has_anthropic_api_key`) only proves a key is set; a stale,
    revoked, or mistyped key still looks "configured" while every AI feature
    fails at use. This checks the same key the AI features will use.

    Returns ``{"valid": bool, "reason": str, "model": str | None, "source": str}`` where
    reason is ``ok`` | ``rejected`` | ``no_key`` | ``provider_error``. Blocking
    (network I/O): callers on the event loop must offload via ``to_thread``.
    """
    key = get_anthropic_api_key(secret_store=secret_store, cfg=cfg)
    if key is None:
        return {"valid": False, "reason": "no_key", "model": None}
    result = check_anthropic_key(key)
    result["source"] = get_anthropic_source(secret_store=secret_store, cfg=cfg)
    return result


__all__ = [
    "anthropic_key_rejected",
    "check_anthropic_key",
    "clear_anthropic_validity_cache",
    "get_anthropic_api_key",
    "get_anthropic_source",
    "has_anthropic_api_key",
    "is_well_formed_anthropic_key",
    "startup_anthropic_key",
    "validate_anthropic_key",
]

"""Helpers for resolving Anthropic credentials across web and CLI flows."""

from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any

from shared.secret_store_service import SecretStoreService

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
        os.environ["ANTHROPIC_API_KEY"] = value
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


def validate_anthropic_key(
    secret_store: Any | None = None,
    cfg: Any | None = None,
) -> dict[str, Any]:
    """Report whether the resolved Anthropic key actually authenticates.

    Presence (`has_anthropic_api_key`) only proves a key is set; a stale,
    revoked, or mistyped key still looks "configured" while every AI feature
    fails at use. This makes a minimal authenticated request (cheapest model,
    one output token) to tell "configured" from "working".

    The ping goes through ``LLMManager``, which resolves the key via
    ``resolve_api_key()`` — so the probe reports on the *same* key the AI
    features will use.

    Returns ``{"valid": bool, "reason": str, "model": str | None}`` where
    reason is ``ok`` | ``rejected`` | ``no_key`` | ``provider_error``. A key with
    characters outside the key alphabet is ``rejected`` without a request. Blocking
    (network I/O) — callers on the event loop must offload via ``to_thread``.
    """
    key = get_anthropic_api_key(secret_store=secret_store, cfg=cfg)
    if key is None:
        return {"valid": False, "reason": "no_key", "model": None}
    if not is_well_formed_anthropic_key(key):
        source = get_anthropic_source(secret_store=secret_store, cfg=cfg)
        return {"valid": False, "reason": "rejected", "model": None, "source": source}

    fingerprint = _fingerprint(key)
    cached = _validity_cache.get(fingerprint)
    now = time.monotonic()
    if cached is not None and cached[1] > now:
        return cached[0]

    from shared.llm_manager import LLMError, LLMManager
    from shared.llm_manager.claude_provider import AnthropicModel

    source = get_anthropic_source(secret_store=secret_store, cfg=cfg)
    model = AnthropicModel.HAIKU_4_5.value
    try:
        LLMManager().query(
            system_message="ping",
            user_query="ping",
            provider="claude",
            api_key=key,
            model=model,
            max_tokens=1,
            temperature=0,
            purpose="anthropic_key_validation",
        )
        result: dict[str, Any] = {
            "valid": True,
            "reason": "ok",
            "model": model,
            "source": source,
        }
    except LLMError as exc:
        rejected = exc.code == "ANTHROPIC_AUTH_INVALID"
        result = {
            "valid": False,
            "reason": "rejected" if rejected else "provider_error",
            "model": model,
            "source": source,
        }
    except Exception:
        result = {
            "valid": False,
            "reason": "provider_error",
            "model": model,
            "source": source,
        }

    _validity_cache[fingerprint] = (result, now + _VALIDITY_TTL_SECONDS)
    if result["reason"] == "rejected":
        _rejected_keys.add(fingerprint)
    return result


__all__ = [
    "anthropic_key_rejected",
    "clear_anthropic_validity_cache",
    "get_anthropic_api_key",
    "get_anthropic_source",
    "has_anthropic_api_key",
    "is_well_formed_anthropic_key",
    "validate_anthropic_key",
]

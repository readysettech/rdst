"""Helpers for resolving Anthropic credentials across web and CLI flows."""

from __future__ import annotations

import hashlib
import os
import time
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


_VALIDITY_TTL_SECONDS = 60.0
# Validity is cached by a fingerprint of the resolved key: a changed key
# re-validates immediately while repeated page loads reuse the last result
# instead of pinging Anthropic every time.
_validity_cache: dict[str, tuple[dict[str, Any], float]] = {}


def clear_anthropic_validity_cache() -> None:
    """Drop cached validity results (used by tests)."""
    _validity_cache.clear()


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
    features will use, and it cooperates with the keyring-precedence fix
    (a present own key beating an exhausted trial) rather than masking it.

    Returns ``{"valid": bool, "reason": str, "model": str | None}`` where
    reason is ``ok`` | ``rejected`` | ``no_key`` | ``provider_error``. Blocking
    (network I/O) — callers on the event loop must offload via ``to_thread``.
    """
    key = get_anthropic_api_key(secret_store=secret_store, cfg=cfg)
    if key is None:
        return {"valid": False, "reason": "no_key", "model": None}

    fingerprint = hashlib.sha256(key.encode()).hexdigest()[:16]
    cached = _validity_cache.get(fingerprint)
    now = time.monotonic()
    if cached is not None and cached[1] > now:
        return cached[0]

    from shared.llm_manager import LLMError, LLMManager
    from shared.llm_manager.claude_provider import AnthropicModel

    model = AnthropicModel.HAIKU_4_5.value
    try:
        LLMManager().query(
            system_message="ping",
            user_query="ping",
            model=model,
            max_tokens=1,
            temperature=0,
        )
        result: dict[str, Any] = {"valid": True, "reason": "ok", "model": model}
    except LLMError as exc:
        rejected = exc.code in ("ANTHROPIC_AUTH_INVALID", "TRIAL_AUTH_INVALID")
        result = {
            "valid": False,
            "reason": "rejected" if rejected else "provider_error",
            "model": model,
        }
    except Exception:
        result = {"valid": False, "reason": "provider_error", "model": model}

    _validity_cache[fingerprint] = (result, now + _VALIDITY_TTL_SECONDS)
    return result


__all__ = [
    "clear_anthropic_validity_cache",
    "get_anthropic_api_key",
    "get_anthropic_source",
    "has_anthropic_api_key",
    "validate_anthropic_key",
]

"""Helpers shared by the Supabase, Neon and DigitalOcean provider modules.

Each provider owns its own API paths, credential path and payload shapes; this
module owns the parts all three spell the same way: bearer GETs, JSON list
coercion, target naming, and the status envelope the discovery UI polls.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from typing import Any, Callable, Optional

import requests

from shared.password_resolver import derive_password_env as password_env_for
from shared.secret_store_service import SecretStoreService

REQUEST_TIMEOUT = 10.0

# Status decorations (organization lists, account names) rarely change, but
# the UI polls status every couple of seconds.
STATUS_TTL_SECONDS = 60.0
_STATUS_CACHES: dict[str, "CredentialCache"] = {}
_STATUS_CACHE_LOCK = threading.Lock()

# Keyring reads cost a round trip to the OS keychain; a status poll and a
# discovery run in the same breath should not pay for it twice.
SECRET_TTL_SECONDS = 5.0
_SECRET_CACHE: dict[str, tuple[float, Optional[str]]] = {}
_SECRET_LOCK = threading.Lock()
_SECRET_STORE: Optional[SecretStoreService] = None


def secret_store() -> SecretStoreService:
    """The process-wide secret store; opening one probes the OS keychain."""
    global _SECRET_STORE
    if _SECRET_STORE is None:
        _SECRET_STORE = SecretStoreService()
    return _SECRET_STORE


def read_secret(name: str) -> Optional[str]:
    """Read a secret from the environment, falling back to the keyring."""
    value = os.environ.get(name)
    if value:
        return value.strip() or None

    now = time.monotonic()
    with _SECRET_LOCK:
        cached = _SECRET_CACHE.get(name)
        if cached is not None and now - cached[0] < SECRET_TTL_SECONDS:
            return cached[1]
    try:
        stored = secret_store().get_secret(name)
    except Exception:
        stored = None
    resolved = stored.strip() if stored else None
    with _SECRET_LOCK:
        _SECRET_CACHE[name] = (time.monotonic(), resolved)
    return resolved


def forget_secret(name: str) -> None:
    """Drop a cached secret so the next read sees a store that just changed."""
    with _SECRET_LOCK:
        _SECRET_CACHE.pop(name, None)


class CredentialCache:
    """Memo for one value, held per credential and for a bounded time."""

    def __init__(self, ttl: float = STATUS_TTL_SECONDS) -> None:
        self.ttl = ttl
        self._lock = threading.Lock()
        self._key: Optional[str] = None
        self._loaded_at = 0.0
        self._value: Any = None

    def get(self, credential: str, load: Callable[[], Any]) -> Any:
        key = hashlib.sha256(credential.encode("utf-8")).hexdigest()
        now = time.monotonic()
        with self._lock:
            if self._key == key and now - self._loaded_at < self.ttl:
                return self._value
        value = load()
        with self._lock:
            self._key = key
            self._loaded_at = time.monotonic()
            self._value = value
        return value

    def clear(self) -> None:
        with self._lock:
            self._key = None
            self._value = None


def status_cache(name: str) -> CredentialCache:
    """A named cache, shared between a provider's discovery and auth modules."""
    with _STATUS_CACHE_LOCK:
        return _STATUS_CACHES.setdefault(name, CredentialCache())


def bearer_get(
    url: str,
    token: str,
    params: Optional[dict[str, Any]] = None,
    timeout: float = REQUEST_TIMEOUT,
) -> requests.Response:
    """GET a provider API endpoint with a bearer token."""
    return requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params=params,
        timeout=timeout,
    )


def json_items(response: requests.Response, field: str = "data") -> list[dict[str, Any]]:
    """Read a list of objects out of a response, bare or wrapped in `field`."""
    body = response.json()
    if isinstance(body, dict):
        body = body.get(field) or []
    if not isinstance(body, list):
        return []
    return [item for item in body if isinstance(item, dict)]


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def unique_name(name: str, used_names: set[str], suffix: str) -> str:
    """Reserve a target name, disambiguating a collision with `suffix`."""
    if name in used_names:
        name = f"{name}-{suffix}"
    used_names.add(name)
    return name


def provider_status(
    api_name: str,
    credential: Callable[[], tuple[Optional[str], Optional[str]]],
    probe: Callable[[str], requests.Response],
    signed_out_detail: str,
    rejected_detail: str,
    connected_detail: Callable[[str, Optional[str], dict[str, Any]], str],
    extra: Optional[dict[str, Any]] = None,
    collect: Optional[Callable[[], bool]] = None,
) -> dict[str, Any]:
    """Report whether this machine can talk to a provider API.

    `probe` is the one call made on every check; `connected_detail` writes the
    provider's own decorations into the status it is handed.
    """
    status: dict[str, Any] = {"connected": False, "method": None, "detail": None}
    status.update(extra or {})

    token, method = credential()
    if token is None and collect is not None and collect():
        # A finished browser approval may still be parked at the broker if the
        # UI's poll loop died with its component; collecting here lets any
        # status check complete the sign-in.
        token, method = credential()
    if token is None:
        status["detail"] = signed_out_detail
        return status

    status["method"] = method
    try:
        response = probe(token)
    except requests.RequestException as exc:
        status["detail"] = f"Could not reach the {api_name} API: {exc}"
        return status

    if response.status_code == 200:
        status["connected"] = True
        status["detail"] = connected_detail(token, method, status)
        return status

    if response.status_code in (401, 403):
        # Method is retained so the UI can say "expired" instead of "signed out".
        api_detail = (response.text or "").strip()[:200]
        status["detail"] = rejected_detail + (f" ({api_detail})" if api_detail else "")
        return status

    status["detail"] = f"{api_name} API returned {response.status_code}"
    return status

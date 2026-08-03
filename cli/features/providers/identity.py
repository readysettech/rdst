"""Best-effort provider account identity capture.

Provider identities are analytics pointers only. They never become entries in
``[[emails]]``, never imply mailbox verification, and never contain tokens.
Network and persistence work runs outside provider connect request paths.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from pathlib import Path
from typing import Any, Optional

import requests

from shared.config.targets import TargetsConfig

from .provider_common import REQUEST_TIMEOUT, bearer_get, read_secret

logger = logging.getLogger(__name__)

SUPABASE_ACCESS_TOKEN = "SUPABASE_ACCESS_TOKEN"
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
SUPPORTED_PROVIDERS = frozenset(
    {"aws", "digitalocean", "supabase", "neon"}
)

_CAPTURE_LOCK = threading.Lock()
_CONFIG_WRITE_LOCK = threading.Lock()
_CAPTURE_ATTEMPTS: set[str] = set()
_LOGGED_FAILURES: set[str] = set()


def _text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _compact(identity: dict[str, Any]) -> Optional[dict[str, Any]]:
    compact = {key: value for key, value in identity.items() if value is not None}
    return compact or None


def parse_digitalocean_identity(body: Any) -> Optional[dict[str, Any]]:
    account = body.get("account") if isinstance(body, dict) else None
    if not isinstance(account, dict):
        return None
    verified = account.get("email_verified")
    return _compact(
        {
            "email": _text(account.get("email")),
            "name": _text(account.get("name")),
            "email_verified": verified if isinstance(verified, bool) else None,
        }
    )


def parse_supabase_identity(body: Any) -> Optional[dict[str, Any]]:
    if not isinstance(body, dict):
        return None
    return _compact(
        {
            "email": _text(body.get("primary_email")),
            "name": _text(body.get("username")),
        }
    )


def parse_neon_identity(body: Any) -> Optional[dict[str, Any]]:
    if not isinstance(body, dict):
        return None
    user = body.get("user") if isinstance(body.get("user"), dict) else body
    return _compact(
        {
            "email": _text(user.get("email")),
            "name": _text(user.get("name")),
        }
    )


def aws_email_from_arn(arn: Any) -> Optional[str]:
    """SSO assumed-role ARNs end in the role session name, usually email."""
    value = _text(arn)
    if not value or "/" not in value:
        return None
    candidate = value.rsplit("/", 1)[-1]
    return candidate if EMAIL_RE.fullmatch(candidate) else None


def _log_once(key: str, message: str, *args: Any) -> None:
    with _CAPTURE_LOCK:
        if key in _LOGGED_FAILURES:
            return
        _LOGGED_FAILURES.add(key)
    logger.info(message, *args)


def _response_identity(
    provider: str,
    response: requests.Response,
    parser,
) -> Optional[dict[str, Any]]:
    if response.status_code != 200:
        _log_once(
            f"{provider}:{response.status_code}",
            "%s identity endpoint returned %s; skipping identity capture",
            provider,
            response.status_code,
        )
        return None
    try:
        return parser(response.json())
    except (ValueError, TypeError):
        _log_once(
            f"{provider}:json",
            "%s identity endpoint returned unreadable JSON",
            provider,
        )
        return None


def fetch_digitalocean_identity(token: str) -> Optional[dict[str, Any]]:
    try:
        response = bearer_get(
            "https://api.digitalocean.com/v2/account", token, timeout=REQUEST_TIMEOUT
        )
    except requests.RequestException:
        _log_once("digitalocean:transport", "DigitalOcean identity lookup failed")
        return None
    return _response_identity("digitalocean", response, parse_digitalocean_identity)


def fetch_supabase_identity(token: str) -> Optional[dict[str, Any]]:
    tokens = [token]
    fallback = read_secret(SUPABASE_ACCESS_TOKEN)
    if fallback and fallback != token:
        tokens.append(fallback)

    for index, candidate in enumerate(tokens):
        try:
            response = bearer_get(
                "https://api.supabase.com/v1/profile",
                candidate,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException:
            _log_once("supabase:transport", "Supabase identity lookup failed")
            return None
        if response.status_code == 200:
            return _response_identity("supabase", response, parse_supabase_identity)
        if response.status_code not in (401, 403) or index == len(tokens) - 1:
            return _response_identity("supabase", response, parse_supabase_identity)
    return None


def fetch_neon_identity(token: str) -> Optional[dict[str, Any]]:
    try:
        response = bearer_get(
            "https://console.neon.tech/api/v2/users/me",
            token,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        _log_once("neon:transport", "Neon identity lookup failed")
        return None
    # Organization API keys are intentionally unable to call /users/me.
    return _response_identity("neon", response, parse_neon_identity)


def fetch_provider_identity(
    provider: str, token: str
) -> Optional[dict[str, Any]]:
    if provider == "digitalocean":
        return fetch_digitalocean_identity(token)
    if provider == "supabase":
        return fetch_supabase_identity(token)
    if provider == "neon":
        return fetch_neon_identity(token)
    return None


def store_provider_identity(
    provider: str,
    identity: dict[str, Any],
    config_path: Optional[str | Path] = None,
) -> bool:
    if provider not in SUPPORTED_PROVIDERS or not identity:
        return False
    with _CONFIG_WRITE_LOCK:
        config = TargetsConfig(path=str(config_path) if config_path else None)
        config.load()
        config.set_provider_identity(provider, identity)
        config.save()
    return True


def capture_provider_identity(
    provider: str,
    token: str,
    config_path: Optional[str | Path] = None,
) -> bool:
    try:
        identity = fetch_provider_identity(provider, token)
        return bool(identity) and store_provider_identity(
            provider, identity or {}, config_path
        )
    except Exception:
        _log_once(f"{provider}:unexpected", "%s identity capture failed", provider)
        return False


def capture_provider_identity_async(provider: str, token: str) -> None:
    """Schedule one best-effort capture per credential without blocking login."""
    fingerprint = f"{provider}:{hashlib.sha256(token.encode()).hexdigest()}"
    with _CAPTURE_LOCK:
        if fingerprint in _CAPTURE_ATTEMPTS:
            return
        _CAPTURE_ATTEMPTS.add(fingerprint)
    threading.Thread(
        target=capture_provider_identity,
        args=(provider, token),
        name=f"rdst-{provider}-identity",
        daemon=True,
    ).start()


def capture_aws_identity_async(arn: Any) -> None:
    email = aws_email_from_arn(arn)
    if not email:
        return
    fingerprint = f"aws:{email}"
    with _CAPTURE_LOCK:
        if fingerprint in _CAPTURE_ATTEMPTS:
            return
        _CAPTURE_ATTEMPTS.add(fingerprint)
    threading.Thread(
        target=store_provider_identity,
        args=("aws", {"email": email}),
        name="rdst-aws-identity",
        daemon=True,
    ).start()

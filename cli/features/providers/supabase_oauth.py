"""Supabase Management API OAuth and the in-process token store.

Sign-in normally runs through the keyservice OAuth broker: the client secret
lives only in the Worker, so nothing sensitive ships to a user machine. This
process holds a one-time pickup key, hands the browser the broker's authorize
URL, and picks the tokens up once the callback completes. The broker plumbing
itself lives in provider_oauth.py.

Direct code exchange against Supabase stays available for development, and is
used only when RDST_SUPABASE_OAUTH_CLIENT_ID and RDST_SUPABASE_OAUTH_CLIENT_SECRET
are both present.

OAuth tokens are never written to disk or keyring: restarting rdst signs the
user out.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from typing import Any, Optional
from urllib.parse import urlencode

import requests

from .provider_common import read_secret, status_cache
from .provider_oauth import REQUEST_TIMEOUT, ProviderBrokerError, ProviderOAuth

API_BASE = "https://api.supabase.com"
AUTHORIZE_URL = f"{API_BASE}/v1/oauth/authorize"
TOKEN_URL = f"{API_BASE}/v1/oauth/token"
REVOKE_URL = f"{API_BASE}/v1/oauth/revoke"

BROKER_PROVIDER = "supabase"

CALLBACK_PATH = "/api/fleet/supabase-callback"

CLIENT_ID_NAME = "RDST_SUPABASE_OAUTH_CLIENT_ID"
CLIENT_SECRET_NAME = "RDST_SUPABASE_OAUTH_CLIENT_SECRET"

SIGNED_OUT_DETAIL = (
    "Not signed in to Supabase. Signing in opens your browser; sessions last "
    "until rdst web restarts."
)


class SupabaseOAuthUnconfigured(RuntimeError):
    """Raised when no OAuth client id/secret is available on this machine."""


class SupabaseAuthError(RuntimeError):
    """Raised when Supabase rejects a token exchange or refresh."""


class SupabaseBrokerError(ProviderBrokerError):
    """Raised when the keyservice OAuth broker cannot be used."""


_OAUTH = ProviderOAuth(
    provider=BROKER_PROVIDER,
    display_name="Supabase",
    broker_error=SupabaseBrokerError,
    auth_error=SupabaseAuthError,
)

# Kept as a dictionary so tests and diagnostics can inspect active logins.
LOGIN_REGISTRY: dict[str, dict[str, Any]] = _OAUTH.registry


def dev_client_config() -> Optional[tuple[str, str]]:
    """Return (client_id, client_secret) when direct exchange is configured."""
    client_id = read_secret(CLIENT_ID_NAME)
    client_secret = read_secret(CLIENT_SECRET_NAME)
    if client_id and client_secret:
        return client_id, client_secret
    return None


def client_config() -> tuple[str, str]:
    """Return (client_id, client_secret) or raise SupabaseOAuthUnconfigured."""
    config = dev_client_config()
    if config is None:
        raise SupabaseOAuthUnconfigured(
            "Supabase OAuth is not configured on this machine. Set "
            f"{CLIENT_ID_NAME} and {CLIENT_SECRET_NAME}."
        )
    return config


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def redirect_uri(base_url: str) -> str:
    """Build the callback URL for the port this server is actually serving."""
    return f"{base_url.rstrip('/')}{CALLBACK_PATH}"


def _register_login(login_id: str, entry: dict[str, Any]) -> None:
    _OAUTH.register_login(login_id, entry)


def _start_login_direct(base_url: str, client_id: str) -> dict[str, Any]:
    """Development path: this process holds the PKCE material and the secret."""
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    callback = redirect_uri(base_url)
    login_id = uuid.uuid4().hex

    _register_login(
        login_id,
        {
            "mode": "direct",
            "state": state,
            "code_verifier": verifier,
            "redirect_uri": callback,
        },
    )

    # Supabase permissions come from the OAuth app registration; requesting
    # scopes here yields tokens the Management API rejects.
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": callback,
            "state": state,
            "code_challenge": _code_challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    return {"login_id": login_id, "authorize_url": f"{AUTHORIZE_URL}?{query}"}


def start_login(base_url: str, return_url: Optional[str] = None) -> dict[str, Any]:
    """Start a sign-in and return the URL the browser must open."""
    config = dev_client_config()
    if config is not None:
        return _start_login_direct(base_url, config[0])
    return _OAUTH.start_login(return_url)


def _mark_login(login_id: str, status: str, detail: str) -> None:
    _OAUTH.mark_login(login_id, status, detail)


def collect_outstanding_logins() -> bool:
    """Attempt pickup for every broker login still waiting on approval."""
    return _OAUTH.collect_outstanding_logins()


def get_login_status(login_id: str) -> dict[str, Any]:
    """Poll a Supabase login started on this server."""
    return _OAUTH.login_status(login_id)


def _token_request(payload: dict[str, str]) -> dict[str, Any]:
    client_id, client_secret = client_config()
    try:
        response = requests.post(
            TOKEN_URL,
            data=payload,
            auth=(client_id, client_secret),
            headers={"Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise SupabaseAuthError(f"Supabase token request failed: {exc}") from exc

    if response.status_code != 200:
        detail = (response.text or "").strip()[:300]
        raise SupabaseAuthError(
            f"Supabase token request returned {response.status_code}: {detail}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise SupabaseAuthError("Supabase token response was not JSON") from exc
    if not body.get("access_token"):
        raise SupabaseAuthError("Supabase token response carried no access token")
    return body


def _store_tokens(body: dict[str, Any], fallback_refresh: Optional[str] = None) -> dict[str, Any]:
    return _OAUTH.store_tokens(body, fallback_refresh=fallback_refresh)


def _clear_tokens() -> None:
    _OAUTH.clear_tokens()


def handle_callback(code: str, state: str) -> dict[str, Any]:
    """Exchange an authorization code and record the login outcome."""
    login_id, entry = _OAUTH.find_login_by_state(state)
    if entry is None or login_id is None:
        return {
            "state": "failed",
            "detail": "This sign-in link has expired. Start the sign-in again.",
        }

    try:
        body = _token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": entry["redirect_uri"],
                "code_verifier": entry["code_verifier"],
            }
        )
    except (SupabaseAuthError, SupabaseOAuthUnconfigured) as exc:
        _mark_login(login_id, "failed", str(exc))
        return {"state": "failed", "detail": str(exc)}

    tokens = _store_tokens(body)
    from .identity import capture_provider_identity_async

    capture_provider_identity_async(BROKER_PROVIDER, tokens["access_token"])
    detail = "Connected to Supabase"
    _mark_login(login_id, "success", detail)
    return {"state": "success", "detail": detail}


def _refresh(tokens: dict[str, Any]) -> Optional[str]:
    """Renew the access token, or clear the session when that is impossible."""
    refresh_token = tokens.get("refresh_token")
    if refresh_token:
        try:
            if dev_client_config() is not None:
                body = _token_request(
                    {"grant_type": "refresh_token", "refresh_token": refresh_token}
                )
            else:
                body = _OAUTH.tokens_from_broker(
                    _OAUTH.broker_post("refresh", {"refresh_token": refresh_token})
                )
            return _store_tokens(body, fallback_refresh=refresh_token)["access_token"]
        except (SupabaseAuthError, SupabaseOAuthUnconfigured, SupabaseBrokerError):
            pass
    _clear_tokens()
    return None


def get_access_token() -> tuple[Optional[str], Optional[str]]:
    """Return (token, method) where method is 'oauth' or None."""
    token = _OAUTH.access_token(refresh=_refresh)
    if token:
        return token, "oauth"
    return None, None


def logout() -> dict[str, Any]:
    """Drop the in-process OAuth session."""
    tokens = _OAUTH.load_tokens() or {}
    refresh_token = tokens.get("refresh_token")
    # The broker has no revoke endpoint; only the direct path can revoke.
    if refresh_token and dev_client_config() is not None:
        try:
            client_id, client_secret = client_config()
            requests.post(
                REVOKE_URL,
                json={"client_id": client_id, "refresh_token": refresh_token},
                auth=(client_id, client_secret),
                timeout=REQUEST_TIMEOUT,
            )
        except (requests.RequestException, SupabaseOAuthUnconfigured):
            pass

    _clear_tokens()
    status_cache(BROKER_PROVIDER).clear()
    return {"signed_out": True}

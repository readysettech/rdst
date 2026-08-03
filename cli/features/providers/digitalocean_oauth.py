"""DigitalOcean OAuth through the keyservice broker.

DigitalOcean's client secret lives only in the keyservice Worker, so this
process never holds one: it keeps a one-time pickup key, hands the browser the
broker's authorize URL, and claims the tokens once the callback completes.
The broker plumbing itself lives in provider_oauth.py.

Tokens are never written to disk or keyring, and there is no API-token
fallback: restarting rdst signs the user out.
"""

from __future__ import annotations

from typing import Any, Optional

from .provider_common import status_cache
from .provider_oauth import ProviderBrokerError, ProviderOAuth

API_BASE = "https://api.digitalocean.com"

BROKER_PROVIDER = "digitalocean"

SIGNED_OUT_DETAIL = (
    "Not signed in to DigitalOcean. Signing in opens your browser; sessions "
    "last until rdst web restarts."
)


class DigitalOceanAuthError(RuntimeError):
    """Raised when a broker reply carries no usable DigitalOcean token."""


class DigitalOceanBrokerError(ProviderBrokerError):
    """Raised when the keyservice OAuth broker cannot be used."""


_OAUTH = ProviderOAuth(
    provider=BROKER_PROVIDER,
    display_name="DigitalOcean",
    broker_error=DigitalOceanBrokerError,
    auth_error=DigitalOceanAuthError,
)

# Kept as a dictionary so tests and diagnostics can inspect active logins.
LOGIN_REGISTRY: dict[str, dict[str, Any]] = _OAUTH.registry


def _clear_tokens() -> None:
    _OAUTH.clear_tokens()


def start_login(return_url: Optional[str] = None) -> dict[str, Any]:
    """Start a sign-in and return the URL the browser must open."""
    return _OAUTH.start_login(return_url)


def get_login_status(login_id: str) -> dict[str, Any]:
    """Poll a DigitalOcean login started on this server."""
    return _OAUTH.login_status(login_id)


def collect_outstanding_logins() -> bool:
    """Attempt pickup for every broker login still waiting on approval."""
    return _OAUTH.collect_outstanding_logins()


def get_access_token() -> tuple[Optional[str], Optional[str]]:
    """Return (token, method) where method is 'oauth' or None."""
    token = _OAUTH.access_token()
    if token:
        return token, "oauth"
    return None, None


def logout() -> dict[str, Any]:
    """Drop the in-process OAuth session.

    The broker has no revoke endpoint, so the session simply stops existing
    here; the grant itself is managed from the DigitalOcean account page.
    """
    _clear_tokens()
    status_cache(BROKER_PROVIDER).clear()
    return {"signed_out": True}

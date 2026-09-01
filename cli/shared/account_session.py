"""Persistent Readyset account sessions used for hosted inference."""

from __future__ import annotations

import hmac
import os
import threading
import time
from typing import Any

import requests

from shared.config.targets import TargetsConfig
from shared.keyservice import keyservice_url
from shared.secret_store_service import SecretStoreService

ACCESS_TOKEN_NAME = "RDST_ACCOUNT_ACCESS_TOKEN"
REFRESH_TOKEN_NAME = "RDST_ACCOUNT_REFRESH_TOKEN"
EXPIRES_AT_NAME = "RDST_ACCOUNT_EXPIRES_AT"
SESSION_SECRET_NAMES = [ACCESS_TOKEN_NAME, REFRESH_TOKEN_NAME, EXPIRES_AT_NAME]
REFRESH_LEEWAY_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 15
_REFRESH_LOCK = threading.Lock()
_SESSION_METADATA_LOCK = threading.RLock()


class AccountSessionError(RuntimeError):
    """The local Readyset account session could not be used."""


def _read_secret(name: str, store: SecretStoreService) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    try:
        return store.get_secret(name)
    except Exception:
        return None


def load_session(
    store: SecretStoreService | None = None,
) -> dict[str, Any] | None:
    store = store or SecretStoreService()
    access_token = _read_secret(ACCESS_TOKEN_NAME, store)
    if not access_token:
        return None
    refresh_token = _read_secret(REFRESH_TOKEN_NAME, store)
    expires_at_raw = _read_secret(EXPIRES_AT_NAME, store)
    if not refresh_token or not expires_at_raw:
        return None
    try:
        expires_at = float(expires_at_raw or 0)
    except (TypeError, ValueError):
        expires_at = 0.0
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
    }


def save_session(
    tokens: dict[str, Any],
    account: dict[str, Any] | None = None,
    store: SecretStoreService | None = None,
) -> dict[str, Any]:
    access_token = str(tokens.get("access_token") or "").strip()
    if not access_token:
        raise AccountSessionError("Readyset sign-in returned no access token")
    refresh_token = str(tokens.get("refresh_token") or "").strip()
    if not refresh_token:
        raise AccountSessionError("Readyset sign-in returned no refresh token")
    try:
        expires_at = time.time() + float(tokens.get("expires_in") or 3600)
    except (TypeError, ValueError):
        expires_at = time.time() + 3600

    store = store or SecretStoreService()
    values = {
        ACCESS_TOKEN_NAME: access_token,
        REFRESH_TOKEN_NAME: refresh_token,
        EXPIRES_AT_NAME: str(expires_at),
    }
    try:
        persistence = {
            name: store.set_secret(name, value, persist=True)
            for name, value in values.items()
        }

        if account:
            save_account_metadata(account)
    except Exception as exc:
        clear_session(store)
        raise AccountSessionError(
            f"Could not save the Readyset account session: {exc}"
        ) from exc
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "persistence": persistence,
    }


def _save_account_metadata_unlocked(account: dict[str, Any]) -> None:
    config = TargetsConfig()
    config.load()
    existing = config.get_account_config()
    config.set_account_config({
        "user_id": str(account.get("user_id") or existing.get("user_id") or ""),
        "email": str(account.get("email") or existing.get("email") or ""),
        "analytics_account_id": str(
            account.get("analytics_account_id")
            or existing.get("analytics_account_id")
            or ""
        ),
        "status": "active",
    })
    config.save()


def save_account_metadata(account: dict[str, Any]) -> None:
    """Persist non-secret account metadata returned by Keyservice."""
    with _SESSION_METADATA_LOCK:
        _save_account_metadata_unlocked(account)


def save_account_metadata_for_access_token(
    account: dict[str, Any],
    access_token: str,
    store: SecretStoreService | None = None,
) -> bool:
    """Persist metadata only while the response's account session is current."""
    store = store or SecretStoreService()
    with _SESSION_METADATA_LOCK:
        session = load_session(store)
        current_token = str((session or {}).get("access_token") or "")
        if not current_token or not hmac.compare_digest(current_token, access_token):
            return False
        _save_account_metadata_unlocked(account)
        return True


def refresh_session(
    session: dict[str, Any] | None = None,
    store: SecretStoreService | None = None,
) -> str | None:
    store = store or SecretStoreService()
    with _REFRESH_LOCK:
        # Reload after acquiring the lock. Another caller may already have
        # rotated the refresh token while this caller was waiting.
        current = load_session(store) or session
        refresh_token = str((current or {}).get("refresh_token") or "").strip()
        if not refresh_token:
            return None
        try:
            response = requests.post(
                keyservice_url("/account-auth/refresh"),
                json={"refresh_token": refresh_token},
                headers={"Accept": "application/json"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise AccountSessionError(f"Could not refresh Readyset sign-in: {exc}") from exc
        if response.status_code != 200:
            if response.status_code in {400, 401}:
                latest = load_session(store)
                if str((latest or {}).get("refresh_token") or "") == refresh_token:
                    clear_session(store)
                return None
            raise AccountSessionError(
                f"Readyset sign-in service returned HTTP {response.status_code}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise AccountSessionError("Readyset sign-in service returned invalid JSON") from exc
        saved = save_session(body, store=store)
        return str(saved["access_token"])


def access_token(
    *,
    force_refresh: bool = False,
    store: SecretStoreService | None = None,
) -> str | None:
    store = store or SecretStoreService()
    session = load_session(store)
    if session is None:
        return None
    if not force_refresh and time.time() < float(session["expires_at"]) - REFRESH_LEEWAY_SECONDS:
        return str(session["access_token"])
    return refresh_session(session, store)


def clear_session(store: SecretStoreService | None = None) -> None:
    store = store or SecretStoreService()
    with _SESSION_METADATA_LOCK:
        store.clear_required(SESSION_SECRET_NAMES)
        try:
            config = TargetsConfig()
            config.load()
            config.clear_account_config()
            config.save()
        except Exception:
            pass


def account_metadata() -> dict[str, Any]:
    try:
        config = TargetsConfig()
        config.load()
        return config.get_account_config()
    except Exception:
        return {}


def is_signed_in_locally(store: SecretStoreService | None = None) -> bool:
    """Return whether a persisted or process-local account session exists."""
    return load_session(store) is not None

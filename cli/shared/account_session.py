"""Persistent Readyset account sessions used for hosted inference."""

from __future__ import annotations

import hmac
import json
import logging
import os
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import requests

from shared.config.targets import TargetsConfig
from shared.constants import rdst_data_dir
from shared.keyservice import keyservice_url
from shared.persistence import replace_with_retry
from shared.secret_store_service import SecretStoreService

ACCESS_TOKEN_NAME = "RDST_ACCOUNT_ACCESS_TOKEN"
REFRESH_TOKEN_NAME = "RDST_ACCOUNT_REFRESH_TOKEN"
EXPIRES_AT_NAME = "RDST_ACCOUNT_EXPIRES_AT"
SESSION_SECRET_NAMES = [ACCESS_TOKEN_NAME, REFRESH_TOKEN_NAME, EXPIRES_AT_NAME]
REFRESH_LEEWAY_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 15
_REFRESH_LOCK = threading.Lock()
_SESSION_METADATA_LOCK = threading.RLock()

logger = logging.getLogger(__name__)


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


def _session_file() -> Path:
    return rdst_data_dir() / "account-session.json"


def _load_file_session() -> dict[str, str]:
    """Read the fallback only when its owner and permissions protect the tokens."""
    try:
        fd = os.open(_session_file(), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or (os.name == "posix" and (
                    info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600)):
                logger.warning("Readyset session file has unsafe ownership or permissions")
                return {}
            values = json.loads(source.read(65536))
        if not isinstance(values, dict) or any(
                not isinstance(values.get(name), str) for name in SESSION_SECRET_NAMES):
            return {}
        return values
    except (OSError, ValueError):
        return {}


def _save_file_session(values: dict[str, str]) -> None:
    """Atomically store a complete session in a private file when the keyring cannot."""
    path = _session_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".account-session-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as destination:
            if os.name == "posix":
                os.fchmod(destination.fileno(), 0o600)
            json.dump(values, destination)
            destination.flush()
            os.fsync(destination.fileno())
        # Windows refuses the swap while a reader has the file open; it retries briefly.
        replace_with_retry(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _clear_file_session() -> None:
    try:
        _session_file().unlink()
    except FileNotFoundError:
        pass


def load_session(
    store: SecretStoreService | None = None,
) -> dict[str, Any] | None:
    """Return the stored session, or None when there is nothing to sign in with.

    The refresh token is what keeps a session alive. Builds that stored the access token
    as one keyring entry lost it on Windows whenever it was longer than Credential Manager
    allows, while the refresh token and expiry were kept. Such a session comes back with an
    empty access token and an expiry in the past, so the next use refreshes it and stores
    the new access token in a form that fits.
    """
    store = store or SecretStoreService()
    fallback = _load_file_session()

    def read(name: str) -> str | None:
        # A complete fallback also covers a keyring that stored only part of a rotation.
        return os.getenv(name) or fallback.get(name) or _read_secret(name, store)

    refresh_token = read(REFRESH_TOKEN_NAME)
    if not refresh_token:
        return None
    access_token = read(ACCESS_TOKEN_NAME) or ""
    expires_at_raw = read(EXPIRES_AT_NAME)
    if not access_token:
        expires_at_raw = "0"
    if not expires_at_raw:
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
        unsaved = [
            name for name, result in persistence.items()
            if isinstance(result, dict) and not result.get("persisted", True)
        ]
        if unsaved:
            _save_file_session(values)
            persistence = {name: {"persisted": True, "session_only": False,
                                  "message": "Readyset session saved in a private file."}
                           for name in values}
        else:
            _clear_file_session()

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
    if (
        not force_refresh
        and session["access_token"]
        and time.time() < float(session["expires_at"]) - REFRESH_LEEWAY_SECONDS
    ):
        return str(session["access_token"])
    return refresh_session(session, store)


def clear_session(store: SecretStoreService | None = None) -> None:
    store = store or SecretStoreService()
    with _SESSION_METADATA_LOCK:
        _clear_file_session()
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

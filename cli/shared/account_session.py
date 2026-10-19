"""Persistent Readyset account sessions used for hosted inference."""

from __future__ import annotations

import base64
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

# Hosted inference without sign-in runs on a Readyset anonymous account. These Key Service
# reasons mean only signing in brings it back.
ANONYMOUS_SIGNIN_REQUIRED = "anonymous_signin_required"
MACHINE_LINKED_SIGNIN_REQUIRED = "machine_linked_signin_required"
# The Key Service won't start an anonymous session right now (too many new installs from
# this network, or anonymous use is switched off); signing in still works.
ANONYMOUS_MINT_RATE_LIMITED = "anonymous_mint_rate_limited"
ANONYMOUS_SIGNIN_DISABLED = "anonymous_signin_disabled"
SIGNIN_REQUIRED_REASONS = (ANONYMOUS_SIGNIN_REQUIRED, MACHINE_LINKED_SIGNIN_REQUIRED,
                           ANONYMOUS_MINT_RATE_LIMITED, ANONYMOUS_SIGNIN_DISABLED)
# How long a sign-in-required answer is trusted before the Key Service is asked again.
SIGNIN_REQUIRED_RECHECK_S = {ANONYMOUS_SIGNIN_REQUIRED: 3600,
                             MACHINE_LINKED_SIGNIN_REQUIRED: 600,
                             ANONYMOUS_MINT_RATE_LIMITED: 300,
                             ANONYMOUS_SIGNIN_DISABLED: 600}
# After a failed anonymous sign-up, wait this long before trying again.
ANONYMOUS_RETRY_S = 60
ANONYMOUS_RATE_LIMIT_RETRY_S = 300
ANONYMOUS_ACCOUNT_FIELDS = ("machine_first_seen_at", "trial_days")
_MINT_LOCK = threading.Lock()
_mint_retry_at = 0.0

logger = logging.getLogger(__name__)


class AccountSessionError(RuntimeError):
    """The local Readyset account session could not be used."""


class SignInRequiredError(AccountSessionError):
    """Hosted inference needs a signed-in Readyset account; ``reason`` says why."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(message or signin_required_message(reason))
        self.reason = reason


def signin_required_message(reason: str | None) -> str:
    if reason == MACHINE_LINKED_SIGNIN_REQUIRED:
        return "Sign in to Readyset to keep using AI on this computer"
    if reason in (ANONYMOUS_MINT_RATE_LIMITED, ANONYMOUS_SIGNIN_DISABLED):
        return "Sign in to Readyset to use hosted AI"
    return "Sign in to keep AI monitoring running"


def signin_required_reason(payload: Any) -> str | None:
    """The sign-in-required reason a Key Service answer carries, wherever it puts it."""
    try:
        text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return None
    return next((reason for reason in SIGNIN_REQUIRED_REASONS if reason in text), None)


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
        if tokens.get("expires_in") is None and tokens.get("expires_at") is not None:
            expires_at = float(tokens["expires_at"])
        else:
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
            if not account.get("is_anonymous"):
                clear_signin_required()
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
    # A sign-in states whether the account is anonymous; other updates keep what's stored.
    anonymous = bool(account["is_anonymous"] if "is_anonymous" in account
                     else existing.get("is_anonymous"))
    values: dict[str, Any] = {
        "user_id": str(account.get("user_id") or existing.get("user_id") or ""),
        "email": str(account.get("email") or existing.get("email") or ""),
        "analytics_account_id": str(
            account.get("analytics_account_id")
            or existing.get("analytics_account_id")
            or ""
        ),
        "status": "active",
        "is_anonymous": anonymous,
    }
    if anonymous:
        for key in ANONYMOUS_ACCOUNT_FIELDS:
            value = account.get(key, existing.get(key))
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values[key] = int(value)
    config.set_account_config(values)
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


def _token_claims(token: str) -> dict[str, Any]:
    """The unverified claims of a Supabase access token; empty when it isn't a JWT."""
    try:
        payload = token.split(".")[1]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(decoded)
    except (IndexError, ValueError, TypeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def is_anonymous_session(
    session: dict[str, Any] | None = None,
    store: SecretStoreService | None = None,
) -> bool:
    """Whether the stored session belongs to an anonymous (not yet signed-in) account."""
    session = session if session is not None else load_session(store)
    if session is None:
        return False
    claims = _token_claims(str(session.get("access_token") or ""))
    if isinstance(claims.get("is_anonymous"), bool):
        return claims["is_anonymous"]
    return bool(account_metadata().get("is_anonymous"))


def _access_state_file() -> Path:
    return rdst_data_dir() / "account-access.json"


def signin_required() -> str | None:
    """The sign-in-required reason the Key Service last gave, while it's still current."""
    try:
        state = json.loads(_access_state_file().read_text(encoding="utf-8"))
        reason = str(state.get("reason") or "")
        if reason in SIGNIN_REQUIRED_REASONS and time.time() < float(state.get("until") or 0):
            return reason
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def record_signin_required(reason: str) -> None:
    if reason not in SIGNIN_REQUIRED_REASONS:
        return
    try:
        from shared.persistence import write_text

        write_text(_access_state_file(), json.dumps({
            "reason": reason,
            "until": time.time() + SIGNIN_REQUIRED_RECHECK_S[reason],
        }))
    except Exception:
        logger.debug("Could not record the sign-in requirement", exc_info=True)


def clear_signin_required() -> None:
    try:
        _access_state_file().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        logger.debug("Could not clear the sign-in requirement", exc_info=True)


def _post_anonymous(payload: dict[str, Any]) -> requests.Response:
    return requests.post(
        keyservice_url("/account-auth/anonymous"),
        json=payload,
        headers={"Accept": "application/json"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


def _anonymous_request() -> dict[str, Any]:
    from shared import product_telemetry
    from shared.machine_id import machine_hash

    return {
        "machine_hash": machine_hash(),
        "client_version": product_telemetry.app_version(),
        "surface": product_telemetry.surface(),
        "internal_user": product_telemetry.internal_user(""),
    }


def _retry_after(response: requests.Response, default: float) -> float:
    try:
        return max(1.0, float(response.headers.get("Retry-After") or default))
    except (TypeError, ValueError, AttributeError):
        return default


def ensure_session(store: SecretStoreService | None = None) -> dict[str, Any] | None:
    """The stored session, or a new anonymous one from the Key Service.

    Returns None when the Key Service declined for now (a sign-up cap or an outage it
    reported); the next call after a short wait tries again. Raises SignInRequiredError when
    this computer has to sign in, and AccountSessionError when the Key Service can't be
    reached.
    """
    global _mint_retry_at
    store = store or SecretStoreService()
    session = load_session(store)
    if session is not None:
        return session
    with _MINT_LOCK:
        session = load_session(store)
        if session is not None:
            return session
        reason = signin_required()
        if reason:
            raise SignInRequiredError(reason)
        if time.monotonic() < _mint_retry_at:
            return None
        try:
            response = _post_anonymous(_anonymous_request())
        except requests.RequestException as exc:
            _mint_retry_at = time.monotonic() + ANONYMOUS_RETRY_S
            raise AccountSessionError(f"Could not reach Readyset: {exc}") from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        reason = signin_required_reason(body) if response.status_code != 200 else None
        if reason:
            record_signin_required(reason)
            raise SignInRequiredError(reason)
        if response.status_code != 200 or not isinstance(body, dict):
            wait = (_retry_after(response, ANONYMOUS_RATE_LIMIT_RETRY_S)
                    if response.status_code == 429 else ANONYMOUS_RETRY_S)
            _mint_retry_at = time.monotonic() + wait
            logger.info("Readyset didn't start an anonymous session (HTTP %s)",
                        response.status_code)
            return None
        account: dict[str, Any] = {"user_id": body.get("user_id"), "is_anonymous": True}
        account.update({key: body.get(key) for key in ANONYMOUS_ACCOUNT_FIELDS})
        saved = save_session(body, account, store=store)
        clear_signin_required()
        _mint_retry_at = 0.0
    try:
        from shared import product_telemetry

        product_telemetry.account_anonymous_created(
            trial_days=account.get("trial_days"),
            machine_first_seen_at=account.get("machine_first_seen_at"))
    except Exception:
        logger.debug("anonymous account event not sent", exc_info=True)
    return saved


def ensure_access_token() -> str | None:
    """A current access token, starting an anonymous session when there's no session."""
    token = access_token()
    if token:
        return token
    if load_session() is not None:
        # A session whose token couldn't be used: never replace it with an anonymous one.
        return None
    session = ensure_session()
    return str(session.get("access_token") or "") or None if session else None

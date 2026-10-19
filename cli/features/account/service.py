"""Keyservice-brokered Readyset account sign-in and quota status."""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import threading
import time
from typing import Any

import requests

from shared import account_session
from shared.keyservice import keyservice_url
from shared.llm_manager.key_resolution import HOSTED_MODEL

REQUEST_TIMEOUT_SECONDS = 15
LOGIN_TTL_SECONDS = 600
ACCESS_CACHE_SECONDS = 60
SIGNIN_PROMPTS = ("none", "soft", "budget", "required")
# Prompting for anonymous use, matching the Key Service's own policy.
SOFT_PROMPT_DAYS = 7
BUDGET_PROMPT_PERCENT = 80
DEFAULT_TRIAL_DAYS = 30
DAY_SECONDS = 86400


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _local_signin_prompt(anonymous: bool, percent_used: float | None,
                         days_remaining: int | None) -> str:
    """The prompt the Key Service would choose, for answers that don't carry one."""
    if not anonymous:
        return "none"
    if account_session.signin_required():
        return "required"
    if days_remaining is not None and days_remaining <= 0:
        return "required"
    if percent_used is not None and percent_used >= 100:
        return "required"
    if percent_used is not None and percent_used >= BUDGET_PROMPT_PERCENT:
        return "budget"
    metadata = account_session.account_metadata()
    first_seen = _number(metadata.get("machine_first_seen_at"))
    if first_seen and time.time() - first_seen >= SOFT_PROMPT_DAYS * DAY_SECONDS:
        return "soft"
    return "none"


class AccountServiceError(RuntimeError):
    """Readyset account sign-in could not be completed."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AccountService:
    def __init__(self) -> None:
        self._logins: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._completed_event = threading.Event()
        self._access_lock = threading.Lock()
        self._access_cache: tuple[float, str, dict[str, Any]] | None = None

    @staticmethod
    def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = requests.post(
                keyservice_url(path),
                json=body,
                headers={"Accept": "application/json"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise AccountServiceError(f"Could not reach Readyset sign-in: {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise AccountServiceError("Readyset sign-in returned invalid JSON") from exc
        if response.status_code != 200:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            raise AccountServiceError(
                str(detail or f"Sign-in returned HTTP {response.status_code}"),
                status_code=response.status_code,
            )
        if not isinstance(payload, dict):
            raise AccountServiceError("Readyset sign-in returned an invalid response")
        return payload

    def _prune(self) -> None:
        now = time.monotonic()
        stale = [
            login_id
            for login_id, login in self._logins.items()
            if now - float(login["created_at"]) > LOGIN_TTL_SECONDS
        ]
        for login_id in stale:
            self._logins.pop(login_id, None)

    @staticmethod
    def _anonymous_access_token() -> str | None:
        """The anonymous session's token, so the Key Service links it instead of starting over."""
        try:
            if not account_session.is_anonymous_session():
                return None
            return account_session.access_token()
        except Exception:
            return None

    @staticmethod
    def _anonymous_session() -> dict[str, str] | None:
        """The anonymous session for the browser to upgrade, with time left on its access token.

        The browser must never refresh this session itself: a rotated refresh token would
        leave the stored copy unusable.
        """
        try:
            if not account_session.is_anonymous_session():
                return None
            token = account_session.access_token()
            session = account_session.load_session()
            if token and session and float(session["expires_at"]) - time.time() < 300:
                token = account_session.access_token(force_refresh=True)
                session = account_session.load_session()
        except Exception:
            return None
        if not token or not session or not session.get("refresh_token"):
            return None
        return {"access_token": token, "refresh_token": str(session["refresh_token"])}

    def start_login(self, return_url: str) -> dict[str, Any]:
        pickup_key = secrets.token_urlsafe(32)
        body: dict[str, Any] = {
            "pickup_key_hash": hashlib.sha256(
                pickup_key.encode("ascii")
            ).hexdigest(),
            "return_url": return_url,
        }
        anonymous_token = self._anonymous_access_token()
        if anonymous_token:
            body["anonymous_access_token"] = anonymous_token
        result = self._post("/account-auth/start", body)
        public = {
            "login_id": str(result.get("login_id") or ""),
            "state": str(result.get("state") or ""),
            "auth_url": str(result.get("auth_url") or ""),
            "publishable_key": str(result.get("publishable_key") or ""),
            "callback_url": str(result.get("callback_url") or ""),
            "expires_in": int(result.get("expires_in") or LOGIN_TTL_SECONDS),
        }
        if not all(public[key] for key in (
            "login_id", "state", "auth_url", "publishable_key", "callback_url"
        )):
            raise AccountServiceError("Readyset sign-in returned incomplete OAuth context")
        anonymous = self._anonymous_session()
        with self._lock:
            self._prune()
            self._logins[public["login_id"]] = {
                **public,
                "pickup_key": pickup_key,
                "created_at": time.monotonic(),
                "status_state": "running",
                "detail": "Waiting for browser sign-in",
                "browser_callback": {"state": "pending"},
            }
        return {**public, "anonymous_session": anonymous} if anonymous else public

    def login_context(self, login_id: str) -> dict[str, Any]:
        with self._lock:
            self._prune()
            login = self._logins.get(login_id)
            if login is None:
                raise KeyError(login_id)
            return {
                key: login[key]
                for key in (
                    "login_id", "state", "auth_url", "publishable_key",
                    "callback_url", "expires_in"
                )
            }

    def record_browser_callback(
        self,
        login_id: str,
        *,
        code: str = "",
        error: str = "",
        error_description: str = "",
    ) -> dict[str, Any]:
        """Capture a browser OAuth result for the initiating desktop process."""
        if not code and not error:
            raise AccountServiceError(
                "Readyset sign-in returned neither an authorization code nor an error"
            )
        with self._lock:
            self._prune()
            login = self._logins.get(login_id)
            if login is None:
                raise KeyError(login_id)
            callback = {
                "state": "ready" if code else "failed",
                "code": code or None,
                "error": error or None,
                "error_description": error_description or None,
            }
            login["browser_callback"] = callback
            return callback

    def browser_callback_status(self, login_id: str) -> dict[str, Any]:
        with self._lock:
            self._prune()
            login = self._logins.get(login_id)
            if login is None:
                raise KeyError(login_id)
            return dict(login.get("browser_callback") or {"state": "pending"})

    def complete_login(
        self,
        login_id: str,
        state: str,
        access_token: str,
        refresh_token: str,
        expires_in: int,
    ) -> dict[str, str]:
        with self._lock:
            self._prune()
            login = self._logins.get(login_id)
            if login is None:
                raise KeyError(login_id)
            if not hmac.compare_digest(str(login["state"]), state):
                raise AccountServiceError("Readyset sign-in state does not match")
        try:
            from shared.telemetry import telemetry

            analytics_disabled = not telemetry.is_enabled()
        except Exception:
            analytics_disabled = True
        body: dict[str, Any] = {
            "login_id": login_id,
            "state": state,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": expires_in,
            "analytics_disabled": analytics_disabled,
        }
        try:
            from shared import product_telemetry

            # The account's own address isn't known here yet; the Key Service checks it.
            body["internal_user"] = product_telemetry.internal_user("")
        except Exception:
            pass
        anonymous_token = self._anonymous_access_token()
        if anonymous_token and anonymous_token != access_token:
            body["anonymous_access_token"] = anonymous_token
        try:
            from shared.machine_id import machine_hash

            body["machine_hash"] = machine_hash()
        except Exception:
            pass
        self._post("/account-auth/complete", body)
        return self.login_status(login_id)

    def login_status(self, login_id: str) -> dict[str, Any]:
        # Serialize result pickup so the browser callback and CLI poller cannot
        # race to consume the same one-time Keyservice response.
        with self._lock:
            self._prune()
            login = self._logins.get(login_id)
            if login is None:
                raise KeyError(login_id)
            if login["status_state"] != "running":
                return {
                    "state": login["status_state"],
                    "detail": login["detail"],
                }
            try:
                result = self._post(
                    "/account-auth/result",
                    {"login_id": login_id, "pickup_key": login["pickup_key"]},
                )
            except AccountServiceError as exc:
                # Result pickup is deliberately replayable until acknowledged.
                # A transport or Keyservice failure must not turn a recoverable
                # login into a permanent local failure.
                return {"state": "running", "detail": str(exc)}
            else:
                broker_status = str(result.get("status") or "")
                if broker_status == "pending":
                    return {
                        "state": "running",
                        "detail": "Waiting for browser sign-in",
                    }
                if broker_status == "ready":
                    linked = self._anonymous_link_context()
                    account = dict(result.get("account") or {})
                    account["is_anonymous"] = bool(account.get("is_anonymous"))
                    try:
                        account_session.save_session(result.get("tokens") or {}, account)
                    except account_session.AccountSessionError as exc:
                        return {"state": "running", "detail": str(exc)}
                    else:
                        self._invalidate_access()
                        self._report_linked(linked)
                        try:
                            self._post(
                                "/account-auth/ack",
                                {"login_id": login_id, "pickup_key": login["pickup_key"]},
                            )
                        except AccountServiceError:
                            # Local storage already succeeded. The parked copy
                            # expires shortly and remains protected by the
                            # high-entropy pickup key in the meantime.
                            pass
                        state, detail = "success", "Signed in to Readyset"
                        self._completed_event.set()
                elif broker_status == "expired":
                    state, detail = "failed", "This sign-in expired. Start again."
                else:
                    state = "failed"
                    detail = str(result.get("detail") or "Readyset sign-in failed")
            login.update(status_state=state, detail=detail)
            return {"state": state, "detail": detail}

    @staticmethod
    def _anonymous_link_context() -> dict[str, Any]:
        """Whether the session being replaced was anonymous, and since when this machine ran."""
        try:
            anonymous = account_session.is_anonymous_session()
            first_seen = _number(account_session.account_metadata().get("machine_first_seen_at"))
        except Exception:
            return {"from_anonymous": False, "days_since_first_seen": None}
        days = int((time.time() - first_seen) // DAY_SECONDS) if anonymous and first_seen else None
        return {"from_anonymous": anonymous, "days_since_first_seen": days}

    @staticmethod
    def _report_linked(linked: dict[str, Any]) -> None:
        try:
            from shared import product_telemetry

            product_telemetry.account_linked(**linked)
        except Exception:
            pass

    def prepare_browser_login(self) -> None:
        self._completed_event.clear()

    def wait_for_browser_login(self, timeout: float) -> bool:
        return self._completed_event.wait(timeout)

    def status(self, include_quota: bool = True, include_access: bool = False) -> dict[str, Any]:
        """The account and, with ``include_access``, the hosted-AI access and sign-in prompt.

        With ``include_access`` and no session, an anonymous session is started unless the
        user's own Anthropic key pays for AI.
        """
        signed_out = {"signed_in": False, "detail": "Not signed in to Readyset"}
        if include_access:
            signed_out.update(self._no_session_access())
        try:
            token = account_session.access_token()
            if not token and include_access and not self._own_key():
                token = account_session.ensure_access_token()
        except account_session.SignInRequiredError as exc:
            self._note_prompt("required")
            return {**signed_out, "detail": str(exc), "signin_prompt": "required",
                    "signin_reason": exc.reason}
        except account_session.AccountSessionError as exc:
            return {**signed_out, "detail": str(exc)}
        if not token:
            return signed_out
        if account_session.is_anonymous_session():
            result = {
                "signed_in": False,
                "has_session": True,
                "is_anonymous": True,
                "user_id": account_session.account_metadata().get("user_id"),
                "detail": "Using Readyset-hosted AI without signing in",
            }
            if include_access:
                result.update(self._access(token, anonymous=True))
            return result
        result = self._signed_in_status(token, include_quota)
        if include_access:
            result.update(self._access(token, anonymous=False))
        return result

    @staticmethod
    def _own_key() -> bool:
        from shared.llm_manager.key_resolution import own_key_configured

        return own_key_configured()

    @staticmethod
    def _no_session_access() -> dict[str, Any]:
        reason = account_session.signin_required()
        return {"has_session": False, "is_anonymous": False,
                "signin_prompt": "required" if reason else "none", "signin_reason": reason,
                "percent_used": None, "days_remaining": None, "trial_days": None}

    def _invalidate_access(self) -> None:
        with self._access_lock:
            self._access_cache = None

    def _inference_status(self, token: str) -> tuple[int, dict[str, Any]] | None:
        def fetch(bearer: str) -> requests.Response:
            return requests.get(
                keyservice_url("/v1/inference/status"),
                headers={"Authorization": f"Bearer {bearer}", "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

        try:
            response = fetch(token)
            if response.status_code == 401:
                refreshed = account_session.access_token(force_refresh=True)
                if refreshed:
                    response = fetch(refreshed)
            payload = response.json()
        except (requests.RequestException, ValueError, account_session.AccountSessionError):
            return None
        return response.status_code, payload if isinstance(payload, dict) else {}

    def _access(self, token: str, *, anonymous: bool) -> dict[str, Any]:
        """Hosted-AI access from /v1/inference/status, cached briefly per session."""
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = time.monotonic()
        with self._access_lock:
            cached = self._access_cache
            if cached and cached[1] == key and now - cached[0] < ACCESS_CACHE_SECONDS:
                return dict(cached[2])
        answer = self._inference_status(token)
        access = self._access_fields(answer, anonymous=anonymous)
        if answer is not None:
            with self._access_lock:
                self._access_cache = (now, key, access)
        self._note_prompt(access["signin_prompt"])
        return dict(access)

    @staticmethod
    def _access_fields(answer: tuple[int, dict[str, Any]] | None, *,
                       anonymous: bool) -> dict[str, Any]:
        status_code, payload = answer if answer is not None else (0, {})
        reason = account_session.signin_required_reason(payload) if status_code != 200 else None
        if status_code == 200 and isinstance(payload.get("is_anonymous"), bool):
            anonymous = payload["is_anonymous"]
        quota = payload.get("quota") if isinstance(payload.get("quota"), dict) else {}
        limit = _number(payload.get("limit_microusd", quota.get("limit_microusd")))
        used = _number(payload.get("used_microusd", quota.get("used_microusd")))
        if used is None and limit is not None:
            remaining = _number(quota.get("remaining_microusd"))
            used = limit - remaining if remaining is not None else None
        percent = _number(payload.get("percent_used"))
        if percent is None and limit and used is not None:
            percent = round(100.0 * used / limit, 1)
        trial_days = _number(payload.get("trial_days"))
        days_remaining = _number(payload.get("days_remaining"))
        metadata = account_session.account_metadata() if anonymous else {}
        if anonymous and trial_days is None:
            trial_days = _number(metadata.get("trial_days")) or DEFAULT_TRIAL_DAYS
        first_seen = _number(payload.get("machine_first_seen_at")) or _number(
            metadata.get("machine_first_seen_at"))
        if anonymous and days_remaining is None and first_seen and trial_days:
            elapsed = (time.time() - first_seen) / DAY_SECONDS
            days_remaining = float(max(0, math.ceil(trial_days - elapsed)))
        prompt = payload.get("signin_prompt") if status_code == 200 else None
        if reason:
            prompt = "required"
        if prompt not in SIGNIN_PROMPTS:
            prompt = _local_signin_prompt(anonymous, percent, None if days_remaining is None
                                          else int(days_remaining))
        if anonymous and status_code:
            if prompt == "required":
                account_session.record_signin_required(
                    reason or account_session.ANONYMOUS_SIGNIN_REQUIRED)
            elif status_code == 200:
                account_session.clear_signin_required()
        return {
            "has_session": True,
            "is_anonymous": anonymous,
            "signin_prompt": prompt,
            "signin_reason": (reason or account_session.ANONYMOUS_SIGNIN_REQUIRED)
            if prompt == "required" and anonymous else None,
            "percent_used": percent,
            "days_remaining": None if days_remaining is None else int(days_remaining),
            "trial_days": None if trial_days is None else int(trial_days),
            "limit_microusd": None if limit is None else int(limit),
            "used_microusd": None if used is None else int(used),
        }

    @staticmethod
    def _note_prompt(prompt: str) -> None:
        try:
            from shared import product_telemetry

            product_telemetry.signin_prompt_state(prompt)
        except Exception:
            pass

    def _signed_in_status(self, token: str, include_quota: bool) -> dict[str, Any]:
        metadata = account_session.account_metadata()
        if not metadata.get("analytics_account_id"):
            try:
                identity_response = requests.get(
                    keyservice_url("/account-auth/user"),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                if identity_response.status_code == 200:
                    identity = identity_response.json()
                    if isinstance(identity, dict):
                        account_session.save_account_metadata(identity)
                        metadata = account_session.account_metadata()
            except Exception:
                pass
        result: dict[str, Any] = {
            "signed_in": True,
            "user_id": metadata.get("user_id"),
            "email": metadata.get("email"),
            "analytics_account_id": metadata.get("analytics_account_id"),
        }
        if not include_quota:
            return result
        result["model"] = HOSTED_MODEL
        try:
            response = requests.get(
                keyservice_url("/v1/inference/status"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 401:
                token = account_session.access_token(force_refresh=True)
                if token:
                    response = requests.get(
                        keyservice_url("/v1/inference/status"),
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Accept": "application/json",
                        },
                        timeout=REQUEST_TIMEOUT_SECONDS,
                    )
            if response.status_code == 200:
                payload = response.json()
                result["quota"] = payload.get("quota")
                result["model"] = payload.get("model") or result["model"]
            else:
                result["detail"] = "Could not load hosted inference quota"
        except (requests.RequestException, ValueError, account_session.AccountSessionError):
            result["detail"] = "Could not load hosted inference quota"
        return result

    def logout(self) -> None:
        account_session.clear_session()
        self._invalidate_access()


account_service = AccountService()

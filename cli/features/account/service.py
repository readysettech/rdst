"""Keyservice-brokered Readyset account sign-in and quota status."""

from __future__ import annotations

import hashlib
import hmac
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

    def start_login(self, return_url: str) -> dict[str, Any]:
        pickup_key = secrets.token_urlsafe(32)
        result = self._post(
            "/account-auth/start",
            {
                "pickup_key_hash": hashlib.sha256(
                    pickup_key.encode("ascii")
                ).hexdigest(),
                "return_url": return_url,
            },
        )
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
        return public

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
        self._post(
            "/account-auth/complete",
            {
                "login_id": login_id,
                "state": state,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_in": expires_in,
            },
        )
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
                    try:
                        account_session.save_session(
                            result.get("tokens") or {}, result.get("account") or {}
                        )
                    except account_session.AccountSessionError as exc:
                        return {"state": "running", "detail": str(exc)}
                    else:
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

    def prepare_browser_login(self) -> None:
        self._completed_event.clear()

    def wait_for_browser_login(self, timeout: float) -> bool:
        return self._completed_event.wait(timeout)

    def status(self, include_quota: bool = True) -> dict[str, Any]:
        try:
            token = account_session.access_token()
        except account_session.AccountSessionError as exc:
            return {"signed_in": False, "detail": str(exc)}
        if not token:
            return {"signed_in": False, "detail": "Not signed in to Readyset"}
        metadata = account_session.account_metadata()
        result: dict[str, Any] = {
            "signed_in": True,
            "user_id": metadata.get("user_id"),
            "email": metadata.get("email"),
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


account_service = AccountService()

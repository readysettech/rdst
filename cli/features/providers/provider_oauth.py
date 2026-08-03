"""Keyservice-brokered OAuth sign-ins and the in-process token store.

Provider client secrets live only in the keyservice Worker, so a sign-in from
this process is: hand the browser the broker's authorize URL, then claim the
tokens exactly once with a one-time pickup key.

Tokens are never written to disk or keyring: restarting rdst signs the user
out. Provider modules own their own naming, extra credential paths and API
calls; this module owns the parts every broker-backed provider shares.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from typing import Any, Callable, Optional

import requests

from shared.keyservice import keyservice_url

REQUEST_TIMEOUT = 10.0
LOGIN_TTL_SECONDS = 600.0
# Refresh slightly before expiry so a discovery call cannot start with a
# token that expires mid-flight.
REFRESH_LEEWAY_SECONDS = 60.0
# Every status check sweeps for parked logins, and the discovery panel polls
# status every couple of seconds; sweeping that often duplicates broker POSTs.
COLLECT_INTERVAL_SECONDS = 3.0

RefreshHook = Callable[[dict[str, Any]], Optional[str]]


class ProviderBrokerError(RuntimeError):
    """Raised when the keyservice OAuth broker cannot be used."""


class ProviderOAuth:
    """Broker sign-ins and the OAuth token store for one provider."""

    def __init__(
        self,
        provider: str,
        display_name: str,
        broker_error: type[Exception],
        auth_error: type[Exception],
    ) -> None:
        self.provider = provider
        self.display_name = display_name
        self.broker_error = broker_error
        self.auth_error = auth_error
        # Kept as a dictionary so tests and diagnostics can inspect active logins.
        self.registry: dict[str, dict[str, Any]] = {}
        self._registry_lock = threading.Lock()
        self._last_collect = 0.0
        self._tokens: Optional[dict[str, Any]] = None
        self._tokens_lock = threading.Lock()

    @property
    def waiting_detail(self) -> str:
        return f"Waiting for {self.display_name} browser approval"

    @property
    def connected_detail(self) -> str:
        return f"Connected to {self.display_name}"

    def broker_url(self, action: str) -> str:
        return keyservice_url(f"/oauth/{self.provider}/{action}")

    def broker_post(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST to the keyservice OAuth broker and return its JSON body."""
        try:
            response = requests.post(
                self.broker_url(action),
                json=payload,
                headers={"Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise self.broker_error(f"Could not reach the RDST sign-in service: {exc}") from exc

        if response.status_code != 200:
            detail = (response.text or "").strip()[:300]
            raise self.broker_error(
                f"RDST sign-in service returned {response.status_code}: {detail}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise self.broker_error("RDST sign-in service response was not JSON") from exc
        if not isinstance(body, dict):
            raise self.broker_error("RDST sign-in service response was not an object")
        return body

    def _prune_expired(self, now: float) -> None:
        stale = [
            login_id
            for login_id, entry in self.registry.items()
            if now - entry["created_at"] > LOGIN_TTL_SECONDS
        ]
        for login_id in stale:
            self.registry.pop(login_id, None)

    def register_login(self, login_id: str, entry: dict[str, Any]) -> None:
        now = time.monotonic()
        with self._registry_lock:
            # A login registered since the last sweep is swept immediately.
            self._last_collect = 0.0
            self._prune_expired(now)
            self.registry[login_id] = {
                "created_at": now,
                "status": "running",
                "detail": self.waiting_detail,
                **entry,
            }

    def find_login_by_state(self, state: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
        now = time.monotonic()
        with self._registry_lock:
            self._prune_expired(now)
            for login_id, entry in self.registry.items():
                if entry.get("state") == state:
                    return login_id, entry
        return None, None

    def mark_login(self, login_id: str, status: str, detail: str) -> None:
        with self._registry_lock:
            entry = self.registry.get(login_id)
            if entry is not None and entry["status"] != "success":
                # Success is sticky: concurrent pollers race for the one-time
                # pickup, and the loser's "expired" must not erase the win.
                entry["status"] = status
                entry["detail"] = detail

    def start_login(self, return_url: Optional[str] = None) -> dict[str, Any]:
        """Ask the keyservice to own the OAuth exchange for this sign-in."""
        pickup_key = secrets.token_urlsafe(32)
        pickup_key_hash = hashlib.sha256(pickup_key.encode("ascii")).hexdigest()
        payload = {"pickup_key_hash": pickup_key_hash}
        if return_url:
            payload["return_url"] = return_url
        body = self.broker_post("start", payload)

        login_id = str(body.get("login_id") or "")
        authorize_url = str(body.get("authorize_url") or "")
        if not login_id or not authorize_url:
            raise self.broker_error("RDST sign-in service did not return a sign-in link")

        self.register_login(login_id, {"mode": "broker", "pickup_key": pickup_key})
        return {"login_id": login_id, "authorize_url": authorize_url}

    def pickup(self, login_id: str, pickup_key: str) -> dict[str, Any]:
        """Poll the broker once; a ready pickup consumes the key on both sides."""
        try:
            body = self.broker_post("result", {"login_id": login_id, "pickup_key": pickup_key})
        except self.broker_error as exc:
            self.mark_login(login_id, "failed", str(exc))
            return {"state": "failed", "detail": str(exc)}

        status = str(body.get("status") or "").lower()
        if status == "pending":
            return {"state": "running", "detail": self.waiting_detail}

        if status == "ready":
            try:
                tokens = self.store_tokens(self.tokens_from_broker(body))
            except self.auth_error as exc:
                self.mark_login(login_id, "failed", str(exc))
                return {"state": "failed", "detail": str(exc)}
            from .identity import capture_provider_identity_async

            capture_provider_identity_async(self.provider, tokens["access_token"])
            self.mark_login(login_id, "success", self.connected_detail)
            return {"state": "success", "detail": self.connected_detail}

        if status == "expired":
            detail = "This sign-in expired. Start the sign-in again."
        else:
            detail = str(body.get("detail") or f"{self.display_name} sign-in failed")
        self.mark_login(login_id, "failed", detail)
        return {"state": "failed", "detail": detail}

    def collect_outstanding_logins(self) -> bool:
        """Attempt pickup for every broker login still waiting on approval.

        The UI's poll loop dies with the component that started it, but the
        browser approval can complete after that. Status checks call this, so
        whichever page next asks "am I signed in" lands the tokens anyway.
        Returns True when a pickup produced a live session; sweeps are rate
        limited so a polling UI cannot stack duplicate broker requests.
        """
        now = time.monotonic()
        with self._registry_lock:
            if now - self._last_collect < COLLECT_INTERVAL_SECONDS:
                return False
            self._last_collect = now
            self._prune_expired(now)
            candidates = [
                (login_id, str(entry["pickup_key"]))
                for login_id, entry in self.registry.items()
                if entry.get("mode") == "broker" and entry["status"] == "running"
            ]
        landed = False
        for login_id, pickup_key in candidates:
            if self.pickup(login_id, pickup_key).get("state") == "success":
                landed = True
        return landed

    def login_status(self, login_id: str) -> dict[str, Any]:
        """Poll a login started on this server."""
        now = time.monotonic()
        with self._registry_lock:
            self._prune_expired(now)
            entry = self.registry.get(login_id)
            if entry is None:
                raise KeyError(login_id)
            settled = {"state": entry["status"], "detail": entry["detail"]}
            if entry.get("mode") != "broker" or entry["status"] != "running":
                return settled
            pickup_key = str(entry["pickup_key"])

        return self.pickup(login_id, pickup_key)

    def tokens_from_broker(self, body: dict[str, Any]) -> dict[str, Any]:
        """Read the token set out of a broker reply, nested or flat."""
        tokens = body.get("tokens")
        payload = tokens if isinstance(tokens, dict) else body
        if not payload.get("access_token"):
            raise self.auth_error("RDST sign-in service returned no access token")
        return payload

    def store_tokens(
        self, body: dict[str, Any], fallback_refresh: Optional[str] = None
    ) -> dict[str, Any]:
        expires_in = body.get("expires_in")
        try:
            lifetime = float(expires_in)
        except (TypeError, ValueError):
            lifetime = 3600.0
        tokens = {
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token") or fallback_refresh,
            "expires_at": time.time() + lifetime,
        }
        with self._tokens_lock:
            self._tokens = tokens
        return tokens

    def load_tokens(self) -> Optional[dict[str, Any]]:
        with self._tokens_lock:
            return dict(self._tokens) if self._tokens else None

    def clear_tokens(self) -> None:
        with self._tokens_lock:
            self._tokens = None

    def refresh(self, tokens: dict[str, Any]) -> Optional[str]:
        """Renew the access token, or clear the session when that is impossible."""
        refresh_token = tokens.get("refresh_token")
        if refresh_token:
            try:
                body = self.tokens_from_broker(
                    self.broker_post("refresh", {"refresh_token": refresh_token})
                )
                return self.store_tokens(body, fallback_refresh=refresh_token)["access_token"]
            except (self.auth_error, self.broker_error):
                pass
        self.clear_tokens()
        return None

    def access_token(self, refresh: Optional[RefreshHook] = None) -> Optional[str]:
        """Return a live OAuth access token, refreshing when it is near expiry."""
        tokens = self.load_tokens()
        if tokens is None:
            return None
        expires_at = tokens.get("expires_at")
        try:
            deadline = float(expires_at)
        except (TypeError, ValueError):
            deadline = 0.0
        if time.time() < deadline - REFRESH_LEEWAY_SECONDS:
            return tokens["access_token"]
        return (refresh or self.refresh)(tokens)

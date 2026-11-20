"""AWS SSO device-code sign-in through botocore, for machines without the AWS CLI.

Runs the same OAuth device authorization the AWS CLI's `aws sso login` runs and
writes the token to ~/.aws/sso/cache in the CLI's format, so botocore's SSO
credential provider and the account/role listing read it exactly as they read
a CLI login. The person approves the request in their own browser through the
verification URL, which works when RDST runs on a server.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
SSO_SCOPES = ["sso:account:access"]
SLOW_DOWN_SECONDS = 5
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class SsoConfigMissing(ValueError):
    """Raised when a profile has no SSO start URL or region to sign in with."""


def cli_available() -> bool:
    return shutil.which("aws") is not None


def sso_cache_dir() -> Path:
    return Path.home() / ".aws" / "sso" / "cache"


def cache_key(start_url: str, session_name: Optional[str]) -> str:
    """The CLI's cache file name: sha1 of the sso-session name, else of the start URL."""
    value = session_name or start_url
    return hashlib.sha1(value.encode("utf-8"), usedforsecurity=False).hexdigest()


def _timestamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(_TIME_FORMAT)


def write_token_cache(
    *,
    start_url: str,
    region: str,
    session_name: Optional[str],
    token: dict[str, Any],
    registration: dict[str, Any],
    now: Optional[datetime] = None,
) -> Path:
    """Write a device-authorized token the way AWS CLI v2 caches it (mode 0600)."""
    now = now or datetime.now(timezone.utc)
    entry: dict[str, Any] = {
        "startUrl": start_url,
        "region": region,
        "accessToken": token["accessToken"],
        "expiresAt": _timestamp(now + timedelta(seconds=int(token.get("expiresIn", 3600)))),
    }
    if session_name:
        # sso-session tokens carry the client registration so botocore can refresh them.
        entry["clientId"] = registration["clientId"]
        entry["clientSecret"] = registration["clientSecret"]
        expires = registration.get("clientSecretExpiresAt")
        if expires:
            entry["registrationExpiresAt"] = _timestamp(
                datetime.fromtimestamp(int(expires), tz=timezone.utc)
            )
        if token.get("refreshToken"):
            entry["refreshToken"] = token["refreshToken"]

    directory = sso_cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{cache_key(start_url, session_name)}.json"
    fd, temporary = tempfile.mkstemp(dir=str(directory), prefix=".rdst-token.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(entry, handle)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


def profile_sso_config(profile: str) -> tuple[str, str, Optional[str]]:
    """Return (start_url, sso_region, sso_session_name) for a profile."""
    import botocore.session

    config = botocore.session.get_session().full_config
    settings = config.get("profiles", {}).get(profile) or {}
    session_name = settings.get("sso_session")
    if session_name:
        session = config.get("sso_sessions", {}).get(session_name) or {}
        start_url, region = session.get("sso_start_url"), session.get("sso_region")
    else:
        start_url, region = settings.get("sso_start_url"), settings.get("sso_region")
    if not start_url or not region:
        raise SsoConfigMissing(f"AWS profile '{profile}' has no SSO start URL and region")
    return start_url, region, session_name


def _oidc_client(region: str):
    import botocore.session
    from botocore.config import Config

    return botocore.session.get_session().create_client(
        "sso-oidc",
        region_name=region,
        config=Config(connect_timeout=5.0, read_timeout=10.0, retries={"max_attempts": 2}),
    )


class DeviceLogin:
    """One device authorization: started synchronously, polled on a thread."""

    def __init__(
        self,
        *,
        start_url: str,
        region: str,
        session_name: Optional[str],
        client_factory: Optional[Callable[[str], Any]] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.start_url = start_url
        self.region = region
        self.session_name = session_name
        self._client = (client_factory or _oidc_client)(region)
        self._sleep = sleep
        self._clock = clock
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self.state = "running"
        self.detail = "Waiting for AWS SSO browser approval"
        self.verification_url: Optional[str] = None
        self.user_code: Optional[str] = None
        self._registration: dict[str, Any] = {}
        self._authorization: dict[str, Any] = {}

    def start(self) -> None:
        """Register a public client and ask for a device code (network calls)."""
        scopes = SSO_SCOPES if self.session_name else None
        params: dict[str, Any] = {"clientName": "rdst", "clientType": "public"}
        if scopes:
            params["scopes"] = scopes
        self._registration = self._client.register_client(**params)
        self._authorization = self._client.start_device_authorization(
            clientId=self._registration["clientId"],
            clientSecret=self._registration["clientSecret"],
            startUrl=self.start_url,
        )
        self.verification_url = self._authorization.get(
            "verificationUriComplete"
        ) or self._authorization.get("verificationUri")
        self.user_code = self._authorization.get("userCode")

    def run_in_background(self, name: str) -> threading.Thread:
        thread = threading.Thread(target=self.poll, name=name, daemon=True)
        thread.start()
        return thread

    def cancel(self) -> None:
        self._cancelled.set()

    def _finish(self, state: str, detail: str) -> None:
        with self._lock:
            self.state, self.detail = state, detail

    def snapshot(self) -> tuple[str, str]:
        with self._lock:
            return self.state, self.detail

    def poll(self) -> None:
        """Exchange the device code for a token, honoring interval and slow_down."""
        client = self._client
        errors = client.exceptions
        interval = int(self._authorization.get("interval") or 5)
        deadline = self._clock() + int(self._authorization.get("expiresIn") or 600)
        while not self._cancelled.is_set():
            if self._clock() >= deadline:
                self._finish("timeout", "The AWS sign-in code expired before it was approved")
                return
            try:
                token = client.create_token(
                    grantType=DEVICE_GRANT_TYPE,
                    deviceCode=self._authorization["deviceCode"],
                    clientId=self._registration["clientId"],
                    clientSecret=self._registration["clientSecret"],
                )
            except errors.AuthorizationPendingException:
                self._sleep(interval)
                continue
            except errors.SlowDownException:
                interval += SLOW_DOWN_SECONDS
                self._sleep(interval)
                continue
            except errors.ExpiredTokenException:
                self._finish("timeout", "The AWS sign-in code expired before it was approved")
                return
            except errors.AccessDeniedException:
                self._finish("failed", "The AWS sign-in request was denied")
                return
            except Exception as exc:  # noqa: BLE001 - surfaced to the person signing in
                self._finish("failed", str(exc) or exc.__class__.__name__)
                return
            try:
                write_token_cache(
                    start_url=self.start_url,
                    region=self.region,
                    session_name=self.session_name,
                    token=token,
                    registration=self._registration,
                )
            except OSError as exc:
                self._finish("failed", f"Couldn't save the AWS sign-in: {exc}")
                return
            self._finish("success", "AWS SSO session is active")
            return


def logout_without_cli() -> dict[str, Any]:
    """Sign out like `aws sso logout`: revoke and delete every cached SSO token."""
    directory = sso_cache_dir()
    removed = 0
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or not data.get("accessToken"):
                continue
            region = data.get("region")
            if region:
                try:
                    import botocore.session

                    sso = botocore.session.get_session().create_client("sso", region_name=region)
                    sso.logout(accessToken=data["accessToken"])
                except Exception:
                    pass
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return {"signed_out": True, "removed": removed}

"""Manage browser-driven AWS SSO logins started by the local RDST server."""

from __future__ import annotations

import re
import shlex
import subprocess
import threading
import time
import uuid
from typing import Any, Optional


LOGIN_TIMEOUT_SECONDS = 300.0
TERMINAL_RETENTION_SECONDS = 60.0
_VERIFICATION_URL_RE = re.compile(
    r"https://(?:"
    r"device\.sso\.[^\s<>'\"]+|"
    r"[A-Za-z0-9.-]+\.awsapps\.com/start/#/device[^\s<>'\"]*"
    r")"
)


class AwsSdkUnavailable(RuntimeError):
    """Raised when botocore is not installed in the RDST environment."""


class UnknownAwsProfile(ValueError):
    """Raised when a requested profile is not known to botocore."""


class AwsCliMissing(RuntimeError):
    """Raised when the AWS CLI executable cannot be found."""


class AwsCliSpawnError(RuntimeError):
    """Raised when the AWS CLI exists but cannot be started."""


class OutputBuffer:
    """Small thread-safe text buffer populated by the process reader thread."""

    def __init__(self) -> None:
        self._chunks: list[str] = []
        self._lock = threading.Lock()

    def append(self, chunk: str) -> None:
        with self._lock:
            self._chunks.append(chunk)

    def text(self) -> str:
        with self._lock:
            return "".join(self._chunks)

    def tail(self, line_count: int = 8) -> str:
        return "\n".join(self.text().splitlines()[-line_count:]).strip()


# Kept as dictionaries so tests and diagnostics can inspect active logins.
LOGIN_REGISTRY: dict[str, dict[str, Any]] = {}
_REGISTRY_LOCK = threading.Lock()


def fallback_command(profile: str) -> str:
    """Return a shell-safe command for the manual fallback path."""
    return shlex.join(["aws", "sso", "login", "--profile", profile])


def sso_session_fallback_command(session_name: str) -> str:
    """Return the manual fallback command for the sso-session login path."""
    return shlex.join(["aws", "sso", "login", "--sso-session", session_name])


def logout() -> dict[str, Any]:
    """Sign out of AWS SSO by clearing the CLI's cached SSO tokens.

    `aws sso logout` invalidates every cached SSO session on this machine;
    the CLI has no per-profile logout.
    """
    import subprocess

    try:
        completed = subprocess.run(
            ["aws", "sso", "logout"], capture_output=True, text=True, timeout=30
        )
    except FileNotFoundError as exc:
        raise AwsCliMissing("AWS CLI v2 is required to sign out") from exc
    except subprocess.TimeoutExpired as exc:
        raise AwsCliSpawnError("aws sso logout timed out") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:300]
        raise AwsCliSpawnError(detail or "aws sso logout failed")
    return {"signed_out": True}


def _load_botocore():
    try:
        import botocore.session
        from botocore.config import Config
    except ImportError as exc:
        raise AwsSdkUnavailable("boto3 is not installed") from exc
    return botocore.session, Config


def available_profiles() -> list[str]:
    """Return profiles exactly as botocore resolves them."""
    botocore_session, _ = _load_botocore()
    try:
        return sorted(botocore_session.get_session().available_profiles)
    except Exception as exc:
        raise AwsSdkUnavailable(f"Unable to read AWS profiles: {exc}") from exc


def verify_profile(profile: str, timeout: float = 3.0) -> tuple[bool, str]:
    """Verify a profile with a short-timeout sts:GetCallerIdentity call."""
    try:
        botocore_session, Config = _load_botocore()
    except AwsSdkUnavailable as exc:
        return False, str(exc)

    try:
        session = botocore_session.get_session()
        session.set_config_variable("profile", profile)
        region = session.get_config_variable("region") or "us-east-1"
        sts = session.create_client(
            "sts",
            region_name=region,
            config=Config(
                connect_timeout=timeout,
                read_timeout=timeout,
                retries={"max_attempts": 1},
            ),
        )
        identity = sts.get_caller_identity()
        arn = identity.get("Arn")
        return True, f"Signed in as {arn}" if arn else "AWS SSO session is active"
    except Exception as exc:
        return False, str(exc) or exc.__class__.__name__


def _drain_output(process: subprocess.Popen[str], buffer: OutputBuffer) -> None:
    stream = process.stdout
    if stream is None:
        return
    try:
        for line in stream:
            buffer.append(line)
    except (OSError, ValueError):
        # The stream can be closed while a timed-out process is being killed.
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _spawn_login_process(command: list[str]) -> subprocess.Popen[str]:
    try:
        return subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise AwsCliMissing("AWS CLI is not installed or is not on PATH") from exc
    except OSError as exc:
        raise AwsCliSpawnError(f"Unable to start AWS CLI: {exc}") from exc


def _register_login(entry: dict[str, Any]) -> str:
    login_id = uuid.uuid4().hex
    with _REGISTRY_LOCK:
        LOGIN_REGISTRY[login_id] = entry
    reader = threading.Thread(
        target=_drain_output,
        args=(entry["process"], entry["buffer"]),
        name=f"rdst-aws-login-{login_id[:8]}",
        daemon=True,
    )
    reader.start()
    return login_id


def start_login(profile: str) -> dict[str, Any]:
    """Validate a profile, verify it, or start a non-blocking AWS CLI login."""
    if profile not in available_profiles():
        raise UnknownAwsProfile(f"Unknown AWS profile: {profile}")

    signed_in, detail = verify_profile(profile)
    if signed_in:
        return {"login_id": None, "state": "already_signed_in", "detail": detail}

    process = _spawn_login_process(["aws", "sso", "login", "--profile", profile])
    login_id = _register_login(
        {
            "process": process,
            "profile": profile,
            "started_at": time.monotonic(),
            "buffer": OutputBuffer(),
        }
    )
    return {
        "login_id": login_id,
        "state": "started",
        "detail": "AWS SSO login started; approve the request in your browser",
    }


def start_sso_session_login(start_url: str, region: str) -> dict[str, Any]:
    """Device-authorize a token from a start URL alone, without a profile yet.

    Writes the stable `[sso-session <name>]` block for this start URL, then
    runs `aws sso login --sso-session <name>` so the browser device flow
    caches a token keyed to that session. Both account listing and every
    finalized profile reuse this session, so the cached token is exactly the
    one botocore loads for credentials. Reports already_signed_in only when a
    non-expired token is loadable for this session specifically; a token
    cached under a different session (a user's own login) does not count.
    """
    from .aws_profiles import ensure_sso_session
    from .aws_sso import load_session_token, stable_session_name

    session_name = stable_session_name(start_url)
    ensure_sso_session(name=session_name, sso_start_url=start_url, sso_region=region)

    token, _region = load_session_token(session_name)
    if token:
        return {
            "login_id": None,
            "state": "already_signed_in",
            "detail": "AWS SSO session is already active",
        }

    process = _spawn_login_process(
        ["aws", "sso", "login", "--sso-session", session_name]
    )
    login_id = _register_login(
        {
            "process": process,
            "start_url": start_url,
            "session_name": session_name,
            "started_at": time.monotonic(),
            "buffer": OutputBuffer(),
        }
    )
    return {
        "login_id": login_id,
        "state": "started",
        "detail": "AWS SSO login started; approve the request in your browser",
    }


def parse_verification_url(output: str) -> Optional[str]:
    """Extract AWS CLI device-authorization URLs from captured output."""
    match = _VERIFICATION_URL_RE.search(output)
    if match is None:
        return None
    return match.group(0).rstrip(".,;:!?)]}")


def _remove_login(login_id: str) -> None:
    with _REGISTRY_LOCK:
        LOGIN_REGISTRY.pop(login_id, None)


def _expire_terminal_login(login_id: str, completed_at: float) -> None:
    """Remove a terminal login after its result has been available briefly."""
    with _REGISTRY_LOCK:
        entry = LOGIN_REGISTRY.get(login_id)
        if entry is not None and entry.get("completed_at") == completed_at:
            LOGIN_REGISTRY.pop(login_id, None)


def _retain_terminal_login(login_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Cache a terminal payload long enough for repeated browser polls."""
    completed_at = time.monotonic()
    with _REGISTRY_LOCK:
        entry = LOGIN_REGISTRY.get(login_id)
        if entry is None:
            return payload
        entry["terminal_payload"] = payload
        entry["completed_at"] = completed_at

    cleanup = threading.Timer(
        TERMINAL_RETENTION_SECONDS,
        _expire_terminal_login,
        args=(login_id, completed_at),
    )
    cleanup.daemon = True
    cleanup.start()
    return payload


def _status_payload(
    *,
    state: str,
    detail: str,
    fallback: str,
    verification_url: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "detail": detail,
        "verification_url": verification_url,
        "fallback_command": fallback,
    }


def _entry_fallback(entry: dict[str, Any]) -> str:
    profile = entry.get("profile")
    if profile:
        return fallback_command(profile)
    return sso_session_fallback_command(entry.get("session_name", ""))


def _verify_login(entry: dict[str, Any]) -> tuple[bool, str]:
    """Detect a completed login for either a named profile or an sso-session.

    Profile logins verify with STS; sso-session logins have no role yet, so a
    freshly cached device token is the success signal.
    """
    profile = entry.get("profile")
    if profile:
        return verify_profile(profile)

    from .aws_sso import load_session_token

    token, _region = load_session_token(entry.get("session_name", ""))
    if token:
        return True, "AWS SSO session is active"
    return False, "Waiting for AWS SSO browser approval"


def get_login_status(login_id: str) -> dict[str, Any]:
    """Poll a login process and opportunistically detect fresh credentials."""
    with _REGISTRY_LOCK:
        entry = LOGIN_REGISTRY.get(login_id)
    if entry is None:
        raise KeyError(login_id)

    terminal_payload = entry.get("terminal_payload")
    completed_at = entry.get("completed_at")
    if terminal_payload is not None and completed_at is not None:
        if time.monotonic() - completed_at >= TERMINAL_RETENTION_SECONDS:
            _remove_login(login_id)
            raise KeyError(login_id)
        return terminal_payload

    process: subprocess.Popen[str] = entry["process"]
    buffer: OutputBuffer = entry["buffer"]
    fallback = _entry_fallback(entry)
    verification_url = parse_verification_url(buffer.text())

    if time.monotonic() - entry["started_at"] > LOGIN_TIMEOUT_SECONDS:
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        return _retain_terminal_login(
            login_id,
            _status_payload(
                state="timeout",
                detail="AWS SSO login timed out after 300 seconds",
                fallback=fallback,
                verification_url=verification_url,
            ),
        )

    return_code = process.poll()
    if return_code is None:
        signed_in, identity_detail = _verify_login(entry)
        if signed_in:
            try:
                process.terminate()
            except OSError:
                pass
            return _retain_terminal_login(
                login_id,
                _status_payload(
                    state="success",
                    detail=identity_detail,
                    fallback=fallback,
                    verification_url=verification_url,
                ),
            )
        output_detail = buffer.tail()
        return _status_payload(
            state="running",
            detail=output_detail or "Waiting for AWS SSO browser approval",
            fallback=fallback,
            verification_url=verification_url,
        )

    if return_code == 0:
        signed_in, identity_detail = _verify_login(entry)
        if signed_in:
            return _retain_terminal_login(
                login_id,
                _status_payload(
                    state="success",
                    detail=identity_detail,
                    fallback=fallback,
                    verification_url=verification_url,
                ),
            )
        return _retain_terminal_login(
            login_id,
            _status_payload(
                state="failed",
                detail=f"AWS CLI exited successfully, but SSO verification failed: {identity_detail}",
                fallback=fallback,
                verification_url=verification_url,
            ),
        )

    output_detail = buffer.tail()
    return _retain_terminal_login(
        login_id,
        _status_payload(
            state="failed",
            detail=output_detail or f"AWS CLI exited with status {return_code}",
            fallback=fallback,
            verification_url=verification_url,
        ),
    )

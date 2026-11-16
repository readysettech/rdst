"""Verified report delivery via the RDST keyservice.

Registering an address sends a random pickup secret along; once the recipient
clicks the link in the email that registration sent, /report-status hands the
report credential to whoever presents that secret, which is only this install.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from filelock import FileLock

from shared.constants import ensure_private_data_dir, rdst_data_dir
from shared.hosted_reports import (
    hosted_reports_disabled_message,
    hosted_reports_enabled,
)
from shared.keyservice import (
    DEFAULT_KEYSERVICE_URL,
    keyservice_base_url,
)
from shared.persistence import replace_with_retry

KEYSERVICE_URL = keyservice_base_url()

# Pickup secrets for registrations still waiting on their verification click, by
# address, kept in a private file so a restart before the click still collects the
# credential. Every registration keeps its own pickup, since each email's link approves
# only its own; a pickup lives as long as the keyservice keeps it. The file lock covers
# every process sharing the data dir (the web server and CLI runs).
PICKUP_TTL_SECONDS = 86400
MAX_PICKUPS_PER_EMAIL = 10
# A status check presents only the newest few, so it stays a few short requests.
MAX_PICKUPS_POLLED = 3
_PICKUPS_FILE = "report-pickups.json"


def _pickups_path() -> Path:
    return ensure_private_data_dir() / _PICKUPS_FILE


@contextmanager
def _pickup_store() -> Iterator[Path]:
    path = _pickups_path()
    with FileLock(f"{path}.lock", timeout=10, mode=0o600):
        yield path


def _load_pickups() -> Dict[str, List[Dict[str, Any]]]:
    """Live pickups by address; a file with unsafe ownership or permissions is ignored."""
    try:
        fd = os.open(_pickups_path(), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or (os.name == "posix" and (
                    info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600)):
                return {}
            values = json.loads(source.read(1 << 20))
    except (OSError, ValueError):
        return {}
    now = time.time()
    live: Dict[str, List[Dict[str, Any]]] = {}
    for email, entries in (values if isinstance(values, dict) else {}).items():
        kept = [entry for entry in (entries if isinstance(entries, list) else [])
                if isinstance(entry, dict) and isinstance(entry.get("pickup"), str)
                and isinstance(entry.get("expires_at"), (int, float))
                and entry["expires_at"] > now]
        if kept:
            live[email] = kept
    return live


def _save_pickups(pickups: Dict[str, List[Dict[str, Any]]]) -> None:
    path = _pickups_path()
    if not pickups:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    fd, temporary = tempfile.mkstemp(prefix=".report-pickups-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as destination:
            if os.name == "posix":
                os.fchmod(destination.fileno(), 0o600)
            json.dump(pickups, destination)
            destination.flush()
            os.fsync(destination.fileno())
        replace_with_retry(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _remember_pickup(email: str, pickup: str) -> None:
    with _pickup_store():
        pickups = _load_pickups()
        entries = pickups.get(email, [])
        entries.append({"pickup": pickup, "expires_at": time.time() + PICKUP_TTL_SECONDS})
        pickups[email] = entries[-MAX_PICKUPS_PER_EMAIL:]
        _save_pickups(pickups)


def _pickups_for(email: str) -> List[str]:
    """This address's pickups, newest first: the latest email is the likeliest clicked."""
    with _pickup_store():
        entries = _load_pickups().get(email, [])
    return [entry["pickup"] for entry in reversed(entries)]


def _discard_pickup(email: str, pickup: str) -> None:
    """Drop a pickup no email waits on; a store that cannot be written keeps it to expire."""
    try:
        _forget_pickups(email, [pickup])
    except Exception:
        pass


def _forget_pickups(email: str, used: List[str]) -> None:
    with _pickup_store():
        pickups = _load_pickups()
        kept = [entry for entry in pickups.get(email, []) if entry["pickup"] not in used]
        if kept:
            pickups[email] = kept
        else:
            pickups.pop(email, None)
        _save_pickups(pickups)


class EmailService:
    def __init__(self, keyservice_url: Optional[str] = None):
        self._url = (keyservice_url or keyservice_base_url()).rstrip("/")

    def register_email(
        self,
        email: str,
        html: Optional[str] = None,
        subject: Optional[str] = None,
        summary: Optional[Dict[str, Any]] = None,
        ttl_days: int = 30,
        mode: str = "link",
    ) -> Dict[str, Any]:
        """Register an email for report delivery. If html/subject are
        supplied, the keyservice queues the report and delivers it on the
        verify-link click. Returns the keyservice JSON response."""
        if not hosted_reports_enabled():
            return {"success": False, "error": hosted_reports_disabled_message()}
        return self._sync(self._register(email, html, subject, summary, ttl_days, mode))

    async def _register(
        self,
        email: str,
        html: Optional[str],
        subject: Optional[str],
        summary: Optional[Dict[str, Any]],
        ttl_days: int,
        mode: str,
    ) -> Dict[str, Any]:
        import httpx2 as httpx
        pickup = secrets.token_urlsafe(32)
        # Stored before anything is sent: a pickup that cannot be kept would leave an email
        # in the inbox whose click can never be collected here.
        try:
            _remember_pickup(email, pickup)
        except Exception as exc:
            return {
                "success": False,
                "error": (f"Could not save the verification state in {rdst_data_dir()} "
                          f"({exc}). No email was sent."),
            }
        try:
            payload: Dict[str, Any] = {"email": email, "pickup": pickup}
            if html:
                payload["html"] = html
                payload["subject"] = subject or "RDST Report"
                payload["mode"] = mode
                payload["ttl_days"] = ttl_days
                if summary:
                    payload["summary"] = summary
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._url}/register-report",
                    json=payload,
                )
                data = resp.json()
                if resp.status_code in (200, 201):
                    if data.get("verified"):
                        # Delivered or already verified: no email waits on this pickup.
                        _discard_pickup(email, pickup)
                    return data
                _discard_pickup(email, pickup)
                return {"success": False, "error": data.get("detail", f"HTTP {resp.status_code}")}
        except Exception as e:
            # The email may still have gone out, so the pickup stays until it expires.
            return {"success": False, "error": str(e)}

    def check_status(self, email: str) -> Dict[str, Any]:
        """One verification check for `email`. A check that outlasts its deadline reads
        as still pending; the next one picks up from the stored pickups."""
        if not hosted_reports_enabled():
            return {"verified": False, "error": hosted_reports_disabled_message()}
        try:
            return self._sync(self._check_status(email))
        except TimeoutError:
            return {"verified": False, "pending": True}

    def poll_verification(
        self, email: str, timeout_seconds: int = 300, interval: float = 3.0
    ) -> Dict[str, Any]:
        """Poll until the email is verified or timeout."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            result = self._sync(self._check_status(email))
            if result.get("verified"):
                return result
            time.sleep(interval)
        return {"verified": False, "timed_out": True}

    async def _check_status(self, email: str) -> Dict[str, Any]:
        """Present each pickup this address holds until one of their links was clicked.

        The collected credential is the account's, so every other pickup for the address
        is then spent; an expired pickup never collects anything. A report this address
        queued that went out through a click on another install's email reads as
        verified for delivery, with no credential.
        """
        import httpx2 as httpx
        pickups = _pickups_for(email)[:MAX_PICKUPS_POLLED]
        spent: List[str] = []
        released = False
        data: Dict[str, Any] = {"verified": False}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                for pickup in pickups or [None]:
                    resp = await client.get(
                        f"{self._url}/report-status",
                        params={"email": email},
                        headers={"X-RDST-Report-Pickup": pickup} if pickup else None,
                    )
                    if resp.status_code != 200:
                        return {
                            "verified": False,
                            "error": f"Verification service returned HTTP {resp.status_code}",
                        }
                    data = resp.json()
                    released = released or bool(data.get("report_released"))
                    if pickup and data.get("expired"):
                        spent.append(pickup)
                    if data.get("report_token"):
                        spent = _pickups_for(email)
                        break
        except Exception as exc:
            return {"verified": False, "error": str(exc)}
        finally:
            if spent:
                _forget_pickups(email, spent)
        if released and not data.get("report_token"):
            return {**data, "verified": True, "report_token": None, "report_released": True}
        return data

    def send_report(
        self,
        report_token: str,
        html_body: str,
        subject: str = "RDST Audit Report",
        as_attachment: bool = False,
        attachment_name: str = "rdst_report.html",
        mode: str = "inline",
        ttl_days: int = 30,
        summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send a report using a verified report token. mode='link' hosts
        the HTML and emails a View button (best for CSS-heavy reports);
        mode='inline' embeds the HTML directly."""
        if not hosted_reports_enabled():
            return {"success": False, "error": hosted_reports_disabled_message()}
        return self._sync(self._send(report_token, html_body, subject, as_attachment, attachment_name, mode, ttl_days, summary))

    async def _send(
        self,
        report_token: str,
        html_body: str,
        subject: str,
        as_attachment: bool = False,
        attachment_name: str = "rdst_report.html",
        mode: str = "inline",
        ttl_days: int = 30,
        summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        import httpx2 as httpx
        try:
            payload = {
                "report_token": report_token,
                "html": html_body,
                "subject": subject,
                "as_attachment": as_attachment,
                "attachment_name": attachment_name,
                "mode": mode,
                "ttl_days": ttl_days,
            }
            if summary:
                payload["summary"] = summary
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._url}/send-report",
                    json=payload,
                )
                if resp.status_code in (200, 201):
                    return {"success": True}
                try:
                    data = resp.json()
                    return {"success": False, "error": data.get("detail", f"HTTP {resp.status_code}")}
                except Exception:
                    return {"success": False, "error": f"HTTP {resp.status_code}"}
        except httpx.TimeoutException:
            return {"success": False, "error": "Request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def queue_report(
        self,
        email: str,
        html_body: str,
        subject: str,
        report_token: Optional[str] = None,
        mode: str = "link",
        ttl_days: int = 30,
        summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send a report, or hand it to the keyservice to release once the
        recipient clicks their verification link.

        Same delivery semantics as `send_report_with_verification` minus the
        blocking wait, so it is safe to call from a request handler. A stale
        `report_token` comes back as `stale_token=True` so the caller can drop
        it from local config; `queued=True` means verification is pending.
        """
        if not hosted_reports_enabled():
            return {"success": False, "error": hosted_reports_disabled_message()}
        stale_token = False
        if report_token:
            result = self.send_report(
                report_token, html_body, subject,
                mode=mode, ttl_days=ttl_days, summary=summary,
            )
            if result.get("success"):
                return {**result, "report_token": report_token, "queued": False}
            err = (result.get("error") or "").lower()
            if "invalid" in err or "not yet verified" in err or "unverified" in err:
                stale_token = True
            else:
                return {**result, "report_token": report_token}

        reg = self.register_email(
            email,
            html=html_body,
            subject=subject,
            summary=summary,
            ttl_days=ttl_days,
            mode=mode,
        )
        if not reg.get("success"):
            return {
                "success": False,
                "stale_token": stale_token,
                "error": reg.get("error", "Registration failed"),
            }

        token = reg.get("report_token")
        if reg.get("verified"):
            if reg.get("report_sent"):
                return {
                    "success": True, "report_token": token,
                    "queued": False, "stale_token": stale_token,
                }
            if token:
                result = self.send_report(
                    token, html_body, subject,
                    mode=mode, ttl_days=ttl_days, summary=summary,
                )
                return {
                    **result, "report_token": token,
                    "queued": False, "stale_token": stale_token,
                }
            return {"success": False, "error": "Keyservice returned verified=true with no token"}

        return {
            "success": True,
            "queued": True,
            "stale_token": stale_token,
            "report_queued": bool(reg.get("report_queued")),
        }

    def send_report_with_verification(
        self,
        email: str,
        html_body: str,
        subject: str,
        report_token: Optional[str] = None,
        on_status=None,
        mode: str = "link",
        ttl_days: int = 30,
        summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send a report, registering + verifying the email if needed.

        Caller must pass a `report_token` that belongs to `email` — passing
        another email's token is rejected upstream.

        On a stale token the result includes `stale_token=True` so the
        caller can drop the entry from local config.
        """
        if not hosted_reports_enabled():
            return {"success": False, "error": hosted_reports_disabled_message()}

        def _send_with_mode(token):
            return self.send_report(
                token, html_body, subject,
                mode=mode, ttl_days=ttl_days, summary=summary,
            )

        if report_token:
            result = _send_with_mode(report_token)
            if result.get("success"):
                return {**result, "report_token": report_token}
            err = (result.get("error") or "").lower()
            if "invalid" in err or "not yet verified" in err or "unverified" in err:
                stale_result = {**result, "stale_token": True}
            else:
                return {**result, "report_token": report_token}

        reg = self.register_email(
            email,
            html=html_body,
            subject=subject,
            summary=summary,
            ttl_days=ttl_days,
            mode=mode,
        )
        if not reg.get("success"):
            return {"success": False, "error": reg.get("error", "Registration failed")}

        if reg.get("verified"):
            token = reg.get("report_token")
            if reg.get("report_sent"):
                return {"success": True, "report_token": token, "queued": False}
            if token:
                result = _send_with_mode(token)
                return {**result, "report_token": token}
            return {"success": False, "error": "Keyservice returned verified=true with no token"}

        report_queued = bool(reg.get("report_queued"))
        if on_status:
            if report_queued:
                on_status("Verification email sent - click the link in your inbox to release your report...")
            else:
                on_status("Verification email sent - click the link in your inbox...")

        poll_result = self.poll_verification(email)
        if not poll_result.get("verified"):
            if report_queued:
                return {
                    "success": True,
                    "queued": True,
                    "message": (
                        "Verification still pending - your report will be sent automatically "
                        "as soon as you click the verify link."
                    ),
                }
            return {"success": False, "error": "Verification timed out - check your email and try again"}

        token = poll_result["report_token"]
        if report_queued:
            return {"success": True, "report_token": token, "queued": False}
        result = _send_with_mode(token)
        return {**result, "report_token": token}

    @staticmethod
    def _sync(coro):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(lambda: asyncio.run(coro)).result(timeout=35)
            return loop.run_until_complete(coro)
        except RuntimeError:
            return asyncio.run(coro)

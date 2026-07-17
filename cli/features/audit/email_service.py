"""Verified report delivery via the RDST keyservice."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from shared.keyservice import (
    DEFAULT_KEYSERVICE_URL,
    keyservice_base_url,
)

KEYSERVICE_URL = keyservice_base_url()


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
        import httpx
        try:
            payload: Dict[str, Any] = {"email": email}
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
                    return data
                return {"success": False, "error": data.get("detail", f"HTTP {resp.status_code}")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def check_status(self, email: str) -> Dict[str, Any]:
        """Single non-blocking verification check for `email`."""
        return self._sync(self._check_status(email))

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
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self._url}/report-status",
                    params={"email": email},
                )
                if resp.status_code == 200:
                    return resp.json()
                return {
                    "verified": False,
                    "error": f"Verification service returned HTTP {resp.status_code}",
                }
        except Exception as exc:
            return {"verified": False, "error": str(exc)}

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
        import httpx
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
                on_status("Verification email sent — click the link in your inbox to release your report...")
            else:
                on_status("Verification email sent — click the link in your inbox...")

        poll_result = self.poll_verification(email)
        if not poll_result.get("verified"):
            if report_queued:
                return {
                    "success": True,
                    "queued": True,
                    "message": (
                        "Verification still pending — your report will be sent automatically "
                        "as soon as you click the verify link."
                    ),
                }
            return {"success": False, "error": "Verification timed out — check your email and try again"}

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

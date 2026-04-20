"""Email service — verified report delivery via the RDST keyservice.

Flow:
  1. register_email() → sends verification email, returns immediately
  2. poll_verification() → CLI polls until user clicks link in email
  3. send_report() → sends HTML report using the verified report_token
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

KEYSERVICE_URL = "https://rdst-keyservice.readysetio.workers.dev"


class EmailService:
    """Handles report email registration, verification polling, and delivery."""

    def __init__(self, keyservice_url: str = KEYSERVICE_URL):
        self._url = keyservice_url

    # ------------------------------------------------------------------
    # Step 1: Register email (sends verification link)
    # ------------------------------------------------------------------

    def register_email(self, email: str) -> Dict[str, Any]:
        """Register an email for report delivery.

        Returns:
            {"success": True, "verified": True, "report_token": "..."} if already verified
            {"success": True, "verified": False} if verification email sent
            {"success": False, "error": "..."} on failure
        """
        return self._sync(self._register(email))

    async def _register(self, email: str) -> Dict[str, Any]:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    f"{self._url}/register-report",
                    json={"email": email},
                )
                data = resp.json()
                if resp.status_code in (200, 201):
                    return data
                return {"success": False, "error": data.get("detail", f"HTTP {resp.status_code}")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Step 2: Poll for verification (user clicks link in email)
    # ------------------------------------------------------------------

    def poll_verification(self, email: str, timeout_seconds: int = 90, interval: float = 3.0) -> Dict[str, Any]:
        """Poll keyservice until the email is verified or timeout.

        Returns:
            {"verified": True, "report_token": "..."} on success
            {"verified": False, "timed_out": True} on timeout
        """
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
                return {"verified": False}
        except Exception:
            return {"verified": False}

    # ------------------------------------------------------------------
    # Step 3: Send report (requires verified report_token)
    # ------------------------------------------------------------------

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
        """Send an HTML report email using a verified report token.

        mode:
            - "inline" (default): embed HTML in the email body. Best for short
              reports that don't rely on much CSS — Gmail will strip most
              styles, so layout suffers for CSS-heavy reports.
            - "link": host the HTML on the keyservice and email a polished
              "View Full Report" button. Recipient opens the link in their
              browser and sees the report at full fidelity. Recommended for
              CSS-heavy reports. Hosted reports auto-expire after ttl_days
              (default 30).

        as_attachment is a back-compat path that attaches the HTML as a .html
        file. Mostly superseded by mode="link" since Gmail's HTML attachment
        preview is broken.
        """
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

    # ------------------------------------------------------------------
    # Convenience: full flow (register + poll + send) in one call
    # ------------------------------------------------------------------

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
        """Full flow: register if needed, poll for verification, then send.

        Args:
            email: Recipient email address
            html_body: HTML report content
            subject: Email subject line
            report_token: Existing verified token (skip registration if provided)
            on_status: Callback(message: str) for status updates
            mode: "link" (default) hosts the HTML and emails a View button.
                  "inline" embeds HTML in the email body (Gmail strips CSS).
            ttl_days: How long hosted reports are kept (default 30).
            summary: Optional summary dict for email preview tiles.

        Returns:
            {"success": True, "report_token": "..."} on success
            {"success": False, "error": "..."} on failure
        """
        def _send_with_mode(token):
            return self.send_report(
                token, html_body, subject,
                mode=mode, ttl_days=ttl_days, summary=summary,
            )

        # If we have a token, try sending directly
        if report_token:
            result = _send_with_mode(report_token)
            if result.get("success"):
                return {**result, "report_token": report_token}
            # Token might be invalid — fall through to re-register

        # Register (may return already-verified)
        reg = self.register_email(email)
        if not reg.get("success"):
            return {"success": False, "error": reg.get("error", "Registration failed")}

        if reg.get("verified"):
            # Already verified — send immediately
            token = reg["report_token"]
            result = _send_with_mode(token)
            return {**result, "report_token": token}

        # Not verified — poll until they click the link
        if on_status:
            on_status("Verification email sent — click the link in your inbox...")

        poll_result = self.poll_verification(email)
        if not poll_result.get("verified"):
            return {"success": False, "error": "Verification timed out — check your email and try again"}

        token = poll_result["report_token"]
        result = _send_with_mode(token)
        return {**result, "report_token": token}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _sync(coro):
        """Run an async coroutine synchronously."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(lambda: asyncio.run(coro)).result(timeout=35)
            return loop.run_until_complete(coro)
        except RuntimeError:
            return asyncio.run(coro)

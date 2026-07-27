"""Email delivery for a saved run's report.

Ships the same HTML artifact the CLI sends, so the audit and fleet API
routes deliver identical output for the run they name.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel

from shared.api.email import normalized_email
from shared.api.guards import require_local_request
from shared.config.targets import TargetsConfig

from .artifact import report_html_for_run

logger = logging.getLogger(__name__)


class RunEmailRequest(BaseModel):
    # Absent = send to the machine's stored identity.
    email: Optional[str] = None


class RunEmailResponse(BaseModel):
    # sent: delivered now. verification_sent: the keyservice holds the report
    # and releases it when the recipient clicks the link in their inbox.
    # unavailable: the keyservice could not be reached.
    status: str
    email: str
    verified: bool


async def deliver_run_report(
    run_id: str, request: Request, body: RunEmailRequest
) -> RunEmailResponse:
    """Email a saved run's report: the same HTML artifact the CLI sends."""
    require_local_request(request)

    artifact = await asyncio.to_thread(report_html_for_run, run_id)
    if artifact is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This run has no report to email. Run a health check on the "
                "target to generate one."
            ),
        )
    html, subject = artifact

    config = TargetsConfig()
    config.load()
    email = normalized_email(body.email) if body.email else config.get_identity()["email"]
    if not email:
        raise HTTPException(status_code=400, detail="No email address on file")

    from features.audit.email_service import EmailService

    token = config.get_token_for_email(email)
    result = await asyncio.to_thread(
        EmailService().queue_report, email, html, subject, token,
    )

    changed = False
    if result.get("stale_token"):
        config.remove_email(email)
        changed = True
    if not config.get_email():
        config.set_email(email)
        changed = True
    if result.get("success") and result.get("report_token"):
        config.add_verified_email(email, result["report_token"])
        changed = True
    if changed:
        config.save()

    if not result.get("success"):
        logger.warning("Report delivery failed for %s: %s", run_id, result.get("error"))
        return RunEmailResponse(status="unavailable", email=email, verified=False)
    if result.get("queued"):
        return RunEmailResponse(status="verification_sent", email=email, verified=False)
    return RunEmailResponse(status="sent", email=email, verified=True)

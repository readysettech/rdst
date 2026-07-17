"""API routes for user-level RDST settings."""

from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.api.guards import is_loopback_request, same_host_from_headers
from shared.config.targets import TargetsConfig

router = APIRouter(prefix="/settings")
logger = logging.getLogger(__name__)

# Kept byte-for-byte in sync with the client gate regex in
# web-apps/apps/rdst/src/components/emailValidation.ts. The parity test
# (tests/unit/test_settings_email_route.py + emailValidation.test.tsx) drives
# both against the same fixture so a divergence turns a suite red.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class EmailRequest(BaseModel):
    email: str
    # Optional lead enrichment: stored locally and attached to telemetry
    # alongside the email. Never required.
    first_name: str = ""
    last_name: str = ""


class EmailResponse(BaseModel):
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    verified: bool = False


class EmailUpdateResponse(BaseModel):
    success: bool
    email: str
    # Whether the email is verified (possibly instantly, when the keyservice
    # already knew it from the trial or CLI report flow) and whether a
    # verification email is on its way. verified=False with
    # verification_started=False means the keyservice was unreachable; the
    # gate holds and offers a retry.
    verified: bool = False
    verification_started: bool = False


class VerifyPollResponse(BaseModel):
    verified: bool


def _normalized_email(email: str) -> str:
    """Trim, lowercase, and format-validate. Lowercasing makes the stored
    identity canonical so the same human maps to one PostHog person."""
    value = (email or "").strip().lower()
    if not _EMAIL_RE.match(value):
        raise HTTPException(status_code=400, detail="Invalid email address")
    return value


def _make_resolver():
    try:
        import dns.resolver
    except ImportError:
        return None
    resolver = dns.resolver.Resolver()
    resolver.timeout = 3.0
    resolver.lifetime = 3.0
    return resolver


def _domain_has_mx(domain: str, resolver=None) -> Optional[bool]:
    """Best-effort mail-domain reachability check.

    Returns True when the domain has a mail route, False when it provably has
    none (reject the address), and None when the answer can't be determined
    (resolver unavailable, timeout, or network error) so the caller lets it
    through — an offline user must never be stranded at the gate.
    """
    if resolver is None:
        resolver = _make_resolver()
    if resolver is None:
        return None
    try:
        import dns.exception
        import dns.resolver
    except ImportError:
        return None
    try:
        answers = resolver.resolve(domain, "MX")
        return len(answers) > 0
    except dns.resolver.NXDOMAIN:
        return False
    except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        # No MX record. Per RFC 5321 an address record acts as an implicit mail
        # exchanger, so a resolvable host is allowed; a nonexistent one is not.
        try:
            resolver.resolve(domain, "A")
            return True
        except dns.resolver.NXDOMAIN:
            return False
        except Exception:
            return None
    except (dns.exception.Timeout, Exception):
        return None


def _fire_email_captured(email: str, domain: str) -> None:
    """Fire the PostHog `email_captured` funnel event, fire-and-forget. Any
    failure here is swallowed so telemetry never blocks or breaks the save."""
    try:
        from shared.telemetry import telemetry

        telemetry.track(
            "email_captured",
            {
                "display_name": "RDST Email Captured",
                "source": "gate",
                "email": email,
                "email_domain": domain,
            },
        )
    except Exception:
        pass


@router.get("/email")
async def get_email(request: Request) -> EmailResponse:
    if not is_loopback_request(request):
        raise HTTPException(status_code=403, detail="Forbidden")
    cfg = TargetsConfig()
    cfg.load()
    identity = cfg.get_identity()
    return EmailResponse(
        email=identity["email"],
        first_name=identity["first_name"],
        last_name=identity["last_name"],
        verified=identity["verified"],
    )


@router.post("/email")
async def set_email(request: Request, body: EmailRequest) -> EmailUpdateResponse:
    if not is_loopback_request(request):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not same_host_from_headers(request):
        raise HTTPException(status_code=403, detail="Origin/Referer host mismatch")
    email = _normalized_email(body.email)
    domain = email.split("@", 1)[1]
    if _domain_has_mx(domain) is False:
        raise HTTPException(
            status_code=400,
            detail=(
                "We couldn't find a mail server for that email domain. "
                "Please double-check the address."
            ),
        )
    cfg = TargetsConfig()
    cfg.load()
    cfg.set_email(email)
    cfg.set_name(body.first_name.strip()[:100], body.last_name.strip()[:100])
    cfg.save()
    _fire_email_captured(email, domain)

    # New signups prove mailbox access through the keyservice — the same
    # verification a trial registration performs, minus the trial token. An
    # email already verified by any flow (trial, CLI audit report, this gate
    # on another machine) comes back verified instantly and no email is sent.
    # Names are deliberately NOT sent: they live on this machine and in
    # telemetry only.
    from features.audit.email_service import EmailService

    result = EmailService().register_email(email)
    if result.get("verified"):
        token = result.get("report_token")
        if token:
            cfg.add_verified_email(email, token)
            cfg.save()
        return EmailUpdateResponse(
            success=True, email=email, verified=True, verification_started=True,
        )
    if result.get("error"):
        logger.warning("Email verification registration failed: %s", result["error"])
        raise HTTPException(
            status_code=502,
            detail=(
                "The RDST verification service is temporarily unavailable. "
                "Please try again in a moment."
            ),
        )
    return EmailUpdateResponse(
        success=True, email=email, verified=False, verification_started=True,
    )


@router.post("/email/verify-poll")
async def verify_poll(request: Request) -> VerifyPollResponse:
    """One non-blocking check of the primary email's verification, mirroring
    the CLI report flow's /report-status poll; persists the report token the
    moment the keyservice confirms the click."""
    if not is_loopback_request(request):
        raise HTTPException(status_code=403, detail="Forbidden")
    cfg = TargetsConfig()
    cfg.load()
    identity = cfg.get_identity()
    if not identity["email"]:
        return VerifyPollResponse(verified=False)
    if identity["verified"]:
        return VerifyPollResponse(verified=True)

    from features.audit.email_service import EmailService

    status = EmailService().check_status(identity["email"])
    if status.get("error"):
        logger.warning("Email verification status check failed: %s", status["error"])
        raise HTTPException(
            status_code=502,
            detail=(
                "The RDST verification service is temporarily unavailable. "
                "Please try again in a moment."
            ),
        )
    if status.get("verified") and status.get("report_token"):
        cfg.add_verified_email(identity["email"], status["report_token"])
        cfg.save()
        return VerifyPollResponse(verified=True)
    return VerifyPollResponse(verified=False)

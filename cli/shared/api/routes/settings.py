"""API routes for user-level RDST settings."""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.api.guards import is_loopback_request, same_host_from_headers
from shared.config.targets import TargetsConfig

router = APIRouter(prefix="/settings")

# Kept byte-for-byte in sync with the client gate regex in
# web-apps/apps/rdst/src/components/emailValidation.ts. The parity test
# (tests/unit/test_settings_email_route.py + emailValidation.test.tsx) drives
# both against the same fixture so a divergence turns a suite red.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class EmailRequest(BaseModel):
    email: str


class EmailResponse(BaseModel):
    email: str | None = None


class EmailUpdateResponse(BaseModel):
    success: bool
    email: str


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
    return EmailResponse(email=cfg.get_email())


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
    cfg.save()
    _fire_email_captured(email, domain)
    return EmailUpdateResponse(success=True, email=email)

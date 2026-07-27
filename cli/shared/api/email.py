"""Email address normalization shared by the local API routes."""

from __future__ import annotations

import re

from fastapi import HTTPException

# Kept byte-for-byte in sync with the client gate regex in
# web-apps/apps/rdst/src/components/emailValidation.ts. The parity test
# (tests/unit/test_settings_email_route.py + emailValidation.test.tsx) drives
# both against the same fixture so a divergence turns a suite red.
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalized_email(email: str) -> str:
    """Trim, lowercase, and format-validate. Lowercasing makes the stored
    identity canonical so the same human maps to one PostHog person."""
    value = (email or "").strip().lower()
    if not EMAIL_RE.match(value):
        raise HTTPException(status_code=400, detail="Invalid email address")
    return value

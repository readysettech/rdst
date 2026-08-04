"""Email address normalization shared by the local API routes."""

from __future__ import annotations

from fastapi import HTTPException

from shared.email import EMAIL_RE


def normalized_email(email: str) -> str:
    """Trim, lowercase, and format-validate. Lowercasing makes the stored
    identity canonical so the same human maps to one PostHog person."""
    value = (email or "").strip().lower()
    if not EMAIL_RE.match(value):
        raise HTTPException(status_code=400, detail="Invalid email address")
    return value

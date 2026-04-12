"""Trial models.

This slice owns free-trial registration, activation, and balance/status checks.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TrialRegisterResult:
    """Result from trial registration attempt."""

    success: bool
    limit_display: str | None = None
    email_tier: str | None = None
    error_code: str | None = None
    detail: str | None = None
    did_you_mean: str | None = None
    status_code: int = 200


@dataclass
class TrialActivateResult:
    """Result from trial token activation."""

    success: bool
    message: str | None = None


@dataclass
class TrialStatusResult:
    """Current trial status and remaining balance."""

    active: bool
    email: str | None = None
    status: str | None = None
    remaining_cents: int | None = None
    limit_cents: int | None = None
    remaining_tokens_display: str | None = None
    limit_tokens_display: str | None = None
    percent_remaining: int | None = None

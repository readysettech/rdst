"""API routes for trial registration and status."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from features.trial.service import TrialService
from shared.api.guards import is_loopback_request, require_local_request
from shared.run_registry import run_registry

router = APIRouter()


class TrialRegisterRequest(BaseModel):
    email: str


class TrialRegisterResponse(BaseModel):
    success: bool
    limit_display: str | None = None
    email_tier: str | None = None
    error_code: str | None = None
    detail: str | None = None
    did_you_mean: str | None = None
    status_code: int = 200
    # Set when the email was already verified and the keyservice handed the
    # token back directly - the UI can activate without any inbox round-trip.
    trial_token: str | None = None
    # Set when the email was already verified and the keyservice emailed a
    # fresh link to the token page instead of starting verification.
    token_resent: bool = False
    # The account's real balance on a resend, so activation preserves it.
    limit_cents: int | None = None
    remaining_cents: int | None = None


class TrialActivateRequest(BaseModel):
    token: str
    email: str
    email_tier: str | None = None
    limit_cents: int | None = None
    remaining_cents: int | None = None


class TrialActivateResponse(BaseModel):
    success: bool
    message: str | None = None


class TrialStatusResponse(BaseModel):
    active: bool
    email: str | None = None
    status: str | None = None
    remaining_cents: int | None = None
    limit_cents: int | None = None
    remaining_tokens_display: str | None = None
    limit_tokens_display: str | None = None
    percent_remaining: int | None = None


class TrialSimulationResponse(BaseModel):
    success: bool
    message: str | None = None


@router.post("/trial/register")
async def register_trial(
    request: Request, body: TrialRegisterRequest
) -> TrialRegisterResponse:
    require_local_request(request)

    service = TrialService()
    result = await service.register(body.email, source="web")
    return TrialRegisterResponse(
        success=result.success,
        limit_display=result.limit_display,
        email_tier=result.email_tier,
        error_code=result.error_code,
        detail=result.detail,
        did_you_mean=result.did_you_mean,
        status_code=result.status_code,
        trial_token=result.trial_token,
        token_resent=result.token_resent,
        limit_cents=result.limit_cents,
        remaining_cents=result.remaining_cents,
    )


@router.post("/trial/activate")
async def activate_trial(
    request: Request, body: TrialActivateRequest
) -> TrialActivateResponse:
    require_local_request(request)

    service = TrialService()
    result = await service.activate(
        body.token, body.email, body.email_tier, source="web",
        limit_cents=body.limit_cents, remaining_cents=body.remaining_cents,
    )
    if result.success:
        run_registry.wake_needs_key()
    return TrialActivateResponse(
        success=result.success,
        message=result.message,
    )


@router.get("/trial/status")
async def get_trial_status(request: Request) -> TrialStatusResponse:
    if not is_loopback_request(request):
        raise HTTPException(status_code=403, detail="Forbidden")

    service = TrialService()
    result = service.get_status()
    return TrialStatusResponse(
        active=result.active,
        email=result.email,
        status=result.status,
        remaining_cents=result.remaining_cents,
        limit_cents=result.limit_cents,
        remaining_tokens_display=result.remaining_tokens_display,
        limit_tokens_display=result.limit_tokens_display,
        percent_remaining=result.percent_remaining,
    )


@router.post("/trial/simulate/exhaust")
async def simulate_trial_exhaustion(request: Request) -> TrialSimulationResponse:
    require_local_request(request)

    service = TrialService()
    result = service.simulate_exhausted()
    if not result.active and result.status == "exhausted":
        return TrialSimulationResponse(
            success=True,
            message="Trial marked as exhausted for simulation.",
        )
    return TrialSimulationResponse(
        success=False,
        message="No active trial token found to simulate.",
    )

"""API routes for trial registration and status."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .env import _is_loopback_request, _same_host_from_headers

router = APIRouter()


# --- Request/Response Models ---


class TrialRegisterRequest(BaseModel):
    email: str


class TrialRegisterResponse(BaseModel):
    success: bool
    limit_display: Optional[str] = None
    email_tier: Optional[str] = None
    error_code: Optional[str] = None
    detail: Optional[str] = None
    did_you_mean: Optional[str] = None
    status_code: int = 200


class TrialActivateRequest(BaseModel):
    token: str
    email: str
    email_tier: Optional[str] = None


class TrialActivateResponse(BaseModel):
    success: bool
    message: Optional[str] = None


class TrialStatusResponse(BaseModel):
    active: bool
    email: Optional[str] = None
    status: Optional[str] = None
    remaining_cents: Optional[int] = None
    limit_cents: Optional[int] = None
    remaining_tokens_display: Optional[str] = None
    limit_tokens_display: Optional[str] = None
    percent_remaining: Optional[int] = None


class TrialSimulationResponse(BaseModel):
    success: bool
    message: Optional[str] = None


# --- Routes ---


@router.post("/trial/register")
async def register_trial(request: Request, body: TrialRegisterRequest) -> TrialRegisterResponse:
    if not _is_loopback_request(request):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not _same_host_from_headers(request):
        raise HTTPException(status_code=403, detail="Origin/Referer host mismatch")

    from ...services.trial_service import TrialService

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
    )


@router.post("/trial/activate")
async def activate_trial(request: Request, body: TrialActivateRequest) -> TrialActivateResponse:
    if not _is_loopback_request(request):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not _same_host_from_headers(request):
        raise HTTPException(status_code=403, detail="Origin/Referer host mismatch")

    from ...services.trial_service import TrialService

    service = TrialService()
    result = await service.activate(body.token, body.email, body.email_tier, source="web")
    return TrialActivateResponse(
        success=result.success,
        message=result.message,
    )


@router.get("/trial/status")
async def get_trial_status(request: Request) -> TrialStatusResponse:
    if not _is_loopback_request(request):
        raise HTTPException(status_code=403, detail="Forbidden")

    from ...services.trial_service import TrialService

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
    if not _is_loopback_request(request):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not _same_host_from_headers(request):
        raise HTTPException(status_code=403, detail="Origin/Referer host mismatch")

    from ...services.trial_service import TrialService

    service = TrialService()
    result = service.simulate_exhausted()
    if not result.active and result.status == "exhausted":
        return TrialSimulationResponse(success=True, message="Trial marked as exhausted for simulation.")
    return TrialSimulationResponse(success=False, message="No active trial token found to simulate.")

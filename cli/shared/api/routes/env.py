"""API routes for secure environment variable handling."""

from __future__ import annotations

import asyncio
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, SecretStr

from shared.anthropic_env import ANTHROPIC_API_KEY_NAMES
from shared.api.guards import require_local_request
from shared.env_requirements_service import EnvRequirementsService
from shared.run_registry import run_registry
from shared.telemetry import telemetry

EnvRequirementKind = Literal["target_password", "anthropic_api_key"]
EnvRequirementSource = Literal[
    "config",
    "process_env",
    "secure_store",
    "trial",
    "trial_exhausted",
    "readyset_account",
    "missing",
]

router = APIRouter()


class EnvRequirement(BaseModel):
    kind: EnvRequirementKind
    accepted_names: List[str]
    target: Optional[str] = None
    satisfied: bool
    source: EnvRequirementSource
    selected_provider: Optional[Literal["claude", "readyset"]] = None
    anthropic_key_configured: Optional[bool] = None
    readyset_account_connected: Optional[bool] = None


class EnvRequirementsResponse(BaseModel):
    keyring_available: bool
    telemetry_enabled: bool
    requirements: List[EnvRequirement]


class EnvSetRequest(BaseModel):
    name: str
    value: SecretStr
    persist: bool = True


class EnvSetResponse(BaseModel):
    success: bool
    name: str
    persisted: bool = False
    session_only: bool = True
    message: Optional[str] = None


class AiProviderSetRequest(BaseModel):
    provider: Literal["claude", "readyset"]


class AiProviderSetResponse(BaseModel):
    success: bool
    provider: Literal["claude", "readyset"]


class AnthropicValidateResponse(BaseModel):
    valid: bool
    reason: Literal["ok", "rejected", "no_key", "provider_error", "exhausted"]
    model: Optional[str] = None
    # Which key backed the check, so the UI can say "trial token accepted by
    # Readyset" vs "Anthropic accepted it" instead of always crediting Anthropic.
    source: Optional[str] = None


@router.get("/env/requirements")
async def get_env_requirements(request: Request) -> EnvRequirementsResponse:
    require_local_request(request)

    service = EnvRequirementsService()
    requirements = await asyncio.to_thread(service.get_requirements)
    return EnvRequirementsResponse(
        keyring_available=service.secret_store.is_available(),
        telemetry_enabled=telemetry.is_enabled(),
        requirements=[EnvRequirement(**item) for item in requirements],
    )


@router.post("/env/set")
async def set_env_secret(request: Request, body: EnvSetRequest) -> EnvSetResponse:
    require_local_request(request)

    service = EnvRequirementsService()
    allowed = set(service.get_allowed_secret_names())
    if body.name not in allowed:
        return EnvSetResponse(
            success=False,
            name=body.name,
            persisted=False,
            session_only=True,
            message="This secret cannot be set here.",
        )

    service.bind_missing_target_password(body.name)
    result = service.secret_store.set_secret(
        name=body.name,
        value=body.value.get_secret_value(),
        persist=body.persist,
    )
    if body.name in ANTHROPIC_API_KEY_NAMES:
        service.set_ai_provider("claude")
        run_registry.wake_needs_key()

    return EnvSetResponse(
        success=True,
        name=body.name,
        persisted=bool(result.get("persisted", False)),
        session_only=bool(result.get("session_only", True)),
        message=result.get("message"),
    )


@router.post("/env/ai-provider")
async def set_ai_provider(
    request: Request, body: AiProviderSetRequest
) -> AiProviderSetResponse:
    require_local_request(request)

    service = EnvRequirementsService()
    try:
        await asyncio.to_thread(service.set_ai_provider, body.provider)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    run_registry.wake_needs_key()
    return AiProviderSetResponse(success=True, provider=body.provider)


@router.post("/env/anthropic/validate")
async def validate_anthropic_key(request: Request) -> AnthropicValidateResponse:
    """Report whether the configured Anthropic key actually authenticates.

    Presence is not validity — a stale or mistyped key still resolves. This
    pings the provider once (cheapest model, one token) so the UI can tell a
    "configured" key from a "working" one. Loopback + same-host guarded; the
    blocking provider call is offloaded off the event loop.
    """
    require_local_request(request)

    import asyncio

    from shared.anthropic_env import validate_anthropic_key as check_key

    result = await asyncio.to_thread(check_key)
    return AnthropicValidateResponse(**result)

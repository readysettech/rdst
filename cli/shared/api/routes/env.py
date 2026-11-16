"""API routes for secure environment variable handling."""

from __future__ import annotations

import asyncio
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, SecretStr

from shared.ai_data_consent import ai_data_sharing_allowed
from shared.anthropic_env import (
    ANTHROPIC_API_KEY_NAMES,
    MALFORMED_ANTHROPIC_KEY_MESSAGE,
    anthropic_key_rejected,
    check_anthropic_key,
    has_anthropic_api_key,
    is_well_formed_anthropic_key,
)
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

KEY_REJECTED_MESSAGE = (
    "Anthropic didn't accept this key, so it wasn't saved. "
    "Copy the key from the Anthropic Console and try again."
)
KEY_UNCHECKED_MESSAGE = (
    "RDST couldn't check this key with Anthropic, so it wasn't saved. "
    "Check your connection and try again."
)
KEY_SAVED_UNCHECKED_MESSAGE = (
    "Key saved, but RDST couldn't check it with Anthropic. "
    "Settings shows whether it works once Anthropic can be reached."
)
KEY_SAVED_AI_OFF_MESSAGE = "Key saved. RDST checks it with Anthropic once AI is on."


def _has_working_anthropic_key(service: EnvRequirementsService) -> bool:
    store = service.secret_store
    return has_anthropic_api_key(secret_store=store) and not anthropic_key_rejected(store)


class EnvRequirement(BaseModel):
    kind: EnvRequirementKind
    accepted_names: List[str]
    target: Optional[str] = None
    satisfied: bool
    source: EnvRequirementSource
    selected_provider: Optional[Literal["claude", "readyset"]] = None
    anthropic_key_configured: Optional[bool] = None
    readyset_account_connected: Optional[bool] = None
    # RDST was started with ANTHROPIC_API_KEY set; that key takes over again on restart.
    anthropic_key_in_startup_env: Optional[bool] = None


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
    value = body.value.get_secret_value()
    if body.name in ANTHROPIC_API_KEY_NAMES and not is_well_formed_anthropic_key(value):
        return EnvSetResponse(
            success=False,
            name=body.name,
            persisted=False,
            session_only=True,
            message=MALFORMED_ANTHROPIC_KEY_MESSAGE,
        )

    unchecked_message = None
    if body.name in ANTHROPIC_API_KEY_NAMES and not ai_data_sharing_allowed():
        # With AI off nothing goes to Anthropic, so the key is saved unchecked.
        unchecked_message = KEY_SAVED_AI_OFF_MESSAGE
    elif body.name in ANTHROPIC_API_KEY_NAMES:
        # A key replaces the current one only once Anthropic accepts it. A key Anthropic can't
        # be asked about is saved only when there is no working key to lose.
        verdict = await asyncio.to_thread(check_anthropic_key, value)
        if not verdict["valid"]:
            keeps_current = await asyncio.to_thread(_has_working_anthropic_key, service)
            if verdict["reason"] == "rejected" or keeps_current:
                message = (KEY_REJECTED_MESSAGE if verdict["reason"] == "rejected"
                           else KEY_UNCHECKED_MESSAGE)
                if keeps_current:
                    message += " Your current key is still in use."
                return EnvSetResponse(success=False, name=body.name, message=message)
            unchecked_message = KEY_SAVED_UNCHECKED_MESSAGE

    service.bind_missing_target_password(body.name)
    result = await asyncio.to_thread(
        service.secret_store.set_secret,
        name=body.name,
        value=value,
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
        message=unchecked_message or result.get("message"),
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

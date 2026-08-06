"""API routes for init workflow."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from features.init.events import (
    InitCompleteEvent,
    InitErrorEvent,
    InitEvent,
    InitLlmValidationEvent,
    InitStatusEvent,
    InitTargetValidationEvent,
)
from features.init.models import InitStatus, InitValidationResult
from features.init.service import InitService
from shared.api.guards import require_local_request

router = APIRouter()


class InitTargetInfo(BaseModel):
    name: str
    engine: str
    has_password: bool
    is_default: bool


class InitStatusResponse(BaseModel):
    initialized: bool
    targets: list[InitTargetInfo]
    default_target: str | None = None
    llm_configured: bool


class InitValidateRequest(BaseModel):
    targets: list[str] | None = None


class TargetValidationResult(BaseModel):
    name: str
    success: bool
    error: str | None = None
    version: str | None = None


class LlmValidationResult(BaseModel):
    success: bool
    error: str | None = None
    model: str | None = None


class InitValidateResponse(BaseModel):
    target_results: list[TargetValidationResult]
    llm_result: LlmValidationResult


class InitCompleteResponse(BaseModel):
    success: bool


def _status_to_response(status: InitStatus) -> InitStatusResponse:
    return InitStatusResponse(
        initialized=status.initialized,
        targets=status.targets,
        default_target=status.default_target,
        llm_configured=status.llm_configured,
    )


def _validation_to_response(result: InitValidationResult) -> InitValidateResponse:
    return InitValidateResponse(
        target_results=result.target_results,
        llm_result=result.llm_result,
    )


def _event_to_sse(event: InitEvent) -> dict:
    if isinstance(event, InitStatusEvent):
        return {"event": "status", "data": json.dumps({"message": event.message})}
    if isinstance(event, InitTargetValidationEvent):
        return {
            "event": "target_validation",
            "data": json.dumps(
                {
                    "name": event.name,
                    "success": event.success,
                    "error": event.error,
                }
            ),
        }
    if isinstance(event, InitLlmValidationEvent):
        return {"event": "llm_validation", "data": json.dumps(event.result)}
    if isinstance(event, InitCompleteEvent):
        payload = {"success": event.success}
        if event.validation is not None:
            payload["validation"] = {
                "target_results": event.validation.target_results,
                "llm_result": event.validation.llm_result,
            }
        if event.status is not None:
            payload["status"] = {
                "initialized": event.status.initialized,
                "targets": event.status.targets,
                "default_target": event.status.default_target,
                "llm_configured": event.status.llm_configured,
            }
        return {"event": "complete", "data": json.dumps(payload)}
    if isinstance(event, InitErrorEvent):
        return {"event": "error", "data": json.dumps({"message": event.message})}
    return {
        "event": "unknown",
        "data": json.dumps({"message": f"Unknown event type: {type(event)}"}),
    }


@router.get("/init/status")
async def get_init_status() -> InitStatusResponse:
    service = InitService()
    status = None
    async for event in service.get_status_events():
        if isinstance(event, InitCompleteEvent) and event.status is not None:
            status = event.status
        elif isinstance(event, InitErrorEvent):
            raise RuntimeError(event.message)
    if status is None:
        raise RuntimeError("No status returned")
    return _status_to_response(status)


@router.post("/init/validate")
async def validate_init(
    http_request: Request, request: InitValidateRequest
) -> InitValidateResponse:
    require_local_request(http_request)

    service = InitService()
    validation = None
    async for event in service.validate_all_events(request.targets):
        if isinstance(event, InitCompleteEvent) and event.validation is not None:
            validation = event.validation
        elif isinstance(event, InitErrorEvent):
            raise RuntimeError(event.message)
    if validation is None:
        raise RuntimeError("No validation result returned")
    return _validation_to_response(validation)


@router.post("/init/complete")
async def complete_init(http_request: Request) -> InitCompleteResponse:
    require_local_request(http_request)

    service = InitService()
    success = False
    async for event in service.mark_complete_events():
        if isinstance(event, InitCompleteEvent):
            success = bool(event.success)
        elif isinstance(event, InitErrorEvent):
            success = False
    if success:
        try:
            from shared.telemetry import telemetry

            telemetry.track("init_complete", {"source": "web"})
        except Exception:
            pass
    return InitCompleteResponse(success=success)


@router.post("/init/validate/stream")
async def validate_init_stream(http_request: Request, request: InitValidateRequest):
    require_local_request(http_request)

    service = InitService()

    async def _generator():
        async for event in service.validate_all_events(request.targets):
            yield _event_to_sse(event)

    return EventSourceResponse(_generator())

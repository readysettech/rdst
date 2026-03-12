"""API routes for init workflow."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ...services.init_service import InitService
from ...services.types import (
    InitCompleteEvent,
    InitErrorEvent,
    InitEvent,
    InitLlmValidationEvent,
    InitStatus,
    InitStatusEvent,
    InitTargetValidationEvent,
    InitValidationResult,
)

router = APIRouter()


class InitStatusResponse(BaseModel):
    initialized: bool
    targets: List[Dict[str, Any]]
    default_target: Optional[str] = None
    llm_configured: bool


class InitValidateRequest(BaseModel):
    targets: Optional[List[str]] = None


class InitValidateResponse(BaseModel):
    target_results: List[Dict[str, Any]]
    llm_result: Dict[str, Any]


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
    import json

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
        return {
            "event": "llm_validation",
            "data": json.dumps(event.result),
        }
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
    """Return init completion status and current targets."""
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
async def validate_init(request: InitValidateRequest) -> InitValidateResponse:
    """Validate targets and LLM connectivity."""
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
async def complete_init() -> InitCompleteResponse:
    """Mark init as completed."""
    service = InitService()
    success = False
    async for event in service.mark_complete_events():
        if isinstance(event, InitCompleteEvent):
            success = bool(event.success)
        elif isinstance(event, InitErrorEvent):
            success = False

    if success:
        try:
            from lib.telemetry import telemetry
            telemetry.track("init_complete", {"source": "web"})
        except Exception:
            pass

    return InitCompleteResponse(success=success)


@router.post("/init/validate/stream")
async def validate_init_stream(request: InitValidateRequest):
    """Validate init targets with SSE streaming events."""
    service = InitService()

    async def _generator():
        async for event in service.validate_all_events(request.targets):
            yield _event_to_sse(event)

    return EventSourceResponse(_generator())

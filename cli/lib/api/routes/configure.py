"""API routes for database target configuration."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Union

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ...services.configure_service import ConfigureService
from ...services.types import (
    ConfigureEvent,
    ConfigureInput,
    ConfigureOptions,
    ConfigureStatusEvent,
    ConfigureTargetListEvent,
    ConfigureTargetDetailEvent,
    ConfigureConnectionTestEvent,
    ConfigureSuccessEvent,
    ConfigureErrorEvent,
)

router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================


class TargetData(BaseModel):
    """Target connection data for add/update operations."""

    engine: str = "postgresql"
    host: str
    port: int = 5432
    database: str
    user: str
    password_env: Optional[str] = None
    tls: bool = False
    read_only: bool = False


class AddTargetRequest(BaseModel):
    """Request body for adding a target."""

    name: str
    target: TargetData


class UpdateTargetRequest(BaseModel):
    """Request body for updating a target."""

    target: TargetData


class SetDefaultRequest(BaseModel):
    """Request body for setting default target."""

    name: str


class TargetResponse(BaseModel):
    """Response for single target operations."""

    success: bool
    message: Optional[str] = None
    target_name: Optional[str] = None


class TargetListResponse(BaseModel):
    """Response for list targets."""

    targets: list[Dict[str, Any]]
    default_target: Optional[str] = None


class TargetDetailResponse(BaseModel):
    """Response for get target details."""

    target_name: str
    engine: str
    host: str
    port: int
    database: str
    user: str
    has_password: bool
    is_default: bool
    tls: bool = False
    read_only: bool = False


class ErrorResponse(BaseModel):
    """Error response."""

    success: bool = False
    message: str


# ============================================================================
# SSE Event Conversion
# ============================================================================


def _event_to_sse(event: ConfigureEvent) -> dict:
    """Convert ConfigureEvent to SSE format."""
    if isinstance(event, ConfigureStatusEvent):
        return {
            "event": "status",
            "data": json.dumps({"message": event.message}),
        }
    elif isinstance(event, ConfigureTargetListEvent):
        return {
            "event": "target_list",
            "data": json.dumps(
                {
                    "targets": event.targets,
                    "default_target": event.default_target,
                }
            ),
        }
    elif isinstance(event, ConfigureTargetDetailEvent):
        return {
            "event": "target_detail",
            "data": json.dumps(
                {
                    "target_name": event.target_name,
                    "engine": event.engine,
                    "host": event.host,
                    "port": event.port,
                    "database": event.database,
                    "user": event.user,
                    "has_password": event.has_password,
                    "is_default": event.is_default,
                    "tls": event.tls,
                    "read_only": event.read_only,
                }
            ),
        }
    elif isinstance(event, ConfigureConnectionTestEvent):
        data = {
            "target_name": event.target_name,
            "status": event.status,
        }
        if event.message:
            data["message"] = event.message
        if event.server_version:
            data["server_version"] = event.server_version
        return {
            "event": "connection_test",
            "data": json.dumps(data),
        }
    elif isinstance(event, ConfigureSuccessEvent):
        data = {
            "operation": event.operation,
        }
        if event.target_name:
            data["target_name"] = event.target_name
        if event.message:
            data["message"] = event.message
        return {
            "event": "success",
            "data": json.dumps(data),
        }
    elif isinstance(event, ConfigureErrorEvent):
        data = {"message": event.message}
        if event.operation:
            data["operation"] = event.operation
        if event.target_name:
            data["target_name"] = event.target_name
        return {
            "event": "error",
            "data": json.dumps(data),
        }
    else:
        return {
            "event": "error",
            "data": json.dumps({"message": f"Unknown event type: {type(event)}"}),
        }


# ============================================================================
# Endpoints
# ============================================================================


@router.get("/configure/targets")
async def list_targets() -> Union[TargetListResponse, ErrorResponse]:
    """List all configured database targets."""
    service = ConfigureService()
    input_data = ConfigureInput()
    options = ConfigureOptions()

    result = None
    error = None

    async for event in service.list_targets(input_data, options):
        if isinstance(event, ConfigureTargetListEvent):
            result = event
        elif isinstance(event, ConfigureErrorEvent):
            error = event

    if result:
        return TargetListResponse(
            targets=result.targets,
            default_target=result.default_target,
        )
    elif error:
        return ErrorResponse(message=error.message)
    else:
        return ErrorResponse(message="No response from service")


@router.get("/configure/targets/{name}")
async def get_target(name: str) -> Union[TargetDetailResponse, ErrorResponse]:
    """Get details of a specific target."""
    service = ConfigureService()

    result = None
    error = None

    async for event in service.get_target(name):
        if isinstance(event, ConfigureTargetDetailEvent):
            result = event
        elif isinstance(event, ConfigureErrorEvent):
            error = event

    if result:
        return TargetDetailResponse(
            target_name=result.target_name,
            engine=result.engine,
            host=result.host,
            port=result.port,
            database=result.database,
            user=result.user,
            has_password=result.has_password,
            is_default=result.is_default,
            tls=result.tls,
            read_only=result.read_only,
        )
    elif error:
        return ErrorResponse(message=error.message)
    else:
        return ErrorResponse(message="No response from service")


@router.post("/configure/targets")
async def add_target(request: AddTargetRequest) -> Union[TargetResponse, ErrorResponse]:
    """Add a new database target."""
    service = ConfigureService()
    input_data = ConfigureInput(target_name=request.name)
    options = ConfigureOptions(
        target_data={
            "engine": request.target.engine,
            "host": request.target.host,
            "port": request.target.port,
            "database": request.target.database,
            "user": request.target.user,
            "password_env": request.target.password_env,
            "tls": request.target.tls,
            "read_only": request.target.read_only,
        }
    )

    result = None
    error = None

    async for event in service.add_target(input_data, options):
        if isinstance(event, ConfigureSuccessEvent):
            result = event
        elif isinstance(event, ConfigureErrorEvent):
            error = event

    if result:
        try:
            from lib.telemetry import telemetry
            telemetry.track("configure_target", {
                "source": "web",
                "operation": "add",
                "engine": request.target.engine,
            })
        except Exception:
            pass
        return TargetResponse(
            success=True,
            message=result.message,
            target_name=result.target_name,
        )
    elif error:
        return ErrorResponse(message=error.message)
    else:
        return ErrorResponse(message="No response from service")


@router.put("/configure/targets/{name}")
async def update_target(
    name: str, request: UpdateTargetRequest
) -> Union[TargetResponse, ErrorResponse]:
    """Update an existing database target."""
    service = ConfigureService()
    input_data = ConfigureInput(target_name=name)
    options = ConfigureOptions(
        target_data={
            "engine": request.target.engine,
            "host": request.target.host,
            "port": request.target.port,
            "database": request.target.database,
            "user": request.target.user,
            "password_env": request.target.password_env,
            "tls": request.target.tls,
            "read_only": request.target.read_only,
        }
    )

    result = None
    error = None

    async for event in service.update_target(name, input_data, options):
        if isinstance(event, ConfigureSuccessEvent):
            result = event
        elif isinstance(event, ConfigureErrorEvent):
            error = event

    if result:
        return TargetResponse(
            success=True,
            message=result.message,
            target_name=result.target_name,
        )
    elif error:
        return ErrorResponse(message=error.message)
    else:
        return ErrorResponse(message="No response from service")


@router.delete("/configure/targets/{name}")
async def remove_target(name: str) -> Union[TargetResponse, ErrorResponse]:
    """Remove a database target."""
    service = ConfigureService()

    result = None
    error = None

    async for event in service.remove_target(name):
        if isinstance(event, ConfigureSuccessEvent):
            result = event
        elif isinstance(event, ConfigureErrorEvent):
            error = event

    if result:
        return TargetResponse(
            success=True,
            message=result.message,
            target_name=result.target_name,
        )
    elif error:
        return ErrorResponse(message=error.message)
    else:
        return ErrorResponse(message="No response from service")


@router.put("/configure/default")
async def set_default(request: SetDefaultRequest) -> Union[TargetResponse, ErrorResponse]:
    """Set a target as the default."""
    service = ConfigureService()

    result = None
    error = None

    async for event in service.set_default(request.name):
        if isinstance(event, ConfigureSuccessEvent):
            result = event
        elif isinstance(event, ConfigureErrorEvent):
            error = event

    if result:
        return TargetResponse(
            success=True,
            message=result.message,
            target_name=result.target_name,
        )
    elif error:
        return ErrorResponse(message=error.message)
    else:
        return ErrorResponse(message="No response from service")


@router.post("/configure/targets/{name}/test")
async def test_connection(name: str):
    """Test connection to a target (SSE stream)."""

    async def _test_generator():
        service = ConfigureService()
        async for event in service.test_connection(name):
            yield _event_to_sse(event)

    return EventSourceResponse(_test_generator())

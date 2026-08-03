"""API routes for database target configuration."""

from __future__ import annotations

import json
from typing import Optional, Union

from fastapi import APIRouter, Body
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from sse_starlette.sse import EventSourceResponse

from features.configure.events import (
    ConfigureEvent,
    ConfigureConnectionTestEvent,
    ConfigureSuccessEvent,
    ConfigureErrorEvent,
    ConfigureStatusEvent,
    ConfigureTargetDetailEvent,
    ConfigureTargetListEvent,
)
from features.configure.models import ConfigureInput, ConfigureOptions
from features.configure.service import ConfigureService
from shared.config.targets import TargetsConfig
from shared.password_resolver import derive_password_env
from shared.secret_store_service import SecretStoreService

router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================


class SshData(BaseModel):
    """SSH jump-host configuration stored as pointers only."""

    model_config = ConfigDict(extra="forbid")

    host: Optional[str] = Field(default=None, min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    user: Optional[str] = None
    key_path: Optional[str] = None
    profile: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def inline_or_profile(self):
        if bool(self.host) == bool(self.profile):
            raise ValueError("Specify exactly one of SSH host or profile")
        if self.profile and (
            self.port != 22 or self.user is not None or self.key_path is not None
        ):
            raise ValueError("An SSH profile cannot be combined with inline fields")
        return self


class TargetData(BaseModel):
    """Target connection data for add/update operations."""

    model_config = ConfigDict(extra="ignore")

    engine: str = "postgresql"
    host: str
    port: int = 5432
    database: str
    user: str
    password_env: Optional[str] = None
    password: Optional[SecretStr] = None
    tls: bool = False
    tls_verify: bool = False
    tls_ca: Optional[str] = None
    read_only: bool = False
    ssh: Optional[SshData] = None

    @field_validator("ssh", mode="before")
    @classmethod
    def empty_ssh_removes_section(cls, value):
        return None if value == {} else value


class AddTargetRequest(BaseModel):
    """Request body for adding a target."""

    name: str
    target: TargetData


class UpdateTargetRequest(BaseModel):
    """Request body for updating a target."""

    target: TargetData

    @model_validator(mode="before")
    @classmethod
    def accept_direct_target_payload(cls, value):
        """Accept either {target: {...}} or a round-tripped target object."""
        if isinstance(value, dict) and "target" not in value:
            return {"target": value}
        return value


class FormTestTargetData(TargetData):
    """Unsaved form values used only for a connection test."""


class FormConnectionTestRequest(BaseModel):
    target: FormTestTargetData


class SetDefaultRequest(BaseModel):
    """Request body for setting default target."""

    name: str


class TargetResponse(BaseModel):
    """Response for single target operations."""

    success: bool
    message: Optional[str] = None
    target_name: Optional[str] = None


class TargetSummaryResponse(BaseModel):
    """Summary fields for a target in the list view."""

    name: str
    engine: Optional[str] = None
    host: Optional[str] = None
    port: Optional[Union[int, str]] = None
    database: Optional[str] = None
    proxy: Optional[str] = None
    endpoint_verified: Optional[bool] = None
    verified: Optional[bool] = None
    has_password: bool = False
    is_default: bool = False
    ssh: Optional[SshData] = None
    publicly_accessible: Optional[bool] = None


class TargetListResponse(BaseModel):
    """Response for list targets."""

    targets: list[TargetSummaryResponse]
    default_target: Optional[str] = None


class TargetDetailResponse(BaseModel):
    """Response for get target details."""

    target_name: str
    engine: str
    host: str
    port: int
    database: str
    user: str
    password_env: Optional[str] = None
    has_password: bool
    is_default: bool
    tls: bool = False
    tls_verify: bool = False
    tls_ca: Optional[str] = None
    read_only: bool = False
    ssh: Optional[SshData] = None
    publicly_accessible: Optional[bool] = None
    tags: list[str] = Field(default_factory=list)
    region: Optional[str] = None
    instance_class: Optional[str] = None
    group: Optional[str] = None


class ErrorResponse(BaseModel):
    """Error response."""

    success: bool = False
    message: str


def _ssh_target_data(ssh: SshData) -> dict:
    """Store profile references without inline-field defaults."""
    if ssh.profile:
        return {"profile": ssh.profile}
    return ssh.model_dump(exclude_none=True)


def _password_env_for_request(
    target_name: str,
    target: TargetData,
    existing: Optional[dict] = None,
) -> Optional[str]:
    """Keep legacy pointers, but derive new ones without exposing that detail."""
    if target.password_env:
        return target.password_env
    if existing and existing.get("password_env"):
        return str(existing["password_env"])
    if target.password is not None:
        return derive_password_env(target_name)
    return None


def _store_submitted_password(
    target: TargetData, password_env: Optional[str]
) -> None:
    if target.password is None or not password_env:
        return
    SecretStoreService().set_secret(
        password_env,
        target.password.get_secret_value(),
        persist=True,
    )


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
                    "password_env": event.password_env,
                    "has_password": event.has_password,
                    "is_default": event.is_default,
                    "tls": event.tls,
                    "tls_verify": event.tls_verify,
                    "tls_ca": event.tls_ca,
                    "read_only": event.read_only,
                    "ssh": event.ssh,
                    "publicly_accessible": event.publicly_accessible,
                    "tags": event.tags,
                    "region": event.region,
                    "instance_class": event.instance_class,
                    "group": event.group,
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
        if event.code:
            data["code"] = event.code
        if event.category:
            data["category"] = event.category
        if event.password_env:
            data["password_env"] = event.password_env
        if event.privileges is not None:
            data["privileges"] = event.privileges
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
            password_env=result.password_env,
            has_password=result.has_password,
            is_default=result.is_default,
            tls=result.tls,
            tls_verify=result.tls_verify,
            tls_ca=result.tls_ca,
            read_only=result.read_only,
            ssh=result.ssh,
            publicly_accessible=result.publicly_accessible,
            tags=result.tags or [],
            region=result.region,
            instance_class=result.instance_class,
            group=result.group,
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
    password_env = _password_env_for_request(request.name, request.target)
    target_data = {
        "engine": request.target.engine,
        "host": request.target.host,
        "port": request.target.port,
        "database": request.target.database,
        "user": request.target.user,
        "password_env": password_env,
        "tls": request.target.tls,
        "tls_verify": request.target.tls_verify,
        "tls_ca": request.target.tls_ca,
        "read_only": request.target.read_only,
    }
    if request.target.ssh is not None:
        target_data["ssh"] = _ssh_target_data(request.target.ssh)
    _store_submitted_password(request.target, password_env)
    options = ConfigureOptions(target_data=target_data)

    result = None
    error = None

    async for event in service.add_target(input_data, options):
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


@router.put("/configure/targets/{name}")
async def update_target(
    name: str, request: UpdateTargetRequest
) -> Union[TargetResponse, ErrorResponse]:
    """Update an existing database target."""
    service = ConfigureService()
    cfg = TargetsConfig()
    cfg.load()
    existing = cfg.get(name) or {}
    password_env = _password_env_for_request(name, request.target, existing)
    _store_submitted_password(request.target, password_env)
    input_data = ConfigureInput(target_name=name)
    target_data = {
        "engine": request.target.engine,
        "host": request.target.host,
        "port": request.target.port,
        "database": request.target.database,
        "user": request.target.user,
        "password_env": password_env,
        "tls": request.target.tls,
        "tls_verify": request.target.tls_verify,
        "tls_ca": request.target.tls_ca,
        "read_only": request.target.read_only,
        "ssh": (
            _ssh_target_data(request.target.ssh)
            if request.target.ssh is not None
            else None
        ),
    }
    if request.target.password is not None:
        # Remove a legacy hand-edited inline password when it is explicitly
        # replaced. New password values are never written to config.toml.
        target_data["password"] = None
    options = ConfigureOptions(target_data=target_data)

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
async def test_connection(
    name: str,
    request: Optional[FormConnectionTestRequest] = Body(default=None),
):
    """Test a saved target or the current unsaved form values (SSE stream)."""

    async def _test_generator():
        service = ConfigureService()
        target_data = None
        if request is not None:
            cfg = TargetsConfig()
            cfg.load()
            existing = cfg.get(name) or {}
            target_data = {
                **existing,
                **request.target.model_dump(exclude_none=True),
            }
            password = request.target.password
            if password is not None:
                target_data["password"] = password.get_secret_value()
            target_data["name"] = name

        async for event in service.test_connection(name, target_data):
            yield _event_to_sse(event)

    return EventSourceResponse(_test_generator())

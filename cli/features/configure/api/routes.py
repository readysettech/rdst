"""API routes for database target configuration."""

from __future__ import annotations

import json
import re
from typing import Optional, Union

from fastapi import APIRouter, Body, HTTPException, Request
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
from shared import product_telemetry
from shared.api.guards import require_local_request
from shared.config.targets import TargetsConfig, validate_target_name
from shared.password_resolver import (
    bind_target_credentials,
    check_password_env,
    credential_endpoint,
    resolve_password,
    new_password_env,
    store_target_password,
)
from shared.secret_store_service import SecretStoreService

router = APIRouter()

PASSWORD_FOR_NEW_ADDRESS = (
    "Enter the password again: RDST sends a saved password to a new address only when you type "
    "it."
)


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

    @field_validator("password_env")
    @classmethod
    def database_password_pointer(cls, value):
        check_password_env(value)
        return value


class AddTargetRequest(BaseModel):
    """Request body for adding a target."""

    name: str
    target: TargetData


class UpdateTargetRequest(BaseModel):
    """Request body for updating a target."""

    name: Optional[str] = Field(default=None, description="A new name; omitted keeps it.")
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


class AddTargetResponse(TargetResponse):
    """A new target; ``persisted`` is False when the OS keyring couldn't store the submitted
    password, so it lasts only for this session, and None when no password was submitted."""

    persisted: Optional[bool] = None


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
    cfg: TargetsConfig,
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
        return new_password_env(target_name, cfg)
    return None


def _connection_identity(target: dict) -> tuple:
    """What makes two saved connections the same database: where its password is sent, and
    the database."""
    return (*credential_endpoint(target), str(target.get("database") or "").strip())


def _saved_as(cfg: TargetsConfig, target: dict) -> Optional[str]:
    """The name an identical connection is already saved under, if any."""
    wanted = _connection_identity(target)
    return next(
        (name for name in cfg.list_targets() if _connection_identity(cfg.get(name) or {}) == wanted),
        None,
    )


def _bind_credentials(cfg: TargetsConfig, name: str, proposed: dict,
                      existing: Optional[dict], password_submitted: bool) -> dict:
    """``proposed`` with only the credentials saved for its server; 422 for a pointer the caller
    may not use."""
    saved = {other: cfg.get(other) or {} for other in cfg.list_targets()}
    try:
        return bind_target_credentials(name, proposed, existing, saved,
                                       password_submitted=password_submitted)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _name_taken(cfg: TargetsConfig, name: str, keep: Optional[str] = None) -> bool:
    return any(
        existing != keep and existing.casefold() == name.casefold()
        for existing in cfg.list_targets()
    )


class _RecordingSecretStore(SecretStoreService):
    """Keeps whether the last secret written reached the OS keyring."""

    persisted: Optional[bool] = None

    def set_secret(self, name, value, *args, **kwargs):
        result = super().set_secret(name, value, *args, **kwargs)
        self.persisted = bool(result.get("persisted")) if isinstance(result, dict) else None
        return result


def _store_submitted_password(
    target: TargetData, password_env: Optional[str]
) -> Optional[bool]:
    """Store a submitted password; whether the keyring kept it, or None with no password."""
    if target.password is None:
        return None
    store = _RecordingSecretStore()
    store_target_password(
        "target",
        target.password.get_secret_value(),
        password_env,
        secret_store=store,
    )
    return store.persisted


def _via(http_request: Request) -> Optional[str]:
    return product_telemetry.normalize_via(http_request.headers.get("X-RDST-Via"))


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
        if event.query_capture is not None:
            data["query_capture"] = event.query_capture
        if event.tls_warning:
            data["tls_warning"] = event.tls_warning
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
async def list_targets(http_request: Request) -> Union[TargetListResponse, ErrorResponse]:
    """List all configured database targets."""
    require_local_request(http_request)
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
async def get_target(
    http_request: Request, name: str
) -> Union[TargetDetailResponse, ErrorResponse]:
    """Get details of a specific target."""
    require_local_request(http_request)
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
async def add_target(
    http_request: Request, request: AddTargetRequest
) -> Union[AddTargetResponse, ErrorResponse]:
    """Add a new database target."""
    require_local_request(http_request)
    # Checked before the password is stored, so a duplicate never overwrites the existing
    # target's saved password.
    existing = TargetsConfig()
    existing.load()
    is_first_database = not existing.list_targets()
    if _name_taken(existing, request.name):
        raise HTTPException(409, f"A database named {request.name} already exists.")
    saved_as = _saved_as(existing, request.target.model_dump())
    if saved_as is not None:
        raise HTTPException(409, f"Already added as {saved_as}.")
    service = ConfigureService()
    input_data = ConfigureInput(target_name=request.name)
    password_env = _password_env_for_request(existing, request.name, request.target)
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
    _bind_credentials(existing, request.name, target_data, None,
                      request.target.password is not None)
    persisted = _store_submitted_password(request.target, password_env)
    options = ConfigureOptions(target_data=target_data)

    result = None
    error = None

    async for event in service.add_target(input_data, options):
        if isinstance(event, ConfigureSuccessEvent):
            result = event
        elif isinstance(event, ConfigureErrorEvent):
            error = event

    via = _via(http_request)
    if result:
        product_telemetry.database_add_saved(request.target.engine, via=via,
                                             is_first_database=is_first_database)
        return AddTargetResponse(
            success=True,
            message=result.message,
            target_name=result.target_name,
            persisted=persisted,
        )
    product_telemetry.database_add_failed(
        request.target.engine, via=via,
        error_class=product_telemetry.connection_error_class(error.message if error else ""))
    if error:
        return ErrorResponse(message=error.message)
    else:
        return ErrorResponse(message="No response from service")


@router.put("/configure/targets/{name}")
async def update_target(
    http_request: Request, name: str, request: UpdateTargetRequest
) -> Union[TargetResponse, ErrorResponse]:
    """Update an existing database target."""
    require_local_request(http_request)
    service = ConfigureService()
    cfg = TargetsConfig()
    cfg.load()
    existing = cfg.get(name) or {}
    new_name = (request.name or "").strip() or name
    if new_name != name:
        try:
            validate_target_name(new_name)
        except ValueError:
            raise HTTPException(422, "Use up to 64 letters, numbers, dots, underscores or "
                                     "hyphens, starting with a letter or number.")
        if _name_taken(cfg, new_name, keep=name):
            raise HTTPException(409, f"A database named {new_name} already exists.")
        blocker = service.rename_blocker(name)
        if blocker:
            raise HTTPException(409, blocker)
    password_env = _password_env_for_request(cfg, name, request.target, existing)
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
    if existing:
        proposed = {**existing, **target_data}
        bound = _bind_credentials(cfg, name, proposed, existing,
                                  request.target.password is not None)
        for field in ("password", "password_env", "password_secret_arn", "password_secret_key"):
            if field in proposed and field not in bound:
                target_data[field] = None
        password_env = target_data["password_env"]
    _store_submitted_password(request.target, password_env)
    options = ConfigureOptions(target_data=target_data)

    result = None
    error = None

    async for event in service.update_target(name, input_data, options):
        if isinstance(event, ConfigureSuccessEvent):
            result = event
        elif isinstance(event, ConfigureErrorEvent):
            error = event

    if result:
        from features.monitor.loop import connection_changed

        connection_changed(name, getattr(http_request.app.state, "monitor_scheduler", None))
    if result and new_name != name:
        try:
            await service.rename_target(name, new_name)
        except Exception as exc:
            return ErrorResponse(message=f"Saved the connection, but couldn't rename it: {exc}")
    if result:
        return TargetResponse(
            success=True,
            message=result.message,
            target_name=new_name,
        )
    elif error:
        return ErrorResponse(message=error.message)
    else:
        return ErrorResponse(message="No response from service")


@router.delete("/configure/targets/{name}")
async def remove_target(
    http_request: Request, name: str
) -> Union[TargetResponse, ErrorResponse]:
    """Remove a database target."""
    require_local_request(http_request)
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
async def set_default(
    http_request: Request, request: SetDefaultRequest
) -> Union[TargetResponse, ErrorResponse]:
    """Set a target as the default."""
    require_local_request(http_request)
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
    http_request: Request,
    name: str,
    request: Optional[FormConnectionTestRequest] = Body(default=None),
):
    """Test a saved target or the current unsaved form values (SSE stream)."""
    require_local_request(http_request)
    via = _via(http_request)
    target_data = None
    refusal = None
    if request is not None:
        cfg = TargetsConfig()
        cfg.load()
        existing = cfg.get(name)
        password = request.target.password
        target_data = _bind_credentials(
            cfg,
            name,
            {**(existing or {}), **request.target.model_dump(exclude_none=True)},
            existing,
            password is not None,
        )
        # A destination that can't have the saved password says why, instead of failing to
        # sign in.
        if (password is None and existing
                and credential_endpoint(existing) != credential_endpoint(target_data)
                and resolve_password(existing).available
                and not resolve_password(target_data).available):
            refusal = ConfigureConnectionTestEvent(
                type="connection_test",
                target_name=name,
                status="failed",
                code="TARGET_PASSWORD_REQUIRED",
                category="target_password_required",
                message=PASSWORD_FOR_NEW_ADDRESS,
            )
        if password is not None:
            target_data["password"] = password.get_secret_value()
        target_data["name"] = name

    async def _test_generator():
        if refusal is not None:
            _report_test(name, target_data, via, refusal)
            yield _event_to_sse(refusal)
            return
        service = ConfigureService()
        async for event in service.test_connection(name, target_data):
            if isinstance(event, ConfigureConnectionTestEvent) and event.status != "in_progress":
                _report_test(name, target_data, via, event)
            yield _event_to_sse(event)

    return EventSourceResponse(_test_generator())


def _report_test(name: str, target_data: Optional[dict], via: Optional[str],
                 event: ConfigureConnectionTestEvent) -> None:
    """Send the connection test's outcome: engine, path and error class, never names."""
    if not product_telemetry.enabled():
        return
    engine = (target_data or {}).get("engine")
    if engine is None:
        engine = product_telemetry.engine_of(name)
    ok = event.status == "success"
    product_telemetry.database_add_test_completed(
        engine, via=via, ok=ok,
        error_class=None if ok else product_telemetry.connection_error_class(
            event.code, event.category, event.message))


@router.get("/configure/targets/{name}/prerequisites")
async def get_prerequisites(http_request: Request, name: str) -> dict:
    """Check that RDST can see the target's queries (monitoring prerequisites).

    Safe to poll every few seconds: each call opens two short sessions and runs
    read-only catalog queries plus one tagged probe statement.
    """
    import asyncio

    from features.configure.prerequisites import check_target
    from shared.api.target_guard import ensure_target_password

    require_local_request(http_request)
    guard = ensure_target_password(name)
    result = await asyncio.to_thread(check_target, guard.target_name, guard.target_config)
    via = http_request.headers.get("x-rdst-via")
    for check in result.get("checks") or []:
        product_telemetry.database_add_prereq_result(
            result.get("engine"), check=str(check.get("id")), ok=check.get("status") == "ok",
            via=via, scope=guard.target_name)
    if result.get("ready"):
        # A fix applied while monitoring runs takes effect without a restart.
        from features.monitor.loop import setup_check_passed

        await setup_check_passed(guard.target_name,
                                 getattr(http_request.app.state, "monitor_scheduler", None))
    return result


class SetupStatementRequest(BaseModel):
    statement: str = Field(min_length=1)


@router.post("/configure/targets/{name}/prerequisites/run")
async def run_prerequisite_statement(http_request: Request, name: str,
                                     body: SetupStatementRequest) -> dict:
    """Run the CREATE EXTENSION the prerequisite check lists for a Neon database, then check
    again. Neon already loads the library, so the extension works at once with no restart;
    anywhere else the user's agent makes the change, and RDST runs nothing."""
    from features.agent_connect import tools
    from features.configure.prerequisites import _host_provider
    from shared.api.target_guard import ensure_target_password

    require_local_request(http_request)
    guard = ensure_target_password(name)
    if _host_provider(str(guard.target_config.get("host") or "")) != "neon" or \
            not re.match(r"(?is)^\s*create\s+extension\b", body.statement):
        raise HTTPException(400, "RDST runs only a listed CREATE EXTENSION on a Neon "
                                 "database. Use Fix with Claude for this one.")
    try:
        return await tools.run_listed_statement(guard.target_name, body.statement.strip(),
                                                via="app", announce=False, lister="the check")
    except tools.StatementRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    except tools.ToolError as exc:
        raise HTTPException(400, str(exc)) from exc

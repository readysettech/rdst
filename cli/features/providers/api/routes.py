"""Providers API routes.

Cloud-provider sign-in (AWS, Supabase, Neon, DigitalOcean) and database
discovery for the web Add-Target flow. `rdst web` runs on the user's
machine, so sign-in uses the local credential chain and browser OAuth
callbacks land back on this server.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict
from html import escape
from typing import Any, AsyncGenerator, Optional
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, SecretStr

from sse_starlette.sse import EventSourceResponse

from shared.api.guards import require_local_request

from ..service import ACCOUNT_PROVIDERS, ProvidersService

router = APIRouter(prefix="/providers", tags=["providers"])

# Providers with a browser sign-in: the module holding their OAuth entry
# points and the name used in user-facing messages. The path prefix and the
# error code both use the key.
OAUTH_PROVIDERS = {
    "supabase": ("supabase_oauth", "Supabase"),
    "digitalocean": ("digitalocean_oauth", "DigitalOcean"),
}


class AwsLoginRequest(BaseModel):
    profile: str


class AwsProfileCreateRequest(BaseModel):
    name: str
    sso_start_url: str
    sso_region: str
    sso_account_id: str
    sso_role_name: str
    region: str


class AwsSsoLoginRequest(BaseModel):
    start_url: str
    region: str


class AwsSsoFinalizeRequest(BaseModel):
    name: str
    start_url: str
    region: str
    account_id: str
    role_name: str


def _event_to_sse(event: Any) -> dict:
    return {"event": event.type, "data": json.dumps(asdict(event), default=str)}


class FleetDiscoverRequest(BaseModel):
    regions: list[str]
    engine_filter: Optional[str] = None
    name_pattern: Optional[str] = None
    password_env: Optional[str] = None
    password: Optional[SecretStr] = None
    user: Optional[str] = None
    default_database: Optional[str] = None
    group: Optional[str] = None
    dry_run: bool = False
    # Named AWS profile to discover with; null = default credential chain.
    profile: Optional[str] = None


@router.get("/aws-status")
async def get_fleet_aws_status(
    http_request: Request, profile: Optional[str] = None
) -> dict[str, Any]:
    """Report local AWS credential state for the discovery UI."""
    require_local_request(http_request)

    from ..auth import get_aws_status

    return await asyncio.to_thread(get_aws_status, 3.0, profile)


@router.post("/aws-login")
async def start_fleet_aws_login(http_request: Request, request: AwsLoginRequest):
    """Start AWS CLI SSO login without blocking the local web request."""
    require_local_request(http_request)

    from ..aws_login import (
        AwsCliMissing,
        AwsCliSpawnError,
        AwsSdkUnavailable,
        UnknownAwsProfile,
        fallback_command,
        start_login,
    )

    try:
        return await asyncio.to_thread(start_login, request.profile)
    except UnknownAwsProfile as exc:
        return JSONResponse(
            status_code=400,
            content={"code": "unknown_profile", "detail": str(exc)},
        )
    except AwsSdkUnavailable as exc:
        return JSONResponse(
            status_code=503,
            content={"code": "aws_sdk_missing", "detail": str(exc)},
        )
    except AwsCliMissing as exc:
        return JSONResponse(
            status_code=409,
            content={
                "code": "aws_cli_missing",
                "detail": str(exc),
                "fallback_command": fallback_command(request.profile),
            },
        )
    except AwsCliSpawnError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "code": "aws_cli_spawn_failed",
                "detail": str(exc),
                "fallback_command": fallback_command(request.profile),
            },
        )


@router.post("/aws-logout")
async def fleet_aws_logout(http_request: Request):
    """Sign out of AWS SSO (clears the CLI's cached SSO sessions)."""
    require_local_request(http_request)

    from ..aws_login import AwsCliMissing, AwsCliSpawnError, logout

    try:
        return await asyncio.to_thread(logout)
    except AwsCliMissing as exc:
        return JSONResponse(status_code=409, content={"code": "aws_cli_missing", "detail": str(exc)})
    except AwsCliSpawnError as exc:
        return JSONResponse(status_code=409, content={"code": "aws_logout_failed", "detail": str(exc)})


@router.get("/aws-login/{login_id}")
async def get_fleet_aws_login(http_request: Request, login_id: str):
    """Poll an AWS CLI login and verify the resulting STS session."""
    require_local_request(http_request)

    from ..aws_login import get_login_status

    try:
        return await asyncio.to_thread(get_login_status, login_id)
    except KeyError:
        return JSONResponse(
            status_code=404,
            content={"code": "unknown_login", "detail": "AWS login was not found"},
        )


@router.post("/aws-profiles")
async def create_fleet_aws_profile(
    http_request: Request, request: AwsProfileCreateRequest
):
    """Create a modern AWS CLI SSO profile in the user's local config."""
    require_local_request(http_request)

    from ..aws_profiles import AwsProfileExists, InvalidAwsProfile, create_sso_profile

    try:
        return await asyncio.to_thread(create_sso_profile, **request.model_dump())
    except InvalidAwsProfile as exc:
        return JSONResponse(
            status_code=400,
            content={"code": "invalid_profile", "detail": str(exc)},
        )
    except AwsProfileExists as exc:
        return JSONResponse(
            status_code=409,
            content={"code": "profile_exists", "detail": str(exc)},
        )


@router.post("/aws-sso-login")
async def start_fleet_aws_sso_login(
    http_request: Request, request: AwsSsoLoginRequest
):
    """Device-authorize an SSO token from a start URL + region alone.

    No account or role is needed yet: the browser flow caches a token that
    the accounts/roles endpoints read. Poll the returned login_id through the
    shared GET /providers/aws-login/{login_id} endpoint.
    """
    require_local_request(http_request)

    from ..aws_login import (
        AwsCliMissing,
        AwsCliSpawnError,
        sso_session_fallback_command,
        start_sso_session_login,
    )
    from ..aws_profiles import InvalidAwsProfile
    from ..aws_sso import stable_session_name

    session_name = stable_session_name(request.start_url)
    try:
        return await asyncio.to_thread(
            start_sso_session_login, request.start_url, request.region
        )
    except InvalidAwsProfile as exc:
        return JSONResponse(
            status_code=400,
            content={"code": "invalid_sso_session", "detail": str(exc)},
        )
    except AwsCliMissing as exc:
        return JSONResponse(
            status_code=409,
            content={
                "code": "aws_cli_missing",
                "detail": str(exc),
                "fallback_command": sso_session_fallback_command(session_name),
            },
        )
    except AwsCliSpawnError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "code": "aws_cli_spawn_failed",
                "detail": str(exc),
                "fallback_command": sso_session_fallback_command(session_name),
            },
        )


@router.get("/aws-sso-accounts")
async def list_fleet_aws_sso_accounts(
    http_request: Request, start_url: str = Query(...)
):
    """List AWS accounts available from the cached SSO token for start_url."""
    require_local_request(http_request)

    from ..aws_sso import list_accounts

    accounts, error = await asyncio.to_thread(list_accounts, start_url)
    return {"accounts": accounts, "error": error}


@router.get("/aws-sso-roles")
async def list_fleet_aws_sso_roles(
    http_request: Request,
    start_url: str = Query(...),
    account_id: str = Query(...),
):
    """List roles available in one account from the cached SSO token."""
    require_local_request(http_request)

    from ..aws_sso import list_account_roles

    roles, error = await asyncio.to_thread(list_account_roles, start_url, account_id)
    return {"roles": roles, "error": error}


@router.post("/aws-sso-finalize")
async def finalize_fleet_aws_sso_profile(
    http_request: Request, request: AwsSsoFinalizeRequest
):
    """Write the chosen account/role as a profile and verify it assumes.

    Reuses create_sso_profile, then runs the STS check. A ForbiddenException
    at GetRoleCredentials surfaces as an actionable error naming the account
    and role rather than a raw botocore trace.
    """
    require_local_request(http_request)

    from ..aws_login import verify_profile
    from ..aws_profiles import (
        AwsProfileExists,
        InvalidAwsProfile,
        ensure_sso_session,
        write_sso_profile,
    )
    from ..aws_sso import stable_session_name

    session_name = stable_session_name(request.start_url)
    try:
        await asyncio.to_thread(
            ensure_sso_session,
            name=session_name,
            sso_start_url=request.start_url,
            sso_region=request.region,
        )
        await asyncio.to_thread(
            write_sso_profile,
            name=request.name,
            sso_session=session_name,
            sso_account_id=request.account_id,
            sso_role_name=request.role_name,
            region=request.region,
        )
    except InvalidAwsProfile as exc:
        return JSONResponse(
            status_code=400,
            content={"code": "invalid_profile", "detail": str(exc)},
        )
    except AwsProfileExists as exc:
        return JSONResponse(
            status_code=409,
            content={"code": "profile_exists", "detail": str(exc)},
        )

    signed_in, detail = await asyncio.to_thread(verify_profile, request.name)
    if not signed_in:
        return JSONResponse(
            status_code=400,
            content={
                "code": "role_verification_failed",
                "created": True,
                "profile": request.name,
                "detail": (
                    f"Profile '{request.name}' was written, but assuming role "
                    f"'{request.role_name}' in account '{request.account_id}' "
                    f"failed: {detail}"
                ),
            },
        )
    return {"created": True, "profile": request.name, "detail": detail}


class ProviderTokenRequest(BaseModel):
    token: str


def _oauth_module(provider: str):
    from importlib import import_module

    return import_module(f"..{OAUTH_PROVIDERS[provider][0]}", __package__)


async def _start_provider_login(provider: str, *args: Any):
    """Start a browser sign-in, or report that the broker is unreachable."""
    from ..provider_oauth import ProviderBrokerError

    try:
        return await asyncio.to_thread(_oauth_module(provider).start_login, *args)
    except ProviderBrokerError as exc:
        return JSONResponse(
            status_code=409,
            content={"code": f"{provider}_oauth_unavailable", "detail": str(exc)},
        )


async def _poll_provider_login(provider: str, login_id: str):
    """Poll a browser sign-in started on this server."""
    try:
        return await asyncio.to_thread(_oauth_module(provider).get_login_status, login_id)
    except KeyError:
        return JSONResponse(
            status_code=404,
            content={
                "code": "unknown_login",
                "detail": f"{OAUTH_PROVIDERS[provider][1]} login was not found",
            },
        )


def _provider_return_url(request: Request, provider: str) -> str:
    """Return to this web app, or to the desktop deep-link when sidecar-hosted."""
    if os.environ.get("RDST_DESKTOP") == "1":
        return f"rdst://oauth-complete?provider={quote(provider)}"

    candidates = [request.headers.get("origin", ""), str(request.base_url)]
    for candidate in candidates:
        try:
            parsed = urlsplit(candidate)
            if (
                parsed.scheme in ("http", "https")
                and parsed.hostname in ("localhost", "127.0.0.1")
            ):
                return f"{parsed.scheme}://{parsed.netloc}/configure?connected={quote(provider)}"
        except ValueError:
            continue

    # ASGI test clients and unusual local proxies may use a synthetic Host.
    # The broker still independently validates this destination at /start.
    return f"http://localhost/configure?connected={quote(provider)}"


@router.get("/supabase-status")
async def get_fleet_supabase_status(http_request: Request) -> dict[str, Any]:
    """Report Supabase Management API credential state for the discovery UI."""
    require_local_request(http_request)

    from ..supabase import get_supabase_status

    return await asyncio.to_thread(get_supabase_status)


@router.post("/supabase-login")
async def start_fleet_supabase_login(request: Request):
    """Start a Supabase OAuth sign-in and hand the browser the authorize URL."""
    require_local_request(request)

    # The redirect URI must match the port this server is actually serving on.
    return await _start_provider_login(
        "supabase",
        str(request.base_url),
        _provider_return_url(request, "supabase"),
    )


def _supabase_callback_page(title: str, detail: str) -> str:
    return (
        "<html><head><title>RDST and Supabase</title></head>"
        "<body style=\"font-family: system-ui, sans-serif; padding: 40px;\">"
        f"<h2>{escape(title)}</h2>"
        f"<p>{escape(detail)}</p>"
        "<p>You can close this tab and return to RDST.</p>"
        "</body></html>"
    )


@router.get("/supabase-callback")
async def fleet_supabase_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """Complete the Supabase OAuth exchange and tell the user to go back."""
    from ..supabase_oauth import handle_callback

    if error:
        return HTMLResponse(
            _supabase_callback_page("Supabase sign-in failed", error), status_code=400
        )
    if not code or not state:
        return HTMLResponse(
            _supabase_callback_page(
                "Supabase sign-in failed", "The callback was missing a code or state."
            ),
            status_code=400,
        )

    result = await asyncio.to_thread(handle_callback, code, state)
    if result["state"] == "success":
        return HTMLResponse(
            _supabase_callback_page("Connected to Supabase", result["detail"])
        )
    return HTMLResponse(
        _supabase_callback_page("Supabase sign-in failed", result["detail"]),
        status_code=400,
    )


@router.get("/supabase-login/{login_id}")
async def get_fleet_supabase_login(http_request: Request, login_id: str):
    """Poll a Supabase OAuth sign-in started on this server."""
    require_local_request(http_request)

    return await _poll_provider_login("supabase", login_id)


@router.post("/supabase-logout")
async def fleet_supabase_logout(http_request: Request):
    """Revoke and clear stored Supabase credentials."""
    require_local_request(http_request)

    from ..supabase_oauth import logout

    return await asyncio.to_thread(logout)


@router.get("/neon-status")
async def get_fleet_neon_status(http_request: Request) -> dict[str, Any]:
    """Report Neon API credential state for the discovery UI."""
    require_local_request(http_request)

    from ..neon import get_neon_status

    return await asyncio.to_thread(get_neon_status)


@router.post("/neon-key")
async def set_fleet_neon_key(http_request: Request, request: ProviderTokenRequest):
    """Store a Neon API key after validating it."""
    require_local_request(http_request)

    from ..neon import store_api_key, validate_key

    token = request.token.strip()
    if not token:
        return JSONResponse(
            status_code=400,
            content={"code": "invalid_token", "detail": "Provide a Neon API key"},
        )
    valid, detail = await asyncio.to_thread(validate_key, token)
    if not valid:
        return JSONResponse(
            status_code=400, content={"code": "invalid_token", "detail": detail}
        )
    return await asyncio.to_thread(store_api_key, token)


@router.post("/neon-logout")
async def fleet_neon_logout(http_request: Request):
    """Clear the stored Neon API key."""
    require_local_request(http_request)

    from ..neon import logout

    return await asyncio.to_thread(logout)


@router.get("/digitalocean-status")
async def get_fleet_digitalocean_status(http_request: Request) -> dict[str, Any]:
    """Report DigitalOcean API credential state for the discovery UI."""
    require_local_request(http_request)

    from ..digitalocean import get_digitalocean_status

    return await asyncio.to_thread(get_digitalocean_status)


@router.post("/digitalocean-login")
async def start_fleet_digitalocean_login(request: Request):
    """Start a DigitalOcean OAuth sign-in and hand the browser the authorize URL."""
    require_local_request(request)

    return await _start_provider_login(
        "digitalocean", _provider_return_url(request, "digitalocean")
    )


@router.get("/digitalocean-login/{login_id}")
async def get_fleet_digitalocean_login(http_request: Request, login_id: str):
    """Poll a DigitalOcean OAuth sign-in started on this server."""
    require_local_request(http_request)

    return await _poll_provider_login("digitalocean", login_id)


@router.post("/digitalocean-logout")
async def fleet_digitalocean_logout(http_request: Request):
    """Clear the in-process DigitalOcean session."""
    require_local_request(http_request)

    from ..digitalocean_oauth import logout

    return await asyncio.to_thread(logout)


class FleetDiscoverPreviewRequest(BaseModel):
    # Regions are AWS-only; the other providers cover the whole account.
    regions: list[str] = []
    engine_filter: Optional[str] = None
    profile: Optional[str] = None
    provider: str = "aws"


class FleetBulkAddRequest(BaseModel):
    members: list[dict[str, Any]]


@router.post("/discover-preview")
async def discover_preview(
    http_request: Request, request: FleetDiscoverPreviewRequest
):
    """List discoverable provider databases without importing them."""
    require_local_request(http_request)

    if request.provider in ACCOUNT_PROVIDERS:
        return await ProvidersService().discover_preview_account(request.provider)
    if not request.regions:
        raise HTTPException(status_code=400, detail="At least one region is required")
    service = ProvidersService()
    engine_filter = (
        request.engine_filter if request.engine_filter not in (None, "all") else None
    )
    return await service.discover_preview(
        request.regions, engine_filter=engine_filter, profile=request.profile
    )


@router.post("/bulk-add")
async def bulk_add_targets(http_request: Request, request: FleetBulkAddRequest):
    """Add previously previewed members as fleet targets."""
    require_local_request(http_request)

    if not request.members:
        raise HTTPException(status_code=400, detail="No members provided")
    service = ProvidersService()
    try:
        return await asyncio.to_thread(service.add_members, request.members)
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid member shape: {exc}")


@router.post("/discover")
async def discover_fleet(http_request: Request, request: FleetDiscoverRequest):
    """Discover RDS/Aurora instances from AWS and add them as targets (SSE stream)."""
    require_local_request(http_request)

    service = ProvidersService()
    engine_filter = request.engine_filter if request.engine_filter not in (None, "all") else None

    async def _generator() -> AsyncGenerator[dict, None]:
        if not request.regions:
            yield {
                "event": "error",
                "data": json.dumps({"message": "At least one region is required", "phase": "discover"}),
            }
            return
        async for event in service.discover(
            request.regions,
            engine_filter=engine_filter,
            name_pattern=request.name_pattern,
            password_env=request.password_env,
            password=(
                request.password.get_secret_value() if request.password else None
            ),
            default_user=request.user,
            default_group=request.group,
            default_database=request.default_database,
            dry_run=request.dry_run,
            profile=request.profile,
        ):
            yield _event_to_sse(event)

    return EventSourceResponse(_generator())

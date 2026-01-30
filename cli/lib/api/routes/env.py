"""API routes for secure environment variable handling."""

from __future__ import annotations

from typing import List, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, SecretStr

from ...services.env_requirements_service import EnvRequirementsService

router = APIRouter()

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class EnvRequirement(BaseModel):
    kind: str
    accepted_names: List[str]
    target: Optional[str] = None
    satisfied: bool
    source: str


class EnvRequirementsResponse(BaseModel):
    keyring_available: bool
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


def _normalize_host(host: str) -> str:
    if host.startswith("::ffff:"):
        return host.split("::ffff:", 1)[1]
    return host


def _is_loopback_request(request: Request) -> bool:
    client = request.client
    if not client:
        return False
    host = _normalize_host(client.host or "")
    return host in _LOOPBACK_HOSTS


def _same_host_from_headers(request: Request) -> bool:
    host_header = request.headers.get("host")
    expected_host = None
    if host_header:
        expected_host = urlsplit(f"http://{host_header}").hostname
        if expected_host:
            expected_host = _normalize_host(expected_host)

    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if not value:
            continue
        parsed_host = urlsplit(value).hostname
        if not parsed_host:
            return False
        parsed_host = _normalize_host(parsed_host)
        if parsed_host not in _LOOPBACK_HOSTS:
            return False
        if expected_host and parsed_host != expected_host:
            return False
    return True


@router.get("/env/requirements")
async def get_env_requirements(request: Request) -> EnvRequirementsResponse:
    if not _is_loopback_request(request):
        raise HTTPException(status_code=403, detail="Forbidden")

    service = EnvRequirementsService()
    requirements = service.get_requirements()
    return EnvRequirementsResponse(
        keyring_available=service.secret_store.is_available(),
        requirements=[EnvRequirement(**item) for item in requirements],
    )


@router.post("/env/set")
async def set_env_secret(request: Request, body: EnvSetRequest) -> EnvSetResponse:
    if not _is_loopback_request(request):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not _same_host_from_headers(request):
        raise HTTPException(status_code=403, detail="Origin/Referer host mismatch")

    service = EnvRequirementsService()
    allowed = set(service.get_allowed_secret_names())
    if body.name not in allowed:
        return EnvSetResponse(
            success=False,
            name=body.name,
            persisted=False,
            session_only=True,
            message="Environment variable is not allowed.",
        )

    result = service.secret_store.set_secret(
        name=body.name,
        value=body.value.get_secret_value(),
        persist=body.persist,
    )

    return EnvSetResponse(
        success=True,
        name=body.name,
        persisted=bool(result.get("persisted", False)),
        session_only=bool(result.get("session_only", True)),
        message=result.get("message"),
    )

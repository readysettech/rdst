"""Read-only allowlist context and explicit provider write-back routes."""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from shared.api.guards import require_local_request
from shared.config.targets import TargetsConfig

from ..service import AllowlistError, add_current_ip, allowlist_context

router = APIRouter(prefix="/allowlist", tags=["allowlist"])


class AllowlistContextResponse(BaseModel):
    provider: str
    signed_in: bool
    current_ip: str
    already_allowed: Optional[bool] = None
    entry_count: Optional[int] = None
    guidance: str
    error: Optional[str] = None


class AllowlistAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str
    expected_ip: Optional[str] = None


class AllowlistAddResponse(BaseModel):
    ok: bool
    added_ip: Optional[str] = None
    message: str
    category: str
    verified: Optional[bool] = None
    credential_method: Optional[str] = None


def _config() -> TargetsConfig:
    config = TargetsConfig()
    config.load()
    return config


def _error_response(exc: AllowlistError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "ok": False,
            "added_ip": None,
            "message": str(exc),
            "category": exc.category,
            "verified": exc.verified,
            "credential_method": None,
        },
    )


def _context_error_response(exc: AllowlistError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "provider": "",
            "signed_in": False,
            "current_ip": "",
            "already_allowed": None,
            "entry_count": None,
            "guidance": "",
            "error": str(exc),
        },
    )


@router.get("/context", response_model=AllowlistContextResponse)
async def get_allowlist_context(
    http_request: Request, target: str = Query(...)
) -> AllowlistContextResponse:
    """Fetch public IP and read provider state. This route never writes."""
    require_local_request(http_request)

    try:
        result = await asyncio.to_thread(allowlist_context, _config(), target)
    except AllowlistError as exc:
        return _context_error_response(exc)
    return AllowlistContextResponse(**result)


@router.post("/add", response_model=AllowlistAddResponse)
async def add_allowlist_ip(
    http_request: Request,
    request: AllowlistAddRequest,
) -> AllowlistAddResponse:
    """On an explicit confirmed click, read-merge-write the provider allowlist."""
    require_local_request(http_request)

    try:
        result = await asyncio.to_thread(
            add_current_ip,
            _config(),
            request.target,
            request.expected_ip,
        )
    except AllowlistError as exc:
        return _error_response(exc)
    return AllowlistAddResponse(**result)

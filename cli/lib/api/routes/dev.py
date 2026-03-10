"""API routes for development-only utilities."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .env import _is_loopback_request, _same_host_from_headers

router = APIRouter()



class ClearKeyringResponse(BaseModel):
    success: bool
    cleared: list[str]
    missing: list[str]
    errors: list[str]
    message: Optional[str] = None


@router.post("/dev/clear-keyring")
async def clear_keyring(request: Request) -> ClearKeyringResponse:
    if not _is_loopback_request(request):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not _same_host_from_headers(request):
        raise HTTPException(status_code=403, detail="Origin/Referer host mismatch")

    from ...cli.rdst_cli import TargetsConfig
    from ...services.env_requirements_service import EnvRequirementsService
    from ...services.secret_store_service import SecretStoreService

    env_service = EnvRequirementsService()
    names = env_service.get_allowed_secret_names()

    secret_store = SecretStoreService()
    result = secret_store.clear_required(names)

    cleared = result.get("cleared", [])
    missing = result.get("missing", [])
    errors = list(result.get("errors", []))

    cfg = TargetsConfig()
    trial_state_cleared = False
    try:
        cfg.load()
        trial_state_cleared = cfg.clear_trial_config()
        if trial_state_cleared:
            cfg.save()
    except Exception as exc:
        errors.append(f"trial config: {exc}")

    reset_parts: list[str] = []
    if cleared:
        reset_parts.append(f"{len(cleared)} secret(s)")
    if trial_state_cleared:
        reset_parts.append("local trial state")

    if errors:
        if reset_parts:
            message = f"Reset {' and '.join(reset_parts)} with {len(errors)} error(s)."
        else:
            message = f"Failed to reset local auth state ({len(errors)} error(s))."
    elif reset_parts:
        message = f"Reset {' and '.join(reset_parts)}."
    else:
        message = "No local auth state found to clear."

    return ClearKeyringResponse(
        success=len(errors) == 0,
        cleared=cleared,
        missing=missing,
        errors=errors,
        message=message,
    )

"""Shared target/password guard for target-bound API routes."""

from __future__ import annotations

import json as _json
from typing import Any, Dict, NamedTuple, Optional, Tuple

from fastapi import HTTPException, Query, Request

from ...cli.rdst_cli import TargetsConfig
from ...services.password_resolver import resolve_password

TARGET_PASSWORD_REQUIRED_CODE = "TARGET_PASSWORD_REQUIRED"


class TargetGuard(NamedTuple):
    target_name: str
    target_config: Dict[str, Any]


def _to_target_dict(target_config: Any) -> Dict[str, Any]:
    if isinstance(target_config, dict):
        return dict(target_config)
    if hasattr(target_config, "__dict__"):
        return dict(target_config.__dict__)
    return dict(target_config)


def resolve_target_config(target: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """Resolve target name and config using explicit target or default target."""
    cfg = TargetsConfig()
    cfg.load()

    target_name = target or cfg.get_default()
    if not target_name:
        raise HTTPException(
            status_code=400,
            detail="No target specified and no default target configured",
        )

    target_config = cfg.get(target_name)
    if not target_config:
        raise HTTPException(status_code=404, detail=f"Target '{target_name}' not found")

    return target_name, _to_target_dict(target_config)


def ensure_target_password(target: Optional[str] = None) -> TargetGuard:
    """Resolve target config and enforce password availability."""
    target_name, target_config = resolve_target_config(target)

    if resolve_password(target_config).available:
        return TargetGuard(target_name, target_config)

    password_env = target_config.get("password_env")
    if password_env:
        message = (
            f"Target '{target_name}' is locked until '{password_env}' is set. "
            "Use Set in Web to provide the secret."
        )
    else:
        message = (
            f"Target '{target_name}' is locked because no password is configured for it."
        )

    raise HTTPException(
        status_code=423,
        detail={
            "code": TARGET_PASSWORD_REQUIRED_CODE,
            "target": target_name,
            "password_env": password_env,
            "message": message,
        },
    )


# ---------------------------------------------------------------------------
# FastAPI Depends() helpers — use these instead of calling ensure_target_password
# ---------------------------------------------------------------------------


def require_target(
    target: str = Query(..., description="Target database name"),
) -> TargetGuard:
    """Depends() for GET/DELETE routes with a required ``target`` query param."""
    return ensure_target_password(target)


def require_target_optional(
    target: Optional[str] = Query(None, description="Target database name"),
) -> TargetGuard:
    """Depends() for GET routes where ``target`` is optional (falls back to default)."""
    return ensure_target_password(target)


async def require_target_body(request: Request) -> TargetGuard:
    """Depends() for POST/PUT routes where ``target`` lives in the JSON body."""
    target: Optional[str] = None
    try:
        body_bytes = await request.body()
        data = _json.loads(body_bytes)
        target = data.get("target") if isinstance(data, dict) else None
    except Exception:
        pass
    return ensure_target_password(target)

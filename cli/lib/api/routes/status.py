from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from ...services.password_resolver import resolve_password

router = APIRouter()


class TargetInfo(BaseModel):
    name: str
    has_password: bool
    is_default: bool


class StatusResponse(BaseModel):
    configured: bool
    default_target: Optional[str] = None
    targets: list[TargetInfo]
    version: Optional[str] = None
    error: Optional[str] = None



def _get_version() -> str:
    """Get RDST version string."""
    try:
        from importlib.metadata import version as get_version

        return get_version("rdst")
    except Exception:
        try:
            from ..._version import __version__

            return __version__
        except Exception:
            return "unknown"


@router.get("/status")
async def get_status() -> StatusResponse:
    """Check if RDST is properly configured with database targets."""
    try:
        from ...cli.rdst_cli import TargetsConfig

        cfg = TargetsConfig()
        cfg.load()

        targets_list = []
        default_target = cfg.get_default()

        # Get all target names using list_targets()
        target_names = cfg.list_targets()

        for name in target_names:
            target_config = cfg.get(name)
            if target_config:
                targets_list.append(
                    TargetInfo(
                        name=name,
                        has_password=resolve_password(target_config).available,
                        is_default=(name == default_target),
                    )
                )

        configured = len(targets_list) > 0 and any(t.has_password for t in targets_list)

        return StatusResponse(
            configured=configured,
            default_target=default_target,
            targets=targets_list,
            version=_get_version(),
        )

    except Exception as e:
        return StatusResponse(
            configured=False,
            targets=[],
            version=_get_version(),
            error=str(e),
        )

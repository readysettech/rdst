"""API routes for inspecting and testing SSH tunnels."""

from __future__ import annotations

import asyncio
import time
from typing import Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

router = APIRouter(prefix="/tunnel", tags=["tunnel"])


def _get_tunnel_manager():
    from shared.ssh_tunnel import get_tunnel_manager

    return get_tunnel_manager()


class TunnelStatusResponse(BaseModel):
    target: str
    jump_host: str
    local_port: int
    state: str
    last_used: float


class CloseTunnelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Optional[str] = None
    all: bool = False

    @model_validator(mode="after")
    def validate_selection(self):
        if bool(self.target) == self.all:
            raise ValueError("Specify exactly one of target or all=true")
        return self


class CloseTunnelResponse(BaseModel):
    ok: bool
    message: str


class TestTunnelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str


class TestTunnelResponse(BaseModel):
    ok: bool
    stage: Literal["tunnel", "database"]
    category: Optional[str] = None
    message: str


class SshAuthOptionResponse(BaseModel):
    kind: str
    label: str
    key_path: Optional[str] = None
    host: Optional[str] = None
    hostname: Optional[str] = None
    port: Optional[int] = None
    user: Optional[str] = None
    outside_ssh_dir: bool = False


class SshKeysResponse(BaseModel):
    options: list[SshAuthOptionResponse]


class ImportSshKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(min_length=1)


class ImportSshKeyResponse(BaseModel):
    key_path: str


class SshProfileData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    user: Optional[str] = None
    key_path: Optional[str] = None


class SshProfileResponse(SshProfileData):
    name: str


@router.get("/status", response_model=list[TunnelStatusResponse])
async def tunnel_status() -> list[TunnelStatusResponse]:
    """Return the tunnels managed by this backend process."""
    statuses = _get_tunnel_manager().status()
    return [TunnelStatusResponse(**status) for status in statuses]


@router.get("/ssh-keys", response_model=SshKeysResponse)
async def ssh_keys(jump_host: str = "") -> SshKeysResponse:
    """Return local SSH agent, key-file, and ssh-config authentication options."""
    from shared.ssh_keys import discover_ssh_auth_options

    options = await asyncio.to_thread(discover_ssh_auth_options, jump_host)
    return SshKeysResponse(
        options=[SshAuthOptionResponse(**option) for option in options]
    )


@router.post("/ssh-keys/import", response_model=ImportSshKeyResponse)
async def import_ssh_key(request: ImportSshKeyRequest) -> ImportSshKeyResponse:
    """Copy a local private key into ~/.ssh without changing the source file."""
    from fastapi import HTTPException

    from shared.ssh_keys import import_ssh_key as copy_ssh_key

    try:
        destination = await asyncio.to_thread(copy_ssh_key, request.source_path)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ImportSshKeyResponse(key_path=str(destination))


@router.get("/ssh-profiles", response_model=list[SshProfileResponse])
async def ssh_profiles() -> list[SshProfileResponse]:
    """Return reusable SSH jump-host profiles without resolving key material."""
    from shared.config.targets import TargetsConfig

    config = TargetsConfig()
    config.load()
    return [
        SshProfileResponse(name=name, **(config.get_ssh_host(name) or {}))
        for name in config.list_ssh_hosts()
    ]


@router.post("/close", response_model=CloseTunnelResponse)
async def close_tunnel(request: CloseTunnelRequest) -> CloseTunnelResponse:
    """Close one target tunnel or every tunnel in this process."""
    manager = _get_tunnel_manager()
    if request.all:
        count = len(manager.status())
        await asyncio.to_thread(manager.close_all)
        return CloseTunnelResponse(ok=True, message=f"Closed {count} SSH tunnel(s).")

    target = request.target or ""
    was_open = any(item["target"] == target for item in manager.status())
    await asyncio.to_thread(manager.close, target)
    message = (
        f"Closed SSH tunnel for '{target}'."
        if was_open
        else f"No open SSH tunnel for '{target}'."
    )
    return CloseTunnelResponse(ok=True, message=message)


@router.post("/test", response_model=TestTunnelResponse)
async def test_tunnel(request: TestTunnelRequest) -> TestTunnelResponse:
    """Open a target's tunnel and probe its database through the local endpoint."""
    from shared.config.targets import TargetsConfig, default_port_for
    from shared.db_connection import normalize_engine_name, probe_target_connection

    cfg = TargetsConfig()
    cfg.load()
    target_config = cfg.get(request.target)
    if target_config is None:
        return TestTunnelResponse(
            ok=False,
            stage="tunnel",
            category="ssh_tunnel_error",
            message=f"Target '{request.target}' was not found.",
        )

    ssh_config = target_config.get("ssh")
    if not ssh_config:
        return TestTunnelResponse(
            ok=False,
            stage="tunnel",
            category="ssh_tunnel_error",
            message=f"Target '{request.target}' has no SSH jump host configured.",
        )

    manager = _get_tunnel_manager()
    engine = normalize_engine_name(str(target_config.get("engine") or "postgresql"))
    default_port = default_port_for(engine)
    dest_port = int(target_config.get("port") or default_port)
    probe_config = dict(target_config)
    probe_config.pop("ssh", None)
    try:

        def _open_and_probe():
            # Hold the target lifecycle through the first DB handshake. A
            # simultaneous explicit test must not refresh this endpoint while
            # its predecessor is still connecting through it.
            with manager.target_lifecycle(request.target):
                local_host, local_port = manager.ensure_tunnel(
                    request.target,
                    ssh_config,
                    str(target_config.get("host") or ""),
                    dest_port,
                    force_fresh=True,
                )
                if probe_config.get("tls_verify"):
                    probe_config["host"] = target_config["host"]
                    probe_config["hostaddr"] = local_host
                else:
                    probe_config["host"] = local_host
                probe_config["port"] = local_port
                state = None
                for attempt in range(2):
                    state = probe_target_connection(
                        probe_config,
                        connect_timeout=5,
                    )
                    if state["success"]:
                        break
                    error = str(state.get("error") or "").lower()
                    reset = (
                        "connection reset" in error
                        or "connection abort" in error
                        or "econnreset" in error
                        or "econnaborted" in error
                        or "errno 104" in error
                        or "errno 103" in error
                        or "winerror 10054" in error
                        or "winerror 10053" in error
                    )
                    if attempt == 0 and reset:
                        time.sleep(0.25)
                        continue
                    break
                assert state is not None
                if not state["success"]:
                    manager.close(request.target)
                return local_host, local_port, state

        local_host, local_port, state = await asyncio.to_thread(_open_and_probe)
    except Exception as exc:
        from shared.api.ssh_errors import ssh_error_payload

        payload = ssh_error_payload(exc, request.target, ssh_config)
        return TestTunnelResponse(
            ok=False,
            stage="tunnel",
            category=payload["category"],
            message=payload["message"],
        )

    if not state["success"]:
        detail = state.get("error") or "Connection failed"
        from shared.api.ssh_errors import connectivity_error_payload

        failure = connectivity_error_payload(
            detail,
            request.target,
            target_config,
        )
        return TestTunnelResponse(
            ok=False,
            stage="database",
            category=(failure or {}).get("category", "database_connection_failed"),
            message=(failure or {}).get(
                "message",
                f"SSH tunnel opened for '{request.target}', but the database is "
                f"unreachable through it: {detail}. Check the database host, port, "
                "network rules, and DB credentials.",
            ),
        )

    return TestTunnelResponse(
        ok=True,
        stage="database",
        message=(
            f"Tunnel test passed for '{request.target}': the jump host and "
            "database are reachable."
        ),
    )

"""Cache lifecycle — probe state + start/stop/restart/remove dispatch by deploy mode.

Each deploy mode (docker, systemd, kubernetes, remote) implements the same
five primitives in its own module; this layer routes by mode.

Probe is mode-aware because "stopped" means different things:
  - docker: container exists in `docker ps -a` but not `docker ps`
  - systemd: unit file exists, `is-active` returns inactive
  - kubernetes: Deployment exists, replicas=0
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from . import local_docker, local_systemd, kubernetes, remote


class ProbeState(str, Enum):
    NOT_DEPLOYED = "not_deployed"
    DEPLOYED_RUNNING = "deployed_running"
    DEPLOYED_STOPPED = "deployed_stopped"
    DEPLOYED_UNREACHABLE = "deployed_unreachable"
    UNKNOWN = "unknown"


@dataclass
class ProbeResult:
    state: ProbeState
    mode: str
    container_id: Optional[str] = None
    detail: Optional[str] = None


@dataclass
class LifecycleResult:
    success: bool
    state_after: Optional[ProbeState] = None
    detail: str = ""
    error: Optional[str] = None


_NOT_IMPLEMENTED_DETAIL = (
    "{op} is not yet implemented for mode '{mode}'. "
    "Tracking: in-request-path branch will add full support."
)


def probe(target_name: str, mode: str, **kwargs) -> ProbeResult:
    """Detect current deployment state for a target."""
    if mode == "docker":
        return local_docker.probe_local_docker(target_name)
    if mode == "systemd":
        return local_systemd.probe_local_systemd(target_name)
    if mode == "kubernetes":
        return kubernetes.probe_kubernetes(target_name, kwargs.get("namespace"))
    if mode in ("remote_docker", "remote_systemd", "remote"):
        return remote.probe_remote(target_name, kwargs.get("host"), kwargs.get("ssh_key"), kwargs.get("ssh_user"))
    return ProbeResult(state=ProbeState.UNKNOWN, mode=mode, detail=f"Unknown deploy mode '{mode}'")


def start(target_name: str, mode: str, **kwargs) -> LifecycleResult:
    """Start an existing-but-stopped cache without redeploying."""
    if mode == "docker":
        return local_docker.start_local_docker(target_name)
    if mode == "systemd":
        return _stub("start", mode)
    if mode == "kubernetes":
        return _stub("start", mode, hint="kubectl scale deploy/readyset --replicas=1")
    if mode in ("remote_docker", "remote_systemd", "remote"):
        return _stub("start", mode)
    return LifecycleResult(success=False, error=f"Unknown deploy mode '{mode}'")


def stop(target_name: str, mode: str, **kwargs) -> LifecycleResult:
    """Stop a running cache without removing it."""
    if mode == "docker":
        return local_docker.stop_local_docker(target_name)
    if mode == "systemd":
        return _stub("stop", mode)
    if mode == "kubernetes":
        return _stub("stop", mode, hint="kubectl scale deploy/readyset --replicas=0")
    if mode in ("remote_docker", "remote_systemd", "remote"):
        return _stub("stop", mode)
    return LifecycleResult(success=False, error=f"Unknown deploy mode '{mode}'")


def restart(target_name: str, mode: str, **kwargs) -> LifecycleResult:
    """Restart a deployed cache (stop + start, preserving config)."""
    if mode == "docker":
        return local_docker.restart_local_docker(target_name)
    if mode == "systemd":
        return _stub("restart", mode)
    if mode == "kubernetes":
        return _stub("restart", mode, hint="kubectl rollout restart deploy/readyset")
    if mode in ("remote_docker", "remote_systemd", "remote"):
        return _stub("restart", mode)
    return LifecycleResult(success=False, error=f"Unknown deploy mode '{mode}'")


def _stub(op: str, mode: str, hint: Optional[str] = None) -> LifecycleResult:
    msg = _NOT_IMPLEMENTED_DETAIL.format(op=f"cache {op}", mode=mode)
    if hint:
        msg += f"\nTo {op} manually: {hint}"
    return LifecycleResult(success=False, error=msg)


# =============================================================================
# Ephemeral lifecycle — for analyze / audit / cache compare
# =============================================================================

from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class EphemeralOutcome:
    """What ephemeral_lifecycle did so the caller can describe it."""
    pre_state: ProbeState
    we_deployed: bool = False  # we created the container; tear down on exit
    we_started: bool = False   # we started a stopped container; stop on exit
    container_name: Optional[str] = None
    error: Optional[str] = None  # set if entry failed
    removed_stale: bool = False


@contextmanager
def ephemeral_lifecycle(
    target_name: str, mode: str = "docker", *, fresh: bool = False,
):
    """Ensure a cache container is running for the duration of the block.

    Default entry semantics:
      - DEPLOYED_RUNNING → reuse, untouched on exit
      - DEPLOYED_STOPPED → `cache start`; on exit, `cache stop` (back to stopped)
      - NOT_DEPLOYED → `cache deploy`; on exit, `cache remove` (full teardown)
      - DEPLOYED_UNREACHABLE / UNKNOWN → yield with error set; caller decides

    Yields an `EphemeralOutcome`. The container's connection info still lives
    in `~/.rdst/config.toml` after entry, so callers continue using
    `_resolve_cache_target` etc. as before.

    With ``fresh=True``, any same-named Docker container and its generated
    cache-target config are removed first. A newly deployed container is then
    unconditionally removed on exit. Audit benchmarks use this mode so a stale
    or unhealthy prior container is never silently reused.

    Currently Docker mode only; other modes yield with `error` set so the caller
    can fall back to legacy "cache target must already exist" behavior.
    """
    outcome = EphemeralOutcome(pre_state=ProbeState.UNKNOWN)
    fresh_cleanup_required = False
    try:
        # Only Docker is implemented; other modes punt to the caller.
        if mode != "docker":
            outcome.error = f"ephemeral_lifecycle does not yet support mode '{mode}'"
            yield outcome
            return

        pre = probe(target_name, mode=mode)
        outcome.pre_state = pre.state
        outcome.container_name = pre.container_id

        if fresh:
            fresh_cleanup_required = True
            cleanup_error = _cleanup_ephemeral_deploy(target_name, mode=mode)
            if cleanup_error:
                outcome.error = f"could not remove stale Readyset container: {cleanup_error}"
                yield outcome
                return
            outcome.removed_stale = pre.state != ProbeState.NOT_DEPLOYED
            # Cleanup also removes a stale generated config row. Confirm Docker
            # agrees before deploying, otherwise DeployCommand may promote/reuse
            # the very container this run intended to replace.
            after_cleanup = probe(target_name, mode=mode)
            if after_cleanup.state != ProbeState.NOT_DEPLOYED:
                outcome.error = (
                    "stale Readyset container still exists after cleanup "
                    f"({after_cleanup.detail or after_cleanup.state.value})"
                )
                yield outcome
                return
            pre = after_cleanup

        if pre.state == ProbeState.DEPLOYED_RUNNING:
            yield outcome
            return

        if pre.state == ProbeState.DEPLOYED_STOPPED:
            r = start(target_name, mode=mode)
            if not r.success:
                outcome.error = r.error or "cache start failed"
                yield outcome
                return
            outcome.we_started = True
            _wait_for_cache_ready(target_name, mode=mode, timeout_s=60)
            yield outcome
            return

        if pre.state == ProbeState.NOT_DEPLOYED:
            # Deploy fresh ephemeral container with default in-request-path settings.
            # Lazy import to avoid pulling in CLI deps at module load.
            from features.cache.cli.deploy import DeployCommand
            r = DeployCommand().execute(target=target_name, mode=mode, quiet=True)
            if not r.ok:
                outcome.error = f"ephemeral deploy failed: {r.message}"
                yield outcome
                return
            outcome.we_deployed = True
            # Wait until the cache port is reachable. ReadySet takes several
            # seconds to start its SQL listener after `docker run` returns.
            ready = _wait_for_cache_ready(target_name, mode=mode, timeout_s=90)
            if fresh and not ready:
                outcome.error = "fresh Readyset container did not become healthy within 90s"
                yield outcome
                return
            yield outcome
            return

        outcome.error = f"unexpected probe state: {pre.state.value} ({pre.detail})"
        yield outcome

    finally:
        # Tear down to original state on exit, regardless of how the block ended.
        if fresh_cleanup_required or outcome.we_deployed:
            try:
                _cleanup_ephemeral_deploy(target_name, mode=mode)
            except Exception:
                pass
        elif outcome.we_started:
            try:
                stop(target_name, mode=mode)
            except Exception:
                pass


def _wait_for_cache_ready(target_name: str, mode: str = "docker", timeout_s: int = 90) -> bool:
    """Wait for the cache container's SQL port to accept connections.

    Returns True on success, False on timeout. ReadySet binds TCP a few seconds
    after `docker run` returns; without this gate, callers attempting EXPLAIN
    CREATE CACHE FROM immediately after deploy hit "Connection refused".
    """
    if mode != "docker":
        return True  # not implemented for other modes; assume ready
    import time
    from . import local_docker
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = probe(target_name, mode=mode)
        if result.state == ProbeState.DEPLOYED_RUNNING and "reachable" in (result.detail or "").lower():
            return True
        time.sleep(2)
    return False


def _cleanup_ephemeral_deploy(
    target_name: str, mode: str = "docker",
) -> Optional[str]:
    """Tear down an ephemerally-deployed cache: stop+rm container, drop config row.

    Used by ephemeral_lifecycle. Does not invoke CacheCommands.remove() because
    that path requires a pre-loaded target_config and produces interactive output;
    here we want a silent, idempotent teardown.
    """
    if mode != "docker":
        return None
    import subprocess
    container_name = f"rdst-readyset-{target_name}"
    try:
        result = subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True, text=True, timeout=15,
        )
        # "No such container" is the desired idempotent end state.
        if result.returncode != 0 and "no such container" not in result.stderr.lower():
            return result.stderr.strip() or "docker rm -f failed"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return str(exc)
    # Remove the cache-target row from config (the deploy path adds one named
    # `{upstream}-cache`). Idempotent: missing row is fine.
    try:
        from shared.config.targets import TargetsConfig
        cfg = TargetsConfig()
        cfg.load()
        cache_target_name = f"{target_name}-cache"
        targets = cfg._data.get("targets", {})
        if cache_target_name in targets:
            del targets[cache_target_name]
            if cfg.get_default() == cache_target_name:
                cfg.set_default(target_name)
            cfg.save()
    except Exception:
        pass
    return None

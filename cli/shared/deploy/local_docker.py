"""Local Docker deployment — promote existing container or create new one."""

from __future__ import annotations

import shutil
import socket
import subprocess  # nosec B404  # nosemgrep: gitlab.bandit.B404
from typing import Any, Dict, Optional
from urllib.parse import quote as urlquote

MANAGED_SANDBOX_NAME = "rdst-readyset-sandbox"
MANAGED_SANDBOX_LABEL = "io.readyset.rdst.sandbox"


def docker_runtime_status() -> Dict[str, bool]:
    """Return whether the local Docker CLI and daemon are available."""
    if shutil.which("docker") is None:
        return {"installed": False, "running": False}
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {"installed": True, "running": False}
    return {"installed": True, "running": result.returncode == 0}


def deploy_local_docker(
    target_name: str,
    variables: Dict[str, str],
    password: str,
) -> Dict[str, Any]:
    """Deploy Readyset locally via Docker.

    Two paths:
    1. A target-specific container already exists → promote to permanent
    2. No container → create new with persistent flags
    """
    # Check for existing container (both naming conventions)
    existing = _find_existing_container(target_name)

    if existing:
        return _promote_container(existing)

    # No existing container — create new
    return _create_container(target_name, variables, password)


def _find_existing_container(target_name: str) -> Optional[Dict[str, Any]]:
    """Check for existing Readyset container for this target."""
    name = f"rdst-readyset-{target_name}"
    try:
        result = subprocess.run(
            [
                "docker", "ps", "-a",
                "--filter", f"name=^{name}$",
                "--format", "{{.Names}}\t{{.Status}}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split("\t")
            running = "Up" in parts[1] if len(parts) > 1 else False
            return {"name": name, "running": running}
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        pass

    return None


def _promote_container(existing: Dict[str, Any]) -> Dict[str, Any]:
    """Make an existing container permanent with restart policy.

    If the container is already running, treat it as already deployed.
    """
    name = existing["name"]
    was_running = existing["running"]

    # Already running — nothing to do
    if was_running:
        return {
            "success": True,
            "container_name": name,
            "already_running": True,
            "promoted": False,
            "was_running": True,
        }

    # Start if not running
    if not was_running:
        try:
            start_result = subprocess.run(
                ["docker", "start", name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if start_result.returncode != 0:
                return {
                    "success": False,
                    "error": f"Failed to start container '{name}': {start_result.stderr.strip()}",
                }
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            return {
                "success": False,
                "error": f"Failed to start container '{name}': {e}",
            }

    # Set restart policy (for ephemeral → persistent promotion)
    try:
        update_result = subprocess.run(
            ["docker", "update", "--restart=unless-stopped", name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if update_result.returncode != 0:
            return {
                "success": False,
                "error": f"Failed to set restart policy: {update_result.stderr.strip()}",
            }
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return {
            "success": False,
            "error": f"Failed to set restart policy: {e}",
        }

    return {
        "success": True,
        "container_name": name,
        "promoted": True,
        "was_running": was_running,
    }


def _create_container(
    target_name: str,
    variables: Dict[str, str],
    password: str,
) -> Dict[str, Any]:
    """Create a new persistent Readyset container."""
    engine = variables["db_engine"]
    db_host = variables["db_host"]
    db_port = variables["db_port"]
    db_user = variables["db_user"]
    db_name = variables["db_name"]
    readyset_port = variables["readyset_port"]
    container_name = variables["container_name"]
    image = variables["readyset_image"]
    # In-request-path is the new default: queries flowing through ReadySet
    # auto-create caches without requiring explicit CREATE CACHE statements.
    # Caller (DeployCommand) sets this to "explicit" via --no-request-path.
    # Note: clap-side value parser expects kebab-case "in-request-path".
    query_caching_mode = variables.get("query_caching", "in-request-path")
    # Resource limits — defaults match Tanmay/Gautam's spec (4 GB, 2 CPU).
    # Memory limit in bytes is what ReadySet's READYSET_MEMORY_LIMIT expects.
    memory_bytes = variables.get("memory_bytes", 4 * 1024 * 1024 * 1024)  # 4 GiB
    cpus = variables.get("cpus", "2")
    docker_memory = variables.get("docker_memory", "4g")

    # For local deployment, use host.docker.internal to reach the host DB
    if db_host in ("localhost", "127.0.0.1", "::1"):
        docker_db_host = "host.docker.internal"
    else:
        docker_db_host = db_host

    # Build DATABASE_URL (URL-encode user/password to handle special chars)
    safe_user = urlquote(db_user, safe="")
    safe_password = urlquote(password, safe="")
    if engine == "mysql":
        db_type = "mysql"
        db_url = f"mysql://{safe_user}:{safe_password}@{docker_db_host}:{db_port}/{db_name}"
    else:
        db_type = "postgresql"
        db_url = f"postgresql://{safe_user}:{safe_password}@{docker_db_host}:{db_port}/{db_name}"

    # Check Docker is available
    try:
        docker_check = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        if docker_check.returncode != 0:
            return {
                "success": False,
                "error": "Docker is not running. Start Docker and try again.",
            }
    except FileNotFoundError:
        return {
            "success": False,
            "error": (
                "Docker CLI was not found on RDST's PATH.\n"
                "Install Docker, or make the docker CLI available on PATH: "
                "https://docs.docker.com/get-docker/"
            ),
        }

    # Build docker run command. In-request-path defaults: every SELECT through
    # ReadySet auto-caches; SHALLOW_MEMORY_PERCENT=100 + READYSET_MEMORY_LIMIT
    # together enable LRU eviction at the configured cap. Docker --memory and
    # --cpus enforce host-level resource limits (Tanmay/Gautam: 2c/4GB).
    docker_cmd = [
        "docker", "run",
        "-d",
        "--restart=unless-stopped",
        "--name", container_name,
        f"--memory={docker_memory}",
        f"--cpus={cpus}",
        "-e", f"UPSTREAM_DB_URL={db_url}",
        "-e", f"DATABASE_TYPE={db_type}",
        "-e", f"LISTEN_ADDRESS=0.0.0.0:{readyset_port}",
        "-e", f"DEPLOYMENT_MODE=standalone",
        "-e", f"QUERY_CACHING={query_caching_mode}",
        "-e", "QUERY_LOG_MODE=enabled",
        "-e", "PROMETHEUS_METRICS=true",
        "-e", f"CACHE_MODE=shallow",
        "-e", f"SHALLOW_MEMORY_PERCENT=100",
        "-e", f"READYSET_MEMORY_LIMIT={memory_bytes}",
        "-e", "DEFAULT_TTL_MS=600000",
        "-e", f"METRICS_ADDRESS=0.0.0.0:{variables.get('metrics_port', '6034')}",
        "-p", f"{readyset_port}:{readyset_port}",
        "-p", f"{variables.get('metrics_port', '6034')}:{variables.get('metrics_port', '6034')}",
        "--add-host=host.docker.internal:host-gateway",
        image,
    ]

    try:
        print("Pulling and starting Readyset container (this may take a while)...")
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip()
            return {
                "success": False,
                "error": _format_docker_error(error_msg),
            }

        return {
            "success": True,
            "container_name": container_name,
            "promoted": False,
            "created": True,
            "port": readyset_port,
            "db_url": db_url.replace(f":{safe_password}@", ":***@") if password else db_url,
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Container creation timed out (5 min). Check your network connection.",
        }


def deploy_managed_sandbox(
    target_name: str,
    variables: Dict[str, str],
    password: str,
    fingerprint: str,
) -> Dict[str, Any]:
    """Create the manager-owned capacity-one sandbox.

    Unlike the legacy deploy path this has a stable name, explicit ownership
    labels, explicit cache creation, and no Docker restart policy.
    """
    variables = dict(variables)
    variables["container_name"] = MANAGED_SANDBOX_NAME
    variables["query_caching"] = "explicit"
    existing, inspection_error = _inspect_exact_container_checked(
        MANAGED_SANDBOX_NAME
    )
    if inspection_error:
        return {
            "success": False,
            "error": f"Could not inspect managed sandbox: {inspection_error}",
        }
    if existing:
        if existing.get("managed") != "true":
            return {
                "success": False,
                "error": (
                    f"Container '{MANAGED_SANDBOX_NAME}' already exists but is not "
                    "owned by RDST. Rename or remove it before running a speed test."
                ),
            }
        removed = remove_managed_sandbox()
        if not removed.get("success"):
            return removed

    result = _create_container_command(
        variables,
        password,
        extra_args=[
            "--label",
            f"{MANAGED_SANDBOX_LABEL}=true",
            "--label",
            f"io.readyset.rdst.target={target_name}",
            "--label",
            f"io.readyset.rdst.fingerprint={fingerprint}",
        ],
        restart_policy=False,
    )
    if result.get("success"):
        result["target"] = target_name
        result["fingerprint"] = fingerprint
    return result


def inspect_managed_sandbox() -> Optional[Dict[str, Any]]:
    """Return labeled sandbox identity, or None for absent/foreign containers."""
    result, inspection_error = _inspect_exact_container_checked(
        MANAGED_SANDBOX_NAME
    )
    if inspection_error:
        raise RuntimeError(f"Could not inspect managed sandbox: {inspection_error}")
    if not result or result.get("managed") != "true":
        return None
    return result


def managed_sandbox_running() -> bool:
    result = inspect_managed_sandbox()
    return bool(result and result.get("running"))


def remove_managed_sandbox() -> Dict[str, Any]:
    """Remove only the exact, labeled RDST sandbox."""
    existing, inspection_error = _inspect_exact_container_checked(
        MANAGED_SANDBOX_NAME
    )
    if inspection_error:
        return {
            "success": False,
            "removed": False,
            "error": f"Could not inspect managed sandbox: {inspection_error}",
        }
    if not existing:
        return {"success": True, "removed": False}
    if existing.get("managed") != "true":
        return {
            "success": False,
            "error": (
                f"Refusing to remove foreign container '{MANAGED_SANDBOX_NAME}'."
            ),
        }
    try:
        result = subprocess.run(
            ["docker", "rm", "-f", MANAGED_SANDBOX_NAME],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return {"success": False, "error": f"Failed to remove sandbox: {exc}"}
    if result.returncode != 0:
        return {
            "success": False,
            "error": result.stderr.strip() or "docker rm failed",
        }
    return {"success": True, "removed": True}


def remove_configured_legacy_container(name: str) -> Dict[str, Any]:
    """Remove one exact legacy container already referenced by RDST config."""
    if (
        not name.startswith("rdst-readyset-")
        or name == MANAGED_SANDBOX_NAME
        or "/" in name
    ):
        return {"success": False, "error": "Not an RDST legacy container name"}
    state = _docker_inspect_state(name)
    if state is None:
        runtime = docker_runtime_status()
        if not runtime.get("running"):
            return {
                "success": False,
                "removed": False,
                "error": (
                    "Docker is unavailable, so RDST could not verify or remove "
                    f"legacy container '{name}'."
                ),
            }
        return {"success": True, "removed": False}
    try:
        result = subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return {"success": False, "error": f"Failed to remove {name}: {exc}"}
    if result.returncode != 0:
        return {
            "success": False,
            "error": result.stderr.strip() or f"docker rm {name} failed",
        }
    return {"success": True, "removed": True}


def _inspect_exact_container(name: str) -> Optional[Dict[str, Any]]:
    value, _error = _inspect_exact_container_checked(name)
    return value


def _inspect_exact_container_checked(
    name: str,
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                (
                    "{{.State.Running}}|"
                    f'{{{{index .Config.Labels "{MANAGED_SANDBOX_LABEL}"}}}}|'
                    '{{index .Config.Labels "io.readyset.rdst.target"}}|'
                    '{{index .Config.Labels "io.readyset.rdst.fingerprint"}}'
                ),
                name,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return None, "docker inspect timed out"
    except FileNotFoundError:
        return None, "Docker CLI was not found"
    except OSError as exc:
        return None, str(exc)
    if result.returncode != 0:
        error = result.stderr.strip() or "docker inspect failed"
        lowered = error.lower()
        if "no such object" in lowered or "no such container" in lowered:
            return None, None
        return None, error
    parts = result.stdout.strip().split("|")
    if len(parts) != 4:
        return None, "docker inspect returned an invalid response"
    return {
        "running": parts[0].lower() == "true",
        "managed": parts[1],
        "target": parts[2],
        "fingerprint": parts[3],
    }, None


def _create_container_command(
    variables: Dict[str, str],
    password: str,
    *,
    extra_args: list[str] | None = None,
    restart_policy: bool,
) -> Dict[str, Any]:
    """Create a Readyset container with an explicit lifecycle policy."""
    engine = variables["db_engine"]
    db_host = variables["db_host"]
    docker_db_host = (
        "host.docker.internal"
        if db_host in ("localhost", "127.0.0.1", "::1")
        else db_host
    )
    safe_user = urlquote(variables["db_user"], safe="")
    safe_password = urlquote(password, safe="")
    db_type = "mysql" if engine == "mysql" else "postgresql"
    db_url = (
        f"{db_type}://{safe_user}:{safe_password}@{docker_db_host}:"
        f"{variables['db_port']}/{variables['db_name']}"
    )
    readyset_port = variables["readyset_port"]
    metrics_port = variables.get("metrics_port", "6034")
    command = ["docker", "run", "-d"]
    if restart_policy:
        command.append("--restart=unless-stopped")
    command.extend(
        [
            "--name",
            variables["container_name"],
            f"--memory={variables.get('docker_memory', '4g')}",
            f"--cpus={variables.get('cpus', '2')}",
        ]
    )
    command.extend(extra_args or [])
    command.extend(
        [
            "-e",
            f"UPSTREAM_DB_URL={db_url}",
            "-e",
            f"DATABASE_TYPE={db_type}",
            "-e",
            f"LISTEN_ADDRESS=0.0.0.0:{readyset_port}",
            "-e",
            "DEPLOYMENT_MODE=standalone",
            "-e",
            f"QUERY_CACHING={variables.get('query_caching', 'explicit')}",
            "-e",
            "QUERY_LOG_MODE=enabled",
            "-e",
            "PROMETHEUS_METRICS=true",
            "-e",
            "CACHE_MODE=shallow",
            "-e",
            "SHALLOW_MEMORY_PERCENT=100",
            "-e",
            f"READYSET_MEMORY_LIMIT={variables.get('memory_bytes', 4 * 1024 * 1024 * 1024)}",
            "-e",
            "DEFAULT_TTL_MS=600000",
            "-e",
            f"METRICS_ADDRESS=0.0.0.0:{metrics_port}",
            "-p",
            f"{readyset_port}:{readyset_port}",
            "-p",
            f"{metrics_port}:{metrics_port}",
            "--add-host=host.docker.internal:host-gateway",
            variables["readyset_image"],
        ]
    )
    try:
        docker_check = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5
        )
        if docker_check.returncode != 0:
            return {
                "success": False,
                "error": "Docker is not running. Start Docker and try again.",
            }
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=300
        )
    except FileNotFoundError:
        return {"success": False, "error": "Docker CLI was not found on RDST's PATH."}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Container creation timed out (5 min)."}
    if result.returncode != 0:
        return {"success": False, "error": _format_docker_error(result.stderr.strip())}
    return {
        "success": True,
        "container_name": variables["container_name"],
        "created": True,
        "port": readyset_port,
    }


def _container_name(target_name: str) -> str:
    return f"rdst-readyset-{target_name}"


def _docker_inspect_state(name: str) -> Optional[Dict[str, Any]]:
    """Return docker inspect state for a container, or None if it doesn't exist.

    Result keys: status (running|exited|created|paused|restarting|...), running (bool),
    started_at, exit_code.
    """
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}|{{.State.Running}}|{{.State.ExitCode}}", name],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    parts = result.stdout.strip().split("|")
    if len(parts) < 3:
        return None
    return {
        "status": parts[0],
        "running": parts[1].lower() == "true",
        "exit_code": parts[2],
    }


def _docker_inspect_port(name: str, container_port: int) -> Optional[int]:
    """Look up the host port mapped to a container port, or None."""
    try:
        result = subprocess.run(
            ["docker", "port", name, f"{container_port}/tcp"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    # Output like "0.0.0.0:5433" — take the part after the last colon
    line = result.stdout.strip().splitlines()[0]
    try:
        return int(line.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        return None


def _docker_list_tcp_ports(name: str) -> list[int]:
    """List all TCP host ports mapped from a container.

    `docker port <name>` returns lines like:
        5436/tcp -> 0.0.0.0:5436
        6037/tcp -> 0.0.0.0:6037

    We exclude well-known metrics-ish ports (6033-6099) and return SQL-listener
    candidates. Falls back to empty list on failure.
    """
    try:
        result = subprocess.run(
            ["docker", "port", name],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    ports: list[int] = []
    for line in result.stdout.strip().splitlines():
        # "<container_port>/tcp -> <host>:<host_port>"
        parts = line.split("->")
        if len(parts) != 2:
            continue
        container_part = parts[0].strip()
        host_part = parts[1].strip()
        if "/tcp" not in container_part:
            continue
        try:
            container_port = int(container_part.split("/")[0])
            host_port = int(host_part.rsplit(":", 1)[-1])
        except (ValueError, IndexError):
            continue
        # Skip metrics/prometheus range — keep SQL listener candidates only
        if 6033 <= container_port <= 6099:
            continue
        ports.append(host_port)
    return ports


def _tcp_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def probe_local_docker(target_name: str) -> "ProbeResult":
    """Detect current state of the cache container for this target."""
    from .lifecycle import ProbeResult, ProbeState
    name = _container_name(target_name)
    state = _docker_inspect_state(name)
    if state is None:
        return ProbeResult(state=ProbeState.NOT_DEPLOYED, mode="docker", container_id=name,
                           detail="No container found")
    if not state["running"]:
        return ProbeResult(state=ProbeState.DEPLOYED_STOPPED, mode="docker", container_id=name,
                           detail=f"Container exists, status={state['status']}, exit_code={state['exit_code']}")

    # Running per docker — that's the authoritative signal. TCP probe is best-effort
    # and runs across ALL exposed SQL ports (Readyset binds to varying ports per
    # target, e.g. 5433/5434/5435/5436/3307/3308 etc., not a fixed pair). A TCP
    # failure does not flip us to UNREACHABLE — Readyset can be mid-startup, behind
    # a slow `docker run`, or use a custom port the user picked.
    sql_ports = _docker_list_tcp_ports(name)
    reachable_port = next((p for p in sql_ports if _tcp_reachable("127.0.0.1", p, timeout=1.0)), None)
    if reachable_port:
        return ProbeResult(state=ProbeState.DEPLOYED_RUNNING, mode="docker", container_id=name,
                           detail=f"Container running, port {reachable_port} reachable")
    return ProbeResult(state=ProbeState.DEPLOYED_RUNNING, mode="docker", container_id=name,
                       detail=f"Container running ({len(sql_ports)} SQL port(s) exposed; TCP probe inconclusive)")


def start_local_docker(target_name: str) -> "LifecycleResult":
    """Start a stopped container (docker start)."""
    from .lifecycle import LifecycleResult, ProbeState
    name = _container_name(target_name)
    state = _docker_inspect_state(name)
    if state is None:
        return LifecycleResult(
            success=False,
            error=f"No container '{name}' to start. Use 'rdst cache deploy' to create one.",
        )
    if state["running"]:
        return LifecycleResult(
            success=True, state_after=ProbeState.DEPLOYED_RUNNING,
            detail=f"Container '{name}' is already running",
        )
    try:
        result = subprocess.run(
            ["docker", "start", name], capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return LifecycleResult(success=False, error=f"Failed to start: {e}")
    if result.returncode != 0:
        return LifecycleResult(success=False, error=result.stderr.strip() or "docker start failed")
    return LifecycleResult(
        success=True, state_after=ProbeState.DEPLOYED_RUNNING,
        detail=f"Started container '{name}'",
    )


def stop_local_docker(target_name: str) -> "LifecycleResult":
    """Stop a running container without removing it (docker stop)."""
    from .lifecycle import LifecycleResult, ProbeState
    name = _container_name(target_name)
    state = _docker_inspect_state(name)
    if state is None:
        return LifecycleResult(
            success=False,
            error=f"No container '{name}' found.",
        )
    if not state["running"]:
        return LifecycleResult(
            success=True, state_after=ProbeState.DEPLOYED_STOPPED,
            detail=f"Container '{name}' is already stopped",
        )
    try:
        result = subprocess.run(
            ["docker", "stop", name], capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return LifecycleResult(success=False, error=f"Failed to stop: {e}")
    if result.returncode != 0:
        return LifecycleResult(success=False, error=result.stderr.strip() or "docker stop failed")
    return LifecycleResult(
        success=True, state_after=ProbeState.DEPLOYED_STOPPED,
        detail=f"Stopped container '{name}'",
    )


def restart_local_docker(target_name: str) -> "LifecycleResult":
    """Restart a container (docker restart)."""
    from .lifecycle import LifecycleResult, ProbeState
    name = _container_name(target_name)
    state = _docker_inspect_state(name)
    if state is None:
        return LifecycleResult(
            success=False,
            error=f"No container '{name}' found. Use 'rdst cache deploy' to create one.",
        )
    try:
        result = subprocess.run(
            ["docker", "restart", name], capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return LifecycleResult(success=False, error=f"Failed to restart: {e}")
    if result.returncode != 0:
        return LifecycleResult(success=False, error=result.stderr.strip() or "docker restart failed")
    return LifecycleResult(
        success=True, state_after=ProbeState.DEPLOYED_RUNNING,
        detail=f"Restarted container '{name}'",
    )


def _format_docker_error(error_msg: str) -> str:
    """Format Docker errors with remediation suggestions."""
    lower = error_msg.lower()

    if "no basic auth" in lower or "unauthorized" in lower or "authentication required" in lower:
        return (
            f"Docker authentication error.\n\n"
            f"If using a private registry, authenticate first:\n"
            f"  docker login <registry>\n\n"
            f"Original error: {error_msg}"
        )

    if "port is already allocated" in lower or "bind: address already in use" in lower:
        return (
            f"Port already in use.\n\n"
            f"Try a different port with --port <number>\n\n"
            f"Original error: {error_msg}"
        )

    if "no such image" in lower or "unable to find image" in lower:
        return (
            f"Readyset image not found.\n\n"
            f"Check your network connection and try again.\n\n"
            f"Original error: {error_msg}"
        )

    if "name is already in use" in lower:
        return (
            f"A container with that name already exists.\n\n"
            f"Remove it first: docker rm -f <container_name>\n\n"
            f"Original error: {error_msg}"
        )

    return f"Docker error: {error_msg}"

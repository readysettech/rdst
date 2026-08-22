"""Local Docker deployment — promote existing container or create new one."""

from __future__ import annotations

import re
import shutil
import socket
import subprocess  # nosec B404  # nosemgrep: gitlab.bandit.B404
import time
import uuid
from typing import Any, Dict, Optional
from urllib.parse import quote as urlquote

from shared.deploy.docker_topology import (
    ContainerNetworkPlan,
    DockerTopology,
    DockerTopologyError,
)

MANAGED_SANDBOX_NAME = "rdst-readyset-sandbox"
MANAGED_SANDBOX_LABEL = "io.readyset.rdst.sandbox"
MANAGED_SANDBOX_INSTANCE_LABEL = "io.readyset.rdst.instance"
MANAGED_SANDBOX_PORT_ATTEMPTS = 3
MANAGED_CREATE_RECONCILE_ATTEMPTS = 60
MANAGED_CREATE_RECONCILE_INTERVAL_SECONDS = 0.5

_PORT_CONFLICT_MARKERS = (
    "port already in use",
    "port is already allocated",
    "address already in use",
    "ports are not available",
    "only one usage of each socket address",
)

# A daemon started with user-namespace remapping rejects host networking at
# container-create time with this message.
_HOST_NETWORK_REFUSAL_MARKER = (
    "network namespace when user namespaces are enabled"
)


def publish_bind() -> str:
    """Host-side interface for `docker run -p`. A local daemon publishes on
    loopback: the Readyset proxy and its unauthenticated metrics endpoint are for
    this machine only. A remote daemon is reached by address, so its ports stay on
    the daemon's interfaces."""
    return "" if DockerTopology.from_environment().remote else "127.0.0.1:"


def _container_network_args(
    network: ContainerNetworkPlan,
    readyset_port: str | int,
    metrics_port: str | int | None,
) -> list[str]:
    if network.host_network:
        return ["--network=host"]
    args: list[str] = []
    if network.docker_network:
        args.extend(["--network", network.docker_network])
    args.extend([
        "-p",
        f"{publish_bind()}{readyset_port}:{readyset_port}",
    ])
    if metrics_port is not None:
        args.extend(
            ["-p", f"{publish_bind()}{metrics_port}:{metrics_port}"]
        )
    args.append("--add-host=host.docker.internal:host-gateway")
    return args


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


def _local_tcp_port_free(port: int) -> bool:
    """Return whether a local Docker daemon can claim this host TCP port."""
    for family, address in (
        (socket.AF_INET, ("127.0.0.1", port)),
        (socket.AF_INET6, ("::1", port)),
    ):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.05)
                if probe.connect_ex(address) == 0:
                    return False
        except OSError:
            pass

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as ipv4:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            ipv4.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            ipv4.bind(("", port))
        except OSError:
            return False

    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as ipv6:
            ipv6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                ipv6.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                ipv6.bind(("::", port))
            except OSError:
                return False
    except OSError:
        # Some hosts have no IPv6 support. The IPv4 probe is sufficient there.
        pass
    return True


def _docker_published_ports() -> set[int]:
    """Return host TCP ports published by running containers."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Ports}}"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode != 0:
        return set()
    # Covers loopback/wildcard/IPv6 addresses and collapsed Docker port ranges.
    ports: set[int] = set()
    for start, end, protocol in re.findall(
        r":(\d+)(?:-(\d+))?->\d+(?:-\d+)?/(tcp|udp)",
        result.stdout,
    ):
        if protocol != "tcp":
            continue
        ports.update(range(int(start), int(end or start) + 1))
    return ports


def _next_managed_sandbox_port(
    base: int,
    taken: set[int],
    *,
    probe_local_host: bool,
) -> int:
    port = base
    while port <= 65535 and (
        port in taken or (probe_local_host and not _local_tcp_port_free(port))
    ):
        port += 1
    if port > 65535:
        raise RuntimeError(
            f"No free TCP port is available at or above {base}. "
            "Stop a local listener or container, then retry."
        )
    taken.add(port)
    return port


def _allocate_managed_sandbox_ports(
    variables: Dict[str, Any],
    *,
    exclude: set[int] | None = None,
) -> Dict[str, Any]:
    """Choose a non-conflicting SQL port for the one local sandbox."""
    topology = DockerTopology.from_environment()
    taken = _docker_published_ports()
    taken.update(exclude or ())
    allocated = dict(variables)
    allocated["readyset_port"] = str(
        _next_managed_sandbox_port(
            int(variables["readyset_port"]),
            taken,
            probe_local_host=not topology.remote,
        )
    )
    return allocated


def _is_port_conflict(error: str) -> bool:
    lowered = error.lower()
    return any(marker in lowered for marker in _PORT_CONFLICT_MARKERS)


def _is_host_network_refusal(error: str) -> bool:
    return _HOST_NETWORK_REFUSAL_MARKER in error.lower()


def _network_plans(
    topology: DockerTopology, db_host: str
) -> list[ContainerNetworkPlan]:
    """Return the preferred container network plan, then any fallback.

    Host networking is the only way a container on a native Linux daemon reaches
    an upstream bound to the client's loopback, but a userns-remapped daemon
    refuses it. Bridge networking with published listeners and a host-gateway
    mapping is the next best plan there, so callers retry with it when the
    daemon rejects the host namespace.
    """
    plan = topology.container_network_for(db_host)
    if not plan.host_network:
        return [plan]
    return [plan, topology.bridge_network_for(db_host)]


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

    try:
        plans = _network_plans(DockerTopology.from_environment(), db_host)
    except DockerTopologyError as exc:
        return {"success": False, "error": str(exc)}

    # Build DATABASE_URL (URL-encode user/password to handle special chars)
    safe_user = urlquote(db_user, safe="")
    safe_password = urlquote(password, safe="")
    db_type = "mysql" if engine == "mysql" else "postgresql"

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
    metrics_port = variables.get("metrics_port", "6034")
    print("Pulling and starting Readyset container (this may take a while)...")
    for network in plans:
        db_url = (
            f"{db_type}://{safe_user}:{safe_password}@"
            f"{network.upstream_host}:{db_port}/{db_name}"
        )
        docker_cmd = [
            "docker", "run",
            "-d",
            "--restart=unless-stopped",
            "--name", container_name,
            f"--memory={docker_memory}",
            f"--cpus={cpus}",
            *_container_network_args(network, readyset_port, metrics_port),
            "-e", f"UPSTREAM_DB_URL={db_url}",
            "-e", f"DATABASE_TYPE={db_type}",
            "-e", f"LISTEN_ADDRESS={network.listen_host}:{readyset_port}",
            "-e", f"DEPLOYMENT_MODE=standalone",
            "-e", f"QUERY_CACHING={query_caching_mode}",
            "-e", "QUERY_LOG_MODE=enabled",
            "-e", "PROMETHEUS_METRICS=true",
            "-e", f"CACHE_MODE=shallow",
            "-e", f"SHALLOW_MEMORY_PERCENT=100",
            "-e", f"READYSET_MEMORY_LIMIT={memory_bytes}",
            "-e", "DEFAULT_TTL_MS=600000",
            "-e", f"METRICS_ADDRESS={network.listen_host}:{metrics_port}",
            image,
        ]

        try:
            result = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Container creation timed out (5 min). Check your network connection.",
            }

        if result.returncode != 0:
            error_msg = result.stderr.strip()
            if network.host_network and _is_host_network_refusal(error_msg):
                continue
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

    raise AssertionError("container network fallback loop did not return")


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
    instance_id = uuid.uuid4().hex
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

    excluded_ports: set[int] = set()
    for attempt in range(MANAGED_SANDBOX_PORT_ATTEMPTS):
        try:
            candidate = _allocate_managed_sandbox_ports(
                variables,
                exclude=excluded_ports,
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            return {"success": False, "error": str(exc)}

        result = _create_container_command(
            candidate,
            password,
            extra_args=[
                "--label",
                f"{MANAGED_SANDBOX_LABEL}=true",
                "--label",
                f"io.readyset.rdst.target={target_name}",
                "--label",
                f"io.readyset.rdst.fingerprint={fingerprint}",
                "--label",
                f"{MANAGED_SANDBOX_INSTANCE_LABEL}={instance_id}",
            ],
            restart_policy=False,
        )
        if result.get("success"):
            result["target"] = target_name
            result["fingerprint"] = fingerprint
            result["instance_id"] = instance_id
            return result

        error = str(result.get("error") or "")
        if not _is_port_conflict(error):
            if result.get("resource_may_exist"):
                removed = remove_managed_sandbox()
                if not removed.get("success"):
                    return removed
            return result
        if attempt + 1 == MANAGED_SANDBOX_PORT_ATTEMPTS:
            removed = remove_managed_sandbox()
            if not removed.get("success"):
                return removed
            return {
                "success": False,
                "error": (
                    "Readyset could not claim a free SQL port after "
                    f"{MANAGED_SANDBOX_PORT_ATTEMPTS} attempts. Stop conflicting "
                    "local listeners or containers, then retry."
                ),
            }

        excluded_ports.add(int(candidate["readyset_port"]))
        removed = remove_managed_sandbox()
        if not removed.get("success"):
            return removed

    raise AssertionError("managed sandbox port retry loop did not return")


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


def image_present(image: str) -> bool:
    """True when the image already exists locally."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def pull_image(image: str, timeout_seconds: int = 1800) -> Dict[str, Any]:
    """Pull an image ahead of container start.

    First-run pulls of the Readyset image are gigabyte-scale and must never
    race a container readiness timeout; callers surface a downloading state
    to the user while this runs.
    """
    try:
        result = subprocess.run(
            ["docker", "pull", image],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "docker pull failed").strip()
            return {"success": False, "error": detail[-400:]}
        return {"success": True}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Downloading {image} timed out"}
    except FileNotFoundError:
        return {"success": False, "error": "Docker CLI was not found on RDST's PATH."}


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
    container_id = str(existing.get("id") or "")
    if not container_id:
        return {
            "success": False,
            "removed": False,
            "error": "Docker inspection did not return the sandbox container ID.",
        }
    try:
        result = subprocess.run(
            ["docker", "rm", "-f", container_id],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return _confirm_managed_container_removed(container_id)
    except (FileNotFoundError, OSError) as exc:
        return {
            "success": False,
            "removed": False,
            "error": f"Failed to remove sandbox: {exc}",
        }
    if result.returncode != 0:
        error = result.stderr.strip() or "docker rm failed"
        if _is_missing_container_error(error):
            return _confirm_managed_container_removed(container_id)
        return {
            "success": False,
            "removed": False,
            "error": error,
        }
    return _confirm_managed_container_removed(container_id)


def _confirm_managed_container_removed(container_id: str) -> Dict[str, Any]:
    current, inspection_error = _inspect_exact_container_checked(
        MANAGED_SANDBOX_NAME
    )
    if inspection_error:
        return {
            "success": False,
            "removed": False,
            "error": (
                "Docker did not confirm sandbox removal: "
                f"{inspection_error}"
            ),
        }
    if current and current.get("id") == container_id:
        return {
            "success": False,
            "removed": False,
            "error": "Docker did not remove the expected sandbox container.",
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
    state, inspection_error = _docker_inspect_state_checked(name)
    if inspection_error:
        return {
            "success": False,
            "removed": False,
            "error": (
                f"Could not verify legacy container '{name}': "
                f"{inspection_error}"
            ),
        }
    if state is None:
        return {"success": True, "removed": False}
    container_id = str(state.get("id") or "")
    if not container_id:
        return {
            "success": False,
            "removed": False,
            "error": f"Docker returned no identity for legacy container '{name}'.",
        }
    try:
        result = subprocess.run(
            ["docker", "rm", "-f", container_id],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return _confirm_legacy_container_removed(name, container_id)
    except (FileNotFoundError, OSError) as exc:
        return {"success": False, "error": f"Failed to remove {name}: {exc}"}
    if result.returncode != 0:
        error = result.stderr.strip() or f"docker rm {name} failed"
        if _is_missing_container_error(error):
            return _confirm_legacy_container_removed(name, container_id)
        return {
            "success": False,
            "error": error,
        }
    return _confirm_legacy_container_removed(name, container_id)


def _confirm_legacy_container_removed(
    name: str, container_id: str
) -> Dict[str, Any]:
    current, inspection_error = _docker_inspect_state_checked(name)
    if inspection_error:
        return {
            "success": False,
            "removed": False,
            "error": f"Docker did not confirm removal of '{name}': {inspection_error}",
        }
    if current and current.get("id") == container_id:
        return {
            "success": False,
            "removed": False,
            "error": f"Docker did not remove legacy container '{name}'.",
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
                    "{{.Id}}|{{.State.Status}}|{{.State.Running}}|"
                    "{{.State.ExitCode}}|{{.State.OOMKilled}}|"
                    f'{{{{index .Config.Labels "{MANAGED_SANDBOX_LABEL}"}}}}|'
                    '{{index .Config.Labels "io.readyset.rdst.target"}}|'
                    '{{index .Config.Labels "io.readyset.rdst.fingerprint"}}|'
                    f'{{{{index .Config.Labels "{MANAGED_SANDBOX_INSTANCE_LABEL}"}}}}'
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
        if _is_missing_container_error(lowered):
            return None, None
        return None, error
    parts = result.stdout.strip().split("|")
    if len(parts) != 9:
        return None, "docker inspect returned an invalid response"
    return {
        "id": parts[0],
        "status": parts[1],
        "running": parts[2].lower() == "true",
        "exit_code": parts[3],
        "oom_killed": parts[4].lower() == "true",
        "managed": parts[5],
        "target": parts[6],
        "fingerprint": parts[7],
        "instance_id": parts[8],
    }, None


def managed_sandbox_startup_failure() -> Dict[str, str]:
    """Classify why the managed sandbox stopped without exposing its logs."""
    existing, inspection_error = _inspect_exact_container_checked(
        MANAGED_SANDBOX_NAME
    )
    if inspection_error:
        return {"kind": "inspection", "error": inspection_error}
    if not existing:
        return {"kind": "missing", "error": "sandbox container disappeared"}
    if existing.get("running"):
        return {"kind": "running", "error": "sandbox container is running"}

    logs = ""
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", "40", str(existing["id"])],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            logs = result.stdout + "\n" + result.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    if existing.get("oom_killed") or str(existing.get("exit_code")) == "137":
        return {
            "kind": "oom",
            "error": "Readyset exceeded the sandbox memory limit during startup",
        }
    if _is_port_conflict(logs):
        return {
            "kind": "port_conflict",
            "error": "Readyset could not bind the selected SQL port",
        }
    status = str(existing.get("status") or "stopped")
    exit_code = str(existing.get("exit_code") or "unknown")
    return {
        "kind": "exited",
        "error": (
            f"Readyset stopped during startup with status {status} "
            f"and exit code {exit_code}"
        ),
    }


def _is_missing_container_error(error: str) -> bool:
    lowered = error.lower()
    return "no such object" in lowered or "no such container" in lowered


def _managed_create_command(
    variables: Dict[str, str],
    password: str,
    network: ContainerNetworkPlan,
    *,
    extra_args: list[str] | None,
    restart_policy: bool,
) -> list[str]:
    """Build the `docker create` argv for one container network plan."""
    safe_user = urlquote(variables["db_user"], safe="")
    safe_password = urlquote(password, safe="")
    db_type = "mysql" if variables["db_engine"] == "mysql" else "postgresql"
    db_url = (
        f"{db_type}://{safe_user}:{safe_password}@{network.upstream_host}:"
        f"{variables['db_port']}/{variables['db_name']}"
    )
    readyset_port = variables["readyset_port"]
    command = ["docker", "create", "--pull=never"]
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
    command.extend(_container_network_args(network, readyset_port, None))
    command.extend(
        [
            "-e",
            f"UPSTREAM_DB_URL={db_url}",
            "-e",
            f"DATABASE_TYPE={db_type}",
            "-e",
            f"LISTEN_ADDRESS={network.listen_host}:{readyset_port}",
            "-e",
            "DEPLOYMENT_MODE=standalone",
            "-e",
            f"QUERY_CACHING={variables.get('query_caching', 'explicit')}",
            "-e",
            "QUERY_LOG_MODE=enabled",
            "-e",
            "PROMETHEUS_METRICS=false",
            "-e",
            "CACHE_MODE=shallow",
            "-e",
            "SHALLOW_MEMORY_PERCENT=80",
            "-e",
            f"READYSET_MEMORY_LIMIT={variables.get('memory_bytes', 4 * 1024 * 1024 * 1024)}",
            "-e",
            "DEFAULT_TTL_MS=600000",
            variables["readyset_image"],
        ]
    )
    return command


def _create_container_command(
    variables: Dict[str, str],
    password: str,
    *,
    extra_args: list[str] | None = None,
    restart_policy: bool,
) -> Dict[str, Any]:
    """Create a Readyset container with an explicit lifecycle policy."""
    try:
        plans = _network_plans(
            DockerTopology.from_environment(), variables["db_host"]
        )
    except DockerTopologyError as exc:
        return {"success": False, "error": str(exc)}
    readyset_port = variables["readyset_port"]
    try:
        docker_check = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5
        )
    except FileNotFoundError:
        return {"success": False, "error": "Docker CLI was not found on RDST's PATH."}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Docker daemon status check timed out."}
    if docker_check.returncode != 0:
        return {
            "success": False,
            "error": "Docker is not running. Start Docker and try again.",
        }
    try:
        image_check = subprocess.run(
            ["docker", "image", "inspect", variables["readyset_image"]],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Docker image inspection timed out before container creation.",
        }
    except (FileNotFoundError, OSError) as exc:
        return {"success": False, "error": f"Could not inspect Readyset image: {exc}"}
    if image_check.returncode != 0:
        try:
            pull_result = subprocess.run(
                ["docker", "pull", variables["readyset_image"]],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Readyset image download timed out (5 min).",
            }
        except (FileNotFoundError, OSError) as exc:
            return {
                "success": False,
                "error": f"Could not download Readyset image: {exc}",
            }
        if pull_result.returncode != 0:
            return {
                "success": False,
                "error": _format_docker_error(pull_result.stderr.strip()),
            }
    for network in plans:
        command = _managed_create_command(
            variables,
            password,
            network,
            extra_args=extra_args,
            restart_policy=restart_policy,
        )
        try:
            create_result = subprocess.run(
                command, capture_output=True, text=True, timeout=300
            )
        except FileNotFoundError:
            return {
                "success": False,
                "error": "Docker CLI was not found on RDST's PATH.",
            }
        except subprocess.TimeoutExpired:
            cleanup = _reconcile_ambiguous_managed_create()
            if not cleanup.get("success"):
                return cleanup
            return {"success": False, "error": "Container creation timed out (5 min)."}
        if create_result.returncode == 0:
            break
        error = create_result.stderr.strip()
        if network.host_network and _is_host_network_refusal(error):
            continue
        return {"success": False, "error": _format_docker_error(error)}
    else:
        raise AssertionError("container network fallback loop did not return")
    container_id = create_result.stdout.strip()
    if not container_id:
        return {
            "success": False,
            "error": "Docker create returned no container identity.",
            "resource_may_exist": True,
        }
    try:
        start_result = subprocess.run(
            ["docker", "start", container_id],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Container startup timed out after Docker created it.",
            "resource_may_exist": True,
        }
    except (FileNotFoundError, OSError) as exc:
        return {
            "success": False,
            "error": f"Could not start container: {exc}",
            "resource_may_exist": True,
        }
    if start_result.returncode != 0:
        return {
            "success": False,
            "error": _format_docker_error(start_result.stderr.strip()),
            "resource_may_exist": True,
        }
    return {
        "success": True,
        "container_name": variables["container_name"],
        "container_id": container_id,
        "created": True,
        "port": readyset_port,
    }


def _reconcile_ambiguous_managed_create() -> Dict[str, Any]:
    """Remove a managed container that appeared after a timed-out create."""
    for attempt in range(MANAGED_CREATE_RECONCILE_ATTEMPTS):
        current, inspection_error = _inspect_exact_container_checked(
            MANAGED_SANDBOX_NAME
        )
        if inspection_error:
            return {
                "success": False,
                "removed": False,
                "error": (
                    "Container creation timed out and Docker could not confirm "
                    f"the final state: {inspection_error}"
                ),
            }
        if current is not None:
            if current.get("managed") != "true":
                return {
                    "success": False,
                    "removed": False,
                    "error": (
                        "Container creation timed out and the sandbox name is "
                        "now owned by a foreign container."
                    ),
                }
            return remove_managed_sandbox()
        if attempt + 1 < MANAGED_CREATE_RECONCILE_ATTEMPTS:
            time.sleep(MANAGED_CREATE_RECONCILE_INTERVAL_SECONDS)
    return {"success": True, "removed": False}


def _container_name(target_name: str) -> str:
    return f"rdst-readyset-{target_name}"


def _docker_inspect_state(name: str) -> Optional[Dict[str, Any]]:
    """Return docker inspect state for a container, or None if it doesn't exist.

    Result keys: status (running|exited|created|paused|restarting|...), running (bool),
    started_at, exit_code.
    """
    state, _error = _docker_inspect_state_checked(name)
    return state


def _docker_inspect_state_checked(
    name: str,
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Inspect state while distinguishing absence from Docker failures."""
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Id}}|{{.State.Status}}|{{.State.Running}}|{{.State.ExitCode}}",
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
        return None, "docker inspect returned an invalid state response"
    return (
        {
            "id": parts[0],
            "status": parts[1],
            "running": parts[2].lower() == "true",
            "exit_code": parts[3],
        },
        None,
    )


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
    published_host = DockerTopology.from_environment().published_host
    reachable_port = next(
        (
            port
            for port in sql_ports
            if _tcp_reachable(published_host, port, timeout=1.0)
        ),
        None,
    )
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

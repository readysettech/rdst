"""Local Docker deployment — promote existing container or create new one.

Reuses patterns from lib/functions/readyset_container.py.
"""

from __future__ import annotations

import subprocess  # nosec B404  # nosemgrep: gitlab.bandit.B404
from typing import Any, Dict, Optional
from urllib.parse import quote as urlquote


def deploy_local_docker(
    target_name: str,
    variables: Dict[str, str],
    password: str,
) -> Dict[str, Any]:
    """Deploy ReadySet locally via Docker.

    Two paths:
    1. Container exists from prior analyze --readyset-cache → promote to permanent
    2. No container → create new with persistent flags
    """
    # Check for existing container (both naming conventions)
    existing = _find_existing_container(target_name)

    if existing:
        return _promote_container(existing)

    # No existing container — create new
    return _create_container(target_name, variables, password)


def _find_existing_container(target_name: str) -> Optional[Dict[str, Any]]:
    """Check for existing ReadySet container for this target."""
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
    """Create a new persistent ReadySet container."""
    engine = variables["db_engine"]
    db_host = variables["db_host"]
    db_port = variables["db_port"]
    db_user = variables["db_user"]
    db_name = variables["db_name"]
    readyset_port = variables["readyset_port"]
    container_name = variables["container_name"]
    image = variables["readyset_image"]

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
                "Docker is not installed.\n"
                "Install Docker: https://docs.docker.com/get-docker/"
            ),
        }

    # Build docker run command
    docker_cmd = [
        "docker", "run",
        "-d",
        "--restart=unless-stopped",
        "--name", container_name,
        "-e", f"UPSTREAM_DB_URL={db_url}",
        "-e", f"DATABASE_TYPE={db_type}",
        "-e", f"LISTEN_ADDRESS=0.0.0.0:{readyset_port}",
        "-e", f"DEPLOYMENT_MODE=standalone",
        "-e", f"QUERY_CACHING=explicit",
        "-e", f"CACHE_MODE=shallow",
        "-p", f"{readyset_port}:{readyset_port}",
        "--add-host=host.docker.internal:host-gateway",
        image,
    ]

    try:
        print("Pulling and starting ReadySet container (this may take a while)...")
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
            f"ReadySet image not found.\n\n"
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

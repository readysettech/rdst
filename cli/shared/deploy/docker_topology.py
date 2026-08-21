"""Docker client/daemon network topology."""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

_TRUE_VALUES = {"1", "true", "yes", "on"}


class DockerTopologyError(RuntimeError):
    """The Docker daemon cannot route an address required by RDST."""


def _is_local_host(host: str) -> bool:
    """Recognize DNS and IP spellings that refer to this machine."""
    normalized = host.strip().lower().rstrip(".")
    if normalized == "localhost":
        return True
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


@dataclass(frozen=True)
class ContainerNetworkPlan:
    """How a container reaches its upstream and exposes local listeners."""

    upstream_host: str
    host_network: bool
    docker_network: str | None = None

    @property
    def listen_host(self) -> str:
        return "127.0.0.1" if self.host_network else "0.0.0.0"


@lru_cache(maxsize=8)
def _context_endpoint(context: str) -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise DockerTopologyError(
            f"Docker context '{context}' is active, but the Docker CLI was not found"
        )
    result = subprocess.run(
        [
            docker,
            "context",
            "inspect",
            context,
            "--format",
            '{{ (index .Endpoints "docker").Host }}',
        ],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=5,
    )
    endpoint = result.stdout.strip()
    if result.returncode != 0 or not endpoint:
        detail = result.stderr.strip() or "Docker returned no endpoint"
        raise DockerTopologyError(
            f"Could not inspect Docker context '{context}': {detail}"
        )
    return endpoint


def _active_context(environment: Mapping[str, str]) -> str:
    """Read the context selected by ``docker context use`` without a CLI call."""
    config_dir = Path(environment.get("DOCKER_CONFIG", Path.home() / ".docker"))
    try:
        config = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return "default"
    context = config.get("currentContext")
    return context if isinstance(context, str) and context else "default"


def _daemon_host(endpoint: str) -> str | None:
    if endpoint.startswith(("unix:", "npipe:")):
        return None
    parsed = urlparse(endpoint) if "://" in endpoint else None
    return parsed.hostname if parsed else None


@dataclass(frozen=True)
class DockerTopology:
    remote: bool
    published_host: str
    upstream_host: str | None = None
    docker_network: str | None = None
    desktop: bool = False
    rootless: bool = False

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> DockerTopology:
        discover_active_context = environment is None
        env = os.environ if environment is None else environment
        docker_host = env.get("DOCKER_HOST", "")
        docker_context = env.get("DOCKER_CONTEXT", "")
        if discover_active_context and not docker_host and not docker_context:
            docker_context = _active_context(env)
        endpoint = (
            _context_endpoint(docker_context)
            if docker_context and docker_context != "default"
            else docker_host
        )
        daemon_host = _daemon_host(endpoint)
        explicit_remote = env.get("RDST_DOCKER_REMOTE", "").lower() in _TRUE_VALUES
        detected_remote = bool(daemon_host and not _is_local_host(daemon_host))
        remote = explicit_remote or detected_remote

        published_host = env.get("RDST_DOCKER_PUBLISHED_HOST")
        if remote and not published_host:
            if explicit_remote and daemon_host and _is_local_host(daemon_host):
                raise DockerTopologyError(
                    "A tunneled remote Docker daemon requires "
                    "RDST_DOCKER_PUBLISHED_HOST"
                )
            published_host = daemon_host

        return cls(
            remote=remote,
            published_host=published_host or "127.0.0.1",
            upstream_host=env.get("RDST_DOCKER_UPSTREAM_HOST"),
            docker_network=(env.get("RDST_DOCKER_NETWORK") or "").strip()
            or None,
            desktop=(
                not remote
                and (
                    docker_context.startswith("desktop-")
                    or "/docker/desktop/" in endpoint
                )
            ),
            rootless=(
                not remote
                and (
                    docker_context == "rootless"
                    or "/run/user/" in endpoint
                )
            ),
        )

    def container_host_for(self, host: str) -> str:
        """Return the address a container should use for a client-side host."""
        if not _is_local_host(host):
            return host
        if not self.remote:
            return "host.docker.internal"
        if self.upstream_host:
            return self.upstream_host
        raise DockerTopologyError(
            "A remote Docker daemon cannot reach the RDST client's localhost. "
            "Set RDST_DOCKER_UPSTREAM_HOST to an address reachable from containers."
        )

    def container_network_for(
        self,
        host: str,
        *,
        platform_name: str | None = None,
    ) -> ContainerNetworkPlan:
        """Choose bridge or host networking for an upstream database address.

        Native Linux bridge containers cannot reach a service bound only to the
        client's loopback interface through ``host.docker.internal``. A local
        daemon can share the host network namespace instead. Remote daemons must
        continue to use an explicitly routable upstream address.
        """
        platform_name = platform_name or sys.platform
        if self.docker_network:
            return ContainerNetworkPlan(
                upstream_host=self.container_host_for(host),
                host_network=False,
                docker_network=self.docker_network,
            )
        if (
            platform_name.startswith("linux")
            and not self.remote
            and not self.desktop
            and not self.rootless
            and _is_local_host(host)
        ):
            return ContainerNetworkPlan(
                upstream_host="localhost",
                host_network=True,
            )
        return ContainerNetworkPlan(
            upstream_host=self.container_host_for(host),
            host_network=False,
        )

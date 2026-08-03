"""Docker client/daemon network topology."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
_TRUE_VALUES = {"1", "true", "yes", "on"}


class DockerTopologyError(RuntimeError):
    """The Docker daemon cannot route an address required by RDST."""


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

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> DockerTopology:
        env = os.environ if environment is None else environment
        docker_host = env.get("DOCKER_HOST", "")
        docker_context = env.get("DOCKER_CONTEXT", "")
        endpoint = (
            _context_endpoint(docker_context)
            if docker_context and docker_context != "default"
            else docker_host
        )
        daemon_host = _daemon_host(endpoint)
        explicit_remote = env.get("RDST_DOCKER_REMOTE", "").lower() in _TRUE_VALUES
        detected_remote = bool(daemon_host and daemon_host not in _LOCAL_HOSTS)
        remote = explicit_remote or detected_remote

        published_host = env.get("RDST_DOCKER_PUBLISHED_HOST")
        if remote and not published_host:
            if explicit_remote and daemon_host in _LOCAL_HOSTS:
                raise DockerTopologyError(
                    "A tunneled remote Docker daemon requires "
                    "RDST_DOCKER_PUBLISHED_HOST"
                )
            published_host = daemon_host

        return cls(
            remote=remote,
            published_host=published_host or "127.0.0.1",
            upstream_host=env.get("RDST_DOCKER_UPSTREAM_HOST"),
        )

    def container_host_for(self, host: str) -> str:
        """Return the address a container should use for a client-side host."""
        if host not in _LOCAL_HOSTS:
            return host
        if not self.remote:
            return "host.docker.internal"
        if self.upstream_host:
            return self.upstream_host
        raise DockerTopologyError(
            "A remote Docker daemon cannot reach the RDST client's localhost. "
            "Set RDST_DOCKER_UPSTREAM_HOST to an address reachable from containers."
        )

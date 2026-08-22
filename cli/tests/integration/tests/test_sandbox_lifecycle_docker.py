"""Docker-backed proof of the managed sandbox lifecycle invariants."""

from __future__ import annotations

import json
import socket
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from shared.deploy.local_docker import (
    MANAGED_SANDBOX_NAME,
    inspect_managed_sandbox,
)
from shared.deploy.sandbox_manager import LocalDockerSandboxAdapter


pytestmark = [pytest.mark.asyncio, pytest.mark.realdocker]


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _remove_exact(container_id: str | None) -> None:
    if container_id:
        _docker("rm", "-f", container_id, check=False)


def _require_free_default_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        try:
            candidate.bind(("127.0.0.1", 5433))
        except OSError:
            pytest.skip("host port 5433 is already owned outside this test")


async def test_real_docker_port_recovery_identity_and_crash_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise allocation, readiness, identity, and dirty restart physically."""
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker daemon is unavailable")
    if inspect_managed_sandbox() is not None:
        pytest.skip(f"{MANAGED_SANDBOX_NAME} is already in use")
    _require_free_default_port()

    suffix = uuid.uuid4().hex[:10]
    upstream_name = f"rdst-sandbox-test-upstream-{suffix}"
    blocker_name = f"rdst-sandbox-test-port-{suffix}"
    upstream_id: str | None = None
    blocker_id: str | None = None
    sandbox_id: str | None = None
    metadata_path = tmp_path / "readyset-sandbox.json"
    monkeypatch.setenv("HOME", str(tmp_path))

    try:
        upstream_id = _docker(
            "run",
            "-d",
            "--rm",
            "--name",
            upstream_name,
            "-e",
            "POSTGRES_PASSWORD=sandbox-test-password",
            # Published on every interface so the sandbox reaches this upstream
            # under host networking and under the bridge fallback alike.
            "-p",
            "5432",
            "postgres:17-alpine",
        ).stdout.strip()
        published = _docker("port", upstream_id, "5432/tcp").stdout.strip()
        upstream_port = int(published.rsplit(":", 1)[1])

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            ready = _docker(
                "exec",
                upstream_id,
                "pg_isready",
                "-U",
                "postgres",
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.25)
        else:
            pytest.fail("disposable PostgreSQL upstream did not become ready")

        blocker_id = _docker(
            "run",
            "-d",
            "--rm",
            "--name",
            blocker_name,
            "-e",
            "POSTGRES_PASSWORD=port-blocker-password",
            "-p",
            "127.0.0.1:5433:5432",
            "postgres:17-alpine",
        ).stdout.strip()

        adapter = LocalDockerSandboxAdapter(metadata_path)
        sandbox = await adapter.provision(
            "docker-recovery-test",
            "docker-recovery-fingerprint",
            {
                "engine": "postgresql",
                "host": "127.0.0.1",
                "port": upstream_port,
                "database": "postgres",
                "user": "postgres",
                "password": "sandbox-test-password",
            },
        )
        sandbox_id = sandbox.container_id
        assert sandbox.connection.port == 5434

        await adapter.wait_ready(sandbox, timeout_seconds=90)

        physical = inspect_managed_sandbox()
        assert physical is not None
        assert physical["running"] is True
        assert physical["id"] == sandbox.container_id
        assert physical["instance_id"] == sandbox.instance_id
        assert physical["target"] == sandbox.target
        assert physical["fingerprint"] == sandbox.fingerprint

        docker_inspect = json.loads(_docker("inspect", sandbox_id).stdout)[0]
        environment = set(docker_inspect["Config"]["Env"])
        host_networked = docker_inspect["HostConfig"]["NetworkMode"] == "host"
        listen_host = "127.0.0.1" if host_networked else "0.0.0.0"
        assert f"LISTEN_ADDRESS={listen_host}:5434" in environment
        assert "PROMETHEUS_METRICS=false" in environment
        assert "SHALLOW_MEMORY_PERCENT=80" in environment
        assert docker_inspect["HostConfig"]["RestartPolicy"]["Name"] == "no"

        metadata = json.loads(metadata_path.read_text())
        assert metadata["ready"] is True
        assert metadata["clean"] is True
        assert metadata["lease_active"] is False
        assert metadata["connection"]["port"] == 5434
        assert metadata["container_id"] == sandbox.container_id
        assert metadata["instance_id"] == sandbox.instance_id

        # Simulate a process dying after it journals an active lease. A fresh
        # adapter must destroy this potentially mutated container, not adopt it.
        metadata.update(clean=False, lease_active=True, lease_token="crashed")
        metadata_path.write_text(json.dumps(metadata))
        assert await LocalDockerSandboxAdapter(metadata_path).inspect() is None
        assert inspect_managed_sandbox() is None
        assert not metadata_path.exists()
        sandbox_id = None

        blocker_state = _docker(
            "inspect", blocker_id, "--format", "{{.State.Running}}"
        )
        assert blocker_state.stdout.strip() == "true"
    finally:
        _remove_exact(sandbox_id)
        _remove_exact(blocker_id)
        _remove_exact(upstream_id)

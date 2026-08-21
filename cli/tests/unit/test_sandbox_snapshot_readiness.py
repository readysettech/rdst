"""Readiness waits for the snapshot, not just for the SQL listener.

Readyset answers SHOW READYSET STATUS long before it has snapshotted the
upstream tables, and a CREATE CACHE issued in that window reads millions of
rows against a statement timeout. Readiness therefore means "Snapshot Status:
Completed", and a sandbox that is only still snapshotting is reported as not
ready yet rather than as a failure.
"""

from __future__ import annotations

import json

import pytest

from shared.deploy import sandbox_manager as sandbox_manager_module
from shared.deploy.sandbox_manager import (
    LocalDockerSandboxAdapter,
    ProvisionedSandbox,
    ReadysetSandboxManager,
    SandboxConnection,
    SandboxNotReadyError,
    target_fingerprint,
)

pytestmark = pytest.mark.usefixtures("run_blocking_inline")

_CONTAINER_ID = "container-id"
_INSTANCE_ID = "instance-id"


def _sandbox() -> ProvisionedSandbox:
    return ProvisionedSandbox(
        target="one",
        fingerprint="fingerprint",
        connection=SandboxConnection(
            engine="postgresql",
            host="127.0.0.1",
            port=5433,
            database="app",
            user="app",
            password="secret",
            cache_target="one-sandbox",
        ),
        container_id=_CONTAINER_ID,
        instance_id=_INSTANCE_ID,
    )


def _metadata_for(sandbox: ProvisionedSandbox, path):
    path.write_text(
        json.dumps(
            {
                "target": sandbox.target,
                "fingerprint": sandbox.fingerprint,
                "container_id": sandbox.container_id,
                "instance_id": sandbox.instance_id,
                "ready": False,
                "clean": False,
                "lease_active": False,
            }
        )
    )
    return path


@pytest.fixture
def readyset_status(monkeypatch):
    """Serve a scripted sequence of SHOW READYSET STATUS replies."""

    def _install(replies: list, running: bool = True) -> list:
        probes: list = []
        monkeypatch.setattr(
            "shared.deploy.local_docker.inspect_managed_sandbox",
            lambda: {
                "running": running,
                "id": _CONTAINER_ID,
                "instance_id": _INSTANCE_ID,
                "target": "one",
                "fingerprint": "fingerprint",
            },
        )
        monkeypatch.setattr(
            "shared.deploy.local_docker.managed_sandbox_startup_failure",
            lambda: {"kind": "exited", "error": "the container is not running"},
        )

        def probe(*_args):
            rows = replies[min(len(probes), len(replies) - 1)]
            probes.append(rows)
            return rows

        monkeypatch.setattr("shared.db_connection.probe_readyset_status", probe)
        return probes

    return _install


@pytest.mark.asyncio
async def test_completed_snapshot_is_ready(tmp_path, readyset_status):
    metadata_path = _metadata_for(_sandbox(), tmp_path / "metadata.json")
    readyset_status([[("Snapshot Status", "Completed")]])

    adapter = LocalDockerSandboxAdapter(metadata_path)
    await adapter.wait_ready(_sandbox(), timeout_seconds=5)

    assert json.loads(metadata_path.read_text())["ready"] is True


@pytest.mark.asyncio
async def test_snapshot_in_progress_is_not_ready_yet(tmp_path, readyset_status):
    readyset_status([[("Snapshot Status", "In Progress")]])
    adapter = LocalDockerSandboxAdapter(tmp_path / "metadata.json")

    with pytest.raises(SandboxNotReadyError, match="still preparing"):
        await adapter.wait_ready(_sandbox(), timeout_seconds=0.2)


@pytest.mark.asyncio
async def test_readiness_waits_for_the_snapshot_to_finish(
    tmp_path, monkeypatch, readyset_status
):
    metadata_path = _metadata_for(_sandbox(), tmp_path / "metadata.json")
    probes = readyset_status(
        [
            [("Snapshot Status", "In Progress")],
            [("Snapshot Status", "Completed")],
        ]
    )

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(sandbox_manager_module.asyncio, "sleep", no_sleep)
    adapter = LocalDockerSandboxAdapter(metadata_path)

    await adapter.wait_ready(_sandbox(), timeout_seconds=5)

    assert len(probes) == 2


@pytest.mark.asyncio
async def test_a_reply_without_a_snapshot_field_stays_ready(
    tmp_path, readyset_status
):
    # Older Readyset builds report no snapshot state; readiness keeps its
    # earlier meaning for them rather than never arriving.
    metadata_path = _metadata_for(_sandbox(), tmp_path / "metadata.json")
    readyset_status([[("Database Connection Point", "127.0.0.1:5432")]])
    adapter = LocalDockerSandboxAdapter(metadata_path)

    await adapter.wait_ready(_sandbox(), timeout_seconds=5)

    assert json.loads(metadata_path.read_text())["ready"] is True


@pytest.mark.asyncio
async def test_a_listener_that_never_answers_times_out(
    tmp_path, monkeypatch, readyset_status
):
    readyset_status([[]])

    def refuse(*_args):
        raise OSError("connection refused")

    monkeypatch.setattr("shared.db_connection.probe_readyset_status", refuse)
    adapter = LocalDockerSandboxAdapter(tmp_path / "metadata.json")

    with pytest.raises(TimeoutError) as failure:
        await adapter.wait_ready(_sandbox(), timeout_seconds=0.2)
    assert not isinstance(failure.value, SandboxNotReadyError)
    assert "connection refused" in str(failure.value)


_TARGET_CONFIG = {
    "engine": "postgresql",
    "host": "localhost",
    "port": 5432,
    "database": "one",
    "user": "postgres",
}


class _SnapshottingAdapter:
    """Reuse-path adapter whose sandbox needs a second, longer wait."""

    def __init__(self) -> None:
        self.current = ProvisionedSandbox(
            target="one",
            fingerprint=target_fingerprint("one", _TARGET_CONFIG),
            connection=_sandbox().connection,
        )
        self.timeouts: list[float] = []
        self.provisioned: list[str] = []
        self.removed = 0

    async def inspect(self):
        return self.current

    async def require_healthy_upstream(self, target_config):
        del target_config

    async def provision(self, target, fingerprint, target_config):
        self.provisioned.append(target)
        self.current = ProvisionedSandbox(
            target=target, fingerprint=fingerprint, connection=_sandbox().connection
        )
        return self.current

    async def wait_ready(self, sandbox, timeout_seconds):
        self.timeouts.append(timeout_seconds)
        if len(self.timeouts) == 1:
            raise SandboxNotReadyError("snapshot status is 'In Progress'")

    async def remove(self):
        self.removed += 1
        self.current = None


@pytest.fixture
def one_target(monkeypatch):
    monkeypatch.setattr(
        "shared.deploy.sandbox_manager._load_target_config",
        lambda target: dict(_TARGET_CONFIG),
    )


@pytest.mark.asyncio
async def test_a_still_snapshotting_sandbox_is_waited_out_not_replaced(
    tmp_path, one_target
):
    adapter = _SnapshottingAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter,
        metadata_path=tmp_path / "metadata.json",
        readiness_timeout_seconds=60,
    )
    await manager.start()

    async with manager.lease(target="one", owner_id="a", purpose="test"):
        pass

    # The short health check gave way to the full readiness deadline, and the
    # container was neither removed nor provisioned again.
    assert adapter.timeouts == [5.0, 60]
    assert adapter.removed == 0
    assert adapter.provisioned == []


@pytest.mark.asyncio
async def test_a_snapshot_that_never_finishes_replaces_the_sandbox(
    tmp_path, one_target
):
    class _NeverReady(_SnapshottingAdapter):
        async def wait_ready(self, sandbox, timeout_seconds):
            self.timeouts.append(timeout_seconds)
            if len(self.timeouts) <= 2:
                raise SandboxNotReadyError("snapshot status is 'In Progress'")

    adapter = _NeverReady()
    manager = ReadysetSandboxManager(
        adapter=adapter,
        metadata_path=tmp_path / "metadata.json",
        readiness_timeout_seconds=1,
    )
    await manager.start()

    async with manager.lease(target="one", owner_id="a", purpose="test"):
        pass

    # Both waits expired, so the sandbox was quarantined and rebuilt.
    assert adapter.timeouts[:2] == [1, 1]
    assert adapter.removed == 1
    assert adapter.provisioned == ["one"]

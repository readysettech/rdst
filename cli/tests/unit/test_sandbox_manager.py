from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from shared.deploy.sandbox_manager import (
    LocalDockerSandboxAdapter,
    ProvisionedSandbox,
    ReadysetSandboxManager,
    SandboxConnection,
    SandboxPriority,
    _finish_before_cancelling,
    _settle_transition,
)

pytestmark = pytest.mark.usefixtures("run_blocking_inline")


async def _wait_for_thread_event(event: threading.Event) -> None:
    while not event.is_set():
        await asyncio.sleep(0.01)


class FakeAdapter:
    def __init__(self) -> None:
        self.current: ProvisionedSandbox | None = None
        self.provisioned: list[str] = []
        self.removed = 0
        self.ready = 0
        self.remove_error: Exception | None = None

    async def inspect(self) -> ProvisionedSandbox | None:
        return self.current

    async def require_healthy_upstream(self, target_config):
        del target_config

    async def provision(self, target, fingerprint, target_config):
        self.provisioned.append(target)
        self.current = ProvisionedSandbox(
            target=target,
            fingerprint=fingerprint,
            connection=SandboxConnection(
                engine=target_config.get("engine", "postgresql"),
                host="127.0.0.1",
                port=5433,
                database=target_config.get("database", "db"),
                user=target_config.get("user", "user"),
                password="secret",
                cache_target=f"{target}-sandbox",
            ),
        )
        return self.current

    async def wait_ready(self, sandbox, timeout_seconds):
        self.ready += 1

    async def remove(self):
        if self.remove_error is not None:
            raise self.remove_error
        self.removed += 1
        self.current = None


@pytest.fixture
def target_configs(monkeypatch):
    configs = {
        "one": {
            "engine": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "one",
            "user": "postgres",
        },
        "two": {
            "engine": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "two",
            "user": "postgres",
        },
    }
    monkeypatch.setattr(
        "shared.deploy.sandbox_manager._load_target_config",
        lambda target: dict(configs[target]),
    )
    return configs


@pytest.mark.asyncio
async def test_startup_discards_container_interrupted_before_readiness(
    tmp_path, monkeypatch
):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "target": "one",
                "fingerprint": "fingerprint",
                "ready": False,
                "connection": {},
            }
        )
    )
    removed: list[bool] = []
    monkeypatch.setattr(
        "shared.deploy.local_docker.inspect_managed_sandbox",
        lambda: {
            "running": True,
            "managed": "true",
            "target": "one",
            "fingerprint": "fingerprint",
        },
    )
    monkeypatch.setattr(
        "shared.deploy.local_docker.remove_managed_sandbox",
        lambda: removed.append(True) or {"success": True, "removed": True},
    )

    adapter = LocalDockerSandboxAdapter(metadata_path)
    assert await adapter.inspect() is None
    assert removed == [True]
    assert not metadata_path.exists()


@pytest.mark.asyncio
async def test_local_adapter_rejects_unhealthy_upstream_before_docker(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "shared.db_connection.probe_target_connection",
        lambda target_config, connect_timeout: {
            "success": False,
            "error": "connection refused",
        },
    )
    adapter = LocalDockerSandboxAdapter(tmp_path / "metadata.json")

    with pytest.raises(RuntimeError, match="source database is unavailable"):
        await adapter.require_healthy_upstream(
            {"engine": "postgresql", "host": "127.0.0.1"}
        )


@pytest.mark.asyncio
async def test_same_target_reuses_one_sandbox(tmp_path, target_configs):
    adapter = FakeAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )

    async with manager.lease(target="one", owner_id="a", purpose="test"):
        pass
    async with manager.lease(target="one", owner_id="b", purpose="test"):
        pass

    assert adapter.provisioned == ["one"]
    assert adapter.removed == 0


@pytest.mark.asyncio
async def test_same_target_replaces_sandbox_that_fails_revalidation(
    tmp_path, target_configs
):
    class FailsOneHealthCheck(FakeAdapter):
        async def wait_ready(self, sandbox, timeout_seconds):
            await super().wait_ready(sandbox, timeout_seconds)
            if self.ready == 2:
                raise RuntimeError("container stopped")

    adapter = FailsOneHealthCheck()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )

    async with manager.lease(target="one", owner_id="a", purpose="test"):
        pass
    async with manager.lease(target="one", owner_id="b", purpose="test"):
        pass

    assert adapter.provisioned == ["one", "one"]
    assert adapter.removed == 1


@pytest.mark.asyncio
async def test_unhealthy_upstream_never_provisions_or_reuses_sandbox(
    tmp_path, target_configs
):
    class UnhealthyAdapter(FakeAdapter):
        async def require_healthy_upstream(self, target_config):
            del target_config
            raise RuntimeError(
                "The source database is unavailable; Readyset was not started."
            )

    adapter = UnhealthyAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )

    with pytest.raises(RuntimeError, match="source database is unavailable"):
        async with manager.lease(target="one", owner_id="a", purpose="test"):
            pass

    diagnostics = await manager.diagnostics()
    assert adapter.provisioned == []
    assert adapter.removed == 0
    assert diagnostics["lease_owner"] is None
    assert diagnostics["queued_requests"] == 0


@pytest.mark.asyncio
async def test_rechecks_upstream_before_reusing_warm_sandbox(
    tmp_path, target_configs
):
    class SwitchableAdapter(FakeAdapter):
        upstream_healthy = True

        async def require_healthy_upstream(self, target_config):
            del target_config
            if not self.upstream_healthy:
                raise RuntimeError(
                    "The source database is unavailable; Readyset was not started."
                )

    adapter = SwitchableAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )
    async with manager.lease(target="one", owner_id="a", purpose="test"):
        pass

    adapter.upstream_healthy = False
    with pytest.raises(RuntimeError, match="source database is unavailable"):
        async with manager.lease(target="one", owner_id="b", purpose="test"):
            pass

    assert adapter.provisioned == ["one"]
    assert adapter.removed == 0
    assert (await manager.diagnostics())["lease_owner"] is None


@pytest.mark.asyncio
async def test_start_keeps_web_available_when_inspection_fails(tmp_path):
    class FailingInspectAdapter(FakeAdapter):
        async def inspect(self):
            raise RuntimeError("Docker CLI was not found")

    manager = ReadysetSandboxManager(
        adapter=FailingInspectAdapter(),
        metadata_path=tmp_path / "metadata.json",
    )

    await manager.start()
    diagnostics = await manager.diagnostics()
    assert diagnostics["phase"] == "error"
    assert diagnostics["healthy"] is False
    assert diagnostics["last_error"] == (
        "Readyset sandbox inspection is unavailable: RuntimeError"
    )
    assert manager._expiry_task is not None
    assert not manager._expiry_task.done()
    await manager.stop()


@pytest.mark.asyncio
async def test_start_restores_persisted_generation(tmp_path, target_configs):
    adapter = FakeAdapter()
    adapter.current = await adapter.provision(
        "one", "fingerprint", target_configs["one"]
    )
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "generation": 7,
                "last_released_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=metadata_path
    )

    await manager.start()
    try:
        diagnostics = await manager.diagnostics()
        assert diagnostics["generation"] == 7
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_start_stop_are_reference_counted_for_overlapping_jobs(
    tmp_path, target_configs
):
    adapter = FakeAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )

    await manager.start()
    await manager.start()
    expiry_task = manager._expiry_task

    await manager.stop()

    assert manager._started is True
    assert manager._expiry_task is expiry_task
    assert expiry_task is not None
    assert not expiry_task.done()

    await manager.stop()
    assert manager._started is False
    assert manager._expiry_task is None


@pytest.mark.asyncio
async def test_changed_password_replaces_same_target_sandbox(
    tmp_path, target_configs
):
    adapter = FakeAdapter()
    target_configs["one"]["password"] = "first"
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )

    async with manager.lease(target="one", owner_id="a", purpose="test"):
        pass
    target_configs["one"]["password"] = "second"
    async with manager.lease(target="one", owner_id="b", purpose="test"):
        pass

    assert adapter.provisioned == ["one", "one"]
    assert adapter.removed == 1


@pytest.mark.asyncio
async def test_wrong_target_waits_for_active_lease_then_replaces(
    tmp_path, target_configs
):
    adapter = FakeAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def first():
        async with manager.lease(target="one", owner_id="a", purpose="test"):
            first_entered.set()
            await release_first.wait()

    async def second():
        await first_entered.wait()
        async with manager.lease(target="two", owner_id="b", purpose="test"):
            assert adapter.provisioned == ["one", "two"]

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await first_entered.wait()
    await asyncio.sleep(0)
    assert adapter.provisioned == ["one"]
    assert adapter.removed == 0
    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert adapter.removed == 1


@pytest.mark.asyncio
async def test_transition_helpers_preserve_late_cancellation():
    """Cancellation wins when the inner transition completes in the same loop turn."""

    async def finish_on_release(release: asyncio.Event):
        await release.wait()
        return "finished"

    release = asyncio.Event()
    finish_task = asyncio.create_task(
        _finish_before_cancelling(finish_on_release(release))
    )
    await asyncio.sleep(0)
    release.set()
    finish_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await finish_task

    release = asyncio.Event()
    settle_task = asyncio.create_task(
        _settle_transition(finish_on_release(release))
    )
    await asyncio.sleep(0)
    release.set()
    settle_task.cancel()
    assert await settle_task == (None, True)


@pytest.mark.asyncio
async def test_transition_state_commit_drains_cancellation(tmp_path):
    manager = ReadysetSandboxManager(
        adapter=FakeAdapter(), metadata_path=tmp_path / "metadata.json"
    )
    committed = False

    def update():
        nonlocal committed
        committed = True

    await manager._condition.acquire()
    commit_task = asyncio.create_task(manager._commit_transition_state(update))
    try:
        await asyncio.sleep(0)
        commit_task.cancel()
    finally:
        manager._condition.release()

    assert await commit_task is True
    assert committed


@pytest.mark.asyncio
async def test_repeated_cancellation_does_not_strand_lease_owner(
    tmp_path, target_configs
):
    manager = ReadysetSandboxManager(
        adapter=FakeAdapter(), metadata_path=tmp_path / "metadata.json"
    )
    entered = asyncio.Event()
    leave = asyncio.Event()

    async def worker():
        async with manager.lease(
            target="one", owner_id="first", purpose="test"
        ):
            entered.set()
            await leave.wait()

    task = asyncio.create_task(worker())
    await entered.wait()
    await manager._condition.acquire()
    try:
        leave.set()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
    finally:
        manager._condition.release()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await manager.diagnostics())["lease_owner"] is None

    async def lease_again():
        async with manager.lease(
            target="one", owner_id="second", purpose="test"
        ):
            pass

    await asyncio.wait_for(lease_again(), timeout=1)


@pytest.mark.asyncio
async def test_cancelled_active_provision_rolls_back_before_next_waiter(
    tmp_path, target_configs
):
    """Cancellation cannot release ownership while provisioning is still running."""

    class BlockingProvisionAdapter(FakeAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.provision_started = asyncio.Event()
            self.finish_provision = threading.Event()
            self.remove_finished = asyncio.Event()
            self.second_provision_started = asyncio.Event()

        async def provision(self, target, fingerprint, target_config):
            self.provisioned.append(target)
            if len(self.provisioned) == 1:
                self.provision_started.set()
                await _wait_for_thread_event(self.finish_provision)
            else:
                self.second_provision_started.set()
            self.current = ProvisionedSandbox(
                target=target,
                fingerprint=fingerprint,
                connection=SandboxConnection(
                    engine="postgresql",
                    host="127.0.0.1",
                    port=5433,
                    database=target,
                    user="postgres",
                    password="secret",
                    cache_target=f"{target}-sandbox",
                ),
            )
            return self.current

        async def remove(self):
            await super().remove()
            self.remove_finished.set()

    adapter = BlockingProvisionAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )

    async def first():
        async with manager.lease(target="one", owner_id="first", purpose="test"):
            pass

    async def second():
        async with manager.lease(target="two", owner_id="second", purpose="test"):
            assert adapter.remove_finished.is_set()

    first_task = asyncio.create_task(first())
    await adapter.provision_started.wait()
    second_task = asyncio.create_task(second())
    first_task.cancel()
    try:
        try:
            await asyncio.wait_for(
                asyncio.shield(adapter.remove_finished.wait()), timeout=0.05
            )
        except asyncio.TimeoutError:
            pass

        second_started_before_provision_finished = (
            adapter.second_provision_started.is_set()
        )
        removed_before_provision_finished = adapter.removed
    finally:
        adapter.finish_provision.set()
    with pytest.raises(asyncio.CancelledError):
        await first_task
    await second_task

    assert not second_started_before_provision_finished
    assert removed_before_provision_finished == 0
    assert adapter.provisioned == ["one", "two"]
    assert adapter.removed == 1


@pytest.mark.asyncio
async def test_cancelled_replacement_removes_sandbox_once(tmp_path, target_configs):
    class BlockingRemovalAdapter(FakeAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.remove_started = asyncio.Event()
            self.finish_remove = threading.Event()

        async def remove(self):
            self.remove_started.set()
            await _wait_for_thread_event(self.finish_remove)
            await super().remove()

    adapter = BlockingRemovalAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )
    async with manager.lease(target="one", owner_id="first", purpose="test"):
        pass

    async def replace():
        async with manager.lease(target="two", owner_id="second", purpose="test"):
            pass

    replacement = asyncio.create_task(replace())
    try:
        await adapter.remove_started.wait()
        replacement.cancel()
        adapter.finish_remove.set()
        with pytest.raises(asyncio.CancelledError):
            await replacement
    finally:
        adapter.finish_remove.set()
        await asyncio.gather(replacement, return_exceptions=True)

    assert adapter.removed == 1
    assert (await manager.diagnostics())["phase"] == "absent"
    async with manager.lease(target="two", owner_id="third", purpose="test"):
        pass
    assert adapter.removed == 1


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_rollback_before_next_waiter(
    tmp_path, target_configs
):
    class BlockingRollbackAdapter(FakeAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.provision_started = asyncio.Event()
            self.finish_provision = threading.Event()
            self.remove_started = asyncio.Event()
            self.finish_remove = threading.Event()
            self.second_provision_started = asyncio.Event()

        async def provision(self, target, fingerprint, target_config):
            self.provisioned.append(target)
            if len(self.provisioned) == 1:
                self.provision_started.set()
                await _wait_for_thread_event(self.finish_provision)
            else:
                self.second_provision_started.set()
            self.current = ProvisionedSandbox(
                target=target,
                fingerprint=fingerprint,
                connection=SandboxConnection(
                    engine="postgresql",
                    host="127.0.0.1",
                    port=5433,
                    database=target,
                    user="postgres",
                    password="secret",
                    cache_target=f"{target}-sandbox",
                ),
            )
            return self.current

        async def remove(self):
            self.remove_started.set()
            await _wait_for_thread_event(self.finish_remove)
            await super().remove()

    adapter = BlockingRollbackAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )

    async def first():
        async with manager.lease(target="one", owner_id="first", purpose="test"):
            pass

    async def second():
        async with manager.lease(target="two", owner_id="second", purpose="test"):
            pass

    first_task = asyncio.create_task(first())
    second_task = None
    try:
        await adapter.provision_started.wait()
        second_task = asyncio.create_task(second())
        first_task.cancel()
        adapter.finish_provision.set()
        await adapter.remove_started.wait()

        first_task.cancel()
        await asyncio.sleep(0)
        assert not adapter.second_provision_started.is_set()

        adapter.finish_remove.set()
        with pytest.raises(asyncio.CancelledError):
            await first_task
        await second_task
    finally:
        adapter.finish_provision.set()
        adapter.finish_remove.set()
        tasks = [first_task]
        if second_task is not None:
            tasks.append(second_task)
        await asyncio.gather(*tasks, return_exceptions=True)

    assert adapter.provisioned == ["one", "two"]
    assert adapter.removed == 1


@pytest.mark.asyncio
async def test_priority_then_fifo_without_preemption(tmp_path, target_configs):
    adapter = FakeAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )
    order: list[str] = []
    release = asyncio.Event()

    async def holder():
        async with manager.reserve_measurement(owner_id="holder", purpose="load"):
            await release.wait()

    async def waiter(name, priority):
        async with manager.lease(
            target="one", owner_id=name, purpose="test", priority=priority
        ):
            order.append(name)

    holding = asyncio.create_task(holder())
    while (await manager.diagnostics())["lease_owner"] != "holder":
        await asyncio.sleep(0)
    low = asyncio.create_task(waiter("prewarm", SandboxPriority.PREWARM))
    high_a = asyncio.create_task(waiter("user-a", SandboxPriority.USER_TEST))
    high_b = asyncio.create_task(waiter("user-b", SandboxPriority.USER_TEST))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(holding, low, high_a, high_b)
    assert order == ["user-a", "user-b", "prewarm"]


@pytest.mark.asyncio
async def test_duplicate_prewarm_does_not_restart_inflight_provisioning(
    tmp_path, target_configs
):
    class SlowReadyAdapter(FakeAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.waiting = asyncio.Event()
            self.release = asyncio.Event()

        async def wait_ready(self, sandbox, timeout_seconds):
            self.ready += 1
            self.waiting.set()
            await self.release.wait()

    adapter = SlowReadyAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )

    manager.request_prewarm("one")
    await adapter.waiting.wait()
    manager.request_prewarm("one")
    await asyncio.sleep(0)

    assert adapter.provisioned == ["one"]
    assert adapter.removed == 0

    adapter.release.set()
    assert manager._prewarm_task is not None
    await manager._prewarm_task
    assert (await manager.diagnostics())["current_target"] == "one"


@pytest.mark.asyncio
async def test_failed_prewarm_is_visible_in_diagnostics(tmp_path, target_configs):
    class FailingReadyAdapter(FakeAdapter):
        async def wait_ready(self, sandbox, timeout_seconds):
            raise TimeoutError("not ready")

    adapter = FailingReadyAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )

    manager.request_prewarm("one")
    assert manager._prewarm_task is not None
    await manager._prewarm_task

    diagnostics = await manager.diagnostics()
    assert diagnostics["phase"] == "error"
    assert diagnostics["failed_target"] == "one"
    assert diagnostics["last_error"] == (
        "Readyset could not be prepared for one. "
        "Check that the database is reachable, then retry."
    )


@pytest.mark.asyncio
async def test_cancelled_waiter_is_removed(tmp_path, target_configs):
    adapter = FakeAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )
    release = asyncio.Event()

    async def holder():
        async with manager.reserve_measurement(owner_id="holder", purpose="load"):
            await release.wait()

    async def queued():
        async with manager.lease(target="one", owner_id="queued", purpose="test"):
            pass

    holding = asyncio.create_task(holder())
    while (await manager.diagnostics())["lease_owner"] != "holder":
        await asyncio.sleep(0)
    waiting = asyncio.create_task(queued())
    while (await manager.diagnostics())["queued_requests"] != 1:
        await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert (await manager.diagnostics())["queued_requests"] == 0
    release.set()
    await holding


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", ["target", "expiry"])
async def test_failed_rollback_remains_tracked_for_cleanup(
    tmp_path, target_configs, cleanup
):
    class FailedRollbackAdapter(FakeAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.remove_attempts = 0

        async def wait_ready(self, sandbox, timeout_seconds):
            del sandbox, timeout_seconds
            raise RuntimeError("readiness failed")

        async def remove(self):
            self.remove_attempts += 1
            if self.remove_attempts == 1:
                raise RuntimeError("rollback failed")
            await super().remove()

    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    clock_value = [now]
    adapter = FailedRollbackAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter,
        metadata_path=tmp_path / "metadata.json",
        idle_ttl=timedelta(seconds=10),
        clock=lambda: clock_value[0],
    )

    with pytest.raises(RuntimeError, match="readiness failed"):
        async with manager.lease(target="one", owner_id="first", purpose="test"):
            pass

    diagnostics = await manager.diagnostics()
    assert diagnostics["phase"] == "dirty"
    assert diagnostics["current_target"] == "one"
    assert diagnostics["expires_at"] is not None

    if cleanup == "target":
        assert await manager.remove_target("one") is True
    else:
        clock_value[0] = now + timedelta(seconds=11)
        assert await manager.expire_idle() is True

    assert adapter.remove_attempts == 2
    assert (await manager.diagnostics())["phase"] == "absent"


@pytest.mark.asyncio
async def test_dirty_sandbox_is_replaced_before_next_lease(
    tmp_path, target_configs
):
    adapter = FakeAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )
    async with manager.lease(
        target="one", owner_id="a", purpose="test"
    ) as lease:
        await lease.mark_dirty("cache cleanup failed")
    async with manager.lease(target="one", owner_id="b", purpose="test"):
        pass
    assert adapter.provisioned == ["one", "one"]
    assert adapter.removed == 1


@pytest.mark.asyncio
async def test_measurement_reservation_blocks_transition(tmp_path, target_configs):
    adapter = FakeAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )
    release = asyncio.Event()

    async def measurement():
        async with manager.reserve_measurement(owner_id="load", purpose="load_test"):
            release.set()
            await asyncio.sleep(0.01)

    async def experiment():
        await release.wait()
        async with manager.lease(target="one", owner_id="test", purpose="test"):
            pass

    await asyncio.gather(measurement(), experiment())
    assert adapter.provisioned == ["one"]


@pytest.mark.asyncio
async def test_idle_expiry_defers_until_release(tmp_path, target_configs):
    adapter = FakeAdapter()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock_value = [now]
    manager = ReadysetSandboxManager(
        adapter=adapter,
        idle_ttl=timedelta(seconds=10),
        clock=lambda: clock_value[0],
        metadata_path=tmp_path / "metadata.json",
    )
    async with manager.lease(target="one", owner_id="a", purpose="test"):
        clock_value[0] = now + timedelta(seconds=20)
        assert await manager.expire_idle() is False
    clock_value[0] = now + timedelta(seconds=31)
    assert await manager.expire_idle() is True
    assert adapter.removed == 1


@pytest.mark.asyncio
async def test_idle_expiry_failure_keeps_sandbox_dirty_for_retry(
    tmp_path, target_configs
):
    adapter = FakeAdapter()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock_value = [now]
    manager = ReadysetSandboxManager(
        adapter=adapter,
        idle_ttl=timedelta(seconds=10),
        clock=lambda: clock_value[0],
        metadata_path=tmp_path / "metadata.json",
    )
    async with manager.lease(target="one", owner_id="a", purpose="test"):
        pass
    adapter.remove_error = RuntimeError("docker unavailable")
    clock_value[0] = now + timedelta(seconds=11)

    assert await manager.expire_idle() is False
    diagnostics = await manager.diagnostics()
    assert diagnostics["phase"] == "dirty"
    assert diagnostics["healthy"] is False

    adapter.remove_error = None
    clock_value[0] = now + timedelta(minutes=2)
    assert await manager.expire_idle() is True


@pytest.mark.asyncio
async def test_idle_expiry_blocks_a_new_lease_until_removal_finishes(
    tmp_path, target_configs
):
    class BlockingRemoveAdapter(FakeAdapter):
        def __init__(self):
            super().__init__()
            self.remove_started = asyncio.Event()
            self.allow_remove = asyncio.Event()

        async def remove(self):
            self.remove_started.set()
            await self.allow_remove.wait()
            await super().remove()

    adapter = BlockingRemoveAdapter()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock_value = [now]
    manager = ReadysetSandboxManager(
        adapter=adapter,
        idle_ttl=timedelta(seconds=10),
        clock=lambda: clock_value[0],
        metadata_path=tmp_path / "metadata.json",
    )
    async with manager.lease(target="one", owner_id="first", purpose="test"):
        pass
    clock_value[0] = now + timedelta(seconds=11)

    expiry = asyncio.create_task(manager.expire_idle())
    await adapter.remove_started.wait()
    acquired = asyncio.Event()

    async def acquire_again():
        async with manager.lease(target="one", owner_id="second", purpose="test"):
            acquired.set()

    lease = asyncio.create_task(acquire_again())
    await asyncio.sleep(0)
    assert acquired.is_set() is False
    assert (await manager.diagnostics())["lease_owner"] is None

    adapter.allow_remove.set()
    assert await expiry is True
    await lease
    assert adapter.provisioned == ["one", "one"]


@pytest.mark.asyncio
async def test_cancelled_expiry_finishes_removal_and_resets_state(
    tmp_path, target_configs
):
    class BlockingRemoveAdapter(FakeAdapter):
        def __init__(self):
            super().__init__()
            self.remove_started = asyncio.Event()
            self.allow_remove = asyncio.Event()

        async def remove(self):
            self.remove_started.set()
            await self.allow_remove.wait()
            await super().remove()

    adapter = BlockingRemoveAdapter()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock_value = [now]
    manager = ReadysetSandboxManager(
        adapter=adapter,
        idle_ttl=timedelta(seconds=10),
        clock=lambda: clock_value[0],
        metadata_path=tmp_path / "metadata.json",
    )
    async with manager.lease(target="one", owner_id="first", purpose="test"):
        pass
    clock_value[0] = now + timedelta(seconds=11)

    expiry = asyncio.create_task(manager.expire_idle())
    await adapter.remove_started.wait()
    expiry.cancel()
    await asyncio.sleep(0)
    assert expiry.done() is False

    adapter.allow_remove.set()
    with pytest.raises(asyncio.CancelledError):
        await expiry
    diagnostics = await manager.diagnostics()
    assert diagnostics["phase"] == "absent"
    assert diagnostics["healthy"] is False


@pytest.mark.asyncio
async def test_remove_unrelated_target_does_not_wait_for_active_lease(
    tmp_path, target_configs
):
    adapter = FakeAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )

    async with manager.lease(target="one", owner_id="holder", purpose="test"):
        removed = await asyncio.wait_for(manager.remove_target("two"), timeout=0.1)

    assert removed is False
    assert adapter.removed == 0


@pytest.mark.asyncio
async def test_remove_unrelated_target_does_not_wait_for_transition(
    tmp_path, target_configs
):
    adapter = FakeAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )
    async with manager.lease(target="one", owner_id="holder", purpose="test"):
        pass

    async with manager._condition:
        manager._transition_in_progress = True
    try:
        removed = await asyncio.wait_for(manager.remove_target("two"), timeout=0.1)
    finally:
        async with manager._condition:
            manager._transition_in_progress = False
            manager._condition.notify_all()

    assert removed is False
    assert adapter.removed == 0


@pytest.mark.asyncio
async def test_remove_target_waits_for_active_lease_then_removes_sandbox(
    tmp_path, target_configs
):
    adapter = FakeAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )
    release = asyncio.Event()
    entered = asyncio.Event()

    async def hold_lease():
        async with manager.lease(target="one", owner_id="active", purpose="test"):
            entered.set()
            await release.wait()

    lease = asyncio.create_task(hold_lease())
    await entered.wait()
    removal = asyncio.create_task(manager.remove_target("one"))
    await asyncio.sleep(0)
    assert removal.done() is False

    release.set()
    await lease
    assert await removal is True
    assert adapter.removed == 1
    assert (await manager.diagnostics())["phase"] == "absent"


@pytest.mark.asyncio
async def test_cancelled_target_removal_preserves_cancellation_while_draining_prewarm(
    tmp_path
):
    manager = ReadysetSandboxManager(
        adapter=FakeAdapter(), metadata_path=tmp_path / "metadata.json"
    )
    started = asyncio.Event()
    prewarm_cancelled = asyncio.Event()
    release = asyncio.Event()

    async def prewarm():
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            prewarm_cancelled.set()
            await release.wait()
            raise

    prewarm_task = asyncio.create_task(prewarm())
    manager._prewarm_target = "one"
    manager._prewarm_task = prewarm_task
    await started.wait()

    removal = asyncio.create_task(manager.remove_target("one"))
    await prewarm_cancelled.wait()
    removal.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await removal
    assert prewarm_task.cancelled()


@pytest.mark.asyncio
async def test_cancelled_target_removal_does_not_block_future_leases(
    tmp_path, target_configs
):
    adapter = FakeAdapter()
    manager = ReadysetSandboxManager(
        adapter=adapter, metadata_path=tmp_path / "metadata.json"
    )
    release = asyncio.Event()
    entered = asyncio.Event()

    async def hold_lease():
        async with manager.lease(target="one", owner_id="active", purpose="test"):
            entered.set()
            await release.wait()

    lease = asyncio.create_task(hold_lease())
    await entered.wait()
    removal = asyncio.create_task(manager.remove_target("one"))
    await asyncio.sleep(0)
    removal.cancel()
    with pytest.raises(asyncio.CancelledError):
        await removal

    release.set()
    await lease

    async def lease_again():
        async with manager.lease(
            target="one", owner_id="next", purpose="test"
        ):
            pass

    await asyncio.wait_for(lease_again(), timeout=1)

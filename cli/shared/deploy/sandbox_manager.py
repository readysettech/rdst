"""Lifecycle manager for RDST's one Readyset demo sandbox.

The manager owns a capacity-one resource. Readyset experiments acquire an
exclusive lease; origin-only measurements acquire an exclusive reservation.
Docker transitions happen only after a waiter reaches the head of the
priority/FIFO queue, so an active experiment can never be replaced by a target
switch or another request in this process.

One OS-backed ownership lock prevents another RDST process from reconciling or
replacing the sandbox while this manager is active.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from pathlib import Path
from typing import (
    Any,
    AsyncContextManager,
    AsyncIterator,
    Awaitable,
    Callable,
    Protocol,
    TypeVar,
)

from filelock import FileLock, Timeout as FileLockTimeout

from shared.config.targets import TargetsConfig
from shared.deploy import READYSET_IMAGE
from shared.deploy.docker_topology import DockerTopology
from shared.persistence import delete_file, update_json, write_text
from shared.password_resolver import resolve_password_value

logger = logging.getLogger(__name__)

SANDBOX_CONTAINER_NAME = "rdst-readyset-sandbox"
SANDBOX_DEPLOYMENT_VERSION = 4
DEFAULT_IDLE_TTL = timedelta(days=1)
DEFAULT_MANAGED_SANDBOX_PORTS = {"postgresql": 5433, "mysql": 3307}
SANDBOX_STARTUP_ATTEMPTS = 3

ProgressCallback = Callable[[str, str], Awaitable[None] | None]
_T = TypeVar("_T")


class SandboxPriority(IntEnum):
    """Lower values run first; an active owner is never preempted."""

    USER_TEST = 10
    ANALYZE_VERIFY = 20
    AUDIT_BATCH = 30
    PREWARM = 100


class SandboxPortConflictError(RuntimeError):
    """Readyset exited because a raced listener claimed its SQL port."""


class SandboxResourceError(RuntimeError):
    """Readyset could not start within the sandbox resource limits."""


class SandboxReconciliationError(RuntimeError):
    """Physical sandbox state could not be reconciled safely."""


class SandboxOwnershipError(RuntimeError):
    """Another RDST process owns the sandbox lifecycle."""


@dataclass(frozen=True)
class SandboxConnection:
    engine: str
    host: str
    port: int
    database: str
    user: str
    password: str
    cache_target: str
    container_name: str = SANDBOX_CONTAINER_NAME

    def as_target_config(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "password": self.password,
            "target_type": "readyset",
            "upstream_target": self.cache_target.removesuffix("-sandbox"),
        }


@dataclass(frozen=True)
class ProvisionedSandbox:
    target: str
    fingerprint: str
    connection: SandboxConnection
    container_id: str = ""
    instance_id: str = ""


@dataclass
class SandboxState:
    phase: str = "absent"
    current_target: str | None = None
    target_fingerprint: str | None = None
    generation: int = 0
    lease_owner: str | None = None
    lease_purpose: str | None = None
    lease_target: str | None = None
    lease_token: str | None = None
    last_released_at: datetime | None = None
    expires_at: datetime | None = None
    dirty_reason: str | None = None
    failed_target: str | None = None
    last_error: str | None = None


@dataclass(order=True)
class _Waiter:
    priority: int
    sequence: int
    owner_id: str
    purpose: str
    target: str | None = None
    progress: ProgressCallback | None = None


class SandboxAdapter(Protocol):
    async def inspect(self) -> ProvisionedSandbox | None: ...

    async def require_healthy_upstream(
        self, target_config: dict[str, Any]
    ) -> None: ...

    async def provision(
        self, target: str, fingerprint: str, target_config: dict[str, Any]
    ) -> ProvisionedSandbox: ...

    async def wait_ready(
        self, sandbox: ProvisionedSandbox, timeout_seconds: float
    ) -> None: ...

    async def remove(self) -> None: ...


class LocalDockerSandboxAdapter:
    """Production adapter around the local Docker deployment helpers."""

    def __init__(self, metadata_path: Path | None = None) -> None:
        self.metadata_path = metadata_path
        self._legacy_reconciled_targets: set[str] = set()

    async def require_healthy_upstream(
        self, target_config: dict[str, Any]
    ) -> None:
        from shared.db_connection import probe_target_connection

        state = await asyncio.to_thread(
            probe_target_connection,
            target_config,
            connect_timeout=3,
        )
        if state.get("success"):
            return
        raise RuntimeError(
            "The source database is unavailable; Readyset was not started."
        )

    async def inspect(self) -> ProvisionedSandbox | None:
        from shared.deploy.local_docker import (
            inspect_managed_sandbox,
            remove_managed_sandbox,
        )

        raw = await asyncio.to_thread(inspect_managed_sandbox)
        if not raw:
            return None

        async def discard_stale() -> None:
            result = await asyncio.to_thread(remove_managed_sandbox)
            if not result.get("success"):
                raise SandboxReconciliationError(
                    result.get("error")
                    or "Could not remove the stale Readyset sandbox"
                )
            try:
                await asyncio.to_thread(_delete_metadata, self.metadata_path)
            except Exception:
                logger.exception("Could not delete stale sandbox metadata")

        if not raw.get("running"):
            await discard_stale()
            return None
        metadata = _read_metadata(self.metadata_path)
        if not metadata:
            await discard_stale()
            return None
        # Metadata is written before the SQL listener is ready so a canceled or
        # interrupted provision can be identified on the next process start.
        # Only a sandbox that completed the readiness probe is adoptable.
        if (
            metadata.get("ready") is not True
            or metadata.get("clean") is not True
            or metadata.get("lease_active") is True
        ):
            await discard_stale()
            return None
        connection = metadata.get("connection")
        if not isinstance(connection, dict):
            await discard_stale()
            return None
        try:
            target = str(metadata["target"])
            fingerprint = str(metadata["fingerprint"])
            container_id = str(metadata["container_id"])
            instance_id = str(metadata["instance_id"])
            target_config = _load_target_config(target)
            connection.pop("password", None)
            connection = {
                **connection,
                "password": resolve_password_value(target_config),
            }
            sandbox_connection = SandboxConnection(**connection)
        except Exception:
            await discard_stale()
            return None
        if (
            raw.get("target") != target
            or raw.get("fingerprint") != fingerprint
            or raw.get("id") != container_id
            or raw.get("instance_id") != instance_id
        ):
            await discard_stale()
            return None
        return ProvisionedSandbox(
            target=target,
            fingerprint=fingerprint,
            connection=sandbox_connection,
            container_id=container_id,
            instance_id=instance_id,
        )

    async def ensure_image_ready(self, on_download=None) -> None:
        """Guarantee the Readyset image is local before any container start.

        A first-run pull is gigabyte-scale and would otherwise be spent
        inside the container readiness timeout, which then fails with
        "container is not running" while the download is still going.
        `on_download` is awaited once before a pull actually starts, so the
        caller can surface a downloading state to the user.
        """
        from shared.deploy import READYSET_IMAGE
        from shared.deploy.local_docker import image_present, pull_image

        if await asyncio.to_thread(image_present, READYSET_IMAGE):
            return
        if on_download is not None:
            result = on_download()
            if inspect.isawaitable(result):
                await result
        pulled = await asyncio.to_thread(pull_image, READYSET_IMAGE)
        if not pulled.get("success"):
            raise RuntimeError(
                f"Could not download the Readyset image: {pulled.get('error')}"
            )

    async def provision(
        self, target: str, fingerprint: str, target_config: dict[str, Any]
    ) -> ProvisionedSandbox:
        from shared.deploy.local_docker import (
            deploy_managed_sandbox,
            remove_configured_legacy_container,
        )
        # One-time migration: only exact container names recorded on RDST
        # Readyset target rows are eligible. Similarly named foreign resources
        # are never touched.
        if target not in self._legacy_reconciled_targets:
            config = TargetsConfig()
            config.load()
            changed = False
            for name in list(config.list_targets()):
                entry = config.get(name) or {}
                legacy_name = str(entry.get("container_name") or "")
                if (
                    entry.get("target_type") != "readyset"
                    or entry.get("upstream_target") != target
                    or not legacy_name
                    or legacy_name == SANDBOX_CONTAINER_NAME
                ):
                    continue
                removed = await asyncio.to_thread(
                    remove_configured_legacy_container, legacy_name
                )
                if not removed.get("success"):
                    raise SandboxReconciliationError(
                        removed.get("error")
                        or f"Could not remove legacy container '{legacy_name}'"
                    )
                config.remove(name)
                changed = True
            if changed:
                config.save()
            self._legacy_reconciled_targets.add(target)

        password = resolve_password_value(target_config)
        engine = str(target_config.get("engine", "postgresql"))
        memory_bytes = 4 * 1024 * 1024 * 1024
        variables = {
            "db_host": str(target_config.get("host", "localhost")),
            "db_port": str(target_config.get("port", 5432)),
            "db_user": str(target_config.get("user", "postgres")),
            "db_name": str(target_config.get("database", "")),
            "db_engine": engine,
            "readyset_port": str(
                DEFAULT_MANAGED_SANDBOX_PORTS.get(engine, 5433)
            ),
            "container_name": SANDBOX_CONTAINER_NAME,
            "readyset_image": READYSET_IMAGE,
            "query_caching": "explicit",
            "memory_bytes": memory_bytes,
            "cpus": "2",
            "docker_memory": "4096m",
        }
        result = await asyncio.to_thread(
            deploy_managed_sandbox,
            target,
            variables,
            password,
            fingerprint,
        )
        if not result.get("success"):
            raise RuntimeError(result.get("error") or "Readyset sandbox deployment failed")
        connection = SandboxConnection(
            engine=str(variables.get("db_engine", "postgresql")),
            host=DockerTopology.from_environment().published_host,
            port=int(result["port"]),
            database=str(variables.get("db_name", "")),
            user=str(variables.get("db_user", "")),
            password=password,
            cache_target=f"{target}-sandbox",
        )
        sandbox = ProvisionedSandbox(
            target=target,
            fingerprint=fingerprint,
            connection=connection,
            container_id=str(result["container_id"]),
            instance_id=str(result["instance_id"]),
        )
        await asyncio.to_thread(
            _write_metadata,
            self.metadata_path,
            {
                "target": target,
                "fingerprint": fingerprint,
                "connection": {
                    key: value
                    for key, value in asdict(connection).items()
                    if key != "password"
                },
                "ready": False,
                "clean": False,
                "lease_active": False,
                "dirty": False,
                "metadata_version": 2,
                "container_id": sandbox.container_id,
                "instance_id": sandbox.instance_id,
            },
        )
        return sandbox

    async def wait_ready(
        self, sandbox: ProvisionedSandbox, timeout_seconds: float
    ) -> None:
        from shared.deploy.local_docker import (
            inspect_managed_sandbox,
            managed_sandbox_startup_failure,
        )
        from shared.db_connection import probe_readyset_status

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        last_error = "Readyset SQL listener did not become ready"
        while asyncio.get_running_loop().time() < deadline:
            try:
                identity = await asyncio.to_thread(inspect_managed_sandbox)
            except Exception as exc:
                last_error = str(exc) or type(exc).__name__
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining > 0:
                    await asyncio.sleep(min(1, remaining))
                continue
            if not identity or not identity.get("running"):
                failure = await asyncio.to_thread(managed_sandbox_startup_failure)
                if failure.get("kind") == "port_conflict":
                    raise SandboxPortConflictError(str(failure["error"]))
                if failure.get("kind") == "oom":
                    raise SandboxResourceError(str(failure["error"]))
                raise RuntimeError(str(failure.get("error") or "Readyset stopped"))
            self._require_identity(sandbox, identity)
            try:
                remaining = deadline - asyncio.get_running_loop().time()
                await asyncio.to_thread(
                    probe_readyset_status,
                    sandbox.connection.as_target_config(),
                    min(3, max(remaining, 0.001)),
                )
                identity = await asyncio.to_thread(inspect_managed_sandbox)
                if not identity or not identity.get("running"):
                    raise RuntimeError(
                        "Readyset stopped after its SQL readiness check"
                    )
                self._require_identity(sandbox, identity)
                await asyncio.to_thread(
                    _update_metadata,
                    self.metadata_path,
                    lambda metadata: _mark_metadata_ready(metadata, sandbox),
                )
                return
            except (
                SandboxPortConflictError,
                SandboxResourceError,
                SandboxReconciliationError,
            ):
                raise
            except Exception as exc:
                last_error = str(exc) or type(exc).__name__
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining > 0:
                    await asyncio.sleep(min(1, remaining))
        raise TimeoutError(f"Readyset sandbox readiness timed out: {last_error}")

    @staticmethod
    def _require_identity(
        sandbox: ProvisionedSandbox, identity: dict[str, Any]
    ) -> None:
        if (
            identity.get("id") != sandbox.container_id
            or identity.get("instance_id") != sandbox.instance_id
            or identity.get("target") != sandbox.target
            or identity.get("fingerprint") != sandbox.fingerprint
        ):
            raise SandboxReconciliationError(
                "The Readyset sandbox identity changed during startup"
            )

    async def remove(self) -> None:
        from shared.deploy.local_docker import remove_managed_sandbox

        result = await asyncio.to_thread(remove_managed_sandbox)
        if not result.get("success"):
            raise RuntimeError(
                result.get("error") or "Readyset sandbox removal failed"
            )
        try:
            await asyncio.to_thread(_delete_metadata, self.metadata_path)
        except Exception:
            logger.exception(
                "Readyset was removed, but its stale metadata could not be deleted"
            )


class SandboxLease:
    """Immutable lease identity plus a controlled dirty-state hook."""

    def __init__(
        self,
        manager: "ReadysetSandboxManager",
        *,
        owner_id: str,
        purpose: str,
        target: str,
        generation: int,
        connection: SandboxConnection,
    ) -> None:
        self._manager = manager
        self.owner_id = owner_id
        self.purpose = purpose
        self.target = target
        self.generation = generation
        self.connection = connection

    async def mark_dirty(self, reason: str) -> None:
        await self._manager.mark_dirty(self, reason)


class ReadysetSandboxManager:
    """Owns the one target-backed Readyset sandbox for this process."""

    def __init__(
        self,
        *,
        adapter: SandboxAdapter | None = None,
        idle_ttl: timedelta = DEFAULT_IDLE_TTL,
        readiness_timeout_seconds: float = 90,
        clock: Callable[[], datetime] | None = None,
        metadata_path: Path | None = None,
    ) -> None:
        self._metadata_path = metadata_path or (
            Path.home() / ".rdst" / "readyset-sandbox.json"
        )
        ownership_path = self._metadata_path.with_name(
            f"{self._metadata_path.name}.lifecycle.lock"
        )
        self._ownership_lock = FileLock(
            str(ownership_path), timeout=0, thread_local=False
        )
        self._owns_lifecycle = False
        self._ownership_error: str | None = None
        self._adapter = adapter or LocalDockerSandboxAdapter(self._metadata_path)
        self._idle_ttl = idle_ttl
        self._readiness_timeout_seconds = readiness_timeout_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._condition = asyncio.Condition()
        self._start_lock = asyncio.Lock()
        self._state = SandboxState()
        self._sandbox: ProvisionedSandbox | None = None
        self._waiters: list[_Waiter] = []
        self._transition_in_progress = False
        self._sequence = 0
        self._expiry_task: asyncio.Task[None] | None = None
        self._prewarm_task: asyncio.Task[None] | None = None
        self._prewarm_tasks: set[asyncio.Task[None]] = set()
        self._prewarm_target: str | None = None
        self._retiring_targets: set[str] = set()
        self._started = False
        self._stopping = False
        self._start_count = 0
        self._ownership_release_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Reconcile a labeled sandbox left warm by a previous web process."""
        async with self._start_lock:
            if self._started:
                self._start_count += 1
                if self._ownership_error is not None:
                    await self._retry_ownership_locked()
                return
            async with self._condition:
                self._stopping = False
                self._condition.notify_all()
            if not self._owns_lifecycle:
                try:
                    await asyncio.to_thread(
                        self._metadata_path.parent.mkdir,
                        parents=True,
                        exist_ok=True,
                    )
                    await _finish_before_cancelling(
                        asyncio.to_thread(
                            self._ownership_lock.acquire, timeout=0
                        )
                    )
                except asyncio.CancelledError:
                    if self._ownership_lock.is_locked:
                        await asyncio.to_thread(self._ownership_lock.release)
                    raise
                except FileLockTimeout:
                    self._ownership_error = (
                        "Another RDST process owns the Readyset sandbox. "
                        "Close the other RDST window or wait for it to exit."
                    )
                    async with self._condition:
                        self._state = SandboxState(
                            phase="error", last_error=self._ownership_error
                        )
                        self._started = True
                        self._start_count = 1
                        if self._expiry_task is None or self._expiry_task.done():
                            self._expiry_task = asyncio.create_task(
                                self._expiry_loop()
                            )
                        self._condition.notify_all()
                    return
                except OSError:
                    self._ownership_error = (
                        "RDST could not secure exclusive ownership of the "
                        "Readyset sandbox lifecycle."
                    )
                    async with self._condition:
                        self._state = SandboxState(
                            phase="error", last_error=self._ownership_error
                        )
                        self._started = True
                        self._start_count = 1
                        if self._expiry_task is None or self._expiry_task.done():
                            self._expiry_task = asyncio.create_task(
                                self._expiry_loop()
                            )
                        self._condition.notify_all()
                    return
                self._owns_lifecycle = True
                self._ownership_error = None
            async with self._condition:
                busy = (
                    self._state.lease_owner is not None
                    or self._transition_in_progress
                    or bool(self._waiters)
                )
                if busy:
                    if self._expiry_task is None or self._expiry_task.done():
                        self._expiry_task = asyncio.create_task(self._expiry_loop())
                    self._started = True
                    self._start_count = 1
                    self._condition.notify_all()
                    return
            try:
                inspected = await _finish_before_cancelling(
                    self._adapter.inspect()
                )
            except asyncio.CancelledError:
                if self._owns_lifecycle:
                    await asyncio.to_thread(self._ownership_lock.release)
                    self._owns_lifecycle = False
                raise
            except Exception as exc:
                logger.warning("Readyset sandbox inspection unavailable: %s", exc)
                async with self._condition:
                    self._sandbox = None
                    generation = self._state.generation
                    self._state = SandboxState(
                        phase="error",
                        generation=generation,
                        last_error=(
                            "Readyset sandbox inspection is unavailable: "
                            f"{type(exc).__name__}"
                        ),
                    )
                    if self._expiry_task is None or self._expiry_task.done():
                        self._expiry_task = asyncio.create_task(self._expiry_loop())
                    self._started = True
                    self._start_count = 1
                    self._condition.notify_all()
                return
            expire_now = False
            async with self._condition:
                self._sandbox = inspected
                if inspected is None:
                    self._state = SandboxState(generation=self._state.generation)
                else:
                    metadata = _read_metadata(self._metadata_path) or {}
                    generation_raw = metadata.get("generation")
                    try:
                        generation = int(generation_raw)
                    except (TypeError, ValueError):
                        generation = self._state.generation
                    generation = max(self._state.generation, generation)
                    now = _safe_clock(self._clock)
                    released, expires_at = _release_window(
                        metadata.get("last_released_at"),
                        now,
                        self._idle_ttl,
                    )
                    self._state = SandboxState(
                        phase="ready",
                        current_target=inspected.target,
                        target_fingerprint=inspected.fingerprint,
                        generation=generation,
                        last_released_at=released,
                        expires_at=expires_at,
                    )
                    expire_now = expires_at <= now
                if self._expiry_task is None or self._expiry_task.done():
                    self._expiry_task = asyncio.create_task(self._expiry_loop())
                self._started = True
                self._start_count = 1
                self._condition.notify_all()
            if expire_now:
                await self.expire_idle()

    async def stop(self) -> None:
        """Stop manager tasks while intentionally leaving the sandbox warm."""
        async with self._start_lock:
            if self._start_count == 0:
                return
            self._start_count -= 1
            if self._start_count > 0:
                return

            async with self._condition:
                self._started = False
                self._stopping = True
                self._condition.notify_all()
            stop_cancelled = False
            task = self._expiry_task
            self._expiry_task = None
            if task is not None:
                task.cancel()
                _, wait_cancelled = await _settle_transition(task)
                stop_cancelled |= wait_cancelled
            prewarms = list(self._prewarm_tasks)
            self._prewarm_task = None
            self._prewarm_target = None
            for prewarm in prewarms:
                if not prewarm.done():
                    prewarm.cancel()
            for prewarm in prewarms:
                if not prewarm.done():
                    _, wait_cancelled = await _settle_transition(prewarm)
                    stop_cancelled |= wait_cancelled
            self._prewarm_tasks.clear()
            async with self._condition:
                safe_to_unlock = (
                    self._state.lease_owner is None
                    and not self._transition_in_progress
                    and not self._waiters
                )
            if self._owns_lifecycle and safe_to_unlock:
                await asyncio.to_thread(self._ownership_lock.release)
                self._owns_lifecycle = False
            elif self._owns_lifecycle:
                logger.warning(
                    "Keeping Readyset lifecycle ownership during active shutdown work"
                )
                self._schedule_stopped_ownership_release()
            if stop_cancelled:
                raise asyncio.CancelledError

    async def _retry_ownership(self) -> bool:
        async with self._start_lock:
            return await self._retry_ownership_locked()

    async def _retry_ownership_locked(self) -> bool:
        if self._ownership_error is None:
            return self._owns_lifecycle
        try:
            await _finish_before_cancelling(
                asyncio.to_thread(self._ownership_lock.acquire, timeout=0)
            )
        except FileLockTimeout:
            return False
        except asyncio.CancelledError:
            if self._ownership_lock.is_locked:
                await asyncio.to_thread(self._ownership_lock.release)
            raise
        except OSError:
            logger.exception("Could not reacquire Readyset lifecycle ownership")
            return False

        self._owns_lifecycle = True
        self._ownership_error = None
        async with self._condition:
            generation = self._state.generation
            self._state = SandboxState(
                phase="error",
                generation=generation,
                last_error="Reconciling Readyset sandbox ownership",
            )
            self._condition.notify_all()
        return await self._retry_reconciliation()

    def _schedule_stopped_ownership_release(self) -> None:
        if (
            self._ownership_release_task is None
            or self._ownership_release_task.done()
        ):
            self._ownership_release_task = asyncio.create_task(
                self._release_stopped_ownership_when_idle()
            )

    async def _release_stopped_ownership_when_idle(self) -> None:
        while True:
            async with self._condition:
                if (
                    self._started
                    or not self._stopping
                    or not self._owns_lifecycle
                ):
                    return
                safe_to_unlock = (
                    self._state.lease_owner is None
                    and not self._transition_in_progress
                    and not self._waiters
                )
                if not safe_to_unlock:
                    await self._condition.wait()
                    continue

            async with self._start_lock:
                async with self._condition:
                    if (
                        self._started
                        or not self._stopping
                        or not self._owns_lifecycle
                    ):
                        return
                    safe_to_unlock = (
                        self._state.lease_owner is None
                        and not self._transition_in_progress
                        and not self._waiters
                    )
                if safe_to_unlock:
                    await asyncio.to_thread(self._ownership_lock.release)
                    self._owns_lifecycle = False
                    return

    def lease(
        self,
        *,
        target: str,
        owner_id: str,
        purpose: str,
        priority: SandboxPriority = SandboxPriority.USER_TEST,
        progress: ProgressCallback | None = None,
    ) -> AsyncContextManager[SandboxLease]:
        return self._lease_context(
            target=target,
            owner_id=owner_id,
            purpose=purpose,
            priority=priority,
            progress=progress,
        )

    @asynccontextmanager
    async def _lease_context(
        self,
        *,
        target: str,
        owner_id: str,
        purpose: str,
        priority: SandboxPriority,
        progress: ProgressCallback | None,
    ) -> AsyncIterator[SandboxLease]:
        waiter = await self._claim(
            target=target,
            owner_id=owner_id,
            purpose=purpose,
            priority=priority,
            progress=progress,
        )
        lease: SandboxLease | None = None
        try:
            sandbox = None
            for attempt in range(SANDBOX_STARTUP_ATTEMPTS):
                try:
                    sandbox = await self._ensure_sandbox(waiter)
                    break
                except SandboxPortConflictError:
                    if attempt + 1 == SANDBOX_STARTUP_ATTEMPTS:
                        raise RuntimeError(
                            "Readyset could not claim a free SQL port after "
                            f"{SANDBOX_STARTUP_ATTEMPTS} startup attempts"
                        )
                    await _emit(
                        progress,
                        "starting_readyset",
                        "Retrying Readyset on another SQL port",
                    )
            assert sandbox is not None
            lease_token = uuid.uuid4().hex
            if sandbox.container_id and sandbox.instance_id:
                try:
                    await _finish_before_cancelling(
                        self._persist_active_lease(sandbox, lease_token)
                    )
                except asyncio.CancelledError:
                    async with self._condition:
                        self._state.dirty_reason = (
                            "Active Readyset lease journaling was interrupted"
                        )
                        self._state.phase = "dirty"
                        self._condition.notify_all()
                    raise
                except Exception:
                    async with self._condition:
                        self._state.dirty_reason = (
                            "Could not journal the active Readyset lease"
                        )
                        self._state.phase = "dirty"
                        self._condition.notify_all()
                    raise RuntimeError(
                        "Readyset could not safely record sandbox ownership"
                    )
            async with self._condition:
                self._state.phase = "leased"
                self._state.current_target = target
                self._state.target_fingerprint = sandbox.fingerprint
                self._state.lease_token = lease_token
                self._state.failed_target = None
                self._state.last_error = None
                self._condition.notify_all()
                lease = SandboxLease(
                    self,
                    owner_id=owner_id,
                    purpose=purpose,
                    target=target,
                    generation=self._state.generation,
                    connection=sandbox.connection,
                )
            yield lease
        finally:
            await _finish_before_cancelling(self._release(owner_id))

    def reserve_measurement(
        self,
        *,
        owner_id: str,
        purpose: str,
        priority: SandboxPriority = SandboxPriority.USER_TEST,
        progress: ProgressCallback | None = None,
    ) -> AsyncContextManager[None]:
        return self._reservation_context(
            owner_id=owner_id,
            purpose=purpose,
            priority=priority,
            progress=progress,
        )

    @asynccontextmanager
    async def _reservation_context(
        self,
        *,
        owner_id: str,
        purpose: str,
        priority: SandboxPriority,
        progress: ProgressCallback | None,
    ) -> AsyncIterator[None]:
        await self._claim(
            target=None,
            owner_id=owner_id,
            purpose=purpose,
            priority=priority,
            progress=progress,
        )
        try:
            async with self._condition:
                self._state.phase = "leased"
                self._condition.notify_all()
            yield None
        finally:
            await _finish_before_cancelling(self._release(owner_id))

    async def prewarm(self, target: str, owner_id: str) -> None:
        async with self.lease(
            target=target,
            owner_id=owner_id,
            purpose="prewarm",
            priority=SandboxPriority.PREWARM,
        ):
            return

    def request_prewarm(self, target: str) -> None:
        """Replace an obsolete queued prewarm with the latest selected target."""
        if self._prewarm_task is not None and not self._prewarm_task.done():
            if self._prewarm_target == target:
                return
            self._prewarm_task.cancel()
        self._prewarm_target = target
        task = asyncio.create_task(
            self._run_requested_prewarm(target)
        )
        self._prewarm_task = task
        self._prewarm_tasks.add(task)
        task.add_done_callback(self._prewarm_tasks.discard)

    async def _run_requested_prewarm(self, target: str) -> None:
        try:
            async with self._condition:
                self._state.failed_target = None
                self._state.last_error = None
                self._condition.notify_all()
            await self.prewarm(
                target,
                owner_id=(
                    f"prewarm-{hashlib.sha256(target.encode()).hexdigest()[:12]}"
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            async with self._condition:
                if self._prewarm_task is asyncio.current_task():
                    if self._sandbox is None and self._state.lease_owner is None:
                        self._state.phase = "error"
                    self._state.failed_target = target
                    self._state.last_error = (
                        f"Readyset could not be prepared for {target}. "
                        "Check that the database is reachable, then retry."
                    )
                self._condition.notify_all()
            logger.exception("Readyset sandbox prewarm failed for %s", target)
        finally:
            if self._prewarm_task is asyncio.current_task():
                self._prewarm_target = None

    async def mark_dirty(self, lease: SandboxLease, reason: str) -> None:
        async with self._condition:
            if (
                self._state.lease_owner != lease.owner_id
                or self._state.generation != lease.generation
            ):
                raise RuntimeError("Cannot dirty a stale Readyset sandbox lease")
            self._state.dirty_reason = reason
            self._state.phase = "dirty"
            lease_token = self._state.lease_token
            sandbox = self._sandbox
            self._condition.notify_all()
        if (
            lease_token is not None
            and sandbox is not None
            and sandbox.container_id
            and sandbox.instance_id
        ):
            try:
                await _finish_before_cancelling(
                    asyncio.to_thread(
                        _update_metadata,
                        self._metadata_path,
                        lambda metadata: _mark_metadata_dirty(
                            metadata, sandbox, lease_token
                        ),
                    )
                )
            except Exception:
                # The active marker remains set, so a restart will discard the
                # sandbox even when the stronger dirty marker cannot be saved.
                logger.exception("Could not persist dirty sandbox metadata")

    async def _persist_active_lease(
        self, sandbox: ProvisionedSandbox, lease_token: str
    ) -> None:
        await asyncio.to_thread(
            _update_metadata,
            self._metadata_path,
            lambda metadata: _mark_metadata_active(
                metadata, sandbox, lease_token
            ),
        )

    async def diagnostics(self) -> dict[str, Any]:
        async with self._condition:
            return {
                "phase": self._state.phase,
                "current_target": self._state.current_target,
                "generation": self._state.generation,
                "lease_owner": self._state.lease_owner,
                "lease_purpose": self._state.lease_purpose,
                "queued_requests": len(self._waiters),
                "dirty_reason": self._state.dirty_reason,
                "failed_target": self._state.failed_target,
                "last_error": self._state.last_error,
                "last_released_at": _iso(self._state.last_released_at),
                "expires_at": _iso(self._state.expires_at),
                "container_name": SANDBOX_CONTAINER_NAME,
                "lifecycle_owned": self._owns_lifecycle,
                "ownership_error": self._ownership_error,
                "healthy": self._sandbox is not None
                and self._state.dirty_reason is None,
            }

    def _may_own_target(self, target: str):
        return (
            self._sandbox is not None and self._sandbox.target == target
        ) or (
            self._state.dirty_reason is not None
            and self._state.current_target == target
        ) or self._state.lease_target == target or any(
            waiter.target == target for waiter in self._waiters
        )

    async def _commit_transition_state(self, update: Callable[[], None]):
        async def commit():
            async with self._condition:
                update()
                self._condition.notify_all()

        error, cancelled = await _settle_transition(commit())
        if error is not None:
            raise RuntimeError("Could not commit Readyset sandbox state") from error
        return cancelled

    async def expire_idle(self) -> bool:
        """Remove an expired, unowned sandbox. Exposed for deterministic tests."""
        async with self._condition:
            now = _safe_clock(self._clock)
            if (
                self._transition_in_progress
                or
                self._state.lease_owner is not None
                or self._waiters
                or (
                    self._sandbox is None
                    and self._state.dirty_reason is None
                )
                or self._state.expires_at is None
                or now < self._state.expires_at
            ):
                return False
            self._transition_in_progress = True
            self._state.phase = "removing"
            self._condition.notify_all()

        error, cancelled = await _settle_transition(self._adapter.remove())
        if error is not None:
            def record_expiry_failure():
                self._state.phase = "dirty"
                self._state.dirty_reason = (
                    f"Expired sandbox removal failed: {type(error).__name__}"
                )
                self._state.expires_at = _safe_expiry(
                    _safe_clock(self._clock), timedelta(minutes=1)
                )
                self._transition_in_progress = False

            cancelled |= await self._commit_transition_state(
                record_expiry_failure
            )
            logger.warning("Failed to expire Readyset sandbox: %s", error)
            if cancelled:
                raise asyncio.CancelledError
            return False

        def record_expiry():
            self._sandbox = None
            generation = self._state.generation
            self._state = SandboxState(generation=generation)
            self._transition_in_progress = False

        cancelled |= await self._commit_transition_state(record_expiry)
        if cancelled:
            raise asyncio.CancelledError
        return True

    async def remove_target(self, target: str) -> bool:
        """Remove this target's sandbox after all current work has settled."""
        async with self.retire_target(target) as removed:
            return removed

    @asynccontextmanager
    async def retire_target(self, target: str) -> AsyncIterator[bool]:
        """Fence a target through sandbox removal and config deletion."""
        async with self._condition:
            if self._ownership_error is not None:
                raise SandboxOwnershipError(self._ownership_error)
            self._retiring_targets.add(target)
            self._condition.notify_all()
        prewarm = self._prewarm_task
        try:
            if (
                self._prewarm_target == target
                and prewarm is not None
                and not prewarm.done()
            ):
                prewarm.cancel()
                _, prewarm_wait_cancelled = await _settle_transition(prewarm)
                if prewarm_wait_cancelled:
                    raise asyncio.CancelledError
            yield await self._remove_retired_target(target)
        finally:
            async with self._condition:
                self._retiring_targets.discard(target)
                self._condition.notify_all()

    async def _remove_retired_target(self, target: str) -> bool:
        async with self._condition:
            if not self._may_own_target(target):
                return False
            while self._transition_in_progress:
                await self._condition.wait()
                if not self._may_own_target(target):
                    return False
            self._transition_in_progress = True
            try:
                while self._state.lease_owner is not None:
                    await self._condition.wait()
            except BaseException:
                self._transition_in_progress = False
                self._condition.notify_all()
                raise
            if not self._may_own_target(target):
                self._transition_in_progress = False
                self._condition.notify_all()
                return False
            self._state.phase = "removing"
            self._condition.notify_all()

        error, cancelled = await _settle_transition(self._adapter.remove())

        def record_removal():
            if error is None:
                self._sandbox = None
                generation = self._state.generation
                self._state = SandboxState(generation=generation)
            else:
                self._state.phase = "dirty"
                self._state.dirty_reason = (
                    f"Target sandbox removal failed: {type(error).__name__}"
                )
                self._state.last_error = str(error)
            self._transition_in_progress = False

        cancelled |= await self._commit_transition_state(record_removal)
        if cancelled:
            raise asyncio.CancelledError
        if error is not None:
            raise RuntimeError(
                f"Could not remove Readyset sandbox for '{target}': {error}"
            ) from error
        return True

    async def _claim(
        self,
        *,
        target: str | None,
        owner_id: str,
        purpose: str,
        priority: SandboxPriority,
        progress: ProgressCallback | None,
    ) -> _Waiter:
        if (
            isinstance(self._adapter, LocalDockerSandboxAdapter)
            and not self._started
        ):
            # A process-wide manager can outlive one FastAPI lifespan (tests,
            # desktop reloads, or an embedded server restart).  ``start()``
            # serializes with an in-progress stop and clears the completed
            # shutdown marker before admitting this new lifecycle.
            await self.start()
        if self._ownership_error is not None:
            await self._retry_ownership()
        if self._state.phase == "error" and self._ownership_error is None:
            await self._retry_reconciliation()
        async with self._condition:
            if self._stopping:
                raise SandboxOwnershipError(
                    "The Readyset sandbox manager is shutting down"
                )
            if self._ownership_error is not None:
                raise SandboxOwnershipError(self._ownership_error)
            if self._state.phase == "error":
                raise SandboxReconciliationError(
                    self._state.last_error
                    or "Readyset sandbox reconciliation is unavailable"
                )
            if target is not None and target in self._retiring_targets:
                raise ValueError(f"Database target '{target}' is being removed")
            self._sequence += 1
            waiter = _Waiter(
                int(priority),
                self._sequence,
                owner_id=owner_id,
                purpose=purpose,
                target=target,
                progress=progress,
            )
            self._waiters.append(waiter)
            self._waiters.sort()
            self._condition.notify_all()
            try:
                while True:
                    if self._stopping:
                        raise SandboxOwnershipError(
                            "The Readyset sandbox manager is shutting down"
                        )
                    if target is not None and target in self._retiring_targets:
                        raise ValueError(
                            f"Database target '{target}' is being removed"
                        )
                    if not (
                        self._transition_in_progress
                        or self._state.lease_owner is not None
                        or self._waiters[0] is not waiter
                    ):
                        break
                    await self._condition.wait()
                self._waiters.remove(waiter)
                self._state.lease_owner = owner_id
                self._state.lease_purpose = purpose
                self._state.lease_target = target
                self._state.expires_at = None
                self._condition.notify_all()
                return waiter
            except BaseException:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
                self._condition.notify_all()
                if self._stopping:
                    self._schedule_stopped_ownership_release()
                raise

    async def _ensure_sandbox(self, waiter: _Waiter) -> ProvisionedSandbox:
        assert waiter.target is not None
        target_config = await asyncio.to_thread(_load_target_config, waiter.target)
        # Recheck after this request reaches the front of the queue. The
        # origin may have gone down since the API accepted the job, and both a
        # reused sandbox and a fresh container are useless without it.
        await self._adapter.require_healthy_upstream(target_config)
        fingerprint = target_fingerprint(waiter.target, target_config)
        reusable = (
            self._sandbox is not None
            and self._sandbox.target == waiter.target
            and self._sandbox.fingerprint == fingerprint
            and self._state.dirty_reason is None
        )
        if reusable:
            await _emit(
                waiter.progress,
                "preparing_sandbox",
                "Checking the existing Readyset sandbox",
            )
            try:
                await _finish_before_cancelling(
                    self._adapter.wait_ready(
                        self._sandbox,
                        timeout_seconds=min(5.0, self._readiness_timeout_seconds),
                    )
                )
            except Exception as exc:
                async with self._condition:
                    self._state.dirty_reason = (
                        "Readyset sandbox health check failed: "
                        f"{type(exc).__name__}"
                    )
            else:
                return self._sandbox

        provisioning_started = False
        try:
            if self._sandbox is not None or self._state.dirty_reason is not None:
                async with self._condition:
                    self._state.phase = "removing"
                await _emit(
                    waiter.progress,
                    "replacing_sandbox",
                    "Replacing Readyset sandbox for this target",
                )
                remove_error, remove_cancelled = await _settle_transition(
                    self._adapter.remove()
                )
                if remove_error is not None:
                    def record_replacement_failure():
                        self._state.phase = "dirty"
                        self._state.dirty_reason = (
                            "Readyset sandbox replacement failed: "
                            f"{type(remove_error).__name__}"
                        )

                    remove_cancelled |= await self._commit_transition_state(
                        record_replacement_failure
                    )
                    if remove_cancelled:
                        raise asyncio.CancelledError from remove_error
                    raise RuntimeError(
                        "Could not replace the Readyset sandbox"
                    ) from remove_error

                def record_replacement():
                    self._sandbox = None
                    self._state.phase = "absent"
                    self._state.dirty_reason = None
                    self._state.current_target = None
                    self._state.target_fingerprint = None

                remove_cancelled |= await self._commit_transition_state(
                    record_replacement
                )
                if remove_cancelled:
                    raise asyncio.CancelledError

            async with self._condition:
                self._state.phase = "provisioning"
                self._state.failed_target = None
                self._state.last_error = None
            ensure_image = getattr(self._adapter, "ensure_image_ready", None)
            if ensure_image is not None:
                async def _announce_download() -> None:
                    await _emit(
                        waiter.progress,
                        "downloading_readyset",
                        "Downloading the Readyset image (first run, one-time)",
                    )
                await _finish_before_cancelling(ensure_image(_announce_download))
            await _emit(waiter.progress, "starting_readyset", "Starting Readyset sandbox")
            provisioning_started = True
            sandbox = await _finish_before_cancelling(
                self._adapter.provision(
                    waiter.target, fingerprint, target_config
                )
            )
            await _emit(
                waiter.progress,
                "waiting_for_readyset",
                "Waiting for Readyset to accept SQL",
            )
            await _finish_before_cancelling(
                self._adapter.wait_ready(
                    sandbox, timeout_seconds=self._readiness_timeout_seconds
                )
            )
            async with self._condition:
                self._sandbox = sandbox
                self._state.generation += 1
                self._state.current_target = waiter.target
                self._state.target_fingerprint = fingerprint
                self._state.dirty_reason = None
                self._state.failed_target = None
                self._state.last_error = None
            return sandbox
        except BaseException as transition_error:
            if not provisioning_started:
                raise
            rollback_error, rollback_cancelled = await _settle_transition(
                self._adapter.remove()
            )
            if rollback_error is not None:
                logger.error(
                    "Failed to roll back Readyset sandbox provisioning",
                    exc_info=(
                        type(rollback_error),
                        rollback_error,
                        rollback_error.__traceback__,
                    ),
                )
            def record_rollback():
                self._sandbox = None
                if rollback_error is not None:
                    self._state.phase = "dirty"
                    self._state.dirty_reason = (
                        "Readyset sandbox rollback failed: "
                        f"{type(rollback_error).__name__}"
                    )
                    self._state.current_target = waiter.target
                    self._state.target_fingerprint = fingerprint
                else:
                    self._state.phase = "absent"
                    self._state.dirty_reason = None
                    self._state.current_target = None
                    self._state.target_fingerprint = None

            rollback_cancelled |= await self._commit_transition_state(
                record_rollback
            )
            if rollback_cancelled and not isinstance(
                transition_error, asyncio.CancelledError
            ):
                raise asyncio.CancelledError from transition_error
            raise

    async def _release(self, owner_id: str) -> None:
        async with self._condition:
            if self._state.lease_owner != owner_id:
                return
            now = _safe_clock(self._clock)
            sandbox = self._sandbox
            lease_token = self._state.lease_token
            dirty = self._state.dirty_reason is not None
            generation = self._state.generation
            self._transition_in_progress = True
            self._state.lease_owner = None
            self._state.lease_purpose = None
            self._state.lease_target = None
            self._state.lease_token = None
            self._state.last_released_at = now
            resource_may_exist = (
                self._sandbox is not None or self._state.dirty_reason is not None
            )
            self._state.expires_at = (
                _safe_expiry(now, self._idle_ttl)
                if resource_may_exist
                else None
            )
            self._state.phase = (
                "dirty"
                if self._state.dirty_reason
                else "ready"
                if self._sandbox is not None
                else "absent"
            )
            self._condition.notify_all()
        try:
            if (
                sandbox is not None
                and sandbox.container_id
                and sandbox.instance_id
                and lease_token is not None
            ):
                await _finish_before_cancelling(
                    asyncio.to_thread(
                        _update_metadata,
                        self._metadata_path,
                        lambda metadata: _mark_metadata_released(
                            metadata,
                            sandbox,
                            lease_token,
                            dirty=dirty,
                            released_at=now,
                            generation=generation,
                        ),
                    ),
                )
        except Exception:
            # The stale active marker is deliberately safer than claiming
            # a clean release. Startup will replace this sandbox.
            logger.exception("Could not persist Readyset sandbox release metadata")
        finally:
            async with self._condition:
                self._transition_in_progress = False
                self._condition.notify_all()
        if self._stopping:
            self._schedule_stopped_ownership_release()

    async def _expiry_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(
                    min(max(self._idle_ttl.total_seconds() / 4, 1), 60)
                )
                if self._ownership_error is not None:
                    await self._retry_ownership()
                elif self._state.phase == "error":
                    await self._retry_reconciliation()
                else:
                    await self.expire_idle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Readyset sandbox expiry check failed")

    async def _retry_reconciliation(self) -> bool:
        async with self._condition:
            if (
                self._state.phase != "error"
                or self._ownership_error is not None
                or self._state.lease_owner is not None
                or self._transition_in_progress
            ):
                return False
            self._transition_in_progress = True
        try:
            inspected = await _finish_before_cancelling(self._adapter.inspect())
        except asyncio.CancelledError:
            async with self._condition:
                self._transition_in_progress = False
                self._condition.notify_all()
            raise
        except Exception as exc:
            async with self._condition:
                self._state.last_error = (
                    "Readyset sandbox inspection is unavailable: "
                    f"{type(exc).__name__}"
                )
                self._transition_in_progress = False
                self._condition.notify_all()
            return False

        async with self._condition:
            generation = self._state.generation
            self._sandbox = inspected
            if inspected is None:
                self._state = SandboxState(generation=generation)
            else:
                now = _safe_clock(self._clock)
                metadata = _read_metadata(self._metadata_path) or {}
                released, expires_at = _release_window(
                    metadata.get("last_released_at"), now, self._idle_ttl
                )
                self._state = SandboxState(
                    phase="ready",
                    current_target=inspected.target,
                    target_fingerprint=inspected.fingerprint,
                    generation=max(
                        generation, _safe_generation(metadata.get("generation"))
                    ),
                    last_released_at=released,
                    expires_at=expires_at,
                )
            self._transition_in_progress = False
            self._condition.notify_all()
        if inspected is not None and expires_at <= now:
            await self.expire_idle()
        return True


def target_fingerprint(target: str, config: dict[str, Any]) -> str:
    """Hash connection behavior without including a password value."""
    password_source = (
        f"env:{config.get('password_env')}"
        if config.get("password_env")
        else f"secret:{config.get('password_secret_arn')}"
        if config.get("password_secret_arn")
        else f"keyring:{config.get('password_key') or target}"
        if config.get("password_key") or config.get("password_keyring")
        else "inline"
    )
    password_fingerprint = hashlib.sha256(
        resolve_password_value(config).encode()
    ).hexdigest()
    payload = {
        "deployment_version": SANDBOX_DEPLOYMENT_VERSION,
        "target": target,
        "engine": config.get("engine"),
        "host": config.get("host"),
        "port": int(config.get("port") or 0),
        "database": config.get("database"),
        "user": config.get("user") or config.get("username"),
        "tls": bool(config.get("tls")),
        "sslmode": config.get("sslmode"),
        "ssl_params": config.get("ssl_params") or {},
        "password_source": password_source,
        "password_fingerprint": password_fingerprint,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _load_target_config(target: str) -> dict[str, Any]:
    config = TargetsConfig()
    config.load()
    target_config = config.get(target)
    if target_config is None or target_config.get("target_type") == "readyset":
        raise ValueError(f"Database target '{target}' is not available")
    return dict(target_config)


async def _emit(
    callback: ProgressCallback | None, stage: str, message: str
) -> None:
    if callback is None:
        return
    result = callback(stage, message)
    if inspect.isawaitable(result):
        await result


async def _finish_before_cancelling(awaitable: Awaitable[_T]) -> _T:
    """Drain thread-backed work before allowing manager rollback to begin."""
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
            if task.done():
                break
        except BaseException:
            break
    if cancelled:
        # Observe any inner failure, then preserve cancellation as the
        # caller-visible outcome. The manager performs exact-name rollback.
        try:
            task.result()
        except BaseException:
            pass
        raise asyncio.CancelledError
    return task.result()


async def _settle_transition(
    awaitable: Awaitable[Any],
) -> tuple[BaseException | None, bool]:
    """Finish a lifecycle transition and report both failure and cancellation."""

    async def capture_error():
        try:
            await awaitable
        except BaseException as error:
            return error
        return None

    task = asyncio.create_task(capture_error())
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
            if task.done():
                break
    return task.result(), cancelled


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _safe_clock(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        logger.exception("Readyset sandbox clock failed")
        value = datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        logger.error("Readyset sandbox clock returned an invalid value")
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _safe_expiry(value: datetime, ttl: timedelta) -> datetime:
    try:
        return value + ttl
    except (OverflowError, TypeError):
        return value


def _safe_generation(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _release_window(
    released_raw: Any,
    now: datetime,
    idle_ttl: timedelta,
) -> tuple[datetime, datetime]:
    try:
        released = (
            datetime.fromisoformat(released_raw)
            if isinstance(released_raw, str)
            else now
        )
    except (TypeError, ValueError):
        released = now
    if released.tzinfo is None:
        released = released.replace(tzinfo=timezone.utc)
    if released > now:
        released = now
    try:
        expires_at = released + idle_ttl
    except (OverflowError, TypeError):
        expires_at = now
    return released, expires_at


def _read_metadata(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _write_metadata(path: Path | None, value: dict[str, Any]) -> None:
    if path is None:
        return
    write_text(path, json.dumps(value, sort_keys=True))


def _metadata_matches(
    metadata: dict[str, Any], sandbox: ProvisionedSandbox
) -> bool:
    return (
        metadata.get("target") == sandbox.target
        and metadata.get("fingerprint") == sandbox.fingerprint
        and metadata.get("container_id") == sandbox.container_id
        and metadata.get("instance_id") == sandbox.instance_id
    )


def _require_metadata_identity(
    metadata: dict[str, Any], sandbox: ProvisionedSandbox
) -> None:
    if not _metadata_matches(metadata, sandbox):
        raise SandboxReconciliationError(
            "Sandbox metadata no longer matches the active Docker container"
        )


def _mark_metadata_ready(
    metadata: dict[str, Any], sandbox: ProvisionedSandbox
) -> dict[str, Any]:
    _require_metadata_identity(metadata, sandbox)
    metadata.update(
        ready=True,
        clean=True,
        dirty=False,
        lease_active=False,
    )
    metadata.pop("lease_token", None)
    return metadata


def _mark_metadata_active(
    metadata: dict[str, Any],
    sandbox: ProvisionedSandbox,
    lease_token: str,
) -> dict[str, Any]:
    _require_metadata_identity(metadata, sandbox)
    metadata.update(
        clean=False,
        dirty=False,
        lease_active=True,
        lease_token=lease_token,
    )
    return metadata


def _mark_metadata_dirty(
    metadata: dict[str, Any],
    sandbox: ProvisionedSandbox,
    lease_token: str,
) -> dict[str, Any]:
    _require_metadata_identity(metadata, sandbox)
    if metadata.get("lease_token") != lease_token:
        raise SandboxReconciliationError("Sandbox lease metadata changed")
    metadata.update(clean=False, dirty=True, lease_active=True)
    return metadata


def _mark_metadata_released(
    metadata: dict[str, Any],
    sandbox: ProvisionedSandbox,
    lease_token: str,
    *,
    dirty: bool,
    released_at: datetime,
    generation: int,
) -> dict[str, Any]:
    _require_metadata_identity(metadata, sandbox)
    if metadata.get("lease_token") != lease_token:
        raise SandboxReconciliationError("Sandbox lease metadata changed")
    connection_metadata = metadata.get("connection")
    if isinstance(connection_metadata, dict):
        connection_metadata.pop("password", None)
    metadata.update(
        clean=not dirty,
        dirty=dirty,
        lease_active=False,
        last_released_at=released_at.isoformat(),
        generation=generation,
    )
    metadata.pop("lease_token", None)
    return metadata


def _update_metadata(path: Path | None, update) -> Any:
    if path is None:
        return {}
    value = update_json(path, update, create=False)
    if value is None:
        raise SandboxReconciliationError("Sandbox metadata is missing")
    return value


def _delete_metadata(path: Path | None) -> None:
    if path is not None:
        delete_file(path)


sandbox_manager = ReadysetSandboxManager()


__all__ = [
    "DEFAULT_IDLE_TTL",
    "SANDBOX_CONTAINER_NAME",
    "LocalDockerSandboxAdapter",
    "ProvisionedSandbox",
    "ReadysetSandboxManager",
    "SandboxConnection",
    "SandboxLease",
    "SandboxOwnershipError",
    "SandboxPortConflictError",
    "SandboxPriority",
    "SandboxReconciliationError",
    "SandboxResourceError",
    "SandboxState",
    "sandbox_manager",
    "target_fingerprint",
]

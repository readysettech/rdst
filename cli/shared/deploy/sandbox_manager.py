"""Process-local lifecycle manager for RDST's one Readyset demo sandbox.

The manager owns a capacity-one resource. Readyset experiments acquire an
exclusive lease; origin-only measurements acquire an exclusive reservation.
Docker transitions happen only after a waiter reaches the head of the
priority/FIFO queue, so an active experiment can never be replaced by a target
switch or another request in this process.

This intentionally does not provide cross-process coordination. RDST must run
one sandbox-owning web process at a time.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
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

from shared.config.targets import TargetsConfig
from shared.deploy.docker_topology import DockerTopology
from shared.persistence import delete_file, update_json, write_text
from shared.password_resolver import resolve_password_value

logger = logging.getLogger(__name__)

SANDBOX_CONTAINER_NAME = "rdst-readyset-sandbox"
DEFAULT_IDLE_TTL = timedelta(days=1)

ProgressCallback = Callable[[str, str], Awaitable[None] | None]
_T = TypeVar("_T")


class SandboxPriority(IntEnum):
    """Lower values run first; an active owner is never preempted."""

    USER_TEST = 10
    ANALYZE_VERIFY = 20
    AUDIT_BATCH = 30
    PREWARM = 100


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


@dataclass
class SandboxState:
    phase: str = "absent"
    current_target: str | None = None
    target_fingerprint: str | None = None
    generation: int = 0
    lease_owner: str | None = None
    lease_purpose: str | None = None
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
            if result.get("success"):
                _delete_metadata(self.metadata_path)
            else:
                logger.warning(
                    "Could not remove stale Readyset sandbox: %s",
                    result.get("error") or "unknown Docker error",
                )

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
        if metadata.get("ready") is not True:
            await discard_stale()
            return None
        connection = metadata.get("connection")
        if not isinstance(connection, dict):
            await discard_stale()
            return None
        try:
            target = str(metadata["target"])
            fingerprint = str(metadata["fingerprint"])
            if raw.get("target") != target or raw.get("fingerprint") != fingerprint:
                await discard_stale()
                return None
            target_config = _load_target_config(target)
            connection.pop("password", None)
            connection = {
                **connection,
                "password": resolve_password_value(target_config),
            }
            return ProvisionedSandbox(
                target=target,
                fingerprint=fingerprint,
                connection=SandboxConnection(**connection),
            )
        except (KeyError, TypeError, ValueError):
            await discard_stale()
            return None

    async def provision(
        self, target: str, fingerprint: str, target_config: dict[str, Any]
    ) -> ProvisionedSandbox:
        from shared.deploy.local_docker import (
            deploy_managed_sandbox,
            remove_configured_legacy_container,
        )
        from shared.deploy.script_generator import build_variables

        # One-time migration: only exact container names recorded on RDST
        # Readyset target rows are eligible. Similarly named foreign resources
        # are never touched.
        config = TargetsConfig()
        config.load()
        changed = False
        for name in list(config.list_targets()):
            entry = config.get(name) or {}
            legacy_name = str(entry.get("container_name") or "")
            if (
                entry.get("target_type") != "readyset"
                or not legacy_name
                or legacy_name == SANDBOX_CONTAINER_NAME
            ):
                continue
            removed = await asyncio.to_thread(
                remove_configured_legacy_container, legacy_name
            )
            if removed.get("success"):
                config.remove(name)
                changed = True
        if changed:
            config.save()

        password = resolve_password_value(target_config)
        variables = await asyncio.to_thread(
            build_variables,
            target,
            target_config,
            password,
            None,
            "readyset",
            "readyset",
            True,
        )
        variables["container_name"] = SANDBOX_CONTAINER_NAME
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
            port=int(variables["readyset_port"]),
            database=str(variables.get("db_name", "")),
            user=str(variables.get("db_user", "")),
            password=password,
            cache_target=f"{target}-sandbox",
        )
        sandbox = ProvisionedSandbox(
            target=target, fingerprint=fingerprint, connection=connection
        )
        _write_metadata(
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
            },
        )
        return sandbox

    async def wait_ready(
        self, sandbox: ProvisionedSandbox, timeout_seconds: float
    ) -> None:
        from shared.deploy.local_docker import managed_sandbox_running
        from shared.db_connection import close_connection, create_direct_connection

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        last_error = "Readyset SQL listener did not become ready"
        while asyncio.get_running_loop().time() < deadline:
            if not await asyncio.to_thread(managed_sandbox_running):
                last_error = "Readyset sandbox container is not running"
                await asyncio.sleep(1)
                continue
            conn = None
            try:
                conn = await asyncio.to_thread(
                    create_direct_connection,
                    sandbox.connection.as_target_config(),
                    3,
                )

                def _probe() -> None:
                    cursor = conn.cursor()
                    try:
                        cursor.execute("SHOW READYSET STATUS")
                        cursor.fetchall()
                    finally:
                        cursor.close()

                await asyncio.to_thread(_probe)
                _update_metadata(
                    self.metadata_path,
                    lambda metadata: {**metadata, "ready": True},
                )
                return
            except Exception as exc:
                last_error = str(exc) or type(exc).__name__
                await asyncio.sleep(1)
            finally:
                if conn is not None:
                    await asyncio.to_thread(close_connection, conn)
        raise TimeoutError(f"Readyset sandbox readiness timed out: {last_error}")

    async def remove(self) -> None:
        from shared.deploy.local_docker import remove_managed_sandbox

        result = await asyncio.to_thread(remove_managed_sandbox)
        if not result.get("success"):
            raise RuntimeError(
                result.get("error") or "Readyset sandbox removal failed"
            )
        _delete_metadata(self.metadata_path)


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
        self._prewarm_target: str | None = None
        self._started = False
        self._start_count = 0

    async def start(self) -> None:
        """Reconcile a labeled sandbox left warm by a previous web process."""
        async with self._start_lock:
            if self._started:
                self._start_count += 1
                return
            try:
                inspected = await self._adapter.inspect()
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
                    self._state.generation = max(
                        self._state.generation, generation
                    )
                    released_raw = metadata.get("last_released_at")
                    try:
                        released = (
                            datetime.fromisoformat(released_raw)
                            if isinstance(released_raw, str)
                            else self._clock()
                        )
                    except ValueError:
                        released = self._clock()
                    self._state.phase = "ready"
                    self._state.current_target = inspected.target
                    self._state.target_fingerprint = inspected.fingerprint
                    self._state.last_released_at = released
                    self._state.expires_at = released + self._idle_ttl
                if self._expiry_task is None or self._expiry_task.done():
                    self._expiry_task = asyncio.create_task(self._expiry_loop())
                self._started = True
                self._start_count = 1
                self._condition.notify_all()

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
            stop_cancelled = False
            task = self._expiry_task
            self._expiry_task = None
            if task is not None:
                task.cancel()
                _, wait_cancelled = await _settle_transition(task)
                stop_cancelled |= wait_cancelled
            prewarm = self._prewarm_task
            self._prewarm_task = None
            self._prewarm_target = None
            if prewarm is not None and not prewarm.done():
                prewarm.cancel()
                _, wait_cancelled = await _settle_transition(prewarm)
                stop_cancelled |= wait_cancelled
            if stop_cancelled:
                raise asyncio.CancelledError

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
            sandbox = await self._ensure_sandbox(waiter)
            async with self._condition:
                self._state.phase = "leased"
                self._state.current_target = target
                self._state.target_fingerprint = sandbox.fingerprint
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
        self._prewarm_task = asyncio.create_task(
            self._run_requested_prewarm(target)
        )

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
            self._condition.notify_all()

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
                "healthy": self._sandbox is not None
                and self._state.dirty_reason is None,
            }

    def _may_own_target(self, target: str):
        return (
            self._sandbox is not None and self._sandbox.target == target
        ) or (
            self._state.dirty_reason is not None
            and self._state.current_target == target
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
            now = self._clock()
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
                self._state.expires_at = self._clock() + timedelta(minutes=1)
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
        prewarm = self._prewarm_task
        if (
            self._prewarm_target == target
            and prewarm is not None
            and not prewarm.done()
        ):
            prewarm.cancel()
            _, prewarm_wait_cancelled = await _settle_transition(prewarm)
            if prewarm_wait_cancelled:
                raise asyncio.CancelledError

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
        async with self._condition:
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
                while (
                    self._transition_in_progress
                    or self._state.lease_owner is not None
                    or self._waiters[0] is not waiter
                ):
                    await self._condition.wait()
                self._waiters.remove(waiter)
                self._state.lease_owner = owner_id
                self._state.lease_purpose = purpose
                self._state.expires_at = None
                self._condition.notify_all()
                return waiter
            except BaseException:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
                self._condition.notify_all()
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
            now = self._clock()
            self._state.lease_owner = None
            self._state.lease_purpose = None
            self._state.last_released_at = now
            resource_may_exist = (
                self._sandbox is not None or self._state.dirty_reason is not None
            )
            self._state.expires_at = (
                now + self._idle_ttl if resource_may_exist else None
            )
            self._state.phase = (
                "dirty"
                if self._state.dirty_reason
                else "ready"
                if self._sandbox is not None
                else "absent"
            )
            if self._sandbox is not None:
                def mark_released(metadata):
                    connection_metadata = metadata.get("connection")
                    if isinstance(connection_metadata, dict):
                        connection_metadata.pop("password", None)
                    metadata["last_released_at"] = now.isoformat()
                    metadata["generation"] = self._state.generation
                    return metadata

                _update_metadata(self._metadata_path, mark_released)
            self._condition.notify_all()

    async def _expiry_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(min(max(self._idle_ttl.total_seconds() / 4, 1), 60))
                await self.expire_idle()
        except asyncio.CancelledError:
            raise


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


def _update_metadata(path: Path | None, update) -> None:
    if path is None:
        return
    update_json(path, update, create=False)


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
    "SandboxPriority",
    "SandboxState",
    "sandbox_manager",
    "target_fingerprint",
]

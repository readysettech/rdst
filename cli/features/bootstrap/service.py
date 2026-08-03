"""Add-database bootstrap orchestrator.

Composes existing services into one background run: a connection test, then
a schema track (structure -> profile -> key gate -> annotate). Readyset is
provisioned lazily by explicit comparisons, never by target creation. Yields
BootstrapEvent dataclasses shaped for shared.run_registry.RunRegistry: the
needs_key event parks the run's status while adapters raise their key/trial
UI, and annotation resumes when a key save wakes it, with polling as a
fallback.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any, AsyncGenerator, Callable

from shared.anthropic_env import validate_anthropic_key
from shared.async_utils import run_blocking
from shared.service_events import ErrorEvent

from .events import (
    STAGE_ANNOTATE,
    STAGE_CONNECTION_TEST,
    STAGE_PROFILE,
    STAGE_STRUCTURE,
    BootstrapEvent,
    BootstrapNeedsKeyEvent,
    BootstrapStageEvent,
)

_TRACK_DONE = object()


@dataclass
class BootstrapOptions:
    annotate: bool = True
    # How often the annotate gate re-validates while its schema track is live.
    key_poll_seconds: float = 5.0
    # A wedged schema/deploy collaborator must settle the background run visibly.
    track_timeout_seconds: float = 900.0


def _stage(
    stage: str, status: str, message: str = "", detail: dict | None = None
) -> BootstrapStageEvent:
    return BootstrapStageEvent(
        type="bootstrap_stage", stage=stage, status=status, message=message, detail=detail
    )


def _progress(stage: str, child: Any) -> BootstrapStageEvent:
    """Surface a child-service event as a progress record of its stage."""
    return _stage(
        stage, "progress", getattr(child, "message", "") or "", asdict(child)
    )


class TargetBootstrapService:
    """Orchestrates the post-add bootstrap for one database target.

    Collaborators are injectable for tests; defaults import lazily so the
    module stays cheap to import.
    """

    def __init__(
        self,
        configure_service: Any = None,
        schema_service: Any = None,
        annotate_service: Any = None,
        key_validator: Callable[[], dict] | None = None,
    ):
        self._configure = configure_service
        self._schema = schema_service
        self._annotate = annotate_service
        self._key_validator = key_validator or validate_anthropic_key

    async def run(
        self,
        target: str,
        target_config: dict[str, Any],
        options: BootstrapOptions | None = None,
        key_wakeup: asyncio.Event | None = None,
    ) -> AsyncGenerator[BootstrapEvent, None]:
        options = options or BootstrapOptions()

        yield _stage(
            STAGE_CONNECTION_TEST, "started", f"Testing connection to {target}..."
        )
        try:
            test_config = {**target_config, "name": target}
            result = await run_blocking(self._connection_test_sync, test_config)
        except Exception as exc:
            result = {"success": False, "message": str(exc)}
        if not result.get("success"):
            message = result.get("message") or "Connection failed"
            yield _stage(STAGE_CONNECTION_TEST, "failed", message)
            yield ErrorEvent(
                type="error",
                message=message,
                code="CONNECTION_FAILED",
                stage=STAGE_CONNECTION_TEST,
            )
            return
        yield _stage(
            STAGE_CONNECTION_TEST,
            "done",
            result.get("message", ""),
            {
                "server_version": result.get("server_version"),
                "privileges": result.get("privileges"),
            },
        )

        # The schema track is pumped through a queue so failures and timeouts become
        # visible events instead of stranding the background run.
        queue: asyncio.Queue = asyncio.Queue()
        tracks = [
            (
                "schema",
                self._schema_track(target, target_config, options, key_wakeup),
            )
        ]
        tasks = [
            asyncio.create_task(
                self._pump(
                    track_name,
                    track,
                    queue,
                    options.track_timeout_seconds,
                )
            )
            for track_name, track in tracks
        ]
        try:
            remaining = len(tasks)
            while remaining:
                item = await queue.get()
                if item is _TRACK_DONE:
                    remaining -= 1
                    continue
                yield item
        finally:
            for task in tasks:
                task.cancel()

    @staticmethod
    async def _pump(
        track_name: str,
        gen: AsyncGenerator,
        queue: asyncio.Queue,
        timeout_seconds: float,
    ) -> None:
        """Drain one track into the queue; a track failure becomes an error
        event rather than killing its sibling."""
        try:
            await asyncio.wait_for(
                TargetBootstrapService._drain_track(gen, queue),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            message = (
                f"{track_name.capitalize()} bootstrap track timed out after "
                f"{timeout_seconds:g} seconds"
            )
            await queue.put(_stage(track_name, "failed", message))
            await queue.put(
                ErrorEvent(
                    type="error",
                    message=message,
                    code="BOOTSTRAP_TRACK_TIMEOUT",
                    stage=track_name,
                )
            )
        except Exception as exc:
            message = str(exc) or f"{track_name.capitalize()} bootstrap track failed"
            await queue.put(_stage(track_name, "failed", message))
            await queue.put(
                ErrorEvent(
                    type="error",
                    message=message,
                    code="BOOTSTRAP_TRACK_FAILED",
                    stage=track_name,
                )
            )
        finally:
            try:
                await gen.aclose()
            except (Exception, asyncio.CancelledError):
                pass
            await queue.put(_TRACK_DONE)

    @staticmethod
    async def _drain_track(gen: AsyncGenerator, queue: asyncio.Queue) -> None:
        async for event in gen:
            await queue.put(event)

    async def _schema_track(
        self,
        target: str,
        target_config: dict[str, Any],
        options: BootstrapOptions,
        key_wakeup: asyncio.Event | None,
    ) -> AsyncGenerator[BootstrapEvent, None]:
        schema = self._schema_service()

        yield _stage(STAGE_STRUCTURE, "started", "Fetching database structure...")
        status = await run_blocking(schema.get_status, target)
        if status.exists:
            # Never force-init an existing layer: refresh merges structural
            # changes while preserving annotations.
            refreshed = await run_blocking(schema.refresh, target, target_config)
            ok, message = bool(refreshed.get("ok")), refreshed.get("message", "")
        else:
            initialized = await run_blocking(schema.init, target, target_config)
            ok = initialized.success
            message = initialized.error or f"{initialized.tables} tables found"
        if not ok:
            yield _stage(STAGE_STRUCTURE, "failed", message)
            return
        yield _stage(STAGE_STRUCTURE, "done", message)

        yield _stage(STAGE_PROFILE, "started", "Profiling columns...")
        profiled = await run_blocking(schema.profile, target, target_config)
        if profiled.get("ok"):
            yield _stage(STAGE_PROFILE, "done", profiled.get("message", ""))
        else:
            # Annotation still runs; prompts just carry fewer statistics.
            yield _stage(STAGE_PROFILE, "failed", profiled.get("message", ""))

        if not options.annotate:
            yield _stage(STAGE_ANNOTATE, "skipped", "Annotation disabled")
            return
        validity = await run_blocking(self._key_validator)
        if not validity.get("valid"):
            yield BootstrapNeedsKeyEvent(
                type="needs_key",
                message=(
                    "An Anthropic key unlocks AI schema descriptions. Add a key "
                    "or start a free trial to continue."
                ),
            )
            validity = await self._await_key(options, key_wakeup)
        yield _stage(STAGE_ANNOTATE, "started", "Generating AI descriptions...")
        failure: str | None = None
        async for event in self._annotate_service().annotate(target, target_config):
            if event.type == "annotate_error":
                failure = event.message
            elif event.type == "annotate_complete" and not getattr(
                event, "success", True
            ):
                failure = event.message or "Some AI descriptions could not be generated"
            yield _progress(STAGE_ANNOTATE, event)
        if failure:
            yield _stage(STAGE_ANNOTATE, "failed", failure)
        else:
            yield _stage(STAGE_ANNOTATE, "done", "AI descriptions generated")

    async def _await_key(
        self, options: BootstrapOptions, key_wakeup: asyncio.Event | None
    ) -> dict:
        """Wait for a usable key until the run is cancelled or RDST exits.

        The web's own-key and trial flows wake this wait immediately. The
        backoff remains for credentials changed through another in-process
        path and as protection against a missed notification.
        """
        interval = options.key_poll_seconds
        while True:
            if key_wakeup is None:
                await asyncio.sleep(interval)
            else:
                try:
                    await asyncio.wait_for(key_wakeup.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
                else:
                    key_wakeup.clear()
            interval = min(interval * 2, options.key_poll_seconds * 6)
            validity = await run_blocking(self._key_validator)
            if validity.get("valid"):
                return validity

    def _connection_test_sync(self, target_config: dict[str, Any]) -> dict:
        """Run the configure test on a worker thread.

        ConfigureService.perform_connection_test is declared async but has no
        await points: its body is blocking DB I/O. Advance that coroutine once
        on the worker instead of nesting an event loop in the worker thread.
        """
        test = self._configure_service().perform_connection_test(target_config)
        try:
            test.send(None)
        except StopIteration as complete:
            return complete.value
        finally:
            test.close()
        raise RuntimeError("Connection test unexpectedly yielded")

    def _configure_service(self) -> Any:
        if self._configure is None:
            from features.configure.service import ConfigureService

            self._configure = ConfigureService()
        return self._configure

    def _schema_service(self) -> Any:
        if self._schema is None:
            from features.schema.service import SchemaService

            self._schema = SchemaService()
        return self._schema

    def _annotate_service(self) -> Any:
        if self._annotate is None:
            from features.schema.annotate_service import AnnotateService

            self._annotate = AnnotateService()
        return self._annotate

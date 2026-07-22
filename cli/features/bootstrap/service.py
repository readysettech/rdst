"""Add-database bootstrap orchestrator.

Composes existing services into one background run: a connection test, then
a schema track (structure -> profile -> key gate -> annotate) in parallel
with an optional Readyset deploy track. Yields BootstrapEvent dataclasses
shaped for shared.run_registry.RunRegistry: the needs_key event parks the
run's status while adapters raise their key/trial UI, and annotation
resumes when a key lands (polled) or is skipped after the wait budget.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any, AsyncGenerator, Callable

from shared.anthropic_env import validate_anthropic_key
from shared.service_events import ErrorEvent

from .events import (
    STAGE_ANNOTATE,
    STAGE_CONNECTION_TEST,
    STAGE_DEPLOY,
    STAGE_PROFILE,
    STAGE_STRUCTURE,
    BootstrapEvent,
    BootstrapNeedsKeyEvent,
    BootstrapStageEvent,
)

_TRACK_DONE = object()


@dataclass
class BootstrapOptions:
    deploy: bool = True
    deploy_mode: str = "docker"
    annotate: bool = True
    # How long the annotate gate waits for a usable key after emitting
    # needs_key, and how often it re-validates while waiting.
    key_wait_seconds: float = 600.0
    key_poll_seconds: float = 5.0


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
        cache_service: Any = None,
        key_validator: Callable[[], dict] | None = None,
    ):
        self._configure = configure_service
        self._schema = schema_service
        self._annotate = annotate_service
        self._cache = cache_service
        self._key_validator = key_validator or validate_anthropic_key

    async def run(
        self,
        target: str,
        target_config: dict[str, Any],
        options: BootstrapOptions | None = None,
    ) -> AsyncGenerator[BootstrapEvent, None]:
        options = options or BootstrapOptions()

        yield _stage(
            STAGE_CONNECTION_TEST, "started", f"Testing connection to {target}..."
        )
        try:
            result = await asyncio.to_thread(self._connection_test_sync, target_config)
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
            {"server_version": result.get("server_version")},
        )

        # Independent tracks share one queue; a sentinel per track marks its
        # end so the merged stream closes exactly when both are done.
        queue: asyncio.Queue = asyncio.Queue()
        tracks = [self._schema_track(target, target_config, options)]
        if options.deploy:
            tracks.append(self._deploy_track(target, options))
        tasks = [asyncio.create_task(self._pump(track, queue)) for track in tracks]
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
    async def _pump(gen: AsyncGenerator, queue: asyncio.Queue) -> None:
        """Drain one track into the queue; a track failure becomes an error
        event rather than killing its sibling."""
        try:
            async for event in gen:
                await queue.put(event)
        except Exception as exc:
            await queue.put(
                ErrorEvent(
                    type="error", message=str(exc), code="BOOTSTRAP_TRACK_FAILED"
                )
            )
        finally:
            await queue.put(_TRACK_DONE)

    async def _schema_track(
        self, target: str, target_config: dict[str, Any], options: BootstrapOptions
    ) -> AsyncGenerator[BootstrapEvent, None]:
        schema = self._schema_service()

        yield _stage(STAGE_STRUCTURE, "started", "Fetching database structure...")
        status = await asyncio.to_thread(schema.get_status, target)
        if status.exists:
            # Never force-init an existing layer: refresh merges structural
            # changes while preserving annotations.
            refreshed = await asyncio.to_thread(schema.refresh, target, target_config)
            ok, message = bool(refreshed.get("ok")), refreshed.get("message", "")
        else:
            initialized = await asyncio.to_thread(schema.init, target, target_config)
            ok = initialized.success
            message = initialized.error or f"{initialized.tables} tables found"
        if not ok:
            yield _stage(STAGE_STRUCTURE, "failed", message)
            return
        yield _stage(STAGE_STRUCTURE, "done", message)

        yield _stage(STAGE_PROFILE, "started", "Profiling columns...")
        profiled = await asyncio.to_thread(schema.profile, target, target_config)
        if profiled.get("ok"):
            yield _stage(STAGE_PROFILE, "done", profiled.get("message", ""))
        else:
            # Annotation still runs; prompts just carry fewer statistics.
            yield _stage(STAGE_PROFILE, "failed", profiled.get("message", ""))

        if not options.annotate:
            yield _stage(STAGE_ANNOTATE, "skipped", "Annotation disabled")
            return
        validity = await asyncio.to_thread(self._key_validator)
        if not validity.get("valid"):
            yield BootstrapNeedsKeyEvent(
                type="needs_key",
                message=(
                    "An Anthropic key unlocks AI schema descriptions. Add a key "
                    "or start a free trial to continue."
                ),
            )
            validity = await self._await_key(options)
        if not validity.get("valid"):
            yield _stage(
                STAGE_ANNOTATE,
                "skipped",
                "No usable Anthropic key; skipping AI descriptions.",
            )
            return

        yield _stage(STAGE_ANNOTATE, "started", "Generating AI descriptions...")
        failure: str | None = None
        async for event in self._annotate_service().annotate(target, target_config):
            if event.type == "annotate_error":
                failure = event.message
            yield _progress(STAGE_ANNOTATE, event)
        if failure:
            yield _stage(STAGE_ANNOTATE, "failed", failure)
        else:
            yield _stage(STAGE_ANNOTATE, "done", "AI descriptions generated")

    async def _await_key(self, options: BootstrapOptions) -> dict:
        """Poll key validity until it lands or the wait budget runs out.

        Each no-key check pays a config read plus keyring lookups (that path
        is not cached), so the interval backs off toward a cap; a key set via
        the web lands in the process env and is seen on the next tick.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + options.key_wait_seconds
        interval = options.key_poll_seconds
        while loop.time() < deadline:
            await asyncio.sleep(interval)
            interval = min(interval * 2, options.key_poll_seconds * 6)
            validity = await asyncio.to_thread(self._key_validator)
            if validity.get("valid"):
                return validity
        return {"valid": False}

    async def _deploy_track(
        self, target: str, options: BootstrapOptions
    ) -> AsyncGenerator[BootstrapEvent, None]:
        from features.cache.models import CacheInput, CacheOptions

        yield _stage(STAGE_DEPLOY, "started", "Deploying Readyset...")
        outcome: Any = None
        deploy = self._cache_service().deploy(
            CacheInput(target=target),
            CacheOptions(mode=options.deploy_mode, yes=True),
        )
        async for event in deploy:
            if event.type in ("deploy_complete", "error"):
                outcome = event
            yield _progress(STAGE_DEPLOY, event)
        if outcome is not None and outcome.type == "deploy_complete" and outcome.success:
            yield _stage(
                STAGE_DEPLOY,
                "done",
                "Readyset ready",
                {"endpoint": outcome.endpoint},
            )
        else:
            yield _stage(
                STAGE_DEPLOY,
                "failed",
                getattr(outcome, "message", "") or "Readyset deploy failed",
            )

    def _connection_test_sync(self, target_config: dict[str, Any]) -> dict:
        """Run the configure test on a worker thread.

        ConfigureService.perform_connection_test is declared async but its
        body is blocking DB I/O, so it gets a private loop off the main one.
        """
        return asyncio.run(
            self._configure_service().perform_connection_test(target_config)
        )

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

    def _cache_service(self) -> Any:
        if self._cache is None:
            from features.cache.service import CacheService

            self._cache = CacheService()
        return self._cache

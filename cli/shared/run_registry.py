"""In-memory background run registry with replayable events.

A run wraps an async generator of event dataclasses. start() schedules it on
the event loop and returns a run_id immediately; the run then outlives any
HTTP request. Every yielded event is buffered in memory, so a subscriber can
replay what it missed and then continue live (SSE reattach) while the process
is alive. Registry state is mutated only on the event loop, so no locking is
needed. Runs intentionally do not survive an RDST restart.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Callable, Iterable, Literal

from shared.service_events import ErrorEvent

TERMINAL_STATUSES = ("done", "partial", "failed", "cancelled")

# Yielding this event type parks the run's status on "needs_key" until the
# next event arrives (the adapter shows its key/trial UI in the meantime).
NEEDS_KEY_EVENT = "needs_key"

# Appended as the final record of every run so subscribers settle cleanly.
RUN_END_EVENT = "run_end"

# A quick audit, a duration capture, and a fleet audit all hold database
# connections for their whole duration, and the web client shows one
# health-check indicator, so only one of these kinds runs at a time across
# every target.
AUDIT_RUN_KINDS = ("audit", "audit_capture", "fleet_audit")


@dataclass
class _RunHandle:
    kind: str
    target: str
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "running"
    events: list[dict] = field(default_factory=list)
    waiters: list[asyncio.Future] = field(default_factory=list)
    resume_event: asyncio.Event | None = None
    child_error_events: frozenset[str] = frozenset()
    task: asyncio.Task | None = None


@dataclass
class RunEndEvent:
    """Terminal event appended by the registry after every run."""

    type: Literal["run_end"]
    status: str


def _event_payload(event: Any) -> tuple[str, dict]:
    """Extract (event_name, data) from a yielded event dataclass."""
    if is_dataclass(event) and not isinstance(event, type):
        data = asdict(event)
        return str(data.pop("type", event.__class__.__name__)), data
    raise TypeError(f"Run events must be event dataclasses, got {type(event)!r}")


class RunRegistry:
    """Registry of detached background runs with in-memory event replay."""

    def __init__(self, max_finished: int = 32):
        self._runs: dict[str, _RunHandle] = {}
        self._max_finished = max_finished

    def start(
        self,
        kind: str,
        target: str,
        gen: AsyncGenerator[Any, None],
        metadata: dict[str, Any] | None = None,
        resume_event: asyncio.Event | None = None,
        child_error_events: frozenset[str] | None = None,
    ) -> str:
        """Schedule a run and return its run_id immediately.

        `child_error_events` names events that report an isolated failure of
        one child track inside an orchestrated run (one target of a fleet,
        say). Those never fail the run: an opted-in run's outcome comes from
        its own `complete` event's `success` flag, from the generator dying,
        or from cancellation.
        """
        run_id = self._new_run_id(kind, target)
        handle = _RunHandle(
            kind=kind,
            target=target,
            metadata=dict(metadata or {}),
            resume_event=resume_event,
            child_error_events=child_error_events or frozenset(),
        )
        handle.task = asyncio.get_running_loop().create_task(self._drive(handle, gen))
        self._runs[run_id] = handle
        handle.task.add_done_callback(
            lambda task: self._terminalize_cancelled_before_start(handle, task)
        )
        return run_id

    def start_factory(
        self,
        kind: str,
        target: str,
        factory: Callable[[str], AsyncGenerator[Any, None]],
        metadata: dict[str, Any] | None = None,
        resume_event: asyncio.Event | None = None,
        child_error_events: frozenset[str] | None = None,
    ) -> str:
        """Start a generator that needs its stable run ID for ownership."""
        run_id = self._new_run_id(kind, target)
        handle = _RunHandle(
            kind=kind,
            target=target,
            metadata=dict(metadata or {}),
            resume_event=resume_event,
            child_error_events=child_error_events or frozenset(),
        )
        generator = factory(run_id)
        handle.task = asyncio.get_running_loop().create_task(
            self._drive(handle, generator)
        )
        self._runs[run_id] = handle
        handle.task.add_done_callback(
            lambda task: self._terminalize_cancelled_before_start(handle, task)
        )
        return run_id

    @staticmethod
    def _new_run_id(kind: str, target: str) -> str:
        safe_target = "".join(c if c.isalnum() or c in "-_" else "-" for c in target)
        return (
            f"{kind}_{safe_target}_"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_"
            f"{uuid.uuid4().hex[:6]}"
        )

    def status(self, run_id: str) -> str | None:
        """Return the current status, or None when this process does not know it."""
        handle = self._runs.get(run_id)
        return handle.status if handle is not None else None

    def describe(self, run_id: str) -> dict[str, Any] | None:
        """Return the current process-local run metadata."""
        handle = self._runs.get(run_id)
        if handle is None:
            return None
        return {
            "run_id": run_id,
            "kind": handle.kind,
            "target": handle.target,
            "status": handle.status,
            "last_seq": len(handle.events),
            "metadata": handle.metadata,
        }

    def find_active(
        self, kinds: str | Iterable[str], target: str | None = None
    ) -> str | None:
        """Find an active run, newest first. A target of None matches any target."""
        accepted = {kinds} if isinstance(kinds, str) else set(kinds)
        for run_id, handle in reversed(self._runs.items()):
            if (
                handle.kind in accepted
                and (target is None or handle.target == target)
                and handle.status not in TERMINAL_STATUSES
            ):
                return run_id
        return None

    def find_active_matching(
        self,
        kind: str,
        target: str,
        metadata: dict[str, Any],
        *,
        keys: Iterable[str],
    ) -> str | None:
        """Find a live run whose selected metadata fields exactly match."""
        selected = tuple(keys)
        for run_id, handle in reversed(self._runs.items()):
            if (
                handle.kind == kind
                and handle.target == target
                and handle.status not in TERMINAL_STATUSES
                and all(handle.metadata.get(key) == metadata.get(key) for key in selected)
            ):
                return run_id
        return None

    def cancel(self, run_id: str) -> bool:
        """Cancel a live run. Returns False for unknown or finished runs."""
        handle = self._runs.get(run_id)
        if handle is None or handle.status in TERMINAL_STATUSES:
            return False
        if handle.task is None:
            return False
        handle.task.cancel()
        return True

    def cancel_target(self, target: str) -> int:
        """Cancel every live run for a target and return the number signalled."""
        cancelled = 0
        for handle in self._runs.values():
            if (
                handle.target == target
                and handle.status not in TERMINAL_STATUSES
                and handle.task is not None
            ):
                handle.task.cancel()
                cancelled += 1
        return cancelled

    def reset(self) -> int:
        """Cancel every live run and forget all runs (local data reset).

        Attached subscribers still settle: their generators hold the handle,
        and cancellation appends the terminal record that wakes them.
        Returns the number of runs cancelled.
        """
        cancelled = 0
        for handle in self._runs.values():
            if handle.status not in TERMINAL_STATUSES and handle.task is not None:
                handle.task.cancel()
                cancelled += 1
        self._runs.clear()
        return cancelled

    def wake_needs_key(self) -> int:
        """Wake every run currently parked on the shared key gate."""
        woken = 0
        for handle in self._runs.values():
            if handle.status == NEEDS_KEY_EVENT and handle.resume_event is not None:
                handle.resume_event.set()
                woken += 1
        return woken

    async def events(
        self, run_id: str, after_seq: int = 0
    ) -> AsyncGenerator[dict, None]:
        """Replay events with seq > after_seq, then stream live until terminal.

        Raises KeyError for a run_id unknown to this process.
        """
        handle = self._runs.get(run_id)
        if handle is None:
            raise KeyError(f"Unknown run: {run_id}")

        index = after_seq
        while True:
            while index < len(handle.events):
                record = handle.events[index]
                index += 1
                yield record
            if handle.status in TERMINAL_STATUSES:
                return
            future = asyncio.get_running_loop().create_future()
            handle.waiters.append(future)
            await future

    async def _drive(self, handle: _RunHandle, gen: AsyncGenerator[Any, None]) -> None:
        status = "done"
        try:
            async for event in gen:
                name, data = _event_payload(event)
                self._append(handle, name, data)
                if name == "complete" and handle.child_error_events:
                    # A run that isolates child failures reports its own
                    # outcome; one failed child must not condemn the run.
                    if data.get("success") is False:
                        status = "failed"
                elif name not in handle.child_error_events and (
                    name == "error" or name.endswith("_error")
                ):
                    preserved_speed_result = (
                        data.get("code") == "speed_test_cleanup_failed"
                        and any(
                            record["event"] == "cache_run_complete"
                            for record in handle.events
                        )
                    )
                    status = (
                        "partial"
                        if preserved_speed_result and status != "failed"
                        else "failed"
                    )
                    # Some orchestrators isolate child-track failures by
                    # yielding an error event and continuing to drain their
                    # healthy siblings. Keep consuming so closing this driver
                    # does not cancel that remaining work; the run still ends
                    # failed once the generator is exhausted.
                # Status only turns terminal below, after this loop exits.
                handle.status = (
                    NEEDS_KEY_EVENT if name == NEEDS_KEY_EVENT else "running"
                )
        except asyncio.CancelledError:
            status = "cancelled"
        except Exception as exc:
            name, data = _event_payload(
                ErrorEvent(type="error", message=str(exc), code="RUN_FAILED")
            )
            self._append(handle, name, data)
            status = "failed"
        finally:
            # Always emit a terminal record so attached subscribers settle.
            try:
                await gen.aclose()
            except (Exception, asyncio.CancelledError):
                pass
            handle.status = status
            self._append(handle, RUN_END_EVENT, {"status": status})
            self._evict_finished()

    def _append(self, handle: _RunHandle, name: str, data: dict) -> None:
        record = {
            "seq": len(handle.events) + 1,
            "event": name,
            "data": data,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        handle.events.append(record)
        waiters, handle.waiters = handle.waiters, []
        for future in waiters:
            if not future.done():
                future.set_result(None)

    def _terminalize_cancelled_before_start(
        self, handle: _RunHandle, task: asyncio.Task
    ) -> None:
        """Finish tasks cancelled before their driver coroutine gets a turn."""
        if not task.cancelled() or handle.status in TERMINAL_STATUSES:
            return
        handle.status = "cancelled"
        self._append(handle, RUN_END_EVENT, {"status": "cancelled"})
        self._evict_finished()

    def _evict_finished(self) -> None:
        """Forget the oldest finished runs beyond the in-memory retention cap."""
        finished = [
            run_id
            for run_id, handle in self._runs.items()
            if handle.status in TERMINAL_STATUSES
        ]
        for run_id in finished[: max(0, len(finished) - self._max_finished)]:
            del self._runs[run_id]


# The one process-wide registry used by every web background-run adapter.
run_registry = RunRegistry()


__all__ = [
    "NEEDS_KEY_EVENT",
    "RUN_END_EVENT",
    "TERMINAL_STATUSES",
    "RunEndEvent",
    "RunRegistry",
    "run_registry",
]

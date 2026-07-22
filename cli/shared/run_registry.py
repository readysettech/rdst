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
from typing import Any, AsyncGenerator, Iterable, Literal

from shared.service_events import ErrorEvent

TERMINAL_STATUSES = ("done", "failed", "cancelled")

# Yielding this event type parks the run's status on "needs_key" until the
# next event arrives (the adapter shows its key/trial UI in the meantime).
NEEDS_KEY_EVENT = "needs_key"

# Appended as the final record of every run so subscribers settle cleanly.
RUN_END_EVENT = "run_end"


@dataclass
class _RunHandle:
    kind: str
    target: str
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "running"
    events: list[dict] = field(default_factory=list)
    waiters: list[asyncio.Future] = field(default_factory=list)
    resume_event: asyncio.Event | None = None
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
    ) -> str:
        """Schedule a run and return its run_id immediately."""
        safe_target = "".join(c if c.isalnum() or c in "-_" else "-" for c in target)
        run_id = (
            f"{kind}_{safe_target}_"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_"
            f"{uuid.uuid4().hex[:6]}"
        )
        handle = _RunHandle(
            kind=kind,
            target=target,
            metadata=dict(metadata or {}),
            resume_event=resume_event,
        )
        handle.task = asyncio.get_running_loop().create_task(self._drive(handle, gen))
        self._runs[run_id] = handle
        return run_id

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

    def find_active(self, kinds: str | Iterable[str], target: str) -> str | None:
        """Find an active run for a target, newest first."""
        accepted = {kinds} if isinstance(kinds, str) else set(kinds)
        for run_id, handle in reversed(self._runs.items()):
            if (
                handle.kind in accepted
                and handle.target == target
                and handle.status not in TERMINAL_STATUSES
            ):
                return run_id
        return None

    def cancel(self, run_id: str) -> bool:
        """Cancel a live run. Returns False for unknown or finished runs."""
        handle = self._runs.get(run_id)
        if handle is None or handle.status in TERMINAL_STATUSES:
            return False
        handle.task.cancel()
        return True

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
                if name == "error" or name.endswith("_error"):
                    status = "failed"
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

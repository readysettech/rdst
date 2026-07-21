"""Background run registry: detached async runs with replayable event logs.

A run wraps an async generator of event dataclasses. start() schedules it on
the event loop and returns a run_id immediately; the run then outlives any
HTTP request. Every yielded event is appended to ``~/.rdst/runs/<run_id>.jsonl``
and buffered in memory, so a subscriber can replay what it missed and then
continue live (SSE reattach). Registry state is mutated only on the event
loop, so no locking is needed; the small synchronous disk append keeps memory
and disk in step within a single loop turn.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

from shared.constants import rdst_runs_dir
from shared.service_events import ErrorEvent

TERMINAL_STATUSES = ("done", "failed", "cancelled", "interrupted")

# Yielding this event type parks the run's status on "needs_key" until the
# next event arrives (the adapter shows its key/trial UI in the meantime).
NEEDS_KEY_EVENT = "needs_key"

# Appended as the final record of every run; carries the terminal status so
# a fresh process can tell a finished log from an interrupted one.
RUN_END_EVENT = "run_end"


@dataclass
class _RunHandle:
    path: Path
    status: str = "running"
    events: list[dict] = field(default_factory=list)
    waiters: list[asyncio.Future] = field(default_factory=list)
    task: asyncio.Task | None = None


def _event_payload(event: Any) -> tuple[str, dict]:
    """Extract (event_name, data) from a yielded event dataclass."""
    if is_dataclass(event) and not isinstance(event, type):
        data = asdict(event)
        return str(data.pop("type", event.__class__.__name__)), data
    raise TypeError(f"Run events must be event dataclasses, got {type(event)!r}")


class RunRegistry:
    """Registry of detached background runs with persisted, replayable events."""

    def __init__(self, base_dir: Path | None = None, max_finished: int = 32):
        self._base_dir = base_dir
        self._runs: dict[str, _RunHandle] = {}
        self._max_finished = max_finished

    def start(self, kind: str, target: str, gen: AsyncGenerator[Any, None]) -> str:
        """Schedule a run and return its run_id immediately."""
        safe_target = "".join(c if c.isalnum() or c in "-_" else "-" for c in target)
        run_id = (
            f"{kind}_{safe_target}_"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_"
            f"{uuid.uuid4().hex[:6]}"
        )
        path = self._run_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = _RunHandle(path=path)
        handle.task = asyncio.get_running_loop().create_task(self._drive(handle, gen))
        self._runs[run_id] = handle
        return run_id

    def status(self, run_id: str) -> str | None:
        """Current status, from memory or (for evicted/old runs) from disk."""
        handle = self._runs.get(run_id)
        if handle is not None:
            return handle.status
        records = self._read_disk(run_id)
        if records is None:
            return None
        if records and records[-1]["event"] == RUN_END_EVENT:
            return records[-1]["data"].get("status", "done")
        return "interrupted"

    def cancel(self, run_id: str) -> bool:
        """Cancel a live run. Returns False for unknown or finished runs."""
        handle = self._runs.get(run_id)
        if handle is None or handle.status in TERMINAL_STATUSES:
            return False
        handle.task.cancel()
        return True

    async def events(
        self, run_id: str, after_seq: int = 0
    ) -> AsyncGenerator[dict, None]:
        """Replay events with seq > after_seq, then stream live until terminal.

        Raises KeyError for a run_id with neither a live handle nor a log on
        disk.
        """
        handle = self._runs.get(run_id)
        if handle is None:
            records = self._read_disk(run_id)
            if records is None:
                raise KeyError(f"Unknown run: {run_id}")
            for record in records:
                if record["seq"] > after_seq:
                    yield record
            return

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
            # The terminal record must be written no matter how generator
            # cleanup goes, or the run would read as interrupted forever.
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
        with open(handle.path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
        waiters, handle.waiters = handle.waiters, []
        for future in waiters:
            if not future.done():
                future.set_result(None)

    def _evict_finished(self) -> None:
        """Drop the oldest finished runs beyond the cap; their logs remain on
        disk, so events() falls back to replay."""
        finished = [
            run_id
            for run_id, handle in self._runs.items()
            if handle.status in TERMINAL_STATUSES
        ]
        for run_id in finished[: max(0, len(finished) - self._max_finished)]:
            del self._runs[run_id]

    def _run_path(self, run_id: str) -> Path:
        base = self._base_dir if self._base_dir is not None else rdst_runs_dir()
        return base / f"{run_id}.jsonl"

    def _read_disk(self, run_id: str) -> list[dict] | None:
        path = self._run_path(run_id)
        if not path.exists():
            return None
        records = []
        for line in path.read_text().splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

"""Process-wide target serialization for cancellable monitor thread work."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()


class RunGuard:
    def __init__(self) -> None:
        self.stopped = threading.Event()

    def check(self) -> None:
        if self.stopped.is_set():
            raise asyncio.CancelledError()

    def run(self, target: str, function: Callable[..., Any], *args: Any) -> Any:
        with _registry_lock:
            lock = _locks.setdefault(target, threading.Lock())
        with lock:
            self.check()
            from shared.monitor.monitor_store import guard_statements

            with guard_statements(self):
                return function(*args)


class ThreadWork:
    def __init__(self) -> None:
        self.guard = RunGuard()
        self.tasks: set[asyncio.Task] = set()
        self.connections: list[tuple[Any, str, dict[str, Any] | None]] = []
        self._connections_lock = threading.Lock()

    async def run(self, target: str, function: Callable[..., Any], *args: Any) -> Any:
        task = asyncio.create_task(asyncio.to_thread(self.guard.run, target, function, *args))
        self.tasks.add(task)
        def finished(done: asyncio.Task) -> None:
            self.tasks.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
        return await asyncio.shield(task)

    def register_connection(self, conn: Any, engine: str,
                            config: dict[str, Any] | None = None) -> None:
        with self._connections_lock:
            self.guard.check()
            self.connections.append((conn, engine, config))

    def unregister_connection(self, conn: Any) -> None:
        with self._connections_lock:
            self.connections = [row for row in self.connections if row[0] is not conn]

    async def stop(self, timeout: float = 15) -> bool:
        self.guard.stopped.set()
        with self._connections_lock:
            connections = list(self.connections)
        if connections:
            from shared.db_connection import cancel_query

            try:
                await asyncio.wait_for(asyncio.gather(
                    *(asyncio.to_thread(cancel_query, *row) for row in connections),
                    return_exceptions=True), timeout=5)
            except asyncio.TimeoutError:
                pass
        if not self.tasks:
            return True
        _, pending = await asyncio.wait(self.tasks, timeout=timeout)
        return not pending

"""Process-wide admission and retry timing for background database access."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class _State:
    failures: int = 0
    due: float = 0.0
    active: bool = False


class TargetBackoff:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._states: dict[str, _State] = {}
        self._lock = threading.Lock()

    def acquire(self, target: str) -> bool:
        with self._lock:
            state = self._states.get(target)
            if state is None:
                # Admission stays bounded even under target creation churn.
                if len(self._states) >= 1024:
                    return False
                state = self._states.setdefault(target, _State())
            if state.active or self.clock() < state.due:
                return False
            state.active = True
            return True

    def finish(self, target: str, *, success: bool) -> None:
        with self._lock:
            state = self._states.get(target)
            if state is None:
                return
            state.active = False
            if success:
                del self._states[target]
            else:
                self._fail(state)

    def record_failure(self, target: str) -> None:
        """Share an asynchronous tunnel failure while preserving worker admission."""
        with self._lock:
            if target not in self._states and len(self._states) >= 1024:
                return
            self._fail(self._states.setdefault(target, _State()))

    def _fail(self, state: _State) -> None:
        # The tunnel and its database caller can report the same failed operation.
        if self.clock() < state.due:
            return
        state.failures = min(state.failures + 1, 6)
        state.due = self.clock() + min(30 * 2 ** (state.failures - 1), 900)

    def failed(self, target: str) -> bool:
        with self._lock:
            state = self._states.get(target)
            return bool(state and state.failures)

    def retry_after(self, target: str) -> float:
        with self._lock:
            state = self._states.get(target)
            return max(0.0, state.due - self.clock()) if state is not None else 0.0

    def limit_retry(self, target: str, seconds: float) -> None:
        """Keep monitored recovery checks within their reading cadence, retaining admission."""
        with self._lock:
            state = self._states.get(target)
            if state is not None:
                state.due = min(state.due, self.clock() + seconds)

    def release(self, target: str) -> None:
        """Release admission when no database operation was attempted."""
        with self._lock:
            state = self._states.get(target)
            if state is not None:
                state.active = False
                if not state.failures:
                    del self._states[target]

    def forget(self, target: str) -> None:
        with self._lock:
            state = self._states.get(target)
            if state is not None and not state.active:
                del self._states[target]


target_backoff = TargetBackoff()

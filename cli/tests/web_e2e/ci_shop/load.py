"""The ci-shop load driver: steady traffic plus phases a test switches on and off.

It runs inside the test server process on its own threads and talks to the database directly,
never through the proxy that RDST uses, so a cut connection is an outage of the monitor alone.
"""

from __future__ import annotations

import heapq
import itertools
import logging
import queue
import random
import threading
import time
from collections import Counter
from typing import Any, Callable

from tests.web_e2e.ci_shop import catalogue
from tests.web_e2e.ci_shop.catalogue import Shape
from tests.web_e2e.ci_shop.schema import SIZES

logger = logging.getLogger(__name__)

NOISE_FACTOR = 5
WORKERS = 6
BACKLOG_LIMIT = 300
EVENT_ID_START = 1_000_000


class LoadDriver:
    def __init__(self, connect: Callable[[], Any], *, seed: int = 20260930) -> None:
        self.connect = connect
        self.phases = {"burst": False, "noise": False, "new_heavy": False}
        self.calls: Counter[str] = Counter()
        self.errors: Counter[str] = Counter()
        self.last_error: dict[str, str] = {}
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._jobs: queue.Queue[Shape | None] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._event_ids = itertools.count(EVENT_ID_START)
        self._inserted: list[int] = []
        self.started_at: float | None = None
        self._version = 0

    def rate(self, shape: Shape) -> float:
        phases = self.phases
        if shape.role in ("burst", "burst_only"):
            return shape.burst_rate if phases["burst"] else shape.rate
        if shape.role == "new_heavy":
            return shape.burst_rate if phases["new_heavy"] else 0.0
        if shape.role == "stopped":
            return 0.0
        if shape.name == catalogue.NOISE and phases["noise"]:
            return shape.rate * NOISE_FACTOR
        return shape.rate

    def set_phase(self, name: str, on: bool) -> dict[str, bool]:
        if name not in self.phases:
            raise ValueError(f"Unknown phase {name}")
        with self._lock:
            self.phases[name] = on
            self._version += 1
        return dict(self.phases)

    def start(self) -> None:
        self.started_at = time.time()
        self._threads = [threading.Thread(target=self._dispatch, name="ci-shop-dispatch",
                                          daemon=True)]
        self._threads += [threading.Thread(target=self._work, name=f"ci-shop-load-{index}",
                                           daemon=True) for index in range(WORKERS)]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        for _ in range(WORKERS):
            self._jobs.put(None)
        for thread in self._threads:
            thread.join(timeout=10)

    def _dispatch(self) -> None:
        def schedule() -> list[tuple[float, int, Shape]]:
            now = time.monotonic()
            due = [(now + self._rng.random() / max(1.0, self.rate(shape)), index, shape)
                   for index, shape in enumerate(catalogue.ALL)]
            heapq.heapify(due)
            return due

        version = self._version
        due = schedule()
        while not self._stop.is_set():
            if version != self._version:
                # A phase change applies its rates now, not after each shape's old interval.
                version = self._version
                due = schedule()
            at, index, shape = due[0]
            wait = at - time.monotonic()
            if wait > 0:
                self._stop.wait(min(wait, 0.05))
                continue
            heapq.heappop(due)
            rate = self.rate(shape)
            if rate <= 0:
                heapq.heappush(due, (at + 0.5, index, shape))
                continue
            if self._jobs.qsize() < BACKLOG_LIMIT:
                self._jobs.put(shape)
            next_at = at + (0.8 + 0.4 * self._rng.random()) / rate
            # A dispatcher that fell behind resumes the schedule instead of catching up at once.
            heapq.heappush(due, (max(next_at, time.monotonic() - 1.0), index, shape))

    def _params(self, shape: Shape) -> tuple:
        with self._lock:
            if shape.name == "w4_record_event":
                event_id = next(self._event_ids)
                self._inserted.append(event_id)
                return (event_id, 1 + event_id % SIZES["customers"], "cart", event_id % 365, 42)
            if shape.name == "w5_forget_event":
                if len(self._inserted) > 20:
                    return (self._inserted.pop(0),)
                return (EVENT_ID_START - 1,)
            return shape.params(self._rng)

    def _work(self) -> None:
        connection = None
        while not self._stop.is_set():
            shape = self._jobs.get()
            if shape is None:
                break
            try:
                if connection is None:
                    connection = self.connect()
                with connection.cursor() as cursor:
                    cursor.execute(shape.sql, self._params(shape))
                    if cursor.description:
                        cursor.fetchall()
                self.calls[shape.name] += 1
            except Exception as exc:
                self.errors[shape.name] += 1
                self.last_error[shape.name] = str(exc)[:300]
                try:
                    if connection is not None:
                        connection.close()
                except Exception:
                    pass
                connection = None
                self._stop.wait(0.2)
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def stats(self) -> dict[str, Any]:
        elapsed = max(1.0, time.time() - (self.started_at or time.time()))
        return {"phases": dict(self.phases), "elapsed_s": round(elapsed, 1),
                "calls": dict(self.calls), "errors": dict(self.errors),
                "last_error": dict(self.last_error), "backlog": self._jobs.qsize(),
                "qps": round(sum(self.calls.values()) / elapsed, 1)}

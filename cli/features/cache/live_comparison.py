"""Live origin-vs-Readyset capacity comparison runner.

Both lanes receive the same closed-model concurrency. Throughput is an
observed result rather than a target, so a faster Readyset lane is free to
complete substantially more requests per second than the upstream.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

from .performance_comparison import (
    ComparisonCancelled,
    ComparisonController,
    _execute_on_connection,
    _open_persistent_connection,
)

MIN_COMPARE_CONCURRENCY_PER_LANE = 1
MAX_COMPARE_CONCURRENCY_PER_LANE = 32
MAX_COMPARE_IN_FLIGHT_PER_LANE = 32
MIN_COMPARE_DURATION_SECONDS = 10
MAX_COMPARE_DURATION_SECONDS = 180
COMPARE_SAMPLE_INTERVAL_SECONDS = 1.0
COMPARE_DRAIN_GRACE_SECONDS = 5.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (
        position - lower
    )


def _summary(
    latencies: list[float],
    scheduled: int,
    completed: int,
    errors: int,
    dropped: int,
    duration_seconds: float,
) -> dict[str, float | int]:
    mean = sum(latencies) / len(latencies) if latencies else 0.0
    return {
        "scheduled": scheduled,
        "completed": completed,
        "errors": errors,
        "dropped": dropped,
        "throughput_rps": completed / max(duration_seconds, 0.001),
        "error_rate": errors / max(completed + errors, 1),
        "mean_ms": mean,
        "p50_ms": _percentile(latencies, 0.50),
        "p95_ms": _percentile(latencies, 0.95),
        "p99_ms": _percentile(latencies, 0.99),
    }


class LiveComparisonController(ComparisonController):
    """Cancellation plus a thread-safe load dial for a running comparison."""

    def __init__(self, concurrency: int):
        super().__init__()
        self._load_lock = threading.Lock()
        self._concurrency = concurrency

    @property
    def concurrency(self) -> int:
        with self._load_lock:
            return self._concurrency

    def set_concurrency(self, concurrency: int) -> None:
        if not (
            MIN_COMPARE_CONCURRENCY_PER_LANE
            <= concurrency
            <= MAX_COMPARE_CONCURRENCY_PER_LANE
        ):
            raise ValueError(
                "Concurrency must be between "
                f"{MIN_COMPARE_CONCURRENCY_PER_LANE} and "
                f"{MAX_COMPARE_CONCURRENCY_PER_LANE} clients per lane."
            )
        with self._load_lock:
            self._concurrency = concurrency


class _LaneRunner:
    def __init__(
        self,
        name: str,
        query: str,
        db_config: Dict[str, Any],
        controller: LiveComparisonController,
        measurements: queue.Queue,
    ):
        self.name = name
        self.query = query
        self.db_config = db_config
        self.controller = controller
        self.measurements = measurements
        self.executor = ThreadPoolExecutor(
            max_workers=MAX_COMPARE_IN_FLIGHT_PER_LANE,
            thread_name_prefix=f"compare-{name}",
        )
        self.local = threading.local()
        self.lock = threading.Lock()
        self.in_flight = 0

    def _connection(self):
        connection = getattr(self.local, "connection", None)
        engine = getattr(self.local, "engine", None)
        if connection is None:
            connection, engine = _open_persistent_connection(self.db_config)
            self.controller.register(connection)
            self.local.connection = connection
            self.local.engine = engine
        return connection, engine

    def _execute(self) -> dict[str, Any]:
        connection, engine = self._connection()
        return _execute_on_connection(
            connection,
            self.query,
            engine,
            self.controller,
        )

    def submit(self) -> bool:
        with self.lock:
            if self.in_flight >= MAX_COMPARE_IN_FLIGHT_PER_LANE:
                return False
            self.in_flight += 1
        try:
            future = self.executor.submit(self._execute)
        except Exception:
            with self.lock:
                self.in_flight -= 1
            raise
        future.add_done_callback(self._complete)
        return True

    def _complete(self, future: Future) -> None:
        try:
            result = future.result()
        except ComparisonCancelled:
            result = {"success": False, "cancelled": True}
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
        finally:
            with self.lock:
                self.in_flight -= 1
        self.measurements.put((self.name, result))

    def current_in_flight(self) -> int:
        with self.lock:
            return self.in_flight

    def shutdown(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)


def _empty_lane_bucket() -> dict[str, Any]:
    return {
        "scheduled": 0,
        "completed": 0,
        "errors": 0,
        "dropped": 0,
        "latencies": [],
    }


def _bucket_payload(
    bucket: dict[str, Any],
    duration_seconds: float,
    in_flight: int,
) -> dict[str, float | int]:
    summary = _summary(
        bucket["latencies"],
        bucket["scheduled"],
        bucket["completed"],
        bucket["errors"],
        bucket["dropped"],
        duration_seconds,
    )
    summary["in_flight"] = in_flight
    return summary


def run_live_comparison(
    query: str,
    original_db_config: Dict[str, Any],
    readyset_db_config: Dict[str, Any],
    duration_seconds: int,
    controller: LiveComparisonController,
    on_sample: Optional[Callable[[dict[str, Any]], None]] = None,
) -> dict[str, Any]:
    """Execute an equal-concurrency comparison and return observed capacity."""
    measurements: queue.Queue = queue.Queue()
    origin = _LaneRunner(
        "origin", query, original_db_config, controller, measurements
    )
    readyset = _LaneRunner(
        "readyset", query, readyset_db_config, controller, measurements
    )
    lanes = {"origin": origin, "readyset": readyset}
    buckets = {name: _empty_lane_bucket() for name in lanes}
    totals = {name: _empty_lane_bucket() for name in lanes}
    timeline: list[dict[str, Any]] = []
    phases: list[dict[str, float]] = []

    start = time.monotonic()
    last_sample = start
    next_sample = start + COMPARE_SAMPLE_INTERVAL_SECONDS
    observed_concurrency = controller.concurrency
    phases.append(
        {"elapsed_seconds": 0.0, "concurrency": observed_concurrency}
    )

    def record_measurement(lane_name: str, result: dict[str, Any]) -> None:
        bucket = buckets[lane_name]
        total = totals[lane_name]
        if result.get("success"):
            latency = float(result["execution_time_ms"])
            bucket["completed"] += 1
            total["completed"] += 1
            bucket["latencies"].append(latency)
            total["latencies"].append(latency)
        elif not result.get("cancelled"):
            bucket["errors"] += 1
            total["errors"] += 1

    def drain_measurements(wait_timeout: float = 0.0) -> None:
        if wait_timeout > 0:
            try:
                lane_name, result = measurements.get(timeout=wait_timeout)
                record_measurement(lane_name, result)
            except queue.Empty:
                return
        while True:
            try:
                lane_name, result = measurements.get_nowait()
            except queue.Empty:
                break
            record_measurement(lane_name, result)

    def emit_sample(now: float) -> None:
        nonlocal buckets, last_sample
        drain_measurements()
        width = max(now - last_sample, 0.001)
        payload = {
            "elapsed_seconds": min(now - start, float(duration_seconds)),
            "concurrency": controller.concurrency,
            "origin": _bucket_payload(
                buckets["origin"], width, origin.current_in_flight()
            ),
            "readyset": _bucket_payload(
                buckets["readyset"], width, readyset.current_in_flight()
            ),
        }
        timeline.append(payload)
        if on_sample:
            on_sample(payload)
        buckets = {name: _empty_lane_bucket() for name in lanes}
        last_sample = now

    try:
        while True:
            controller.raise_if_cancelled()
            now = time.monotonic()
            elapsed = now - start
            if elapsed >= duration_seconds:
                break

            concurrency = controller.concurrency
            if concurrency != observed_concurrency:
                observed_concurrency = concurrency
                phases.append(
                    {
                        "elapsed_seconds": elapsed,
                        "concurrency": concurrency,
                    }
                )

            for lane_name, lane in lanes.items():
                bucket = buckets[lane_name]
                total = totals[lane_name]
                due = max(0, concurrency - lane.current_in_flight())
                for _ in range(due):
                    if lane.submit():
                        bucket["scheduled"] += 1
                        total["scheduled"] += 1

            # Refill immediately after a lane completes. A fixed polling sleep
            # would impose an artificial ceiling on the faster lane and make
            # Readyset appear slower than its actual capacity.
            drain_measurements(
                wait_timeout=min(
                    0.01,
                    max(next_sample - time.monotonic(), 0.0001),
                )
            )
            now = time.monotonic()
            if now >= next_sample:
                emit_sample(now)
                while next_sample <= now:
                    next_sample += COMPARE_SAMPLE_INTERVAL_SECONDS

        # Give already-scheduled work a bounded opportunity to finish. Queries
        # that exceed the grace window are interrupted and counted as errors.
        drain_deadline = time.monotonic() + COMPARE_DRAIN_GRACE_SECONDS
        while (
            origin.current_in_flight() > 0 or readyset.current_in_flight() > 0
        ) and time.monotonic() < drain_deadline:
            controller.raise_if_cancelled()
            drain_measurements(wait_timeout=0.01)
            now = time.monotonic()
            if now >= next_sample:
                emit_sample(now)
                next_sample += COMPARE_SAMPLE_INTERVAL_SECONDS

        if origin.current_in_flight() > 0 or readyset.current_in_flight() > 0:
            controller.close_connections()
        drain_measurements()
        if any(
            buckets[name]["scheduled"]
            or buckets[name]["completed"]
            or buckets[name]["errors"]
            or buckets[name]["dropped"]
            for name in lanes
        ):
            emit_sample(time.monotonic())

        elapsed_total = max(time.monotonic() - start, 0.001)
        measurement_window = max(float(duration_seconds), 0.001)
        origin_summary = _summary(
            totals["origin"]["latencies"],
            totals["origin"]["scheduled"],
            totals["origin"]["completed"],
            totals["origin"]["errors"],
            totals["origin"]["dropped"],
            measurement_window,
        )
        readyset_summary = _summary(
            totals["readyset"]["latencies"],
            totals["readyset"]["scheduled"],
            totals["readyset"]["completed"],
            totals["readyset"]["errors"],
            totals["readyset"]["dropped"],
            measurement_window,
        )
        success = (
            origin_summary["completed"] > 0
            and readyset_summary["completed"] > 0
        )
        speedup = (
            float(origin_summary["mean_ms"]) / float(readyset_summary["mean_ms"])
            if float(readyset_summary["mean_ms"]) > 0
            else 0.0
        )
        return {
            "success": success,
            "query": query,
            "duration_seconds": duration_seconds,
            "elapsed_seconds": elapsed_total,
            "concurrency": controller.concurrency,
            "origin": origin_summary,
            "readyset": readyset_summary,
            "timeline": timeline,
            "phases": phases,
            "speedup_mean": speedup,
            "improvement_pct": (speedup - 1) * 100,
            "winner": "readyset" if speedup > 1 else "origin",
            "error": None
            if success
            else "No successful measurements were collected from both lanes.",
        }
    except ComparisonCancelled:
        return {
            "success": False,
            "cancelled": True,
            "error": "Comparison cancelled",
        }
    finally:
        controller.close_connections()
        origin.shutdown()
        readyset.shutdown()

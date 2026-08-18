"""Temporary Readyset experiments backed by the process-local sandbox manager."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
import uuid
from queue import Empty, Queue
from typing import Any, AsyncGenerator, Callable

import sqlglot

from features.query_registry.service import benchmark_read_only_reason
from shared.async_utils import start_blocking
from shared.config.targets import TargetsConfig
from shared.deploy.sandbox_manager import (
    ReadysetSandboxManager,
    SandboxLease,
    SandboxPriority,
    sandbox_manager,
)
from shared.password_resolver import resolve_password_value
from shared.query_registry.observation_store import ExecutionEvidenceWriter
from shared.service_events import ErrorEvent, ProgressEvent

from .events import (
    CacheCompareCompleteEvent,
    CacheCompareSampleEvent,
    CacheEvent,
    CacheRunCompleteEvent,
)
from .live_comparison import LiveComparisonController, run_live_comparison
from .performance_comparison import (
    ComparisonController,
    run_comparison,
)
from .service import CacheService

_DONE = object()
_CACHE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

# Bound on post-cancel settling so a hung driver connect or execute cannot stall cancellation.
COMPARE_CANCEL_GRACE_SECONDS = 10


class _CompareEvidenceRecorder:
    """Persist origin completions and tokenized in-flight uncertainty."""

    def __init__(self, target: str, query: str) -> None:
        self.target = target
        self.query = query
        self.run_id = uuid.uuid4().hex
        self._buckets: dict[int, tuple[int, float, float]] = {}
        self._dirty: set[int] = set()
        self._outstanding: dict[Any, float] = {}
        self._missing_since: float | None = None
        self._lock = threading.Lock()
        self._writer = ExecutionEvidenceWriter(target, lane="rdst/compare")
        self.total = 0
        self.last_event_at: float | None = None

    def note_started(self, token: Any, occurred_at: float | None = None) -> None:
        at = time.time() if occurred_at is None else float(occurred_at)
        with self._lock:
            self._outstanding[token] = at

    def note_completed(
        self, token: Any, occurred_at: float | None = None, count: int = 1
    ) -> None:
        if isinstance(count, bool) or count <= 0:
            return
        at = time.time() if occurred_at is None else float(occurred_at)
        second = int(at)
        with self._lock:
            # A completion without a matching start cannot prove which
            # outstanding execution finished, so keep the older uncertainty.
            if token not in self._outstanding:
                self._missing_since = (
                    at
                    if self._missing_since is None
                    else min(self._missing_since, at)
                )
                return
            self._outstanding.pop(token)
            previous = self._buckets.get(second)
            if previous is None:
                self._buckets[second] = (count, at, at)
            else:
                old_count, first, last = previous
                self._buckets[second] = (
                    old_count + count,
                    min(first, at),
                    max(last, at),
                )
            self._dirty.add(second)
            self.total += count
            self.last_event_at = (
                at if self.last_event_at is None else max(self.last_event_at, at)
            )

    def reconcile(self, expected_total: int, uncertain_from: float) -> bool:
        """Verify callbacks without inventing a time for missing completions."""
        with self._lock:
            exact = (
                expected_total == self.total
                and not self._outstanding
                and self._missing_since is None
            )
            if not exact:
                candidates = [float(uncertain_from), *self._outstanding.values()]
                if self._missing_since is not None:
                    candidates.append(self._missing_since)
                self._missing_since = min(candidates)
            return exact

    def uncertain_started_at(self) -> float | None:
        with self._lock:
            candidates = list(self._outstanding.values())
            if self._missing_since is not None:
                candidates.append(self._missing_since)
            return min(candidates) if candidates else None

    async def flush_closed(self, now: float | None = None) -> None:
        current = int(time.time() if now is None else now)
        with self._lock:
            seconds = tuple(second for second in self._dirty if second < current)
        await self._flush(seconds)

    async def flush_all(self) -> None:
        with self._lock:
            seconds = tuple(self._dirty)
        await self._flush(seconds)

    async def _flush(self, seconds) -> None:
        for second in sorted(tuple(seconds)):
            with self._lock:
                bucket = self._buckets.get(second)
                if bucket is None or second not in self._dirty:
                    continue
                count, first, last = bucket
                self._dirty.discard(second)
            await asyncio.to_thread(
                self._writer.record,
                [{"sql": self.query, "exec_count": count}],
                run_id=f"{self.run_id}:exact:{second}",
                started_at=first,
                ended_at=last,
            )

    async def record_unknown(self, started_at: float, ended_at: float) -> None:
        await asyncio.to_thread(
            self._writer.record,
            [{"sql": self.query, "exec_count": None}],
            run_id=f"{self.run_id}:unknown",
            started_at=started_at,
            ended_at=ended_at,
        )

    async def close(self) -> None:
        await asyncio.to_thread(self._writer.close)


def parameter_fingerprint(query: str) -> str:
    """Fingerprint the concrete SQL without retaining it in job metadata."""
    return hashlib.sha256(query.strip().encode()).hexdigest()


def temporary_cache_name(owner_id: str, query: str) -> str:
    owner = re.sub(r"[^a-z0-9]", "", owner_id.lower())[-12:] or "run"
    query_hash = hashlib.sha256(query.strip().encode()).hexdigest()[:12]
    name = f"rdst_tmp_{owner}_{query_hash}"
    if not _CACHE_NAME_RE.fullmatch(name):
        raise ValueError("Could not generate a safe Readyset cache name")
    return name


class ReadysetExperimentService:
    """Public cache-feature surface for verification and measured speed tests."""

    def __init__(
        self,
        manager: ReadysetSandboxManager | None = None,
        cache_service: CacheService | None = None,
    ) -> None:
        self._manager = manager or sandbox_manager
        self._cache = cache_service or CacheService()

    async def compare(
        self,
        *,
        owner_id: str,
        target: str,
        query: str,
        iterations: int = 15,
        warmup: int = 5,
        interval_ms: int | None = None,
        concurrency: int | None = None,
        duration_seconds: int | None = None,
    ) -> AsyncGenerator[CacheEvent, None]:
        """Provision, verify, create, validate, run bounded measurements, and clean up."""
        queue: asyncio.Queue[Any] = asyncio.Queue()
        worker = asyncio.create_task(
            self._compare_worker(
                queue=queue,
                owner_id=owner_id,
                target=target,
                query=query,
                iterations=iterations,
                warmup=warmup,
                interval_ms=interval_ms,
                concurrency=concurrency,
                duration_seconds=duration_seconds,
                live_controller=None,
            )
        )
        try:
            while True:
                event = await queue.get()
                if event is _DONE:
                    break
                yield event
        except asyncio.CancelledError:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            raise
        finally:
            if not worker.done():
                worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    async def compare_live(
        self,
        *,
        owner_id: str,
        target: str,
        query: str,
        duration_seconds: int,
        controller: LiveComparisonController,
    ) -> AsyncGenerator[CacheEvent, None]:
        """Run a live equal-concurrency comparison in the managed sandbox."""
        queue: asyncio.Queue[Any] = asyncio.Queue()
        worker = asyncio.create_task(
            self._compare_worker(
                queue=queue,
                owner_id=owner_id,
                target=target,
                query=query,
                iterations=1,
                warmup=0,
                interval_ms=None,
                concurrency=controller.concurrency,
                duration_seconds=duration_seconds,
                live_controller=controller,
            )
        )
        try:
            while True:
                event = await queue.get()
                if event is _DONE:
                    break
                yield event
        except asyncio.CancelledError:
            controller.cancel()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            raise
        finally:
            if not worker.done():
                controller.cancel()
                worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    async def _compare_worker(
        self,
        *,
        queue: asyncio.Queue[Any],
        owner_id: str,
        target: str,
        query: str,
        iterations: int,
        warmup: int,
        interval_ms: int | None,
        concurrency: int | None,
        duration_seconds: int | None,
        live_controller: LiveComparisonController | None,
    ) -> None:
        cache_name = temporary_cache_name(owner_id, query)
        created = False
        result_event: CacheRunCompleteEvent | CacheCompareCompleteEvent | None = None
        error_event: ErrorEvent | None = None
        benchmark_started_at: float | None = None
        evidence = _CompareEvidenceRecorder(target, query)

        async def progress(stage: str, message: str, percent: int = 0) -> None:
            await queue.put(
                ProgressEvent(
                    type="progress", stage=stage, percent=percent, message=message
                )
            )

        # The request supplies this SQL and it is both interpolated into cache
        # DDL and executed against the origin, so a speed test only ever runs a
        # single read-only statement.
        not_read_only = benchmark_read_only_reason(query)
        if not_read_only:
            await queue.put(
                ErrorEvent(
                    type="error",
                    message=not_read_only,
                    code="speed_test_read_only",
                    stage="checking_query",
                )
            )
            await queue.put(_DONE)
            return

        try:
            await progress("queued", "Queued for the Readyset sandbox", 0)
            async with self._manager.lease(
                target=target,
                owner_id=owner_id,
                purpose="live_compare" if live_controller else "speed_test",
                priority=SandboxPriority.USER_TEST,
                progress=lambda stage, message: progress(stage, message, 10),
            ) as acquired:
                try:
                    origin = await _blocking_call(
                        _origin_connection_config, target
                    )
                    readyset = acquired.connection.as_target_config()
                    readyset_query = _readyset_query(query, readyset["engine"])

                    await progress(
                        "checking_query", "Checking Readyset compatibility", 25
                    )
                    explain, cancelled = await _run_readyset_sql_settled(
                        self._cache,
                        f"EXPLAIN CREATE CACHE FROM {readyset_query}",
                        acquired,
                    )
                    if cancelled:
                        raise asyncio.CancelledError
                    if not explain.get("success"):
                        raise RuntimeError(
                            "Readyset could not verify this query. "
                            + str(explain.get("error") or "")
                        )
                    if _explain_is_unsupported(str(explain.get("output") or "")):
                        raise ValueError("This query is unsupported by Readyset.")

                    await progress(
                        "creating_test_cache",
                        "Creating a temporary Readyset cache",
                        40,
                    )
                    create, cancelled = await _run_readyset_sql_settled(
                        self._cache,
                        f"CREATE CACHE {cache_name} FROM {readyset_query}",
                        acquired,
                    )
                    if not create.get("success"):
                        raise RuntimeError(
                            "Readyset could not create the temporary cache. "
                            + str(create.get("error") or "")
                        )
                    created = True
                    if cancelled:
                        raise asyncio.CancelledError

                    await progress("warming", "Warming the temporary cache", 50)
                    await _execute_rows_cancellable(readyset, query)

                    await progress(
                        "validating_results",
                        "Validating origin and Readyset results",
                        58,
                    )

                    def mark_origin_execute_started() -> str:
                        token = "validation"
                        evidence.note_started(token, time.time())
                        return token

                    def mark_origin_execute_completed(
                        token: str, completed_at: float
                    ) -> None:
                        evidence.note_completed(token, completed_at)

                    origin_rows, readyset_rows = await _execute_validation_pair(
                        origin,
                        readyset,
                        query,
                        on_origin_execute=mark_origin_execute_started,
                        on_origin_complete=mark_origin_execute_completed,
                        on_wait=evidence.flush_closed,
                    )
                    # A completed validation pair contains exactly one origin
                    # execution, even when the result comparison below fails.
                    await evidence.flush_closed()
                    results_match = _canonical_rows(
                        origin_rows, order_sensitive=False
                    ) == _canonical_rows(readyset_rows, order_sensitive=False)
                    order_indexes = _top_level_order_key_indexes(query)
                    if results_match and order_indexes is not None:
                        results_match = _canonical_order_keys(
                            origin_rows, order_indexes
                        ) == _canonical_order_keys(readyset_rows, order_indexes)
                    if not results_match:
                        if _has_top_level_limit_without_order(query):
                            raise RuntimeError(
                                "Origin and Readyset returned different rows. "
                                "This query uses LIMIT without ORDER BY, so "
                                "the database may return any matching rows "
                                "and the two results are not comparable. Add "
                                "an ORDER BY to the query for a meaningful "
                                "comparison."
                            )
                        raise RuntimeError(
                            "Readyset returned a different result from the origin; "
                            "the speed test was stopped."
                        )

                    await progress(
                        "benchmarking_origin",
                        "Benchmarking origin and Readyset",
                        65,
                    )
                    # Execution-boundary tokens preserve exact completions and
                    # keep any in-flight tail unknown through cancellation.
                    benchmark_started_at = time.time()
                    if live_controller is not None:
                        result = await _run_live_comparison_cancellable(
                            query=query,
                            origin=origin,
                            readyset=readyset,
                            duration_seconds=duration_seconds or 30,
                            controller=live_controller,
                            event_queue=queue,
                            evidence=evidence,
                        )
                    else:
                        result = await _run_comparison_cancellable(
                            query=query,
                            origin=origin,
                            readyset=readyset,
                            iterations=iterations,
                            warmup=warmup,
                            interval_ms=interval_ms,
                            concurrency=concurrency,
                            duration_seconds=duration_seconds,
                            progress=progress,
                            evidence=evidence,
                        )
                    benchmark_executions = _origin_benchmark_executions(
                        result, live=live_controller is not None
                    )
                    if benchmark_executions is not None:
                        evidence.reconcile(
                            benchmark_executions + 1,
                            benchmark_started_at,
                        )
                    if not result.get("success"):
                        if result.get("cancelled"):
                            raise asyncio.CancelledError
                        raise RuntimeError(
                            result.get("error") or "Speed test failed"
                        )
                    if live_controller is not None:
                        result_event = CacheCompareCompleteEvent(
                            type="cache_compare_complete",
                            success=True,
                            query=query,
                            duration_seconds=result["duration_seconds"],
                            elapsed_seconds=result["elapsed_seconds"],
                            concurrency=result["concurrency"],
                            origin=result["origin"],
                            readyset=result["readyset"],
                            timeline=result["timeline"],
                            phases=result["phases"],
                            speedup_mean=result["speedup_mean"],
                            improvement_pct=result["improvement_pct"],
                            winner=result["winner"],
                        )
                    else:
                        result_event = CacheRunCompleteEvent(
                            type="cache_run_complete",
                            success=True,
                            query=query,
                            iterations=result["iterations"],
                            origin_stats=result["original"]["stats"],
                            cache_stats=result["readyset"]["stats"],
                            origin_samples_ms=result["original"].get("times", []),
                            cache_samples_ms=result["readyset"].get("times", []),
                            speedup_mean=result["speedup"]["mean"],
                            speedup_median=result["speedup"]["median"],
                            improvement_pct=result["speedup"]["improvement_pct"],
                            winner=result["winner"],
                            origin_iterations=result["original"].get(
                                "iterations", result["iterations"]
                            ),
                            cache_iterations=result["readyset"].get(
                                "iterations", result["iterations"]
                            ),
                        )
                finally:
                    if created:
                        await progress(
                            "cleaning_up",
                            "Removing the temporary Readyset cache",
                            95,
                        )
                        try:
                            dropped, cancelled = await _run_readyset_sql_settled(
                                self._cache,
                                f"DROP CACHE {cache_name}",
                                acquired,
                            )
                            if not dropped.get("success"):
                                raise RuntimeError(
                                    dropped.get("error") or "DROP CACHE failed"
                                )
                            if cancelled:
                                raise asyncio.CancelledError
                        except asyncio.CancelledError:
                            await asyncio.shield(
                                acquired.mark_dirty(
                                    "Temporary cache cleanup was interrupted"
                                )
                            )
                            raise
                        except Exception as exc:
                            await asyncio.shield(
                                acquired.mark_dirty(
                                    "Temporary cache cleanup failed: "
                                    f"{type(exc).__name__}"
                                )
                            )
                            if result_event is not None:
                                error_event = ErrorEvent(
                                    type="error",
                                    message=(
                                        "The speed result was measured, but temporary "
                                        "cache cleanup failed. The sandbox will be "
                                        "replaced."
                                    ),
                                    code="speed_test_cleanup_failed",
                                    stage="cleaning_up",
                                )
        except asyncio.CancelledError:
            raise
        except ValueError as exc:
            error_event = ErrorEvent(
                type="error",
                message=str(exc),
                code="readyset_unsupported",
                stage="checking_query",
            )
        except Exception as exc:
            error_event = ErrorEvent(
                type="error",
                message=str(exc),
                code="speed_test_failed",
                stage="speed_test",
            )
        finally:
            # Closed one-second buckets are written while the compare runs;
            # flush the final partial second before publishing completion.
            await evidence.flush_all()
            uncertain_from = evidence.uncertain_started_at()
            if uncertain_from is not None:
                # Exact progress before a cancellation remains attributable
                # to its own windows. Only the unresolved tail is marked
                # unknown, preserving earlier windows' exact subtraction.
                await evidence.record_unknown(uncertain_from, time.time())
            await evidence.close()
            if result_event is not None:
                await queue.put(result_event)
            if error_event is not None:
                await queue.put(error_event)
            await queue.put(_DONE)


def _origin_benchmark_executions(
    result: dict[str, Any], *, live: bool
) -> int | None:
    """Return an exact completed origin count when the runner provides one."""
    section_name = "origin" if live else "original"
    count_name = "completed" if live else "executions"
    section = result.get(section_name)
    if not isinstance(section, dict):
        return None
    count = section.get(count_name)
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None
    return count


def _origin_connection_config(target: str) -> dict[str, Any]:
    config = TargetsConfig()
    config.load()
    value = config.get(target)
    if value is None or value.get("target_type") == "readyset":
        raise ValueError(f"Database target '{target}' is not available")
    result = dict(value)
    result["password"] = resolve_password_value(value)
    return result


def _connection_kwargs(lease: SandboxLease) -> dict[str, Any]:
    connection = lease.connection
    return {
        "host": connection.host,
        "port": connection.port,
        "engine": connection.engine,
        "user": connection.user,
        "database": connection.database,
        "password": connection.password,
    }


async def _blocking_call(
    callback: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """Await a daemon worker while keeping cancellation responsive."""
    future = start_blocking(callback, *args, **kwargs)
    while not future.done():
        await asyncio.sleep(0.01)
    return future.result()


def _mark_outcome_retrieved(future: asyncio.Future) -> None:
    if not future.cancelled():
        future.exception()


async def _settle_after_cancel(future: asyncio.Future) -> bool:
    """Bounded wait for a cancelled worker; False when it must be abandoned.

    The worker's outcome is marked retrieved either way so an abandoned
    thread's late failure is not reported as an unhandled exception.
    """
    future.add_done_callback(_mark_outcome_retrieved)
    deadline = time.monotonic() + COMPARE_CANCEL_GRACE_SECONDS
    while not future.done():
        if time.monotonic() >= deadline:
            return False
        try:
            await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            # Repeated cancels must not extend or restart the bounded wait.
            continue
    return True


def _readyset_query(query: str, engine: str) -> str:
    from shared.query_registry.sql_normalizer import denormalize_for_readyset

    return denormalize_for_readyset(query, engine=engine)


def _explain_is_unsupported(output: str) -> bool:
    lowered = output.lower()
    if any(
        marker in lowered
        for marker in ("db error", "connection refused", "timed out", "unavailable")
    ):
        raise RuntimeError("Readyset could not complete the compatibility check.")
    return "unsupported" in lowered or "\tno" in lowered or "|no" in lowered


def _execute_rows(
    config: dict[str, Any],
    query: str,
    controller: ComparisonController | None = None,
    on_execute: Callable[[], Any] | None = None,
) -> list[Any]:
    from shared.db_connection import close_connection, create_direct_connection

    conn = create_direct_connection(config, lane="rdst/compare")
    if controller is not None:
        controller.register(conn)
    try:
        cursor = conn.cursor()
        try:
            if on_execute is not None:
                on_execute()
            cursor.execute(query)
            rows = list(cursor.fetchall())
            return rows
        finally:
            cursor.close()
    finally:
        if controller is not None:
            controller.unregister(conn)
        close_connection(conn)


async def _execute_rows_cancellable(
    config: dict[str, Any],
    query: str,
    *,
    on_execute: Callable[[], Any] | None = None,
    on_complete: Callable[[Any, float], None] | None = None,
) -> list[Any]:
    controller = ComparisonController()
    started_tokens: list[Any] = []

    def mark_started() -> Any:
        # A cancelled run's worker must not record evidence for an execution
        # nobody will observe complete.
        controller.raise_if_cancelled()
        token = on_execute() if on_execute is not None else None
        started_tokens.append(token)
        return token

    def execute_rows() -> tuple[list[Any], float]:
        if on_execute is None:
            rows = _execute_rows(config, query, controller)
        else:
            rows = _execute_rows(
                config,
                query,
                controller,
                on_execute=mark_started,
            )
        # Capture completion inside the blocking worker, immediately after
        # fetchall returns, rather than after the event loop's polling delay.
        return rows, time.time()

    future = start_blocking(execute_rows)
    try:
        while not future.done():
            await asyncio.sleep(0.01)
        rows, completed_at = future.result()
        if on_complete is not None and started_tokens:
            on_complete(started_tokens[-1], completed_at)
        return rows
    except asyncio.CancelledError as cancellation:
        controller.cancel()
        if not await _settle_after_cancel(future):
            # A stalled connect or execute must not hold the cancel. Any late
            # registration is closed by the cancelled controller, and the
            # abandoned worker's outcome is discarded.
            controller.close_connections()
            raise cancellation
        try:
            _rows, completed_at = future.result()
        except Exception:
            pass
        else:
            if on_complete is not None and started_tokens:
                on_complete(started_tokens[-1], completed_at)
        raise cancellation


async def _execute_validation_pair(
    origin: dict[str, Any],
    readyset: dict[str, Any],
    query: str,
    *,
    on_origin_execute: Callable[[], Any] | None = None,
    on_origin_complete: Callable[[Any, float], None] | None = None,
    on_wait: Callable[[], Any] | None = None,
) -> tuple[list[Any], list[Any]]:
    """Settle both validation queries before their shared lease can be released."""
    tasks = (
        asyncio.create_task(
            _execute_rows_cancellable(
                origin,
                query,
                on_execute=on_origin_execute,
                on_complete=on_origin_complete,
            )
        ),
        asyncio.create_task(_execute_rows_cancellable(readyset, query)),
    )
    try:
        while any(not task.done() for task in tasks):
            await asyncio.sleep(0.05)
            for task in tasks:
                if task.done():
                    # Surface one side's failure immediately so the sibling
                    # is cancelled and settled below instead of waiting for
                    # an unrelated slow validation query.
                    task.result()
            if on_wait is not None:
                await on_wait()
        return tasks[0].result(), tasks[1].result()
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _run_readyset_sql_settled(
    cache_service: CacheService,
    statement: str,
    lease: SandboxLease,
) -> tuple[dict[str, Any], bool]:
    """Let bounded Readyset DDL settle before releasing its sandbox lease."""
    future = start_blocking(
        cache_service._run_readyset_sql,
        statement,
        **_connection_kwargs(lease),
    )
    try:
        while not future.done():
            await asyncio.sleep(0.01)
    except asyncio.CancelledError:
        # _run_readyset_sql has 30-second driver timeouts, so settled DDL
        # cannot race the next lease. A stall past the cancel grace abandons
        # the statement and quarantines the sandbox instead of hanging.
        if not await _settle_after_cancel(future):
            await asyncio.shield(
                lease.mark_dirty("Readyset DDL was abandoned during cancel")
            )
            raise
        return future.result(), True
    return future.result(), False


def _has_top_level_limit_without_order(query: str) -> bool:
    """True when the statement has a top-level LIMIT but no top-level ORDER BY.

    Such queries may legitimately return different row sets from different
    databases, so a result mismatch is expected rather than a correctness
    signal. Subquery LIMIT/ORDER clauses do not count; unparseable queries
    return False.
    """
    try:
        expression = sqlglot.parse_one(query)
    except Exception:
        return False
    return (
        expression.args.get("limit") is not None
        and expression.args.get("order") is None
    )


def _top_level_order_key_indexes(query: str) -> tuple[int, ...] | None:
    """Map simple top-level ORDER BY keys to projected column indexes."""
    try:
        expression = sqlglot.parse_one(query)
    except Exception:
        return None
    order = expression.args.get("order")
    if order is None:
        return None

    projection_indexes = {
        projection.alias_or_name.lower(): index
        for index, projection in enumerate(expression.expressions)
        if projection.alias_or_name and projection.alias_or_name != "*"
    }
    indexes: list[int] = []
    for ordered in order.expressions:
        key = ordered.this
        if isinstance(key, sqlglot.exp.Literal) and key.is_int:
            index = int(key.this) - 1
            if index < 0 or index >= len(expression.expressions):
                return None
            indexes.append(index)
        elif isinstance(key, sqlglot.exp.Column):
            index = projection_indexes.get(key.name.lower())
            if index is None:
                return None
            indexes.append(index)
        else:
            return None
    return tuple(indexes) or None


def _canonical_rows(rows: list[Any], *, order_sensitive: bool) -> str:
    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): normalize(v) for k, v in sorted(value.items())}
        if isinstance(value, (tuple, list)):
            return [normalize(v) for v in value]
        if isinstance(value, bytes):
            return value.hex()
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return str(value)

    normalized = [
        json.dumps(normalize(row), sort_keys=True, separators=(",", ":"))
        for row in rows
    ]
    if not order_sensitive:
        normalized.sort()
    return json.dumps(normalized, separators=(",", ":"))


def _canonical_order_keys(
    rows: list[Any], indexes: tuple[int, ...]
) -> str | None:
    try:
        projected = [
            tuple(row[index] for index in indexes)
            for row in rows
        ]
    except (IndexError, KeyError, TypeError):
        return None
    return _canonical_rows(projected, order_sensitive=True)


async def _run_comparison_cancellable(
    *,
    query: str,
    origin: dict[str, Any],
    readyset: dict[str, Any],
    iterations: int,
    warmup: int,
    interval_ms: int | None,
    concurrency: int | None,
    duration_seconds: int | None,
    progress,
    evidence: _CompareEvidenceRecorder | None = None,
) -> dict[str, Any]:
    progress_queue: Queue = Queue()
    evidence_queue: Queue = Queue()
    controller = ComparisonController()

    def on_progress(stage: str, current: int, total: int) -> None:
        progress_queue.put((stage, current, total))

    def on_origin_progress(token: Any, occurred_at: float, count: int) -> None:
        evidence_queue.put(("complete", token, occurred_at, count))

    def on_origin_start(token: Any, occurred_at: float) -> None:
        evidence_queue.put(("start", token, occurred_at, 0))

    async def collect_evidence() -> None:
        while True:
            try:
                kind, token, occurred_at, count = evidence_queue.get_nowait()
            except Empty:
                break
            if evidence is not None:
                if kind == "start":
                    evidence.note_started(token, occurred_at)
                else:
                    evidence.note_completed(token, occurred_at, count)
        if evidence is not None:
            await evidence.flush_closed()

    last_percent = 65
    future = start_blocking(
        run_comparison,
        query=query,
        original_db_config=origin,
        readyset_db_config=readyset,
        iterations=iterations,
        warmup_iterations=warmup,
        interval_ms=interval_ms,
        concurrency=concurrency,
        duration_seconds=duration_seconds,
        on_progress=on_progress,
        on_origin_progress=on_origin_progress,
        on_origin_start=on_origin_start,
        controller=controller,
    )
    try:
        while True:
            await collect_evidence()
            try:
                stage, current, total = progress_queue.get_nowait()
            except Empty:
                if future.done():
                    break
                await asyncio.sleep(0.05)
                continue

            if stage.endswith("_warmup"):
                candidate = 65 + int((current / total) * 5) if total else 65
            elif stage == "cache":
                candidate = 80 + int((current / total) * 10) if total else 80
            else:
                candidate = 70 + int((current / total) * 10) if total else 70
            last_percent = max(last_percent, candidate)
            readyset_stage = stage.startswith("cache")
            warming = stage.endswith("_warmup")
            await progress(
                "benchmarking_readyset"
                if readyset_stage
                else "benchmarking_origin",
                (
                    f"Warming {'Readyset' if readyset_stage else 'origin'} benchmark"
                    if warming
                    else (
                        "Benchmarking Readyset"
                        if readyset_stage
                        else "Benchmarking origin"
                    )
                ),
                last_percent,
            )
        await collect_evidence()
        return future.result()
    except asyncio.CancelledError as cancellation:
        controller.cancel()
        if not await _settle_after_cancel(future):
            controller.close_connections()
        await collect_evidence()
        raise cancellation


async def _run_live_comparison_cancellable(
    *,
    query: str,
    origin: dict[str, Any],
    readyset: dict[str, Any],
    duration_seconds: int,
    controller: LiveComparisonController,
    event_queue: asyncio.Queue[Any],
    evidence: _CompareEvidenceRecorder | None = None,
) -> dict[str, Any]:
    """Bridge the blocking live runner into replayable background events."""
    sample_queue: Queue = Queue()
    evidence_queue: Queue = Queue()
    future = start_blocking(
        run_live_comparison,
        query=query,
        original_db_config=origin,
        readyset_db_config=readyset,
        duration_seconds=duration_seconds,
        controller=controller,
        on_sample=sample_queue.put,
        on_origin_progress=lambda token, occurred_at, count: evidence_queue.put(
            ("complete", token, occurred_at, count)
        ),
        on_origin_start=lambda token, occurred_at: evidence_queue.put(
            ("start", token, occurred_at, 0)
        ),
    )

    async def collect_evidence() -> None:
        while True:
            try:
                kind, token, occurred_at, count = evidence_queue.get_nowait()
            except Empty:
                break
            if evidence is not None:
                if kind == "start":
                    evidence.note_started(token, occurred_at)
                else:
                    evidence.note_completed(token, occurred_at, count)
        if evidence is not None:
            await evidence.flush_closed()

    async def publish_samples() -> None:
        while True:
            try:
                sample = sample_queue.get_nowait()
            except Empty:
                return
            await event_queue.put(
                CacheCompareSampleEvent(
                    type="cache_compare_sample",
                    elapsed_seconds=sample["elapsed_seconds"],
                    concurrency=sample["concurrency"],
                    origin=sample["origin"],
                    readyset=sample["readyset"],
                )
            )

    try:
        while not future.done():
            await publish_samples()
            await collect_evidence()
            await asyncio.sleep(0.05)
        await publish_samples()
        await collect_evidence()
        return future.result()
    except asyncio.CancelledError as cancellation:
        controller.cancel()
        if not await _settle_after_cancel(future):
            controller.close_connections()
        await collect_evidence()
        raise cancellation


__all__ = [
    "ReadysetExperimentService",
    "parameter_fingerprint",
    "temporary_cache_name",
]

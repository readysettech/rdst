"""Temporary Readyset experiments backed by the process-local sandbox manager."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
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
from shared.service_events import ErrorEvent, ProgressEvent

from .events import CacheEvent, CacheRunCompleteEvent
from .performance_comparison import (
    ComparisonController,
    run_comparison,
)
from .service import CacheService

_DONE = object()
_CACHE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


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
    ) -> None:
        cache_name = temporary_cache_name(owner_id, query)
        created = False
        result_event: CacheRunCompleteEvent | None = None
        error_event: ErrorEvent | None = None

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
                purpose="speed_test",
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
                    origin_rows, readyset_rows = await _execute_validation_pair(
                        origin, readyset, query
                    )
                    results_match = _canonical_rows(
                        origin_rows, order_sensitive=False
                    ) == _canonical_rows(readyset_rows, order_sensitive=False)
                    order_indexes = _top_level_order_key_indexes(query)
                    if results_match and order_indexes is not None:
                        results_match = _canonical_order_keys(
                            origin_rows, order_indexes
                        ) == _canonical_order_keys(readyset_rows, order_indexes)
                    if not results_match:
                        raise RuntimeError(
                            "Readyset returned a different result from the origin; "
                            "the speed test was stopped."
                        )

                    await progress(
                        "benchmarking_origin",
                        "Benchmarking origin and Readyset",
                        65,
                    )
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
                    )
                    if not result.get("success"):
                        if result.get("cancelled"):
                            raise asyncio.CancelledError
                        raise RuntimeError(
                            result.get("error") or "Speed test failed"
                        )
                    result_event = CacheRunCompleteEvent(
                        type="cache_run_complete",
                        success=True,
                        query=query,
                        iterations=result["iterations"],
                        origin_stats=result["original"]["stats"],
                        cache_stats=result["readyset"]["stats"],
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
            if result_event is not None:
                await queue.put(result_event)
            if error_event is not None:
                await queue.put(error_event)
            await queue.put(_DONE)


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
) -> list[Any]:
    from shared.db_connection import close_connection, create_direct_connection

    conn = create_direct_connection(config)
    if controller is not None:
        controller.register(conn)
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(query)
            return list(cursor.fetchall())
        finally:
            cursor.close()
    finally:
        if controller is not None:
            controller.unregister(conn)
        close_connection(conn)


async def _execute_rows_cancellable(
    config: dict[str, Any], query: str
) -> list[Any]:
    controller = ComparisonController()
    future = start_blocking(_execute_rows, config, query, controller)
    try:
        while not future.done():
            await asyncio.sleep(0.01)
        return future.result()
    except asyncio.CancelledError as cancellation:
        controller.cancel()
        while not future.done():
            await asyncio.sleep(0.01)
        raise cancellation


async def _execute_validation_pair(
    origin: dict[str, Any],
    readyset: dict[str, Any],
    query: str,
) -> tuple[list[Any], list[Any]]:
    """Settle both validation queries before their shared lease can be released."""
    tasks = (
        asyncio.create_task(_execute_rows_cancellable(origin, query)),
        asyncio.create_task(_execute_rows_cancellable(readyset, query)),
    )
    try:
        origin_rows, readyset_rows = await asyncio.gather(*tasks)
        return origin_rows, readyset_rows
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
    cancelled = False
    while not future.done():
        try:
            await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            # _run_readyset_sql has 30-second driver timeouts. Waiting here
            # avoids abandoned DDL racing the next lease or target transition.
            cancelled = True
    return future.result(), cancelled


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
) -> dict[str, Any]:
    progress_queue: Queue = Queue()
    controller = ComparisonController()

    def on_progress(stage: str, current: int, total: int) -> None:
        progress_queue.put((stage, current, total))

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
        controller=controller,
    )
    try:
        while True:
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
        return future.result()
    except asyncio.CancelledError as cancellation:
        controller.cancel()
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        raise cancellation


__all__ = [
    "ReadysetExperimentService",
    "parameter_fingerprint",
    "temporary_cache_name",
]

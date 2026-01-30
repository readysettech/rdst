"""Service for query registry and benchmark workflows."""

from __future__ import annotations

import asyncio
import re
import statistics
import threading
import time
from dataclasses import dataclass, field
from queue import Empty, Queue
from threading import Lock
from typing import Any, AsyncGenerator, List, Optional

from .types import (
    QueryBenchmarkProgressEvent,
    QueryBenchmarkStats,
    QueryCommandInput,
    QueryCompleteEvent,
    QueryErrorEvent,
    QueryEvent,
    QueryStatusEvent,
)


class QueryService:
    """Stateless query service for shared CLI + Web usage."""

    async def execute(
        self, input_data: QueryCommandInput
    ) -> AsyncGenerator[QueryEvent, None]:
        """Execute a query subcommand via the existing QueryCommand."""
        from lib.cli.query_command import QueryCommand

        yield QueryStatusEvent(
            type="status", message=f"Running query subcommand '{input_data.subcommand}'"
        )

        try:
            query_cmd = QueryCommand()
            result = await asyncio.to_thread(
                query_cmd.execute, input_data.subcommand, **input_data.kwargs
            )

            payload = {
                "ok": bool(result.ok),
                "message": result.message,
                "data": result.data or {},
            }
            yield QueryCompleteEvent(
                type="complete", success=bool(result.ok), result=payload
            )
        except Exception as e:
            yield QueryErrorEvent(type="error", message=str(e))

    async def stream_benchmark(
        self,
        queries: list,
        target: Optional[str],
        mode: str,
        interval_ms: int,
        concurrency: int,
        duration_seconds: int,
        max_count: Optional[int],
    ) -> AsyncGenerator[QueryBenchmarkProgressEvent, None]:
        """Stream benchmark progress events from a background worker."""
        progress_queue: Queue = Queue(maxsize=100)
        stop_event = threading.Event()

        def run_sync() -> None:
            self._run_benchmark_sync(
                queries=queries,
                target=target,
                mode=mode,
                interval_ms=interval_ms,
                concurrency=concurrency,
                duration_seconds=duration_seconds,
                max_count=max_count,
                progress_queue=progress_queue,
                stop_event=stop_event,
            )

        loop = asyncio.get_event_loop()
        future = loop.run_in_executor(None, run_sync)

        try:
            while True:
                try:
                    progress = progress_queue.get_nowait()
                    yield progress

                    if progress.type in ("complete", "error"):
                        break
                except Empty:
                    if future.done():
                        while True:
                            try:
                                progress = progress_queue.get_nowait()
                                yield progress
                            except Empty:
                                break
                        break
                    await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            stop_event.set()
            raise
        finally:
            stop_event.set()

    def _run_benchmark_sync(
        self,
        queries: list,
        target: Optional[str],
        mode: str,
        interval_ms: int,
        concurrency: int,
        duration_seconds: int,
        max_count: Optional[int],
        progress_queue: Queue,
        stop_event: threading.Event,
    ) -> None:
        """Synchronous benchmark worker that reports progress events."""
        del concurrency  # reserved for future parity with CLI concurrency mode

        @dataclass
        class _QueryStats:
            query_name: str
            query_hash: str
            executions: int = 0
            successes: int = 0
            failures: int = 0
            timings_ms: list[float] = field(default_factory=list)
            last_error: str | None = None

            def to_model(self) -> QueryBenchmarkStats:
                timings = self.timings_ms
                return QueryBenchmarkStats(
                    query_name=self.query_name,
                    query_hash=self.query_hash,
                    executions=self.executions,
                    successes=self.successes,
                    failures=self.failures,
                    min_ms=min(timings) if timings else 0.0,
                    avg_ms=statistics.mean(timings) if timings else 0.0,
                    p50_ms=statistics.median(timings) if timings else 0.0,
                    p95_ms=(
                        sorted(timings)[int(len(timings) * 0.95)]
                        if len(timings) >= 2
                        else (max(timings) if timings else 0.0)
                    ),
                    p99_ms=(
                        sorted(timings)[int(len(timings) * 0.99)]
                        if len(timings) >= 2
                        else (max(timings) if timings else 0.0)
                    ),
                    max_ms=max(timings) if timings else 0.0,
                    last_error=self.last_error,
                )

        @dataclass
        class _ResolvedQuery:
            identifier: str
            name: str
            sql: str

        def _has_unresolved_placeholders(sql: str) -> bool:
            if re.search(r"\$\d+", sql):
                return True
            if re.search(r"(?<!:):\w+", sql):
                return True
            if "?" in sql:
                return True
            return False

        try:
            from lib.cli.rdst_cli import TargetsConfig
            from lib.db_connection import close_connection, create_direct_connection
            from lib.query_registry import QueryRegistry

            registry = QueryRegistry()
            registry.load()

            resolved_queries: list[_ResolvedQuery] = []
            skipped_queries: list[str] = []

            for spec in queries:
                if isinstance(spec, dict) or hasattr(spec, "sql"):
                    spec_dict = spec if isinstance(spec, dict) else spec.model_dump()
                    raw_sql = spec_dict.get("sql")
                    identifier = spec_dict.get("identifier") or "custom"

                    if raw_sql:
                        if _has_unresolved_placeholders(raw_sql):
                            skipped_queries.append(
                                f"{identifier[:8]} (has unresolved parameters)"
                            )
                            continue
                        resolved_queries.append(
                            _ResolvedQuery(
                                identifier=identifier,
                                name=identifier[:8]
                                if len(identifier) > 8
                                else identifier,
                                sql=raw_sql,
                            )
                        )
                        continue

                spec_str = (
                    spec
                    if isinstance(spec, str)
                    else (
                        spec.get("identifier")
                        if isinstance(spec, dict)
                        else getattr(spec, "identifier", None)
                    )
                )
                if not spec_str:
                    continue

                entry = registry.get_query_by_tag(spec_str)
                if not entry:
                    entry = registry.get_query(spec_str)
                if not entry:
                    raise ValueError(f"Query not found: {spec_str}")

                sql = registry.get_executable_query(entry.hash, interactive=False)
                if not sql:
                    sql = entry.sql

                if _has_unresolved_placeholders(sql):
                    skipped_queries.append(
                        f"{entry.tag or entry.hash[:8]} (has unresolved parameters like $1, :p1)"
                    )
                    continue

                resolved_queries.append(
                    _ResolvedQuery(
                        identifier=entry.hash,
                        name=entry.tag or entry.hash[:8],
                        sql=sql,
                    )
                )

            if skipped_queries and not resolved_queries:
                raise ValueError(
                    f"All queries have unresolved parameters: {', '.join(skipped_queries)}. "
                    "Benchmark requires queries with concrete values, not placeholders."
                )

            if not resolved_queries:
                raise ValueError("No queries to run")

            cfg = TargetsConfig()
            cfg.load()

            if not target:
                target = cfg.get_default()

            if not target:
                raise ValueError("No target specified")

            target_config = cfg.get(target)
            if not target_config:
                raise ValueError(f"Target '{target}' not found")

            query_stats: dict[str, _QueryStats] = {}
            stats_lock = Lock()
            start_time = time.perf_counter()

            def _record_execution(
                query_hash: str,
                query_name: str,
                duration_ms: float,
                success: bool,
                error_msg: str | None = None,
            ) -> None:
                with stats_lock:
                    if query_hash not in query_stats:
                        query_stats[query_hash] = _QueryStats(query_name, query_hash)
                    stats = query_stats[query_hash]
                    stats.executions += 1
                    if success:
                        stats.successes += 1
                        stats.timings_ms.append(duration_ms)
                    else:
                        stats.failures += 1
                        if error_msg:
                            stats.last_error = error_msg

            def _progress(event_type: str) -> QueryBenchmarkProgressEvent:
                with stats_lock:
                    elapsed = time.perf_counter() - start_time
                    total_exec = sum(s.executions for s in query_stats.values())
                    total_succ = sum(s.successes for s in query_stats.values())
                    total_fail = sum(s.failures for s in query_stats.values())
                    qps = total_exec / elapsed if elapsed > 0 else 0
                    return QueryBenchmarkProgressEvent(
                        type=event_type,
                        elapsed_seconds=elapsed,
                        total_executions=total_exec,
                        total_successes=total_succ,
                        total_failures=total_fail,
                        qps=qps,
                        queries=[s.to_model() for s in query_stats.values()],
                    )

            conn = create_direct_connection(target_config)
            query_index = 0

            try:
                last_progress_time = 0.0
                progress_interval = 0.25

                while not stop_event.is_set():
                    elapsed = time.perf_counter() - start_time

                    if duration_seconds and elapsed >= duration_seconds:
                        break

                    total_exec = sum(s.executions for s in query_stats.values())
                    if max_count and total_exec >= max_count:
                        break

                    rq = resolved_queries[query_index]
                    query_index = (query_index + 1) % len(resolved_queries)

                    exec_start = time.perf_counter()
                    try:
                        cursor = conn.cursor()
                        cursor.execute(rq.sql)
                        cursor.fetchall()
                        cursor.close()
                        duration_ms = (time.perf_counter() - exec_start) * 1000
                        _record_execution(
                            rq.identifier, rq.name, duration_ms, success=True
                        )
                    except Exception as e:
                        duration_ms = (time.perf_counter() - exec_start) * 1000
                        _record_execution(
                            rq.identifier,
                            rq.name,
                            duration_ms,
                            success=False,
                            error_msg=str(e),
                        )

                    now = time.perf_counter()
                    if now - last_progress_time >= progress_interval:
                        try:
                            progress_queue.put_nowait(_progress("progress"))
                        except Exception:
                            pass
                        last_progress_time = now

                    if mode == "interval" and interval_ms > 0:
                        stop_event.wait(interval_ms / 1000.0)
            finally:
                close_connection(conn)

            final = _progress("complete")
            try:
                progress_queue.put_nowait(final)
            except Exception:
                pass
        except Exception as e:
            error_progress = QueryBenchmarkProgressEvent(
                type="error",
                elapsed_seconds=0,
                total_executions=0,
                total_successes=0,
                total_failures=0,
                qps=0,
                queries=[],
                error=str(e),
            )
            try:
                progress_queue.put_nowait(error_progress)
            except Exception:
                pass

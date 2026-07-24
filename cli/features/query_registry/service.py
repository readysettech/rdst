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
from typing import Any, AsyncGenerator, List, Literal, Optional

from .events import (
    QueryBenchmarkCompleteEvent,
    QueryBenchmarkErrorEvent,
    QueryBenchmarkEvent,
    QueryBenchmarkProgressEvent,
    QueryCompleteEvent,
    QueryErrorEvent,
    QueryEvent,
    QueryStatusEvent,
)
from .models import (
    QueryBenchmarkStats,
    QueryCommandInput,
)

# ---------------------------------------------------------------------------
# Benchmark safety rails (B5 / code-backend F4)
#
# The web benchmark path executes raw client SQL in a tight loop against the
# selected target. These server-side rails bound a *UI-bypassing* caller — they
# are enforced here regardless of what the confirm dialog does, so a direct POST
# to /api/query-registry/benchmark cannot run writes or an unbounded loop.
# ---------------------------------------------------------------------------

MAX_BENCHMARK_DURATION_SECONDS = 300
MAX_BENCHMARK_MAX_COUNT = 100_000

# DML/DDL keywords that must never appear anywhere in a benchmarked statement —
# scanned even mid-statement to defeat data-modifying CTEs, e.g.
# ``WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x``.
_BENCHMARK_WRITE_KEYWORDS = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "REPLACE",
        "UPSERT",
        "CREATE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "RENAME",
        "GRANT",
        "REVOKE",
        "COMMENT",
    }
)


class BenchmarkValidationError(Exception):
    """A benchmark request rejected before/at execution for a reason that is
    safe to show the user (read-only violation, over-cap, unresolved
    parameters, unknown query, missing target).

    Distinguished from unexpected exceptions so the humane message survives to
    the client while raw driver / ``str(e)`` text stays hidden (B7/T24)."""

    def __init__(self, message: str, code: str = "benchmark_rejected") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class _BenchmarkController:
    """Thread-safe cancellation bridge for active load-test connections."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._connections: list[Any] = []

    def register(self, connection: Any) -> None:
        with self._lock:
            self._connections.append(connection)

    def unregister(self, connection: Any) -> None:
        with self._lock:
            if connection in self._connections:
                self._connections.remove(connection)

    def cancel(self) -> None:
        with self._lock:
            connections = list(self._connections)
        for connection in connections:
            try:
                cancel = getattr(connection, "cancel", None)
                if callable(cancel):
                    cancel()
                else:
                    connection.close()
            except Exception:
                pass


def set_session_read_only(conn: Any, engine: str) -> None:
    """Make the benchmark's DB session read-only at the database level.

    The lexical classifier (`benchmark_read_only_reason`) is a humane
    pre-flight, but it cannot catch SELECT-invoked write functions —
    ``SELECT setval('seq', 42)``, ``dblink_exec(...)``, DML-bearing UDFs —
    which parse as plain reads. Setting the session read-only means any such
    write fails **at execution inside the database**, regardless of what the
    SQL looks like. PostgreSQL: every autocommit statement runs under
    ``default_transaction_read_only = on``; MySQL: subsequent transactions in
    the session are READ ONLY.
    """
    statement = (
        "SET SESSION TRANSACTION READ ONLY"
        if "mysql" in (engine or "").lower()
        else "SET default_transaction_read_only = on"
    )
    cursor = conn.cursor()
    try:
        cursor.execute(statement)
    finally:
        cursor.close()


def benchmark_read_only_reason(sql: str) -> Optional[str]:
    """Return a human reason if ``sql`` is not a single read-only statement,
    else ``None``.

    Rejects: empty input, multiple ``;``-separated statements, a lead keyword
    other than ``SELECT`` / ``WITH``, ``SELECT ... INTO`` (which writes a new
    table), and any DML/DDL keyword anywhere (data-modifying CTEs). The parser
    tokenizes first, so keywords inside string literals or identifiers are not
    matched.
    """
    import sqlparse
    from sqlparse.tokens import DDL, DML, Keyword

    statements = [
        s
        for s in sqlparse.parse(sql or "")
        if s.token_first(skip_cm=True) is not None
    ]
    if not statements:
        return "The benchmark query is empty."
    if len(statements) > 1:
        return (
            "Benchmark runs a single read-only statement per query; "
            "multiple statements are not allowed."
        )

    stmt = statements[0]
    first = stmt.token_first(skip_cm=True)
    first_kw = (first.value or "").upper() if first is not None else ""
    if first_kw not in ("SELECT", "WITH"):
        label = first_kw or "This statement"
        return (
            f"Benchmark only executes read-only SELECT queries; "
            f"'{label}' is not allowed."
        )

    for token in stmt.flatten():
        value = (token.value or "").upper()
        if token.ttype in (DML, DDL) and value in _BENCHMARK_WRITE_KEYWORDS:
            return (
                f"Benchmark rejected: '{value}' is a write operation, but the "
                "benchmark is read-only."
            )
        if token.ttype is Keyword and value == "INTO":
            return (
                "Benchmark rejected: 'SELECT ... INTO' writes a new table, but "
                "the benchmark is read-only."
            )
    return None


class QueryService:
    """Stateless query service for shared CLI + Web usage."""

    async def execute(
        self, input_data: QueryCommandInput
    ) -> AsyncGenerator[QueryEvent, None]:
        """Execute a query subcommand via the existing QueryCommand."""
        from .cli.command import QueryCommand

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
    ) -> AsyncGenerator[QueryBenchmarkEvent, None]:
        """Stream benchmark progress events from a background worker."""
        progress_queue: Queue = Queue(maxsize=100)
        stop_event = threading.Event()
        controller = _BenchmarkController()

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
                controller=controller,
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
        except asyncio.CancelledError as cancellation:
            stop_event.set()
            controller.cancel()
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            raise cancellation
        finally:
            stop_event.set()
            if not future.done():
                controller.cancel()

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
        controller: _BenchmarkController,
    ) -> None:
        """Synchronous benchmark worker that reports progress events."""
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
            # Cap rail: reject an over-cap request outright (do not silently
            # clamp) so a UI-bypassing caller cannot request an unbounded run.
            if (
                duration_seconds is not None
                and duration_seconds > MAX_BENCHMARK_DURATION_SECONDS
            ):
                raise BenchmarkValidationError(
                    f"Benchmark duration is capped at {MAX_BENCHMARK_DURATION_SECONDS}s "
                    f"({duration_seconds}s requested).",
                    code="benchmark_duration_capped",
                )
            if max_count is not None and max_count > MAX_BENCHMARK_MAX_COUNT:
                raise BenchmarkValidationError(
                    f"Benchmark execution count is capped at {MAX_BENCHMARK_MAX_COUNT:,} "
                    f"({max_count:,} requested).",
                    code="benchmark_count_capped",
                )
            if mode == "concurrency" or concurrency != 1:
                raise BenchmarkValidationError(
                    "Concurrent workers are not supported by Query Load Test yet. "
                    "Use fixed interval mode.",
                    code="load_test_concurrency_unsupported",
                )
            # Even when the caller omits max_count, bound the loop so a tight
            # loop (interval 0) can never run unbounded.
            effective_max_count = (
                max_count if max_count is not None else MAX_BENCHMARK_MAX_COUNT
            )

            from shared.config.targets import create_targets_config
            from shared.db_connection import close_connection, create_direct_connection
            from shared.query_registry import QueryRegistry

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
                    raise BenchmarkValidationError(
                        f"Query not found: {spec_str}",
                        code="benchmark_query_not_found",
                    )

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
                raise BenchmarkValidationError(
                    f"All queries have unresolved parameters: {', '.join(skipped_queries)}. "
                    "Benchmark requires queries with concrete values, not placeholders.",
                    code="benchmark_unresolved_params",
                )

            if not resolved_queries:
                raise BenchmarkValidationError(
                    "No queries to run", code="benchmark_no_queries"
                )

            # Read-only rail: reject the whole run if any resolved query is not a
            # single read-only statement — enforced server-side regardless of the
            # UI, before opening a DB connection.
            for rq in resolved_queries:
                reason = benchmark_read_only_reason(rq.sql)
                if reason:
                    raise BenchmarkValidationError(reason, code="benchmark_read_only")

            cfg = create_targets_config()
            cfg.load()

            if not target:
                target = cfg.get_default()

            if not target:
                raise BenchmarkValidationError(
                    "No target specified", code="benchmark_no_target"
                )

            target_config = cfg.get(target)
            if not target_config:
                raise BenchmarkValidationError(
                    f"Target '{target}' not found. Run 'rdst configure add' to set one up.",
                    code="benchmark_target_not_found",
                )

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

            def _progress(
                event_type: Literal["progress", "complete"],
            ) -> QueryBenchmarkProgressEvent | QueryBenchmarkCompleteEvent:
                with stats_lock:
                    elapsed = time.perf_counter() - start_time
                    total_exec = sum(s.executions for s in query_stats.values())
                    total_succ = sum(s.successes for s in query_stats.values())
                    total_fail = sum(s.failures for s in query_stats.values())
                    qps = total_exec / elapsed if elapsed > 0 else 0
                    queries_list = [s.to_model() for s in query_stats.values()]
                    if event_type == "complete":
                        return QueryBenchmarkCompleteEvent(
                            type="complete",
                            elapsed_seconds=elapsed,
                            total_executions=total_exec,
                            total_successes=total_succ,
                            total_failures=total_fail,
                            qps=qps,
                            queries=queries_list,
                        )
                    return QueryBenchmarkProgressEvent(
                        type="progress",
                        elapsed_seconds=elapsed,
                        total_executions=total_exec,
                        total_successes=total_succ,
                        total_failures=total_fail,
                        qps=qps,
                        queries=queries_list,
                    )

            conn = create_direct_connection(target_config)
            controller.register(conn)
            query_index = 0

            try:
                # Session-level read-only rail (B5 must-fix): even a write that
                # slips past the lexical classifier (SELECT-invoked functions
                # like setval()) fails at execution inside the DB. Fail closed:
                # if the session cannot be made read-only, do not run at all.
                try:
                    set_session_read_only(conn, str(target_config.get("engine", "")))
                except Exception as exc:
                    raise BenchmarkValidationError(
                        "Could not establish a read-only session on the target; "
                        "benchmark aborted.",
                        code="benchmark_read_only_session",
                    ) from exc

                last_progress_time = 0.0
                progress_interval = 0.25

                while not stop_event.is_set():
                    elapsed = time.perf_counter() - start_time

                    if duration_seconds and elapsed >= duration_seconds:
                        break

                    total_exec = sum(s.executions for s in query_stats.values())
                    if effective_max_count and total_exec >= effective_max_count:
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
                controller.unregister(conn)
                close_connection(conn)

            final = _progress("complete")
            try:
                progress_queue.put_nowait(final)
            except Exception:
                pass
        except BenchmarkValidationError as e:
            # Safe-to-show rejection (read-only, over-cap, bad target/query):
            # surface the humane message + stable code via the shared envelope.
            error_progress = QueryBenchmarkErrorEvent(
                type="error",
                message=e.message,
                code=e.code,
                detail=None,
            )
            try:
                progress_queue.put_nowait(error_progress)
            except Exception:
                pass
        except Exception as e:
            # Unexpected failure: keep the message generic and put only the
            # exception class name in detail — never raw str(e) (B7/T24).
            error_progress = QueryBenchmarkErrorEvent(
                type="error",
                message="The benchmark could not be completed.",
                code="internal_error",
                detail=type(e).__name__,
            )
            try:
                progress_queue.put_nowait(error_progress)
            except Exception:
                pass

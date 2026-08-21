"""Service for query registry and benchmark workflows."""

from __future__ import annotations

import asyncio
import logging
import re
import statistics
import threading
import time
import uuid
from dataclasses import dataclass, field
from queue import Empty, Queue
from threading import Lock
from typing import Any, AsyncGenerator, Callable, List, Literal, Optional

from shared.query_registry.observation_store import ExecutionEvidenceWriter
from shared.query_registry.sql_normalizer import mask_string_literals

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
    QuerySkip,
)
from .readyset_lane import (
    LANE_ORIGIN,
    LANE_READYSET,
    LANE_TAGS,
    SKIP_READYSET_CACHE_FAILED,
    ReadysetEndpoint,
    normalize_lanes,
    prepare_lane_caches,
)

# ---------------------------------------------------------------------------
# Benchmark safety rails (B5 / code-backend F4)
#
# The web benchmark path executes raw client SQL in a tight loop against the
# selected target. These server-side rails bound a *UI-bypassing* caller — they
# are enforced here regardless of what the confirm dialog does, so a direct POST
# to /api/query-registry/benchmark cannot run writes or an unbounded loop.
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

MAX_BENCHMARK_DURATION_SECONDS = 300
MAX_BENCHMARK_MAX_COUNT = 100_000
MAX_BENCHMARK_CONCURRENCY = 32

# A load test that replays one concrete value measures that value's cache
# profile, not the query's. Each query rotates through several parameter sets:
# the stored values, plus sets derived from what the database itself knows
# about the compared columns.
LOAD_TEST_PARAMETER_SETS = 5
# Short, per-query, and off the clock: enough to leave the first-execution
# costs (plan caching, buffer warming) out of the measured window.
LOAD_TEST_WARMUP_EXECUTIONS = 3
# One pathological statement must not hold a worker for the whole run.
LOAD_TEST_STATEMENT_TIMEOUT_MS = 30_000
MAX_LOAD_TEST_WARMUP_EXECUTIONS = 20

# Why a query the caller asked for is not in the run. Stable codes: the client
# renders them, and they sit beside the sanitized execution-error summaries.
SKIP_UNRESOLVED_PARAMETERS = "unresolved_parameters"
SKIP_TARGET_MISMATCH = "target_mismatch"

# After a cancel, how long to wait for workers blocked in driver calls before
# force-closing their connections and returning the cancelled result.
BENCHMARK_CANCEL_GRACE_SECONDS = 10

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


# Stable summaries for a failing benchmark statement, matched in order. Raw
# driver text names schemas, columns, hosts, and roles, and it changes with
# every engine release; the client sees one of these instead (B7/T24).
_MISSING_OBJECT = ("does not exist", "unknown column", "unknown table")
_CONNECTION_LOST = ("connection", "server closed", "broken pipe", "gone away")
_EXECUTION_ERROR_SUMMARIES = (
    (("read-only", "read only"), "read-only transaction"),
    (("timeout", "timed out", "canceling statement"), "statement timeout"),
    (("permission denied", "access denied", "not allowed"), "permission denied"),
    (("syntax error", "parse error"), "syntax error"),
    (_MISSING_OBJECT, "missing table or column"),
    (_CONNECTION_LOST, "connection error"),
)
_EXECUTION_ERROR_FALLBACK = "execution error"


def _execution_error_summary(error: str) -> str:
    """Classify one driver error into text that is safe and stays stable."""
    lowered = error.lower()
    for needles, summary in _EXECUTION_ERROR_SUMMARIES:
        if any(needle in lowered for needle in needles):
            return summary
    return _EXECUTION_ERROR_FALLBACK


def _lane_stats_payload(stats: QueryBenchmarkStats) -> dict[str, Any]:
    """One lane's numbers, in the shape of the top-level per-query fields."""
    return {name: value for name, value in vars(stats).items() if name != "lanes"}


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


class _LoadTestEvidenceRecorder:
    """Thread-safe, run-scoped attribution buckets for one lane of a load test."""

    def __init__(self, target: str, lane: str = LANE_TAGS[LANE_ORIGIN]) -> None:
        self.run_id = uuid.uuid4().hex
        self._writer = ExecutionEvidenceWriter(target, lane=lane)
        self._lock = Lock()
        self._next_token = 0
        self._outstanding: dict[int, tuple[str, float]] = {}
        self._exact: dict[tuple[str, int], tuple[int, float, float]] = {}
        self._unknown: dict[tuple[str, int], tuple[float, float]] = {}
        self._dirty_seconds: set[int] = set()

    def note_started(self, sql: str, occurred_at: float | None = None) -> int:
        with self._lock:
            at = time.time() if occurred_at is None else float(occurred_at)
            self._next_token += 1
            token = self._next_token
            self._outstanding[token] = (sql, at)
            return token

    def note_completed(self, token: int, occurred_at: float | None = None) -> None:
        with self._lock:
            # Capture the timestamp under the lock so the bucket second is
            # assigned atomically with respect to a concurrent flush cutoff.
            at = time.time() if occurred_at is None else float(occurred_at)
            started = self._outstanding.pop(token, None)
            if started is None:
                return
            sql, _ = started
            second = int(at)
            key = (sql, second)
            previous = self._exact.get(key)
            if previous is None:
                self._exact[key] = (1, at, at)
            else:
                count, first, last = previous
                self._exact[key] = (count + 1, min(first, at), max(last, at))
            self._dirty_seconds.add(second)

    def note_failed(self, token: int, occurred_at: float | None = None) -> None:
        with self._lock:
            at = time.time() if occurred_at is None else float(occurred_at)
            started = self._outstanding.pop(token, None)
            if started is None:
                return
            sql, began_at = started
            second = int(at)
            key = (sql, second)
            previous = self._unknown.get(key)
            if previous is None:
                self._unknown[key] = (began_at, at)
            else:
                first, last = previous
                self._unknown[key] = (min(first, began_at), max(last, at))
            self._dirty_seconds.add(second)

    def flush_closed(self, now: float | None = None) -> None:
        current = int(time.time() if now is None else now)
        self._flush(lambda second: second < current)

    def flush_all(self) -> None:
        # Anything left in flight after worker settlement is conservatively
        # unknown through the final flush boundary.
        ended_at = time.time()
        with self._lock:
            tokens = tuple(self._outstanding)
        for token in tokens:
            self.note_failed(token, ended_at)
        self._flush(lambda _second: True)

    def _flush(self, selected: Callable[[int], bool]) -> None:
        with self._lock:
            seconds = {second for second in self._dirty_seconds if selected(second)}
            exact: dict[int, list[tuple[str, int, float, float]]] = {}
            for (sql, second), (count, first, last) in self._exact.items():
                if second in seconds:
                    exact.setdefault(second, []).append((sql, count, first, last))
            unknown: dict[int, list[tuple[str, float, float]]] = {}
            for (sql, second), (first, last) in self._unknown.items():
                if second in seconds:
                    unknown.setdefault(second, []).append((sql, first, last))
            self._dirty_seconds.difference_update(seconds)
        # One write per (kind, second) under a stable run_id: the store's
        # (target_id, run_id, normalized_hash) upsert makes re-flushing a
        # still-hot cumulative bucket an in-place update of the count instead
        # of a second row, and the writer merges same-hash SQL within a call.
        for second in sorted(seconds):
            buckets = exact.get(second)
            if buckets:
                self._writer.record(
                    [{"sql": sql, "exec_count": count} for sql, count, _, _ in buckets],
                    run_id=f"{self.run_id}:exact:{second}",
                    started_at=min(first for _, _, first, _ in buckets),
                    ended_at=max(last for _, _, _, last in buckets),
                )
            failures = unknown.get(second)
            if failures:
                self._writer.record(
                    [{"sql": sql, "exec_count": None} for sql, _, _ in failures],
                    run_id=f"{self.run_id}:unknown:{second}",
                    started_at=min(first for _, first, _ in failures),
                    ended_at=max(last for _, _, last in failures),
                )

    def close(self) -> None:
        self._writer.close()


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


def set_session_statement_timeout(conn: Any, engine: str, timeout_ms: int) -> None:
    """Bound every statement this session runs.

    Without it one pathological query holds its worker until the run's own
    deadline, and a concurrency-mode run loses that worker entirely. A
    statement stopped this way surfaces as a 'statement timeout' failure and
    the worker moves on to the next query.
    """
    statement = (
        f"SET SESSION MAX_EXECUTION_TIME = {int(timeout_ms)}"
        if "mysql" in (engine or "").lower()
        else f"SET statement_timeout = {int(timeout_ms)}"
    )
    cursor = conn.cursor()
    try:
        cursor.execute(statement)
    finally:
        cursor.close()


def parameter_variants(
    entry: Any,
    resolved_sql: str,
    target: str,
    target_config: dict[str, Any],
    wanted: int,
) -> list[str]:
    """Build up to ``wanted`` concrete SQL variants for one stored query.

    The stored values are the first set; the rest come from the same
    suggestion machinery the parameter editor offers, so a rotation replays
    values the database actually holds. Ordering is deterministic, so two runs
    of the same query rotate through the same SQL in the same order.
    """
    variants = [resolved_sql]
    if wanted <= 1:
        return variants
    # Column sampling needs a database to read; a target that names none
    # cannot be sampled, so the stored values stand alone.
    if not target_config.get("database"):
        return variants

    from shared.query_registry.query_registry import (
        dialect_for_target,
        typed_parameter,
    )
    from shared.query_registry.sql_normalizer import (
        get_placeholder_names,
        reconstruct_sql,
    )

    template = entry.sql
    placeholder_names = get_placeholder_names(template)
    if not placeholder_names:
        return variants

    try:
        from features.analyze.parameter_suggestions import suggest_parameter_values

        suggested = suggest_parameter_values(
            template, target, target_config, query_hash=entry.hash
        )
    except Exception as exc:
        logger.debug("Load test could not suggest parameter values: %s", exc)
        return variants

    by_name: dict[str, list[str]] = {}
    for placeholder in suggested.get("placeholders") or ():
        name = str(placeholder.get("placeholder") or "").lstrip(":")
        values = [
            str(suggestion.get("value"))
            for suggestion in placeholder.get("suggestions") or ()
            if suggestion.get("value") is not None
        ]
        if name in placeholder_names and values:
            by_name[name] = values
    if not by_name:
        return variants

    stored = dict(entry.parameters or {})
    dialect = dialect_for_target(target)
    for offset in range(wanted - 1):
        params = dict(stored)
        used_any = False
        for name, values in by_name.items():
            if offset >= len(values):
                continue
            params[name] = typed_parameter(values[offset], "suggested")
            used_any = True
        if not used_any:
            break
        if set(params) < placeholder_names:
            continue
        try:
            candidate = reconstruct_sql(template, params, dialect)
        except Exception as exc:
            logger.debug("Load test could not build a parameter variant: %s", exc)
            continue
        if candidate not in variants and not _has_unresolved_placeholders(candidate):
            variants.append(candidate)
    return variants


def _has_unresolved_placeholders(sql: str) -> bool:
    """True when ``sql`` still carries a placeholder rather than a value."""
    # Scan code only: a substituted value may legitimately spell `?` or `$1`
    # inside a string literal.
    sql = mask_string_literals(sql)
    return bool(
        re.search(r"\$\d+", sql) or re.search(r"(?<!:):\w+", sql) or "?" in sql
    )


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
        parameter_sets: int = LOAD_TEST_PARAMETER_SETS,
        warmup_executions: int = LOAD_TEST_WARMUP_EXECUTIONS,
        statement_timeout_ms: int = LOAD_TEST_STATEMENT_TIMEOUT_MS,
        lanes: Optional[List[str]] = None,
        readyset: Optional[ReadysetEndpoint] = None,
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
                parameter_sets=parameter_sets,
                warmup_executions=warmup_executions,
                statement_timeout_ms=statement_timeout_ms,
                lanes=lanes,
                readyset=readyset,
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
            deadline = time.monotonic() + BENCHMARK_CANCEL_GRACE_SECONDS
            while not future.done() and time.monotonic() < deadline:
                try:
                    await asyncio.wait_for(asyncio.shield(future), timeout=0.1)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    continue
                except Exception:
                    break
            if not future.done():
                # Grace expired: force-close registered connections and stop
                # waiting on the daemon worker threads.
                controller.cancel()
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
        parameter_sets: int = LOAD_TEST_PARAMETER_SETS,
        warmup_executions: int = LOAD_TEST_WARMUP_EXECUTIONS,
        statement_timeout_ms: int = LOAD_TEST_STATEMENT_TIMEOUT_MS,
        lanes: Optional[List[str]] = None,
        readyset: Optional[ReadysetEndpoint] = None,
    ) -> None:
        """Synchronous benchmark worker that reports progress events."""
        evidence_recorders: list[_LoadTestEvidenceRecorder] = []

        @dataclass
        class _QueryStats:
            query_name: str
            query_hash: str
            variant_count: int = 1
            executions: int = 0
            successes: int = 0
            failures: int = 0
            timeouts: int = 0
            timings_ms: list[float] = field(default_factory=list)
            last_error: str | None = None
            # Server-side only: the driver text behind last_error, kept to
            # log each distinct failure once rather than every occurrence.
            last_error_detail: str | None = None

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
                    timeouts=self.timeouts,
                    variant_count=self.variant_count,
                )

        @dataclass
        class _ResolvedQuery:
            identifier: str
            name: str
            # The concrete SQL this query rotates through. A later dual-target
            # run pairs each variant with a second endpoint; nothing here
            # assumes one connection.
            variants: tuple[str, ...]
            # The registry entry behind an identifier-only request, which is
            # what a rotation needs to derive further parameter sets from.
            # Requests that carry their own SQL have none.
            entry: Any = None

        class _LaneRun:
            """One lane: its endpoint, its workload, its schedule, its numbers.

            Lanes measure the same workload against different endpoints and
            share nothing but the cancellation controller, so neither lane's
            pacing is a function of the other's latency.
            """

            def __init__(
                self,
                lane: str,
                target_config: dict[str, Any],
                queries: list[_ResolvedQuery],
                evidence: _LoadTestEvidenceRecorder,
            ) -> None:
                self.lane = lane
                self.target_config = target_config
                self.queries = queries
                self.evidence = evidence
                self.stop = threading.Event()
                self.failure: str | None = None
                # Every query the lane runs is listed from the start, so one
                # that never produced a measurement is visible as zeros
                # rather than absent.
                self.stats: dict[str, _QueryStats] = {
                    rq.identifier: _QueryStats(
                        rq.name, rq.identifier, variant_count=len(rq.variants)
                    )
                    for rq in queries
                }
                self.lock = Lock()
                self.warmup_completed = 0
                self.start_time = time.perf_counter()
                self.scheduler_lock = Lock()
                self.query_index = 0
                self.variant_index = 0
                self.claimed_count = 0
                self.warmup_index = 0
                self.measuring = False
                # Deterministic warmup plan: a fixed number of executions per
                # query, rotating its variants, drained by whichever worker is
                # free first.
                self.warmup_plan: list[tuple[int, int]] = [
                    (index, execution % len(rq.variants))
                    for index, rq in enumerate(queries)
                    for execution in range(warmup_executions)
                ]

            def record_execution(
                self,
                query_hash: str,
                query_name: str,
                duration_ms: float,
                success: bool,
                error_msg: str | None = None,
            ) -> None:
                with self.lock:
                    if query_hash not in self.stats:
                        self.stats[query_hash] = _QueryStats(query_name, query_hash)
                    stats = self.stats[query_hash]
                    stats.executions += 1
                    if success:
                        stats.successes += 1
                        stats.timings_ms.append(duration_ms)
                    else:
                        stats.failures += 1
                        if error_msg:
                            if error_msg != stats.last_error_detail:
                                logger.warning(
                                    "Benchmark query %s failed on the %s lane "
                                    "of %s: %s",
                                    query_name,
                                    self.lane,
                                    target,
                                    error_msg,
                                )
                            stats.last_error_detail = error_msg
                            stats.last_error = _execution_error_summary(error_msg)
                            if stats.last_error == "statement timeout":
                                stats.timeouts += 1

            def record_warmup(self) -> None:
                with self.lock:
                    self.warmup_completed += 1

            def has_measurements(self) -> bool:
                with self.lock:
                    return any(stats.executions > 0 for stats in self.stats.values())

            def claim(self) -> tuple[_ResolvedQuery, str, bool] | None:
                """Claim one execution: the query, its SQL, and whether it warms.

                Queries rotate in round-robin order and each pass moves to the
                next parameter variant, so a run covers every query at every
                value before repeating any pair.
                """
                with self.scheduler_lock:
                    if stop_event.is_set() or self.stop.is_set():
                        return None
                    if self.warmup_index < len(self.warmup_plan):
                        planned_query, planned_variant = self.warmup_plan[
                            self.warmup_index
                        ]
                        self.warmup_index += 1
                        rq = self.queries[planned_query]
                        return rq, rq.variants[planned_variant], True
                    if not self.measuring:
                        # Warmup runs off the clock, so the requested duration
                        # is all measurement.
                        self.measuring = True
                        self.start_time = time.perf_counter()
                    elapsed = time.perf_counter() - self.start_time
                    if duration_seconds and elapsed >= duration_seconds:
                        return None
                    if (
                        effective_max_count
                        and self.claimed_count >= effective_max_count
                    ):
                        return None
                    rq = self.queries[self.query_index]
                    sql = rq.variants[self.variant_index % len(rq.variants)]
                    self.query_index += 1
                    if self.query_index >= len(self.queries):
                        self.query_index = 0
                        self.variant_index += 1
                    self.claimed_count += 1
                    return rq, sql, False

            def snapshot(self) -> tuple[float, int, dict[str, QueryBenchmarkStats]]:
                """Elapsed time, completed warmups, and per-query statistics."""
                with self.lock:
                    return (
                        time.perf_counter() - self.start_time,
                        self.warmup_completed,
                        {
                            identifier: stats.to_model()
                            for identifier, stats in self.stats.items()
                        },
                    )

        try:
            try:
                requested_lanes = normalize_lanes(lanes)
            except ValueError as exc:
                raise BenchmarkValidationError(
                    str(exc), code="benchmark_lane_invalid"
                ) from exc
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
            if concurrency < 1 or concurrency > MAX_BENCHMARK_CONCURRENCY:
                raise BenchmarkValidationError(
                    "Concurrent workers must be between 1 and "
                    f"{MAX_BENCHMARK_CONCURRENCY}.",
                    code="benchmark_concurrency_capped",
                )
            if interval_ms < 0:
                raise BenchmarkValidationError(
                    "The pacing interval cannot be negative.",
                    code="benchmark_interval_invalid",
                )
            # Even when the caller omits max_count, bound the loop so a tight
            # loop (interval 0) can never run unbounded.
            effective_max_count = (
                max_count if max_count is not None else MAX_BENCHMARK_MAX_COUNT
            )
            # Tuning knobs, unlike the safety rails above, are clamped: a
            # caller that asks for more rotation or warmup than is useful gets
            # the useful amount rather than a rejected run.
            parameter_sets = max(1, min(int(parameter_sets), LOAD_TEST_PARAMETER_SETS))
            warmup_executions = max(
                0, min(int(warmup_executions), MAX_LOAD_TEST_WARMUP_EXECUTIONS)
            )
            statement_timeout_ms = max(
                0,
                min(
                    int(statement_timeout_ms),
                    MAX_BENCHMARK_DURATION_SECONDS * 1000,
                ),
            )

            from shared.config.targets import create_targets_config
            from shared.db_connection import close_connection, create_direct_connection
            from shared.query_registry import QueryRegistry

            registry = QueryRegistry()
            registry.load()

            resolved_queries: list[_ResolvedQuery] = []
            skipped_queries: list[QuerySkip] = []

            for spec in queries:
                if isinstance(spec, dict) or hasattr(spec, "sql"):
                    spec_dict = spec if isinstance(spec, dict) else spec.model_dump()
                    raw_sql = spec_dict.get("sql")
                    identifier = spec_dict.get("identifier") or "custom"
                    name = identifier[:8] if len(identifier) > 8 else identifier

                    if raw_sql:
                        if _has_unresolved_placeholders(raw_sql):
                            skipped_queries.append(
                                QuerySkip(
                                    identifier, name, SKIP_UNRESOLVED_PARAMETERS
                                )
                            )
                            continue
                        resolved_queries.append(
                            _ResolvedQuery(
                                identifier=identifier,
                                name=name,
                                variants=(raw_sql,),
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

                name = entry.tag or entry.hash[:8]
                sql = registry.get_executable_query(entry.hash, interactive=False)
                if not sql:
                    sql = entry.sql

                if _has_unresolved_placeholders(sql):
                    skipped_queries.append(
                        QuerySkip(entry.hash, name, SKIP_UNRESOLVED_PARAMETERS)
                    )
                    continue

                resolved_queries.append(
                    _ResolvedQuery(
                        identifier=entry.hash,
                        name=name,
                        variants=(sql,),
                        entry=entry,
                    )
                )

            if skipped_queries and not resolved_queries:
                if all(
                    skip.reason == SKIP_UNRESOLVED_PARAMETERS
                    for skip in skipped_queries
                ):
                    raise BenchmarkValidationError(
                        "All queries have unresolved parameters: "
                        + ", ".join(skip.query_name for skip in skipped_queries)
                        + ". Benchmark requires queries with concrete values, "
                        "not placeholders.",
                        code="benchmark_unresolved_params",
                    )
                raise BenchmarkValidationError(
                    "No query could be run: "
                    + ", ".join(
                        f"{skip.query_name} ({skip.reason})"
                        for skip in skipped_queries
                    )
                    + ".",
                    code="benchmark_all_queries_skipped",
                )

            if not resolved_queries:
                raise BenchmarkValidationError(
                    "No queries to run", code="benchmark_no_queries"
                )

            # Read-only rail: reject the whole run if any resolved query is not a
            # single read-only statement — enforced server-side regardless of the
            # UI, before opening a DB connection.
            for rq in resolved_queries:
                reason = benchmark_read_only_reason(rq.variants[0])
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

            # With the target settled, a stored query can be scoped to it and
            # rotated across the values that target knows about. A request that
            # brought its own SQL is left exactly as it was sent.
            scoped_queries: list[_ResolvedQuery] = []
            for rq in resolved_queries:
                if rq.entry is None:
                    scoped_queries.append(rq)
                    continue
                # A run measures one target, so a query with no activity on it
                # is not part of this run's workload.
                if not rq.entry.belongs_to_target(target):
                    skipped_queries.append(
                        QuerySkip(rq.identifier, rq.name, SKIP_TARGET_MISMATCH)
                    )
                    continue
                rq.variants = tuple(
                    parameter_variants(
                        rq.entry,
                        rq.variants[0],
                        target,
                        target_config,
                        parameter_sets,
                    )
                )
                scoped_queries.append(rq)
            resolved_queries = scoped_queries

            if not resolved_queries:
                raise BenchmarkValidationError(
                    "No query could be run: "
                    + ", ".join(
                        f"{skip.query_name} ({skip.reason})"
                        for skip in skipped_queries
                    )
                    + ".",
                    code="benchmark_all_queries_skipped",
                )

            # A substituted value must not turn a read into anything else.
            for rq in resolved_queries:
                for variant in rq.variants[1:]:
                    reason = benchmark_read_only_reason(variant)
                    if reason:
                        raise BenchmarkValidationError(
                            reason, code="benchmark_read_only"
                        )

            # The Readyset lane needs a cache per query on the leased sandbox
            # before any of its workers connect. A query the sandbox refuses
            # to cache is out of that lane and stays in the origin one.
            readyset_setup: dict[str, str] | None = None
            readyset_queries: list[_ResolvedQuery] = []
            if LANE_READYSET in requested_lanes:
                endpoint = readyset or ReadysetEndpoint(
                    status="unavailable",
                    detail="No Readyset sandbox was leased for this run.",
                )
                if endpoint.available:

                    # A cold sandbox can spend minutes building the first
                    # cache, so preparation reports itself on the stream
                    # rather than leaving the client with nothing to show.
                    def _note_preparing(prepared: int, total: int) -> None:
                        """Report cache preparation progress."""
                        try:
                            progress_queue.put_nowait(
                                QueryBenchmarkProgressEvent(
                                    type="progress",
                                    elapsed_seconds=0.0,
                                    total_executions=0,
                                    total_successes=0,
                                    total_failures=0,
                                    qps=0.0,
                                    queries=[],
                                    phase="preparing",
                                    prepared_count=prepared,
                                    prepare_total=total,
                                )
                            )
                        except Exception:
                            pass

                    _note_preparing(0, len(resolved_queries))
                    refused = prepare_lane_caches(
                        endpoint,
                        uuid.uuid4().hex,
                        [
                            (rq.identifier, rq.variants[0])
                            for rq in resolved_queries
                        ],
                        on_progress=_note_preparing,
                    )
                    for rq in resolved_queries:
                        if rq.identifier in refused:
                            skipped_queries.append(
                                QuerySkip(
                                    rq.identifier,
                                    rq.name,
                                    SKIP_READYSET_CACHE_FAILED,
                                    lanes={
                                        LANE_READYSET: SKIP_READYSET_CACHE_FAILED
                                    },
                                )
                            )
                        else:
                            readyset_queries.append(rq)
                    if not readyset_queries:
                        endpoint = ReadysetEndpoint(
                            status="unavailable",
                            detail=(
                                "Readyset could not cache any of this run's "
                                "queries."
                            ),
                        )
                readyset_setup = endpoint.as_payload()

            lane_runs: list[_LaneRun] = []
            for lane in requested_lanes:
                if lane == LANE_ORIGIN:
                    lane_runs.append(
                        _LaneRun(
                            lane,
                            target_config,
                            resolved_queries,
                            _LoadTestEvidenceRecorder(target, LANE_TAGS[lane]),
                        )
                    )
                elif readyset is not None and readyset.available and readyset_queries:
                    lane_runs.append(
                        _LaneRun(
                            lane,
                            dict(readyset.config or {}),
                            readyset_queries,
                            _LoadTestEvidenceRecorder(target, LANE_TAGS[lane]),
                        )
                    )
            if not lane_runs:
                # The Readyset lane is an addition to a run, never the whole
                # of one: a run left with no lane measures the origin.
                lane_runs.append(
                    _LaneRun(
                        LANE_ORIGIN,
                        target_config,
                        resolved_queries,
                        _LoadTestEvidenceRecorder(
                            target, LANE_TAGS[LANE_ORIGIN]
                        ),
                    )
                )
            evidence_recorders.extend(run.evidence for run in lane_runs)
            # Back-compatible reporting: the top-level tallies and the
            # per-query fields carry the first lane, which is the origin
            # whenever the run measures it.
            primary = lane_runs[0]
            worker_errors: list[BenchmarkValidationError] = []

            def _fail_lane(run: _LaneRun, error: BenchmarkValidationError) -> None:
                """Stop one lane; the primary lane stops the whole run."""
                run.failure = error.message
                run.stop.set()
                if run is primary:
                    worker_errors.append(error)
                    stop_event.set()

            def _progress(
                event_type: Literal["progress", "complete"],
            ) -> QueryBenchmarkProgressEvent | QueryBenchmarkCompleteEvent:
                snapshots = {run.lane: run.snapshot() for run in lane_runs}
                elapsed, warmup_completed, models = snapshots[primary.lane]
                total_exec = sum(model.executions for model in models.values())
                total_succ = sum(model.successes for model in models.values())
                total_fail = sum(model.failures for model in models.values())
                # Throughput reports successful work. Failed attempts stay
                # visible in total_failures/error rate instead of inflating
                # the headline QPS while latency is success-only.
                qps = total_succ / elapsed if elapsed > 0 else 0
                queries_list = list(models.values())
                if len(lane_runs) > 1:
                    for model in queries_list:
                        model.lanes = {
                            lane: _lane_stats_payload(lane_models[model.query_hash])
                            for lane, (_, _, lane_models) in snapshots.items()
                            if model.query_hash in lane_models
                        }
                # A query missing from one lane is still in the run, so the
                # count of queries the run left out stays what it was.
                skipped_count = sum(
                    1 for skip in skipped_queries if skip.lanes is None
                )
                if event_type == "complete":
                    return QueryBenchmarkCompleteEvent(
                        type="complete",
                        elapsed_seconds=elapsed,
                        total_executions=total_exec,
                        total_successes=total_succ,
                        total_failures=total_fail,
                        qps=qps,
                        queries=queries_list,
                        warmup_executions=warmup_completed,
                        skipped_count=skipped_count,
                        skipped_queries=list(skipped_queries),
                        lanes_run=[
                            run.lane
                            for run in lane_runs
                            if run.failure is None or run.has_measurements()
                        ],
                        readyset_setup=_readyset_setup(),
                    )
                return QueryBenchmarkProgressEvent(
                    type="progress",
                    elapsed_seconds=elapsed,
                    total_executions=total_exec,
                    total_successes=total_succ,
                    total_failures=total_fail,
                    qps=qps,
                    queries=queries_list,
                    warmup_executions=warmup_completed,
                    skipped_count=skipped_count,
                    skipped_queries=list(skipped_queries),
                )

            def _readyset_setup() -> dict[str, str] | None:
                """The Readyset lane's outcome, including a mid-run failure."""
                if readyset_setup is None:
                    return None
                for run in lane_runs:
                    if run.lane == LANE_READYSET and run.failure:
                        return {"status": "unavailable", "detail": run.failure}
                return readyset_setup

            def _has_measurements() -> bool:
                """Return whether the run has produced an observable result."""
                return any(run.has_measurements() for run in lane_runs)

            def _worker(run: _LaneRun) -> None:
                conn = None
                engine = str(run.target_config.get("engine", ""))
                try:
                    # Each concurrent worker owns its connection. Sharing a DB
                    # connection would serialize driver calls and make the
                    # advertised concurrency fictional.
                    conn = create_direct_connection(
                        run.target_config, lane=LANE_TAGS[run.lane]
                    )
                    # Register with the controller so a cancel can abort a
                    # statement this worker is blocked in server-side.
                    controller.register(conn)
                    try:
                        set_session_read_only(conn, engine)
                    except Exception as exc:
                        raise BenchmarkValidationError(
                            "Could not establish a read-only session on the "
                            f"{run.lane} endpoint; that lane was stopped.",
                            code="benchmark_read_only_session",
                        ) from exc
                    if statement_timeout_ms > 0:
                        try:
                            set_session_statement_timeout(
                                conn, engine, statement_timeout_ms
                            )
                        except Exception as exc:
                            # An engine that refuses the setting still runs the
                            # measurement; only the per-statement bound is lost.
                            logger.warning(
                                "Could not bound benchmark statements on the "
                                "%s lane of %s: %s",
                                run.lane,
                                target,
                                exc,
                            )

                    while not stop_event.is_set() and not run.stop.is_set():
                        claim = run.claim()
                        if claim is None:
                            break
                        rq, sql, warming = claim

                        exec_start = time.perf_counter()
                        cursor = None
                        evidence_token: int | None = None
                        try:
                            cursor = conn.cursor()
                            # This is the attribution boundary: connection and
                            # cursor failures before it record no traffic.
                            evidence_token = run.evidence.note_started(sql)
                            cursor.execute(sql)
                            cursor.fetchall()
                            run.evidence.note_completed(evidence_token)
                            if warming:
                                run.record_warmup()
                            else:
                                run.record_execution(
                                    rq.identifier,
                                    rq.name,
                                    (time.perf_counter() - exec_start) * 1000,
                                    success=True,
                                )
                        except Exception as exc:
                            if evidence_token is not None:
                                run.evidence.note_failed(evidence_token)
                            if warming:
                                # A warmup failure is not a measurement; the
                                # same query fails again under measurement and
                                # is reported there.
                                run.record_warmup()
                            else:
                                run.record_execution(
                                    rq.identifier,
                                    rq.name,
                                    (time.perf_counter() - exec_start) * 1000,
                                    success=False,
                                    error_msg=str(exc),
                                )
                        finally:
                            if cursor is not None:
                                try:
                                    cursor.close()
                                except Exception:
                                    pass

                        # Interval mode is intentionally a paced closed loop:
                        # wait after the completed query. Concurrency mode keeps
                        # the configured number of independent workers busy.
                        if mode == "interval" and interval_ms > 0:
                            stop_event.wait(interval_ms / 1000.0)
                except BenchmarkValidationError as exc:
                    _fail_lane(run, exc)
                except Exception:
                    _fail_lane(
                        run,
                        BenchmarkValidationError(
                            "A benchmark worker could not connect to the "
                            f"{run.lane} endpoint.",
                            code="benchmark_worker_failed",
                        ),
                    )
                finally:
                    if conn is not None:
                        controller.unregister(conn)
                        close_connection(conn)

            # Each lane gets its own pool of the requested size, so both
            # endpoints see the same offered load and neither lane's clients
            # queue behind the other's.
            worker_count = concurrency if mode == "concurrency" else 1
            workers = [
                threading.Thread(
                    target=_worker,
                    args=(run,),
                    name=f"query-benchmark-{run.lane}-{index + 1}",
                    daemon=True,
                )
                for run in lane_runs
                for index in range(worker_count)
            ]
            for worker in workers:
                worker.start()

            last_progress_time = 0.0
            progress_interval = 0.25
            stop_deadline: float | None = None
            while any(worker.is_alive() for worker in workers):
                now = time.perf_counter()
                if stop_event.is_set():
                    if stop_deadline is None:
                        stop_deadline = now + BENCHMARK_CANCEL_GRACE_SECONDS
                    elif now >= stop_deadline:
                        # A worker blocked in a driver call cannot observe
                        # stop_event; force-close its connection and stop
                        # waiting on the daemon threads.
                        controller.cancel()
                        break
                if (
                    now - last_progress_time >= progress_interval
                    and _has_measurements()
                ):
                    try:
                        progress_queue.put_nowait(_progress("progress"))
                    except Exception:
                        pass
                    last_progress_time = now
                for worker in workers:
                    worker.join(timeout=0.02)
                for recorder in evidence_recorders:
                    recorder.flush_closed()

            if worker_errors:
                raise worker_errors[0]

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
        finally:
            for recorder in evidence_recorders:
                recorder.flush_all()
                recorder.close()

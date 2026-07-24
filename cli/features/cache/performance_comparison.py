from __future__ import annotations

import subprocess  # nosec B404  # nosemgrep: gitlab.bandit.B404 - subprocess required for Docker/database operations
import time
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, cast

from shared.ui import (
    get_console,
    StyleTokens,
    SectionHeader,
    StatusLine,
    Rule,
    Icons,
    Text,
)
from shared.ui.theme import duration_style

MIN_COMPARISON_ITERATIONS = 1
MAX_COMPARISON_ITERATIONS = 1000
MIN_COMPARISON_WARMUP = 0
MAX_COMPARISON_WARMUP = 100
MAX_COMPARISON_INTERVAL_MS = 60_000
MAX_COMPARISON_CONCURRENCY = 64
MAX_COMPARISON_DURATION_SECONDS = 300


class ComparisonCancelled(Exception):
    """Raised inside the benchmark worker when its owning run is cancelled."""


class ComparisonController:
    """Thread-safe cancellation bridge for synchronous database drivers."""

    def __init__(self):
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._connections: list[Any] = []

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def register(self, connection: Any) -> None:
        with self._lock:
            if self.cancelled:
                self._cancel_connection(connection)
                raise ComparisonCancelled()
            self._connections.append(connection)

    def unregister(self, connection: Any) -> None:
        with self._lock:
            if connection in self._connections:
                self._connections.remove(connection)

    def cancel(self) -> None:
        self._cancelled.set()
        with self._lock:
            connections = list(self._connections)
        self.cancel_connections(connections)

    @classmethod
    def cancel_connections(cls, connections: list[Any]) -> None:
        for connection in connections:
            cls._cancel_connection(connection)

    @staticmethod
    def _cancel_connection(connection: Any) -> None:
        try:
            cancel = getattr(connection, "cancel", None)
            if callable(cancel):
                cancel()
            else:
                connection.close()
        except Exception:
            pass

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise ComparisonCancelled()

    def wait(self, timeout: float) -> None:
        if self._cancelled.wait(timeout):
            raise ComparisonCancelled()


def _open_persistent_connection(db_config: Dict[str, Any]):
    """Open a persistent database connection for benchmarking.

    Returns (connection, engine) tuple. Caller must close the connection.
    """
    engine = (db_config.get("engine") or "postgresql").lower()
    host = str(db_config.get("host") or "localhost")
    default_port = 3306 if engine == "mysql" else 5432
    port = int(db_config.get("port") or default_port)
    database = str(db_config.get("database") or "")
    user = str(db_config.get("user") or "")
    password = db_config.get("password", "")

    if engine == "mysql":
        normalized_host = host if host != "localhost" else "127.0.0.1"
        import pymysql
        conn = pymysql.connect(
            host=normalized_host, port=port, user=user,
            password=password, database=database, connect_timeout=30,
            autocommit=True,
        )
    else:
        import psycopg2
        conn = psycopg2.connect(
            host=host, port=port, user=user,
            password=password, database=database, connect_timeout=30,
        )
        conn.autocommit = True
    return conn, engine


def _execute_on_connection(
    conn,
    query: str,
    engine: str,
    controller: Optional[ComparisonController] = None,
) -> Dict[str, Any]:
    """Execute a query on an existing connection and measure query time only."""
    try:
        if controller:
            controller.raise_if_cancelled()
        start = time.perf_counter()
        with conn.cursor() as cursor:
            cursor.execute(query)
            cursor.fetchall()
        end = time.perf_counter()
        if controller:
            controller.raise_if_cancelled()
        return {"success": True, "execution_time_ms": (end - start) * 1000}
    except Exception as e:
        if controller and controller.cancelled:
            raise ComparisonCancelled() from e
        return {"success": False, "error": str(e)}


def _run_concurrent_measurements(
    *,
    query: str,
    db_config: Dict[str, Any],
    label: str,
    stage: str,
    iterations: int,
    warmup_iterations: int,
    concurrency: int,
    duration_seconds: int | None,
    progress,
    controller: ComparisonController | None,
) -> Dict[str, Any]:
    connections: list[Any] = []
    samples: list[tuple[int, float]] = []
    state_lock = threading.Lock()
    stop = threading.Event()
    deadline_expired = threading.Event()
    deadline_timer: threading.Timer | None = None
    next_index = 0
    failure: tuple[int, str] | None = None

    try:
        engine = (db_config.get("engine") or "postgresql").lower()
        worker_count = min(concurrency, iterations)
        for _ in range(worker_count):
            connection, connection_engine = _open_persistent_connection(db_config)
            engine = connection_engine
            if controller:
                controller.register(connection)
            connections.append(connection)

        deadline = (
            time.perf_counter() + duration_seconds
            if duration_seconds is not None
            else None
        )
        if duration_seconds is not None:
            def expire_deadline() -> None:
                deadline_expired.set()
                stop.set()
                ComparisonController.cancel_connections(connections)

            deadline_timer = threading.Timer(duration_seconds, expire_deadline)
            deadline_timer.daemon = True
            deadline_timer.start()

        for index in range(warmup_iterations):
            if deadline_expired.is_set():
                break
            result = _execute_on_connection(
                connections[0], query, engine, controller
            )
            if not result["success"]:
                if deadline_expired.is_set():
                    break
                return {
                    "success": False,
                    "error": (
                        f"{label} warmup iteration {index + 1} failed: "
                        f"{result.get('error') or 'unknown error'}"
                    ),
                }
            progress(f"{stage}_warmup", index + 1, warmup_iterations)

        def worker(connection: Any) -> None:
            nonlocal next_index, failure
            while not stop.is_set():
                if controller:
                    controller.raise_if_cancelled()
                with state_lock:
                    if next_index >= iterations:
                        return
                    if deadline is not None and time.perf_counter() >= deadline:
                        return
                    sample_index = next_index
                    next_index += 1

                result = _execute_on_connection(
                    connection, query, engine, controller
                )
                if not result["success"]:
                    if deadline_expired.is_set():
                        return
                    should_cancel = False
                    with state_lock:
                        if failure is None:
                            failure = (
                                sample_index,
                                str(result.get("error") or "unknown error"),
                            )
                            should_cancel = True
                    stop.set()
                    if should_cancel:
                        ComparisonController.cancel_connections(connections)
                    return

                with state_lock:
                    samples.append((sample_index, result["execution_time_ms"]))
                    completed = len(samples)
                    progress(stage, completed, iterations)

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(worker, connection) for connection in connections
            ]
            for future in futures:
                future.result()

        if controller:
            controller.raise_if_cancelled()
        if failure is not None:
            index, error = failure
            return {
                "success": False,
                "error": f"{label} iteration {index + 1} failed: {error}",
            }
        if not samples:
            return {
                "success": False,
                "error": f"{label} completed no measured iterations",
            }
        samples.sort(key=lambda item: item[0])
        return {"success": True, "times": [value for _, value in samples]}
    finally:
        if deadline_timer is not None:
            deadline_timer.cancel()
            deadline_timer.join()
        for connection in connections:
            if controller:
                controller.unregister(connection)
            try:
                connection.close()
            except Exception:
                pass


def run_comparison(
    query: str,
    original_db_config: Dict[str, Any],
    readyset_db_config: Dict[str, Any],
    iterations: int = 5,
    warmup_iterations: int = 2,
    interval_ms: int | None = None,
    concurrency: int | None = None,
    duration_seconds: int | None = None,
    on_progress: Optional[Any] = None,
    controller: Optional[ComparisonController] = None,
) -> Dict[str, Any]:
    """Run a headless performance comparison — no console output.

    Uses persistent connections so that connection overhead is excluded
    from timing measurements.  Only query execution time is measured.

    Args:
        interval_ms: Fixed start interval for sequential executions.
        concurrency: Number of concurrent persistent connections per side.
        duration_seconds: Per-side measurement deadline; count remains the cap.
        on_progress: Optional callable(stage, current, total) for progress updates.

    Suitable for service-layer / API use.
    """
    if not MIN_COMPARISON_ITERATIONS <= iterations <= MAX_COMPARISON_ITERATIONS:
        return {
            "success": False,
            "error": (
                "Measured iterations must be between "
                f"{MIN_COMPARISON_ITERATIONS} and {MAX_COMPARISON_ITERATIONS}"
            ),
        }
    if not MIN_COMPARISON_WARMUP <= warmup_iterations <= MAX_COMPARISON_WARMUP:
        return {
            "success": False,
            "error": (
                "Warmup iterations must be between "
                f"{MIN_COMPARISON_WARMUP} and {MAX_COMPARISON_WARMUP}"
            ),
        }
    if interval_ms is not None and concurrency is not None:
        return {
            "success": False,
            "error": "Cannot specify both interval and concurrency",
        }
    if (
        interval_ms is not None
        and not 0 <= interval_ms <= MAX_COMPARISON_INTERVAL_MS
    ):
        return {
            "success": False,
            "error": (
                "Comparison interval must be between 0 and "
                f"{MAX_COMPARISON_INTERVAL_MS} milliseconds"
            ),
        }
    if (
        concurrency is not None
        and not 1 <= concurrency <= MAX_COMPARISON_CONCURRENCY
    ):
        return {
            "success": False,
            "error": (
                "Comparison concurrency must be between 1 and "
                f"{MAX_COMPARISON_CONCURRENCY}"
            ),
        }
    if (
        duration_seconds is not None
        and not 1 <= duration_seconds <= MAX_COMPARISON_DURATION_SECONDS
    ):
        return {
            "success": False,
            "error": (
                "Comparison duration must be between 1 and "
                f"{MAX_COMPARISON_DURATION_SECONDS} seconds"
            ),
        }

    def _progress(stage: str, current: int, total: int):
        if on_progress:
            try:
                on_progress(stage, current, total)
            except Exception:
                pass

    def _run_sequential_measurements(
        connection: Any, engine: str, label: str, stage: str
    ) -> Dict[str, Any]:
        times: List[float] = []
        interval_seconds = (interval_ms or 0) / 1000
        deadline = (
            time.perf_counter() + duration_seconds
            if duration_seconds is not None
            else None
        )
        deadline_expired = threading.Event()
        deadline_timer = None
        if duration_seconds is not None:
            def expire_deadline() -> None:
                deadline_expired.set()
                ComparisonController.cancel_connections([connection])

            deadline_timer = threading.Timer(duration_seconds, expire_deadline)
            deadline_timer.daemon = True
            deadline_timer.start()

        next_start = time.perf_counter()
        try:
            for index in range(warmup_iterations):
                if deadline_expired.is_set():
                    break
                result = _execute_on_connection(
                    connection, query, engine, controller
                )
                if not result["success"]:
                    if deadline_expired.is_set():
                        break
                    return {
                        "success": False,
                        "error": (
                            f"{label} warmup iteration {index + 1} failed: "
                            f"{result.get('error') or 'unknown error'}"
                        ),
                    }
                _progress(f"{stage}_warmup", index + 1, warmup_iterations)

            for index in range(iterations):
                now = time.perf_counter()
                if index > 0 and now < next_start:
                    delay = next_start - now
                    if deadline is not None:
                        delay = min(delay, max(0, deadline - now))
                    if delay > 0:
                        if controller:
                            controller.wait(delay)
                        else:
                            time.sleep(delay)
                if deadline is not None and time.perf_counter() >= deadline:
                    break

                started = time.perf_counter()
                result = _execute_on_connection(
                    connection, query, engine, controller
                )
                if not result["success"]:
                    if deadline_expired.is_set():
                        break
                    return {
                        "success": False,
                        "error": (
                            f"{label} iteration {index + 1} failed: "
                            f"{result.get('error') or 'unknown error'}"
                        ),
                    }
                times.append(result["execution_time_ms"])
                _progress(stage, len(times), iterations)
                next_start = started + interval_seconds
        finally:
            if deadline_timer is not None:
                deadline_timer.cancel()
                deadline_timer.join()

        if not times:
            return {
                "success": False,
                "error": f"{label} completed no measured iterations",
            }
        return {"success": True, "times": times}

    origin_conn = None
    cache_conn = None
    try:
        if concurrency is not None:
            original_result = _run_concurrent_measurements(
                query=query,
                db_config=original_db_config,
                label="Original database",
                stage="origin",
                iterations=iterations,
                warmup_iterations=warmup_iterations,
                concurrency=concurrency,
                duration_seconds=duration_seconds,
                progress=_progress,
                controller=controller,
            )
            if not original_result["success"]:
                return original_result
            readyset_result = _run_concurrent_measurements(
                query=query,
                db_config=readyset_db_config,
                label="Readyset",
                stage="cache",
                iterations=iterations,
                warmup_iterations=warmup_iterations,
                concurrency=concurrency,
                duration_seconds=duration_seconds,
                progress=_progress,
                controller=controller,
            )
            if not readyset_result["success"]:
                return readyset_result
            original_times = original_result["times"]
            readyset_times = readyset_result["times"]
        else:
            origin_conn, origin_engine = _open_persistent_connection(
                original_db_config
            )
            if controller:
                controller.register(origin_conn)
            cache_conn, cache_engine = _open_persistent_connection(
                readyset_db_config
            )
            if controller:
                controller.register(cache_conn)

            original_result = _run_sequential_measurements(
                origin_conn, origin_engine, "Original database", "origin"
            )
            if not original_result["success"]:
                return original_result
            readyset_result = _run_sequential_measurements(
                cache_conn, cache_engine, "Readyset", "cache"
            )
            if not readyset_result["success"]:
                return readyset_result
            original_times = original_result["times"]
            readyset_times = readyset_result["times"]

        original_stats = _calculate_statistics(original_times)
        readyset_stats = _calculate_statistics(readyset_times)

        speedup = (
            original_stats["mean"] / readyset_stats["mean"]
            if readyset_stats["mean"] > 0
            else 0
        )
        speedup_median = (
            original_stats["median"] / readyset_stats["median"]
            if readyset_stats["median"] > 0
            else 0
        )

        return {
            "success": True,
            "query": query,
            "iterations": min(len(original_times), len(readyset_times)),
            "requested_iterations": iterations,
            "original": {
                "host": original_db_config.get("host"),
                "port": original_db_config.get("port"),
                "stats": original_stats,
                "times": original_times,
                "iterations": len(original_times),
            },
            "readyset": {
                "host": readyset_db_config.get("host"),
                "port": readyset_db_config.get("port"),
                "stats": readyset_stats,
                "times": readyset_times,
                "iterations": len(readyset_times),
            },
            "speedup": {
                "mean": speedup,
                "median": speedup_median,
                "improvement_pct": ((speedup - 1) * 100)
                if speedup >= 1
                else -((1 - speedup) * 100),
            },
            "winner": "readyset" if speedup > 1 else "original",
        }
    except ComparisonCancelled:
        return {"success": False, "cancelled": True, "error": "Comparison cancelled"}
    except Exception as e:
        return {"success": False, "error": f"Performance comparison failed: {str(e)}"}
    finally:
        if origin_conn:
            if controller:
                controller.unregister(origin_conn)
            try:
                origin_conn.close()
            except Exception:
                pass
        if cache_conn:
            if controller:
                controller.unregister(cache_conn)
            try:
                cache_conn.close()
            except Exception:
                pass


def compare_query_performance(
    query: Optional[str] = None,
    original_db_config: Optional[Dict[str, Any]] = None,
    readyset_port: int | str = 5433,
    readyset_host: str = "localhost",
    iterations: int | str = 10,
    warmup_iterations: int | str = 2,
    readyset_db_config: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Compare query performance between original database and Readyset.

    CLI-facing wrapper that prints Rich output during execution.
    Delegates to run_comparison() for the actual benchmarking.
    """
    try:
        if not query or not original_db_config:
            return {
                "success": False,
                "error": "Query and original database configuration are required",
            }

        # Parse config if it's a JSON string
        if isinstance(original_db_config, str):
            import json

            original_db_config = json.loads(original_db_config)

        original_db_config = cast(Dict[str, Any], original_db_config)
        readyset_db_config = cast(Dict[str, Any], readyset_db_config or {})

        iterations = int(iterations)
        warmup_iterations = int(warmup_iterations)
        readyset_port = int(readyset_port)

        engine = (original_db_config.get("engine") or "postgresql").lower()

        # Build readyset config
        if readyset_db_config:
            readyset_config = {
                "engine": engine,
                "host": readyset_host,
                "port": readyset_port,
                "database": readyset_db_config.get(
                    "database", original_db_config.get("database")
                ),
                "user": readyset_db_config.get("user", original_db_config.get("user")),
                "password": readyset_db_config.get("password", ""),
            }
        else:
            readyset_config = {
                "engine": engine,
                "host": readyset_host,
                "port": readyset_port,
                "database": original_db_config.get("database"),
                "user": original_db_config.get("user"),
                "password": original_db_config.get("password", ""),
            }

        console = get_console()
        console.print(SectionHeader("Benchmarking Query Performance"))
        console.print(StatusLine("Iterations", str(iterations)))
        console.print(StatusLine("Warmup", f"{warmup_iterations} iterations"))
        console.print()

        # Warmup - Original DB
        console.print(StatusLine("Warmup", "Original database"))
        for _ in range(warmup_iterations):
            _execute_query_timed(query, original_db_config, is_readyset=False)

        # Warmup - Readyset
        console.print(StatusLine("Warmup", "Readyset"))
        for _ in range(warmup_iterations):
            _execute_query_timed(query, readyset_config, is_readyset=True)

        console.print()
        console.print(SectionHeader("Running Benchmarks"))

        # Benchmark Original DB
        console.print(
            StatusLine(
                "Original",
                f"{original_db_config.get('host')}:{original_db_config.get('port')}",
            )
        )
        original_times = []
        for i in range(iterations):
            result = _execute_query_timed(query, original_db_config, is_readyset=False)
            if result["success"]:
                original_times.append(result["execution_time_ms"])
                console.print(
                    StatusLine(
                        f"Run {i + 1}/{iterations}",
                        f"{result['execution_time_ms']:.2f}ms",
                    )
                )
            else:
                console.print(
                    StatusLine(
                        f"Run {i + 1}/{iterations}",
                        f"FAILED - {result.get('error')}",
                        style=StyleTokens.ERROR,
                    )
                )

        if not original_times:
            return {"success": False, "error": "All original database queries failed"}

        console.print()

        # Benchmark Readyset
        console.print(StatusLine("Readyset", f"{readyset_host}:{readyset_port}"))
        readyset_times = []
        for i in range(iterations):
            result = _execute_query_timed(query, readyset_config, is_readyset=True)
            if result["success"]:
                readyset_times.append(result["execution_time_ms"])
                console.print(
                    StatusLine(
                        f"Run {i + 1}/{iterations}",
                        f"{result['execution_time_ms']:.2f}ms",
                    )
                )
            else:
                console.print(
                    StatusLine(
                        f"Run {i + 1}/{iterations}",
                        f"FAILED - {result.get('error')}",
                        style=StyleTokens.ERROR,
                    )
                )

        if not readyset_times:
            return {"success": False, "error": "All Readyset queries failed"}

        # Calculate statistics
        original_stats = _calculate_statistics(original_times)
        readyset_stats = _calculate_statistics(readyset_times)

        # Calculate speedup
        speedup = (
            original_stats["mean"] / readyset_stats["mean"]
            if readyset_stats["mean"] > 0
            else 0
        )
        speedup_median = (
            original_stats["median"] / readyset_stats["median"]
            if readyset_stats["median"] > 0
            else 0
        )

        return {
            "success": True,
            "query": query,
            "iterations": iterations,
            "original": {
                "host": original_db_config.get("host"),
                "port": original_db_config.get("port"),
                "stats": original_stats,
                "times": original_times,
            },
            "readyset": {
                "host": readyset_host,
                "port": readyset_port,
                "stats": readyset_stats,
                "times": readyset_times,
            },
            "speedup": {
                "mean": speedup,
                "median": speedup_median,
                "improvement_pct": ((speedup - 1) * 100)
                if speedup >= 1
                else -((1 - speedup) * 100),
            },
            "winner": "readyset" if speedup > 1 else "original",
        }

    except Exception as e:
        return {"success": False, "error": f"Performance comparison failed: {str(e)}"}


def _execute_query_timed(
    query: str, db_config: Dict[str, Any], is_readyset: bool = False
) -> Dict[str, Any]:
    """
    Execute a query and measure its execution time.

    Args:
        query: SQL query to execute
        db_config: Database configuration
        is_readyset: Whether this is a Readyset connection

    Returns:
        Dict with success, execution_time_ms, and optional error
    """
    try:
        engine = (db_config.get("engine") or "postgresql").lower()
        host = str(db_config.get("host") or "localhost")
        default_port = 3306 if engine == "mysql" else 5432
        port = int(db_config.get("port") or default_port)
        database = str(db_config.get("database") or "")
        user = str(db_config.get("user") or "")
        password = db_config.get("password", "")

        start_time = time.perf_counter()

        if engine == "mysql":
            result = _execute_mysql_query(
                query=query,
                host=host,
                port=port,
                user=user,
                database=database,
                password=password,
            )
        else:
            result = _execute_postgres_query(
                query=query,
                host=host,
                port=port,
                user=user,
                database=database,
                password=password,
            )

        end_time = time.perf_counter()
        execution_time_ms = (end_time - start_time) * 1000

        if result.returncode != 0:
            return {
                "success": False,
                "error": result.stderr.strip()
                if result.stderr
                else "Query execution failed",
                "execution_time_ms": execution_time_ms,
            }

        return {
            "success": True,
            "execution_time_ms": execution_time_ms,
            "rows": result.stdout.count("\n") if result.stdout else 0,
        }

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Query execution timed out"}
    except Exception as e:
        return {"success": False, "error": f"Query execution failed: {str(e)}"}


def _execute_postgres_query(
    query: str, host: str, port: int, user: str, database: str, password: str
):
    """Execute query using psycopg2 library or psql client as fallback."""
    # Try psycopg2 first
    try:
        import psycopg2

        return _execute_postgres_query_psycopg2(
            query=query,
            host=host,
            port=port,
            user=user,
            database=database,
            password=password,
        )
    except ImportError:
        # Fall back to psql CLI if psycopg2 not available
        pass

    psql_cmd = [
        "psql",
        "-h",
        host,
        "-p",
        str(port),
        "-U",
        user,
        "-d",
        database,
        "-c",
        query,
        "-t",  # Tuples only
        "-A",  # Unaligned
        "-q",  # Quiet
    ]

    import os

    env = os.environ.copy()
    env["PGPASSWORD"] = password if password else ""

    return subprocess.run(psql_cmd, capture_output=True, text=True, env=env, timeout=30)


def _execute_postgres_query_psycopg2(
    query: str, host: str, port: int, user: str, database: str, password: str
):
    """Execute query using psycopg2 library."""
    import psycopg2
    from dataclasses import dataclass

    @dataclass
    class CompletedProcess:
        returncode: int
        stdout: str
        stderr: str

    try:
        connection = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=30,
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(query)
                result = cursor.fetchall()

                # Format output similar to psql CLI (pipe-separated rows)
                output_lines = []
                for row in result:
                    output_lines.append(
                        "|".join(str(val) if val is not None else "" for val in row)
                    )

                return CompletedProcess(
                    returncode=0, stdout="\n".join(output_lines), stderr=""
                )
        finally:
            connection.close()

    except Exception as e:
        return CompletedProcess(returncode=1, stdout="", stderr=str(e))


def _execute_mysql_query(
    query: str, host: str, port: int, user: str, database: str, password: str
):
    """Execute query using mysql client or pymysql as fallback."""
    # Ensure TCP is used even if host is "localhost"
    normalized_host = host or "127.0.0.1"
    if normalized_host == "localhost":
        normalized_host = "127.0.0.1"

    # Try pymysql first (works with Readyset's mysql_native_password auth)
    try:
        import pymysql

        return _execute_mysql_query_pymysql(
            query=query,
            host=normalized_host,
            port=port,
            user=user,
            database=database,
            password=password,
        )
    except ImportError:
        # Fall back to mysql CLI if pymysql not available
        pass

    mysql_cmd = [
        "mysql",
        "--protocol=TCP",
        f"--host={normalized_host}",
        f"--port={port}",
        f"--user={user}",
        f"--database={database}",
        "--batch",
        "--skip-column-names",
        "--raw",
        "--execute",
        query,
    ]

    import os

    env = os.environ.copy()
    if password:
        env["MYSQL_PWD"] = password

    return subprocess.run(
        mysql_cmd, capture_output=True, text=True, env=env, timeout=30
    )


def _execute_mysql_query_pymysql(
    query: str, host: str, port: int, user: str, database: str, password: str
):
    """Execute query using PyMySQL library (handles mysql_native_password)."""
    import pymysql
    from dataclasses import dataclass

    @dataclass
    class CompletedProcess:
        returncode: int
        stdout: str
        stderr: str

    try:
        start = time.perf_counter()
        connection = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=30,
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(query)
                result = cursor.fetchall()

                # Format output similar to mysql CLI (tab-separated rows)
                output_lines = []
                for row in result:
                    output_lines.append(
                        "\t".join(
                            str(val) if val is not None else "NULL" for val in row
                        )
                    )

                return CompletedProcess(
                    returncode=0, stdout="\n".join(output_lines), stderr=""
                )
        finally:
            connection.close()

    except Exception as e:
        return CompletedProcess(returncode=1, stdout="", stderr=str(e))


def _calculate_statistics(times: List[float]) -> Dict[str, float]:
    """Calculate performance statistics from a list of execution times."""
    if not times:
        return {
            "mean": 0,
            "median": 0,
            "min": 0,
            "max": 0,
            "stddev": 0,
            "p50": 0,
            "p95": 0,
            "p99": 0,
        }

    sorted_times = sorted(times)

    return {
        "mean": statistics.mean(times),
        "median": statistics.median(times),
        "min": min(times),
        "max": max(times),
        "stddev": statistics.stdev(times) if len(times) > 1 else 0,
        "p50": _percentile(sorted_times, 50),
        "p95": _percentile(sorted_times, 95),
        "p99": _percentile(sorted_times, 99),
    }


def _percentile(sorted_data: List[float], percentile: float) -> float:
    """Calculate percentile from sorted data."""
    if not sorted_data:
        return 0

    k = (len(sorted_data) - 1) * (percentile / 100)
    f = int(k)
    c = f + 1

    if c >= len(sorted_data):
        return sorted_data[-1]

    d0 = sorted_data[f]
    d1 = sorted_data[c]

    return d0 + (d1 - d0) * (k - f)


def format_performance_comparison(result: Dict[str, Any]) -> str:
    """
    Format performance comparison results as human-readable text.

    Args:
        result: Performance comparison result dict

    Returns:
        Formatted string with performance comparison
    """
    if not result.get("success"):
        return f"Performance comparison failed: {result.get('error')}"

    console = get_console()
    with console.capture() as capture:
        console.print(SectionHeader("PERFORMANCE COMPARISON", icon=Icons.CHART))
        console.print()

        # Original DB stats
        orig = result["original"]
        console.print(
            f"Original Database ({orig['host']}:{orig['port']})",
            style=StyleTokens.HEADER,
        )
        console.print(Rule(style=StyleTokens.MUTED))
        console.print(
            "  ",
            StatusLine("Mean", f"{orig['stats']['mean']:>8.2f} ms"),
            "  ",
            StatusLine("Median", f"{orig['stats']['median']:>8.2f} ms"),
        )
        console.print(
            "  ",
            StatusLine("Min", f"{orig['stats']['min']:>8.2f} ms"),
            "   ",
            StatusLine("Max", f"{orig['stats']['max']:>8.2f} ms"),
        )
        console.print(
            "  ",
            StatusLine("P95", f"{orig['stats']['p95']:>8.2f} ms"),
            "   ",
            StatusLine("P99", f"{orig['stats']['p99']:>8.2f} ms"),
        )
        console.print()

        # Readyset stats
        rs = result["readyset"]
        console.print(
            f"Readyset Cache ({rs['host']}:{rs['port']})", style=StyleTokens.HEADER
        )
        console.print(Rule(style=StyleTokens.MUTED))
        console.print(
            "  ",
            StatusLine(
                "Mean",
                f"{rs['stats']['mean']:>8.2f} ms",
                style=duration_style(rs["stats"]["mean"]),
            ),
            "  ",
            StatusLine("Median", f"{rs['stats']['median']:>8.2f} ms"),
        )
        console.print(
            "  ",
            StatusLine("Min", f"{rs['stats']['min']:>8.2f} ms"),
            "   ",
            StatusLine("Max", f"{rs['stats']['max']:>8.2f} ms"),
        )
        console.print(
            "  ",
            StatusLine("P95", f"{rs['stats']['p95']:>8.2f} ms"),
            "   ",
            StatusLine("P99", f"{rs['stats']['p99']:>8.2f} ms"),
        )
        console.print()

        # Speedup
        speedup = result["speedup"]
        console.print("Performance Improvement", style=StyleTokens.HEADER)
        console.print(Rule(style=StyleTokens.MUTED))

        if speedup["mean"] > 1:
            console.print(
                f"  {Icons.SUCCESS} Readyset is [{StyleTokens.SUCCESS}]{speedup['mean']:.2f}x faster[/{StyleTokens.SUCCESS}] (mean)"
            )
            console.print(
                f"  {Icons.SUCCESS} [{StyleTokens.SUCCESS}]{speedup['improvement_pct']:.1f}% improvement[/{StyleTokens.SUCCESS}]"
            )
        elif speedup["mean"] < 1:
            console.print(
                f"  {Icons.ERROR} Readyset is [{StyleTokens.ERROR}]{(1 / speedup['mean']):.2f}x slower[/{StyleTokens.ERROR}] (mean)"
            )
            console.print(
                f"  {Icons.ERROR} [{StyleTokens.ERROR}]{abs(speedup['improvement_pct']):.1f}% slower[/{StyleTokens.ERROR}]"
            )
        else:
            console.print(f"  {Icons.INFO} Performance is roughly equal")

        console.print(f"  Median speedup: {speedup['median']:.2f}x")
        console.print()

        # Summary
        winner = result["winner"]
        if winner == "readyset":
            console.print(
                f"{Icons.ROCKET} [{StyleTokens.SUCCESS}]Readyset cache provides better performance![/{StyleTokens.SUCCESS}]"
            )
        else:
            console.print(
                f"{Icons.WARNING} [{StyleTokens.WARNING}]Original database is faster for this query[/{StyleTokens.WARNING}]"
            )
            console.print(
                f"   [{StyleTokens.MUTED}]Consider query optimization or check if cache is warmed up[/{StyleTokens.MUTED}]"
            )

        console.print()
        console.print(Rule(style=StyleTokens.MUTED))

    return capture.get()

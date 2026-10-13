"""
EXPLAIN ANALYZE Execution for RDST Analyze

Executes EXPLAIN ANALYZE queries against target databases to gather
actual execution plans and performance metrics for analysis.
"""

import contextvars
import json
import os
import re
import time
import sys
import signal
import select
import threading
import uuid
try:
    import termios
    import tty
except ImportError:
    # Windows: termios/tty not available, interactive skip won't work
    termios = None
    tty = None
from typing import Dict, Any, Optional, List
from contextlib import contextmanager
from sqlglot import exp, parse

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

try:
    import pymysql
    import pymysql.cursors
except ImportError:
    pymysql = None

# Import shared cancellation utilities
from shared.db_connection import (
    cancel_postgres_by_pid,
    cancel_mysql_by_thread_id,
    create_mysql_connection_from_params,
    postgres_connection_kwargs,
    rdst_self_marker,
    resolve_connection_params,
)
from shared.query_registry.observation_store import record_execution_evidence
from shared import analysis_lane


_WORKER_SETTLE_TIMEOUT_SECONDS = 1.0

# EXPLAIN wrappers reference the user's tables and land in statement stores
# as their own texts; the marker keeps them out of registry admission.
_SELF_MARKER = rdst_self_marker("rdst/analyze")


def _mysql_analyze_sql(sql: str, version: str, timeout_ms: int | None) -> str | None:
    """Only a single read-only query (a SELECT, with or without CTEs, or a set operation)
    can be executed by MySQL Deep Analyze. A statement that starts with SELECT carries the
    timeout as a MAX_EXECUTION_TIME hint; for the others the caller's wait loop enforces it
    with KILL QUERY."""
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    if ("mariadb" in version.lower() or not match
            or tuple(map(int, match.groups())) < (8, 0, 18)):
        return None
    try:
        statements = [statement for statement in parse(sql, read="mysql") if statement]
    except Exception:
        return None
    if (len(statements) != 1
            or not isinstance(statements[0], (exp.Select, exp.Union, exp.Except, exp.Intersect))
            or any(isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Into, exp.Lock))
                   for node in statements[0].walk())
            or re.search(r"\bLOCK\s+IN\s+SHARE\s+MODE\b|\bFOR\s+(?:UPDATE|SHARE)\b",
                         sql, re.IGNORECASE)):
        return None
    if timeout_ms is None or not re.match(r"^\s*SELECT\b", sql, re.IGNORECASE):
        return sql
    # The hint is attached to the executed SELECT, not to EXPLAIN or a session.
    return re.sub(r"^(\s*SELECT\b)",
                  rf"\1 /*+ MAX_EXECUTION_TIME({timeout_ms}) */", sql,
                  count=1, flags=re.IGNORECASE)


def _mysql_restore_table_case(cursor, monitor_run, sql: str) -> str:
    """Digest-derived text can spell a keyword-named table (events) in upper case; match it
    to the schema's tables. Best effort: the SQL is unchanged when the catalog read fails."""
    from features.query_registry.statement_stats import MYSQL_TABLE_NAMES_SQL, mysql_table_case

    try:
        analysis_lane.execute(monitor_run, lambda: cursor.execute(MYSQL_TABLE_NAMES_SQL))
        tables = [next(iter(row.values())) if isinstance(row, dict) else row[0]
                  for row in cursor.fetchall()]
    except Exception:
        return sql
    return mysql_table_case(sql, tables)


@contextmanager
def _monitored_connection(target, conn, engine, config):
    run = analysis_lane.register(target, conn, engine, config)
    try:
        yield run
    finally:
        analysis_lane.unregister(run, conn)


class _AnalyzeWorkerState:
    """Thread-safe lifecycle for one background EXPLAIN ANALYZE execution."""

    def __init__(self, identifier_key: str):
        self.identifier_key = identifier_key
        self.lock = threading.Lock()
        self.cancel_requested = threading.Event()
        self.result = {
            'completed': False,
            'started': False,
            'started_at': None,
            'ended_at': None,
            'data': None,
            'error': None,
            identifier_key: None,
        }

    def set_identifier(self, identifier: int) -> None:
        with self.lock:
            self.result[self.identifier_key] = identifier

    def identifier(self) -> Optional[int]:
        with self.lock:
            return self.result[self.identifier_key]

    def request_cancel(self) -> None:
        self.cancel_requested.set()

    def begin_execution(self) -> bool:
        """Open the execution interval unless cancellation won the race."""
        with self.lock:
            if self.cancel_requested.is_set():
                return False
            self.result['started'] = True
            self.result['started_at'] = float(time.time())
            return True

    def mark_database_completion(self) -> None:
        """Capture the database statement's completion, before result decoding."""
        with self.lock:
            if self.result['started'] and self.result['ended_at'] is None:
                self.result['ended_at'] = float(time.time())

    def finish(self, *, data: Any = None, error: Optional[str] = None) -> None:
        with self.lock:
            if self.result['started'] and self.result['ended_at'] is None:
                self.result['ended_at'] = float(time.time())
            if data is not None:
                self.result['data'] = data
            if error is not None:
                self.result['error'] = error
            self.result['completed'] = True

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return dict(self.result)


def _settle_analyze_worker(
    worker: threading.Thread,
    timeout: float = _WORKER_SETTLE_TIMEOUT_SECONDS,
) -> bool:
    """Wait briefly for cancellation without allowing an unbounded response."""
    worker.join(timeout=max(0.0, timeout))
    return not worker.is_alive()


def _close_open_analyze_evidence(
    worker: threading.Thread,
    state: _AnalyzeWorkerState,
    *,
    target: Optional[str],
    sql: str,
    run_id: str,
    started_at: float,
) -> None:
    """Close an open evidence row only after its worker has definitely exited."""
    worker.join()
    snapshot = state.snapshot()
    ended_at = snapshot.get('ended_at')
    if ended_at is None:
        ended_at = float(time.time())
    record_execution_evidence(
        target,
        [{"sql": sql, "exec_count": None}],
        lane="rdst/analyze",
        run_id=run_id,
        started_at=started_at,
        ended_at=float(ended_at),
    )


def _normalize_plan_data(raw: Any) -> Dict[str, Any]:
    """Ensure plan data is a parsed dict, handling string JSON from psycopg2."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list) and len(raw) > 0 and isinstance(raw[0], dict):
        return raw[0]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and len(parsed) > 0:
                return parsed[0]
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def _resolve_explain_password(
    target_config: Dict[str, Any], resolved_password: str
) -> str:
    """Resolve passwords for EXPLAIN paths, keeping the legacy RDST_DB_PASSWORD fallback.
"""
    if resolved_password:
        return resolved_password

    # An explicitly empty password means passwordless auth, not "try legacy env".
    if target_config.get('password') == '':
        return ''

    password_env = (target_config.get('password_env') or '').strip()
    if password_env:
        return ''

    return os.getenv('RDST_DB_PASSWORD', '')


def execute_explain_analyze(sql: str, target: str = None, **kwargs) -> Dict[str, Any]:
    """
    Execute EXPLAIN ANALYZE on a SQL query to gather execution metrics.

    Args:
        sql: The SQL query to analyze
        target: Target database configuration name
        **kwargs: Additional workflow parameters including:
            - target_config: Database connection configuration
            - fast_mode: If True, auto-skip after 10 seconds
            - rewrite_max_time_ms: Max time for rewrite testing (auto-cancel if exceeded)

    Returns:
        Dict containing:
        - success: boolean indicating if execution succeeded
        - explain_plan: execution plan data
        - execution_time_ms: actual execution time
        - rows_examined: number of rows examined
        - rows_returned: number of rows returned
        - cost_estimate: query cost estimate
        - error: error message if failed
    """
    try:
        # Get target configuration from workflow context
        target_config = kwargs.get('target_config')

        # Handle case where WorkflowManager serialized the config to a string
        if isinstance(target_config, str):
            target_config = None

        if not target_config and target:
            # Load target config if not provided in workflow context
            from shared.config.targets import TargetsConfig
            cfg = TargetsConfig()
            cfg.load()
            target_config = cfg.get(target)

        if not target_config:
            return {
                "success": False,
                "error": f"Target configuration not found: {target}",
                "explain_plan": None,
                "execution_time_ms": 0
            }

        # Determine database engine
        engine = target_config.get('engine', '').lower()

        # Extract fast_mode from kwargs
        fast_mode = kwargs.get('fast_mode', False)
        # Handle case where WorkflowManager passes "False" or "True" as strings
        if isinstance(fast_mode, str):
            fast_mode = fast_mode.lower() in ['true', '1', 'yes']
        non_interactive = kwargs.get('non_interactive', False) in (True, 'True', 'true', '1')
        statement_timeout_ms = kwargs.get('statement_timeout_ms')
        try:
            statement_timeout_ms = max(1000, min(30000, int(statement_timeout_ms)))
        except (ValueError, TypeError):
            statement_timeout_ms = None

        # Extract rewrite_max_time_ms for rewrite testing timeout
        rewrite_max_time_ms = kwargs.get('rewrite_max_time_ms')
        if isinstance(rewrite_max_time_ms, str):
            try:
                rewrite_max_time_ms = float(rewrite_max_time_ms)
            except (ValueError, TypeError):
                rewrite_max_time_ms = None

        if engine in ['postgresql', 'postgres']:
            runner = _execute_postgres_explain_analyze
        elif engine in ['mysql', 'mariadb']:
            runner = _execute_mysql_explain_analyze
        else:
            return {
                "success": False,
                "error": f"Unsupported database engine: {engine}",
                "explain_plan": None,
                "execution_time_ms": 0
            }

        result = runner(
            sql,
            target_config,
            fast_mode=fast_mode,
            rewrite_max_time_ms=rewrite_max_time_ms,
            target=target,
            non_interactive=non_interactive,
            statement_timeout_ms=statement_timeout_ms,
        )

        # These private values exist only when a cancelled worker could not
        # settle within the bounded response deadline. They must never leak
        # into the workflow result (the thread/state objects are not serializable).
        open_worker = result.pop('_analyze_worker_thread', None)
        open_state = result.pop('_analyze_worker_state', None)

        # Attribution evidence (research Q11) counts executions of the user's
        # statement, not the surrounding EXPLAIN statements. Plain EXPLAIN
        # therefore contributes zero; successful EXPLAIN ANALYZE contributes
        # exactly one. A cancelled, timed-out, or failed ANALYZE is unknown
        # only when its database execution may actually have started.
        if result.get("success") and result.get("plan_format") == "analyze":
            # A completed one-off execution belongs to exactly one adjacent
            # observation window, so represent it as a point at completion.
            completed_at = result.get("explain_analyze_ended_at")
            if completed_at is None:
                completed_at = float(time.time())
            completed_at = float(completed_at)
            record_execution_evidence(
                target,
                [{"sql": sql, "exec_count": 1}],
                lane="rdst/analyze",
                run_id=uuid.uuid4().hex,
                started_at=completed_at,
                ended_at=completed_at,
            )
        elif result.get("explain_analyze_started"):
            # A cancelled/failed execution is not safe to subtract. Preserve
            # its real execution span and keep it open if cancellation could
            # not settle promptly; the finalizer closes it after worker exit.
            started_at = result.get("explain_analyze_started_at")
            if started_at is None:
                started_at = float(time.time())
            started_at = float(started_at)
            ended_at = result.get("explain_analyze_ended_at")
            if ended_at is not None:
                ended_at = float(ended_at)
            elif open_worker is None or open_state is None:
                # Compatibility for injected/custom runners that do not
                # expose worker state: there is no later worker to close it.
                ended_at = float(time.time())
            run_id = uuid.uuid4().hex
            record_execution_evidence(
                target,
                [{"sql": sql, "exec_count": None}],
                lane="rdst/analyze",
                run_id=run_id,
                started_at=started_at,
                ended_at=ended_at,
            )
            if ended_at is None and open_worker is not None and open_state is not None:
                try:
                    threading.Thread(
                        target=_close_open_analyze_evidence,
                        kwargs={
                            "worker": open_worker,
                            "state": open_state,
                            "target": target,
                            "sql": sql,
                            "run_id": run_id,
                            "started_at": started_at,
                        },
                        name="rdst-analyze-evidence-finalizer",
                        daemon=True,
                    ).start()
                except Exception:
                    # Evidence is best-effort and remains safely open-ended;
                    # bookkeeping must never turn a valid analysis into failure.
                    pass
        return result

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to execute EXPLAIN ANALYZE: {str(e)}",
            "explain_plan": None,
            "execution_time_ms": 0
        }


def _execute_postgres_explain_analyze(
    sql: str,
    target_config: Dict[str, Any],
    fast_mode: bool = False,
    rewrite_max_time_ms: float = None,
    target: str = None,
    non_interactive: bool = False,
    statement_timeout_ms: int | None = None,
) -> Dict[str, Any]:
    """Execute EXPLAIN ANALYZE for PostgreSQL with timeout handling.

    Behavior:
    - fast_mode=True: SKIP EXPLAIN ANALYZE entirely, only run EXPLAIN (instant)
    - rewrite_max_time_ms set: Run with timeout, cancel if exceeds baseline time
    - Normal mode: Run EXPLAIN ANALYZE, offer interactive skip after 10s
    """
    if psycopg2 is None:
        return {
            "success": False,
            "error": "psycopg2 not available for PostgreSQL connections",
            "explain_plan": None,
            "execution_time_ms": 0
        }

    # Check if SQL contains parameter placeholders
    import re
    import threading
    has_pg_params = re.search(r'\$\d+', sql) is not None
    has_question_mark = '?' in sql
    # A plan-only run can keep `$N` placeholders: PostgreSQL 16+ plans them generically.
    if (has_pg_params or has_question_mark) and not (fast_mode and has_pg_params):
        placeholder_type = "$1, $2" if has_pg_params else "?"
        return {
            "success": False,
            "error": (
                "WARNING: Cannot get reliable EXPLAIN ANALYZE for parameterized queries.\n\n"
                f"The query contains '{placeholder_type}' placeholders (from query normalization).\n"
                "PostgreSQL EXPLAIN ANALYZE with placeholders uses generic plan estimates\n"
                "which can differ SIGNIFICANTLY from execution with actual values.\n\n"
                "Solution: Copy the query from your application code and run:\n"
                "   rdst analyze \"SELECT ... WHERE col = <actual_value>\"\n\n"
                "Note: rdst top is designed for monitoring query traffic (like htop).\n"
                "      For detailed analysis, use rdst analyze with the original query."
            ),
            "explain_plan": None,
            "execution_time_ms": 0,
            "skipped_reason": "parameterized_query_from_digest"
        }

    try:
        resolved_params = resolve_connection_params(
            target=target,
            target_config=target_config,
            lane="rdst/analyze",
        )
        password = _resolve_explain_password(
            target_config, resolved_params['password']
        )

        conn_params = postgres_connection_kwargs(
            resolved_params,
            password=password,
        )

        # FAST MODE: Skip EXPLAIN ANALYZE entirely, only run EXPLAIN
        if fast_mode:
            try:
                with _postgres_connection(conn_params) as conn, _monitored_connection(
                    target, conn, 'postgresql', target_config) as monitor_run:
                    with conn.cursor() as cursor:
                        if statement_timeout_ms:
                            analysis_lane.execute(monitor_run, lambda: cursor.execute(
                                "SET LOCAL statement_timeout = %s", (statement_timeout_ms,)))
                        # PostgreSQL 16+ can plan without choosing concrete
                        # parameter values. This is the appropriate plan for
                        # unattended plain-EXPLAIN fallback work.
                        generic = "GENERIC_PLAN true, " if getattr(conn, "server_version", 0) >= 160000 else ""
                        if has_pg_params and not generic:
                            return {
                                "success": False,
                                "error": ("No real values were available for this query's "
                                          "parameters, and PostgreSQL before 16 can't plan it "
                                          "without them."),
                                "explain_plan": None,
                                "execution_time_ms": 0,
                                "skipped_reason": "parameterized_query_from_digest",
                            }
                        explain_query = f"{_SELF_MARKER}EXPLAIN ({generic}VERBOSE true, COSTS true, FORMAT JSON) {sql}"
                        start_time = time.perf_counter()
                        analysis_lane.execute(monitor_run, lambda: cursor.execute(explain_query))  # nosem
                        end_time = time.perf_counter()

                        result = cursor.fetchone()
                        explain_plan_time_ms = (end_time - start_time) * 1000

                        if result and result[0]:
                            explain_plan_data = result[0][0] if isinstance(result[0], list) else result[0]
                            return {
                                "success": True,
                                "explain_plan": explain_plan_data,
                                "execution_time_ms": explain_plan_time_ms,
                                "rows_examined": _extract_postgres_rows_examined(explain_plan_data),
                                "rows_returned": _extract_postgres_rows_returned(explain_plan_data),
                                "cost_estimate": _extract_postgres_cost(explain_plan_data),
                                "database_engine": "postgresql",
                                "plan_format": "json",
                                "explain_analyze_skipped": True,
                                "skip_reason": "fast_mode - EXPLAIN ANALYZE skipped",
                                "fallback_reason": "Using EXPLAIN plan only (fast mode)"
                            }
                        else:
                            return {
                                "success": False,
                                "error": "No EXPLAIN plan returned",
                                "explain_plan": None,
                                "execution_time_ms": 0
                            }
            except Exception as e:
                return {
                    "success": False,
                    "error": f"PostgreSQL EXPLAIN failed: {str(e)}",
                    "explain_plan": None,
                    "execution_time_ms": 0
                }

        # NORMAL MODE: Run EXPLAIN first (for fallback), then EXPLAIN ANALYZE with timeout
        explain_plan_data = None
        explain_plan_time_ms = 0

        try:
            with _postgres_connection(conn_params) as conn, _monitored_connection(
                target, conn, 'postgresql', target_config) as monitor_run:
                with conn.cursor() as cursor:
                    if statement_timeout_ms:
                        analysis_lane.execute(monitor_run, lambda: cursor.execute(
                            "SET LOCAL statement_timeout = %s", (statement_timeout_ms,)))
                    explain_query = f"{_SELF_MARKER}EXPLAIN (VERBOSE true, COSTS true, FORMAT JSON) {sql}"
                    start_time = time.perf_counter()
                    analysis_lane.execute(monitor_run, lambda: cursor.execute(explain_query))  # nosem
                    end_time = time.perf_counter()

                    result = cursor.fetchone()
                    explain_plan_time_ms = (end_time - start_time) * 1000

                    if result and result[0]:
                        raw = result[0][0] if isinstance(result[0], list) else result[0]
                        explain_plan_data = _normalize_plan_data(raw)
        except Exception as explain_error:
            return {
                "success": False,
                "error": f"PostgreSQL EXPLAIN failed: {str(explain_error)}",
                "explain_plan": None,
                "execution_time_ms": 0
            }

        # Now run EXPLAIN ANALYZE in background thread with timeout handling
        worker_state = _AnalyzeWorkerState('backend_pid')
        query_result = worker_state.result
        query_lock = worker_state.lock
        active_connection = [None]

        def execute_explain_analyze():
            query_conn = None
            monitor_run = None
            data = None
            error = None
            try:
                query_conn = psycopg2.connect(**conn_params)
                active_connection[0] = query_conn
                monitor_run = analysis_lane.register(target, query_conn, 'postgresql', target_config)
                with query_conn.cursor() as query_cursor:
                    if statement_timeout_ms:
                        analysis_lane.execute(monitor_run, lambda: query_cursor.execute(
                            "SET LOCAL statement_timeout = %s", (statement_timeout_ms,)))
                    analysis_lane.execute(monitor_run, lambda: query_cursor.execute("SELECT pg_backend_pid()"))
                    backend_pid = query_cursor.fetchone()[0]
                    worker_state.set_identifier(backend_pid)

                    explain_analyze_query = f"{_SELF_MARKER}EXPLAIN (ANALYZE true, VERBOSE true, COSTS true, BUFFERS true, FORMAT JSON) {sql}"
                    if not worker_state.begin_execution():
                        return
                    analysis_lane.execute(monitor_run, lambda: query_cursor.execute(explain_analyze_query))  # nosem
                    worker_state.mark_database_completion()
                    result = query_cursor.fetchone()

                    if result and result[0]:
                        raw = result[0][0] if isinstance(result[0], list) else result[0]
                        data = _normalize_plan_data(raw)
            except Exception as e:
                error = str(e)
            finally:
                analysis_lane.unregister(monitor_run, query_conn)
                active_connection[0] = None
                # Publish the terminal execution state before connection
                # cleanup, which may itself block in a broken driver/socket.
                worker_state.finish(data=data, error=error)
                if query_conn:
                    try:
                        query_conn.close()
                    except:
                        pass

        start_time = time.perf_counter()
        analyze_thread = threading.Thread(target=execute_explain_analyze, daemon=True)
        analyze_thread.start()

        user_skipped = False
        rewrite_timeout_exceeded = False
        max_wait_timeout = False
        ctrl_c_pressed = False
        prompt_shown = False
        max_wait_time = 600  # 10 minutes total

        # Helper to cancel the backend query using shared utility
        def cancel_backend_query():
            worker_state.request_cancel()
            if active_connection[0] is not None:
                try:
                    active_connection[0].cancel()
                    return True
                except Exception:
                    pass
            backend_pid = worker_state.identifier()
            if backend_pid:
                return cancel_postgres_by_pid(conn_params, backend_pid, verbose=True)
            else:
                print(f"   >> No backend PID available to cancel", flush=True)
                return False

        # SIGINT handler for Ctrl+C cancellation (only works in main thread)
        original_sigint_handler = None
        sigint_supported = False
        def sigint_handler(signum, frame):
            nonlocal ctrl_c_pressed, user_skipped
            ctrl_c_pressed = True
            user_skipped = True
            print(f"\n>> Ctrl+C pressed - cancelling query...\n")
            sys.stdout.flush()
            cancel_backend_query()

        # TTY for interactive skip
        tty_fd = None
        old_settings = None
        if not non_interactive and not rewrite_max_time_ms:
            try:
                tty_fd = open('/dev/tty', 'r')
                old_settings = termios.tcgetattr(tty_fd)
                tty.setcbreak(tty_fd)
            except:
                pass

        try:
            # Install SIGINT handler (only works in main thread)
            if not non_interactive:
                try:
                    original_sigint_handler = signal.getsignal(signal.SIGINT)
                    signal.signal(signal.SIGINT, sigint_handler)
                    sigint_supported = True
                except ValueError:
                    pass

            while time.perf_counter() - start_time < max_wait_time:
                # Check if Ctrl+C was pressed
                if ctrl_c_pressed:
                    break

                with query_lock:
                    if query_result['completed']:
                        break

                elapsed = time.perf_counter() - start_time
                elapsed_ms = elapsed * 1000

                # Check rewrite timeout (for rewrite testing - cancel if slower than baseline)
                if rewrite_max_time_ms and elapsed_ms >= rewrite_max_time_ms:
                    rewrite_timeout_exceeded = True
                    user_skipped = True
                    cancel_backend_query()
                    break

                # Interactive mode: show prompt after 10 seconds
                if elapsed >= 10 and not prompt_shown and not rewrite_max_time_ms and not non_interactive:
                    prompt_shown = True
                    # Always show the prompt (Ctrl+C works even without TTY)
                    print(f"\n** EXPLAIN ANALYZE has been running for 10 seconds...", flush=True)
                    if tty_fd:
                        print(f"   Press ENTER to skip and continue with EXPLAIN plan only", flush=True)
                    print(f"   Press Ctrl+C to cancel and exit", flush=True)
                    print(f"   Or wait for full analysis (max 10 minutes)", flush=True)

                # Check for user input
                if prompt_shown and tty_fd and not rewrite_max_time_ms:
                    try:
                        if select.select([tty_fd], [], [], 0.1)[0]:
                            user_input = tty_fd.read(1)
                            if user_input in ['\n', '\r', '1']:
                                user_skipped = True
                                print(f"\n>> Skipping EXPLAIN ANALYZE - using EXPLAIN plan only\n")
                                sys.stdout.flush()
                                cancel_backend_query()
                                break
                    except (OSError, ValueError):
                        tty_fd.close()
                        tty_fd = None

                time.sleep(0.1)
            else:
                # Loop completed due to max_wait_time timeout (not break)
                # Cancel the still-running backend query
                with query_lock:
                    should_cancel = not query_result['completed']
                if should_cancel:
                    max_wait_timeout = True
                    user_skipped = True
                    if not non_interactive:
                        print(f"\n>> Max wait time ({max_wait_time}s) exceeded - cancelling query...\n")
                        sys.stdout.flush()
                    cancel_backend_query()
        finally:
            # Restore original SIGINT handler if we installed one
            if sigint_supported and original_sigint_handler is not None:
                try:
                    signal.signal(signal.SIGINT, original_sigint_handler)
                except ValueError:
                    pass  # Not in main thread

            if old_settings and tty_fd:
                try:
                    termios.tcsetattr(tty_fd, termios.TCSADRAIN, old_settings)
                except:
                    pass
            if tty_fd:
                try:
                    tty_fd.close()
                except:
                    pass

        # A cancel can race a worker that has connected but not entered the
        # user's statement. The pre-start gate stops that case; a worker that
        # already entered is given a bounded settle window. If cancellation
        # fails, the caller records an open interval and closes it only after
        # this worker eventually exits.
        if user_skipped or rewrite_timeout_exceeded or max_wait_timeout or ctrl_c_pressed:
            worker_state.request_cancel()
        worker_settled = _settle_analyze_worker(analyze_thread)
        query_snapshot = worker_state.snapshot()

        end_time = time.perf_counter()
        execution_time_ms = (end_time - start_time) * 1000

        execution_evidence = {
            "explain_analyze_started": bool(query_snapshot['started']),
            "explain_analyze_started_at": query_snapshot['started_at'],
            "explain_analyze_ended_at": query_snapshot['ended_at'],
        }
        if (
            not worker_settled
            and query_snapshot['started']
            and query_snapshot['ended_at'] is None
        ):
            execution_evidence.update(
                {
                    "_analyze_worker_thread": analyze_thread,
                    "_analyze_worker_state": worker_state,
                }
            )

        # Check results
        if query_snapshot['data'] and not user_skipped:
            plan_data = query_snapshot['data']
            return {
                "success": True,
                "explain_plan": plan_data,
                "execution_time_ms": execution_time_ms,
                "rows_examined": _extract_postgres_rows_examined(plan_data),
                "rows_returned": _extract_postgres_rows_returned(plan_data),
                "cost_estimate": _extract_postgres_cost(plan_data),
                "actual_time_ms": _extract_postgres_actual_time(plan_data),
                "planning_time_ms": plan_data.get('Planning Time', 0),
                "execution_time_ms": plan_data.get('Execution Time', execution_time_ms),
                "database_engine": "postgresql",
                "plan_format": "analyze",
                "explain_only_plan": explain_plan_data,
                **execution_evidence,
            }
        elif explain_plan_data:
            elapsed_seconds = execution_time_ms / 1000
            if elapsed_seconds >= 60:
                elapsed_str = f"{int(elapsed_seconds // 60)} min {int(elapsed_seconds % 60)} sec"
            else:
                elapsed_str = f"{elapsed_seconds:.1f} sec"

            if rewrite_timeout_exceeded:
                skip_reason = f"Rewrite slower than baseline (cancelled after {elapsed_str})"
            elif ctrl_c_pressed:
                skip_reason = f"Cancelled by user (Ctrl+C) after {elapsed_str}"
            elif max_wait_timeout:
                skip_reason = f"Max wait time exceeded (cancelled after {elapsed_str})"
            elif user_skipped:
                skip_reason = f"User skipped after {elapsed_str}"
            else:
                skip_reason = f"EXPLAIN ANALYZE timed out after {elapsed_str}: {query_result.get('error', 'timeout')}"

            return {
                "success": True,
                "explain_plan": explain_plan_data,
                "execution_time_ms": explain_plan_time_ms,
                "actual_elapsed_time_ms": execution_time_ms,
                "rows_examined": _extract_postgres_rows_examined(explain_plan_data),
                "rows_returned": _extract_postgres_rows_returned(explain_plan_data),
                "cost_estimate": _extract_postgres_cost(explain_plan_data),
                "database_engine": "postgresql",
                "plan_format": "json",
                "explain_analyze_timeout": not user_skipped,
                "explain_analyze_skipped": user_skipped,
                "rewrite_timeout_exceeded": rewrite_timeout_exceeded,
                "fallback_reason": f"Using EXPLAIN plan only - {skip_reason}",
                "skip_reason": skip_reason,
                **execution_evidence,
            }
        else:
            return {
                "success": False,
                "error": f"No EXPLAIN plan available. ANALYZE error: {query_result.get('error', 'unknown')}",
                "explain_plan": None,
                "execution_time_ms": 0,
                **execution_evidence,
            }

    except Exception as e:
        import traceback
        error_details = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        return {
            "success": False,
            "error": f"PostgreSQL EXPLAIN ANALYZE failed: {error_details}",
            "explain_plan": None,
            "execution_time_ms": 0
        }


def _execute_mysql_explain_analyze(
    sql: str,
    target_config: Dict[str, Any],
    fast_mode: bool = False,
    rewrite_max_time_ms: float = None,
    target: str = None,
    non_interactive: bool = False,
    statement_timeout_ms: int | None = None,
) -> Dict[str, Any]:
    """Execute EXPLAIN ANALYZE for MySQL.

    Behavior:
    - fast_mode=True: SKIP EXPLAIN ANALYZE entirely, only run EXPLAIN (instant)
    - rewrite_max_time_ms set: Run with timeout, cancel if exceeds baseline time
    - Normal mode: Run EXPLAIN ANALYZE, offer interactive skip after 10s
    """
    if pymysql is None:
        return {
            "success": False,
            "error": "pymysql not available for MySQL connections",
            "explain_plan": None,
            "execution_time_ms": 0
        }

    # Check if SQL contains parameter placeholders - MySQL doesn't support these in EXPLAIN
    if '?' in sql:
        return {
            "success": False,
            "error": (
                "WARNING: Cannot run EXPLAIN on queries from MySQL digest table.\n\n"
                "The query contains '?' placeholders (from performance_schema digest normalization).\n"
                "MySQL EXPLAIN requires actual literal values.\n\n"
                "💡 Solution: Copy the query from your application code and run:\n"
                "   rdst analyze \"SELECT ... WHERE col = <actual_value>\"\n\n"
                "Note: rdst top is designed for monitoring query traffic (like htop).\n"
                "      For detailed analysis, use rdst analyze with the original query."
            ),
            "explain_plan": None,
            "execution_time_ms": 0,
            "skipped_reason": "parameterized_query_from_digest"
        }

    try:
        resolved_params = resolve_connection_params(
            target=target,
            target_config=target_config,
            lane="rdst/analyze",
        )
        password = _resolve_explain_password(
            target_config, resolved_params['password']
        )

        mysql_params = dict(resolved_params)
        mysql_params['password'] = password
        conn_overrides = {
            'charset': 'utf8mb4',
            'cursorclass': pymysql.cursors.DictCursor
        }

        with _mysql_connection(mysql_params, conn_overrides) as conn, _monitored_connection(
            target, conn, 'mysql', target_config) as monitor_run:
            with conn.cursor() as cursor:
                sql = _mysql_restore_table_case(cursor, monitor_run, sql)
                # TWO-PHASE APPROACH for slow queries:
                # Phase 1: Always run EXPLAIN (instant, no execution) to get query plan
                # Phase 2: Try EXPLAIN ANALYZE (executes query, may timeout)
                # If Phase 2 times out, we still have Phase 1 data for LLM analysis

                # PHASE 1: Get instant query plan with EXPLAIN FORMAT=JSON
                explain_plan_data = None
                try:
                    explain_query = f"{_SELF_MARKER}EXPLAIN FORMAT=JSON {sql}"
                    start_time = time.perf_counter()
                    # Safe: sql parameter is user\'s query to analyze (intended functionality), validated by query_safety.py

                    analysis_lane.execute(monitor_run, lambda: cursor.execute(explain_query))  # nosem
                    end_time = time.perf_counter()

                    result = cursor.fetchone()
                    plan_time_ms = (end_time - start_time) * 1000

                    if result and 'EXPLAIN' in result:
                        explain_plan_data = result['EXPLAIN']
                except Exception as explain_error:
                    # If even EXPLAIN fails, we can't proceed
                    return {
                        "success": False,
                        "error": f"MySQL EXPLAIN failed: {str(explain_error)}",
                        "explain_plan": None,
                        "execution_time_ms": 0
                    }

                analyze_sql = None
                if not fast_mode:
                    analysis_lane.execute(monitor_run, lambda: cursor.execute("SELECT VERSION()"))
                    version_row = cursor.fetchone()
                    version = str(next(iter(version_row.values())) if isinstance(version_row, dict)
                                  else version_row[0])
                    analyze_sql = _mysql_analyze_sql(sql, version, statement_timeout_ms)
                if fast_mode or analyze_sql is None:
                    return {
                        "success": True, "explain_plan": explain_plan_data,
                        "execution_time_ms": plan_time_ms,
                        "rows_examined": _extract_mysql_json_rows_examined(explain_plan_data),
                        "rows_returned": _extract_mysql_json_rows_returned(explain_plan_data),
                        "cost_estimate": _extract_mysql_json_cost(explain_plan_data),
                        "database_engine": "mysql", "plan_format": "json",
                        "explain_analyze_skipped": True,
                    }

                # PHASE 2: Try EXPLAIN ANALYZE with interactive skip option
                # After 10 seconds, user can press ENTER to skip and continue with EXPLAIN only
                import threading
                import select
                import sys

                # Get connection ID for query cancellation (from current connection)
                cursor.execute("SELECT CONNECTION_ID()")
                connection_id = cursor.fetchone()['CONNECTION_ID()']

                # Shared state between threads
                worker_state = _AnalyzeWorkerState('connection_id')
                query_result = worker_state.result
                query_lock = worker_state.lock

                def execute_explain_analyze():
                    """Execute EXPLAIN ANALYZE in separate thread with its own connection."""
                    query_conn = None
                    monitor_run = None
                    data = None
                    error = None
                    try:
                        # Create new connection for this thread (cursors aren't thread-safe)
                        query_conn = create_mysql_connection_from_params(
                            mysql_params,
                            **conn_overrides,
                        )
                        monitor_run = analysis_lane.register(target, query_conn, 'mysql', target_config)
                        with query_conn.cursor() as query_cursor:
                            # Get this thread's connection ID
                            analysis_lane.execute(monitor_run, lambda: query_cursor.execute("SELECT CONNECTION_ID()"))
                            thread_conn_id = query_cursor.fetchone()['CONNECTION_ID()']
                            worker_state.set_identifier(thread_conn_id)

                            # Execute EXPLAIN ANALYZE
                            explain_query = f"{_SELF_MARKER}EXPLAIN ANALYZE {analyze_sql}"
                            # Safe: sql parameter is user\'s query to analyze (intended functionality), validated by query_safety.py

                            if not worker_state.begin_execution():
                                return
                            analysis_lane.execute(monitor_run, lambda: query_cursor.execute(explain_query))  # nosem
                            worker_state.mark_database_completion()
                            results = query_cursor.fetchall()
                            data = results
                    except Exception as e:
                        error = str(e)
                    finally:
                        analysis_lane.unregister(monitor_run, query_conn)
                        # Publish completion before potentially blocking
                        # connection cleanup; no SQL can run after this point.
                        worker_state.finish(data=data, error=error)
                        if query_conn:
                            try:
                                query_conn.close()
                            except:
                                pass

                # Start EXPLAIN ANALYZE in background thread
                start_time = time.perf_counter()
                # The worker's connection is recorded into the caller's agent action.
                analyze_thread = threading.Thread(
                    target=contextvars.copy_context().run, args=(execute_explain_analyze,),
                    daemon=True)
                analyze_thread.start()

                # Monitor for 10 seconds, then offer skip option
                user_skipped = False
                max_wait_timeout = False
                rewrite_timeout_exceeded = False
                ctrl_c_pressed = False
                prompt_shown = False
                max_wait_time = 600  # 10 minutes total timeout
                if statement_timeout_ms:
                    # A CTE or set operation can't carry the hint; the wait loop's
                    # KILL QUERY bounds it (a hinted SELECT stops on its own first).
                    max_wait_time = min(max_wait_time, statement_timeout_ms / 1000 + 1)

                # Helper to kill the MySQL query using shared utility
                def kill_mysql_query():
                    worker_state.request_cancel()
                    thread_conn_id = worker_state.identifier()
                    if thread_conn_id:
                        return cancel_mysql_by_thread_id(
                            target_config,
                            thread_conn_id,
                            verbose=True,
                            target=target,
                        )
                    else:
                        print(f"   >> No MySQL connection ID available to kill", flush=True)
                        return False

                # SIGINT handler for Ctrl+C cancellation (only works in main thread)
                original_sigint_handler = None
                sigint_supported = False
                def sigint_handler(signum, frame):
                    nonlocal ctrl_c_pressed, user_skipped
                    ctrl_c_pressed = True
                    user_skipped = True
                    print(f"\n>> Ctrl+C pressed - cancelling query...\n")
                    sys.stdout.flush()
                    kill_mysql_query()

                # Try to open /dev/tty for interactive input (works even if stdin is redirected)
                tty_fd = None
                old_settings = None
                if not non_interactive:
                    try:
                        tty_fd = open('/dev/tty', 'r')
                        old_settings = termios.tcgetattr(tty_fd)
                        tty.setcbreak(tty_fd)
                    except:
                        pass

                try:
                    # Install SIGINT handler (only works in main thread)
                    if not non_interactive:
                        try:
                            original_sigint_handler = signal.getsignal(signal.SIGINT)
                            signal.signal(signal.SIGINT, sigint_handler)
                            sigint_supported = True
                        except ValueError:
                            pass

                    while time.perf_counter() - start_time < max_wait_time:
                        # Check if Ctrl+C was pressed
                        if ctrl_c_pressed:
                            break

                        with query_lock:
                            if query_result['completed']:
                                break

                        elapsed = time.perf_counter() - start_time

                        # Rewrite testing: cancel once the rewrite is slower than the baseline.
                        if rewrite_max_time_ms and elapsed * 1000 >= rewrite_max_time_ms:
                            rewrite_timeout_exceeded = True
                            user_skipped = True
                            kill_mysql_query()
                            break

                        # After 10 seconds: auto-skip if fast_mode, or show prompt
                        if (elapsed >= 10 and not prompt_shown and not rewrite_max_time_ms
                                and not non_interactive):
                            prompt_shown = True

                            if fast_mode:
                                # Auto-skip in fast mode (for testing)
                                user_skipped = True
                                print(f"\n>> Auto-skipping EXPLAIN ANALYZE after 10 seconds (fast mode)\n")
                                sys.stdout.flush()
                                kill_mysql_query()
                                break  # Exit the waiting loop
                            else:
                                # Always show the prompt (Ctrl+C works even without TTY)
                                print(f"\n** EXPLAIN ANALYZE has been running for 10 seconds...", flush=True)
                                if tty_fd:
                                    print(f"   Press ENTER to skip and continue with EXPLAIN plan only", flush=True)
                                print(f"   Press Ctrl+C to cancel and exit", flush=True)
                                print(f"   Or wait for full analysis (max 10 minutes)", flush=True)

                        # Check for user input from /dev/tty (non-blocking) - only if not fast_mode
                        if prompt_shown and tty_fd and not fast_mode:
                            try:
                                if select.select([tty_fd], [], [], 0.1)[0]:
                                    user_input = tty_fd.read(1)
                                    # Check for Enter key (\n or \r) or '1' for backwards compatibility
                                    if user_input in ['\n', '\r', '1']:
                                        user_skipped = True
                                        print(f"\n>> Skipping EXPLAIN ANALYZE - using EXPLAIN plan only\n")
                                        sys.stdout.flush()
                                        kill_mysql_query()
                                        break
                            except (OSError, ValueError):
                                # select() failed, disable interactive prompt
                                tty_fd.close()
                                tty_fd = None

                        time.sleep(0.1)
                    else:
                        # Loop completed due to max_wait_time timeout (not break)
                        # Kill the still-running query
                        with query_lock:
                            should_cancel = not query_result['completed']
                        if should_cancel:
                            max_wait_timeout = True
                            user_skipped = True
                            if not non_interactive:
                                print(f"\n>> Max wait time ({max_wait_time}s) exceeded - cancelling query...\n")
                                sys.stdout.flush()
                            kill_mysql_query()
                finally:
                    # Restore original SIGINT handler if we installed one
                    if sigint_supported and original_sigint_handler is not None:
                        try:
                            signal.signal(signal.SIGINT, original_sigint_handler)
                        except ValueError:
                            pass  # Not in main thread

                    # Restore terminal settings and clean up TTY file descriptor
                    if old_settings and tty_fd:
                        try:
                            termios.tcsetattr(tty_fd, termios.TCSADRAIN, old_settings)
                        except:
                            pass
                    if tty_fd:
                        try:
                            tty_fd.close()
                        except:
                            pass

                if user_skipped or rewrite_timeout_exceeded or max_wait_timeout or ctrl_c_pressed:
                    worker_state.request_cancel()
                worker_settled = _settle_analyze_worker(analyze_thread)
                query_snapshot = worker_state.snapshot()

                end_time = time.perf_counter()
                execution_time_ms = (end_time - start_time) * 1000

                execution_evidence = {
                    "explain_analyze_started": bool(query_snapshot['started']),
                    "explain_analyze_started_at": query_snapshot['started_at'],
                    "explain_analyze_ended_at": query_snapshot['ended_at'],
                }
                if (
                    not worker_settled
                    and query_snapshot['started']
                    and query_snapshot['ended_at'] is None
                ):
                    execution_evidence.update(
                        {
                            "_analyze_worker_thread": analyze_thread,
                            "_analyze_worker_state": worker_state,
                        }
                    )

                # Check results
                if query_snapshot['data'] and not user_skipped:
                    # EXPLAIN ANALYZE completed successfully
                    return {
                        "success": True,
                        "explain_plan": query_snapshot['data'],
                        "execution_time_ms": execution_time_ms,
                        "rows_examined": _extract_mysql_rows_examined(query_snapshot['data']),
                        "rows_returned": _extract_mysql_rows_returned(query_snapshot['data']),
                        "cost_estimate": _extract_mysql_cost(query_snapshot['data']),
                        "database_engine": "mysql",
                        "plan_format": "analyze",
                        "explain_only_plan": explain_plan_data,
                        **execution_evidence,
                    }
                elif explain_plan_data:
                    # Use EXPLAIN data (user skipped or timeout/error)
                    # Format the actual elapsed time nicely
                    elapsed_seconds = execution_time_ms / 1000
                    if elapsed_seconds >= 60:
                        elapsed_str = f"{int(elapsed_seconds // 60)} min {int(elapsed_seconds % 60)} sec"
                    else:
                        elapsed_str = f"{int(elapsed_seconds)} sec"

                    if rewrite_timeout_exceeded:
                        skip_reason = f"Rewrite slower than baseline (cancelled after {elapsed_str})"
                    elif ctrl_c_pressed:
                        skip_reason = f"Cancelled by user (Ctrl+C) after {elapsed_str}"
                    elif max_wait_timeout:
                        skip_reason = f"Max wait time exceeded (cancelled after {elapsed_str})"
                    elif user_skipped:
                        skip_reason = f"User skipped after {elapsed_str}"
                    else:
                        skip_reason = f"EXPLAIN ANALYZE timed out after {elapsed_str}: {query_result.get('error', 'timeout')}"

                    return {
                        "success": True,
                        "explain_plan": explain_plan_data,
                        "execution_time_ms": plan_time_ms,  # This is the EXPLAIN time, not actual query time
                        "actual_elapsed_time_ms": execution_time_ms,  # This is how long we actually waited
                        "rows_examined": _extract_mysql_json_rows_examined(explain_plan_data),
                        "rows_returned": _extract_mysql_json_rows_returned(explain_plan_data),
                        "cost_estimate": _extract_mysql_json_cost(explain_plan_data),
                        "database_engine": "mysql",
                        "plan_format": "json",
                        "explain_analyze_timeout": not user_skipped,
                        "explain_analyze_skipped": user_skipped,
                        "rewrite_timeout_exceeded": rewrite_timeout_exceeded,
                        "fallback_reason": f"Using EXPLAIN plan only - {skip_reason}",
                        "skip_reason": skip_reason,
                        **execution_evidence,
                    }
                else:
                    return {
                        "success": False,
                        "error": f"No EXPLAIN plan available. ANALYZE error: {query_result.get('error', 'unknown')}",
                        "explain_plan": None,
                        "execution_time_ms": 0,
                        **execution_evidence,
                    }

    except Exception as e:
        return {
            "success": False,
            "error": f"MySQL EXPLAIN ANALYZE failed: {str(e)}",
            "explain_plan": None,
            "execution_time_ms": 0
        }


@contextmanager
def _postgres_connection(conn_params):
    """Context manager for PostgreSQL connections."""
    conn = None
    with analysis_lane.connection_budget():
        try:
            conn = psycopg2.connect(**conn_params)
            yield conn
        finally:
            if conn:
                conn.close()


@contextmanager
def _mysql_connection(params, overrides=None):
    """Context manager for MySQL connections."""
    conn = None
    with analysis_lane.connection_budget():
        try:
            conn = create_mysql_connection_from_params(params, **(overrides or {}))
            yield conn
        finally:
            if conn:
                conn.close()


# PostgreSQL plan analysis helpers
def _get_plan_row_count(node: Dict[str, Any]) -> int:
    """Get row count from a plan node, preferring Actual Rows over Plan Rows."""
    rows = node.get('Actual Rows')
    return rows if rows is not None else node.get('Plan Rows', 0)


def _extract_postgres_rows_examined(plan_data: Dict[str, Any]) -> int:
    """Extract total rows examined from PostgreSQL EXPLAIN plan.

    Uses 'Actual Rows' from EXPLAIN ANALYZE output, falling back to
    'Plan Rows' (estimated) when ANALYZE data isn't available. A node's
    output excludes the rows its filter read and discarded, so those count too.
    """
    def extract_from_node(node):
        total = _get_plan_row_count(node) + (node.get('Rows Removed by Filter') or 0)
        for child in node.get('Plans', []):
            total += extract_from_node(child)
        return total

    if isinstance(plan_data, dict) and 'Plan' in plan_data:
        return extract_from_node(plan_data['Plan'])
    return 0


def _extract_postgres_rows_returned(plan_data: Dict[str, Any]) -> int:
    """Extract rows returned from PostgreSQL EXPLAIN plan.

    Uses 'Actual Rows' from the top-level Plan node, falling back to
    'Plan Rows' (estimated) when ANALYZE data isn't available.
    """
    if isinstance(plan_data, dict) and 'Plan' in plan_data:
        return _get_plan_row_count(plan_data['Plan'])
    return 0


def _extract_postgres_cost(plan_data: Dict[str, Any]) -> float:
    """Extract total cost from PostgreSQL EXPLAIN plan."""
    if isinstance(plan_data, dict) and 'Plan' in plan_data:
        try:
            return float(plan_data['Plan'].get('Total Cost', 0.0))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _extract_postgres_actual_time(plan_data: Dict[str, Any]) -> float:
    """Extract actual execution time from PostgreSQL EXPLAIN plan."""
    if isinstance(plan_data, dict) and 'Plan' in plan_data:
        actual_time = plan_data['Plan'].get('Actual Total Time', 0.0)
        return actual_time
    return 0.0


# MySQL plan analysis helpers
def _extract_mysql_rows_examined(results: List[Dict[str, Any]]) -> int:
    """Extract rows examined from MySQL EXPLAIN ANALYZE results."""
    import re

    total_examined = 0
    for row in results:
        if isinstance(row, dict) and 'EXPLAIN' in row:
            explain_text = row['EXPLAIN']

            # Parse MySQL EXPLAIN ANALYZE text format
            # Look for patterns like "rows=396793 loops=1" in table scans and operations
            # This represents actual rows examined during execution
            row_patterns = re.findall(r'rows=([0-9,]+)\s+loops=([0-9,]+)', explain_text)

            for rows_str, loops_str in row_patterns:
                try:
                    rows = int(rows_str.replace(',', ''))
                    loops = int(loops_str.replace(',', ''))
                    total_examined += rows * loops
                except ValueError:
                    continue

    return total_examined


def _extract_mysql_rows_returned(results: List[Dict[str, Any]]) -> int:
    """Extract rows returned from MySQL EXPLAIN ANALYZE results."""
    import re

    for row in results:
        if isinstance(row, dict) and 'EXPLAIN' in row:
            explain_text = row['EXPLAIN']

            # Look for the final result rows from Limit or top-level operation
            # Pattern like "-> Limit: 10 row(s)  (cost=2.51 rows=0.05) (actual time=2158..2158 rows=10 loops=1)"
            # We want the "actual time" section rows, not the cost section rows
            limit_match = re.search(r'-> Limit:.*?\(actual time=[^)]*rows=([0-9,]+)', explain_text)
            if limit_match:
                try:
                    return int(limit_match.group(1).replace(',', ''))
                except ValueError:
                    pass

            # If no limit, the outermost operation's actual rows. Its "(cost=... rows=N)" is the
            # optimizer's estimate (10% of the table for an unindexed equality filter), which
            # would present a selective filter as an unselective one.
            first_line = explain_text.split('\n')[0]
            row_match = (re.search(r'\(actual time=[^)]*rows=([0-9,]+)', first_line)
                         or re.search(r'rows=([0-9,]+)', first_line))
            if row_match:
                try:
                    return int(row_match.group(1).replace(',', ''))
                except ValueError:
                    pass

    return 0


def _extract_mysql_cost(results: List[Dict[str, Any]]) -> float:
    """Extract cost estimate from MySQL EXPLAIN ANALYZE results."""
    import re

    total_cost = 0.0
    for row in results:
        if isinstance(row, dict) and 'EXPLAIN' in row:
            explain_text = row['EXPLAIN']

            # Parse cost estimates from MySQL EXPLAIN ANALYZE
            # Look for patterns like "(cost=2.6 rows=1)"
            cost_patterns = re.findall(r'\(cost=([0-9.]+)', explain_text)

            for cost_str in cost_patterns:
                try:
                    total_cost += float(cost_str)
                except ValueError:
                    continue

    return total_cost


# MySQL JSON format helpers (for EXPLAIN FORMAT=JSON fallback)
def _extract_mysql_json_rows_examined(plan_data: Dict[str, Any]) -> int:
    """Extract rows examined from MySQL JSON EXPLAIN plan."""
    def extract_from_node(node):
        total = 0
        if isinstance(node, dict):
            total += node.get('rows_examined_per_scan', 0) * node.get('rows_produced_per_join', 1)
            # Recursively check nested tables
            for key, value in node.items():
                if key in ['nested_loop', 'table', 'materialized_from_subquery'] and isinstance(value, dict):
                    total += extract_from_node(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            total += extract_from_node(item)
        return total

    if isinstance(plan_data, dict) and 'query_block' in plan_data:
        return extract_from_node(plan_data['query_block'])
    return 0


def _extract_mysql_json_rows_returned(plan_data: Dict[str, Any]) -> int:
    """Extract rows returned from MySQL JSON EXPLAIN plan."""
    if isinstance(plan_data, dict) and 'query_block' in plan_data:
        qb = plan_data['query_block']
        return qb.get('rows_produced_per_join', 0)
    return 0


def _extract_mysql_json_cost(plan_data: Dict[str, Any]) -> float:
    """Extract cost from MySQL JSON EXPLAIN plan."""
    def extract_cost_from_node(node):
        total_cost = 0.0
        if isinstance(node, dict):
            total_cost += node.get('read_cost', 0.0)
            total_cost += node.get('eval_cost', 0.0)
            total_cost += node.get('sort_cost', 0.0)

            # Recursively check nested operations
            for key, value in node.items():
                if isinstance(value, dict) and key != 'cost_info':
                    total_cost += extract_cost_from_node(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            total_cost += extract_cost_from_node(item)
        return total_cost

    if isinstance(plan_data, dict) and 'query_block' in plan_data:
        return extract_cost_from_node(plan_data['query_block'])
    return 0.0

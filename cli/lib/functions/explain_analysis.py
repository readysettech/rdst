"""
EXPLAIN ANALYZE Execution for RDST Analyze

Executes EXPLAIN ANALYZE queries against target databases to gather
actual execution plans and performance metrics for analysis.
"""

import time
import sys
import select
import termios
import tty
from typing import Dict, Any, Optional, List
from contextlib import contextmanager

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
            from ..cli.rdst_cli import TargetsConfig
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

        # Extract rewrite_max_time_ms for rewrite testing timeout
        rewrite_max_time_ms = kwargs.get('rewrite_max_time_ms')
        if isinstance(rewrite_max_time_ms, str):
            try:
                rewrite_max_time_ms = float(rewrite_max_time_ms)
            except (ValueError, TypeError):
                rewrite_max_time_ms = None

        if engine in ['postgresql', 'postgres']:
            return _execute_postgres_explain_analyze(sql, target_config, fast_mode=fast_mode, rewrite_max_time_ms=rewrite_max_time_ms)
        elif engine in ['mysql', 'mariadb']:
            return _execute_mysql_explain_analyze(sql, target_config, fast_mode=fast_mode, rewrite_max_time_ms=rewrite_max_time_ms)
        else:
            return {
                "success": False,
                "error": f"Unsupported database engine: {engine}",
                "explain_plan": None,
                "execution_time_ms": 0
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to execute EXPLAIN ANALYZE: {str(e)}",
            "explain_plan": None,
            "execution_time_ms": 0
        }


def _execute_postgres_explain_analyze(sql: str, target_config: Dict[str, Any], fast_mode: bool = False, rewrite_max_time_ms: float = None) -> Dict[str, Any]:
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
    if has_pg_params or has_question_mark:
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
        import os

        password = target_config.get('password')
        if not password:
            password_env = target_config.get('password_env', 'RDST_DB_PASSWORD')
            password = os.getenv(password_env, '')

        conn_params = {
            'host': target_config['host'],
            'port': target_config.get('port', 5432),
            'database': target_config['database'],
            'user': target_config['user'],
            'password': password,
        }

        if target_config.get('tls', False):
            conn_params['sslmode'] = 'require'

        # FAST MODE: Skip EXPLAIN ANALYZE entirely, only run EXPLAIN
        if fast_mode:
            try:
                with _postgres_connection(conn_params) as conn:
                    with conn.cursor() as cursor:
                        explain_query = f"EXPLAIN (VERBOSE true, COSTS true, FORMAT JSON) {sql}"
                        start_time = time.perf_counter()
                        cursor.execute(explain_query)  # nosem
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
            with _postgres_connection(conn_params) as conn:
                with conn.cursor() as cursor:
                    explain_query = f"EXPLAIN (VERBOSE true, COSTS true, FORMAT JSON) {sql}"
                    start_time = time.perf_counter()
                    cursor.execute(explain_query)  # nosem
                    end_time = time.perf_counter()

                    result = cursor.fetchone()
                    explain_plan_time_ms = (end_time - start_time) * 1000

                    if result and result[0]:
                        explain_plan_data = result[0][0] if isinstance(result[0], list) else result[0]
        except Exception as explain_error:
            return {
                "success": False,
                "error": f"PostgreSQL EXPLAIN failed: {str(explain_error)}",
                "explain_plan": None,
                "execution_time_ms": 0
            }

        # Now run EXPLAIN ANALYZE in background thread with timeout handling
        query_result = {'completed': False, 'data': None, 'error': None, 'backend_pid': None}
        query_lock = threading.Lock()

        def execute_explain_analyze():
            query_conn = None
            try:
                query_conn = psycopg2.connect(**conn_params)
                with query_conn.cursor() as query_cursor:
                    query_cursor.execute("SELECT pg_backend_pid()")
                    backend_pid = query_cursor.fetchone()[0]

                    with query_lock:
                        query_result['backend_pid'] = backend_pid

                    explain_analyze_query = f"EXPLAIN (ANALYZE true, VERBOSE true, COSTS true, BUFFERS true, FORMAT JSON) {sql}"
                    query_cursor.execute(explain_analyze_query)  # nosem
                    result = query_cursor.fetchone()

                    with query_lock:
                        query_result['completed'] = True
                        if result and result[0]:
                            query_result['data'] = result[0][0] if isinstance(result[0], list) else result[0]
            except Exception as e:
                with query_lock:
                    query_result['completed'] = True
                    query_result['error'] = str(e)
            finally:
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
        prompt_shown = False
        max_wait_time = 600  # 10 minutes total

        # TTY for interactive skip
        tty_fd = None
        old_settings = None
        if not rewrite_max_time_ms:  # Only try TTY if not in rewrite testing mode
            try:
                tty_fd = open('/dev/tty', 'r')
                old_settings = termios.tcgetattr(tty_fd)
                tty.setcbreak(tty_fd)
            except:
                pass

        try:
            while time.perf_counter() - start_time < max_wait_time:
                with query_lock:
                    if query_result['completed']:
                        break

                elapsed = time.perf_counter() - start_time
                elapsed_ms = elapsed * 1000

                # Check rewrite timeout (for rewrite testing - cancel if slower than baseline)
                if rewrite_max_time_ms and elapsed_ms >= rewrite_max_time_ms:
                    rewrite_timeout_exceeded = True
                    user_skipped = True

                    try:
                        with query_lock:
                            backend_pid = query_result.get('backend_pid')
                        if backend_pid:
                            cancel_conn = psycopg2.connect(**conn_params)
                            with cancel_conn.cursor() as cancel_cursor:
                                cancel_cursor.execute(f"SELECT pg_cancel_backend({backend_pid})")  # nosem
                            cancel_conn.close()
                    except Exception:
                        pass  # Cancellation is best-effort
                    break

                # Interactive mode: show prompt after 10 seconds
                if elapsed >= 10 and not prompt_shown and not rewrite_max_time_ms:
                    prompt_shown = True
                    if tty_fd:
                        print(f"\n** EXPLAIN ANALYZE has been running for 10 seconds...")
                        print(f"   Press ENTER to skip and continue with EXPLAIN plan only")
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

                                try:
                                    with query_lock:
                                        backend_pid = query_result.get('backend_pid')
                                    if backend_pid:
                                        cancel_conn = psycopg2.connect(**conn_params)
                                        with cancel_conn.cursor() as cancel_cursor:
                                            cancel_cursor.execute(f"SELECT pg_cancel_backend({backend_pid})")  # nosem
                                        cancel_conn.close()
                                except:
                                    pass
                                break
                    except (OSError, ValueError):
                        tty_fd.close()
                        tty_fd = None

                time.sleep(0.1)
        finally:
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

        end_time = time.perf_counter()
        execution_time_ms = (end_time - start_time) * 1000

        # Check results
        with query_lock:
            if query_result['data'] and not user_skipped:
                plan_data = query_result['data']
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
                    "explain_only_plan": explain_plan_data
                }
            elif explain_plan_data:
                elapsed_seconds = execution_time_ms / 1000
                if elapsed_seconds >= 60:
                    elapsed_str = f"{int(elapsed_seconds // 60)} min {int(elapsed_seconds % 60)} sec"
                else:
                    elapsed_str = f"{elapsed_seconds:.1f} sec"

                if rewrite_timeout_exceeded:
                    skip_reason = f"Rewrite slower than baseline (cancelled after {elapsed_str})"
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
                    "skip_reason": skip_reason
                }
            else:
                return {
                    "success": False,
                    "error": f"No EXPLAIN plan available. ANALYZE error: {query_result.get('error', 'unknown')}",
                    "explain_plan": None,
                    "execution_time_ms": 0
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


def _execute_mysql_explain_analyze(sql: str, target_config: Dict[str, Any], fast_mode: bool = False, rewrite_max_time_ms: float = None) -> Dict[str, Any]:
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
        import os

        # Get password - either directly from config or from environment variable
        password = target_config.get('password')
        if not password:
            password_env = target_config.get('password_env', 'RDST_DB_PASSWORD')
            password = os.getenv(password_env, '')

        conn_params = {
            'host': target_config['host'],
            'port': target_config.get('port', 3306),
            'database': target_config['database'],
            'user': target_config['user'],
            'password': password,
            'charset': 'utf8mb4',
            'cursorclass': pymysql.cursors.DictCursor
        }

        # Add SSL configuration if specified
        if target_config.get('tls', False):
            conn_params['ssl'] = {'ssl_disabled': False}

        with _mysql_connection(conn_params) as conn:
            with conn.cursor() as cursor:
                # TWO-PHASE APPROACH for slow queries:
                # Phase 1: Always run EXPLAIN (instant, no execution) to get query plan
                # Phase 2: Try EXPLAIN ANALYZE (executes query, may timeout)
                # If Phase 2 times out, we still have Phase 1 data for LLM analysis

                # PHASE 1: Get instant query plan with EXPLAIN FORMAT=JSON
                explain_plan_data = None
                try:
                    explain_query = f"EXPLAIN FORMAT=JSON {sql}"
                    start_time = time.perf_counter()
                    # Safe: sql parameter is user\'s query to analyze (intended functionality), validated by query_safety.py

                    cursor.execute(explain_query)  # nosem
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

                # PHASE 2: Try EXPLAIN ANALYZE with interactive skip option
                # After 30 seconds, user can press 1 to skip and continue with EXPLAIN only
                import threading
                import select
                import sys

                # Get connection ID for query cancellation (from current connection)
                cursor.execute("SELECT CONNECTION_ID()")
                connection_id = cursor.fetchone()['CONNECTION_ID()']

                # Shared state between threads
                query_result = {'completed': False, 'data': None, 'error': None, 'connection_id': None}
                query_lock = threading.Lock()

                def execute_explain_analyze():
                    """Execute EXPLAIN ANALYZE in separate thread with its own connection."""
                    query_conn = None
                    try:
                        # Create new connection for this thread (cursors aren't thread-safe)
                        query_conn = pymysql.connect(**conn_params)
                        with query_conn.cursor() as query_cursor:
                            # Get this thread's connection ID
                            query_cursor.execute("SELECT CONNECTION_ID()")
                            thread_conn_id = query_cursor.fetchone()['CONNECTION_ID()']

                            with query_lock:
                                query_result['connection_id'] = thread_conn_id

                            # Execute EXPLAIN ANALYZE
                            explain_query = f"EXPLAIN ANALYZE {sql}"
                            # Safe: sql parameter is user\'s query to analyze (intended functionality), validated by query_safety.py

                            query_cursor.execute(explain_query)  # nosem
                            results = query_cursor.fetchall()

                            with query_lock:
                                query_result['completed'] = True
                                query_result['data'] = results
                    except Exception as e:
                        with query_lock:
                            query_result['completed'] = True
                            query_result['error'] = str(e)
                    finally:
                        if query_conn:
                            try:
                                query_conn.close()
                            except:
                                pass

                # Start EXPLAIN ANALYZE in background thread
                start_time = time.perf_counter()
                analyze_thread = threading.Thread(target=execute_explain_analyze, daemon=True)
                analyze_thread.start()

                # Monitor for 10 seconds, then offer skip option
                user_skipped = False
                prompt_shown = False
                max_wait_time = 600  # 10 minutes total timeout

                # Try to open /dev/tty for interactive input (works even if stdin is redirected)
                tty_fd = None
                old_settings = None
                try:
                    tty_fd = open('/dev/tty', 'r')
                    # Put terminal in raw mode so we can read single characters without Enter
                    old_settings = termios.tcgetattr(tty_fd)
                    tty.setcbreak(tty_fd)
                except:
                    pass  # No TTY available, will just timeout normally

                try:
                    while time.perf_counter() - start_time < max_wait_time:
                        with query_lock:
                            if query_result['completed']:
                                break

                        elapsed = time.perf_counter() - start_time

                        # After 10 seconds: auto-skip if fast_mode, or show prompt
                        if elapsed >= 10 and not prompt_shown:
                            prompt_shown = True

                            if fast_mode:
                                # Auto-skip in fast mode (for testing)
                                user_skipped = True
                                print(f"\n>> Auto-skipping EXPLAIN ANALYZE after 10 seconds (fast mode)\n")
                                sys.stdout.flush()
    
                                # Kill the running query using its connection ID
                                try:
                                    with query_lock:
                                        thread_conn_id = query_result.get('connection_id')
    
                                    if thread_conn_id:
                                        kill_conn = pymysql.connect(**conn_params)
                                        with kill_conn.cursor() as kill_cursor:
                                            # Safe: thread_conn_id is numeric ID from CONNECTION_ID(), not user input

                                            kill_cursor.execute(f"KILL QUERY {thread_conn_id}")  # nosem
                                        kill_conn.close()
                                except Exception as e_kill:
                                    pass  # Query may have already completed
    
                                break  # Exit the waiting loop
                            elif tty_fd:
                                # Show interactive prompt when TTY available
                                print(f"\n** EXPLAIN ANALYZE has been running for 10 seconds...")
                                print(f"   Press ENTER to skip and continue with EXPLAIN plan only")
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
    
                                        # Kill the running query using its connection ID
                                        try:
                                            with query_lock:
                                                thread_conn_id = query_result.get('connection_id')
    
                                            if thread_conn_id:
                                                kill_conn = pymysql.connect(**conn_params)
                                                with kill_conn.cursor() as kill_cursor:
                                                    # Safe: thread_conn_id is numeric ID from CONNECTION_ID(), not user input

                                                    kill_cursor.execute(f"KILL QUERY {thread_conn_id}")  # nosem
                                                kill_conn.close()
                                        except:
                                            pass  # Ignore errors during kill
    
                                        break
                            except (OSError, ValueError):
                                # select() failed, disable interactive prompt
                                tty_fd.close()
                                tty_fd = None
    
                            time.sleep(0.1)
                finally:
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

                end_time = time.perf_counter()
                execution_time_ms = (end_time - start_time) * 1000

                # Check results
                with query_lock:
                    if query_result['data'] and not user_skipped:
                        # EXPLAIN ANALYZE completed successfully
                        return {
                            "success": True,
                            "explain_plan": query_result['data'],
                            "execution_time_ms": execution_time_ms,
                            "rows_examined": _extract_mysql_rows_examined(query_result['data']),
                            "rows_returned": _extract_mysql_rows_returned(query_result['data']),
                            "cost_estimate": _extract_mysql_cost(query_result['data']),
                            "database_engine": "mysql",
                            "plan_format": "analyze",
                            "explain_only_plan": explain_plan_data
                        }
                    elif explain_plan_data:
                        # Use EXPLAIN data (user skipped or timeout/error)
                        # Format the actual elapsed time nicely
                        elapsed_seconds = execution_time_ms / 1000
                        if elapsed_seconds >= 60:
                            elapsed_str = f"{int(elapsed_seconds // 60)} min {int(elapsed_seconds % 60)} sec"
                        else:
                            elapsed_str = f"{int(elapsed_seconds)} sec"

                        skip_reason = f"User skipped after {elapsed_str}" if user_skipped else \
                                     f"EXPLAIN ANALYZE timed out after {elapsed_str}: {query_result.get('error', 'timeout')}"

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
                            "fallback_reason": f"Using EXPLAIN plan only - {skip_reason}",
                            "skip_reason": skip_reason
                        }
                    else:
                        return {
                            "success": False,
                            "error": f"No EXPLAIN plan available. ANALYZE error: {query_result.get('error', 'unknown')}",
                            "explain_plan": None,
                            "execution_time_ms": 0
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
    try:
        conn = psycopg2.connect(**conn_params)
        yield conn
    finally:
        if conn:
            conn.close()


@contextmanager
def _mysql_connection(conn_params):
    """Context manager for MySQL connections."""
    conn = None
    try:
        conn = pymysql.connect(**conn_params)
        yield conn
    finally:
        if conn:
            conn.close()


# PostgreSQL plan analysis helpers
def _extract_postgres_rows_examined(plan_data: Dict[str, Any]) -> int:
    """Extract total rows examined from PostgreSQL EXPLAIN plan."""
    def extract_from_node(node):
        total = node.get('Actual Rows', 0)
        for child in node.get('Plans', []):
            total += extract_from_node(child)
        return total

    if isinstance(plan_data, dict) and 'Plan' in plan_data:
        return extract_from_node(plan_data['Plan'])
    return 0


def _extract_postgres_rows_returned(plan_data: Dict[str, Any]) -> int:
    """Extract rows returned from PostgreSQL EXPLAIN plan."""
    if isinstance(plan_data, dict) and 'Plan' in plan_data:
        return plan_data['Plan'].get('Actual Rows', 0)
    return 0


def _extract_postgres_cost(plan_data: Dict[str, Any]) -> float:
    """Extract total cost from PostgreSQL EXPLAIN plan."""
    if isinstance(plan_data, dict) and 'Plan' in plan_data:
        return plan_data['Plan'].get('Total Cost', 0.0)
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

            # If no limit, look for the outermost operation's row count
            first_line = explain_text.split('\n')[0]
            row_match = re.search(r'rows=([0-9,]+)', first_line)
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
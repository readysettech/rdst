"""
Phase 5: SQL Execution

Executes validated SQL against the database.
Handles timeouts and execution errors.
"""

from __future__ import annotations

import logging
import math
import re
import time
import uuid
from typing import TYPE_CHECKING, Dict, Any, List

if TYPE_CHECKING:
    from ..context import Ask3Context
    from ..presenter import Ask3Presenter

from shared.query_registry.observation_store import record_execution_evidence

from ..types import ExecutionResult, DbType, StatementCancel

logger = logging.getLogger(__name__)

# Most rows an Ask answer returns while result-limit enforcement is on, whatever form the
# query's own limit takes (FETCH ... WITH TIES returns every row tied at its boundary).
MAX_RESULT_ROWS = 1000


def execute_query(
    ctx: 'Ask3Context',
    presenter: 'Ask3Presenter',
    db_executor=None
) -> 'Ask3Context':
    """
    Execute SQL against the database.

    Args:
        ctx: Ask3Context with validated sql
        presenter: For progress output
        db_executor: Optional custom executor (for testing)

    Returns:
        Updated context with execution_result populated
    """
    ctx.phase = 'execute'
    presenter.executing_query()

    if not ctx.sql:
        return _refuse(ctx, presenter, "There is no SQL to run.")

    if not ctx.target_config:
        return _refuse(ctx, presenter, "The target has no connection configuration.")

    # The validate phase covers the linear flow only: agent escalation and direct
    # agent mode hand their own SQL straight to execution. Validate here so every
    # path is gated at the point of no return.
    from features.ask.sql_validation import describe_validation_issues, validate_sql_for_ask

    db_type = ctx.db_type or ctx.target_config.get('engine', 'postgresql').lower()
    validation = validate_sql_for_ask(
        sql=ctx.sql,
        max_limit=MAX_RESULT_ROWS,
        default_limit=ctx.max_rows,
        enforce_result_limit=ctx.enforce_result_limit,
        dialect=db_type,
    )
    if not validation.get('is_valid'):
        return _refuse(ctx, presenter, describe_validation_issues(validation.get('issues')))

    if validation.get('limit_added') or validation.get('limit_reduced'):
        ctx.limit_added = bool(validation.get('limit_added'))
        ctx.limit_reduced = bool(validation.get('limit_reduced'))
    elif ctx.sql != getattr(ctx, "limit_sql", None):
        # The flags describe the cap validation put on another statement (the agent,
        # escalation and repairs replace the SQL), so none of them applies to this one.
        ctx.limit_added = ctx.limit_reduced = False
    ctx.sql = validation.get('validated_sql', ctx.sql)
    ctx.limit_sql = ctx.sql
    row_limit = validation.get('limit_value')
    capped = (ctx.limit_added or ctx.limit_reduced) and isinstance(row_limit, int)
    ctx.row_limit = row_limit
    ctx.rows_capped = False
    fetch_limit = MAX_RESULT_ROWS + 1 if ctx.enforce_result_limit else None
    # One row past an Ask-imposed cap tells a cut result from one that fits exactly.
    run_sql = (_with_trailing_limit(ctx.sql, row_limit) if capped else None) or ctx.sql
    probed = run_sql != ctx.sql

    start_time = time.time()

    try:
        # Use injected executor if provided (for testing)
        if db_executor:
            result = db_executor(run_sql, ctx.target_config)
        else:
            if db_type == DbType.POSTGRESQL or 'postgres' in db_type:
                result = _execute_postgres(
                    run_sql, ctx.target_config, ctx.timeout_seconds, ctx.target,
                    statement_cancel=getattr(ctx, "statement_cancel", None),
                    max_rows=fetch_limit,
                )
            elif db_type == DbType.MYSQL or 'mysql' in db_type:
                result = _execute_mysql(
                    run_sql, ctx.target_config, ctx.timeout_seconds, ctx.target,
                    statement_cancel=getattr(ctx, "statement_cancel", None),
                    max_rows=fetch_limit,
                )
            else:
                return _refuse(ctx, presenter, f"Ask can't run queries on {db_type} databases.")

        completed_at = float(time.time())
        execution_time_ms = (completed_at - start_time) * 1000

        if not result.get('success'):
            error = result.get('error', 'Unknown execution error')
            ctx.execution_result = ExecutionResult(
                error=error,
                error_kind=result.get('error_kind'),
                execution_time_ms=execution_time_ms
            )
            presenter.execution_error(error)
            return ctx

        # Build execution result
        columns = result.get('columns', [])
        rows = result.get('rows', [])
        if not capped:
            # Nothing Ask imposed cut the result; a full page still reads as possibly
            # partial to the post-execution repairs.
            truncated = len(rows) >= ctx.max_rows
        elif probed:
            truncated = len(rows) > row_limit
            rows = rows[:row_limit]
        else:
            truncated = len(rows) >= row_limit
        if fetch_limit is not None and len(rows) > MAX_RESULT_ROWS:
            # A limit form Ask doesn't rewrite (FETCH ... WITH TIES) returned more rows
            # than an answer may hold; Ask's cap cuts it.
            rows = rows[:MAX_RESULT_ROWS]
            truncated = True
            ctx.row_limit = MAX_RESULT_ROWS
            ctx.rows_capped = True
        row_count = len(rows)

        ctx.execution_result = ExecutionResult(
            columns=columns,
            rows=rows,
            row_count=row_count,
            execution_time_ms=execution_time_ms,
            truncated=truncated
        )

        ctx.mark_success()

        # Attribution evidence (research Q11): a completed one-off execution
        # belongs to exactly one adjacent observation window, so record it as
        # a point at the actual executor completion time.
        record_execution_evidence(
            ctx.target,
            [{"sql": ctx.sql, "exec_count": 1}],
            lane="rdst/ask",
            run_id=uuid.uuid4().hex,
            started_at=completed_at,
            ended_at=completed_at,
        )

    except Exception as e:
        execution_time_ms = (time.time() - start_time) * 1000
        logger.error(f"Query execution failed: {e}")

        ctx.execution_result = ExecutionResult(
            error=str(e),
            error_kind=type(e).__name__,
            execution_time_ms=execution_time_ms
        )
        presenter.execution_error(str(e))

    return ctx


def _refuse(ctx: 'Ask3Context', presenter: 'Ask3Presenter', message: str) -> 'Ask3Context':
    """Record a statement Ask refused to run as a failed execution, never a stale result."""
    ctx.mark_error(message)
    ctx.execution_result = ExecutionResult(error=message, error_kind="refused")
    presenter.error(message)
    return ctx


_TRAILING_LIMIT = re.compile(r"\bLIMIT\s+(\d+)(\s*;?\s*)$", re.IGNORECASE)


def _with_trailing_limit(sql: str, limit: Any) -> str | None:
    """The SQL with its trailing ``LIMIT n`` raised to n + 1, or None when it has none."""
    match = _TRAILING_LIMIT.search(sql)
    if not isinstance(limit, int) or match is None or int(match.group(1)) != limit:
        return None
    return f"{sql[:match.start()]}LIMIT {limit + 1}{match.group(2)}"


def _cancelled_result() -> Dict[str, Any]:
    return {
        'success': False,
        'error': 'Query cancelled.',
        'error_kind': 'cancelled',
        'rows': [],
        'columns': [],
    }


def _execute_postgres(
    sql: str,
    config: Dict[str, Any],
    timeout_seconds: int,
    target: str = None,
    *,
    statement_cancel: StatementCancel | None = None,
    max_rows: int | None = None,
) -> Dict[str, Any]:
    """Execute query against PostgreSQL."""
    try:
        import psycopg2
        from shared.db_connection import (
            create_mysql_connection_from_params,
            postgres_connection_kwargs,
            resolve_connection_params,
        )

        params = resolve_connection_params(target=target, target_config=config, lane="rdst/ask")

        if not all([params['host'], params['user'], params['database']]):
            return {
                'success': False,
                'error': 'Missing connection parameters (host, user, or database)',
                'rows': [],
                'columns': []
            }

        # Connect
        conn = psycopg2.connect(**postgres_connection_kwargs(
            params,
            connect_timeout=max(1, math.ceil(timeout_seconds)) if timeout_seconds > 0 else 10,
        ))
        if statement_cancel is not None and not statement_cancel.register(
            conn, DbType.POSTGRESQL, target, config
        ):
            conn.close()
            return _cancelled_result()

        try:
            with conn.cursor() as cursor:
                # Set query timeout
                if timeout_seconds > 0:
                    timeout_ms = timeout_seconds * 1000
                    cursor.execute(f"SET statement_timeout = '{timeout_ms}ms'")

                if statement_cancel is not None and statement_cancel.cancelled:
                    return _cancelled_result()
                # Execute query
                cursor.execute(sql)

                # Get results
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                rows = cursor.fetchall() if max_rows is None else cursor.fetchmany(max_rows)
                rows_list = [list(row) for row in rows]

                return {
                    'success': True,
                    'rows': rows_list,
                    'columns': columns,
                    'error': None
                }
        finally:
            if statement_cancel is not None:
                statement_cancel.unregister(conn)
            conn.close()

    except ImportError:
        return {
            'success': False,
            'error': 'psycopg2 not installed',
            'error_kind': 'local_dependency',
            'rows': [],
            'columns': []
        }
    except Exception as e:
        message = str(e)
        if statement_cancel is not None and statement_cancel.cancelled:
            return _cancelled_result()
        if getattr(e, "pgcode", None) == "57014" or "statement timeout" in message.lower():
            error_kind = "query_timeout"
            message = f"Query exceeded the {timeout_seconds}-second execution limit."
        elif isinstance(e, psycopg2.OperationalError):
            error_kind = "connection"
        else:
            error_kind = "query_execution"
        return {
            'success': False,
            'error': message,
            'error_kind': error_kind,
            'rows': [],
            'columns': []
        }


def _execute_mysql(
    sql: str,
    config: Dict[str, Any],
    timeout_seconds: int,
    target: str = None,
    *,
    statement_cancel: StatementCancel | None = None,
    max_rows: int | None = None,
) -> Dict[str, Any]:
    """Execute query against MySQL."""
    try:
        import pymysql
        from shared.db_connection import (
            create_mysql_connection_from_params,
            resolve_connection_params,
        )

        params = resolve_connection_params(target=target, target_config=config, lane="rdst/ask")

        if not all([params['host'], params['user'], params['database']]):
            return {
                'success': False,
                'error': 'Missing connection parameters (host, user, or database)',
                'rows': [],
                'columns': []
            }

        # Connect
        timeout_options = (
            {
                "connect_timeout": max(1, math.ceil(timeout_seconds)),
                "read_timeout": timeout_seconds,
                "write_timeout": timeout_seconds,
            }
            if timeout_seconds > 0
            else {}
        )
        conn = create_mysql_connection_from_params(params, **timeout_options)
        if statement_cancel is not None and not statement_cancel.register(
            conn, DbType.MYSQL, target, config
        ):
            conn.close()
            return _cancelled_result()

        try:
            with conn.cursor() as cursor:
                # Set query timeout
                if timeout_seconds > 0:
                    timeout_ms = timeout_seconds * 1000
                    try:
                        cursor.execute(f"SET SESSION max_execution_time = {timeout_ms}")
                    except Exception:
                        # Older MySQL versions may not support this
                        pass

                if statement_cancel is not None and statement_cancel.cancelled:
                    return _cancelled_result()
                # Execute query
                cursor.execute(sql)

                # Get results
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                rows = cursor.fetchall() if max_rows is None else cursor.fetchmany(max_rows)
                rows_list = [list(row) for row in rows]

                return {
                    'success': True,
                    'rows': rows_list,
                    'columns': columns,
                    'error': None
                }
        finally:
            if statement_cancel is not None:
                statement_cancel.unregister(conn)
            conn.close()

    except ImportError:
        return {
            'success': False,
            'error': 'pymysql not installed',
            'error_kind': 'local_dependency',
            'rows': [],
            'columns': []
        }
    except Exception as e:
        message = str(e)
        if statement_cancel is not None and statement_cancel.cancelled:
            return _cancelled_result()
        args = getattr(e, "args", ())
        error_code = args[0] if args and isinstance(args[0], int) else None
        if error_code == 3024 or "maximum statement execution time" in message.lower():
            error_kind = "query_timeout"
            message = f"Query exceeded the {timeout_seconds}-second execution limit."
        elif isinstance(e, pymysql.OperationalError) and error_code in {
            2002, 2003, 2006, 2013,
        }:
            error_kind = "connection"
        else:
            error_kind = "query_execution"
        return {
            'success': False,
            'error': message,
            'error_kind': error_kind,
            'rows': [],
            'columns': []
        }

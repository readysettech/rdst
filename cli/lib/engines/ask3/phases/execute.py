"""
Phase 5: SQL Execution

Executes validated SQL against the database.
Handles timeouts and execution errors.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Dict, Any, List

if TYPE_CHECKING:
    from ..context import Ask3Context
    from ..presenter import Ask3Presenter

from ..types import ExecutionResult, DbType

logger = logging.getLogger(__name__)


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
        ctx.mark_error("No SQL to execute")
        presenter.error("No SQL to execute")
        return ctx

    if not ctx.target_config:
        ctx.mark_error("No target configuration")
        presenter.error("No target configuration")
        return ctx

    start_time = time.time()

    try:
        # Use injected executor if provided (for testing)
        if db_executor:
            result = db_executor(ctx.sql, ctx.target_config)
        else:
            # Execute based on database type
            db_type = ctx.db_type or ctx.target_config.get('engine', 'postgresql').lower()

            if db_type == DbType.POSTGRESQL or 'postgres' in db_type:
                result = _execute_postgres(ctx.sql, ctx.target_config, ctx.timeout_seconds)
            elif db_type == DbType.MYSQL or 'mysql' in db_type:
                result = _execute_mysql(ctx.sql, ctx.target_config, ctx.timeout_seconds)
            else:
                ctx.mark_error(f"Unsupported database type: {db_type}")
                presenter.error(f"Unsupported database type: {db_type}")
                return ctx

        execution_time_ms = (time.time() - start_time) * 1000

        if not result.get('success'):
            error = result.get('error', 'Unknown execution error')
            ctx.execution_result = ExecutionResult(
                error=error,
                execution_time_ms=execution_time_ms
            )
            presenter.execution_error(error)
            return ctx

        # Build execution result
        columns = result.get('columns', [])
        rows = result.get('rows', [])
        row_count = len(rows)

        # Check if truncated
        truncated = row_count >= ctx.max_rows

        ctx.execution_result = ExecutionResult(
            columns=columns,
            rows=rows,
            row_count=row_count,
            execution_time_ms=execution_time_ms,
            truncated=truncated
        )

        ctx.mark_success()

    except Exception as e:
        execution_time_ms = (time.time() - start_time) * 1000
        logger.error(f"Query execution failed: {e}")

        ctx.execution_result = ExecutionResult(
            error=str(e),
            execution_time_ms=execution_time_ms
        )
        presenter.execution_error(str(e))

    return ctx


def _execute_postgres(sql: str, config: Dict[str, Any], timeout_seconds: int) -> Dict[str, Any]:
    """Execute query against PostgreSQL."""
    try:
        import psycopg2

        host = config.get('host', 'localhost')
        port = config.get('port', 5432)
        user = config.get('user') or config.get('username')
        database = config.get('database') or config.get('dbname')

        # Get password from environment or config
        password_env = config.get('password_env')
        password = os.environ.get(password_env) if password_env else config.get('password')

        if not all([host, user, database]):
            return {
                'success': False,
                'error': 'Missing connection parameters (host, user, or database)',
                'rows': [],
                'columns': []
            }

        # Determine SSL mode
        tls_enabled = config.get('tls', False)
        sslmode = 'prefer' if tls_enabled else 'disable'

        # Connect
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=10,
            sslmode=sslmode
        )

        try:
            with conn.cursor() as cursor:
                # Set query timeout
                if timeout_seconds > 0:
                    timeout_ms = timeout_seconds * 1000
                    cursor.execute(f"SET statement_timeout = '{timeout_ms}ms'")

                # Execute query
                cursor.execute(sql)

                # Get results
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                rows = cursor.fetchall()
                rows_list = [list(row) for row in rows]

                return {
                    'success': True,
                    'rows': rows_list,
                    'columns': columns,
                    'error': None
                }
        finally:
            conn.close()

    except ImportError:
        return {
            'success': False,
            'error': 'psycopg2 not installed',
            'rows': [],
            'columns': []
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'rows': [],
            'columns': []
        }


def _execute_mysql(sql: str, config: Dict[str, Any], timeout_seconds: int) -> Dict[str, Any]:
    """Execute query against MySQL."""
    try:
        import pymysql

        host = config.get('host', 'localhost')
        port = config.get('port', 3306)
        user = config.get('user') or config.get('username')
        database = config.get('database') or config.get('dbname')

        # Get password from environment or config
        password_env = config.get('password_env')
        password = os.environ.get(password_env) if password_env else config.get('password')

        if not all([host, user, database]):
            return {
                'success': False,
                'error': 'Missing connection parameters (host, user, or database)',
                'rows': [],
                'columns': []
            }

        # Connect
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=10
        )

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

                # Execute query
                cursor.execute(sql)

                # Get results
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                rows = cursor.fetchall()
                rows_list = [list(row) for row in rows]

                return {
                    'success': True,
                    'rows': rows_list,
                    'columns': columns,
                    'error': None
                }
        finally:
            conn.close()

    except ImportError:
        return {
            'success': False,
            'error': 'pymysql not installed',
            'rows': [],
            'columns': []
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'rows': [],
            'columns': []
        }

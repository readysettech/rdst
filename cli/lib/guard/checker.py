"""
Query checker - Validate SQL against guard rules.

Performs structural analysis of SQL to detect:
- Missing WHERE clause
- Missing LIMIT clause
- SELECT * usage
- Too many tables in JOIN
- (Optional) EXPLAIN cost estimation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .config import GuardConfig

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Result of a single guard check."""
    passed: bool
    level: str  # "block", "warn", "info"
    guard_name: str
    message: str
    suggestion: str | None = None


def check_query(
    sql: str,
    config: GuardConfig,
    target_name: str | None = None,
    target_config: dict[str, Any] | None = None,
) -> list[CheckResult]:
    """Check SQL against all enabled guards.

    Args:
        sql: The SQL query to check.
        config: Guard configuration with enabled checks.
        target_name: Optional target name (for cost/row estimation).
        target_config: Optional target config (for cost/row estimation).

    Returns:
        List of CheckResult for each check performed.
    """
    results = []

    # Always check read-only
    results.append(check_read_only(sql))

    # Structural guards
    if config.guards.require_where:
        results.append(check_require_where(sql))

    if config.guards.require_limit:
        results.append(check_require_limit(sql))

    if config.guards.no_select_star:
        results.append(check_no_select_star(sql))

    if config.guards.max_tables:
        results.append(check_max_tables(sql, config.guards.max_tables))

    # Cost estimation (requires database connection)
    if config.guards.cost_limit and target_config:
        results.append(
            check_cost_limit(sql, target_config, config.guards.cost_limit)
        )

    # Row estimation (requires database connection) - stronger than require_where
    if config.guards.max_estimated_rows and target_config:
        results.append(
            check_estimated_rows(sql, config.guards.max_estimated_rows, target_config)
        )

    # Check denied columns
    if config.restrictions.denied_columns:
        results.append(
            check_denied_columns(sql, config.restrictions.denied_columns)
        )

    # Check allowed tables
    if config.restrictions.allowed_tables:
        results.append(
            check_allowed_tables(sql, config.restrictions.allowed_tables)
        )

    # Check required filters - stronger than require_where
    if config.restrictions.required_filters:
        results.append(
            check_required_filters(sql, config.restrictions.required_filters)
        )

    return results


def check_read_only(sql: str) -> CheckResult:
    """Check that query is read-only (SELECT/WITH only)."""
    # Strip SQL comments before checking
    sql_stripped = _strip_sql_comments(sql).strip().upper()

    # Allow SELECT and WITH (CTEs)
    if sql_stripped.startswith("SELECT") or sql_stripped.startswith("WITH"):
        return CheckResult(
            passed=True,
            level="info",
            guard_name="read_only",
            message="Read-only query",
        )

    # Block write operations
    write_keywords = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE"]
    for kw in write_keywords:
        if sql_stripped.startswith(kw):
            return CheckResult(
                passed=False,
                level="block",
                guard_name="read_only",
                message=f"Write operation not allowed: {kw}",
                suggestion="Only SELECT queries are permitted",
            )

    return CheckResult(
        passed=False,
        level="block",
        guard_name="read_only",
        message="Unknown query type",
        suggestion="Query must start with SELECT or WITH",
    )


def _strip_sql_comments(sql: str) -> str:
    """Strip SQL comments (-- line comments and /* block comments */)."""
    import re

    # Remove block comments /* ... */
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)

    # Remove line comments -- ...
    lines = []
    for line in sql.split('\n'):
        # Remove -- comments (but not inside strings)
        # Simple approach: remove from -- to end of line
        if '--' in line:
            # Find -- that's not inside a string (simple heuristic)
            idx = line.find('--')
            # Check if there's an odd number of quotes before it
            before = line[:idx]
            if before.count("'") % 2 == 0 and before.count('"') % 2 == 0:
                line = before
        lines.append(line)

    return '\n'.join(lines).strip()


def check_require_where(sql: str) -> CheckResult:
    """Check that SELECT has a WHERE clause."""
    try:
        import sqlglot
        parsed = sqlglot.parse_one(sql)

        # Find WHERE clause
        where = parsed.find(sqlglot.exp.Where)
        if where is not None:
            return CheckResult(
                passed=True,
                level="info",
                guard_name="require_where",
                message="WHERE clause present",
            )

        return CheckResult(
            passed=False,
            level="block",
            guard_name="require_where",
            message="Missing WHERE clause",
            suggestion="Add a WHERE clause to filter results",
        )

    except ImportError:
        logger.warning("sqlglot not installed, skipping require_where check")
        return CheckResult(
            passed=True,
            level="warn",
            guard_name="require_where",
            message="Could not verify WHERE clause (sqlglot not installed)",
        )
    except Exception as e:
        logger.warning(f"Could not parse SQL for WHERE check: {e}")
        return CheckResult(
            passed=True,
            level="warn",
            guard_name="require_where",
            message=f"Could not verify WHERE clause: {e}",
        )


def check_require_limit(sql: str) -> CheckResult:
    """Check that SELECT has a LIMIT clause."""
    try:
        import sqlglot
        parsed = sqlglot.parse_one(sql)

        # Find LIMIT clause
        limit = parsed.find(sqlglot.exp.Limit)
        if limit is not None:
            return CheckResult(
                passed=True,
                level="info",
                guard_name="require_limit",
                message="LIMIT clause present",
            )

        return CheckResult(
            passed=False,
            level="block",
            guard_name="require_limit",
            message="Missing LIMIT clause",
            suggestion="Add a LIMIT clause to bound result size",
        )

    except ImportError:
        logger.warning("sqlglot not installed, skipping require_limit check")
        return CheckResult(
            passed=True,
            level="warn",
            guard_name="require_limit",
            message="Could not verify LIMIT clause (sqlglot not installed)",
        )
    except Exception as e:
        logger.warning(f"Could not parse SQL for LIMIT check: {e}")
        return CheckResult(
            passed=True,
            level="warn",
            guard_name="require_limit",
            message=f"Could not verify LIMIT clause: {e}",
        )


def check_no_select_star(sql: str) -> CheckResult:
    """Check that query doesn't use SELECT *."""
    try:
        import sqlglot
        parsed = sqlglot.parse_one(sql)

        # Find Star expressions
        stars = list(parsed.find_all(sqlglot.exp.Star))
        if not stars:
            return CheckResult(
                passed=True,
                level="info",
                guard_name="no_select_star",
                message="No SELECT * detected",
            )

        return CheckResult(
            passed=False,
            level="warn",  # Warning, not block
            guard_name="no_select_star",
            message="SELECT * detected",
            suggestion="Specify explicit columns instead of *",
        )

    except ImportError:
        logger.warning("sqlglot not installed, skipping SELECT * check")
        return CheckResult(
            passed=True,
            level="warn",
            guard_name="no_select_star",
            message="Could not check for SELECT * (sqlglot not installed)",
        )
    except Exception as e:
        logger.warning(f"Could not parse SQL for SELECT * check: {e}")
        return CheckResult(
            passed=True,
            level="warn",
            guard_name="no_select_star",
            message=f"Could not check for SELECT *: {e}",
        )


def check_max_tables(sql: str, max_tables: int) -> CheckResult:
    """Check that query doesn't join too many tables."""
    try:
        import sqlglot
        parsed = sqlglot.parse_one(sql)

        # Find all table references
        tables = set()
        for table in parsed.find_all(sqlglot.exp.Table):
            tables.add(table.name)

        table_count = len(tables)

        if table_count <= max_tables:
            return CheckResult(
                passed=True,
                level="info",
                guard_name="max_tables",
                message=f"Query uses {table_count} table(s) (limit: {max_tables})",
            )

        return CheckResult(
            passed=False,
            level="warn",  # Warning, not block
            guard_name="max_tables",
            message=f"Query uses {table_count} tables (limit: {max_tables})",
            suggestion="Consider simplifying the query or breaking it into parts",
        )

    except ImportError:
        logger.warning("sqlglot not installed, skipping max_tables check")
        return CheckResult(
            passed=True,
            level="warn",
            guard_name="max_tables",
            message="Could not count tables (sqlglot not installed)",
        )
    except Exception as e:
        logger.warning(f"Could not parse SQL for table count: {e}")
        return CheckResult(
            passed=True,
            level="warn",
            guard_name="max_tables",
            message=f"Could not count tables: {e}",
        )


def check_denied_columns(sql: str, denied_columns: list[str]) -> CheckResult:
    """Check that query doesn't reference denied columns."""
    try:
        import sqlglot
        import fnmatch

        parsed = sqlglot.parse_one(sql)

        # Find all column references
        violations = []
        for col in parsed.find_all(sqlglot.exp.Column):
            col_name = col.name.lower()
            for pattern in denied_columns:
                if fnmatch.fnmatch(col_name, pattern.lower()):
                    violations.append(col_name)
                    break

        if not violations:
            return CheckResult(
                passed=True,
                level="info",
                guard_name="denied_columns",
                message="No denied columns referenced",
            )

        return CheckResult(
            passed=False,
            level="block",
            guard_name="denied_columns",
            message=f"Denied column(s) referenced: {', '.join(violations)}",
            suggestion="Remove references to restricted columns",
        )

    except ImportError:
        logger.warning("sqlglot not installed, skipping denied_columns check")
        return CheckResult(
            passed=True,
            level="warn",
            guard_name="denied_columns",
            message="Could not check denied columns (sqlglot not installed)",
        )
    except Exception as e:
        logger.warning(f"Could not parse SQL for column check: {e}")
        return CheckResult(
            passed=True,
            level="warn",
            guard_name="denied_columns",
            message=f"Could not check denied columns: {e}",
        )


def check_allowed_tables(sql: str, allowed_tables: list[str]) -> CheckResult:
    """Check that query only references allowed tables."""
    try:
        import sqlglot

        parsed = sqlglot.parse_one(sql)

        # Find all table references
        allowed_lower = [t.lower() for t in allowed_tables]
        violations = []

        for table in parsed.find_all(sqlglot.exp.Table):
            table_name = table.name.lower()
            if table_name not in allowed_lower:
                violations.append(table_name)

        if not violations:
            return CheckResult(
                passed=True,
                level="info",
                guard_name="allowed_tables",
                message="All tables are allowed",
            )

        return CheckResult(
            passed=False,
            level="block",
            guard_name="allowed_tables",
            message=f"Table(s) not in allowlist: {', '.join(violations)}",
            suggestion=f"Only these tables are allowed: {', '.join(allowed_tables)}",
        )

    except ImportError:
        logger.warning("sqlglot not installed, skipping allowed_tables check")
        return CheckResult(
            passed=True,
            level="warn",
            guard_name="allowed_tables",
            message="Could not check allowed tables (sqlglot not installed)",
        )
    except Exception as e:
        logger.warning(f"Could not parse SQL for table check: {e}")
        return CheckResult(
            passed=True,
            level="warn",
            guard_name="allowed_tables",
            message=f"Could not check allowed tables: {e}",
        )


def check_cost_limit(
    sql: str,
    target_config: dict[str, Any],
    cost_limit: int,
) -> CheckResult:
    """Check query cost against limit using EXPLAIN.

    Note: This requires a database connection.
    """
    try:
        # Get database type
        engine = target_config.get("engine", "postgresql").lower()

        if "postgres" in engine:
            cost = _get_postgres_cost(sql, target_config)
        elif "mysql" in engine:
            cost = _get_mysql_cost(sql, target_config)
        else:
            return CheckResult(
                passed=True,
                level="warn",
                guard_name="cost_limit",
                message=f"Cost estimation not supported for {engine}",
            )

        if cost is None:
            return CheckResult(
                passed=True,
                level="warn",
                guard_name="cost_limit",
                message="Could not estimate query cost",
            )

        if cost <= cost_limit:
            return CheckResult(
                passed=True,
                level="info",
                guard_name="cost_limit",
                message=f"Query cost: {cost:,.0f} (limit: {cost_limit:,})",
            )

        return CheckResult(
            passed=False,
            level="block",
            guard_name="cost_limit",
            message=f"Query cost {cost:,.0f} exceeds limit {cost_limit:,}",
            suggestion="Optimize the query or add more specific filters",
        )

    except Exception as e:
        logger.warning(f"Could not estimate query cost: {e}")
        return CheckResult(
            passed=True,
            level="warn",
            guard_name="cost_limit",
            message=f"Could not estimate query cost: {e}",
        )


def _get_postgres_cost(sql: str, config: dict[str, Any]) -> float | None:
    """Get query cost from PostgreSQL EXPLAIN."""
    import os
    try:
        import psycopg2

        host = config.get("host", "localhost")
        port = config.get("port", 5432)
        user = config.get("user") or config.get("username")
        database = config.get("database") or config.get("dbname")
        password_env = config.get("password_env")
        password = os.environ.get(password_env) if password_env else config.get("password")

        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=10,
        )

        try:
            with conn.cursor() as cursor:
                cursor.execute(f"EXPLAIN (FORMAT JSON) {sql}")
                result = cursor.fetchone()
                if result and result[0]:
                    plan = result[0][0]
                    return plan.get("Plan", {}).get("Total Cost", 0)
        finally:
            conn.close()

    except Exception as e:
        logger.warning(f"PostgreSQL EXPLAIN failed: {e}")
        return None

    return None


def _get_mysql_cost(sql: str, config: dict[str, Any]) -> float | None:
    """Get query cost from MySQL EXPLAIN."""
    import os
    try:
        import pymysql

        host = config.get("host", "localhost")
        port = config.get("port", 3306)
        user = config.get("user") or config.get("username")
        database = config.get("database") or config.get("dbname")
        password_env = config.get("password_env")
        password = os.environ.get(password_env) if password_env else config.get("password")

        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=10,
        )

        try:
            with conn.cursor() as cursor:
                cursor.execute(f"EXPLAIN FORMAT=JSON {sql}")
                result = cursor.fetchone()
                if result:
                    import json
                    plan = json.loads(result[0])
                    # MySQL cost is in query_block.cost_info.query_cost
                    cost_info = plan.get("query_block", {}).get("cost_info", {})
                    cost_str = cost_info.get("query_cost", "0")
                    return float(cost_str)
        finally:
            conn.close()

    except Exception as e:
        logger.warning(f"MySQL EXPLAIN failed: {e}")
        return None

    return None


def check_required_filters(
    sql: str,
    required_filters: dict[str, list[str]],
) -> CheckResult:
    """Check that query has meaningful filters on required columns.

    This is stronger than require_where because it detects trivial bypasses:
    - "WHERE id IS NOT NULL" - doesn't actually filter
    - "WHERE 1=1" - always true
    - "WHERE true" - always true

    Args:
        sql: The SQL query to check.
        required_filters: Dict mapping table names to required filter columns.
            Example: {"users": ["id", "email"]} means queries on users
            must filter on id OR email with an actual value.

    Returns:
        CheckResult with pass/fail status.
    """
    try:
        import sqlglot

        parsed = sqlglot.parse_one(sql)

        # Find all tables in query
        tables_in_query: set[str] = set()
        for table in parsed.find_all(sqlglot.exp.Table):
            if table.name:
                tables_in_query.add(table.name.lower())

        # Check each table with requirements
        for table, required_cols in required_filters.items():
            if table.lower() not in tables_in_query:
                continue

            # Find WHERE clause
            where = parsed.find(sqlglot.exp.Where)
            if not where:
                return CheckResult(
                    passed=False,
                    level="block",
                    guard_name="required_filters",
                    message=f"Query on '{table}' requires filter on: {', '.join(required_cols)}",
                    suggestion=f"Add WHERE clause filtering on {' or '.join(required_cols)}",
                )

            # Check for meaningful filter on at least one required column
            has_meaningful_filter = False
            for col in required_cols:
                if _has_value_filter(where, col):
                    has_meaningful_filter = True
                    break

            if not has_meaningful_filter:
                return CheckResult(
                    passed=False,
                    level="block",
                    guard_name="required_filters",
                    message=f"Query on '{table}' requires actual value filter on: {', '.join(required_cols)}",
                    suggestion="Use a specific value like 'WHERE id = 123', not 'WHERE id IS NOT NULL'",
                )

        return CheckResult(
            passed=True,
            level="info",
            guard_name="required_filters",
            message="Required filters present",
        )

    except ImportError:
        logger.warning("sqlglot not installed, skipping required_filters check")
        return CheckResult(
            passed=True,
            level="warn",
            guard_name="required_filters",
            message="Could not check required filters (sqlglot not installed)",
        )
    except Exception as e:
        logger.warning(f"Could not check required filters: {e}")
        return CheckResult(
            passed=True,
            level="warn",
            guard_name="required_filters",
            message=f"Could not check required filters: {e}",
        )


def _has_value_filter(where_clause, column_name: str) -> bool:
    """Check if WHERE has a meaningful value filter on column.

    Accepts:
    - id = 123
    - email = 'user@example.com'
    - created_at > '2024-01-01'
    - status IN ('active', 'pending')

    Rejects:
    - id IS NOT NULL
    - id IS NULL
    - 1=1, true, etc.
    """
    import sqlglot

    # Look for comparison expressions involving our column
    comparison_types = (
        sqlglot.exp.EQ,
        sqlglot.exp.NEQ,
        sqlglot.exp.GT,
        sqlglot.exp.GTE,
        sqlglot.exp.LT,
        sqlglot.exp.LTE,
        sqlglot.exp.In,
        sqlglot.exp.Like,
        sqlglot.exp.Between,
    )

    for expr in where_clause.find_all(*comparison_types):
        # Check if this comparison involves our column
        for col in expr.find_all(sqlglot.exp.Column):
            if col.name.lower() == column_name.lower():
                # Found our column - verify it's not a trivial comparison
                if not _is_trivial_comparison(expr):
                    return True

    return False


def _is_trivial_comparison(expr) -> bool:
    """Detect trivial comparisons that don't actually filter.

    Returns True for expressions like:
    - IS NOT NULL, IS NULL
    - 1=1, '1'='1'
    - true, false
    """
    import sqlglot

    # Check for IS NULL / IS NOT NULL
    if isinstance(expr, (sqlglot.exp.Is,)):
        return True

    # Check the SQL representation for common trivial patterns
    try:
        expr_sql = expr.sql().upper()
        trivial_patterns = [
            "IS NOT NULL",
            "IS NULL",
            "1=1", "1 = 1",
            "'1'='1'", "'1' = '1'",
            "TRUE",
            "FALSE",
        ]
        return any(p in expr_sql for p in trivial_patterns)
    except Exception:
        return False


def check_estimated_rows(
    sql: str,
    max_rows: int,
    target_config: dict[str, Any],
) -> CheckResult:
    """Check estimated row count using database EXPLAIN.

    This catches bypass attempts that syntactic checks miss:
    - "WHERE id IS NOT NULL" - planner knows this returns all rows
    - "WHERE status IN ('a','b','c','d','e')" - if that's all statuses

    Args:
        sql: The SQL query to check.
        max_rows: Maximum estimated rows to allow.
        target_config: Database connection configuration.

    Returns:
        CheckResult with pass/fail status.
    """
    try:
        engine = target_config.get("engine", "postgresql").lower()

        if "postgres" in engine:
            estimated = _get_postgres_estimated_rows(sql, target_config)
        elif "mysql" in engine:
            estimated = _get_mysql_estimated_rows(sql, target_config)
        else:
            return CheckResult(
                passed=True,
                level="warn",
                guard_name="max_estimated_rows",
                message=f"Row estimation not supported for {engine}",
            )

        if estimated is None:
            return CheckResult(
                passed=True,
                level="warn",
                guard_name="max_estimated_rows",
                message="Could not estimate row count",
            )

        if estimated <= max_rows:
            return CheckResult(
                passed=True,
                level="info",
                guard_name="max_estimated_rows",
                message=f"Estimated rows: {estimated:,.0f} (limit: {max_rows:,})",
            )

        return CheckResult(
            passed=False,
            level="block",
            guard_name="max_estimated_rows",
            message=f"Query estimated to return {estimated:,.0f} rows (limit: {max_rows:,})",
            suggestion="Add more specific WHERE filters to reduce result size",
        )

    except Exception as e:
        logger.warning(f"Could not estimate row count: {e}")
        return CheckResult(
            passed=True,
            level="warn",
            guard_name="max_estimated_rows",
            message=f"Could not estimate row count: {e}",
        )


def _get_postgres_estimated_rows(sql: str, config: dict[str, Any]) -> float | None:
    """Get estimated row count from PostgreSQL EXPLAIN."""
    import os
    try:
        import psycopg2

        host = config.get("host", "localhost")
        port = config.get("port", 5432)
        user = config.get("user") or config.get("username")
        database = config.get("database") or config.get("dbname")
        password_env = config.get("password_env")
        password = os.environ.get(password_env) if password_env else config.get("password")

        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=10,
        )

        try:
            with conn.cursor() as cursor:
                cursor.execute(f"EXPLAIN (FORMAT JSON) {sql}")
                result = cursor.fetchone()
                if result and result[0]:
                    plan = result[0][0]
                    return plan.get("Plan", {}).get("Plan Rows", 0)
        finally:
            conn.close()

    except Exception as e:
        logger.warning(f"PostgreSQL row estimation failed: {e}")
        return None

    return None


def _get_mysql_estimated_rows(sql: str, config: dict[str, Any]) -> float | None:
    """Get estimated row count from MySQL EXPLAIN."""
    import os
    try:
        import pymysql
        import json

        host = config.get("host", "localhost")
        port = config.get("port", 3306)
        user = config.get("user") or config.get("username")
        database = config.get("database") or config.get("dbname")
        password_env = config.get("password_env")
        password = os.environ.get(password_env) if password_env else config.get("password")

        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=10,
        )

        try:
            with conn.cursor() as cursor:
                cursor.execute(f"EXPLAIN FORMAT=JSON {sql}")
                result = cursor.fetchone()
                if result:
                    plan = json.loads(result[0])
                    # MySQL stores rows in query_block.table.rows_examined_per_scan
                    # or in nested_loop[].table.rows_examined_per_scan
                    query_block = plan.get("query_block", {})

                    # Try direct table access
                    table = query_block.get("table", {})
                    if "rows_examined_per_scan" in table:
                        return float(table["rows_examined_per_scan"])

                    # Try nested loop
                    nested_loop = query_block.get("nested_loop", [])
                    if nested_loop:
                        total_rows = 1
                        for item in nested_loop:
                            table = item.get("table", {})
                            rows = table.get("rows_examined_per_scan", 1)
                            total_rows *= rows
                        return float(total_rows)

                    return None
        finally:
            conn.close()

    except Exception as e:
        logger.warning(f"MySQL row estimation failed: {e}")
        return None

    return None

"""
RDST Scan Index Checker

Compares queries in a diff against existing database indexes.
Can also detect indexes being added in alembic migrations in the same diff.

CI/CD Flow:
1. Extract queries from changed files (AST-based)
2. Get existing indexes from database
3. Get indexes being added in alembic migrations (if any in diff)
4. Flag queries that filter/join on unindexed columns
5. Allow bypass via annotation or if index is in same PR

Usage:
    rdst scan --diff HEAD~1 --schema mydb --check
"""

import ast
import re
from pathlib import Path
from typing import List, Dict, Set, Optional, Tuple
from dataclasses import dataclass


@dataclass
class IndexInfo:
    """Represents a database index."""
    name: str
    table: str
    columns: List[str]
    is_unique: bool = False
    source: str = "database"  # "database" or "migration"


@dataclass
class QueryIndexIssue:
    """A query that might need an index."""
    file: str
    function: str
    sql: str
    table: str
    column: str
    issue_type: str  # "missing_index", "missing_composite_index"
    severity: str  # "error", "warning", "info"
    suggestion: str


def get_existing_indexes(db_connection) -> List[IndexInfo]:
    """
    Get all indexes from the database.

    For PostgreSQL:
        SELECT indexname, tablename, indexdef FROM pg_indexes WHERE schemaname = 'public';

    For MySQL:
        SELECT INDEX_NAME, TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.STATISTICS;
    """
    # This would use the actual db connection
    # For now, return empty - would be implemented with real DB query
    indexes = []

    # Example of what we'd get:
    # indexes.append(IndexInfo(
    #     name="ix_customer_custkey",
    #     table="customer",
    #     columns=["c_custkey"],
    #     is_unique=True,
    #     source="database"
    # ))

    return indexes


def parse_alembic_migrations(diff_files: List[str], repo_root: str) -> List[IndexInfo]:
    """
    Parse alembic migration files in the diff to find indexes being added.

    Looks for patterns like:
        op.create_index('ix_name', 'table', ['col1', 'col2'])
        op.create_index(op.f('ix_name'), 'table', ['col1'])
    """
    indexes = []

    for filepath in diff_files:
        # Only check alembic migration files
        if 'alembic' not in filepath and 'migrations' not in filepath:
            continue
        if not filepath.endswith('.py'):
            continue

        full_path = Path(repo_root) / filepath
        if not full_path.exists():
            continue

        try:
            content = full_path.read_text()
            tree = ast.parse(content)
        except Exception:
            continue

        # Walk AST looking for op.create_index calls
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            # Check if it's op.create_index(...)
            if isinstance(node.func, ast.Attribute):
                if node.func.attr == 'create_index':
                    index = _parse_create_index_call(node, filepath)
                    if index:
                        indexes.append(index)

    return indexes


def _parse_create_index_call(node: ast.Call, source_file: str) -> Optional[IndexInfo]:
    """
    Parse an op.create_index() call to extract index info.

    Patterns:
        op.create_index('ix_name', 'table', ['col1', 'col2'])
        op.create_index(op.f('ix_name'), 'table', ['col1'], unique=True)
    """
    if len(node.args) < 3:
        return None

    # Get index name (might be string or op.f('name'))
    name_arg = node.args[0]
    if isinstance(name_arg, ast.Constant):
        index_name = name_arg.value
    elif isinstance(name_arg, ast.Call):
        # op.f('name') pattern
        if name_arg.args and isinstance(name_arg.args[0], ast.Constant):
            index_name = name_arg.args[0].value
        else:
            index_name = "unknown"
    else:
        index_name = "unknown"

    # Get table name
    table_arg = node.args[1]
    if isinstance(table_arg, ast.Constant):
        table_name = table_arg.value
    else:
        return None

    # Get columns (should be a list)
    cols_arg = node.args[2]
    columns = []
    if isinstance(cols_arg, ast.List):
        for elt in cols_arg.elts:
            if isinstance(elt, ast.Constant):
                columns.append(elt.value)

    if not columns:
        return None

    # Check for unique=True in kwargs
    is_unique = False
    for kw in node.keywords:
        if kw.arg == 'unique' and isinstance(kw.value, ast.Constant):
            is_unique = kw.value.value

    return IndexInfo(
        name=index_name,
        table=table_name,
        columns=columns,
        is_unique=is_unique,
        source=f"migration:{source_file}"
    )


def extract_query_columns(sql: str) -> Dict[str, Set[str]]:
    """
    Extract table.column references from SQL for WHERE, JOIN, ORDER BY clauses.

    Returns: {table_name: {col1, col2, ...}}

    This is a simplified parser - production would use sqlglot or similar.
    """
    columns_by_table: Dict[str, Set[str]] = {}

    # Normalize SQL
    sql_upper = sql.upper()
    sql_lower = sql.lower()

    # Simple pattern matching for WHERE clauses
    # Matches: table.column = or column = (assumes table from FROM)
    where_pattern = r'where\s+(\w+)\.(\w+)\s*='
    for match in re.finditer(where_pattern, sql_lower):
        table, col = match.groups()
        if table not in columns_by_table:
            columns_by_table[table] = set()
        columns_by_table[table].add(col)

    # Also match simple column references after WHERE
    # WHERE column_name = $1
    simple_where = r'where\s+(\w+)\s*[=<>]'
    from_match = re.search(r'from\s+(\w+)', sql_lower)
    if from_match:
        main_table = from_match.group(1)
        for match in re.finditer(simple_where, sql_lower):
            col = match.group(1)
            if col not in ('select', 'from', 'where', 'and', 'or'):
                if main_table not in columns_by_table:
                    columns_by_table[main_table] = set()
                columns_by_table[main_table].add(col)

    return columns_by_table


def check_query_indexes(
    sql: str,
    existing_indexes: List[IndexInfo],
    migration_indexes: List[IndexInfo],
    file: str,
    function: str
) -> List[QueryIndexIssue]:
    """
    Check if a query has appropriate indexes.

    Returns list of issues found.
    """
    issues = []

    # Combine all available indexes
    all_indexes = existing_indexes + migration_indexes

    # Build lookup: (table, column) -> index
    indexed_columns: Dict[Tuple[str, str], IndexInfo] = {}
    for idx in all_indexes:
        for col in idx.columns:
            indexed_columns[(idx.table.lower(), col.lower())] = idx

    # Extract columns used in WHERE/JOIN from the query
    query_columns = extract_query_columns(sql)

    for table, columns in query_columns.items():
        for col in columns:
            key = (table.lower(), col.lower())
            if key not in indexed_columns:
                # Check if it's being added in a migration
                in_migration = any(
                    idx.table.lower() == table.lower() and col.lower() in [c.lower() for c in idx.columns]
                    for idx in migration_indexes
                )

                if in_migration:
                    severity = "info"
                    suggestion = f"Index on {table}.{col} is being added in this PR's migration"
                else:
                    severity = "warning"
                    suggestion = f"Consider adding: CREATE INDEX ix_{table}_{col} ON {table} ({col});"

                issues.append(QueryIndexIssue(
                    file=file,
                    function=function,
                    sql=sql[:100] + "..." if len(sql) > 100 else sql,
                    table=table,
                    column=col,
                    issue_type="missing_index",
                    severity=severity,
                    suggestion=suggestion
                ))

    return issues


class ScanIndexChecker:
    """
    Main class for CI/CD index checking.

    Usage:
        checker = ScanIndexChecker(repo_root="/path/to/repo", db_target="mydb")
        issues = checker.check_diff(diff_files=["app/services/new_service.py"])

        if issues.has_errors():
            sys.exit(1)  # Fail CI
    """

    def __init__(self, repo_root: str, db_connection=None):
        self.repo_root = repo_root
        self.db_connection = db_connection
        self.existing_indexes: List[IndexInfo] = []
        self.migration_indexes: List[IndexInfo] = []

    def load_existing_indexes(self):
        """Load indexes from database."""
        if self.db_connection:
            self.existing_indexes = get_existing_indexes(self.db_connection)

    def check_diff(self, diff_files: List[str], queries: List[Dict]) -> Dict:
        """
        Check queries from diff against indexes.

        Args:
            diff_files: List of changed file paths
            queries: List of extracted queries (from AST extractor)

        Returns:
            {
                "issues": [...],
                "summary": {...},
                "pass": bool
            }
        """
        # Load migration indexes from diff
        self.migration_indexes = parse_alembic_migrations(diff_files, self.repo_root)

        all_issues = []

        for query in queries:
            sql = query.get("sql", "")
            if not sql or sql.startswith("--"):
                continue

            issues = check_query_indexes(
                sql=sql,
                existing_indexes=self.existing_indexes,
                migration_indexes=self.migration_indexes,
                file=query.get("file", "unknown"),
                function=query.get("function", "unknown")
            )
            all_issues.extend(issues)

        # Summarize
        errors = [i for i in all_issues if i.severity == "error"]
        warnings = [i for i in all_issues if i.severity == "warning"]
        infos = [i for i in all_issues if i.severity == "info"]

        return {
            "issues": [
                {
                    "file": i.file,
                    "function": i.function,
                    "table": i.table,
                    "column": i.column,
                    "severity": i.severity,
                    "suggestion": i.suggestion,
                }
                for i in all_issues
            ],
            "summary": {
                "total_queries_checked": len(queries),
                "errors": len(errors),
                "warnings": len(warnings),
                "indexes_in_migration": len(self.migration_indexes),
            },
            "pass": len(errors) == 0,
        }


# Example usage
if __name__ == "__main__":
    # Simulate a check
    checker = ScanIndexChecker(repo_root="/path/to/repo")

    # Pretend we have some existing indexes
    checker.existing_indexes = [
        IndexInfo("pk_customer", "customer", ["c_custkey"], is_unique=True),
        IndexInfo("pk_orders", "orders", ["o_orderkey"], is_unique=True),
    ]

    # Pretend diff includes a migration adding an index
    diff_files = ["alembic/versions/001_add_segment_index.py"]

    # Pretend we extracted these queries
    queries = [
        {
            "file": "app/services/customer_service.py",
            "function": "get_by_segment",
            "sql": "SELECT * FROM customer WHERE c_mktsegment = $1",
        }
    ]

    result = checker.check_diff(diff_files, queries)

    print(f"Pass: {result['pass']}")
    print(f"Warnings: {result['summary']['warnings']}")
    for issue in result['issues']:
        print(f"  [{issue['severity']}] {issue['file']}:{issue['function']}")
        print(f"    {issue['suggestion']}")

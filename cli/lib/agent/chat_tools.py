"""
Chat Tools for Conversational Agent

Defines tools available to the ChatAgent for interacting with databases.
The LLM decides when to use each tool based on user intent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
import json
import logging

if TYPE_CHECKING:
    from .runtime import AgentRuntime

logger = logging.getLogger(__name__)


# Anthropic tool definitions
CHAT_TOOLS = [
    {
        "name": "query_database",
        "description": (
            "Convert a natural language question into SQL and execute it against the database. "
            "Use this when the user wants to retrieve, count, analyze, or explore data. "
            "Examples: 'Show me top customers', 'How many orders last month?', "
            "'What's the average order value?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Natural language question about the data",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "get_schema",
        "description": (
            "Get information about database tables and columns. "
            "Use this when the user asks about what data is available, table structures, "
            "or column names. Can get all tables or details for a specific table."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "Optional: specific table to get details for. If omitted, returns all tables.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "run_sql",
        "description": (
            "Execute a specific SQL query provided by the user. "
            "Use this when the user provides explicit SQL to run, or asks to re-run "
            "a previous query with modifications. Safety checks still apply."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The SQL query to execute",
                },
            },
            "required": ["sql"],
        },
    },
]


@dataclass
class ToolResult:
    """Result from executing a tool."""

    tool_use_id: str
    success: bool
    content: str
    data: dict[str, Any] | None = None


class ChatToolExecutor:
    """
    Executes chat tools using the AgentRuntime.

    Wraps AgentRuntime methods to provide tool-compatible interface
    with proper error handling and result formatting.
    """

    def __init__(self, runtime: "AgentRuntime"):
        """
        Initialize the tool executor.

        Args:
            runtime: AgentRuntime for database operations.
        """
        self.runtime = runtime

    def execute(self, tool_name: str, tool_input: dict[str, Any], tool_use_id: str) -> ToolResult:
        """
        Execute a tool by name.

        Args:
            tool_name: Name of the tool to execute.
            tool_input: Input parameters for the tool.
            tool_use_id: ID from the tool_use block.

        Returns:
            ToolResult with execution outcome.
        """
        handlers = {
            "query_database": self._query_database,
            "get_schema": self._get_schema,
            "run_sql": self._run_sql,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return ToolResult(
                tool_use_id=tool_use_id,
                success=False,
                content=f"Unknown tool: {tool_name}",
            )

        try:
            return handler(tool_input, tool_use_id)
        except Exception as e:
            logger.exception(f"Tool execution failed: {tool_name}")
            return ToolResult(
                tool_use_id=tool_use_id,
                success=False,
                content=f"Tool error: {e}",
            )

    def _query_database(self, inputs: dict[str, Any], tool_use_id: str) -> ToolResult:
        """Execute natural language query via Ask3Engine."""
        question = inputs.get("question", "")
        if not question:
            return ToolResult(
                tool_use_id=tool_use_id,
                success=False,
                content="No question provided",
            )

        response = self.runtime.ask(question)

        if not response.success:
            return ToolResult(
                tool_use_id=tool_use_id,
                success=False,
                content=f"Query failed: {response.error}",
                data={"sql": response.sql} if response.sql else None,
            )

        # Format successful result
        result_lines = []

        if response.sql:
            result_lines.append(f"SQL: {response.sql}")
            result_lines.append("")

        if response.columns and response.rows:
            # Format as simple table for LLM context
            result_lines.append("Results:")
            result_lines.append(" | ".join(response.columns))
            result_lines.append("-" * 40)
            for row in response.rows[:20]:  # Limit rows for context
                result_lines.append(" | ".join(str(v) if v is not None else "NULL" for v in row))

            if response.row_count > 20:
                result_lines.append(f"... ({response.row_count} total rows)")

            if response.truncated:
                result_lines.append("(Results truncated by safety limit)")
        else:
            result_lines.append("No rows returned")

        return ToolResult(
            tool_use_id=tool_use_id,
            success=True,
            content="\n".join(result_lines),
            data={
                "sql": response.sql,
                "columns": response.columns,
                "rows": response.rows,
                "row_count": response.row_count,
                "execution_time_ms": response.execution_time_ms,
                "truncated": response.truncated,
            },
        )

    def _get_schema(self, inputs: dict[str, Any], tool_use_id: str) -> ToolResult:
        """Get database schema information."""
        table_name = inputs.get("table_name")

        schema = self.runtime.get_schema_summary()

        if "error" in schema:
            return ToolResult(
                tool_use_id=tool_use_id,
                success=False,
                content=f"Failed to get schema: {schema['error']}",
            )

        tables = schema.get("tables", [])

        if not tables:
            return ToolResult(
                tool_use_id=tool_use_id,
                success=True,
                content="No tables found in schema",
            )

        # If specific table requested, filter
        if table_name:
            table_name_lower = table_name.lower()
            tables = [t for t in tables if t["name"].lower() == table_name_lower]

            if not tables:
                return ToolResult(
                    tool_use_id=tool_use_id,
                    success=False,
                    content=f"Table '{table_name}' not found",
                )

        # Format schema info
        lines = [f"Schema (source: {schema.get('source', 'unknown')})", ""]

        for table in tables:
            lines.append(f"Table: {table['name']}")
            if table.get("description"):
                lines.append(f"  Description: {table['description']}")
            if table.get("columns"):
                cols = table["columns"]
                if isinstance(cols, list):
                    lines.append(f"  Columns: {', '.join(cols)}")
                elif isinstance(cols, dict):
                    for col_name, col_info in cols.items():
                        if isinstance(col_info, dict):
                            lines.append(f"    {col_name}: {col_info.get('type', 'unknown')}")
                        else:
                            lines.append(f"    {col_name}")
            lines.append("")

        return ToolResult(
            tool_use_id=tool_use_id,
            success=True,
            content="\n".join(lines),
            data={"tables": tables, "source": schema.get("source")},
        )

    def _run_sql(self, inputs: dict[str, Any], tool_use_id: str) -> ToolResult:
        """Execute provided SQL directly."""
        sql = inputs.get("sql", "")
        if not sql:
            return ToolResult(
                tool_use_id=tool_use_id,
                success=False,
                content="No SQL provided",
            )

        try:
            import os
            import time

            target_config = self.runtime._get_target_config()

            # Validate safety first
            self.runtime._validate_safety(sql)

            # Get limits
            guard = self.runtime._get_guard_config()
            if guard:
                max_rows = guard.limits.max_rows
                timeout_seconds = guard.limits.timeout_seconds
            else:
                max_rows = self.runtime.config.safety.max_rows
                timeout_seconds = self.runtime.config.safety.timeout_seconds

            # Execute the SQL using psycopg2 directly
            import psycopg2

            host = target_config.get('host', 'localhost')
            port = target_config.get('port', 5432)
            user = target_config.get('user') or target_config.get('username')
            database = target_config.get('database') or target_config.get('dbname')

            password_env = target_config.get('password_env')
            password = os.environ.get(password_env) if password_env else target_config.get('password')

            tls_enabled = target_config.get('tls', False)
            sslmode = 'prefer' if tls_enabled else 'disable'

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
                start = time.time()

                with conn.cursor() as cursor:
                    if timeout_seconds > 0:
                        timeout_ms = timeout_seconds * 1000
                        cursor.execute(f"SET statement_timeout = '{timeout_ms}ms'")

                    cursor.execute(sql)
                    columns = [desc[0] for desc in cursor.description] if cursor.description else []
                    rows = cursor.fetchmany(max_rows + 1)

                    execution_time_ms = (time.time() - start) * 1000
                    truncated = len(rows) > max_rows
                    if truncated:
                        rows = rows[:max_rows]

                    # Convert to list of lists
                    rows_list = [list(row) for row in rows]

                    # Apply masking
                    masked_rows = self.runtime._apply_masking(columns, rows_list)

                    # Format result
                    result_lines = [f"Executed: {sql}", ""]

                    if columns and masked_rows:
                        result_lines.append("Results:")
                        result_lines.append(" | ".join(columns))
                        result_lines.append("-" * 40)
                        for row in masked_rows[:20]:
                            result_lines.append(" | ".join(str(v) if v is not None else "NULL" for v in row))

                        row_count = len(masked_rows)
                        if row_count > 20:
                            result_lines.append(f"... ({row_count} total rows)")
                        if truncated:
                            result_lines.append("(Results truncated by safety limit)")
                    else:
                        result_lines.append("No rows returned")

                    return ToolResult(
                        tool_use_id=tool_use_id,
                        success=True,
                        content="\n".join(result_lines),
                        data={
                            "sql": sql,
                            "columns": columns,
                            "rows": masked_rows,
                            "row_count": len(masked_rows),
                            "execution_time_ms": execution_time_ms,
                            "truncated": truncated,
                        },
                    )
            finally:
                conn.close()

        except Exception as e:
            return ToolResult(
                tool_use_id=tool_use_id,
                success=False,
                content=f"SQL execution failed: {e}",
                data={"sql": sql},
            )


def format_tool_result_for_display(result: ToolResult) -> str:
    """
    Format tool result for terminal display.

    Args:
        result: ToolResult to format.

    Returns:
        Human-readable string for display.
    """
    if not result.success:
        return f"Error: {result.content}"

    # For query results with data, format nicely
    if result.data and "columns" in result.data and "rows" in result.data:
        lines = []
        if result.data.get("sql"):
            lines.append(f"SQL: {result.data['sql']}")
            lines.append("")

        columns = result.data["columns"]
        rows = result.data["rows"]

        if columns and rows:
            # Calculate column widths
            widths = [len(str(c)) for c in columns]
            for row in rows[:20]:
                for i, val in enumerate(row):
                    if i < len(widths):
                        widths[i] = max(widths[i], len(str(val) if val is not None else "NULL"))

            # Format header
            header = " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(columns))
            lines.append(header)
            lines.append("-" * len(header))

            # Format rows
            for row in rows[:20]:
                row_str = " | ".join(
                    (str(v) if v is not None else "NULL").ljust(widths[i])
                    for i, v in enumerate(row)
                )
                lines.append(row_str)

            row_count = result.data.get("row_count", len(rows))
            if row_count > 20:
                lines.append(f"... ({row_count} total rows)")

            lines.append("")
            lines.append(f"({row_count} rows, {result.data.get('execution_time_ms', 0):.1f}ms)")

            if result.data.get("truncated"):
                lines.append("(Results truncated by safety limit)")

            return "\n".join(lines)

    return result.content

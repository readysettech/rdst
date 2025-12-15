"""
Agent Tools - Tool implementations for the Ask3 agent.

These tools allow the agent to iteratively explore the schema, sample data,
validate queries, and interact with the user to find the correct SQL.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from .agent_context import AgentExplorationContext
    from .presenter import Ask3Presenter
    from .types import SchemaInfo

logger = logging.getLogger(__name__)


# Tool definitions for LLM (Anthropic Claude format)
# https://docs.anthropic.com/en/docs/build-with-claude/tool-use
AGENT_TOOL_DEFINITIONS = [
    {
        "name": "explore_schema",
        "description": "Search for tables and columns matching a pattern. Use this to discover relevant tables for your query.",
        "input_schema": {
            "type": "object",
            "properties": {
                "table_pattern": {
                    "type": "string",
                    "description": "Keyword or pattern to match table/column names (e.g., 'vote', 'user', 'order')"
                }
            },
            "required": ["table_pattern"]
        }
    },
    {
        "name": "sample_table_data",
        "description": "Get sample rows from a table to understand column semantics. Use this BEFORE assuming what a column contains.",
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "Name of the table to sample"
                },
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific columns to include (empty for all)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of rows to sample (max 10)"
                }
            },
            "required": ["table_name"]
        }
    },
    {
        "name": "validate_sql_approach",
        "description": "Validate SQL syntax and check if referenced tables/columns exist in the schema.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SQL query to validate"
                }
            },
            "required": ["sql"]
        }
    },
    {
        "name": "execute_query",
        "description": "Execute a validated SQL query and return results. Only use after validation passes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SQL query to execute"
                }
            },
            "required": ["sql"]
        }
    },
    {
        "name": "ask_clarification",
        "description": "Pause and ask the user a clarifying question when genuinely ambiguous.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user"
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-4 suggested answers"
                }
            },
            "required": ["question", "options"]
        }
    },
    {
        "name": "submit_final_query",
        "description": "Submit the final SQL query that answers the user's question. Use when confident in the result.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The final SQL query"
                },
                "explanation": {
                    "type": "string",
                    "description": "Brief explanation of how this answers the question"
                }
            },
            "required": ["sql", "explanation"]
        }
    }
]


class AgentToolExecutor:
    """
    Executes agent tools with access to schema, database, and presenter.

    Each tool method returns a string result that gets sent back to the LLM.
    """

    def __init__(
        self,
        schema_info: Optional['SchemaInfo'],
        target_config: Dict[str, Any],
        db_type: str,
        presenter: 'Ask3Presenter',
        timeout_seconds: int = 30
    ):
        self.schema_info = schema_info
        self.target_config = target_config
        self.db_type = db_type
        self.presenter = presenter
        self.timeout_seconds = timeout_seconds

        # Tool dispatch table
        self._tools: Dict[str, Callable] = {
            'explore_schema': self._explore_schema,
            'sample_table_data': self._sample_table_data,
            'validate_sql_approach': self._validate_sql_approach,
            'execute_query': self._execute_query,
            'ask_clarification': self._ask_clarification,
            'submit_final_query': self._submit_final_query,
        }

    def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        ctx: 'AgentExplorationContext'
    ) -> str:
        """
        Execute a tool and return the result.

        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            ctx: Agent context (updated by tool)

        Returns:
            String result for LLM
        """
        if tool_name not in self._tools:
            return f"Error: Unknown tool '{tool_name}'"

        try:
            ctx.increment_tool_call()
            result = self._tools[tool_name](arguments, ctx)
            return result
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}")
            return f"Error executing {tool_name}: {str(e)}"

    def _explore_schema(
        self,
        arguments: Dict[str, Any],
        ctx: 'AgentExplorationContext'
    ) -> str:
        """Search for tables/columns matching a pattern."""
        pattern = arguments.get('table_pattern', '').lower()

        if not pattern:
            return "Error: table_pattern is required"

        if not self.schema_info or not self.schema_info.tables:
            return "Error: No schema information available"

        matches = []

        for table_name, table_info in self.schema_info.tables.items():
            table_lower = table_name.lower()

            # Check if pattern matches table name
            table_match = pattern in table_lower

            # Check columns
            matching_columns = []
            for col_name, col_info in table_info.columns.items():
                if pattern in col_name.lower():
                    col_desc = f"  - {col_name} ({col_info.data_type})"
                    if col_info.description:
                        col_desc += f": {col_info.description}"
                    matching_columns.append(col_desc)

            if table_match or matching_columns:
                table_desc = f"Table: {table_name}"
                if table_info.description:
                    table_desc += f" -- {table_info.description}"

                if table_match:
                    # Show all columns for matching tables
                    cols = []
                    for col_name, col_info in table_info.columns.items():
                        col_desc = f"  - {col_name} ({col_info.data_type})"
                        if col_info.description:
                            col_desc += f": {col_info.description}"
                        cols.append(col_desc)
                    matches.append(table_desc + "\n" + "\n".join(cols))
                else:
                    # Show only matching columns
                    matches.append(table_desc + "\n" + "\n".join(matching_columns))

                ctx.record_table_exploration(table_name)

        if not matches:
            return f"No tables or columns found matching '{pattern}'"

        return f"Found {len(matches)} matching tables:\n\n" + "\n\n".join(matches)

    def _sample_table_data(
        self,
        arguments: Dict[str, Any],
        ctx: 'AgentExplorationContext'
    ) -> str:
        """Get sample rows from a table."""
        table_name = arguments.get('table_name', '')
        columns = arguments.get('columns', [])
        limit = min(arguments.get('limit', 5), 10)  # Max 10 rows

        if not table_name:
            return "Error: table_name is required"

        # Verify table exists
        if self.schema_info:
            valid_tables = {t.lower(): t for t in self.schema_info.tables.keys()}
            if table_name.lower() not in valid_tables:
                return f"Error: Table '{table_name}' not found in schema"
            table_name = valid_tables[table_name.lower()]

        # Build sample query
        if columns:
            # Sanitize column names
            safe_cols = [c for c in columns if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', c)]
            col_list = ', '.join(safe_cols) if safe_cols else '*'
        else:
            col_list = '*'

        sql = f"SELECT {col_list} FROM {table_name} LIMIT {limit}"

        # Execute
        result = self._run_query(sql)

        if result.get('error'):
            return f"Error sampling {table_name}: {result['error']}"

        rows = result.get('rows', [])
        cols = result.get('columns', [])

        if not rows:
            return f"Table '{table_name}' is empty (0 rows)"

        # Format as table
        sample_data = []
        for row in rows:
            row_dict = dict(zip(cols, row))
            sample_data.append(row_dict)

        ctx.record_table_sample(table_name, sample_data)

        # Build output
        lines = [f"Sample data from {table_name} ({len(rows)} rows):"]
        lines.append("Columns: " + ", ".join(cols))
        lines.append("")

        for i, row_dict in enumerate(sample_data, 1):
            row_str = ", ".join(f"{k}={repr(v)}" for k, v in row_dict.items())
            lines.append(f"  Row {i}: {row_str}")

        return "\n".join(lines)

    def _validate_sql_approach(
        self,
        arguments: Dict[str, Any],
        ctx: 'AgentExplorationContext'
    ) -> str:
        """Validate SQL syntax and column references."""
        sql = arguments.get('sql', '')

        if not sql:
            return "Error: sql is required"

        # Import validation functions
        from ...functions.sql_validation import (
            validate_sql_for_ask,
            validate_columns_against_schema,
        )

        # Step 1: Read-only and LIMIT check
        validation_result = validate_sql_for_ask(
            sql=sql,
            max_limit=1000,
            default_limit=100
        )

        if not validation_result.get('is_valid'):
            issues = validation_result.get('issues', [])
            return "Validation FAILED:\n" + "\n".join(f"- {i}" for i in issues)

        validated_sql = validation_result.get('validated_sql', sql)

        # Step 2: Column validation
        if self.schema_info:
            schema_dict = {
                table_name: list(table.columns.keys())
                for table_name, table in self.schema_info.tables.items()
            }

            column_validation = validate_columns_against_schema(validated_sql, schema_dict)

            if not column_validation.get('is_valid', True):
                invalid_cols = column_validation.get('invalid_columns', [])
                issues = []
                for col_info in invalid_cols:
                    msg = f"- {col_info.get('column', '?')}: {col_info.get('error', 'not found')}"
                    suggestions = col_info.get('suggestions', [])
                    if suggestions:
                        msg += f" (suggestions: {', '.join(suggestions[:3])})"
                    issues.append(msg)
                return "Validation FAILED (invalid columns):\n" + "\n".join(issues)

        warnings = validation_result.get('warnings', [])
        if warnings:
            return f"Validation PASSED with warnings:\n" + "\n".join(f"- {w}" for w in warnings) + f"\n\nValidated SQL:\n{validated_sql}"

        return f"Validation PASSED\n\nValidated SQL:\n{validated_sql}"

    def _execute_query(
        self,
        arguments: Dict[str, Any],
        ctx: 'AgentExplorationContext'
    ) -> str:
        """Execute a SQL query."""
        sql = arguments.get('sql', '')

        if not sql:
            return "Error: sql is required"

        result = self._run_query(sql)

        if result.get('error'):
            ctx.record_query_attempt(sql, error=result['error'])
            return f"Query FAILED:\n{result['error']}"

        rows = result.get('rows', [])
        cols = result.get('columns', [])
        row_count = len(rows)

        # Record attempt
        sample_data = []
        if rows:
            for row in rows[:5]:
                sample_data.append(dict(zip(cols, row)))

        ctx.record_query_attempt(
            sql=sql,
            result_rows=row_count,
            columns=cols,
            sample_data=sample_data
        )

        if row_count == 0:
            return "Query executed successfully but returned 0 rows.\n\nThis might indicate:\n- The filter conditions are too restrictive\n- The data you're looking for doesn't exist\n- There's a semantic mismatch (e.g., using wrong column)"

        # Format output
        lines = [f"Query returned {row_count} rows"]
        lines.append(f"Columns: {', '.join(cols)}")
        lines.append("")
        lines.append("Sample rows (first 5):")

        for i, row in enumerate(rows[:5], 1):
            row_dict = dict(zip(cols, row))
            row_str = ", ".join(f"{k}={repr(v)}" for k, v in row_dict.items())
            lines.append(f"  {i}. {row_str}")

        if row_count > 5:
            lines.append(f"  ... ({row_count - 5} more rows)")

        return "\n".join(lines)

    def _ask_clarification(
        self,
        arguments: Dict[str, Any],
        ctx: 'AgentExplorationContext'
    ) -> str:
        """Ask user a clarifying question."""
        question = arguments.get('question', '')
        options = arguments.get('options', [])

        if not question:
            return "Error: question is required"

        if len(options) < 2:
            options = ["Yes", "No"]

        # Display question to user
        self.presenter._print(f"\n[bold yellow]Agent needs clarification:[/bold yellow]" if self.presenter.use_rich
                             else "\nAgent needs clarification:")
        self.presenter._print(f"\n{question}")
        self.presenter._print("")

        for i, opt in enumerate(options, 1):
            self.presenter._print(f"  [{i}] {opt}")
        self.presenter._print(f"  [{len(options) + 1}] Other (type your answer)")
        self.presenter._print("")

        # Get user input
        try:
            choice = input("Your choice: ").strip()

            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(options):
                    answer = options[idx]
                elif idx == len(options):
                    answer = input("Your answer: ").strip()
                else:
                    answer = choice
            else:
                answer = choice

            ctx.record_clarification(question, answer)
            return f"User response: {answer}"

        except (EOFError, KeyboardInterrupt):
            return "User cancelled clarification"

    def _submit_final_query(
        self,
        arguments: Dict[str, Any],
        ctx: 'AgentExplorationContext'
    ) -> str:
        """Submit the final query as the solution."""
        sql = arguments.get('sql', '')
        explanation = arguments.get('explanation', '')

        if not sql:
            return "Error: sql is required"

        ctx.final_sql = sql
        ctx.final_explanation = explanation

        return f"FINAL_QUERY_SUBMITTED"

    def _run_query(self, sql: str) -> Dict[str, Any]:
        """Execute a query against the database."""
        from .phases.execute import _execute_postgres, _execute_mysql
        from .types import DbType

        if not self.target_config:
            return {'error': 'No database connection configured', 'rows': [], 'columns': []}

        try:
            db_type = self.db_type or self.target_config.get('engine', 'postgresql').lower()

            if db_type == DbType.POSTGRESQL or 'postgres' in db_type:
                return _execute_postgres(sql, self.target_config, self.timeout_seconds)
            elif db_type == DbType.MYSQL or 'mysql' in db_type:
                return _execute_mysql(sql, self.target_config, self.timeout_seconds)
            else:
                return {'error': f'Unsupported database type: {db_type}', 'rows': [], 'columns': []}

        except Exception as e:
            return {'error': str(e), 'rows': [], 'columns': []}


def get_tool_definitions() -> List[Dict[str, Any]]:
    """Get tool definitions for LLM."""
    return AGENT_TOOL_DEFINITIONS

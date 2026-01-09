"""
Result Display and Formatting for RDST Ask Command

Handles:
- Table formatting with Rich (fallback to plain text)
- Quick statistics computation (min/max/avg/count)
- Next actions menu generation
- Result insights (optional LLM-powered)
"""

from typing import Dict, Any, List, Optional, Tuple
from decimal import Decimal
from datetime import datetime, date

from rich.console import Group

# Import UI system
from lib.ui import (
    DataTable,
    Icons,
    KeyValueTable,
    Layout,
    NextSteps,
    QueryPanel,
    StyleTokens,
    get_console,
)


def format_query_results(
    rows: List[Tuple],
    columns: List[str],
    execution_time_ms: float,
    max_rows_display: int = 50,
    **kwargs,
) -> Dict[str, Any]:
    """
    Format query results for display with Rich tables.

    Args:
        rows: List of result tuples
        columns: List of column names
        execution_time_ms: Query execution time in milliseconds
        max_rows_display: Maximum rows to display
        **kwargs: Additional parameters

    Returns:
        Dict containing:
        - formatted_output: String representation of results
        - row_count: Number of rows returned
        - column_count: Number of columns
        - truncated: Whether output was truncated
        - display_method: 'rich'
    """
    row_count = len(rows)
    column_count = len(columns)
    truncated = row_count > max_rows_display

    output = _format_with_rich(
        rows[:max_rows_display],
        columns,
        execution_time_ms,
        total_rows=row_count,
        truncated=truncated,
    )

    return {
        "formatted_output": output,
        "row_count": row_count,
        "column_count": column_count,
        "truncated": truncated,
        "display_method": "rich",
    }


def _format_with_rich(
    rows: List[Tuple],
    columns: List[str],
    execution_time_ms: float,
    total_rows: int,
    truncated: bool,
) -> str:
    """Format results using Rich library for beautiful terminal output."""
    c = get_console()

    # Create Rich table with consistent styling
    table = DataTable(
        columns=columns,
        rows=rows,
        title=f"Query Results ({total_rows} rows, {execution_time_ms:.2f}ms)",
    )

    # Capture table output
    with c.capture() as capture:
        c.print(table)
        if truncated:
            c.print(
                f"\n[{StyleTokens.WARNING}]Note: Showing first {len(rows)} of {total_rows} rows[/{StyleTokens.WARNING}]"
            )

    return capture.get()


def _format_plain_text(
    rows: List[Tuple],
    columns: List[str],
    execution_time_ms: float,
    total_rows: int,
    truncated: bool,
) -> str:
    """Format results as plain text table (fallback when Rich not available)."""
    output_lines = []

    # Calculate column widths
    col_widths = [len(col) for col in columns]
    for row in rows:
        for i, val in enumerate(row):
            val_str = _format_value(val)
            col_widths[i] = max(col_widths[i], len(val_str))

    # Header
    header = " | ".join(col.ljust(col_widths[i]) for i, col in enumerate(columns))
    separator = "-+-".join("-" * width for width in col_widths)

    output_lines.append(
        f"\nQuery Results ({total_rows} rows, {execution_time_ms:.2f}ms)"
    )
    output_lines.append(header)
    output_lines.append(separator)

    # Rows
    for row in rows:
        row_str = " | ".join(
            _format_value(val).ljust(col_widths[i]) for i, val in enumerate(row)
        )
        output_lines.append(row_str)

    if truncated:
        output_lines.append(f"\nNote: Showing first {len(rows)} of {total_rows} rows")

    return "\n".join(output_lines)


def _format_value(val: Any) -> str:
    """Format a single value for display."""
    if val is None:
        return "NULL"
    elif isinstance(val, (datetime, date)):
        return val.isoformat()
    elif isinstance(val, Decimal):
        return str(val)
    elif isinstance(val, (bytes, bytearray)):
        return f"<binary: {len(val)} bytes>"
    elif isinstance(val, str) and len(val) > 100:
        return val[:97] + "..."
    else:
        return str(val)


def compute_quick_stats(
    rows: List[Tuple], columns: List[str], **kwargs
) -> Dict[str, Any]:
    """
    Compute quick statistics on numeric columns.

    Args:
        rows: Query result rows
        columns: Column names
        **kwargs: Additional parameters

    Returns:
        Dict containing statistics for each numeric column
    """
    if not rows:
        return {"row_count": 0, "column_count": len(columns), "stats_by_column": {}}

    column_count = len(columns)
    stats_by_column = {}

    # Analyze each column
    for col_idx, col_name in enumerate(columns):
        col_values = [row[col_idx] for row in rows if row[col_idx] is not None]

        if not col_values:
            continue

        # Check if column is numeric
        first_val = col_values[0]
        if isinstance(first_val, (int, float, Decimal)):
            numeric_values = [float(v) for v in col_values]

            stats_by_column[col_name] = {
                "type": "numeric",
                "count": len(numeric_values),
                "min": min(numeric_values),
                "max": max(numeric_values),
                "avg": sum(numeric_values) / len(numeric_values),
                "null_count": len(rows) - len(col_values),
            }
        elif isinstance(first_val, str):
            stats_by_column[col_name] = {
                "type": "string",
                "count": len(col_values),
                "unique_count": len(set(col_values)),
                "null_count": len(rows) - len(col_values),
                "max_length": max(len(v) for v in col_values),
                "avg_length": sum(len(v) for v in col_values) / len(col_values),
            }
        elif isinstance(first_val, (datetime, date)):
            stats_by_column[col_name] = {
                "type": "datetime",
                "count": len(col_values),
                "min": min(col_values),
                "max": max(col_values),
                "null_count": len(rows) - len(col_values),
            }

    return {
        "row_count": len(rows),
        "column_count": column_count,
        "stats_by_column": stats_by_column,
    }


def format_quick_stats(stats: Dict[str, Any]) -> str:
    """
    Format statistics for display.

    Args:
        stats: Statistics dict from compute_quick_stats

    Returns:
        Formatted statistics string
    """
    if stats["row_count"] == 0:
        return "No rows returned"

    summary_table = KeyValueTable(
        {
            "Total Rows": str(stats["row_count"]),
            "Columns": str(stats["column_count"]),
        },
        title="Quick Statistics",
    )

    stats_by_col = stats.get("stats_by_column", {})
    column_rows = []
    if stats_by_col:
        for col_name, col_stats in stats_by_col.items():
            col_type = col_stats.get("type", "unknown")
            if col_type == "numeric":
                details = (
                    f"min={col_stats['min']:.2f}, max={col_stats['max']:.2f}, "
                    f"avg={col_stats['avg']:.2f}"
                )
                if col_stats["null_count"] > 0:
                    details += f", nulls={col_stats['null_count']}"
            elif col_type == "string":
                details = f"unique={col_stats['unique_count']}, max_len={col_stats['max_length']}"
                if col_stats["null_count"] > 0:
                    details += f", nulls={col_stats['null_count']}"
            elif col_type == "datetime":
                details = f"earliest={col_stats['min']}, latest={col_stats['max']}"
            else:
                details = ""
            column_rows.append((col_name, col_type, details))

    renderables = [summary_table]
    if column_rows:
        renderables.append(
            DataTable(
                columns=["Column", "Type", "Details"],
                rows=column_rows,
                title="Column Statistics",
            )
        )

    console = get_console()
    with console.capture() as capture:
        console.print(Group(*renderables))
    return capture.get().rstrip()


def generate_next_actions_menu(
    query_hash: str, has_results: bool, confidence: float, **kwargs
) -> Dict[str, Any]:
    """
    Generate context-aware next actions menu for ask command.

    Args:
        query_hash: Hash of the generated/executed query
        has_results: Whether query returned any results
        confidence: Confidence score of SQL generation (0.0-1.0)
        **kwargs: Additional context

    Returns:
        Dict containing:
        - actions: List of action dicts with name, description, command
        - formatted_menu: Formatted menu string
    """
    actions = []

    # Always offer refinement if confidence is not perfect
    if confidence < 1.0:
        actions.append(
            {
                "key": "1",
                "name": "Refine Query",
                "description": "Modify the SQL or ask a refined question",
                "command": "refine",
            }
        )

    # Offer to analyze performance if we have results
    if has_results:
        actions.append(
            {
                "key": "2",
                "name": "Analyze Performance",
                "description": f"Run rdst analyze on this query (hash: {query_hash[:8]})",
                "command": f"analyze --hash {query_hash}",
            }
        )

    # Offer to test caching if we have results
    if has_results:
        actions.append(
            {
                "key": "3",
                "name": "Test Caching",
                "description": "Check if this query can be cached with Readyset",
                "command": f"cache --hash {query_hash}",
            }
        )

    # Always offer to save
    actions.append(
        {
            "key": "4",
            "name": "Save Query",
            "description": "Save this query to registry with a name",
            "command": "save",
        }
    )

    # Offer to ask another question
    actions.append(
        {
            "key": "5",
            "name": "Ask Another Question",
            "description": "Start a new natural language query",
            "command": "ask_again",
        }
    )

    # Quit option
    actions.append(
        {"key": "q", "name": "Quit", "description": "Exit rdst ask", "command": "quit"}
    )

    steps = [
        (f"[{action['key']}] {action['name']}", action["description"])
        for action in actions
    ]
    formatted_menu = str(NextSteps(steps, title="What would you like to do next?"))

    return {"actions": actions, "formatted_menu": formatted_menu}


def display_sql_preview(
    sql: str,
    explanation: str,
    confidence: float,
    warnings: Optional[List[str]] = None,
    **kwargs,
) -> str:
    """
    Display SQL preview before execution with explanation.

    Args:
        sql: Generated SQL query
        explanation: Plain English explanation
        confidence: Confidence score (0.0-1.0)
        warnings: List of warning messages
        **kwargs: Additional parameters

    Returns:
        Formatted preview string
    """
    warnings = warnings or []

    c = get_console()
    with c.capture() as capture:
        # SQL panel using UI component
        c.print(QueryPanel(sql, title="Generated SQL"))

        # Explanation
        c.print(f"\n[bold]Explanation:[/bold] {explanation}")

        # Confidence with color from theme
        confidence_pct = confidence * 100
        if confidence >= 0.9:
            conf_text = (
                f"[{StyleTokens.SUCCESS}]{confidence_pct:.1f}%[/{StyleTokens.SUCCESS}]"
            )
        elif confidence >= 0.7:
            conf_text = (
                f"[{StyleTokens.WARNING}]{confidence_pct:.1f}%[/{StyleTokens.WARNING}]"
            )
        else:
            conf_text = (
                f"[{StyleTokens.ERROR}]{confidence_pct:.1f}%[/{StyleTokens.ERROR}]"
            )

        c.print(
            f"\n[{StyleTokens.HEADER}]Confidence:[/{StyleTokens.HEADER}] {conf_text}"
        )

        # Warnings
        if warnings:
            c.print(
                f"\n[{StyleTokens.STATUS_WARNING}]Warnings:[/{StyleTokens.STATUS_WARNING}]"
            )
            for warning in warnings:
                c.print(f"  {Icons.WARNING} {warning}")

    return capture.get()

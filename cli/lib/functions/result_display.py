"""
Result Display and Formatting for RDST Ask Command

Handles:
- Table formatting with Rich (fallback to plain text)
- Quick statistics computation (min/max/avg/count)
- Next actions menu generation
- Result insights (optional LLM-powered)
"""

from typing import Dict, Any, List, Tuple, Optional
from decimal import Decimal
from datetime import datetime, date
import sys

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False


def format_query_results(
    rows: List[Tuple],
    columns: List[str],
    execution_time_ms: float,
    use_rich: bool = True,
    max_rows_display: int = 50,
    **kwargs
) -> Dict[str, Any]:
    """
    Format query results for display with Rich tables or plain text fallback.

    Args:
        rows: List of result tuples
        columns: List of column names
        execution_time_ms: Query execution time in milliseconds
        use_rich: Whether to use Rich formatting
        max_rows_display: Maximum rows to display
        **kwargs: Additional parameters

    Returns:
        Dict containing:
        - formatted_output: String representation of results
        - row_count: Number of rows returned
        - column_count: Number of columns
        - truncated: Whether output was truncated
        - display_method: 'rich' or 'plain'
    """
    row_count = len(rows)
    column_count = len(columns)
    truncated = row_count > max_rows_display

    if use_rich and _RICH_AVAILABLE:
        output = _format_with_rich(
            rows[:max_rows_display],
            columns,
            execution_time_ms,
            total_rows=row_count,
            truncated=truncated
        )
        display_method = 'rich'
    else:
        output = _format_plain_text(
            rows[:max_rows_display],
            columns,
            execution_time_ms,
            total_rows=row_count,
            truncated=truncated
        )
        display_method = 'plain'

    return {
        'formatted_output': output,
        'row_count': row_count,
        'column_count': column_count,
        'truncated': truncated,
        'display_method': display_method
    }


def _format_with_rich(
    rows: List[Tuple],
    columns: List[str],
    execution_time_ms: float,
    total_rows: int,
    truncated: bool
) -> str:
    """Format results using Rich library for beautiful terminal output."""
    console = Console()

    # Create Rich table
    table = Table(title=f"Query Results ({total_rows} rows, {execution_time_ms:.2f}ms)")

    # Add columns
    for col in columns:
        table.add_column(col, style="cyan", no_wrap=False)

    # Add rows
    for row in rows:
        # Convert values to strings, handling None and special types
        str_row = [_format_value(val) for val in row]
        table.add_row(*str_row)

    # Capture table output
    with console.capture() as capture:
        console.print(table)
        if truncated:
            console.print(f"\n[yellow]Note: Showing first {len(rows)} of {total_rows} rows[/yellow]")

    return capture.get()


def _format_plain_text(
    rows: List[Tuple],
    columns: List[str],
    execution_time_ms: float,
    total_rows: int,
    truncated: bool
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

    output_lines.append(f"\nQuery Results ({total_rows} rows, {execution_time_ms:.2f}ms)")
    output_lines.append(header)
    output_lines.append(separator)

    # Rows
    for row in rows:
        row_str = " | ".join(
            _format_value(val).ljust(col_widths[i])
            for i, val in enumerate(row)
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
    rows: List[Tuple],
    columns: List[str],
    **kwargs
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
        return {
            'row_count': 0,
            'column_count': len(columns),
            'stats_by_column': {}
        }

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
                'type': 'numeric',
                'count': len(numeric_values),
                'min': min(numeric_values),
                'max': max(numeric_values),
                'avg': sum(numeric_values) / len(numeric_values),
                'null_count': len(rows) - len(col_values)
            }
        elif isinstance(first_val, str):
            stats_by_column[col_name] = {
                'type': 'string',
                'count': len(col_values),
                'unique_count': len(set(col_values)),
                'null_count': len(rows) - len(col_values),
                'max_length': max(len(v) for v in col_values),
                'avg_length': sum(len(v) for v in col_values) / len(col_values)
            }
        elif isinstance(first_val, (datetime, date)):
            stats_by_column[col_name] = {
                'type': 'datetime',
                'count': len(col_values),
                'min': min(col_values),
                'max': max(col_values),
                'null_count': len(rows) - len(col_values)
            }

    return {
        'row_count': len(rows),
        'column_count': column_count,
        'stats_by_column': stats_by_column
    }


def format_quick_stats(stats: Dict[str, Any], use_rich: bool = True) -> str:
    """
    Format statistics for display.

    Args:
        stats: Statistics dict from compute_quick_stats
        use_rich: Whether to use Rich formatting

    Returns:
        Formatted statistics string
    """
    if stats['row_count'] == 0:
        return "No rows returned"

    output_lines = []
    output_lines.append(f"\nQuick Statistics:")
    output_lines.append(f"  Total Rows: {stats['row_count']}")
    output_lines.append(f"  Columns: {stats['column_count']}")

    stats_by_col = stats.get('stats_by_column', {})
    if stats_by_col:
        output_lines.append("\n  Column Statistics:")
        for col_name, col_stats in stats_by_col.items():
            col_type = col_stats.get('type', 'unknown')

            if col_type == 'numeric':
                output_lines.append(f"    {col_name} (numeric):")
                output_lines.append(f"      Min: {col_stats['min']:.2f}")
                output_lines.append(f"      Max: {col_stats['max']:.2f}")
                output_lines.append(f"      Avg: {col_stats['avg']:.2f}")
                if col_stats['null_count'] > 0:
                    output_lines.append(f"      Nulls: {col_stats['null_count']}")

            elif col_type == 'string':
                output_lines.append(f"    {col_name} (string):")
                output_lines.append(f"      Unique: {col_stats['unique_count']}")
                output_lines.append(f"      Max Length: {col_stats['max_length']}")
                if col_stats['null_count'] > 0:
                    output_lines.append(f"      Nulls: {col_stats['null_count']}")

            elif col_type == 'datetime':
                output_lines.append(f"    {col_name} (datetime):")
                output_lines.append(f"      Earliest: {col_stats['min']}")
                output_lines.append(f"      Latest: {col_stats['max']}")

    return "\n".join(output_lines)


def generate_next_actions_menu(
    query_hash: str,
    has_results: bool,
    confidence: float,
    **kwargs
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
        actions.append({
            'key': '1',
            'name': 'Refine Query',
            'description': 'Modify the SQL or ask a refined question',
            'command': 'refine'
        })

    # Offer to analyze performance if we have results
    if has_results:
        actions.append({
            'key': '2',
            'name': 'Analyze Performance',
            'description': f'Run rdst analyze on this query (hash: {query_hash[:8]})',
            'command': f'analyze --hash {query_hash}'
        })

    # Offer to test caching if we have results
    if has_results:
        actions.append({
            'key': '3',
            'name': 'Test Caching',
            'description': f'Check if this query can be cached with ReadySet',
            'command': f'cache --hash {query_hash}'
        })

    # Always offer to save
    actions.append({
        'key': '4',
        'name': 'Save Query',
        'description': 'Save this query to registry with a name',
        'command': 'save'
    })

    # Offer to ask another question
    actions.append({
        'key': '5',
        'name': 'Ask Another Question',
        'description': 'Start a new natural language query',
        'command': 'ask_again'
    })

    # Quit option
    actions.append({
        'key': 'q',
        'name': 'Quit',
        'description': 'Exit rdst ask',
        'command': 'quit'
    })

    # Format menu
    formatted_lines = ["\nWhat would you like to do next?"]
    for action in actions:
        formatted_lines.append(f"  [{action['key']}] {action['name']}: {action['description']}")

    return {
        'actions': actions,
        'formatted_menu': "\n".join(formatted_lines)
    }


def display_sql_preview(
    sql: str,
    explanation: str,
    confidence: float,
    warnings: List[str] = None,
    use_rich: bool = True,
    **kwargs
) -> str:
    """
    Display SQL preview before execution with explanation.

    Args:
        sql: Generated SQL query
        explanation: Plain English explanation
        confidence: Confidence score (0.0-1.0)
        warnings: List of warning messages
        use_rich: Whether to use Rich formatting
        **kwargs: Additional parameters

    Returns:
        Formatted preview string
    """
    warnings = warnings or []

    if use_rich and _RICH_AVAILABLE:
        console = Console()
        with console.capture() as capture:
            # SQL panel
            console.print(Panel(sql, title="Generated SQL", border_style="cyan"))

            # Explanation
            console.print(f"\n[bold]Explanation:[/bold] {explanation}")

            # Confidence
            confidence_pct = confidence * 100
            if confidence >= 0.9:
                color = "green"
            elif confidence >= 0.7:
                color = "yellow"
            else:
                color = "red"

            console.print(f"\n[bold]Confidence:[/bold] [{color}]{confidence_pct:.1f}%[/{color}]")

            # Warnings
            if warnings:
                console.print("\n[bold yellow]Warnings:[/bold yellow]")
                for warning in warnings:
                    console.print(f"  ⚠️  {warning}")

        return capture.get()

    else:
        # Plain text fallback
        output_lines = []
        output_lines.append("\n" + "="*60)
        output_lines.append("Generated SQL:")
        output_lines.append("-"*60)
        output_lines.append(sql)
        output_lines.append("-"*60)
        output_lines.append(f"\nExplanation: {explanation}")
        output_lines.append(f"Confidence: {confidence*100:.1f}%")

        if warnings:
            output_lines.append("\nWarnings:")
            for warning in warnings:
                output_lines.append(f"  - {warning}")

        output_lines.append("="*60)

        return "\n".join(output_lines)

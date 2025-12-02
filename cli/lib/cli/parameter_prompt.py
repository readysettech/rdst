"""
Interactive Parameter Prompting for Parameterized Queries

When a query contains placeholders ($1, $2 for PostgreSQL or ? for MySQL)
without stored parameter values, this module prompts the user to provide
example values so analysis can proceed.
"""

import re
from typing import Dict, List, Tuple, Optional, Any

# Rich imports for beautiful formatting
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.table import Table
    from rich.prompt import Prompt
    from rich.text import Text
    from rich import box
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False
    Console = None


def detect_placeholders(sql: str) -> List[Tuple[str, int]]:
    """
    Detect parameter placeholders in SQL query.

    Args:
        sql: SQL query string

    Returns:
        List of (placeholder, position) tuples, e.g. [('$1', 0), ('$2', 1)]
        Returns empty list if no placeholders found.
    """
    if not sql:
        return []

    placeholders = []

    # PostgreSQL-style: $1, $2, etc.
    pg_matches = re.finditer(r'\$(\d+)', sql)
    for match in pg_matches:
        param_num = int(match.group(1))
        placeholders.append((match.group(0), param_num - 1))  # 0-indexed

    # If no PostgreSQL placeholders, check for ? placeholders
    if not placeholders:
        # Remove string literals to avoid matching ? inside strings
        sql_no_strings = re.sub(r"'[^']*'", "''", sql)
        # Count ? outside of strings
        position = 0
        for i, char in enumerate(sql_no_strings):
            if char == '?':
                placeholders.append(('?', position))
                position += 1

    return placeholders


def infer_parameter_type(sql: str, placeholder: str, position: int) -> Tuple[str, str]:
    """
    Try to infer the expected type of a parameter from SQL context.

    Args:
        sql: Full SQL query
        placeholder: The placeholder string ($1, $2, or ?)
        position: 0-indexed position of placeholder

    Returns:
        Tuple of (inferred_type, example_hint)
        e.g. ('integer', '123') or ('string', "'example'")
    """
    # Find the context around the placeholder
    # Look for patterns like "column = $1" or "column > $1"

    # Common patterns that suggest integer
    int_patterns = [
        r'\bid\s*[=<>!]+\s*' + re.escape(placeholder),
        r'_id\s*[=<>!]+\s*' + re.escape(placeholder),
        r'\bLIMIT\s+' + re.escape(placeholder),
        r'\bOFFSET\s+' + re.escape(placeholder),
        r'\byear\s*[=<>!]+\s*' + re.escape(placeholder),
        r'\bcount\s*[=<>!]+\s*' + re.escape(placeholder),
        r'\bage\s*[=<>!]+\s*' + re.escape(placeholder),
        r'\bprice\s*[=<>!]+\s*' + re.escape(placeholder),
        r'\bquantity\s*[=<>!]+\s*' + re.escape(placeholder),
    ]

    # Common patterns that suggest string
    string_patterns = [
        r'\bname\s*[=<>!]+\s*' + re.escape(placeholder),
        r'\btitle\s*[=<>!]+\s*' + re.escape(placeholder),
        r'\bLIKE\s+' + re.escape(placeholder),
        r'\bemail\s*[=<>!]+\s*' + re.escape(placeholder),
        r'\bstatus\s*[=<>!]+\s*' + re.escape(placeholder),
        r'\btype\s*[=<>!]+\s*' + re.escape(placeholder),
    ]

    # Common patterns that suggest date
    date_patterns = [
        r'\bdate\s*[=<>!]+\s*' + re.escape(placeholder),
        r'\bcreated\s*[=<>!]+\s*' + re.escape(placeholder),
        r'\bupdated\s*[=<>!]+\s*' + re.escape(placeholder),
        r'\btimestamp\s*[=<>!]+\s*' + re.escape(placeholder),
    ]

    sql_lower = sql.lower()

    for pattern in int_patterns:
        if re.search(pattern, sql_lower, re.IGNORECASE):
            return ('integer', '123')

    for pattern in string_patterns:
        if re.search(pattern, sql_lower, re.IGNORECASE):
            return ('string', 'example')

    for pattern in date_patterns:
        if re.search(pattern, sql_lower, re.IGNORECASE):
            return ('date', '2024-01-15')

    # Default to string if we can't infer
    return ('unknown', 'value')


def validate_value(value: str, expected_type: str) -> Tuple[bool, str, Any]:
    """
    Validate and convert user input to appropriate type.

    Args:
        value: User-provided value string
        expected_type: Expected type ('integer', 'string', 'date', 'unknown')

    Returns:
        Tuple of (is_valid, error_message, converted_value)
    """
    value = value.strip()

    if not value:
        return (False, "Value cannot be empty", None)

    if expected_type == 'integer':
        try:
            converted = int(value)
            return (True, "", converted)
        except ValueError:
            # Allow it but warn - user might know better
            return (True, f"Note: '{value}' doesn't look like an integer, but proceeding anyway", value)

    elif expected_type == 'date':
        # Basic date format check
        if re.match(r'^\d{4}-\d{2}-\d{2}', value):
            return (True, "", value)
        else:
            return (True, f"Note: '{value}' doesn't look like a date (YYYY-MM-DD), but proceeding anyway", value)

    # For string and unknown, accept anything
    return (True, "", value)


def substitute_placeholders(sql: str, values: Dict[int, Any]) -> str:
    """
    Substitute parameter values back into SQL query.

    Args:
        sql: SQL with placeholders
        values: Dict mapping position (0-indexed) to value

    Returns:
        SQL with placeholders replaced by actual values
    """
    result = sql

    # Check if PostgreSQL-style ($1, $2) or MySQL-style (?)
    if re.search(r'\$\d+', sql):
        # PostgreSQL style - replace $N with values
        for position, value in sorted(values.items(), reverse=True):
            placeholder = f'${position + 1}'
            if isinstance(value, str) and not value.isdigit():
                # Quote strings
                quoted_value = "'" + value.replace("'", "''") + "'"
                result = result.replace(placeholder, quoted_value)
            else:
                result = result.replace(placeholder, str(value))
    else:
        # MySQL style - replace ? in order
        parts = []
        last_end = 0
        position = 0
        # Remove string literals temporarily
        sql_no_strings = re.sub(r"'[^']*'", lambda m: '\x00' * len(m.group()), sql)

        for i, char in enumerate(sql_no_strings):
            if char == '?':
                parts.append(sql[last_end:i])
                value = values.get(position, '?')
                if isinstance(value, str) and not str(value).isdigit():
                    parts.append("'" + str(value).replace("'", "''") + "'")
                else:
                    parts.append(str(value))
                last_end = i + 1
                position += 1

        parts.append(sql[last_end:])
        result = ''.join(parts)

    return result


def prompt_for_parameters(sql: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Interactive prompt for parameter values.

    Args:
        sql: SQL query with placeholders

    Returns:
        Tuple of (substituted_sql, parameters_dict) or None if user cancels
    """
    placeholders = detect_placeholders(sql)

    if not placeholders:
        return None

    if _RICH_AVAILABLE:
        return _prompt_for_parameters_rich(sql, placeholders)
    else:
        return _prompt_for_parameters_plain(sql, placeholders)


def _prompt_for_parameters_rich(sql: str, placeholders: List[Tuple[str, int]]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Rich-formatted parameter prompting."""
    console = Console()

    # Show the query in a nice panel with syntax highlighting
    console.print()
    syntax = Syntax(sql, "sql", theme="monokai", word_wrap=True)
    console.print(Panel(
        syntax,
        title="[bold cyan]Parameterized Query[/bold cyan]",
        subtitle=f"[dim]{len(placeholders)} parameter{'s' if len(placeholders) > 1 else ''} needed[/dim]",
        border_style="cyan",
        padding=(1, 2)
    ))

    # Brief explanation
    console.print(
        "[dim]This query has placeholders that need values before we can run EXPLAIN ANALYZE.[/dim]"
    )
    console.print()

    # Build a table showing what parameters are needed
    table = Table(
        show_header=True,
        header_style="bold white",
        box=box.ROUNDED,
        border_style="dim",
        padding=(0, 1)
    )
    table.add_column("Param", style="bold yellow", width=6)
    table.add_column("Usage", style="white")
    table.add_column("Type", style="dim cyan", width=10)

    for placeholder, position in placeholders:
        context = extract_placeholder_context(sql, placeholder)
        inferred_type, _ = infer_parameter_type(sql, placeholder, position)
        type_display = inferred_type if inferred_type != 'unknown' else '-'
        table.add_row(placeholder, context or "-", type_display)

    console.print(table)
    console.print()

    # Collect values
    values = {}
    param_dict = {}

    for placeholder, position in placeholders:
        inferred_type, example = infer_parameter_type(sql, placeholder, position)
        context = extract_placeholder_context(sql, placeholder)

        # Build the prompt
        prompt_parts = [f"[bold yellow]{placeholder}[/bold yellow]"]
        if context:
            prompt_parts.append(f"[dim]({context})[/dim]")

        prompt_text = " ".join(prompt_parts)

        # Add example hint
        if example:
            default_hint = f"[dim]e.g. {example}[/dim]"
        else:
            default_hint = ""

        while True:
            try:
                if default_hint:
                    console.print(f"  {prompt_text} {default_hint}")
                    user_input = Prompt.ask(f"  [bold cyan]>[/bold cyan]")
                else:
                    user_input = Prompt.ask(f"  {prompt_text}")

                if not user_input.strip():
                    console.print(f"    [red]Please enter a value[/red]")
                    continue

                is_valid, message, converted = validate_value(user_input, inferred_type)

                if message:
                    console.print(f"    [yellow]{message}[/yellow]")

                if is_valid:
                    values[position] = converted
                    param_dict[f'param_{position + 1}'] = converted
                    break

            except KeyboardInterrupt:
                console.print("\n[dim]Cancelled[/dim]")
                return None

    # Substitute values into SQL
    substituted_sql = substitute_placeholders(sql, values)

    # Show the final query
    console.print()
    syntax = Syntax(substituted_sql, "sql", theme="monokai", word_wrap=True)
    console.print(Panel(
        syntax,
        title="[bold green]Query Ready[/bold green]",
        border_style="green",
        padding=(1, 2)
    ))

    # Confirm
    try:
        confirm = Prompt.ask(
            "[bold]Proceed with analysis?[/bold]",
            choices=["y", "n"],
            default="y"
        )
        if confirm.lower() == 'y':
            return (substituted_sql, param_dict)
        else:
            console.print("[dim]Cancelled[/dim]")
            return None
    except KeyboardInterrupt:
        console.print("\n[dim]Cancelled[/dim]")
        return None


def _prompt_for_parameters_plain(sql: str, placeholders: List[Tuple[str, int]]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Plain text fallback for parameter prompting."""
    print("\n" + "-" * 60)
    print("Parameterized Query - Values Needed")
    print("-" * 60)
    print(f"\n  {sql[:200]}{'...' if len(sql) > 200 else ''}")
    print(f"\nFound {len(placeholders)} parameter(s). Please provide values.")
    print("(Ctrl+C to cancel)\n")

    values = {}
    param_dict = {}

    for placeholder, position in placeholders:
        inferred_type, example = infer_parameter_type(sql, placeholder, position)
        context = extract_placeholder_context(sql, placeholder)

        context_hint = f" ({context})" if context else ""
        type_hint = f" [{inferred_type}]" if inferred_type != 'unknown' else ""
        example_hint = f" e.g., {example}" if example else ""

        while True:
            try:
                prompt = f"  {placeholder}{context_hint}{type_hint}{example_hint}: "
                user_input = input(prompt)

                if not user_input.strip():
                    print(f"    Please enter a value for {placeholder}")
                    continue

                is_valid, message, converted = validate_value(user_input, inferred_type)

                if message:
                    print(f"    {message}")

                if is_valid:
                    values[position] = converted
                    param_dict[f'param_{position + 1}'] = converted
                    break

            except KeyboardInterrupt:
                print("\n\nCancelled.")
                return None

    substituted_sql = substitute_placeholders(sql, values)

    print("\n" + "-" * 60)
    print("Query with values:")
    print(f"\n  {substituted_sql[:300]}{'...' if len(substituted_sql) > 300 else ''}")
    print("-" * 60)

    try:
        confirm = input("\nProceed? [Y/n]: ").strip().lower()
        if confirm in ['', 'y', 'yes']:
            return (substituted_sql, param_dict)
        else:
            print("Cancelled.")
            return None
    except KeyboardInterrupt:
        print("\n\nCancelled.")
        return None


def extract_placeholder_context(sql: str, placeholder: str) -> str:
    """
    Extract the SQL context around a placeholder to show what it's used for.

    Args:
        sql: Full SQL query
        placeholder: The placeholder string ($1, $2, or ?)

    Returns:
        Context string like "tr.numVotes > $1" or "tb.titleType = $1"
    """
    import re

    # Find the position of this placeholder
    if placeholder == '?':
        # For ?, we need to find the nth occurrence - this is trickier
        # For now, just return generic context
        return ""

    # For PostgreSQL $N placeholders, find the context
    # Look for pattern: column_or_expression <operator> $N
    # or $N <operator> column_or_expression

    # Pattern to match: word.word or word followed by operator and placeholder
    # e.g., "tr.numVotes > $1" or "tb.titleType = $2"
    escaped_placeholder = re.escape(placeholder)

    # Try to match: identifier <op> $N
    pattern1 = r'(\w+(?:\.\w+)?)\s*([<>=!]+|LIKE|IN)\s*' + escaped_placeholder
    match1 = re.search(pattern1, sql, re.IGNORECASE)
    if match1:
        return f"{match1.group(1)} {match1.group(2)} {placeholder}"

    # Try to match: $N <op> identifier (less common but possible)
    pattern2 = escaped_placeholder + r'\s*([<>=!]+)\s*(\w+(?:\.\w+)?)'
    match2 = re.search(pattern2, sql, re.IGNORECASE)
    if match2:
        return f"{placeholder} {match2.group(1)} {match2.group(2)}"

    # Try to match LIMIT $N or OFFSET $N
    pattern3 = r'(LIMIT|OFFSET)\s+' + escaped_placeholder
    match3 = re.search(pattern3, sql, re.IGNORECASE)
    if match3:
        return f"{match3.group(1)} {placeholder}"

    # Try to match ORDER BY ... $N (for dynamic ordering)
    pattern4 = r'(ORDER\s+BY)\s+[^$]*' + escaped_placeholder
    match4 = re.search(pattern4, sql, re.IGNORECASE)
    if match4:
        return f"ORDER BY ... {placeholder}"

    return ""


def has_unresolved_placeholders(sql: str) -> bool:
    """
    Check if SQL contains unresolved parameter placeholders.

    Args:
        sql: SQL query string

    Returns:
        True if query has $1, $2, or ? placeholders
    """
    if not sql:
        return False

    # PostgreSQL-style: $1, $2, etc.
    if re.search(r'\$\d+', sql):
        return True

    # MySQL/generic style: ? (outside of string literals)
    sql_no_strings = re.sub(r"'[^']*'", '', sql)
    if '?' in sql_no_strings:
        return True

    return False

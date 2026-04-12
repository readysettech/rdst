"""
Interactive parameter prompting for parameterized queries.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from shared.ui import (
    Confirm,
    DataTable,
    Group,
    Layout,
    MessagePanel,
    Prompt,
    QueryPanel,
    SectionBox,
    StyleTokens,
    format_sql_for_display,
    get_console,
)


def detect_placeholders(sql: str) -> List[Tuple[str, int]]:
    if not sql:
        return []

    placeholders = []
    pg_matches = re.finditer(r"\$(\d+)", sql)
    for match in pg_matches:
        param_num = int(match.group(1))
        placeholders.append((match.group(0), param_num - 1))

    if not placeholders:
        sql_no_strings = re.sub(r"'[^']*'", "''", sql)
        position = 0
        for char in sql_no_strings:
            if char == "?":
                placeholders.append(("?", position))
                position += 1

    return placeholders


def infer_parameter_type(sql: str, placeholder: str, position: int) -> Tuple[str, str]:
    int_patterns = [
        r"\bid\s*[=<>!]+\s*" + re.escape(placeholder),
        r"_id\s*[=<>!]+\s*" + re.escape(placeholder),
        r"\bLIMIT\s+" + re.escape(placeholder),
        r"\bOFFSET\s+" + re.escape(placeholder),
        r"\byear\s*[=<>!]+\s*" + re.escape(placeholder),
        r"\bcount\s*[=<>!]+\s*" + re.escape(placeholder),
        r"\bage\s*[=<>!]+\s*" + re.escape(placeholder),
        r"\bprice\s*[=<>!]+\s*" + re.escape(placeholder),
        r"\bquantity\s*[=<>!]+\s*" + re.escape(placeholder),
    ]

    string_patterns = [
        r"\bname\s*[=<>!]+\s*" + re.escape(placeholder),
        r"\btitle\s*[=<>!]+\s*" + re.escape(placeholder),
        r"\bLIKE\s+" + re.escape(placeholder),
        r"\bemail\s*[=<>!]+\s*" + re.escape(placeholder),
        r"\bstatus\s*[=<>!]+\s*" + re.escape(placeholder),
        r"\btype\s*[=<>!]+\s*" + re.escape(placeholder),
    ]

    date_patterns = [
        r"\bdate\s*[=<>!]+\s*" + re.escape(placeholder),
        r"\bcreated\s*[=<>!]+\s*" + re.escape(placeholder),
        r"\bupdated\s*[=<>!]+\s*" + re.escape(placeholder),
        r"\btimestamp\s*[=<>!]+\s*" + re.escape(placeholder),
    ]

    sql_lower = sql.lower()
    for pattern in int_patterns:
        if re.search(pattern, sql_lower, re.IGNORECASE):
            return ("integer", "123")

    for pattern in string_patterns:
        if re.search(pattern, sql_lower, re.IGNORECASE):
            return ("string", "example")

    for pattern in date_patterns:
        if re.search(pattern, sql_lower, re.IGNORECASE):
            return ("date", "2024-01-15")

    return ("unknown", "value")


def validate_value(value: str, expected_type: str) -> Tuple[bool, str, Any]:
    value = value.strip()
    if not value:
        return (False, "Value cannot be empty", None)

    if expected_type == "integer":
        try:
            return (True, "", int(value))
        except ValueError:
            return (
                True,
                f"Note: '{value}' doesn't look like an integer, but proceeding anyway",
                value,
            )

    if expected_type == "date":
        if re.match(r"^\d{4}-\d{2}-\d{2}", value):
            return (True, "", value)
        return (
            True,
            f"Note: '{value}' doesn't look like a date (YYYY-MM-DD), but proceeding anyway",
            value,
        )

    return (True, "", value)


def substitute_placeholders(sql: str, values: Dict[int, Any]) -> str:
    result = sql

    if re.search(r"\$\d+", sql):
        for position, value in sorted(values.items(), reverse=True):
            placeholder = f"${position + 1}"
            if isinstance(value, str) and not value.isdigit():
                quoted_value = "'" + value.replace("'", "''") + "'"
                result = result.replace(placeholder, quoted_value)
            else:
                result = result.replace(placeholder, str(value))
    else:
        parts = []
        last_end = 0
        position = 0
        sql_no_strings = re.sub(r"'[^']*'", lambda m: "\x00" * len(m.group()), sql)

        for i, char in enumerate(sql_no_strings):
            if char == "?":
                parts.append(sql[last_end:i])
                value = values.get(position, "?")
                if isinstance(value, str) and not str(value).isdigit():
                    parts.append("'" + str(value).replace("'", "''") + "'")
                else:
                    parts.append(str(value))
                last_end = i + 1
                position += 1

        parts.append(sql[last_end:])
        result = "".join(parts)

    return result


def prompt_for_parameters(sql: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    placeholders = detect_placeholders(sql)
    if not placeholders:
        return None
    return _prompt_for_parameters_rich(sql, placeholders)


def _prompt_for_parameters_rich(
    sql: str,
    placeholders: List[Tuple[str, int]],
) -> Optional[Tuple[str, Dict[str, Any]]]:
    console = get_console()

    console.print()
    param_count = len(placeholders)
    query_panel = QueryPanel(
        sql,
        title="Parameterized Query",
        border_style=StyleTokens.SECONDARY,
    )
    subtitle = (
        f"[{StyleTokens.MUTED}]"
        f"{param_count} parameter{'s' if param_count > 1 else ''} needed"
        f"[/{StyleTokens.MUTED}]"
    )
    console.print(Group(query_panel, subtitle))
    console.print(
        f"[{StyleTokens.MUTED}]"
        "This query has placeholders that need values before we can run EXPLAIN ANALYZE."
        f"[/{StyleTokens.MUTED}]"
    )
    console.print()

    columns = ["Param", "Usage", "Type"]
    rows = []
    for placeholder, position in placeholders:
        context = extract_placeholder_context(sql, placeholder)
        inferred_type, _ = infer_parameter_type(sql, placeholder, position)
        type_display = inferred_type if inferred_type != "unknown" else "-"
        rows.append(
            (
                f"[{StyleTokens.WARNING}]{placeholder}[/{StyleTokens.WARNING}]",
                context or "-",
                f"[{StyleTokens.SECONDARY}]{type_display}[/{StyleTokens.SECONDARY}]",
            )
        )

    console.print(DataTable(columns, rows))
    console.print()

    values: Dict[int, Any] = {}
    param_dict: Dict[str, Any] = {}
    for placeholder, position in placeholders:
        inferred_type, example = infer_parameter_type(sql, placeholder, position)
        context = extract_placeholder_context(sql, placeholder)

        prompt_parts = [f"[{StyleTokens.WARNING}]{placeholder}[/{StyleTokens.WARNING}]"]
        if context:
            prompt_parts.append(f"[{StyleTokens.MUTED}]({context})[/{StyleTokens.MUTED}]")
        prompt_text = " ".join(prompt_parts)
        default_hint = (
            f"[{StyleTokens.MUTED}]e.g. {example}[/{StyleTokens.MUTED}]"
            if example
            else ""
        )

        while True:
            try:
                if default_hint:
                    console.print(f"  {prompt_text} {default_hint}")
                    user_input = Prompt.ask(
                        f"  [{StyleTokens.PROMPT}]>[/{StyleTokens.PROMPT}]"
                    )
                else:
                    user_input = Prompt.ask(f"  {prompt_text}")

                if not user_input.strip():
                    console.print(MessagePanel("Please enter a value", variant="warning"))
                    continue

                is_valid, message, converted = validate_value(user_input, inferred_type)
                if message:
                    console.print(
                        f"    [{StyleTokens.WARNING}]{message}[/{StyleTokens.WARNING}]"
                    )

                if is_valid:
                    values[position] = converted
                    param_dict[f"param_{position + 1}"] = converted
                    break
            except KeyboardInterrupt:
                console.print(MessagePanel("Cancelled", variant="warning"))
                return None

    substituted_sql = substitute_placeholders(sql, values)
    console.print()
    console.print(
        QueryPanel(
            substituted_sql,
            title="Query Ready",
            border_style=StyleTokens.SUCCESS,
        )
    )

    try:
        confirm = Prompt.ask(
            "[bold]Proceed with analysis?[/bold]",
            choices=["y", "n"],
            default="y",
        )
        if confirm.lower() == "y":
            return (substituted_sql, param_dict)
        console.print(MessagePanel("Cancelled", variant="warning"))
        return None
    except KeyboardInterrupt:
        console.print(MessagePanel("Cancelled", variant="warning"))
        return None


def _prompt_for_parameters_plain(
    sql: str,
    placeholders: List[Tuple[str, int]],
) -> Optional[Tuple[str, Dict[str, Any]]]:
    console = get_console()
    formatted_sql = format_sql_for_display(sql)
    sql_preview = formatted_sql[:200] + ("..." if len(formatted_sql) > 200 else "")
    console.print(
        SectionBox(
            title="Parameterized Query - Values Needed",
            content=sql_preview,
            subtitle=f"Found {len(placeholders)} parameter(s). Please provide values.",
            hint="Ctrl+C to cancel",
        )
    )

    values: Dict[int, Any] = {}
    param_dict: Dict[str, Any] = {}
    for placeholder, position in placeholders:
        inferred_type, example = infer_parameter_type(sql, placeholder, position)
        context = extract_placeholder_context(sql, placeholder)
        context_hint = f" ({context})" if context else ""
        type_hint = f" [{inferred_type}]" if inferred_type != "unknown" else ""
        example_hint = f" e.g., {example}" if example else ""

        while True:
            try:
                prompt_label = f"{placeholder}{context_hint}{type_hint}{example_hint}"
                user_input = Prompt.ask(f"  {prompt_label}")
                if not user_input.strip():
                    console.print(
                        f"    [{StyleTokens.WARNING}]Please enter a value for "
                        f"{placeholder}[/{StyleTokens.WARNING}]"
                    )
                    continue

                is_valid, message, converted = validate_value(user_input, inferred_type)
                if message:
                    console.print(
                        f"    [{StyleTokens.WARNING}]{message}[/{StyleTokens.WARNING}]"
                    )
                if is_valid:
                    values[position] = converted
                    param_dict[f"param_{position + 1}"] = converted
                    break
            except KeyboardInterrupt:
                console.print(MessagePanel("Cancelled", variant="warning"))
                return None

    substituted_sql = substitute_placeholders(sql, values)
    formatted_sql = format_sql_for_display(substituted_sql)
    sql_preview = formatted_sql[:300] + ("..." if len(formatted_sql) > 300 else "")
    console.print(
        SectionBox(
            title="Query Ready",
            content=sql_preview,
            border_style=StyleTokens.SUCCESS,
        )
    )

    try:
        if Confirm.ask("Proceed with analysis?", default=True):
            return (substituted_sql, param_dict)
        console.print(MessagePanel("Cancelled", variant="warning"))
        return None
    except KeyboardInterrupt:
        console.print(MessagePanel("Cancelled", variant="warning"))
        return None


def extract_placeholder_context(sql: str, placeholder: str) -> str:
    if placeholder == "?":
        return ""

    escaped_placeholder = re.escape(placeholder)
    pattern1 = r"(\w+(?:\.\w+)?)\s*([<>=!]+|LIKE|IN)\s*" + escaped_placeholder
    match1 = re.search(pattern1, sql, re.IGNORECASE)
    if match1:
        return f"{match1.group(1)} {match1.group(2)} {placeholder}"

    pattern2 = escaped_placeholder + r"\s*([<>=!]+)\s*(\w+(?:\.\w+)?)"
    match2 = re.search(pattern2, sql, re.IGNORECASE)
    if match2:
        return f"{placeholder} {match2.group(1)} {match2.group(2)}"

    pattern3 = r"(LIMIT|OFFSET)\s+" + escaped_placeholder
    match3 = re.search(pattern3, sql, re.IGNORECASE)
    if match3:
        return f"{match3.group(1)} {placeholder}"

    pattern4 = r"(ORDER\s+BY)\s+[^$]*" + escaped_placeholder
    match4 = re.search(pattern4, sql, re.IGNORECASE)
    if match4:
        return f"ORDER BY ... {placeholder}"

    return ""


def has_unresolved_placeholders(sql: str) -> bool:
    if not sql:
        return False
    if re.search(r"\$\d+", sql):
        return True
    sql_no_strings = re.sub(r"'[^']*'", "", sql)
    return "?" in sql_no_strings


__all__ = [
    "_prompt_for_parameters_plain",
    "_prompt_for_parameters_rich",
    "detect_placeholders",
    "extract_placeholder_context",
    "has_unresolved_placeholders",
    "infer_parameter_type",
    "prompt_for_parameters",
    "substitute_placeholders",
    "validate_value",
]

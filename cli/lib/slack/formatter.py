"""
Slack message formatting using Block Kit.
"""

from typing import Any, Optional


class SlackFormatter:
    """Formats Ask3 results for Slack Block Kit."""

    MAX_DISPLAY_ROWS = 20
    MAX_TEXT_LENGTH = 3000  # Slack's limit for text blocks

    def format_response(self, ctx: Any) -> dict:
        """
        Convert Ask3Context to Slack Block Kit message.

        Args:
            ctx: Ask3Context with query results.

        Returns:
            Slack message dict with blocks.
        """
        from ..engines.ask3.context import Status

        blocks = []

        # Add SQL block if we have a query
        if ctx.sql:
            blocks.append(self._sql_block(ctx.sql))

        # Add results or error
        if ctx.status == Status.SUCCESS and ctx.execution_result:
            result = ctx.execution_result
            if result.rows:
                blocks.append(self._results_block(result.columns, result.rows))
                blocks.append(self._metadata_block(result, ctx))
            else:
                blocks.append(self._text_block("No results found."))
        elif ctx.status == Status.ERROR:
            blocks.append(self._error_block(ctx.error_message or "An error occurred."))
        elif ctx.status == Status.CANCELLED:
            blocks.append(self._text_block("Query was cancelled."))

        return {"blocks": blocks}

    def format_error(self, error: Exception) -> dict:
        """
        Format an exception as a Slack message.

        Args:
            error: The exception.

        Returns:
            Slack message dict with error block.
        """
        return {
            "blocks": [self._error_block(str(error))],
        }

    def format_thinking(self) -> dict:
        """
        Format a "thinking" indicator message.

        Returns:
            Slack message dict.
        """
        return {
            "blocks": [
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": ":hourglass_flowing_sand: Thinking..."}
                    ],
                }
            ]
        }

    def format_help(self) -> dict:
        """
        Format a help message.

        Returns:
            Slack message dict.
        """
        return {
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "*How to use this bot:*\n"
                            "Just ask me a question about your data in plain English!\n\n"
                            "*Examples:*\n"
                            "- How many orders did we get this week?\n"
                            "- Show me the top 10 customers by revenue\n"
                            "- What's the average order value by month?\n"
                        ),
                    },
                }
            ]
        }

    def _sql_block(self, sql: str) -> dict:
        """Create a code block for SQL."""
        # Truncate if too long
        if len(sql) > self.MAX_TEXT_LENGTH - 20:
            sql = sql[: self.MAX_TEXT_LENGTH - 50] + "\n-- (truncated)"

        return {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"```sql\n{sql}\n```"},
        }

    def _results_block(self, columns: list[str], rows: list[list[Any]]) -> dict:
        """Create a formatted table block for results."""
        # Limit rows displayed
        display_rows = rows[: self.MAX_DISPLAY_ROWS]

        # Format as monospace table
        table_text = self._format_table(columns, display_rows)

        # Truncate if too long
        if len(table_text) > self.MAX_TEXT_LENGTH - 20:
            table_text = table_text[: self.MAX_TEXT_LENGTH - 50] + "\n... (truncated)"

        return {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"```\n{table_text}\n```"},
        }

    def _format_table(self, columns: list[str], rows: list[list[Any]]) -> str:
        """
        Format results as a monospace table.

        Args:
            columns: Column names.
            rows: Data rows.

        Returns:
            Formatted table string.
        """
        if not columns:
            return "(no columns)"
        if not rows:
            return "(no rows)"

        # Calculate column widths
        widths = [len(str(col)) for col in columns]
        for row in rows:
            for i, val in enumerate(row):
                if i < len(widths):
                    widths[i] = max(widths[i], len(self._format_value(val)))

        # Cap column widths to avoid overly wide tables
        widths = [min(w, 30) for w in widths]

        # Build table
        lines = []

        # Header
        header = " | ".join(
            str(col)[: widths[i]].ljust(widths[i]) for i, col in enumerate(columns)
        )
        lines.append(header)

        # Separator
        separator = "-+-".join("-" * w for w in widths)
        lines.append(separator)

        # Rows
        for row in rows:
            formatted_row = " | ".join(
                self._format_value(val)[: widths[i]].ljust(widths[i])
                for i, val in enumerate(row)
                if i < len(widths)
            )
            lines.append(formatted_row)

        return "\n".join(lines)

    def _format_value(self, val: Any) -> str:
        """Format a single cell value."""
        if val is None:
            return "NULL"
        if isinstance(val, bool):
            return "true" if val else "false"
        if isinstance(val, float):
            # Format floats nicely
            if val == int(val):
                return str(int(val))
            return f"{val:.2f}"
        return str(val)

    def _metadata_block(self, result: Any, ctx: Any) -> dict:
        """Create a context block with query metadata."""
        total = result.row_count
        shown = min(self.MAX_DISPLAY_ROWS, total)
        time_ms = result.execution_time_ms

        parts = []

        if total > shown:
            parts.append(f"Showing {shown} of {total} rows")
        else:
            parts.append(f"{total} row{'s' if total != 1 else ''}")

        if time_ms:
            parts.append(f"{time_ms:.0f}ms")

        return {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": " | ".join(parts)}],
        }

    def _text_block(self, text: str) -> dict:
        """Create a simple text block."""
        return {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        }

    def _error_block(self, message: str) -> dict:
        """Create an error block."""
        # Truncate if too long
        if len(message) > self.MAX_TEXT_LENGTH - 50:
            message = message[: self.MAX_TEXT_LENGTH - 80] + "..."

        return {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":x: *Error:* {message}",
            },
        }

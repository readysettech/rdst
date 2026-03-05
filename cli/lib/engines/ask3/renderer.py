"""
AskRenderer - Maps service events to Rich terminal output.

Pure rendering, no input collection. Consumes AskEvent stream and
displays appropriate output for each event type.
"""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ...services.types import (
        AskEvent,
        AskStatusEvent,
        AskSchemaLoadedEvent,
        AskClarificationNeededEvent,
        AskSqlGeneratedEvent,
        AskResultEvent,
        AskErrorEvent,
    )

from lib.ui import (
    get_console,
    StyleTokens,
    DataTable,
    MessagePanel,
    SectionBox,
    SelectionTable,
    Status,
    Syntax,
    format_sql_for_display,
)


class AskRenderer:
    """
    Renders AskEvent stream to terminal using Rich.

    Usage:
        renderer = AskRenderer(verbose=True)
        async for event in service.ask(input, options):
            renderer.render(event)
    """

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self._console = get_console()
        self._current_status: Optional[Status] = None

    def cleanup(self) -> None:
        """Stop any active spinner. Call when done processing events."""
        if self._current_status is not None:
            self._current_status.stop()
            self._current_status = None

    def render(self, event: "AskEvent") -> None:
        """Render an event to the terminal."""
        from ...services.types import (
            AskStatusEvent,
            AskSchemaLoadedEvent,
            AskClarificationNeededEvent,
            AskSqlGeneratedEvent,
            AskResultEvent,
            AskErrorEvent,
        )

        if isinstance(event, AskStatusEvent):
            self._render_status(event)
        elif isinstance(event, AskSchemaLoadedEvent):
            self._render_schema_loaded(event)
        elif isinstance(event, AskClarificationNeededEvent):
            self._render_clarification_needed(event)
        elif isinstance(event, AskSqlGeneratedEvent):
            self._render_sql_generated(event)
        elif isinstance(event, AskResultEvent):
            self._render_result(event)
        elif isinstance(event, AskErrorEvent):
            self._render_error(event)

    def _render_status(self, event: "AskStatusEvent") -> None:
        """Render status/progress event with animated spinner for long operations."""
        # Phases that show animated spinner (long-running operations)
        spinner_phases = {"schema", "filter", "clarify", "generate", "validate", "execute"}

        if event.phase in spinner_phases:
            # Show animated spinner for long-running phases
            if self._current_status is None:
                self._console.print()  # Blank line before spinner
                self._current_status = Status(event.message, spinner="dots", console=self._console)
                self._current_status.start()
            else:
                self._current_status.update(event.message)
        elif event.phase == "config":
            # Config is quick, show inline if verbose
            if self.verbose:
                self._console.print(f"[{StyleTokens.MUTED}]{event.message}[/{StyleTokens.MUTED}]")
        elif self.verbose:
            self._console.print(f"[{StyleTokens.MUTED}]{event.message}[/{StyleTokens.MUTED}]")

    def _render_schema_loaded(self, event: "AskSchemaLoadedEvent") -> None:
        """Render schema loaded event."""
        if self.verbose:
            self._console.print(
                f"[{StyleTokens.MUTED}]Schema loaded from {event.source} "
                f"({event.table_count} tables)[/{StyleTokens.MUTED}]"
            )

    def _render_clarification_needed(self, event: "AskClarificationNeededEvent") -> None:
        """Render clarification needed - shows interpretations and questions."""
        self.cleanup()  # Stop spinner

        # Show interpretations panel
        if event.interpretations:
            self._console.print(
                MessagePanel(
                    "I found multiple ways to interpret your question.",
                    variant="warning",
                )
            )

            option_texts = []
            for interp in event.interpretations:
                label = self._get_likelihood_label(interp.likelihood)
                style = self._get_likelihood_style(interp.likelihood)
                styled_label = f"[{style}][{label}][/{style}]" if style else f"[{label}]"
                assumptions = ", ".join(interp.assumptions) if interp.assumptions else ""
                suffix = f" — {assumptions}" if assumptions else ""
                option_texts.append(f"{interp.description}\n{styled_label}{suffix}")

            self._console.print(SelectionTable(option_texts))
            self._console.print()

    def _render_sql_generated(self, event: "AskSqlGeneratedEvent") -> None:
        """Render generated SQL with syntax highlighting."""
        self.cleanup()  # Stop spinner

        formatted_sql = format_sql_for_display(event.sql)

        # Create syntax-highlighted SQL
        syntax = Syntax(
            formatted_sql.strip(),
            "sql",
            theme=StyleTokens.SQL_THEME,
            word_wrap=True,
            background_color="default",
        )

        self._console.print(
            SectionBox(
                title="Generated SQL",
                content=syntax,  # Rich Syntax object for highlighting
                subtitle=f"Explanation: {event.explanation}" if event.explanation else None,
            )
        )

    def _render_result(self, event: "AskResultEvent") -> None:
        """Render query results."""
        self.cleanup()  # Stop spinner

        if not event.rows:
            self._console.print(
                f"[{StyleTokens.MUTED}]No results returned "
                f"(0 rows in {event.execution_time_ms:.1f}ms)[/{StyleTokens.MUTED}]"
            )
            return

        # Format rows as strings
        str_rows = [tuple(self._format_value(v) for v in row) for row in event.rows]

        table = DataTable(
            columns=event.columns,
            rows=str_rows,
            title=f"Results ({event.row_count} {'row' if event.row_count == 1 else 'rows'}, {event.execution_time_ms:.1f}ms)",
        )
        self._console.print(table)

    def _render_error(self, event: "AskErrorEvent") -> None:
        """Render error event."""
        self.cleanup()  # Stop spinner

        phase_info = f" (during {event.phase})" if event.phase else ""
        self._console.print(
            f"\n[{StyleTokens.STATUS_ERROR}]Error{phase_info}:[/{StyleTokens.STATUS_ERROR}] "
            f"{event.message}"
        )

    # === Helper Methods ===

    def _get_likelihood_style(self, likelihood: float) -> str:
        """Get Rich style based on likelihood threshold."""
        if likelihood >= 0.7:
            return StyleTokens.SUCCESS
        elif likelihood >= 0.3:
            return StyleTokens.WARNING
        else:
            return StyleTokens.MUTED

    def _get_likelihood_label(self, likelihood: float) -> str:
        """Get text label for likelihood."""
        if likelihood >= 0.7:
            return "High"
        elif likelihood >= 0.3:
            return "Medium"
        else:
            return "Low"

    def _format_value(self, val: Any) -> str:
        """Format a value for display."""
        if val is None:
            return "NULL"
        elif isinstance(val, (bytes, bytearray)):
            return f"<binary: {len(val)} bytes>"
        elif isinstance(val, str) and len(val) > 50:
            return val[:47] + "..."
        else:
            return str(val)


class QuietRenderer(AskRenderer):
    """Renderer that suppresses most output - for testing/programmatic use."""

    def __init__(self):
        super().__init__(verbose=False)

    def render(self, event: "AskEvent") -> None:
        """Only render errors."""
        from ...services.types import AskErrorEvent

        if isinstance(event, AskErrorEvent):
            print(f"Error: {event.message}")

"""
AskRenderer - Maps service events to Rich terminal output.

Pure rendering, no input collection. Consumes AskEvent stream and
displays appropriate output for each event type.
"""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from features.ask.events import (
        AskEvent,
        AskStatusEvent,
        AskSchemaLoadedEvent,
        AskClarificationNeededEvent,
        AskSqlGeneratedEvent,
        AskResultEvent,
        AskErrorEvent,
    )

from shared.ui import (
    get_console,
    StyleTokens,
    DataTable,
    MessagePanel,
    QueryPanel,
    SelectionTable,
    Status,
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
        from features.ask.events import (
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

        self._console.print(QueryPanel(event.sql, title="Generated SQL"))
        if event.explanation:
            self._console.print(
                f"[{StyleTokens.MUTED}]Explanation: {event.explanation}[/{StyleTokens.MUTED}]"
            )

    def _render_result(self, event: "AskResultEvent") -> None:
        """Render query results."""
        self.cleanup()  # Stop spinner

        if not event.rows:
            if event.execution_time_ms == 0.0 and not event.columns:
                self._console.print(
                    f"[{StyleTokens.MUTED}]Dry run — SQL generated but not executed[/{StyleTokens.MUTED}]"
                )
            else:
                self._console.print(
                    f"[{StyleTokens.MUTED}]No results returned "
                    f"(0 rows in {event.execution_time_ms:.1f}ms)[/{StyleTokens.MUTED}]"
                )
            if event.query_hash:
                short_hash = event.query_hash[:8]
                self._console.print(
                    f"\n[{StyleTokens.MUTED}]saved as {short_hash} — "
                    f"rdst analyze --hash {short_hash}[/{StyleTokens.MUTED}]"
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

        # Breadcrumb: show saved query info
        if event.query_hash:
            short_hash = event.query_hash[:8]
            self._console.print(
                f"\n[{StyleTokens.MUTED}]saved as {short_hash} — "
                f"rdst analyze --hash {short_hash}[/{StyleTokens.MUTED}]"
            )

    # Map internal phase names to user-friendly error prefixes
    _PHASE_ERROR_PREFIX: dict[str, str] = {
        "schema": "Error loading schema:",
        "filter": "Error filtering schema:",
        "clarify": "Error analyzing question:",
        "generate": "Error generating SQL:",
        "validate": "Error validating SQL:",
        "execute": "Error executing query:",
        "config": "Error loading configuration:",
    }

    def _render_error(self, event: "AskErrorEvent") -> None:
        """Render error event."""
        self.cleanup()  # Stop spinner

        from shared.ui import create_console
        stderr_console = create_console(stderr=True)

        # Map phase enum to user-friendly prefix
        phase_key = event.phase.value if hasattr(event.phase, "value") else event.phase
        error_prefix = self._PHASE_ERROR_PREFIX.get(phase_key, "Error:") if phase_key else "Error:"
        stderr_console.print(
            f"\n[{StyleTokens.STATUS_ERROR}]{error_prefix}[/{StyleTokens.STATUS_ERROR}] "
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
        from features.ask.events import AskErrorEvent

        if isinstance(event, AskErrorEvent):
            print(f"Error: {event.message}")

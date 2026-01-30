"""
AnnotateRenderer - Maps AnnotateService events to Rich terminal output.

Pure rendering, no business logic. Consumes AnnotateEvent stream and
displays appropriate output for each event type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from rich.status import Status

from lib.ui import (
    get_console,
    StyleTokens,
)

if TYPE_CHECKING:
    from lib.services.types import (
        AnnotateEvent,
        AnnotateStartedEvent,
        AnnotateProgressEvent,
        AnnotateTableCompleteEvent,
        AnnotateCompleteEvent,
        AnnotateErrorEvent,
    )


class AnnotateRenderer:
    """
    Renders AnnotateEvent stream to terminal using Rich.

    Handles progress spinner with table name, checkmarks for completed
    tables, and summary at the end.

    Usage:
        renderer = AnnotateRenderer()
        async for event in service.annotate(target, config):
            renderer.render(event)
        renderer.cleanup()  # Stop any active spinner
    """

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self._console = get_console()
        self._current_status: Optional[Status] = None

    def render(self, event: "AnnotateEvent") -> None:
        """Render an event to the terminal."""
        from lib.services.types import (
            AnnotateStartedEvent,
            AnnotateProgressEvent,
            AnnotateTableCompleteEvent,
            AnnotateCompleteEvent,
            AnnotateErrorEvent,
        )

        if isinstance(event, AnnotateStartedEvent):
            self._render_started(event)
        elif isinstance(event, AnnotateProgressEvent):
            self._render_progress(event)
        elif isinstance(event, AnnotateTableCompleteEvent):
            self._render_table_complete(event)
        elif isinstance(event, AnnotateCompleteEvent):
            self._render_complete(event)
        elif isinstance(event, AnnotateErrorEvent):
            self._render_error(event)

    def cleanup(self) -> None:
        """Stop any active spinner. Call when done processing events."""
        if self._current_status is not None:
            self._current_status.stop()
            self._current_status = None

    def _render_started(self, event: "AnnotateStartedEvent") -> None:
        """Render annotation started event."""
        self._console.print(
            f"[{StyleTokens.INFO}]Generating AI annotations for {event.tables} table(s)...[/{StyleTokens.INFO}]"
        )
        self._console.print(
            f"[{StyleTokens.MUTED}](This may take a minute for large schemas)[/{StyleTokens.MUTED}]\n"
        )

    def _render_progress(self, event: "AnnotateProgressEvent") -> None:
        """Render progress event with spinner."""
        # Stop previous spinner if any
        if self._current_status is not None:
            self._current_status.stop()

        # Start new spinner
        self._current_status = self._console.status(
            f"[{StyleTokens.MUTED}]({event.table_index}/{event.total_tables})[/{StyleTokens.MUTED}] "
            f"Annotating [bold]{event.table}[/bold]...",
            spinner="dots",
        )
        self._current_status.start()

    def _render_table_complete(self, event: "AnnotateTableCompleteEvent") -> None:
        """Render table completion event."""
        # Stop spinner
        if self._current_status is not None:
            self._current_status.stop()
            self._current_status = None

        # Print checkmark
        cols_text = (
            f" ({event.columns_annotated} columns)"
            if event.columns_annotated > 0
            else ""
        )
        self._console.print(
            f"  [{StyleTokens.SUCCESS}]✓[/{StyleTokens.SUCCESS}] "
            f"[bold]{event.table}[/bold]{cols_text}"
        )

    def _render_complete(self, event: "AnnotateCompleteEvent") -> None:
        """Render completion event."""
        self.cleanup()

        self._console.print()
        if event.success:
            self._console.print(
                f"[{StyleTokens.SUCCESS}]Generated annotations for "
                f"{event.tables_annotated} table(s) and {event.columns_annotated} column(s)[/{StyleTokens.SUCCESS}]"
            )
        else:
            self._console.print(
                f"[{StyleTokens.WARNING}]Annotation completed with issues[/{StyleTokens.WARNING}]"
            )

    def _render_error(self, event: "AnnotateErrorEvent") -> None:
        """Render error event."""
        self.cleanup()

        self._console.print(
            f"[{StyleTokens.ERROR}]Error: {event.message}[/{StyleTokens.ERROR}]"
        )

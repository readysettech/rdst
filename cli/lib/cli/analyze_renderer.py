"""
AnalyzeRenderer - Maps AnalyzeService events to Rich terminal output.

Pure rendering, no business logic. Consumes AnalyzeEvent stream and
displays appropriate output for each event type.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from rich.status import Status

from lib.ui import (
    get_console,
    StatusLine,
    StyleTokens,
)

if TYPE_CHECKING:
    from lib.services.types import (
        AnalyzeEvent,
        ProgressEvent,
        ExplainCompleteEvent,
        RewritesTestedEvent,
        ReadysetCheckedEvent,
        CompleteEvent,
        ErrorEvent,
    )


class _ElapsedStatusMessage:
    """Dynamic status text that includes elapsed seconds while rendering."""

    def __init__(self, message: str, step_start_time: float):
        self._message = message
        self._step_start_time = step_start_time

    def __rich__(self) -> str:
        elapsed = int(time.monotonic() - self._step_start_time)
        if elapsed < 1:
            return self._message
        return f"{self._message} ({elapsed}s)"


class AnalyzeRenderer:
    """
    Renders AnalyzeEvent stream to terminal using Rich.

    Handles progress spinner with elapsed time, status lines for
    completed phases, and error display.

    Usage:
        renderer = AnalyzeRenderer()
        async for event in service.analyze(input, options):
            renderer.render(event)
        renderer.cleanup()  # Stop any active spinner
    """

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self._console = get_console()
        self._current_status: Optional[Status] = None
        self._step_start_time: Optional[float] = None
        self._current_stage: Optional[str] = None
        self._current_message: Optional[str] = None

    def render(self, event: "AnalyzeEvent") -> None:
        """Render an event to the terminal."""
        from lib.services.types import (
            ProgressEvent,
            ExplainCompleteEvent,
            RewritesTestedEvent,
            ReadysetCheckedEvent,
            CompleteEvent,
            ErrorEvent,
        )

        if isinstance(event, ProgressEvent):
            self._render_progress(event)
        elif isinstance(event, ExplainCompleteEvent):
            self._render_explain_complete(event)
        elif isinstance(event, RewritesTestedEvent):
            self._render_rewrites_tested(event)
        elif isinstance(event, ReadysetCheckedEvent):
            self._render_readyset_checked(event)
        elif isinstance(event, CompleteEvent):
            self._render_complete(event)
        elif isinstance(event, ErrorEvent):
            self._render_error(event)

    def cleanup(self) -> None:
        """Stop any active spinner. Call when done processing events."""
        if self._current_status is not None:
            self._current_status.stop()
            self._current_status = None
        self._step_start_time = None
        self._current_stage = None
        self._current_message = None

    def _render_progress(self, event: "ProgressEvent") -> None:
        """Render progress event with spinner."""
        if event.percent < 100:
            stage_changed = event.stage != self._current_stage
            message_changed = event.message != self._current_message
            if stage_changed or message_changed or self._step_start_time is None:
                self._step_start_time = time.monotonic()
                self._current_stage = event.stage
                self._current_message = event.message

            progress_text = _ElapsedStatusMessage(event.message, self._step_start_time)

            if self._current_status is None:
                self._current_status = Status(
                    progress_text, spinner="dots", console=self._console
                )
                self._current_status.start()
            else:
                self._current_status.update(progress_text)
        else:
            # 100% complete - stop spinner
            self.cleanup()

    def _render_explain_complete(self, event: "ExplainCompleteEvent") -> None:
        """Render EXPLAIN ANALYZE completion."""
        if not event.success:
            return

        self.cleanup()
        self._console.print(
            StatusLine(
                "EXPLAIN ANALYZE",
                f"{event.execution_time_ms:.1f}ms, {event.rows_examined:,} rows examined",
                style=StyleTokens.SUCCESS,
            )
        )

    def _render_rewrites_tested(self, event: "RewritesTestedEvent") -> None:
        """Render query rewrite testing completion."""
        if not event.tested:
            return

        self.cleanup()
        self._console.print(
            StatusLine(
                "Rewrites",
                event.message or "tested",
                style=StyleTokens.INFO,
            )
        )

    def _render_readyset_checked(self, event: "ReadysetCheckedEvent") -> None:
        """Render Readyset cacheability check completion."""
        if not event.checked:
            return

        self.cleanup()
        status = "cacheable" if event.cacheable else "not cacheable"
        style = StyleTokens.SUCCESS if event.cacheable else StyleTokens.WARNING
        self._console.print(
            StatusLine(
                "Readyset",
                f"{status} ({event.confidence})",
                style=style,
            )
        )

    def _render_complete(self, event: "CompleteEvent") -> None:
        """Render analysis completion."""
        self.cleanup()
        # CompleteEvent doesn't need special rendering -
        # the formatted results are displayed separately

    def _render_error(self, event: "ErrorEvent") -> None:
        """Render error event."""
        self.cleanup()
        # Error display is handled by the CLI command after getting the event
        # This just ensures spinner is stopped


class QuietRenderer(AnalyzeRenderer):
    """Renderer that suppresses output - for testing/programmatic use."""

    def __init__(self):
        super().__init__(verbose=False)

    def render(self, event: "AnalyzeEvent") -> None:
        """Only cleanup on completion/error, no output."""
        from lib.services.types import CompleteEvent, ErrorEvent

        if isinstance(event, (CompleteEvent, ErrorEvent)):
            self.cleanup()

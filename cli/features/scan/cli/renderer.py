"""ScanRenderer - Renders ScanEvent stream to terminal using Rich.

Consumes ScanEvent objects from ScanService and renders them as
Rich progress bars, spinners, and status messages. Follows the
same pattern as TopRenderer and AnalyzeRenderer.
"""

import sys
from typing import Optional

from shared.ui import (
    Progress,
    SpinnerColumn,
    TextColumn,
    get_console,
)

from ..events import (
    ScanCompleteEvent,
    ScanErrorEvent,
    ScanEvent,
    ScanFilesFoundEvent,
    ScanProgressEvent,
    ScanQueryResultEvent,
    ScanRegistryEvent,
    ScanStatusEvent,
)


class ScanRenderer:
    """Renders ScanEvent stream to terminal using Rich.

    Usage:
        renderer = ScanRenderer()
        async for event in service.scan_directory(input_data, options):
            renderer.render(event)
        renderer.cleanup()
    """

    def __init__(self) -> None:
        self._console = get_console()
        self._progress: Optional[Progress] = None
        self._task_id = None
        self._current_phase: Optional[str] = None
        self._spinner_progress: Optional[Progress] = None
        self._spinner_task = None

    def render(self, event: ScanEvent) -> None:
        """Render an event to the terminal."""
        if isinstance(event, ScanStatusEvent):
            self._render_status(event)
        elif isinstance(event, ScanFilesFoundEvent):
            self._render_files_found(event)
        elif isinstance(event, ScanProgressEvent):
            self._render_progress(event)
        elif isinstance(event, ScanQueryResultEvent):
            pass  # Collected by caller, not rendered inline
        elif isinstance(event, ScanRegistryEvent):
            self._render_registry(event)
        elif isinstance(event, ScanCompleteEvent):
            self._render_complete(event)
        elif isinstance(event, ScanErrorEvent):
            self._render_error(event)

    def cleanup(self) -> None:
        """Stop any active progress displays."""
        self._stop_spinner()
        self._stop_progress()
        # Safety: restore terminal state
        try:
            if sys.stdout.isatty():
                sys.stdout.write("\033[0m\033[?25h\033[?7h")
                sys.stdout.flush()
        except Exception:
            pass

    # =========================================================================
    # Event Renderers
    # =========================================================================

    def _render_status(self, event: ScanStatusEvent) -> None:
        if event.phase == "config":
            if "valid" in event.message.lower():
                # Config validated — no output needed, keep clean
                pass
            # else: validating — also skip, fast
        elif event.phase == "discovery":
            self._start_spinner(event.message)
        elif event.phase == "extraction":
            self._stop_spinner()
            # Progress bar will be started by _render_progress
        elif event.phase == "conversion":
            self._stop_progress()
            self._start_spinner(event.message)
        elif event.phase == "registry":
            self._stop_spinner()
            self._stop_progress()
        elif event.phase == "analysis":
            self._stop_spinner()
            self._stop_progress()
            self._start_spinner(event.message)

    def _render_files_found(self, event: ScanFilesFoundEvent) -> None:
        self._stop_spinner()
        if event.total > 0:
            self._console.print(
                f"Found [cyan]{event.total}[/cyan] files with ORM patterns\n"
            )
        else:
            self._console.print("[dim]No files with ORM patterns found.[/dim]")

    def _render_progress(self, event: ScanProgressEvent) -> None:
        if event.phase == "extraction":
            self._stop_spinner()
            if self._progress is None:
                self._progress = Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=self._console,
                )
                self._progress.start()
                self._task_id = self._progress.add_task(
                    event.message, total=event.total
                )
                self._current_phase = "extraction"
            self._progress.update(
                self._task_id,
                completed=event.current,
                description=event.message,
            )
        elif event.phase == "conversion":
            self._stop_spinner()
            if self._progress is None or self._current_phase != "conversion":
                self._stop_progress()
                self._progress = Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=self._console,
                )
                self._progress.start()
                self._task_id = self._progress.add_task(
                    event.message, total=event.total
                )
                self._current_phase = "conversion"
            self._progress.update(
                self._task_id,
                completed=event.current,
                description=event.message,
            )
        elif event.phase == "analysis":
            self._stop_spinner()
            if self._progress is None or self._current_phase != "analysis":
                self._stop_progress()
                self._progress = Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=self._console,
                )
                self._progress.start()
                self._task_id = self._progress.add_task(
                    event.message, total=event.total
                )
                self._current_phase = "analysis"
            self._progress.update(
                self._task_id,
                completed=event.current,
                description=event.message,
            )

    def _render_registry(self, event: ScanRegistryEvent) -> None:
        self._stop_progress()
        if event.skipped:
            self._console.print("[dim]Registry: skipped (--nosave)[/dim]")
        else:
            self._console.print(
                f"[bold]Registry:[/bold] {event.new_queries} new, "
                f"{event.updated_queries} updated, "
                f"{event.total_queries} total"
            )

    def _render_complete(self, event: ScanCompleteEvent) -> None:
        self._stop_progress()
        self._stop_spinner()
        # Report is printed separately by the caller (_print_report)

    def _render_error(self, event: ScanErrorEvent) -> None:
        self._stop_progress()
        self._stop_spinner()
        phase_str = f" ({event.phase})" if event.phase else ""
        self._console.print(f"[red]Error{phase_str}:[/red] {event.message}")

    # =========================================================================
    # Helpers
    # =========================================================================

    def _start_spinner(self, message: str) -> None:
        self._stop_spinner()
        self._spinner_progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self._console,
        )
        self._spinner_progress.start()
        self._spinner_task = self._spinner_progress.add_task(message, total=None)

    def _stop_spinner(self) -> None:
        if self._spinner_progress is not None:
            self._spinner_progress.stop()
            self._spinner_progress = None
            self._spinner_task = None

    def _stop_progress(self) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
            self._task_id = None
            self._current_phase = None


class ScanQuietRenderer:
    """Suppresses all output for JSON mode."""

    def render(self, event: ScanEvent) -> None:
        pass

    def cleanup(self) -> None:
        pass

"""TopRenderer - Renders TopEvent stream to terminal using Rich.

This renderer supports both snapshot (one-time) and realtime (Live updates) modes.
It consumes TopEvent objects from TopService and renders them appropriately.
"""

import datetime
import sys
import time
from typing import List, Optional

from lib.ui import (
    DataTable,
    DataTableBase,
    EmptyState,
    Group,
    KeyboardShortcuts,
    MessagePanel,
    MonitorHeader,
    NoticePanel,
    StatusLine,
    StyleTokens,
    TopQueryTable,
    create_console,
    get_console,
)

from ..services.types import (
    TopCompleteEvent,
    TopConnectedEvent,
    TopErrorEvent,
    TopEvent,
    TopQueriesEvent,
    TopQueryData,
    TopQuerySavedEvent,
    TopSourceFallbackEvent,
    TopStatusEvent,
)


class TopRenderer:
    """Renders TopEvent stream to terminal using Rich.

    Supports both snapshot (one-time) and realtime (Live updates) modes.

    Usage:
        # Snapshot mode
        renderer = TopRenderer()
        async for event in service.get_top_queries(input, options):
            renderer.render(event)

        # Realtime mode
        renderer = TopRenderer(realtime=True)
        renderer.start_live()
        try:
            async for event in service.stream_realtime(input, options):
                renderer.render(event)
        finally:
            renderer.cleanup()
    """

    def __init__(
        self,
        verbose: bool = False,
        no_color: bool = False,
        realtime: bool = False,
    ):
        """Initialize the renderer.

        Args:
            verbose: Show verbose output
            no_color: Disable ANSI color formatting
            realtime: Enable realtime mode with Live display
        """
        self._console = get_console()
        self._verbose = verbose
        self._no_color = no_color
        self._realtime = realtime
        self._live_started: bool = False
        self._current_queries: List[TopQueryData] = []
        self._target_name: str = ""
        self._db_engine: str = ""
        self._source: str = ""
        self._runtime_seconds: float = 0.0
        self._total_tracked: int = 0
        self._auto_saved_count: int = 0
        self._last_display_update: float = 0.0

    def render(self, event: TopEvent) -> None:
        """Render an event to the terminal.

        Args:
            event: TopEvent to render
        """
        if isinstance(event, TopStatusEvent):
            self._render_status(event)
        elif isinstance(event, TopConnectedEvent):
            self._render_connected(event)
        elif isinstance(event, TopSourceFallbackEvent):
            self._render_fallback(event)
        elif isinstance(event, TopQueriesEvent):
            self._render_queries(event)
        elif isinstance(event, TopQuerySavedEvent):
            self._render_query_saved(event)
        elif isinstance(event, TopCompleteEvent):
            self._render_complete(event)
        elif isinstance(event, TopErrorEvent):
            self._render_error(event)

    def start_live(self) -> None:
        """Start realtime display mode (manual cursor control, no Rich Live)."""
        if self._realtime and not self._live_started:
            self._live_started = True
            # Clear screen and home cursor
            if sys.stdout.isatty():
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.flush()

    def stop_live(self) -> None:
        """Stop realtime display."""
        self._live_started = False

    def cleanup(self) -> None:
        """Stop any active display. Call when done."""
        if self._live_started and sys.stdout.isatty():
            # Clear the screen so no ghost content remains
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()
        self.stop_live()
        self._restore_terminal()

    def get_current_queries(self) -> List[TopQueryData]:
        """Get the current list of queries."""
        return self._current_queries

    # =========================================================================
    # Event Renderers
    # =========================================================================

    def _render_status(self, event: TopStatusEvent) -> None:
        """Render a status event."""
        if not self._realtime:
            # In snapshot mode, show status as a status line
            self._console.print(
                StatusLine("Status", event.message, style=StyleTokens.INFO)
            )

    def _render_connected(self, event: TopConnectedEvent) -> None:
        """Render a connected event."""
        self._target_name = event.target_name
        self._db_engine = event.db_engine
        self._source = event.source

        if not self._realtime:
            self._console.print(
                StatusLine(
                    "Connected",
                    f"{event.target_name} ({event.db_engine}) - {event.source}",
                    style=StyleTokens.SUCCESS,
                )
            )

    def _render_fallback(self, event: TopSourceFallbackEvent) -> None:
        """Render a source fallback event."""
        self._source = event.to_source

        # Show notice panel for fallback
        if event.from_source == "pg_stat" and event.to_source == "activity":
            self._console.print(
                NoticePanel(
                    title="pg_stat_statements not found",
                    description="Falling back to live activity view.",
                    variant="warning",
                    bullets=[
                        "To enable better query statistics, run: CREATE EXTENSION IF NOT EXISTS pg_stat_statements;",
                        "Then add 'shared_preload_libraries = pg_stat_statements' to postgresql.conf and restart PostgreSQL.",
                    ],
                )
            )
        else:
            self._console.print(
                NoticePanel(
                    title=f"Source Fallback",
                    description=f"Switched from '{event.from_source}' to '{event.to_source}'",
                    variant="warning",
                    bullets=[event.reason] if event.reason else None,
                )
            )

    def _render_queries(self, event: TopQueriesEvent) -> None:
        """Render a queries event."""
        self._current_queries = event.queries
        self._target_name = event.target_name
        self._db_engine = event.db_engine
        self._source = event.source
        self._runtime_seconds = event.runtime_seconds or 0.0
        self._total_tracked = event.total_tracked or 0

        if self._realtime:
            # Direct terminal update (throttle to once per second)
            now = time.monotonic()
            if self._live_started and (now - self._last_display_update) >= 1.0:
                self._last_display_update = now
                # Home cursor, print, clear remainder
                if sys.stdout.isatty():
                    sys.stdout.write("\033[H")
                    sys.stdout.flush()
                self._console.print(self._build_display())
                if sys.stdout.isatty():
                    sys.stdout.write("\033[J")
                    sys.stdout.flush()
        else:
            # Snapshot mode: render the table directly
            self._render_queries_snapshot(event)

    def _render_queries_snapshot(self, event: TopQueriesEvent) -> None:
        """Render queries in snapshot mode (static table)."""
        if not event.queries:
            self._console.print(
                EmptyState(
                    f"No active queries found for target '{event.target_name}' using source '{event.source}'.",
                    title="rdst top",
                    suggestion="Run some database queries in another session to see them here.",
                )
            )
            return

        # Convert TopQueryData to dict format for TopQueryTable
        queries_data = [
            {
                "query_hash": q.query_hash,
                "query_text": q.query_text,
                "freq": q.freq,
                "total_time": q.total_time,
                "avg_time": q.avg_time,
                "pct_load": q.pct_load,
            }
            for q in event.queries
        ]

        title = f"Top queries: {event.target_name} ({event.db_engine}) - {event.source}"
        table = TopQueryTable(
            queries=queries_data,
            source=event.source,
            target_name=event.target_name,
            db_engine=event.db_engine,
            title=title,
        )
        self._console.print(table)

    def _render_query_saved(self, event: TopQuerySavedEvent) -> None:
        """Render a query saved event."""
        if event.is_new:
            self._auto_saved_count += 1
            if self._verbose and not self._realtime:
                self._console.print(
                    f"[{StyleTokens.SUCCESS}]Saved query {event.query_hash[:8]} to registry[/{StyleTokens.SUCCESS}]"
                )

    def _render_complete(self, event: TopCompleteEvent) -> None:
        """Render a complete event."""
        if not self._realtime:
            if event.newly_saved > 0:
                self._console.print(
                    MessagePanel(
                        f"Saved {event.newly_saved} new queries to registry",
                        variant="success",
                    )
                )

    def _render_error(self, event: TopErrorEvent) -> None:
        """Render an error event."""
        self._console.print(
            MessagePanel(
                event.message,
                title=f"Error{' (' + event.stage + ')' if event.stage else ''}",
                variant="error",
            )
        )

    # =========================================================================
    # Display Building
    # =========================================================================

    def _build_display(self):
        """Build current display for Live updates."""
        # Header using MonitorHeader component
        stats = {
            "Runtime": f"{int(self._runtime_seconds)}s",
            "Tracked": str(self._total_tracked),
            "Polling": "200ms",
        }
        if self._auto_saved_count > 0:
            stats["Auto-Saved"] = str(self._auto_saved_count)

        warning = None
        if self._db_engine and self._db_engine.lower() == "mysql":
            warning = (
                "MySQL: Queries <1s may not be tracked. Duration has 1s granularity."
            )

        header = MonitorHeader(
            title="RDST Top - Real-Time Query Monitor",
            stats=stats,
            hint="Press Ctrl+C to exit.",
            warning=warning,
        )

        # Create table
        table = DataTableBase(
            title="Top 10 Slowest Queries (by Max Duration Observed)",
            show_header=True,
        )

        table.add_column("#", style=StyleTokens.SECONDARY, width=3)
        table.add_column("Max", style=StyleTokens.DURATION_SLOW, width=11)
        table.add_column("Avg", style=StyleTokens.WARNING, width=11)
        table.add_column("Obs", style=StyleTokens.SUCCESS, width=5)
        table.add_column("Run", style=StyleTokens.ACCENT, width=5)
        table.add_column("Query", style=StyleTokens.SQL)

        # Add rows
        for idx, query in enumerate(self._current_queries[:10]):
            # Format durations (they're already formatted as strings like "X.XXXs")
            # For realtime, we have max_duration_ms
            if query.max_duration_ms is not None:
                max_dur = f"{query.max_duration_ms:,.1f}ms"
                # Parse avg_time to get ms
                try:
                    avg_s = float(query.avg_time.rstrip("s"))
                    avg_dur = f"{avg_s * 1000:,.1f}ms"
                except (ValueError, AttributeError):
                    avg_dur = query.avg_time
            else:
                max_dur = query.total_time
                avg_dur = query.avg_time

            obs_count = str(query.observation_count or query.freq)
            running_now = str(query.current_instances or 0)

            # Use normalized query for display
            query_text = query.normalized_query or query.query_text
            query_text = " ".join(query_text.split())

            # Highlight if currently running
            instances = query.current_instances or 0
            style = "bold" if instances > 0 else StyleTokens.MUTED

            table.add_row(
                str(idx),
                max_dur,
                avg_dur,
                obs_count,
                running_now,
                query_text,
                style=style,
            )

        # Add empty rows if fewer than 10
        for idx in range(len(self._current_queries), 10):
            table.add_row(str(idx), "-", "-", "-", "-", "-", style=StyleTokens.MUTED)

        # Footer with keyboard shortcuts
        footer = KeyboardShortcuts(
            title="Quick Actions",
            shortcuts=[
                ("0-9", "save", StyleTokens.SUCCESS),
                ("a", "save all", StyleTokens.SUCCESS),
                ("z+0-9", "analyze", StyleTokens.WARNING),
                ("q", "quit", StyleTokens.ERROR),
                ("Ctrl+C", "quit", StyleTokens.ERROR),
            ],
        )

        return Group(header, table, footer)

    def _restore_terminal(self) -> None:
        """Restore terminal to normal state after Live display exits.

        Ensures cursor is visible, alternate screen buffer is exited,
        and terminal settings are restored.
        """
        import os

        try:
            # Show cursor and exit alternate screen buffer using ANSI codes
            if sys.stdout.isatty():
                sys.stdout.write("\033[?25h")  # Show cursor
                sys.stdout.write("\033[?1049l")  # Exit alternate screen buffer
                sys.stdout.flush()

            # Restore terminal settings on Unix
            if os.name == "posix":
                try:
                    import subprocess

                    subprocess.run(
                        ["stty", "sane"],
                        check=False,
                        stdin=sys.stdin,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    pass
        except Exception:
            # Best effort - don't let cleanup failure cause issues
            pass


def render_top_queries_json(
    queries: List[TopQueryData],
    target_name: str,
    db_engine: str,
    source: str,
    runtime_seconds: Optional[float] = None,
    total_tracked: Optional[int] = None,
) -> dict:
    """Render top queries as JSON-serializable dict.

    Args:
        queries: List of TopQueryData
        target_name: Target name
        db_engine: Database engine
        source: Data source
        runtime_seconds: Runtime in seconds (for realtime mode)
        total_tracked: Total queries tracked (for realtime mode)

    Returns:
        JSON-serializable dict
    """
    def _duration_to_ms(value: Optional[str]) -> float:
        """Convert a formatted duration string to milliseconds.

        Supports common forms like "0.123s", "123ms", or plain numbers.
        Returns 0.0 when parsing fails.
        """
        if value is None:
            return 0.0
        text = str(value).strip().lower()
        if not text:
            return 0.0
        try:
            if text.endswith("ms"):
                return float(text[:-2].strip())
            if text.endswith("s"):
                return float(text[:-1].strip()) * 1000.0
            return float(text)
        except (TypeError, ValueError):
            return 0.0

    queries_data = []
    for q in queries:
        avg_duration_ms = round(_duration_to_ms(q.avg_time), 2)
        max_duration_ms = (
            round(q.max_duration_ms, 2)
            if q.max_duration_ms is not None
            else round(_duration_to_ms(q.total_time), 2)
        )
        query_dict = {
            "query_hash": q.query_hash,
            "normalized_query": q.normalized_query,
            "query_text": q.query_text,
            "freq": q.freq,
            "total_time": q.total_time,
            "avg_time": q.avg_time,
            "avg_duration_ms": avg_duration_ms,
            "pct_load": q.pct_load,
            "max_duration_ms": max_duration_ms,
            "current_instances_running": q.current_instances or 0,
            "observation_count": q.observation_count or q.freq,
        }
        queries_data.append(query_dict)

    result = {
        "target": target_name,
        "engine": db_engine,
        "source": source,
        "queries": queries_data,
    }

    if runtime_seconds is not None:
        result["runtime_seconds"] = round(runtime_seconds, 2)
    if total_tracked is not None:
        result["total_queries_tracked"] = total_tracked

    return result

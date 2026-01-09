"""
Real-time display for RDST Top command using Rich library.

Provides live-updating table showing top 10 queries with keyboard interaction.
"""

import time
import threading
from typing import List, Callable

from lib.top_monitor import QueryMetrics

# Import UI system
from lib.ui import (
    get_console,
    StyleTokens,
    MonitorHeader,
    KeyboardShortcuts,
    DataTableBase,
    RichLayout,
    Live,
)


class TopDisplay:
    """
    Live display for top queries with keyboard interaction.

    Features:
    - Updates every 200ms
    - Shows top 10 queries
    - Press 0-9 to save individual query
    - Press 'a' to save all queries
    - Press ESC or 'q' to quit
    """

    def __init__(self, console=None, db_engine: str = None):
        """Initialize display.

        Args:
            console: Optional console instance (Rich or fallback)
            db_engine: Database engine type ('mysql' or 'postgresql')
        """
        self.console = console or get_console()
        self.db_engine = db_engine
        self.running = False
        self.selected_query_index = None
        self.save_all_requested = False
        self.analyze_requested = False
        self.quit_requested = False
        self.current_queries = []  # Store latest queries for saving

    def create_table(
        self,
        queries: List[QueryMetrics],
        runtime_seconds: float,
        total_tracked: int,
        db_engine: str = None,
        auto_saved_count: int = 0,
    ) -> object:
        """
        Create Rich table showing top queries.

        Args:
            queries: List of QueryMetrics (top 10)
            runtime_seconds: How long tracker has been running
            total_tracked: Total unique queries tracked
            db_engine: Database engine type ('mysql' or 'postgresql')
            auto_saved_count: Number of queries auto-saved to registry this session

        Returns:
            Layout with table and header
        """
        layout = RichLayout()

        # Header using MonitorHeader component
        stats = {
            "Runtime": f"{int(runtime_seconds)}s",
            "Tracked": str(total_tracked),
            "Polling": "200ms",
        }
        if auto_saved_count > 0:
            stats["Auto-Saved"] = str(auto_saved_count)

        warning = None
        if db_engine and db_engine.lower() == "mysql":
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
        table.add_column("Max Duration", style=StyleTokens.DURATION_SLOW, width=12)
        table.add_column("Avg Duration", style=StyleTokens.WARNING, width=12)
        table.add_column("Observations", style=StyleTokens.SUCCESS, width=12)
        table.add_column("Instances Running", style=StyleTokens.ACCENT, width=18)
        table.add_column("Query", style=StyleTokens.SQL, no_wrap=True)

        # Add rows
        for idx, query in enumerate(queries):
            # Format durations
            max_dur = f"{query.max_duration_seen:,.1f}ms"
            avg_dur = f"{query.avg_duration:,.1f}ms"
            obs_count = str(query.observation_count)
            running_now = str(query.current_instances_running)

            # Use normalized (parameterized) query for display
            query_text = (
                query.normalized_query if query.normalized_query else query.query_text
            )
            # Collapse whitespace and truncate for single-line display
            query_text = " ".join(query_text.split())
            if len(query_text) > 100:
                query_text = query_text[:97] + "..."

            # Highlight if currently running
            style = "bold" if query.current_instances_running > 0 else StyleTokens.MUTED

            table.add_row(
                str(idx),
                max_dur,
                avg_dur,
                obs_count,
                running_now,
                query_text,
                style=style,
            )

        # If fewer than 10 queries, add empty rows
        for idx in range(len(queries), 10):
            table.add_row(str(idx), "-", "-", "-", "-", "-", style=StyleTokens.MUTED)

        # Combine header and table
        layout.split_column(RichLayout(header, size=8), RichLayout(table))

        return layout

    def start_keyboard_listener(self):
        """
        Start background thread to listen for keyboard input.

        Listens for:
        - 0-9: Save that query
        - 'a': Save all queries
        - ESC or 'q': Quit

        Uses simpler approach without select module for WSL compatibility.
        """

        def listen():
            try:
                import sys
                import tty
                import termios
                import os

                # Save terminal settings
                old_settings = termios.tcgetattr(sys.stdin)
                try:
                    tty.setcbreak(sys.stdin.fileno())

                    # Set stdin to non-blocking mode
                    fd = sys.stdin.fileno()
                    import fcntl

                    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

                    while self.running:
                        try:
                            # Try to read one character (non-blocking)
                            ch = os.read(fd, 1).decode("utf-8")

                            # Check for ESC (ASCII 27) or 'q'
                            if ch == "\x1b" or ch == "q":
                                self.quit_requested = True
                                self.running = False
                                break

                            # Check for 0-9
                            elif ch.isdigit():
                                self.selected_query_index = int(ch)
                                self.running = False
                                break

                            # Check for 'a' (all)
                            elif ch == "a":
                                self.save_all_requested = True
                                self.running = False
                                break

                        except (BlockingIOError, OSError):
                            # No input available, sleep briefly
                            time.sleep(0.1)
                            continue

                finally:
                    # Restore terminal settings and blocking mode
                    fcntl.fcntl(fd, fcntl.F_SETFL, flags)
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

            except Exception:
                # Fallback: if keyboard listener fails, just keep running
                pass

        # Only start listener on Unix-like systems
        try:
            import fcntl

            listener_thread = threading.Thread(target=listen, daemon=True)
            listener_thread.start()
        except ImportError:
            # Windows doesn't have fcntl, skip keyboard interaction
            pass

    def run(self, get_queries_func: Callable[[], tuple]):
        """
        Run live display loop with single-keypress commands.

        Commands (single keypress, no Enter needed):
        - Press 0-9: Save that query
        - Press 'a': Save all queries
        - Press 's' then 0-9: Save that query (alternative)
        - Press 'z' then 0-9: Analyze that query
        - Press 'q' or Ctrl+C: Quit

        Args:
            get_queries_func: Function that returns (queries, runtime, total_tracked)
        """
        self.running = True

        # Create footer using KeyboardShortcuts component
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

        # Start keyboard listener for single keypress commands
        import threading
        import queue

        command_queue = queue.Queue()

        waiting_for_analyze_index = False

        def keypress_thread():
            """Background thread to capture single keypresses."""
            nonlocal waiting_for_analyze_index
            import sys
            import tty
            import termios
            import os

            old_settings = termios.tcgetattr(sys.stdin)
            try:
                tty.setcbreak(sys.stdin.fileno())
                fd = sys.stdin.fileno()

                while self.running:
                    try:
                        import select

                        ready, _, _ = select.select([sys.stdin], [], [], 0.1)

                        if ready:
                            ch = os.read(fd, 1).decode("utf-8", errors="ignore")

                            if ch == "\x03" or ch == "q":  # Ctrl+C or q
                                command_queue.put(("quit", None))
                                break
                            elif ch == "a":  # Save all
                                command_queue.put(("save_all", None))
                                break
                            elif ch == "z":  # Analyze mode
                                waiting_for_analyze_index = True
                                continue
                            elif ch.isdigit():
                                if waiting_for_analyze_index:
                                    command_queue.put(("analyze", int(ch)))
                                    waiting_for_analyze_index = False
                                    break
                                else:
                                    command_queue.put(("save", int(ch)))
                                    break
                    except Exception:
                        continue

            finally:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

        listener = threading.Thread(target=keypress_thread, daemon=True)
        listener.start()

        try:
            with Live(console=self.console, refresh_per_second=2, screen=True) as live:
                while self.running:
                    # Get current data (supports both 3-tuple and 4-tuple returns for backwards compat)
                    result = get_queries_func()
                    if len(result) == 4:
                        queries, runtime, total_tracked, auto_saved_count = result
                    else:
                        queries, runtime, total_tracked = result
                        auto_saved_count = 0
                    self.current_queries = queries

                    # Create table layout
                    table_layout = self.create_table(
                        queries,
                        runtime,
                        total_tracked,
                        self.db_engine,
                        auto_saved_count,
                    )

                    # Combine table and footer
                    main_layout = RichLayout()
                    main_layout.split_column(table_layout, RichLayout(footer, size=5))

                    live.update(main_layout)

                    # Check for commands (non-blocking)
                    try:
                        command, value = command_queue.get_nowait()
                        if command == "quit":
                            self.quit_requested = True
                            self.running = False
                        elif command == "save_all":
                            self.save_all_requested = True
                            self.running = False
                        elif command == "save":
                            self.selected_query_index = value
                            self.running = False
                        elif command == "analyze":
                            self.selected_query_index = value
                            self.analyze_requested = True
                            self.running = False
                    except queue.Empty:
                        pass

                    time.sleep(0.5)

        except KeyboardInterrupt:
            self.quit_requested = True

        finally:
            # Ensure terminal is properly restored after Live exits
            # This is critical because the daemon keypress thread may not
            # get a chance to restore settings when interrupted by Ctrl+C
            self._restore_terminal()

    def _restore_terminal(self):
        """Restore terminal to normal state after Live display exits.

        Ensures:
        - Cursor is visible
        - Alternate screen buffer is exited
        - Terminal settings are restored (echo, canonical mode)
        """
        import sys
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
                    fd = sys.stdin.fileno()
                    # Get current settings and restore to sane defaults
                    try:
                        # Try to restore canonical mode and echo
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
                    pass

        except Exception:
            # Best effort - don't let cleanup failure cause issues
            pass


def format_query_for_save(query: QueryMetrics) -> dict:
    """
    Format QueryMetrics for saving to query registry.

    Args:
        query: QueryMetrics object

    Returns:
        Dict with query info for registry
    """
    return {
        "query_text": query.query_text,
        "query_hash": query.query_hash,
        "max_duration_ms": query.max_duration_seen,
        "avg_duration_ms": query.avg_duration,
        "observation_count": query.observation_count,
        "captured_from": "rdst_top_realtime",
    }

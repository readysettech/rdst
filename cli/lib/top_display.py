"""
Real-time display for RDST Top command using Rich library.

Provides live-updating table showing top 10 queries with keyboard interaction.
"""

import time
import sys
import threading
from typing import List, Optional, Callable
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text

from lib.top_monitor import QueryMetrics


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

    def __init__(self, console: Optional[Console] = None, db_engine: str = None):
        """Initialize display.

        Args:
            console: Optional Rich console instance
            db_engine: Database engine type ('mysql' or 'postgresql')
        """
        self.console = console or Console()
        self.db_engine = db_engine
        self.running = False
        self.selected_query_index = None
        self.save_all_requested = False
        self.analyze_requested = False
        self.quit_requested = False
        self.current_queries = []  # Store latest queries for saving

    def create_table(self, queries: List[QueryMetrics], runtime_seconds: float,
                     total_tracked: int, db_engine: str = None, auto_saved_count: int = 0) -> Layout:
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
        layout = Layout()

        # Header
        header_text = Text()
        header_text.append("RDST Top - Real-Time Query Monitor\n", style="bold cyan")
        header_text.append(f"Runtime: {int(runtime_seconds)}s  ", style="dim")
        header_text.append(f"Queries Tracked: {total_tracked}  ", style="dim")
        if auto_saved_count > 0:
            header_text.append(f"Auto-Saved to Registry: {auto_saved_count}  ", style="green")
        header_text.append(f"Polling: 200ms\n", style="dim")

        # MySQL-specific limitation warning
        if db_engine and db_engine.lower() == 'mysql':
            header_text.append("\n", style="dim")
            header_text.append("⚠  MySQL Limitations: ", style="yellow bold")
            header_text.append("Queries <1s may not be tracked. ", style="yellow")
            header_text.append("Duration has 1-second granularity (using PROCESSLIST.TIME)", style="yellow")

        header_text.append("\n\n", style="bold green")
        header_text.append("Press Ctrl+C to exit.", style="cyan")

        # Create table
        table = Table(
            title="Top 10 Slowest Queries (by Max Duration Observed)",
            show_header=True,
            header_style="bold magenta",
            border_style="blue"
        )

        table.add_column("#", style="cyan", width=3)
        table.add_column("Max Duration", style="red bold", width=12)
        table.add_column("Avg Duration", style="yellow", width=12)
        table.add_column("Observations", style="green", width=12)
        table.add_column("Instances Running", style="blue", width=18)
        table.add_column("Query", style="white", no_wrap=True)

        # Add rows
        for idx, query in enumerate(queries):
            # Format durations
            max_dur = f"{query.max_duration_seen:,.1f}ms"
            avg_dur = f"{query.avg_duration:,.1f}ms"
            obs_count = str(query.observation_count)
            running_now = str(query.current_instances_running)

            # Use normalized (parameterized) query for display
            query_text = query.normalized_query if query.normalized_query else query.query_text
            # Collapse whitespace and truncate for single-line display
            query_text = ' '.join(query_text.split())
            if len(query_text) > 100:
                query_text = query_text[:97] + '...'

            # Highlight if currently running
            style = "bold" if query.current_instances_running > 0 else "dim"

            table.add_row(
                str(idx),
                max_dur,
                avg_dur,
                obs_count,
                running_now,
                query_text,
                style=style
            )

        # If fewer than 10 queries, add empty rows
        for idx in range(len(queries), 10):
            table.add_row(
                str(idx),
                "-",
                "-",
                "-",
                "-",
                "-",
                style="dim"
            )

        # Combine header and table
        layout.split_column(
            Layout(Panel(header_text, border_style="cyan"), size=8),
            Layout(table)
        )

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
                            ch = os.read(fd, 1).decode('utf-8')

                            # Check for ESC (ASCII 27) or 'q'
                            if ch == '\x1b' or ch == 'q':
                                self.quit_requested = True
                                self.running = False
                                break

                            # Check for 0-9
                            elif ch.isdigit():
                                self.selected_query_index = int(ch)
                                self.running = False
                                break

                            # Check for 'a' (all)
                            elif ch == 'a':
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

            except Exception as e:
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

        # Create static footer panel (shown once at bottom)
        footer_text = Text()
        footer_text.append("\nCommands: ", style="bold cyan")
        footer_text.append("Press 0-9", style="green")
        footer_text.append(" (save) | ", style="dim")
        footer_text.append("a", style="green")
        footer_text.append(" (save all) | ", style="dim")
        footer_text.append("z+0-9", style="yellow")
        footer_text.append(" (analyze) | ", style="dim")
        footer_text.append("q", style="red")
        footer_text.append(" (quit) | ", style="dim")
        footer_text.append("Ctrl+C", style="red")
        footer_text.append(" (quit)", style="dim")

        footer_panel = Panel(footer_text, border_style="cyan", title="[bold]Quick Actions[/bold]")

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
                            ch = os.read(fd, 1).decode('utf-8', errors='ignore')

                            if ch == '\x03' or ch == 'q':  # Ctrl+C or q
                                command_queue.put(('quit', None))
                                break
                            elif ch == 'a':  # Save all
                                command_queue.put(('save_all', None))
                                break
                            elif ch == 'z':  # Analyze mode
                                waiting_for_analyze_index = True
                                continue
                            elif ch.isdigit():
                                if waiting_for_analyze_index:
                                    command_queue.put(('analyze', int(ch)))
                                    waiting_for_analyze_index = False
                                    break
                                else:
                                    command_queue.put(('save', int(ch)))
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
                    table_layout = self.create_table(queries, runtime, total_tracked, self.db_engine, auto_saved_count)

                    # Combine table and footer
                    main_layout = Layout()
                    main_layout.split_column(
                        table_layout,
                        Layout(footer_panel, size=5)
                    )

                    live.update(main_layout)

                    # Check for commands (non-blocking)
                    try:
                        command, value = command_queue.get_nowait()
                        if command == 'quit':
                            self.quit_requested = True
                            self.running = False
                        elif command == 'save_all':
                            self.save_all_requested = True
                            self.running = False
                        elif command == 'save':
                            self.selected_query_index = value
                            self.running = False
                        elif command == 'analyze':
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
                sys.stdout.write('\033[?25h')  # Show cursor
                sys.stdout.write('\033[?1049l')  # Exit alternate screen buffer
                sys.stdout.flush()

            # Restore terminal settings on Unix
            if os.name == 'posix':
                try:
                    import termios
                    import tty

                    fd = sys.stdin.fileno()
                    # Get current settings and restore to sane defaults
                    try:
                        # Try to restore canonical mode and echo
                        import subprocess
                        subprocess.run(['stty', 'sane'], check=False,
                                      stdin=sys.stdin, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
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
        'query_text': query.query_text,
        'query_hash': query.query_hash,
        'max_duration_ms': query.max_duration_seen,
        'avg_duration_ms': query.avg_duration,
        'observation_count': query.observation_count,
        'captured_from': 'rdst_top_realtime'
    }

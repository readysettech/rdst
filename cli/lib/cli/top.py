"""
RDST Top Command Module

This module contains all the functionality for the 'rdst top' command,
providing live views of top slow queries from database telemetry.

Refactored to use the event-driven service architecture with TopService
and TopRenderer for both CLI and Web API support.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import sys
import threading
import time
from dataclasses import asdict
from typing import TYPE_CHECKING, List, Optional

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..functions.db_config_check import TargetConfig

# Import UI system - handles Rich availability internally
from lib.ui import (
    DataTable,
    EmptyState,
    Group,
    Live,
    MessagePanel,
    NextSteps,
    NoticePanel,
    Prompt,
    SectionBox,
    StatusLine,
    StyleTokens,
    TopQueryTable,
    create_console,
    get_console,
)

# Import shared utilities
from ..query_registry import hash_sql


class TopCommand:
    """Handles all functionality for the rdst top command.

    Uses TopService for data fetching and TopRenderer for display.
    Keyboard handling remains here as it's CLI-specific.
    """

    def __init__(self, client=None):
        """Initialize the TopCommand with an optional CloudAgentClient."""
        self.client = client
        self._console = get_console()

    def execute(
        self,
        target: str = None,
        source: str = "auto",
        limit: int = 10,
        sort: str = "total_time",
        filter: str = None,
        json: bool = False,
        watch: bool = False,
        no_color: bool = False,
        interactive: bool = False,
        historical: bool = False,
        duration: int = None,
        **kwargs,
    ):
        """Live view of top slow queries from database telemetry.

        Default: Real-time monitoring polling pg_stat_activity/PROCESSLIST every 200ms
        --historical: Historical statistics from pg_stat_statements/performance_schema
        --duration N: Run real-time Top for N seconds then output results (snapshot mode)
        """
        from .rdst_cli import RdstResult

        try:
            # Route based on mode
            if not historical:
                # DEFAULT: Real-time monitoring using TopService
                return self._run_realtime_with_service(
                    target=target,
                    limit=limit,
                    json_output=json,
                    duration=duration,
                    no_color=no_color,
                )

            # HISTORICAL MODE: Use TopService with appropriate options
            if watch:
                return self._run_watch_with_service(
                    target=target,
                    source=source,
                    limit=limit,
                    sort=sort,
                    filter_pattern=filter,
                    no_color=no_color,
                )
            elif interactive:
                return self._run_interactive_with_service(
                    target=target,
                    source=source,
                    limit=limit,
                    sort=sort,
                    filter_pattern=filter,
                    no_color=no_color,
                )
            else:
                return self._run_snapshot_with_service(
                    target=target,
                    source=source,
                    limit=limit,
                    sort=sort,
                    filter_pattern=filter,
                    json_output=json,
                    no_color=no_color,
                )

        except KeyboardInterrupt:
            self._force_restore_terminal()
            return RdstResult(True, "\nTop view cancelled by user")
        except Exception as e:
            self._force_restore_terminal()
            import traceback

            error_msg = f"top failed: {e}"
            if kwargs.get("verbose"):
                error_msg += f"\n{traceback.format_exc()}"
            return RdstResult(False, error_msg)

    def _run_realtime_with_service(
        self,
        target: Optional[str],
        limit: int,
        json_output: bool,
        duration: Optional[int],
        no_color: bool,
    ):
        """Real-time mode using TopService.stream_realtime().

        This mode shows a live-updating display of currently running queries.
        Supports keyboard shortcuts for saving and analyzing queries.
        """
        from .rdst_cli import RdstResult
        from .top_renderer import TopRenderer, render_top_queries_json
        from ..services.top_service import TopService
        from ..services.types import (
            TopCompleteEvent,
            TopConnectedEvent,
            TopErrorEvent,
            TopInput,
            TopOptions,
            TopQueriesEvent,
            TopQuerySavedEvent,
        )

        # If json_output requested without duration, auto-set a short snapshot
        if json_output and duration is None:
            duration = 2

        service = TopService()
        input_data = TopInput(target=target, source="activity")
        options = TopOptions(limit=limit, poll_interval_ms=200, auto_save_registry=True)

        # For snapshot mode (duration specified or json), collect and return
        if duration:
            return self._run_snapshot_realtime(
                service, input_data, options, duration, json_output
            )

        # Interactive mode with Live display
        renderer = TopRenderer(realtime=True, no_color=no_color)

        # Track state for keyboard handling
        running = True
        selected_query_index = None
        save_all_requested = False
        analyze_requested = False
        quit_requested = False
        target_name = ""
        newly_saved = 0
        error_event = None  # Capture errors to display after Live cleanup

        # Set up keyboard listener
        command_queue = queue.Queue()

        def keypress_thread():
            """Background thread to capture single keypresses."""
            nonlocal running
            waiting_for_analyze_index = False

            try:
                import fcntl
                import select
                import termios
                import tty

                old_settings = termios.tcgetattr(sys.stdin)
                try:
                    tty.setcbreak(sys.stdin.fileno())
                    fd = sys.stdin.fileno()

                    while running:
                        try:
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
            except ImportError:
                # Windows doesn't have these modules
                pass
            except OSError:
                # Non-TTY stdin (e.g., pytest redirected pseudofile)
                pass

        # Start keyboard listener
        try:
            import fcntl

            listener = threading.Thread(target=keypress_thread, daemon=True)
            listener.start()
        except ImportError:
            pass

        error_event = None
        live_started = False

        async def run_async():
            nonlocal running, selected_query_index, save_all_requested
            nonlocal analyze_requested, quit_requested, target_name, newly_saved
            nonlocal error_event, live_started
            try:
                async for event in service.stream_realtime(input_data, options, None):
                    # Handle errors BEFORE starting Live display
                    if isinstance(event, TopErrorEvent) and not live_started:
                        error_event = event
                        running = False
                        break

                    # Start Live display only after successful connection
                    if isinstance(event, TopConnectedEvent) and not live_started:
                        target_name = event.target_name
                        renderer.start_live()
                        live_started = True

                    # Track state
                    if isinstance(event, TopQuerySavedEvent):
                        if event.is_new:
                            newly_saved += 1
                    elif isinstance(event, TopErrorEvent):
                        # Don't render to Live display — it would be lost
                        # when the alternate screen buffer exits. Capture
                        # the event and display it after cleanup.
                        error_event = event
                        running = False
                        break

                    # Render non-error events to Live display
                    if live_started:
                        renderer.render(event)

                    # Check for keyboard commands
                    try:
                        command, value = command_queue.get_nowait()
                        if command == "quit":
                            quit_requested = True
                            running = False
                            break
                        elif command == "save_all":
                            save_all_requested = True
                            running = False
                            break
                        elif command == "save":
                            selected_query_index = value
                            running = False
                            break
                        elif command == "analyze":
                            selected_query_index = value
                            analyze_requested = True
                            running = False
                            break
                    except queue.Empty:
                        pass

                    if not running:
                        break

            except KeyboardInterrupt:
                quit_requested = True
            finally:
                if live_started:
                    renderer.cleanup()

        try:
            asyncio.run(run_async())
        except KeyboardInterrupt:
            quit_requested = True
            renderer.cleanup()

        # Display error after Live cleanup so it's visible on the normal terminal
        if error_event is not None:
            if not live_started:
                # Error before Live display started — restore terminal and show clearly
                self._force_restore_terminal()
                self._console.print(
                    MessagePanel(error_event.message, title="Error", variant="error")
                )
            else:
                renderer.render(error_event)
            return RdstResult(False, error_event.message)

        # Handle post-exit actions
        current_queries = renderer.get_current_queries()

        # Show exit breadcrumb
        if newly_saved > 0:
            self._console.print(
                f"\n[{StyleTokens.SUCCESS}]Top saved {newly_saved} new queries to registry.[/{StyleTokens.SUCCESS}]"
            )
            if current_queries:
                steps = []
                for q in current_queries[:3]:
                    h = q.query_hash
                    preview = (
                        q.normalized_query[:50] + "..."
                        if len(q.normalized_query) > 50
                        else q.normalized_query
                    )
                    steps.append(
                        (
                            f"rdst [{StyleTokens.SUCCESS}]analyze[/{StyleTokens.SUCCESS}] --hash [{StyleTokens.ACCENT}]{h[:12]}[/{StyleTokens.ACCENT}] --target [{StyleTokens.ACCENT}]{target_name}[/{StyleTokens.ACCENT}]",
                            preview,
                        )
                    )
                if len(current_queries) > 3:
                    steps.append(
                        (
                            f"rdst [{StyleTokens.SUCCESS}]query list[/{StyleTokens.SUCCESS}]",
                            "View all saved queries",
                        )
                    )
                self._console.print(NextSteps(steps))
        else:
            self._console.print(
                NextSteps(
                    [
                        (
                            f"rdst [{StyleTokens.SUCCESS}]query list[/{StyleTokens.SUCCESS}]",
                            "View saved queries",
                        ),
                        (
                            f'rdst [{StyleTokens.SUCCESS}]analyze[/{StyleTokens.SUCCESS}] -q [{StyleTokens.ACCENT}]"SELECT ..."[/{StyleTokens.ACCENT}] --target [{StyleTokens.ACCENT}]{target_name}[/{StyleTokens.ACCENT}]',
                            "Analyze a specific query",
                        ),
                    ]
                )
            )

        # Handle user actions
        if save_all_requested and current_queries:
            self._save_queries_to_registry_from_top(
                [
                    {
                        "query_text": q.query_text,
                        "normalized_query": q.normalized_query,
                        "query_hash": q.query_hash,
                        "max_duration_ms": q.max_duration_ms or 0,
                        "avg_duration_ms": float(q.avg_time.rstrip("s")) * 1000
                        if q.avg_time
                        else 0,
                        "observation_count": q.observation_count or 0,
                    }
                    for q in current_queries
                ],
                None,
                target_name,
            )

        elif selected_query_index is not None and current_queries:
            if analyze_requested:
                if selected_query_index < len(current_queries):
                    query = current_queries[selected_query_index]
                    self._console.print(
                        f"\n[{StyleTokens.INFO}]Running analyze on query [{selected_query_index}]...[/{StyleTokens.INFO}]"
                    )
                    query_display = query.normalized_query or query.query_text
                    self._console.print(
                        f"[{StyleTokens.WARNING}]Query:[/{StyleTokens.WARNING}] {query_display}\n"
                    )

                    # Call rdst analyze via subprocess
                    import subprocess

                    cmd = [
                        sys.executable,
                        "rdst.py",
                        "analyze",
                        "--target",
                        target_name,
                        "--query",
                        query.query_text,
                        "--interactive",
                    ]

                    try:
                        subprocess.run(
                            cmd, check=False, stdin=None, stdout=None, stderr=None
                        )
                    except Exception as e:
                        self._console.print(
                            f"[{StyleTokens.ERROR}]Error running analyze: {e}[/{StyleTokens.ERROR}]"
                        )
            else:
                # Save selected query
                if selected_query_index < len(current_queries):
                    query = current_queries[selected_query_index]
                    self._save_queries_to_registry_from_top(
                        [
                            {
                                "query_text": query.query_text,
                                "normalized_query": query.normalized_query,
                                "query_hash": query.query_hash,
                                "max_duration_ms": query.max_duration_ms or 0,
                                "avg_duration_ms": float(query.avg_time.rstrip("s"))
                                * 1000
                                if query.avg_time
                                else 0,
                                "observation_count": query.observation_count or 0,
                            }
                        ],
                        [0],
                        target_name,
                    )

        return RdstResult(True, "Real-time monitoring stopped")

    def _run_snapshot_realtime(
        self,
        service,
        input_data,
        options,
        duration: int,
        json_output: bool,
    ):
        """Run realtime monitoring for a fixed duration and return results."""
        from .rdst_cli import RdstResult
        from .top_renderer import render_top_queries_json
        from ..services.types import TopCompleteEvent, TopErrorEvent, TopQueriesEvent

        result_data = None
        target_name = ""
        db_engine = ""

        async def run_async():
            nonlocal result_data, target_name, db_engine

            async for event in service.stream_realtime(input_data, options, duration):
                if hasattr(event, "target_name"):
                    target_name = event.target_name
                if hasattr(event, "db_engine"):
                    db_engine = event.db_engine

                if isinstance(event, TopCompleteEvent):
                    result_data = event
                elif isinstance(event, TopErrorEvent):
                    return RdstResult(False, event.message)

        asyncio.run(run_async())

        if result_data is None:
            return RdstResult(False, "No results collected")

        if json_output:
            output = render_top_queries_json(
                queries=result_data.queries,
                target_name=target_name,
                db_engine=db_engine,
                source="activity",
                runtime_seconds=duration,
                total_tracked=len(result_data.queries),
            )
            return RdstResult(True, "", data=output)
        else:
            # Text output
            lines = []
            lines.append(f"RDST Top - Snapshot Mode ({duration}s)")
            lines.append(f"Target: {target_name} ({db_engine})")
            lines.append(
                f"Runtime: {duration}s | Total Queries Tracked: {len(result_data.queries)}"
            )
            lines.append("")
            lines.append(
                "Top {} Slowest Queries (by Max Duration):".format(options.limit)
            )
            lines.append("-" * 120)
            lines.append(
                f"{'#':<3} | {'Hash':<12} | {'Max Duration':<12} | {'Avg Duration':<12} | {'Observations':<12} | {'Running Now':<12} | {'Query'}"
            )
            lines.append("-" * 120)

            for idx, query in enumerate(result_data.queries):
                max_dur = (
                    f"{query.max_duration_ms:,.1f}ms"
                    if query.max_duration_ms
                    else query.total_time
                )
                try:
                    avg_s = float(query.avg_time.rstrip("s"))
                    avg_dur = f"{avg_s * 1000:,.1f}ms"
                except (ValueError, AttributeError):
                    avg_dur = query.avg_time
                obs_count = str(query.observation_count or query.freq)
                running_now = str(query.current_instances or 0)
                query_text = query.normalized_query[:60] + (
                    "..." if len(query.normalized_query) > 60 else ""
                )

                lines.append(
                    f"{idx:<3} | {query.query_hash[:12]:<12} | {max_dur:<12} | {avg_dur:<12} | {obs_count:<12} | {running_now:<12} | {query_text}"
                )

            return RdstResult(True, "\n".join(lines))

    def _run_snapshot_with_service(
        self,
        target: Optional[str],
        source: str,
        limit: int,
        sort: str,
        filter_pattern: Optional[str],
        json_output: bool,
        no_color: bool,
    ):
        """Single historical snapshot using TopService."""
        from .rdst_cli import RdstResult
        from .top_renderer import TopRenderer, render_top_queries_json
        from ..services.top_service import TopService
        from ..services.types import (
            TopCompleteEvent,
            TopErrorEvent,
            TopInput,
            TopOptions,
            TopQueriesEvent,
        )

        service = TopService()
        renderer = TopRenderer(no_color=no_color)

        input_data = TopInput(target=target, source=source)
        options = TopOptions(
            limit=limit,
            sort=sort,
            filter_pattern=filter_pattern,
            auto_save_registry=True,
        )

        result = None
        target_name = ""
        db_engine = ""
        actual_source = source

        async def run_async():
            nonlocal result, target_name, db_engine, actual_source

            async for event in service.get_top_queries(input_data, options):
                if not json_output:
                    renderer.render(event)

                if hasattr(event, "target_name"):
                    target_name = event.target_name
                if hasattr(event, "db_engine"):
                    db_engine = event.db_engine
                if hasattr(event, "source"):
                    actual_source = event.source

                if isinstance(event, TopCompleteEvent):
                    result = event
                elif isinstance(event, TopErrorEvent):
                    return

        asyncio.run(run_async())

        if result is None:
            return RdstResult(False, "Failed to get top queries")

        if json_output:
            output = render_top_queries_json(
                queries=result.queries,
                target_name=target_name,
                db_engine=db_engine,
                source=actual_source,
            )
            return RdstResult(True, "", data=output)

        return RdstResult(True, "")

    def _run_watch_with_service(
        self,
        target: Optional[str],
        source: str,
        limit: int,
        sort: str,
        filter_pattern: Optional[str],
        no_color: bool,
    ):
        """Watch mode: Call service repeatedly in loop with Live display."""
        from .rdst_cli import RdstResult
        from .top_renderer import TopRenderer
        from ..services.top_service import TopService
        from ..services.types import (
            TopCompleteEvent,
            TopErrorEvent,
            TopInput,
            TopOptions,
            TopQueriesEvent,
        )

        service = TopService()
        input_data = TopInput(target=target, source=source)
        options = TopOptions(
            limit=limit,
            sort=sort,
            filter_pattern=filter_pattern,
            auto_save_registry=False,  # Don't auto-save in watch mode
        )

        if no_color:
            # Fallback to simple refresh
            return self._run_watch_simple(service, input_data, options)

        # Use Rich Live for smooth updates
        target_name = ""
        db_engine = ""
        actual_source = source
        current_queries = []

        def generate_table():
            """Generate the current top queries table."""
            if not current_queries:
                return EmptyState(
                    "No active queries found.",
                    title="rdst top",
                    suggestion="Run some database queries in another session to see them here.",
                )

            import datetime

            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            title = (
                f"rdst top - {timestamp} - {target_name} ({db_engine}) - {actual_source}"
            )

            queries_data = [
                {
                    "query_hash": q.query_hash,
                    "query_text": q.query_text,
                    "freq": q.freq,
                    "total_time": q.total_time,
                    "avg_time": q.avg_time,
                    "pct_load": q.pct_load,
                }
                for q in current_queries
            ]

            table = TopQueryTable(
                queries=queries_data,
                source=actual_source,
                target_name=target_name,
                db_engine=db_engine,
                title=title,
            )

            status_panel = MessagePanel(
                "Press Ctrl+C to exit - Refreshing every 5 seconds",
                variant="info",
            )

            return Group(table, status_panel)

        async def fetch_once():
            nonlocal current_queries, target_name, db_engine, actual_source

            async for event in service.get_top_queries(input_data, options):
                if hasattr(event, "target_name"):
                    target_name = event.target_name
                if hasattr(event, "db_engine"):
                    db_engine = event.db_engine
                if hasattr(event, "source"):
                    actual_source = event.source

                if isinstance(event, TopQueriesEvent):
                    current_queries = event.queries
                elif isinstance(event, TopErrorEvent):
                    raise Exception(event.message)

        try:
            with Live(
                generate_table(), refresh_per_second=0.2
            ) as live:
                while True:
                    asyncio.run(fetch_once())
                    live.update(generate_table())
                    time.sleep(5)
        except KeyboardInterrupt:
            pass
        finally:
            self._restore_terminal()

        return RdstResult(True, "\nWatch mode stopped")

    def _run_watch_simple(self, service, input_data, options):
        """Simple watch mode without Rich Live (for no_color mode)."""
        from .rdst_cli import RdstResult
        from ..services.types import TopCompleteEvent, TopErrorEvent, TopQueriesEvent

        def clear_screen():
            if os.name == "posix":
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.flush()
            else:
                os.system("cls")

        clear_screen()
        first_run = True

        try:
            while True:
                if not first_run:
                    if os.name == "posix":
                        sys.stdout.write("\033[H")
                        sys.stdout.flush()
                first_run = False

                try:
                    target_name = ""
                    db_engine = ""
                    actual_source = ""
                    queries = []

                    async def fetch():
                        nonlocal target_name, db_engine, actual_source, queries

                        async for event in service.get_top_queries(
                            input_data, options
                        ):
                            if hasattr(event, "target_name"):
                                target_name = event.target_name
                            if hasattr(event, "db_engine"):
                                db_engine = event.db_engine
                            if hasattr(event, "source"):
                                actual_source = event.source
                            if isinstance(event, TopQueriesEvent):
                                queries = event.queries

                    asyncio.run(fetch())

                    # Print output
                    import datetime

                    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                    print(
                        f"rdst top - {timestamp} - {target_name} ({db_engine}) - {actual_source}"
                    )
                    print("-" * 80)

                    for i, q in enumerate(queries):
                        query_text = q.query_text[:60] + (
                            "..." if len(q.query_text) > 60 else ""
                        )
                        print(
                            f"{i}: {q.query_hash[:8]} | {q.total_time} | {q.freq} calls | {query_text}"
                        )

                    print("\nPress Ctrl+C to exit - Refreshing every 5 seconds")

                    if os.name == "posix":
                        sys.stdout.write("\033[J")
                        sys.stdout.flush()

                except Exception as e:
                    print(f"Error refreshing data: {e}")

                time.sleep(5)

        except KeyboardInterrupt:
            if os.name == "posix":
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.flush()
            return RdstResult(True, "\nWatch mode stopped")

    def _run_interactive_with_service(
        self,
        target: Optional[str],
        source: str,
        limit: int,
        sort: str,
        filter_pattern: Optional[str],
        no_color: bool,
    ):
        """Interactive mode: Get queries, then prompt for selection."""
        from .rdst_cli import RdstResult
        from .top_renderer import TopRenderer
        from ..services.top_service import TopService
        from ..services.types import (
            TopCompleteEvent,
            TopErrorEvent,
            TopInput,
            TopOptions,
            TopQueriesEvent,
        )

        service = TopService()
        renderer = TopRenderer(no_color=no_color)

        input_data = TopInput(target=target, source=source)
        options = TopOptions(
            limit=limit,
            sort=sort,
            filter_pattern=filter_pattern,
            auto_save_registry=False,  # Don't auto-save - user will select
        )

        result = None
        target_name = ""
        db_engine = ""

        async def run_async():
            nonlocal result, target_name, db_engine

            async for event in service.get_top_queries(input_data, options):
                renderer.render(event)

                if hasattr(event, "target_name"):
                    target_name = event.target_name
                if hasattr(event, "db_engine"):
                    db_engine = event.db_engine

                if isinstance(event, TopCompleteEvent):
                    result = event
                elif isinstance(event, TopErrorEvent):
                    return

        asyncio.run(run_async())

        if result is None or not result.queries:
            return RdstResult(False, f"No queries found for target '{target_name}'")

        # Display queries with numbers for selection
        self._display_interactive_queries(
            result.queries, result.source, target_name, db_engine
        )

        # Get user selection
        while True:
            try:
                prompt_text = f"\n[{StyleTokens.HEADER}]Select query to analyze[/{StyleTokens.HEADER}] ([{StyleTokens.WARNING}]1-{len(result.queries)}[/{StyleTokens.WARNING}], [{StyleTokens.ERROR}]q[/{StyleTokens.ERROR}] to quit)"
                choice = Prompt.ask(prompt_text, default="", show_default=False)

                if choice.lower() in ["q", "quit", "exit"]:
                    return RdstResult(True, "Selection cancelled")

                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(result.queries):
                        selected_query = result.queries[idx]
                        return self._analyze_selected_query_from_top(
                            selected_query, target_name
                        )
                    else:
                        self._console.print(
                            MessagePanel(
                                f"Invalid selection. Please enter 1-{len(result.queries)} or 'q'",
                                variant="warning",
                            )
                        )
                except ValueError:
                    self._console.print(
                        MessagePanel(
                            f"Invalid input. Please enter a number (1-{len(result.queries)}) or 'q'",
                            variant="warning",
                        )
                    )

            except (KeyboardInterrupt, EOFError):
                return RdstResult(True, "\nSelection cancelled")

    def _display_interactive_queries(
        self, queries, source: str, target_name: str, db_engine: str
    ):
        """Display queries with selection numbers."""
        columns = ["#", "HASH", "QUERY", "FREQ", "TOTAL TIME"]
        rows = []
        for i, query in enumerate(queries, 1):
            query_display = query.query_text[:50] + (
                "..." if len(query.query_text) > 50 else ""
            )
            rows.append(
                [
                    str(i),
                    query.query_hash[:12],
                    query_display,
                    str(query.freq),
                    query.total_time,
                ]
            )

        title = f"Select Query for Analysis - {target_name} ({db_engine}) - {source}"
        table = DataTable(columns=columns, rows=rows, title=title)
        self._console.print(table)

    def _analyze_selected_query_from_top(self, selected_query, target_name: str):
        """Analyze the selected query from interactive mode."""
        from ..query_registry import QueryRegistry
        from .rdst_cli import RdstResult
        from ..data_manager_service.data_manager_service_command_sets import (
            MAX_QUERY_LENGTH,
        )

        try:
            query_text = selected_query.query_text
            query_bytes = len(query_text.encode("utf-8")) if query_text else 0

            if query_bytes > MAX_QUERY_LENGTH:
                # Query exceeds 4KB limit - cannot save to registry
                return RdstResult(
                    False,
                    f"Query size ({query_bytes:,} bytes) exceeds the 4KB limit.\n\n"
                    "Queries captured from 'rdst top' cannot exceed 4KB.\n"
                    "To analyze this query, get the full SQL from your application and run:\n"
                    f"  rdst analyze --large-query-bypass '<full query>'\n\n"
                    "This allows one-time analysis of queries up to 10KB.",
                )

            # Store query in registry
            registry = QueryRegistry()
            query_hash, is_new = registry.add_query(
                sql=query_text,
                source="top",
                frequency=selected_query.freq if isinstance(selected_query.freq, int) else 0,
                target="",
            )

            # Import and run analyze
            from .analyze_command import AnalyzeCommand, AnalyzeInput
            from ..query_registry.query_registry import normalize_sql

            analyze_cmd = AnalyzeCommand()
            query_sql = query_text
            normalized_sql = normalize_sql(query_sql)

            resolved_input = AnalyzeInput(
                sql=query_sql,
                normalized_sql=normalized_sql,
                source="top",
                hash=query_hash,
                tag="",
                save_as="",
            )

            self._console.print(
                StatusLine("Status", "Running analysis...", style=StyleTokens.INFO)
            )

            return analyze_cmd.execute_analyze(resolved_input, target=target_name)

        except Exception as e:
            return RdstResult(False, f"Analysis failed: {e}")

    def _save_queries_to_registry_from_top(
        self, queries: List[dict], selected_indices: Optional[List[int]], target_name: str
    ):
        """Save queries to query registry (from realtime mode)."""
        try:
            from ..query_registry import QueryRegistry
            from ..query_registry.query_registry import generate_query_name
        except ImportError:
            self._console.print(
                f"[{StyleTokens.WARNING}]Query registry not available - skipping save[/{StyleTokens.WARNING}]"
            )
            return []

        try:
            registry = QueryRegistry()
            saved_queries = []
            new_count = 0
            existing_count = 0

            if selected_indices is None:
                indices_to_save = range(len(queries))
                self._console.print(
                    f"\n[{StyleTokens.INFO}]Saving all {len(queries)} queries to registry...[/{StyleTokens.INFO}]\n"
                )
            else:
                indices_to_save = selected_indices
                self._console.print(
                    f"\n[{StyleTokens.INFO}]Saving {len(selected_indices)} selected queries to registry...[/{StyleTokens.INFO}]\n"
                )

            existing_names = {e.tag for e in registry.list_queries() if e.tag}
            skipped_queries = []

            for idx in indices_to_save:
                if idx >= len(queries):
                    continue

                query = queries[idx]
                query_text = query.get("query_text", "")
                query_hash = query.get("query_hash", hash_sql(query_text))

                existing = registry.get_query(query_hash)

                if existing:
                    existing_count += 1
                    display_tag = existing.tag if existing.tag else None
                    status = (
                        f"[{StyleTokens.WARNING}]exists as '{display_tag}'[/{StyleTokens.WARNING}]"
                        if display_tag
                        else f"[{StyleTokens.WARNING}]exists[/{StyleTokens.WARNING}]"
                    )

                    saved_queries.append(
                        {
                            "index": idx,
                            "hash": query_hash[:8],
                            "query_text": query.get("normalized_query", query_text)[:80]
                            + "...",
                            "tag": display_tag or query_hash[:8],
                        }
                    )
                else:
                    normalized_query = query.get("normalized_query", query_text)
                    auto_name = generate_query_name(normalized_query, existing_names)
                    existing_names.add(auto_name)

                    try:
                        registry.add_query(
                            tag=auto_name,
                            sql=query_text,
                            source="top",
                            target=target_name,
                            max_duration_ms=query.get("max_duration_ms"),
                            avg_duration_ms=query.get("avg_duration_ms"),
                            observation_count=query.get("observation_count"),
                        )

                        new_count += 1
                        status = (
                            f"[{StyleTokens.SUCCESS}]new: '{auto_name}'[/{StyleTokens.SUCCESS}]"
                        )

                        saved_queries.append(
                            {
                                "index": idx,
                                "hash": query_hash[:8],
                                "query_text": normalized_query[:80] + "...",
                                "tag": auto_name,
                            }
                        )
                    except ValueError:
                        skipped_queries.append(idx)
                        status = (
                            f"[{StyleTokens.WARNING}]skipped (>4KB)[/{StyleTokens.WARNING}]"
                        )

                query_preview = query.get("normalized_query", query_text)[:70] + (
                    "..." if len(query.get("normalized_query", query_text)) > 70 else ""
                )
                self._console.print(
                    f"  [{idx}] {query_hash[:8]} - {query_preview} ({status})"
                )

            if skipped_queries:
                self._console.print(
                    f"\n[{StyleTokens.WARNING}]Note: {len(skipped_queries)} queries exceeded the 4KB limit and were not saved.[/{StyleTokens.WARNING}]"
                )
                self._console.print(
                    f"[{StyleTokens.WARNING}]Use 'rdst analyze --large-query-bypass' to analyze large queries.[/{StyleTokens.WARNING}]"
                )

            if new_count > 0 and existing_count > 0:
                self._console.print(
                    f"\n[{StyleTokens.SUCCESS}]Saved {new_count} new, {existing_count} already existed[/{StyleTokens.SUCCESS}]"
                )
            elif new_count > 0:
                self._console.print(
                    f"\n[{StyleTokens.SUCCESS}]Saved {new_count} queries[/{StyleTokens.SUCCESS}]"
                )
            else:
                self._console.print(
                    f"\n[{StyleTokens.WARNING}]All {existing_count} queries already in registry[/{StyleTokens.WARNING}]"
                )

            steps = [
                ("rdst query list", "View saved queries"),
            ]
            if saved_queries:
                example_query = saved_queries[0]
                if example_query.get("tag"):
                    steps.append(
                        (
                            f"rdst analyze --name {example_query['tag']}",
                            "Analyze a query",
                        )
                    )
                else:
                    steps.append(
                        (
                            f"rdst analyze --hash {example_query['hash']}",
                            "Analyze a query",
                        )
                    )
            self._console.print(NextSteps(steps))
            return saved_queries

        except Exception as e:
            self._console.print(
                f"[{StyleTokens.ERROR}]Error saving to registry: {e}[/{StyleTokens.ERROR}]"
            )
            return []

    def _force_restore_terminal(self):
        """Force-restore terminal to sane state. Called on ANY exit path from execute().

        This is the nuclear option — it always runs regardless of what state
        the renderer or Live display is in. Ensures the user never gets a
        broken terminal from rdst top crashing.
        """
        try:
            if sys.stdout.isatty():
                sys.stdout.write("\033[2J\033[H")  # Clear screen and home cursor
                sys.stdout.write("\033[?25h")  # Show cursor
                sys.stdout.write("\033[?1049l")  # Exit alternate screen buffer
                sys.stdout.flush()
        except Exception:
            pass

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

    def _restore_terminal(self):
        """Restore terminal to normal state after Live display exits."""
        self._force_restore_terminal()

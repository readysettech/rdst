"""
RDST Top Command Module

This module contains all the functionality for the 'rdst top' command,
providing live views of top slow queries from database telemetry.

Extracted from rdst_cli.py to improve code organization and maintainability.
"""
from __future__ import annotations

from typing import List, TYPE_CHECKING
import sys
import os

if TYPE_CHECKING:
    from ..functions.db_config_check import TargetConfig

try:
    # Rich is optional; if unavailable, we still work without pretty output
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.live import Live
    from rich.layout import Layout
    from rich.console import Group
    _RICH_AVAILABLE = True
except Exception:  # pragma: no cover - we keep imports soft
    _RICH_AVAILABLE = False
    Console = None  # type: ignore
    Panel = None  # type: ignore
    Table = None  # type: ignore
    Live = None  # type: ignore
    Layout = None  # type: ignore
    Group = None  # type: ignore

# Import shared utilities - avoid circular import by importing at function level
from ..query_registry import hash_sql


class TopCommand:
    """Handles all functionality for the rdst top command."""

    def __init__(self, client=None):
        """Initialize the TopCommand with an optional CloudAgentClient."""
        self.client = client
        self._console = Console() if _RICH_AVAILABLE else None

    def execute(self, target: str = None, source: str = "auto", limit: int = 10,
                sort: str = "total_time", filter: str = None, json: bool = False,
                watch: bool = False, no_color: bool = False, interactive: bool = False,
                historical: bool = False, duration: int = None, **kwargs):
        """Live view of top slow queries from database telemetry.

        Default: Real-time monitoring polling pg_stat_activity/PROCESSLIST every 200ms
        --historical: Historical statistics from pg_stat_statements/performance_schema
        --duration N: Run real-time Top for N seconds then output results (snapshot mode)
        """
        # Import shared classes to avoid circular imports
        from .rdst_cli import RdstResult, TargetsConfig, normalize_db_type

        try:
            # 1. Load and validate target configuration
            cfg = TargetsConfig()
            cfg.load()

            target_name = target or cfg.get_default()
            if not target_name:
                return RdstResult(False, "No target specified and no default configured. Run 'rdst configure' first.")

            target_config = cfg.get(target_name)
            if not target_config:
                available_targets = cfg.list_targets()
                targets_str = ', '.join(available_targets) if available_targets else 'none'
                return RdstResult(False, f"Target '{target_name}' not found. Available targets: {targets_str}")

            # 2. Determine database type and validate source
            db_engine = normalize_db_type(target_config.get('engine'))
            if not db_engine:
                return RdstResult(False, f"Invalid database engine for target '{target_name}': {target_config.get('engine')}")

            # 3. Route based on historical flag
            if not historical:
                # DEFAULT: Use new real-time monitoring (polls every 200ms)
                return self._run_realtime_monitor(target_config, db_engine, no_color, limit, json, duration)

            # HISTORICAL MODE: Use existing DataManager approach
            # 4. Auto-select source if needed and validate
            if source == "auto":
                source = self._auto_select_source(db_engine, target_config)

            if not self._validate_source_for_engine(source, db_engine):
                valid_sources = self._get_valid_sources_for_engine(db_engine)
                return RdstResult(False, f"Source '{source}' not supported for {db_engine}. Valid sources: {', '.join(valid_sources)}")

            # 5. Execute based on mode (historical)
            if watch:
                return self._run_watch_mode(target_config, db_engine, source, limit, sort, filter, no_color)
            elif interactive:
                return self._run_interactive_mode(target_config, db_engine, source, limit, sort, filter, no_color)
            else:
                return self._run_single_snapshot(target_config, db_engine, source, limit, sort, filter, json, no_color)

        except KeyboardInterrupt:
            return RdstResult(True, "\nTop view cancelled by user")
        except Exception as e:
            import traceback
            error_msg = f"top failed: {e}"
            if kwargs.get('verbose'):
                error_msg += f"\n{traceback.format_exc()}"
            return RdstResult(False, error_msg)

    def _auto_select_source(self, db_engine: str, target_config: TargetConfig) -> str:
        """Auto-select the best source for the database engine."""
        if db_engine == "postgresql":
            # Check if pg_stat_statements is likely available
            return "pg_stat"  # Try pg_stat_statements first, fallback in execution
        elif db_engine == "mysql":
            return "digest"   # performance_schema digest is best for MySQL
        else:
            return "activity" # fallback to live activity view

    def _validate_source_for_engine(self, source: str, db_engine: str) -> bool:
        """Validate that the source is supported for the database engine."""
        valid_sources = self._get_valid_sources_for_engine(db_engine)
        return source in valid_sources

    def _get_valid_sources_for_engine(self, db_engine: str) -> List[str]:
        """Get valid sources for a database engine."""
        if db_engine == "postgresql":
            return ["auto", "pg_stat", "activity"]  # slowlog, rds, pmm for v1.x
        elif db_engine == "mysql":
            return ["auto", "digest", "activity"]   # slowlog, rds, pmm for v1.x
        else:
            return ["auto", "activity"]

    def _get_command_set_for_source(self, db_engine: str, source: str) -> str:
        """Get the appropriate command set name for the database engine and source."""
        if db_engine == "postgresql":
            if source in ["pg_stat", "auto"]:
                return "rdst_top_pg_stat"
            elif source == "activity":
                return "rdst_top_pg_activity"
        elif db_engine == "mysql":
            if source in ["digest", "auto"]:
                return "rdst_top_mysql_digest"
            elif source == "activity":
                return "rdst_top_mysql_activity"

        raise ValueError(f"No command set available for engine='{db_engine}' source='{source}'")

    def _run_realtime_monitor(self, target_config: TargetConfig, db_engine: str, no_color: bool,
                             limit: int = 10, json_output: bool = False, duration: int = None):
        """Run new real-time monitoring (default behavior - polls every 200ms).

        Args:
            target_config: Database target configuration
            db_engine: Database engine type
            no_color: Disable ANSI color formatting
            limit: Number of top queries to show
            json_output: Output results as JSON
            duration: Run for N seconds then output results (snapshot mode)
        """
        from .rdst_cli import RdstResult
        from lib.top_realtime import run_realtime_monitor
        from rich.console import Console

        console = Console() if not no_color else Console(no_color=True)

        try:
            result = run_realtime_monitor(target_config, console, limit=limit,
                                         json_output=json_output, duration=duration)

            # If snapshot mode was used (--json or --duration), result contains the output data
            # Note: --json auto-enables snapshot mode in top_realtime.py
            if (json_output or duration) and result:
                return RdstResult(True, result)

            return RdstResult(True, "Real-time monitoring stopped")
        except KeyboardInterrupt:
            return RdstResult(True, "\nReal-time monitoring interrupted")
        except Exception as e:
            return RdstResult(False, f"Real-time monitoring failed: {e}")

    def _run_single_snapshot(self, target_config: TargetConfig, db_engine: str, source: str,
                            limit: int, sort: str, filter_pattern: str, json_output: bool, no_color: bool):
        """Run a single snapshot of top queries."""
        from .rdst_cli import RdstResult

        try:
            # 1. Execute the query via DataManager
            data = self._execute_top_query(target_config, db_engine, source)

            # 2. Process and format the results
            actual_source = data.get('source', source)  # Use actual source from execution
            processed_data = self._process_top_data(data, actual_source, limit, sort, filter_pattern)

            # 3. Output in requested format
            if json_output:
                return RdstResult(True, "", data={
                    "queries": processed_data,
                    "source": source,
                    "target": target_config.get("name", "unknown"),
                    "engine": db_engine
                })
            else:
                formatted_output = self._format_top_display(processed_data, actual_source, no_color, db_engine, target_config.get("name", "unknown"))
                return RdstResult(True, formatted_output)

        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            return RdstResult(False, f"Failed to get top queries: {e}\n{error_detail}")

    def _run_watch_mode(self, target_config: TargetConfig, db_engine: str, source: str,
                       limit: int, sort: str, filter_pattern: str, no_color: bool):
        """Run continuous watch mode with smooth screen updates."""
        from .rdst_cli import RdstResult
        import time
        import sys

        # Try to use Rich Live if available for smooth updates
        if _RICH_AVAILABLE and self._console and not no_color:
            return self._run_watch_mode_rich(target_config, db_engine, source, limit, sort, filter_pattern)

        # Fallback to terminal control sequences for smoother updates than os.system('clear')
        def clear_screen():
            if os.name == 'posix':
                # Use ANSI escape sequences for smoother clearing
                sys.stdout.write('\033[2J\033[H')  # Clear screen and move cursor to top
                sys.stdout.flush()
            else:
                os.system('cls')

        def move_cursor_home():
            if os.name == 'posix':
                sys.stdout.write('\033[H')  # Move cursor to home position
                sys.stdout.flush()

        # Initial clear
        clear_screen()
        first_run = True

        try:
            while True:
                # Only clear on first run, then just move cursor to home
                if first_run:
                    first_run = False
                else:
                    move_cursor_home()

                # Get latest data and display
                try:
                    data = self._execute_top_query(target_config, db_engine, source)
                    processed_data = self._process_top_data(data, source, limit, sort, filter_pattern)
                    formatted_output = self._format_top_display(processed_data, source, no_color, db_engine,
                                                              target_config.get("name", "unknown"), watch_mode=True)

                    # Print output and ensure we clear any leftover lines
                    lines = formatted_output.split('\n')
                    for i, line in enumerate(lines):
                        # Clear to end of line to remove any leftover text
                        if os.name == 'posix':
                            print(f"{line}\033[K")  # Print line and clear to end of line
                        else:
                            print(line)

                    # Add status line
                    status_line = "\nPress Ctrl+C to exit - Refreshing every 5 seconds"
                    if os.name == 'posix':
                        print(f"{status_line}\033[K")
                    else:
                        print(status_line)

                    # Clear any remaining lines from previous output
                    if os.name == 'posix':
                        sys.stdout.write('\033[J')  # Clear from cursor to end of screen
                        sys.stdout.flush()

                except Exception as e:
                    error_msg = f"Error refreshing data: {e}"
                    if os.name == 'posix':
                        print(f"{error_msg}\033[K")
                    else:
                        print(error_msg)

                # Wait before next update
                time.sleep(5)

        except KeyboardInterrupt:
            # Clean up terminal before exiting
            if os.name == 'posix':
                sys.stdout.write('\033[2J\033[H')  # Clear screen and move cursor to top
                sys.stdout.flush()
            return RdstResult(True, "\nWatch mode stopped")

    def _run_watch_mode_rich(self, target_config: TargetConfig, db_engine: str, source: str,
                           limit: int, sort: str, filter_pattern: str):
        """Run watch mode with Rich Live for smooth updates."""
        from .rdst_cli import RdstResult
        import time
        import datetime

        def generate_table():
            """Generate the current top queries table."""
            try:
                data = self._execute_top_query(target_config, db_engine, source)
                processed_data = self._process_top_data(data, source, limit, sort, filter_pattern)

                if not processed_data:
                    return Panel("No active queries found. Run some database queries in another session to see them here.", title="rdst top", border_style="yellow")

                # Create table
                table = Table(show_header=True, header_style="bold cyan")
                table.add_column("QUERY HASH", style="bright_blue", width=12)
                table.add_column("QUERY", style="white", width=20)
                table.add_column("FREQ", style="green", width=7)
                table.add_column("TOTAL TIME", style="yellow", width=10)
                table.add_column("AVG TIME", style="yellow", width=8)
                table.add_column("% LOAD", style="red", width=6)
                table.add_column("SOURCE", style="magenta", width=8)
                table.add_column("READYSET", style="blue", width=8)

                # Add rows
                for row in processed_data:
                    readyset_advice = "N/A"  # Placeholder for future readyset integration
                    query_display = row['query_text'][:20] + ("..." if len(row['query_text']) > 20 else "")
                    table.add_row(
                        row['query_hash'],
                        query_display,
                        str(row['freq']),
                        row['total_time'],
                        row['avg_time'],
                        row['pct_load'],
                        source,
                        readyset_advice
                    )

                # Create title with metadata and timestamp
                timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                target_name = target_config.get("name", "unknown")
                title = f"rdst top - {timestamp} - {target_name} ({db_engine}) - {source}"
                table.title = title

                # Create status panel
                status_panel = Panel(
                    "Press Ctrl+C to exit • Refreshing every 5 seconds",
                    style="dim",
                    border_style="dim"
                )

                return Group(table, status_panel)

            except Exception as e:
                error_panel = Panel(
                    f"Error refreshing data: {e}",
                    title="Error",
                    border_style="red"
                )
                return error_panel

        # Start Live display
        try:
            with Live(generate_table(), refresh_per_second=0.2, screen=True) as live:
                while True:
                    time.sleep(5)  # Update every 5 seconds
                    live.update(generate_table())
        except KeyboardInterrupt:
            pass
        finally:
            # Ensure terminal is properly restored after Live exits
            self._restore_terminal()
        return RdstResult(True, "\nWatch mode stopped")

    def _restore_terminal(self):
        """Restore terminal to normal state after Live display exits.

        Ensures cursor is visible, alternate screen buffer is exited,
        and terminal settings are restored.
        """
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
                    import subprocess
                    subprocess.run(['stty', 'sane'], check=False,
                                  stdin=sys.stdin, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
                except Exception:
                    pass
        except Exception:
            # Best effort - don't let cleanup failure cause issues
            pass

    def _run_interactive_mode(self, target_config: TargetConfig, db_engine: str, source: str,
                             limit: int, sort: str, filter_pattern: str, no_color: bool):
        """Run interactive mode where user can select queries for analysis."""
        from .rdst_cli import RdstResult

        try:
            # Get current data
            data = self._execute_top_query(target_config, db_engine, source)
            processed_data = self._process_top_data(data, source, limit, sort, filter_pattern)

            if not processed_data:
                return RdstResult(False, f"No queries found for target '{target_config.get('name', 'unknown')}'")

            # Display queries with numbers for selection
            self._display_interactive_queries(processed_data, source, target_config.get('name', 'unknown'), db_engine)

            # Get user selection
            while True:
                try:
                    if _RICH_AVAILABLE and self._console:
                        from rich.prompt import Prompt
                        choice = Prompt.ask(
                            "\n[bold cyan]Select query to analyze[/bold cyan] ([bold yellow]1-{}[/bold yellow], [bold red]q[/bold red] to quit)".format(len(processed_data)),
                            show_default=False
                        )
                    else:
                        choice = input(f"\nSelect query to analyze (1-{len(processed_data)}, 'q' to quit): ")

                    if choice.lower() in ['q', 'quit', 'exit']:
                        return RdstResult(True, "Selection cancelled")

                    # Try to parse as number
                    try:
                        idx = int(choice) - 1  # Convert to 0-based index
                        if 0 <= idx < len(processed_data):
                            selected_query = processed_data[idx]

                            # Run analyze on selected query
                            return self._analyze_selected_query(selected_query, target_config.get('name'))
                        else:
                            print(f"Invalid selection. Please enter 1-{len(processed_data)} or 'q'")
                    except ValueError:
                        print(f"Invalid input. Please enter a number (1-{len(processed_data)}) or 'q'")

                except (KeyboardInterrupt, EOFError):
                    return RdstResult(True, "\nSelection cancelled")

        except Exception as e:
            return RdstResult(False, f"Interactive mode failed: {e}")

    def _display_interactive_queries(self, queries: list, source: str, target_name: str, db_engine: str):
        """Display queries with selection numbers."""
        if _RICH_AVAILABLE and self._console:
            from rich.table import Table

            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("#", style="bold yellow", width=3, justify="right")
            table.add_column("HASH", style="bright_blue", width=12)
            table.add_column("QUERY", style="white", width=50)
            table.add_column("FREQ", style="green", width=7)
            table.add_column("TOTAL TIME", style="yellow", width=10)

            for i, query in enumerate(queries, 1):
                query_display = query['query_text'][:50] + ("..." if len(query['query_text']) > 50 else "")
                table.add_row(
                    str(i),
                    query['query_hash'],
                    query_display,
                    str(query['freq']),
                    query['total_time']
                )

            table.title = f"Select Query for Analysis - {target_name} ({db_engine}) - {source}"
            self._console.print(table)
        else:
            # Plain text fallback
            print(f"\nSelect Query for Analysis - {target_name} ({db_engine}) - {source}")
            print("-" * 80)
            print(f"{'#':<3} | {'HASH':<12} | {'QUERY':<35} | {'FREQ':<7} | {'TIME':<10}")
            print("-" * 80)

            for i, query in enumerate(queries, 1):
                query_display = query['query_text'][:35] + ("..." if len(query['query_text']) > 35 else "")
                print(f"{i:<3} | {query['query_hash']:<12} | {query_display:<35} | {query['freq']:<7} | {query['total_time']:<10}")

    def _analyze_selected_query(self, selected_query: dict, target_name: str):
        """Analyze the selected query."""
        from ..query_registry import QueryRegistry
        from .rdst_cli import RdstResult
        from ..data_manager_service.data_manager_service_command_sets import MAX_QUERY_LENGTH

        try:
            # Check query size before attempting to save
            query_text = selected_query['query_text']
            query_bytes = len(query_text.encode('utf-8')) if query_text else 0

            if query_bytes > MAX_QUERY_LENGTH:
                # Query exceeds 1KB limit - cannot save to registry
                return RdstResult(
                    False,
                    f"Query size ({query_bytes:,} bytes) exceeds the 1KB limit.\n\n"
                    "Queries captured from 'rdst top' cannot exceed 1KB.\n"
                    "To analyze this query, get the full SQL from your application and run:\n"
                    f"  rdst analyze --large-query-bypass '<full query>'\n\n"
                    "This allows one-time analysis of queries up to 10KB."
                )

            # Store query in registry with metadata from top
            registry = QueryRegistry()
            query_hash, is_new = registry.add_query(
                sql=query_text,
                source="top",
                frequency=selected_query['freq'] if isinstance(selected_query['freq'], int) else 0,
                target=""  # Top command doesn't specify target, will be updated when analyzed
            )

            # Import and run analyze
            from .analyze_command import AnalyzeCommand
            analyze_cmd = AnalyzeCommand()

            # Normalize the SQL for the AnalyzeInput
            from ..query_registry.query_registry import normalize_sql
            query_sql = selected_query['query_text']
            normalized_sql = normalize_sql(query_sql)

            # Note: Queries from MySQL digest contain ? placeholders and cannot be used
            # directly with EXPLAIN ANALYZE. The EXPLAIN step will detect this and provide
            # a helpful error message guiding the user to use rdst analyze with literal values.
            # This is by design - rdst top is for monitoring, rdst analyze is for deep analysis.

            # Create resolved input for the selected query
            from .analyze_command import AnalyzeInput
            resolved_input = AnalyzeInput(
                sql=query_sql,  # Will contain ? placeholders from digest
                normalized_sql=normalized_sql,
                source="top",
                hash=query_hash,
                tag="",
                save_as=""
            )

            print(f"\nQuery selected and stored in registry with hash: {query_hash}")
            print("Running analysis...")

            return analyze_cmd.execute_analyze(resolved_input, target=target_name)

        except Exception as e:
            return RdstResult(False, f"Analysis failed: {e}")

    def _execute_top_query(self, target_config: TargetConfig, db_engine: str, source: str) -> dict:
        """Execute the top query using DataManager."""
        import sys
        import tempfile

        # Import required modules
        from lib.data_manager.data_manager import DataManager
        from lib.data_manager_service import ConnectionConfig, DMSDbType, DataManagerQueryType

        # Get password from environment
        password = None
        if target_config.get('password_env'):
            password = os.getenv(target_config['password_env'])
        elif target_config.get('password'):
            password = target_config['password']

        if not password:
            raise ValueError(f"No password found. Set environment variable {target_config.get('password_env', 'DB_PASSWORD')}")

        # Create connection config
        connection_config = ConnectionConfig(
            host=target_config['host'],
            port=target_config['port'],
            database=target_config['database'],
            username=target_config['user'],
            password=password,
            db_type=DMSDbType.MySql if db_engine == 'mysql' else DMSDbType.PostgreSQL,
            query_type=DataManagerQueryType.UPSTREAM
        )

        # Get command set name - try the preferred source, fallback if needed
        try:
            command_set_name = self._get_command_set_for_source(db_engine, source)
        except ValueError as e:
            # If the preferred source fails, try fallback
            if db_engine == "postgresql" and source == "pg_stat":
                print("Note: pg_stat_statements extension not found. Falling back to live activity view.")
                print("To enable better query statistics, run: CREATE EXTENSION IF NOT EXISTS pg_stat_statements;")
                print("Then add 'shared_preload_libraries = pg_stat_statements' to postgresql.conf and restart PostgreSQL.")
                command_set_name = self._get_command_set_for_source(db_engine, "activity")
                source = "activity"  # Update source for display
            else:
                raise e

        # Create temporary output directory
        output_dir = tempfile.mkdtemp(prefix="rdst_")

        try:
            # Create a simple logger wrapper for DataManager
            import logging

            class SimpleLoggerWrapper:
                def __init__(self):
                    self.logger = logging.getLogger('rdst_data_manager')
                    self.logger.setLevel(logging.INFO)

                def info(self, msg, **kwargs):
                    # Ignore extra keyword arguments like highlight=True
                    self.logger.info(msg)

                def debug(self, msg, **kwargs):
                    self.logger.debug(msg)

                def warning(self, msg, **kwargs):
                    # Filter out S3 sync warnings - not relevant for RDST
                    if "S3 sync" in str(msg):
                        return
                    self.logger.warning(msg)

                def error(self, msg, **kwargs):
                    self.logger.error(msg)

            logger = SimpleLoggerWrapper()

            # Initialize DataManager
            dm = DataManager(
                connection_config={DataManagerQueryType.UPSTREAM: connection_config},
                global_logger=logger,
                command_sets=[command_set_name],
                data_directory=output_dir,
                cli_mode=True
            )

            # Get the command name from the command set
            command_name = list(dm._available_commands[command_set_name]['commands'].keys())[0]

            # Execute command (suppress DataManager error output when we know it will fail)
            import contextlib
            import io

            if db_engine == "postgresql" and source == "pg_stat":
                # Suppress stderr for the first attempt since we expect it might fail
                stderr_capture = io.StringIO()
                with contextlib.redirect_stderr(stderr_capture):
                    result = dm.execute_command(command_set_name, command_name)
                # Only print captured stderr if it's not the expected pg_stat_statements error
                captured_stderr = stderr_capture.getvalue()
                if captured_stderr and 'pg_stat_statements' not in captured_stderr:
                    print(captured_stderr, file=sys.stderr)
            else:
                result = dm.execute_command(command_set_name, command_name)

            # Check if command failed and we can fallback
            if not result.get('success') and db_engine == "postgresql" and source == "pg_stat":
                error_msg = result.get('error', '')
                if 'pg_stat_statements' in error_msg:
                    print("Note: pg_stat_statements extension not found. Falling back to live activity view.")
                    print("To enable better query statistics, run: CREATE EXTENSION IF NOT EXISTS pg_stat_statements;")
                    print("Then add 'shared_preload_libraries = pg_stat_statements' to postgresql.conf and restart PostgreSQL.")
                    # Retry with activity source
                    command_set_name = self._get_command_set_for_source(db_engine, "activity")
                    command_name = list(dm._available_commands[command_set_name]['commands'].keys())[0]

                    # Re-create DataManager with activity command set
                    dm = DataManager(
                        connection_config={DataManagerQueryType.UPSTREAM: connection_config},
                        global_logger=logger,
                        command_sets=[command_set_name],
                        data_directory=output_dir,
                        cli_mode=True
                    )
                    result = dm.execute_command(command_set_name, command_name)
                    source = "activity"  # Update source for display

            # Add source info to result for processing
            result['source'] = source
            return result

        finally:
            # Clean up temporary directory
            import shutil
            try:
                shutil.rmtree(output_dir)
            except:
                pass

    def _process_top_data(self, data: dict, source: str, limit: int, sort: str, filter_pattern: str) -> List[dict]:
        """Process and format the top queries data."""
        if not data.get('success') or not data.get('data') is not None:
            return []

        import re

        df = data['data']
        if df.empty:
            return []

        # For activity sources, remove duplicates and system noise
        if source == 'activity':
            # Remove duplicates by query_hash, keeping the longest running
            if 'duration_ms' in df.columns:
                df = df.sort_values('duration_ms', ascending=False).drop_duplicates('query_hash', keep='first')
            else:
                df = df.drop_duplicates('query_hash', keep='first')

        # Apply filter if specified
        if filter_pattern:
            try:
                pattern = re.compile(filter_pattern, re.IGNORECASE)
                mask = df['query_text'].str.contains(pattern, na=False)
                df = df[mask]
            except re.error:
                # If regex is invalid, treat as literal string
                mask = df['query_text'].str.contains(filter_pattern, case=False, na=False)
                df = df[mask]

        # Normalize column names based on source
        if source in ['pg_stat', 'digest']:
            # Historical sources
            if 'calls' in df.columns:
                df['freq'] = df['calls']
            elif 'count_star' in df.columns:
                df['freq'] = df['count_star']

            if 'total_time' in df.columns:
                df['total_time_sort'] = df['total_time']
            elif 'sum_timer_wait' in df.columns:
                df['total_time_sort'] = df['sum_timer_wait']
                df['total_time'] = df['sum_timer_wait']

            if 'mean_time' in df.columns:
                df['avg_time'] = df['mean_time']
            elif 'avg_timer_wait' in df.columns:
                df['avg_time'] = df['avg_timer_wait']

        else:
            # Activity sources - different columns for MySQL vs PostgreSQL
            if 'time' in df.columns:
                # MySQL PROCESSLIST - TIME column is in seconds
                df['freq'] = 1  # Each row is a single active query
                df['total_time_sort'] = df['time'].astype(float)
                df['total_time'] = df['total_time_sort']
                df['avg_time'] = df['total_time_sort']

                # For fast queries (TIME=0), show a minimal time value
                df.loc[df['total_time_sort'] == 0, 'total_time_sort'] = 0.001
                df.loc[df['total_time'] == 0, 'total_time'] = 0.001
                df.loc[df['avg_time'] == 0, 'avg_time'] = 0.001

            elif 'duration_ms' in df.columns:
                # PostgreSQL pg_stat_activity - duration_ms column
                df['freq'] = 1
                df['total_time_sort'] = df['duration_ms'].astype(float) / 1000.0  # Convert to seconds
                df['total_time'] = df['total_time_sort']
                df['avg_time'] = df['total_time_sort']

        # Calculate percentage load for activity sources
        if 'pct_load' not in df.columns:
            if source == 'activity' and 'total_time_sort' in df.columns:
                # For activity sources, calculate relative load based on query duration
                total_activity_time = df['total_time_sort'].sum()
                if total_activity_time > 0:
                    df['pct_load'] = (df['total_time_sort'] / total_activity_time * 100).round(1)
                else:
                    df['pct_load'] = 0.0
            else:
                df['pct_load'] = 0.0

        # Sort the data
        sort_column_map = {
            'freq': 'freq',
            'total_time': 'total_time_sort',
            'avg_time': 'avg_time',
            'load': 'pct_load'
        }

        sort_col = sort_column_map.get(sort, 'total_time_sort')
        if sort_col in df.columns:
            df = df.sort_values(sort_col, ascending=False)

        # Limit results
        df = df.head(limit)

        # Convert to list of dicts
        results = []
        for _, row in df.iterrows():
            query_text = str(row.get('query_text', ''))
            # Generate our own normalized hash instead of using database hash
            our_hash = hash_sql(query_text) if query_text else ''

            results.append({
                'query_hash': our_hash,
                'query_text': query_text,  # Keep full text for processing, format in display
                'freq': int(row.get('freq', 0)),
                'total_time': f"{float(row.get('total_time', 0)):.3f}s",
                'avg_time': f"{float(row.get('avg_time', 0)):.3f}s",
                'pct_load': f"{float(row.get('pct_load', 0)):.1f}%"
            })

        return results

    def _format_top_display(self, data: List[dict], source: str, no_color: bool, db_engine: str, target_name: str, watch_mode: bool = False) -> str:
        """Format the top queries data for display."""
        if not data:
            if _RICH_AVAILABLE and self._console and not no_color:
                self._console.print(Panel(f"No active queries found for target '{target_name}' using source '{source}'. Run some database queries in another session to see them here.",
                                       title="rdst top", border_style="yellow"))
                return ""
            else:
                return f"No active queries found for target '{target_name}' using source '{source}'. Run some database queries in another session to see them here."

        # Use Rich formatting if available
        if _RICH_AVAILABLE and self._console and not no_color:
            import datetime

            # Create table
            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("QUERY HASH", style="bright_blue", width=12)
            table.add_column("QUERY", style="white", width=15)
            table.add_column("FREQ", style="green", width=7)
            table.add_column("TOTAL TIME", style="yellow", width=10)
            table.add_column("AVG TIME", style="yellow", width=8)
            table.add_column("% LOAD", style="red", width=6)
            table.add_column("SOURCE", style="magenta", width=8)
            table.add_column("READYSET", style="blue", width=8)

            # Add rows
            for row in data:
                readyset_advice = "N/A"  # Placeholder for future readyset integration
                query_display = row['query_text'][:15] + ("..." if len(row['query_text']) > 15 else "")
                table.add_row(
                    row['query_hash'],
                    query_display,
                    str(row['freq']),
                    row['total_time'],
                    row['avg_time'],
                    row['pct_load'],
                    source,
                    readyset_advice
                )

            # Create title with metadata
            if watch_mode:
                timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                title = f"rdst top - {timestamp} - {target_name} ({db_engine}) - {source}"
            else:
                title = f"Top queries: {target_name} ({db_engine}) - {source}"

            # Set table title
            table.title = title

            # Capture Rich output as string
            console = Console(file=None, width=120)
            with console.capture() as capture:
                console.print(table)
            return capture.get()

        else:
            # Fallback to plain text formatting
            header_lines = []
            if watch_mode:
                import datetime
                timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                header_lines.append(f"rdst top - {timestamp} - {target_name} ({db_engine}) - source: {source}")
                header_lines.append("")
            else:
                header_lines.append(f"Top queries for target '{target_name}' ({db_engine}) using source '{source}':")
                header_lines.append("")

            # Column headers
            header_lines.append(" QUERY HASH   | QUERY           | FREQ    | TOTAL TIME | AVG TIME | % LOAD | SOURCE   | READYSET")
            header_lines.append("-" * 105)

            # Data rows
            data_lines = []
            for row in data:
                readyset_advice = "N/A"  # Placeholder for future readyset integration
                query_display = row['query_text'][:15] + ("..." if len(row['query_text']) > 15 else "")
                line = f" {row['query_hash']:<12} | {query_display:<15} | {row['freq']:<7} | {row['total_time']:<10} | {row['avg_time']:<8} | {row['pct_load']:<6} | {source:<8} | {readyset_advice}"
                data_lines.append(line)

            if not data_lines:
                data_lines.append(" No matching queries found")

            return "\n".join(header_lines + data_lines)
"""
Real-time monitoring orchestration for RDST Top command.

Coordinates:
- Database connection
- Query collection (200ms polls)
- QueryTracker state management
- Live display with Rich
- Query saving to registry
"""

import time
from typing import Dict, Any

# Import UI system for styled text and console
from lib.ui import StyleTokens, get_console, NextSteps

from lib.db_connection import create_direct_connection, close_connection
from lib.top_monitor import ActivityQueryCollector, QueryTracker
from lib.top_display import TopDisplay, format_query_for_save

# Query registry is optional - only needed for saving queries
try:
    from lib.query_registry.query_registry import (
        QueryRegistry,
        hash_sql,
        generate_query_name,
    )

    HAS_QUERY_REGISTRY = True
except ImportError:
    HAS_QUERY_REGISTRY = False


def run_realtime_monitor(
    target_config: Dict[str, Any],
    console=None,
    limit: int = 10,
    json_output: bool = False,
    duration: int = None,
):
    """
    Run real-time query monitoring with live display or snapshot mode.

    Args:
        target_config: Database target configuration
        console: Console instance (optional, uses UI lib default)
        limit: Number of top queries to show
        json_output: Output results as JSON (auto-enables snapshot mode if duration not set)
        duration: Run for N seconds then return results (snapshot mode, non-interactive)

    Flow:
        1. Connect to database
        2. Create collector and tracker
        3. If json_output or duration set: Run polling loop, return results
        4. Otherwise: Display with Rich Live + interactive prompt
        5. Auto-save new queries to registry as they're discovered
        6. Handle save or analyze requests
    """
    console = console or get_console()
    connection = None

    # If json_output requested without duration, auto-set a short snapshot duration
    # This enables --json to work standalone without requiring --duration
    if json_output and duration is None:
        duration = 2  # 2 second snapshot - enough to catch a few poll cycles

    try:
        # Connect to database (silently)
        connection = create_direct_connection(target_config)
        db_engine = target_config.get("engine", "").lower()

        # Create collector and tracker
        collector = ActivityQueryCollector(db_engine, connection)
        tracker = QueryTracker()

        # SNAPSHOT MODE: Run for specified duration and return results
        if duration:
            return _run_snapshot_mode(
                collector,
                tracker,
                duration,
                limit,
                json_output,
                db_engine,
                target_config,
            )

        # INTERACTIVE MODE: Rich Live display with keyboard controls
        # Create display
        display = TopDisplay(console, db_engine)

        # Auto-save state: track which queries we've already saved to registry this session
        saved_query_hashes = set()  # Query hashes already in registry (loaded at start)
        newly_saved_count = 0  # Count of queries we saved during this session

        # Load existing registry hashes to avoid re-saving
        if HAS_QUERY_REGISTRY:
            try:
                registry = QueryRegistry()
                registry.load()
                for entry in registry.list_queries():
                    saved_query_hashes.add(entry.hash)
            except Exception:
                pass  # If registry load fails, we'll just save everything

        target_name = target_config.get("name", "default")

        # Define function to get current state (called by display loop)
        def get_current_state():
            nonlocal newly_saved_count

            # Poll database
            try:
                query_data = collector.fetch_active_queries()
                tracker.update(query_data)
            except Exception:
                # If poll fails, just continue with existing data
                pass

            # Auto-save new queries to registry
            if HAS_QUERY_REGISTRY:
                for query_hash, query_metrics in tracker.queries.items():
                    # Use our normalized hash (12 char) for registry
                    from lib.query_registry import hash_sql

                    registry_hash = hash_sql(query_metrics.query_text)

                    if registry_hash not in saved_query_hashes:
                        try:
                            registry = QueryRegistry()
                            registry.add_query(
                                sql=query_metrics.query_text,
                                source="top",
                                target=target_name,
                            )
                            saved_query_hashes.add(registry_hash)
                            newly_saved_count += 1
                        except Exception:
                            pass  # Don't let save failures break monitoring

            # Get top N queries
            top_queries = tracker.get_top_n(limit, sort_by="max")
            runtime = tracker.get_runtime_seconds()
            total_tracked = tracker.get_total_queries_tracked()

            return (top_queries, runtime, total_tracked, newly_saved_count)

        # Run display loop with interactive input
        display.run(get_current_state)

        # Show exit breadcrumb - always show next steps
        if newly_saved_count > 0:
            console.print(
                f"\n[{StyleTokens.SUCCESS}]Top saved {newly_saved_count} new queries to registry.[/{StyleTokens.SUCCESS}]"
            )
            # Show example hashes for easy copy-paste
            if display.current_queries:
                from lib.query_registry import hash_sql

                steps = []
                for q in display.current_queries[:3]:
                    h = hash_sql(q.query_text)
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
                if len(display.current_queries) > 3:
                    steps.append(
                        (
                            f"rdst [{StyleTokens.SUCCESS}]query list[/{StyleTokens.SUCCESS}]",
                            "View all saved queries",
                        )
                    )
                console.print(NextSteps(steps))
        else:
            # No new queries, but still show helpful breadcrumb
            console.print(
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

        # Handle user action after display exits
        if display.save_all_requested:
            save_queries_to_registry(
                display.current_queries, None, target_config, console
            )

        elif display.selected_query_index is not None:
            if display.analyze_requested:
                # Run analyze on selected query
                if display.selected_query_index < len(display.current_queries):
                    query = display.current_queries[display.selected_query_index]
                    console.print(
                        f"\n[{StyleTokens.INFO}]Running analyze on query [{display.selected_query_index}]...[/{StyleTokens.INFO}]"
                    )

                    # Display the query being analyzed
                    query_display = (
                        query.normalized_query
                        if query.normalized_query
                        else query.query_text
                    )
                    console.print(
                        f"[{StyleTokens.WARNING}]Query:[/{StyleTokens.WARNING}] {query_display}\n"
                    )

                    # Call rdst.py analyze via subprocess in interactive mode
                    # Subprocess is used to call internal rdst.py tool with controlled arguments, not executing user input
                    import subprocess  # nosemgrep: gitlab.bandit.B404
                    import sys

                    target_name = target_config.get("name", "default")

                    # Build command to run analyze in interactive mode
                    cmd = [
                        sys.executable,  # Use same python interpreter
                        "rdst.py",
                        "analyze",
                        "--target",
                        target_name,
                        "--query",
                        query.query_text,
                        "--interactive",  # Enable interactive REPL mode
                    ]

                    try:
                        # Run with inherited stdin/stdout/stderr for proper interactive mode
                        subprocess.run(
                            cmd, check=False, stdin=None, stdout=None, stderr=None
                        )
                    except Exception as e:
                        console.print(
                            f"[{StyleTokens.ERROR}]Error running analyze: {e}[/{StyleTokens.ERROR}]"
                        )
            else:
                # Save selected query
                save_queries_to_registry(
                    display.current_queries,
                    [display.selected_query_index],
                    target_config,
                    console,
                )

        return None  # Interactive mode returns None

    except KeyboardInterrupt:
        # Ensure terminal is restored on Ctrl+C
        _restore_terminal()
        return None

    except Exception as e:
        console.print(
            f"\n\n[{StyleTokens.ERROR}]Error during monitoring: {e}[/{StyleTokens.ERROR}]"
        )
        raise

    finally:
        # Clean up connection (silently)
        if connection:
            close_connection(connection)


def _run_snapshot_mode(
    collector, tracker, duration, limit, json_output, db_engine, target_config
):
    """
    Run Top in snapshot mode: collect metrics for N seconds then output results.

    Args:
        collector: ActivityQueryCollector instance
        tracker: QueryTracker instance
        duration: How long to run (seconds)
        limit: Number of top queries to show
        json_output: Whether to output as JSON
        db_engine: Database engine type
        target_config: Target configuration dict

    Returns:
        Formatted output string (text or JSON)
    """
    import json

    # Run polling loop for the specified duration
    start_time = time.time()
    poll_interval = 0.2  # 200ms, same as interactive mode

    while (time.time() - start_time) < duration:
        try:
            # Poll database
            query_data = collector.fetch_active_queries()
            tracker.update(query_data)
        except Exception:
            # If poll fails, just continue
            pass

        # Sleep until next poll
        time.sleep(poll_interval)

    # Get final results
    top_queries = tracker.get_top_n(limit, sort_by="max")
    runtime = tracker.get_runtime_seconds()
    total_tracked = tracker.get_total_queries_tracked()

    # Format output
    if json_output:
        # JSON output
        queries_data = []
        for query in top_queries:
            queries_data.append(
                {
                    "query_hash": query.query_hash,
                    "normalized_query": query.normalized_query,
                    "query_text": query.query_text,
                    "max_duration_ms": round(query.max_duration_seen, 2),
                    "avg_duration_ms": round(query.avg_duration, 2),
                    "observation_count": query.observation_count,
                    "current_instances_running": query.current_instances_running,
                }
            )

        result = {
            "target": target_config.get("name", "unknown"),
            "engine": db_engine,
            "runtime_seconds": round(runtime, 2),
            "total_queries_tracked": total_tracked,
            "queries": queries_data,
        }
        return json.dumps(result, indent=2)
    else:
        # Text output
        lines = []
        lines.append(f"RDST Top - Snapshot Mode ({duration}s)")
        lines.append(f"Target: {target_config.get('name', 'unknown')} ({db_engine})")
        lines.append(
            f"Runtime: {round(runtime, 1)}s | Total Queries Tracked: {total_tracked}"
        )
        lines.append("")
        lines.append("Top {} Slowest Queries (by Max Duration):".format(limit))
        lines.append("-" * 120)
        lines.append(
            f"{'#':<3} | {'Hash':<12} | {'Max Duration':<12} | {'Avg Duration':<12} | {'Observations':<12} | {'Running Now':<12} | {'Query'}"
        )
        lines.append("-" * 120)

        for idx, query in enumerate(top_queries):
            max_dur = f"{query.max_duration_seen:,.1f}ms"
            avg_dur = f"{query.avg_duration:,.1f}ms"
            obs_count = str(query.observation_count)
            running_now = str(query.current_instances_running)
            query_text = query.normalized_query[:60] + (
                "..." if len(query.normalized_query) > 60 else ""
            )

            lines.append(
                f"{idx:<3} | {query.query_hash[:12]:<12} | {max_dur:<12} | {avg_dur:<12} | {obs_count:<12} | {running_now:<12} | {query_text}"
            )

        return "\n".join(lines)


def _restore_terminal():
    """Restore terminal to normal state after interrupted display.

    Ensures cursor is visible, alternate screen buffer is exited,
    and terminal settings are restored.
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


def save_queries_to_registry(queries, selected_indices, target_config, console):
    """
    Save queries to query registry.

    Args:
        queries: List of QueryMetrics
        selected_indices: List of indices to save, or None for all
        target_config: Database target configuration
        console: Rich console

    Returns:
        List of saved queries with their info
    """
    if not HAS_QUERY_REGISTRY:
        console.print(
            f"[{StyleTokens.WARNING}]Query registry not available - skipping save[/{StyleTokens.WARNING}]"
        )
        return []

    try:
        registry = QueryRegistry()
        target_name = target_config.get("name", "default")
        saved_queries = []
        new_count = 0
        existing_count = 0

        # Determine which queries to save
        if selected_indices is None:
            # Save all queries
            indices_to_save = range(len(queries))
            console.print(
                f"\n[{StyleTokens.INFO}]Saving all {len(queries)} queries to registry...[/{StyleTokens.INFO}]\n"
            )
        else:
            # Save selected queries
            indices_to_save = selected_indices
            console.print(
                f"\n[{StyleTokens.INFO}]Saving {len(selected_indices)} selected queries to registry...[/{StyleTokens.INFO}]\n"
            )

        # Get existing names for collision detection
        existing_names = {e.tag for e in registry.list_queries() if e.tag}

        # Save each query
        skipped_queries = []
        for idx in indices_to_save:
            if idx >= len(queries):
                continue

            query = queries[idx]
            query_info = format_query_for_save(query)

            # Check if query already exists by hash FIRST
            query_hash = hash_sql(query_info["query_text"])
            existing = registry.get_query(query_hash)

            if existing:
                # Query already exists - don't add again, just report
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
                        "query_text": query.normalized_query[:80] + "..."
                        if len(query.normalized_query) > 80
                        else query.normalized_query,
                        "tag": display_tag or query_hash[:8],
                    }
                )
            else:
                # New query - generate auto-name and add
                auto_name = generate_query_name(query.normalized_query, existing_names)
                existing_names.add(auto_name)  # Track for subsequent collisions

                try:
                    registry.add_query(
                        tag=auto_name,
                        sql=query_info["query_text"],
                        source="top",
                        target=target_name,
                        max_duration_ms=query_info["max_duration_ms"],
                        avg_duration_ms=query_info["avg_duration_ms"],
                        observation_count=query_info["observation_count"],
                    )

                    new_count += 1
                    status = f"[{StyleTokens.SUCCESS}]new: '{auto_name}'[/{StyleTokens.SUCCESS}]"

                    saved_queries.append(
                        {
                            "index": idx,
                            "hash": query_hash[:8],
                            "query_text": query.normalized_query[:80] + "..."
                            if len(query.normalized_query) > 80
                            else query.normalized_query,
                            "tag": auto_name,
                        }
                    )
                except ValueError:
                    # Query exceeds 4KB limit
                    skipped_queries.append(idx)
                    status = (
                        f"[{StyleTokens.WARNING}]skipped (>4KB)[/{StyleTokens.WARNING}]"
                    )

            # Print saved query with status
            query_preview = (
                query.normalized_query[:70] + "..."
                if len(query.normalized_query) > 70
                else query.normalized_query
            )
            console.print(f"  [{idx}] {query_hash[:8]} - {query_preview} ({status})")

        # Summary
        if skipped_queries:
            console.print(
                f"\n[{StyleTokens.WARNING}]Note: {len(skipped_queries)} queries exceeded the 4KB limit and were not saved.[/{StyleTokens.WARNING}]"
            )
            console.print(
                f"[{StyleTokens.WARNING}]Use 'rdst analyze --large-query-bypass' to analyze large queries.[/{StyleTokens.WARNING}]"
            )

        if new_count > 0 and existing_count > 0:
            console.print(
                f"\n[{StyleTokens.SUCCESS}]Saved {new_count} new, {existing_count} already existed[/{StyleTokens.SUCCESS}]"
            )
        elif new_count > 0:
            console.print(
                f"\n[{StyleTokens.SUCCESS}]Saved {new_count} queries[/{StyleTokens.SUCCESS}]"
            )
        else:
            console.print(
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
        console.print(NextSteps(steps))
        return saved_queries

    except Exception as e:
        console.print(
            f"[{StyleTokens.ERROR}]Error saving to registry: {e}[/{StyleTokens.ERROR}]"
        )
        return []

"""
Query Command Implementation

Manages query registry: add, edit, list, delete queries.
Separate from analysis - purely for query management.
"""

from __future__ import annotations

import os
import re
import signal
import statistics
import subprocess  # nosemgrep: gitlab.bandit.B404
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue, Empty
from shutil import which
from threading import Lock
from typing import Optional, Any

# Import UI system
from lib.ui import (
    get_console,
    StyleTokens,
    Layout as UILayout,
    RegistryTable,
    QueryStatsTable,
    KeyValueTable,
    Confirm,
    NextSteps,
    QueryPanel,
    Live,
    MessagePanel,
    EmptyState,
    SectionBox,
    StatusLine,
    DurationDisplay,
    DataTable,
    QueryTable,
)

try:
    import sqlparse

    SQLPARSE_AVAILABLE = True
except ImportError:
    SQLPARSE_AVAILABLE = False

from lib.query_registry.query_registry import QueryRegistry


@dataclass
class QueryStats:
    """Statistics for a single query during run execution."""

    query_name: str
    query_hash: str
    executions: int = 0
    successes: int = 0
    failures: int = 0
    timings_ms: list[float] = field(default_factory=list)

    @property
    def min_ms(self) -> float:
        return min(self.timings_ms) if self.timings_ms else 0.0

    @property
    def max_ms(self) -> float:
        return max(self.timings_ms) if self.timings_ms else 0.0

    @property
    def avg_ms(self) -> float:
        return statistics.mean(self.timings_ms) if self.timings_ms else 0.0

    @property
    def p50_ms(self) -> float:
        return statistics.median(self.timings_ms) if self.timings_ms else 0.0

    @property
    def p95_ms(self) -> float:
        if len(self.timings_ms) < 2:
            return self.max_ms
        sorted_times = sorted(self.timings_ms)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def p99_ms(self) -> float:
        if len(self.timings_ms) < 2:
            return self.max_ms
        sorted_times = sorted(self.timings_ms)
        idx = int(len(sorted_times) * 0.99)
        return sorted_times[min(idx, len(sorted_times) - 1)]


@dataclass
class RunStatistics:
    """Aggregated statistics for a run session."""

    start_time: float
    query_stats: dict[str, QueryStats] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def record_execution(
        self, query_hash: str, query_name: str, duration_ms: float, success: bool
    ) -> None:
        with self._lock:
            if query_hash not in self.query_stats:
                self.query_stats[query_hash] = QueryStats(query_name, query_hash)

            stats = self.query_stats[query_hash]
            stats.executions += 1
            if success:
                stats.successes += 1
                stats.timings_ms.append(duration_ms)
            else:
                stats.failures += 1

    @property
    def total_executions(self) -> int:
        return sum(s.executions for s in self.query_stats.values())

    @property
    def total_successes(self) -> int:
        return sum(s.successes for s in self.query_stats.values())

    @property
    def total_failures(self) -> int:
        return sum(s.failures for s in self.query_stats.values())

    @property
    def elapsed_seconds(self) -> float:
        return time.perf_counter() - self.start_time


class QueryCommand:
    """
    Manages the query registry through query subcommands.

    Provides add, edit, list, show, and delete operations for queries.
    Uses $EDITOR for multi-line query input.
    """

    def __init__(self):
        self.registry = QueryRegistry()
        self.console = get_console()

    def execute(self, subcommand: str, **kwargs):
        """
        Route to appropriate subcommand handler.

        Args:
            subcommand: One of: add, edit, list, show, delete, rm, import
            **kwargs: Subcommand-specific arguments

        Returns:
            RdstResult with operation outcome
        """
        # Lazy import to avoid circular dependency
        from .rdst_cli import RdstResult

        if subcommand == "add":
            return self.add(**kwargs)
        elif subcommand == "import":
            return self.import_queries(**kwargs)
        elif subcommand == "edit":
            return self.edit(**kwargs)
        elif subcommand == "list":
            return self.list(**kwargs)
        elif subcommand == "show":
            return self.show(**kwargs)
        elif subcommand in ["delete", "rm"]:
            return self.delete(**kwargs)
        elif subcommand == "run":
            return self.run(**kwargs)
        else:
            return RdstResult(
                ok=False,
                message=f"Unknown query subcommand: {subcommand}",
                data={"subcommand": subcommand},
            )

    def add(
        self,
        name: str,
        query: Optional[str] = None,
        file: Optional[str] = None,
        target: Optional[str] = None,
        **kwargs,
    ):
        """
        Add a new query to the registry.

        Args:
            name: Name/tag for the query (required)
            query: Optional inline query string
            file: Optional file path to read query from
            target: Optional target database name (uses default if not provided)

        Returns:
            RdstResult with query hash and status
        """
        from .rdst_cli import RdstResult, TargetsConfig

        # Get default target if none specified
        if not target:
            try:
                cfg = TargetsConfig()
                cfg.load()
                target = cfg.get_default()
            except Exception:
                pass  # Leave as None if config fails

        if not name:
            return RdstResult(
                ok=False, message="Query name is required for 'rdst query add'", data={}
            )

        # Check if query name already exists
        existing = self.registry.get_query_by_tag(name)
        if existing:
            return RdstResult(
                ok=False,
                message=f"Query '{name}' already exists (hash: {existing.hash}). Use 'rdst query edit {name}' to modify.",
                data={"name": name, "existing_hash": existing.hash},
            )

        # Determine query source
        if query:
            sql = query
            source = "manual"
        elif file:
            sql = self._read_query_from_file(file)
            if not sql:
                return RdstResult(
                    ok=False,
                    message=f"Could not read query from file: {file}",
                    data={"file": file},
                )
            source = "file"
        else:
            # Open editor for multi-line input
            sql = self._open_editor_for_query(name, target_name=target)
            if not sql:
                return RdstResult(
                    ok=False,
                    message="No query provided (editor was empty or cancelled)",
                    data={"name": name},
                )
            source = "manual"

        # Add to registry
        try:
            query_hash, is_new = self.registry.add_query(
                sql=sql, tag=name, source=source, target=target or ""
            )

            self.console.print(
                MessagePanel("Query added to registry", variant="success")
            )
            summary = {
                "Name": name,
                "Hash": query_hash,
                "Source": source,
            }
            if target:
                summary["Target"] = target
            self.console.print(KeyValueTable(summary))

            steps = []
            if target:
                steps.append(
                    (
                        f"rdst analyze --hash {query_hash[:8]} --target {target}",
                        "Analyze this query",
                    )
                )
            else:
                steps.append(
                    (
                        f"rdst analyze --hash {query_hash[:8]} --target <target>",
                        "Analyze this query",
                    )
                )
            steps.append(
                (
                    f"rdst query show {name}",
                    "View query details",
                )
            )
            self.console.print(NextSteps(steps))

            msg = ""

            return RdstResult(
                ok=True,
                message=msg,
                data={"name": name, "hash": query_hash, "is_new": is_new, "sql": sql},
            )
        except Exception as e:
            return RdstResult(
                ok=False,
                message=f"Failed to add query: {str(e)}",
                data={"name": name, "error": str(e)},
            )

    def import_queries(
        self, file: str, update: bool = False, target: Optional[str] = None, **kwargs
    ):
        """
        Import multiple queries from a SQL file.

        Supports:
        - Multiple queries separated by semicolons
        - Comment-based metadata (-- name:, -- target:, -- frequency:)
        - Duplicate handling (skip by default, update with --update)

        Args:
            file: Path to SQL file
            update: Whether to update existing queries (default: skip)
            target: Default target for queries without target comment

        Returns:
            RdstResult with import summary
        """
        from .rdst_cli import RdstResult

        if not file:
            return RdstResult(
                ok=False, message="File path is required for import", data={}
            )

        # Read file
        try:
            file_path = Path(file)
            if not file_path.exists():
                return RdstResult(
                    ok=False, message=f"File not found: {file}", data={"file": file}
                )

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            return RdstResult(
                ok=False,
                message=f"Failed to read file: {str(e)}",
                data={"file": file, "error": str(e)},
            )

        # Parse queries from file
        queries = self._parse_import_file(content, default_target=target)

        if not queries:
            return RdstResult(
                ok=False,
                message=f"No queries found in file: {file}",
                data={"file": file},
            )

        # Import each query
        imported = 0
        updated = 0
        skipped = 0
        errors = []

        for query_data in queries:
            name = query_data.get("name")
            sql = query_data.get("sql")
            query_target = query_data.get("target", target)

            if not name or not sql:
                errors.append(f"Query missing name or SQL: {name or '(unnamed)'}")
                continue

            # Check if query already exists
            existing = self.registry.get_query_by_tag(name)

            if existing:
                if update:
                    # Update existing query
                    try:
                        new_hash, is_new = self.registry.add_query(
                            sql=sql,
                            tag=name,
                            source="import",
                            target=query_target or "",
                        )
                        if new_hash != existing.hash:
                            self.registry.remove_query(existing.hash)
                        updated += 1
                    except Exception as e:
                        errors.append(f"Failed to update '{name}': {str(e)}")
                else:
                    # Skip duplicate
                    skipped += 1
            else:
                # Add new query
                try:
                    self.registry.add_query(
                        sql=sql, tag=name, source="import", target=query_target or ""
                    )
                    imported += 1
                except Exception as e:
                    errors.append(f"Failed to import '{name}': {str(e)}")

        # Build summary message
        total_processed = imported + updated + skipped
        msg_parts = [f"✓ Import complete: {total_processed} queries processed"]

        if imported > 0:
            msg_parts.append(f"  {imported} imported")
        if updated > 0:
            msg_parts.append(f"  {updated} updated")
        if skipped > 0:
            msg_parts.append(f"  {skipped} skipped (duplicates)")
        if errors:
            msg_parts.append(f"\n⚠ {len(errors)} errors:")
            for error in errors[:5]:  # Show first 5 errors
                msg_parts.append(f"  - {error}")
            if len(errors) > 5:
                msg_parts.append(f"  ... and {len(errors) - 5} more")

        return RdstResult(
            ok=len(errors) == 0 or (imported + updated) > 0,
            message="\n".join(msg_parts),
            data={
                "imported": imported,
                "updated": updated,
                "skipped": skipped,
                "errors": errors,
                "file": file,
            },
        )

    def _parse_import_file(
        self, content: str, default_target: Optional[str] = None
    ) -> list:
        """
        Parse SQL file content to extract queries and metadata.

        Supports format:
        -- name: query_name
        -- target: target_name
        -- frequency: 1000
        SELECT * FROM users WHERE id = ?;

        Args:
            content: File content
            default_target: Default target if not specified in comments

        Returns:
            List of dicts with keys: name, sql, target, frequency
        """
        queries = []
        lines = content.split("\n")

        i = 0
        while i < len(lines):
            # Look for metadata comments
            metadata = {}
            sql_lines = []

            # Parse metadata comments
            while i < len(lines):
                line = lines[i].strip()

                # Check for metadata comment
                if line.startswith("-- name:"):
                    metadata["name"] = line.split(":", 1)[1].strip()
                    i += 1
                elif line.startswith("-- target:"):
                    metadata["target"] = line.split(":", 1)[1].strip()
                    i += 1
                elif line.startswith("-- frequency:"):
                    try:
                        metadata["frequency"] = int(line.split(":", 1)[1].strip())
                    except:
                        pass
                    i += 1
                elif line.startswith("--") and not line.startswith("---"):
                    # Skip other comments
                    i += 1
                elif not line:
                    # Skip empty lines
                    i += 1
                else:
                    # Found SQL content
                    break

            # Parse SQL until semicolon
            while i < len(lines):
                line = lines[i]
                sql_lines.append(line)

                # Check if line contains semicolon (end of query)
                if ";" in line:
                    i += 1
                    break
                i += 1

            # Extract SQL and clean up
            if sql_lines:
                sql = "\n".join(sql_lines).strip()
                # Remove trailing semicolon for consistency
                if sql.endswith(";"):
                    sql = sql[:-1].strip()

                # Only add if we have both name and SQL
                if metadata.get("name") and sql:
                    queries.append(
                        {
                            "name": metadata["name"],
                            "sql": sql,
                            "target": metadata.get("target", default_target),
                            "frequency": metadata.get("frequency"),
                        }
                    )

        return queries

    def edit(self, name: Optional[str] = None, hash: Optional[str] = None, **kwargs):
        """
        Edit an existing query in the registry.

        Opens $EDITOR with the current query SQL pre-filled.
        If the SQL changes significantly (new hash), preserves the name.

        Args:
            name: Query name to edit
            hash: Alternate: query hash to edit

        Returns:
            RdstResult with updated query information
        """
        from .rdst_cli import RdstResult

        # Load existing query
        if name:
            entry = self.registry.get_query_by_tag(name)
            if not entry:
                return RdstResult(
                    ok=False,
                    message=f"No query found with name: {name}",
                    data={"name": name},
                )
            identifier = name
            identifier_type = "name"
        elif hash:
            entry = self.registry.get_query(hash)
            if not entry:
                return RdstResult(
                    ok=False,
                    message=f"No query found with hash: {hash}",
                    data={"hash": hash},
                )
            identifier = hash
            identifier_type = "hash"
        else:
            return RdstResult(
                ok=False,
                message="Must provide either a query name or --hash for edit",
                data={},
            )

        # Get executable SQL with most recent parameters
        old_sql = self.registry.get_executable_query(entry.hash, interactive=False)
        if not old_sql:
            old_sql = entry.sql  # Fallback to parameterized version

        old_hash = entry.hash
        old_tag = entry.tag

        # Open editor with current SQL
        new_sql = self._open_editor_for_query(
            name=old_tag or identifier,
            existing_sql=old_sql,
            target_name=entry.last_target,
        )

        if not new_sql:
            return RdstResult(
                ok=False,
                message="Edit cancelled (no changes or empty query)",
                data={identifier_type: identifier},
            )

        # Check if SQL actually changed
        if new_sql.strip() == old_sql.strip():
            return RdstResult(
                ok=True,
                message=f"No changes made to query {identifier_type}: {identifier}",
                data={identifier_type: identifier, "hash": old_hash},
            )

        # Update registry
        # Note: If SQL changes significantly, hash will change
        # We'll remove old entry and add new one with same tag
        try:
            new_hash, is_new = self.registry.add_query(
                sql=new_sql,
                tag=old_tag,  # Preserve tag
                source=entry.source,
                target=entry.last_target,
            )

            # If hash changed, remove old entry
            if new_hash != old_hash:
                self.registry.remove_query(old_hash)
                msg = f"✓ Query updated (hash changed due to SQL modifications)\n  Tag: {old_tag}\n  Old hash: {old_hash}\n  New hash: {new_hash}"
            else:
                msg = f"✓ Query updated\n  Tag: {old_tag}\n  Hash: {new_hash}"

            return RdstResult(
                ok=True,
                message=msg,
                data={
                    "tag": old_tag,
                    "old_hash": old_hash,
                    "new_hash": new_hash,
                    "hash_changed": new_hash != old_hash,
                    "sql": new_sql,
                },
            )
        except Exception as e:
            return RdstResult(
                ok=False,
                message=f"Failed to update query: {str(e)}",
                data={identifier_type: identifier, "error": str(e)},
            )

    def delete(
        self,
        name: Optional[str] = None,
        hash: Optional[str] = None,
        force: bool = False,
        **kwargs,
    ):
        """
        Delete a query from the registry.

        Args:
            name: Query name to delete
            hash: Alternate: query hash to delete
            force: Skip confirmation prompt

        Returns:
            RdstResult with deletion status
        """
        from .rdst_cli import RdstResult

        # Find query to delete
        if name:
            entry = self.registry.get_query_by_tag(name)
            if not entry:
                return RdstResult(
                    ok=False,
                    message=f"No query found with name: {name}",
                    data={"name": name},
                )
            query_hash = entry.hash
            identifier = f"query '{name}'"
        elif hash:
            entry = self.registry.get_query(hash)
            if not entry:
                return RdstResult(
                    ok=False,
                    message=f"No query found with hash: {hash}",
                    data={"hash": hash},
                )
            query_hash = entry.hash  # Use full hash from entry, not the input prefix
            identifier = f"hash {hash}"
        else:
            return RdstResult(
                ok=False,
                message="Must provide either a query name or --hash for delete",
                data={},
            )

        # Confirm deletion unless --force
        if not force:
            confirmed = Confirm.ask(f"Delete query {identifier}?", default=False)

            if not confirmed:
                return RdstResult(
                    ok=False,
                    message="Deletion cancelled",
                    data={"identifier": identifier},
                )

        # Delete from registry
        try:
            removed = self.registry.remove_query(query_hash)
            if removed:
                msg = f"✓ Query deleted: {identifier} (hash: {query_hash})"
                return RdstResult(
                    ok=True, message=msg, data={"hash": query_hash, "name": name or ""}
                )
            else:
                return RdstResult(
                    ok=False,
                    message=f"Failed to delete query {identifier}",
                    data={"hash": query_hash},
                )
        except Exception as e:
            return RdstResult(
                ok=False,
                message=f"Error deleting query: {str(e)}",
                data={"identifier": identifier, "error": str(e)},
            )

    def list(
        self,
        limit: int = 10,
        target: str = None,
        filter: str = None,
        interactive: bool = False,
        **kwargs,
    ):
        """
        List all queries in the registry.

        By default, shows a plain text list. Use --interactive for selection mode.

        Args:
            limit: Number of queries to show (default: 10)
            target: Filter by target database
            filter: Smart filter across SQL, names, hash, source
            interactive: Enable interactive mode to select queries for analysis

        Returns:
            RdstResult with query list (and optional selected query hash)
        """
        import sys
        from .rdst_cli import RdstResult

        # Get all queries for filtering
        queries = self.registry.list_queries(limit=200)  # Get more for filtering

        if not queries:
            return RdstResult(
                ok=True,
                message="No queries in registry. Use 'rdst query add' to add queries.",
                data={"queries": []},
            )

        # Apply target filter if specified
        if target:
            target_lower = target.lower()
            queries = [
                q
                for q in queries
                if q.last_target and target_lower in q.last_target.lower()
            ]
            if not queries:
                return RdstResult(
                    ok=True,
                    message=f"No queries found for target: '{target}'",
                    data={"queries": []},
                )

        # Apply smart filter if specified
        if filter:
            filter_lower = filter.lower()
            filtered_queries = []
            for query in queries:
                matches = [
                    filter_lower in query.sql.lower(),  # SQL content
                    query.tag and filter_lower in query.tag.lower(),  # Name/tag
                    filter_lower in query.hash.lower(),  # Hash
                    filter_lower in query.source.lower(),  # Source
                    query.last_target
                    and filter_lower in query.last_target.lower(),  # Target
                ]
                if any(matches):
                    filtered_queries.append(query)
            queries = filtered_queries
            if not queries:
                return RdstResult(
                    ok=True,
                    message=f"No queries found matching filter: '{filter}'",
                    data={"queries": []},
                )

        total_queries = len(queries)

        # Use interactive mode only if explicitly requested AND we have a TTY
        use_interactive = interactive and sys.stdin.isatty()

        if use_interactive:
            return self._interactive_query_list(queries, limit, target, filter)
        else:
            return self._plain_query_list(queries, limit, target, filter)

    def _plain_query_list(
        self, queries: list, limit: int, target: str = None, filter: str = None
    ):
        """Plain text output for query list (non-interactive)."""
        from .rdst_cli import RdstResult

        # Apply limit
        queries = queries[:limit]

        # Build title with filter info
        title_parts = [f"Query Registry ({len(queries)} queries"]
        if target:
            title_parts.append(f", target: {target}")
        if filter:
            title_parts.append(f", filter: '{filter}'")
        title_parts.append(")")
        title = "".join(title_parts)

        # Format output using UI component
        table = RegistryTable(queries, title=title, show_numbers=False)
        self.console.print(table)

        return RdstResult(
            ok=True,
            message=f"Listed {len(queries)} queries",
            data={
                "queries": [
                    {
                        "tag": q.tag,
                        "hash": q.hash,
                        "sql": q.sql,
                        "target": q.last_target,
                    }
                    for q in queries
                ]
            },
        )

    def _interactive_query_list(
        self, queries: list, page_size: int = 10, target: str = None, filter: str = None
    ):
        """Interactive query list with pagination and selection - uses table format."""
        from .rdst_cli import RdstResult

        total = len(queries)
        page = 0
        max_page = (total - 1) // page_size if total > 0 else 0

        while True:
            # Calculate page bounds
            start = page * page_size
            end = min(start + page_size, total)
            page_queries = queries[start:end]

            # Clear screen
            self.console.print("\033[H\033[J", end="")

            # Build title with filter info
            title_parts = [f"Query Registry ({total} queries"]
            if target:
                title_parts.append(f", target: {target}")
            if filter:
                title_parts.append(f", filter: '{filter}'")
            title_parts.append(")")
            title = "".join(title_parts)

            # Show table with selection numbers using UI component
            table = RegistryTable(page_queries, title=title, show_numbers=True)
            self.console.print(table)
            self.console.print(
                f"\n[{StyleTokens.MUTED}]Page {page + 1}/{max_page + 1} (showing {start + 1}-{end} of {total})[/{StyleTokens.MUTED}]"
            )

            # Show navigation options
            self.console.print()
            nav_options = []
            if page > 0:
                nav_options.append("[p] Prev")
            if page < max_page:
                nav_options.append("[n] Next")
            nav_options.append("[q/Esc] Quit")

            self.console.print(
                f"[{StyleTokens.MUTED}]Enter # to analyze | {' | '.join(nav_options)}[/{StyleTokens.MUTED}]"
            )

            # Get user input
            try:
                choice = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                self.console.print(MessagePanel("Cancelled", variant="warning"))
                return RdstResult(
                    ok=True, message="Query list cancelled", data={"queries": []}
                )

            # Handle escape key (shows as empty or \x1b)
            if choice == "" or choice == "\x1b" or choice.lower() == "q":
                return RdstResult(
                    ok=True,
                    message=f"Listed {total} queries",
                    data={
                        "queries": [
                            {
                                "tag": q.tag,
                                "hash": q.hash,
                                "sql": q.sql,
                                "target": q.last_target,
                            }
                            for q in queries[:page_size]
                        ]
                    },
                )
            elif choice.lower() == "n" and page < max_page:
                page += 1
                continue
            elif choice.lower() == "p" and page > 0:
                page -= 1
                continue
            elif choice.isdigit():
                num = int(choice)
                if 1 <= num <= len(page_queries):
                    selected = page_queries[num - 1]
                    # Exit interactive mode and return selection for analyze
                    # Clear screen to restore normal terminal
                    self.console.print("\033[H\033[J", end="")
                    return RdstResult(
                        ok=True,
                        message="",  # Message will be handled by caller
                        data={
                            "action": "analyze",
                            "selected_hash": selected.hash,
                            "selected_tag": selected.tag,
                            "selected_sql": selected.sql,
                            "selected_target": selected.last_target,
                        },
                    )
                else:
                    self.console.print(
                        MessagePanel(
                            "Invalid selection",
                            variant="warning",
                            hint=f"Enter 1-{len(page_queries)}",
                        )
                    )
                    input("Press Enter to continue...")
            else:
                self.console.print(
                    MessagePanel(
                        f"Unknown option: {choice}",
                        variant="warning",
                    )
                )
                input("Press Enter to continue...")

    def _analyze_selected_query(self, query_entry):
        """Analyze the selected query."""
        from .rdst_cli import RdstResult

        self.console.print(
            f"\n[bold]Analyzing query:[/bold] {query_entry.tag or query_entry.hash[:8]}"
        )

        # Import and run analyze
        try:
            from .analyze_command import AnalyzeCommand, AnalyzeInput

            analyze_cmd = AnalyzeCommand()

            # Get executable query (with parameters if available)
            sql = self.registry.get_executable_query(
                query_entry.hash, interactive=False
            )
            if not sql:
                sql = query_entry.sql

            # Create AnalyzeInput
            resolved_input = AnalyzeInput(
                sql=sql,
                normalized_sql=query_entry.sql,
                source="registry",
                hash=query_entry.hash,
                tag=query_entry.tag or "",
                save_as="",
            )

            # Run analysis
            result = analyze_cmd.execute_analyze(
                resolved_input=resolved_input,
                target=query_entry.last_target,
                interactive=False,
            )

            return RdstResult(
                ok=True,
                message="Analysis complete",
                data={"selected_hash": query_entry.hash, "analysis": result},
            )
        except Exception as e:
            return RdstResult(
                ok=False,
                message=f"Failed to analyze query: {e}",
                data={"selected_hash": query_entry.hash, "error": str(e)},
            )

    def show(
        self, name: str = None, query_name: str = None, hash: str = None, **kwargs
    ):
        """
        Show detailed information about a specific query.

        Args:
            name: Query name to display (mutually exclusive with hash)
            query_name: Query name from argparse (alias for name)
            hash: Query hash to display (mutually exclusive with name)

        Returns:
            RdstResult with query details
        """
        from .rdst_cli import RdstResult

        # Handle both name and query_name (from argparse)
        name = name or query_name

        # Look up by name or hash
        if name:
            entry = self.registry.get_query_by_tag(name)
            identifier = name
            id_type = "name"
        else:
            entry = self.registry.get_query(hash)
            identifier = hash
            id_type = "hash"

        if not entry:
            return RdstResult(
                ok=False,
                message=f"No query found with {id_type}: {identifier}",
                data={id_type: identifier},
            )

        # Format output
        display_name = entry.tag or entry.hash[:12]

        details = f"""[{StyleTokens.SECONDARY}]Name:[/{StyleTokens.SECONDARY}] {entry.tag or "(unnamed)"}
[{StyleTokens.HASH}]Hash:[/{StyleTokens.HASH}] {entry.hash}
[{StyleTokens.SUCCESS}]Source:[/{StyleTokens.SUCCESS}] {entry.source}
[{StyleTokens.ACCENT}]First Analyzed:[/{StyleTokens.ACCENT}] {entry.first_analyzed[:19].replace("T", " ") if entry.first_analyzed else "never"}
[{StyleTokens.ACCENT}]Last Analyzed:[/{StyleTokens.ACCENT}] {entry.last_analyzed[:19].replace("T", " ") if entry.last_analyzed else "never"}
[{StyleTokens.ACCENT}]Frequency:[/{StyleTokens.ACCENT}] {entry.frequency}
[{StyleTokens.SECONDARY}]Target:[/{StyleTokens.SECONDARY}] {entry.last_target or "(none)"}
[{StyleTokens.SQL}]Stored Params:[/{StyleTokens.SQL}] {"yes" if entry.most_recent_params else "none"}"""

        # Add runtime stats if available (from rdst top)
        if entry.max_duration_ms > 0 or entry.observation_count > 0:
            details += f"""

[bold]Runtime Statistics[/bold] (from rdst top):
[{StyleTokens.ERROR}]Max Duration:[/{StyleTokens.ERROR}] {entry.max_duration_ms:,.1f}ms
[{StyleTokens.WARNING}]Avg Duration:[/{StyleTokens.WARNING}] {entry.avg_duration_ms:,.1f}ms
[{StyleTokens.SUCCESS}]Observations:[/{StyleTokens.SUCCESS}] {entry.observation_count}"""

        panel = MessagePanel(
            details,
            title=f"Query: {display_name}",
            variant="success",
        )
        self.console.print(panel)

        # SQL pattern with highlighted placeholders
        self.console.print(f"\n[{StyleTokens.TITLE}]SQL Pattern:[/{StyleTokens.TITLE}]")
        # Highlight :pN placeholders in bright_magenta for visibility
        sql_with_highlights = re.sub(
            r"(:p\d+)", f"[{StyleTokens.PARAM}]\\1[/{StyleTokens.PARAM}]", entry.sql
        )
        self.console.print(sql_with_highlights)

        # Show parameters and reconstructed query if parameters exist
        if entry.parameters:
            from ..query_registry import reconstruct_sql

            self.console.print(
                f"\n[{StyleTokens.TITLE}]Parameters:[/{StyleTokens.TITLE}]"
            )
            for param_name in sorted(
                entry.parameters.keys(),
                key=lambda x: int(x[1:]) if x[1:].isdigit() else 0,
            ):
                param_info = entry.parameters[param_name]
                value = param_info["value"]
                ptype = param_info["type"]
                if ptype == "string":
                    self.console.print(
                        f"  [{StyleTokens.ACCENT}]{f':{param_name}'}[/{StyleTokens.ACCENT}] = [{StyleTokens.SUCCESS}]{repr(value)}[/{StyleTokens.SUCCESS}] (string)"
                    )
                else:
                    self.console.print(
                        f"  [{StyleTokens.ACCENT}]{f':{param_name}'}[/{StyleTokens.ACCENT}] = [{StyleTokens.WARNING}]{str(value)}[/{StyleTokens.WARNING}] (number)"
                    )

            # Show reconstructed executable SQL
            try:
                executable_sql = reconstruct_sql(entry.sql, entry.parameters)
                self.console.print(
                    f"\n[{StyleTokens.TITLE}]Executable SQL:[/{StyleTokens.TITLE}] (with current parameters)"
                )
                self.console.print(
                    QueryPanel(
                        executable_sql,
                        title="Executable SQL",
                        border_style=StyleTokens.PANEL_BORDER,
                    )
                )
            except Exception:
                pass  # Reconstruction failed, just skip

        self.console.print()

        return RdstResult(
            ok=True,
            message=f"Showing query: {display_name}",
            data={
                "tag": entry.tag,
                "hash": entry.hash,
                "sql": entry.sql,
                "metadata": {
                    "source": entry.source,
                    "first_analyzed": entry.first_analyzed,
                    "last_analyzed": entry.last_analyzed,
                    "frequency": entry.frequency,
                    "target": entry.last_target,
                    "max_duration_ms": entry.max_duration_ms,
                    "avg_duration_ms": entry.avg_duration_ms,
                    "observation_count": entry.observation_count,
                },
            },
        )

    def _validate_editor(self, editor_name: str) -> Optional[str]:
        """
        Validate and resolve editor command to absolute path.

        Args:
            editor_name: Name or path of editor from environment or default

        Returns:
            Absolute path to validated editor executable, or None if invalid
        """
        if not editor_name:
            return None

        # Extract just the command name (first part before any spaces)
        # This prevents command injection via editor name
        command = editor_name.split()[0]

        # Resolve to absolute path using which()
        # This validates the command exists and is executable
        resolved_path = which(command)

        if not resolved_path:
            return None

        # Additional validation: ensure it's an absolute path
        path_obj = Path(resolved_path)
        if not path_obj.is_absolute():
            return None

        # Ensure the file exists and is executable
        if not (path_obj.exists() and os.access(str(path_obj), os.X_OK)):
            return None

        return resolved_path

    def _open_editor_for_query(
        self,
        name: str,
        existing_sql: Optional[str] = None,
        target_name: Optional[str] = None,
    ) -> Optional[str]:
        """
        Open $EDITOR for multi-line query input.

        Args:
            name: Name for the query
            existing_sql: Optional existing SQL to pre-fill
            target_name: Optional target database name

        Returns:
            SQL query string, or None if cancelled/empty
        """
        # Determine editor to use from environment
        editor_name = os.environ.get("EDITOR") or os.environ.get("VISUAL")

        # Validate editor from environment
        editor = self._validate_editor(editor_name) if editor_name else None

        if not editor:
            # Try common editors in order of preference
            for candidate in ["vim", "nano", "vi", "emacs"]:
                editor = self._validate_editor(candidate)
                if editor:
                    break

        if not editor:
            self.console.print(
                MessagePanel(
                    "No editor found.",
                    variant="error",
                    hint="Set $EDITOR environment variable or install vim/nano.",
                )
            )
            return None

        # Create template content
        template = self._create_editor_template(name, existing_sql, target_name)

        # Create temporary file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False) as f:
            f.write(template)
            f.flush()
            temp_path = f.name

        try:
            # Open editor
            # SECURITY: editor is validated via _validate_editor() to ensure:
            # 1. It's resolved to an absolute path via shutil.which()
            # 2. It's an actual executable file
            # 3. Command injection is prevented by using list form (not shell=True)
            # 4. Only the first word of editor name is used (splits on spaces)
            # Justification for nosemgrep: Editor path is validated through _validate_editor()
            # which uses shutil.which() to resolve to absolute path and verifies it's an
            # executable. List form prevents shell injection.
            subprocess.call([editor, temp_path])  # nosemgrep

            # Read edited content
            with open(temp_path, "r") as f:
                content = f.read()

            # Parse SQL from content
            sql = self._parse_query_from_editor_content(content)

            return sql
        finally:
            # Clean up temp file
            try:
                os.unlink(temp_path)
            except Exception:
                pass

    def _format_sql(self, sql: str) -> str:
        """
        Format SQL for better readability in the editor.

        Args:
            sql: Raw SQL string

        Returns:
            Formatted SQL string
        """
        if not sql:
            return sql

        if SQLPARSE_AVAILABLE:
            # Use sqlparse for professional formatting
            formatted = sqlparse.format(
                sql, reindent=True, keyword_case="upper", indent_width=2, wrap_after=80
            )
            return formatted
        else:
            # Fallback: basic formatting without sqlparse
            # Add newlines before major keywords
            keywords = [
                "SELECT",
                "FROM",
                "WHERE",
                "JOIN",
                "LEFT JOIN",
                "RIGHT JOIN",
                "INNER JOIN",
                "OUTER JOIN",
                "ON",
                "GROUP BY",
                "ORDER BY",
                "HAVING",
                "LIMIT",
                "OFFSET",
                "UNION",
                "WITH",
            ]

            formatted = sql
            for kw in keywords:
                # Add newline before keyword if not already at start of line
                formatted = formatted.replace(f" {kw} ", f"\n{kw} ")

            return formatted

    def _create_editor_template(
        self,
        name: str,
        existing_sql: Optional[str] = None,
        target_name: Optional[str] = None,
    ) -> str:
        """
        Create template content for editor.

        Args:
            name: Query name
            existing_sql: Optional existing SQL
            target_name: Optional target name

        Returns:
            Template string
        """
        template_lines = [
            f"-- rdst query: {name}",
        ]

        if target_name:
            template_lines.append(f"-- Target: {target_name}")
        else:
            template_lines.append("-- Target: (will prompt if needed)")

        template_lines.extend(
            [
                "--",
                "-- Enter your SQL query below this line.",
                "-- Lines starting with -- will be ignored.",
                "-- Save and exit to save to registry.",
                "",
            ]
        )

        if existing_sql:
            # Format the SQL for better readability
            formatted_sql = self._format_sql(existing_sql.strip())
            template_lines.append(formatted_sql)
        else:
            template_lines.extend(
                [
                    "SELECT ",
                    "  -- your columns here",
                    "FROM ",
                    "  -- your table here",
                    "WHERE ",
                    "  -- your conditions here",
                    ";",
                ]
            )

        return "\n".join(template_lines)

    def _parse_query_from_editor_content(self, content: str) -> Optional[str]:
        """
        Extract SQL from editor content, removing comment lines.

        Args:
            content: Raw content from editor

        Returns:
            SQL query string, or None if empty
        """
        lines = content.split("\n")
        sql_lines = []

        for line in lines:
            stripped = line.strip()
            # Skip comment lines (starting with --)
            if stripped.startswith("--"):
                continue
            # Skip empty lines
            if not stripped:
                continue
            # Keep SQL lines
            sql_lines.append(line.rstrip())

        sql = "\n".join(sql_lines).strip()

        # Return None if no actual SQL content
        if not sql:
            return None

        return sql

    def _read_query_from_file(self, file_path: str) -> Optional[str]:
        """
        Read SQL query from a file.

        Args:
            file_path: Path to SQL file

        Returns:
            SQL query string, or None if file not found/empty
        """
        try:
            path = Path(file_path)
            if not path.exists():
                return None

            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()

            # If file contains multiple statements, take the first one
            if ";" in content:
                statements = content.split(";")
                return statements[0].strip()

            return content
        except Exception as e:
            self.console.print(
                MessagePanel(
                    f"Failed to read file: {file_path}",
                    variant="error",
                    hint=str(e),
                )
            )
            return None

    def _command_exists(self, command: str) -> bool:
        """
        Check if a command exists in PATH.

        Args:
            command: Command name to check

        Returns:
            True if command exists, False otherwise
        """
        return which(command) is not None

    # =========================================================================
    # Run command implementation
    # =========================================================================

    def _execute_with_cancellation(
        self,
        conn,
        sql: str,
        stop_event: threading.Event,
        engine: str,
        target_config: dict,
    ) -> tuple[list | None, Exception | None]:
        """
        Execute query in thread with cancellation support.

        Runs the query in a background thread while monitoring stop_event.
        If stop_event is set (e.g., Ctrl+C), cancels the query on the server.

        Args:
            conn: Database connection
            sql: SQL query to execute
            stop_event: Event that signals cancellation request
            engine: 'postgresql' or 'mysql'
            target_config: Target configuration for MySQL cancel connection

        Returns:
            (results, exception) - results if successful, exception if failed/cancelled
        """
        from lib.db_connection import cancel_query

        result: list[list | None] = [None]
        exception: list[Exception | None] = [None]
        done = threading.Event()

        def execute():
            try:
                cursor = conn.cursor()
                cursor.execute(sql)
                result[0] = cursor.fetchall()
                cursor.close()
            except Exception as e:
                exception[0] = e
            finally:
                done.set()

        thread = threading.Thread(target=execute, daemon=True)
        thread.start()

        # Poll for completion or cancellation
        while not done.is_set():
            if stop_event.is_set():
                cancel_query(conn, engine, target_config)
                done.wait(timeout=2.0)  # Wait for query to abort
                if exception[0] is None:
                    exception[0] = KeyboardInterrupt("Query cancelled")
                break
            done.wait(timeout=0.1)

        return result[0], exception[0]

    def run(
        self,
        queries: list[str],
        target: str | None = None,
        interval: int | None = None,
        concurrency: int | None = None,
        duration: int | None = None,
        count: int | None = None,
        quiet: bool = False,
        **kwargs,
    ):
        """
        Run saved queries for benchmarking and load generation.

        Args:
            queries: Query names or hashes to run
            target: Target database (uses query's stored target if omitted)
            interval: Fixed interval mode - run every N milliseconds
            concurrency: Concurrency mode - maintain N concurrent executions
            duration: Stop after N seconds
            count: Stop after N total executions
            quiet: Suppress progress output

        Returns:
            RdstResult with execution statistics
        """
        from .rdst_cli import RdstResult, TargetsConfig
        from lib.db_connection import create_direct_connection, close_connection

        # Validate mode - cannot use both interval and concurrency
        if interval is not None and concurrency is not None:
            return RdstResult(
                ok=False,
                message="Cannot specify both --interval and --concurrency",
                data={},
            )

        # Determine execution mode
        # If --duration or --count specified without loop mode, default to tight loop (interval=0)
        if interval is None and concurrency is None:
            if duration is not None or count is not None:
                mode = "interval"
                interval = 0  # Run as fast as possible
            else:
                mode = "singleton"
        elif interval is not None:
            mode = "interval"
        else:
            mode = "concurrency"

        # Resolve queries from registry
        try:
            resolved_queries = self._resolve_queries(queries)
        except ValueError as e:
            return RdstResult(ok=False, message=str(e), data={})

        if not resolved_queries:
            return RdstResult(ok=False, message="No queries to run", data={})

        # Get target configuration
        cfg = TargetsConfig()
        cfg.load()

        # Use provided target, or fall back to first query's stored target, or default
        if not target:
            first_entry = resolved_queries[0][0]
            target = first_entry.last_target or cfg.get_default()

        if not target:
            return RdstResult(
                ok=False,
                message="No target specified. Use --target or configure a default.",
                data={},
            )

        target_config = cfg.get(target)
        if not target_config:
            return RdstResult(
                ok=False,
                message=f"Target '{target}' not found in configuration",
                data={"target": target},
            )

        # Initialize statistics
        stats = RunStatistics(start_time=time.perf_counter())

        # Set up signal handler for graceful shutdown
        stop_event = threading.Event()
        original_handler = signal.getsignal(signal.SIGINT)

        def signal_handler(signum, frame):
            if not quiet:
                self.console.print(
                    MessagePanel("Stopping (Ctrl+C)...", variant="warning")
                )
            stop_event.set()

        signal.signal(signal.SIGINT, signal_handler)

        try:
            if not quiet:
                query_names = [e.tag or e.hash[:8] for e, _ in resolved_queries]
                summary = {
                    "Queries": f"{len(resolved_queries)} ({', '.join(query_names)})",
                    "Target": target,
                    "Mode": mode,
                }
                if mode == "interval":
                    summary["Interval"] = (
                        "tight loop (no delay)" if interval == 0 else f"{interval}ms"
                    )
                elif mode == "concurrency":
                    summary["Concurrency"] = f"{concurrency}"
                if duration:
                    summary["Duration limit"] = f"{duration}s"
                if count:
                    summary["Count limit"] = f"{count}"
                self.console.print(
                    SectionBox("Query run", content=KeyValueTable(summary))
                )
                self.console.print()

            # Execute based on mode
            if mode == "singleton":
                self._run_singleton(
                    resolved_queries,
                    target_config,
                    stats,
                    stop_event,
                    quiet,
                    create_direct_connection,
                    close_connection,
                )
            elif mode == "interval":
                self._run_interval(
                    resolved_queries,
                    target_config,
                    stats,
                    interval,
                    stop_event,
                    duration,
                    count,
                    quiet,
                    create_direct_connection,
                    close_connection,
                )
            elif mode == "concurrency":
                self._run_concurrency(
                    resolved_queries,
                    target_config,
                    stats,
                    concurrency,
                    stop_event,
                    duration,
                    count,
                    quiet,
                    create_direct_connection,
                    close_connection,
                )

        except Exception as e:
            return RdstResult(
                ok=False, message=f"Error during execution: {e}", data={"error": str(e)}
            )
        finally:
            # Restore original signal handler
            signal.signal(signal.SIGINT, original_handler)

        # Print summary
        self._print_run_summary(stats)

        return RdstResult(
            ok=True,
            message=f"Completed {stats.total_executions} executions",
            data={
                "total_executions": stats.total_executions,
                "total_successes": stats.total_successes,
                "total_failures": stats.total_failures,
                "elapsed_seconds": stats.elapsed_seconds,
                "queries": {
                    h: {
                        "name": s.query_name,
                        "executions": s.executions,
                        "successes": s.successes,
                        "failures": s.failures,
                        "min_ms": s.min_ms,
                        "avg_ms": s.avg_ms,
                        "p95_ms": s.p95_ms,
                        "max_ms": s.max_ms,
                    }
                    for h, s in stats.query_stats.items()
                },
            },
        )

    def _resolve_queries(self, query_specs: list[str]) -> list[tuple[Any, str]]:
        """
        Resolve query names/hashes to QueryEntry and executable SQL.

        Args:
            query_specs: List of query names or hashes

        Returns:
            List of (QueryEntry, executable_sql) tuples

        Raises:
            ValueError: If any query not found
        """
        resolved = []
        for spec in query_specs:
            # Try by name first
            entry = self.registry.get_query_by_tag(spec)
            if not entry:
                # Try by hash
                entry = self.registry.get_query(spec)
            if not entry:
                raise ValueError(f"Query not found: {spec}")

            # Get executable SQL with parameters reconstructed
            sql = self.registry.get_executable_query(entry.hash, interactive=False)
            if not sql:
                sql = entry.sql  # Fallback to parameterized version

            resolved.append((entry, sql))

        return resolved

    def _run_singleton(
        self,
        queries: list[tuple[Any, str]],
        target_config: dict,
        stats: RunStatistics,
        stop_event: threading.Event,
        quiet: bool,
        create_conn,
        close_conn,
    ) -> None:
        """Run each query once sequentially."""
        if not quiet:
            self.console.print(
                MessagePanel("Connecting to database...", variant="info")
            )

        conn = create_conn(target_config)
        engine = target_config.get("engine", "postgresql")

        try:
            for entry, sql in queries:
                if stop_event.is_set():
                    break

                query_name = entry.tag or entry.hash[:8]
                if not quiet:
                    self.console.print(StatusLine("Executing", query_name))

                start = time.perf_counter()
                results, exc = self._execute_with_cancellation(
                    conn, sql, stop_event, engine, target_config
                )
                duration_ms = (time.perf_counter() - start) * 1000

                if isinstance(exc, KeyboardInterrupt):
                    if not quiet:
                        self.console.print(
                            StatusLine(
                                query_name, "CANCELLED", style=StyleTokens.WARNING
                            )
                        )
                    break
                elif exc:
                    stats.record_execution(
                        entry.hash, query_name, duration_ms, success=False
                    )
                    if not quiet:
                        self.console.print(
                            StatusLine(
                                query_name, f"FAILED ({exc})", style=StyleTokens.ERROR
                            )
                        )
                else:
                    stats.record_execution(
                        entry.hash, query_name, duration_ms, success=True
                    )
                    if not quiet:
                        self.console.print(
                            DurationDisplay(duration_ms, label=query_name)
                        )
        finally:
            close_conn(conn)

    def _run_interval(
        self,
        queries: list[tuple[Any, str]],
        target_config: dict,
        stats: RunStatistics,
        interval_ms: int,
        stop_event: threading.Event,
        max_duration: int | None,
        max_count: int | None,
        quiet: bool,
        create_conn,
        close_conn,
    ) -> None:
        """Run queries round-robin at fixed interval."""
        conn = create_conn(target_config)
        engine = target_config.get("engine", "postgresql")
        query_index = 0
        interval_sec = interval_ms / 1000.0
        last_display_update = 0
        display_interval = 0.25  # Update display every 250ms

        def run_loop(live=None):
            nonlocal query_index, last_display_update
            while not stop_event.is_set():
                # Check stop conditions
                if max_duration and stats.elapsed_seconds >= max_duration:
                    break
                if max_count and stats.total_executions >= max_count:
                    break

                entry, sql = queries[query_index]
                query_index = (query_index + 1) % len(queries)
                query_name = entry.tag or entry.hash[:8]

                start = time.perf_counter()
                results, exc = self._execute_with_cancellation(
                    conn, sql, stop_event, engine, target_config
                )
                duration_ms = (time.perf_counter() - start) * 1000

                if isinstance(exc, KeyboardInterrupt):
                    break
                elif exc:
                    stats.record_execution(
                        entry.hash, query_name, duration_ms, success=False
                    )
                else:
                    stats.record_execution(
                        entry.hash, query_name, duration_ms, success=True
                    )

                # Update live display periodically
                if (
                    live
                    and (time.perf_counter() - last_display_update) >= display_interval
                ):
                    live.update(self._create_progress_table(stats))
                    last_display_update = time.perf_counter()

                # Sleep for remaining interval time
                elapsed = time.perf_counter() - start
                sleep_time = max(0, interval_sec - elapsed)
                if sleep_time > 0 and not stop_event.is_set():
                    stop_event.wait(sleep_time)

        try:
            if not quiet:
                with Live(
                    self._create_progress_table(stats),
                    console=self.console,
                    refresh_per_second=4,
                ) as live:
                    run_loop(live)
            else:
                run_loop()
        finally:
            close_conn(conn)

    def _run_concurrency(
        self,
        queries: list[tuple[Any, str]],
        target_config: dict,
        stats: RunStatistics,
        concurrency: int,
        stop_event: threading.Event,
        max_duration: int | None,
        max_count: int | None,
        quiet: bool,
        create_conn,
        close_conn,
    ) -> None:
        """Maintain N concurrent query executions."""
        from lib.db_connection import cancel_query

        engine = target_config.get("engine", "postgresql")

        # Connection pool - one per worker
        connections: Queue = Queue()
        for _ in range(concurrency):
            conn = create_conn(target_config)
            connections.put(conn)

        # Track active connections for cancellation
        active_connections: set = set()
        active_lock = Lock()

        query_index = [0]  # Mutable for closure
        index_lock = threading.Lock()
        last_display_update = [0]  # Mutable for closure
        display_interval = 0.25

        def get_next_query() -> tuple[Any, str]:
            with index_lock:
                entry, sql = queries[query_index[0]]
                query_index[0] = (query_index[0] + 1) % len(queries)
                return entry, sql

        def cancel_all_active():
            """Cancel all currently executing queries."""
            with active_lock:
                for conn in active_connections:
                    cancel_query(conn, engine, target_config)

        def execute_query() -> bool:
            """Execute one query. Returns True if should continue."""
            if stop_event.is_set():
                return False
            if max_duration and stats.elapsed_seconds >= max_duration:
                return False
            if max_count and stats.total_executions >= max_count:
                return False

            try:
                conn = connections.get(timeout=1.0)
            except Empty:
                return True  # Retry

            try:
                entry, sql = get_next_query()
                query_name = entry.tag or entry.hash[:8]

                # Track this connection as active
                with active_lock:
                    active_connections.add(conn)

                start = time.perf_counter()
                try:
                    cursor = conn.cursor()
                    cursor.execute(sql)
                    cursor.fetchall()
                    cursor.close()
                    duration_ms = (time.perf_counter() - start) * 1000
                    stats.record_execution(
                        entry.hash, query_name, duration_ms, success=True
                    )
                except Exception:
                    duration_ms = (time.perf_counter() - start) * 1000
                    # Don't record cancelled queries as failures
                    if not stop_event.is_set():
                        stats.record_execution(
                            entry.hash, query_name, duration_ms, success=False
                        )
                finally:
                    with active_lock:
                        active_connections.discard(conn)
            finally:
                connections.put(conn)

            return True

        def run_executor(live=None):
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = set()

                # Submit initial batch
                for _ in range(concurrency):
                    if stop_event.is_set():
                        break
                    futures.add(executor.submit(execute_query))

                # Keep submitting as tasks complete
                while futures and not stop_event.is_set():
                    # Check stop conditions
                    if max_duration and stats.elapsed_seconds >= max_duration:
                        break
                    if max_count and stats.total_executions >= max_count:
                        break

                    # Update live display periodically
                    if (
                        live
                        and (time.perf_counter() - last_display_update[0])
                        >= display_interval
                    ):
                        live.update(self._create_progress_table(stats))
                        last_display_update[0] = time.perf_counter()

                    # Wait for any task to complete
                    done, futures = wait(
                        futures, timeout=0.1, return_when=FIRST_COMPLETED
                    )

                    # Submit replacement tasks
                    for future in done:
                        try:
                            should_continue = future.result()
                            if should_continue and not stop_event.is_set():
                                if not (
                                    max_count and stats.total_executions >= max_count
                                ):
                                    futures.add(executor.submit(execute_query))
                        except Exception:
                            pass

                # Cancel all active queries on stop
                if stop_event.is_set():
                    cancel_all_active()

                # Cancel remaining futures
                for future in futures:
                    future.cancel()

        try:
            if not quiet:
                with Live(
                    self._create_progress_table(stats),
                    console=self.console,
                    refresh_per_second=4,
                ) as live:
                    run_executor(live)
            else:
                run_executor()
        finally:
            # Close all connections
            while not connections.empty():
                try:
                    conn = connections.get_nowait()
                    close_conn(conn)
                except Empty:
                    break

    def _create_progress_table(self, stats: RunStatistics):
        """Create a Rich table showing live progress stats."""
        return QueryStatsTable(
            stats,
            title="rdst query run",
            show_qps=True,
            show_percentiles=False,
            show_caption=True,
        )

    def _print_run_summary(self, stats: RunStatistics) -> None:
        """Print execution summary table."""
        if not stats.query_stats:
            self.console.print(
                EmptyState("No executions recorded.", title="rdst query run Summary")
            )
            return

        # Use UI component with consistent styling
        table = QueryStatsTable(stats, title="rdst query run Summary")
        self.console.print()
        self.console.print(table)

        # Summary line
        elapsed = stats.elapsed_seconds
        qps = stats.total_executions / elapsed if elapsed > 0 else 0
        error_rate = (
            (stats.total_failures / stats.total_executions * 100)
            if stats.total_executions > 0
            else 0
        )

        self.console.print(
            KeyValueTable(
                {
                    "Duration": f"{elapsed:.1f}s",
                    "QPS": f"{qps:.2f}",
                    "Errors": f"{error_rate:.2f}%",
                }
            )
        )

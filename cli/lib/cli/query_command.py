"""
Query Command Implementation

Manages query registry: add, edit, list, delete queries.
Separate from analysis - purely for query management.
"""
from __future__ import annotations

import os
import shlex
import subprocess  # nosemgrep: gitlab.bandit.B404
import tempfile
from pathlib import Path
from shutil import which
from typing import Optional, Tuple

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

try:
    import sqlparse
    SQLPARSE_AVAILABLE = True
except ImportError:
    SQLPARSE_AVAILABLE = False

from lib.query_registry.query_registry import QueryRegistry


class QueryCommand:
    """
    Manages the query registry through query subcommands.

    Provides add, edit, list, show, and delete operations for queries.
    Uses $EDITOR for multi-line query input.
    """

    def __init__(self):
        self.registry = QueryRegistry()
        self.console = Console() if RICH_AVAILABLE else None

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
        else:
            return RdstResult(
                ok=False,
                message=f"Unknown query subcommand: {subcommand}",
                data={"subcommand": subcommand}
            )

    def add(self, name: str, query: Optional[str] = None,
            file: Optional[str] = None, target: Optional[str] = None,
            **kwargs):
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
                ok=False,
                message="Query name is required for 'rdst query add'",
                data={}
            )

        # Check if query name already exists
        existing = self.registry.get_query_by_tag(name)
        if existing:
            return RdstResult(
                ok=False,
                message=f"Query '{name}' already exists (hash: {existing.hash}). Use 'rdst query edit {name}' to modify.",
                data={"name": name, "existing_hash": existing.hash}
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
                    data={"file": file}
                )
            source = "file"
        else:
            # Open editor for multi-line input
            sql = self._open_editor_for_query(name, target_name=target)
            if not sql:
                return RdstResult(
                    ok=False,
                    message="No query provided (editor was empty or cancelled)",
                    data={"name": name}
                )
            source = "manual"

        # Add to registry
        try:
            query_hash, is_new = self.registry.add_query(
                sql=sql,
                tag=name,
                source=source,
                target=target or ""
            )

            # Show formatted output with colors if Rich available
            if RICH_AVAILABLE and self.console:
                self.console.print(f"[green]✓ Query added to registry[/green]")
                self.console.print(f"  Name: [cyan]{name}[/cyan]")
                self.console.print(f"  Hash: [yellow]{query_hash}[/yellow]")
                self.console.print(f"  Source: {source}")
                if target:
                    self.console.print(f"  Target: [magenta]{target}[/magenta]")

                # Breadcrumb with colors
                # Colors: rdst=white, subcommand=green, values/quoted=blue, descriptions=dim
                self.console.print()
                self.console.print("[cyan]Next Steps:[/cyan]")
                if target:
                    self.console.print(f"  rdst [green]analyze[/green] --hash [blue]{query_hash[:8]}[/blue] --target [blue]{target}[/blue]   [dim]Analyze this query[/dim]")
                else:
                    self.console.print(f"  rdst [green]analyze[/green] --hash [blue]{query_hash[:8]}[/blue] --target [blue]<target>[/blue]   [dim]Analyze this query[/dim]")
                self.console.print(f"  rdst [green]query show[/green] [blue]{name}[/blue]                              [dim]View query details[/dim]")

                msg = ""  # Already printed
            else:
                msg = f"✓ Query added to registry\n  Name: {name}\n  Hash: {query_hash}\n  Source: {source}"
                if target:
                    msg += f"\n  Target: {target}"
                msg += "\n\nNext Steps:"
                if target:
                    msg += f"\n  rdst analyze --hash {query_hash[:8]} --target {target}   Analyze this query"
                else:
                    msg += f"\n  rdst analyze --hash {query_hash[:8]} --target <target>   Analyze this query"
                msg += f"\n  rdst query show {name}                              View query details"

            return RdstResult(
                ok=True,
                message=msg,
                data={
                    "name": name,
                    "hash": query_hash,
                    "is_new": is_new,
                    "sql": sql
                }
            )
        except Exception as e:
            return RdstResult(
                ok=False,
                message=f"Failed to add query: {str(e)}",
                data={"name": name, "error": str(e)}
            )

    def import_queries(self, file: str, update: bool = False, target: Optional[str] = None, **kwargs):
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
                ok=False,
                message="File path is required for import",
                data={}
            )

        # Read file
        try:
            file_path = Path(file)
            if not file_path.exists():
                return RdstResult(
                    ok=False,
                    message=f"File not found: {file}",
                    data={"file": file}
                )

            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            return RdstResult(
                ok=False,
                message=f"Failed to read file: {str(e)}",
                data={"file": file, "error": str(e)}
            )

        # Parse queries from file
        queries = self._parse_import_file(content, default_target=target)

        if not queries:
            return RdstResult(
                ok=False,
                message=f"No queries found in file: {file}",
                data={"file": file}
            )

        # Import each query
        imported = 0
        updated = 0
        skipped = 0
        errors = []

        for query_data in queries:
            name = query_data.get('name')
            sql = query_data.get('sql')
            query_target = query_data.get('target', target)

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
                            target=query_target or ""
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
                        sql=sql,
                        tag=name,
                        source="import",
                        target=query_target or ""
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
                "file": file
            }
        )

    def _parse_import_file(self, content: str, default_target: Optional[str] = None) -> list:
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
        lines = content.split('\n')

        i = 0
        while i < len(lines):
            # Look for metadata comments
            metadata = {}
            sql_lines = []

            # Parse metadata comments
            while i < len(lines):
                line = lines[i].strip()

                # Check for metadata comment
                if line.startswith('-- name:'):
                    metadata['name'] = line.split(':', 1)[1].strip()
                    i += 1
                elif line.startswith('-- target:'):
                    metadata['target'] = line.split(':', 1)[1].strip()
                    i += 1
                elif line.startswith('-- frequency:'):
                    try:
                        metadata['frequency'] = int(line.split(':', 1)[1].strip())
                    except:
                        pass
                    i += 1
                elif line.startswith('--') and not line.startswith('---'):
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
                if ';' in line:
                    i += 1
                    break
                i += 1

            # Extract SQL and clean up
            if sql_lines:
                sql = '\n'.join(sql_lines).strip()
                # Remove trailing semicolon for consistency
                if sql.endswith(';'):
                    sql = sql[:-1].strip()

                # Only add if we have both name and SQL
                if metadata.get('name') and sql:
                    queries.append({
                        'name': metadata['name'],
                        'sql': sql,
                        'target': metadata.get('target', default_target),
                        'frequency': metadata.get('frequency')
                    })

        return queries

    def edit(self, name: Optional[str] = None, hash: Optional[str] = None,
             **kwargs):
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
                    data={"name": name}
                )
            identifier = name
            identifier_type = "name"
        elif hash:
            entry = self.registry.get_query(hash)
            if not entry:
                return RdstResult(
                    ok=False,
                    message=f"No query found with hash: {hash}",
                    data={"hash": hash}
                )
            identifier = hash
            identifier_type = "hash"
        else:
            return RdstResult(
                ok=False,
                message="Must provide either a query name or --hash for edit",
                data={}
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
            target_name=entry.last_target
        )

        if not new_sql:
            return RdstResult(
                ok=False,
                message="Edit cancelled (no changes or empty query)",
                data={identifier_type: identifier}
            )

        # Check if SQL actually changed
        if new_sql.strip() == old_sql.strip():
            return RdstResult(
                ok=True,
                message=f"No changes made to query {identifier_type}: {identifier}",
                data={identifier_type: identifier, "hash": old_hash}
            )

        # Update registry
        # Note: If SQL changes significantly, hash will change
        # We'll remove old entry and add new one with same tag
        try:
            new_hash, is_new = self.registry.add_query(
                sql=new_sql,
                tag=old_tag,  # Preserve tag
                source=entry.source,
                target=entry.last_target
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
                    "sql": new_sql
                }
            )
        except Exception as e:
            return RdstResult(
                ok=False,
                message=f"Failed to update query: {str(e)}",
                data={identifier_type: identifier, "error": str(e)}
            )

    def delete(self, name: Optional[str] = None, hash: Optional[str] = None,
               force: bool = False, **kwargs):
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
                    data={"name": name}
                )
            query_hash = entry.hash
            identifier = f"query '{name}'"
        elif hash:
            entry = self.registry.get_query(hash)
            if not entry:
                return RdstResult(
                    ok=False,
                    message=f"No query found with hash: {hash}",
                    data={"hash": hash}
                )
            query_hash = entry.hash  # Use full hash from entry, not the input prefix
            identifier = f"hash {hash}"
        else:
            return RdstResult(
                ok=False,
                message="Must provide either a query name or --hash for delete",
                data={}
            )

        # Confirm deletion unless --force
        if not force:
            if RICH_AVAILABLE:
                confirmed = Confirm.ask(f"Delete query {identifier}?", default=False)
            else:
                response = input(f"Delete query {identifier}? (y/N): ").strip().lower()
                confirmed = response == 'y'

            if not confirmed:
                return RdstResult(
                    ok=False,
                    message="Deletion cancelled",
                    data={"identifier": identifier}
                )

        # Delete from registry
        try:
            removed = self.registry.remove_query(query_hash)
            if removed:
                msg = f"✓ Query deleted: {identifier} (hash: {query_hash})"
                return RdstResult(
                    ok=True,
                    message=msg,
                    data={"hash": query_hash, "name": name or ""}
                )
            else:
                return RdstResult(
                    ok=False,
                    message=f"Failed to delete query {identifier}",
                    data={"hash": query_hash}
                )
        except Exception as e:
            return RdstResult(
                ok=False,
                message=f"Error deleting query: {str(e)}",
                data={"identifier": identifier, "error": str(e)}
            )

    def list(self, limit: int = 10, target: str = None, filter: str = None,
             interactive: bool = False, **kwargs):
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
                data={"queries": []}
            )

        # Apply target filter if specified
        if target:
            target_lower = target.lower()
            queries = [q for q in queries if q.last_target and target_lower in q.last_target.lower()]
            if not queries:
                return RdstResult(
                    ok=True,
                    message=f"No queries found for target: '{target}'",
                    data={"queries": []}
                )

        # Apply smart filter if specified
        if filter:
            filter_lower = filter.lower()
            filtered_queries = []
            for query in queries:
                matches = [
                    filter_lower in query.sql.lower(),                    # SQL content
                    query.tag and filter_lower in query.tag.lower(),      # Name/tag
                    filter_lower in query.hash.lower(),                   # Hash
                    filter_lower in query.source.lower(),                 # Source
                    query.last_target and filter_lower in query.last_target.lower(),  # Target
                ]
                if any(matches):
                    filtered_queries.append(query)
            queries = filtered_queries
            if not queries:
                return RdstResult(
                    ok=True,
                    message=f"No queries found matching filter: '{filter}'",
                    data={"queries": []}
                )

        total_queries = len(queries)

        # Use interactive mode only if explicitly requested AND we have a TTY
        use_interactive = interactive and sys.stdin.isatty()

        if use_interactive:
            return self._interactive_query_list(queries, limit, target, filter)
        else:
            return self._plain_query_list(queries, limit, target, filter)

    def _plain_query_list(self, queries: list, limit: int, target: str = None, filter: str = None):
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

        # Format output
        if RICH_AVAILABLE and self.console:
            table = Table(title=title)
            table.add_column("Name", style="cyan")
            table.add_column("Hash", style="yellow")
            table.add_column("Target", style="magenta")
            table.add_column("Source", style="green")
            table.add_column("Last Analyzed", style="blue")
            table.add_column("SQL Preview", style="white")

            for q in queries:
                # Format timestamp
                timestamp = q.last_analyzed[:19].replace('T', ' ') if q.last_analyzed else "never"
                # Preview SQL (first 50 chars)
                sql_preview = (q.sql[:50] + '...') if len(q.sql) > 50 else q.sql

                table.add_row(
                    q.tag or "(unnamed)",
                    q.hash[:8],
                    q.last_target or "-",
                    q.source,
                    timestamp,
                    sql_preview
                )

            self.console.print(table)
        else:
            # Plain text output
            print(f"\n{title}")
            print("-" * 100)
            for q in queries:
                timestamp = q.last_analyzed[:19].replace('T', ' ') if q.last_analyzed else "never"
                sql_preview = (q.sql[:50] + '...') if len(q.sql) > 50 else q.sql
                target_display = q.last_target or "-"
                print(f"Name: {q.tag or '(unnamed)':20} Hash: {q.hash[:8]:10} Target: {target_display:15} Source: {q.source:10}")
                print(f"  Last analyzed: {timestamp}")
                print(f"  SQL: {sql_preview}")
                print()

        return RdstResult(
            ok=True,
            message=f"Listed {len(queries)} queries",
            data={"queries": [{"tag": q.tag, "hash": q.hash, "sql": q.sql, "target": q.last_target} for q in queries]}
        )

    def _interactive_query_list(self, queries: list, page_size: int = 10,
                                 target: str = None, filter: str = None):
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
            print("\033[H\033[J", end="")

            # Build title with filter info
            title_parts = [f"Query Registry ({total} queries"]
            if target:
                title_parts.append(f", target: {target}")
            if filter:
                title_parts.append(f", filter: '{filter}'")
            title_parts.append(")")
            title = "".join(title_parts)

            # Show table with selection numbers
            if RICH_AVAILABLE and self.console:
                table = Table(title=title)
                table.add_column("#", style="bold green", width=3)
                table.add_column("Name", style="cyan")
                table.add_column("Hash", style="yellow")
                table.add_column("Target", style="magenta")
                table.add_column("Source", style="green")
                table.add_column("Last Analyzed", style="blue")
                table.add_column("SQL Preview", style="white")

                for i, q in enumerate(page_queries):
                    num = i + 1
                    timestamp = q.last_analyzed[:19].replace('T', ' ') if q.last_analyzed else "never"
                    sql_preview = (q.sql[:50] + '...') if len(q.sql) > 50 else q.sql

                    table.add_row(
                        str(num),
                        q.tag or "(unnamed)",
                        q.hash[:8],
                        q.last_target or "-",
                        q.source,
                        timestamp,
                        sql_preview
                    )

                self.console.print(table)
                self.console.print(f"\n[dim]Page {page+1}/{max_page+1} (showing {start+1}-{end} of {total})[/dim]")
            else:
                # Plain text table
                print(f"\n{title}")
                print(f"Page {page+1}/{max_page+1} (showing {start+1}-{end} of {total})\n")
                print("-" * 100)
                for i, q in enumerate(page_queries):
                    num = i + 1
                    timestamp = q.last_analyzed[:19].replace('T', ' ') if q.last_analyzed else "never"
                    sql_preview = (q.sql[:50] + '...') if len(q.sql) > 50 else q.sql
                    target_display = q.last_target or "-"
                    print(f"[{num}] Name: {q.tag or '(unnamed)':20} Hash: {q.hash[:8]:10} Target: {target_display:15}")
                    print(f"    Last: {timestamp}  SQL: {sql_preview}")
                    print()

            # Show navigation options
            print()
            nav_options = []
            if page > 0:
                nav_options.append("[p] Prev")
            if page < max_page:
                nav_options.append("[n] Next")
            nav_options.append("[q/Esc] Quit")

            if RICH_AVAILABLE and self.console:
                self.console.print(f"[dim]Enter # to analyze | {' | '.join(nav_options)}[/dim]")
            else:
                print(f"Enter # to analyze | {' | '.join(nav_options)}")

            # Get user input
            try:
                choice = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nCancelled")
                return RdstResult(ok=True, message="Query list cancelled", data={"queries": []})

            # Handle escape key (shows as empty or \x1b)
            if choice == '' or choice == '\x1b' or choice.lower() == 'q':
                return RdstResult(
                    ok=True,
                    message=f"Listed {total} queries",
                    data={"queries": [{"tag": q.tag, "hash": q.hash, "sql": q.sql, "target": q.last_target} for q in queries[:page_size]]}
                )
            elif choice.lower() == 'n' and page < max_page:
                page += 1
                continue
            elif choice.lower() == 'p' and page > 0:
                page -= 1
                continue
            elif choice.isdigit():
                num = int(choice)
                if 1 <= num <= len(page_queries):
                    selected = page_queries[num - 1]
                    # Exit interactive mode and return selection for analyze
                    # Clear screen to restore normal terminal
                    print("\033[H\033[J", end="")
                    return RdstResult(
                        ok=True,
                        message="",  # Message will be handled by caller
                        data={
                            "action": "analyze",
                            "selected_hash": selected.hash,
                            "selected_tag": selected.tag,
                            "selected_sql": selected.sql,
                            "selected_target": selected.last_target
                        }
                    )
                else:
                    print(f"Invalid selection. Enter 1-{len(page_queries)}")
                    input("Press Enter to continue...")
            else:
                print(f"Unknown option: {choice}")
                input("Press Enter to continue...")

    def _analyze_selected_query(self, query_entry):
        """Analyze the selected query."""
        from .rdst_cli import RdstResult

        if RICH_AVAILABLE and self.console:
            self.console.print(f"\n[bold]Analyzing query:[/bold] {query_entry.tag or query_entry.hash[:8]}")
        else:
            print(f"\nAnalyzing query: {query_entry.tag or query_entry.hash[:8]}")

        # Import and run analyze
        try:
            from .analyze_command import AnalyzeCommand, AnalyzeInput
            analyze_cmd = AnalyzeCommand()

            # Get executable query (with parameters if available)
            sql = self.registry.get_executable_query(query_entry.hash, interactive=False)
            if not sql:
                sql = query_entry.sql

            # Create AnalyzeInput
            resolved_input = AnalyzeInput(
                sql=sql,
                normalized_sql=query_entry.sql,
                source="registry",
                hash=query_entry.hash,
                tag=query_entry.tag or "",
                save_as=""
            )

            # Run analysis
            result = analyze_cmd.execute_analyze(
                resolved_input=resolved_input,
                target=query_entry.last_target,
                interactive=False
            )

            return RdstResult(
                ok=True,
                message="Analysis complete",
                data={"selected_hash": query_entry.hash, "analysis": result}
            )
        except Exception as e:
            return RdstResult(
                ok=False,
                message=f"Failed to analyze query: {e}",
                data={"selected_hash": query_entry.hash, "error": str(e)}
            )

    def show(self, name: str = None, query_name: str = None, hash: str = None, **kwargs):
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
                data={id_type: identifier}
            )

        # Format output
        display_name = entry.tag or entry.hash[:12]
        if RICH_AVAILABLE:
            from rich.syntax import Syntax

            details = f"""[cyan]Name:[/cyan] {entry.tag or '(unnamed)'}
[yellow]Hash:[/yellow] {entry.hash}
[green]Source:[/green] {entry.source}
[blue]First Analyzed:[/blue] {entry.first_analyzed[:19].replace('T', ' ') if entry.first_analyzed else 'never'}
[blue]Last Analyzed:[/blue] {entry.last_analyzed[:19].replace('T', ' ') if entry.last_analyzed else 'never'}
[magenta]Frequency:[/magenta] {entry.frequency}
[cyan]Target:[/cyan] {entry.last_target or '(none)'}
[white]Parameters:[/white] {len(entry.parameter_history) if entry.parameter_history else 0} sets in history"""

            panel = Panel(details, title=f"Query: {display_name}", border_style="green")
            self.console.print(panel)

            # SQL with syntax highlighting
            self.console.print("\n[bold]SQL:[/bold]")
            sql_syntax = Syntax(entry.sql, "sql", theme="monokai", line_numbers=False, word_wrap=True)
            self.console.print(sql_syntax)
            self.console.print()
        else:
            # Plain text output
            print(f"\n{'='*80}")
            print(f"Query: {display_name}")
            print(f"{'='*80}")
            print(f"Name:           {entry.tag or '(unnamed)'}")
            print(f"Hash:           {entry.hash}")
            print(f"Source:         {entry.source}")
            print(f"First Analyzed: {entry.first_analyzed[:19].replace('T', ' ') if entry.first_analyzed else 'never'}")
            print(f"Last Analyzed:  {entry.last_analyzed[:19].replace('T', ' ') if entry.last_analyzed else 'never'}")
            print(f"Frequency:      {entry.frequency}")
            print(f"Target:         {entry.last_target or '(none)'}")
            print(f"Parameters:     {len(entry.parameter_history) if entry.parameter_history else 0} sets in history")
            print(f"\nSQL:")
            print(entry.sql)
            print(f"{'='*80}")

            # Breadcrumb for plain text
            print("\nNext Steps:")
            if entry.last_target:
                print(f"  rdst analyze --hash {entry.hash[:8]} --target {entry.last_target}   Analyze this query")
            else:
                print(f"  rdst analyze --hash {entry.hash[:8]} --target <target>   Analyze this query")
            print(f"  rdst query edit {name}                        Edit this query")
            print(f"  rdst query list                              View all queries")
            print()

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
                    "target": entry.last_target
                }
            }
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

    def _open_editor_for_query(self, name: str, existing_sql: Optional[str] = None,
                                target_name: Optional[str] = None) -> Optional[str]:
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
        editor_name = os.environ.get('EDITOR') or os.environ.get('VISUAL')

        # Validate editor from environment
        editor = self._validate_editor(editor_name) if editor_name else None

        if not editor:
            # Try common editors in order of preference
            for candidate in ['vim', 'nano', 'vi', 'emacs']:
                editor = self._validate_editor(candidate)
                if editor:
                    break

        if not editor:
            print("Error: No editor found. Set $EDITOR environment variable or install vim/nano.")
            return None

        # Create template content
        template = self._create_editor_template(name, existing_sql, target_name)

        # Create temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False) as f:
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
            with open(temp_path, 'r') as f:
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
                sql,
                reindent=True,
                keyword_case='upper',
                indent_width=2,
                wrap_after=80
            )
            return formatted
        else:
            # Fallback: basic formatting without sqlparse
            # Add newlines before major keywords
            keywords = ['SELECT', 'FROM', 'WHERE', 'JOIN', 'LEFT JOIN', 'RIGHT JOIN',
                       'INNER JOIN', 'OUTER JOIN', 'ON', 'GROUP BY', 'ORDER BY',
                       'HAVING', 'LIMIT', 'OFFSET', 'UNION', 'WITH']

            formatted = sql
            for kw in keywords:
                # Add newline before keyword if not already at start of line
                formatted = formatted.replace(f' {kw} ', f'\n{kw} ')

            return formatted

    def _create_editor_template(self, name: str, existing_sql: Optional[str] = None,
                                 target_name: Optional[str] = None) -> str:
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

        template_lines.extend([
            "--",
            "-- Enter your SQL query below this line.",
            "-- Lines starting with -- will be ignored.",
            "-- Save and exit to save to registry.",
            "",
        ])

        if existing_sql:
            # Format the SQL for better readability
            formatted_sql = self._format_sql(existing_sql.strip())
            template_lines.append(formatted_sql)
        else:
            template_lines.extend([
                "SELECT ",
                "  -- your columns here",
                "FROM ",
                "  -- your table here",
                "WHERE ",
                "  -- your conditions here",
                ";"
            ])

        return "\n".join(template_lines)

    def _parse_query_from_editor_content(self, content: str) -> Optional[str]:
        """
        Extract SQL from editor content, removing comment lines.

        Args:
            content: Raw content from editor

        Returns:
            SQL query string, or None if empty
        """
        lines = content.split('\n')
        sql_lines = []

        for line in lines:
            stripped = line.strip()
            # Skip comment lines (starting with --)
            if stripped.startswith('--'):
                continue
            # Skip empty lines
            if not stripped:
                continue
            # Keep SQL lines
            sql_lines.append(line.rstrip())

        sql = '\n'.join(sql_lines).strip()

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

            with open(path, 'r', encoding='utf-8') as f:
                content = f.read().strip()

            # If file contains multiple statements, take the first one
            if ';' in content:
                statements = content.split(';')
                return statements[0].strip()

            return content
        except Exception as e:
            print(f"Error reading file {file_path}: {e}")
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

"""
RDST Analyze Command Implementation

Handles all query input modes for the 'rdst analyze' command:
1. Inline query input (-q)
2. File input (-f)
3. Stdin input (--stdin)
4. Interactive prompt (fallback)
5. Registry lookup by hash (--hash)
6. Registry lookup by name (--name)
7. Input precedence and deduplication
8. SQL normalization and dialect detection
"""
from __future__ import annotations

import sys
import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from .rdst_cli import RdstResult
from concurrent.futures import ThreadPoolExecutor

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False
    Console = None
    Panel = None
    Prompt = None

from ..query_registry.query_registry import QueryRegistry, normalize_sql, hash_sql
from ..query_registry.conversation_registry import ConversationRegistry
from ..llm_manager.llm_manager import LLMManager
from .parameter_prompt import has_unresolved_placeholders, prompt_for_parameters


@dataclass
class AnalyzeInput:
    """Represents the resolved input for analyze command."""
    sql: str  # Original SQL with actual parameter values
    normalized_sql: str  # Normalized SQL with ? placeholders
    source: str  # "query-id", "inline", "file", "stdin", "prompt"
    hash: str
    tag: str = ""
    save_as: str = ""


class AnalyzeInputError(Exception):
    """Raised when there are issues with analyze input."""
    pass


class AnalyzeCommand:
    """Handles all functionality for the rdst analyze command."""

    def __init__(self, client=None):
        """Initialize the AnalyzeCommand with an optional CloudAgentClient."""
        self.client = client
        self._console = Console() if _RICH_AVAILABLE else None
        self.registry = QueryRegistry()

    def resolve_input(self, hash: Optional[str] = None,
                      inline_query: Optional[str] = None,
                      file_path: Optional[str] = None,
                      use_stdin: bool = False,
                      name: Optional[str] = None,
                      positional_query: Optional[str] = None,
                      save_as: Optional[str] = None,
                      large_query_bypass: bool = False) -> AnalyzeInput:
        """
        Resolve query input using strict precedence rules.

        Precedence: hash > name > inline (-q) > file (-f) > stdin > prompt > positional

        Args:
            hash: Query hash from registry
            inline_query: SQL query string from -q flag
            file_path: Path to SQL file from -f flag
            use_stdin: Whether to read from stdin
            name: Query name for registry lookup
            positional_query: Positional query argument (backward compatibility)
            save_as: Name to save query as after analysis
            large_query_bypass: If True, allows queries up to 10KB instead of 1KB

        Returns:
            AnalyzeInput with resolved SQL and metadata

        Raises:
            AnalyzeInputError: If input resolution fails
        """

        # Count non-None inputs for warning about extras
        inputs_provided = [
            ("hash", hash),
            ("name", name),
            ("inline", inline_query),
            ("file", file_path),
            ("stdin", use_stdin),
            ("positional", positional_query)
        ]
        active_inputs = [(name, value) for name, value in inputs_provided if value]

        if len(active_inputs) > 1:
            primary = active_inputs[0][0]
            ignored = [name for name, _ in active_inputs[1:]]
            print(f"Warning: Using {primary} input, ignoring: {', '.join(ignored)}")

        # Apply precedence rules
        try:
            # 1. Registry lookup by hash
            if hash:
                return self._resolve_by_hash(hash, save_as)

            # 2. Registry lookup by name
            if name:
                return self._resolve_by_name(name, save_as)

            # 3. Inline query
            if inline_query:
                return self._resolve_inline_query(inline_query, save_as, large_query_bypass)

            # 4. File input
            if file_path:
                return self._resolve_file_input(file_path, save_as, large_query_bypass)

            # 5. Stdin input
            if use_stdin:
                return self._resolve_stdin_input(save_as, large_query_bypass)

            # 6. Interactive prompt
            if not positional_query:
                return self._resolve_interactive_prompt(save_as)

            # 7. Positional query (lowest precedence, backward compatibility)
            # Auto-detect if positional argument is a hash (12-char hex)
            if positional_query and self._looks_like_hash(positional_query):
                return self._resolve_by_hash(positional_query, save_as)

            return self._resolve_inline_query(positional_query, save_as, large_query_bypass)

        except Exception as e:
            raise AnalyzeInputError(f"Failed to resolve input: {e}")

    def _resolve_by_hash(self, hash: str, save_as: str) -> AnalyzeInput:
        """Resolve query by hash from registry."""
        entry = self.registry.get_query(hash)
        if not entry:
            raise AnalyzeInputError(f"Query hash '{hash}' not found in registry. Run 'rdst query list' to see available queries.")

        # Get the executable SQL with parameter values reconstructed
        executable_sql = self.registry.get_executable_query(hash, interactive=False)
        if not executable_sql:
            raise AnalyzeInputError(f"Could not reconstruct executable query for hash '{hash}'")

        return AnalyzeInput(
            sql=executable_sql,  # Original SQL with parameter values
            normalized_sql=entry.sql,  # Normalized SQL with ? placeholders
            source="hash",
            hash=entry.hash,
            tag=entry.tag,
            save_as=save_as
        )

    def _resolve_by_name(self, name: str, save_as: str) -> AnalyzeInput:
        """Resolve query by name from registry."""
        entry = self.registry.get_query_by_tag(name)
        if not entry:
            raise AnalyzeInputError(f"Query '{name}' not found in registry. Run 'rdst query list' to see available queries.")

        # Get the executable SQL with parameter values reconstructed
        executable_sql = self.registry.get_executable_query_by_tag(name, interactive=False)
        if not executable_sql:
            raise AnalyzeInputError(f"Could not reconstruct executable query for '{name}'")

        return AnalyzeInput(
            sql=executable_sql,  # Original SQL with parameter values
            normalized_sql=entry.sql,  # Normalized SQL with ? placeholders
            source="name",
            hash=entry.hash,
            tag=entry.tag,
            save_as=save_as
        )

    def _enforce_query_size_limit(self, query: str, bypass: bool = False) -> None:
        """
        Enforce query size limits.

        Args:
            query: The SQL query string to check
            bypass: If True, use 10KB limit instead of 1KB

        Raises:
            AnalyzeInputError: If query exceeds the size limit
        """
        from lib.data_manager_service.data_manager_service_command_sets import MAX_QUERY_LENGTH
        query_bytes = len(query.encode('utf-8'))

        if bypass:
            # With bypass flag, allow up to 10KB
            max_bypass_size = 10 * 1024  # 10KB
            if query_bytes > max_bypass_size:
                raise AnalyzeInputError(
                    f"Query size ({query_bytes:,} bytes) exceeds maximum allowed size (10KB).\n\n"
                    "Even with --large-query-bypass, queries cannot exceed 10KB due to LLM context limits."
                )
            # Warn if query is very large (> 5KB)
            if query_bytes > 5 * 1024:
                print(f"Note: Large query ({query_bytes:,} bytes). Analysis may use significant LLM tokens.")
        else:
            # Default 1KB limit
            if query_bytes > MAX_QUERY_LENGTH:
                raise AnalyzeInputError(
                    f"Query size ({query_bytes:,} bytes) exceeds the default limit (1KB).\n\n"
                    "To analyze large queries, use --large-query-bypass:\n"
                    "  rdst analyze --large-query-bypass -f your_file.sql\n"
                    "  rdst analyze --large-query-bypass -q '<your query>'\n\n"
                    "This allows queries up to 10KB."
                )

    def _resolve_inline_query(self, query: str, save_as: str, bypass: bool = False) -> AnalyzeInput:
        """Resolve inline query string."""
        if not query or not query.strip():
            raise AnalyzeInputError("Empty query provided")

        query = query.strip()
        self._enforce_query_size_limit(query, bypass)

        # Normalize and hash
        normalized_sql = normalize_sql(query)
        query_hash = hash_sql(query)

        return AnalyzeInput(
            sql=query,  # Original SQL for EXPLAIN ANALYZE
            normalized_sql=normalized_sql,  # Normalized SQL for registry/LLM
            source="inline",
            hash=query_hash,
            save_as=save_as
        )

    def _resolve_file_input(self, file_path: str, save_as: str, bypass: bool = False) -> AnalyzeInput:
        """Resolve query from file input."""
        path = Path(file_path)

        if not path.exists():
            raise AnalyzeInputError(f"File not found: {file_path}")

        if not path.is_file():
            raise AnalyzeInputError(f"Path is not a file: {file_path}")

        try:
            # Read file with UTF-8 encoding, handling BOM
            content = path.read_text(encoding='utf-8-sig')
        except Exception as e:
            raise AnalyzeInputError(f"Could not read file {file_path}: {e}")

        if not content.strip():
            raise AnalyzeInputError(f"File is empty: {file_path}")

        # Handle multi-statement files - take the first non-empty statement
        content = content.strip()

        # Split by semicolon and take first statement
        statements = [stmt.strip() for stmt in content.split(';') if stmt.strip()]
        if not statements:
            raise AnalyzeInputError(f"No valid SQL statements found in file: {file_path}")

        if len(statements) > 1:
            print(f"Warning: File contains {len(statements)} statements, analyzing the first one")

        query = statements[0].strip()
        self._enforce_query_size_limit(query, bypass)

        normalized_sql = normalize_sql(query)
        query_hash = hash_sql(query)

        return AnalyzeInput(
            sql=query,  # Original SQL
            normalized_sql=normalized_sql,
            source="file",
            hash=query_hash,
            save_as=save_as
        )

    def _resolve_stdin_input(self, save_as: str, bypass: bool = False) -> AnalyzeInput:
        """Resolve query from stdin input."""
        if not sys.stdin.isatty():
            # Reading from pipe
            try:
                content = sys.stdin.read()
            except Exception as e:
                raise AnalyzeInputError(f"Could not read from stdin: {e}")
        else:
            raise AnalyzeInputError("No input provided via stdin. Use pipe or redirect input.")

        if not content.strip():
            raise AnalyzeInputError("Empty input received from stdin")

        content = content.strip()
        self._enforce_query_size_limit(content, bypass)

        normalized_sql = normalize_sql(content)
        query_hash = hash_sql(content)

        return AnalyzeInput(
            sql=content,  # Original SQL
            normalized_sql=normalized_sql,
            source="stdin",
            hash=query_hash,
            save_as=save_as
        )

    def _resolve_interactive_prompt(self, save_as: str) -> AnalyzeInput:
        """Resolve query from interactive user prompt or registry browser."""
        if not sys.stdin.isatty():
            raise AnalyzeInputError("No query provided and stdin is not interactive")

        # First, check if there are saved queries to browse
        saved_queries = self.registry.list_queries(limit=100)  # Get up to 100 recent queries

        if saved_queries:
            # Offer to browse saved queries or enter new one
            try:
                if _RICH_AVAILABLE and self._console:
                    from rich.prompt import Confirm
                    browse_saved = Confirm.ask(
                        f"📚 Found {len(saved_queries)} saved queries. Browse them instead of entering new query?",
                        default=True
                    )
                else:
                    choice = input(f"Found {len(saved_queries)} saved queries. Browse them? (Y/n): ").strip()
                    browse_saved = choice.lower() in ['', 'y', 'yes']

                if browse_saved:
                    return self._browse_saved_queries(save_as)

            except (KeyboardInterrupt, EOFError):
                raise AnalyzeInputError("Query selection cancelled by user")

        # Fall back to manual query input with multiline support
        try:
            if _RICH_AVAILABLE and self._console:
                self._console.print(Panel(
                    "Paste your SQL query below (multiline supported).\n"
                    "End with a semicolon (;) and press Enter, or press Enter twice on a blank line.\n"
                    "Press Ctrl+C to cancel.",
                    title="SQL Query Input",
                    border_style="cyan"
                ))
            else:
                print("Enter your SQL query (multiline supported):")
                print("End with ; and Enter, or Enter twice on blank line. Ctrl+C to cancel.")

            # Collect multiline input
            lines = []
            while True:
                try:
                    line = input("> " if not lines else "  ")
                except EOFError:
                    break

                # Check for termination conditions
                if not line.strip():
                    # Empty line - if we have content, we're done
                    if lines:
                        break
                    # Otherwise, continue waiting for input
                    continue

                lines.append(line)

                # If line ends with semicolon, we're done
                if line.rstrip().endswith(';'):
                    break

            query = '\n'.join(lines)

        except KeyboardInterrupt:
            raise AnalyzeInputError("Query input cancelled by user")

        if not query or not query.strip():
            raise AnalyzeInputError("Empty query provided")

        normalized_sql = normalize_sql(query)
        query_hash = hash_sql(query)

        return AnalyzeInput(
            sql=query.strip(),  # Original SQL
            normalized_sql=normalized_sql,
            source="prompt",
            hash=query_hash,
            save_as=save_as
        )

    def _looks_like_hash(self, text: str) -> bool:
        import re
        return bool(re.match(r'^[0-9a-f]{12}$', text.lower()))

    def _browse_saved_queries(self, save_as: str) -> AnalyzeInput:
        """Browse and select from saved queries."""
        saved_queries = self.registry.list_queries(limit=50)  # Show up to 50 queries

        if not saved_queries:
            raise AnalyzeInputError("No saved queries found")

        try:
            if _RICH_AVAILABLE and self._console:
                from rich.table import Table
                from rich.prompt import Prompt

                # Display queries with numbers for selection
                table = Table(show_header=True, header_style="bold cyan")
                table.add_column("#", style="bold yellow", width=3, justify="right")
                table.add_column("HASH", style="bright_blue", width=12)
                table.add_column("TAG", style="green", width=12)
                table.add_column("SQL", style="white", width=22)
                table.add_column("SOURCE", style="magenta", width=8)
                table.add_column("LAST TARGET", style="red", width=10)
                table.add_column("FIRST", style="cyan", width=6)
                table.add_column("LAST", style="yellow", width=6)

                for i, query in enumerate(saved_queries, 1):
                    # Format timestamps
                    import datetime
                    try:
                        dt = datetime.datetime.fromisoformat(query.last_analyzed.replace('Z', '+00:00'))
                        last_analyzed = dt.strftime("%m-%d")
                    except:
                        last_analyzed = "Unknown"

                    try:
                        dt = datetime.datetime.fromisoformat(query.first_analyzed.replace('Z', '+00:00'))
                        first_analyzed = dt.strftime("%m-%d")
                    except:
                        first_analyzed = "Unknown"

                    sql_display = query.sql[:22] + ("..." if len(query.sql) > 22 else "")
                    tag_display = query.tag if query.tag else "-"
                    target_display = getattr(query, 'last_target', '') or "-"

                    table.add_row(
                        str(i),
                        query.hash,
                        tag_display,
                        sql_display,
                        query.source,
                        target_display,
                        first_analyzed,
                        last_analyzed
                    )

                table.title = f"Select Query to Analyze ({len(saved_queries)} queries)"
                self._console.print(table)

                # Get user selection
                while True:
                    choice = Prompt.ask(
                        f"\n[bold cyan]Select query to analyze[/bold cyan] ([bold yellow]1-{len(saved_queries)}[/bold yellow], [bold red]q[/bold red] to quit)"
                    )

                    if choice.lower() in ['q', 'quit', 'exit']:
                        raise AnalyzeInputError("Query selection cancelled by user")

                    try:
                        idx = int(choice) - 1
                        if 0 <= idx < len(saved_queries):
                            selected_query = saved_queries[idx]
                            # Get executable SQL with parameter values reconstructed
                            executable_sql = self.registry.get_executable_query(selected_query.hash, interactive=False)
                            if not executable_sql:
                                executable_sql = selected_query.sql  # Fallback to normalized
                            return AnalyzeInput(
                                sql=executable_sql,
                                normalized_sql=selected_query.sql,
                                source="registry",
                                hash=selected_query.hash,
                                tag=selected_query.tag,
                                save_as=save_as
                            )
                        else:
                            self._console.print(f"[red]Invalid selection. Please enter 1-{len(saved_queries)} or 'q'[/red]")
                    except ValueError:
                        self._console.print("[red]Invalid input. Please enter a number or 'q'[/red]")

            else:
                # Plain text fallback
                print(f"\nSelect Query to Analyze ({len(saved_queries)} queries):")
                print("-" * 105)
                print(f"{'#':<3} | {'HASH':<12} | {'TAG':<12} | {'SQL':<18} | {'SOURCE':<8} | {'LAST TARGET':<11} | {'FIRST':<6} | {'LAST':<6}")
                print("-" * 105)

                for i, query in enumerate(saved_queries, 1):
                    # Format timestamps
                    import datetime
                    try:
                        dt = datetime.datetime.fromisoformat(query.last_analyzed.replace('Z', '+00:00'))
                        last_analyzed = dt.strftime("%m-%d")
                    except:
                        last_analyzed = "Unknown"

                    try:
                        dt = datetime.datetime.fromisoformat(query.first_analyzed.replace('Z', '+00:00'))
                        first_analyzed = dt.strftime("%m-%d")
                    except:
                        first_analyzed = "Unknown"

                    sql_display = query.sql[:18] + ("..." if len(query.sql) > 18 else "")
                    tag_display = query.tag if query.tag else "-"
                    target_display = getattr(query, 'last_target', '') or "-"
                    print(f"{i:<3} | {query.hash:<12} | {tag_display:<12} | {sql_display:<18} | {query.source:<8} | {target_display:<11} | {first_analyzed:<6} | {last_analyzed:<6}")

                while True:
                    choice = input(f"\nSelect query (1-{len(saved_queries)}, 'q' to quit): ").strip()

                    if choice.lower() in ['q', 'quit', 'exit']:
                        raise AnalyzeInputError("Query selection cancelled by user")

                    try:
                        idx = int(choice) - 1
                        if 0 <= idx < len(saved_queries):
                            selected_query = saved_queries[idx]
                            # Get executable SQL with parameter values reconstructed
                            executable_sql = self.registry.get_executable_query(selected_query.hash, interactive=False)
                            if not executable_sql:
                                executable_sql = selected_query.sql  # Fallback to normalized
                            return AnalyzeInput(
                                sql=executable_sql,
                                normalized_sql=selected_query.sql,
                                source="registry",
                                hash=selected_query.hash,
                                tag=selected_query.tag,
                                save_as=save_as
                            )
                        else:
                            print(f"Invalid selection. Please enter 1-{len(saved_queries)} or 'q'")
                    except ValueError:
                        print("Invalid input. Please enter a number or 'q'")

        except (KeyboardInterrupt, EOFError):
            raise AnalyzeInputError("Query selection cancelled by user")

    def detect_sql_dialect(self, sql: str) -> str:
        """
        Detect SQL dialect from query text using heuristics.

        Args:
            sql: SQL query text

        Returns:
            "postgresql", "mysql", or "unknown"
        """
        sql_lower = sql.lower()

        # PostgreSQL-specific indicators
        pg_indicators = [
            'limit', 'offset', '::',  # Cast syntax
            'ilike', 'similar to', 'regexp_matches',
            'array[', 'jsonb', 'uuid',
            'generate_series', 'extract(', 'interval'
        ]

        # MySQL-specific indicators
        mysql_indicators = [
            'limit', '`',  # Backtick identifiers
            'auto_increment', 'engine=', 'charset=',
            'ifnull(', 'concat(', 'date_format(',
            'unix_timestamp', 'from_unixtime'
        ]

        pg_score = sum(1 for indicator in pg_indicators if indicator in sql_lower)
        mysql_score = sum(1 for indicator in mysql_indicators if indicator in sql_lower)

        if pg_score > mysql_score:
            return "postgresql"
        elif mysql_score > pg_score:
            return "mysql"
        else:
            return "unknown"

    def execute_analyze(self, resolved_input: AnalyzeInput, target: Optional[str] = None, readyset: bool = False, readyset_cache: bool = False, fast: bool = False, interactive: bool = False, review: bool = False) -> RdstResult:
        """
        Execute the analyze command with resolved input using the workflow engine.

        Args:
            resolved_input: Resolved input from resolve_input()
            target: Target database name
            readyset: Whether to run parallel workflow with ReadySet testing
            readyset_cache: Whether to evaluate ReadySet caching with performance comparison
            fast: Whether to auto-skip slow EXPLAIN ANALYZE queries after 10 seconds
            interactive: Whether to enter interactive mode after analysis
            review: Whether to review conversation history instead of analyzing

        Returns:
            RdstResult with analysis results
        """
        from .rdst_cli import RdstResult, TargetsConfig
        from .interactive_mode import display_conversation_history

        try:
            # Check for API key BEFORE any LLM operations (interactive mode, review, or analysis)
            api_key_error = self._check_api_key_configured()
            if api_key_error:
                from .rdst_cli import RdstResult
                return RdstResult(False, api_key_error)

            # Handle --review flag (show conversation history without analysis)
            if review:
                conv_registry = ConversationRegistry()
                llm_manager = LLMManager()
                provider = llm_manager.defaults.provider

                if conv_registry.conversation_exists(resolved_input.hash, provider):
                    conversation = conv_registry.load_conversation(resolved_input.hash, provider)
                    display_conversation_history(conversation, show_system_messages=False)
                    return RdstResult(True, f"Conversation history for query hash: {resolved_input.hash}")
                else:
                    return RdstResult(False, f"No conversation found for query hash: {resolved_input.hash}")

            # Handle --interactive flag: Check for existing conversation BEFORE running analysis
            if interactive:
                conv_registry = ConversationRegistry()
                llm_manager = LLMManager()
                provider = llm_manager.defaults.provider

                if conv_registry.conversation_exists(resolved_input.hash, provider):
                    print(f"\n{'='*80}")
                    print(f"Found existing conversation for this query (hash: {resolved_input.hash})")
                    print(f"{'='*80}")

                    while True:
                        choice = input("\nContinue existing conversation or start new? [c/n]: ").strip().lower()
                        if choice in ['c', 'continue']:
                            # Load conversation and enter interactive mode directly
                            conversation = conv_registry.load_conversation(resolved_input.hash, provider)
                            print(f"Continuing conversation from {conversation.started_at}")

                            # Get analysis results from registry to pass to interactive mode
                            from ..query_registry.query_registry import QueryRegistry
                            query_registry = QueryRegistry()
                            query_entry = query_registry.get_query(resolved_input.hash)

                            # We need to load the analysis results - for now use empty dict
                            # The conversation already has the context in the system messages
                            from .interactive_mode import run_interactive_mode
                            run_interactive_mode(conversation, {}, llm_manager)

                            return RdstResult(True, "Interactive session completed")
                        elif choice in ['n', 'new']:
                            # Delete old conversation and continue to run analysis
                            conv_registry.delete_conversation(resolved_input.hash, provider)
                            print("Starting fresh conversation...")
                            break
                        else:
                            print("Please enter 'c' for continue or 'n' for new")

            # Check for unresolved parameter placeholders
            if has_unresolved_placeholders(resolved_input.sql):
                # First, check if we have stored parameters for this query
                from ..query_registry.query_registry import reconstruct_query_with_params

                existing_entry = self.registry.get_query(resolved_input.hash)
                stored_params = existing_entry.most_recent_params if existing_entry else None

                if stored_params:
                    # We have stored parameters - use them automatically
                    substituted_sql = reconstruct_query_with_params(
                        resolved_input.normalized_sql or resolved_input.sql,
                        stored_params
                    )
                    print(f"\nUsing stored parameters for query {resolved_input.hash}:")
                    print(f"  {substituted_sql[:150]}{'...' if len(substituted_sql) > 150 else ''}")
                    print()

                    resolved_input = AnalyzeInput(
                        sql=substituted_sql,
                        normalized_sql=resolved_input.normalized_sql,
                        source=resolved_input.source,
                        hash=resolved_input.hash,
                        tag=resolved_input.tag,
                        save_as=resolved_input.save_as
                    )
                else:
                    # No stored parameters - prompt the user
                    result = prompt_for_parameters(resolved_input.sql)

                    if result is None:
                        return RdstResult(False, "Analysis cancelled - parameter values required")

                    substituted_sql, param_dict = result

                    # Update resolved_input with substituted SQL
                    resolved_input = AnalyzeInput(
                        sql=substituted_sql,
                        normalized_sql=resolved_input.normalized_sql,
                        source=resolved_input.source,
                        hash=resolved_input.hash,
                        tag=resolved_input.tag,
                        save_as=resolved_input.save_as
                    )

                    # Store these parameters in the registry for future use
                    try:
                        self.registry.update_parameter_history(
                            query_hash=resolved_input.hash,
                            parameters=param_dict
                        )
                    except Exception:
                        # Non-fatal - continue with analysis even if storage fails
                        pass

                    print()

            # Load target configuration
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

            readyset_analysis_result = None
            cache_performance_result = None

            # If cache is enabled, we need readyset containers too
            if readyset_cache:
                readyset = True

            if readyset:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    analyze_future = executor.submit(
                        self._run_analyze_workflow,
                        resolved_input,
                        target_name,
                        target_config,
                        save_as=resolved_input.save_as,
                        source=resolved_input.source,
                        fast=fast
                    )
                    readyset_future = executor.submit(
                        self._run_readyset_analysis,
                        resolved_input,
                        target_name=target_name,
                        target_config=target_config
                    )

                    workflow_result = analyze_future.result()
                    try:
                        readyset_analysis_result = readyset_future.result()
                    except Exception as exc:  # pragma: no cover - defensive
                        readyset_analysis_result = {
                            "success": False,
                            "error": f"ReadySet analysis failed: {exc}"
                        }
            else:
                workflow_result = self._run_analyze_workflow(
                    resolved_input=resolved_input,
                    target=target_name,
                    target_config=target_config,
                    save_as=resolved_input.save_as,
                    source=resolved_input.source,
                    fast=fast
                )

            if readyset and readyset_analysis_result:
                if workflow_result.get("success"):
                    readyset_analysis_result.setdefault(
                        "static_cacheability",
                        workflow_result["result"].get("CheckReadySetCacheability", {})
                    )
                    context = workflow_result["result"]
                    context["readyset_analysis"] = readyset_analysis_result

                    formatted_output = context.get("FormatFinalResults")
                    if isinstance(formatted_output, dict):
                        formatted_output["readyset_analysis"] = readyset_analysis_result

                        if readyset_analysis_result.get("success"):
                            final_verdict = readyset_analysis_result.get("final_verdict", {})
                            explain_result = readyset_analysis_result.get("explain_cache_result", {})

                            readyset_summary = formatted_output.get("readyset_cacheability", {}) or {}
                            readyset_summary.update({
                                "checked": True,
                                "method": final_verdict.get("method", readyset_analysis_result.get("method", "readyset_explain_cache")),
                                "cacheable": final_verdict.get("cacheable", False),
                                "confidence": final_verdict.get("confidence", "unknown"),
                                "explanation": explain_result.get("explanation") or readyset_analysis_result.get("static_cacheability", {}).get("explanation"),
                                "issues": explain_result.get("issues"),
                            })
                            formatted_output["readyset_cacheability"] = readyset_summary

                            # Preserve explain result for downstream consumers
                            formatted_output["readyset_explain_cache"] = explain_result

                        context["FormatFinalResults"] = formatted_output
                else:
                    workflow_result["readyset_analysis"] = readyset_analysis_result

            # Run cache performance comparison if --readyset-cache flag is set
            if readyset_cache and readyset_analysis_result and readyset_analysis_result.get("success"):
                cache_performance_result = self._run_cache_performance_comparison(
                    resolved_input=resolved_input,
                    target_name=target_name,
                    target_config=target_config,
                    readyset_analysis_result=readyset_analysis_result
                )

                # Add cache results to workflow result
                if workflow_result.get("success"):
                    workflow_result["result"]["cache_performance"] = cache_performance_result
                else:
                    workflow_result["cache_performance"] = cache_performance_result

            if workflow_result["success"]:
                # Clear all the workflow progress output before showing final result
                import sys
                if sys.stdout.isatty():
                    # Clear screen and move cursor to top
                    print("\033[2J\033[H", end='', flush=True)

                # Format the results for user display using new clean formatter
                from .output_formatter import format_analyze_output
                # Include target_config for copy-paste test commands (uses env var name, not actual password)
                workflow_result["result"]["target_config"] = target_config
                formatted_results = format_analyze_output(workflow_result["result"])

                # Append cache performance results if available
                if readyset_cache and cache_performance_result:
                    from ..functions.performance_comparison import format_performance_comparison
                    formatted_results += "\n\n" + "="*70 + "\n"
                    formatted_results += "ReadySet Cache Performance Analysis\n"
                    formatted_results += "="*70 + "\n\n"

                    if cache_performance_result.get("success"):
                        perf_comparison = cache_performance_result.get("performance_comparison", {})
                        if perf_comparison:
                            formatted_results += format_performance_comparison(perf_comparison)

                        # Add deployment instructions
                        deployment_instructions = cache_performance_result.get("deployment_instructions", "")
                        if deployment_instructions:
                            formatted_results += "\n" + deployment_instructions
                    else:
                        error = cache_performance_result.get("error", "Unknown error")
                        formatted_results += f"\nCache performance comparison failed: {error}\n"

                # Handle --interactive flag (enter interactive mode after analysis)
                # IMPORTANT: Only enter interactive mode if explain_results succeeded
                # Without successful EXPLAIN, there's no analysis to discuss
                if interactive:
                    # Print results before entering interactive mode
                    print(formatted_results)

                    explain_results = workflow_result["result"].get("explain_results", {})
                    if explain_results and explain_results.get("success"):
                        self._handle_interactive_mode(
                            resolved_input=resolved_input,
                            target_name=target_name,
                            analysis_results=workflow_result["result"]
                        )
                    else:
                        print("\n" + "="*80)
                        print("Cannot enter interactive mode: Query analysis failed")
                        print("="*80)
                        error_msg = explain_results.get("error", "Unknown error")
                        print(f"\nError: {error_msg}")
                        print("\nPlease fix the query and try again.")
                        print()

                    # Already printed everything - return empty to avoid duplicate output
                    return RdstResult(True, "")

                # Print formatted results first
                print(formatted_results)

                # Add breadcrumb for next steps (non-interactive mode) with colors
                # Colors: rdst=white, subcommand=green, values/quoted=blue, descriptions=dim
                if _RICH_AVAILABLE and self._console:
                    self._console.print()
                    self._console.print("[cyan]Next Steps:[/cyan]")
                    if not readyset_cache:
                        self._console.print(f"  rdst [green]analyze[/green] --hash [blue]{resolved_input.hash[:8]}[/blue] --readyset-cache   [dim]Test ReadySet caching[/dim]")
                    self._console.print(f"  rdst [green]analyze[/green] --hash [blue]{resolved_input.hash[:8]}[/blue] --interactive      [dim]Ask follow-up questions[/dim]")
                    self._console.print(f"  rdst [green]query show[/green] [blue]{resolved_input.hash[:8]}[/blue]                        [dim]View saved query details[/dim]")
                    self._console.print()
                else:
                    print("\n" + "─" * 50)
                    print("Next Steps:")
                    if not readyset_cache:
                        print(f"  rdst analyze --hash {resolved_input.hash[:8]} --readyset-cache   Test ReadySet caching")
                    print(f"  rdst analyze --hash {resolved_input.hash[:8]} --interactive      Ask follow-up questions")
                    print(f"  rdst query show {resolved_input.hash[:8]}                        View saved query details")
                    print()

                return RdstResult(True, "")  # Already printed
            else:
                return RdstResult(False, workflow_result["error"])

        except Exception as e:
            return RdstResult(False, f"analyze failed: {e}")

    def _handle_interactive_mode(self, resolved_input: AnalyzeInput, target_name: str, analysis_results: dict) -> None:
        """
        Handle interactive mode flow after analysis completes: create new conversation and enter REPL.

        Note: The check for existing conversation now happens BEFORE analysis in execute_analyze()

        Args:
            resolved_input: Resolved input with query hash
            target_name: Target database name
            analysis_results: Full analysis results from workflow
        """
        from .interactive_mode import run_interactive_mode
        from datetime import datetime, timezone

        conv_registry = ConversationRegistry()
        llm_manager = LLMManager()
        provider = llm_manager.defaults.provider
        model = llm_manager.defaults.model

        query_hash = resolved_input.hash

        # Create new conversation (we've already checked/deleted old one in execute_analyze)
        conversation = conv_registry.create_conversation(
            query_hash=query_hash,
            provider=provider,
            model=model,
            analysis_id=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            target=target_name,
            query_sql=resolved_input.sql
        )

        # Build comprehensive analysis context for system message
        system_context = self._build_analysis_context(analysis_results, resolved_input.sql)
        conversation.add_message("system", system_context)

        # Save initial conversation
        conv_registry.save_conversation(conversation)

        # Enter interactive REPL
        run_interactive_mode(conversation, analysis_results, llm_manager)

    def _build_analysis_context(self, analysis_results: dict, query_sql: str) -> str:
        """
        Build comprehensive analysis context for the initial system message.

        Args:
            analysis_results: Full analysis results from workflow
            query_sql: Original SQL query

        Returns:
            Formatted context string for system message
        """
        import json

        # Extract key components from workflow context
        explain_results = analysis_results.get("explain_results", {})
        llm_analysis = analysis_results.get("llm_analysis", {})
        schema_collection = analysis_results.get("schema_collection", {})
        schema_info = schema_collection.get("schema_info", {}) if schema_collection else {}
        query_metrics = analysis_results.get("query_metrics", {})

        # Build context string
        context_parts = []

        context_parts.append("# QUERY ANALYSIS RESULTS")
        context_parts.append("\n## Original Query")
        context_parts.append(f"```sql\n{query_sql}\n```")

        context_parts.append("\n## Performance Metrics")
        if explain_results:
            exec_time = explain_results.get("execution_time_ms", 0)
            rows_examined = explain_results.get("rows_examined", 0)
            rows_returned = explain_results.get("rows_returned", 0)
            context_parts.append(f"- Execution Time: {exec_time:.2f}ms")
            context_parts.append(f"- Rows Examined: {rows_examined:,}")
            context_parts.append(f"- Rows Returned: {rows_returned:,}")

        context_parts.append("\n## EXPLAIN ANALYZE Output")
        if explain_results and "raw_explain" in explain_results:
            context_parts.append(f"```\n{explain_results['raw_explain']}\n```")

        context_parts.append("\n## Database Schema")
        if schema_info:
            context_parts.append(f"```json\n{json.dumps(schema_info, indent=2)}\n```")

        context_parts.append("\n## AI Analysis & Recommendations")
        if llm_analysis:
            # Index recommendations
            index_recs = llm_analysis.get("index_recommendations", [])
            if index_recs:
                context_parts.append("\n### Index Recommendations")
                for i, rec in enumerate(index_recs, 1):
                    context_parts.append(f"\n{i}. **{rec.get('table', 'N/A')}.{rec.get('columns', [])}**")
                    context_parts.append(f"   - Rationale: {rec.get('rationale', 'N/A')}")
                    context_parts.append(f"   - SQL: `{rec.get('sql', 'N/A')}`")

            # Query rewrite suggestions
            rewrite_sugs = llm_analysis.get("rewrite_suggestions", [])
            if rewrite_sugs:
                context_parts.append("\n### Query Rewrite Suggestions")
                for i, sug in enumerate(rewrite_sugs, 1):
                    context_parts.append(f"\n{i}. **{sug.get('type', 'N/A')}**")
                    context_parts.append(f"   - Description: {sug.get('description', 'N/A')}")
                    if sug.get('rewritten_query'):
                        context_parts.append(f"   - Rewritten Query: ```sql\n{sug['rewritten_query']}\n```")

            # Hotspots and issues
            hotspots = llm_analysis.get("hotspots", {})
            if hotspots:
                context_parts.append("\n### Performance Hotspots")
                context_parts.append(f"```json\n{json.dumps(hotspots, indent=2)}\n```")

        return "\n".join(context_parts)

    def _run_readyset_analysis(
        self,
        resolved_input: AnalyzeInput,
        target_name: Optional[str] = None,
        target_config: Optional[dict] = None,
        analyze_workflow_output: Optional[dict] = None
    ) -> dict:
        """
        Run ReadySet container setup and cacheability testing.

        Args:
            resolved_input: Resolved input with query info
            target_name: Name of the target database to mirror
            target_config: Resolved target configuration
            analyze_workflow_output: Results from the regular analysis workflow (optional)

        Returns:
            Dict containing ReadySet analysis results
        """
        try:
            from .rdst_cli import TargetsConfig
            from .readyset_setup import setup_readyset_containers

            print("\n🔧 Setting up ReadySet container for cacheability testing...")

            # Load target configuration
            cfg = TargetsConfig()
            cfg.load()
            effective_target_name = target_name or cfg.get_default()
            if not target_config and effective_target_name:
                target_config = cfg.get(effective_target_name)

            if not target_config or not effective_target_name:
                return {
                    "success": False,
                    "error": f"Target '{effective_target_name or ''}' not found"
                }
            target_name = effective_target_name

            # Use shared setup function
            print("  -> Setting up test database and ReadySet containers...")
            setup_result_wrapper = setup_readyset_containers(
                target_name=target_name,
                target_config=target_config,
                test_data_rows=100,
                llm_model=None  # Use provider's default model
            )

            if not setup_result_wrapper.get("success"):
                return {
                    "success": False,
                    "error": setup_result_wrapper.get("error", "Setup failed")
                }

            # Extract values from setup result
            # When containers are already running, values are at top level
            # When creating new containers, they're nested under 'setup_result'
            readyset_port = setup_result_wrapper.get("readyset_port")

            if not readyset_port:
                return {
                    "success": False,
                    "error": f"ReadySet port not found in setup result. Available keys: {list(setup_result_wrapper.keys())}"
                }

            # For target_config, check both top level and nested
            if "setup_result" in setup_result_wrapper:
                # New containers case - nested structure
                setup_result = setup_result_wrapper["setup_result"]
            else:
                # Already running case - use the wrapper itself as setup_result
                setup_result = setup_result_wrapper

            # Get test_db_config and ensure password is set
            test_db_config = setup_result.get("target_config", {})

            # Resolve password from environment if needed
            import os
            password = target_config.get('password', '')
            password_env = target_config.get('password_env')
            if password_env:
                password = os.environ.get(password_env, '')

            # Ensure password is in test_db_config
            if not test_db_config.get('password'):
                test_db_config['password'] = password

            # Now run EXPLAIN CREATE CACHE against ReadySet
            print("  -> Running EXPLAIN CREATE CACHE on ReadySet...")
            from ..functions.readyset_explain_cache import explain_create_cache_readyset, create_cache_readyset

            explain_result = explain_create_cache_readyset(
                query=resolved_input.sql,
                readyset_port=readyset_port,
                test_db_config=test_db_config
            )

            print("  DONE: ReadySet cacheability analysis complete")

            # If the query is cacheable, try to create the cache (unless already cached)
            create_result = {}
            already_cached = "already cached" in explain_result.get("explanation", "").lower()
            explanation = explain_result.get("explanation", "")
            if explain_result.get("cacheable", False):
                if already_cached:
                    print(f"  ✓ {explanation}")
                    create_result = {
                        "success": True,
                        "cached": True,
                        "already_cached": True,
                        "message": "Query already cached"
                    }

                    # Get cache ID for existing cache
                    try:
                        cache_id = self._get_cache_id_for_query(
                            resolved_input.sql,
                            readyset_port,
                            test_db_config
                        )
                        if cache_id:
                            print(f"     Cache ID: {cache_id}")
                            create_result['cache_id'] = cache_id
                    except Exception as e:
                        print(f"     (Could not retrieve cache ID: {e})")
                else:
                    print("  -> Query is cacheable, creating cache...")
                    print(f"     Query to cache: {resolved_input.sql[:100]}{'...' if len(resolved_input.sql) > 100 else ''}")
                    create_result = create_cache_readyset(
                        query=resolved_input.sql,
                        readyset_port=readyset_port,
                        test_db_config=test_db_config
                    )
                    if create_result.get("cached"):
                        print("  ✓ Cache created successfully")

                        # Get cache ID by querying SHOW CACHES
                        try:
                            cache_id = self._get_cache_id_for_query(
                                resolved_input.sql,
                                readyset_port,
                                test_db_config
                            )
                            if cache_id:
                                print(f"     Cache ID: {cache_id}")
                                create_result['cache_id'] = cache_id
                        except Exception as e:
                            print(f"     (Could not retrieve cache ID: {e})")
                    else:
                        print(f"  ⚠ Cache creation failed: {create_result.get('error', 'Unknown error')}")
                        print(f"     Details: {create_result}")
            else:
                print("  ℹ️  EXPLAIN CREATE CACHE: Query may not be cacheable")
                explanation = explain_result.get('explanation', 'Unknown')
                if explanation and explanation != 'Unknown':
                    print(f"     Reason: {explanation}")
                print("     Note: Will attempt to create cache anyway (EXPLAIN can be conservative)")

            # Merge static cacheability check with actual ReadySet result
            static_cacheability = {}
            if analyze_workflow_output:
                static_cacheability = analyze_workflow_output.get("CheckReadySetCacheability", {})

            return {
                "success": True,
                "readyset_port": readyset_port,
                "setup_result": setup_result,
                "explain_cache_result": explain_result,
                "create_cache_result": create_result,
                "static_cacheability": static_cacheability,
                "readyset_container": setup_result.get("readyset_container", {}),
                "test_db_container": setup_result.get("container_start", {}),
                "final_verdict": {
                    "cacheable": explain_result.get("cacheable", False),
                    "confidence": explain_result.get("confidence", "unknown"),
                    "method": "readyset_container" if explain_result.get("success") else "static_analysis",
                    "cached": create_result.get("cached", False)
                }
            }

        except Exception as e:
            print(f"  ERROR: ReadySet analysis failed: {str(e)}")
            return {
                "success": False,
                "error": f"ReadySet analysis failed: {str(e)}"
            }

    def _check_api_key_configured(self) -> Optional[str]:
        """Check if an API key is configured for Anthropic (Claude).

        RDST officially uses Claude/Anthropic for AI analysis.

        Returns:
            Error message if no API key configured, None if OK
        """
        try:
            # RDST uses Anthropic/Claude - check for that key
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                return (
                    "No LLM API key configured.\n\n"
                    "Please provide your Anthropic API key to enable query analysis.\n"
                    "You can get one at: https://console.anthropic.com/"
                )

            return None  # Key is configured

        except Exception as e:
            return f"Configuration error: {e}"

    def _run_analyze_workflow(self, resolved_input: AnalyzeInput, target: str, target_config: dict,
                             save_as: str = "", source: str = "manual", fast: bool = False) -> dict:
        """Run the complete analyze workflow using WorkflowManager."""
        try:
            from ..workflow_manager.workflow_manager import WorkflowManager, DEFAULT_FUNCTIONS
            from ..functions import ANALYZE_WORKFLOW_FUNCTIONS
            from pathlib import Path

            # Set up workflow manager with analyze functions
            workflow_functions = {
                **DEFAULT_FUNCTIONS,  # Built-in workflow functions
                **ANALYZE_WORKFLOW_FUNCTIONS,  # Our analyze functions
            }

            # Load workflow definition - always use simple workflow
            workflow_path = Path(__file__).parent.parent / "workflows" / "analyze_workflow_simple.json"

            if not workflow_path.exists():
                return {
                    "success": False,
                    "error": f"Workflow file not found: {workflow_path}"
                }

            mgr = WorkflowManager.from_file(str(workflow_path), resources=workflow_functions)

            # Prepare initial workflow input
            initial_input = {
                "query": resolved_input.sql,  # Original SQL for EXPLAIN ANALYZE
                "normalized_query": resolved_input.normalized_sql,  # Normalized SQL for registry/LLM
                "target": target,
                "target_config": target_config,
                "test_rewrites": True,  # Enable rewrite testing by default
                "llm_model": None,  # Use provider's default model
                "save_as": save_as,
                "source": source,
                "fast_mode": fast  # Auto-skip slow queries after 10 seconds
            }

            # Execute workflow with detailed progress tracking
            result = self._run_workflow_with_progress(mgr, initial_input)

            return {"success": True, "result": result}

        except Exception as e:
            print(f"  ERROR: Workflow failed: {str(e)}")
            return {
                "success": False,
                "error": f"Workflow execution failed: {str(e)}"
            }

    def _run_workflow_with_progress(self, mgr, initial_input):
        """Run workflow with detailed step-by-step progress indicators and heartbeat."""
        import time
        import threading

        # Get LLM info for display
        try:
            from .rdst_cli import TargetsConfig
            config = TargetsConfig()
            config.load()
            model = config.get_llm_model() or "sonnet-4.5"
            llm_display = f"Analysis via {model}"
        except:
            llm_display = "Analysis"

        # Step mapping for user-friendly names
        step_names = {
            "ValidateQuerySafety": "Validating query safety",
            "NormalizeForRegistry": "Normalizing query for registry",
            "ParameterizeForLLM": "Parameterizing query for AI analysis",
            "ExecuteExplainAnalyze": "Executing EXPLAIN ANALYZE on database",
            "CollectQueryMetrics": "Collecting additional database metrics",
            "CollectDatabaseSchema": "Collecting database schema",
            "PerformLLMAnalysis": f"{llm_display} (may take 30-60s)",
            "ExtractOptimizationSuggestions": "Extracting optimization suggestions",
            "TestQueryRewrites": "Testing suggested query rewrites",
            "StoreAnalysisResults": "Storing results in registry",
            "FormatFinalResults": "Formatting final results"
        }

        # Track execution state
        execution_state = {
            "current_step": None,
            "current_step_raw": None,  # Raw step name for token display logic
            "step_start_time": None,
            "heartbeat_active": False,
            "completed_steps": set(),
            "estimated_tokens": None,  # Estimated input tokens for LLM step
            "token_usage": None,  # Final token usage after LLM completes
        }

        def heartbeat_thread():
            """Show heartbeat dots with live timer while steps are running."""
            heartbeat_chars = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
            i = 0
            last_line_length = 0
            while execution_state["heartbeat_active"]:
                if execution_state["step_start_time"] and (time.time() - execution_state["step_start_time"]) > 0.5:
                    current_step = execution_state.get('current_step', 'Processing')
                    raw_step = execution_state.get('current_step_raw', '')
                    elapsed_time = time.time() - execution_state["step_start_time"]

                    # Build time display
                    if elapsed_time >= 1.0:
                        time_str = f"{elapsed_time:.0f}s"
                    else:
                        time_str = f"{elapsed_time:.1f}s"

                    # For LLM step, show input tokens in the spinner line
                    extra_info = ""
                    if raw_step == "PerformLLMAnalysis":
                        est_tokens = execution_state.get('estimated_tokens')
                        if est_tokens:
                            extra_info = f" | ~{est_tokens:,} tokens"

                    line = f"  {heartbeat_chars[i % len(heartbeat_chars)]} {current_step}... ({time_str}{extra_info})"

                    # Only clear extra characters from previous line if it was longer
                    padding = max(0, last_line_length - len(line))
                    print(f"\r{line}{' ' * padding}", end='', flush=True)
                    last_line_length = len(line)
                    i += 1
                time.sleep(0.2)  # Update 5 times per second for responsive feedback

        # Start heartbeat thread
        execution_state["heartbeat_active"] = True
        heartbeat = threading.Thread(target=heartbeat_thread, daemon=True)
        heartbeat.start()

        def step_start_callback(step_name, input_data):
            """Called when a workflow step starts."""
            friendly_name = step_names.get(step_name, step_name)
            execution_state["current_step"] = friendly_name
            execution_state["current_step_raw"] = step_name
            execution_state["step_start_time"] = time.time()

            # For LLM step, estimate tokens and include in spinner
            if step_name == "PerformLLMAnalysis":
                try:
                    from ..functions.llm_analysis import estimate_tokens
                    # Estimate tokens from context that will be sent to LLM
                    total_chars = 0
                    for key in ['parameterized_sql', 'original_sql', 'schema_info', 'explain_results']:
                        val = input_data.get(key)
                        if val:
                            total_chars += len(str(val))
                    # Add system prompt overhead (~200 tokens)
                    est_tokens = estimate_tokens(str(total_chars * 'x')) + 200
                    execution_state["estimated_tokens"] = est_tokens
                except Exception:
                    pass

        def step_complete_callback(step_name, result, execution_time):
            """Called when a workflow step completes."""
            friendly_name = step_names.get(step_name, step_name)
            execution_state["completed_steps"].add(step_name)

            # Capture token usage from LLM step
            if step_name == "PerformLLMAnalysis" and result:
                token_usage = result.get("token_usage")
                if token_usage:
                    execution_state["token_usage"] = token_usage

            # Just update execution state - the next step or final cleanup will update the display
            # This keeps everything on one line that continuously updates


        try:
            # Monkey patch the workflow manager to add progress callbacks at the right points
            original_run_workflow_with_retry = mgr._run_workflow_with_retry

            def enhanced_run_workflow_with_retry(execution, initial_input):
                """Enhanced workflow execution with progress tracking."""
                wf = getattr(mgr, "_workflow", None)
                if not wf:
                    raise Exception("No workflow loaded")

                states = wf.get("States") or {}
                current = wf.get("StartAt")
                if not current:
                    raise Exception("Workflow missing StartAt")

                context = execution.context.copy()
                if initial_input:
                    context.update(initial_input)
                context.setdefault("States", {})

                # Initialize step tracking
                for state_name in states.keys():
                    from ..workflow_manager.workflow_manager import StepResult, WorkflowStatus
                    execution.steps[state_name] = StepResult(
                        step_name=state_name,
                        status=WorkflowStatus.PENDING
                    )

                # Execute workflow loop with progress tracking
                while current:
                    execution.current_step = current
                    state_name = current
                    state = states.get(state_name)
                    if not state:
                        raise Exception(f"State '{state_name}' not found")

                    step_result = execution.steps[state_name]
                    from ..workflow_manager.workflow_manager import WorkflowStatus
                    from datetime import datetime

                    step_result.status = WorkflowStatus.RUNNING
                    step_result.started_at = datetime.now()

                    # Show step start
                    step_start_callback(state_name, context)
                    start_time = time.time()

                    try:
                        # Execute the actual step
                        result = mgr._execute_step_with_retry(state, context, state_name)

                        # Store result
                        result_path = state.get("ResultPath") or f"$.{state_name}"
                        mgr._assign_path(context, result_path, result)
                        context["States"][state_name] = result

                        step_result.result = result
                        step_result.status = WorkflowStatus.COMPLETED
                        step_result.completed_at = datetime.now()

                        # Show completion
                        end_time = time.time()
                        step_complete_callback(state_name, result, end_time - start_time)

                    except Exception as e:
                        step_result.error = str(e)
                        step_result.status = WorkflowStatus.FAILED
                        step_result.completed_at = datetime.now()
                        print(f"\r  ERROR: {step_names.get(state_name, state_name)} failed: {str(e)}")
                        raise Exception(f"Step '{state_name}' failed: {e}")

                    # Transition
                    if state.get("End") is True:
                        break
                    current = state.get("Next")
                    if not current:
                        raise Exception(f"State '{state_name}' has no Next and is not End=true")

                return context

            mgr._run_workflow_with_retry = enhanced_run_workflow_with_retry

            # Run the workflow
            result = mgr.run(initial_input)

            # Stop heartbeat and show completion message
            execution_state["heartbeat_active"] = False
            time.sleep(0.1)  # Give heartbeat thread time to stop
            print("\r" + " " * 120, end='\r')  # Clear any remaining heartbeat
            print("  ✓ Analysis complete")  # Final completion message
            print()  # Add blank line before results

            return result

        finally:
            execution_state["heartbeat_active"] = False

    def _format_workflow_results(self, workflow_result: dict, skip_slow_fallback: bool = False) -> str:
        """Format workflow results for user display."""
        try:
            # Get the formatted output from the workflow
            formatted_output = workflow_result.get("FormatFinalResults", {})

            if not formatted_output:
                return "Analysis completed but no formatted results available."

            # Check if formatting failed and use raw data instead
            if not formatted_output.get("success", True):
                # Silently use fallback formatting when workflow formatting fails
                return self._format_raw_workflow_results(workflow_result, skip_slow_fallback)

            # Build user-friendly output
            lines = []
            lines.append("Query Analysis Results")
            lines.append("=" * 50)

            # Analysis Summary
            summary = formatted_output.get("analysis_summary", {})
            if summary:
                lines.append(f"Overall Rating: {summary.get('overall_rating', 'unknown')}")
                lines.append(f"Execution Time: {summary.get('execution_time_ms', 0):.1f}ms")
                lines.append(f"Efficiency Score: {summary.get('efficiency_score', 0)}/100")

                rows_processed = summary.get('rows_processed', {})
                lines.append(f"Rows Examined: {rows_processed.get('examined', 0):,}")
                lines.append(f"Rows Returned: {rows_processed.get('returned', 0):,}")

                concerns = summary.get('primary_concerns', [])
                if concerns:
                    lines.append(f"Primary Concerns: {', '.join(concerns)}")
                lines.append("")

            # Recommendations
            recommendations = formatted_output.get("recommendations", {})
            if recommendations.get("available", False):
                rewrites = recommendations.get("query_rewrites", [])
                if rewrites:
                    lines.append(f"Query Rewrites ({len(rewrites)} suggestions):")
                    for i, rewrite in enumerate(rewrites[:3], 1):
                        lines.append(f"  {i}. {rewrite.get('type', 'Unknown')} ({rewrite.get('priority', 'medium')} priority)")
                        lines.append(f"     {rewrite.get('explanation', 'No explanation')}")
                    lines.append("")

                indexes = recommendations.get("index_suggestions", [])
                if indexes:
                    lines.append(f"Index Suggestions ({len(indexes)} suggestions):")
                    for i, index in enumerate(indexes[:3], 1):
                        columns = ', '.join(index.get('columns', []))
                        lines.append(f"  {i}. {index.get('table', 'unknown')}: {columns}")
                        lines.append(f"     {index.get('rationale', 'No rationale')}")
                    lines.append("")

            # Rewrite Testing Results
            rewrite_testing = formatted_output.get("rewrite_testing", {})
            if rewrite_testing.get("tested", False):
                lines.append("Rewrite Testing Results:")
                lines.append(f"   {rewrite_testing.get('summary', 'No summary')}")
                best = rewrite_testing.get("best_rewrite")
                if best:
                    improvement = best.get("improvement", {}).get("overall", {})
                    improvement_pct = improvement.get("improvement_pct", 0)
                    lines.append(f"   Best rewrite: {improvement_pct:+.1f}% performance change")
                lines.append("")

            # ReadySet Cacheability (check both formatted output and sequential result)
            readyset_analysis = (
                workflow_result.get("readyset_analysis")
                or workflow_result.get("States", {}).get("readyset_analysis")
                or formatted_output.get("readyset_analysis")
                or {}
            )
            readyset_cacheability = formatted_output.get("readyset_cacheability", {})

            # Use sequential ReadySet result if available, otherwise use formatted output
            if readyset_analysis.get("success"):
                lines.append("🚀 ReadySet Cacheability:")
                final_verdict = readyset_analysis.get("final_verdict", {})
                cacheable = final_verdict.get("cacheable", False)
                confidence = final_verdict.get("confidence", "unknown")
                method = final_verdict.get("method", "unknown")
                reason = readyset_analysis.get('explain_cache_result').get("error", "")

                status = "CACHEABLE" if cacheable else "NOT CACHEABLE"
                lines.append(f"   {status} (confidence: {confidence})")
                lines.append(f"   Method: {method}")
                if reason:
                    lines.append(f"Reason: {reason}")

                # Show cache creation status
                cached = final_verdict.get("cached", False)
                create_result = readyset_analysis.get("create_cache_result", {})
                if cacheable and create_result.get("already_cached"):
                    lines.append("   ℹ️  Query already cached in ReadySet")
                elif cacheable and cached:
                    lines.append("   Cache created in ReadySet")
                elif cacheable and create_result:
                    # Cache creation was attempted but failed
                    error = create_result.get("error", "Unknown error")
                    lines.append(f"   ⚠ Cache creation failed: {error}")

                explain_result = readyset_analysis.get("explain_cache_result", {})
                if explain_result.get("explanation"):
                    lines.append(f"   Explanation: {explain_result.get('explanation')}")
                issues = explain_result.get("issues") or []
                if issues:
                    lines.append("   Issues:")
                    for issue in issues:
                        lines.append(f"     • {issue}")
                else:
                    details = explain_result.get("details")
                    if isinstance(details, str) and details.strip():
                        lines.append(f"   Details: {details}")
                lines.append("")
            elif readyset_cacheability.get("checked"):
                lines.append("🚀 ReadySet Cacheability:")
                cacheable = readyset_cacheability.get("cacheable", False)
                confidence = readyset_cacheability.get("confidence", "unknown")

                status = "CACHEABLE" if cacheable else "NOT CACHEABLE"
                lines.append(f"   {status} (confidence: {confidence})")

                if readyset_cacheability.get("explanation"):
                    lines.append(f"   {readyset_cacheability.get('explanation')}")
                lines.append("")

            # Metadata
            metadata = formatted_output.get("metadata", {})
            if metadata:
                lines.append("📋 Analysis Metadata:")
                lines.append(f"   Target: {metadata.get('target', 'N/A')}")
                lines.append(f"   Database: {metadata.get('database_engine', 'N/A')}")
                lines.append(f"   Analysis ID: {metadata.get('analysis_id', 'N/A')}")

            return "\n".join(lines)

        except Exception as e:
            return f"Analysis completed but formatting failed: {str(e)}\n\nRaw result available in registry."

    def _format_raw_workflow_results(self, workflow_result: dict, skip_slow_fallback: bool = False) -> str:
        """Format raw workflow results when the main formatter fails."""
        lines = []
        lines.append("Query Analysis Results")
        lines.append("=" * 50)

        query = workflow_result.get("query", "")
        target = workflow_result.get("target", "")

        lines.append(f"Query: {query}")
        lines.append(f"Target: {target}")
        lines.append("")

        # Show basic execution info
        explain_results = workflow_result.get("explain_results", {})
        if explain_results and explain_results.get("success"):
            lines.append("Database Performance:")

            # Check if EXPLAIN ANALYZE was skipped
            was_skipped = explain_results.get('explain_analyze_skipped', False) or explain_results.get('explain_analyze_timeout', False)

            if was_skipped:
                # Show actual elapsed time when skipped, not the instant EXPLAIN time
                actual_elapsed = explain_results.get('actual_elapsed_time_ms', 0)
                elapsed_seconds = actual_elapsed / 1000
                if elapsed_seconds >= 60:
                    elapsed_str = f"{int(elapsed_seconds // 60)} min {int(elapsed_seconds % 60)} sec"
                else:
                    elapsed_str = f"{elapsed_seconds:.1f}s"

                lines.append(f"   WARNING: Execution Time: N/A (skipped after {elapsed_str})")
                skip_reason = explain_results.get('skip_reason', '')
                if skip_reason:
                    lines.append(f"   Note: {skip_reason}")
            else:
                # Show actual execution time with performance rating
                exec_time = explain_results.get('execution_time_ms', 0)
                if exec_time > 1000:
                    lines.append(f"   WARNING: Execution Time: {exec_time/1000:.2f}s (slow)")
                elif exec_time > 100:
                    lines.append(f"   WARNING: Execution Time: {exec_time:.0f}ms (moderate)")
                else:
                    lines.append(f"   OK: Execution Time: {exec_time:.1f}ms (fast)")

            lines.append(f"   Database Engine: {explain_results.get('database_engine', 'unknown').upper()}")
            lines.append(f"   Rows Examined: {explain_results.get('rows_examined', 0):,}")
            lines.append(f"   Rows Returned: {explain_results.get('rows_returned', 0):,}")
            cost = explain_results.get('cost_estimate', 0)
            if cost > 0:
                lines.append(f"   Query Cost: {cost:.1f}")
            lines.append("")
        else:
            lines.append("ERROR: Database execution failed or skipped")
            if explain_results.get("error"):
                lines.append(f"   Error: {explain_results.get('error')}")
            lines.append("")

        # Show LLM analysis if available
        llm_analysis = workflow_result.get("llm_analysis", {})
        if llm_analysis and llm_analysis.get("success"):
            analysis_results = llm_analysis.get("analysis_results", {})
            performance = analysis_results.get("performance_assessment", {})
            if performance:
                lines.append("AI Performance Analysis:")
                rating = performance.get('overall_rating', 'unknown')
                score = performance.get('efficiency_score', 0)

                if rating == 'excellent':
                    lines.append(f"   Overall Rating: {rating.upper()} ({score}/100)")
                elif rating == 'good':
                    lines.append(f"   Overall Rating: {rating.upper()} ({score}/100)")
                elif rating == 'fair':
                    lines.append(f"   WARNING: Overall Rating: {rating.upper()} ({score}/100)")
                else:
                    lines.append(f"   ERROR: Overall Rating: {rating.upper()} ({score}/100)")

                concerns = performance.get("primary_concerns", [])
                if concerns:
                    lines.append("   Key Issues:")
                    for concern in concerns[:4]:
                        lines.append(f"     • {concern}")

                # Show optimization opportunities (general recommendations)
                optimization_opportunities = llm_analysis.get("analysis_results", {}).get("optimization_opportunities", [])
                if optimization_opportunities:
                    lines.append("   General Recommendations:")
                    for i, opp in enumerate(optimization_opportunities[:3], 1):
                        desc = opp.get('description', 'No description')
                        priority = opp.get('priority', 'medium')
                        lines.append(f"     {i}. [{priority.upper()}] {desc}")

                # Show index recommendations
                index_recommendations = llm_analysis.get("index_recommendations", [])
                if index_recommendations:
                    lines.append("   Index Recommendations:")
                    for i, idx_rec in enumerate(index_recommendations[:3], 1):
                        sql = idx_rec.get('sql', '')
                        rationale = idx_rec.get('rationale', 'No rationale provided')
                        impact = idx_rec.get('estimated_impact', 'unknown')
                        lines.append(f"     {i}. {rationale}")
                        if sql:
                            lines.append(f"        SQL: {sql}")
                        lines.append(f"        Impact: {impact.upper()}")

                # Show rewrite suggestions
                rewrite_suggestions = llm_analysis.get("rewrite_suggestions", [])
                if rewrite_suggestions:
                    lines.append("   Query Rewrite Suggestions:")
                    for i, rewrite in enumerate(rewrite_suggestions[:3], 1):
                        explanation = rewrite.get('explanation', 'No explanation')
                        expected_improvement = rewrite.get('expected_improvement', 'unknown')
                        lines.append(f"     {i}. {explanation}")
                        lines.append(f"        Expected improvement: {expected_improvement}")
                        rewritten_sql = rewrite.get('rewritten_sql', '')
                        if rewritten_sql:
                            lines.append(f"        SQL: {rewritten_sql}")

                lines.append("")
        else:
            # Try to run LLM analysis directly as fallback
            explain_results = workflow_result.get("explain_results", {})
            if explain_results and explain_results.get("success"):
                lines.append("AI Performance Analysis:")
                # ONLY use workflow LLM results - no fallback
                workflow_llm_analysis = workflow_result.get("llm_analysis", {})
                if workflow_llm_analysis and workflow_llm_analysis.get("success"):
                    llm_result = workflow_llm_analysis.get("analysis_results", {})
                    # Also get index_recommendations from top level
                    llm_result['index_recommendations'] = workflow_llm_analysis.get("index_recommendations", [])
                    llm_result['rewrite_suggestions'] = workflow_llm_analysis.get("rewrite_suggestions", [])
                else:
                    llm_result = None
                    # Show what went wrong with workflow analysis
                    error_msg = workflow_llm_analysis.get("error", "Workflow LLM analysis failed")
                    lines.append(f"   WARNING: {error_msg[:100]}...")

                if llm_result:
                        performance = llm_result.get("performance_assessment", {})
                        rating = performance.get('overall_rating', 'unknown')
                        score = performance.get('efficiency_score', 0)

                        if rating == 'excellent':
                            lines.append(f"   Overall Rating: {rating.upper()} ({score}/100)")
                        elif rating == 'good':
                            lines.append(f"   Overall Rating: {rating.upper()} ({score}/100)")
                        elif rating == 'fair':
                            lines.append(f"   WARNING: Overall Rating: {rating.upper()} ({score}/100)")
                        else:
                            lines.append(f"   ERROR: Overall Rating: {rating.upper()} ({score}/100)")

                        concerns = performance.get("primary_concerns", [])
                        if concerns:
                            lines.append("   Key Issues:")
                            for concern in concerns[:4]:
                                lines.append(f"     • {concern}")

                        # Show index recommendations from AI analysis
                        index_recommendations = llm_result.get("index_recommendations", [])
                        if index_recommendations:
                            lines.append("   Index Recommendations:")
                            for i, idx_rec in enumerate(index_recommendations[:3], 1):
                                rationale = idx_rec.get('rationale', 'No rationale provided')
                                impact = idx_rec.get('estimated_impact', 'unknown')

                                lines.append(f"     {i}. {rationale}")
                                lines.append(f"        Impact: {impact.upper()}")

                        # Show AI-suggested rewrites
                        rewrite_suggestions = llm_result.get("rewrite_suggestions", [])
                        if rewrite_suggestions:
                            lines.append("   AI Suggested Query Rewrites:")
                            for i, rewrite in enumerate(rewrite_suggestions[:3], 1):
                                explanation = rewrite.get('explanation', 'No explanation')
                                expected_improvement = rewrite.get('expected_improvement', 'unknown')
                                lines.append(f"     {i}. {explanation} (Expected: {expected_improvement})")

                                # Show SQL preview
                                rewritten_sql = rewrite.get('rewritten_sql', '')
                                if rewritten_sql:
                                    sql_preview = rewritten_sql[:60] + "..." if len(rewritten_sql) > 60 else rewritten_sql
                                    lines.append(f"        → {sql_preview}")
                        else:
                            print("DEBUG: No rewrite suggestions found in llm_result")
                else:
                    lines.append("   WARNING: AI analysis unavailable")
                lines.append("")
            else:
                lines.append("WARNING: AI analysis unavailable (no database results)")
                lines.append("")

        # Show rewrite testing results if available
        rewrite_results = workflow_result.get("rewrite_test_results", {})

        if rewrite_results and rewrite_results.get("success"):
            tested_rewrites = rewrite_results.get("rewrite_results", [])
            best_rewrite = rewrite_results.get("best_rewrite")
            original_performance = rewrite_results.get("original_performance", {})
            testing_summary = rewrite_results.get("testing_summary", "")
            baseline_skipped = rewrite_results.get("baseline_skipped", False)
            baseline_skip_reason = rewrite_results.get("baseline_skip_reason", "")

            if tested_rewrites:
                lines.append("Query Rewrite Testing Results:")

                # Add clear visual indicators for each rewrite tested at the top
                all_results = rewrite_results.get("rewrite_results", [])
                for i, result in enumerate(all_results[:3], 1):
                    sql = result.get("sql", "")
                    if sql:
                        lines.append(f"   QUERY {i}: {sql}")
                lines.append("")

                successful_tests = [r for r in tested_rewrites if r.get("success")]
                lines.append(f"   {testing_summary}")
                lines.append("")

                # Only show performance comparison if we have valid baseline
                if original_performance and not baseline_skipped:
                    baseline_time = original_performance.get("execution_time_ms", 0)
                    lines.append("   Performance Comparison:")
                    lines.append(f"     Original Query: {baseline_time:.1f}ms")
                elif baseline_skipped:
                    lines.append("   Performance Comparison:")
                    lines.append("     WARNING: Original query was skipped - no baseline for comparison")
                    if baseline_skip_reason:
                        lines.append(f"     ({baseline_skip_reason})")

                # Show what AI suggested and attempted
                if rewrite_results.get("rewrite_results"):
                    lines.append("   AI Rewrite Attempts:")
                    all_results = rewrite_results.get("rewrite_results", [])
                    for i, result in enumerate(all_results[:3], 1):
                        # Get explanation and SQL
                        metadata = result.get("suggestion_metadata", {})
                        explanation = metadata.get("explanation", "Rewrite attempt")
                        expected_improvement = metadata.get("expected_improvement", "unknown")

                        # Show what was attempted
                        lines.append(f"     {i}. {explanation}")
                        lines.append(f"        Expected: {expected_improvement} improvement")

                        # Show FULL SQL (not truncated)
                        sql = result.get("sql", "")
                        if sql:
                            lines.append(f"        FULL SQL: {sql}")

                        # Show result - check if this rewrite was also skipped
                        if result.get("success"):
                            recommendation = result.get("recommendation", "")
                            if recommendation == "advisory_ddl":
                                lines.append("        Result: ADVISORY: DDL suggestion (not executed for safety)")
                                lines.append("        Note: This DDL can be applied manually if desired")
                            else:
                                perf = result.get("performance", {})
                                was_skipped = result.get("was_skipped", False) or perf.get("was_skipped", False)

                                if was_skipped:
                                    # Show actual elapsed time when skipped
                                    actual_elapsed = perf.get("actual_elapsed_time_ms", 0)
                                    skip_reason = result.get("skip_reason") or perf.get("skip_reason", "")
                                    lines.append(f"        Result: N/A (skipped after {actual_elapsed / 1000:.1f}s)")
                                    if skip_reason:
                                        lines.append(f"        Note: {skip_reason}")
                                else:
                                    # Show actual execution time
                                    exec_time = perf.get("execution_time_ms", 0)
                                    lines.append(f"        Result: {exec_time:.1f}ms")

                                    # Only show comparison if baseline wasn't skipped
                                    if not baseline_skipped and original_performance:
                                        baseline_time = original_performance.get("execution_time_ms", 0)
                                        if baseline_time > 0:
                                            improvement_pct = ((baseline_time - exec_time) / baseline_time) * 100
                                            lines.append(f"        vs Original: {improvement_pct:+.1f}%")
                        else:
                            error = result.get("error", "Failed")
                            # Better error messages for common issues
                            if "Key" in error and "doesn't exist" in error:
                                lines.append("        Result: ERROR: Missing index (suggested index not found)")
                            elif "syntax error" in error.lower():
                                lines.append("        Result: ERROR: SQL syntax error")
                            elif "safety validation" in error.lower():
                                lines.append("        Result: ERROR: Blocked for safety (dangerous keyword)")
                            else:
                                error_short = error[:60] + "..." if len(error) > 60 else error
                                lines.append(f"        Result: ERROR: {error_short}")
                        lines.append("")

                # Show detailed results for each tested rewrite ONLY if baseline wasn't skipped
                # (If baseline was skipped, we can't compare performance so this section is meaningless)
                if not baseline_skipped:
                    for i, result in enumerate(successful_tests[:3], 1):  # Show top 3
                        if result.get("success"):
                            # Check if THIS rewrite was also skipped
                            perf = result.get("performance", {})
                            was_skipped = result.get("was_skipped", False) or perf.get("was_skipped", False)

                            if was_skipped:
                                # Skip this rewrite in the comparison - can't compare EXPLAIN times
                                continue

                            recommendation = result.get("recommendation", "")

                            if recommendation == "advisory_ddl":
                                # Handle advisory DDL suggestions
                                status = "ADVISORY"
                                lines.append(f"     {status} Advisory DDL {i}: Index/schema suggestion (review manually)")
                                sql_preview = result.get("sql", "")[:70] + "..." if len(result.get("sql", "")) > 70 else result.get("sql", "")
                                lines.append(f"       → {sql_preview}")
                            else:
                                # Handle executable rewrites with performance data
                                improvement = result.get("improvement", {})
                                time_improvement = improvement.get("execution_time", {})
                                overall_improvement = improvement.get("overall", {})

                                rewrite_time = time_improvement.get("rewrite_ms", 0)
                                improvement_pct = overall_improvement.get("improvement_pct", 0)

                                # Status icon based on improvement
                                if improvement_pct >= 10:
                                    status = "BETTER"
                                elif improvement_pct >= 5:
                                    status = "MODERATE"
                                elif improvement_pct > 0:
                                    status = "MINOR"
                                else:
                                    status = "WORSE"

                                lines.append(f"     {status} Rewrite {i}: {rewrite_time:.1f}ms ({improvement_pct:+.1f}%)")

                                # Show SQL preview for significant improvements
                                if improvement_pct >= 5:
                                    sql_preview = result.get("sql", "")[:50] + "..." if len(result.get("sql", "")) > 50 else result.get("sql", "")
                                    lines.append(f"       → {sql_preview}")

                    # Show best rewrite recommendation ONLY if we have valid comparisons
                    if best_rewrite:
                        # Check if best rewrite was skipped
                        best_perf = best_rewrite.get("performance", {})
                        best_was_skipped = best_rewrite.get("was_skipped", False) or best_perf.get("was_skipped", False)

                        if not best_was_skipped:
                            overall_best = best_rewrite.get("improvement", {}).get("overall", {})
                            best_improvement = overall_best.get("improvement_pct", 0)
                            recommendation = best_rewrite.get("recommendation", "")

                            if best_improvement >= 10:
                                lines.append(f"   Best Performance: {best_improvement:.1f}% improvement - {recommendation}")
                            elif best_improvement >= 5:
                                lines.append(f"   MODERATE Improvement: {best_improvement:.1f}% - Consider testing in production")
                            elif best_improvement > 0:
                                lines.append(f"   Minor Improvement: {best_improvement:.1f}% - Marginal benefit")
                            else:
                                lines.append("   No beneficial rewrites found")
                        else:
                            lines.append("   Best rewrite was also skipped - no valid comparison")
                    else:
                        lines.append("   No beneficial rewrites identified")

                lines.append("")

        # Add actionable optimization suggestions based on results
        rewrite_results = workflow_result.get("rewrite_test_results", {})
        if rewrite_results and rewrite_results.get("success"):
            rewrite_test_results = rewrite_results.get("rewrite_results", [])
            failed_rewrites = [r for r in rewrite_test_results if not r.get("success")]
            successful_rewrites = [r for r in rewrite_test_results if r.get("success")]

            # Generate actionable suggestions
            suggestions = []

            # Check for missing index suggestions from failed rewrites
            for failed in failed_rewrites:
                error = failed.get("error", "")
                if "doesn't exist" in error and "Key" in error:
                    # Extract index name from error
                    import re
                    match = re.search(r"Key '([^']+)' doesn't exist", error)
                    if match:
                        index_name = match.group(1)
                        # Try to generate the actual CREATE INDEX statement
                        create_statement = _generate_create_index_statement(index_name, workflow_result.get("query", ""))
                        if create_statement:
                            suggestions.append("Create missing index:")
                            suggestions.append(f"     {create_statement}")
                        else:
                            suggestions.append(f"Create missing index: {index_name}")
                elif "Missing index" in error:
                    suggestions.append("Consider adding indexes on join and filter columns")

            # Add general performance suggestions based on results
            if not successful_rewrites and rewrite_test_results:
                # If no rewrites provided meaningful improvement - suggest specific indexes
                suggestions.append("Add indexes for better performance:")
                # Extract table and column info from the original query
                query = workflow_result.get("query", "").upper()
                if "JOIN" in query and "TCONST" in query:
                    suggestions.append("     CREATE INDEX idx_tconst ON title_basics (tconst);")
                    suggestions.append("     CREATE INDEX idx_tconst_ratings ON title_ratings (tconst);")
                if "NUMVOTES" in query:
                    suggestions.append("     CREATE INDEX idx_numvotes ON title_ratings (numVotes);")
                if "TITLETYPE" in query:
                    suggestions.append("     CREATE INDEX idx_titletype ON title_basics (titleType);")
            elif successful_rewrites:
                # Check if improvements were minimal
                minimal_improvements = [r for r in successful_rewrites
                                      if r.get("improvement", {}).get("overall", {}).get("improvement_pct", 0) < 10]
                if minimal_improvements:
                    suggestions.append("Consider composite indexes for better performance:")
                    query = workflow_result.get("query", "").upper()
                    if "NUMVOTES" in query and "TCONST" in query:
                        suggestions.append("     CREATE INDEX idx_numvotes_tconst ON title_ratings (numVotes, tconst);")
                    if "TITLETYPE" in query and "TCONST" in query:
                        suggestions.append("     CREATE INDEX idx_titletype_tconst ON title_basics (titleType, tconst);")

            # Show suggestions if we have any
            if suggestions:
                lines.append("Recommended Improvements:")
                for suggestion in suggestions[:3]:  # Show top 3 suggestions
                    lines.append(f"   • {suggestion}")
                lines.append("")

        # Quick Summary of Proven Query Rewrites (only rewrites with valid performance data)
        rewrite_results = workflow_result.get("rewrite_test_results", {})
        has_rewrites = False

        if rewrite_results and rewrite_results.get("success"):
            tested_rewrites = rewrite_results.get("rewrite_results", [])
            baseline_skipped = rewrite_results.get("baseline_skipped", False)

            # Only include rewrites where both baseline and rewrite actually executed (not skipped)
            successful_rewrites = []
            for r in tested_rewrites:
                if r.get("success") and r.get("recommendation") not in ["advisory_ddl"]:
                    # Check if this rewrite was skipped
                    perf = r.get("performance", {})
                    was_skipped = r.get("was_skipped", False) or perf.get("was_skipped", False)
                    # Only include if baseline wasn't skipped AND this rewrite wasn't skipped
                    if not baseline_skipped and not was_skipped:
                        successful_rewrites.append(r)

            if successful_rewrites:
                has_rewrites = True
                lines.append("Proven Query Rewrites:")
                for i, rewrite in enumerate(successful_rewrites[:3], 1):
                    metadata = rewrite.get("suggestion_metadata", {})
                    explanation = metadata.get("explanation", "Query rewrite")
                    improvement = rewrite.get("improvement", {}).get("overall", {}).get("improvement_pct", 0)
                    sql = rewrite.get("sql", "")

                    lines.append(f"   {i}. {explanation}")
                    lines.append(f"      Performance: {improvement:+.1f}% improvement")
                    lines.append(f"      SQL: {sql}")
                lines.append("")

        # ReadySet Cacheability Results (from parallel analysis)
        readyset_analysis = workflow_result.get("readyset_analysis", {})
        if readyset_analysis and readyset_analysis.get("success"):
            lines.append("🚀 ReadySet Cacheability:")
            final_verdict = readyset_analysis.get("final_verdict", {})
            cacheable = final_verdict.get("cacheable", False)
            confidence = final_verdict.get("confidence", "unknown")
            method = final_verdict.get("method", "unknown")
            reason = readyset_analysis.get('explain_cache_result', {}).get("error", "")

            status = "CACHEABLE" if cacheable else "NOT CACHEABLE"
            lines.append(f"   {status} (confidence: {confidence})")
            lines.append(f"   Method: {method}")
            if reason:
                lines.append(f"Reason: {reason}")

            # Show cache creation status
            cached = final_verdict.get("cached", False)
            create_result = readyset_analysis.get("create_cache_result", {})
            if cacheable and create_result.get("already_cached"):
                lines.append("   ℹ️  Query already cached in ReadySet")
            elif cacheable and cached:
                lines.append("   Cache created in ReadySet")
            elif cacheable and create_result:
                # Cache creation was attempted but failed
                error = create_result.get("error", "Unknown error")
                lines.append(f"   ⚠ Cache creation failed: {error}")

            explain_result = readyset_analysis.get("explain_cache_result", {})
            if explain_result:
                if explain_result.get("explanation"):
                    lines.append(f"   Explanation: {explain_result.get('explanation')}")
                issues = explain_result.get("issues") or []
                if issues:
                    lines.append("   Issues:")
                    for issue in issues:
                        lines.append(f"     • {issue}")
                elif not explain_result.get("explanation"):
                    # Show details if no explanation
                    details = explain_result.get("details")
                    if isinstance(details, str) and details.strip():
                        lines.append(f"   Details: {details}")
            lines.append("")

        # Show completion status
        lines.append("Analysis Summary:")
        lines.append(f"   • Query executed against {target}")
        lines.append("   • Results stored in query registry")
        lines.append("   • Run `rdst query list --limit 5` to see recent queries")

        storage_result = workflow_result.get("storage_result", {})
        if storage_result and storage_result.get("success"):
            analysis_id = storage_result.get("analysis_id")
            if analysis_id:
                lines.append(f"   • Analysis ID: {analysis_id}")

        return "\n".join(lines)

    def _get_cache_id_for_query(
        self,
        query: str,
        readyset_port: int,
        db_config: dict
    ) -> str:
        """
        Query SHOW CACHES to get the cache ID for a specific query.

        Args:
            query: SQL query to find cache for
            readyset_port: ReadySet port
            db_config: Database configuration

        Returns:
            Cache ID (query_id) if found, None otherwise
        """
        import subprocess

        try:
            database = db_config.get('database', 'testdb')
            user = db_config.get('user', 'postgres')
            password = db_config.get('password', '')
            engine = (db_config.get('engine') or 'postgresql').lower()

            # Normalize query for comparison (remove extra whitespace)
            normalized_query = ' '.join(query.strip().split())

            if engine == 'mysql':
                cmd = [
                    'mysql',
                    '--protocol=TCP',
                    '--host=localhost',
                    f'--port={readyset_port}',
                    f'--user={user}',
                    f'--database={database}',
                    '-e', 'SHOW CACHES;'
                ]
                env = {'MYSQL_PWD': password} if password else {}
            else:
                # PostgreSQL - use unaligned output for easier parsing
                cmd = [
                    'psql',
                    '-h', 'localhost',
                    '-p', str(readyset_port),
                    '-U', user,
                    '-d', database,
                    '-c', 'SHOW CACHES;',
                    '-A',  # Unaligned output
                    '-t',  # Tuples only (no headers)
                    '-F', '|||'  # Use triple pipe as field separator (less likely to conflict)
                ]
                env = {'PGPASSWORD': password} if password else {}

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
                env={**subprocess.os.environ, **env}
            )

            if result.returncode != 0:
                return None

            # Parse output to find matching query
            # SHOW CACHES with -A -t -F '|||' gives: query_id|||cache_name|||query_text|||fallback|||count
            # Query text may span multiple lines, so we need to accumulate
            lines = result.stdout.strip().split('\n')

            current_cache_id = None
            current_query_parts = []

            for line in lines:
                if not line.strip():
                    continue

                parts = line.split('|||')

                # New cache entry starts when first field has a query_id (starts with 'q_')
                if len(parts) >= 3 and parts[0].strip().startswith('q_'):
                    # Check previous cache if we have one
                    if current_cache_id and current_query_parts:
                        full_query = ' '.join(current_query_parts)
                        normalized_cache_query = ' '.join(full_query.strip().split())

                        if normalized_query.lower() in normalized_cache_query.lower():
                            return current_cache_id

                    # Start new cache entry
                    current_cache_id = parts[0].strip()
                    current_query_parts = [parts[2].strip()]
                elif current_cache_id and len(parts) >= 3:
                    # Continuation line for current cache
                    current_query_parts.append(parts[2].strip())

            # Check the last cache entry
            if current_cache_id and current_query_parts:
                full_query = ' '.join(current_query_parts)
                normalized_cache_query = ' '.join(full_query.strip().split())

                if normalized_query.lower() in normalized_cache_query.lower():
                    return current_cache_id

            return None

        except Exception:
            # Silently fail - cache ID is nice to have but not critical
            return None

    def _run_cache_performance_comparison(
        self,
        resolved_input: AnalyzeInput,
        target_name: str,
        target_config: dict,
        readyset_analysis_result: dict
    ) -> dict:
        """
        Run cache creation and performance comparison workflow.

        Args:
            resolved_input: Resolved input with query info
            target_name: Name of the target database
            target_config: Resolved target configuration
            readyset_analysis_result: Results from ReadySet analysis (contains container info)

        Returns:
            Dict containing cache performance comparison results
        """
        try:
            import os
            from ..functions.readyset_explain_cache import create_cache_readyset
            from ..functions.performance_comparison import compare_query_performance, format_performance_comparison

            print("\n🚀 Running cache performance comparison...")

            # Extract ReadySet container info from analysis result
            if not readyset_analysis_result.get("success"):
                return {
                    "success": False,
                    "error": "ReadySet analysis failed, cannot run cache comparison"
                }

            readyset_port = readyset_analysis_result.get("readyset_port")
            if not readyset_port:
                return {
                    "success": False,
                    "error": "ReadySet port not available from setup"
                }

            # Get test_db_config from setup result
            test_db_config = readyset_analysis_result.get("setup_result", {}).get("target_config", {})

            # Get password from environment
            password = target_config.get('password', '')
            password_env = target_config.get('password_env')
            if password_env:
                password = os.environ.get(password_env, '')

            # Ensure password is set from our target_config
            if not test_db_config.get('password'):
                test_db_config['password'] = password

            # Check if cache is already created or if we need to create it
            create_result = readyset_analysis_result.get("create_cache_result", {})

            if not create_result.get("cached") and not create_result.get("already_cached"):
                # Cache wasn't created in ReadySet analysis, create it now
                print("  -> Creating cache in ReadySet...")
                print(f"     Query: {resolved_input.sql[:100]}{'...' if len(resolved_input.sql) > 100 else ''}")
                create_result = create_cache_readyset(
                    query=resolved_input.sql,
                    readyset_port=readyset_port,
                    test_db_config=test_db_config
                )

                if not create_result.get('success') and not create_result.get('cached'):
                    print(f"  ✗ Cache creation failed: {create_result.get('error', 'Unknown error')}")
                    return {
                        "success": False,
                        "error": f"Failed to create cache: {create_result.get('error', 'Unknown error')}"
                    }
                print("  ✓ Cache created successfully")

                # Get cache ID by querying SHOW CACHES
                try:
                    cache_id = self._get_cache_id_for_query(
                        resolved_input.sql,
                        readyset_port,
                        test_db_config
                    )
                    if cache_id:
                        print(f"     Cache ID: {cache_id}")
                        create_result['cache_id'] = cache_id
                except Exception as e:
                    print(f"     (Could not retrieve cache ID: {e})")
            else:
                print("  ℹ️  Cache already exists")
                # Try to get the cache ID even if it already existed
                try:
                    cache_id = self._get_cache_id_for_query(
                        resolved_input.sql,
                        readyset_port,
                        test_db_config
                    )
                    if cache_id:
                        print(f"     Cache ID: {cache_id}")
                        create_result['cache_id'] = cache_id
                except Exception:
                    pass

            # Run performance comparison
            print("  -> Running performance comparison (10 iterations with 2 warmup)...")

            # Use the original target DB configuration (production database)
            original_db_config = {
                'engine': target_config.get('engine', 'postgresql'),
                'host': target_config.get('host', 'localhost'),
                'port': target_config.get('port', 5432),
                'database': target_config.get('database', 'postgres'),
                'user': target_config.get('user', 'postgres'),
                'password': password
            }

            perf_result = compare_query_performance(
                query=resolved_input.sql,
                original_db_config=original_db_config,
                readyset_port=readyset_port,
                readyset_host='localhost',
                iterations=10,
                warmup_iterations=2,
                readyset_db_config=test_db_config
            )

            if not perf_result.get('success'):
                return {
                    "success": False,
                    "error": f"Performance comparison failed: {perf_result.get('error', 'Unknown error')}"
                }

            print("  ✓ Performance comparison complete")

            # Generate deployment instructions
            from ..functions.readyset_cacheability import check_readyset_cacheability
            static_result = check_readyset_cacheability(query=resolved_input.sql)
            cache_command = static_result.get('create_cache_command') or f"CREATE CACHE FROM {resolved_input.sql};"

            deployment_instructions = []
            deployment_instructions.append("\n" + "="*70)
            deployment_instructions.append("Deployment Instructions")
            deployment_instructions.append("="*70)
            deployment_instructions.append("")
            deployment_instructions.append("To cache this query in your ReadySet instance:")
            deployment_instructions.append("")
            deployment_instructions.append(cache_command)
            deployment_instructions.append("")
            deployment_instructions.append("Connect to your ReadySet and run this command:")
            if target_config.get('engine') == 'mysql':
                deployment_instructions.append(f"  mysql -h YOUR_READYSET_HOST -P YOUR_READYSET_PORT -u {target_config['user']} -D {target_config['database']}")
            else:
                deployment_instructions.append(f"  psql -h YOUR_READYSET_HOST -p YOUR_READYSET_PORT -U {target_config['user']} -d {target_config['database']}")
            deployment_instructions.append("")
            deployment_instructions.append("=" * 70)
            deployment_instructions.append("Notes on Local Test Containers")
            deployment_instructions.append("=" * 70)
            deployment_instructions.append("")
            deployment_instructions.append("• Test containers are persistent and reused across runs")
            deployment_instructions.append(f"• ReadySet container port: {readyset_port}")
            deployment_instructions.append(f"• To view all caches: psql -h localhost -p {readyset_port} -U {target_config['user']} -d {target_config['database']} -c 'SHOW CACHES;'")
            deployment_instructions.append(f"• To drop this cache: psql -h localhost -p {readyset_port} -U {target_config['user']} -d {target_config['database']} -c \"DROP CACHE <cache_name>;\"")
            deployment_instructions.append("")

            return {
                "success": True,
                "performance_comparison": perf_result,
                "cache_command": cache_command,
                "deployment_instructions": "\n".join(deployment_instructions),
                "create_result": create_result
            }

        except ImportError as e:
            print(f"  ERROR: Missing required dependency: {str(e)}")
            return {
                "success": False,
                "error": f"Missing required dependency for cache comparison: {str(e)}"
            }
        except KeyError as e:
            print(f"  ERROR: Missing required configuration key: {str(e)}")
            return {
                "success": False,
                "error": f"Invalid configuration - missing required key {str(e)}. Please check your target configuration."
            }
        except ConnectionError as e:
            print(f"  ERROR: Failed to connect to database or ReadySet: {str(e)}")
            return {
                "success": False,
                "error": f"Database connection failed: {str(e)}. Ensure containers are running and accessible."
            }
        except TimeoutError as e:
            print(f"  ERROR: Operation timed out: {str(e)}")
            return {
                "success": False,
                "error": f"Performance comparison timed out: {str(e)}. Database or ReadySet may be unresponsive."
            }
        except Exception as e:
            # Catch-all for unexpected errors - include more context
            import traceback
            error_context = f"{type(e).__name__}: {str(e)}"
            print(f"  ERROR: Cache performance comparison failed: {error_context}")
            print(f"  Debug info: {traceback.format_exc()[:500]}")  # Print first 500 chars of traceback
            return {
                "success": False,
                "error": f"Cache performance comparison failed with {error_context}. Run with --verbose for full traceback."
            }


def _generate_create_index_statement(index_name: str, query: str) -> str:
    """Generate CREATE INDEX statement from index name and query analysis."""
    try:
        # Common patterns for index names and their corresponding CREATE statements
        index_name_lower = index_name.lower()
        query_upper = query.upper()

        # Map common index patterns to CREATE statements
        if "numvotes" in index_name_lower and "tconst" in index_name_lower:
            return "CREATE INDEX idx_numvotes_tconst ON title_ratings (numVotes, tconst);"
        elif "numvotes" in index_name_lower:
            return "CREATE INDEX idx_numvotes ON title_ratings (numVotes);"
        elif "titletype" in index_name_lower and "tconst" in index_name_lower:
            return "CREATE INDEX idx_titletype_tconst ON title_basics (titleType, tconst);"
        elif "titletype" in index_name_lower:
            return "CREATE INDEX idx_titletype ON title_basics (titleType);"
        elif "tconst" in index_name_lower:
            # Determine table from query context
            if "title_ratings" in query_upper or "tr." in query_upper:
                return "CREATE INDEX idx_tconst ON title_ratings (tconst);"
            else:
                return "CREATE INDEX idx_tconst ON title_basics (tconst);"
        else:
            # Generic case - try to extract table and columns from index name
            return f"CREATE INDEX {index_name} ON <table> (<columns>);"
    except Exception:
        return ""

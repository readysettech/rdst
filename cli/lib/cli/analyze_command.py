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

import asyncio
import logging
import sys
import os
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass
from .rdst_cli import RdstResult

logger = logging.getLogger(__name__)

# Import UI system
from rich.console import Group

from lib.ui import (
    Banner,
    KeyValueTable,
    Layout,
    MessagePanel,
    NextSteps,
    NoticePanel,
    Prompt,
    RegistryTable,
    SectionBox,
    StyleTokens,
    get_console,
)

from ..query_registry.query_registry import QueryRegistry, normalize_sql, hash_sql
from ..query_registry.conversation_registry import ConversationRegistry
from ..llm_manager.llm_manager import LLMManager
from .parameter_prompt import has_unresolved_placeholders, prompt_for_parameters
from ..functions.readyset_explain_cache import get_cache_id_for_query


@dataclass
class AnalyzeInput:
    """Represents the resolved input for analyze command."""

    sql: str  # Original SQL with actual parameter values
    normalized_sql: str  # Normalized SQL with ? placeholders
    source: str  # "query-id", "inline", "file", "stdin", "prompt"
    hash: str
    tag: str = ""
    save_as: str = ""
    registry_target: str = ""  # Target from registry (last_target)


class AnalyzeInputError(Exception):
    """Raised when there are issues with analyze input."""

    pass


class AnalyzeCommand:
    """Handles all functionality for the rdst analyze command."""

    def __init__(self, client=None):
        """Initialize the AnalyzeCommand with an optional CloudAgentClient."""
        self.client = client
        self._console = get_console()
        self.registry = QueryRegistry()
        self._renderer = None  # Lazy-initialized AnalyzeRenderer

    def resolve_input(
        self,
        hash: Optional[str] = None,
        inline_query: Optional[str] = None,
        file_path: Optional[str] = None,
        use_stdin: bool = False,
        name: Optional[str] = None,
        positional_query: Optional[str] = None,
        save_as: Optional[str] = None,
        large_query_bypass: bool = False,
    ) -> AnalyzeInput:
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
            large_query_bypass: If True, allows queries up to 10KB instead of 4KB

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
            ("positional", positional_query),
        ]
        active_inputs = [(name, value) for name, value in inputs_provided if value]

        if len(active_inputs) > 1:
            primary = active_inputs[0][0]
            ignored = [name for name, _ in active_inputs[1:]]
            self._console.print(
                f"[{StyleTokens.WARNING}]Using {primary} input, ignoring: {', '.join(ignored)}[/{StyleTokens.WARNING}]"
            )

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
                return self._resolve_inline_query(
                    inline_query, save_as, large_query_bypass
                )

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

            return self._resolve_inline_query(
                positional_query, save_as, large_query_bypass
            )

        except Exception as e:
            raise AnalyzeInputError(f"Failed to resolve input: {e}")

    def _resolve_by_hash(self, hash: str, save_as: str) -> AnalyzeInput:
        """Resolve query by hash from registry."""
        entry = self.registry.get_query(hash)
        if not entry:
            raise AnalyzeInputError(
                f"Query hash '{hash}' not found in registry. Run 'rdst query list' to see available queries."
            )

        # Get the executable SQL with parameter values reconstructed
        executable_sql = self.registry.get_executable_query(hash, interactive=False)
        if not executable_sql:
            raise AnalyzeInputError(
                f"Could not reconstruct executable query for hash '{hash}'"
            )

        return AnalyzeInput(
            sql=executable_sql,  # Original SQL with parameter values
            normalized_sql=entry.sql,  # Normalized SQL with ? placeholders
            source="hash",
            hash=entry.hash,
            tag=entry.tag,
            save_as=save_as,
            registry_target=entry.last_target,
        )

    def _resolve_by_name(self, name: str, save_as: str) -> AnalyzeInput:
        """Resolve query by name from registry."""
        entry = self.registry.get_query_by_tag(name)
        if not entry:
            raise AnalyzeInputError(
                f"Query '{name}' not found in registry. Run 'rdst query list' to see available queries."
            )

        # Get the executable SQL with parameter values reconstructed
        executable_sql = self.registry.get_executable_query_by_tag(
            name, interactive=False
        )
        if not executable_sql:
            raise AnalyzeInputError(
                f"Could not reconstruct executable query for '{name}'"
            )

        return AnalyzeInput(
            sql=executable_sql,  # Original SQL with parameter values
            normalized_sql=entry.sql,  # Normalized SQL with ? placeholders
            source="name",
            hash=entry.hash,
            tag=entry.tag,
            save_as=save_as,
            registry_target=entry.last_target,
        )

    def _enforce_query_size_limit(self, query: str, bypass: bool = False) -> None:
        """
        Enforce query size limits.

        Default limit is 4KB (MAX_QUERY_LENGTH). Use --large-query-bypass
        for one-time analysis of queries up to 10KB.

        Args:
            query: The SQL query string to check
            bypass: If True, allow up to 10KB instead of 4KB

        Raises:
            AnalyzeInputError: If query exceeds the size limit
        """
        from lib.data_manager_service.data_manager_service_command_sets import (
            MAX_QUERY_LENGTH,
        )

        query_bytes = len(query.encode("utf-8"))

        if not bypass:
            # Default 4KB limit (MAX_QUERY_LENGTH) - registry limit
            if query_bytes > MAX_QUERY_LENGTH:
                raise AnalyzeInputError(
                    f"Query size ({query_bytes:,} bytes) exceeds the default limit (4KB).\n\n"
                    "Use --large-query-bypass for one-time analysis of larger queries:\n"
                    "  rdst analyze --large-query-bypass -f your_file.sql\n"
                    "  rdst analyze --large-query-bypass -q '<your query>'\n\n"
                    "This allows queries up to 10KB (will not be saved to registry)."
                )
        else:
            # With bypass, allow up to 10KB
            max_size = 10 * 1024  # 10KB
            if query_bytes > max_size:
                raise AnalyzeInputError(
                    f"Query size ({query_bytes:,} bytes) exceeds maximum allowed size (10KB).\n\n"
                    "Please reduce your query size or break it into smaller parts."
                )

    def _resolve_inline_query(
        self, query: str, save_as: str, bypass: bool = False
    ) -> AnalyzeInput:
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
            save_as=save_as,
        )

    def _resolve_file_input(
        self, file_path: str, save_as: str, bypass: bool = False
    ) -> AnalyzeInput:
        """Resolve query from file input."""
        path = Path(file_path)

        if not path.exists():
            raise AnalyzeInputError(f"File not found: {file_path}")

        if not path.is_file():
            raise AnalyzeInputError(f"Path is not a file: {file_path}")

        try:
            # Read file with UTF-8 encoding, handling BOM
            content = path.read_text(encoding="utf-8-sig")
        except Exception as e:
            raise AnalyzeInputError(f"Could not read file {file_path}: {e}")

        if not content.strip():
            raise AnalyzeInputError(f"File is empty: {file_path}")

        # Handle multi-statement files - take the first non-empty statement
        content = content.strip()

        # Split by semicolon and take first statement
        statements = [stmt.strip() for stmt in content.split(";") if stmt.strip()]
        if not statements:
            raise AnalyzeInputError(
                f"No valid SQL statements found in file: {file_path}"
            )

        if len(statements) > 1:
            self._console.print(
                f"[{StyleTokens.WARNING}]File contains {len(statements)} statements, analyzing the first one[/{StyleTokens.WARNING}]"
            )

        query = statements[0].strip()
        self._enforce_query_size_limit(query, bypass)

        normalized_sql = normalize_sql(query)
        query_hash = hash_sql(query)

        return AnalyzeInput(
            sql=query,  # Original SQL
            normalized_sql=normalized_sql,
            source="file",
            hash=query_hash,
            save_as=save_as,
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
            raise AnalyzeInputError(
                "No input provided via stdin. Use pipe or redirect input."
            )

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
            save_as=save_as,
        )

    def _resolve_interactive_prompt(self, save_as: str) -> AnalyzeInput:
        """Resolve query from interactive user prompt or registry browser."""
        if not sys.stdin.isatty():
            raise AnalyzeInputError("No query provided and stdin is not interactive")

        # First, check if there are saved queries to browse
        saved_queries = self.registry.list_queries(
            limit=100
        )  # Get up to 100 recent queries

        if saved_queries:
            # Offer to browse saved queries or enter new one
            try:
                from lib.ui import Confirm

                browse_saved = Confirm.ask(
                    f"Found {len(saved_queries)} saved queries. Browse them instead of entering new query?",
                    default=True,
                )

                if browse_saved:
                    return self._browse_saved_queries(save_as)

            except (KeyboardInterrupt, EOFError):
                raise AnalyzeInputError("Query selection cancelled by user")

        # Fall back to manual query input with multiline support
        try:
            self._console.print(
                SectionBox(
                    title="SQL Query Input",
                    content=(
                        "Paste your SQL query below (multiline supported).\n"
                        "End with a semicolon (;) and press Enter, or press Enter twice on a blank line."
                    ),
                    hint="Press Ctrl+C to cancel.",
                    border_style=StyleTokens.PANEL_BORDER,
                    width=Layout.PANEL_WIDTH,
                )
            )

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
                if line.rstrip().endswith(";"):
                    break

            query = "\n".join(lines)

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
            save_as=save_as,
        )

    def _looks_like_hash(self, text: str) -> bool:
        import re

        return bool(re.match(r"^[0-9a-f]{12}$", text.lower()))

    def _browse_saved_queries(self, save_as: str) -> AnalyzeInput:
        """Browse and select from saved queries."""
        saved_queries = self.registry.list_queries(limit=50)  # Show up to 50 queries

        if not saved_queries:
            raise AnalyzeInputError("No saved queries found")

        try:
            # Display queries using RegistryTable component (handles Rich/plain fallback)
            table = RegistryTable(
                saved_queries,
                show_numbers=True,
                title=f"Select Query to Analyze ({len(saved_queries)} queries)",
            )
            self._console.print(table)

            # Get user selection
            while True:
                choice = Prompt.ask(
                    f"\n[{StyleTokens.HEADER}]Select query to analyze[/{StyleTokens.HEADER}] ([{StyleTokens.WARNING}]1-{len(saved_queries)}[/{StyleTokens.WARNING}], [{StyleTokens.ERROR}]q[/{StyleTokens.ERROR}] to quit)"
                )

                if choice.lower() in ["q", "quit", "exit"]:
                    raise AnalyzeInputError("Query selection cancelled by user")

                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(saved_queries):
                        selected_query = saved_queries[idx]
                        # Get executable SQL with parameter values reconstructed
                        executable_sql = self.registry.get_executable_query(
                            selected_query.hash, interactive=False
                        )
                        if not executable_sql:
                            executable_sql = (
                                selected_query.sql
                            )  # Fallback to normalized
                        return AnalyzeInput(
                            sql=executable_sql,
                            normalized_sql=selected_query.sql,
                            source="registry",
                            hash=selected_query.hash,
                            tag=selected_query.tag,
                            save_as=save_as,
                        )
                    else:
                        self._console.print(
                            f"[{StyleTokens.ERROR}]Invalid selection. Please enter 1-{len(saved_queries)} or 'q'[/{StyleTokens.ERROR}]"
                        )
                except ValueError:
                    self._console.print(
                        f"[{StyleTokens.ERROR}]Invalid input. Please enter a number or 'q'[/{StyleTokens.ERROR}]"
                    )

        except (KeyboardInterrupt, EOFError):
            raise AnalyzeInputError("Query selection cancelled by user")

    def execute_analyze(
        self,
        resolved_input: AnalyzeInput,
        target: Optional[str] = None,
        readyset: bool = False,
        readyset_cache: bool = False,
        fast: bool = False,
        interactive: bool = False,
        review: bool = False,
        output_json: bool = False,
        skip_warning: bool = False,
    ) -> RdstResult:
        """
        Execute the analyze command with resolved input using the workflow engine.

        Args:
            resolved_input: Resolved input from resolve_input()
            target: Target database name
            readyset: Whether to run parallel workflow with Readyset testing
            readyset_cache: Whether to evaluate Readyset caching with performance comparison
            fast: Whether to auto-skip slow EXPLAIN ANALYZE queries after 10 seconds
            interactive: Whether to enter interactive mode after analysis
            review: Whether to review conversation history instead of analyzing
            output_json: Whether to output results as JSON (for programmatic use)

        Returns:
            RdstResult with analysis results
        """
        from .rdst_cli import RdstResult, TargetsConfig
        from .interactive_mode import display_conversation_history

        try:
            if output_json and interactive:
                return RdstResult(False, "--json cannot be used with --interactive")

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
                    conversation = conv_registry.load_conversation(
                        resolved_input.hash, provider
                    )
                    display_conversation_history(
                        conversation, show_system_messages=False
                    )
                    return RdstResult(
                        True,
                        f"Conversation history for query hash: {resolved_input.hash}",
                    )
                else:
                    return RdstResult(
                        False,
                        f"No conversation found for query hash: {resolved_input.hash}",
                    )

            # Handle --interactive flag: Check for existing conversation BEFORE running analysis
            if interactive:
                conv_registry = ConversationRegistry()
                llm_manager = LLMManager()
                provider = llm_manager.defaults.provider

                if conv_registry.conversation_exists(resolved_input.hash, provider):
                    self._console.print(
                        Banner(
                            f"Found existing conversation for this query (hash: {resolved_input.hash})"
                        )
                    )

                    while True:
                        choice = (
                            input(
                                "\nContinue existing conversation or start new? [c/n]: "
                            )
                            .strip()
                            .lower()
                        )
                        if choice in ["c", "continue"]:
                            # Load conversation and enter interactive mode directly
                            conversation = conv_registry.load_conversation(
                                resolved_input.hash, provider
                            )
                            print(
                                f"Continuing conversation from {conversation.started_at}"
                            )

                            # Get analysis results from registry to pass to interactive mode
                            from ..query_registry.query_registry import QueryRegistry

                            query_registry = QueryRegistry()
                            query_entry = query_registry.get_query(resolved_input.hash)

                            # We need to load the analysis results - for now use empty dict
                            # The conversation already has the context in the system messages
                            from .interactive_mode import run_interactive_mode

                            run_interactive_mode(conversation, {})

                            return RdstResult(True, "Interactive session completed")
                        elif choice in ["n", "new"]:
                            # Delete old conversation and continue to run analysis
                            conv_registry.delete_conversation(
                                resolved_input.hash, provider
                            )
                            print("Starting fresh conversation...")
                            break
                        else:
                            print("Please enter 'c' for continue or 'n' for new")

            # Check for unresolved parameter placeholders
            if has_unresolved_placeholders(resolved_input.sql):
                # First, check if we have stored parameters for this query
                from ..query_registry.query_registry import (
                    reconstruct_query_with_params,
                )

                existing_entry = self.registry.get_query(resolved_input.hash)
                stored_params = (
                    existing_entry.most_recent_params if existing_entry else None
                )

                if stored_params:
                    # We have stored parameters - use them automatically
                    substituted_sql = reconstruct_query_with_params(
                        resolved_input.normalized_sql or resolved_input.sql,
                        stored_params,
                    )
                    print(f"\nUsing stored parameters for query {resolved_input.hash}:")
                    print(
                        f"  {substituted_sql[:150]}{'...' if len(substituted_sql) > 150 else ''}"
                    )
                    print()

                    resolved_input = AnalyzeInput(
                        sql=substituted_sql,
                        normalized_sql=resolved_input.normalized_sql,
                        source=resolved_input.source,
                        hash=resolved_input.hash,
                        tag=resolved_input.tag,
                        save_as=resolved_input.save_as,
                    )
                else:
                    # No stored parameters - prompt the user
                    result = prompt_for_parameters(resolved_input.sql)

                    if result is None:
                        return RdstResult(
                            False, "Analysis cancelled - parameter values required"
                        )

                    substituted_sql, param_dict = result

                    # Update resolved_input with substituted SQL
                    resolved_input = AnalyzeInput(
                        sql=substituted_sql,
                        normalized_sql=resolved_input.normalized_sql,
                        source=resolved_input.source,
                        hash=resolved_input.hash,
                        tag=resolved_input.tag,
                        save_as=resolved_input.save_as,
                    )

                    # Store these parameters in the registry for future use
                    try:
                        self.registry.update_parameter_history(
                            query_hash=resolved_input.hash, parameters=param_dict
                        )
                    except Exception:
                        # Non-fatal - continue with analysis even if storage fails
                        pass

                    if not output_json:
                        print()

            # Backward compatibility: treat --readyset as alias for --readyset-cache.
            if readyset and not readyset_cache:
                readyset_cache = True

            # Check Docker availability upfront when --readyset-cache is enabled
            if readyset_cache:
                from ..functions.readyset_container import check_docker_available

                docker_status = check_docker_available()
                if not docker_status.get("success"):
                    error_msg = docker_status.get('error', 'Docker not available')
                    remediation = docker_status.get('remediation', '')
                    full_msg = f"--readyset-cache requires Docker: {error_msg}"
                    if remediation:
                        full_msg += f"\nHint: {remediation}"
                    return RdstResult(False, full_msg)

            # EXPLAIN ANALYZE safety warning (unless --skip-warning or --fast)
            showed_warning = False
            if not skip_warning and not fast and not output_json and sys.stdout.isatty():
                from lib.ui import Confirm

                self._console.print(
                    MessagePanel(
                        "This will run EXPLAIN ANALYZE against your database.\n"
                        "While this is a read-only operation (SELECT), it executes the full\n"
                        "query plan which could impact database performance for slow or\n"
                        "resource-intensive queries.\n\n"
                        "To suppress this prompt in the future:\n"
                        "  --skip-warning   Run without this confirmation\n"
                        "  --fast           Skip EXPLAIN ANALYZE entirely (schema-only analysis)",
                        variant="warning",
                        title="EXPLAIN ANALYZE Warning",
                    )
                )
                if not Confirm.ask("Continue with EXPLAIN ANALYZE?", default=True):
                    return RdstResult(False, "Analysis cancelled by user")
                showed_warning = True

            success, workflow_result, error_msg = asyncio.run(
                self._execute_analyze_async(
                    resolved_input=resolved_input,
                    target=target,
                    readyset_cache=readyset_cache,
                    fast=fast,
                    quiet=output_json,
                )
            )

            if not success:
                if output_json:
                    import json

                    error = error_msg or "Analysis failed"
                    payload = {"success": False, "error": error}
                    return RdstResult(False, json.dumps(payload, indent=2), data=payload)
                return RdstResult(False, error_msg or "Analysis failed")

            if not output_json and sys.stdout.isatty():
                print("\033[2J\033[H", end="", flush=True)

            from .output_formatter import format_analyze_output

            cfg = TargetsConfig()
            cfg.load()
            target_name = target or cfg.get_default()
            target_config = cfg.get(target_name) if target_name else {}
            workflow_result["target_config"] = target_config
            formatted_results = format_analyze_output(workflow_result)

            if output_json:
                import json

                result_data = dict(workflow_result)
                # Remove internal/sensitive keys from JSON output
                result_data.pop("target_config", None)
                return RdstResult(
                    True,
                    json.dumps(result_data, indent=2, default=str),
                    data=result_data,
                )

            if interactive:
                print(formatted_results)

                explain_results = workflow_result.get("explain_results", {})
                if explain_results and explain_results.get("success"):
                    self._handle_interactive_mode(
                        resolved_input=resolved_input,
                        target_name=target_name or "",
                        analysis_results=workflow_result,
                    )
                else:
                    error_msg = explain_results.get("error", "Unknown error")
                    self._console.print(
                        MessagePanel(
                            error_msg,
                            variant="error",
                            title="Cannot enter interactive mode: Query analysis failed",
                            hint="Please fix the query and try again.",
                        )
                    )

                return RdstResult(True, "")

            print(formatted_results)

            # Breadcrumb: remind users about --skip-warning if they saw the prompt
            if showed_warning:
                self._console.print(
                    f"\n[dim]Tip: Use --skip-warning to skip the EXPLAIN ANALYZE confirmation next time.[/dim]"
                )

            return RdstResult(True, "")

        except Exception as e:
            if output_json:
                import json

                payload = {"success": False, "error": str(e)}
                return RdstResult(False, json.dumps(payload, indent=2), data=payload)
            return RdstResult(False, f"analyze failed: {e}")

    def _handle_interactive_mode(
        self, resolved_input: AnalyzeInput, target_name: str, analysis_results: dict
    ) -> None:
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
            analysis_id=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            target=target_name,
            query_sql=resolved_input.sql,
        )

        # Build comprehensive analysis context for system message
        system_context = self._build_analysis_context(
            analysis_results, resolved_input.sql
        )
        conversation.add_message("system", system_context)

        # Save initial conversation
        conv_registry.save_conversation(conversation)

        # Enter interactive REPL
        run_interactive_mode(conversation, analysis_results)

    def _build_analysis_context(self, analysis_results: dict, query_sql: str) -> str:
        import json

        explain_results = analysis_results.get("explain_results", {})
        llm_analysis = analysis_results.get("llm_analysis", {})
        schema_collection = analysis_results.get("schema_collection", {})
        schema_info = (
            schema_collection.get("schema_info", {}) if schema_collection else {}
        )

        parts = []
        parts.append("# QUERY ANALYSIS RESULTS")
        parts.append("\n## Original Query")
        parts.append(f"```sql\n{query_sql}\n```")

        parts.append("\n## Performance Metrics")
        if explain_results:
            exec_time = explain_results.get("execution_time_ms", 0)
            rows_examined = explain_results.get("rows_examined", 0)
            rows_returned = explain_results.get("rows_returned", 0)
            parts.append(f"- Execution Time: {exec_time:.2f}ms")
            parts.append(f"- Rows Examined: {rows_examined:,}")
            parts.append(f"- Rows Returned: {rows_returned:,}")

        parts.append("\n## EXPLAIN ANALYZE Output")
        if explain_results and "raw_explain" in explain_results:
            parts.append(f"```\n{explain_results['raw_explain']}\n```")

        parts.append("\n## Database Schema")
        if schema_info:
            parts.append(f"```json\n{json.dumps(schema_info, indent=2)}\n```")

        parts.append("\n## AI Analysis & Recommendations")
        if llm_analysis:
            index_recs = llm_analysis.get("index_recommendations", [])
            if index_recs:
                parts.append("\n### Index Recommendations")
                for i, rec in enumerate(index_recs, 1):
                    parts.append(
                        f"\n{i}. **{rec.get('table', 'N/A')}.{rec.get('columns', [])}**"
                    )
                    parts.append(f"   - Rationale: {rec.get('rationale', 'N/A')}")
                    parts.append(f"   - SQL: `{rec.get('sql', 'N/A')}`")

            rewrite_sugs = llm_analysis.get("rewrite_suggestions", [])
            if rewrite_sugs:
                parts.append("\n### Query Rewrite Suggestions")
                for i, sug in enumerate(rewrite_sugs, 1):
                    parts.append(f"\n{i}. **{sug.get('type', 'N/A')}**")
                    parts.append(f"   - Description: {sug.get('description', 'N/A')}")
                    if sug.get("rewritten_query"):
                        parts.append(
                            f"   - Rewritten Query: ```sql\n{sug['rewritten_query']}\n```"
                        )

            hotspots = llm_analysis.get("hotspots", {})
            if hotspots:
                parts.append("\n### Performance Hotspots")
                parts.append(f"```json\n{json.dumps(hotspots, indent=2)}\n```")

        return "\n".join(parts)

    def _check_api_key_configured(self) -> Optional[str]:
        """Check if an API key is configured for Anthropic (Claude).

        RDST officially uses Claude/Anthropic for AI analysis.

        Returns:
            Error message if no API key configured, None if OK
        """
        try:
            # Check env vars first, then trial token from config
            key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("RDST_TRIAL_TOKEN")
            if key:
                return None

            # Check for active trial token (or detect exhausted trial)
            try:
                from ..llm_manager.key_resolution import resolve_api_key
                resolve_api_key()
                return None
            except Exception as trial_err:
                # Check if this is a trial exhaustion (not just missing key)
                err_code = getattr(trial_err, "code", None)
                if err_code == "TRIAL_EXHAUSTED":
                    return str(trial_err)
                pass

            return (
                "No LLM API key configured.\n\n"
                "Options:\n"
                "  1. Run 'rdst init' to sign up for a free trial (up to 925K tokens)\n"
                '  2. Set your own key: export ANTHROPIC_API_KEY="sk-ant-..."\n'
                "     Get one at: https://console.anthropic.com/"
            )

        except Exception as e:
            return f"Configuration error: {e}"

    async def _execute_analyze_async(
        self,
        resolved_input: AnalyzeInput,
        target: Optional[str] = None,
        readyset_cache: bool = False,
        fast: bool = False,
        quiet: bool = False,
    ) -> tuple[bool, dict, Optional[str]]:
        """Execute analysis using AnalyzeService async generator."""
        from lib.services.analyze_service import AnalyzeService
        from lib.services.types import (
            AnalyzeInput as ServiceAnalyzeInput,
            AnalyzeOptions,
            CompleteEvent,
            ErrorEvent,
        )
        from .analyze_renderer import AnalyzeRenderer, QuietRenderer

        service = AnalyzeService()
        renderer = QuietRenderer() if quiet else AnalyzeRenderer()

        input_data = ServiceAnalyzeInput(
            sql=resolved_input.sql,
            normalized_sql=resolved_input.normalized_sql,
            source=resolved_input.source,
            hash=resolved_input.hash,
            tag=resolved_input.tag,
            save_as=resolved_input.save_as,
        )

        options_data = AnalyzeOptions(
            target=target,
            fast=fast,
            readyset_cache=readyset_cache,
            test_rewrites=True,
            model=None,
        )

        last_event = None
        try:
            async for event in service.analyze(input_data, options_data):
                last_event = event
                renderer.render(event)
        finally:
            renderer.cleanup()

        if isinstance(last_event, CompleteEvent):
            readyset_payload = last_event.readyset_cacheability or {}
            readyset_analysis = {}
            if isinstance(readyset_payload, dict) and (
                "final_verdict" in readyset_payload
                or "explain_cache_result" in readyset_payload
                or "error" in readyset_payload  # Include error results
            ):
                readyset_analysis = readyset_payload

            result_context = {
                "success": True,
                "target": target,
                "query": resolved_input.sql,
                "normalized_query": resolved_input.normalized_sql,
                "parameterized_sql": resolved_input.normalized_sql,
                "query_hash": last_event.query_hash,
                "analysis_id": last_event.analysis_id,
                "explain_results": last_event.explain_results or {},
                "llm_analysis": last_event.llm_analysis or {},
                "rewrite_test_results": last_event.rewrite_testing or {},
                "readyset_analysis": readyset_analysis,
                "readyset_cacheability": last_event.readyset_cacheability or {},
                "FormatFinalResults": last_event.formatted or {},
                "storage_result": {"analysis_id": last_event.analysis_id},
            }
            return True, result_context, None
        elif isinstance(last_event, ErrorEvent):
            return False, {}, last_event.message
        else:
            return False, {}, "Analysis did not complete"

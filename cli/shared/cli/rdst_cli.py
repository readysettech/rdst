"""
Readyset CLI stubs (programmatic surface)

This module defines a small, modern-feeling programmatic interface for a future
`rdst` CLI. Each method returns a structured result and serves as a stub where
integration with cloud agent modules can be added.

No side-effects: Nothing executes long-running operations or requires external
services simply by importing this module.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Optional
import os

# Import UI system
from rich.console import Group

from shared.cli.types import RdstResult
from shared.config import (
    ENGINES,
    PROXY_TYPES,
    TargetsConfig,
    default_port_for,
    normalize_db_type,
    parse_connection_string,
)
from shared.ui import KeyValueTable, MessagePanel, SimpleTree, get_console

# Local cloud agent modules (will be used by future implementations)
# We import lazily inside methods to avoid side-effects and heavy imports at module load time.


class CloudAgentClient:
    """Lightweight accessor to cloud agent modules (lazy imports).

    This centralizes how the CLI accesses cloud agent functionality and keeps
    imports lazy to minimize side effects during CLI discovery.
    """

    def __init__(self):
        self._console = get_console()

    # Example accessors (add more as needed)
    def configuration_manager(self):  # -> ConfigurationManager
        from configuration_manager import ConfigurationManager  # local import

        return ConfigurationManager()

    def data_manager_service(self):  # -> DataManagerService
        from shared.data_manager_service.data_manager_service import (
            DataManagerService,
        )  # local import

        return DataManagerService

    def cache_manager(self):  # -> CacheManager
        # Note: CacheManager currently requires initialization context; defer wiring
        cache_manager_module = import_module("features.cache.cache_manager")

        return cache_manager_module.CacheManager

    def llm_manager(self):  # -> LLMManager
        from shared.llm_manager import LLMManager  # local import

        return LLMManager()

    def print_panel(self, title: str, message: str):
        self._console.print(MessagePanel(message, title=title))


class RdstCLI:
    """Stubs for rdst commands. Each returns RdstResult and shows intended integrations."""

    def __init__(self, client: Optional[CloudAgentClient] = None):
        self.client = client or CloudAgentClient()

    # rdst configure
    def configure(self, config_path: Optional[str] = None, **kwargs) -> RdstResult:
        """Manages database targets and connection profiles."""
        try:
            from features.configure.cli.command import ConfigureCommand

            subcmd = (kwargs.get("subcommand") or "menu").lower()

            cfg = TargetsConfig()
            cfg.load()

            if subcmd == "llm":
                from features.configure.cli.wizard import ConfigurationWizard

                wizard = ConfigurationWizard(console=self.client._console)
                return wizard.configure_llm(cfg, kwargs)

            command = ConfigureCommand(client=self.client)
            return command.execute(config_path=config_path, **kwargs)

        except Exception as e:
            return RdstResult(False, f"configure failed: {e}")

    def _test_connection(self, cfg: TargetsConfig, kwargs: dict) -> RdstResult:
        """Test database connection for a target. Returns JSON-formatted result."""
        import json

        target_name = kwargs.get("target") or kwargs.get("name")

        # If no target specified, use default
        if not target_name:
            target_name = cfg.get_default()
            if not target_name:
                result = {
                    "success": False,
                    "error": "No target specified and no default target configured",
                }
                return RdstResult(False, json.dumps(result, indent=2))

        # Get target configuration
        target_config = cfg.get(target_name)
        if not target_config:
            result = {
                "success": False,
                "error": f"Target '{target_name}' not found in configuration",
            }
            return RdstResult(False, json.dumps(result, indent=2))

        # Extract connection parameters
        engine = target_config.get("engine", "").lower()
        host = target_config.get("host", "localhost")
        port = target_config.get("port")
        user = target_config.get("user", "postgres")
        database = target_config.get("database", "postgres")
        from shared.password_resolver import resolve_password_value
        password = resolve_password_value(target_config)
        password_env = target_config.get("password_env", "")
        if password_env and not password:
            result = {
                "success": False,
                "target": target_name,
                "error": f"Password environment variable '{password_env}' is not set",
            }
            return RdstResult(False, json.dumps(result, indent=2))

        # Test connection based on engine
        try:
            if engine == "postgresql":
                import psycopg2

                conn = psycopg2.connect(
                    host=host,
                    port=port or 5432,
                    user=user,
                    password=password,
                    database=database,
                    connect_timeout=10,
                )
                cursor = conn.cursor()
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
                cursor.close()
                conn.close()

                result = {
                    "success": True,
                    "target": target_name,
                    "engine": engine,
                    "host": host,
                    "port": port or 5432,
                    "database": database,
                    "server_version": version,
                }
                return RdstResult(True, json.dumps(result, indent=2))

            elif engine == "mysql":
                import pymysql

                conn = pymysql.connect(
                    host=host,
                    port=port or 3306,
                    user=user,
                    password=password,
                    database=database,
                    connect_timeout=10,
                )
                cursor = conn.cursor()
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
                cursor.close()
                conn.close()

                result = {
                    "success": True,
                    "target": target_name,
                    "engine": engine,
                    "host": host,
                    "port": port or 3306,
                    "database": database,
                    "server_version": version,
                }
                return RdstResult(True, json.dumps(result, indent=2))

            else:
                result = {"success": False, "error": f"Unsupported engine: {engine}"}
                return RdstResult(False, json.dumps(result, indent=2))

        except Exception as e:
            error_msg = str(e)
            # Provide helpful hints for common errors
            hints = []
            if (
                "authentication failed" in error_msg.lower()
                or "access denied" in error_msg.lower()
            ):
                hints.append("Check that your password is correct")
                hints.append(
                    f"Verify the password environment variable '{password_env}' is set correctly"
                )
            elif (
                "could not connect" in error_msg.lower()
                or "connection refused" in error_msg.lower()
            ):
                hints.append(
                    f"Check that the database server is running on {host}:{port or (5432 if engine == 'postgresql' else 3306)}"
                )
                hints.append("Verify the host and port are correct")
            elif "does not exist" in error_msg.lower():
                hints.append(f"Check that the database '{database}' exists")

            result = {
                "success": False,
                "target": target_name,
                "engine": engine,
                "host": host,
                "port": port or (5432 if engine == "postgresql" else 3306),
                "error": error_msg,
                "hints": hints if hints else None,
            }
            return RdstResult(False, json.dumps(result, indent=2))

    # rdst top
    def top(
        self,
        target: str = None,
        source: str = "auto",
        limit: int = 10,
        sort: str = "total_time",
        filter: str = None,
        json: bool = False,
        watch: bool = False,
        no_color: bool = False,
        **kwargs,
    ) -> RdstResult:
        """Live view of top slow queries from database telemetry.

        Telemetry: `command_run_sync` lives inside `TopCommand.execute`
        (per-feature CLI layer) so the event-name split between
        historical (`top_run`) and realtime (`top_realtime_run`) mirrors
        the web side without leaking into this dispatcher.
        """
        from features.top.cli.command import TopCommand

        top_command = TopCommand(client=self.client)
        return top_command.execute(
            target, source, limit, sort, filter, json, watch, no_color, **kwargs
        )

    # rdst analyze
    def analyze(
        self,
        hash: Optional[str] = None,
        query: Optional[str] = None,
        file: Optional[str] = None,
        stdin: bool = False,
        name: Optional[str] = None,
        positional_query: Optional[str] = None,
        target: Optional[str] = None,
        save_as: Optional[str] = None,
        fast: bool = False,
        interactive: bool = False,
        review: bool = False,
        output_json: bool = False,
        skip_warning: bool = False,
        **kwargs,
    ) -> RdstResult:
        """
        Analyze SQL query with support for multiple input modes.

        Supports input from:
        - Registry by hash (--hash)
        - Registry by name (--name)
        - Inline query (-q/--query)
        - File (-f/--file)
        - Stdin (--stdin)
        - Interactive prompt (fallback)
        - Positional argument (backward compatibility)

        Args:
            hash: Query hash from registry
            query: SQL query string from -q flag
            file: Path to SQL file from -f flag
            stdin: Whether to read from stdin
            name: Query name for registry lookup
            positional_query: Positional query argument
            target: Target database
            save_as: Name to save query as after analysis
            fast: Whether to skip EXPLAIN ANALYZE and use EXPLAIN only
            interactive: Whether to enter interactive mode after analysis
            review: Whether to review conversation history instead of analyzing
            **kwargs: Additional arguments

        Returns:
            RdstResult with analysis results
        """
        # Thin dispatcher. Telemetry CM, target fallback, and the
        # first-analyze feedback prompt all live inside
        # `AnalyzeCommand.execute_analyze`, so every caller — direct
        # `rdst analyze`, `rdst top → analyze`, `rdst query → analyze` —
        # gets identical behavior.
        from features.analyze.cli.command import AnalyzeCommand, AnalyzeInputError

        analyze_cmd = AnalyzeCommand(client=self.client)
        try:
            resolved_input = analyze_cmd.resolve_input(
                hash=hash,
                inline_query=query,
                file_path=file,
                use_stdin=stdin,
                name=name,
                positional_query=positional_query,
                save_as=save_as,
            )
        except AnalyzeInputError as e:
            return RdstResult(False, str(e))

        return analyze_cmd.execute_analyze(
            resolved_input,
            target=analyze_cmd.resolve_target(target, resolved_input),
            fast=fast,
            interactive=interactive,
            review=review,
            output_json=output_json,
            skip_warning=skip_warning,
        )

    # rdst init
    def init(self, **kwargs) -> RdstResult:
        """First-time guided setup (init)."""
        try:
            # Determine interactivity and force flags from kwargs
            force = bool(kwargs.get("force", False))
            interactive = kwargs.get("interactive", None)
            # Run the init command
            from features.init.cli.command import InitCommand

            wizard = InitCommand(console=self.client._console, cli=self)
            return wizard.run(force=force, interactive=interactive)
        except Exception as e:
            return RdstResult(False, f"init failed: {e}")

    # rdst query - query registry management
    def query(self, subcommand: str, **kwargs) -> RdstResult:
        """
        Manage query registry: add, edit, list, show, delete queries.

        This is separate from analysis - purely for query management.

        Args:
            subcommand: One of: add, edit, list, show, delete, rm
            **kwargs: Subcommand-specific arguments

        Returns:
            RdstResult with operation outcome
        """
        import asyncio

        try:
            from features.query_registry.events import QueryCompleteEvent, QueryErrorEvent
            from features.query_registry.models import QueryCommandInput
            from features.query_registry.service import QueryService
            from features.query_registry.cli.renderer import QueryRenderer

            service = QueryService()
            renderer = QueryRenderer()
            complete_event = None
            error_event = None

            async def _run() -> None:
                nonlocal complete_event, error_event
                try:
                    async for event in service.execute(
                        QueryCommandInput(subcommand=subcommand, kwargs=kwargs)
                    ):
                        renderer.render(event)
                        if isinstance(event, QueryCompleteEvent):
                            complete_event = event
                        elif isinstance(event, QueryErrorEvent):
                            error_event = event
                finally:
                    renderer.cleanup()

            asyncio.run(_run())

            if complete_event:
                payload = complete_event.result
                return RdstResult(
                    bool(payload.get("ok", complete_event.success)),
                    payload.get("message", ""),
                    payload.get("data") or {},
                )
            if error_event:
                # QueryRenderer already printed the error to the console.
                # Return empty message so rdst.py main() does not print it again.
                return RdstResult(False, "")
            return RdstResult(False, "query command returned no result")
        except Exception as e:
            return RdstResult(False, f"query command failed: {e}")

    # rdst help / rdst version
    def help(self) -> RdstResult:
        """Display a friendly welcome/help page."""
        banner = (
            "\n"
            "==============================================\n"
            "  Readyset Data and SQL Toolkit (rdst)\n"
            "==============================================\n"
        )
        intro = (
            "Troubleshoot latency, analyze queries, and get tuning insights.\n"
            "\n"
            "Common commands:\n"
            "  - rdst init             Set up rdst for first use\n"
            "  - rdst configure        Manage database targets and connection profiles\n"
            "  - rdst top              Monitor slow queries in real-time\n"
            "  - rdst analyze          Analyze SQL query performance\n"
            "  - rdst ask              Ask questions about your database in natural language\n"
            "  - rdst agent            Manage and run data agents with safety policies\n"
            "  - rdst query            Manage saved queries (add/list/delete)\n"
            "  - rdst schema           Manage semantic layer for your database\n"
            "  - rdst cache            Deploy and manage Readyset shallow caches\n"
            "  - rdst fleet            Manage and audit database fleets\n"
            "  - rdst audit            Run a deep health audit of a database target\n"
            "  - rdst guard            Manage reusable safety policies\n"
            "  - rdst scan             Scan codebase for ORM queries (experimental)\n"
            "  - rdst report           Submit feedback or bug reports\n"
            "  - rdst version          Show version information\n"
            "\n"
            "Examples:\n"
            "  rdst init\n"
            "  rdst configure add --target prod --host db.example.com --user admin\n"
            '  rdst analyze -q "SELECT * FROM users WHERE active = true" --target mydb\n'
            '  rdst help "how do I find slow queries?"\n'
        )
        return RdstResult(True, f"{banner}{intro}")

    def version(self) -> RdstResult:
        """Report CLI/library version."""
        try:
            from importlib.metadata import version as get_version

            pkg_version = get_version("rdst")
        except Exception:
            # Fallback to _version.py if package metadata not available
            try:
                from _version import __version__

                pkg_version = __version__
            except Exception:
                pkg_version = "unknown"

        return RdstResult(
            True, f"Readyset Data and SQL Toolkit (rdst) version {pkg_version}"
        )

    # rdst report
    def report(self, title: str, body: str = "", **kwargs) -> RdstResult:
        """Stub: Submit feedback or bug reports from within the CLI."""
        if not title:
            return RdstResult(False, "report requires a title")
        msg = "Report stub – would submit feedback via control plane API."
        return RdstResult(True, msg, data={"title": title, "body": body})

    # ============================================================================
    # RDST ASK - Text-to-SQL with hybrid linear + agent architecture
    # NOTE: Not yet exposed in CLI - internal API only
    # ============================================================================
    def ask(
        self,
        question: Optional[str] = None,
        target: Optional[str] = None,
        dry_run: bool = False,
        timeout: int = 30,
        verbose: bool = False,
        agent_mode: bool = False,
        no_interactive: bool = False,
        **kwargs,
    ) -> RdstResult:
        """
        Generate SQL from natural language using hybrid linear + agent architecture.

        Uses a fast linear flow (schema → filter → clarify → generate → validate → execute)
        for most queries, with automatic escalation to an intelligent agent for complex cases.

        The agent can:
        - Explore the schema iteratively
        - Sample data to understand semantics
        - Ask the user clarifying questions
        - Refine its approach based on observations

        Args:
            question: Natural language question (if None, prompt user interactively)
            target: Target database name (if None, use default)
            dry_run: Generate SQL but don't execute (default: False)
            timeout: Query timeout in seconds (default: 30)
            verbose: Show detailed information
            agent_mode: Skip linear flow and go directly to agent exploration
            no_interactive: Skip clarification prompts, use first interpretation
            **kwargs: Additional parameters

        Returns:
            RdstResult with generated SQL, execution results, and metadata

        Examples:
            # Basic usage
            rdst ask "Show me the top 10 customers by revenue"

            # Dry run (generate but don't execute)
            rdst ask "Count active users" --dry-run

            # Direct agent mode for complex queries
            rdst ask "Find users who give the most downvotes" --agent

            # Verbose output
            rdst ask "Show slow queries" --verbose
        """
        # Thin dispatcher — input resolution, telemetry CM, and the
        # ask service plumbing all live in
        # `features/ask/cli/command.py:AskCommand`, mirroring
        # analyze/top/scan and the web side at
        # `features/ask/api/routes.py`. The import itself is wrapped to
        # catch ImportError at the dispatcher boundary so an unavailable
        # ask feature surfaces a friendly message rather than a raw
        # traceback.
        try:
            from features.ask.cli.command import AskCommand
        except ImportError as import_err:
            return RdstResult(
                False,
                "The 'ask' command is not available. "
                "Some required components could not be loaded. "
                "Try reinstalling rdst or check your installation. "
                f"(detail: {import_err})",
            )

        return AskCommand().execute(
            question=question,
            target=target,
            dry_run=dry_run,
            timeout=timeout,
            verbose=verbose,
            agent_mode=agent_mode,
            no_interactive=no_interactive,
            **kwargs,
        )

    # ============================================================================
    # RDST SCHEMA - Semantic layer management
    # NOTE: Not yet exposed in CLI - internal API only
    # ============================================================================
    def schema(
        self, subcommand: str = None, target: str = None, **kwargs
    ) -> RdstResult:
        """
        Manage semantic layer for better SQL generation.

        Args:
            subcommand: One of: show, init, edit, annotate, export, delete, list, add-table, add-term
            target: Target database name
            **kwargs: Subcommand-specific arguments

        Returns:
            RdstResult with operation outcome
        """
        import asyncio

        try:
            from features.schema.cli.command import SchemaCommand
            from features.schema.cli.renderer import SchemaRenderer
            from features.schema.events import SchemaCompleteEvent, SchemaErrorEvent
            from features.schema.models import SchemaInitOptions
            from features.schema.service import SchemaService

            schema_cmd = SchemaCommand()
            service = SchemaService()
            renderer = SchemaRenderer()

            # Interactive menu if no subcommand provided
            if not subcommand:
                import sys

                if not sys.stdin.isatty():
                    return RdstResult(
                        False,
                        "Schema command requires a subcommand: show, init, edit, annotate, export, delete, list\nTry: rdst schema --help",
                    )
                from shared.ui import SelectPrompt

                options = [
                    "show - Display semantic layer",
                    "init - Initialize from database",
                    "annotate - Add descriptions",
                    "edit - Edit in $EDITOR",
                ]
                try:
                    choice = SelectPrompt.ask(
                        "Schema subcommands:", options, default=1, allow_cancel=True
                    )
                except (EOFError, KeyboardInterrupt):
                    return RdstResult(False, "Cancelled")
                if choice is None:
                    return RdstResult(False, "Cancelled")
                subcommand = ["show", "init", "annotate", "edit"][choice - 1]

            # Keep interactive/editor-only flows on legacy command implementation.
            if subcommand in ("edit", "annotate"):
                if not target:
                    target = self._get_default_target()
                    if not target:
                        return RdstResult(
                            False,
                            "No target specified and no default target configured.",
                        )

                if subcommand == "edit":
                    result = schema_cmd.edit(target, kwargs.get("table"))
                else:
                    table = kwargs.get("table")
                    use_llm = kwargs.get("use_llm", False)
                    auto_accept = kwargs.get("auto_accept", False)
                    sample_rows = kwargs.get("sample_rows", 5)
                    target_config = self._get_target_config(target)
                    # Validate target exists before entering wizard
                    if not target_config:
                        return RdstResult(
                            False,
                            f"Target '{target}' not found. Run 'rdst configure add' to set one up.",
                        )
                    if use_llm and not target_config:
                        return RdstResult(
                            False,
                            f"Target '{target}' not found. Run 'rdst configure add' to set one up.",
                        )
                    if auto_accept and not use_llm:
                        return RdstResult(
                            False,
                            "--auto-accept requires --use-llm.",
                        )
                    result = schema_cmd.annotate(
                        target, table, use_llm=use_llm,
                        auto_accept=auto_accept,
                        sample_rows=sample_rows, target_config=target_config,
                    )
                return RdstResult(bool(result.get("ok")), result.get("message", ""))

            if subcommand != "list" and not target:
                target = self._get_default_target()
                if not target:
                    return RdstResult(
                        False,
                        "No target specified and no default target configured. "
                        "Use --target or run 'rdst configure add' to set one up.",
                    )

            complete_event = None
            error_event = None

            async def _consume(generator):
                nonlocal complete_event, error_event
                async for event in generator:
                    renderer.render(event)
                    if isinstance(event, SchemaCompleteEvent):
                        complete_event = event
                    elif isinstance(event, SchemaErrorEvent):
                        error_event = event

            if subcommand == "show":
                # Check that the target is configured before checking for semantic layer.
                # This gives a clearer error when the target itself doesn't exist.
                target_cfg = self._get_target_config(target)
                if not target_cfg and not service._manager.exists(target):
                    return RdstResult(
                        False,
                        f"Target '{target}' not found. Run 'rdst configure add' to set one up.",
                    )
                asyncio.run(
                    _consume(service.get_schema_events(target, kwargs.get("table")))
                )
            elif subcommand == "init":
                target_config = self._get_target_config(target)
                if not target_config:
                    return RdstResult(
                        False,
                        f"Target '{target}' not found. Run 'rdst configure add' to set one up.",
                    )
                if kwargs.get("interactive", False):
                    # keep interactive enum flow on legacy command
                    result = schema_cmd.init(
                        target,
                        target_config,
                        kwargs.get("enum_threshold", 20),
                        kwargs.get("force", False),
                        True,
                    )
                    return RdstResult(bool(result.get("ok")), result.get("message", ""))

                options = SchemaInitOptions(
                    enum_threshold=kwargs.get("enum_threshold", 20),
                    force=kwargs.get("force", False),
                    sample_enums=True,
                )
                asyncio.run(
                    _consume(service.init_events(target, target_config, options))
                )
            elif subcommand == "export":
                export_target_config = self._get_target_config(target)
                if not export_target_config:
                    return RdstResult(
                        False,
                        f"Target '{target}' not found. Run 'rdst configure add' to set one up.",
                    )
                asyncio.run(
                    _consume(
                        service.export_events(
                            target, kwargs.get("output_format", "yaml")
                        )
                    )
                )
            elif subcommand == "delete":
                # Validate target and schema existence before prompting
                delete_target_config = self._get_target_config(target)
                if not delete_target_config:
                    return RdstResult(
                        False,
                        f"Target '{target}' not found. Run 'rdst configure add' to set one up.",
                    )
                if not service._manager.exists(target):
                    return RdstResult(
                        False,
                        f"No semantic layer found for '{target}'. Run 'rdst schema init' first.",
                    )
                force = kwargs.get("force", False)
                if not force:
                    try:
                        confirm = input(f"Delete semantic layer for '{target}'? [y/N] ")
                        if confirm.lower() != "y":
                            return RdstResult(False, "Cancelled")
                    except EOFError:
                        return RdstResult(
                            False,
                            "Cannot prompt for confirmation in non-interactive mode. Use --force",
                        )
                asyncio.run(_consume(service.delete_events(target)))
            elif subcommand == "list":
                asyncio.run(_consume(service.list_targets_events()))
            elif subcommand == "refresh":
                # Keep refresh on the legacy command for now (not implemented as a service event stream).
                target_config = self._get_target_config(target)
                if not target_config:
                    return RdstResult(
                        False,
                        f"Target '{target}' not found. Run 'rdst configure add' to set one up.",
                    )

                result = schema_cmd.refresh(target, target_config)
                return RdstResult(bool(result.get("ok")), result.get("message", ""))
            elif subcommand == "profile":
                target_config = self._get_target_config(target)
                if not target_config:
                    return RdstResult(
                        False,
                        f"Target '{target}' not found. Run 'rdst configure add' to set one up.",
                    )
                if not schema_cmd.manager.exists(target):
                    return RdstResult(
                        False,
                        f"No semantic layer for '{target}'. Run 'rdst schema init' first.",
                    )
                result = schema_cmd.profile(target, target_config, kwargs.get("table"))
                return RdstResult(bool(result.get("ok")), result.get("message", ""))
            elif subcommand == "add-table":
                result = service.add_table(
                    target,
                    kwargs.get("table"),
                    kwargs.get("description", ""),
                    kwargs.get("context", ""),
                )
                return RdstResult(
                    bool(result.success), result.message or result.error or ""
                )
            elif subcommand == "add-term":
                result = service.add_terminology(
                    target,
                    kwargs.get("term"),
                    kwargs.get("definition", ""),
                    kwargs.get("sql_pattern", ""),
                )
                return RdstResult(
                    bool(result.success), result.message or result.error or ""
                )
            else:
                return RdstResult(False, f"Unknown schema subcommand: {subcommand}")

            if error_event:
                # The renderer already displayed the error to the console via Rich,
                # so return an empty message to avoid a duplicate print in rdst.py main().
                return RdstResult(False, "")
            if not complete_event:
                return RdstResult(False, "schema command returned no result")

            if subcommand == "export" and complete_event.export_result:
                return RdstResult(True, complete_event.export_result.content)
            if subcommand == "list" and complete_event.target_list:
                targets = complete_event.target_list.targets
                if not targets:
                    return RdstResult(True, "No semantic layers found")
                lines = [f"Found {len(targets)} semantic layer(s):\n"]
                for t in targets:
                    updated = t.updated_at or "unknown"
                    lines.append(
                        f"  {t.name:<20s}  {t.tables} table(s), "
                        f"{t.terminology} term(s), updated {updated}"
                    )
                return RdstResult(True, "\n".join(lines))
            if subcommand == "show":
                return RdstResult(True, "")
            if subcommand == "init" and complete_event.init_result:
                return RdstResult(
                    bool(complete_event.init_result.success),
                    ""
                    if complete_event.init_result.success
                    else (complete_event.init_result.error or ""),
                )
            if subcommand == "delete" and complete_event.delete_result:
                if complete_event.delete_result.success:
                    return RdstResult(True, f"Deleted semantic layer for '{complete_event.delete_result.target}'")
                return RdstResult(False, complete_event.delete_result.error or "Delete failed")
            return RdstResult(True, "")
        except Exception as e:
            return RdstResult(False, f"schema command failed: {e}")

    def _format_schema_show(self, data: dict) -> str:
        """Format schema show output for display."""
        console = get_console()
        renderables: list[Any] = []

        summary = data.get("summary", {})
        renderables.append(
            KeyValueTable(
                {
                    "Tables": summary.get("tables", 0),
                    "Columns": summary.get("columns", 0),
                    "Terminology": summary.get("terminology", 0),
                },
                title="Summary",
            )
        )

        tables = data.get("tables", {})
        if tables:
            tree = SimpleTree("Tables")
            for name, table in tables.items():
                desc = table.get("description", "No description")
                table_node = tree.add(f"{name}: {desc}")
                if table.get("columns"):
                    for col_name, col in table["columns"].items():
                        col_desc = col.get("description", "")
                        col_type = col.get("type", "")
                        if col.get("enum_values"):
                            enum_preview = list(col["enum_values"].keys())[:3]
                            col_type = f"enum({', '.join(enum_preview)}...)"
                        table_node.add(f"{col_name} ({col_type}): {col_desc}")
            renderables.append(tree)

        terminology = data.get("terminology", {})
        if terminology:
            term_tree = SimpleTree("Terminology")
            for term, info in terminology.items():
                term_tree.add(f"{term}: {info.get('definition', '')}")
            renderables.append(term_tree)

        with console.capture() as capture:
            console.print(Group(*renderables))
        return capture.get().rstrip()

    def _get_default_target(self) -> str:
        """Get the default target from config."""
        try:
            cfg = TargetsConfig()
            cfg.load()
            return cfg.get_default() or ""
        except Exception:
            return ""

    def _get_target_config(self, target: str) -> dict:
        """Get target configuration by name."""
        try:
            cfg = TargetsConfig()
            cfg.load()
            return cfg.get(target) or {}
        except Exception:
            return {}


# Ready-to-use singleton for simple imports.
rdst = RdstCLI()

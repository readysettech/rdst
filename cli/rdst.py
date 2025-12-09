#!/usr/bin/env python3
"""
rdst - Readyset Diagnostics & SQL Tuning

A command-line interface for diagnostics, query analysis, performance tuning,
and caching with Readyset.
"""
import sys
import argparse
from typing import List, Dict, Any, Optional

# Optional pretty output
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    _RICH_AVAILABLE = True
except Exception:  # pragma: no cover
    Console = None  # type: ignore
    Panel = None  # type: ignore
    Table = None  # type: ignore
    Text = None  # type: ignore
    _RICH_AVAILABLE = False

# Import the CLI functionality
from lib.cli import RdstCLI, RdstResult


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog='rdst',
        description='Readyset Diagnostics & SQL Tuning - Diagnose, analyze, and tune SQL performance',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  configure     Manage database targets and connection profiles
  top          Live view of top slow queries
  analyze      Analyze and explain SQL queries
  tune         Get optimization suggestions for queries
  init         First-time setup wizard
  tag          Tag and store queries for later reference
  list         Show saved queries
  version      Show version information
  report       Submit feedback or bug reports
  help         Show detailed help

Examples:
  rdst configure add --target prod --host db.example.com --user admin
  rdst configure list
  rdst analyze "SELECT * FROM users WHERE active = true"
  rdst analyze "SELECT COUNT(*) FROM orders WHERE status = 'pending'" --readyset-cache
  rdst tune "SELECT u.name, p.title FROM users u JOIN posts p ON u.id = p.user_id"
  rdst top --limit 10
        """
    )

    # Add global options
    parser.add_argument(
        '--config',
        help='Path to configuration file'
    )

    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose output'
    )

    # Subcommands
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # configure command
    configure_parser = subparsers.add_parser('configure', help='Manage database targets')
    configure_parser.add_argument('subcommand', nargs='?', default='menu',
                                  help='Subcommand: menu (default), add, edit, list, remove, default')
    configure_parser.add_argument('name', nargs='?', help='Target name for edit/remove/default')
    configure_parser.add_argument('--target', '--name', help='Target name')
    configure_parser.add_argument('--engine', choices=['postgresql', 'mysql'], help='Database engine')
    configure_parser.add_argument('--host', help='Database host')
    configure_parser.add_argument('--port', type=int, help='Database port')
    configure_parser.add_argument('--user', help='Database user')
    configure_parser.add_argument('--database', help='Database name')
    configure_parser.add_argument('--password-env', help='Environment variable for password')
    configure_parser.add_argument('--read-only', action='store_true', help='Read-only connection')
    configure_parser.add_argument('--proxy', choices=['none', 'readyset', 'proxysql', 'pgbouncer', 'tunnel', 'custom'],
                                  help='Proxy type')
    configure_parser.add_argument('--tls', action='store_true', help='Enable TLS')
    configure_parser.add_argument('--no-tls', action='store_true', help='Disable TLS')
    configure_parser.add_argument('--default', action='store_true', help='Set as default target')
    configure_parser.add_argument('--confirm', action='store_true', help='Confirm removal without prompting')

    # top command
    top_parser = subparsers.add_parser('top', help='Live view of slow queries')
    top_parser.add_argument('--target', help='Specific configured DB target')
    top_parser.add_argument('--source', choices=['auto', 'pg_stat', 'activity', 'slowlog', 'digest', 'rds', 'pmm'], 
                           default='auto', help='Telemetry source to use')
    top_parser.add_argument('--limit', type=int, default=10, help='Number of queries to show')
    top_parser.add_argument('--sort', choices=['freq', 'total_time', 'avg_time', 'load'], 
                           default='total_time', help='Sort field')
    top_parser.add_argument('--filter', help='Regex to filter query text')
    top_parser.add_argument('--json', action='store_true', help='Output machine-readable JSON')
    top_parser.add_argument('--watch', action='store_true', help='Continuously refresh the view')
    top_parser.add_argument('--interactive', action='store_true', help='Interactive mode to select queries for analysis')
    top_parser.add_argument('--no-color', action='store_true', help='Disable ANSI color formatting')
    top_parser.add_argument('--historical', action='store_true', help='Use historical statistics (pg_stat_statements/performance_schema) instead of real-time monitoring')
    top_parser.add_argument('--duration', type=int, help='Run real-time Top for N seconds then output results (snapshot mode, non-interactive)')

    # analyze command
    analyze_parser = subparsers.add_parser('analyze', help='Analyze SQL query')

    # Query input modes (mutually exclusive group)
    query_group = analyze_parser.add_mutually_exclusive_group()
    query_group.add_argument('-q', '--query', dest='inline_query', help='SQL query to analyze (use quotes for multiline)')
    query_group.add_argument('-f', '--file', help='Read SQL from file (supports multiline)')
    query_group.add_argument('--stdin', action='store_true', help='Read SQL from stdin (e.g., echo "SELECT..." | rdst analyze --stdin)')
    query_group.add_argument('--hash', dest='hash', help='Load query by hash from registry')
    query_group.add_argument('--name', help='Load query by name from registry')

    # Backward compatibility: positional query argument (lowest precedence)
    analyze_parser.add_argument('query', nargs='?', help='SQL query to analyze (fallback)')

    # Other options
    analyze_parser.add_argument('--target', help='Target database')
    analyze_parser.add_argument('--save-as', help='Name to save query as after analysis')
    analyze_parser.add_argument('--readyset', action='store_true', help='Run analysis against local Readyset Docker container')
    analyze_parser.add_argument('--readyset-cache', action='store_true', dest='readyset_cache', help='Evaluate ReadySet caching with performance comparison')
    analyze_parser.add_argument('--fast', action='store_true', help='Auto-skip slow EXPLAIN ANALYZE queries after 10 seconds (for testing)')
    analyze_parser.add_argument('--interactive', action='store_true', help='Enter interactive mode after analysis for Q&A about recommendations')
    analyze_parser.add_argument('--review', action='store_true', help='Review conversation history for this query without re-analyzing')
    analyze_parser.add_argument('--workload', action='store_true', help='Analyze multiple queries together for holistic index recommendations (coming soon)')

    # tune command
    tune_parser = subparsers.add_parser('tune', help='Get optimization suggestions')
    tune_parser.add_argument('query', help='SQL query to tune')

    # init command
    init_parser = subparsers.add_parser('init', help='First-time setup wizard')
    init_parser.add_argument('--force', action='store_true', help='Re-run setup even if config exists')
    init_parser.add_argument('--interactive', action='store_true', help='Force interactive mode')

    # query command - query registry management
    query_parser = subparsers.add_parser('query', help='Manage query registry (add/edit/list/delete queries)')
    query_subparsers = query_parser.add_subparsers(dest='query_subcommand', help='Query subcommands')

    # query add
    query_add_parser = query_subparsers.add_parser('add', help='Add a new query to registry')
    query_add_parser.add_argument('query_name', help='Name for the query')
    query_add_parser.add_argument('-q', '--query', help='Inline SQL query (optional, will open $EDITOR if not provided)')
    query_add_parser.add_argument('-f', '--file', help='Read SQL from file')
    query_add_parser.add_argument('--target', help='Target database name')

    # query import
    query_import_parser = query_subparsers.add_parser('import', help='Import multiple queries from SQL file')
    query_import_parser.add_argument('file', help='Path to SQL file containing multiple queries')
    query_import_parser.add_argument('--update', action='store_true', help='Update existing queries instead of skipping')
    query_import_parser.add_argument('--target', help='Default target database for queries without target comment')

    # query edit
    query_edit_parser = query_subparsers.add_parser('edit', help='Edit an existing query')
    query_edit_group = query_edit_parser.add_mutually_exclusive_group(required=True)
    query_edit_group.add_argument('query_name', nargs='?', help='Query name to edit')
    query_edit_group.add_argument('--hash', help='Query hash to edit')

    # query list
    query_list_parser = query_subparsers.add_parser('list', help='List all queries (interactive by default)')
    query_list_parser.add_argument('--limit', type=int, default=10, help='Queries per page (default: 10)')
    query_list_parser.add_argument('--target', help='Filter queries by target database')
    query_list_parser.add_argument('--filter', help='Smart filter: search across SQL, tags, hash, source')
    query_list_parser.add_argument('--no-interactive', action='store_true', help='Plain text output without selection prompt')

    # query show
    query_show_parser = query_subparsers.add_parser('show', help='Show details of a specific query')
    query_show_parser.add_argument('query_name', help='Query name to show')

    # query delete/rm
    query_delete_parser = query_subparsers.add_parser('delete', help='Delete a query from registry')
    query_delete_group = query_delete_parser.add_mutually_exclusive_group(required=True)
    query_delete_group.add_argument('query_name', nargs='?', help='Query name to delete')
    query_delete_group.add_argument('--hash', help='Query hash to delete')
    query_delete_parser.add_argument('--force', action='store_true', help='Skip confirmation prompt')

    # query rm (alias for delete)
    query_rm_parser = query_subparsers.add_parser('rm', help='Delete a query from registry (alias for delete)')
    query_rm_group = query_rm_parser.add_mutually_exclusive_group(required=True)
    query_rm_group.add_argument('query_name', nargs='?', help='Query name to delete')
    query_rm_group.add_argument('--hash', help='Query hash to delete')
    query_rm_parser.add_argument('--force', action='store_true', help='Skip confirmation prompt')

    # version command
    subparsers.add_parser('version', help='Show version')

    # report command - user feedback
    report_parser = subparsers.add_parser('report', help='Submit feedback about RDST')
    report_parser.add_argument('--hash', help='Query hash to provide feedback on')
    report_parser.add_argument('--reason', '-r', help='Feedback reason (interactive if not provided)')
    report_parser.add_argument('--email', '-e', help='Email for follow-up (optional)')
    report_parser.add_argument('--positive', action='store_true', help='Mark as positive feedback')
    report_parser.add_argument('--negative', action='store_true', help='Mark as negative feedback')
    report_parser.add_argument('--include-query', action='store_true', help='Include raw SQL in feedback')
    report_parser.add_argument('--include-plan', action='store_true', help='Include execution plan in feedback')

    # help command
    subparsers.add_parser('help', help='Show help')

    return parser.parse_args()


def execute_command(cli: RdstCLI, args: argparse.Namespace) -> RdstResult:
    """Execute the appropriate CLI command based on parsed arguments."""

    # Convert argparse Namespace to kwargs dictionary
    kwargs = {k: v for k, v in vars(args).items() if v is not None and k != 'command'}

    command = args.command

    if command == 'configure':
        return cli.configure(config_path=args.config, **kwargs)
    elif command == 'top':
        return cli.top(**kwargs)
    elif command == 'analyze':
        # Create filtered kwargs for analyze (exclude analyze-specific parameters)
        analyze_exclude_keys = ['query', 'hash', 'inline_query', 'file', 'stdin', 'name', 'target', 'save_as', 'readyset', 'readyset_cache', 'fast', 'interactive', 'review']
        filtered_kwargs = {k: v for k, v in kwargs.items() if k not in analyze_exclude_keys}

        return cli.analyze(
            hash=getattr(args, 'hash', None),
            query=getattr(args, 'inline_query', None),  # -q/--query flag
            file=getattr(args, 'file', None),
            stdin=getattr(args, 'stdin', False),
            name=getattr(args, 'name', None),
            positional_query=getattr(args, 'query', None),  # positional argument
            target=getattr(args, 'target', None),
            save_as=getattr(args, 'save_as', None),
            readyset=getattr(args, 'readyset', False),
            readyset_cache=getattr(args, 'readyset_cache', False),
            fast=getattr(args, 'fast', False),
            interactive=getattr(args, 'interactive', False),
            review=getattr(args, 'review', False),
            **filtered_kwargs
        )
    elif command == 'tune':
        tune_exclude_keys = ['query']
        filtered_kwargs = {k: v for k, v in kwargs.items() if k not in tune_exclude_keys}
        return cli.tune(args.query, **filtered_kwargs)
    elif command == 'init':
        return cli.init(**kwargs)
    elif command == 'query':
        # Query command with subcommands
        if not hasattr(args, 'query_subcommand') or not args.query_subcommand:
            return RdstResult(False, "Query command requires a subcommand: add, edit, list, show, delete, rm\nTry: rdst query --help")

        query_subcommand = args.query_subcommand

        # Build kwargs for query command
        query_kwargs = {}
        if query_subcommand in ['add', 'edit', 'delete', 'rm', 'show']:
            query_kwargs['name'] = getattr(args, 'query_name', None)
        if query_subcommand in ['edit', 'delete', 'rm']:
            query_kwargs['hash'] = getattr(args, 'hash', None)
        if query_subcommand == 'add':
            query_kwargs['query'] = getattr(args, 'query', None)
            query_kwargs['file'] = getattr(args, 'file', None)
            query_kwargs['target'] = getattr(args, 'target', None)
        if query_subcommand == 'import':
            query_kwargs['file'] = getattr(args, 'file', None)
            query_kwargs['update'] = getattr(args, 'update', False)
            query_kwargs['target'] = getattr(args, 'target', None)
        if query_subcommand in ['list']:
            query_kwargs['limit'] = getattr(args, 'limit', 10)
            query_kwargs['target'] = getattr(args, 'target', None)
            query_kwargs['filter'] = getattr(args, 'filter', None)
            query_kwargs['no_interactive'] = getattr(args, 'no_interactive', False)
        if query_subcommand in ['delete', 'rm']:
            query_kwargs['force'] = getattr(args, 'force', False)

        result = cli.query(subcommand=query_subcommand, **query_kwargs)

        # If user selected a query to analyze, exec into analyze command for clean terminal
        if result.data and result.data.get('action') == 'analyze':
            import os
            selected_hash = result.data.get('selected_hash')
            selected_target = result.data.get('selected_target')

            # Build args for analyze command - use Python interpreter since rdst.py is a script
            analyze_args = [sys.executable, sys.argv[0], 'analyze', '--hash', selected_hash]
            if selected_target:
                analyze_args.extend(['--target', selected_target])

            # Replace this process with analyze - gives clean terminal state
            os.execv(sys.executable, analyze_args)

        return result
    elif command == 'version':
        return cli.version()
    elif command == 'report':
        from lib.cli.report_command import ReportCommand
        report_cmd = ReportCommand()
        success = report_cmd.run(
            query_hash=getattr(args, 'hash', None),
            reason=getattr(args, 'reason', None),
            email=getattr(args, 'email', None),
            positive=getattr(args, 'positive', False),
            negative=getattr(args, 'negative', False),
            include_query=getattr(args, 'include_query', False),
            include_plan=getattr(args, 'include_plan', False),
        )
        return RdstResult(success, "")
    elif command == 'help' or command is None:
        return cli.help()
    else:
        return RdstResult(False, f"Unknown command: {command}")


def _interactive_menu(cli: RdstCLI) -> RdstResult:
    """Interactive menu when no command is provided.

    Presents a simple numbered list of commands and prompts for minimal
    required inputs when needed. Falls back to help on invalid input.
    """
    try:
        # If stdin is not a TTY, fall back to help behavior
        if not sys.stdin.isatty():
            return cli.help()

        # Define commands once
        commands = [
            ("configure", "Manage database targets"),
            ("top", "Live view of slow queries"),
            ("analyze", "Analyze a SQL query"),
            ("tune", "Suggest optimizations for a SQL query"),
            ("init", "First-time setup wizard"),
            ("query", "Manage query registry"),
            ("list", "Show saved queries"),
            ("version", "Show version information"),
            ("report", "Submit feedback or bug reports"),
            ("help", "Show help"),
            ("Exit", "Exit rdst")
        ]

        if _RICH_AVAILABLE and Console and Panel and Table:
            console = Console()
            # Header panel
            header_text = (
                "Troubleshoot latency, analyze queries, and get tuning insights.\n"
                "Type a number to choose a command."
            )
            console.print(Panel.fit(
                header_text,
                title="Readyset Diagnostics & SQL Tuning (rdst)",
                title_align="left",
                subtitle="Readyset",
                subtitle_align="right",
                border_style="cyan"
            ))
            # Commands table
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("#", justify="right", no_wrap=True)
            table.add_column("Command", style="bold cyan", no_wrap=True)
            table.add_column("Description")
            for i, (cmd, desc) in enumerate(commands, start=1):
                table.add_row(str(i), cmd, desc)
            console.print(table)
            prompt = "Select option [1]: "
            choice = input(prompt).strip()
        else:
            print("rdst - Readyset Diagnostics & SQL Tuning")
            print("Select a command to run:")
            for i, (cmd, desc) in enumerate(commands, start=1):
                print(f"  [{i}] {cmd} - {desc}")
            choice = input("Select option [1]: ").strip()
        if not choice:
            choice_idx = 1
        else:
            try:
                choice_idx = int(choice)
            except ValueError:
                return cli.help()
        if choice_idx < 1 or choice_idx > len(commands):
            return cli.help()
        cmd = commands[choice_idx - 1][0]

        # Prompt for required parameters for certain commands
        if cmd == "configure":
            # Let the configure flow handle interactive wizard by default
            return cli.configure()
        elif cmd == "top":
            limit_str = input("Limit [20]: ").strip()
            try:
                limit = int(limit_str) if limit_str else 20
            except ValueError:
                limit = 20
            return cli.top(limit=limit)
        elif cmd in ("analyze", "tune"):
            query = input("SQL query: ").strip()
            if not query:
                return RdstResult(False, f"{cmd} requires a SQL query")
            if cmd == "analyze":
                return cli.analyze(query)
            if cmd == "tune":
                return cli.tune(query)
        elif cmd == "init":
            return cli.init()
        elif cmd == "query":
            # Query command now has subcommands
            print("Query subcommands:")
            print("  [1] add - Add a new query")
            print("  [2] list - List all queries")
            print("  [3] edit - Edit existing query")
            print("  [4] delete - Delete a query")
            subcmd_choice = input("Select subcommand [1]: ").strip() or "1"

            if subcmd_choice == "1":  # add
                queryname = input("Query name: ").strip()
                if not queryname:
                    return RdstResult(False, "Query name is required")
                # Will open $EDITOR if no query provided
                return cli.query(subcommand="add", name=queryname)
            elif subcmd_choice == "2":  # list
                return cli.query(subcommand="list")
            elif subcmd_choice == "3":  # edit
                queryname = input("Query name to edit: ").strip()
                if not queryname:
                    return RdstResult(False, "Query name is required for edit")
                return cli.query(subcommand="edit", name=queryname)
            elif subcmd_choice == "4":  # delete
                queryname = input("Query name to delete: ").strip()
                if not queryname:
                    return RdstResult(False, "Query name is required for delete")
                return cli.query(subcommand="delete", name=queryname)
            else:
                return RdstResult(False, "Invalid query subcommand")
        elif cmd == "list":
            return cli.list()
        elif cmd == "version":
            return cli.version()
        elif cmd == "report":
            title = input("Title: ").strip()
            if not title:
                return RdstResult(False, "report requires a title")
            body = input("Body (optional): ").strip()
            return cli.report(title, body=body)
        else:  # help
            return cli.help()
    except (EOFError, KeyboardInterrupt):
        return cli.help()


def main():
    """Main entry point for the rdst CLI wrapper."""
    try:
        args = parse_arguments()

        # Initialize the CLI
        cli = RdstCLI()

        # If no command specified, offer interactive menu
        if not args.command:
            result = _interactive_menu(cli)
        else:
            # Execute the command
            result = execute_command(cli, args)

        # Handle the result
        if result.ok:
            if result.message:
                print(result.message)

            # Check for periodic NPS prompt (every ~100 commands)
            try:
                from lib.telemetry import telemetry
                if telemetry.should_show_nps_prompt():
                    telemetry.show_nps_prompt()
            except Exception:
                pass  # Don't fail if NPS prompt fails
        else:
            print(f"Error: {result.message}", file=sys.stderr)
            sys.exit(1)

    except KeyboardInterrupt:
        print("\nOperation cancelled.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        # Report crash to telemetry
        try:
            from lib.telemetry import telemetry
            command = args.command if 'args' in locals() and hasattr(args, 'command') else "unknown"
            telemetry.report_crash(e, context={"command": command, "source": "main"})
        except Exception:
            pass  # Don't fail if telemetry fails

        if args.verbose if 'args' in locals() else False:
            import traceback
            traceback.print_exc()
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        # Ensure telemetry events are flushed before exit
        try:
            from lib.telemetry import telemetry
            telemetry.flush()
        except Exception:
            pass


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
rdst - Readyset Diagnostics & SQL Tuning

A command-line interface for diagnostics, query analysis, performance tuning,
and caching with Readyset.
"""
import os
import sys
import argparse
import shutil
import subprocess

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


def print_rich_help():
    """Print colorized help using Rich."""
    if not _RICH_AVAILABLE:
        return False

    console = Console()

    # Header
    console.print()
    console.print("[bold blue]rdst[/bold blue] - [dim]ReadySet Diagnostics & SQL Tuning[/dim]")
    console.print()

    # Commands table
    table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    table.add_column("Command", style="green")
    table.add_column("Description")

    commands = [
        ("configure", "Manage database targets and connection profiles"),
        ("top", "Live view of slow queries"),
        ("analyze", "Analyze SQL query performance"),
        ("ask", "Ask questions about your database in natural language"),
        ("init", "First-time setup wizard"),
        ("query", "Manage saved queries (add/list/delete)"),
        ("schema", "Manage semantic layer for your database"),
        ("report", "Submit feedback or bug reports"),
        ("howdoi", "Quick documentation lookup"),
        ("claude", "Register RDST with Claude Code (MCP)"),
        ("version", "Show version information"),
    ]

    for cmd, desc in commands:
        table.add_row(cmd, desc)

    console.print("[bold]Commands:[/bold]")
    console.print(table)
    console.print()

    # Examples
    console.print("[bold]Examples:[/bold]")
    examples = [
        ("rdst init", "First-time setup wizard"),
        ("rdst top --target mydb", "Monitor slow queries"),
        ("rdst analyze -q \"SELECT * FROM users\" --target mydb", "Analyze a query"),
        ("rdst analyze -q \"SELECT ...\" --readyset-cache", "Test ReadySet caching"),
        ("rdst howdoi \"how do I find slow queries?\"", "Quick docs lookup"),
    ]

    for cmd, desc in examples:
        console.print(f"  [cyan]{cmd}[/cyan]")
        console.print(f"    [dim]{desc}[/dim]")

    console.print()
    console.print("[dim]Use[/dim] [cyan]rdst <command> --help[/cyan] [dim]for command-specific options[/dim]")
    console.print()

    return True

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
  rdst configure add --target prod --connection-string "postgresql://user:pass@host:5432/db"
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
    configure_parser = subparsers.add_parser('configure', help='Manage database targets',
        description='''Manage database connection targets.

Targets are saved connection profiles that RDST uses to connect to your databases.
Each target has a name, connection details, and an environment variable for the password.

Subcommands:
  add      Add a new database target
  list     List all configured targets
  edit     Edit an existing target
  remove   Remove a target
  default  Set the default target
  test     Test connection to a target

Examples:
  rdst configure add --target prod --host db.example.com --user admin --database mydb --password-env PROD_DB_PASS
  rdst configure list
  rdst configure test prod
  rdst configure default prod''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    configure_parser.add_argument('subcommand', nargs='?', default='menu',
                                  help='Subcommand: menu (default), add, edit, list, remove, default, test')
    configure_parser.add_argument('name', nargs='?', help='Target name for edit/remove/default')
    configure_parser.add_argument('--connection-string', help='Database connection string (postgresql://user:pass@host:port/db or mysql://...)')
    configure_parser.add_argument('--target', '--name', help='Target name')
    configure_parser.add_argument('--engine', choices=['postgresql', 'mysql'], help='Database engine (overrides connection string)')
    configure_parser.add_argument('--host', help='Database host (overrides connection string)')
    configure_parser.add_argument('--port', type=int, help='Database port (overrides connection string)')
    configure_parser.add_argument('--user', help='Database user (overrides connection string)')
    configure_parser.add_argument('--database', help='Database name (overrides connection string)')
    configure_parser.add_argument('--password-env', help='Environment variable for password')
    configure_parser.add_argument('--read-only', action='store_true', help='Read-only connection')
    configure_parser.add_argument('--proxy', choices=['none', 'readyset', 'proxysql', 'pgbouncer', 'tunnel', 'custom'],
                                  help='Proxy type')
    configure_parser.add_argument('--tls', action='store_true', help='Enable TLS (overrides connection string)')
    configure_parser.add_argument('--no-tls', action='store_true', help='Disable TLS (overrides connection string)')
    configure_parser.add_argument('--default', action='store_true', help='Set as default target')
    configure_parser.add_argument('--confirm', action='store_true', help='Confirm removal without prompting')
    configure_parser.add_argument('--skip-verify', action='store_true', help='Skip connection verification (for non-interactive use)')

    # top command
    top_parser = subparsers.add_parser('top', help='Live view of slow queries',
        description='''Monitor database queries in real-time and identify slow queries.

Queries are automatically saved to the registry as they're detected.
Use the displayed hash values with 'rdst analyze' to investigate further.

Examples:
  rdst top --target mydb              Monitor queries on 'mydb' target
  rdst top --duration 30              Run for 30 seconds and output results
  rdst top --json --duration 10       JSON output for scripting
  rdst top --historical               Use pg_stat_statements instead of live monitoring''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
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
    analyze_parser = subparsers.add_parser('analyze', help='Analyze SQL query',
        description='''Analyze a SQL query for performance issues and get optimization recommendations.

Runs EXPLAIN ANALYZE and uses AI to provide index recommendations, query rewrites,
and ReadySet caching opportunities.

Examples:
  rdst analyze -q "SELECT * FROM users WHERE id = 1" --target mydb
  rdst analyze --hash abc123 --target mydb    Analyze query from registry by hash
  rdst analyze -f query.sql --target mydb     Analyze query from file
  rdst analyze -q "SELECT ..." --readyset-cache   Test ReadySet caching performance''',
        formatter_class=argparse.RawDescriptionHelpFormatter)

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
    analyze_parser.add_argument('--readyset-cache', action='store_true', dest='readyset_cache',
                               help='Test ReadySet caching: spins up a Docker container with your schema, caches the query, and shows performance comparison and whether the query is supported')
    analyze_parser.add_argument('--fast', action='store_true',
                               help='Skip EXPLAIN ANALYZE entirely and use EXPLAIN only (much faster, less accurate timing)')
    analyze_parser.add_argument('--interactive', action='store_true', help='Enter interactive mode after analysis for Q&A about recommendations')
    analyze_parser.add_argument('--review', action='store_true',
                               help='Review conversation history for this query without re-running analysis')
    analyze_parser.add_argument('--workload', action='store_true', help='Analyze multiple queries together for holistic index recommendations (coming soon)')
    analyze_parser.add_argument('--large-query-bypass', action='store_true', help='Bypass the 1KB query size limit (allows up to 10KB) for -q, -f, or --stdin input')

    # tune command
    tune_parser = subparsers.add_parser('tune', help='Get optimization suggestions')
    tune_parser.add_argument('query', help='SQL query to tune')

    # init command
    init_parser = subparsers.add_parser('init', help='First-time setup wizard',
        description='''Run the first-time setup wizard to configure RDST.

This interactive wizard helps you:
  - Set up your Anthropic API key for AI-powered analysis
  - Add your first database target
  - Test the connection

Example:
  rdst init                 Run setup wizard
  rdst init --force         Re-run even if already configured''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    init_parser.add_argument('--force', action='store_true', help='Re-run setup even if config exists')
    init_parser.add_argument('--interactive', action='store_true', help='Force interactive mode')

    # query command - query registry management
    query_parser = subparsers.add_parser('query', help='Manage query registry (add/edit/list/delete queries)',
        description='''Manage saved queries in the query registry.

The query registry stores SQL queries for easy reuse with 'rdst analyze' and 'rdst ask'.
Queries captured by 'rdst top' are automatically saved here as they're detected.

Subcommands:
  add      Add a new query to the registry
  list     List all saved queries (interactive selection)
  show     Show full details of a specific query
  edit     Edit an existing query in $EDITOR
  delete   Delete a query by name or hash
  import   Import multiple queries from a SQL file

Examples:
  rdst query add my-query -q "SELECT * FROM users"
  rdst query list
  rdst query list --filter "users"
  rdst query show my-query
  rdst query delete --hash abc123''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
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
    query_list_parser = query_subparsers.add_parser('list', help='List saved queries')
    query_list_parser.add_argument('--limit', type=int, default=10, help='Number of queries to show (default: 10)')
    query_list_parser.add_argument('--target', help='Filter queries by target database')
    query_list_parser.add_argument('--filter', help='Smart filter: search across SQL, names, hash, source')
    query_list_parser.add_argument('-i', '--interactive', action='store_true', help='Interactive mode to select queries for analysis')

    # query show
    query_show_parser = query_subparsers.add_parser('show', help='Show details of a specific query')
    query_show_group = query_show_parser.add_mutually_exclusive_group(required=True)
    query_show_group.add_argument('query_name', nargs='?', help='Query name to show')
    query_show_group.add_argument('--hash', help='Query hash to show')

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

    # ============================================================================
    # RDST ASK & SCHEMA - Natural language to SQL and semantic layer management
    # ============================================================================
    # ask command - Natural language to SQL
    ask_parser = subparsers.add_parser('ask', help='Ask questions about your database in natural language',
        description='''Ask questions about your database using natural language.

Converts your question into SQL, executes it, and returns the results.
Use this to explore data and answer questions - for query optimization, use 'rdst analyze' instead.

The quality of results improves when you have a semantic layer configured (see 'rdst schema').
The more details you provide with 'rdst schema annotate', the better the SQL generation.

Modes:
  Default     Linear flow: generate SQL, confirm, execute, show results
  --agent     Agent mode: explores schema iteratively for complex questions

Examples:
  rdst ask "How many customers are there?" --target mydb
  rdst ask "Show top 10 orders by price" --target mydb
  rdst ask "Which products have the most sales?" --target mydb --agent
  rdst ask "Count users by country" --target mydb --dry-run''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ask_parser.add_argument('question', nargs='?', help='Natural language question about your data')
    ask_parser.add_argument('--target', help='Target database')
    ask_parser.add_argument('--dry-run', action='store_true', help='Generate SQL but do not execute')
    ask_parser.add_argument('--timeout', type=int, default=30, help='Query timeout in seconds')
    ask_parser.add_argument('--verbose', action='store_true', help='Show detailed information')
    ask_parser.add_argument('--agent', dest='agent_mode', action='store_true', help='Agent mode: iteratively explores schema for complex questions')
    ask_parser.add_argument('--no-interactive', action='store_true', help='Non-interactive mode')

    # schema command - Semantic layer management
    schema_parser = subparsers.add_parser('schema', help='Manage semantic layer for your database',
        description='''Manage the semantic layer for your database target.

The semantic layer stores metadata about your schema to improve 'rdst ask' results:
  - Table and column descriptions
  - Enum values with their meanings (e.g., status codes, category types)
  - Business terminology and relationships
  - Foreign key documentation

The more comprehensive your semantic layer, the better 'rdst ask' can generate accurate SQL.

Subcommands:
  init       Initialize from database (introspects tables, columns, detects enums)
  show       Display semantic layer for a target or specific table
  annotate   Add descriptions interactively or with AI assistance (--use-llm)
  edit       Open semantic layer in $EDITOR for manual editing
  export     Export as YAML or JSON
  delete     Remove semantic layer for a target
  list       List all configured semantic layers

Examples:
  rdst schema init --target mydb                Bootstrap from database
  rdst schema annotate --target mydb --use-llm  AI-generate descriptions
  rdst schema show --target mydb                View current semantic layer
  rdst schema show --target mydb customer       Show specific table details''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    schema_subparsers = schema_parser.add_subparsers(dest='schema_subcommand', help='Schema subcommands')

    # schema show
    schema_show_parser = schema_subparsers.add_parser('show', help='Display semantic layer')
    schema_show_parser.add_argument('table', nargs='?', help='Specific table to show')
    schema_show_parser.add_argument('--target', help='Target database name')

    # schema init
    schema_init_parser = schema_subparsers.add_parser('init', help='Initialize semantic layer from database')
    schema_init_parser.add_argument('--target', help='Target database name')
    schema_init_parser.add_argument('--enum-threshold', type=int, default=20, help='Max distinct values for enum detection')
    schema_init_parser.add_argument('--force', action='store_true', help='Overwrite existing semantic layer')
    schema_init_parser.add_argument('-i', '--interactive', action='store_true', help='Interactively annotate enum values')

    # schema edit
    schema_edit_parser = schema_subparsers.add_parser('edit', help='Edit semantic layer in $EDITOR')
    schema_edit_parser.add_argument('table', nargs='?', help='Specific table to focus on')
    schema_edit_parser.add_argument('--target', help='Target database name')

    # schema annotate
    schema_annotate_parser = schema_subparsers.add_parser('annotate', help='Annotate columns interactively')
    schema_annotate_parser.add_argument('table', nargs='?', help='Table to annotate')
    schema_annotate_parser.add_argument('--target', help='Target database name')
    schema_annotate_parser.add_argument('--use-llm', action='store_true', help='Use LLM to suggest annotations')
    schema_annotate_parser.add_argument('--sample-rows', type=int, default=5, help='Sample rows for LLM context')

    # schema export
    schema_export_parser = schema_subparsers.add_parser('export', help='Export semantic layer')
    schema_export_parser.add_argument('--target', help='Target database name')
    schema_export_parser.add_argument('--format', dest='output_format', choices=['yaml', 'json'], default='yaml', help='Output format')

    # schema delete
    schema_delete_parser = schema_subparsers.add_parser('delete', help='Delete semantic layer')
    schema_delete_parser.add_argument('--target', help='Target database name')
    schema_delete_parser.add_argument('--force', action='store_true', help='Skip confirmation')

    # schema list
    schema_subparsers.add_parser('list', help='List all semantic layers')

    # version command
    subparsers.add_parser('version', help='Show version')

    # claude command - register with Claude Code
    claude_parser = subparsers.add_parser('claude', help='Register RDST with Claude Code',
        description='''Register RDST as an MCP server with Claude Code.

This enables Claude Code to use RDST tools directly for database analysis.
After registration, Claude can analyze queries, monitor slow queries, and
provide optimization recommendations.

Examples:
  rdst claude add         Register RDST with Claude Code (default)
  rdst claude remove      Unregister RDST from Claude Code''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    claude_parser.add_argument('action', nargs='?', default='add', choices=['add', 'remove'],
                               help='Action: add (default) or remove')

    # report command - user feedback
    report_parser = subparsers.add_parser('report', help='Submit feedback about RDST',
        description='''Submit feedback or bug reports about RDST.

Use this to report issues, suggest improvements, or provide feedback about
analysis results. Optionally include query details for context.

Examples:
  rdst report --negative -r "Index suggestion was incorrect"
  rdst report --positive -r "Great recommendation!"
  rdst report --hash abc123 --include-query -r "Unexpected result"''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    report_parser.add_argument('--hash', help='Query hash to provide feedback on')
    report_parser.add_argument('--reason', '-r', help='Feedback reason (interactive if not provided)')
    report_parser.add_argument('--email', '-e', help='Email for follow-up (optional)')
    report_parser.add_argument('--positive', action='store_true', help='Mark as positive feedback')
    report_parser.add_argument('--negative', action='store_true', help='Mark as negative feedback')
    report_parser.add_argument('--include-query', action='store_true', help='Include raw SQL in feedback')
    report_parser.add_argument('--include-plan', action='store_true', help='Include execution plan in feedback')

    # help command
    subparsers.add_parser('help', help='Show help')

    # howdoi command - quick docs lookup
    howdoi_parser = subparsers.add_parser('howdoi', help='Ask a question about RDST usage',
        description='''Get quick answers about how to use RDST.

Uses built-in documentation to answer common questions about commands,
configuration, and workflows.

Examples:
  rdst howdoi "analyze a query"
  rdst howdoi "find slow queries"
  rdst howdoi "configure database"
  rdst howdoi "test readyset caching"''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    howdoi_parser.add_argument('question', nargs='*', help='Your question in quotes (e.g., "how do I analyze a query?")')

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
        analyze_exclude_keys = ['query', 'hash', 'inline_query', 'file', 'stdin', 'name', 'target', 'save_as', 'readyset_cache', 'fast', 'interactive', 'review', 'large_query_bypass']
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
            readyset_cache=getattr(args, 'readyset_cache', False),
            fast=getattr(args, 'fast', False),
            interactive=getattr(args, 'interactive', False),
            review=getattr(args, 'review', False),
            large_query_bypass=getattr(args, 'large_query_bypass', None),
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
        if query_subcommand in ['edit', 'delete', 'rm', 'show']:
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
            query_kwargs['interactive'] = getattr(args, 'interactive', False)
        if query_subcommand in ['delete', 'rm']:
            query_kwargs['force'] = getattr(args, 'force', False)

        result = cli.query(subcommand=query_subcommand, **query_kwargs)

        # If user selected a query to analyze, exec into analyze command for clean terminal
        if result.data and result.data.get('action') == 'analyze':
            selected_hash = result.data.get('selected_hash')
            selected_target = result.data.get('selected_target')

            # Build args for analyze command - use Python interpreter since rdst.py is a script
            analyze_args = [sys.executable, sys.argv[0], 'analyze', '--hash', selected_hash]
            if selected_target:
                analyze_args.extend(['--target', selected_target])

            # Replace this process with analyze - gives clean terminal state
            os.execv(sys.executable, analyze_args)

        return result

    # ============================================================================
    # RDST ASK & SCHEMA - Natural language to SQL and semantic layer
    # ============================================================================
    elif command == 'ask':
        return cli.ask(
            question=getattr(args, 'question', None),
            target=getattr(args, 'target', None),
            dry_run=getattr(args, 'dry_run', False),
            timeout=getattr(args, 'timeout', 30),
            verbose=getattr(args, 'verbose', False),
            agent_mode=getattr(args, 'agent_mode', False),
            no_interactive=getattr(args, 'no_interactive', False)
        )

    elif command == 'schema':
        schema_subcommand = getattr(args, 'schema_subcommand', None)
        schema_kwargs = {
            'subcommand': schema_subcommand,
            'target': getattr(args, 'target', None),
        }

        if schema_subcommand in ['show', 'edit', 'annotate']:
            schema_kwargs['table'] = getattr(args, 'table', None)
        if schema_subcommand == 'annotate':
            schema_kwargs['use_llm'] = getattr(args, 'use_llm', False)
            schema_kwargs['sample_rows'] = getattr(args, 'sample_rows', 5)
        if schema_subcommand == 'init':
            schema_kwargs['enum_threshold'] = getattr(args, 'enum_threshold', 20)
            schema_kwargs['force'] = getattr(args, 'force', False)
            schema_kwargs['interactive'] = getattr(args, 'interactive', False)
        if schema_subcommand == 'export':
            schema_kwargs['output_format'] = getattr(args, 'output_format', 'yaml')
        if schema_subcommand == 'delete':
            schema_kwargs['force'] = getattr(args, 'force', False)

        return cli.schema(**schema_kwargs)

    elif command == 'version':
        return cli.version()
    elif command == 'claude':
        # Register or remove RDST from Claude Code
        action = getattr(args, 'action', 'add')

        # Check if claude CLI is available
        claude_path = shutil.which('claude')
        if not claude_path:
            return RdstResult(False, "Claude Code CLI not found. Install it from: https://claude.ai/code")

        if action == 'add':
            # Register the MCP server
            # Determine the best way to run the MCP server:
            # 1. If rdst-mcp is in PATH (pip installed), use it
            # 2. Otherwise, use python3 with full path to mcp_server.py
            rdst_mcp_path = shutil.which('rdst-mcp')
            if rdst_mcp_path:
                mcp_command = ['rdst-mcp']
            else:
                # Find mcp_server.py relative to this script
                script_dir = os.path.dirname(os.path.abspath(__file__))
                mcp_server_path = os.path.join(script_dir, 'mcp_server.py')
                if not os.path.exists(mcp_server_path):
                    return RdstResult(False, f"MCP server not found at {mcp_server_path}")
                mcp_command = ['python3', mcp_server_path]

            # Install the /rdst slash command globally
            slash_cmd_content = '''# RDST Mode Activated

You have RDST (ReadySet Diagnostics & SQL Tuning) tools available.

**First, call the `rdst_help` tool to check the user's setup.**

Based on the result:

## If NO targets are configured (first-time user):

Present a friendly welcome:

---

**Welcome to RDST!**

Looks like this is your first time using RDST. I'll help you get set up.

To analyze your database queries, I need to connect to your database. Please provide:

1. **Database type**: PostgreSQL or MySQL?
2. **Host**: Where is your database? (e.g., localhost, db.example.com)
3. **Port**: What port? (default: 5432 for PostgreSQL, 3306 for MySQL)
4. **Username**: Database user to connect as
5. **Database name**: Which database to connect to
6. **Password env var name**: What should I call the environment variable for the password? (e.g., MY_DB_PASSWORD)

Once you give me these details, I'll configure RDST and we can start analyzing your slow queries!

---

## If targets ARE configured:

Present a status summary:

---

**RDST Ready**

[List their configured targets - show which are ready vs need passwords]

[If any need passwords, show: "To use [target], export: `export VAR_NAME='password'`"]

**What would you like to do?**
- Analyze a SQL query
- Find and fix slow queries
- Explore your database
- Add another database connection

---

Keep it conversational. The user shouldn't need to know the underlying commands - just help them with their database.
'''
            # Install slash command to ~/.claude/commands/
            claude_commands_dir = os.path.expanduser('~/.claude/commands')
            os.makedirs(claude_commands_dir, exist_ok=True)
            slash_cmd_path = os.path.join(claude_commands_dir, 'rdst.md')
            try:
                with open(slash_cmd_path, 'w') as f:
                    f.write(slash_cmd_content)
            except Exception:
                # Non-fatal - continue with MCP registration
                pass

            try:
                result = subprocess.run(
                    ['claude', 'mcp', 'add', 'rdst', '--'] + mcp_command,
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    return RdstResult(True, """RDST registered with Claude Code!

To use RDST in Claude:
  1. Start a new Claude Code session
  2. Type /rdst to activate RDST mode

Claude will now have access to all RDST tools for query analysis and optimization.""")
                else:
                    # Check if already registered
                    if 'already exists' in result.stderr.lower():
                        return RdstResult(True, "RDST is already registered with Claude Code.")
                    return RdstResult(False, f"Failed to register: {result.stderr}")
            except Exception as e:
                return RdstResult(False, f"Error running claude command: {e}")

        elif action == 'remove':
            # Remove the slash command
            slash_cmd_path = os.path.expanduser('~/.claude/commands/rdst.md')
            if os.path.exists(slash_cmd_path):
                try:
                    os.remove(slash_cmd_path)
                except Exception:
                    pass  # Non-fatal

            try:
                result = subprocess.run(
                    ['claude', 'mcp', 'remove', 'rdst'],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    return RdstResult(True, "RDST removed from Claude Code.")
                else:
                    return RdstResult(False, f"Failed to remove: {result.stderr}")
            except Exception as e:
                return RdstResult(False, f"Error running claude command: {e}")

        return RdstResult(False, f"Unknown action: {action}")
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
    elif command == 'howdoi':
        from lib.cli.howdoi_command import HowDoICommand
        howdoi_cmd = HowDoICommand()
        question = ' '.join(args.question) if args.question else ''
        if not question:
            return RdstResult(False, "Please provide a question. Example: rdst howdoi \"how do I analyze a query?\"")
        result = howdoi_cmd.run(question)
        if result.success:
            howdoi_cmd.print_formatted(result.answer)
            return RdstResult(True, "")
        else:
            return RdstResult(False, result.error or "Failed to answer question")
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
            ("ask", "Ask questions in natural language"),
            ("tune", "Suggest optimizations for a SQL query"),
            ("init", "First-time setup wizard"),
            ("query", "Manage query registry"),
            ("schema", "Manage semantic layer"),
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
        elif cmd == "ask":
            return cli.ask()
        elif cmd == "schema":
            return cli.schema()
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
        else:  # help, Exit
            return cli.help()
    except (EOFError, KeyboardInterrupt):
        return cli.help()


def main():
    """Main entry point for the rdst CLI wrapper."""
    try:
        # Intercept top-level --help for Rich formatted output
        if len(sys.argv) == 2 and sys.argv[1] in ('--help', '-h', 'help'):
            if print_rich_help():
                sys.exit(0)
            # Fall through to argparse if Rich not available

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
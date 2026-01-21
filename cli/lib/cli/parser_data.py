"""
RDST CLI Definitions - Command structure, arguments, and help text.

Single source of truth for all CLI commands.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple, Union


@dataclass
class ArgDef:
    name: str
    help: str
    short: Optional[str] = None
    aliases: List[str] = field(default_factory=list)
    type: Optional[Callable] = None
    default: Any = None
    action: Optional[str] = None
    choices: Optional[List[str]] = None
    nargs: Optional[str] = None
    dest: Optional[str] = None
    metavar: Optional[str] = None
    required: Optional[bool] = None

    def is_positional(self) -> bool:
        return not self.name.startswith("-")


@dataclass
class MutuallyExclusiveGroup:
    args: List[ArgDef]
    required: bool = False


@dataclass
class SubcommandDef:
    name: str
    help: str
    args: List[Union[ArgDef, MutuallyExclusiveGroup]] = field(default_factory=list)


@dataclass
class CommandDef:
    name: str
    short_help: str
    description: str
    args: List[Union[ArgDef, MutuallyExclusiveGroup]] = field(default_factory=list)
    subcommand_defs: List[SubcommandDef] = field(default_factory=list)
    subcommand_dest: Optional[str] = None
    examples: List[Tuple[str, str]] = field(default_factory=list)
    subcommands: List[Tuple[str, str]] = field(default_factory=list)


COMMANDS: dict[str, CommandDef] = {
    "configure": CommandDef(
        name="configure",
        short_help="Manage database targets and connection profiles",
        description="""Manage database connection targets.

Targets are saved connection profiles that RDST uses to connect to your databases.
Each target has a name, connection details, and an environment variable for the password.""",
        args=[
            ArgDef(
                "subcommand",
                nargs="?",
                default="menu",
                help="Subcommand: menu (default), add, edit, list, remove, default, test",
            ),
            ArgDef("name", nargs="?", help="Target name for edit/remove/default"),
            ArgDef(
                "--connection-string",
                help="Database connection string (postgresql://user:pass@host:port/db or mysql://...)",
            ),
            ArgDef("--target", aliases=["--name"], help="Target name"),
            ArgDef(
                "--engine",
                choices=["postgresql", "mysql"],
                help="Database engine (overrides connection string)",
            ),
            ArgDef("--host", help="Database host (overrides connection string)"),
            ArgDef(
                "--port", type=int, help="Database port (overrides connection string)"
            ),
            ArgDef("--user", help="Database user (overrides connection string)"),
            ArgDef("--database", help="Database name (overrides connection string)"),
            ArgDef("--password-env", help="Environment variable for password"),
            ArgDef("--read-only", action="store_true", help="Read-only connection"),
            ArgDef(
                "--proxy",
                choices=[
                    "none",
                    "readyset",
                    "proxysql",
                    "pgbouncer",
                    "tunnel",
                    "custom",
                ],
                help="Proxy type",
            ),
            ArgDef(
                "--tls",
                action="store_true",
                help="Enable TLS (overrides connection string)",
            ),
            ArgDef(
                "--no-tls",
                action="store_true",
                help="Disable TLS (overrides connection string)",
            ),
            ArgDef("--default", action="store_true", help="Set as default target"),
            ArgDef(
                "--confirm",
                action="store_true",
                help="Confirm removal without prompting",
            ),
            ArgDef(
                "--skip-verify",
                action="store_true",
                help="Skip connection verification (for non-interactive use)",
            ),
        ],
        subcommands=[
            ("add", "Add a new database target"),
            ("list", "List all configured targets"),
            ("edit", "Edit an existing target"),
            ("remove", "Remove a target"),
            ("default", "Set the default target"),
            ("test", "Test connection to a target"),
        ],
        examples=[
            (
                "rdst configure add --target prod --host db.example.com --user admin --database mydb --password-env PROD_DB_PASS",
                "Add a new target",
            ),
            ("rdst configure list", "List all targets"),
            ("rdst configure test prod", "Test connection"),
            ("rdst configure default prod", "Set default target"),
        ],
    ),
    "top": CommandDef(
        name="top",
        short_help="Live view of slow queries",
        description="""Monitor database queries in real-time and identify slow queries.

Queries are automatically saved to the registry as they're detected.
Use the displayed hash values with 'rdst analyze' to investigate further.""",
        args=[
            ArgDef("--target", help="Specific configured DB target"),
            ArgDef(
                "--source",
                choices=[
                    "auto",
                    "pg_stat",
                    "activity",
                    "slowlog",
                    "digest",
                    "rds",
                    "pmm",
                ],
                default="auto",
                help="Telemetry source to use",
            ),
            ArgDef("--limit", type=int, default=10, help="Number of queries to show"),
            ArgDef(
                "--sort",
                choices=["freq", "total_time", "avg_time", "load"],
                default="total_time",
                help="Sort field",
            ),
            ArgDef("--filter", help="Regex to filter query text"),
            ArgDef("--json", action="store_true", help="Output machine-readable JSON"),
            ArgDef(
                "--watch", action="store_true", help="Continuously refresh the view"
            ),
            ArgDef(
                "--interactive",
                action="store_true",
                help="Interactive mode to select queries for analysis",
            ),
            ArgDef(
                "--no-color", action="store_true", help="Disable ANSI color formatting"
            ),
            ArgDef(
                "--historical",
                action="store_true",
                help="Use historical statistics (pg_stat_statements/performance_schema) instead of real-time monitoring",
            ),
            ArgDef(
                "--duration",
                type=int,
                help="Run real-time Top for N seconds then output results (snapshot mode, non-interactive)",
            ),
        ],
        examples=[
            ("rdst top --target mydb", "Monitor queries on 'mydb' target"),
            ("rdst top --duration 30", "Run for 30 seconds and output results"),
            ("rdst top --json --duration 10", "JSON output for scripting"),
            (
                "rdst top --historical",
                "Use pg_stat_statements instead of live monitoring",
            ),
        ],
    ),
    "analyze": CommandDef(
        name="analyze",
        short_help="Analyze SQL query performance",
        description="""Analyze a SQL query for performance issues and get optimization recommendations.

Runs EXPLAIN ANALYZE and uses AI to provide index recommendations, query rewrites,
and Readyset caching opportunities.""",
        args=[
            MutuallyExclusiveGroup(
                args=[
                    ArgDef(
                        "--query",
                        short="-q",
                        dest="inline_query",
                        help="SQL query to analyze (use quotes for multiline)",
                    ),
                    ArgDef(
                        "--file",
                        short="-f",
                        help="Read SQL from file (supports multiline)",
                    ),
                    ArgDef(
                        "--stdin",
                        action="store_true",
                        help='Read SQL from stdin (e.g., echo "SELECT..." | rdst analyze --stdin)',
                    ),
                    ArgDef(
                        "--hash", dest="hash", help="Load query by hash from registry"
                    ),
                    ArgDef("--name", help="Load query by name from registry"),
                ],
                required=False,
            ),
            ArgDef("query", nargs="?", help="SQL query to analyze (fallback)"),
            ArgDef("--target", help="Target database"),
            ArgDef("--save-as", help="Name to save query as after analysis"),
            ArgDef(
                "--readyset-cache",
                action="store_true",
                dest="readyset_cache",
                help="Test Readyset caching: spins up a Docker container with your schema, caches the query, and shows performance comparison and whether the query is supported",
            ),
            ArgDef(
                "--fast",
                action="store_true",
                help="Skip EXPLAIN ANALYZE entirely and use EXPLAIN only (much faster, less accurate timing)",
            ),
            ArgDef(
                "--interactive",
                action="store_true",
                help="Enter interactive mode after analysis for Q&A about recommendations",
            ),
            ArgDef(
                "--review",
                action="store_true",
                help="Review conversation history for this query without re-running analysis",
            ),
            ArgDef(
                "--workload",
                action="store_true",
                help="Analyze multiple queries together for holistic index recommendations (coming soon)",
            ),
            ArgDef(
                "--large-query-bypass",
                action="store_true",
                help="Bypass the 4KB query size limit (allows up to 10KB) for -q, -f, or --stdin input",
            ),
        ],
        examples=[
            (
                'rdst analyze -q "SELECT * FROM users WHERE id = 1" --target mydb',
                "Analyze a query",
            ),
            (
                "rdst analyze --hash abc123 --target mydb",
                "Analyze query from registry by hash",
            ),
            ("rdst analyze -f query.sql --target mydb", "Analyze query from file"),
            (
                'rdst analyze -q "SELECT ..." --readyset-cache',
                "Test Readyset caching performance",
            ),
        ],
    ),
    "ask": CommandDef(
        name="ask",
        short_help="Ask questions about your database in natural language",
        description="""Ask questions about your database using natural language.

Converts your question into SQL, executes it, and returns the results.
Use this to explore data and answer questions - for query optimization, use 'rdst analyze' instead.

The quality of results improves when you have a semantic layer configured (see 'rdst schema').
The more details you provide with 'rdst schema annotate', the better the SQL generation.

Modes:
  Default     Linear flow: generate SQL, confirm, execute, show results
  --agent     Agent mode: explores schema iteratively for complex questions""",
        args=[
            ArgDef(
                "question", nargs="?", help="Natural language question about your data"
            ),
            ArgDef("--target", help="Target database"),
            ArgDef(
                "--dry-run", action="store_true", help="Generate SQL but do not execute"
            ),
            ArgDef("--timeout", type=int, default=30, help="Query timeout in seconds"),
            ArgDef("--verbose", action="store_true", help="Show detailed information"),
            ArgDef(
                "--agent",
                dest="agent_mode",
                action="store_true",
                help="Agent mode: iteratively explores schema for complex questions",
            ),
            ArgDef(
                "--no-interactive", action="store_true", help="Non-interactive mode"
            ),
        ],
        examples=[
            (
                'rdst ask "How many customers are there?" --target mydb',
                "Simple question",
            ),
            (
                'rdst ask "Show top 10 orders by price" --target mydb',
                "Data exploration",
            ),
            (
                'rdst ask "Which products have the most sales?" --target mydb --agent',
                "Complex question with agent mode",
            ),
            (
                'rdst ask "Count users by country" --target mydb --dry-run',
                "Generate SQL without executing",
            ),
        ],
    ),
    "init": CommandDef(
        name="init",
        short_help="First-time setup wizard",
        description="""Run the first-time setup wizard to configure RDST.

This interactive wizard helps you:
  - Set up your Anthropic API key for AI-powered analysis
  - Add your first database target
  - Test the connection""",
        args=[
            ArgDef(
                "--force",
                action="store_true",
                help="Re-run setup even if config exists",
            ),
            ArgDef("--interactive", action="store_true", help="Force interactive mode"),
        ],
        examples=[
            ("rdst init", "Run setup wizard"),
            ("rdst init --force", "Re-run even if already configured"),
        ],
    ),
    "query": CommandDef(
        name="query",
        short_help="Manage saved queries (add/list/delete)",
        description="""Manage saved queries in the query registry.

The query registry stores SQL queries for easy reuse with 'rdst analyze' and 'rdst ask'.
Queries captured by 'rdst top' are automatically saved here as they're detected.""",
        subcommand_dest="query_subcommand",
        subcommand_defs=[
            SubcommandDef(
                name="add",
                help="Add a new query to registry",
                args=[
                    ArgDef("query_name", help="Name for the query"),
                    ArgDef(
                        "--query",
                        short="-q",
                        help="Inline SQL query (optional, will open $EDITOR if not provided)",
                    ),
                    ArgDef("--file", short="-f", help="Read SQL from file"),
                    ArgDef("--target", help="Target database name"),
                ],
            ),
            SubcommandDef(
                name="import",
                help="Import multiple queries from SQL file",
                args=[
                    ArgDef("file", help="Path to SQL file containing multiple queries"),
                    ArgDef(
                        "--update",
                        action="store_true",
                        help="Update existing queries instead of skipping",
                    ),
                    ArgDef(
                        "--target",
                        help="Default target database for queries without target comment",
                    ),
                ],
            ),
            SubcommandDef(
                name="edit",
                help="Edit an existing query",
                args=[
                    MutuallyExclusiveGroup(
                        args=[
                            ArgDef("query_name", nargs="?", help="Query name to edit"),
                            ArgDef("--hash", help="Query hash to edit"),
                        ],
                        required=True,
                    ),
                ],
            ),
            SubcommandDef(
                name="list",
                help="List saved queries",
                args=[
                    ArgDef(
                        "--limit",
                        type=int,
                        default=10,
                        help="Number of queries to show (default: 10)",
                    ),
                    ArgDef("--target", help="Filter queries by target database"),
                    ArgDef(
                        "--filter",
                        help="Smart filter: search across SQL, names, hash, source",
                    ),
                    ArgDef(
                        "--interactive",
                        short="-i",
                        action="store_true",
                        help="Interactive mode to select queries for analysis",
                    ),
                ],
            ),
            SubcommandDef(
                name="show",
                help="Show details of a specific query",
                args=[
                    MutuallyExclusiveGroup(
                        args=[
                            ArgDef("query_name", nargs="?", help="Query name to show"),
                            ArgDef("--hash", help="Query hash to show"),
                        ],
                        required=True,
                    ),
                ],
            ),
            SubcommandDef(
                name="delete",
                help="Delete a query from registry",
                args=[
                    MutuallyExclusiveGroup(
                        args=[
                            ArgDef(
                                "query_name", nargs="?", help="Query name to delete"
                            ),
                            ArgDef("--hash", help="Query hash to delete"),
                        ],
                        required=True,
                    ),
                    ArgDef(
                        "--force", action="store_true", help="Skip confirmation prompt"
                    ),
                ],
            ),
            SubcommandDef(
                name="rm",
                help="Delete a query from registry (alias for delete)",
                args=[
                    MutuallyExclusiveGroup(
                        args=[
                            ArgDef(
                                "query_name", nargs="?", help="Query name to delete"
                            ),
                            ArgDef("--hash", help="Query hash to delete"),
                        ],
                        required=True,
                    ),
                    ArgDef(
                        "--force", action="store_true", help="Skip confirmation prompt"
                    ),
                ],
            ),
            SubcommandDef(
                name="run",
                help="Run saved queries for benchmarking/load generation",
                args=[
                    ArgDef(
                        "queries",
                        nargs="+",
                        help="Query names or hashes to run (round-robin if multiple)",
                    ),
                    ArgDef(
                        "--target",
                        short="-t",
                        help="Target database (uses query's stored target if omitted)",
                    ),
                    ArgDef(
                        "--interval",
                        type=int,
                        metavar="MS",
                        help="Fixed interval mode: run every N milliseconds",
                    ),
                    ArgDef(
                        "--concurrency",
                        short="-c",
                        type=int,
                        metavar="N",
                        help="Concurrency mode: maintain N concurrent executions",
                    ),
                    ArgDef(
                        "--duration",
                        type=int,
                        metavar="SECS",
                        help="Stop after N seconds",
                    ),
                    ArgDef(
                        "--count",
                        type=int,
                        metavar="N",
                        help="Stop after N total executions",
                    ),
                    ArgDef(
                        "--quiet",
                        short="-q",
                        action="store_true",
                        help="Minimal output, only show summary",
                    ),
                ],
            ),
        ],
        subcommands=[
            ("add", "Add a new query to the registry"),
            ("list", "List all saved queries (interactive selection)"),
            ("show", "Show full details of a specific query"),
            ("edit", "Edit an existing query in $EDITOR"),
            ("delete", "Delete a query by name or hash"),
            ("import", "Import multiple queries from a SQL file"),
            ("run", "Run saved queries for benchmarking/load generation"),
        ],
        examples=[
            ('rdst query add my-query -q "SELECT * FROM users"', "Add a query"),
            ("rdst query list", "List all queries"),
            ('rdst query list --filter "users"', "Filter queries"),
            ("rdst query show my-query", "Show query details"),
            ("rdst query delete --hash abc123", "Delete by hash"),
        ],
    ),
    "schema": CommandDef(
        name="schema",
        short_help="Manage semantic layer for your database",
        description="""Manage the semantic layer for your database target.

The semantic layer stores metadata about your schema to improve 'rdst ask' results:
  - Table and column descriptions
  - Enum values with their meanings (e.g., status codes, category types)
  - Business terminology and relationships
  - Foreign key documentation

The more comprehensive your semantic layer, the better 'rdst ask' can generate accurate SQL.""",
        subcommand_dest="schema_subcommand",
        subcommand_defs=[
            SubcommandDef(
                name="show",
                help="Display semantic layer",
                args=[
                    ArgDef("table", nargs="?", help="Specific table to show"),
                    ArgDef("--target", help="Target database name"),
                ],
            ),
            SubcommandDef(
                name="init",
                help="Initialize semantic layer from database",
                args=[
                    ArgDef("--target", help="Target database name"),
                    ArgDef(
                        "--enum-threshold",
                        type=int,
                        default=20,
                        help="Max distinct values for enum detection",
                    ),
                    ArgDef(
                        "--force",
                        action="store_true",
                        help="Overwrite existing semantic layer",
                    ),
                    ArgDef(
                        "--interactive",
                        short="-i",
                        action="store_true",
                        help="Interactively annotate enum values",
                    ),
                ],
            ),
            SubcommandDef(
                name="edit",
                help="Edit semantic layer in $EDITOR",
                args=[
                    ArgDef("table", nargs="?", help="Specific table to focus on"),
                    ArgDef("--target", help="Target database name"),
                ],
            ),
            SubcommandDef(
                name="annotate",
                help="Annotate columns interactively",
                args=[
                    ArgDef("table", nargs="?", help="Table to annotate"),
                    ArgDef("--target", help="Target database name"),
                    ArgDef(
                        "--use-llm",
                        action="store_true",
                        help="Use LLM to suggest annotations",
                    ),
                    ArgDef(
                        "--sample-rows",
                        type=int,
                        default=5,
                        help="Sample rows for LLM context",
                    ),
                ],
            ),
            SubcommandDef(
                name="export",
                help="Export semantic layer",
                args=[
                    ArgDef("--target", help="Target database name"),
                    ArgDef(
                        "--format",
                        dest="output_format",
                        choices=["yaml", "json"],
                        default="yaml",
                        help="Output format",
                    ),
                ],
            ),
            SubcommandDef(
                name="delete",
                help="Delete semantic layer",
                args=[
                    ArgDef("--target", help="Target database name"),
                    ArgDef("--force", action="store_true", help="Skip confirmation"),
                ],
            ),
            SubcommandDef(name="list", help="List all semantic layers", args=[]),
        ],
        subcommands=[
            (
                "init",
                "Initialize from database (introspects tables, columns, detects enums)",
            ),
            ("show", "Display semantic layer for a target or specific table"),
            (
                "annotate",
                "Add descriptions interactively or with AI assistance (--use-llm)",
            ),
            ("edit", "Open semantic layer in $EDITOR for manual editing"),
            ("export", "Export as YAML or JSON"),
            ("delete", "Remove semantic layer for a target"),
            ("list", "List all configured semantic layers"),
        ],
        examples=[
            ("rdst schema init --target mydb", "Bootstrap from database"),
            (
                "rdst schema annotate --target mydb --use-llm",
                "AI-generate descriptions",
            ),
            ("rdst schema show --target mydb", "View current semantic layer"),
            ("rdst schema show --target mydb customer", "Show specific table details"),
        ],
    ),
    "report": CommandDef(
        name="report",
        short_help="Submit feedback or bug reports",
        description="""Submit feedback or bug reports about RDST.

Use this to report issues, suggest improvements, or provide feedback about
analysis results. Optionally include query details for context.""",
        args=[
            ArgDef("--hash", help="Query hash to provide feedback on"),
            ArgDef(
                "--reason",
                short="-r",
                help="Feedback reason (interactive if not provided)",
            ),
            ArgDef("--email", short="-e", help="Email for follow-up (optional)"),
            ArgDef("--positive", action="store_true", help="Mark as positive feedback"),
            ArgDef("--negative", action="store_true", help="Mark as negative feedback"),
            ArgDef(
                "--include-query",
                action="store_true",
                help="Include raw SQL in feedback",
            ),
            ArgDef(
                "--include-plan",
                action="store_true",
                help="Include execution plan in feedback",
            ),
        ],
        examples=[
            (
                'rdst report --negative -r "Index suggestion was incorrect"',
                "Report an issue",
            ),
            ('rdst report --positive -r "Great recommendation!"', "Positive feedback"),
            (
                'rdst report --hash abc123 --include-query -r "Unexpected result"',
                "Include query context",
            ),
        ],
    ),
    "help": CommandDef(
        name="help",
        short_help='Show help or ask a question (rdst help "...")',
        description="""Show help or get quick answers about how to use RDST.

Without arguments: shows general help and available commands.
With a question: uses built-in documentation to answer your question.""",
        args=[
            ArgDef(
                "question",
                nargs="*",
                help='Your question in quotes (e.g., "how do I analyze a query?")',
            ),
        ],
        examples=[
            ("rdst help", "Show general help"),
            ('rdst help "analyze a query"', "Ask a question"),
            ('rdst help "find slow queries"', "Ask a question"),
            ('rdst help "configure database"', "Ask a question"),
            ('rdst help "test readyset caching"', "Ask a question"),
        ],
    ),
    "claude": CommandDef(
        name="claude",
        short_help="Register RDST with Claude Code (MCP)",
        description="""Register RDST as an MCP server with Claude Code.

This enables Claude Code to use RDST tools directly for database analysis.
After registration, Claude can analyze queries, monitor slow queries, and
provide optimization recommendations.""",
        args=[
            ArgDef(
                "action",
                nargs="?",
                default="add",
                choices=["add", "remove"],
                help="Action: add (default) or remove",
            ),
        ],
        examples=[
            ("rdst claude add", "Register RDST with Claude Code"),
            ("rdst claude remove", "Unregister RDST from Claude Code"),
        ],
    ),
    "version": CommandDef(
        name="version",
        short_help="Show version information",
        description="Show RDST version information.",
        args=[],
        examples=[("rdst version", "Show version")],
    ),
}

COMMAND_ORDER = [
    "configure",
    "top",
    "analyze",
    "ask",
    "init",
    "query",
    "schema",
    "report",
    "help",
    "claude",
    "version",
]


# =============================================================================
# Display Helpers
# =============================================================================


def get_commands_for_table() -> List[Tuple[str, str]]:
    return [(COMMANDS[name].name, COMMANDS[name].short_help) for name in COMMAND_ORDER]


def get_main_examples() -> List[Tuple[str, str]]:
    return [
        ("rdst init", "First-time setup wizard"),
        ("rdst top --target mydb", "Monitor slow queries"),
        ('rdst analyze -q "SELECT * FROM users" --target mydb', "Analyze a query"),
        ('rdst analyze -q "SELECT ..." --readyset-cache', "Test Readyset caching"),
        ('rdst help "how do I find slow queries?"', "Quick docs lookup"),
    ]


def get_argparse_description(command: str) -> str:
    cmd = COMMANDS.get(command)
    if not cmd:
        return ""

    RESET, BOLD, DIM, CYAN = "\033[0m", "\033[1m", "\033[2m", "\033[36m"
    parts = [cmd.description]

    if cmd.subcommands:
        parts.append("")
        parts.append(f"{BOLD}Subcommands:{RESET}")
        for subcmd, desc in cmd.subcommands:
            parts.append(f"  {CYAN}{subcmd:<10}{RESET} {DIM}{desc}{RESET}")

    if cmd.examples:
        parts.append("")
        parts.append(f"{BOLD}Examples:{RESET}")
        for cmd_str, desc in cmd.examples:
            parts.append(f"  {CYAN}{cmd_str}{RESET}")
            parts.append(f"    {DIM}{desc}{RESET}")

    return "\n".join(parts)


# =============================================================================
# Parser Builders
# =============================================================================


def _add_arg_to_parser(parser_or_group, arg: ArgDef) -> None:
    kwargs: dict[str, Any] = {"help": arg.help}
    if arg.type is not None:
        kwargs["type"] = arg.type
    if arg.default is not None:
        kwargs["default"] = arg.default
    if arg.action is not None:
        kwargs["action"] = arg.action
    if arg.choices is not None:
        kwargs["choices"] = arg.choices
    if arg.nargs is not None:
        kwargs["nargs"] = arg.nargs
    if arg.dest is not None:
        kwargs["dest"] = arg.dest
    if arg.metavar is not None:
        kwargs["metavar"] = arg.metavar
    if arg.required is not None and not arg.is_positional():
        kwargs["required"] = arg.required

    if arg.is_positional():
        parser_or_group.add_argument(arg.name, **kwargs)
    else:
        names = ([arg.short] if arg.short else []) + [arg.name] + arg.aliases
        parser_or_group.add_argument(*names, **kwargs)


def _add_args_to_parser(
    parser, args: List[Union[ArgDef, MutuallyExclusiveGroup]]
) -> None:
    for item in args:
        if isinstance(item, MutuallyExclusiveGroup):
            group = parser.add_mutually_exclusive_group(required=item.required)
            for arg in item.args:
                _add_arg_to_parser(group, arg)
        else:
            _add_arg_to_parser(parser, item)


def build_subparser(subparsers, name: str, *, formatter_class=None) -> Any:
    import argparse

    if formatter_class is None:
        formatter_class = argparse.RawDescriptionHelpFormatter

    cmd = COMMANDS[name]
    parser = subparsers.add_parser(
        name,
        help=cmd.short_help,
        description=get_argparse_description(name),
        formatter_class=formatter_class,
    )
    _add_args_to_parser(parser, cmd.args)

    if cmd.subcommand_defs:
        sub_subparsers = parser.add_subparsers(
            dest=cmd.subcommand_dest, help=f"{name.capitalize()} subcommands"
        )
        for subcmd in cmd.subcommand_defs:
            sub_parser = sub_subparsers.add_parser(subcmd.name, help=subcmd.help)
            _add_args_to_parser(sub_parser, subcmd.args)

    return parser


def build_all_subparsers(subparsers, *, formatter_class=None) -> dict[str, Any]:
    return {
        name: build_subparser(subparsers, name, formatter_class=formatter_class)
        for name in COMMAND_ORDER
    }

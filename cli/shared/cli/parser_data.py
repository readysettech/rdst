"""
RDST CLI Definitions - Command structure, arguments, and help text.

Single source of truth for all CLI commands.
"""

import argparse
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
    # If true, suppress this flag from --help output (used for internal/dev plumbing).
    hidden: bool = False

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
        subcommand_dest="configure_subcommand",
        subcommand_defs=[
            SubcommandDef(
                name="add",
                help="Add a new database target",
                args=[
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
                        choices=["none", "readyset", "proxysql", "pgbouncer", "tunnel", "custom"],
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
                        "--skip-verify",
                        action="store_true",
                        help="Skip connection verification (for non-interactive use)",
                    ),
                    ArgDef(
                        "--group",
                        help="Fleet group (e.g., production, us-east-1, my-aurora-cluster)",
                    ),
                    ArgDef(
                        "--tags",
                        help="Comma-separated tags (e.g., aurora,reader)",
                    ),
                ],
            ),
            SubcommandDef(
                name="list",
                help="List all configured targets",
                args=[],
            ),
            SubcommandDef(
                name="edit",
                help="Edit an existing target",
                args=[
                    ArgDef("name", nargs="?", help="Target name to edit"),
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
                        choices=["none", "readyset", "proxysql", "pgbouncer", "tunnel", "custom"],
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
                        "--skip-verify",
                        action="store_true",
                        help="Skip connection verification (for non-interactive use)",
                    ),
                    ArgDef(
                        "--group",
                        help="Fleet group (e.g., production, us-east-1, my-aurora-cluster)",
                    ),
                    ArgDef(
                        "--tags",
                        help="Comma-separated tags (e.g., aurora,reader)",
                    ),
                ],
            ),
            SubcommandDef(
                name="remove",
                help="Remove a target",
                args=[
                    ArgDef("name", nargs="?", help="Target name to remove"),
                    ArgDef("--target", aliases=["--name"], help="Target name"),
                    ArgDef(
                        "--confirm",
                        action="store_true",
                        help="Confirm removal without prompting",
                    ),
                ],
            ),
            SubcommandDef(
                name="default",
                help="Set the default target",
                args=[
                    ArgDef("name", nargs="?", help="Target name to set as default"),
                    ArgDef("--target", aliases=["--name"], help="Target name"),
                ],
            ),
            SubcommandDef(
                name="test",
                help="Test connection to a target",
                args=[
                    ArgDef("name", nargs="?", help="Target name to test"),
                    ArgDef("--target", aliases=["--name"], help="Target name"),
                ],
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
        short_help="Monitor slow queries in real-time",
        description="""Monitor database queries in real-time and identify slow queries.

Queries are automatically saved to the registry as they're detected.
Use the displayed hash values with 'rdst analyze' to investigate further.

MySQL Sources (use with --historical):
  - digest: Query stats from performance_schema (default, always available)
  - slowlog: Individual slow queries from mysql.slow_log table
  - activity: Currently running queries from PROCESSLIST

MySQL Slow Log Setup:
  The 'slowlog' source requires enabling MySQL's slow query log with TABLE output.
  For self-hosted MySQL:
    SET GLOBAL slow_query_log = 'ON';
    SET GLOBAL long_query_time = 1;
    SET GLOBAL log_output = 'TABLE';
  For RDS/Aurora: Modify parameter group (slow_query_log=1, log_output=TABLE)
  No restart required - changes take effect immediately.""",
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
                help="Data source (implies --historical): auto (default), pg_stat (PostgreSQL), activity (both), digest (MySQL), slowlog (MySQL, requires setup)",
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
                help="Use historical statistics (pg_stat_statements/performance_schema/slowlog) instead of real-time monitoring",
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
                "View aggregated stats (pg_stat_statements / performance_schema)",
            ),
            (
                "rdst top --source slowlog --target mysql-db",
                "MySQL: Query mysql.slow_log table (requires setup)",
            ),
            (
                "rdst top --source digest --target mysql-db",
                "MySQL: Query performance_schema aggregated stats",
            ),
        ],
    ),
    "analyze": CommandDef(
        name="analyze",
        short_help="Analyze SQL query performance",
        description="""Analyze a SQL query for performance issues and get optimization recommendations.

Runs EXPLAIN ANALYZE and uses AI to provide index recommendations, query rewrites,
and ReadySet caching opportunities.""",
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
                "--json",
                action="store_true",
                help="Output results as JSON (for programmatic use)",
            ),
            ArgDef(
                "--skip-warning",
                action="store_true",
                help="Skip the EXPLAIN ANALYZE safety confirmation prompt",
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
                'rdst analyze -q "SELECT ..." --target mydb',
                "Analyze with automatic ReadySet performance test",
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
            ArgDef("--timeout", type=int, default=600, help="Query timeout in seconds (default: 600)"),
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
        short_help="Set up rdst for first use",
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
                    ArgDef("query_name", nargs="?", help="Name for the query"),
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
                    ArgDef(
                        "file",
                        nargs="?",
                        help="Path to SQL file containing multiple queries",
                    ),
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
                        required=False,
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
                        required=False,
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
                        required=False,
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
                        required=False,
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
                        nargs="*",
                        help="Query names or hashes to run (round-robin if multiple)",
                    ),
                    ArgDef(
                        "--file",
                        short="-f",
                        help="CSV file of queries to run",
                    ),
                    ArgDef(
                        "--analyze",
                        action="store_true",
                        help="LLM analysis of results",
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
                    ArgDef(
                        "--skip-warning",
                        action="store_true",
                        dest="skip_warning",
                        help="Skip the execution warning prompt",
                    ),
                ],
            ),
            SubcommandDef(
                name="cache-compare",
                help="Compare query performance: upstream vs ReadySet cache",
                args=[
                    ArgDef(
                        "queries",
                        nargs="*",
                        help="Query names or hashes to compare",
                    ),
                    ArgDef(
                        "--target",
                        short="-t",
                        help="Target database (upstream or cache — resolves automatically)",
                    ),
                    ArgDef(
                        "--interval",
                        type=int,
                        metavar="MS",
                        help="Fixed interval mode: run every N milliseconds per target",
                    ),
                    ArgDef(
                        "--concurrency",
                        short="-c",
                        type=int,
                        metavar="N",
                        help="Concurrency mode: maintain N concurrent executions per target",
                    ),
                    ArgDef(
                        "--duration",
                        type=int,
                        metavar="SECS",
                        help="Stop after N seconds per target",
                    ),
                    ArgDef(
                        "--count",
                        type=int,
                        default=100,
                        metavar="N",
                        help="Stop after N total executions per target (default: 100)",
                    ),
                    ArgDef(
                        "--quiet",
                        action="store_true",
                        help="Minimal output",
                    ),
                    ArgDef(
                        "--skip-warning",
                        action="store_true",
                        dest="skip_warning",
                        help="Skip the execution warning prompt",
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
            ("cache-compare", "Compare query performance: upstream vs ReadySet cache"),
        ],
        examples=[
            ('rdst query add my-query -q "SELECT * FROM users"', "Add a query"),
            ("rdst query list", "List all queries"),
            ('rdst query list --filter "users"', "Filter queries"),
            ("rdst query show my-query", "Show query details"),
            ("rdst query delete --hash abc123", "Delete by hash"),
            ("rdst query cache-compare my-query --count 100", "Compare upstream vs cache"),
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
                        help="LLM-guided mode: profiles data, drafts annotations, asks targeted questions",
                    ),
                    ArgDef(
                        "--auto-accept",
                        action="store_true",
                        help="Auto-accept all LLM annotations without interactive review (requires --use-llm)",
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
            SubcommandDef(
                name="refresh",
                help="Refresh structural data (indexes, columns, row counts) while preserving annotations",
                args=[
                    ArgDef("--target", help="Target database name"),
                ],
            ),
            SubcommandDef(
                name="profile",
                help="Collect and persist column statistics (null rates, distinct counts, top values)",
                args=[
                    ArgDef("table", nargs="?", help="Specific table to profile"),
                    ArgDef("--target", help="Target database name"),
                ],
            ),
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
            ("refresh", "Update indexes, columns, row estimates — keeps descriptions"),
            ("profile", "Collect column stats (null rates, distinct counts, top values)"),
        ],
        examples=[
            ("rdst schema init --target mydb", "Bootstrap from database"),
            (
                "rdst schema annotate --target mydb --use-llm",
                "AI-generate descriptions",
            ),
            ("rdst schema show --target mydb", "View current semantic layer"),
            ("rdst schema show --target mydb customer", "Show specific table details"),
            ("rdst schema refresh --target mydb", "Update indexes without losing annotations"),
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
    "demo": CommandDef(
        name="demo",
        short_help="Try RDST with a demo database",
        description="""Try RDST with a demo database (requires Docker).

Spins up a PostgreSQL container, loads the DBA StackExchange dataset (~2M rows),
and walks you through rdst features interactively.""",
        subcommand_dest="demo_subcommand",
        subcommand_defs=[
            SubcommandDef(
                name="setup",
                help="Start a PostgreSQL Docker container for the demo",
                args=[],
            ),
            SubcommandDef(
                name="load",
                help="Download and load demo data into the container",
                args=[],
            ),
            SubcommandDef(
                name="tour",
                help="Interactive guided walkthrough of rdst features",
                args=[
                    ArgDef("tour_name", nargs="?", default="quickstart",
                           help="Tour: quickstart, schema, analyze, ask, chat"),
                ],
            ),
            SubcommandDef(
                name="teardown",
                help="Stop and remove the demo container",
                args=[],
            ),
        ],
        subcommands=[
            ("setup", "Start PostgreSQL Docker container"),
            ("load", "Download and load DBA StackExchange data"),
            ("tour", "Interactive guided walkthrough"),
            ("teardown", "Stop and remove demo container"),
        ],
        examples=[
            ("rdst demo setup", "Start demo database"),
            ("rdst demo load", "Load demo data"),
            ("rdst demo tour", "Start the quickstart tour"),
            ("rdst demo tour analyze", "Tour the analyze feature"),
            ("rdst demo teardown", "Clean up"),
        ],
    ),
    "version": CommandDef(
        name="version",
        short_help="Show version information",
        description="Show RDST version information.",
        args=[],
        examples=[("rdst version", "Show version")],
    ),
    "web": CommandDef(
        name="web",
        short_help="Start the RDST web server for the web client",
        description="""Start the RDST API server for the web client.

This starts a local HTTP server that exposes the RDST API for the web interface.
The server provides REST endpoints with Server-Sent Events (SSE) for real-time
progress updates during analysis.

The CLI continues to work directly without needing the server.
The server is only required for the web client.""",
        args=[
            # Internal/dev-only UI mode selector.
            # Kept hidden from CLI help to avoid exposing local development plumbing.
            ArgDef(
                "--ui",
                choices=["auto", "dist", "none"],
                default="auto",
                help="UI mode (default: auto)",
                hidden=True,
            ),
            ArgDef(
                "--port",
                short="-p",
                type=int,
                default=8787,
                help="Port to listen on (default: 8787)",
            ),
            ArgDef(
                "--host",
                default="127.0.0.1",
                help="Host to bind to (default: 127.0.0.1)",
            ),
            # Internal/dev-only FastAPI reload flag.
            # Kept hidden from CLI help.
            ArgDef(
                "--reload",
                short="-r",
                action="store_true",
                help="Auto-reload on file changes (development mode)",
                hidden=True,
            ),
            # Internal/debug-only keyring clear flag.
            # Kept hidden from CLI help to avoid exposing maintenance plumbing.
            ArgDef(
                "--clear",
                action="store_true",
                help="Clear RDST web secure env vars from keyring and exit",
                hidden=True,
            ),
        ],
        examples=[
            ("rdst web", "Start server on localhost:8787"),
            ("rdst web --port 9000", "Start server on custom port"),
        ],
    ),
    "slack": CommandDef(
        name="slack",
        short_help="Deploy a Slack bot for database queries",
        description="""Deploy a Slack bot that answers database questions in natural language.

The bot uses Socket Mode (no public URL needed) and connects to your configured
database targets. Users can @mention the bot or DM it to ask questions.""",
        args=[
            ArgDef(
                "subcommand",
                nargs="?",
                default="list",
                choices=["setup", "start", "list", "status"],
                help="Subcommand: setup, start, list, status",
            ),
            ArgDef("--agent", short="-a", help="Agent name (for start/status)"),
        ],
        subcommands=[
            ("setup", "Interactive setup wizard"),
            ("start", "Start the bot"),
            ("list", "List configured agents"),
            ("status", "Show agent status"),
        ],
        examples=[
            ("rdst slack setup", "Interactive setup wizard"),
            ("rdst slack start --agent sales-bot", "Start the bot"),
            ("rdst slack list", "List configured agents"),
            ("rdst slack status --agent sales-bot", "Show agent status"),
        ],
    ),
    "agent": CommandDef(
        name="agent",
        short_help="Manage and run data agents with safety policies",
        description="""Data agents provide safe, scalable database access for AI applications.

Create named agents that wrap your database targets with safety policies,
then expose them via HTTP API, MCP, or Slack.""",
        subcommand_dest="agent_subcommand",
        subcommand_defs=[
            SubcommandDef(
                name="create",
                help="Create a new data agent",
                args=[
                    ArgDef("--name", short="-n", help="Agent name"),
                    ArgDef("--target", short="-t", help="Database target"),
                    ArgDef("--description", short="-d", default="", help="Agent description"),
                    ArgDef("--max-rows", type=int, default=1000, help="Maximum rows to return (default 1000)"),
                    ArgDef("--timeout", type=int, default=600, help="Query timeout in seconds (default 600 = 10 min)"),
                    ArgDef("--deny-columns", nargs="*", help="Column patterns to deny access"),
                    ArgDef("--allow-tables", nargs="*", help="Tables to allow (whitelist)"),
                    ArgDef("--guard", short="-g", help="Guard to apply (created via rdst guard create)"),
                ],
            ),
            SubcommandDef(
                name="list",
                help="List all configured agents",
                args=[],
            ),
            SubcommandDef(
                name="show",
                help="Show agent details",
                args=[
                    ArgDef("agent_name", nargs="?", help="Agent name"),
                    ArgDef("--name", short="-n", help="Agent name"),
                ],
            ),
            SubcommandDef(
                name="delete",
                help="Delete an agent",
                args=[
                    ArgDef("agent_name", nargs="?", help="Agent name"),
                    ArgDef("--name", short="-n", help="Agent name"),
                ],
            ),
            SubcommandDef(
                name="chat",
                help="Interactive chat with an agent",
                args=[
                    ArgDef("--name", short="-n", help="Agent name"),
                    ArgDef("--verbose", action="store_true", help="Show full SQL and results (default: compact)"),
                ],
            ),
            SubcommandDef(
                name="serve",
                help="Start HTTP API server",
                args=[
                    ArgDef("--name", short="-n", help="Agent name"),
                    ArgDef("--port", short="-p", type=int, default=8080, help="HTTP port (default: 8080)"),
                    ArgDef("--verbose", action="store_true", help="Verbose output"),
                ],
            ),
            SubcommandDef(
                name="mcp",
                help="Start MCP server mode",
                args=[
                    ArgDef("--name", short="-n", help="Agent name"),
                ],
            ),
            SubcommandDef(
                name="slack",
                help="Start Slack bot mode",
                args=[
                    ArgDef("--name", short="-n", help="Agent name"),
                ],
            ),
        ],
        subcommands=[
            ("create", "Create a new data agent"),
            ("list", "List all configured agents"),
            ("show", "Show agent details"),
            ("delete", "Delete an agent"),
            ("chat", "Interactive chat with an agent"),
            ("serve", "Start HTTP API server"),
            ("mcp", "Start MCP server mode"),
            ("slack", "Start Slack bot mode"),
        ],
        examples=[
            ('rdst agent create --name sales-agent --target prod-db --description "Sales data"', "Create agent"),
            ("rdst agent list", "List agents"),
            ("rdst agent chat --name sales-agent", "Interactive chat"),
            ("rdst agent serve --name sales-agent --port 8080", "Start HTTP server"),
        ],
    ),
    "guard": CommandDef(
        name="guard",
        short_help="Manage reusable safety policies",
        description="""Guards define reusable safety policies for data agents.

A guard specifies output masking, query restrictions, and execution limits
that can be applied to one or more agents.""",
        subcommand_dest="guard_subcommand",
        subcommand_defs=[
            SubcommandDef(
                name="create",
                help="Create a new guard",
                args=[
                    ArgDef("--name", short="-n", help="Guard name"),
                    ArgDef("--description", short="-d", default="", help="Guard description"),
                    ArgDef("--mask", action="append", metavar="PATTERN:TYPE", help='Add masking pattern (e.g., "*.email:email", "*.ssn:redact")'),
                    ArgDef("--deny-columns", nargs="*", help="Column patterns to deny access"),
                    ArgDef("--allow-tables", nargs="*", help="Tables to allow (whitelist)"),
                    ArgDef("--require-where", action="store_true", help="Require WHERE clause"),
                    ArgDef("--require-limit", action="store_true", help="Require LIMIT clause"),
                    ArgDef("--no-select-star", action="store_true", help="Disallow SELECT *"),
                    ArgDef("--max-tables", type=int, help="Maximum tables in JOIN"),
                    ArgDef("--cost-limit", type=int, help="EXPLAIN cost threshold"),
                    ArgDef("--max-estimated-rows", type=int, help="Max rows from EXPLAIN estimate"),
                    ArgDef("--required-filters", action="append", metavar="TABLE:COLS", help='Require filter on columns (e.g., "users:id,email")'),
                    ArgDef("--intent", help="Natural language policy intent (LLM derives rules)"),
                    ArgDef("--schema-context", help="Database schema context for intent derivation"),
                    ArgDef("--max-rows", type=int, default=1000, help="Maximum rows to return"),
                    ArgDef("--timeout", type=int, default=30, help="Query timeout in seconds"),
                ],
            ),
            SubcommandDef(
                name="list",
                help="List all configured guards",
                args=[],
            ),
            SubcommandDef(
                name="show",
                help="Show guard details",
                args=[
                    ArgDef("guard_name", nargs="?", help="Guard name"),
                    ArgDef("--name", short="-n", help="Guard name"),
                ],
            ),
            SubcommandDef(
                name="delete",
                help="Delete a guard",
                args=[
                    ArgDef("guard_name", nargs="?", help="Guard name"),
                    ArgDef("--name", short="-n", help="Guard name"),
                ],
            ),
            SubcommandDef(
                name="edit",
                help="Edit guard in $EDITOR",
                args=[
                    ArgDef("guard_name", nargs="?", help="Guard name"),
                    ArgDef("--name", short="-n", help="Guard name"),
                ],
            ),
            SubcommandDef(
                name="check",
                help="Test SQL against a guard (pre-flight validation)",
                args=[
                    ArgDef("sql", nargs="?", help="SQL to check"),
                    ArgDef("--sql", dest="sql_flag", help="SQL to check (alternative to positional)"),
                    ArgDef("--guard", short="-g", dest="check_guard", help="Guard to check against"),
                    ArgDef("--target", short="-t", help="Target database (for cost estimation)"),
                ],
            ),
        ],
        subcommands=[
            ("create", "Create a new guard"),
            ("list", "List all configured guards"),
            ("show", "Show guard details"),
            ("delete", "Delete a guard"),
            ("edit", "Edit guard in $EDITOR"),
            ("check", "Test SQL against a guard (pre-flight validation)"),
        ],
        examples=[
            ('rdst guard create --name pii-safe --mask "*.email:email" --require-where', "Create guard"),
            ("rdst guard list", "List guards"),
            ("rdst guard show pii-safe", "Show guard details"),
            ('rdst guard check "SELECT * FROM users" --guard pii-safe', "Check SQL"),
            ("rdst agent create --name bot --target prod --guard pii-safe", "Create agent with guard"),
        ],
    ),
    "scan": CommandDef(
        name="scan",
        short_help="Scan codebase for ORM queries (experimental)",
        description="""Scan a codebase directory for ORM queries and analyze them.

This command finds SQL queries in ORM code and can optionally analyze them for
performance issues.

Supported ORMs: SQLAlchemy, Django ORM, Prisma, Drizzle

Modes:
  Default              Scan and list all queries found
  --analyze            Deep analysis with EXPLAIN ANALYZE (requires DB connection)
  --analyze --shallow  Schema-only analysis, no DB connection needed
  --check              CI mode with exit codes (0=pass, 1=fail)
  --diff               Only scan files changed in git (uncommitted changes)""",
        args=[
            ArgDef("directory", nargs="?", default=".", help="Directory to scan"),
            ArgDef("--dry-run", action="store_true", help="Show what would be scanned without scanning"),
            ArgDef("--analyze", action="store_true", help="Analyze queries for performance issues (runs EXPLAIN ANALYZE, requires DB connection)"),
            ArgDef("--shallow", action="store_true", help="Schema-only analysis, no DB connection needed (use with --analyze)"),
            ArgDef("--schema", dest="target", help="Target name for schema context"),
            ArgDef("--output", choices=["table", "json"], default="table", help="Output format"),
            ArgDef("--diff", metavar="REF", help="Only scan changed files since REF. Use HEAD for uncommitted, HEAD~1 for last commit, or any commit/branch"),
            ArgDef("--check", action="store_true", help="CI mode: exit 1 if issues found"),
            ArgDef("--warn-threshold", type=int, default=50, help="Risk score threshold for warnings (0-100)"),
            ArgDef("--fail-threshold", type=int, default=30, help="Risk score threshold for failures (0-100)"),
            ArgDef("--file-pattern", help="Glob pattern to filter files (e.g., '*.py')"),
            ArgDef("--nosave", action="store_true", help="Don't save queries to the registry"),
            ArgDef("--sequential", action="store_true", help="Run analysis queries one at a time (more deterministic scores, slower)"),
        ],
        examples=[
            ("rdst scan ./backend --schema mydb", "Scan directory for ORM queries"),
            ("rdst scan /path/to/app/services --schema mydb", "Scan specific subdirectory"),
            ("rdst scan . --analyze --schema mydb", "Deep analysis (EXPLAIN ANALYZE)"),
            ("rdst scan . --analyze --shallow --schema mydb", "Schema-only analysis"),
            ("rdst scan . --diff HEAD --analyze --schema mydb", "Analyze only changed files"),
            ("rdst scan . --check --analyze --schema mydb", "CI mode with exit codes"),
            ("rdst scan ./backend --schema mydb --nosave", "Scan without saving to registry"),
            ("rdst scan . --analyze --schema mydb --sequential", "Deterministic analysis (one query at a time)"),
        ],
    ),
    "cache": CommandDef(
        name="cache",
        short_help="Deploy and manage ReadySet shallow caches",
        description="""Deploy ReadySet and manage shallow caches.

Use 'cache deploy' to deploy a ReadySet instance, then 'cache add/show/delete'
to manage cached queries. After deploying, a ReadySet target is automatically
registered (e.g., mydb-cache). Use that target name with cache commands.""",
        subcommand_dest="cache_subcommand",
        subcommand_defs=[
            SubcommandDef(
                name="deploy",
                help="Deploy ReadySet cache to local or remote environment",
                args=[
                    ArgDef("--target", required=True, help="Database target to deploy for"),
                    ArgDef(
                        "--mode",
                        choices=["docker", "systemd", "kubernetes"],
                        required=True,
                        help="Deployment mode: docker, systemd, or kubernetes",
                    ),
                    ArgDef(
                        "--config",
                        choices=["readyset", "readyset-squeepy"],
                        default="readyset",
                        dest="deploy_config",
                        help="Deployment config: readyset (default) or readyset-squeepy",
                    ),
                    ArgDef("--host", help="Remote host for SSH deployment (omit for local)"),
                    ArgDef("--ssh-key", help="SSH private key path"),
                    ArgDef("--ssh-user", default="root", help="SSH username (default: root)"),
                    ArgDef("--port", type=int, help="ReadySet listen port"),
                    ArgDef("--namespace", default="readyset", help="Kubernetes namespace"),
                    ArgDef("--kubeconfig", help="Path to kubeconfig file for Kubernetes deployment"),
                    ArgDef(
                        "--script-only",
                        action="store_true",
                        help="Generate deployment script without executing",
                    ),
                    ArgDef("--json", action="store_true", dest="output_json", help="JSON output"),
                ],
            ),
            SubcommandDef(
                name="add",
                help="Create a shallow cache for a query",
                args=[
                    ArgDef("query", nargs="?", help="SQL query or registry hash_id (12 hex chars)"),
                    ArgDef("--target", required=True, help="ReadySet target name"),
                    ArgDef("--tag", help="Tag for query in registry"),
                    ArgDef("--dry-run", action="store_true", help="Check if query is cacheable without creating it"),
                    ArgDef("--json", action="store_true", dest="json_output", help="JSON output"),
                ],
            ),
            SubcommandDef(
                name="show",
                help="List cached queries in ReadySet",
                args=[
                    ArgDef("--target", required=True, help="ReadySet target name"),
                    ArgDef("--json", action="store_true", dest="json_output", help="JSON output"),
                ],
            ),
            SubcommandDef(
                name="delete",
                help="Remove a cache from ReadySet by cache ID",
                args=[
                    ArgDef("cache_id", help="Cache ID (from 'rdst cache show')"),
                    ArgDef("--target", required=True, help="ReadySet target name"),
                    ArgDef("--json", action="store_true", dest="json_output", help="JSON output"),
                ],
            ),
            SubcommandDef(
                name="drop-all",
                help="Remove all caches from ReadySet",
                args=[
                    ArgDef("--target", required=True, help="ReadySet target name"),
                    ArgDef("--yes", short="-y", action="store_true", help="Skip confirmation prompt"),
                    ArgDef("--json", action="store_true", dest="json_output", help="JSON output"),
                ],
            ),
            SubcommandDef(
                name="remove",
                help="Remove cache deployment (stops local Docker container and removes target)",
                args=[
                    ArgDef("--target", help="Target to remove cache for (upstream or cache name, defaults to default target)"),
                    ArgDef("--yes", short="-y", action="store_true", help="Skip confirmation prompt"),
                    ArgDef("--json", action="store_true", dest="json_output", help="JSON output"),
                ],
            ),
        ],
        subcommands=[
            ("deploy", "Deploy ReadySet cache"),
            ("add", "Create a shallow cache for a query"),
            ("show", "List all cached queries"),
            ("delete", "Remove a cache by ID"),
            ("drop-all", "Remove all caches"),
            ("remove", "Remove cache deployment (stops container + removes target)"),
        ],
        examples=[
            ("rdst cache deploy --target mydb --mode docker", "Deploy locally (Docker)"),
            ("rdst cache deploy --target mydb --mode systemd", "Deploy as systemd service"),
            ("rdst cache deploy --target mydb --mode docker --host 10.0.1.50", "Deploy to remote server"),
            ("rdst cache deploy --target mydb --mode kubernetes", "Deploy to Kubernetes"),
            ("rdst cache deploy --target mydb --mode docker --script-only", "Generate script only"),
            ("rdst cache add abc123def456 --target mydb-cache", "Cache query by registry hash"),
            ('rdst cache add "SELECT * FROM orders WHERE id = ?" --target mydb-cache', "Cache direct SQL"),
            ("rdst cache show --target mydb-cache", "List all caches"),
            ("rdst cache delete q_abc123 --target mydb-cache", "Remove a cache"),
            ("rdst cache drop-all --target mydb-cache --yes", "Remove all caches"),
        ],
    ),
    # =========================================================================
    # Fleet — Multi-target management and fleet-wide audit
    # =========================================================================
    "fleet": CommandDef(
        name="fleet",
        short_help="Manage and audit database fleets",
        description="Import, discover, and audit multiple database targets as a fleet.",
        subcommand_dest="fleet_subcommand",
        subcommand_defs=[
            SubcommandDef(
                name="configure",
                help="Interactive fleet configuration",
                args=[
                    ArgDef("--from", dest="csv_file", help="Import from CSV file"),
                    ArgDef("--password-env", default="FLEET_PASS", help="Default password env var"),
                    ArgDef("--discover", action="store_true", help="Discover from AWS"),
                    ArgDef("--group", help="Default group for new targets"),
                ],
            ),
            SubcommandDef(
                name="import",
                help="Bulk import targets from CSV file",
                args=[
                    ArgDef("--from", dest="csv_file", help="Path to CSV file"),
                    ArgDef(
                        "--password-env",
                        default="FLEET_PASS",
                        help="Env var holding fleet password (default: FLEET_PASS)",
                    ),
                    ArgDef("--group", help="Assign all imported targets to this group"),
                    ArgDef("--tag", action="append", dest="tags", help="Add tag (repeatable)"),
                    ArgDef("--dry-run", action="store_true", help="Preview without saving"),
                ],
            ),
            SubcommandDef(
                name="discover",
                help="Discover RDS instances from AWS",
                args=[
                    ArgDef("--regions", help="Comma-separated AWS regions"),
                    ArgDef(
                        "--engine-filter",
                        choices=["postgresql", "mysql", "all"],
                        default="all",
                        help="Filter by engine type",
                    ),
                    ArgDef("--name-pattern", help="Glob pattern for instance names"),
                    ArgDef(
                        "--password-env",
                        default="FLEET_PASS",
                        help="Env var for fleet password",
                    ),
                    ArgDef("--user", help="DB username (default: postgres/admin)"),
                    ArgDef("--group", help="Assign discovered targets to this group"),
                    ArgDef("--dry-run", action="store_true", help="Preview without saving"),
                ],
            ),
            SubcommandDef(
                name="list",
                help="List fleet members",
                args=[
                    ArgDef("--group", help="Filter by group"),
                    ArgDef("--tag", help="Filter by tag"),
                    ArgDef("--json", action="store_true", dest="output_json", help="JSON output"),
                ],
            ),
            SubcommandDef(
                name="status",
                help="Check fleet connectivity",
                args=[
                    ArgDef("--group", help="Filter by group"),
                    ArgDef("--tag", help="Filter by tag"),
                    ArgDef("--json", action="store_true", dest="output_json", help="JSON output"),
                ],
            ),
            SubcommandDef(
                name="audit",
                help="Run audit across fleet targets",
                args=[
                    ArgDef("--group", help="Filter by group"),
                    ArgDef("--tag", help="Filter by tag"),
                    ArgDef("--duration", help="Live capture duration per target (e.g., 2m, 5m)"),
                    ArgDef("--save", dest="save_name", help="Save snapshot with name"),
                    ArgDef("--no-save", action="store_true", dest="no_save", help="Don't auto-save snapshot"),
                    ArgDef("--diff", dest="diff_baseline", help="Compare against saved snapshot"),
                    ArgDef("--no-insights", action="store_true", help="Skip LLM fleet insights"),
                    ArgDef("--json", action="store_true", dest="output_json", help="JSON output"),
                    ArgDef("--verbose", short="-v", action="store_true", dest="verbose", help=argparse.SUPPRESS),
                    ArgDef("--yes", short="-y", action="store_true", dest="auto_yes", help="Auto-accept Readyset cache deployment (no prompt)"),
                ],
            ),
            SubcommandDef(
                name="diff",
                help="Compare two fleet audit snapshots",
                args=[
                    ArgDef("snapshot1", help="First snapshot name or ID"),
                    ArgDef("snapshot2", help="Second snapshot name or ID"),
                    ArgDef("--json", action="store_true", dest="output_json", help="JSON output"),
                ],
            ),
            SubcommandDef(
                name="snapshots",
                help="List saved audit snapshots",
                args=[
                    ArgDef("--json", action="store_true", dest="output_json", help="JSON output"),
                ],
            ),
        ],
        subcommands=[
            ("configure", "Configure fleet targets"),
            ("import", "Bulk import targets from CSV"),
            ("discover", "Discover RDS instances from AWS"),
            ("list", "List fleet members"),
            ("status", "Check fleet connectivity"),
            ("audit", "Run fleet-wide audit"),
            ("diff", "Compare two audit snapshots"),
            ("snapshots", "List saved snapshots"),
        ],
        examples=[
            ("rdst fleet import --from fleet.csv --password-env FLEET_PASS", "Import from CSV"),
            ("rdst fleet discover --regions us-east-1,us-west-2", "Discover from AWS"),
            ("rdst fleet list --group production", "List production fleet"),
            ("rdst fleet status", "Check all fleet connectivity"),
            ("rdst fleet audit --save march-baseline", "Audit fleet and save snapshot"),
            ("rdst fleet diff march-baseline april-baseline", "Compare snapshots"),
        ],
    ),
    # =========================================================================
    # Audit — Single-target deep health audit
    # =========================================================================
    "audit": CommandDef(
        name="audit",
        short_help="Run a deep health audit of a database target",
        description="Run a deep health audit on a single database target. Collects metrics, sizing assessment, and cache opportunity score.",
        args=[
            ArgDef("audit_subcommand", nargs="?", default=None,
                   help="Subcommand: list, show"),
            ArgDef("run_id", nargs="?", default=None,
                   help="Run ID for 'audit show' (find IDs with 'audit list')"),
            ArgDef("--target", short="-t", help="Target name"),
            ArgDef("--no-insights", action="store_true", dest="no_insights", help="Skip LLM analysis"),
            ArgDef("--duration", help="Live capture duration (e.g., 30s, 5m, 1h)"),
            ArgDef("--source", choices=["auto", "pg_stat_statements", "activity"], default="auto", help="Query capture source"),
            ArgDef("--limit", type=int, default=50, help="Top N queries for analysis (default: 50)"),
            ArgDef("--no-save", action="store_true", dest="no_save", help="Don't save queries to registry"),
            ArgDef("--save", dest="save_name", help="Save result with name"),
            ArgDef("--diff", dest="diff_baseline", help="Compare against saved baseline"),
            ArgDef("--export-queries", action="store_true", dest="export_queries", help="Export captured queries (or top queries if no capture)"),
            ArgDef("--export-top-queries", action="store_true", dest="export_top_queries", help="Export cumulative top queries from stats"),
            ArgDef("--export-captured-queries", action="store_true", dest="export_captured_queries", help="Export queries captured during --duration window"),
            ArgDef("--json", action="store_true", dest="output_json", help="JSON output"),
            ArgDef("--verbose", short="-v", action="store_true", dest="verbose", help=argparse.SUPPRESS),
            ArgDef("--yes", short="-y", action="store_true", dest="auto_yes", help="Auto-accept Readyset cache deployment (no prompt)"),
        ],
        subcommands=[
            ("list", "List past audit runs"),
            ("show <run_id>", "View a past audit run"),
        ],
        examples=[
            ("rdst audit --target mydb", "Audit a database"),
            ("rdst audit --target mydb --duration 5m", "Audit with live capture"),
            ("rdst audit --target mydb --no-insights", "Skip LLM analysis"),
            ("rdst audit list", "List past audits (find run IDs here)"),
            ("rdst audit list --target mydb", "List audits for a specific target"),
            ("rdst audit show audit_mydb_20260325", "View a saved audit"),
            ("rdst audit show audit_mydb_20260325 --export-captured-queries", "Export full query text"),
            ("rdst audit --target mydb --json", "JSON output"),
        ],
    ),
}

COMMAND_ORDER = [
    "configure",
    "top",
    "analyze",
    "ask",
    "agent",
    "init",
    "query",
    "schema",
    "slack",
    "guard",
    "scan",
    "cache",
    "fleet",
    "audit",
    "demo",
    "report",
    "help",
    "claude",
    "web",
    "version",
]

COMMAND_GROUPS: list[tuple[str, list[str]]] = [
    ("Analysis", ["top", "analyze", "ask", "agent"]),
    ("Configuration", ["init", "configure", "schema", "query", "guard"]),
    ("Cache & Fleet", ["cache", "fleet", "audit"]),
    ("Integrations", ["claude", "slack", "web"]),
    ("Other", ["demo", "scan", "report", "help", "version"]),
]


# =============================================================================
# Display Helpers
# =============================================================================


def get_commands_for_table() -> List[Tuple[str, str]]:
    return [(COMMANDS[name].name, COMMANDS[name].short_help) for name in COMMAND_ORDER]


def get_grouped_commands() -> list[tuple[str, list[tuple[str, str]]]]:
    """Return commands organized by group for display."""
    return [
        (group, [(COMMANDS[name].name, COMMANDS[name].short_help) for name in cmds])
        for group, cmds in COMMAND_GROUPS
    ]


def get_main_examples() -> List[Tuple[str, str]]:
    return [
        ("rdst init", "First-time setup wizard"),
        ("rdst top --target mydb", "Monitor slow queries"),
        ('rdst analyze -q "SELECT * FROM users" --target mydb', "Analyze a query"),
        ('rdst query cache-compare my-query --target mydb', "Compare upstream vs ReadySet cache performance"),
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
    if getattr(arg, "hidden", False):
        import argparse

        kwargs["help"] = argparse.SUPPRESS
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
            sub_parser = sub_subparsers.add_parser(
                subcmd.name, help=subcmd.help, description=subcmd.help,
            )
            _add_args_to_parser(sub_parser, subcmd.args)

    return parser


def build_all_subparsers(subparsers, *, formatter_class=None) -> dict[str, Any]:
    return {
        name: build_subparser(subparsers, name, formatter_class=formatter_class)
        for name in COMMAND_ORDER
    }

#!/usr/bin/env python3
"""
RDST MCP Server - Model Context Protocol server for RDST (Readyset Data and SQL Toolkit)

This server exposes RDST functionality to AI assistants like Claude through the MCP protocol.
It provides tools for database diagnostics, query analysis, and performance tuning.

INSTALLATION:
    pip install rdst

REGISTRATION WITH CLAUDE CODE:
    claude mcp add rdst -- python3 -m rdst.mcp_server

WHAT THIS PROVIDES:
    - Database target configuration management
    - SQL query analysis with AI-powered recommendations
    - Live slow query monitoring (top)
    - Query registry management
    - Performance tuning suggestions

CONFIG LOCATION:
    ~/.rdst/config.toml - Contains database targets and settings
    ~/.rdst/queries.toml - Query registry storage

PASSWORD HANDLING:
    Passwords are NEVER stored in config files. Instead, each target specifies a
    password_env field containing the name of an environment variable that holds
    the password. Before running RDST commands, ensure the required environment
    variables are exported.
"""

import json
import sys
import os
import subprocess
from typing import Any, Dict, List, Optional

# MCP Protocol constants
JSONRPC_VERSION = "2.0"
MCP_VERSION = "2024-11-05"

# Single source of truth for the slow query workflow
SLOW_QUERY_WORKFLOW = """1. `rdst_query_list` → Check saved queries FIRST
2. If queries exist → `rdst_analyze` with `name` parameter
3. If empty → `rdst_top` (defaults to historical, INSTANT)
4. ONLY if historical empty → `rdst_top` with `duration=30`
5. Then `rdst_analyze` with `name` or `hash`
6. If STILL no queries found → Ask user to provide a slow query, then use `rdst_analyze` with `query` parameter"""

SLOW_QUERY_WORKFLOW_DETAILED = f"""### MANDATORY Workflow for Slow Query Analysis

**CRITICAL: Follow this exact order. Do NOT skip steps or go straight to live monitoring.**

{SLOW_QUERY_WORKFLOW}

**WRONG**: Going straight to `rdst_top` without checking registry first
**WRONG**: Suggesting users run CLI commands manually when MCP tools work"""

# RDST context information for the AI
RDST_CONTEXT = f"""
## RDST (Readyset Data and SQL Toolkit) - Context for AI Assistants

### What is RDST?
RDST is a command-line tool for database diagnostics and SQL query optimization.
It connects to PostgreSQL or MySQL databases and provides AI-powered analysis
of query performance, index recommendations, and caching suggestions.

### Installation & Upgrade
- Install: `pip install rdst`
- Upgrade: `pip install --upgrade rdst`
- Check version: `rdst version`

{SLOW_QUERY_WORKFLOW_DETAILED}

### CRITICAL: COMMAND SYNTAX
RDST CLI uses SUBCOMMANDS (not flags):

CORRECT:
- `rdst configure list` (subcommand)
- `rdst query list` (subcommand)
- `rdst analyze --name my-query` (analyze saved query by name)
- `rdst analyze --hash abc123` (analyze saved query by hash)
- `rdst analyze -q "SELECT ..."` (analyze inline query)

WRONG - DO NOT USE:
- `rdst configure --list` (WRONG - no such flag)
- `rdst query --list` (WRONG - no such flag)
- `rdst analyze --query-id X` (WRONG - use --name or --hash)
- `rdst_help` as CLI command (WRONG - MCP tool only, `rdst help` is the CLI command)

### MCP Tools vs CLI Commands
MCP tool names use underscores; CLI commands use spaces:
- MCP `rdst_query_list` → CLI `rdst query list`
- MCP `rdst_analyze` → CLI `rdst analyze`
- MCP `rdst_help` → CLI `rdst help`

### What is RDST?
A CLI tool for database diagnostics and SQL optimization. Connects to PostgreSQL
or MySQL and provides AI-powered query analysis, index recommendations, and
caching suggestions.

### Configuration
- Config: `~/.rdst/config.toml`
- Query registry: `~/.rdst/queries.toml`
- Conversation history: `~/.rdst/conversations/`

### Password Handling (CRITICAL)
RDST never stores passwords in config files. Instead, each database target
specifies a `password_env` field with the name of an environment variable.

Example config entry:
```toml
[targets.my-database]
name = "my-database"
engine = "postgresql"
host = "db.example.com"
port = 5432
user = "admin"
database = "mydb"
password_env = "MY_DB_PASSWORD"  # <-- User must export this env var
```

Before running commands, the user must:
```bash
export MY_DB_PASSWORD="actual-password-here"
```

If a command fails with authentication error, check:
1. Is the password_env variable exported?
2. Is the password correct?
3. Can the host/port be reached?

### LLM Setup (CRITICAL)
RDST requires an LLM provider for query analysis. Two options:

**Option 1: RDST Free Trial (Recommended for new users)**
- Run `rdst configure llm` in terminal (INTERACTIVE - cannot be done via MCP)
- Select "Sign up for free RDST trial"
- Enter email → receive verification code → enter code
- Business emails get $5.00 in credits, personal emails get $1.50
- No API key needed after setup - RDST uses a trial proxy
- Check balance anytime: `rdst configure llm`

**Option 2: Your Own Anthropic API Key**
- Export: `export ANTHROPIC_API_KEY="sk-ant-..."`
- No credit limits, direct API access

If a user has no ANTHROPIC_API_KEY and hasn't set up a trial, tell them to run
`rdst configure llm` in their terminal to set up free trial credits.

### Common CLI Workflows

1. **First-time setup**: `rdst init` - Interactive wizard
2. **Add a database target**: `rdst configure add --target NAME --engine postgresql --host HOST --port PORT --user USER --database DB --password-env VAR_NAME`
3. **List targets**: `rdst configure list`
4. **Analyze a query**: `rdst analyze -q "SELECT * FROM users WHERE id = 1" --target my-target`
5. **Save query for later**: `rdst query add my-query -q "SELECT ..." --target my-target`

### CLI-Only Features (Tell user to run in terminal)
- `rdst ask "question" --target mydb` - Natural language to SQL
- `rdst schema annotate --target mydb` - Add descriptions
- `rdst schema edit --target mydb` - Edit in $EDITOR
"""


def read_message() -> Optional[Dict[str, Any]]:
    """Read a JSON-RPC message from stdin."""
    try:
        line = sys.stdin.readline()
        if not line:
            return None
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def write_message(message: Dict[str, Any]) -> None:
    """Write a JSON-RPC message to stdout."""
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def make_response(id: Any, result: Any) -> Dict[str, Any]:
    """Create a JSON-RPC response."""
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": id,
        "result": result
    }


def make_error(id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    """Create a JSON-RPC error response."""
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": id,
        "error": error
    }


def _get_rdst_command() -> List[str]:
    """Get the command to run RDST.

    Prefers local rdst.py (development mode) over installed rdst command.
    This ensures MCP uses the same version as the local development environment.
    """
    # Check for rdst.py in the same directory as this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_rdst = os.path.join(script_dir, "rdst.py")

    if os.path.exists(local_rdst):
        # Use local development version
        return [sys.executable, local_rdst]

    # Fall back to installed rdst command
    return ["rdst"]


def run_rdst_command(args: List[str]) -> Dict[str, Any]:
    """Execute an RDST CLI command and return the result."""
    try:
        cmd = _get_rdst_command() + args
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout for long-running commands
            env=os.environ.copy()  # Pass through environment variables
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": "Command timed out after 5 minutes",
            "returncode": -1
        }
    except FileNotFoundError:
        return {
            "success": False,
            "stdout": "",
            "stderr": "RDST not found. Install with: pip install rdst",
            "returncode": -1
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "returncode": -1
        }


def get_tools() -> List[Dict[str, Any]]:
    """Return the list of available MCP tools."""
    return [
        {
            "name": "rdst_configure_add",
            "description": """Add a new database target configuration to RDST.

This creates a connection profile that RDST will use to connect to your database.
The password is NOT stored - instead you specify an environment variable name
that will contain the password at runtime.

IMPORTANT: After adding a target, the user must export the password environment
variable before running other RDST commands:
  export PASSWORD_ENV_NAME="actual-password"

Example:
  rdst_configure_add(
    target="prod-db",
    engine="postgresql",
    host="db.example.com",
    port=5432,
    user="admin",
    database="myapp",
    password_env="PROD_DB_PASSWORD"
  )

Then user runs: export PROD_DB_PASSWORD="secret123"
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Unique name for this database target (e.g., 'prod-db', 'staging')"
                    },
                    "engine": {
                        "type": "string",
                        "enum": ["postgresql", "mysql"],
                        "description": "Database engine type"
                    },
                    "host": {
                        "type": "string",
                        "description": "Database host address (IP or hostname)"
                    },
                    "port": {
                        "type": "integer",
                        "description": "Database port (default: 5432 for PostgreSQL, 3306 for MySQL)"
                    },
                    "user": {
                        "type": "string",
                        "description": "Database username"
                    },
                    "database": {
                        "type": "string",
                        "description": "Database name to connect to"
                    },
                    "password_env": {
                        "type": "string",
                        "description": "Name of environment variable containing the password (NOT the password itself)"
                    },
                    "make_default": {
                        "type": "boolean",
                        "description": "Set as the default target for commands"
                    }
                },
                "required": ["target", "engine", "host", "user", "database", "password_env"]
            }
        },
        {
            "name": "rdst_configure_list",
            "description": """List all configured database targets.

Shows all database connection profiles that have been configured in RDST.
For each target, displays: name, engine (postgresql/mysql), host, port,
database name, and which environment variable holds the password.

The output also indicates which target is set as the default.

Use this to:
- See what databases are configured
- Find the password_env variable name for a target
- Identify the default target
""",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "rdst_configure_remove",
            "description": """Remove a database target configuration.

Deletes the specified target from RDST configuration. This only removes
the configuration - it does not affect the actual database.

Use --confirm to skip the interactive confirmation prompt.
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Name of the target to remove"
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "Skip confirmation prompt"
                    }
                },
                "required": ["target"]
            }
        },
        {
            "name": "rdst_configure_llm",
            "description": """Configure the LLM (AI) provider for RDST analysis.

RDST uses AI to analyze query execution plans and provide recommendations.
By default, it uses Claude (Anthropic). This command configures the AI provider.

SUPPORTED PROVIDERS:
- claude: Anthropic's Claude (default, requires ANTHROPIC_API_KEY env var)
- openai: OpenAI's GPT models (requires OPENAI_API_KEY env var)
- lmstudio: Local LM Studio server (no API key needed)
- trial: RDST free trial credits (no API key needed)

TRIAL SIGNUP (INTERACTIVE - must be done in user's terminal):
Users who don't have an ANTHROPIC_API_KEY can sign up for free RDST trial credits.
The trial signup is INTERACTIVE and cannot be done via MCP. Tell the user:
  "Run `rdst configure llm` in your terminal and select the free trial option"

The trial flow:
1. User runs `rdst configure llm` in their terminal
2. Selects "Sign up for free RDST trial"
3. Enters their email address
4. Gets a verification code sent to their email
5. Enters the code
6. Gets free credits ($5.00 for business emails, $1.50 for personal emails)
7. No ANTHROPIC_API_KEY needed after this - RDST uses a trial proxy

EXAMPLES:
  rdst configure llm --provider claude --model claude-sonnet-4-6
  rdst configure llm --provider openai --model gpt-4
  rdst configure llm --provider lmstudio --base-url http://localhost:1234
  rdst configure llm  (interactive - includes trial signup option)

REQUIRED ENV VARS:
- For Claude: export ANTHROPIC_API_KEY="sk-ant-..."
- For OpenAI: export OPENAI_API_KEY="sk-..."
- For LM Studio: No API key needed, just base_url
- For Trial: No env var needed - credits are managed by RDST
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": ["claude", "openai", "lmstudio", "trial"],
                        "description": "LLM provider to use. Use 'trial' for free RDST trial credits (but trial signup is INTERACTIVE - tell user to run `rdst configure llm` in their terminal instead)"
                    },
                    "model": {
                        "type": "string",
                        "description": "Model name (e.g., claude-sonnet-4-6, gpt-4)"
                    },
                    "base_url": {
                        "type": "string",
                        "description": "Base URL for API (required for lmstudio, optional for others)"
                    }
                },
                "required": ["provider"]
            }
        },
        {
            "name": "rdst_configure_default",
            "description": """Set the default database target.

When running RDST commands without --target, this target will be used.
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Name of target to set as default"
                    }
                },
                "required": ["target"]
            }
        },
        {
            "name": "rdst_analyze",
            "description": """Analyze a SQL query for performance optimization.

PREFERRED: Analyze queries from the registry using `name` or `hash` parameter.
Call `rdst_query_list` first to see saved queries, then analyze by name/hash.

INPUT OPTIONS (use ONE):
- `name`: Analyze a saved query by name (PREFERRED)
- `hash`: Analyze a saved query by hash (PREFERRED)
- `query`: Analyze an inline SQL query

WORKFLOW:
1. Call `rdst_query_list` to see saved queries
2. Call `rdst_analyze` with `name="query-name"` to analyze

CLI EQUIVALENT:
- `rdst analyze --name my-query --target mydb`
- `rdst analyze --hash abc123 --target mydb`
- `rdst analyze -q "SELECT ..." --target mydb`

WRONG (do not use):
- `rdst analyze --query-id X` (no such flag - use --name or --hash)

WHAT IT DOES:
1. Runs EXPLAIN ANALYZE on the query
2. AI analyzes the execution plan
3. Returns index recommendations, query rewrites, caching suggestions

AFTER ANALYSIS: ALWAYS display the key findings to the user:
- Performance issues found
- CREATE INDEX statements
- Query rewrites if suggested
The user wants to SEE the analysis, not just know it completed.

READYSET CACHING:
If the query takes >1 second (1000ms), suggest testing Readyset caching using
the --readyset-cache flag. This will:
1. Pull Readyset Docker containers (first run downloads images - may take a while)
2. Create a test replica of the user's database schema
3. Attempt to cache the query in Readyset
4. Run a performance comparison (original DB vs Readyset cached)
5. Show whether the query is cacheable and the speedup achieved

Warn the user this process may take a while, especially on first run.

PREREQUISITES:
- A database target must be configured (use rdst_configure_add first)
- The password environment variable must be exported

COMMON ISSUES:
- "Authentication failed" → Check if password_env is exported
- "Connection refused" → Check host/port accessibility
- Timeout → Query may be too slow; consider adding --fast flag
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Query name from registry (PREFERRED - call rdst_query_list first)"
                    },
                    "hash": {
                        "type": "string",
                        "description": "Query hash from registry (alternative to name)"
                    },
                    "query": {
                        "type": "string",
                        "description": "Inline SQL query (use name/hash for saved queries instead)"
                    },
                    "target": {
                        "type": "string",
                        "description": "Database target name (uses default if not specified)"
                    },
                    "fast": {
                        "type": "boolean",
                        "description": "Skip slow EXPLAIN ANALYZE queries (timeout after 10s)"
                    },
                    "readyset_cache": {
                        "type": "boolean",
                        "description": """Test if this query can be cached by Readyset and show performance improvement.

This process:
1. Pulls Readyset Docker containers (first run downloads images - may take a while)
2. Creates a test replica of the user's database schema
3. Attempts to cache the query in Readyset
4. Runs a performance comparison (original DB vs Readyset cached)

REQUIRES: Docker must be installed and running.
WARNING: This may take a while, especially on first run. Warn the user before running.

OUTPUT INCLUDES:
- Whether query is cacheable (and the reason if not)
- Performance comparison: original DB vs Readyset cache
- CREATE CACHE command for production deployment"""
                    }
                },
                "required": []
            }
        },
        {
            "name": "rdst_top",
            "description": """Capture slow queries from the database.

DEFAULT: Historical mode (instant results from pg_stat_statements).
Only use `duration` parameter as fallback when historical returns nothing.

DATA SOURCES:
- **Historical mode** (default, instant): Queries pg_stat_statements (PostgreSQL) or
  performance_schema.events_statements_summary_by_digest (MySQL). Shows aggregated
  statistics of ALL queries that have run since stats were last reset.
- **Live monitoring mode** (with duration): Polls pg_stat_activity (PostgreSQL) or
  INFORMATION_SCHEMA.PROCESSLIST (MySQL) repeatedly. Only captures queries actively
  running during the monitoring window - fast queries may be missed.

MYSQL SLOW LOG (optional source='slowlog'):
For MySQL, you can also query individual slow queries from mysql.slow_log table.
This requires enabling the MySQL slow query log with TABLE output:
  - For self-hosted: SET GLOBAL slow_query_log = 'ON'; SET GLOBAL long_query_time = 1; SET GLOBAL log_output = 'TABLE';
  - For RDS/Aurora: Modify parameter group (slow_query_log=1, long_query_time=1, log_output=TABLE)
No restart required - changes take effect immediately.
Use source='slowlog' parameter to enable. If not enabled, RDST will show setup instructions.

OUTPUT INCLUDES (JSON):
- query_hash: Unique identifier for the query pattern
- normalized_query: Query with parameters replaced by $1, $2, etc.
- max_duration_ms: Slowest observed execution
- avg_duration_ms: Average execution time
- observation_count: How many times query was seen (historical) or caught running (live)

If using duration, tell user "Monitoring for N seconds..." BEFORE calling.

Use this to identify which queries to optimize with rdst_analyze.

Captured queries auto-save to registry for later analysis with rdst_analyze.
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Database target name"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of queries to show (default: 10)"
                    },
                    "sort": {
                        "type": "string",
                        "enum": ["freq", "total_time", "avg_time", "load"],
                        "description": "Sort field (default: total_time)"
                    },
                    "filter": {
                        "type": "string",
                        "description": "Regex to filter query text"
                    },
                    "source": {
                        "type": "string",
                        "enum": ["auto", "pg_stat", "activity", "digest", "slowlog"],
                        "description": "Data source (database-specific): auto (default - picks correct source), pg_stat (PostgreSQL only), activity (both), digest (MySQL only), slowlog (MySQL only, requires setup)"
                    },
                    "historical": {
                        "type": "boolean",
                        "description": "Use historical statistics (default, non-interactive)"
                    },
                    "duration": {
                        "type": "integer",
                        "description": "Run real-time monitoring for N seconds then output"
                    }
                },
                "required": []
            }
        },
        {
            "name": "rdst_query_add",
            "description": """Save a SQL query to the registry for later use.

The query registry allows you to store frequently-used queries with names
for easy reference. Queries can then be analyzed by name or hash.

Each saved query includes:
- Name (user-provided identifier)
- SQL text
- Target database (optional)
- Hash (auto-generated unique identifier)
- Metadata (creation date, source, etc.)
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name for the query (e.g., 'user-lookup', 'order-summary')"
                    },
                    "query": {
                        "type": "string",
                        "description": "The SQL query to save"
                    },
                    "target": {
                        "type": "string",
                        "description": "Associated database target (optional)"
                    }
                },
                "required": ["name", "query"]
            }
        },
        {
            "name": "rdst_query_list",
            "description": """List all queries in the registry.

Shows saved queries with their names, hashes, and targets.
Use --filter to search across query text, names, and tags.
Use --target to filter by database target.
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Filter queries by target database"
                    },
                    "filter": {
                        "type": "string",
                        "description": "Search filter (matches SQL, names, tags)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum queries to show (default: 10)"
                    }
                },
                "required": []
            }
        },
        {
            "name": "rdst_query_delete",
            "description": """Delete a query from the registry.

Removes a saved query by name or hash.
Use --force to skip confirmation prompt.
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Query name to delete"
                    },
                    "hash": {
                        "type": "string",
                        "description": "Query hash to delete (alternative to name)"
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Skip confirmation prompt"
                    }
                },
                "required": []
            }
        },
        {
            "name": "rdst_version",
            "description": """Show RDST version information.

Displays the installed version of RDST. Use this to:
- Verify RDST is installed correctly
- Check if an upgrade is available
- Report version for troubleshooting

To upgrade: pip install --upgrade rdst
""",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "rdst_read_config",
            "description": """Read the RDST configuration file.

Returns the contents of ~/.rdst/config.toml which contains:
- Database target configurations
- Default target setting
- LLM provider settings

Use this to understand:
- What targets are configured
- Which environment variables are needed for passwords
- What the current default target is

NOTE: Passwords are never stored in config - only password_env variable names.
""",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "rdst_set_env",
            "description": """Set an environment variable for database password authentication.

When a target's password_env is not set, use this tool to set it so that
subsequent RDST commands can authenticate to the database.

WORKFLOW:
1. Call rdst_help to see which targets need passwords
2. Ask the user for the password value
3. Call rdst_set_env with the env var name and password value
4. Now rdst_analyze, rdst_top, etc. will work for that target

The env var persists for the lifetime of this MCP session.
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Environment variable name (e.g., PROD_DB_PASSWORD)"
                    },
                    "value": {
                        "type": "string",
                        "description": "The value to set (e.g., the actual password)"
                    }
                },
                "required": ["name", "value"]
            }
        },
        {
            "name": "rdst_help",
            "description": """Get RDST status and configured targets.

MCP-only tool (no CLI equivalent). Shows configured database targets and setup status.

This tool:
1. Reads the user's RDST configuration
2. Checks which password environment variables are set vs missing
3. Returns a summary of configured targets and their readiness
4. Provides guidance on what the user can do next

After calling this tool, you'll know:
- Which database targets are available
- Which ones are ready to use (env vars set)
- What actions you can take to help the user
""",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "rdst_test_connection",
            "description": """Test database connection for a configured target.

Use this after rdst_configure_add to verify the password is correct and the
database is reachable. Returns a simple success/failure with details.

WORKFLOW:
1. Configure a target with rdst_configure_add (uses --skip-verify)
2. Set the password with rdst_set_env
3. Call rdst_test_connection to verify connectivity
4. If successful, the target is ready for rdst_analyze and rdst_top

COMMON FAILURE REASONS:
- Password env var not set → Use rdst_set_env first
- Wrong password → Ask user to verify the password
- Connection refused → Check host/port, firewall rules
- Timeout → Database may be unreachable or slow

Returns JSON:
{
  "connected": true/false,
  "target": "target-name",
  "engine": "postgresql/mysql",
  "host": "host:port",
  "database": "database-name",
  "server_version": "PostgreSQL 15.2..." (if connected),
  "error": "error message" (if failed)
}
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Name of the target to test (uses default if not specified)"
                    }
                },
                "required": []
            }
        },
        {
            "name": "rdst_report",
            "description": """Submit feedback about RDST or a specific query analysis.

Use this when the user wants to:
- Report a bug or issue with RDST
- Provide feedback (positive or negative) about analysis results
- Suggest improvements or features

The feedback is sent to the RDST team for review. Users can optionally
include their email for follow-up.

WORKFLOW:
1. Ask the user what feedback they want to provide
2. Ask if it's positive or negative feedback
3. Optionally ask for their email if they want follow-up
4. Call rdst_report with the details

Example: User says "The index recommendation was wrong"
→ Call rdst_report(reason="Index recommendation was incorrect - suggested index already exists", negative=True)
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "The feedback message describing the issue or suggestion"
                    },
                    "hash": {
                        "type": "string",
                        "description": "Query hash if feedback is about a specific analysis (optional)"
                    },
                    "email": {
                        "type": "string",
                        "description": "User's email for follow-up (optional)"
                    },
                    "positive": {
                        "type": "boolean",
                        "description": "Set to true for positive feedback"
                    },
                    "negative": {
                        "type": "boolean",
                        "description": "Set to true for negative feedback"
                    },
                    "include_query": {
                        "type": "boolean",
                        "description": "Include the raw SQL in the feedback (if hash provided)"
                    },
                    "include_plan": {
                        "type": "boolean",
                        "description": "Include the execution plan in the feedback (if hash provided)"
                    }
                },
                "required": ["reason"]
            }
        },
        {
            "name": "rdst_init",
            "description": """Run the RDST first-time setup wizard.

This interactive wizard helps new users configure RDST by:
1. Setting up the LLM provider (Claude, OpenAI, LM Studio, or free RDST trial)
2. Adding their first database target
3. Testing the connection

The init wizard includes the trial signup option - users without an
ANTHROPIC_API_KEY can sign up for free RDST trial credits during init.
Trial credits: $5.00 for business emails, $1.50 for personal emails.

Use this when:
- User is setting up RDST for the first time
- User wants to reconfigure their LLM provider
- User says "help me set up RDST" or similar

NOTE: This runs an interactive wizard - best used when the user
explicitly asks to set up or reconfigure RDST. Tell the user to run
`rdst init` in their terminal for the full interactive experience.
""",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "rdst_schema",
            "description": """Manage semantic layer for better SQL generation.

The semantic layer stores information about your database schema including:
- Table and column descriptions
- Enum value meanings
- Business terminology
- Relationships between tables

This metadata helps generate better SQL queries and understand the database.

SUBCOMMANDS (available via MCP):
- show: Display semantic layer (all or specific table)
- init: Initialize from database introspection
- export: Export as YAML/JSON
- delete: Remove semantic layer
- list: List all semantic layers

CLI-ONLY FEATURES (tell user to run these in their terminal):
- `rdst schema annotate --target <target>` - Interactive wizard to add descriptions
- `rdst schema annotate --target <target> --use-llm` - AI-generated descriptions
- `rdst schema edit --target <target>` - Opens in $EDITOR
- `rdst ask "question" --target <target>` - Natural language to SQL (interactive)

The `rdst ask` command converts natural language questions into SQL queries.
It requires an interactive terminal for its multi-step flow (clarifications,
execution confirmation, result display). Tell users to run it directly in CLI.

EXAMPLES:
  rdst_schema(subcommand="init", target="mydb")
  rdst_schema(subcommand="show", target="mydb", table="customers")
  rdst_schema(subcommand="export", target="mydb", output_format="yaml")
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "subcommand": {
                        "type": "string",
                        "enum": ["show", "init", "export", "delete", "list"],
                        "description": "Schema subcommand to run (annotate/edit require CLI)"
                    },
                    "target": {
                        "type": "string",
                        "description": "Database target name"
                    },
                    "table": {
                        "type": "string",
                        "description": "Specific table (for show)"
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Overwrite existing (for init, delete)"
                    },
                    "output_format": {
                        "type": "string",
                        "enum": ["yaml", "json"],
                        "description": "Export format (for export)"
                    }
                },
                "required": ["subcommand"]
            }
        },
        {
            "name": "rdst_agent_list",
            "description": """List all configured data agents.

Data agents provide safe, scalable database access for AI applications.
Each agent wraps a database target with safety policies like row limits,
column restrictions, and read-only enforcement.

Use this to see what agents are available for querying.
""",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "rdst_agent_ask",
            "description": """Ask a natural language question to a data agent.

Data agents convert natural language questions into SQL queries, execute them
safely with configured restrictions, and return the results.

Example:
  rdst_agent_ask(agent_name="sales-agent", question="How many orders were placed last month?")

The agent will:
1. Generate SQL from the question
2. Validate against safety policies (read-only, column restrictions)
3. Execute with timeout and row limits
4. Return structured results

IMPORTANT: If no agents exist, tell the user to create one with:
  rdst agent create --name <name> --target <database-target>
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_name": {
                        "type": "string",
                        "description": "Name of the agent to query"
                    },
                    "question": {
                        "type": "string",
                        "description": "Natural language question about the data"
                    }
                },
                "required": ["agent_name", "question"]
            }
        },
        {
            "name": "rdst_agent_create",
            "description": """Create a new data agent.

Data agents wrap database targets with safety policies for AI access.

Example:
  rdst_agent_create(
    name="sales-agent",
    target="prod-db",
    description="Sales data analysis agent",
    max_rows=1000,
    timeout=30
  )

After creation, use rdst_agent_ask to query the agent.
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Unique name for the agent"
                    },
                    "target": {
                        "type": "string",
                        "description": "Database target name (from rdst configure list)"
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable description of what this agent does"
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "Maximum rows to return (default 1000)"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Query timeout in seconds (default 30)"
                    }
                },
                "required": ["name", "target"]
            }
        },
        {
            "name": "rdst_scan",
            "description": """Scan a codebase for ORM queries and optionally analyze them for performance issues.

Supports 4 ORMs across Python and JavaScript/TypeScript:
- Python: SQLAlchemy (1.x and 2.0), Django ORM
- JS/TS: Prisma, Drizzle

PREREQUISITE: Run 'rdst schema init --target <name>' first to provide table/column
context for ORM-to-SQL conversion. This introspects your database and creates a
local schema YAML — it only needs to be done once per target.

How it works:
1. Finds files with ORM patterns (AST parsing for Python, regex for JS/TS — deterministic)
2. Extracts query snippets and converts them to SQL using schema context (Haiku, cached)
3. Optionally analyzes queries for performance issues

Analysis modes:
- No --analyze: Extract and convert only. No DB connection needed. Shows SQL + anti-patterns.
- --analyze --shallow: Schema-only analysis via LLM. No DB connection needed. Fast.
  Assigns risk scores (0-100) based on query structure, missing indexes, anti-patterns.
- --analyze (deep): Runs EXPLAIN ANALYZE against the live database + LLM analysis.
  Requires DB password set via password_env. Shows execution plans, index recommendations,
  and query rewrite suggestions. Scores may vary slightly between runs due to DB state.

Note: Deep analysis executes queries against your database in read-only transactions.
Results depend on current table sizes, indexes, and data distribution. If a query times
out or the DB is under load, that query's analysis may fail — the rest will still complete.

Examples:
  rdst_scan(directory="./backend", schema="mydb")
  rdst_scan(directory="./backend", schema="mydb", diff="HEAD")  # Uncommitted changes only
  rdst_scan(directory="./backend", schema="mydb", analyze=True)  # Deep analysis
  rdst_scan(directory="./backend", schema="mydb", analyze=True, check=True)  # CI mode
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Directory or file to scan for ORM code (default: current directory)"
                    },
                    "schema": {
                        "type": "string",
                        "description": "Target name for schema context (required)"
                    },
                    "diff": {
                        "type": "string",
                        "description": "Git ref to diff against: HEAD (uncommitted), HEAD~1 (last commit), commit ID, or branch"
                    },
                    "analyze": {
                        "type": "boolean",
                        "description": "Run analysis on extracted queries. Deep by default (EXPLAIN ANALYZE against live DB). Add shallow=true for schema-only analysis without a DB connection."
                    },
                    "shallow": {
                        "type": "boolean",
                        "description": "Use schema-only analysis (no DB connection needed). Must be used with analyze=true."
                    },
                    "check": {
                        "type": "boolean",
                        "description": "CI mode: return exit code 1 if issues found below threshold"
                    },
                    "fail_threshold": {
                        "type": "integer",
                        "description": "Risk score below which to fail (0-100, default: 30)"
                    },
                    "output": {
                        "type": "string",
                        "enum": ["table", "json"],
                        "description": "Output format (default: table)"
                    },
                    "sequential": {
                        "type": "boolean",
                        "description": "Run analysis queries one at a time (more deterministic scores, slower)"
                    },
                    "nosave": {
                        "type": "boolean",
                        "description": "Don't save extracted queries to the registry"
                    }
                },
                "required": ["schema"]
            }
        },
        {
            "name": "rdst_cache_deploy",
            "description": """Deploy ReadySet shallow cache permanently to local, remote, or Kubernetes environments.

Modes:
- docker: Docker container with restart policy
- systemd: Native binary with systemd service
- kubernetes: K8s Deployment + Service via kubectl

For remote deployment, specify host to deploy via SSH.
Use script_only to generate the deployment script without executing.

After deployment, shows the connection endpoint to point your application to.
Auto-registers a ReadySet target (e.g., mydb-cache) for use with cache add/show/delete.

Examples:
  rdst_cache_deploy(target="mydb", mode="docker")  # Local Docker deploy
  rdst_cache_deploy(target="mydb", mode="systemd")  # Local systemd
  rdst_cache_deploy(target="mydb", mode="docker", host="10.0.1.50")  # Remote Docker via SSH
  rdst_cache_deploy(target="mydb", mode="kubernetes")  # Kubernetes
  rdst_cache_deploy(target="mydb", mode="docker", script_only=True)  # Generate script only
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Database target name to deploy for (from rdst configure list)"
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["docker", "systemd", "kubernetes"],
                        "description": "Deployment mode (default: docker)"
                    },
                    "host": {
                        "type": "string",
                        "description": "Remote host for SSH deployment (omit for local)"
                    },
                    "ssh_key": {
                        "type": "string",
                        "description": "SSH private key path"
                    },
                    "ssh_user": {
                        "type": "string",
                        "description": "SSH username (default: root)"
                    },
                    "port": {
                        "type": "integer",
                        "description": "ReadySet listen port"
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Kubernetes namespace (default: readyset)"
                    },
                    "kubeconfig": {
                        "type": "string",
                        "description": "Path to kubeconfig file for Kubernetes deployment"
                    },
                    "script_only": {
                        "type": "boolean",
                        "description": "Generate deployment script without executing"
                    },
                    "output_json": {
                        "type": "boolean",
                        "description": "Return JSON output"
                    }
                },
                "required": ["target"]
            }
        },
        {
            "name": "rdst_cache_add",
            "description": """Create a shallow cache for a query in a deployed ReadySet instance.

Shallow caching stores query results in ReadySet's in-memory cache with a TTL
(time-to-live). Queries are served from cache until the TTL expires, then
refreshed from the upstream database. This provides dramatic latency improvements
(often 10-100x) for read-heavy workloads without requiring full materialized views.

IMPORTANT: The target must be a ReadySet target (target_type=readyset), not a
database target. Deploy ReadySet first with rdst_cache_deploy(), which auto-registers
a cache target named "{original_target}-cache".

The query can be:
- Direct SQL: A SELECT statement to cache
- Registry hash: A 4-12 character hex hash from rdst query list

After caching, use rdst query run to benchmark performance against both the
ReadySet target and the upstream database target.

Examples:
  rdst_cache_add(query="SELECT * FROM orders WHERE id = 1", target="mydb-cache")
  rdst_cache_add(query="abc123de", target="mydb-cache")  # By registry hash
  rdst_cache_add(query="SELECT COUNT(*) FROM users", target="mydb-cache", tag="user-count")
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SQL SELECT query or registry hash (4-12 hex chars)"
                    },
                    "target": {
                        "type": "string",
                        "description": "ReadySet target name (target_type=readyset, e.g., mydb-cache)"
                    },
                    "tag": {
                        "type": "string",
                        "description": "Tag for the query in the registry"
                    },
                    "output_json": {
                        "type": "boolean",
                        "description": "Return JSON output"
                    }
                },
                "required": ["query", "target"]
            }
        },
        {
            "name": "rdst_cache_show",
            "description": """List all cached queries in a deployed ReadySet instance.

Shows a table of all shallow caches with columns: Cache Name, Query, Type, TTL.
The cache name/ID is used with rdst_cache_delete to remove specific caches.

IMPORTANT: The target must be a ReadySet target (target_type=readyset).

Examples:
  rdst_cache_show(target="mydb-cache")
  rdst_cache_show(target="mydb-cache", output_json=True)
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "ReadySet target name (target_type=readyset)"
                    },
                    "output_json": {
                        "type": "boolean",
                        "description": "Return JSON output with cache details"
                    }
                },
                "required": ["target"]
            }
        },
        {
            "name": "rdst_cache_delete",
            "description": """Remove a specific cache from a deployed ReadySet instance.

Use rdst_cache_show to get the cache ID/name, then pass it here to remove.

IMPORTANT: The target must be a ReadySet target (target_type=readyset).

Examples:
  rdst_cache_delete(cache_id="q_54fc6da6d5703402", target="mydb-cache")
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "cache_id": {
                        "type": "string",
                        "description": "Cache ID or name (from rdst_cache_show output)"
                    },
                    "target": {
                        "type": "string",
                        "description": "ReadySet target name (target_type=readyset)"
                    },
                    "output_json": {
                        "type": "boolean",
                        "description": "Return JSON output"
                    }
                },
                "required": ["cache_id", "target"]
            }
        },
        {
            "name": "rdst_cache_drop_all",
            "description": """Remove ALL caches from a deployed ReadySet instance.

Runs DROP ALL CACHES against ReadySet. This removes every cached query.
Use with caution — there is no undo.

IMPORTANT: The target must be a ReadySet target (target_type=readyset).

Examples:
  rdst_cache_drop_all(target="mydb-cache")
""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "ReadySet target name (target_type=readyset)"
                    },
                    "output_json": {
                        "type": "boolean",
                        "description": "Return JSON output"
                    }
                },
                "required": ["target"]
            }
        }
    ]


def get_prompts() -> List[Dict[str, Any]]:
    """Return the list of available MCP prompts.

    Note: We return an empty list because prompts show up alongside slash commands
    and create confusion. The /rdst slash command handles the entry point instead.
    """
    return []


def get_resources() -> List[Dict[str, Any]]:
    """Return the list of available MCP resources."""
    return [
        {
            "uri": "rdst://config",
            "name": "RDST Configuration",
            "description": "Current RDST configuration including database targets",
            "mimeType": "text/plain"
        },
        {
            "uri": "rdst://context",
            "name": "RDST Context",
            "description": "Comprehensive context about RDST for AI assistants",
            "mimeType": "text/markdown"
        }
    ]


def handle_tool_call(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle a tool call and return the result."""

    if name == "rdst_configure_add":
        # Always use --skip-verify for MCP (non-interactive, can't prompt for confirmation)
        # Note: "add" is positional, --skip-verify comes after
        args = ["configure", "add"]
        args.extend(["--target", arguments["target"]])
        args.extend(["--engine", arguments["engine"]])
        args.extend(["--host", arguments["host"]])
        if "port" in arguments:
            args.extend(["--port", str(arguments["port"])])
        args.extend(["--user", arguments["user"]])
        args.extend(["--database", arguments["database"]])
        args.extend(["--password-env", arguments["password_env"]])
        if arguments.get("make_default"):
            args.append("--default")
        args.append("--skip-verify")  # Skip connection verification for MCP
        result = run_rdst_command(args)

        # Add helpful context about next steps
        if result["success"]:
            result["next_steps"] = f"""
Target '{arguments["target"]}' added successfully.

IMPORTANT: Before using this target, the user must export the password:
  export {arguments["password_env"]}="<actual-password>"

Then they can run:
  rdst analyze -q "SELECT 1" --target {arguments["target"]}
"""
        return result

    elif name == "rdst_configure_list":
        return run_rdst_command(["configure", "list"])

    elif name == "rdst_configure_remove":
        args = ["configure", "remove", arguments["target"]]
        if arguments.get("confirm"):
            args.append("--confirm")
        return run_rdst_command(args)

    elif name == "rdst_configure_llm":
        args = ["configure", "llm", "--provider", arguments["provider"]]
        if "model" in arguments:
            args.extend(["--model", arguments["model"]])
        if "base_url" in arguments:
            args.extend(["--base-url", arguments["base_url"]])
        result = run_rdst_command(args)
        if result["success"]:
            provider = arguments["provider"]
            api_key_info = {
                "claude": "ANTHROPIC_API_KEY",
                "openai": "OPENAI_API_KEY",
                "lmstudio": "(no API key needed)",
                "trial": "(no API key needed - using RDST trial credits)"
            }
            result["next_steps"] = f"""
LLM provider configured to: {provider}

Required environment variable: {api_key_info.get(provider, 'Check provider docs')}
"""
        return result

    elif name == "rdst_configure_default":
        return run_rdst_command(["configure", "default", arguments["target"]])

    elif name == "rdst_analyze":
        args = ["analyze"]
        # Support three input modes: name, hash, or inline query
        if "name" in arguments:
            args.extend(["--name", arguments["name"]])
        elif "hash" in arguments:
            args.extend(["--hash", arguments["hash"]])
        elif "query" in arguments:
            args.extend(["-q", arguments["query"]])
        else:
            return {
                "success": False,
                "stdout": "",
                "stderr": "Must provide one of: name (preferred), hash, or query. Call rdst_query_list first to see saved queries.",
                "returncode": 1
            }
        if "target" in arguments:
            args.extend(["--target", arguments["target"]])
        if arguments.get("fast"):
            args.append("--fast")
        if arguments.get("readyset_cache"):
            args.append("--readyset-cache")
        return run_rdst_command(args)

    elif name == "rdst_top":
        # Always use --json for MCP (no interactive TUI in subprocess)
        args = ["top", "--json"]

        # WORKFLOW: Default to historical mode for instant results
        # Only use duration-based live monitoring when explicitly requested
        if arguments.get("historical") or "duration" not in arguments:
            # Historical mode: instant results from pg_stat_statements
            args.append("--historical")
        else:
            # Live monitoring: duration must be explicitly specified
            args.extend(["--duration", str(arguments["duration"])])

        if "target" in arguments:
            args.extend(["--target", arguments["target"]])
        if "source" in arguments:
            args.extend(["--source", arguments["source"]])
        if "limit" in arguments:
            args.extend(["--limit", str(arguments["limit"])])
        if "sort" in arguments:
            args.extend(["--sort", arguments["sort"]])
        if "filter" in arguments:
            args.extend(["--filter", arguments["filter"]])
        return run_rdst_command(args)

    elif name == "rdst_query_add":
        args = ["query", "add", arguments["name"], "-q", arguments["query"]]
        if "target" in arguments:
            args.extend(["--target", arguments["target"]])
        return run_rdst_command(args)

    elif name == "rdst_query_list":
        args = ["query", "list"]
        if "target" in arguments:
            args.extend(["--target", arguments["target"]])
        if "filter" in arguments:
            args.extend(["--filter", arguments["filter"]])
        if "limit" in arguments:
            args.extend(["--limit", str(arguments["limit"])])
        return run_rdst_command(args)

    elif name == "rdst_query_delete":
        args = ["query", "delete"]
        if "name" in arguments:
            args.append(arguments["name"])
        if "hash" in arguments:
            args.extend(["--hash", arguments["hash"]])
        if arguments.get("force"):
            args.append("--force")
        return run_rdst_command(args)

    elif name == "rdst_version":
        return run_rdst_command(["version"])

    elif name == "rdst_read_config":
        config_path = os.path.expanduser("~/.rdst/config.toml")
        try:
            with open(config_path, "r") as f:
                content = f.read()
            return {
                "success": True,
                "stdout": content,
                "stderr": "",
                "returncode": 0,
                "context": """
This is the RDST configuration file. Key sections:
- [targets.NAME]: Database connection profiles
  - password_env: Environment variable name for password (MUST be exported before use)
- default: Name of the default target
- [llm]: AI provider settings
"""
            }
        except FileNotFoundError:
            return {
                "success": False,
                "stdout": "",
                "stderr": "Config file not found at ~/.rdst/config.toml. Run 'rdst init' to create it.",
                "returncode": 1
            }
        except Exception as e:
            return {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": 1
            }

    elif name == "rdst_set_env":
        env_name = arguments["name"]
        env_value = arguments["value"]
        os.environ[env_name] = env_value
        return {
            "success": True,
            "stdout": f"Environment variable '{env_name}' has been set. Subsequent RDST commands will now have access to it.",
            "stderr": "",
            "returncode": 0
        }

    elif name == "rdst_help":
        # This is the main entry point - reads config and checks env vars
        config_path = os.path.expanduser("~/.rdst/config.toml")
        try:
            import toml
            with open(config_path, "r") as f:
                config = toml.load(f)

            targets = config.get("targets", {})
            default_target = config.get("default")
            llm_config = config.get("llm", {})

            # Build status report
            report = []
            report.append("## RDST - Database Query Analysis Tool\n")

            if not targets:
                # First-time user with config file but no targets
                report.append("Welcome! You have RDST installed but no database targets configured yet.\n")
                report.append("**Quick Start:**")
                report.append("1. Tell me your database connection details (host, port, database name, user)")
                report.append("2. I'll set up the target for you using `rdst_configure_add`")
                report.append("3. Then provide your database password when ready to connect\n")
                report.append("**What RDST can do:**")
                report.append("- Analyze SQL queries and suggest index optimizations")
                report.append("- Monitor slow queries in real-time with `rdst_top`")
                report.append("- Help you understand query execution plans")
            else:
                # Show targets table
                report.append(f"**Database Targets ({len(targets)}):**\n")
                for tgt_name, target in targets.items():
                    is_default = tgt_name == default_target
                    marker = " (default)" if is_default else ""
                    host = target.get("host", "")
                    port = target.get("port", "")
                    host_port = f"{host}:{port}" if port else host
                    engine = target.get("engine", "unknown")
                    database = target.get("database", "")

                    report.append(f"- **{tgt_name}**{marker}: {engine} @ {host_port}/{database}")

                report.append("")
                report.append("**Try:**")
                report.append('- "Analyze this query: SELECT ..."')
                report.append('- "Show me slow queries" (uses rdst_top)')
                report.append('- "Test my database connection"')

            return {
                "success": True,
                "stdout": "\n".join(report),
                "stderr": "",
                "returncode": 0,
                "targets": list(targets.keys()),
                "default_target": default_target,
                "context": RDST_CONTEXT
            }

        except FileNotFoundError:
            return {
                "success": True,  # Not an error, just first-time setup
                "stdout": """## RDST - Database Query Analysis Tool

Welcome! RDST helps you optimize SQL queries by analyzing execution plans and suggesting indexes.

**Getting Started:**
Tell me about your database and I'll set everything up:
- What type? (PostgreSQL or MySQL)
- Connection details (host, port, database, username)

**What I can do:**
- Analyze slow queries and recommend indexes
- Monitor database performance in real-time
- Explain query execution plans in plain English

Just describe your database and we'll get connected!
""",
                "stderr": "",
                "returncode": 0,
                "context": RDST_CONTEXT
            }
        except ImportError:
            # toml not available, fall back to raw read
            try:
                with open(config_path, "r") as f:
                    content = f.read()
                return {
                    "success": True,
                    "stdout": f"Config file found. Contents:\n\n{content}\n\n(Note: Install 'toml' package for detailed parsing)",
                    "stderr": "",
                    "returncode": 0,
                    "context": RDST_CONTEXT
                }
            except Exception as e:
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": str(e),
                    "returncode": 1
                }
        except Exception as e:
            return {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": 1
            }

    elif name == "rdst_test_connection":
        # Test database connection for a target via CLI
        target_name = arguments.get("target")

        args = ["configure", "test"]
        if target_name:
            args.extend(["--target", target_name])

        return run_rdst_command(args)

    elif name == "rdst_report":
        # Submit feedback - MUST provide --reason to avoid interactive mode
        args = ["report"]
        args.extend(["--reason", arguments["reason"]])
        if arguments.get("hash"):
            args.extend(["--hash", arguments["hash"]])
        if arguments.get("email"):
            args.extend(["--email", arguments["email"]])
        if arguments.get("positive"):
            args.append("--positive")
        elif arguments.get("negative"):
            args.append("--negative")
        else:
            # Default to neutral by not passing either flag
            pass
        if arguments.get("include_query"):
            args.append("--include-query")
        if arguments.get("include_plan"):
            args.append("--include-plan")
        return run_rdst_command(args)

    elif name == "rdst_init":
        # Init is interactive - warn that it may not work well via MCP
        result = run_rdst_command(["init"])
        if not result["success"] and "interactive" in result.get("stderr", "").lower():
            result["stderr"] += "\n\nNote: rdst init is interactive. For MCP, use rdst_configure_add to add targets directly."
        return result

    elif name == "rdst_schema":
        subcommand = arguments.get("subcommand", "show")

        # Block interactive-only subcommands
        if subcommand in ["annotate", "edit"]:
            return {
                "success": False,
                "stdout": "",
                "stderr": f"'{subcommand}' requires an interactive terminal. Please use the CLI directly:\n  rdst schema {subcommand} --target <target>",
                "returncode": 1
            }

        args = ["schema", subcommand]

        if "target" in arguments:
            args.extend(["--target", arguments["target"]])

        if subcommand == "show" and "table" in arguments:
            args.append(arguments["table"])

        if subcommand in ["init", "delete"] and arguments.get("force"):
            args.append("--force")

        if subcommand == "export" and "output_format" in arguments:
            args.extend(["--format", arguments["output_format"]])

        return run_rdst_command(args)

    elif name == "rdst_scan":
        # Scan codebase for ORM queries
        args = ["scan"]

        if "directory" in arguments:
            args.append(arguments["directory"])

        if "schema" in arguments:
            args.extend(["--schema", arguments["schema"]])

        if "diff" in arguments:
            args.extend(["--diff", arguments["diff"]])

        if arguments.get("analyze"):
            args.append("--analyze")

        if arguments.get("shallow"):
            args.append("--shallow")

        if arguments.get("check"):
            args.append("--check")

        if "fail_threshold" in arguments:
            args.extend(["--fail-threshold", str(arguments["fail_threshold"])])

        if "output" in arguments:
            args.extend(["--output", arguments["output"]])

        if arguments.get("sequential"):
            args.append("--sequential")

        if arguments.get("nosave"):
            args.append("--nosave")

        return run_rdst_command(args)

    elif name == "rdst_cache_deploy":
        # Deploy ReadySet shallow cache
        args = ["cache", "deploy"]

        args.extend(["--target", arguments["target"]])

        # --mode is required by CLI; default to docker for MCP callers
        mode = arguments.get("mode", "docker")
        args.extend(["--mode", mode])

        if "host" in arguments:
            args.extend(["--host", arguments["host"]])

        if "ssh_key" in arguments:
            args.extend(["--ssh-key", arguments["ssh_key"]])

        if "ssh_user" in arguments:
            args.extend(["--ssh-user", arguments["ssh_user"]])

        if "port" in arguments:
            args.extend(["--port", str(arguments["port"])])

        if "namespace" in arguments:
            args.extend(["--namespace", arguments["namespace"]])

        if "kubeconfig" in arguments:
            args.extend(["--kubeconfig", arguments["kubeconfig"]])

        if arguments.get("script_only"):
            args.append("--script-only")

        if arguments.get("output_json"):
            args.append("--json")

        return run_rdst_command(args)

    elif name == "rdst_cache_add":
        args = ["cache", "add", arguments["query"]]
        args.extend(["--target", arguments["target"]])
        if "tag" in arguments:
            args.extend(["--tag", arguments["tag"]])
        if arguments.get("output_json"):
            args.append("--json")
        result = run_rdst_command(args)
        if result["success"]:
            result["next_steps"] = f"""
Cache created. Next steps:
  Benchmark:  rdst query run <hash> --target {arguments["target"]}
  Compare:    rdst query run <hash> --target <upstream-target>
  View:       rdst_cache_show(target="{arguments["target"]}")
"""
        return result

    elif name == "rdst_cache_show":
        args = ["cache", "show"]
        args.extend(["--target", arguments["target"]])
        if arguments.get("output_json"):
            args.append("--json")
        return run_rdst_command(args)

    elif name == "rdst_cache_delete":
        args = ["cache", "delete", arguments["cache_id"]]
        args.extend(["--target", arguments["target"]])
        if arguments.get("output_json"):
            args.append("--json")
        return run_rdst_command(args)

    elif name == "rdst_cache_drop_all":
        args = ["cache", "drop-all"]
        args.extend(["--target", arguments["target"]])
        args.append("--yes")  # Skip confirmation for MCP (non-interactive)
        if arguments.get("output_json"):
            args.append("--json")
        return run_rdst_command(args)

    elif name == "rdst_agent_list":
        # List all configured data agents
        try:
            from lib.agent import AgentManager
            manager = AgentManager()
            names = manager.list()

            if not names:
                return {
                    "success": True,
                    "stdout": "No agents configured.\n\nCreate one with:\n  rdst agent create --name <name> --target <database-target>",
                    "stderr": "",
                    "returncode": 0
                }

            lines = ["Configured agents:\n"]
            for name_item in names:
                try:
                    agent = manager.get(name_item)
                    lines.append(f"  {name_item} -> {agent.target} ({agent.description or 'no description'})")
                except Exception:
                    lines.append(f"  {name_item} -> (error loading)")

            return {
                "success": True,
                "stdout": "\n".join(lines),
                "stderr": "",
                "returncode": 0
            }
        except Exception as e:
            return {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": 1
            }

    elif name == "rdst_agent_ask":
        # Ask a question to a data agent
        agent_name = arguments.get("agent_name")
        question = arguments.get("question")

        if not agent_name or not question:
            return {
                "success": False,
                "stdout": "",
                "stderr": "Both 'agent_name' and 'question' are required",
                "returncode": 1
            }

        try:
            from lib.agent import AgentManager, AgentRuntime
            manager = AgentManager()

            if not manager.exists(agent_name):
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": f"Agent '{agent_name}' not found. Use rdst_agent_list to see available agents.",
                    "returncode": 1
                }

            agent = manager.get(agent_name)
            runtime = AgentRuntime(agent)
            response = runtime.ask(question)

            if response.success:
                result_lines = []
                if response.sql:
                    result_lines.append(f"SQL:\n{response.sql}\n")
                if response.columns and response.rows:
                    result_lines.append(f"Columns: {', '.join(response.columns)}")
                    result_lines.append(f"Rows ({response.row_count}):")
                    for row in response.rows[:20]:  # Limit display
                        result_lines.append(f"  {row}")
                    if response.row_count > 20:
                        result_lines.append(f"  ... and {response.row_count - 20} more rows")
                    if response.truncated:
                        result_lines.append("(Results truncated)")
                elif response.row_count == 0:
                    result_lines.append("No results found")

                return {
                    "success": True,
                    "stdout": "\n".join(result_lines),
                    "stderr": "",
                    "returncode": 0
                }
            else:
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": response.error or "Query failed",
                    "returncode": 1
                }
        except Exception as e:
            return {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": 1
            }

    elif name == "rdst_agent_create":
        # Create a new data agent
        args = ["agent", "create"]
        args.extend(["--name", arguments["name"]])
        args.extend(["--target", arguments["target"]])
        if "description" in arguments:
            args.extend(["--description", arguments["description"]])
        if "max_rows" in arguments:
            args.extend(["--max-rows", str(arguments["max_rows"])])
        if "timeout" in arguments:
            args.extend(["--timeout", str(arguments["timeout"])])

        result = run_rdst_command(args)
        if result["success"]:
            result["next_steps"] = f"""
Agent '{arguments["name"]}' created successfully.

You can now query it with:
  rdst_agent_ask(agent_name="{arguments["name"]}", question="<your question>")

Or start an HTTP API server:
  rdst agent serve --name {arguments["name"]} --port 8080
"""
        return result

    else:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Unknown tool: {name}",
            "returncode": 1
        }


def handle_prompt(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle a prompt request and return the messages."""

    if name == "rdst_getting_started":
        return {
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": """I want to set up RDST to analyze queries on my database.

Please help me:
1. First, check if RDST is installed (rdst version)
2. If not installed, tell me to run: pip install rdst
3. Check if I have any targets configured (rdst configure list)
4. If no targets, help me add one with rdst_configure_add
5. Remind me to export the password environment variable
6. Run a simple test query to verify connectivity

What database details do you need from me?"""
                    }
                }
            ]
        }

    elif name == "rdst_analyze_workflow":
        query = arguments.get("query", "SELECT 1")
        return {
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": f"""Please analyze this SQL query and help me optimize it:

```sql
{query}
```

Steps:
1. First check my configured targets (rdst_configure_list)
2. Ask which target to use if not specified
3. Run rdst_analyze on the query
4. Explain the execution plan
5. Summarize the index recommendations
6. Show any query rewrites
7. Explain which recommendations have the highest impact"""
                    }
                }
            ]
        }

    return {"messages": []}


def handle_resource(uri: str) -> Dict[str, Any]:
    """Handle a resource request and return the content."""

    if uri == "rdst://config":
        config_path = os.path.expanduser("~/.rdst/config.toml")
        try:
            with open(config_path, "r") as f:
                content = f.read()
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "text/plain",
                        "text": content
                    }
                ]
            }
        except FileNotFoundError:
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "text/plain",
                        "text": "# Config not found. Run 'rdst init' to create it."
                    }
                ]
            }

    elif uri == "rdst://context":
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "text/markdown",
                    "text": RDST_CONTEXT
                }
            ]
        }

    return {"contents": []}


def handle_request(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle an incoming JSON-RPC request."""
    method = request.get("method", "")
    id = request.get("id")
    params = request.get("params", {})

    # Initialize
    if method == "initialize":
        return make_response(id, {
            "protocolVersion": MCP_VERSION,
            "capabilities": {
                "tools": {},
                "prompts": {},
                "resources": {}
            },
            "serverInfo": {
                "name": "rdst",
                "version": "0.1.0"
            },
            "instructions": RDST_CONTEXT
        })

    # List tools
    elif method == "tools/list":
        return make_response(id, {"tools": get_tools()})

    # Call tool
    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        result = handle_tool_call(tool_name, arguments)

        # Format as MCP tool result
        content = []
        if result.get("stdout"):
            content.append({"type": "text", "text": result["stdout"]})
        if result.get("stderr"):
            content.append({"type": "text", "text": f"STDERR: {result['stderr']}"})
        if result.get("next_steps"):
            content.append({"type": "text", "text": result["next_steps"]})
        if result.get("context"):
            content.append({"type": "text", "text": result["context"]})

        if not content:
            content.append({"type": "text", "text": "Command completed with no output"})

        return make_response(id, {
            "content": content,
            "isError": not result.get("success", False)
        })

    # List prompts
    elif method == "prompts/list":
        return make_response(id, {"prompts": get_prompts()})

    # Get prompt
    elif method == "prompts/get":
        prompt_name = params.get("name", "")
        arguments = params.get("arguments", {})
        result = handle_prompt(prompt_name, arguments)
        return make_response(id, result)

    # List resources
    elif method == "resources/list":
        return make_response(id, {"resources": get_resources()})

    # Read resource
    elif method == "resources/read":
        uri = params.get("uri", "")
        result = handle_resource(uri)
        return make_response(id, result)

    # Notifications (no response needed)
    elif method == "notifications/initialized":
        return None

    # Unknown method
    else:
        return make_error(id, -32601, f"Method not found: {method}")


def main():
    """Main entry point for the MCP server."""
    # Set up unbuffered I/O for stdio transport
    sys.stdin = open(sys.stdin.fileno(), 'r', buffering=1)
    sys.stdout = open(sys.stdout.fileno(), 'w', buffering=1)

    while True:
        try:
            message = read_message()
            if message is None:
                break

            response = handle_request(message)
            if response is not None:
                write_message(response)

        except KeyboardInterrupt:
            break
        except Exception as e:
            # Try to send error response
            try:
                write_message(make_error(None, -32603, str(e)))
            except:
                pass


if __name__ == "__main__":
    main()

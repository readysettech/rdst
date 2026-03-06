from __future__ import annotations

"""
RDST Help - Quick documentation lookup using Haiku.

Usage:
    rdst help "how do I analyze a query?"
    rdst help "what's the difference between top and analyze?"
"""

from dataclasses import dataclass
from typing import Optional
import os

from lib.ui import (
    get_console,
    MarkdownContent,
    Spinner,
    StyleTokens,
    Layout as UILayout,
    StyledPanel,
)

# Embedded documentation for RDST
RDST_DOCS = """
# RDST (Readyset Data and SQL Toolkit) Documentation

## Overview
RDST is a CLI tool for database performance analysis and SQL query optimization.
It connects to PostgreSQL or MySQL databases and provides AI-powered recommendations.

## Installation

**Requirements:** Python 3.9+

### macOS
```bash
pip install rdst
```

### Linux
```bash
pip install rdst
```

### Windows
Coming soon.

### Verify Installation
```bash
rdst version
```

## Quick Start
```bash
# First-time setup wizard
rdst init

# Or manually add a database target
rdst configure add --target mydb --engine postgresql --host localhost --port 5432 --user postgres --database myapp --password-env MY_DB_PASSWORD

# Set your password (never stored in config)
export MY_DB_PASSWORD="your-password"

# Analyze a slow query
rdst analyze -q "SELECT * FROM users WHERE email = 'test@example.com'" --target mydb

# Monitor slow queries in real-time
rdst top --target mydb
```

## Recommended Workflow

**Step 1: Initial Setup**
```bash
rdst init                    # First-time setup wizard
# OR manually:
rdst configure add ...       # Add database target
export MY_DB_PASSWORD="..."  # Set password env var
rdst configure test mydb     # Verify connection works
```

**Step 2: Find Slow Queries**
```bash
# Historical stats (instant results - works for both PostgreSQL and MySQL)
rdst top --target mydb --historical

# Real-time monitoring (captures queries as they run)
rdst top --target mydb --duration 30

# MySQL: Additional slow log source (requires setup)
rdst top --source slowlog --target mysql-db

# Save interesting queries to analyze later
rdst query save abc123 --name "slow-order-query"
```

**Step 3: Analyze Queries**
```bash
# Analyze a captured query
rdst analyze --hash abc123 --target mydb

# Or analyze a query directly
rdst analyze -q "SELECT ..." --target mydb

# Get AI optimization suggestions
rdst analyze -q "SELECT ..." --target mydb --interactive
```

**Step 4: Test Readyset Caching (Optional)**
```bash
# Requires Docker installed and running
rdst analyze -q "SELECT ..." --target mydb --readyset-cache
```

**Step 5: Iterate**
```bash
# List saved queries
rdst query list

# Re-analyze with changes
rdst analyze --hash abc123 --interactive

# Benchmark query after implementing changes
rdst query run slow-order-query --target mydb
```

## Commands

### rdst init
Interactive setup wizard for first-time configuration.
- Guides you through adding database targets
- Configures LLM API key (Anthropic recommended)
- Tests connectivity

### rdst configure
Manage database targets.

```bash
# Add a new target
rdst configure add --target prod-db --engine postgresql --host db.example.com --port 5432 --user admin --database myapp --password-env PROD_DB_PASSWORD

# List all targets
rdst configure list

# Remove a target
rdst configure remove --target old-db

# Set default target
rdst configure default --target prod-db

# Configure LLM provider
rdst configure llm --provider anthropic
```

### rdst analyze
Analyze a SQL query for performance optimization.

```bash
# Basic analysis
rdst analyze -q "SELECT * FROM orders WHERE status = 'pending'" --target mydb

# Fast mode (10s timeout for slow queries)
rdst analyze -q "SELECT * FROM big_table" --target mydb --fast

# Test Readyset cacheability (requires Docker)
rdst analyze -q "SELECT * FROM orders" --target mydb --readyset-cache

# Continue previous analysis interactively
rdst analyze --hash abc123 --interactive
```

Output includes:
- Execution plan analysis
- Index recommendations (CREATE INDEX statements)
- Query rewrites (optimized SQL)
- Performance rating

### rdst top
Monitor slow queries in real-time.

```bash
# Watch for slow queries (default 10 seconds)
rdst top --target mydb

# Run for 30 seconds
rdst top --target mydb --duration 30

# Set minimum query duration to capture (ms)
rdst top --target mydb --min-duration 100
```

Shows:
- Currently running queries
- Query duration
- Normalized query patterns
- Execution counts

After monitoring, you'll be prompted to save discovered queries to the registry
for later analysis or benchmarking.

### rdst query
Manage saved queries in your registry.

```bash
# Save a query for later analysis
rdst query add my-slow-query -q "SELECT * FROM orders JOIN items ON ..." --target mydb

# List saved queries
rdst query list

# List with filtering
rdst query list --filter "users"           # Search SQL, names, hash, source
rdst query list --target prod              # Filter by target database
rdst query list --interactive              # Paginated selection mode

# Show query details
rdst query show my-slow-query

# Edit a query (opens $EDITOR)
rdst query edit my-slow-query

# Delete a saved query
rdst query delete my-slow-query
```

### rdst query run
Run saved queries for benchmarking and load generation.

```bash
# Run a query once
rdst query run my-query

# Run multiple queries round-robin
rdst query run query1 query2 query3 --target mydb

# Fixed interval mode - run every 100ms
rdst query run my-query --interval 100

# Concurrency mode - maintain 10 concurrent executions
rdst query run my-query --concurrency 10

# With limits
rdst query run my-query --duration 60      # Stop after 60 seconds
rdst query run my-query --count 1000       # Stop after 1000 executions

# Tight loop (as fast as possible)
rdst query run my-query --duration 30      # Run for 30s with no delay

# Quiet mode (summary only)
rdst query run my-query --duration 60 --quiet
```

Output includes:
- Live progress table with QPS
- Per-query statistics (min/avg/p95/max latency)
- Success/failure counts
- Final summary

### rdst report
Send feedback to the RDST team.

```bash
# Report an issue
rdst report --reason "Analysis gave wrong recommendation" --hash abc123 --negative

# Report positive feedback
rdst report --reason "Great index suggestion!" --hash abc123 --positive
```

### rdst ask
Generate SQL from natural language questions.

```bash
# Ask a question about your data
rdst ask "Show me top 10 customers by order value" --target mydb

# Dry run - generate SQL without executing
rdst ask "Count orders by status" --target mydb --dry-run

# Use agent mode for complex queries
rdst ask "What's the relationship between customers and orders?" --target mydb --agent
```

The ask command:
- Understands your database schema automatically
- Generates optimized SQL queries
- Validates SQL before execution
- Shows results in a readable table

### rdst schema
Manage semantic layer for better SQL generation.

```bash
# Initialize semantic layer from database
rdst schema init --target mydb

# View semantic layer
rdst schema show --target mydb

# AI-generate column/table descriptions
rdst schema annotate --target mydb --use-llm

# Edit semantic layer manually
rdst schema edit --target mydb

# Export semantic layer
rdst schema export --target mydb --format yaml

# Delete semantic layer
rdst schema delete --target mydb
```

The semantic layer stores:
- Table and column descriptions
- Enum value meanings
- Business terminology
- Relationships between tables

This helps `rdst ask` generate more accurate SQL.

### rdst scan
Scan codebases for ORM queries and analyze them for performance issues.

Supports 4 ORMs across Python and JavaScript/TypeScript:
- **Python**: SQLAlchemy (1.x and 2.0), Django ORM
- **JS/TS**: Prisma, Drizzle

```bash
# Basic scan - find all ORM queries in a directory
rdst scan ./backend --schema mydb

# Git diff mode - only scan changed files (great for CI)
rdst scan ./backend --schema mydb --diff HEAD        # Uncommitted changes
rdst scan ./backend --schema mydb --diff HEAD~1      # Since last commit
rdst scan ./backend --schema mydb --diff abc123      # Since specific commit

# Shallow analysis (schema-only, no DB connection needed)
rdst scan ./backend --schema mydb --analyze --shallow

# Deep analysis (EXPLAIN ANALYZE against live DB - requires DB password)
rdst scan ./backend --schema mydb --analyze

# CI mode - exit code 1 if any query scores below fail threshold
rdst scan ./backend --schema mydb --analyze --check
rdst scan ./backend --schema mydb --analyze --check --fail-threshold 50

# JSON output for scripting
rdst scan ./backend --schema mydb --output json
```

The scan command:
- Uses AST parsing (Python) and regex extraction (JS/TS) to find ORM patterns (100% deterministic)
- Converts ORM code to SQL using schema context (Claude Haiku, cached by hash)
- Git diff integration for incremental CI checks
- Two analysis modes: shallow (schema-only) and deep (EXPLAIN ANALYZE + LLM)
- Assigns risk scores (0-100) for CI pass/fail decisions
- Shows per-query progress with real-time score/timing output during deep analysis

Analysis modes:
- **Shallow** (`--analyze --shallow`): Schema-only, no DB connection. Fast. Good for CI without DB access.
- **Deep** (`--analyze`): Runs EXPLAIN ANALYZE against live DB + LLM analysis. Requires `ANTHROPIC_API_KEY` and DB password. Shows execution time, index recommendations, query rewrites.

Options:
- `--diff REF`: Only scan files changed since REF (HEAD, HEAD~1, commit ID, branch)
- `--analyze`: Run analysis on queries (deep by default, add `--shallow` for schema-only)
- `--shallow`: Use schema-only analysis (no DB connection needed)
- `--check`: CI mode - set exit code based on scores (0=pass, 1=fail)
- `--warn-threshold N`: Score below which to warn (default: 50)
- `--fail-threshold N`: Score below which to fail (default: 30)
- `--output {table,json}`: Output format
- `--nosave`: Don't save queries to registry
- `--sequential`: Run analysis queries one at a time instead of in parallel batches. Produces more deterministic scores for deep analysis since queries don't compete for database resources. Slower but more reproducible.
- `--dry-run`: Show what would be scanned without calling the LLM
- `--file-pattern GLOB`: Only scan files matching this pattern

CI thresholds:
- `--check` enables exit codes. Without it, scores are informational only (always exit 0).
- Queries scoring below `--fail-threshold` trigger CI failure (exit 1).
- Queries scoring below `--warn-threshold` trigger a warning (exit 0).
- The summary shows which queries breached each threshold.

Risk score ranges (0-100, higher is better):

- **86-100 (excellent)**: Well-optimized with indexes and LIMIT.
  Example: `SELECT * FROM orders WHERE o_custkey = $1 ORDER BY o_orderdate DESC LIMIT 20`
  (Index on o_custkey, bounded result set, fast lookup)

- **71-85 (good)**: Works well, minor improvements possible.
  Example: `SELECT COUNT(*) FROM orders WHERE o_orderstatus = 'F'`
  (Scans matching rows but no index on status — still acceptable for moderate tables)

- **51-70 (fair)**: Noticeable issues, may scan more rows than needed.
  Example: `SELECT * FROM customer WHERE c_name LIKE '%smith%'`
  (Leading wildcard prevents index usage, scans full table, but has implicit single-table scope)

- **31-50 (poor)**: Significant risk — full scans, missing indexes, anti-patterns.
  Example: `SELECT * FROM orders ORDER BY o_totalprice DESC`
  (Full table scan + sort on unindexed column, no LIMIT — returns ALL rows sorted)

- **0-30 (critical)**: Severe — unbounded scans on large tables, dangerous operations.
  Example: `DELETE FROM customer` or `SELECT * FROM lineitem` (60M rows, no WHERE, no LIMIT)

Recommended threshold presets:

- **Lenient** (`--fail-threshold 20 --warn-threshold 40`):
  Only blocks critical queries (score < 20). Poor queries (31-50) pass silently.
  Use for: Legacy codebases, initial adoption, non-critical batch jobs.

- **Default** (`--fail-threshold 30 --warn-threshold 50`):
  Blocks critical queries, warns on poor ones. Fair queries (51-70) pass cleanly.
  Use for: Most applications, CI pipelines, general development.

- **Strict** (`--fail-threshold 50 --warn-threshold 70`):
  Blocks poor AND critical. Only fair-or-better queries pass.
  Use for: Production APIs, user-facing endpoints, high-traffic services.

- **Aggressive** (`--fail-threshold 70 --warn-threshold 85`):
  Requires good or excellent. Even fair queries trigger failure.
  Use for: Performance-critical hot paths, latency-sensitive microservices.

### rdst cache
Deploy ReadySet and manage shallow caches. Shallow caching stores query results in
ReadySet's in-memory cache with a configurable TTL (time-to-live). Cached queries are
served directly from memory (typically 10-100x faster), then refreshed from the upstream
database when the TTL expires.

**Important:** Cache add/show/delete commands only work with ReadySet targets
(`target_type=readyset`). These are auto-created by `rdst cache deploy` with the name
`{original_target}-cache`. If you try to use a regular database target, you'll get an
error with instructions to deploy first.

#### rdst cache deploy
Deploy ReadySet shallow cache permanently to local, remote, or Kubernetes environments.

If a ReadySet container already exists from a prior `analyze --readyset-cache`, it will
be promoted to a permanent deployment. Otherwise creates a new one.

```bash
# Deploy locally via Docker
rdst cache deploy --target mydb --mode docker

# Deploy as a systemd service (native binary)
rdst cache deploy --target mydb --mode systemd

# Deploy to a remote server via SSH (Docker)
rdst cache deploy --target mydb --mode docker --host 10.0.1.50

# Deploy to a remote server via SSH (systemd)
rdst cache deploy --target mydb --mode systemd --host 10.0.1.50

# Deploy to Kubernetes
rdst cache deploy --target mydb --mode kubernetes
rdst cache deploy --target mydb --mode kubernetes --kubeconfig /path/to/kubeconfig.yaml

# Generate deployment script without executing
rdst cache deploy --target mydb --mode docker --script-only
rdst cache deploy --target mydb --mode systemd --script-only
rdst cache deploy --target mydb --mode kubernetes --script-only

# JSON output
rdst cache deploy --target mydb --mode docker --json
```

Deployment modes:
- **docker**: Runs ReadySet in a Docker container with `--restart=unless-stopped`.
  For local deploy, reuses/promotes existing containers from `analyze --readyset-cache`.
- **systemd**: Installs ReadySet as a native binary with a systemd service unit.
  Extracts binary from Docker image, creates config and service file.
- **kubernetes**: Creates Kubernetes Secret, Deployment, and Service via kubectl.
  Requires kubectl configured with cluster access.

Remote deployment (--host):
- Uses SSH/SCP to deploy to remote servers. Respects `~/.ssh/config` and ssh-agent.
- Leaves a management script at `/opt/rdst/deploy-<target>.sh` on the remote host.
- Management commands: `status`, `logs`, `restart`, `stop`, `uninstall`

Options:
- `--target NAME`: Database target to deploy for (required)
- `--mode {docker,systemd,kubernetes}`: Deployment mode (required)
- `--host HOST`: Remote host for SSH deployment (omit for local)
- `--ssh-key PATH`: SSH private key path
- `--ssh-user USER`: SSH username (default: root)
- `--port PORT`: ReadySet listen port (default: auto based on engine)
- `--namespace NS`: Kubernetes namespace (default: readyset)
- `--kubeconfig PATH`: Path to kubeconfig file for Kubernetes deployment
- `--script-only`: Generate script without executing
- `--config {readyset,readyset-squeepy}`: Deployment config
- `--json`: JSON output

After deployment, the output shows:
- Connection endpoint to point your application to (instead of the database)
- Management commands for the deployed instance
- Auto-registered ReadySet target (e.g., `mydb-cache`) for use with `rdst cache`

#### rdst cache add
Create a shallow cache for a query.

```bash
# Cache a SQL query
rdst cache add "SELECT * FROM orders WHERE status = 'pending'" --target mydb-cache

# Cache by registry hash (4-12 hex chars, like git short hashes)
rdst cache add abc123de --target mydb-cache

# Cache with a tag for the registry
rdst cache add "SELECT COUNT(*) FROM users" --target mydb-cache --tag user-count
```

What happens when you run `cache add`:
1. Static cacheability check (rejects non-SELECT, NOW(), RANDOM(), etc.)
2. EXPLAIN CREATE CACHE against ReadySet (tests if the query structure is supported)
3. CREATE SHALLOW CACHE (creates the cache with TTL)
4. Saves query to registry (normalized, with hash for later reference)

After caching, benchmark with:
```bash
rdst query run <hash> --target mydb-cache    # ReadySet (cached)
rdst query run <hash> --target mydb          # Direct database
```

#### rdst cache show
List all cached queries with their type and TTL.

```bash
rdst cache show --target mydb-cache
rdst cache show --target mydb-cache --json
```

Output columns: Cache Name, Query, Type (shallow/full), TTL (e.g., 10s).
Use the Cache Name with `cache delete` to remove specific caches.

#### rdst cache delete
Remove a specific cache by its cache name/ID (from `cache show` output).

```bash
rdst cache delete q_54fc6da6d5703402 --target mydb-cache
```

#### rdst cache drop-all
Remove ALL caches from ReadySet. Asks for confirmation unless `--yes` is passed.

```bash
rdst cache drop-all --target mydb-cache        # Prompts for confirmation
rdst cache drop-all --target mydb-cache --yes  # Skip confirmation
```

#### Typical workflow
```bash
# 1. Deploy ReadySet (creates mydb-cache target automatically)
rdst cache deploy --target mydb --mode docker

# 2. Find slow queries
rdst top --target mydb

# 3. Cache them
rdst cache add "SELECT ..." --target mydb-cache

# 4. Verify performance improvement
rdst query run <hash> --target mydb-cache   # Should be much faster
rdst query run <hash> --target mydb         # Compare with direct DB

# 5. View all caches
rdst cache show --target mydb-cache

# 6. Clean up if needed
rdst cache drop-all --target mydb-cache --yes
```

## Password Handling
RDST never stores passwords in config files. Each target has a `password_env` field
specifying which environment variable holds the password.

```bash
# Config shows: password_env = "PROD_DB_PASSWORD"
# You must export this before running commands:
export PROD_DB_PASSWORD="your-actual-password"
```

## Common Workflows

### Optimizing a Slow Query
1. Identify slow query with `rdst top --target mydb`
2. Copy the query and run `rdst analyze -q "..." --target mydb`
3. Review index recommendations
4. Create suggested indexes
5. Re-run analysis to verify improvement

### Testing Readyset Caching
1. Run `rdst analyze -q "..." --target mydb --readyset-cache`
2. Wait 30-60 seconds for containers to start
3. Review if query is cacheable
4. If cacheable, see the CREATE CACHE command for production

### Benchmarking Queries
1. Discover slow queries with `rdst top --target mydb`
2. Save them to the registry when prompted
3. Analyze with `rdst analyze --name my-query`
4. Apply recommended optimizations (indexes, rewrites)
5. Benchmark with `rdst query run my-query --duration 30`
6. Compare QPS and latency before/after changes

### Load Testing
Generate sustained load against your database:
```bash
# Constant rate: 10 queries/second for 60 seconds
rdst query run my-query --interval 100 --duration 60

# High concurrency: 50 parallel connections for 60 seconds
rdst query run my-query --concurrency 50 --duration 60

# Mixed workload: multiple queries round-robin
rdst query run read_query write_query --concurrency 20 --duration 120
```

### Setting Up Multiple Databases
```bash
rdst configure add --target prod --engine postgresql --host prod.db.com ...
rdst configure add --target staging --engine postgresql --host staging.db.com ...
rdst configure default --target prod
```

## Supported Databases
- PostgreSQL
- MySQL

### rdst top --source Options (Database-Specific)
| Source | PostgreSQL | MySQL | Description |
|--------|------------|-------|-------------|
| auto | ✓ | ✓ | Automatically selects correct source (default) |
| pg_stat | ✓ | ✗ | pg_stat_statements aggregated stats |
| activity | ✓ | ✓ | Currently running queries |
| digest | ✗ | ✓ | performance_schema aggregated stats |
| slowlog | ✗ | ✓ | mysql.slow_log table (requires setup) |

**Important:**
- Using a source incompatible with your database will fail
- Specifying `--source` automatically enables historical mode (no need for `--historical` flag)
- Use `--source auto` (default) to let RDST pick the correct source

## PostgreSQL-Specific Features

### rdst top Sources for PostgreSQL
RDST supports multiple data sources for PostgreSQL query monitoring:

```bash
# Default: Use pg_stat_statements (aggregated stats)
rdst top --target pg-db --historical

# Explicit sources:
rdst top --target pg-db --source pg_stat    # pg_stat_statements
rdst top --target pg-db --source activity   # pg_stat_activity (running queries)
```

**Source comparison:**
| Source | What it shows | Requirements |
|--------|--------------|--------------|
| pg_stat | Aggregated query stats | pg_stat_statements extension |
| activity | Currently running queries | None |

### Enabling pg_stat_statements
The pg_stat_statements extension provides the best query statistics:

**For self-hosted PostgreSQL:**
```sql
-- Add to postgresql.conf:
shared_preload_libraries = 'pg_stat_statements'

-- Then restart PostgreSQL and run:
CREATE EXTENSION pg_stat_statements;
```

**For AWS RDS/Aurora PostgreSQL:**
pg_stat_statements is available by default. Enable it with:
```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
```

**For Google Cloud SQL:**
Enable the `cloudsql.enable_pg_stat_statements` flag in your instance settings.

### PostgreSQL EXPLAIN Output
PostgreSQL EXPLAIN ANALYZE provides detailed execution statistics:
- Actual vs estimated rows at each plan node
- Time spent in each operation (actual time)
- Buffer usage (shared hit, read, written)
- Sort and hash memory usage

### PostgreSQL Example Workflow
```bash
# 1. Add PostgreSQL target
rdst configure add --target pg-prod --engine postgresql --host db.example.com --port 5432 --user admin --database myapp --password-env PG_PASSWORD

# 2. Check historical query stats
rdst top --target pg-prod --historical

# 3. Monitor for slow queries in real-time
rdst top --target pg-prod --duration 30

# 4. Analyze a slow query
rdst analyze -q "SELECT * FROM orders WHERE status = 'pending'" --target pg-prod
```

## MySQL-Specific Features

### rdst top Sources for MySQL
RDST supports multiple data sources for MySQL query monitoring:

```bash
# Default: Use performance_schema digest (aggregated stats)
rdst top --target mysql-db --historical

# Explicit sources:
rdst top --target mysql-db --source digest     # performance_schema
rdst top --target mysql-db --source activity   # SHOW PROCESSLIST (running queries)
rdst top --target mysql-db --source slowlog    # mysql.slow_log table
```

**Source comparison:**
| Source | What it shows | Requirements |
|--------|--------------|--------------|
| digest | Aggregated query stats | performance_schema enabled (default) |
| activity | Currently running queries | None |
| slowlog | Individual slow query executions | slow_query_log enabled, log_output=TABLE |

### Enabling MySQL Slow Query Log
The `slowlog` source requires enabling MySQL's slow query log with TABLE output:

**For self-hosted MySQL:**
```sql
SET GLOBAL slow_query_log = 'ON';
SET GLOBAL long_query_time = 1;      -- Log queries > 1 second
SET GLOBAL log_output = 'TABLE';     -- Required for RDST access
```
No restart required - changes take effect immediately.

**For AWS RDS/Aurora:**
Modify your RDS parameter group:
- slow_query_log = 1
- long_query_time = 1
- log_output = TABLE

Apply in RDS Console or via AWS CLI. These are dynamic parameters (no reboot needed).

### MySQL EXPLAIN Output
MySQL EXPLAIN ANALYZE output differs from PostgreSQL:
- Times are shown per operation
- Index usage is shown in `key` column
- `rows` shows estimated rows examined
- `filtered` shows percentage of rows that pass conditions

### MySQL Example Workflow
```bash
# 1. Add MySQL target
rdst configure add --target mysql-prod --engine mysql --host db.example.com --port 3306 --user admin --database myapp --password-env MYSQL_PASSWORD

# 2. Monitor for slow queries
rdst top --target mysql-prod --duration 30

# 3. Analyze a slow query
rdst analyze -q "SELECT * FROM orders WHERE status = 'pending'" --target mysql-prod

# 4. View historical stats from performance_schema
rdst top --target mysql-prod --source digest
```

## LLM Provider
- Anthropic Claude - requires ANTHROPIC_API_KEY

## Semantic Layer (Annotations)

The semantic layer lets you document business logic that isn't obvious from the schema alone.
This helps RDST's AI provide better recommendations.

**Why annotate?** Database schemas don't capture business meaning. For example, if you have:
```sql
CREATE TABLE posts (
    id INT,
    post_type_id INT,  -- What do these values mean?
    ...
);
```

The AI doesn't know that `post_type_id = 1` means "question", `2` means "answer", `3` means "comment".
With annotations, you tell RDST what these values mean so it can give smarter recommendations.

**Usage:**
```bash
# Initialize from your database schema
rdst schema init --target mydb

# Auto-generate descriptions using AI (good starting point)
rdst schema annotate --target mydb --use-llm

# Or manually add business context
rdst schema annotate --target mydb

# View current annotations
rdst schema show --target mydb
```

**What to annotate:**
- Enum values with business meanings (status codes, type IDs)
- Columns with non-obvious purposes
- Table relationships and business rules
- Domain-specific terminology

Annotations are **optional** but recommended for complex schemas with business logic encoded in numeric values.

## Troubleshooting

### "Authentication failed"
- Check if password environment variable is exported
- Verify the password is correct
- Check host/port connectivity

### "Connection refused"
- Verify database host and port
- Check firewall rules
- Ensure database is running

### "No LLM API key configured"
- Run `rdst configure llm --provider anthropic`
- Export ANTHROPIC_API_KEY environment variable

## Docker Requirements (--readyset-cache)

The `--readyset-cache` flag for `rdst analyze` uses Docker to test Readyset cacheability.

**Prerequisites:**
- Docker must be installed and running
- User must have permission to run Docker commands
- First run downloads container image (~500MB)

**What happens when you use --readyset-cache:**
1. RDST starts a single Readyset container (`rdst-readyset`) that connects directly to your upstream database
2. Uses shallow caching mode - no data replication or snapshotting required
3. Attempts to cache the query in Readyset
4. Reports cacheability status and any issues
5. Container is kept running for subsequent tests

**Resource usage:**
- Memory: ~500MB-1GB for Readyset container
- Disk: ~500MB for image (first run)
- CPU: Moderate during cacheability testing

**Cleanup:**
Container remains running after tests. To stop it:
```bash
docker stop rdst-readyset
docker rm rdst-readyset
```

**Important:** The first `--readyset-cache` run may take 30-60 seconds while the image downloads.
Subsequent runs are faster (5-10 seconds).

## Troubleshooting

### Readyset cache errors
- Docker not found: Install Docker and ensure daemon is running
- If a query can't be cached, Readyset will explain why in the output

### MySQL slow log not accessible
If `rdst top --source slowlog` fails:
1. Check if slow_query_log is enabled: `SELECT @@slow_query_log;`
2. Check log_output includes TABLE: `SELECT @@log_output;`
3. For RDS, check your parameter group settings
4. Alternative: Use `--source digest` for aggregated stats instead

### MySQL performance_schema not available
If digest source fails:
- performance_schema is enabled by default in MySQL 5.6+
- Check: `SHOW VARIABLES LIKE 'performance_schema';`
- If disabled, add `performance_schema=ON` to my.cnf and restart

## Config File Location
- Main config: ~/.rdst/config.toml
- Query registry: ~/.rdst/queries.toml
- Conversation history: ~/.rdst/conversations/
"""


@dataclass
class HelpResult:
    """Result from help command."""

    success: bool
    answer: str
    error: Optional[str] = None


class HelpCommand:
    """Implements `rdst help` quick docs lookup."""

    def __init__(self):
        self.console = get_console()

    def print_formatted(self, text: str) -> None:
        """Print text with markdown formatting."""
        self.console.print(
            StyledPanel(
                MarkdownContent(text),
                title=f"[{StyleTokens.HEADER}]RDST Help[/{StyleTokens.HEADER}]",
                border_style=StyleTokens.PANEL_BORDER,
                box=UILayout.BOX_DEFAULT,
            )
        )

    def run(self, question: str) -> HelpResult:
        """
        Answer a question about RDST using embedded docs and Haiku.

        Args:
            question: Natural language question like "how do I analyze a query?"

        Returns:
            HelpResult with the answer
        """
        # Check for API key or trial token
        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("RDST_TRIAL_TOKEN")
        if not api_key:
            try:
                from ..llm_manager.key_resolution import resolve_api_key
                resolve_api_key()
                api_key = True  # Trial token available
            except Exception:
                pass
        if not api_key:
            return HelpResult(
                success=False,
                answer="",
                error="""No LLM API key configured.

Options:
  1. Run 'rdst init' to sign up for a free trial (up to 925K tokens)
  2. Set your own key: export ANTHROPIC_API_KEY="sk-ant-..."
     Get one at: https://console.anthropic.com/
""",
            )

        try:
            from lib.llm_manager.llm_manager import LLMManager

            # Use Haiku for fast, cheap responses
            from lib.llm_manager.claude_provider import AnthropicModel
            llm = LLMManager(defaults={"model": AnthropicModel.HAIKU_4_5.value})

            # Build prompt
            system_message = """You are a helpful assistant for RDST, a database performance analysis CLI tool.
Answer the user's question based on the documentation provided. Be concise and practical.
Include command examples when relevant. If the question isn't covered in the docs, say so."""

            user_query = f"""## RDST Documentation
{RDST_DOCS}

## User Question
{question}

## Answer (be concise, include command examples):"""

            # Call LLM with spinner feedback
            with Spinner("Thinking..."):
                response = llm.query(
                    system_message=system_message,
                    user_query=user_query,
                    max_tokens=1000,
                    model=AnthropicModel.HAIKU_4_5.value,
                )

            # Response format: {"text": "...", "usage": {...}, "provider": "...", "model": "..."}
            if response.get("text"):
                return HelpResult(success=True, answer=response["text"].strip())
            else:
                return HelpResult(
                    success=False,
                    answer="",
                    error=response.get("error") or "Failed to get response from LLM",
                )

        except Exception as e:
            # Fallback: simple keyword search if LLM fails
            return self._fallback_search(question, str(e))

    def _fallback_search(self, question: str, error: str) -> HelpResult:
        """Fallback when LLM is unavailable - basic keyword matching."""
        question_lower = question.lower()

        # Simple keyword matching
        if "analyze" in question_lower:
            answer = """To analyze a query:

```bash
rdst analyze -q "YOUR SQL QUERY" --target your-target
```

Options:
- --fast: Skip slow queries (10s timeout)
- --readyset-cache: Test if query can be cached by Readyset
- --interactive: Continue analysis conversation

Example:
```bash
rdst analyze -q "SELECT * FROM users WHERE id = 1" --target mydb
```"""
        elif (
            "top" in question_lower
            or "slow" in question_lower
            or "monitor" in question_lower
        ):
            answer = """To monitor slow queries:

```bash
rdst top --target your-target
```

Options:
- --duration N: Run for N seconds (default 10)
- --min-duration N: Only show queries slower than N ms

Example:
```bash
rdst top --target mydb --duration 30
```

After monitoring, you can save queries to the registry for analysis or benchmarking."""
        elif "list" in question_lower and "query" in question_lower:
            answer = """To list saved queries:

```bash
# List all queries
rdst query list

# Filter by SQL content, name, hash, or source
rdst query list --filter "users"

# Filter by target database
rdst query list --target prod

# Interactive mode with pagination
rdst query list --interactive

# Show details of a specific query
rdst query show my-query
```"""
        elif (
            "run" in question_lower
            or "benchmark" in question_lower
            or "load" in question_lower
        ):
            answer = """To run queries for benchmarking or load testing:

```bash
# Run a query once
rdst query run my-query

# Fixed interval mode (every 100ms)
rdst query run my-query --interval 100 --duration 60

# Concurrency mode (10 parallel connections)
rdst query run my-query --concurrency 10 --duration 60

# Multiple queries round-robin
rdst query run query1 query2 --concurrency 20
```

Options:
- --interval MS: Run every N milliseconds
- --concurrency N: Maintain N concurrent executions
- --duration SECS: Stop after N seconds
- --count N: Stop after N executions
- --quiet: Show only summary

Output includes QPS, latency stats (min/avg/p95/max), and success/failure counts."""
        elif (
            "configure" in question_lower
            or "add" in question_lower
            or "target" in question_lower
        ):
            answer = """To configure a database target:

```bash
rdst configure add --target NAME --engine postgresql --host HOST --port PORT --user USER --database DB --password-env ENV_VAR
```

Then export your password:
```bash
export ENV_VAR="your-password"
```

List targets: `rdst configure list`
Set default: `rdst configure default --target NAME`"""
        elif "password" in question_lower:
            answer = """RDST never stores passwords. Each target has a password_env field.

1. Check your target's password_env: `rdst configure list`
2. Export it: `export MY_DB_PASSWORD="your-password"`
3. Run your command

The password must be exported before each session."""
        elif "cache" in question_lower or "readyset" in question_lower:
            answer = """ReadySet shallow caching — two approaches:

**Quick test (ephemeral, for exploration):**
```bash
rdst analyze -q "YOUR QUERY" --target your-target --readyset-cache
```

**Production caching (persistent, via rdst cache commands):**
```bash
# 1. Deploy ReadySet (auto-registers mydb-cache target)
rdst cache deploy --target mydb --mode docker

# 2. Cache queries (target must be readyset type, e.g., mydb-cache)
rdst cache add "SELECT * FROM orders WHERE id = 1" --target mydb-cache

# 3. View caches (shows cache name, type, TTL)
rdst cache show --target mydb-cache

# 4. Benchmark cached vs direct
rdst query run <hash> --target mydb-cache  # Cached (fast)
rdst query run <hash> --target mydb        # Direct DB (slow)

# 5. Remove caches
rdst cache delete <cache_name> --target mydb-cache
rdst cache drop-all --target mydb-cache --yes
```

Cache commands require a ReadySet target (target_type=readyset).
Deploy creates this automatically as {target}-cache."""
        elif (
            "init" in question_lower
            or "setup" in question_lower
            or "start" in question_lower
        ):
            answer = """To set up RDST for the first time:

```bash
rdst init
```

This wizard will:
1. Add your database target(s)
2. Configure LLM API key
3. Test connectivity

Or manually:
```bash
rdst configure add --target mydb --engine postgresql ...
rdst configure llm --provider anthropic
export ANTHROPIC_API_KEY="your-key"
```"""
        else:
            answer = f"""I couldn't find specific docs for your question.

Try:
- `rdst --help` for all commands
- `rdst COMMAND --help` for command-specific help
- Common commands: analyze, top, configure, init

(LLM unavailable: {error})"""

        return HelpResult(success=True, answer=answer)

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

from shared.ui import (
    get_console,
    MarkdownContent,
    Spinner,
    StyleTokens,
    Layout as UILayout,
    StyledPanel,
)

# Embedded documentation for RDST
RDST_DOCS = """
# RDST (ReadySet Data and SQL Toolkit) Documentation

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
rdst query add abc123 --name "slow-order-query"
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

**Step 4: Compare ReadySet Performance**
```bash
# ReadySet caching is tested automatically during analyze (if Docker is available)
# For a dedicated benchmark comparison:
rdst query cache-compare <query-name> --target mydb --count 100
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

```

### rdst analyze
Analyze a SQL query for performance optimization.

```bash
# Basic analysis
rdst analyze -q "SELECT * FROM orders WHERE status = 'pending'" --target mydb

# Fast mode (10s timeout for slow queries)
rdst analyze -q "SELECT * FROM big_table" --target mydb --fast

# ReadySet caching is tested automatically (requires Docker)
rdst analyze -q "SELECT * FROM orders" --target mydb

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
Run saved queries or queries from a file for benchmarking
and load generation.

```bash
# Run a query once
rdst query run my-query

# Run multiple queries round-robin
rdst query run query1 query2 query3 --target mydb

# Run queries from a CSV file
rdst query run --file queries.csv --target mydb

# Replay from file with LLM analysis of results
rdst query run --file queries.csv --target mydb --analyze

# Replay for a duration
rdst query run --file queries.csv --target mydb --duration 5m

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

CSV format: a file with a `query` column header and one
SQL query per row.

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

If a ReadySet container already exists from a prior `analyze --target mydb`, it will
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
  For local deploy, reuses/promotes existing containers from `analyze --target mydb`.
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

### rdst audit
Health audit of a single database target. Includes metrics,
top queries, and ReadySet cache opportunity scoring.

```bash
# Quick audit (metrics + top queries + insights)
rdst audit --target mydb

# Skip LLM insights (faster, metrics-only)
rdst audit --target mydb --no-insights

# Live capture: collect queries over a time window
rdst audit --target mydb --duration 5m

# Don't save captured queries to the registry
rdst audit --target mydb --duration 5m --no-save

# Save audit result with a name for later comparison
rdst audit --target mydb --save baseline

# JSON output for scripting
rdst audit --target mydb --json
```

Collects: connection utilization, buffer cache hit rate,
database size, read/write ratio, replication status,
top queries from pg_stat_statements / performance_schema.
Computes sizing verdict (under-provisioned/oversized/
right-sized) and ReadySet cache opportunity score (0-100).

#### Duration mode (`--duration`)

With `--duration`, rdst captures all queries over a time window
with start/end database snapshots, intermediate 30s samples,
and per-table statistics. LLM analysis produces health score,
bottlenecks, index recommendations, caching candidates,
and optimization priorities. Queries are auto-saved to the
registry (use `--no-save` to skip).

When using `--duration`, rdst also collects table schema
information (existing indexes, column types) so that index
recommendations are schema-aware. This means rdst will not
suggest creating an index that already exists, and
recommendations include the exact CREATE INDEX DDL ready
to copy-paste.

**Truncation warning:** If MySQL's digest length settings are too low
(default 1024 bytes), captured query text will be truncated. The audit
output warns when this is detected. To fix:

1. Set `performance_schema_max_digest_length = 16384` in the DB parameter group
2. Set `max_digest_length = 16384` in the DB parameter group
3. Restart the instance
4. Run `CALL sys.ps_truncate_all_tables(FALSE)` to clear old truncated entries
5. Wait for new query traffic to accumulate, then re-run audit

#### Browsing past audits

```bash
# List all saved audit runs
rdst audit list
rdst audit list --target mydb --json

# View a saved audit with full analysis
rdst audit show <run_id>
rdst audit show <run_id> --json
```

#### Exporting queries from a saved audit

Use `--export-queries`, `--export-top-queries`, or
`--export-captured-queries` with `audit show` to export
queries to stdout in a format suitable for piping or saving.

```bash
# Export all queries (captured if available, otherwise top)
rdst audit show <run_id> --export-queries

# Export only the cumulative top queries from stats
# (pg_stat_statements / performance_schema)
rdst audit show <run_id> --export-top-queries

# Export only queries captured during the --duration window
rdst audit show <run_id> --export-captured-queries
```

#### Options reference

- `--target NAME`: Database target (required for new audit)
- `--duration DURATION`: Live capture window (e.g., 2m, 5m, 1h)
- `--no-insights`: Skip LLM analysis (faster, metrics-only)
- `--no-save`: Don't save captured queries to the registry
- `--save NAME`: Save the audit result with a name
- `--source {auto,pg_stat_statements,activity}`: Query capture source
- `--limit N`: Top N queries to include (default: 50)
- `--diff BASELINE`: Compare against a saved baseline
- `--export-queries`: Export queries from a saved audit
- `--export-top-queries`: Export cumulative top queries only
- `--export-captured-queries`: Export duration-captured queries only
- `--json`: JSON output
- `--verbose` / `-v`: Print the full report directly to the terminal (skips email)

#### Output modes

By default, `rdst audit` shows a **compact summary in the terminal** and
emails the full report. The first time you run an audit you'll be prompted
for an email address and asked to click a verification link; after that
every subsequent run sends the report automatically to the verified email.
Use `--verbose` (or `-v`) to **skip the email entirely** and print the full
report directly to the terminal — useful for CI or when you don't want
email at all.

#### HTML report delivery

Every audit generates an HTML report saved locally to `~/.rdst/reports/`.
In default mode it's also hosted on the RDST keyservice and a "View Full
Report" link is sent to the verified email. The report has three sections:
Overview (topology + sizing & caching candidates), Detailed Analysis (per
target deep dive with captured queries, index recommendations, and
optimization priorities), and Next Steps (numbered actions with runnable
`rdst` commands and inline savings estimates).

#### ReadySet cache testing

When a ReadySet cache target is deployed for the audited database
and `--duration` is used, queries captured during the window are
automatically tested against the cache. Speedup measurements for
each query are included in the report.

### rdst fleet
Manage and audit multiple database targets as a fleet.

#### fleet configure
Interactive wizard for setting up fleet targets.

```bash
rdst fleet configure
rdst fleet configure --discover   # Start with AWS discovery
```

#### fleet import
Bulk import targets from a CSV file.

```bash
rdst fleet import --from fleet.csv --password-env FLEET_PASS
rdst fleet import --from fleet.csv --group production --tag critical
rdst fleet import --from fleet.csv --dry-run   # Preview without saving
```

CSV format columns: `name`, `host`, `port`, `database`, `user`,
`engine`, `group`, `tags`, `password_env`.
- `engine`: `postgresql` or `mysql`
- `tags`: comma-separated (e.g., `critical,us-east-1`)
- `password_env`: the **name** of the environment variable that
  holds the password (not the password itself). For example, if
  `password_env` is `PROD_DB_PASS`, you must `export PROD_DB_PASS="..."`
  before running rdst commands. Passwords are never stored in config
  files or CSV — only the env var name is stored.

Example CSV:
```
name,host,port,database,user,engine,group,tags,password_env
prod-primary,db1.example.com,5432,myapp,admin,postgresql,production,critical,PROD_DB_PASS
prod-replica,db2.example.com,5432,myapp,admin,postgresql,production,replica,PROD_DB_PASS
staging,staging.example.com,3306,myapp,admin,mysql,staging,,STAGING_PASS
```

Then before running commands:
```bash
export PROD_DB_PASS="my-prod-password"
export STAGING_PASS="my-staging-password"
```

#### fleet discover
Discover RDS/Aurora instances from AWS.

```bash
# Discover all RDS instances in specific regions
rdst fleet discover --regions us-east-1,us-west-2

# Filter by engine type
rdst fleet discover --regions us-east-1 --engine-filter postgresql

# Filter by instance name pattern
rdst fleet discover --regions us-east-1 --name-pattern "prod-*"

# Assign to a group and preview first
rdst fleet discover --regions us-east-1 --group production --dry-run

# Specify DB username
rdst fleet discover --regions us-east-1 --user admin
```

**Aurora cluster discovery:** When rdst discovers Aurora clusters,
it auto-creates separate targets for the cluster writer endpoint
and each individual reader instance. All targets from the same
cluster are automatically grouped by cluster ID and tagged as
`aurora/writer` or `aurora/reader`. This lets you audit writers
and readers independently or filter by role.

**Auto-scaling support:** Re-run `fleet discover` anytime to pick
up new auto-scaled reader instances. Existing targets are skipped
(matched by hostname), and new readers are automatically added to
the same cluster group with the correct tags. The credential wizard
runs only for newly discovered instances.

**Credential wizard:** After discovery completes, rdst prompts
with three credential options:
1. **Shared password** — one `password_env` for all discovered targets
2. **Per-instance credentials** — set a different `password_env` for each target
3. **Skip** — configure credentials later via `rdst configure`

To remove a target: `rdst configure remove --target <name>`

#### fleet list
List all fleet targets (excludes ReadySet cache targets).

```bash
rdst fleet list
rdst fleet list --group production
rdst fleet list --tag critical
rdst fleet list --json
```

#### fleet status
Check connectivity for all fleet targets. Runs a quick
connection test against each target and reports success/failure.

```bash
rdst fleet status
rdst fleet status --group production
rdst fleet status --tag aurora/writer
rdst fleet status --json
```

#### fleet audit
Run a health audit across all fleet targets concurrently
(max 10 targets in parallel).

```bash
# Basic fleet audit
rdst fleet audit

# Filter by group or tag
rdst fleet audit --group production
rdst fleet audit --tag critical

# Save the snapshot for later comparison
rdst fleet audit --save march-baseline

# Live capture mode: collect queries for each target over a window
rdst fleet audit --duration 2m

# Skip auto-saving the snapshot
rdst fleet audit --no-save

# Skip LLM insights (faster, metrics-only)
rdst fleet audit --no-insights

# JSON output
rdst fleet audit --json
```

With `--duration`, each target gets its own live capture
window where queries are observed in real-time. The per-target
results include the same depth of analysis as `rdst audit --duration`
(health score, bottlenecks, index recommendations, caching
candidates). Without `--duration`, uses cumulative stats from
pg_stat_statements / performance_schema.

By default, `rdst fleet audit` shows a compact summary in the terminal
and emails a hosted **combined HTML report** to your verified email
covering all audited targets. The report has three sections: Overview
(fleet topology + per-target sizing & caching candidates), Detailed
Analysis (per-target deep dives — collapsible), and Next Steps with
runnable commands.

Use `--verbose` (or `-v`) to print the full fleet report directly to
the terminal and skip the email. Per-target reports are also saved
locally to `~/.rdst/reports/` regardless of which mode you used.

#### fleet snapshots
List all saved fleet audit snapshots.

```bash
rdst fleet snapshots
rdst fleet snapshots --json
```

#### fleet diff
Compare two saved fleet audit snapshots side by side.
Shows changes in health scores, new/resolved issues,
and query pattern drift between the two points in time.

```bash
rdst fleet diff march-baseline april-baseline
rdst fleet diff march-baseline april-baseline --json
```

#### Typical fleet workflow
```bash
# 1. Discover your AWS fleet
rdst fleet discover --regions us-east-1,us-west-2
# → credentials wizard runs automatically

# 2. Verify connectivity
rdst fleet status

# 3. Run baseline audit and save it
rdst fleet audit --save baseline --duration 2m

# 4. After changes, run another audit
rdst fleet audit --save post-migration --duration 2m

# 5. Compare before/after
rdst fleet diff baseline post-migration
```

### rdst guard
Manage reusable safety policies for data agents.

#### guard create
Create a new guard policy.

```bash
rdst guard create --name my-policy
```

#### guard list
List all guard policies.

```bash
rdst guard list
```

#### guard show
Show details of a guard policy.

```bash
rdst guard show --name my-policy
```

#### guard delete
Delete a guard policy.

```bash
rdst guard delete --name my-policy
```

#### guard edit
Edit a guard policy in $EDITOR.

```bash
rdst guard edit --name my-policy
```

#### guard check
Check whether a SQL query passes a guard policy.

```bash
rdst guard check --guard my-policy --sql "DELETE FROM users"
```

### rdst agent
Manage and run data agents with safety policies.

#### agent create
Create a new data agent.

```bash
rdst agent create --name my-agent --target mydb
```

#### agent list
List all data agents.

```bash
rdst agent list
```

#### agent show
Show details of a data agent.

```bash
rdst agent show --name my-agent
```

#### agent delete
Delete a data agent.

```bash
rdst agent delete --name my-agent
```

#### agent chat
Start an interactive chat session with a data agent.

```bash
rdst agent chat --name my-agent
rdst agent chat --name my-agent --target mydb
```

The agent uses AI to answer questions about your data, guided by its
configured safety policies (guards).

### rdst web
Start the RDST web UI and API server.

```bash
# Start server on default port (8787)
rdst web

# Specify host and port
rdst web --host 0.0.0.0 --port 9000

# Clear persisted secure env vars from keyring
rdst web --clear
```

The web server provides a browser-based interface and REST API for all RDST
functionality. Requires `pip install rdst[server]` for server dependencies.

### rdst slack
Manage Slack integrations for data agents.

```bash
# List configured Slack agents
rdst slack list

# Connect a data agent to Slack
rdst slack connect --agent my-agent
```

The Slack integration lets data agents answer database questions directly from
Slack channels and DMs.

### rdst claude
Register or remove RDST as a Claude Code MCP server.

```bash
# Register RDST with Claude Code
rdst claude add

# Remove RDST from Claude Code
rdst claude remove
```

After registering, start a new Claude Code session and type `/rdst` to activate
RDST mode. Claude will have access to all RDST tools for query analysis and
optimization.

## Password Handling
RDST never stores passwords in config files. Each target has a
`password_env` field specifying a key name used to look up the
password. RDST checks these sources in order:

1. **Environment variable** — `export PROD_DB_PASS="..."`
2. **OS keyring** — stored via `rdst fleet configure` or manually
3. **AWS Secrets Manager** — if `password_secret_arn` is set

```bash
# Config shows: password_env = "PROD_DB_PASSWORD"

# Option 1: Environment variable (works everywhere)
export PROD_DB_PASSWORD="your-actual-password"

# Option 2: OS keyring (persists across sessions, no export needed)
# Set during fleet configure, or manually:
python3 -c "import keyring; keyring.set_password('rdst', 'PROD_DB_PASSWORD', 'your-password')"
```

The keyring option works on macOS (Keychain), Linux (GNOME Keyring),
and Windows (Credential Manager). On headless servers or containers
without a keyring backend, use environment variables instead.

## Common Workflows

### Optimizing a Slow Query
1. Identify slow query with `rdst top --target mydb`
2. Copy the query and run `rdst analyze -q "..." --target mydb`
3. Review index recommendations
4. Create suggested indexes
5. Re-run analysis to verify improvement

### Testing ReadySet Caching
1. Run `rdst analyze -q "..." --target mydb` (ReadySet tested automatically if Docker available)
2. Review the ReadySet Performance section in the output
3. For a full benchmark: `rdst query cache-compare <query> --target mydb --count 100`
4. If cacheable, deploy permanently: `rdst cache deploy --target mydb --mode docker`

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
- Export ANTHROPIC_API_KEY environment variable
- Or run `rdst init` to configure your API key interactively

## Docker Requirements (ReadySet Performance)

`rdst analyze` automatically tests ReadySet caching in parallel when Docker is available.
`rdst query cache-compare` and `rdst cache deploy` also use Docker.

**Prerequisites:**
- Docker must be installed and running
- User must have permission to run Docker commands
- First run downloads container image (~500MB)

**What happens automatically:**
1. RDST starts a ReadySet container that connects directly to your upstream database
2. Uses shallow caching mode (10-minute TTL) - no data replication required
3. Attempts to cache the query and measures performance
4. Reports cacheability status and cached query latency
5. Container is kept running for subsequent use

**If Docker is not available:** ReadySet performance testing is silently skipped. All other analysis runs normally.

**Resource usage:**
- Memory: ~500MB-1GB for ReadySet container
- Disk: ~500MB for image (first run)
- CPU: Moderate during cacheability testing

**Cleanup:**
Container remains running after tests. To stop it:
```bash
docker stop rdst-readyset-<target>
docker rm rdst-readyset-<target>
```

**Important:** The first run may take 30-60 seconds while the image downloads.
Subsequent runs are faster (5-10 seconds).

## Troubleshooting

### ReadySet cache errors
- Docker not found: Install Docker and ensure daemon is running
- If a query can't be cached, ReadySet will explain why in the output

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
            # Use keyword-based fallback when no API key available
            result = self._fallback_search(question, "no_api_key")
            result.answer += (
                "\n\n---\n"
                "Note: For more detailed AI-powered help, run `rdst init` to get a free API key."
            )
            return result

        try:
            from shared.llm_manager import LLMManager

            # Use Haiku for fast, cheap responses
            from shared.llm_manager.claude_provider import AnthropicModel
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
- --interactive: Continue analysis conversation

ReadySet cache performance is tested automatically when Docker is available.

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
            answer = """ReadySet shallow caching:

**Automatic testing during analyze:**
```bash
rdst analyze -q "YOUR QUERY" --target mydb
# ReadySet performance is tested automatically if Docker is available
```

**Compare performance (upstream vs cache):**
```bash
rdst query cache-compare <query> --target mydb --count 100
# Auto-deploys cache if needed, shows side-by-side comparison
```

**Manual cache management:**
```bash
# 1. Deploy ReadySet (auto-registers mydb-cache target)
rdst cache deploy --target mydb --mode docker

# 2. Cache queries
rdst cache add "SELECT * FROM orders WHERE id = 1" --target mydb-cache

# 3. View caches and connection string
rdst cache show --target mydb-cache

# 4. Remove caches
rdst cache delete <cache_name> --target mydb-cache
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

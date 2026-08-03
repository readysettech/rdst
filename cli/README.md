# RDST - Readyset Data and SQL Toolkit

A command-line tool for database diagnostics, query analysis, performance tuning, and caching optimization with Readyset.

## What is RDST?

RDST helps you:
- Analyze SQL queries for caching opportunities
- Identify slow queries in real-time
- Get optimization suggestions
- Evaluate query compatibility with Readyset cache
- Manage database connection profiles

## Installation

Install RDST on macOS or Linux without sudo, pip, or a preinstalled Python:

```bash
curl -fsSL https://downloads.readyset.io/packages/rdst-cli/install.sh | sh
```

Open a new shell, then verify the installation:

```bash
rdst version
```

The installer supports Apple silicon, Intel macOS, and glibc-based x86_64 or
arm64 Linux distributions. To install an exact package release:

```bash
curl -fsSL https://downloads.readyset.io/packages/rdst-cli/install.sh | \
  sh -s -- --version 0.1.1589
```

Immutable release scripts and checksums are published under
`packages/rdst-cli/versions/VERSION/` on the download origin.

Installer-managed RDST never updates silently. Check for and install updates explicitly:

```bash
rdst update --check
rdst update
```

`rdst update` installs the exact resolved release and makes no changes when the
installed version is already current.

To uninstall the managed runtime while preserving `~/.rdst` configuration:

```bash
curl -fsSL https://downloads.readyset.io/packages/rdst-cli/install.sh | \
  sh -s -- --uninstall
```

Existing uv and pipx users can continue to use `uv tool install rdst` or
`pipx install rdst`; those installations remain managed by their package manager.
Update them with `uv tool upgrade rdst` or `pipx upgrade rdst`.

> **After installing**, run `rdst init` to configure your first database connection.

### Native Windows development

Use PowerShell with Python 3.12+ and uv:

```powershell
cd rdst
uv sync --group dev
uv run rdst version
$env:PROD_DB_PASSWORD = "your-password"
```

RDST stores user data under `%USERPROFILE%\.rdst`. Interactive editor commands
use `EDITOR` or `VISUAL` and fall back to Notepad.

When the Docker CLI talks to a daemon on another machine, configure the daemon
network explicitly:

```powershell
$env:RDST_DOCKER_REMOTE = "1"
$env:RDST_DOCKER_PUBLISHED_HOST = "192.168.122.1"
$env:RDST_DOCKER_UPSTREAM_HOST = "192.168.122.222"
```

`RDST_DOCKER_PUBLISHED_HOST` is where Windows reaches published container ports.
`RDST_DOCKER_UPSTREAM_HOST` is where containers reach a database running on the
Windows client. The database must listen on that routable address.

## Quick Start

1. **Initialize RDST:**
   ```bash
   rdst init
   # Or with uvx (no installation needed):
   uvx rdst init
   ```

2. **Configure database connection:**
   ```bash
   rdst configure add-target mydb \
     --host localhost \
     --port 5432 \
     --database myapp \
     --user postgres
   ```

3. **Analyze queries:**
   ```bash
   # Analyze a specific query
   rdst analyze "SELECT * FROM users WHERE active = true"

   # With uvx:
   uvx rdst analyze "SELECT * FROM users WHERE active = true"

   # Analyze with Readyset cache evaluation
   rdst analyze --readyset-cache "SELECT * FROM products ORDER BY created_at"
   ```

4. **Monitor slow queries:**
   ```bash
   rdst top
   # Or: uvx rdst top
   ```

5. **Audit your fleet:**
   ```bash
   # Snapshot all ReadySet clusters and audit their health
   rdst fleet audit --target prod

   # Import clusters from your infrastructure
   rdst fleet import --target prod
   ```

   Fleet audit checks cache utilization, query support, and configuration
   across your ReadySet deployments. See the
   [fleet audit docs](https://readyset.io/docs/readyset-ai/rdst/fleet-and-audit/fleet-audit)
   for more.

## Commands

All commands can be run with `rdst` (if installed) or `uvx rdst` (no installation):

- `rdst analyze` - Analyze SQL queries and evaluate caching opportunities
- `rdst top` - Live view of slow queries
- `rdst ask` - Natural language to SQL queries
- `rdst scan` - Scan codebases for ORM queries and analyze performance
- `rdst fleet audit` - Audit ReadySet cluster health and cache utilization
- `rdst fleet import` - Import ReadySet clusters from your infrastructure
- `rdst schema` - Manage the semantic layer for better query generation
- `rdst query` - Manage query registry
- `rdst configure` - Manage database targets and connection profiles
- `rdst init` - First-time setup wizard
- `rdst version` - Show version information

**Example with uvx:**
```bash
uvx rdst analyze "SELECT * FROM orders WHERE status = 'pending'"
```

## Requirements

- Packaged CLI: macOS or a glibc-based Linux distribution on x86_64 or arm64
- Native Windows development: Python 3.12+, uv, and PowerShell
- `curl` or `wget` for the macOS/Linux installer
- PostgreSQL or MySQL database access

## About Readyset

Readyset is a SQL caching engine that sits between your application and database, automatically caching query results to improve performance. Learn more at [readyset.io](https://readyset.io).

## Documentation

- [Readyset Documentation](https://docs.readyset.io)
- [GitHub Repository](https://github.com/readysettech/readyset)
- [Report Issues](https://github.com/readysettech/readyset/issues)

## License

MIT License - see LICENSE file for details

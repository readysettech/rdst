# RDST - ReadySet Diagnostics & SQL Tuning

A command-line tool for database diagnostics, query analysis, performance tuning, and caching optimization with ReadySet.

## What is RDST?

RDST helps you:
- Analyze SQL queries for caching opportunities
- Identify slow queries in real-time
- Get optimization suggestions
- Evaluate query compatibility with ReadySet cache
- Manage database connection profiles

## Installation

### Using uvx (recommended - no installation required)

Run RDST directly without installing:

```bash
uvx rdst --help
uvx rdst analyze "SELECT * FROM users WHERE id = 1"
```

### Using pipx (persistent installation)

Install globally:

```bash
# Install
pipx install rdst

# Run
rdst --help

# Upgrade to latest version
pipx upgrade rdst
```

### Using pip

```bash
pip install rdst
```

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

   # Analyze with ReadySet cache evaluation
   rdst analyze --readyset-cache "SELECT * FROM products ORDER BY created_at"
   ```

4. **Monitor slow queries:**
   ```bash
   rdst top
   # Or: uvx rdst top
   ```

## Commands

All commands can be run with `rdst` (if installed) or `uvx rdst` (no installation):

- `rdst configure` - Manage database targets and connection profiles
- `rdst analyze` - Analyze SQL queries and evaluate caching opportunities
- `rdst top` - Live view of slow queries
- `rdst tune` - Get query optimization suggestions
- `rdst query` - Manage query registry
- `rdst init` - First-time setup wizard
- `rdst version` - Show version information

**Example with uvx:**
```bash
uvx rdst analyze "SELECT * FROM orders WHERE status = 'pending'"
```

## Requirements

- Python 3.11 or higher
- PostgreSQL or MySQL database access

## About ReadySet

ReadySet is a SQL caching engine that sits between your application and database, automatically caching query results to improve performance. Learn more at [readyset.io](https://readyset.io).

## Documentation

- [ReadySet Documentation](https://docs.readyset.io)
- [GitHub Repository](https://github.com/readysettech/readyset)
- [Report Issues](https://github.com/readysettech/readyset/issues)

## License

MIT License - see LICENSE file for details

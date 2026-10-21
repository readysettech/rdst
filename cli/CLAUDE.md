# RDST - Readyset Data and SQL Toolkit

## Quick Reference

**Root directory**: `readyset/rdst/` (NOT `cloud/cloud_agent/` - that's deprecated)

**Entry point**: `rdst.py` - main CLI

**Architecture**: See [ARCHITECTURE.md](ARCHITECTURE.md) for the current `features/` + `shared/` ownership model.

## Running RDST

### Local Development (Recommended)

```bash
# Run directly from source - no installation needed
python3 rdst.py <command>

# Or with uv (if using uv for dependency management)
uv run rdst.py <command>
```

### Installation Options

```bash
# Using uv (recommended - doesn't pollute system Python)
uv pip install -e .

# Using pip (installs to system/virtualenv)
pip install -e .

# The -e flag means "editable" - changes to source are reflected immediately
```

After installation, you can run `rdst <command>` directly instead of `python3 rdst.py <command>`.

## Key Commands

| Command                  | Description                          |
| ------------------------ | ------------------------------------ |
| `rdst init`              | First-time setup wizard              |
| `rdst configure`         | Manage database targets              |
| `rdst analyze -q "SQL"`  | Analyze query performance            |
| `rdst top --target X`    | Monitor slow queries                 |
| `rdst ask "question"`    | Natural language to SQL              |
| `rdst schema show`       | View/manage semantic layer           |
| `rdst query list`        | View saved queries                   |
| `rdst query run <names>` | Benchmark/load test queries          |
| `rdst help "question"`   | Documentation lookup                 |
| `rdst claude add`        | Register MCP server with Claude Code |

## Configuration

- Config file: `~/.rdst/config.toml`
- Query registry: `~/.rdst/queries.toml`
- Passwords: Never stored - use `password_env` to reference environment variables

## Testing

```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/ask_experimental/test_ask3_engine/test_engine.py -v

# Quick validation
python3 rdst.py version
python3 rdst.py --help
```

## Development Guidelines

### Adding New Features

1. **Add the feature behavior** under `features/<name>/`
2. **Wire CLI/parser changes** through `rdst.py`, `shared/cli/rdst_cli.py`, and the feature's `cli/command.py`
3. **Add tests** - look at existing patterns in `tests/`
4. **Update help docs** if user-facing - see `shared/cli/help_command.py` (RDST_DOCS constant)
5. **Update MCP tools** if should be exposed to Claude - see `mcp_server.py`

### Code Patterns

- **Temperature 0.0** for LLM calls (deterministic output)
- **Lazy imports** in rdst_cli.py to minimize startup time
- **RdstResult** dataclass for all command returns
- **TargetsConfig** class for config file access

### Domain-Specific Guidelines

- **UI Components**: Use `shared.ui` and the Rich import rule below
- **Ask3 Engine**: See `features/ask/engine/ask3/` for the NL-to-SQL engine
- **Devtools**: See `devtools/AGENTS.md` for storybook rendering
- **Web Client**: See `web-apps/apps/rdst/AGENTS.md` for React frontend patterns

### Rich Component Imports (CRITICAL)

**Never import Rich components directly.** Always import from `shared.ui`:

```python
# WRONG
from rich.console import Group
from rich.text import Text

# CORRECT
from shared.ui import Group, Text, Tree, Spinner, Live
```

If you need a Rich component not yet exported, add it to `shared/ui/components.py` and `shared/ui/__init__.py`.

### Critical Files to Preserve

These files contain important improvements - be careful when merging:

- `features/analyze/functions/llm_analysis.py` - Anti-pattern rules, SELECT \* column variance fix
- `features/analyze/functions/explain_analysis.py` - Interactive skip mechanism
- `features/analyze/cli/command.py` - Analyze UX improvements

## Natural Language to SQL (rdst ask)

Convert natural language questions into SQL queries:

```bash
# Basic usage
rdst ask "Show me top 10 orders by price" --target tpch

# Dry run - generate SQL without executing
rdst ask "Count customers by market segment" --target tpch --dry-run

# Agent mode for complex queries
rdst ask "Which suppliers have the most orders?" --target tpch
```

**How it works**:

1. Loads database schema (from semantic layer if available, otherwise introspects DB)
2. LLM generates SQL based on question and schema context
3. Validates SQL (read-only, columns exist, has LIMIT)
4. Executes and displays results

**Requires**: `ANTHROPIC_API_KEY` environment variable

**Best experience**: Run interactively in terminal (not via MCP)

## Semantic Layer (rdst schema)

The semantic layer stores metadata about your database to improve `rdst ask` results:

```bash
# Initialize from database (introspects tables, columns, detects enums)
rdst schema init --target tpch

# View semantic layer
rdst schema show --target tpch
rdst schema show --target tpch customer   # Specific table

# AI-generate descriptions (requires ANTHROPIC_API_KEY)
rdst schema annotate --target tpch --use-llm

# Manual annotation wizard
rdst schema annotate --target tpch

# Edit in $EDITOR
rdst schema edit --target tpch

# Export/delete
rdst schema export --target tpch --format yaml
rdst schema delete --target tpch
```

**Storage**: `~/.rdst/semantic-layer/<target>.yaml`

**What it stores**:

- Table descriptions and row estimates
- Column descriptions and types
- Enum values with meanings (e.g., `AUTOMOBILE` = "Automotive industry customers")
- Business terminology definitions
- Foreign key relationships

**Workflow**:

1. `rdst schema init` - Bootstrap from database
2. `rdst schema annotate --use-llm` - AI fills in descriptions
3. `rdst schema edit` - Manual tweaks if needed
4. `rdst ask` - Now generates better SQL with context

## Testing Ask/Schema

```bash
# Run tests
pytest tests/ask_experimental/ -v

# Manual testing guide
cat tests/ask_experimental/MANUAL_TEST_CASES.md

# Quick validation (requires TPC-H database)
export TPCH_PASSWORD=tpchtest
rdst ask "How many customers?" --target tpch --no-interactive
rdst schema show --target tpch
```

## Environment Variables

```bash
# Required for database connections
export <TARGET>_PASSWORD="..."   # As configured in password_env

# LLM providers (one required)
export ANTHROPIC_API_KEY="..."   # For Claude
export OPENAI_API_KEY="..."      # For OpenAI
```

## Common Issues

| Issue                   | Solution                         |
| ----------------------- | -------------------------------- |
| "Authentication failed" | Check password_env is exported   |
| Import errors           | Run from `rdst/` directory       |
| LLM timeout             | Check API key, try `--fast` flag |
| Test failures           | Ensure test DB is accessible     |

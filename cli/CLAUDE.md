# RDST - ReadySet Diagnostics & SQL Tuning

## Quick Reference

**Root directory**: `readyset/rdst/` (NOT `cloud/cloud_agent/` - that's deprecated)

**Entry point**: `rdst.py` - main CLI

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

## Project Structure

```
rdst/
├── rdst.py                 # CLI entry point, argparse setup
├── mcp_server.py           # MCP server for Claude Code integration
├── lib/
│   ├── cli/                # Command implementations
│   │   ├── rdst_cli.py     # RdstCLI class with all command methods
│   │   ├── analyze_command.py   # Main analyze logic (~2400 lines)
│   │   ├── top.py          # Top slow queries command
│   │   ├── configuration_wizard.py  # Target setup wizard
│   │   ├── howdoi_command.py    # Documentation lookup
│   │   └── query_command.py     # Query registry management
│   ├── functions/          # Core business logic
│   │   ├── llm_analysis.py      # LLM prompts & analysis (~950 lines)
│   │   ├── explain_analysis.py  # EXPLAIN ANALYZE execution
│   │   ├── schema_collector.py  # DB schema introspection
│   │   └── rewrite_testing.py   # Query rewrite benchmarking
│   ├── llm_manager/        # LLM provider abstraction
│   ├── engines/            # [NOT RELEASED] Ask3 text-to-SQL engine
│   ├── semantic_layer/     # [NOT RELEASED] Schema semantic context
│   └── prompts/            # LLM prompt templates
├── test/                   # Test cases
└── devtools/               # Development utilities
```

## Key Commands

| Command | Description |
|---------|-------------|
| `rdst init` | First-time setup wizard |
| `rdst configure` | Manage database targets |
| `rdst analyze -q "SQL"` | Analyze query performance |
| `rdst top --target X` | Monitor slow queries |
| `rdst query list` | View saved queries |
| `rdst howdoi "question"` | Documentation lookup |
| `rdst claude add` | Register MCP server with Claude Code |

## Configuration

- Config file: `~/.rdst/config.toml`
- Query registry: `~/.rdst/queries.toml`
- Passwords: Never stored - use `password_env` to reference environment variables

## Testing

```bash
# Run all tests
pytest test/

# Run specific test file
pytest test/test_ask3_engine/test_engine.py -v

# Quick validation
python3 rdst.py version
python3 rdst.py --help
```

## Development Guidelines

### Adding New Features

1. **Add CLI command** in `rdst.py` (argparse) and `lib/cli/rdst_cli.py` (method)
2. **Add tests** - look at existing patterns in `test/`
3. **Update howdoi docs** if user-facing - see `lib/cli/howdoi_command.py`
4. **Update MCP tools** if should be exposed to Claude - see `mcp_server.py`

### Code Patterns

- **Temperature 0.0** for LLM calls (deterministic output)
- **Lazy imports** in rdst_cli.py to minimize startup time
- **RdstResult** dataclass for all command returns
- **TargetsConfig** class for config file access

### Critical Files to Preserve

These files contain important improvements - be careful when merging:

- `lib/functions/llm_analysis.py` - Anti-pattern rules, SELECT * column variance fix
- `lib/functions/explain_analysis.py` - Interactive skip mechanism
- `lib/cli/analyze_command.py` - All UX improvements

## Experimental Features (RDST_EXPERIMENTAL=1)

Hidden features accessible via environment variable:

```bash
export RDST_EXPERIMENTAL=1

# Natural language to SQL
rdst ask "Show me top 10 orders by price" --target tpch

# Semantic layer management
rdst schema init --target tpch    # Initialize from database
rdst schema show --target tpch    # Display semantic layer
rdst schema edit --target tpch    # Edit in $EDITOR
rdst schema annotate --target tpch customer  # Add descriptions
```

**Note**: `rdst ask` requires an LLM API key (ANTHROPIC_API_KEY).
`rdst schema` commands work without LLM.

### Testing Experimental Features

```bash
# Run experimental tests (skipped by default)
pytest tests/ask_experimental/ -v

# Manual testing guide
cat tests/ask_experimental/MANUAL_TEST_CASES.md
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

| Issue | Solution |
|-------|----------|
| "Authentication failed" | Check password_env is exported |
| Import errors | Run from `rdst/` directory |
| LLM timeout | Check API key, try `--fast` flag |
| Test failures | Ensure test DB is accessible |

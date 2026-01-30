# RDST - Python CLI & API

**Stack**: Python 3.11+, Typer, Rich, FastAPI

## STRUCTURE

```
rdst/
├── rdst.py              # CLI entry point (argparse)
├── mcp_server.py        # MCP server for Claude Code
├── lib/
│   ├── cli/             # Command implementations
│   ├── api/             # FastAPI backend for web client
│   ├── functions/       # Core business logic (shared by CLI + API)
│   ├── engines/ask3/    # NL-to-SQL engine (see ask3/AGENTS.md)
│   ├── ui/              # Rich component wrappers (see ui/AGENTS.md)
│   └── prompts/         # LLM prompt templates
└── test/                # Tests
```

## COMMANDS

```bash
python3 rdst.py <command>    # Direct run
uv run rdst.py <command>     # With uv
rdst <command>               # After pip install -e .
```

## CONVENTIONS

### Rich Component Imports

**NEVER import Rich directly. Always use lib.ui:**

```python
# NO
from rich.console import Group
from rich.text import Text

# YES
from lib.ui import Group, Text, Tree, Spinner, Live
```

If you need a component not yet exported, add it to `lib/ui/components.py` and `lib/ui/__init__.py`.

### Shared Core Logic

CLI and Web API share the same functions - NO duplication:

```python
# lib/functions/ is the source of truth
from lib.functions.llm_analysis import analyze_with_llm
from lib.functions.explain_analysis import execute_explain_analyze

# Both CLI and API call these
```

### Password Handling

Targets use `password_env` (env var name), not direct password storage:

```python
def _check_password(target_config: dict) -> bool:
    password = target_config.get("password")
    password_env = target_config.get("password_env")
    
    if password:
        return True
    if password_env and os.environ.get(password_env):
        return True
    return False
```

### API Security

Status endpoint must NOT expose sensitive connection details:

```python
class TargetInfo(BaseModel):
    name: str
    has_password: bool
    is_default: bool
    # NO: host, port, database, password
```

## ANTI-PATTERNS

- **NO direct Rich imports** - Use `lib.ui` wrappers
- **NO duplicate logic** - CLI and API share `lib/functions/`
- **NO password storage** - Use `password_env` references
- **NO sensitive data in API responses** - No connection strings

## WHERE TO LOOK

| Task | Location |
|------|----------|
| Add CLI command | `rdst.py` + `lib/cli/rdst_cli.py` |
| Add API endpoint | `lib/api/routes/` |
| Core logic | `lib/functions/` |
| NL-to-SQL | `lib/engines/ask3/` (see AGENTS.md) |
| CLI UI components | `lib/ui/` (see AGENTS.md) |
| LLM prompts | `lib/prompts/` |

## WEB CLIENT

The React frontend is at `web-apps/apps/rdst/` - see that directory's AGENTS.md for web UI patterns.

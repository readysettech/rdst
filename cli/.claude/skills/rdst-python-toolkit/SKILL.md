---
name: rdst-python-toolkit
description: This skill should be used when working with the RDST Python CLI and library for database diagnostics and query optimization. It applies when implementing CLI commands, adding API endpoints, creating UI components, working with services, or modifying LLM prompts. Triggers on work in rdst/, imports from lib.ui, lib.services, lib.functions, or mentions of rdst, Rich UI components, or Readyset toolkit.
---

# RDST - Readyset Data and SQL Toolkit

Python CLI & API for database diagnostics, query optimization, and NL-to-SQL.

**Stack**: Python 3.11+, Rich (terminal UI), FastAPI (web API), Typer (CLI)

## Project Structure

```
rdst/
├── rdst.py              # CLI entry point (argparse)
├── mcp_server.py        # MCP server for Claude Code integration
├── lib/
│   ├── cli/             # Command implementations
│   ├── api/             # FastAPI routes
│   ├── services/        # Event-driven async services
│   ├── functions/       # Core business logic (shared)
│   ├── ui/              # Rich terminal components
│   ├── llm_manager/     # LLM provider abstraction
│   ├── engines/ask3/    # NL-to-SQL engine
│   └── prompts/         # LLM prompt templates
└── tests/               # Test cases
```

## Running RDST

```bash
python3 rdst.py <command>    # Direct run
uv run rdst.py <command>     # With uv
rdst <command>               # After pip install -e .
```

## Key Commands

| Command | Description |
|---------|-------------|
| `rdst init` | First-time setup wizard |
| `rdst configure` | Manage database targets |
| `rdst analyze -q "SQL"` | Analyze query performance |
| `rdst top --target X` | Monitor slow queries |
| `rdst ask "question"` | Natural language to SQL |
| `rdst schema show` | View semantic layer |
| `rdst query list` | View saved queries |

---

## CRITICAL: Rich Component Imports

**NEVER import Rich directly. Always use lib.ui:**

```python
# WRONG - direct Rich imports
from rich.console import Group
from rich.text import Text
from rich.tree import Tree
from rich.panel import Panel

# CORRECT - import from lib.ui
from lib.ui import Group, Text, Tree, Spinner, Live
from lib.ui import StyledPanel, StyledTable, MessagePanel
from lib.ui import get_console, StyleTokens, Icons
```

If a Rich component is needed but not exported, add it to `lib/ui/components.py` and `lib/ui/__init__.py`.

---

## UI Design System (lib/ui/)

### Atomic Design Hierarchy

| Layer | Purpose | Examples |
|-------|---------|----------|
| **Atoms** | Base building blocks | `StyledPanel`, `StyledTable` |
| **Molecules** | Composable units | `MessagePanel`, `DataTable`, `StatusLine` |
| **Organisms** | Domain-specific | `QueryPanel`, `TopQueryTable`, `AnalysisHeader` |

### Console & Printing

```python
from lib.ui import get_console, print_success, print_error, print_warning

console = get_console()
console.print(MessagePanel("Query saved!", variant="success"))

# Helper functions
print_success("Operation completed")
print_error("Something failed")
print_warning("This might take a while")
```

### Style Tokens

```python
from lib.ui import StyleTokens, Icons

# Colors
console.print(f"[{StyleTokens.SUCCESS}]Done![/{StyleTokens.SUCCESS}]")
console.print(f"[{StyleTokens.ERROR}]Failed[/{StyleTokens.ERROR}]")
console.print(f"[{StyleTokens.WARNING}]Warning[/{StyleTokens.WARNING}]")
console.print(f"[{StyleTokens.MUTED}]Additional info[/{StyleTokens.MUTED}]")

# Icons
print(f"{Icons.SUCCESS} Success")
print(f"{Icons.ERROR} Error")
print(f"{Icons.WARNING} Warning")
```

### Common Components

```python
from lib.ui import (
    MessagePanel, NoticePanel, QueryPanel,
    DataTable, KeyValueTable, StatusLine,
    StyledPanel, StyledTable,
    SectionHeader, Banner, Rule,
    EmptyState, NextSteps,
)

# Message panels
MessagePanel("Saved!", variant="success")
MessagePanel("Failed to connect", variant="error", hint="Check password")

# Notice with bullets and actions
NoticePanel(
    title="REWRITE TESTING SKIPPED",
    description="Query contains parameter placeholders",
    variant="warning",
    bullets=["Query was captured from rdst top"],
    action_command='rdst analyze --query "SELECT ..."'
)

# Data tables
DataTable(
    columns=["Name", "Value"],
    rows=[("key1", "val1"), ("key2", "val2")],
    title="Results"
)

# SQL display
QueryPanel(sql, title="Generated SQL")

# Status
StatusLine("Duration", "1.2ms")
```

---

## Services Architecture (lib/services/)

Services provide a unified interface for CLI and Web API. Each service:
- Yields typed events via async generators
- Is stateless (no callbacks or injected dependencies)
- Separates concerns: service = logic, renderer = output

### Service Pattern

```python
from lib.services import AskService, AskInput, AskOptions

service = AskService()

async for event in service.ask(input, options):
    if isinstance(event, AskClarificationNeededEvent):
        # Pause - collect user input
        answers = get_answers(event.questions)
        async for resume_event in service.resume(event.session_id, answers):
            handle(resume_event)
    else:
        handle(event)
```

### Available Services

| Service | Purpose | Pattern |
|---------|---------|---------|
| `AskService` | NL-to-SQL | Pause/Resume (clarification) |
| `AnalyzeService` | Query analysis | One-shot |
| `TopService` | Query monitoring | One-shot or Streaming |
| `InteractiveService` | Follow-up chat | Streaming chunks |

### Event Types

Events are dataclasses with discriminated unions:

```python
from dataclasses import dataclass
from typing import Literal, Union

@dataclass
class AskStatusEvent:
    type: Literal["status"]
    phase: str
    message: str

@dataclass
class AskResultEvent:
    type: Literal["result"]
    sql: str
    rows: list

AskEvent = Union[AskStatusEvent, AskResultEvent, AskErrorEvent, ...]
```

### CLI Integration

```python
def my_command(self, ...) -> RdstResult:
    from lib.services import MyService, MyInput
    from lib.cli.my_renderer import MyRenderer

    service = MyService()
    renderer = MyRenderer(verbose=verbose)

    async def _run():
        async for event in service.do_thing(input):
            renderer.render(event)
            if isinstance(event, ResultEvent):
                return event
        return None

    result = asyncio.run(_run())
    return RdstResult(ok=True, data=result)
```

### Web API Integration

```python
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

router = APIRouter()

async def _generator(request):
    service = MyService()
    async for event in service.do_thing(...):
        yield {"event": event.type, "data": json.dumps(asdict(event))}

@router.post("/my-endpoint")
async def endpoint(request: MyRequest):
    return EventSourceResponse(_generator(request))
```

---

## Shared Functions (lib/functions/)

CLI and API share the same core logic - NO duplication:

```python
# lib/functions/ is the source of truth
from lib.functions.llm_analysis import analyze_with_llm
from lib.functions.explain_analysis import execute_explain_analyze
from lib.functions.schema_collector import collect_schema
from lib.functions.query_safety import validate_query_safety
```

---

## Password Handling

Targets use `password_env` (environment variable name), not direct password storage:

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

**Config file**: `~/.rdst/config.toml`

---

## LLM Integration

```python
from lib.llm_manager import LLMManager

# Always use temperature 0.0 for deterministic output
manager = LLMManager()
response = await manager.complete(
    prompt="...",
    temperature=0.0,
    model="claude-sonnet-4-20250514"
)
```

---

## Adding New Features

### New CLI Command

1. Add argparse in `rdst.py`
2. Implement method in `lib/cli/rdst_cli.py`
3. Add tests in `tests/`
4. Update help docs in `lib/cli/help_command.py` (RDST_DOCS constant)
5. Update MCP tools in `mcp_server.py` if needed

### New API Endpoint

1. Create route in `lib/api/routes/`
2. Define request/response models in `lib/api/models.py`
3. Register in `lib/api/app.py`

### New Service

1. Define types in `lib/services/types.py`
2. Create `lib/services/my_service.py`
3. Create renderer `lib/cli/my_renderer.py`
4. Export from `lib/services/__init__.py`

### New UI Component

1. Add to `lib/ui/components.py`
2. Export from `lib/ui/__init__.py`
3. Follow atomic design (atom → molecule → organism)

---

## Anti-Patterns

| Avoid | Instead |
|-------|---------|
| `from rich.text import Text` | `from lib.ui import Text` |
| `from rich.panel import Panel` | `from lib.ui import StyledPanel` |
| Duplicate logic in CLI and API | Share via `lib/functions/` |
| Storing passwords directly | Use `password_env` references |
| Sensitive data in API responses | Expose only safe fields |
| Heavy imports at module load | Use lazy imports |
| LLM temperature > 0 | Use `temperature=0.0` |

---

## Testing

```bash
pytest tests/                              # All tests
pytest tests/test_ask3_engine/ -v          # Specific module
python3 rdst.py version                    # Quick validation
```

---

## File Locations

| Task | Location |
|------|----------|
| Add CLI command | `rdst.py` + `lib/cli/rdst_cli.py` |
| Add API endpoint | `lib/api/routes/` |
| Core business logic | `lib/functions/` |
| NL-to-SQL engine | `lib/engines/ask3/` |
| CLI UI components | `lib/ui/` |
| LLM prompts | `lib/prompts/` |
| Services | `lib/services/` |
| Tests | `tests/` |

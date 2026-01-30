# Services Layer Architecture

This document describes the event-driven service architecture used by RDST commands.

## Overview

The services layer provides a unified interface for both CLI and Web consumers. Each service:
- Yields typed events during execution via async generators
- Is stateless (no callbacks or injected dependencies)
- Separates concerns: service = logic, renderer = output, input handler = user interaction

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CONSUMERS                                    │
├─────────────────────────────────────────────────────────────────────┤
│  CLI                              │  Web API                         │
│  - Uses Renderer for output       │  - Yields SSE events             │
│  - Uses InputHandler for prompts  │  - Returns JSON to client        │
│  - asyncio.run() bridge           │  - Native async                  │
└─────────────────────────────────────────────────────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    │      SERVICE          │
                    │  async def method()   │
                    │    -> AsyncGenerator  │
                    │       [Event, None]   │
                    └───────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    │   TYPED EVENTS        │
                    │  @dataclass           │
                    │  type: Literal["..."] │
                    └───────────────────────┘
```

## Services

### AskService (`ask_service.py`)

Text-to-SQL conversion with clarification support.

**Key Pattern**: Pause/Resume for user interaction

```python
from lib.services import AskService, AskInput, AskOptions

service = AskService()

async for event in service.ask(input, options):
    if isinstance(event, AskClarificationNeededEvent):
        # Execution pauses here - collect user input
        answers = get_answers(event.questions)
        # Resume with answers
        async for resume_event in service.resume(event.session_id, answers):
            handle(resume_event)
    else:
        handle(event)
```

**Events**:
| Event | Description |
|-------|-------------|
| `AskStatusEvent` | Progress update (phase, message) |
| `AskSchemaLoadedEvent` | Schema loaded (source, table_count) |
| `AskClarificationNeededEvent` | User input needed - **execution pauses** |
| `AskSqlGeneratedEvent` | SQL generated (sql, explanation) |
| `AskResultEvent` | Query executed (rows, columns, timing) |
| `AskErrorEvent` | Error occurred (message, phase) |

**Clarification Flow**:
```
ask() ──► schema ──► clarify ──► [PAUSE]
                                    │
                     user answers ◄─┘
                                    │
resume() ──► generate ──► validate ──► execute ──► result
```

### AnalyzeService (`analyze_service.py`)

Query performance analysis with EXPLAIN ANALYZE, LLM insights, and optional Readyset testing.

**Key Pattern**: One-shot execution (no pause/resume)

```python
from lib.services import AnalyzeService, AnalyzeInput, AnalyzeOptions

service = AnalyzeService()

async for event in service.analyze(input, options):
    handle(event)  # No pause - runs to completion
```

**Events**:
| Event | Description |
|-------|-------------|
| `ProgressEvent` | Progress update (stage, percent, message) |
| `ExplainCompleteEvent` | EXPLAIN finished (timing, rows, cost) |
| `RewritesTestedEvent` | Query rewrites tested |
| `ReadysetCheckedEvent` | Readyset cacheability checked |
| `CompleteEvent` | Analysis complete (all results) |
| `ErrorEvent` | Error occurred |

### TopService (`top_service.py`)

Real-time and historical query monitoring from database telemetry.

**Key Pattern**: Two modes - one-shot and streaming

```python
from lib.services import TopService, TopInput, TopOptions

service = TopService()

# Historical one-shot (pg_stat_statements/performance_schema)
async for event in service.get_top_queries(input, options):
    handle(event)

# Real-time streaming (pg_stat_activity with 200ms polling)
async for event in service.stream_realtime(input, options, duration=30):
    handle(event)  # Repeating TopQueriesEvent until duration expires
```

**Events**:
| Event | Description |
|-------|-------------|
| `TopStatusEvent` | Progress update (message) |
| `TopConnectedEvent` | Connected (target, engine, source) |
| `TopSourceFallbackEvent` | Source fallback (pg_stat → activity) |
| `TopQueriesEvent` | Query batch (queries, runtime_seconds) |
| `TopQuerySavedEvent` | Query saved to registry (hash, is_new) |
| `TopCompleteEvent` | Complete (queries, newly_saved) |
| `TopErrorEvent` | Error occurred |

**Real-time Flow**:
```
stream_realtime() ──► connect ──► [poll loop]
                                      │
                         TopQueriesEvent (every 200ms)
                                      │
                         duration expires or client disconnects
                                      │
                                  complete
```

### InteractiveService (`interactive_service.py`)

Streaming chat for follow-up questions about analysis results.

**Key Pattern**: Streaming text chunks

```python
from lib.services import InteractiveService

service = InteractiveService()

async for event in service.chat(query_hash, message):
    if isinstance(event, ChunkEvent):
        print(event.text, end="")  # Stream to terminal
```

**Events**:
| Event | Description |
|-------|-------------|
| `ChunkEvent` | Text chunk for streaming display |
| `MessageEvent` | Complete message (non-streaming fallback) |
| `InteractiveCompleteEvent` | Session complete |
| `InteractiveErrorEvent` | Error occurred |

## Renderers

Renderers map events to terminal output using Rich. They are **pure output** - no business logic.

### AskRenderer (`lib/engines/ask3/renderer.py`)

```python
from lib.engines.ask3 import AskRenderer

renderer = AskRenderer(verbose=True)

async for event in service.ask(input, options):
    renderer.render(event)
```

**Methods**:
- `render(event)` - Display event to terminal
- Internal: `_render_status()`, `_render_sql_generated()`, `_render_result()`, etc.

### AnalyzeRenderer (`lib/cli/analyze_renderer.py`)

```python
from lib.cli.analyze_renderer import AnalyzeRenderer

renderer = AnalyzeRenderer()

try:
    async for event in service.analyze(input, options):
        renderer.render(event)
finally:
    renderer.cleanup()  # Stop spinner if active
```

**Methods**:
- `render(event)` - Display event (progress spinner, status lines)
- `cleanup()` - Stop any active spinner

## Input Handlers

Handle user input collection for CLI. **Pure input** - no rendering.

### AskInputHandler (`lib/engines/ask3/input_handler.py`)

```python
from lib.engines.ask3 import AskInputHandler, NonInteractiveInputHandler

# Interactive mode
handler = AskInputHandler()
answers = handler.collect_clarifications(clarification_event)

# Non-interactive mode (--no-interactive flag)
handler = NonInteractiveInputHandler()
answers = handler.collect_clarifications(event)  # Returns first option
```

## Types (`types.py`)

All events and input types are dataclasses with discriminated unions:

```python
@dataclass
class AskStatusEvent:
    type: Literal["status"]  # Discriminator
    phase: str
    message: str

# Union for type checking
AskEvent = Union[
    AskStatusEvent,
    AskSchemaLoadedEvent,
    AskClarificationNeededEvent,
    ...
]
```

**Pattern**: Use `isinstance()` checks in consumers:
```python
if isinstance(event, AskResultEvent):
    print(f"Got {event.row_count} rows")
elif isinstance(event, AskErrorEvent):
    print(f"Error: {event.message}")
```

## CLI Integration Pattern

Standard pattern for CLI commands using services:

```python
def my_command(self, ...) -> RdstResult:
    from lib.services import MyService, MyInput, MyOptions
    from lib.cli.my_renderer import MyRenderer

    service = MyService()
    renderer = MyRenderer(verbose=verbose)

    input_data = MyInput(...)
    options = MyOptions(...)

    result = None
    error = None

    async def _run():
        nonlocal result, error
        try:
            async for event in service.do_thing(input_data, options):
                renderer.render(event)

                if isinstance(event, ResultEvent):
                    result = event
                elif isinstance(event, ErrorEvent):
                    error = event
        finally:
            renderer.cleanup()

    asyncio.run(_run())

    if result:
        return RdstResult(ok=True, data={...})
    elif error:
        return RdstResult(ok=False, message=error.message)
```

## Web API Integration Pattern

Standard pattern for FastAPI routes:

```python
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

router = APIRouter()

def _event_to_sse(event: MyEvent) -> dict:
    """Convert typed event to SSE format."""
    if isinstance(event, StatusEvent):
        return {"event": "status", "data": json.dumps({...})}
    elif isinstance(event, ResultEvent):
        return {"event": "result", "data": json.dumps({...})}
    # ...

async def _generator(request: MyRequest):
    service = MyService()
    async for event in service.do_thing(...):
        yield _event_to_sse(event)

@router.post("/my-endpoint")
async def my_endpoint(request: MyRequest):
    return EventSourceResponse(_generator(request))
```

## Adding a New Service

1. **Define types** in `types.py`:
   ```python
   @dataclass
   class MyInput:
       field: str

   @dataclass
   class MyStatusEvent:
       type: Literal["status"]
       message: str

   MyEvent = Union[MyStatusEvent, MyResultEvent, MyErrorEvent]
   ```

2. **Create service** in `my_service.py`:
   ```python
   class MyService:
       async def do_thing(self, input, options) -> AsyncGenerator[MyEvent, None]:
           yield MyStatusEvent(type="status", message="Starting...")
           # ... logic ...
           yield MyResultEvent(type="result", ...)
   ```

3. **Create renderer** (for CLI):
   ```python
   class MyRenderer:
       def render(self, event: MyEvent) -> None:
           if isinstance(event, MyStatusEvent):
               self._console.print(event.message)
   ```

4. **Create input handler** (if user interaction needed):
   ```python
   class MyInputHandler:
       def collect_input(self, event: MyInputNeededEvent) -> dict:
           return prompt_user(event.questions)
   ```

5. **Export** from `__init__.py`

6. **Integrate** in CLI command and/or API route

## File Structure

```
lib/services/
├── AGENTS.md              # This file
├── __init__.py            # Exports
├── types.py               # All event/input dataclasses
├── analyze_service.py     # Query analysis service
├── ask_service.py         # Text-to-SQL service
├── interactive_service.py # Chat service
└── top_service.py         # Top queries service

lib/engines/ask3/
├── renderer.py            # AskRenderer
└── input_handler.py       # AskInputHandler

lib/cli/
├── analyze_renderer.py    # AnalyzeRenderer
└── top_renderer.py        # TopRenderer

lib/api/routes/
├── analyze.py             # /api/analyze endpoint
├── ask.py                 # /api/ask endpoint
├── interactive.py         # /api/interactive endpoint
└── top.py                 # /api/top endpoint

web-apps/apps/rdst/src/
├── routes/top.tsx         # Top Queries page
├── routes/results.tsx     # Analysis results (handles parameterized queries)
├── lib/useTop.ts          # SSE streaming hook
├── components/top/        # TopFilters, TopQueryTable, ParameterDialog
└── types/top.ts           # Frontend types
```

## Web UI: Parameterized Query Handling

When analyzing queries with placeholders (`$1`, `?`, `:name`), the web UI intercepts
before sending to the backend and prompts for parameter values via `ParameterDialog`.

```
User clicks Analyze ──► hasParameters(sql)? ──► YES ──► ParameterDialog
                              │                              │
                              NO                     user enters values
                              │                              │
                              ▼                              ▼
                      /api/analyze ◄──────────── substituted query
```

This happens at the results page level, catching queries from any source
(Top Queries, Query Registry, or direct URL).

## Key Principles

1. **Services are stateless** - No constructor parameters, no injected dependencies
2. **Events are typed** - Use dataclasses with Literal type discriminators
3. **Renderers are pure output** - No business logic, just display
4. **Input handlers are pure input** - No rendering, just collection
5. **Async generators** - All services yield events, enabling streaming
6. **Pause/Resume pattern** - For mid-execution user interaction (Ask)
7. **One-shot pattern** - For operations that run to completion (Analyze)

# RDST Service Creation Guide

This skill provides guidance for creating new services in `rdst/lib/services/`.

## Why Services Exist

Services are the **single source of truth** for business logic shared between:
- **CLI commands** - via `asyncio.run()` bridge with Renderer for terminal output
- **Web API** - via native async with SSE (Server-Sent Events) streaming
- **MCP server** - via async interface for Claude Code integration

This architecture ensures:
- No logic duplication between CLI and web
- Consistent behavior across all interfaces
- Testable, isolated business logic
- Type-safe event contracts

## Service Patterns

RDST uses two primary patterns:

### 1. Async Generator Services (Streaming)

Use for operations that:
- Take time and benefit from progress updates
- Need to stream results incrementally
- May require user interaction mid-execution

```python
# my_service.py
from typing import AsyncGenerator
from .types import MyEvent, MyInput, MyOptions

class MyService:
    """Stateless service for [description]."""

    def __init__(self) -> None:
        """Initialize (no constructor parameters - stateless)."""
        pass

    async def do_thing(
        self,
        input: MyInput,
        options: MyOptions,
    ) -> AsyncGenerator[MyEvent, None]:
        """Execute operation and yield events.

        Yields:
            MyStatusEvent: Progress updates
            MyResultEvent: Final results
            MyErrorEvent: On failure
        """
        try:
            yield MyStatusEvent(type="status", message="Starting...")
            
            # Do work...
            result = await self._perform_work(input)
            
            yield MyResultEvent(type="result", data=result)
            
        except Exception as e:
            yield MyErrorEvent(type="error", message=str(e))
```

### 2. Synchronous Services (Return-based)

Use for operations that:
- Complete quickly
- Don't need streaming feedback
- Return structured results

```python
# my_service.py
from .types import MyResult, MyInput

class MyService:
    """Stateless service for [description]."""

    def __init__(self) -> None:
        pass

    def get_status(self) -> MyResult:
        """Check status and return result."""
        # Do work...
        return MyResult(
            success=True,
            data={"key": "value"}
        )

    def perform_action(self, input: MyInput) -> MyResult:
        """Perform action and return result."""
        try:
            # Do work...
            return MyResult(success=True, message="Done")
        except Exception as e:
            return MyResult(success=False, error=str(e))
```

## Step-by-Step: Creating a New Service

### Step 1: Define Types in `types.py`

All event and input types go in `types.py` as dataclasses:

```python
# In types.py

# ============================================================================
# MyFeature Types - Input/Options
# ============================================================================

@dataclass
class MyInput:
    """Input for my service."""
    query: str
    target: Optional[str] = None

@dataclass
class MyOptions:
    """Options for my service execution."""
    verbose: bool = False
    timeout: int = 30

# ============================================================================
# MyFeature Event Types
# ============================================================================

@dataclass
class MyStatusEvent:
    """Progress status update."""
    type: Literal["status"]  # Discriminator for union typing
    message: str
    phase: Optional[str] = None

@dataclass
class MyResultEvent:
    """Operation completed successfully."""
    type: Literal["result"]
    success: bool
    data: Dict[str, Any]

@dataclass
class MyErrorEvent:
    """Operation encountered an error."""
    type: Literal["error"]
    message: str
    stage: Optional[str] = None

# Union type for all events (enables isinstance() checks)
MyEvent = Union[
    MyStatusEvent,
    MyResultEvent,
    MyErrorEvent,
]
```

**Key conventions:**
- Events have a `type` field with `Literal["..."]` for discriminated unions
- Use `Optional[T]` for nullable fields with `= None` default
- Group related types with section comments
- Name events as `{Feature}{Purpose}Event`

### Step 2: Create the Service File

```python
# my_service.py
"""MyService - Description of what this service does.

Usage:
    service = MyService()
    
    async for event in service.do_thing(input, options):
        handle(event)
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Any, Dict, Optional

from .types import (
    MyEvent,
    MyInput,
    MyOptions,
    MyStatusEvent,
    MyResultEvent,
    MyErrorEvent,
)


class MyService:
    """Stateless service for [feature description].

    Both CLI and Web API consume the same event stream.
    """

    def __init__(self) -> None:
        """Initialize (stateless - no parameters)."""
        pass

    async def do_thing(
        self,
        input: MyInput,
        options: MyOptions,
    ) -> AsyncGenerator[MyEvent, None]:
        """Execute the main operation.

        Args:
            input: Resolved input with required data
            options: Execution options

        Yields:
            MyStatusEvent: Progress updates
            MyResultEvent: Success result (final)
            MyErrorEvent: Error occurred (final)
        """
        try:
            # Phase 1: Setup
            yield MyStatusEvent(
                type="status",
                message="Loading configuration...",
                phase="setup",
            )

            config = await self._load_config(input.target)
            if config is None:
                yield MyErrorEvent(
                    type="error",
                    message="Configuration not found",
                    stage="setup",
                )
                return

            # Phase 2: Execute
            yield MyStatusEvent(
                type="status",
                message="Processing...",
                phase="execute",
            )

            # Run blocking work in thread
            result = await asyncio.to_thread(
                self._perform_work_sync,
                input,
                config,
            )

            # Phase 3: Complete
            yield MyResultEvent(
                type="result",
                success=True,
                data=result,
            )

        except Exception as e:
            yield MyErrorEvent(
                type="error",
                message=str(e),
            )

    async def _load_config(self, target: Optional[str]) -> Optional[Dict[str, Any]]:
        """Load configuration from TargetsConfig."""
        from ..cli.rdst_cli import TargetsConfig

        cfg = TargetsConfig()
        cfg.load()
        target_name = target or cfg.get_default()

        if not target_name:
            return None

        return cfg.get(target_name)

    def _perform_work_sync(
        self,
        input: MyInput,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Synchronous work (runs in thread via asyncio.to_thread)."""
        # Heavy/blocking operations go here
        return {"result": "data"}
```

### Step 3: Export from `__init__.py`

```python
# In __init__.py

from .my_service import MyService
from .types import (
    # MyFeature types
    MyEvent,
    MyInput,
    MyOptions,
    MyStatusEvent,
    MyResultEvent,
    MyErrorEvent,
)

__all__ = [
    # Services
    "MyService",
    # MyFeature types
    "MyEvent",
    "MyInput",
    "MyOptions",
    "MyStatusEvent",
    "MyResultEvent",
    "MyErrorEvent",
]
```

### Step 4: Create CLI Integration

```python
# In lib/cli/rdst_cli.py or separate command file

def my_command(self, query: str, target: Optional[str] = None, verbose: bool = False) -> RdstResult:
    """Execute my feature."""
    from lib.services import MyService, MyInput, MyOptions
    from lib.cli.my_renderer import MyRenderer

    service = MyService()
    renderer = MyRenderer(verbose=verbose)

    input_data = MyInput(query=query, target=target)
    options = MyOptions(verbose=verbose)

    result = None
    error = None

    async def _run():
        nonlocal result, error
        try:
            async for event in service.do_thing(input_data, options):
                renderer.render(event)

                if isinstance(event, MyResultEvent):
                    result = event
                elif isinstance(event, MyErrorEvent):
                    error = event
        finally:
            renderer.cleanup()

    asyncio.run(_run())

    if result:
        return RdstResult(ok=True, data=result.data)
    elif error:
        return RdstResult(ok=False, message=error.message)
    else:
        return RdstResult(ok=False, message="Unknown error")
```

### Step 5: Create Web API Integration

```python
# In lib/api/routes/my_feature.py

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel
import json

from lib.services import MyService, MyInput, MyOptions

router = APIRouter()


class MyRequest(BaseModel):
    query: str
    target: str | None = None
    verbose: bool = False


def _event_to_sse(event) -> dict:
    """Convert typed event to SSE format."""
    if hasattr(event, "type"):
        return {
            "event": event.type,
            "data": json.dumps({
                k: v for k, v in event.__dict__.items()
            })
        }
    return {"event": "unknown", "data": "{}"}


async def _stream_generator(request: MyRequest):
    service = MyService()
    input_data = MyInput(query=request.query, target=request.target)
    options = MyOptions(verbose=request.verbose)

    async for event in service.do_thing(input_data, options):
        yield _event_to_sse(event)


@router.post("/my-feature")
async def my_feature_endpoint(request: MyRequest):
    return EventSourceResponse(_stream_generator(request))
```

## Advanced Patterns

### Pause/Resume for User Interaction

When you need user input mid-execution (like `AskService`):

```python
# Store sessions for pause/resume
_sessions: Dict[str, MyContext] = {}

class MyService:
    async def do_thing(self, input, options) -> AsyncGenerator[MyEvent, None]:
        # ... initial work ...
        
        if needs_clarification:
            session_id = str(uuid.uuid4())
            _sessions[session_id] = context
            
            yield MyClarificationNeededEvent(
                type="clarification_needed",
                session_id=session_id,
                questions=questions,
            )
            return  # Pause - consumer calls resume()
        
        # Continue if no clarification needed
        async for event in self._run_to_completion(context):
            yield event

    async def resume(
        self,
        session_id: str,
        answers: Dict[str, str],
    ) -> AsyncGenerator[MyEvent, None]:
        """Resume after user provides answers."""
        if session_id not in _sessions:
            yield MyErrorEvent(type="error", message="Session expired")
            return
        
        context = _sessions.pop(session_id)
        context.apply_answers(answers)
        
        async for event in self._run_to_completion(context):
            yield event
```

### Streaming Real-time Data

For continuous streaming (like `TopService` real-time mode):

```python
async def stream_realtime(
    self,
    input: MyInput,
    options: MyOptions,
    duration: int = 30,
) -> AsyncGenerator[MyEvent, None]:
    """Stream real-time data for specified duration."""
    import time
    
    start = time.time()
    
    while time.time() - start < duration:
        data = await self._fetch_current_data()
        
        yield MyDataEvent(
            type="data",
            items=data,
            runtime_seconds=time.time() - start,
        )
        
        await asyncio.sleep(0.2)  # Poll interval
    
    yield MyCompleteEvent(type="complete", success=True)
```

### Running Blocking Code

Always use `asyncio.to_thread()` for blocking operations:

```python
async def do_thing(self, input, options):
    # BAD - blocks event loop
    result = self._blocking_work(input)
    
    # GOOD - runs in thread pool
    result = await asyncio.to_thread(self._blocking_work, input)
```

## Renderer Pattern (CLI Only)

Renderers map events to terminal output using Rich:

```python
# lib/cli/my_renderer.py
from rich.console import Console
from lib.services import MyEvent, MyStatusEvent, MyResultEvent, MyErrorEvent

class MyRenderer:
    def __init__(self, verbose: bool = False):
        self._console = Console()
        self._verbose = verbose
        self._spinner = None

    def render(self, event: MyEvent) -> None:
        if isinstance(event, MyStatusEvent):
            self._render_status(event)
        elif isinstance(event, MyResultEvent):
            self._render_result(event)
        elif isinstance(event, MyErrorEvent):
            self._render_error(event)

    def _render_status(self, event: MyStatusEvent) -> None:
        if self._verbose:
            self._console.print(f"[dim]{event.message}[/dim]")

    def _render_result(self, event: MyResultEvent) -> None:
        self._console.print("[green]Success![/green]")

    def _render_error(self, event: MyErrorEvent) -> None:
        self._console.print(f"[red]Error: {event.message}[/red]")

    def cleanup(self) -> None:
        """Stop any active spinner."""
        if self._spinner:
            self._spinner.stop()
```

## Key Principles

1. **Services are stateless** - No constructor parameters, no injected dependencies
2. **Events are typed** - Use dataclasses with `Literal` type discriminators
3. **Renderers are pure output** - No business logic, just display
4. **Input handlers are pure input** - No rendering, just collection
5. **Async generators** - All streaming services yield events
6. **Use `asyncio.to_thread()`** - For blocking operations in async context
7. **Error handling** - Always yield error events, never raise from generators

## File Checklist

When creating a new service:

- [ ] `types.py` - Add Input, Options, and Event dataclasses
- [ ] `my_service.py` - Create the service class
- [ ] `__init__.py` - Export service and types
- [ ] `lib/cli/my_renderer.py` - Create CLI renderer (if CLI support needed)
- [ ] `lib/cli/rdst_cli.py` - Add CLI command
- [ ] `lib/api/routes/my_feature.py` - Add API route (if web support needed)
- [ ] `lib/api/main.py` - Register router

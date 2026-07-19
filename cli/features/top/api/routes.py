"""Top queries API endpoint with SSE streaming.

Provides both historical (one-shot) and realtime (SSE streaming) modes
for monitoring top slow queries from database telemetry.
"""

import json
from typing import AsyncGenerator, Optional, Union

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from shared.api.target_guard import TargetGuard, require_target
from shared.telemetry import telemetry
from ..service import TopService
from ..events import (
    TopCompleteEvent,
    TopConnectedEvent,
    TopDbLimitWarningEvent,
    TopErrorEvent,
    TopEvent,
    TopQueriesEvent,
    TopQuerySavedEvent,
    TopSourceFallbackEvent,
    TopStatusEvent,
)
from ..telemetry import top_terminal_detector
from ..models import (
    TopInput,
    TopOptions,
    TopQueryData,
)

router = APIRouter()


class TopQueryResponseItem(BaseModel):
    query_hash: str
    query_text: str
    normalized_query: str
    freq: int
    total_time: str
    avg_time: str
    pct_load: str
    qps: Optional[float] = None
    max_duration_ms: Optional[float] = None
    current_instances_running: Optional[int] = None
    observation_count: Optional[int] = None


class TopDbLimitWarning(BaseModel):
    db_limit_bytes: int
    recommended_bytes: int
    setting_name: str
    db_engine: str


class TopHistoricalResponse(BaseModel):
    success: bool
    target: Optional[str] = None
    engine: Optional[str] = None
    source: Optional[str] = None
    queries: list[TopQueryResponseItem] = []
    newly_saved: int = 0
    db_limit_warning: Optional[TopDbLimitWarning] = None
    error: Optional[str] = None


def _serialize_query_data(query: TopQueryData) -> dict:
    """Serialize TopQueryData to JSON-compatible dict."""
    data = {
        "query_hash": query.query_hash,
        "query_text": query.query_text,
        "normalized_query": query.normalized_query,
        "freq": query.freq,
        "total_time": query.total_time,
        "avg_time": query.avg_time,
        "pct_load": query.pct_load,
    }
    if query.qps is not None:
        data["qps"] = query.qps
    if query.max_duration_ms is not None:
        data["max_duration_ms"] = round(query.max_duration_ms, 2)
    if query.current_instances is not None:
        data["current_instances_running"] = query.current_instances
    if query.observation_count is not None:
        data["observation_count"] = query.observation_count
    return data


def _event_to_sse(event: TopEvent) -> dict:
    """Convert TopEvent to SSE format."""
    if isinstance(event, TopStatusEvent):
        return {
            "event": "status",
            "data": json.dumps({"message": event.message}),
        }
    elif isinstance(event, TopConnectedEvent):
        return {
            "event": "connected",
            "data": json.dumps(
                {
                    "target_name": event.target_name,
                    "db_engine": event.db_engine,
                    "source": event.source,
                }
            ),
        }
    elif isinstance(event, TopSourceFallbackEvent):
        return {
            "event": "source_fallback",
            "data": json.dumps(
                {
                    "from_source": event.from_source,
                    "to_source": event.to_source,
                    "reason": event.reason,
                }
            ),
        }
    elif isinstance(event, TopDbLimitWarningEvent):
        return {
            "event": "db_limit_warning",
            "data": json.dumps(
                {
                    "db_limit_bytes": event.db_limit_bytes,
                    "recommended_bytes": event.recommended_bytes,
                    "setting_name": event.setting_name,
                    "db_engine": event.db_engine,
                }
            ),
        }
    elif isinstance(event, TopQueriesEvent):
        return {
            "event": "queries",
            "data": json.dumps(
                {
                    "queries": [_serialize_query_data(q) for q in event.queries],
                    "source": event.source,
                    "target_name": event.target_name,
                    "db_engine": event.db_engine,
                    "runtime_seconds": event.runtime_seconds,
                    "total_tracked": event.total_tracked,
                }
            ),
        }
    elif isinstance(event, TopQuerySavedEvent):
        return {
            "event": "query_saved",
            "data": json.dumps(
                {
                    "query_hash": event.query_hash,
                    "is_new": event.is_new,
                }
            ),
        }
    elif isinstance(event, TopCompleteEvent):
        return {
            "event": "complete",
            "data": json.dumps(
                {
                    "success": event.success,
                    "queries": [_serialize_query_data(q) for q in event.queries],
                    "source": event.source,
                    "newly_saved": event.newly_saved,
                }
            ),
        }
    elif isinstance(event, TopErrorEvent):
        # Shared {code, message, detail} envelope (B7/T24).
        error_data = {
            "code": event.code,
            "message": event.message,
            "detail": event.detail,
        }
        if event.stage:
            error_data["stage"] = event.stage
        return {
            "event": "error",
            "data": json.dumps(error_data),
        }
    else:
        return {
            "event": "error",
            "data": json.dumps({"message": f"Unknown event type: {type(event)}"}),
        }


async def _realtime_generator(
    target: str,
    target_engine: str,
    options: TopOptions,
    duration: Optional[int],
) -> AsyncGenerator[dict, None]:
    """Generate SSE events for realtime streaming.

    Emits `top_realtime_run` (not `top_run`) because the success semantics
    differ: realtime streams typically end via client disconnect, so
    `success` here means "no error during the stream", and `duration_ms`
    captures how long the user actually watched.
    """
    async with telemetry.command_run(
        "top_realtime",
        source="web",
        target_engine=target_engine,
        terminal_detector=top_terminal_detector,
        limit=options.limit,
        duration=duration,
    ) as run:
        service = TopService()
        input_data = TopInput(target=target, source="activity")
        try:
            async for event in service.stream_realtime(input_data, options, duration):
                # Capture the connected engine when announced — more accurate
                # than the configured engine.
                if isinstance(event, TopConnectedEvent) and event.db_engine:
                    run.target_engine = event.db_engine
                run.observe(event)
                yield _event_to_sse(event)
            # Clean stream exit (client disconnected or `duration` elapsed):
            # treat as success unless an error event already marked failure.
            if run.success is None:
                run.success = True
        except Exception as e:
            run.error(e)
            # Shared {code, message, detail} envelope (B7/T24); never raw str(e).
            yield {
                "event": "error",
                "data": json.dumps(
                    {
                        "code": "internal_error",
                        "message": "The slow-query lookup could not be completed.",
                        "detail": type(e).__name__,
                    }
                ),
            }


async def _historical_generator(
    target: str,
    target_engine: str,
    source: str,
    options: TopOptions,
) -> AsyncGenerator[dict, None]:
    """Generate SSE events for historical one-shot.

    Emits `top_run`. `success` here has a real answer: did the engine
    return queries without error?
    """
    async with telemetry.command_run(
        "top",
        source="web",
        target_engine=target_engine,
        terminal_detector=top_terminal_detector,
        limit=options.limit,
        data_source=source,
    ) as run:
        service = TopService()
        input_data = TopInput(target=target, source=source)
        try:
            async for event in service.get_top_queries(input_data, options):
                if isinstance(event, TopConnectedEvent) and event.db_engine:
                    run.target_engine = event.db_engine
                run.observe(event)
                yield _event_to_sse(event)
        except Exception as e:
            run.error(e)
            # Shared {code, message, detail} envelope (B7/T24); never raw str(e).
            yield {
                "event": "error",
                "data": json.dumps(
                    {
                        "code": "internal_error",
                        "message": "The slow-query lookup could not be completed.",
                        "detail": type(e).__name__,
                    }
                ),
            }


@router.get("/top", response_model=TopHistoricalResponse)
async def get_top_queries(
    guard: TargetGuard = Depends(require_target),
    limit: int = Query(10, description="Number of top queries to return"),
    realtime: bool = Query(False, description="Enable realtime SSE streaming"),
    duration: Optional[int] = Query(
        None, description="Duration in seconds for realtime mode (auto-complete)"
    ),
    source: str = Query("auto", description="Data source (auto, pg_stat, activity, digest)"),
    sort: str = Query("total_time", description="Sort by (total_time, freq, avg_time, load)"),
    filter_pattern: Optional[str] = Query(None, description="Regex filter for queries"),
    min_freq: int = Query(0, ge=0, description="Minimum executions/frequency"),
    min_load_pct: float = Query(0.0, ge=0.0, description="Minimum load percentage"),
    min_total_time_s: float = Query(0.0, ge=0.0, description="Minimum total time in seconds"),
    auto_save: bool = Query(True, description="Auto-save queries to registry"),
    stream: bool = Query(False, description="Return SSE stream instead of JSON (alias for realtime)"),
) -> Union[TopHistoricalResponse, EventSourceResponse]:
    """Get top queries from database telemetry.

    Two modes available:
    - Historical (default): One-shot query against pg_stat_statements/performance_schema
    - Realtime (realtime=true or stream=true): SSE streaming of active queries

    For realtime mode:
    - Events stream continuously until client disconnects
    - Use duration parameter to auto-complete after N seconds
    - Events: status, connected, queries (repeating), query_saved, complete/error

    For historical mode:
    - Returns JSON response with query list
    - Can also use stream=true to get SSE events during execution

    Example usage:
    ```
    # Historical snapshot (JSON)
    GET /api/top?target=mydb&limit=10

    # Historical with streaming
    GET /api/top?target=mydb&stream=true

    # Realtime SSE (continuous)
    GET /api/top?target=mydb&realtime=true

    # Realtime with auto-complete
    GET /api/top?target=mydb&realtime=true&duration=10
    ```
    """
    options = TopOptions(
        limit=limit,
        sort=sort,
        filter_pattern=filter_pattern,
        auto_save_registry=auto_save,
        min_freq=min_freq,
        min_load_pct=min_load_pct,
        min_total_time_s=min_total_time_s,
    )

    if realtime:
        return EventSourceResponse(
            _realtime_generator(guard.target_name, guard.target_engine, options, duration)
        )

    if stream:
        return EventSourceResponse(
            _historical_generator(guard.target_name, guard.target_engine, source, options)
        )

    # Historical one-shot - collect all events and return JSON
    service = TopService()
    input_data = TopInput(target=guard.target_name, source=source)

    result = None
    target_name = ""
    db_engine = ""
    actual_source = source
    error_message = None
    db_limit_warning = None

    async with telemetry.command_run(
        "top",
        source="web",
        target_engine=guard.target_engine,
        terminal_detector=top_terminal_detector,
        limit=limit,
        data_source=source,
    ) as run:
        try:
            async for event in service.get_top_queries(input_data, options):
                if isinstance(event, TopConnectedEvent):
                    target_name = event.target_name
                    db_engine = event.db_engine
                    actual_source = event.source
                    if event.db_engine:
                        run.target_engine = event.db_engine
                elif isinstance(event, TopDbLimitWarningEvent):
                    db_limit_warning = event
                elif isinstance(event, TopCompleteEvent):
                    result = event
                elif isinstance(event, TopErrorEvent):
                    error_message = event.message
                    break
                run.observe(event)
        except Exception as e:
            run.error(e)
            raise

    if error_message:
        return TopHistoricalResponse(success=False, error=error_message)

    if result is None:
        return TopHistoricalResponse(success=False, error="No results collected")

    return TopHistoricalResponse(
        success=True,
        target=target_name,
        engine=db_engine,
        source=actual_source,
        queries=[TopQueryResponseItem(**_serialize_query_data(q)) for q in result.queries],
        newly_saved=result.newly_saved,
        db_limit_warning=(
            TopDbLimitWarning(
                db_limit_bytes=db_limit_warning.db_limit_bytes,
                recommended_bytes=db_limit_warning.recommended_bytes,
                setting_name=db_limit_warning.setting_name,
                db_engine=db_limit_warning.db_engine,
            )
            if db_limit_warning
            else None
        ),
    )


@router.get("/top/realtime")
async def get_top_queries_realtime(
    guard: TargetGuard = Depends(require_target),
    limit: int = Query(10, description="Number of top queries to return"),
    duration: Optional[int] = Query(
        None, description="Duration in seconds (auto-complete after N seconds)"
    ),
    auto_save: bool = Query(True, description="Auto-save queries to registry"),
    min_freq: int = Query(0, ge=0, description="Minimum executions/frequency"),
    min_load_pct: float = Query(0.0, ge=0.0, description="Minimum load percentage"),
    min_total_time_s: float = Query(0.0, ge=0.0, description="Minimum total time in seconds"),
):
    """Realtime top queries via SSE streaming.

    Streams active query monitoring data from pg_stat_activity (PostgreSQL)
    or SHOW FULL PROCESSLIST (MySQL) with 200ms polling.

    Events:
    - `status`: Progress messages
    - `connected`: Connection established (target, engine, source)
    - `queries`: Current top queries (repeats every poll)
    - `query_saved`: New query saved to registry
    - `complete`: Streaming complete (only if duration specified)
    - `error`: Error occurred

    Example:
    ```javascript
    const eventSource = new EventSource('/api/top/realtime?target=mydb&duration=30');

    eventSource.addEventListener('queries', (event) => {
        const data = JSON.parse(event.data);
        console.log('Top queries:', data.queries);
    });

    eventSource.addEventListener('complete', (event) => {
        eventSource.close();
    });
    ```
    """
    options = TopOptions(
        limit=limit,
        auto_save_registry=auto_save,
        min_freq=min_freq,
        min_load_pct=min_load_pct,
        min_total_time_s=min_total_time_s,
    )
    return EventSourceResponse(
        _realtime_generator(guard.target_name, guard.target_engine, options, duration)
    )

"""Top queries API endpoint with SSE streaming.

Provides both historical (one-shot) and realtime (SSE streaming) modes
for monitoring top slow queries from database telemetry.
"""

import json
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, Query
from sse_starlette.sse import EventSourceResponse

from .target_guard import TargetGuard, require_target
from ...services.top_service import TopService
from ...services.types import (
    TopCompleteEvent,
    TopConnectedEvent,
    TopErrorEvent,
    TopEvent,
    TopInput,
    TopOptions,
    TopQueriesEvent,
    TopQueryData,
    TopQuerySavedEvent,
    TopSourceFallbackEvent,
    TopStatusEvent,
)

router = APIRouter()


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
        error_data = {"message": event.message}
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
    limit: int,
    duration: Optional[int],
    auto_save: bool,
) -> AsyncGenerator[dict, None]:
    """Generate SSE events for realtime streaming."""
    try:
        from lib.telemetry import telemetry
        telemetry.track("top_run", {
            "source": "web",
            "target": target,
            "mode": "realtime",
            "limit": limit,
            "duration": duration,
        })
    except Exception:
        pass

    service = TopService()
    input_data = TopInput(target=target, source="activity")
    options = TopOptions(
        limit=limit,
        poll_interval_ms=200,
        auto_save_registry=auto_save,
    )

    try:
        async for event in service.stream_realtime(input_data, options, duration):
            yield _event_to_sse(event)
    except Exception as e:
        yield {"event": "error", "data": json.dumps({"message": str(e)})}


async def _historical_generator(
    target: str,
    source: str,
    limit: int,
    sort: str,
    filter_pattern: Optional[str],
    auto_save: bool,
) -> AsyncGenerator[dict, None]:
    """Generate SSE events for historical one-shot."""
    service = TopService()
    input_data = TopInput(target=target, source=source)
    options = TopOptions(
        limit=limit,
        sort=sort,
        filter_pattern=filter_pattern,
        auto_save_registry=auto_save,
    )

    try:
        async for event in service.get_top_queries(input_data, options):
            yield _event_to_sse(event)
    except Exception as e:
        yield {"event": "error", "data": json.dumps({"message": str(e)})}


@router.get("/top")
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
    auto_save: bool = Query(True, description="Auto-save queries to registry"),
    stream: bool = Query(False, description="Return SSE stream instead of JSON (alias for realtime)"),
):
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
    if realtime:
        return EventSourceResponse(
            _realtime_generator(guard.target_name, limit, duration, auto_save)
        )

    if stream:
        return EventSourceResponse(
            _historical_generator(
                guard.target_name, source, limit, sort, filter_pattern, auto_save
            )
        )

    try:
        from lib.telemetry import telemetry
        telemetry.track("top_run", {
            "source": "web",
            "target": guard.target_name,
            "mode": "historical",
            "limit": limit,
            "data_source": source,
        })
    except Exception:
        pass

    # Historical one-shot - collect all events and return JSON
    service = TopService()
    input_data = TopInput(target=guard.target_name, source=source)
    options = TopOptions(
        limit=limit,
        sort=sort,
        filter_pattern=filter_pattern,
        auto_save_registry=auto_save,
    )

    result = None
    target_name = ""
    db_engine = ""
    actual_source = source
    error_message = None

    async for event in service.get_top_queries(input_data, options):
        if isinstance(event, TopConnectedEvent):
            target_name = event.target_name
            db_engine = event.db_engine
            actual_source = event.source
        elif isinstance(event, TopCompleteEvent):
            result = event
        elif isinstance(event, TopErrorEvent):
            error_message = event.message
            break

    if error_message:
        return {
            "success": False,
            "error": error_message,
        }

    if result is None:
        return {
            "success": False,
            "error": "No results collected",
        }

    return {
        "success": True,
        "target": target_name,
        "engine": db_engine,
        "source": actual_source,
        "queries": [_serialize_query_data(q) for q in result.queries],
        "newly_saved": result.newly_saved,
    }


@router.get("/top/realtime")
async def get_top_queries_realtime(
    guard: TargetGuard = Depends(require_target),
    limit: int = Query(10, description="Number of top queries to return"),
    duration: Optional[int] = Query(
        None, description="Duration in seconds (auto-complete after N seconds)"
    ),
    auto_save: bool = Query(True, description="Auto-save queries to registry"),
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
    return EventSourceResponse(
        _realtime_generator(guard.target_name, limit, duration, auto_save)
    )

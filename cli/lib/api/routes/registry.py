from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Any, Optional, Literal, AsyncGenerator, Union
from datetime import datetime
from sse_starlette.sse import EventSourceResponse
import json
import asyncio
import time

from .target_guard import TargetGuard, require_target_body

router = APIRouter()


class QueryRegistryEntry(BaseModel):
    sql: str
    hash: str
    tag: str
    last_analyzed: str
    target: str
    frequency: int
    source: str
    most_recent_params: dict = {}


class QueryRegistryResponse(BaseModel):
    queries: list[QueryRegistryEntry]
    error: Optional[str] = None


class AddQueryRequest(BaseModel):
    sql: str
    target: Optional[str] = None


class AddQueryResponse(BaseModel):
    success: bool
    hash: Optional[str] = None
    error: Optional[str] = None


class RemoveQueryResponse(BaseModel):
    success: bool
    error: Optional[str] = None


@router.get("/query-registry")
async def get_query_registry(limit: int = 50) -> QueryRegistryResponse:
    """Get queries from the shared query registry."""
    try:
        from ...query_registry import QueryRegistry

        registry = QueryRegistry()
        registry.load()

        queries = registry.list_queries(limit=limit)

        return QueryRegistryResponse(
            queries=[
                QueryRegistryEntry(
                    sql=q.sql,
                    hash=q.hash,
                    tag=q.tag,
                    last_analyzed=q.last_analyzed,
                    target=q.last_target,
                    frequency=q.frequency,
                    source=q.source,
                    most_recent_params=q.most_recent_params,
                )
                for q in queries
            ]
        )

    except Exception as e:
        return QueryRegistryResponse(queries=[], error=str(e))


@router.post("/query-registry")
async def add_query_to_registry(request: AddQueryRequest) -> AddQueryResponse:
    """Add a query to the registry."""
    try:
        from ...query_registry import QueryRegistry

        registry = QueryRegistry()
        registry.load()

        query_hash, _ = registry.add_query(
            sql=request.sql,
            source="web",
            target=request.target or "",
        )

        return AddQueryResponse(success=True, hash=query_hash)

    except Exception as e:
        return AddQueryResponse(success=False, error=str(e))


@router.delete("/query-registry/{query_hash}")
async def remove_query_from_registry(query_hash: str) -> RemoveQueryResponse:
    """Remove a query from the registry."""
    try:
        from ...query_registry import QueryRegistry

        registry = QueryRegistry()
        registry.load()

        removed = registry.remove_query(query_hash)

        if removed:
            return RemoveQueryResponse(success=True)
        else:
            return RemoveQueryResponse(success=False, error="Query not found")

    except Exception as e:
        return RemoveQueryResponse(success=False, error=str(e))


class UpdateTagRequest(BaseModel):
    tag: str


class UpdateTagResponse(BaseModel):
    success: bool
    error: Optional[str] = None


@router.patch("/query-registry/{query_hash}/tag")
async def update_query_tag(
    query_hash: str, request: UpdateTagRequest
) -> UpdateTagResponse:
    """Update the tag/name of a query in the registry."""
    try:
        from ...query_registry import QueryRegistry

        registry = QueryRegistry()
        registry.load()

        updated = registry.update_query_tag(query_hash, request.tag)

        if updated:
            return UpdateTagResponse(success=True)
        else:
            return UpdateTagResponse(success=False, error="Query not found")

    except Exception as e:
        return UpdateTagResponse(success=False, error=str(e))


# ============================================================================
# Benchmark/Run endpoint
# ============================================================================


class BenchmarkQueryInput(BaseModel):
    """A query to benchmark - either by identifier or raw SQL."""

    identifier: Optional[str] = None  # Query name or hash (for stats tracking)
    sql: Optional[str] = None  # Raw SQL to execute (if provided, skips registry lookup)


class BenchmarkRequest(BaseModel):
    """Request to run benchmark on queries."""

    queries: list[
        Union[str, BenchmarkQueryInput]
    ]  # Query names/hashes OR BenchmarkQueryInput objects
    mode: Literal["interval", "concurrency"] = "interval"
    interval_ms: Optional[int] = 100  # For interval mode
    concurrency: Optional[int] = 1  # For concurrency mode
    duration_seconds: Optional[int] = 30
    max_count: Optional[int] = None


class QueryBenchmarkStats(BaseModel):
    """Statistics for a single query."""

    query_name: str
    query_hash: str
    executions: int
    successes: int
    failures: int
    min_ms: float
    avg_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    last_error: Optional[str] = None


class BenchmarkProgress(BaseModel):
    """Progress update during benchmark."""

    type: Literal["progress", "complete", "error"]
    elapsed_seconds: float
    total_executions: int
    total_successes: int
    total_failures: int
    qps: float
    queries: list[QueryBenchmarkStats]
    error: Optional[str] = None


def _progress_to_sse(progress: Any) -> dict:
    """Convert progress to SSE event format for EventSourceResponse.

    Uses event names matching the analyze endpoint pattern:
    - 'progress' for ongoing updates
    - 'complete' for successful completion
    - 'error' for failures
    """
    # Map progress.type to SSE event name (same pattern as analyze.py)
    event_name = progress.type
    if event_name not in ("progress", "complete", "error"):
        event_name = "unknown"

    if isinstance(progress, BaseModel):
        payload = progress.model_dump_json()
    else:
        payload = json.dumps(
            {
                "type": progress.type,
                "elapsed_seconds": progress.elapsed_seconds,
                "total_executions": progress.total_executions,
                "total_successes": progress.total_successes,
                "total_failures": progress.total_failures,
                "qps": progress.qps,
                "queries": [q.__dict__ for q in progress.queries],
                "error": progress.error,
            }
        )
    return {
        "event": event_name,
        "data": payload,
    }


async def _benchmark_generator(
    queries, target, mode, interval_ms, concurrency, duration_seconds, max_count,
) -> AsyncGenerator[dict, None]:
    """Async generator that yields SSE events from query service."""
    from ...services.query_service import QueryService

    service = QueryService()
    async for progress in service.stream_benchmark(
        queries=queries,
        target=target,
        mode=mode,
        interval_ms=interval_ms,
        concurrency=concurrency,
        duration_seconds=duration_seconds,
        max_count=max_count,
    ):
        yield _progress_to_sse(progress)


@router.post("/query-registry/benchmark")
async def run_benchmark(request: BenchmarkRequest, guard: TargetGuard = Depends(require_target_body)):
    """
    Run benchmark on queries with live progress updates via SSE.

    Returns Server-Sent Events with progress updates during execution.
    """
    return EventSourceResponse(_benchmark_generator(
        queries=request.queries,
        target=guard.target_name,
        mode=request.mode,
        interval_ms=request.interval_ms or 100,
        concurrency=request.concurrency or 1,
        duration_seconds=request.duration_seconds or 30,
        max_count=request.max_count,
    ))

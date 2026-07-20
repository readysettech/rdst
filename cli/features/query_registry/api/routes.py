from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Any, Optional, Literal, AsyncGenerator, Union
from datetime import datetime
from sse_starlette.sse import EventSourceResponse
import json
import asyncio
import logging
import time

from shared.api.target_guard import TargetGuard, require_target_body

logger = logging.getLogger(__name__)

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
    first_analyzed: Optional[str] = None
    max_duration_ms: float = 0.0
    avg_duration_ms: float = 0.0
    observation_count: int = 0
    original_sql: str = ""
    question: str = ""


class QueryRegistryResponse(BaseModel):
    queries: list[QueryRegistryEntry]
    total: int
    limit: Optional[int] = None
    offset: int = 0
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
async def get_query_registry(
    limit: Optional[int] = 200, offset: int = 0
) -> QueryRegistryResponse:
    """Get queries from the shared query registry."""
    try:
        from shared.query_registry import QueryRegistry

        registry = QueryRegistry()
        registry.load()

        all_queries = registry.list_queries(limit=None)
        total = len(all_queries)

        if offset < 0:
            offset = 0
        if limit is not None and limit < 0:
            limit = None

        if offset > 0:
            all_queries = all_queries[offset:]
        if limit:
            queries = all_queries[:limit]
        else:
            queries = all_queries

        entries: list[QueryRegistryEntry] = []
        for q in queries:
            try:
                entries.append(
                    QueryRegistryEntry(
                        sql=q.sql,
                        hash=q.hash,
                        tag=q.tag,
                        last_analyzed=q.last_analyzed,
                        target=q.last_target,
                        frequency=q.frequency,
                        source=q.source,
                        most_recent_params=q.most_recent_params,
                        first_analyzed=q.first_analyzed,
                        max_duration_ms=q.max_duration_ms,
                        avg_duration_ms=q.avg_duration_ms,
                        observation_count=q.observation_count,
                        original_sql=q.original_sql,
                        question=q.question,
                    )
                )
            except Exception:
                # A single malformed entry must not blank the whole listing.
                logger.warning("Skipping malformed registry entry %s", q.hash)

        return QueryRegistryResponse(
            queries=entries,
            total=total,
            limit=limit,
            offset=offset,
        )

    except Exception as e:
        return QueryRegistryResponse(
            queries=[], total=0, limit=limit, offset=offset, error=str(e)
        )


@router.post("/query-registry")
async def add_query_to_registry(request: AddQueryRequest) -> AddQueryResponse:
    """Add a query to the registry."""
    try:
        from shared.query_registry import QueryRegistry

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
        from shared.query_registry import QueryRegistry

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
        from shared.query_registry import QueryRegistry

        registry = QueryRegistry()
        registry.load()

        updated = registry.update_query_tag(query_hash, request.tag)

        if updated:
            return UpdateTagResponse(success=True)
        else:
            return UpdateTagResponse(success=False, error="Query not found")

    except Exception as e:
        return UpdateTagResponse(success=False, error=str(e))


class UpdateSqlRequest(BaseModel):
    sql: str


class UpdateSqlResponse(BaseModel):
    success: bool
    hash: Optional[str] = None
    hash_changed: bool = False
    error: Optional[str] = None


@router.patch("/query-registry/{query_hash}/sql")
async def update_query_sql(
    query_hash: str, request: UpdateSqlRequest
) -> UpdateSqlResponse:
    """Replace a query's SQL, preserving its tag. The hash changes with the SQL."""
    try:
        from shared.query_registry import QueryRegistry

        registry = QueryRegistry()
        registry.load()

        entry = registry.get_query(query_hash)
        if not entry:
            return UpdateSqlResponse(success=False, error="Query not found")

        new_hash, _ = registry.add_query(
            sql=request.sql,
            tag=entry.tag,
            source=entry.source,
            target=entry.last_target,
        )
        if new_hash != query_hash:
            registry.remove_query(query_hash)

        return UpdateSqlResponse(
            success=True, hash=new_hash, hash_changed=new_hash != query_hash,
        )
    except Exception as e:
        return UpdateSqlResponse(success=False, error=str(e))


class ImportQueriesRequest(BaseModel):
    file: str
    update: bool = False
    target: Optional[str] = None


class ImportQueriesResponse(BaseModel):
    success: bool
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = []
    message: str = ""


@router.post("/query-registry/import")
async def import_queries(request: ImportQueriesRequest) -> ImportQueriesResponse:
    """Import queries from a local SQL file (semicolon-separated, with
    optional `-- name:` / `-- target:` metadata comments)."""
    from ..models import QueryCommandInput
    from ..service import QueryService

    service = QueryService()
    payload = None
    error_message = None
    async for event in service.execute(
        QueryCommandInput(
            subcommand="import",
            kwargs={
                "file": request.file,
                "update": request.update,
                "target": request.target,
            },
        )
    ):
        if event.type == "complete":
            payload = event.result
        elif event.type == "error":
            error_message = event.message

    if payload is None:
        return ImportQueriesResponse(
            success=False, message=error_message or "Import failed",
        )

    data = payload.get("data") or {}
    return ImportQueriesResponse(
        success=bool(payload.get("ok")),
        imported=data.get("imported", 0),
        updated=data.get("updated", 0),
        skipped=data.get("skipped", 0),
        errors=data.get("errors", []),
        message=payload.get("message", ""),
    )


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
    target: Optional[str] = None
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
    event_name = progress.type
    if event_name not in ("progress", "complete", "error"):
        event_name = "unknown"

    if isinstance(progress, BaseModel):
        payload = progress.model_dump_json()
    elif event_name == "error":
        # Shared error envelope {code, message, detail} (B7/T24) so the client
        # normalizes a benchmark failure like every other SSE error.
        payload = json.dumps(
            {
                "type": "error",
                "code": getattr(progress, "code", None) or "error",
                "message": getattr(progress, "message", None)
                or "The benchmark could not be completed.",
                "detail": getattr(progress, "detail", None),
            }
        )
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
    from ..service import QueryService

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

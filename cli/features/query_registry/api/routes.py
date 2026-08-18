from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from typing import Any, Optional, Literal, AsyncGenerator, Union
from datetime import datetime
from sse_starlette.sse import EventSourceResponse
import json
import asyncio
import hashlib
import logging
import os
import threading
import time
import uuid

from shared.api.target_guard import TargetGuard, require_target, require_target_body
from .. import read_model
from ..discovery import BOOKMARK_INTERVAL_SECONDS, query_discovery

logger = logging.getLogger(__name__)

router = APIRouter()

# Bound the browser's reconnect backoff to a known value (research 2b);
# reconnects are cheap because Last-Event-ID resumes from the durable seq.
SSE_RETRY_MILLISECONDS = 5000

_read_model_store_lock = threading.Lock()
_read_model_store_factory = None
_read_model_store_instance = None
_read_model_store_path = None


def _reset_read_model_store_cache() -> None:
    """Deterministically invalidate the process cache (tests/app teardown)."""
    global _read_model_store_factory, _read_model_store_instance
    global _read_model_store_path
    with _read_model_store_lock:
        _read_model_store_factory = None
        _read_model_store_instance = None
        _read_model_store_path = None


def _cached_read_model_store():
    """Reuse the concurrency-safe store instead of checking it per page.

    Tests replace ``shared.query_registry.QueryRegistry`` with a fixture
    factory; comparing the factory identity naturally invalidates this small
    process-local cache without a production reset hook.
    """
    from shared.query_registry import QueryRegistry
    from shared.query_registry.library_store import default_library_db_path

    global _read_model_store_factory, _read_model_store_instance
    global _read_model_store_path
    store_path = os.path.realpath(default_library_db_path())
    with _read_model_store_lock:
        if (
            _read_model_store_factory is not QueryRegistry
            or _read_model_store_path != store_path
        ):
            registry = QueryRegistry()
            _read_model_store_factory = QueryRegistry
            _read_model_store_instance = registry.library_store
            _read_model_store_path = store_path
        return _read_model_store_instance


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
    # Readyset cache identity, populated once a query has been cache-checked.
    # Empty for queries never touched by caching (the Queries workbench reads
    # these to show per-query cacheability/cache status without SQL-text
    # matching against SHOW CACHES). rdst-41p.1.
    readyset_query_id: str = ""
    readyset_supported: str = ""
    last_cache_target: str = ""
    readyset_last_observed_at: str = ""
    # Target-scoped lifecycle read model used by the unified Query Library.
    first_observed_at: str = ""
    last_observed_at: str = ""
    reviewed_at: str = ""
    saved_at: str = ""
    last_analyzed_at: str = ""
    analysis_count: int = 0
    last_compared_at: str = ""
    comparison_count: int = 0
    sources: list[str] = Field(default_factory=list)
    is_new: bool = False


class QueryRegistryResponse(BaseModel):
    queries: list[QueryRegistryEntry]
    total: int
    limit: Optional[int] = None
    offset: int = 0
    error: Optional[str] = None


class QueryLibraryFacetCounts(BaseModel):
    """Per-facet counts over the full filtered set, never a single page."""

    view: dict[str, int]
    source: dict[str, int]
    params: dict[str, int]
    activity: dict[str, int]
    impact: dict[str, int]


class QueryLibraryFreshness(BaseModel):
    """Collector state for the target, when the observation store has one."""

    state: str = ""
    last_success_at: Optional[str] = None
    epoch_id: Optional[str] = None


class QueryLibraryResponse(BaseModel):
    """Read-model response for GET /api/query-registry library params."""

    queries: list[QueryRegistryEntry]
    facet_counts: QueryLibraryFacetCounts
    next_cursor: Optional[str] = None
    total: int
    freshness: Optional[QueryLibraryFreshness] = None
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


class MarkQueryReviewedRequest(BaseModel):
    target: str


class MarkQueryReviewedResponse(BaseModel):
    success: bool
    error: Optional[str] = None


class QueryAnalysisSummary(BaseModel):
    """Compact record of one stored analysis run for a query."""

    analysis_id: str
    analyzed_at: str
    target: str
    overall_rating: str = ""
    efficiency_score: Optional[float] = None


class LatestAnalysisResponse(BaseModel):
    """Latest stored analysis summary for one query hash, when any exists."""

    found: bool = False
    analysis: Optional[QueryAnalysisSummary] = None
    error: Optional[str] = None


@router.get("/query-registry/discovery/stream")
async def stream_query_discovery(
    request: Request,
    guard: TargetGuard = Depends(require_target),
    cursor: Optional[int] = Query(
        None,
        ge=0,
        description="Last processed discovery cursor for reconnect reconciliation",
    ),
) -> EventSourceResponse:
    """Stream automatic Query Library discovery updates for one target.

    This web/desktop-only endpoint opts into the background collector. Existing
    CLI commands and the legacy top endpoints keep their current behavior.
    """
    header_cursor = request.headers.get("last-event-id")
    after_cursor = cursor
    if after_cursor is None and header_cursor:
        try:
            after_cursor = max(int(header_cursor), 0)
        except ValueError:
            after_cursor = None

    collector = query_discovery.collector_for(guard.target_name)

    async def event_stream() -> AsyncGenerator[dict, None]:
        yield {"retry": SSE_RETRY_MILLISECONDS}
        async for event in collector.subscribe(after_cursor):
            if await request.is_disconnected():
                break
            yield event.to_sse()

    # Bookmarks are the keepalive; the transport's comment ping only
    # backstops a stalled subscriber loop at the same cadence.
    return EventSourceResponse(event_stream(), ping=BOOKMARK_INTERVAL_SECONDS)


def _to_registry_entry(q, target: Optional[str]) -> QueryRegistryEntry:
    """Map one registry row to its API entry for the requested target."""
    entry_target = target or q.home_target
    lifecycle = q.lifecycle_for(entry_target)
    return QueryRegistryEntry(
        sql=q.sql,
        hash=q.hash,
        tag=q.tag,
        last_analyzed=q.last_analyzed,
        # Clients treat target as the DB a query belongs to; the
        # mutable last_target alone leaked queries into the wrong
        # per-DB list (rdst-e7s.31).
        target=entry_target,
        frequency=q.frequency,
        source=q.source,
        most_recent_params=q.most_recent_params,
        first_analyzed=q.first_analyzed,
        max_duration_ms=q.max_duration_ms,
        avg_duration_ms=q.avg_duration_ms,
        observation_count=q.observation_count,
        original_sql=q.original_sql,
        question=q.question,
        readyset_query_id=q.readyset_query_id,
        readyset_supported=q.readyset_supported,
        last_cache_target=q.last_cache_target,
        readyset_last_observed_at=q.readyset_last_observed_at,
        first_observed_at=lifecycle.first_observed_at if lifecycle else "",
        last_observed_at=lifecycle.last_observed_at if lifecycle else "",
        reviewed_at=lifecycle.reviewed_at if lifecycle else "",
        saved_at=lifecycle.saved_at if lifecycle else "",
        last_analyzed_at=lifecycle.last_analyzed_at if lifecycle else "",
        analysis_count=lifecycle.analysis_count if lifecycle else 0,
        last_compared_at=lifecycle.last_compared_at if lifecycle else "",
        comparison_count=lifecycle.comparison_count if lifecycle else 0,
        sources=(
            list(lifecycle.sources)
            if lifecycle
            else ([q.source] if q.source else [])
        ),
        is_new=q.is_new_for(entry_target),
    )


def _target_scoped_queries(target: Optional[str]) -> list:
    """One registry load per request, scoped to the requested target."""
    from shared.query_registry import QueryRegistry

    registry = QueryRegistry()
    registry.load()
    all_queries = registry.list_queries(limit=None)
    if target:
        # Scope to the selected database by the query's home target. Without
        # this the list leaks cross-dialect queries, e.g. a Postgres query
        # shown and cached against a MySQL target.
        all_queries = [q for q in all_queries if q.belongs_to_target(target)]
    return all_queries


# Ceiling for one read-model page; the cursor covers anything longer.
_LIBRARY_MAX_PAGE = 500


def _library_read_model(
    *,
    target: Optional[str],
    search: Optional[str],
    view: Optional[str],
    source: Optional[str],
    params: Optional[str],
    activity: Optional[str],
    impact: Optional[str],
    sort: Optional[str],
    cursor: Optional[str],
    limit: Optional[int],
) -> QueryLibraryResponse:
    """Full-set filtering, facets, and keyset pagination for the Query Library."""
    resolved = {
        "target": target or "",
        "search": search or "",
        "view": view or "all",
        "source": source or "all",
        "params": params or "all",
        "activity": activity or "all",
        "impact": impact or "all",
        "sort": sort or "highest-impact",
    }
    spec = read_model.spec_hash(**resolved)

    cursor_position = None
    if cursor is not None:
        try:
            cursor_position = read_model.decode_cursor(cursor, spec)
        except read_model.CursorError as exc:
            # The client treats this as "restart from page 1".
            raise HTTPException(
                status_code=400,
                detail={"code": "cursor_invalid", "message": str(exc)},
            ) from exc

    page_size = limit if limit is not None and limit > 0 else 50
    page_size = min(page_size, _LIBRARY_MAX_PAGE)
    try:
        from shared.query_registry import QueryRegistry
        from shared.query_registry.query_registry import QueryEntry

        store = _cached_read_model_store()
        if target and store is not None:
            stored_page, facet_counts, total, next_position = store.query_library(
                target=target,
                search=resolved["search"],
                view=resolved["view"],
                source=resolved["source"],
                params=resolved["params"],
                activity=resolved["activity"],
                impact=resolved["impact"],
                sort=resolved["sort"],
                cursor_position=cursor_position,
                limit=page_size,
                now_ms=time.time() * 1000.0,
            )
            page = []
            for entry in stored_page:
                try:
                    page.append(_to_registry_entry(QueryEntry.from_dict(entry), target))
                except Exception:
                    logger.warning(
                        "Skipping malformed registry entry %s", entry.get("hash")
                    )
        else:
            # Compatibility path for targetless callers and the temporary
            # RDST_REGISTRY_SQLITE=0 rollback gate. The normal web Query
            # Library always supplies a target and never enters this O(N)
            # branch.
            entries: list[QueryRegistryEntry] = []
            for q in _target_scoped_queries(target):
                try:
                    entries.append(_to_registry_entry(q, target))
                except Exception:
                    logger.warning("Skipping malformed registry entry %s", q.hash)
            selected, facet_counts = read_model.select_library(
                entries,
                search=resolved["search"],
                view=resolved["view"],
                source=resolved["source"],
                params=resolved["params"],
                activity=resolved["activity"],
                impact=resolved["impact"],
                sort=resolved["sort"],
            )
            page, next_position = read_model.paginate(
                selected, resolved["sort"], cursor_position, page_size
            )
            total = len(selected)
    except Exception as e:
        return QueryLibraryResponse(
            queries=[],
            facet_counts=QueryLibraryFacetCounts(**read_model.empty_facet_counts()),
            total=0,
            error=str(e),
        )

    next_cursor = (
        read_model.encode_cursor(next_position[0], next_position[1], spec)
        if next_position
        else None
    )

    freshness = read_model.collector_freshness(target)
    return QueryLibraryResponse(
        queries=page,
        facet_counts=QueryLibraryFacetCounts(**facet_counts),
        next_cursor=next_cursor,
        total=total,
        freshness=QueryLibraryFreshness(**freshness) if freshness else None,
    )


@router.get("/query-registry")
async def get_query_registry(
    limit: Optional[int] = 200,
    offset: int = 0,
    target: Optional[str] = None,
    search: Optional[str] = None,
    view: Optional[read_model.ViewName] = None,
    source: Optional[read_model.SourceName] = None,
    params: Optional[read_model.ParamsName] = None,
    activity: Optional[read_model.ActivityName] = None,
    impact: Optional[read_model.ImpactName] = None,
    sort: Optional[read_model.SortName] = None,
    cursor: Optional[str] = None,
) -> Union[QueryLibraryResponse, QueryRegistryResponse]:
    """Get queries from the shared query registry, optionally scoped to a target.

    Passing any Query Library read-model parameter (search, view, source,
    params, activity, impact, sort, cursor) switches the response to
    {queries, facet_counts, next_cursor, total, freshness}, computed over the
    full target-scoped set with keyset pagination. Without them the legacy
    limit/offset contract is unchanged.
    """
    if any(
        value is not None
        for value in (search, view, source, params, activity, impact, sort, cursor)
    ):
        return _library_read_model(
            target=target,
            search=search,
            view=view,
            source=source,
            params=params,
            activity=activity,
            impact=impact,
            sort=sort,
            cursor=cursor,
            limit=limit,
        )

    try:
        all_queries = _target_scoped_queries(target)
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
                entries.append(_to_registry_entry(q, target))
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


@router.post("/query-registry/{query_hash}/reviewed")
async def mark_query_reviewed(
    query_hash: str,
    request: MarkQueryReviewedRequest,
) -> MarkQueryReviewedResponse:
    """Mark a query reviewed for exactly one target."""
    try:
        from shared.query_registry import QueryRegistry

        registry = QueryRegistry()
        registry.load()
        updated = registry.mark_reviewed(query_hash, target=request.target)
        if not updated:
            return MarkQueryReviewedResponse(success=False, error="Query not found")
        return MarkQueryReviewedResponse(success=True)
    except Exception as e:
        return MarkQueryReviewedResponse(success=False, error=str(e))


@router.get("/query-registry/{query_hash}/analysis/latest")
async def get_latest_query_analysis(query_hash: str) -> LatestAnalysisResponse:
    """Get the most recent stored analysis summary for a query.

    Detail-level companion to the library listing: the list rides only the
    lifecycle timestamps, and the expanded query detail fetches this compact
    outcome on demand.
    """
    try:
        from shared.query_registry import (
            AnalysisResultsRegistry,
            extract_performance_assessment,
        )

        result = AnalysisResultsRegistry().get_latest_analysis(query_hash)
        if result is None:
            return LatestAnalysisResponse(found=False)

        assessment = extract_performance_assessment(result.llm_analysis or {})
        rating = assessment.get("overall_rating")
        score = assessment.get("efficiency_score")
        return LatestAnalysisResponse(
            found=True,
            analysis=QueryAnalysisSummary(
                analysis_id=result.analysis_id,
                analyzed_at=result.timestamp,
                target=result.target,
                overall_rating=rating if isinstance(rating, str) else "",
                efficiency_score=(
                    float(score)
                    if isinstance(score, (int, float)) and not isinstance(score, bool)
                    else None
                ),
            ),
        )
    except Exception as e:
        return LatestAnalysisResponse(found=False, error=str(e))


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


class LoadTestRunStartResponse(BaseModel):
    run_id: str


def _benchmark_request_fingerprint(request: BenchmarkRequest) -> str:
    """Identify the exact password-free load-test specification."""
    payload = json.dumps(
        request.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _benchmark_request_metadata(request: BenchmarkRequest) -> dict[str, Any]:
    """Keep reload metadata useful without retaining concrete SQL text."""
    payload = request.model_dump(mode="json")
    payload["queries"] = [
        ({**query, "sql": None} if isinstance(query, dict) else query)
        for query in payload["queries"]
    ]
    return payload


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
    """Compatibility SSE path, protected by a measurement reservation."""
    from ..service import QueryService
    from shared.deploy.sandbox_manager import sandbox_manager

    service = QueryService()
    async with sandbox_manager.reserve_measurement(
        owner_id=f"request-{uuid.uuid4().hex[:12]}",
        purpose="load_test",
    ):
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
        interval_ms=100 if request.interval_ms is None else request.interval_ms,
        concurrency=1 if request.concurrency is None else request.concurrency,
        duration_seconds=(
            30 if request.duration_seconds is None else request.duration_seconds
        ),
        max_count=request.max_count,
    ))


@router.post(
    "/query-registry/load-test-runs",
    response_model=LoadTestRunStartResponse,
    summary="Start Benchmark Run",
)
async def start_load_test_run(
    request: BenchmarkRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> LoadTestRunStartResponse:
    """Start or attach to the target's detached origin-only benchmark."""
    from shared.deploy.sandbox_manager import sandbox_manager
    from shared.run_registry import run_registry
    from ..service import QueryService

    metadata = {
        "request_fingerprint": _benchmark_request_fingerprint(request),
        "query_count": len(request.queries),
        # Keep identifiers and run settings for reload summaries, but do not
        # retain concrete user SQL in process-level run metadata.
        "request": _benchmark_request_metadata(request),
    }
    existing = run_registry.find_active_matching(
        "load_test",
        guard.target_name,
        metadata,
        keys=("request_fingerprint",),
    )
    if existing is not None:
        return LoadTestRunStartResponse(run_id=existing)

    async def factory(owner_id: str):
        async with sandbox_manager.reserve_measurement(
            owner_id=owner_id,
            purpose="load_test",
        ):
            async for event in QueryService().stream_benchmark(
                queries=request.queries,
                target=guard.target_name,
                mode=request.mode,
                interval_ms=(
                    request.interval_ms
                    if request.interval_ms is not None
                    else 100
                ),
                concurrency=(
                    request.concurrency
                    if request.concurrency is not None
                    else 1
                ),
                duration_seconds=(
                    request.duration_seconds
                    if request.duration_seconds is not None
                    else 30
                ),
                max_count=request.max_count,
            ):
                yield event

    run_id = run_registry.start_factory(
        "load_test",
        guard.target_name,
        factory,
        metadata=metadata,
    )
    return LoadTestRunStartResponse(run_id=run_id)

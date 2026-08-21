from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, StrictBool, StrictFloat, StrictInt, StrictStr
from typing import Any, Optional, Literal, AsyncGenerator, Union
from datetime import datetime, timezone
from sse_starlette.sse import EventSourceResponse
import json
import asyncio
import hashlib
import logging
import os
import threading
import time
import uuid

from shared.api.guards import require_local_request
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


class LastCompareOutcome(BaseModel):
    """What the query's most recent Compare run found, as stored.

    Written by the compare-outcome endpoint and read back here, so a
    measurement survives the browser that took it. Fields the run did not
    measure come back null.
    """

    status: str
    at: str = ""
    readyset_ms: Optional[float] = None
    origin_ms: Optional[float] = None
    detail: Optional[str] = None


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
    # The user's star, stored as saved_at. The pair is what the library
    # renders; saved_at above stays for clients that already read it.
    starred: bool = False
    starred_at: str = ""
    last_compare: Optional[LastCompareOutcome] = None


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


class AnalysisHistoryEntry(BaseModel):
    """One entry in a query's bounded analysis history."""

    analysis_id: str
    created_at: str
    target: str
    overall_rating: str = ""
    efficiency_score: Optional[float] = None


class AnalysisHistoryResponse(BaseModel):
    """A query's stored analyses, newest first."""

    hash: str
    analyses: list[AnalysisHistoryEntry]


class StoredAnalysisResponse(BaseModel):
    """One stored analysis, whole, for read-only redisplay."""

    hash: str
    analysis_id: str
    created_at: str
    target: str
    overall_rating: str = ""
    efficiency_score: Optional[float] = None
    analysis: dict


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


def _compare_measurement(value: Any) -> Optional[float]:
    """A measured millisecond value, or None for anything unmeasured."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _last_compare(lifecycle) -> Optional[LastCompareOutcome]:
    """The stored Compare outcome for one target, when a run recorded one."""
    outcome = getattr(lifecycle, "last_compare", None)
    if not isinstance(outcome, dict) or not outcome.get("status"):
        return None
    detail = outcome.get("detail")
    return LastCompareOutcome(
        status=str(outcome["status"]),
        at=str(outcome.get("at") or ""),
        readyset_ms=_compare_measurement(outcome.get("readyset_ms")),
        origin_ms=_compare_measurement(outcome.get("origin_ms")),
        detail=str(detail) if detail is not None else None,
    )


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
        starred=bool(lifecycle and lifecycle.saved_at),
        starred_at=lifecycle.saved_at if lifecycle else "",
        last_compare=_last_compare(lifecycle),
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
    starred: Optional[bool],
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
        "starred": starred,
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
                starred=resolved["starred"],
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
                starred=resolved["starred"],
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
    starred: Optional[bool] = Query(
        None,
        description=(
            "Restrict to starred queries (1) or unstarred ones (0). "
            "Omit for both."
        ),
    ),
    source: Optional[read_model.SourceName] = None,
    params: Optional[read_model.ParamsName] = None,
    activity: Optional[read_model.ActivityName] = None,
    impact: Optional[read_model.ImpactName] = None,
    sort: Optional[read_model.SortName] = None,
    cursor: Optional[str] = None,
) -> Union[QueryLibraryResponse, QueryRegistryResponse]:
    """Get queries from the shared query registry, optionally scoped to a target.

    Passing any Query Library read-model parameter (search, view, starred,
    source, params, activity, impact, sort, cursor) switches the response to
    {queries, facet_counts, next_cursor, total, freshness}, computed over the
    full target-scoped set with keyset pagination. Without them the legacy
    limit/offset contract is unchanged.

    ``starred`` is a facet of its own rather than a value of ``view``, so a
    shortlist composes with every other filter ("starred and not yet
    analyzed"). ``view=saved`` selects the same rows and keeps working.
    """
    if any(
        value is not None
        for value in (
            search, view, starred, source, params, activity, impact, sort, cursor,
        )
    ):
        return _library_read_model(
            target=target,
            search=search,
            view=view,
            starred=starred,
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
    """Add a query to the registry.

    Typing a query into the Add query dialog is the one place a person
    hands RDST a query to keep, so the new entry arrives starred.
    """
    try:
        from shared.query_registry import QueryRegistry

        registry = QueryRegistry()
        registry.load()

        query_hash, _ = registry.add_query(
            sql=request.sql,
            source="web",
            target=request.target or "",
            save_intent=True,
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


class SetQueryStarredRequest(BaseModel):
    """Whether the user wants this query starred on one target."""

    starred: StrictBool
    target: StrictStr = ""


class SetQueryStarredResponse(BaseModel):
    hash: str
    target: str
    starred: bool
    starred_at: str


@router.patch("/query-registry/queries/{query_hash}/starred")
async def set_query_starred(
    query_hash: str,
    request: SetQueryStarredRequest,
    http_request: Request,
) -> SetQueryStarredResponse:
    """Star or unstar a query for one target.

    The star is the library's only user-authored mark: nothing RDST does on
    its own sets it, and this is the one place it is cleared. Starring a
    query that already carries a star keeps the moment it was first
    starred, so the toggle is idempotent.
    """
    require_local_request(http_request)
    from shared.query_registry import QueryRegistry

    registry = QueryRegistry()
    registry.load()
    entry = registry.get_query(query_hash)
    if entry is None:
        raise HTTPException(status_code=404, detail="Query not found")

    target = request.target or entry.home_target
    lifecycle = entry.lifecycle_for(target, create=request.starred)
    if lifecycle is None:
        # The query has no history on this target, so there is no star there
        # to clear and nothing to write.
        return SetQueryStarredResponse(
            hash=entry.hash, target=target, starred=False, starred_at="",
        )

    if request.starred:
        lifecycle.saved_at = lifecycle.saved_at or (
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
    else:
        lifecycle.saved_at = ""
    registry.save()

    return SetQueryStarredResponse(
        hash=entry.hash,
        target=target,
        starred=bool(lifecycle.saved_at),
        starred_at=lifecycle.saved_at,
    )


def _analysis_assessment(llm_analysis: Any) -> tuple[str, Optional[float]]:
    """Return the (rating, score) pair the compact summary shows."""
    from shared.query_registry import extract_performance_assessment

    assessment = extract_performance_assessment(
        llm_analysis if isinstance(llm_analysis, dict) else {}
    )
    rating = assessment.get("overall_rating")
    score = assessment.get("efficiency_score")
    return (
        rating if isinstance(rating, str) else "",
        float(score)
        if isinstance(score, (int, float)) and not isinstance(score, bool)
        else None,
    )


@router.get("/query-registry/{query_hash}/analysis/latest")
async def get_latest_query_analysis(query_hash: str) -> LatestAnalysisResponse:
    """Get the most recent stored analysis summary for a query.

    Detail-level companion to the library listing: the list rides only the
    lifecycle timestamps, and the expanded query detail fetches this compact
    outcome on demand.
    """
    try:
        from shared.query_registry import AnalysisResultsRegistry

        result = AnalysisResultsRegistry().get_latest_analysis(query_hash)
        if result is None:
            return LatestAnalysisResponse(found=False)

        rating, score = _analysis_assessment(result.llm_analysis)
        return LatestAnalysisResponse(
            found=True,
            analysis=QueryAnalysisSummary(
                analysis_id=result.analysis_id,
                analyzed_at=result.timestamp,
                target=result.target,
                overall_rating=rating,
                efficiency_score=score,
            ),
        )
    except Exception as e:
        return LatestAnalysisResponse(found=False, error=str(e))


@router.get("/query-registry/{query_hash}/analyses")
async def list_query_analyses(query_hash: str) -> AnalysisHistoryResponse:
    """List a query's stored analyses, newest first.

    The bounded history behind the results viewer: enough of each run to
    choose one, with the body left to the per-analysis route. A query that
    was never analyzed has an empty history rather than an error.
    """
    from shared.query_registry import AnalysisResultsRegistry

    entries = []
    for result in AnalysisResultsRegistry().get_all_analyses_for_query(query_hash):
        rating, score = _analysis_assessment(result.llm_analysis)
        entries.append(
            AnalysisHistoryEntry(
                analysis_id=result.analysis_id,
                created_at=result.timestamp,
                target=result.target,
                overall_rating=rating,
                efficiency_score=score,
            )
        )
    return AnalysisHistoryResponse(hash=query_hash, analyses=entries)


@router.get("/query-registry/{query_hash}/analysis/{analysis_id}")
async def get_stored_query_analysis(
    query_hash: str, analysis_id: str
) -> StoredAnalysisResponse:
    """Return one stored analysis whole, so it can be reopened without a re-run.

    The body is served exactly as it was stored, including any field a
    newer build wrote, and ``display_payload`` carries the finished run in
    the shape the results view already renders.
    """
    from shared.query_registry import AnalysisResultsRegistry

    stored = AnalysisResultsRegistry().get_stored_payload(query_hash, analysis_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Analysis not found")

    rating, score = _analysis_assessment(stored.get("llm_analysis"))
    return StoredAnalysisResponse(
        hash=query_hash,
        analysis_id=str(stored.get("analysis_id") or analysis_id),
        created_at=str(stored.get("timestamp") or ""),
        target=str(stored.get("target") or ""),
        overall_rating=rating,
        efficiency_score=score,
        analysis=stored,
    )


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


class UpdateParametersRequest(BaseModel):
    """Concrete values for one query's placeholders, keyed by parameter name."""

    values: dict[str, Union[StrictStr, StrictInt, StrictFloat]] = Field(min_length=1)
    source: Literal["user", "suggested"] = "user"


class UpdateParametersResponse(BaseModel):
    hash: str
    parameters: dict


@router.patch("/query-registry/queries/{query_hash}/parameters")
async def update_query_parameters(
    query_hash: str,
    request: UpdateParametersRequest,
    http_request: Request,
) -> UpdateParametersResponse:
    """Store values for a query's placeholders and return what was stored.

    The values land in both parameter fields, so they show as the query's
    most recent values and also substitute into its placeholders when it is
    next run.
    """
    require_local_request(http_request)
    from shared.query_registry import QueryRegistry

    registry = QueryRegistry()
    registry.load()
    updated = registry.update_parameter_history(
        query_hash, request.values, source=request.source
    )
    entry = registry.get_query(query_hash) if updated else None
    if entry is None:
        raise HTTPException(status_code=404, detail="Query not found")
    return UpdateParametersResponse(hash=query_hash, parameters=entry.parameters)


class CompareOutcomeRequest(BaseModel):
    """What one Compare run found for a query on one target."""

    target: StrictStr = ""
    status: Literal[
        "improved", "regressed", "equivalent", "not_comparable", "error"
    ]
    readyset_ms: Optional[float] = Field(default=None, ge=0)
    origin_ms: Optional[float] = Field(default=None, ge=0)
    detail: Optional[str] = Field(default=None, max_length=500)
    # Readyset's own verdict on the query, when the run reached the EXPLAIN
    # CREATE CACHE that decides it.
    readyset_supported: Optional[Literal["yes", "no", "pending"]] = None
    unsupported_reason: Optional[str] = Field(default=None, max_length=500)


class CompareOutcomeResponse(BaseModel):
    hash: str
    target: str
    comparison_count: int
    last_compared_at: str
    last_compare: dict
    readyset_supported: str


def _readyset_support_value(
    verdict: str, reason: Optional[str], current: str
) -> str:
    """Map a compare run's verdict onto the stored readyset_supported text.

    The column's vocabulary predates this endpoint, so the spelling comes from
    the cache feature that owns it rather than from a second copy here.
    """
    from features.cache.readyset_explain_cache import readyset_support_text

    if not verdict:
        return current
    return readyset_support_text(verdict, reason or "")


@router.post("/query-registry/queries/{query_hash}/compare-outcome")
async def record_compare_outcome(
    query_hash: str,
    request: CompareOutcomeRequest,
    http_request: Request,
) -> CompareOutcomeResponse:
    """Record what comparing a query against Readyset found.

    Compare is the measurement the Query Library is for, so its result
    belongs beside the query rather than in one browser: the target's
    lifecycle counts the run, and the latest outcome stays readable for the
    next person who opens the query.
    """
    require_local_request(http_request)
    from shared.query_registry import QueryRegistry

    registry = QueryRegistry()
    registry.load()
    entry = registry.get_query(query_hash)
    if entry is None:
        raise HTTPException(status_code=404, detail="Query not found")

    target = request.target or entry.home_target
    lifecycle = entry.lifecycle_for(target, create=True)
    recorded_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    outcome: dict[str, Any] = {"status": request.status, "at": recorded_at}
    for key, value in (
        ("readyset_ms", request.readyset_ms),
        ("origin_ms", request.origin_ms),
        ("detail", request.detail),
    ):
        if value is not None:
            outcome[key] = value
    lifecycle.comparison_count += 1
    lifecycle.last_compared_at = recorded_at
    lifecycle.last_compare = outcome
    if request.readyset_supported is not None:
        entry.readyset_supported = _readyset_support_value(
            request.readyset_supported,
            request.unsupported_reason,
            entry.readyset_supported,
        )
    registry.save()

    return CompareOutcomeResponse(
        hash=entry.hash,
        target=target,
        comparison_count=lifecycle.comparison_count,
        last_compared_at=lifecycle.last_compared_at,
        last_compare=outcome,
        readyset_supported=entry.readyset_supported,
    )


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

        lifecycle = entry.lifecycle_for(entry.last_target)
        new_hash, _ = registry.add_query(
            sql=request.sql,
            tag=entry.tag,
            source=entry.source,
            target=entry.last_target,
            # Editing rewrites a query the user already starred, so the star
            # follows it rather than being dropped with the old hash.
            save_intent=bool(lifecycle and lifecycle.saved_at),
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
    # The endpoints the same workload runs against. The origin alone is the
    # default and the fallback; asking for "readyset" adds a lane measured
    # through the managed sandbox, reported beside the origin one.
    lanes: list[Literal["origin", "readyset"]] = ["origin"]
    # Concrete parameter sets each stored query rotates through, so a run is
    # not one value's cache profile. 1 keeps the stored values only.
    parameter_sets: Optional[int] = None
    # Executions per query before the clock starts, excluded from every stat.
    warmup_executions: Optional[int] = None
    # Bound on one statement, so a pathological query cannot hold its worker.
    statement_timeout_ms: Optional[int] = None


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
    timeouts: int = 0
    variant_count: int = 1
    # Per-lane numbers in the shape of the fields above, which themselves
    # carry the origin lane. Absent from an origin-only run.
    lanes: Optional[dict[str, dict[str, Any]]] = None


class QuerySkip(BaseModel):
    """A query the run left out, and why.

    A skip with no lanes is out of the whole run and counted in
    ``skipped_count``; a skip that names lanes runs in the run's others.
    """

    query_hash: str
    query_name: str
    reason: str
    lanes: Optional[dict[str, str]] = None


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
    warmup_executions: int = 0
    skipped_count: int = 0
    skipped_queries: list[QuerySkip] = []
    # Completion only: the lanes that produced measurements, and how the
    # Readyset lane's setup went when the caller asked for it.
    lanes_run: Optional[list[str]] = None
    readyset_setup: Optional[dict[str, str]] = None


def _present_fields(value: Any) -> dict[str, Any]:
    """Serialize a benchmark record, leaving an unset ``lanes`` field out.

    Only a run with a second lane sets one, so an origin-only run keeps the
    payload shape it has always had.
    """
    return {
        name: field
        for name, field in vars(value).items()
        if field is not None or name != "lanes"
    }


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
        body = {
            "type": progress.type,
            "elapsed_seconds": progress.elapsed_seconds,
            "total_executions": progress.total_executions,
            "total_successes": progress.total_successes,
            "total_failures": progress.total_failures,
            "qps": progress.qps,
            "queries": [_present_fields(q) for q in progress.queries],
            "warmup_executions": getattr(progress, "warmup_executions", 0),
            "skipped_count": getattr(progress, "skipped_count", 0),
            "skipped_queries": [
                _present_fields(skip)
                for skip in getattr(progress, "skipped_queries", ())
            ],
        }
        for name in (
            "lanes_run",
            "readyset_setup",
            "phase",
            "prepared_count",
            "prepare_total",
        ):
            value = getattr(progress, name, None)
            if value is not None:
                body[name] = value
        payload = json.dumps(body)
    return {
        "event": event_name,
        "data": payload,
    }


async def _benchmark_generator(
    queries, target, mode, interval_ms, concurrency, duration_seconds, max_count,
    lanes=None,
    **tuning,
) -> AsyncGenerator[dict, None]:
    """Compatibility SSE path, protected by a measurement reservation."""
    from ..readyset_lane import benchmark_sandbox
    from ..service import QueryService

    service = QueryService()
    lanes = lanes or ["origin"]
    async with benchmark_sandbox(
        target=target,
        owner_id=f"request-{uuid.uuid4().hex[:12]}",
        lanes=lanes,
    ) as readyset:
        async for progress in service.stream_benchmark(
            queries=queries,
            target=target,
            mode=mode,
            interval_ms=interval_ms,
            concurrency=concurrency,
            duration_seconds=duration_seconds,
            max_count=max_count,
            lanes=lanes,
            readyset=readyset,
            **tuning,
        ):
            yield _progress_to_sse(progress)


def _benchmark_tuning(request: BenchmarkRequest) -> dict[str, int]:
    """Collect the run's optional tuning, leaving unset fields to the service."""
    return {
        name: value
        for name, value in (
            ("parameter_sets", request.parameter_sets),
            ("warmup_executions", request.warmup_executions),
            ("statement_timeout_ms", request.statement_timeout_ms),
        )
        if value is not None
    }


@router.post("/query-registry/benchmark")
async def run_benchmark(
    request: BenchmarkRequest,
    http_request: Request,
    guard: TargetGuard = Depends(require_target_body),
):
    """
    Run benchmark on queries with live progress updates via SSE.

    Returns Server-Sent Events with progress updates during execution.

    This path executes SQL the body carries, so it is restricted to the
    same callers as every other write endpoint: loopback, or the page RDST
    itself served.
    """
    require_local_request(http_request)
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
        lanes=list(request.lanes),
        **_benchmark_tuning(request),
    ))


@router.post(
    "/query-registry/load-test-runs",
    response_model=LoadTestRunStartResponse,
    summary="Start Benchmark Run",
)
async def start_load_test_run(
    request: BenchmarkRequest,
    http_request: Request,
    guard: TargetGuard = Depends(require_target_body),
) -> LoadTestRunStartResponse:
    """Start or attach to the target's detached benchmark."""
    require_local_request(http_request)
    from shared.run_registry import run_registry
    from ..readyset_lane import benchmark_sandbox
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
        async with benchmark_sandbox(
            target=guard.target_name,
            owner_id=owner_id,
            lanes=request.lanes,
        ) as readyset:
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
                lanes=list(request.lanes),
                readyset=readyset,
                **_benchmark_tuning(request),
            ):
                yield event

    run_id = run_registry.start_factory(
        "load_test",
        guard.target_name,
        factory,
        metadata=metadata,
    )
    return LoadTestRunStartResponse(run_id=run_id)

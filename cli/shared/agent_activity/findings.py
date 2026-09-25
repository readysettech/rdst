"""The key finding of a finished deep analysis, in a few plain words.

Read from the stored analysis the entry points at, so backfilled entries get one too. A
stored analysis's AI result doesn't change once written, so findings are cached.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

_REF = re.compile(r"^/api/query-registry/([^/]+)/analysis/([^/]+)$")
_CACHE_SIZE = 1024
_CACHE: OrderedDict[str, str | None] = OrderedDict()
_CACHE_LOCK = threading.Lock()


def analysis_finding(llm_analysis: dict[str, Any] | None) -> str | None:
    """"suggested an index on events(event_type, created_at)", or None when the AI result
    says nothing a person can act on."""
    llm = llm_analysis if isinstance(llm_analysis, dict) else {}
    indexes = [item for item in llm.get("index_recommendations") or []
               if isinstance(item, dict)]
    if indexes:
        first = indexes[0]
        table = str(first.get("table") or "").rsplit(".", 1)[-1]
        columns = [str(column) for column in first.get("columns") or []]
        where = (f" on {table}({', '.join(columns)})" if table and columns
                 else f" on {table}" if table else "")
        more = len(indexes) - 1
        return f"suggested an index{where}" + (f" and {more} more" if more else "")
    rewrites = [item for item in llm.get("rewrite_suggestions") or []
                if isinstance(item, dict)]
    if rewrites:
        return "suggested a rewrite" if len(rewrites) == 1 else \
            f"suggested {len(rewrites)} rewrites"
    assessment = llm.get("performance_assessment")
    rating = assessment.get("overall_rating") if isinstance(assessment, dict) else None
    if rating in ("excellent", "good"):
        return f"rated it {rating}, no index or rewrite needed"
    if rating in ("fair", "poor"):
        return f"rated it {rating}, with no index or rewrite that would help"
    return None


def finding_for_ref(analysis_ref: str | None) -> str | None:
    """The finding of the stored analysis at `analysis_ref`, or None when it's gone."""
    match = _REF.match(analysis_ref or "")
    if not match:
        return None
    with _CACHE_LOCK:
        if analysis_ref in _CACHE:
            _CACHE.move_to_end(analysis_ref)
            return _CACHE[analysis_ref]
    try:
        from shared.query_registry import AnalysisResultsRegistry

        stored = AnalysisResultsRegistry().get_stored_payload(match.group(1), match.group(2))
    except Exception:
        logger.debug("Stored analysis unreadable for %s", analysis_ref, exc_info=True)
        return None
    if stored is None:
        return None
    finding = analysis_finding(stored.get("llm_analysis"))
    with _CACHE_LOCK:
        _CACHE[analysis_ref] = finding
        while len(_CACHE) > _CACHE_SIZE:
            _CACHE.popitem(last=False)
    return finding

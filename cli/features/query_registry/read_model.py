"""Server-side Query Library read model (T7B M3).

Filtering, facet counting, sorting, and keyset pagination for
GET /api/query-registry, computed over the full target-scoped registry
instead of the client's first-150-rows window. The semantics mirror the
web client's queryLibrarySelectors.ts (and the queryIdentity /
sqlParameters helpers it uses) exactly: the frontend swaps to these
server values later, and any drift shows up as changed counts.

The authoritative SQLite backend materializes selector keys beside each
target lifecycle row. The API pushes filtering, facet aggregation, sorting,
and keyset predicates into library.db; these Python selectors remain the
semantic compatibility path for the temporary TOML rollback gate and for
focused parity tests.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

logger = logging.getLogger(__name__)

ViewName = Literal[
    "all", "new", "saved", "high-impact", "needs-analysis", "ready-to-cache", "cached"
]
SourceName = Literal["all", "observed", "ask", "manual", "file", "scan"]
ParamsName = Literal["all", "without-parameters", "values-ready", "values-needed"]
ActivityName = Literal["all", "1m", "1h", "8h", "24h", "7d", "30d"]
ImpactName = Literal["all", "1m", "10m", "1h"]
SortName = Literal[
    "highest-impact",
    "recently-observed",
    "newest",
    "most-frequent",
    "slowest-average",
    "recently-analyzed",
]

VIEWS: Tuple[str, ...] = (
    "all", "new", "saved", "high-impact", "needs-analysis", "ready-to-cache", "cached",
)
SOURCES: Tuple[str, ...] = ("all", "observed", "ask", "manual", "file", "scan")
PARAMS_FILTERS: Tuple[str, ...] = (
    "all", "without-parameters", "values-ready", "values-needed",
)
ACTIVITY_WINDOWS: Tuple[str, ...] = ("all", "1m", "1h", "8h", "24h", "7d", "30d")
IMPACT_FILTERS: Tuple[str, ...] = ("all", "1m", "10m", "1h")

_OBSERVED_SOURCES = frozenset({"top", "top-historical", "audit"})
_ASK_SOURCES = frozenset({"ask", "prompt"})
_MANUAL_SOURCES = frozenset({"manual", "web"})

_ACTIVITY_WINDOW_MS: Dict[str, float] = {
    "1m": 60 * 1000,
    "1h": 60 * 60 * 1000,
    "8h": 8 * 60 * 60 * 1000,
    "24h": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
    "30d": 30 * 24 * 60 * 60 * 1000,
}

_IMPACT_THRESHOLD_MS: Dict[str, float] = {
    "all": 0,
    "1m": 60 * 1000,
    "10m": 10 * 60 * 1000,
    "1h": 60 * 60 * 1000,
}

_WHITESPACE_RE = re.compile(r"\s+")
# JS-regex ports from the client's queryIdentity.ts; re.ASCII mirrors the
# ASCII-only \w of JavaScript.
_VERB_RE = re.compile(
    r"^(select|insert|update|delete|with|create|alter|drop|truncate)\b",
    re.IGNORECASE,
)
_AGGREGATE_RE = re.compile(r"\b(count|sum|avg|min|max)\s*\(", re.IGNORECASE)
_FROM_RE = re.compile(r"\bfrom\s+(?:[\"'`]|\[)?([\w.]+)", re.IGNORECASE | re.ASCII)
_INTO_RE = re.compile(r"\binto\s+(?:[\"'`]|\[)?([\w.]+)", re.IGNORECASE | re.ASCII)
_UPDATE_RE = re.compile(r"^update\s+(?:[\"'`]|\[)?([\w.]+)", re.IGNORECASE | re.ASCII)
# JS-regex ports from the client's sqlParameters.ts.
_PG_PARAM_RE = re.compile(r"\$(\d+)")
_QUESTION_PARAM_RE = re.compile(r"\?")
_NAMED_PARAM_RE = re.compile(r"(?<!:)[:@]([a-zA-Z_][a-zA-Z0-9_]*)", re.ASCII)


def _collapse_whitespace(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value).strip()


def _normalize_search(value: str) -> str:
    return _collapse_whitespace(value).lower()


def derive_query_name(sql: str) -> str:
    """Mirror the client's deriveQueryName so name search matches the UI."""
    normalized = _collapse_whitespace(sql)
    if not normalized:
        return "Untitled query"

    verb_match = _VERB_RE.match(normalized)
    verb = verb_match.group(1).lower() if verb_match else ""
    aggregate_match = _AGGREGATE_RE.search(normalized)
    aggregate = aggregate_match.group(1).upper() if aggregate_match else ""
    table_match = (
        _FROM_RE.search(normalized)
        or _INTO_RE.search(normalized)
        or _UPDATE_RE.search(normalized)
    )
    table = table_match.group(1).split(".")[-1] if table_match else ""
    verb_title = verb[:1].upper() + verb[1:] if verb else ""

    if aggregate and table:
        return f"{aggregate} on {table}"
    if verb_title and table:
        # Middle-dot and ellipsis escapes match the client's rendering exactly.
        return f"{verb_title} \u00b7 {table}"
    if table:
        return table
    if verb_title:
        return verb_title
    return f"{normalized[:40]}\u2026" if len(normalized) > 40 else normalized


def _display_name(entry: Any) -> str:
    tag = (entry.tag or "").strip()
    return tag or derive_query_name(entry.original_sql or entry.sql)


def filter_by_search(entries: List[Any], raw_search: str) -> List[Any]:
    """Mirror the client's filterQueriesBySearch: normalized substring match
    over tag, display name, question, and SQL, plus exact-hash and
    unambiguous hash-prefix (>= 4 chars) matches."""
    search = _normalize_search(raw_search)
    if not search:
        return list(entries)

    unique_prefix_matches = 0
    if len(search) >= 4:
        unique_prefix_matches = sum(
            1 for entry in entries if entry.hash.lower().startswith(search)
        )

    matched: List[Any] = []
    for entry in entries:
        entry_hash = entry.hash.lower()
        if entry_hash == search:
            matched.append(entry)
            continue
        if (
            len(search) >= 4
            and unique_prefix_matches == 1
            and entry_hash.startswith(search)
        ):
            matched.append(entry)
            continue
        haystacks = (
            entry.tag,
            _display_name(entry),
            entry.question,
            entry.sql,
            entry.original_sql,
        )
        if any(value and search in _normalize_search(value) for value in haystacks):
            matched.append(entry)
    return matched


def _entry_sources(entry: Any) -> set:
    return {value for value in [*(entry.sources or []), entry.source] if value}


def _source_matches(entry: Any) -> Dict[str, bool]:
    sources = _entry_sources(entry)
    return {
        "all": True,
        "observed": bool(sources & _OBSERVED_SOURCES),
        "ask": bool(sources & _ASK_SOURCES),
        "manual": bool(sources & _MANUAL_SOURCES),
        "file": "file" in sources,
        "scan": "scan" in sources,
    }


def detect_parameters(sql: str) -> List[Tuple[str, int, str]]:
    """Mirror the client's detectParameters: (placeholder, index, type)."""
    params: List[Tuple[str, int, str]] = []
    seen: set = set()

    for match in _PG_PARAM_RE.finditer(sql):
        placeholder = match.group(0)
        if placeholder not in seen:
            seen.add(placeholder)
            params.append((placeholder, int(match.group(1)), "positional"))

    question_index = 1
    for _ in _QUESTION_PARAM_RE.finditer(sql):
        params.append(("?", question_index, "positional"))
        question_index += 1

    for match in _NAMED_PARAM_RE.finditer(sql):
        placeholder = match.group(0)
        if placeholder not in seen:
            seen.add(placeholder)
            params.append((placeholder, len(params) + 1, "named"))

    params.sort(key=lambda param: param[1])
    return params


def _resolve_initial_value(
    param: Tuple[str, int, str], stored: Optional[Dict[str, Any]]
) -> str:
    placeholder, index, param_type = param
    if not stored:
        return ""
    if param_type == "named":
        key = placeholder[1:]
    elif placeholder.startswith("$"):
        key = "p" + placeholder[1:]
    else:
        key = f"p{index}"
    value = stored.get(key)
    return "" if value is None else str(value)


def _parameter_matches(entry: Any) -> Dict[str, bool]:
    params = detect_parameters(entry.original_sql or entry.sql)
    has_parameters = bool(params)
    values_ready = has_parameters and all(
        _resolve_initial_value(param, entry.most_recent_params).strip() != ""
        for param in params
    )
    return {
        "all": True,
        "without-parameters": not has_parameters,
        "values-ready": values_ready,
        "values-needed": has_parameters and not values_ready,
    }


def _timestamp_ms(value: Optional[str]) -> float:
    """ISO 8601 to epoch milliseconds; unparseable or empty is 0, matching the
    client's Date.parse fallback."""
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return _finite_number(parsed.timestamp() * 1000.0)
    except (ValueError, OverflowError, OSError):
        return 0.0


def _finite_number(value: Any) -> float:
    """Normalize legacy NaN/Infinity so pages and cursors stay JSON-safe."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (TypeError, OverflowError):
        return False


def _latest_activity(entry: Any) -> float:
    return max(
        _timestamp_ms(entry.last_observed_at),
        _timestamp_ms(entry.last_analyzed_at),
        _timestamp_ms(entry.last_compared_at),
        _timestamp_ms(entry.last_analyzed),
    )


def _activity_matches(entry: Any, now_ms: float) -> Dict[str, bool]:
    activity = _latest_activity(entry)
    matches: Dict[str, bool] = {"all": True}
    for window, span_ms in _ACTIVITY_WINDOW_MS.items():
        matches[window] = activity > 0 and activity >= now_ms - span_ms
    return matches


def impact_ms(entry: Any) -> float:
    return _finite_number(
        _finite_number(entry.avg_duration_ms)
        * _finite_number(entry.observation_count)
    )


def _impact_matches(entry: Any) -> Dict[str, bool]:
    impact = impact_ms(entry)
    return {name: impact >= _IMPACT_THRESHOLD_MS[name] for name in IMPACT_FILTERS}


def _view_matches(entry: Any) -> Dict[str, bool]:
    # The client additionally treats rows whose lifecycle fields are absent
    # (undefined) as legacy-saved; the API always serializes those fields, so
    # that branch never fires for server-produced entries.
    cached = bool(entry.readyset_query_id)
    supported = entry.readyset_supported or ""
    return {
        "all": True,
        "new": bool(entry.is_new),
        "saved": bool(entry.saved_at),
        "high-impact": impact_ms(entry) > 0,
        "needs-analysis": not entry.last_analyzed_at,
        "ready-to-cache": (
            not cached and supported == "yes" and not supported.startswith("unsupported")
        ),
        "cached": cached,
    }


def sort_value(entry: Any, sort: str) -> float:
    """Descending sort metric for one entry, mirroring compareQueries."""
    if sort == "highest-impact":
        return impact_ms(entry)
    if sort == "recently-observed":
        return _timestamp_ms(entry.last_observed_at)
    if sort == "newest":
        return max(
            _timestamp_ms(entry.saved_at),
            _timestamp_ms(entry.first_observed_at),
            _timestamp_ms(entry.first_analyzed),
        )
    if sort == "most-frequent":
        return _finite_number(entry.frequency)
    if sort == "slowest-average":
        return _finite_number(entry.avg_duration_ms)
    if sort == "recently-analyzed":
        value = (
            entry.last_analyzed_at
            if entry.last_analyzed_at is not None
            else entry.last_analyzed
        )
        return _timestamp_ms(value)
    raise ValueError(f"Unknown sort: {sort}")


def empty_facet_counts() -> Dict[str, Dict[str, int]]:
    return {
        "view": {name: 0 for name in VIEWS},
        "source": {name: 0 for name in SOURCES},
        "params": {name: 0 for name in PARAMS_FILTERS},
        "activity": {name: 0 for name in ACTIVITY_WINDOWS},
        "impact": {name: 0 for name in IMPACT_FILTERS},
    }


def select_library(
    entries: List[Any],
    *,
    search: str = "",
    view: str = "all",
    starred: Optional[bool] = None,
    source: str = "all",
    params: str = "all",
    activity: str = "all",
    impact: str = "all",
    sort: str = "highest-impact",
    now_ms: Optional[float] = None,
) -> Tuple[List[Any], Dict[str, Dict[str, int]]]:
    """Filter, facet-count, and sort in one pass over the full set.

    Facet semantics mirror selectQueryLibrary: counts run over the
    search-filtered set with every OTHER facet's selected filter applied,
    never a facet's own filter, so each facet shows what selecting it
    would yield. Returns (sorted selected rows, facet_counts).

    ``starred`` narrows the set the same way search does, before any facet
    is counted: it is the user's own shortlist, so every other facet counts
    within it rather than beside it.
    """
    if now_ms is None:
        now_ms = time.time() * 1000.0
    searched = filter_by_search(entries, search)
    if starred is not None:
        searched = [entry for entry in searched if bool(entry.saved_at) is starred]

    facet_counts = empty_facet_counts()
    selected: List[Any] = []

    for entry in searched:
        views = _view_matches(entry)
        sources = _source_matches(entry)
        parameters = _parameter_matches(entry)
        activities = _activity_matches(entry, now_ms)
        impacts = _impact_matches(entry)
        matches_view = views[view]
        matches_source = sources[source]
        matches_params = parameters[params]
        matches_activity = activities[activity]
        matches_impact = impacts[impact]

        if matches_source and matches_params and matches_activity and matches_impact:
            for candidate in VIEWS:
                if views[candidate]:
                    facet_counts["view"][candidate] += 1
        if matches_view and matches_params and matches_activity and matches_impact:
            for candidate in SOURCES:
                if sources[candidate]:
                    facet_counts["source"][candidate] += 1
        if matches_view and matches_source and matches_activity and matches_impact:
            for candidate in PARAMS_FILTERS:
                if parameters[candidate]:
                    facet_counts["params"][candidate] += 1
        if matches_view and matches_source and matches_params and matches_impact:
            for candidate in ACTIVITY_WINDOWS:
                if activities[candidate]:
                    facet_counts["activity"][candidate] += 1
        if matches_view and matches_source and matches_params and matches_activity:
            for candidate in IMPACT_FILTERS:
                if impacts[candidate]:
                    facet_counts["impact"][candidate] += 1

        if (
            matches_view
            and matches_source
            and matches_params
            and matches_activity
            and matches_impact
        ):
            selected.append(entry)

    # Metric descending with the hash as a stable, unique tiebreaker
    # (compareQueries falls back to hash comparison the same way).
    selected.sort(key=lambda entry: (-sort_value(entry, sort), entry.hash))
    return selected, facet_counts


class CursorError(ValueError):
    """The cursor is malformed or was minted for a different sort/filter
    spec; the client should restart from page 1."""


def spec_hash(
    *,
    target: str,
    search: str,
    view: str,
    source: str,
    params: str,
    activity: str,
    impact: str,
    sort: str,
    starred: Optional[bool] = None,
) -> str:
    """Short fingerprint of everything that defines row order and membership.

    Embedded in each cursor so a cursor minted under one sort/filter spec is
    rejected instead of silently returning rows from a different ordering.
    """
    payload = json.dumps(
        {
            "target": target or "",
            "search": _normalize_search(search or ""),
            "view": view,
            "starred": starred,
            "source": source,
            "params": params,
            "activity": activity,
            "impact": impact,
            "sort": sort,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def encode_cursor(value: float, entry_hash: str, spec: str) -> str:
    """Opaque keyset cursor: base64 of (sort_value, hash, spec_hash)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CursorError("Cannot encode a non-numeric cursor position")
    if not _is_finite_number(value):
        raise CursorError("Cannot encode a non-finite cursor position")
    raw = json.dumps(
        {"v": value, "h": entry_hash, "s": spec}, separators=(",", ":")
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, expected_spec: str) -> Tuple[float, str]:
    """Decode and validate a cursor; raises CursorError on any mismatch."""
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise CursorError("Malformed cursor") from exc
    if not isinstance(payload, dict):
        raise CursorError("Malformed cursor")
    value = payload.get("v")
    entry_hash = payload.get("h")
    spec = payload.get("s")
    if (
        not _is_finite_number(value)
        or not isinstance(entry_hash, str)
        or not isinstance(spec, str)
    ):
        raise CursorError("Malformed cursor")
    if spec != expected_spec:
        raise CursorError("Cursor does not match the requested sort/filter spec")
    return float(value), entry_hash


def paginate(
    rows: List[Any],
    sort: str,
    cursor_position: Optional[Tuple[float, str]],
    limit: int,
) -> Tuple[List[Any], Optional[Tuple[float, str]]]:
    """Keyset page: rows strictly after the cursor in (metric desc, hash asc)
    order. Row values, not row counts, define the page boundary, so rows whose
    keys are stable are returned exactly once even when other rows move
    between requests."""
    if cursor_position is not None:
        cursor_value, cursor_hash = cursor_position
        rows = [
            entry
            for entry in rows
            if sort_value(entry, sort) < cursor_value
            or (sort_value(entry, sort) == cursor_value and entry.hash > cursor_hash)
        ]
    page = rows[:limit]
    if len(rows) > limit and page:
        last = page[-1]
        return page, (sort_value(last, sort), last.hash)
    return page, None


def collector_freshness(target: Optional[str]) -> Optional[Dict[str, Any]]:
    """Read collector_state for a target from the observation store.

    Best effort by contract: a missing store, missing row, or store failure
    yields None and must never fail the registry request. The store file is
    only opened when it already exists, so a plain listing never creates
    cache.db as a side effect.
    """
    if not target:
        return None
    try:
        from features.query_registry.discovery import query_discovery

        store = query_discovery.existing_store()
        if store is None:
            return None
        row = store.get_collector_state(target)
    except Exception:
        logger.warning(
            "Collector freshness unavailable for target %s", target, exc_info=True
        )
        return None
    if not row:
        return None
    return {
        "state": row.get("state") or "",
        "last_success_at": _epoch_to_iso(row.get("last_success_at")),
        "epoch_id": row.get("epoch_id") or None,
    }


def _epoch_to_iso(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    return (
        datetime.fromtimestamp(int(value), timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

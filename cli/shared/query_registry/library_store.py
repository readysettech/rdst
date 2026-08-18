"""Durable query-library store (``library.db``) for the query registry.

SQLite-backed authoritative storage for user-meaningful registry rows:
query identities plus their per-target lifecycle state. Unlike
``cache.db`` (observation_store.py), this file is PRECIOUS: migrations
are additive only, the file is never auto-deleted or rebuilt, and a file
written by a newer build refuses writes while continuing to serve
compatible reads.

Design (see docs/architecture/rdst-query-observation-deep-research.md,
Q4/Q12 M2):

- WAL mode so a long-lived server and short-lived CLI processes can read
  and write the same file concurrently (with the DELETE rollback fallback
  on runtimes lacking the WAL-reset fix; see observation_store); every
  write runs under ``BEGIN IMMEDIATE`` so contention surfaces at ``BEGIN``
  where ``busy_timeout`` applies.
- Hand-rolled ``PRAGMA user_version`` migration ladder.
- First open of a data dir that has ``queries.toml`` but no library rows
  imports the TOML inside the schema transaction, writes
  ``queries.toml.pre-sqlite-<version>.bak`` beside it, and seeds
  ``reviewed_at`` for every imported target/query pair so the upgrade
  does not flood the New view. From then on the TOML is ignored; the
  on-demand projection is ``rdst query export --format=toml``.
- Entries round-trip losslessly: known QueryEntry fields map to typed
  columns, unknown (newer-build) fields ride in a JSON ``extra`` column.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional

import toml

import shared.constants as shared_constants
from shared.persistence import merge_mapping_changes
from shared.query_registry.observation_store import (
    _apply_journal_mode,
    _check_sqlite_runtime,
    _connection_pragmas,
    _refuse_network_path,
)

logger = logging.getLogger(__name__)

__all__ = [
    "LibraryStore",
    "RegistryReadOnlyError",
    "SCHEMA_VERSION",
    "default_library_db_path",
    "library_db_path_for",
]

SCHEMA_VERSION = 8

# QueryRegistry is constructed by nearly every CLI command; run the SQLite
# runtime check once per process, at first actual store use rather than at
# construction. A command that never opens a SQLite store stays unaffected;
# any command that does open one enforces the feature floor and follows the
# process's journal strategy (WAL, or the DELETE fallback on unfixed runtimes).
_strict_available: Optional[bool] = None


def _strict_mode() -> bool:
    global _strict_available
    if _strict_available is None:
        _strict_available = _check_sqlite_runtime()
    return _strict_available


class RegistryReadOnlyError(RuntimeError):
    """library.db was written by a newer rdst; writes are refused.

    Reads keep serving the columns this build understands; the user should
    upgrade rdst or recover the data with ``rdst query export``.
    """


def default_library_db_path() -> Path:
    """Return the default location of library.db, beside queries.toml."""
    return shared_constants.rdst_data_dir() / "library.db"


def library_db_path_for(registry_path: Path) -> Path:
    """Return the library.db path serving a given queries.toml path.

    The canonical registry file is named ``library.db`` beside the default
    ``queries.toml``. A custom TOML filename (tests, side-by-side
    registries) gets its own ``<name>.library.db`` so two registries in
    one directory stay independent.
    """
    if registry_path.name == "queries.toml":
        return registry_path.with_name("library.db")
    return registry_path.with_name(registry_path.name + ".library.db")


# Scalar QueryEntry fields stored as typed columns, in schema order.
_TEXT_COLUMNS = (
    "sql",
    "tag",
    "original_sql",
    "question",
    "first_analyzed",
    "last_analyzed",
    "source",
    "last_target",
    "ask_target",
    "readyset_query_id",
    "readyset_supported",
    "last_cache_target",
    "readyset_last_observed_at",
)
_INT_COLUMNS = ("frequency", "observation_count")
_REAL_COLUMNS = ("max_duration_ms", "avg_duration_ms")
_JSON_COLUMNS = ("parameters", "most_recent_params")

_LIFECYCLE_TEXT_COLUMNS = (
    "first_observed_at",
    "last_observed_at",
    "reviewed_at",
    "saved_at",
    "last_analyzed_at",
    "last_compared_at",
)
_LIFECYCLE_INT_COLUMNS = ("analysis_count", "comparison_count")

_IDENTITY_FIELDS = _TEXT_COLUMNS + _INT_COLUMNS + _REAL_COLUMNS + _JSON_COLUMNS
_LIFECYCLE_FIELDS = _LIFECYCLE_TEXT_COLUMNS + _LIFECYCLE_INT_COLUMNS + ("sources",)

# Materialized target-scoped Query Library read-model columns. Keeping every
# mutable sort key beside target_key lets SQLite serve keyset pages from one
# covering index instead of loading and sorting the full registry in Python.
_READ_MODEL_COLUMNS = (
    "rm_hash",
    "rm_search_text",
    "rm_source_observed",
    "rm_source_ask",
    "rm_source_manual",
    "rm_source_file",
    "rm_source_scan",
    "rm_has_parameters",
    "rm_parameter_values_ready",
    "rm_is_new",
    "rm_is_saved",
    "rm_high_impact",
    "rm_needs_analysis",
    "rm_ready_to_cache",
    "rm_is_cached",
    "rm_latest_activity_ms",
    "rm_impact_ms",
    "rm_last_observed_ms",
    "rm_newest_ms",
    "rm_frequency",
    "rm_avg_duration_ms",
    "rm_recently_analyzed_ms",
)

_READ_MODEL_INDEXES = (
    "CREATE INDEX IF NOT EXISTS tq_rm_hash ON target_query"
    "(target_key, rm_hash ASC)",
    "CREATE INDEX IF NOT EXISTS tq_rm_impact ON target_query"
    "(target_key, rm_impact_ms DESC, rm_hash ASC)",
    "CREATE INDEX IF NOT EXISTS tq_rm_observed ON target_query"
    "(target_key, rm_last_observed_ms DESC, rm_hash ASC)",
    "CREATE INDEX IF NOT EXISTS tq_rm_newest ON target_query"
    "(target_key, rm_newest_ms DESC, rm_hash ASC)",
    "CREATE INDEX IF NOT EXISTS tq_rm_frequency ON target_query"
    "(target_key, rm_frequency DESC, rm_hash ASC)",
    "CREATE INDEX IF NOT EXISTS tq_rm_average ON target_query"
    "(target_key, rm_avg_duration_ms DESC, rm_hash ASC)",
    "CREATE INDEX IF NOT EXISTS tq_rm_analyzed ON target_query"
    "(target_key, rm_recently_analyzed_ms DESC, rm_hash ASC)",
)

_SORT_COLUMNS = {
    "highest-impact": ("rm_impact_ms", "tq_rm_impact"),
    "recently-observed": ("rm_last_observed_ms", "tq_rm_observed"),
    "newest": ("rm_newest_ms", "tq_rm_newest"),
    "most-frequent": ("rm_frequency", "tq_rm_frequency"),
    "slowest-average": ("rm_avg_duration_ms", "tq_rm_average"),
    "recently-analyzed": ("rm_recently_analyzed_ms", "tq_rm_analyzed"),
}

_VIEW_COLUMNS = {
    "all": "1",
    "new": "rm_is_new",
    "saved": "rm_is_saved",
    "high-impact": "rm_high_impact",
    "needs-analysis": "rm_needs_analysis",
    "ready-to-cache": "rm_ready_to_cache",
    "cached": "rm_is_cached",
}
_SOURCE_COLUMNS = {
    "all": "1",
    "observed": "rm_source_observed",
    "ask": "rm_source_ask",
    "manual": "rm_source_manual",
    "file": "rm_source_file",
    "scan": "rm_source_scan",
}
_PARAM_COLUMNS = {
    "all": "1",
    "without-parameters": "NOT rm_has_parameters",
    "values-ready": "rm_parameter_values_ready",
    "values-needed": "rm_has_parameters AND NOT rm_parameter_values_ready",
}
_ACTIVITY_WINDOWS_MS = {
    "all": None,
    "1m": 60 * 1000,
    "1h": 60 * 60 * 1000,
    "8h": 8 * 60 * 60 * 1000,
    "24h": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
    "30d": 30 * 24 * 60 * 60 * 1000,
}
_IMPACT_THRESHOLDS_MS = {
    "all": 0,
    "1m": 60 * 1000,
    "10m": 10 * 60 * 1000,
    "1h": 60 * 60 * 1000,
}


def _activity_sql(name: str, now_ms: float) -> str:
    window = _ACTIVITY_WINDOWS_MS[name]
    if window is None:
        return "1"
    # now_ms is generated in-process, not user input.
    cutoff = float(now_ms) - window
    return f"rm_latest_activity_ms > 0 AND rm_latest_activity_ms >= {cutoff!r}"


def _impact_sql(name: str) -> str:
    return f"rm_impact_ms >= {_IMPACT_THRESHOLDS_MS[name]:d}"


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

_IDENTITY_SELECT = (
    "SELECT id, hash, " + ", ".join(_IDENTITY_FIELDS) + ", extra FROM query_identity"
)
_LIFECYCLE_SELECT = (
    "SELECT identity_id, target_key, "
    + ", ".join(_LIFECYCLE_FIELDS)
    + ", extra FROM target_query"
)
_READ_MODEL_JOIN_SELECT = (
    "SELECT qi.id AS identity_id, qi.hash, "
    + ", ".join(f"qi.{column}" for column in _IDENTITY_FIELDS)
    + ", qi.extra AS identity_extra, tq.target_key, "
    + ", ".join(f"tq.{column}" for column in _LIFECYCLE_FIELDS)
    + ", tq.extra AS lifecycle_extra "
    "FROM query_identity AS qi "
    "JOIN target_query AS tq ON tq.identity_id = qi.id "
    "ORDER BY qi.id, tq.target_key"
)


def _schema_v1(strict: bool) -> tuple[str, ...]:
    """Initial library.db schema. STRICT is dropped when the runtime lacks it."""
    s = " STRICT," if strict else ""
    s_only = " STRICT" if strict else ""
    return (
        f"""
        CREATE TABLE query_identity (
          id INTEGER PRIMARY KEY,
          hash TEXT NOT NULL,
          sql TEXT NOT NULL,
          tag TEXT NOT NULL DEFAULT '',
          original_sql TEXT NOT NULL DEFAULT '',
          question TEXT NOT NULL DEFAULT '',
          first_analyzed TEXT NOT NULL DEFAULT '',
          last_analyzed TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL DEFAULT 'manual',
          last_target TEXT NOT NULL DEFAULT '',
          ask_target TEXT NOT NULL DEFAULT '',
          readyset_query_id TEXT NOT NULL DEFAULT '',
          readyset_supported TEXT NOT NULL DEFAULT '',
          last_cache_target TEXT NOT NULL DEFAULT '',
          readyset_last_observed_at TEXT NOT NULL DEFAULT '',
          frequency INTEGER NOT NULL DEFAULT 0,
          observation_count INTEGER NOT NULL DEFAULT 0,
          max_duration_ms REAL NOT NULL DEFAULT 0,
          avg_duration_ms REAL NOT NULL DEFAULT 0,
          parameters TEXT NOT NULL DEFAULT '{{}}',
          most_recent_params TEXT NOT NULL DEFAULT '{{}}',
          extra TEXT NOT NULL DEFAULT '{{}}'
        ){s_only}""",
        "CREATE UNIQUE INDEX ux_qi_hash ON query_identity(hash)",
        f"""
        CREATE TABLE target_query (
          identity_id INTEGER NOT NULL
            REFERENCES query_identity(id) ON DELETE CASCADE,
          target_key TEXT NOT NULL,
          first_observed_at TEXT NOT NULL DEFAULT '',
          last_observed_at TEXT NOT NULL DEFAULT '',
          reviewed_at TEXT NOT NULL DEFAULT '',
          saved_at TEXT NOT NULL DEFAULT '',
          last_analyzed_at TEXT NOT NULL DEFAULT '',
          last_compared_at TEXT NOT NULL DEFAULT '',
          analysis_count INTEGER NOT NULL DEFAULT 0,
          comparison_count INTEGER NOT NULL DEFAULT 0,
          sources TEXT NOT NULL DEFAULT '[]',
          extra TEXT NOT NULL DEFAULT '{{}}',
          PRIMARY KEY (identity_id, target_key)
        ){s} WITHOUT ROWID""",
    )


# Migration ladder: version N maps to the DDL that brings a version N-1
# file to version N. Additive only; this file is never rebuilt. Version 2
# is a data cleanup with no DDL; its logic lives in
# _prune_system_only_entries and runs inside the same transaction.
_MIGRATIONS: Dict[int, Any] = {
    1: _schema_v1,
    2: lambda strict: (),
    3: lambda strict: (),
    4: lambda strict: (),
    5: lambda strict: (),
    6: lambda strict: (),
    7: lambda strict: (
        "ALTER TABLE target_query ADD COLUMN rm_hash TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE target_query ADD COLUMN rm_search_text TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE target_query ADD COLUMN rm_source_observed "
        "INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_source_ask INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_source_manual "
        "INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_source_file INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_source_scan INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_has_parameters "
        "INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_parameter_values_ready "
        "INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_is_new INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_is_saved INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_high_impact INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_needs_analysis "
        "INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE target_query ADD COLUMN rm_ready_to_cache "
        "INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_is_cached INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_latest_activity_ms "
        "REAL NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_impact_ms REAL NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_last_observed_ms "
        "REAL NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_newest_ms REAL NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_frequency REAL NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_avg_duration_ms "
        "REAL NOT NULL DEFAULT 0",
        "ALTER TABLE target_query ADD COLUMN rm_recently_analyzed_ms "
        "REAL NOT NULL DEFAULT 0",
    ),
    # Version 8 is a data migration with no DDL: hash_sql now canonicalizes
    # engine placeholder styles before hashing, so identities are re-keyed
    # and duplicates merged (see _canonicalize_placeholder_hashes).
    8: lambda strict: (),
}

# Data-cleanup versions: each runs _prune_system_only_entries inside its
# migration transaction. Version 3 exists because version 2's criteria
# treated the legacy identity-level last_analyzed as a curation signal,
# although mere observation writes it, so v2 pruned nothing on real data.
# Version 4 additionally recognizes the pre-T7A import synthesis that stamps
# saved_at = first_analyzed on every row, which is not real save intent.
# Version 5 re-runs the prune with the upgraded relation classifier (self
# marker, utility statements, parse-failure relation-token fallback), which
# catches RDST's own text-fetch statements and tooling SQL the AST parser
# cannot handle.
# Version 6 re-runs it once more: entries admitted during the same local
# iteration window could land between the classifier upgrade and the v5
# stamp; the prune is idempotent.
_PRUNE_VERSIONS = (2, 3, 4, 5, 6)

# Sources produced by automatic observation rather than a user action.
# Mirrors the web library's observed-source set plus the realtime spelling.
_AUTO_OBSERVED_SOURCES = {"top", "top-historical", "top-realtime", "audit"}


def _prune_system_only_entries(conn: sqlite3.Connection) -> int:
    """Schema v2 cleanup: drop auto-observed entries that are not user workload.

    Early automatic discovery admitted RDST's own catalog and statistics
    queries (pg_stat_user_indexes, pg_tables, ...) into the library. An
    entry is removed only when every signal says nobody curated it: all of
    its sources are automatic observation, it was never saved, analyzed,
    compared, cached, or asked about, and its SQL references no user
    relation. Every removal is logged; anything ambiguous is kept.
    """
    from shared.query_registry.sql_normalizer import references_user_relations

    removed = 0
    rows = conn.execute(
        """
        SELECT id, hash, sql, original_sql, question, source,
               first_analyzed, readyset_query_id
        FROM query_identity
        """
    ).fetchall()
    for row in rows:
        # Identity-level first_analyzed/last_analyzed are legacy-overloaded:
        # mere observation writes them on every add_query, so they carry no
        # curation signal. Real analysis marks the target lifecycle below.
        if row["question"] or row["readyset_query_id"]:
            continue
        if row["source"] not in _AUTO_OBSERVED_SOURCES:
            continue
        lifecycles = conn.execute(
            """
            SELECT saved_at, last_analyzed_at, last_compared_at,
                   analysis_count, comparison_count, sources
            FROM target_query WHERE identity_id = ?
            """,
            (row["id"],),
        ).fetchall()
        # A saved_at equal to the identity's first_analyzed is the pre-T7A
        # import synthesis ("every registry row appeared in Saved"), not a
        # user's save action; any other saved_at value counts as curation.
        curated = any(
            (item["saved_at"] and item["saved_at"] != row["first_analyzed"])
            or item["last_analyzed_at"]
            or item["last_compared_at"]
            or item["analysis_count"]
            or item["comparison_count"]
            for item in lifecycles
        )
        if curated:
            continue
        lifecycle_sources: set = set()
        for item in lifecycles:
            try:
                lifecycle_sources.update(json.loads(item["sources"] or "[]"))
            except (TypeError, ValueError):
                curated = True
                break
        if curated or lifecycle_sources - _AUTO_OBSERVED_SOURCES:
            continue
        sql_text = row["original_sql"] or row["sql"]
        if references_user_relations(sql_text):
            continue
        conn.execute("DELETE FROM target_query WHERE identity_id = ?", (row["id"],))
        conn.execute("DELETE FROM query_identity WHERE id = ?", (row["id"],))
        removed += 1
        logger.info(
            "Pruned system-only observed entry %s: %.60s",
            row["hash"],
            " ".join(sql_text.split()),
        )
    if removed:
        logger.info("Library cleanup removed %d system-only observed entries", removed)
    return removed


def _earliest(*timestamps: str) -> str:
    """Earliest non-empty ISO-8601 UTC timestamp (lexicographic order holds)."""
    present = [value for value in timestamps if value]
    return min(present) if present else ""


def _latest(*timestamps: str) -> str:
    present = [value for value in timestamps if value]
    return max(present) if present else ""


# Lifecycle timestamps that union to the earliest user/system action vs.
# the most recent one when duplicate identities merge.
_LIFECYCLE_EARLIEST = ("first_observed_at", "reviewed_at", "saved_at")
_LIFECYCLE_LATEST = ("last_observed_at", "last_analyzed_at", "last_compared_at")


def _merge_lifecycle(base: Dict[str, Any], other: Dict[str, Any]) -> None:
    """Union one target's lifecycle from a duplicate identity into ``base``."""
    for column in _LIFECYCLE_EARLIEST:
        base[column] = _earliest(base.get(column, ""), other.get(column, ""))
    for column in _LIFECYCLE_LATEST:
        base[column] = _latest(base.get(column, ""), other.get(column, ""))
    for column in _LIFECYCLE_INT_COLUMNS:
        base[column] = max(
            _finite_int(base.get(column)), _finite_int(other.get(column))
        )
    sources = list(base.get("sources") or [])
    for source in other.get("sources") or []:
        if source not in sources:
            sources.append(source)
    base["sources"] = sources
    for key, value in other.items():
        base.setdefault(key, value)


def _merge_identity(survivor: Dict[str, Any], other: Dict[str, Any]) -> None:
    """Union a duplicate identity into the surviving entry dict.

    The survivor's values win wherever it has any; the duplicate only fills
    gaps, extends timestamp ranges, raises counters, and contributes
    lifecycle rows for targets the survivor has not seen.
    """
    for column in _TEXT_COLUMNS:
        if column in ("first_analyzed", "last_analyzed"):
            continue
        if not survivor.get(column):
            survivor[column] = other.get(column, "")
    survivor["first_analyzed"] = _earliest(
        survivor.get("first_analyzed", ""), other.get("first_analyzed", "")
    )
    survivor["last_analyzed"] = _latest(
        survivor.get("last_analyzed", ""), other.get("last_analyzed", "")
    )
    for column in _INT_COLUMNS:
        survivor[column] = max(
            _finite_int(survivor.get(column)), _finite_int(other.get(column))
        )
    for column in _REAL_COLUMNS:
        survivor[column] = max(
            _finite_float(survivor.get(column)), _finite_float(other.get(column))
        )
    for column in _JSON_COLUMNS:
        if not survivor.get(column):
            survivor[column] = other.get(column) or {}
    lifecycles = survivor.setdefault("target_lifecycle", {})
    for target_key, lifecycle in (other.get("target_lifecycle") or {}).items():
        if target_key in lifecycles:
            _merge_lifecycle(lifecycles[target_key], lifecycle)
        else:
            lifecycles[target_key] = lifecycle
    for key, value in other.items():
        if key not in ("hash", "target_lifecycle"):
            survivor.setdefault(key, value)


def _curation_rank(entry: Dict[str, Any]) -> tuple[int, int, int, int]:
    """Order duplicate identities by how much a user has invested in them."""
    lifecycles = list((entry.get("target_lifecycle") or {}).values())
    saved = any(item.get("saved_at") for item in lifecycles)
    analyzed = sum(
        _finite_int(item.get("analysis_count"))
        + _finite_int(item.get("comparison_count"))
        for item in lifecycles
    )
    has_params = bool(
        entry.get("most_recent_params") or entry.get("parameters")
    )
    curated_meta = bool(
        entry.get("tag") or entry.get("question") or entry.get("readyset_query_id")
    )
    return (int(saved), analyzed, int(has_params), int(curated_meta))


def _json_dumps(value: Any) -> str:
    # default=str: a hand-edited legacy TOML may parse timestamps into
    # datetime objects; degrade them to strings instead of failing import.
    return json.dumps(value, sort_keys=True, default=str)


def _finite_float(value: Any) -> float:
    """Return a SQLite/JSON-safe finite number for legacy numeric input."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _finite_int(value: Any) -> int:
    """Return an integer for legacy input, normalizing NaN/Infinity to zero."""
    try:
        if isinstance(value, float) and not math.isfinite(value):
            return 0
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _encode_identity(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Split an entry dict into query_identity column values plus extra."""
    row: Dict[str, Any] = {}
    for column in _TEXT_COLUMNS:
        value = entry.get(column, "")
        row[column] = value if isinstance(value, str) else str(value)
    for column in _INT_COLUMNS:
        row[column] = _finite_int(entry.get(column))
    for column in _REAL_COLUMNS:
        row[column] = _finite_float(entry.get(column))
    for column in _JSON_COLUMNS:
        row[column] = _json_dumps(entry.get(column) or {})
    known = set(_IDENTITY_FIELDS) | {"hash", "target_lifecycle"}
    row["extra"] = _json_dumps(
        {key: value for key, value in entry.items() if key not in known}
    )
    return row


def _encode_lifecycle(lifecycle: Dict[str, Any]) -> Dict[str, Any]:
    """Split a lifecycle dict into target_query column values plus extra."""
    row: Dict[str, Any] = {}
    for column in _LIFECYCLE_TEXT_COLUMNS:
        value = lifecycle.get(column, "")
        row[column] = value if isinstance(value, str) else str(value)
    for column in _LIFECYCLE_INT_COLUMNS:
        row[column] = int(lifecycle.get(column) or 0)
    row["sources"] = _json_dumps(lifecycle.get("sources") or [])
    known = set(_LIFECYCLE_FIELDS)
    row["extra"] = _json_dumps(
        {key: value for key, value in lifecycle.items() if key not in known}
    )
    return row


def _read_model_values(
    query_hash: str,
    entry: Dict[str, Any],
    lifecycle: Dict[str, Any],
) -> Dict[str, Any]:
    """Materialize selector semantics used by the web Query Library.

    The canonical helper functions live in ``features.query_registry.read_model``;
    importing them lazily avoids a module cycle while making persistence and
    the compatibility Python selector share the exact same name, parameter,
    timestamp, source, and impact rules.
    """
    from features.query_registry import read_model

    sql = str(entry.get("sql") or "")
    original_sql = str(entry.get("original_sql") or "")
    tag = str(entry.get("tag") or "")
    question = str(entry.get("question") or "")
    display_name = tag.strip() or read_model.derive_query_name(original_sql or sql)
    search_text = "\n".join(
        read_model._normalize_search(value)
        for value in (tag, display_name, question, sql, original_sql)
        if value
    )

    source_values = {
        str(value)
        for value in [*(lifecycle.get("sources") or []), entry.get("source")]
        if value
    }
    parameters = read_model.detect_parameters(original_sql or sql)
    most_recent_params = entry.get("most_recent_params") or {}
    has_parameters = bool(parameters)
    parameter_values_ready = has_parameters and all(
        read_model._resolve_initial_value(param, most_recent_params).strip() != ""
        for param in parameters
    )

    avg_duration_ms = _finite_float(entry.get("avg_duration_ms"))
    observation_count = _finite_float(entry.get("observation_count"))
    impact = _finite_float(avg_duration_ms * observation_count)
    first_observed_ms = read_model._timestamp_ms(lifecycle.get("first_observed_at"))
    last_observed_ms = read_model._timestamp_ms(lifecycle.get("last_observed_at"))
    saved_ms = read_model._timestamp_ms(lifecycle.get("saved_at"))
    analyzed_ms = read_model._timestamp_ms(lifecycle.get("last_analyzed_at"))
    compared_ms = read_model._timestamp_ms(lifecycle.get("last_compared_at"))
    legacy_first_analyzed_ms = read_model._timestamp_ms(entry.get("first_analyzed"))
    legacy_last_analyzed_ms = read_model._timestamp_ms(entry.get("last_analyzed"))
    readyset_query_id = str(entry.get("readyset_query_id") or "")
    readyset_supported = str(entry.get("readyset_supported") or "")

    return {
        "rm_hash": query_hash,
        "rm_search_text": search_text,
        "rm_source_observed": int(
            bool(source_values & read_model._OBSERVED_SOURCES)
        ),
        "rm_source_ask": int(bool(source_values & read_model._ASK_SOURCES)),
        "rm_source_manual": int(bool(source_values & read_model._MANUAL_SOURCES)),
        "rm_source_file": int("file" in source_values),
        "rm_source_scan": int("scan" in source_values),
        "rm_has_parameters": int(has_parameters),
        "rm_parameter_values_ready": int(parameter_values_ready),
        "rm_is_new": int(
            bool(lifecycle.get("first_observed_at"))
            and not bool(lifecycle.get("reviewed_at"))
        ),
        "rm_is_saved": int(bool(lifecycle.get("saved_at"))),
        "rm_high_impact": int(impact > 0),
        "rm_needs_analysis": int(not bool(lifecycle.get("last_analyzed_at"))),
        "rm_ready_to_cache": int(
            not readyset_query_id
            and readyset_supported == "yes"
            and not readyset_supported.startswith("unsupported")
        ),
        "rm_is_cached": int(bool(readyset_query_id)),
        "rm_latest_activity_ms": max(
            last_observed_ms, analyzed_ms, compared_ms, legacy_last_analyzed_ms
        ),
        "rm_impact_ms": impact,
        "rm_last_observed_ms": last_observed_ms,
        "rm_newest_ms": max(saved_ms, first_observed_ms, legacy_first_analyzed_ms),
        "rm_frequency": _finite_float(entry.get("frequency")),
        "rm_avg_duration_ms": avg_duration_ms,
        # API entries always serialize last_analyzed_at as a string (possibly
        # empty), so the current selector does not fall back to the legacy
        # identity timestamp when a lifecycle exists.
        "rm_recently_analyzed_ms": analyzed_ms,
    }


def _decode_identity(row: sqlite3.Row) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"hash": row["hash"]}
    for column in _TEXT_COLUMNS:
        entry[column] = row[column]
    for column in _INT_COLUMNS:
        entry[column] = _finite_int(row[column])
    for column in _REAL_COLUMNS:
        entry[column] = _finite_float(row[column])
    for column in _JSON_COLUMNS:
        entry[column] = json.loads(row[column])
    entry.update(json.loads(row["extra"]))
    entry["target_lifecycle"] = {}
    return entry


def _decode_lifecycle(row: sqlite3.Row) -> Dict[str, Any]:
    lifecycle: Dict[str, Any] = {}
    for column in _LIFECYCLE_TEXT_COLUMNS + _LIFECYCLE_INT_COLUMNS:
        lifecycle[column] = row[column]
    lifecycle["sources"] = json.loads(row["sources"])
    lifecycle.update(json.loads(row["extra"]))
    return lifecycle


class LibraryStore:
    """Authoritative SQLite storage behind QueryRegistry.

    Connections are opened per operation, so any number of registry
    instances (and processes) may share one file; writes serialize on
    SQLite's own lock via ``BEGIN IMMEDIATE`` plus ``busy_timeout``, with
    an in-process lock keeping one logical writer per instance.
    """

    def __init__(self, db_path: Path, toml_path: Path) -> None:
        self._db_path = Path(os.path.realpath(db_path))
        self._toml_path = toml_path
        self._lock = threading.Lock()
        self._opened = False
        self._read_only_reason: Optional[str] = None

    @property
    def path(self) -> Path:
        return self._db_path

    # -- lifecycle ----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._db_path, isolation_level=None, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        for pragma in _connection_pragmas():
            conn.execute(pragma)
        return conn

    def ensure_open(self) -> None:
        """Create, migrate, or first-run-import the store; idempotent."""
        with self._lock:
            if self._opened:
                return
            # Check before touching either a fresh or current-schema file, so
            # a store that needs no migration still enforces the feature
            # floor before any SQLite work.
            _strict_mode()
            _refuse_network_path(str(self._db_path))
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = self._connect()
            try:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    # Fresh file: auto_vacuum is a one-shot decision that must
                    # precede any CREATE TABLE, then the journal strategy.
                    conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
                    _apply_journal_mode(conn, self._db_path)
                    self._create_and_import(conn)
                elif version > SCHEMA_VERSION:
                    # Precious file written by a newer build: never rebuild.
                    # Reads still follow this runtime's journal strategy.
                    _apply_journal_mode(conn, self._db_path)
                    self._read_only_reason = (
                        f"library.db at {self._db_path} was written by a newer "
                        f"rdst (schema {version}, this build understands "
                        f"{SCHEMA_VERSION}). Writes are disabled; upgrade rdst "
                        "or recover with 'rdst query export --format=toml'."
                    )
                    logger.warning(self._read_only_reason)
                else:
                    # Existing files follow this runtime's journal strategy,
                    # in both directions (WAL upgrade and DELETE fallback).
                    _apply_journal_mode(conn, self._db_path)
                    check = conn.execute("PRAGMA quick_check").fetchone()[0]
                    if check != "ok":
                        raise RuntimeError(
                            f"library.db at {self._db_path} failed integrity "
                            f"check ({check}). It is never rebuilt "
                            "automatically; restore the file from backup or "
                            "re-import from the queries.toml backup beside it."
                        )
                    self._migrate(conn, version)
            finally:
                conn.close()
            self._opened = True

    def _migrate(self, conn: sqlite3.Connection, from_version: int) -> None:
        for version in range(from_version + 1, SCHEMA_VERSION + 1):
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in _MIGRATIONS[version](_strict_mode()):
                    if version == 7 and statement.startswith("ALTER TABLE"):
                        column = statement.split()[5]
                        existing = {
                            row["name"]
                            for row in conn.execute("PRAGMA table_info(target_query)")
                        }
                        if column in existing:
                            continue
                    conn.execute(statement)
                if version in _PRUNE_VERSIONS:
                    _prune_system_only_entries(conn)
                if version == 7:
                    self._backfill_read_model(conn)
                    for statement in _READ_MODEL_INDEXES:
                        conn.execute(statement)
                if version == 8:
                    self._canonicalize_placeholder_hashes(conn)
                conn.execute(f"PRAGMA user_version = {version:d}")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def _create_and_import(self, conn: sqlite3.Connection) -> None:
        """Create schema v1 and import queries.toml, atomically.

        The whole first run happens inside one BEGIN IMMEDIATE transaction:
        a failure rolls everything back (user_version stays 0), leaving the
        TOML untouched and authoritative for the next attempt. The TOML
        backup is written before the transaction commits and is never
        overwritten once present.
        """
        # QueryEntry.from_dict lives one import up; deferred to avoid a cycle.
        from shared.query_registry.query_registry import QueryEntry

        entries: Dict[str, Dict[str, Any]] = {}
        raw_by_hash: Dict[str, Dict[str, Any]] = {}
        if self._toml_path.exists():
            data = toml.load(self._toml_path)
            for query_hash, raw in (data.get("queries") or {}).items():
                entry = QueryEntry.from_dict(raw)
                entries[query_hash] = entry.to_dict()
                raw_by_hash[query_hash] = raw
            backup = self._toml_path.with_name(
                f"{self._toml_path.name}.pre-sqlite-{SCHEMA_VERSION}.bak"
            )
            if not backup.exists():
                shutil.copy2(self._toml_path, backup)

        seeded_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Another process may have migrated between our version read and
            # this write lock; re-check before creating anything.
            if conn.execute("PRAGMA user_version").fetchone()[0] != 0:
                conn.execute("ROLLBACK")
                return
            for statement in _MIGRATIONS[1](_strict_mode()):
                conn.execute(statement)
            # Fresh stores are built through the same additive v7 DDL as an
            # upgraded v6 file. There are no rows to backfill yet; imported
            # entries below are inserted with materialized values directly.
            for statement in _MIGRATIONS[7](_strict_mode()):
                conn.execute(statement)
            for statement in _READ_MODEL_INDEXES:
                conn.execute(statement)
            for query_hash, entry in entries.items():
                raw = raw_by_hash[query_hash]
                # Preserve fields written by a newer build that this build's
                # QueryEntry does not know about.
                merged = dict(entry)
                for key, value in raw.items():
                    if key not in merged and key != "parameter_history":
                        merged[key] = value
                for lifecycle in merged.get("target_lifecycle", {}).values():
                    if not lifecycle.get("reviewed_at"):
                        lifecycle["reviewed_at"] = seeded_at
                self._insert_entry(conn, query_hash, merged)
            _prune_system_only_entries(conn)
            self._canonicalize_placeholder_hashes(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION:d}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        if entries:
            logger.info(
                "Imported %d registry entries from %s into %s",
                len(entries),
                self._toml_path,
                self._db_path,
            )

    # -- reads ----------------------------------------------------------------

    def load_all(self) -> Dict[str, Dict[str, Any]]:
        """Return every entry as {hash: entry_dict}, in insertion order.

        A fresh data dir (no library.db, no legacy TOML) serves an empty
        registry without touching the filesystem; the store file is
        created by the first write, or by a load that imports a TOML.
        """
        if (
            not self._opened
            and not self._db_path.exists()
            and not self._toml_path.exists()
        ):
            return {}
        self.ensure_open()
        conn = self._connect()
        try:
            return self._load_all(conn)
        finally:
            conn.close()

    def _load_all(self, conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
        entries: Dict[str, Dict[str, Any]] = {}
        by_id: Dict[int, Dict[str, Any]] = {}
        for row in conn.execute(_IDENTITY_SELECT + " ORDER BY id"):
            entry = _decode_identity(row)
            entries[row["hash"]] = entry
            by_id[row["id"]] = entry
        for row in conn.execute(_LIFECYCLE_SELECT):
            entry = by_id.get(row["identity_id"])
            if entry is not None:
                entry["target_lifecycle"][row["target_key"]] = _decode_lifecycle(row)
        return entries

    def _backfill_read_model(self, conn: sqlite3.Connection) -> None:
        """Populate v7 in bounded batches without loading the registry.

        One streaming join replaces the old full ``load_all`` mapping and its
        per-identity lookup. Batched ``executemany`` updates cap Python memory
        independently of registry size while the outer migration transaction
        keeps the precious store atomic.
        """
        update_sql = (
            "UPDATE target_query SET "
            + ", ".join(f"{column} = ?" for column in _READ_MODEL_COLUMNS)
            + " WHERE identity_id = ? AND target_key = ?"
        )
        cursor = conn.execute(_READ_MODEL_JOIN_SELECT)
        while rows := cursor.fetchmany(1000):
            updates = []
            for row in rows:
                entry = self._decode_library_entry(row)
                target_key = row["target_key"]
                lifecycle = entry["target_lifecycle"][target_key]
                values = _read_model_values(row["hash"], entry, lifecycle)
                updates.append(
                    [values[column] for column in _READ_MODEL_COLUMNS]
                    + [row["identity_id"], target_key]
                )
            conn.executemany(update_sql, updates)

    def _load_entry_by_id(
        self, conn: sqlite3.Connection, identity_id: int
    ) -> Dict[str, Any]:
        row = conn.execute(
            _IDENTITY_SELECT + " WHERE id = ?", (identity_id,)
        ).fetchone()
        entry = _decode_identity(row)
        for lifecycle_row in conn.execute(
            _LIFECYCLE_SELECT + " WHERE identity_id = ?", (identity_id,)
        ):
            entry["target_lifecycle"][lifecycle_row["target_key"]] = (
                _decode_lifecycle(lifecycle_row)
            )
        return entry

    def _canonicalize_placeholder_hashes(self, conn: sqlite3.Connection) -> int:
        """Schema v8 data migration: one identity per logical query.

        hash_sql now canonicalizes engine placeholder styles ($N from
        pg_stat_statements texts, anonymous ? from MySQL digests) onto :pN
        before hashing, so texts that differ only in placeholder style share
        one hash. Re-key every row whose stored hash matches the previous
        derivation of its own text, and merge rows that now collide:
        lifecycle fields union per target (earliest
        first_observed_at/reviewed_at/saved_at, latest last_*_at, max
        counters, union of sources), the row with the richer curation
        survives and takes the canonical hash, and every re-key and merge is
        logged. Rows whose stored hash matches neither derivation
        (hand-imported, or minted by an older normalizer) are left
        untouched, as are rows whose text carries no engine placeholders.
        Idempotent: a second run finds every affected row already canonical.
        """
        from shared.query_registry.query_registry import _sql_digest, normalize_sql
        from shared.query_registry.sql_normalizer import (
            canonicalize_placeholder_style,
        )

        moves: Dict[str, list[tuple[int, str]]] = {}
        rows = conn.execute(
            "SELECT id, hash, sql, original_sql FROM query_identity"
        ).fetchall()
        for row in rows:
            text = row["original_sql"] or row["sql"]
            if not text:
                continue
            try:
                normalized = normalize_sql(text)
                canonical_text = canonicalize_placeholder_style(normalized)
            except Exception:
                continue
            if canonical_text == normalized:
                continue
            canonical = _sql_digest(canonical_text)
            if row["hash"] == canonical or row["hash"] != _sql_digest(normalized):
                continue
            moves.setdefault(canonical, []).append((row["id"], row["hash"]))

        merged_groups = 0
        for canonical, movers in moves.items():
            members = list(movers)
            existing = conn.execute(
                "SELECT id, hash FROM query_identity WHERE hash = ?", (canonical,)
            ).fetchone()
            if existing is not None:
                members.append((existing["id"], existing["hash"]))
            if len(members) == 1:
                identity_id, old_hash = members[0]
                conn.execute(
                    "UPDATE query_identity SET hash = ? WHERE id = ?",
                    (canonical, identity_id),
                )
                conn.execute(
                    "UPDATE target_query SET rm_hash = ? WHERE identity_id = ?",
                    (canonical, identity_id),
                )
                logger.info(
                    "Re-keyed query identity %s to canonical hash %s",
                    old_hash,
                    canonical,
                )
                continue
            entries = {
                identity_id: self._load_entry_by_id(conn, identity_id)
                for identity_id, _ in members
            }
            survivor_id, survivor_hash = max(
                members,
                key=lambda member: (_curation_rank(entries[member[0]]), -member[0]),
            )
            merged = entries[survivor_id]
            for identity_id, _ in members:
                if identity_id != survivor_id:
                    _merge_identity(merged, entries[identity_id])
            merged["hash"] = canonical
            for identity_id, _ in members:
                conn.execute(
                    "DELETE FROM target_query WHERE identity_id = ?", (identity_id,)
                )
                conn.execute(
                    "DELETE FROM query_identity WHERE id = ?", (identity_id,)
                )
            self._insert_entry(conn, canonical, merged)
            merged_groups += 1
            logger.info(
                "Merged duplicate query identities %s into canonical hash %s "
                "(survivor %s)",
                ", ".join(old for _, old in members if old != survivor_hash),
                canonical,
                survivor_hash,
            )
        if merged_groups:
            logger.info(
                "Placeholder-style canonicalization merged %d duplicate "
                "identity groups",
                merged_groups,
            )
        return merged_groups

    @staticmethod
    def _dimension_predicates(
        *,
        view: str,
        source: str,
        params: str,
        activity: str,
        impact: str,
        now_ms: float,
    ) -> Dict[str, str]:
        return {
            "view": _VIEW_COLUMNS[view],
            "source": _SOURCE_COLUMNS[source],
            "params": _PARAM_COLUMNS[params],
            "activity": _activity_sql(activity, now_ms),
            "impact": _impact_sql(impact),
        }

    @staticmethod
    def _search_clause(search: str, values: Dict[str, Any]) -> str:
        from features.query_registry import read_model

        normalized = read_model._normalize_search(search)
        if not normalized:
            return "1"
        values["search"] = normalized
        values["search_prefix"] = _escape_like(normalized) + "%"
        values["search_contains"] = "%" + _escape_like(normalized) + "%"
        values["search_length"] = len(normalized)
        return """
            (
              rm_hash = :search
              OR (
                :search_length >= 4
                AND rm_hash LIKE :search_prefix ESCAPE '\\'
                AND (
                  SELECT COUNT(*) FROM target_query AS prefix_match
                  WHERE prefix_match.target_key = :target
                    AND prefix_match.rm_hash LIKE :search_prefix ESCAPE '\\'
                ) = 1
              )
              OR rm_search_text LIKE :search_contains ESCAPE '\\'
            )
        """

    @staticmethod
    def _decode_library_entry(row: sqlite3.Row) -> Dict[str, Any]:
        entry: Dict[str, Any] = {"hash": row["hash"]}
        for column in _TEXT_COLUMNS:
            entry[column] = row[column]
        for column in _INT_COLUMNS:
            entry[column] = _finite_int(row[column])
        for column in _REAL_COLUMNS:
            entry[column] = _finite_float(row[column])
        for column in _JSON_COLUMNS:
            entry[column] = json.loads(row[column])
        entry.update(json.loads(row["identity_extra"]))
        lifecycle: Dict[str, Any] = {}
        for column in _LIFECYCLE_TEXT_COLUMNS + _LIFECYCLE_INT_COLUMNS:
            lifecycle[column] = row[column]
        lifecycle["sources"] = json.loads(row["sources"])
        lifecycle.update(json.loads(row["lifecycle_extra"]))
        entry["target_lifecycle"] = {row["target_key"]: lifecycle}
        return entry

    def query_library(
        self,
        *,
        target: str,
        search: str,
        view: str,
        source: str,
        params: str,
        activity: str,
        impact: str,
        sort: str,
        cursor_position: Optional[tuple[float, str]],
        limit: int,
        now_ms: float,
    ) -> tuple[
        list[Dict[str, Any]],
        Dict[str, Dict[str, int]],
        int,
        Optional[tuple[float, str]],
    ]:
        """Read one indexed target page and full-set facet counts in SQLite."""
        if (
            not self._opened
            and not self._db_path.exists()
            and not self._toml_path.exists()
        ):
            from features.query_registry import read_model

            return [], read_model.empty_facet_counts(), 0, None
        self.ensure_open()
        values: Dict[str, Any] = {"target": target}
        search_clause = self._search_clause(search, values)
        selected = self._dimension_predicates(
            view=view,
            source=source,
            params=params,
            activity=activity,
            impact=impact,
            now_ms=now_ms,
        )

        dimensions: Dict[str, Dict[str, str]] = {
            "view": _VIEW_COLUMNS,
            "source": _SOURCE_COLUMNS,
            "params": _PARAM_COLUMNS,
            "activity": {
                name: _activity_sql(name, now_ms) for name in _ACTIVITY_WINDOWS_MS
            },
            "impact": {name: _impact_sql(name) for name in _IMPACT_THRESHOLDS_MS},
        }
        aggregates = [
            "SUM(CASE WHEN "
            + " AND ".join(selected.values())
            + " THEN 1 ELSE 0 END) AS selected_total"
        ]
        for dimension, candidates in dimensions.items():
            other_predicates = [
                predicate
                for name, predicate in selected.items()
                if name != dimension
            ]
            for candidate, predicate in candidates.items():
                aggregates.append(
                    "SUM(CASE WHEN "
                    + " AND ".join([*other_predicates, predicate])
                    + " THEN 1 ELSE 0 END) AS "
                    + f"{dimension}__{candidate.replace('-', '_')}"
                )
        facet_sql = (
            "WITH candidate AS (SELECT * FROM target_query "
            "WHERE target_key = :target AND "
            + search_clause
            + ") SELECT "
            + ", ".join(aggregates)
            + " FROM candidate"
        )

        sort_column, sort_index = _SORT_COLUMNS[sort]
        page_predicates = [search_clause, *selected.values()]
        values["page_probe"] = limit + 1
        identity_columns = ", ".join(
            ["qi.hash", *[f"qi.{column}" for column in _IDENTITY_FIELDS]]
        )
        lifecycle_columns = ", ".join(
            ["tq.target_key", *[f"tq.{column}" for column in _LIFECYCLE_FIELDS]]
        )
        result_select = f"""
            SELECT {identity_columns},
                   qi.extra AS identity_extra,
                   {lifecycle_columns},
                   tq.extra AS lifecycle_extra,
                   tq.{sort_column} AS page_sort_value
        """
        if cursor_position is None:
            page_sql = result_select + f"""
                FROM target_query AS tq INDEXED BY {sort_index}
                JOIN query_identity AS qi ON qi.id = tq.identity_id
                WHERE tq.target_key = :target
                  AND {' AND '.join(page_predicates)}
                ORDER BY tq.{sort_column} DESC, tq.rm_hash ASC
                LIMIT :page_probe
            """
        else:
            values["cursor_value"], values["cursor_hash"] = cursor_position
            base_predicates = " AND ".join(page_predicates)
            # The order is metric DESC but hash ASC, so a single row-value
            # inequality cannot represent the cursor. Split it into two
            # independently indexed range seeks and cap both at page+1 before
            # merging; this avoids both OFFSET and a scan from page one.
            page_sql = f"""
                WITH lower_metric AS (
                  SELECT tq.*
                  FROM target_query AS tq INDEXED BY {sort_index}
                  WHERE tq.target_key = :target
                    AND {base_predicates}
                    AND tq.{sort_column} < :cursor_value
                  ORDER BY tq.{sort_column} DESC, tq.rm_hash ASC
                  LIMIT :page_probe
                ), same_metric AS (
                  SELECT tq.*
                  FROM target_query AS tq INDEXED BY {sort_index}
                  WHERE tq.target_key = :target
                    AND {base_predicates}
                    AND tq.{sort_column} = :cursor_value
                    AND tq.rm_hash > :cursor_hash
                  ORDER BY tq.{sort_column} DESC, tq.rm_hash ASC
                  LIMIT :page_probe
                ), page_keys AS (
                  SELECT * FROM lower_metric
                  UNION ALL
                  SELECT * FROM same_metric
                )
                {result_select}
                FROM page_keys AS tq
                JOIN query_identity AS qi ON qi.id = tq.identity_id
                ORDER BY tq.{sort_column} DESC, tq.rm_hash ASC
                LIMIT :page_probe
            """

        conn = self._connect()
        try:
            # Facets, total, and page must describe one registry generation.
            # The explicit read transaction pins one snapshot across both
            # statements (and under WAL still lets concurrent writers commit).
            conn.execute("BEGIN")
            try:
                facet_row = conn.execute(facet_sql, values).fetchone()
                rows = conn.execute(page_sql, values).fetchall()
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

        from features.query_registry import read_model

        facet_counts = read_model.empty_facet_counts()
        for dimension, candidates in dimensions.items():
            for candidate in candidates:
                alias = f"{dimension}__{candidate.replace('-', '_')}"
                facet_counts[dimension][candidate] = int(facet_row[alias] or 0)
        total = int(facet_row["selected_total"] or 0)
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        entries = [self._decode_library_entry(row) for row in page_rows]
        next_position = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_position = (float(last["page_sort_value"]), last["hash"])
        return entries, facet_counts, total, next_position

    def explain_query_library_page(self, *, target: str, sort: str) -> list[str]:
        """Return the planner detail for the simplest page of one sort.

        This deliberately mirrors the production FROM/WHERE/ORDER shape and
        is exposed only to keep the required index-use regression test small.
        """
        self.ensure_open()
        sort_column, sort_index = _SORT_COLUMNS[sort]
        conn = self._connect()
        try:
            rows = conn.execute(
                f"""
                EXPLAIN QUERY PLAN
                WITH lower_metric AS (
                  SELECT tq.identity_id, tq.{sort_column}, tq.rm_hash
                  FROM target_query AS tq INDEXED BY {sort_index}
                  WHERE tq.target_key = ? AND tq.{sort_column} < ?
                  ORDER BY tq.{sort_column} DESC, tq.rm_hash ASC
                  LIMIT 51
                ), same_metric AS (
                  SELECT tq.identity_id, tq.{sort_column}, tq.rm_hash
                  FROM target_query AS tq INDEXED BY {sort_index}
                  WHERE tq.target_key = ? AND tq.{sort_column} = ?
                    AND tq.rm_hash > ?
                  ORDER BY tq.{sort_column} DESC, tq.rm_hash ASC
                  LIMIT 51
                )
                SELECT * FROM lower_metric
                UNION ALL
                SELECT * FROM same_metric
                """,
                (target, 1.0, target, 1.0, "000000000000"),
            ).fetchall()
            return [str(row[3]) for row in rows]
        finally:
            conn.close()

    # -- writes ---------------------------------------------------------------

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """Serialized write transaction: in-process lock + BEGIN IMMEDIATE."""
        self.ensure_open()
        if self._read_only_reason:
            raise RegistryReadOnlyError(self._read_only_reason)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
            finally:
                conn.close()

    def apply_changes(
        self,
        baseline: Dict[str, Dict[str, Any]],
        current: Dict[str, Dict[str, Any]],
        *,
        validate: Optional[Callable[[Dict[str, Dict[str, Any]]], None]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Merge one writer's baseline->current delta onto the stored state.

        Mirrors the merge contract of shared.persistence.update_toml: the
        store is re-read inside the write transaction, this writer's
        changes are applied per key, ``validate`` sees the merged mapping
        before anything is written, and the merged mapping is returned so
        the caller can adopt it as its new baseline.
        """
        with self._write() as conn:
            latest = self._load_all(conn)
            merged = merge_mapping_changes(latest, baseline, current)
            if validate is not None:
                validate(merged)
            for query_hash in set(latest) - set(merged):
                conn.execute(
                    "DELETE FROM query_identity WHERE hash = ?", (query_hash,)
                )
            for query_hash, entry in merged.items():
                if latest.get(query_hash) != entry:
                    self._upsert_entry(conn, query_hash, entry)
            return merged

    def _insert_entry(
        self, conn: sqlite3.Connection, query_hash: str, entry: Dict[str, Any]
    ) -> None:
        row = _encode_identity(entry)
        columns = ("hash",) + _IDENTITY_FIELDS + ("extra",)
        values = [query_hash] + [
            row[column] for column in _IDENTITY_FIELDS + ("extra",)
        ]
        cursor = conn.execute(
            f"INSERT INTO query_identity ({', '.join(columns)})"
            f" VALUES ({', '.join('?' for _ in columns)})",
            values,
        )
        identity_id = cursor.lastrowid
        for target_key, lifecycle in (entry.get("target_lifecycle") or {}).items():
            self._insert_lifecycle(
                conn, identity_id, target_key, query_hash, entry, lifecycle
            )

    def _insert_lifecycle(
        self,
        conn: sqlite3.Connection,
        identity_id: int,
        target_key: str,
        query_hash: str,
        entry: Dict[str, Any],
        lifecycle: Dict[str, Any],
    ) -> None:
        row = _encode_lifecycle(lifecycle)
        read_model = _read_model_values(query_hash, entry, lifecycle)
        columns = (
            ("identity_id", "target_key")
            + _LIFECYCLE_FIELDS
            + ("extra",)
            + _READ_MODEL_COLUMNS
        )
        values = [identity_id, target_key] + [
            row[column] for column in _LIFECYCLE_FIELDS + ("extra",)
        ] + [read_model[column] for column in _READ_MODEL_COLUMNS]
        conn.execute(
            f"INSERT OR REPLACE INTO target_query ({', '.join(columns)})"
            f" VALUES ({', '.join('?' for _ in columns)})",
            values,
        )

    def _update_read_model_row(
        self,
        conn: sqlite3.Connection,
        identity_id: int,
        target_key: str,
        query_hash: str,
        entry: Dict[str, Any],
        lifecycle: Dict[str, Any],
    ) -> None:
        values = _read_model_values(query_hash, entry, lifecycle)
        conn.execute(
            "UPDATE target_query SET "
            + ", ".join(f"{column} = ?" for column in _READ_MODEL_COLUMNS)
            + " WHERE identity_id = ? AND target_key = ?",
            [values[column] for column in _READ_MODEL_COLUMNS]
            + [identity_id, target_key],
        )

    def _upsert_entry(
        self, conn: sqlite3.Connection, query_hash: str, entry: Dict[str, Any]
    ) -> None:
        existing = conn.execute(
            "SELECT id FROM query_identity WHERE hash = ?", (query_hash,)
        ).fetchone()
        if existing is None:
            self._insert_entry(conn, query_hash, entry)
            return
        identity_id = existing["id"]
        row = _encode_identity(entry)
        assignments = ", ".join(
            f"{column} = ?" for column in _IDENTITY_FIELDS + ("extra",)
        )
        conn.execute(
            f"UPDATE query_identity SET {assignments} WHERE id = ?",
            [row[column] for column in _IDENTITY_FIELDS + ("extra",)] + [identity_id],
        )
        lifecycles = entry.get("target_lifecycle") or {}
        conn.execute(
            "DELETE FROM target_query WHERE identity_id = ?"
            + (
                " AND target_key NOT IN (%s)"
                % ", ".join("?" for _ in lifecycles)
                if lifecycles
                else ""
            ),
            [identity_id] + list(lifecycles),
        )
        for target_key, lifecycle in lifecycles.items():
            self._insert_lifecycle(
                conn, identity_id, target_key, query_hash, entry, lifecycle
            )

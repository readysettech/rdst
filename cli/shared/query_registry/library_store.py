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
- Analysis bodies follow the same import philosophy: schema v13 reads
  ``analysis_results.toml`` into ``query_analysis`` and leaves the file
  beside its ``.pre-sqlite-<version>.bak`` copy, readable by the
  previous release.
- Entries round-trip losslessly: known QueryEntry fields map to typed
  columns, unknown (newer-build) fields ride in a JSON ``extra`` column.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from itertools import count
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
    "ANALYSIS_HISTORY_LIMIT",
    "LibraryMigrationError",
    "LibraryStore",
    "RegistryReadOnlyError",
    "SCHEMA_VERSION",
    "analysis_toml_path_for",
    "default_library_db_path",
    "library_db_path_for",
]

SCHEMA_VERSION = 13

# Analyses kept per query. A re-run appends, and the oldest beyond this
# many is dropped at insert, so the history stays bounded per query
# without a sweep.
ANALYSIS_HISTORY_LIMIT = 10

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


class LibraryMigrationError(RuntimeError):
    """A schema migration failed; library.db still holds its prior version.

    Carries the whole user-facing explanation -- which step failed, why, and
    where the pre-migration backup is -- so the message needs no traceback
    to act on.
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


def analysis_toml_path_for(registry_path: Path) -> Path:
    """Return the legacy analysis_results.toml serving a queries.toml path.

    Named on the same rule as ``library_db_path_for`` so a side-by-side
    registry keeps its own analysis history: the canonical registry reads
    ``analysis_results.toml``, any other registry filename reads
    ``<name>.analysis_results.toml``.
    """
    if registry_path.name == "queries.toml":
        return registry_path.with_name("analysis_results.toml")
    return registry_path.with_name(registry_path.name + ".analysis_results.toml")


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


def _schema_v13(strict: bool) -> tuple[str, ...]:
    """Analysis bodies, the history the results viewer reopens.

    One row per analysis run: the indexed identity columns answer the
    history list and the setup signals, and ``payload`` carries the whole
    AnalysisResult so a stored run round-trips unchanged.

    The DDL is written IF NOT EXISTS so the step is re-runnable: a fresh
    store creates this table alongside v1 and v7, and the ladder must be
    able to pass over it again on a file whose version was rewound.
    """
    s_only = " STRICT" if strict else ""
    return (
        f"""
        CREATE TABLE IF NOT EXISTS query_analysis (
          id INTEGER PRIMARY KEY,
          query_hash TEXT NOT NULL,
          analysis_id TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT '',
          target TEXT NOT NULL DEFAULT '',
          payload TEXT NOT NULL DEFAULT '{{}}'
        ){s_only}""",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_qa_analysis"
        " ON query_analysis(query_hash, analysis_id)",
        "CREATE INDEX IF NOT EXISTS ix_qa_history"
        " ON query_analysis(query_hash, created_at DESC, id DESC)",
        "CREATE INDEX IF NOT EXISTS ix_qa_target ON query_analysis(target)",
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
    # Version 9 is a data migration with no DDL: it repairs identities
    # written by the digit-lifting normalizer (fused $:pN text, lifted
    # placeholder digits stored as parameter values, hashes derived from
    # the damaged text); see _repair_placeholder_artifacts.
    9: lambda strict: (),
    # Version 10 is a data migration with no DDL: it finishes the v9 repair
    # on databases v9 already migrated, and re-points cache.db observation
    # history at every re-keyed identity; see _repair_v9_residue.
    10: lambda strict: (),
    # Version 11 is a data migration with no DDL: it prunes the RDST
    # diagnostic statements admitted before the self markers existed; see
    # _prune_self_traffic.
    11: lambda strict: (),
    # Version 12 is a data migration with no DDL: it drops the catalog and
    # probe statements that reference no user relation, whatever review or
    # save intent they collected on the way in; see _prune_system_statements.
    12: lambda strict: (),
    # Version 13 moves analysis bodies out of analysis_results.toml and into
    # query_analysis, importing the file inside this transaction; see
    # _import_analysis_toml.
    13: _schema_v13,
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

# Sources RDST itself writes: automatic observation plus the cache pipeline,
# which admits whatever a cache run happened to touch. Every other source
# names a person choosing a query, so v12 leaves those entries alone.
_SYSTEM_ADMITTED_SOURCES = _AUTO_OBSERVED_SOURCES | {"cache"}


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


# Fused placeholder artifact written by the digit-lifting normalizer: a
# `$N` parameter whose digit was extracted as if it were a literal value,
# leaving `$:pN` in stored text. Genuine `$N` placeholders never match.
_FUSED_PLACEHOLDER = re.compile(r"\$(:p\d+\b)")

# Digit-lifting damage always leaves a placeholder token in the stored
# text (`:pN` from the lift itself, or `$N` alongside it); texts spelling
# none are never re-derived, keeping the v9 scan cheap on large stores.
_REPAIR_CANDIDATE = re.compile(r":p\d|\$\d")

# A select-list ordinal in the one position the digit-lifting normalizer
# also extracted; a text spelling one normalizes differently under it.
_ORDINAL_HINT = re.compile(r"\b(?:GROUP|ORDER)\s+BY\s+\d", re.IGNORECASE)

# A `$N` placeholder a dialect-less regeneration read as a string literal:
# the stored text spells `'$N'` where the original text spells `$N`.
_QUOTED_PLACEHOLDER = re.compile(r"'\$\d+'")

# A GROUP BY / ORDER BY list built entirely from `:pN` terms, which is
# what a lifted select-list ordinal leaves behind. Lists mixing ordinals
# with expressions are left alone: their spelling is not provable.
_ORDINAL_CLAUSE = re.compile(
    r"(\b(?:GROUP|ORDER)\s+BY\s+)"
    r"(:p\d+(?:\s+(?:ASC|DESC))?(?:\s*,\s*:p\d+(?:\s+(?:ASC|DESC))?)*)",
    re.IGNORECASE,
)
_PLACEHOLDER_NAME = re.compile(r":p(\d+)\b")

# Placeholder canonicalization as it stood before `$:pN` counted as one
# token, kept verbatim because it is the frozen spelling that older
# identity hashes digest.
_LEGACY_PLACEHOLDER_TOKEN = re.compile(r":p\d+\b|\$\d+\b|(?<![?@])\?(?![?|&])")


def _param_value(params: Dict[str, Any], name: str) -> str:
    """Read one parameter value from either stored parameter shape."""
    value = params.get(name)
    if isinstance(value, dict):
        value = value.get("value")
    return "" if value is None else str(value)


def _has_explicit_source(params: Dict[str, Any]) -> bool:
    """True when stored values record where they came from.

    Only the typed parameter shape carries provenance, and only a caller
    that knows it writes one (see query_registry.typed_parameter): a user
    typing values into a dialog, or a suggestion they accepted. Such values
    name real input whatever they spell.
    """
    return any(
        isinstance(value, dict) and value.get("source")
        for value in params.values()
    )


def _lifted_slot_indices(params: Dict[str, Any]) -> bool:
    """True when parameter values are the slot indices a digit lift stored.

    The digit-lifting normalizer numbered slots in AST-traversal order and
    recorded each slot's own index as its "value", so the values of such
    an entry are exactly 1..N in some order rather than observed data.
    Values carrying their own provenance are exempt: `{"p1": "1"}` is a
    perfectly ordinary thing to ask a query about.
    """
    if not params or _has_explicit_source(params):
        return False
    values = [_param_value(params, name) for name in params]
    if not all(value.isdigit() for value in values):
        return False
    return sorted(int(value) for value in values) == list(
        range(1, len(values) + 1)
    )


def _restore_ordinals(text: str, params: Dict[str, Any]) -> str:
    """Put GROUP BY / ORDER BY select-list ordinals back into a stored text.

    A prior normalizer extracted those ordinals as if they were values,
    leaving `:pN` in the clause and the digit among the entry's
    parameters. Substituting the digits back reproduces the text the
    current pipeline normalizes, so the entry can be re-keyed onto the
    identity that live observations of the same query mint. A clause whose
    parameters carry a non-digit value keeps its stored spelling.
    """

    def restore_clause(clause: re.Match[str]) -> str:
        def restore_name(name: re.Match[str]) -> str:
            value = _param_value(params, f"p{name.group(1)}")
            return value if value.isdigit() else name.group(0)

        return clause.group(1) + _PLACEHOLDER_NAME.sub(
            restore_name, clause.group(2)
        )

    return _ORDINAL_CLAUSE.sub(restore_clause, text)


def _legacy_normalized(text: str) -> str:
    """Reproduce the digit-lifting normalization a stale hash digests.

    That normalizer extracted every non-JSON-path literal, including the
    index digit naming a `$N` slot and GROUP BY/ORDER BY ordinals, and its
    regex fallback lifted a digit behind a `$` the same way. Identity
    hashes minted under it digest a spelling the current pipeline no
    longer produces, so reproducing it is how a migration recognizes such
    a hash as one of its own rows.
    """
    from sqlglot import exp, parse_one

    from shared.query_registry.query_registry import canonicalize_sql
    from shared.query_registry.sql_normalizer import (
        _compat_generator,
        _is_json_path_literal,
    )

    canonical = canonicalize_sql(text)
    if not canonical:
        return ""
    try:
        tree = parse_one(canonical, dialect=None)
    except Exception:
        tree = None
    if tree is None:
        position = count(1)
        collapsed = re.sub(r"\s+", " ", canonical)
        collapsed = re.sub(
            r"'[^']*'", lambda _: f":p{next(position)}", collapsed
        )
        return re.sub(
            r"\b\d+(?:\.\d+)?\b", lambda _: f":p{next(position)}", collapsed
        ).strip()
    literals = [
        literal
        for literal in tree.find_all(exp.Literal)
        if not _is_json_path_literal(literal)
    ]
    for index, literal in enumerate(literals, 1):
        literal.replace(exp.Placeholder(this=f"p{index}"))
    return _compat_generator(None).generate(tree)


def _legacy_canonicalized(normalized: str) -> str:
    """Renumber placeholders the way the pre-`$:pN` canonicalization did."""
    position = count(1)
    return _LEGACY_PLACEHOLDER_TOKEN.sub(
        lambda _: f":p{next(position)}", normalized
    )


def _drop_unnamed_parameters(entry: Dict[str, Any]) -> None:
    """Drop parameter values naming a placeholder the text does not spell."""
    named = {
        f"p{number}"
        for number in _PLACEHOLDER_NAME.findall(str(entry.get("sql") or ""))
    }
    for column in ("parameters", "most_recent_params"):
        values = entry.get(column)
        if isinstance(values, dict):
            entry[column] = {
                name: value for name, value in values.items() if name in named
            }


def _repaired_parameters(
    sql: str,
    original: str,
    params: Dict[str, Any],
    observed: Dict[str, Any],
    dialect: Optional[str],
    query_hash: str,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Return the parameter values an entry's own text supports.

    An entry minted from literal-bearing SQL keeps its values in the
    numbering its stored text spells, so re-extracting the original
    through the current pipeline is authoritative whenever the extraction
    regenerates exactly that text. Failing that, values that reproduce the
    entry's own identity when substituted back are the ones it was minted
    from. An entry minted from engine-normalized text instead holds values
    the activity sampler keyed by slot position; those stand unless they
    are the slot indices a digit lift stored (see _lifted_slot_indices),
    which name no observation at all.
    """
    from shared.query_registry.query_registry import (
        _identity_slot_count,
        canonicalize_sql,
        hash_sql,
    )
    from shared.query_registry.sql_normalizer import (
        normalize_and_extract,
        reconstruct_sql,
    )

    slots = _identity_slot_count(sql)
    if original and not slots:
        try:
            regenerated, extracted = normalize_and_extract(
                canonicalize_sql(original), dialect
            )
        except Exception:
            regenerated, extracted = "", {}
        if extracted and regenerated == sql:
            return extracted, {
                name: info["value"] for name, info in extracted.items()
            }
    if params and not slots:
        try:
            substituted = reconstruct_sql(sql, params, dialect)
        except Exception:
            substituted = sql
        if substituted != sql and hash_sql(substituted) == query_hash:
            return params, observed
    # most_recent_params stores bare values, so the typed parameters are the
    # only place provenance can be read; when they carry it, the pair of
    # them belongs to whoever wrote them.
    if _lifted_slot_indices(params) or (
        not _has_explicit_source(params) and _lifted_slot_indices(observed)
    ):
        return {}, {}
    # A slot-bearing text resolves its k-th engine slot as `pk`; a text
    # spelling `:pN` names its own.
    named = (
        {f"p{index}" for index in range(1, slots + 1)}
        if slots
        else {f"p{number}" for number in _PLACEHOLDER_NAME.findall(sql)}
    )
    return (
        {name: value for name, value in params.items() if name in named},
        {name: value for name, value in observed.items() if name in named},
    )


def _normalized_text(
    original: str, dialect: Optional[str], query_hash: str
) -> str:
    """Normalize an entry's original text, diagnosing a text that cannot."""
    from shared.query_registry.query_registry import normalize_sql

    try:
        return normalize_sql(original, dialect)
    except Exception:
        logger.warning(
            "Query identity %s keeps its stored text: the original does not "
            "normalize",
            query_hash,
            exc_info=True,
        )
        return ""


def _dialect_resolver() -> Callable[[str], Optional[str]]:
    """Memoize target-to-dialect resolution across one migration pass.

    Stored normalized text is generated with the dialect of the target it
    came from, so regenerating it identity-safely needs the same dialect;
    resolution reads the targets config, so one lookup per target is
    plenty.
    """
    from shared.query_registry.query_registry import dialect_for_target

    resolved: Dict[str, Optional[str]] = {}

    def resolve(target: str) -> Optional[str]:
        if target not in resolved:
            resolved[target] = dialect_for_target(target)
        return resolved[target]

    return resolve


def _scrub_placeholder_artifacts(entry: Dict[str, Any]) -> bool:
    """Fold the fused `$:pN` artifact to `:pN` in an entry's stored texts.

    Entries carrying the artifact also carry parameter "observations" that
    are really the lifted placeholder digits. Stored values have no
    provenance that could separate those from real observations, so
    affected entries drop all observed parameter values; the activity
    sampler repopulates them from live traffic. Returns True when the
    entry carried the artifact.
    """
    affected = False
    for column in ("sql", "original_sql"):
        text = str(entry.get(column) or "")
        folded = _FUSED_PLACEHOLDER.sub(r"\1", text)
        if folded != text:
            entry[column] = folded
            affected = True
    if affected:
        entry["parameters"] = {}
        entry["most_recent_params"] = {}
    return affected


def _json_dumps(value: Any) -> str:
    # default=str: a hand-edited legacy TOML may parse timestamps into
    # datetime objects; degrade them to strings instead of failing import.
    return json.dumps(value, sort_keys=True, default=str)


def _decode_analysis_payload(payload: Any) -> Dict[str, Any]:
    """Decode a stored analysis body, tolerating a row written by hand."""
    try:
        decoded = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


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
        # Identity re-keys this process's migrations performed, drained by
        # _rekey_observation_history once the migration has committed.
        self._identity_moves: Dict[str, str] = {}

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
                    if version < SCHEMA_VERSION:
                        backup = self._backup_before_migration(conn, version)
                        try:
                            self._migrate(conn, version)
                        except BaseException as exc:
                            raise LibraryMigrationError(
                                self._migration_failure_message(
                                    version, backup, exc
                                )
                            ) from None
            finally:
                conn.close()
            self._rekey_observation_history()
            self._opened = True

    def _backup_before_migration(
        self, conn: sqlite3.Connection, version: int
    ) -> Path:
        """Copy the file aside before the ladder changes anything.

        One backup per source version: an install that upgrades from v9 keeps
        ``library.db.pre-v9.bak`` and never rewrites it, so a second attempt
        after a failed migration still restores the original. The copy goes
        through SQLite's own backup API, which captures committed WAL frames
        the raw file does not yet hold, and lands atomically under its final
        name so a partial copy is never mistaken for a backup.
        """
        backup = self._db_path.with_name(f"{self._db_path.name}.pre-v{version}.bak")
        if backup.exists():
            return backup
        staging = backup.with_name(backup.name + ".tmp")
        staging.unlink(missing_ok=True)
        target = sqlite3.connect(staging)
        try:
            conn.backup(target)
        finally:
            target.close()
        os.replace(staging, backup)
        logger.info("Backed up %s to %s before migrating", self._db_path, backup)
        return backup

    def _migration_failure_message(
        self, from_version: int, backup: Path, exc: BaseException
    ) -> str:
        """Explain a failed migration in one actionable sentence sequence.

        The version is re-read rather than assumed: a ladder that failed on
        its third step has already committed the first two.
        """
        stored = from_version
        try:
            conn = self._connect()
            try:
                stored = conn.execute("PRAGMA user_version").fetchone()[0]
            finally:
                conn.close()
        except sqlite3.Error:
            pass
        return (
            f"library.db at {self._db_path} could not be migrated to schema "
            f"{stored + 1:d} ({type(exc).__name__}: {exc}). The migration was "
            f"rolled back, so the file still holds every query at schema "
            f"{stored:d}, and a copy of it as it stood before this upgrade is "
            f"at {backup}. Reinstall the previous rdst to keep working, and "
            "report this message."
        )

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
                if version == 9:
                    self._repair_placeholder_artifacts(conn)
                if version == 10:
                    self._repair_v9_residue(conn)
                if version == 11:
                    self._prune_self_traffic(conn)
                if version == 12:
                    self._prune_system_statements(conn)
                if version == 13:
                    self._import_analysis_toml(conn)
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
            for statement in _MIGRATIONS[13](_strict_mode()):
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
            self._repair_placeholder_artifacts(conn)
            self._repair_v9_residue(conn)
            self._prune_self_traffic(conn)
            self._prune_system_statements(conn)
            self._import_analysis_toml(conn)
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

    def _import_analysis_toml(self, conn: sqlite3.Connection) -> int:
        """Import analysis_results.toml into query_analysis, once.

        Runs inside the caller's transaction, so a failure rolls the whole
        migration back and the TOML stays authoritative for the next
        attempt. The file is copied to
        ``analysis_results.toml.pre-sqlite-<version>.bak`` and left in
        place, readable by the previous release, matching what the
        queries.toml import does. Rows already present are ignored, so a
        second pass imports nothing twice.

        A TOML this build cannot parse is reported and skipped rather than
        failing the upgrade: the query library is the precious data, and an
        unreadable analysis sidecar must not hold it at the old schema.
        """
        path = analysis_toml_path_for(self._toml_path)
        if not path.exists():
            return 0
        try:
            data = toml.load(path)
        except Exception as exc:
            logger.warning("Skipping unreadable analysis history at %s: %s", path, exc)
            return 0

        imported = 0
        for query_hash, analyses in (data.get("results") or {}).items():
            if not isinstance(analyses, dict):
                continue
            bodies = [body for body in analyses.values() if isinstance(body, dict)]
            bodies.sort(key=lambda body: str(body.get("timestamp") or ""), reverse=True)
            for body in bodies[:ANALYSIS_HISTORY_LIMIT]:
                analysis_id = str(body.get("analysis_id") or "")
                if not analysis_id:
                    continue
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO query_analysis"
                    " (query_hash, analysis_id, created_at, target, payload)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        query_hash,
                        analysis_id,
                        str(body.get("timestamp") or ""),
                        str(body.get("target") or ""),
                        _json_dumps(body),
                    ),
                )
                imported += cursor.rowcount or 0

        backup = path.with_name(f"{path.name}.pre-sqlite-{SCHEMA_VERSION}.bak")
        if not backup.exists():
            shutil.copy2(path, backup)
        if imported:
            logger.info(
                "Imported %d stored analyses from %s into %s",
                imported,
                path,
                self._db_path,
            )
        return imported

    # -- analyses -------------------------------------------------------------

    def record_analysis(
        self,
        *,
        query_hash: str,
        analysis_id: str,
        created_at: str,
        target: str,
        payload: Dict[str, Any],
        keep: int = ANALYSIS_HISTORY_LIMIT,
    ) -> None:
        """Append one analysis, then drop the oldest beyond ``keep``.

        A re-run appends rather than replacing, so before/after stays
        comparable; the same analysis id written twice updates its own row.
        """
        with self._write() as conn:
            conn.execute(
                "INSERT INTO query_analysis"
                " (query_hash, analysis_id, created_at, target, payload)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(query_hash, analysis_id) DO UPDATE SET"
                " created_at = excluded.created_at, target = excluded.target,"
                " payload = excluded.payload",
                (query_hash, analysis_id, created_at, target, _json_dumps(payload)),
            )
            self._prune_analysis_history(conn, query_hash, keep)

    def update_analysis_payload(
        self, query_hash: str, analysis_id: str, payload: Dict[str, Any]
    ) -> bool:
        """Replace one stored analysis body; False when the row is gone."""
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE query_analysis SET payload = ?"
                " WHERE query_hash = ? AND analysis_id = ?",
                (_json_dumps(payload), query_hash, analysis_id),
            )
            return bool(cursor.rowcount)

    def analysis_payloads(self, query_hash: str) -> list[Dict[str, Any]]:
        """Every stored body for one query, newest first."""
        if not self._analyses_readable():
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT payload FROM query_analysis WHERE query_hash = ?"
                " ORDER BY created_at DESC, id DESC",
                (query_hash,),
            ).fetchall()
        finally:
            conn.close()
        return [_decode_analysis_payload(row["payload"]) for row in rows]

    def analysis_payload(
        self, query_hash: str, analysis_id: str
    ) -> Optional[Dict[str, Any]]:
        """One stored body by id, or None when it was never stored or aged out."""
        if not self._analyses_readable():
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT payload FROM query_analysis"
                " WHERE query_hash = ? AND analysis_id = ?",
                (query_hash, analysis_id),
            ).fetchone()
        finally:
            conn.close()
        return _decode_analysis_payload(row["payload"]) if row else None

    def analyzed_query_hashes(self) -> list[str]:
        """Query hashes that hold an analysis, most recently analyzed first."""
        if not self._analyses_readable():
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT query_hash FROM query_analysis GROUP BY query_hash"
                " ORDER BY MAX(created_at) DESC"
            ).fetchall()
        finally:
            conn.close()
        return [row["query_hash"] for row in rows]

    def delete_analyses(self, query_hash: str) -> int:
        """Drop every stored analysis for one query; returns how many went."""
        if not self._analyses_readable():
            return 0
        with self._write() as conn:
            cursor = conn.execute(
                "DELETE FROM query_analysis WHERE query_hash = ?", (query_hash,)
            )
            return cursor.rowcount or 0

    def prune_analyses(self, keep: int) -> int:
        """Trim every query's history to ``keep``; returns how many went."""
        if not self._analyses_readable():
            return 0
        with self._write() as conn:
            removed = 0
            for row in conn.execute(
                "SELECT query_hash FROM query_analysis GROUP BY query_hash"
                " HAVING COUNT(*) > ?",
                (max(keep, 0),),
            ).fetchall():
                removed += self._prune_analysis_history(conn, row["query_hash"], keep)
            return removed

    @staticmethod
    def _prune_analysis_history(
        conn: sqlite3.Connection, query_hash: str, keep: int
    ) -> int:
        cursor = conn.execute(
            "DELETE FROM query_analysis WHERE query_hash = ? AND id NOT IN ("
            "  SELECT id FROM query_analysis WHERE query_hash = ?"
            "  ORDER BY created_at DESC, id DESC LIMIT ?"
            ")",
            (query_hash, query_hash, max(keep, 0)),
        )
        return cursor.rowcount or 0

    def _analyses_readable(self) -> bool:
        """Whether opening the store could serve an analysis at all.

        A data dir with neither a store file nor a legacy TOML has no
        history to read, and reading must not create the file.
        """
        if (
            not self._opened
            and not self._db_path.exists()
            and not self._toml_path.exists()
        ):
            return False
        self.ensure_open()
        return True

    # -- setup signals --------------------------------------------------------

    def setup_signals(self, target: str) -> Dict[str, bool]:
        """Answer the library-derived setup-guide questions in one connection.

        ``queries_found`` counts only user workload: RDST's own diagnostic
        statements are matched by structure and skipped, so a target that
        has seen nothing but profiling reads still reads as empty.
        """
        signals = {"queries_found": False, "analyzed": False, "compared": False}
        if not target or not self._analyses_readable():
            return signals
        from shared.query_registry.self_traffic import match_self_template

        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT qi.sql, qi.original_sql FROM target_query AS tq"
                " JOIN query_identity AS qi ON qi.id = tq.identity_id"
                " WHERE tq.target_key = ?",
                (target,),
            )
            signals["queries_found"] = any(
                match_self_template(row["original_sql"] or row["sql"]) is None
                for row in rows
            )
            signals["analyzed"] = (
                conn.execute(
                    "SELECT 1 FROM query_analysis WHERE target = ? LIMIT 1",
                    (target,),
                ).fetchone()
                is not None
            )
            signals["compared"] = (
                conn.execute(
                    "SELECT 1 FROM target_query"
                    " WHERE target_key = ? AND comparison_count > 0 LIMIT 1",
                    (target,),
                ).fetchone()
                is not None
            )
        finally:
            conn.close()
        return signals

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

    def _record_identity_move(self, old_hash: str, new_hash: str) -> None:
        """Note a re-key so observation history can follow it after commit."""
        if not old_hash or old_hash == new_hash:
            return
        for source in [
            source
            for source, destination in self._identity_moves.items()
            if destination == old_hash
        ]:
            self._identity_moves[source] = new_hash
        self._identity_moves[old_hash] = new_hash

    def _apply_identity_moves(
        self,
        conn: sqlite3.Connection,
        entries: Dict[int, Dict[str, Any]],
        targets: Dict[int, str],
        old_hashes: Dict[int, str],
    ) -> tuple[int, int]:
        """Write repaired entries onto their target hashes, merging collisions.

        Every prepared row is vacated before target-slot occupancy is
        checked, so a slot freed by one move can be claimed by another and
        occupancy checks only ever see identities staying put. A move onto
        an occupied slot merges with the v8 lifecycle union: earliest
        first_observed_at/reviewed_at/saved_at, latest last_*_at, max
        counters, union of sources, richest curation surviving. The
        occupant's parameters are filtered exactly as a mover's are, so
        neither side can pass lifted digits off as curated values.
        Returns (rows re-keyed, groups merged).
        """
        for identity_id in entries:
            conn.execute(
                "DELETE FROM target_query WHERE identity_id = ?", (identity_id,)
            )
            conn.execute("DELETE FROM query_identity WHERE id = ?", (identity_id,))

        groups: Dict[str, list[int]] = {}
        for identity_id, canonical in targets.items():
            groups.setdefault(canonical, []).append(identity_id)

        rekeyed = 0
        merged_groups = 0
        for canonical, member_ids in groups.items():
            members = {mid: entries[mid] for mid in member_ids}
            member_hashes = {mid: old_hashes[mid] for mid in member_ids}
            occupant = conn.execute(
                "SELECT id FROM query_identity WHERE hash = ?", (canonical,)
            ).fetchone()
            if occupant is not None:
                occupant_entry = self._load_entry_by_id(conn, occupant["id"])
                _drop_unnamed_parameters(occupant_entry)
                members[occupant["id"]] = occupant_entry
                member_hashes[occupant["id"]] = canonical
                conn.execute(
                    "DELETE FROM target_query WHERE identity_id = ?",
                    (occupant["id"],),
                )
                conn.execute(
                    "DELETE FROM query_identity WHERE id = ?", (occupant["id"],)
                )
            if len(members) == 1:
                ((identity_id, entry),) = members.items()
                entry["hash"] = canonical
                self._insert_entry(conn, canonical, entry)
                if member_hashes[identity_id] == canonical:
                    logger.info(
                        "Repaired stored state of query identity %s", canonical
                    )
                else:
                    rekeyed += 1
                    logger.info(
                        "Re-keyed query identity %s to canonical hash %s",
                        member_hashes[identity_id],
                        canonical,
                    )
            else:
                survivor_id = max(
                    members, key=lambda mid: (_curation_rank(members[mid]), -mid)
                )
                merged = members[survivor_id]
                for identity_id, entry in members.items():
                    if identity_id != survivor_id:
                        _merge_identity(merged, entry)
                merged["hash"] = canonical
                self._insert_entry(conn, canonical, merged)
                merged_groups += 1
                logger.info(
                    "Merged query identities %s onto canonical hash %s "
                    "(survivor carried %s)",
                    ", ".join(sorted(member_hashes[mid] for mid in members)),
                    canonical,
                    member_hashes[survivor_id],
                )
            for identity_id in members:
                self._record_identity_move(member_hashes[identity_id], canonical)
        return rekeyed, merged_groups

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
                self._record_identity_move(old_hash, canonical)
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
            for _, old in members:
                self._record_identity_move(old, canonical)
        if merged_groups:
            logger.info(
                "Placeholder-style canonicalization merged %d duplicate "
                "identity groups",
                merged_groups,
            )
        return merged_groups

    def _repair_placeholder_artifacts(self, conn: sqlite3.Connection) -> int:
        """Schema v9 data migration: repair digit-lifting placeholder damage.

        A prior normalizer extracted the digit of `$N` parameters (and of
        GROUP BY/ORDER BY ordinals) as if it were a literal value, minting
        identities whose stored text carries the fused `$:pN` artifact,
        whose parameter "observations" are the lifted digits, and whose
        hashes derive from the damaged text. The v8 canonicalization left
        them alone because their stored hash matched neither derivation it
        recognized. Re-derive each placeholder-bearing identity's hash
        from its stored text through the current pipeline and re-key any
        row whose hash differs, folding the fused artifact out of stored
        text and dropping the lifted parameter values on the way (see
        _scrub_placeholder_artifacts). Re-keyed rows with an original text
        get their normalized text regenerated through the current
        pipeline, and parameter observations naming a placeholder the
        repaired text no longer spells are dropped: they are lifted
        ordinal or placeholder digits, and real values are reconstructible
        by the activity sampler. Rows without the artifact re-key only on
        proven pipeline provenance: their stored hash must equal a digest
        of their own stored text under some prior spelling (the raw
        normalized text or its textual canonicalization), which covers
        identities minted while ordinals and placeholder digits were still
        lifted; hashes matching no derivation (hand-imported data) stay
        untouched, exactly as in v8. A re-key that lands on an existing
        identity merges with the v8 lifecycle union: earliest
        first_observed_at/reviewed_at/saved_at, latest last_*_at, max
        counters, union of sources, richest curation surviving; artifacts
        are scrubbed before ranking so lifted digits cannot pass for
        curated parameters. Idempotent: after one run every candidate's
        hash equals the current derivation of its own stored text and no
        stored text carries `$:pN`.
        """
        from shared.query_registry.query_registry import _sql_digest, normalize_sql
        from shared.query_registry.sql_normalizer import (
            canonicalize_placeholder_style,
        )

        movers: Dict[int, tuple[str, str]] = {}
        rows = conn.execute(
            "SELECT id, hash, sql, original_sql FROM query_identity"
        ).fetchall()
        for row in rows:
            text = row["original_sql"] or row["sql"]
            if not text or not (
                _REPAIR_CANDIDATE.search(row["sql"])
                or _REPAIR_CANDIDATE.search(row["original_sql"])
            ):
                continue
            try:
                normalized = normalize_sql(text)
                if not normalized:
                    continue
                canonical = _sql_digest(canonicalize_placeholder_style(normalized))
            except Exception:
                logger.warning(
                    "Query identity %s (row %d) stays as stored: its text "
                    "does not normalize",
                    row["hash"],
                    row["id"],
                    exc_info=True,
                )
                continue
            damaged = bool(
                _FUSED_PLACEHOLDER.search(row["sql"])
                or _FUSED_PLACEHOLDER.search(row["original_sql"])
            )
            if canonical == row["hash"] and not damaged:
                continue
            if not damaged and row["hash"] not in {
                _sql_digest(row["sql"]),
                _sql_digest(canonicalize_placeholder_style(row["sql"])),
                _sql_digest(normalized),
            }:
                continue
            movers[row["id"]] = (row["hash"], canonical)

        dialect_of = _dialect_resolver()
        entries: Dict[int, Dict[str, Any]] = {}
        targets: Dict[int, str] = {}
        old_hashes: Dict[int, str] = {}
        for identity_id, (old_hash, canonical) in movers.items():
            entry = self._load_entry_by_id(conn, identity_id)
            _scrub_placeholder_artifacts(entry)
            original = str(entry.get("original_sql") or "")
            if original:
                # Stored text is generated with its target's dialect, which
                # is what keeps `DATE_TRUNC($1, col)` a parameter rather
                # than a string literal. Identity stays dialect-less: only
                # the displayed and reconstructed text is regenerated here.
                regenerated = _normalized_text(
                    original,
                    dialect_of(str(entry.get("last_target") or "")),
                    old_hash,
                )
                if regenerated:
                    entry["sql"] = regenerated
            _drop_unnamed_parameters(entry)
            entries[identity_id] = entry
            targets[identity_id] = canonical
            old_hashes[identity_id] = old_hash

        rekeyed, merged_groups = self._apply_identity_moves(
            conn, entries, targets, old_hashes
        )
        if movers:
            logger.info(
                "Placeholder-artifact repair touched %d identities "
                "(%d re-keyed, %d merged groups)",
                len(movers),
                rekeyed,
                merged_groups,
            )
        return len(movers)

    def _repair_v9_residue(self, conn: sqlite3.Connection) -> int:
        """Schema v10 data migration: finish the v9 placeholder repair.

        v9 re-keyed the identities the digit-lifting normalizer minted, and
        left three residues in the databases it migrated. An entry whose
        hash was already canonical kept the lifted slot indices as its
        parameter "observations". Regenerating stored text without the
        entry's dialect turned a `DATE_TRUNC($1, col)` parameter into the
        string literal `'$1'`. And an entry whose only text carries lifted
        GROUP BY/ORDER BY ordinals still hashes apart from live
        observations of the same query, because v9 re-derived from that
        same lifted text.

        Every repair here is gated on pipeline provenance: the entry's
        stored hash must equal the current derivation of its own stored
        text, which leaves hand-imported rows exactly as they are.
        Parameters are re-extracted from a literal-bearing original where
        the entry's text supports it, so real values are restored rather
        than dropped; entries whose text carries engine slots keep their
        sampled values unless those values are the slot indices themselves.
        Ordinal reconstruction re-keys through the v9 occupancy and merge
        machinery. Idempotent: a second run finds nothing to repair.
        """
        from shared.query_registry.query_registry import _sql_digest, normalize_sql
        from shared.query_registry.sql_normalizer import (
            canonicalize_placeholder_style,
        )

        dialect_of = _dialect_resolver()
        entries: Dict[int, Dict[str, Any]] = {}
        targets: Dict[int, str] = {}
        old_hashes: Dict[int, str] = {}
        for row in conn.execute(
            "SELECT id, hash, sql, original_sql, parameters, most_recent_params,"
            " last_target FROM query_identity"
        ).fetchall():
            sql = row["sql"] or ""
            original = row["original_sql"] or ""
            params = json.loads(row["parameters"])
            observed = json.loads(row["most_recent_params"])
            if not (params or observed or _QUOTED_PLACEHOLDER.search(sql)):
                continue
            text = original or sql
            try:
                derived = _sql_digest(
                    canonicalize_placeholder_style(normalize_sql(text))
                )
            except Exception:
                logger.warning(
                    "Query identity %s (row %d) stays as stored: its text "
                    "does not normalize",
                    row["hash"],
                    row["id"],
                    exc_info=True,
                )
                continue
            if derived != row["hash"]:
                continue
            dialect = dialect_of(row["last_target"])
            canonical = row["hash"]
            if original and _QUOTED_PLACEHOLDER.search(sql):
                regenerated = _normalized_text(original, dialect, row["hash"])
                if regenerated and not _QUOTED_PLACEHOLDER.search(regenerated):
                    sql = regenerated
            restored = _restore_ordinals(text, params or observed)
            if restored != text:
                try:
                    moved = _sql_digest(
                        canonicalize_placeholder_style(normalize_sql(restored))
                    )
                except Exception:
                    moved = row["hash"]
                if moved != row["hash"]:
                    canonical = moved
                    sql = _normalized_text(restored, dialect, row["hash"]) or sql
                    if original:
                        original = restored
            params, observed = _repaired_parameters(
                sql, original, params, observed, dialect, canonical
            )
            if (
                canonical == row["hash"]
                and sql == row["sql"]
                and original == row["original_sql"]
                and params == json.loads(row["parameters"])
                and observed == json.loads(row["most_recent_params"])
            ):
                continue
            entry = self._load_entry_by_id(conn, row["id"])
            entry["sql"] = sql
            entry["original_sql"] = original
            entry["parameters"] = params
            entry["most_recent_params"] = observed
            entries[row["id"]] = entry
            targets[row["id"]] = canonical
            old_hashes[row["id"]] = row["hash"]

        rekeyed, merged_groups = self._apply_identity_moves(
            conn, entries, targets, old_hashes
        )
        self._identity_moves.update(self._stale_identity_hashes(conn))
        if entries:
            logger.info(
                "Placeholder-residue repair touched %d identities "
                "(%d re-keyed, %d merged groups)",
                len(entries),
                rekeyed,
                merged_groups,
            )
        return len(entries)

    def _prune_self_traffic(self, conn: sqlite3.Connection) -> int:
        """Schema v11 data migration: drop RDST's own diagnostic statements.

        Schema profiling reads user relations, so its statements look like
        user workload to automatic discovery. They now carry an
        ``/*rdst:...*/`` self marker that admission rejects, which no marker
        can do for rows admitted before it existed. Those rows are matched
        here by structure: the stored text has to be an exact instance of one
        of the profiler, introspector, or pattern-detector templates
        (see shared.query_registry.self_traffic).

        Curation wins over the match, on the same terms
        _prune_system_only_entries uses: a row someone saved, analyzed,
        compared, asked about, or cached stays, and every such skip is
        logged. reviewed_at is not curation here either -- a bulk review
        sweep stamps it across whatever the list happened to show.

        Observation history in cache.db is left alone, matching the earlier
        prunes: cache.db is rebuildable, its recent_observation rows age out
        on their own retention cutoff, and a pruned hash simply stops being
        looked up. Re-keys recorded by earlier migrations that would land on
        a pruned hash are dropped, so nothing re-points history at an
        identity this migration removed.

        Idempotent: a second run finds no template instance left to prune.
        """
        from shared.query_registry.self_traffic import match_self_template

        pruned: set = set()
        shapes: Dict[str, int] = {}
        for row in conn.execute(
            "SELECT id, hash, sql, original_sql, question, readyset_query_id"
            " FROM query_identity"
        ).fetchall():
            sql_text = row["original_sql"] or row["sql"]
            shape = match_self_template(sql_text)
            if shape is None:
                continue
            reason = ""
            if row["question"]:
                reason = "it carries a question"
            elif row["readyset_query_id"]:
                reason = "it is cached in Readyset"
            else:
                lifecycles = conn.execute(
                    """
                    SELECT saved_at, last_analyzed_at, last_compared_at,
                           analysis_count, comparison_count
                    FROM target_query WHERE identity_id = ?
                    """,
                    (row["id"],),
                ).fetchall()
                if any(item["saved_at"] for item in lifecycles):
                    reason = "it was saved"
                elif any(
                    item["last_analyzed_at"] or item["analysis_count"]
                    for item in lifecycles
                ):
                    reason = "it was analyzed"
                elif any(
                    item["last_compared_at"] or item["comparison_count"]
                    for item in lifecycles
                ):
                    reason = "it was compared"
            if reason:
                logger.warning(
                    "Keeping RDST %s statement %s: %s",
                    shape,
                    row["hash"],
                    reason,
                )
                continue
            conn.execute("DELETE FROM target_query WHERE identity_id = ?", (row["id"],))
            conn.execute("DELETE FROM query_identity WHERE id = ?", (row["id"],))
            pruned.add(row["hash"])
            shapes[shape] = shapes.get(shape, 0) + 1
            logger.info(
                "Pruned RDST %s statement %s: %.60s",
                shape,
                row["hash"],
                " ".join(sql_text.split()),
            )
        if pruned:
            self._identity_moves = {
                old: new
                for old, new in self._identity_moves.items()
                if new not in pruned
            }
            logger.info(
                "Library cleanup removed %d RDST self-traffic entries (%s)",
                len(pruned),
                ", ".join(f"{name} {count}" for name, count in sorted(shapes.items())),
            )
        return len(pruned)

    def _prune_system_statements(self, conn: sqlite3.Connection) -> int:
        """Schema v12 data migration: drop entries that are not user workload.

        Two provable classes leave, whatever a bulk review sweep or an
        automatic save stamped on them:

        - a statement referencing no user relation (catalog introspection,
          ``SELECT VERSION()``, connection probes), which nothing in Readyset
          can cache and no measurement can improve;
        - an exact instance of an RDST diagnostic template (see
          shared.query_registry.self_traffic), including the setting probe the
          v11 pass did not yet recognize.

        Only entries the system admitted are eligible: an entry a user typed,
        imported, or asked for keeps its place even when its SQL touches no
        table. Real investment wins over the match -- an entry someone
        analyzed or compared stays, because its measurements would otherwise
        lose their subject. saved_at and reviewed_at do not count: both are
        written by flows that sweep whatever the list happened to show.

        cache.db observation history is left alone for the reasons
        _prune_self_traffic gives, and re-keys landing on a pruned hash are
        dropped the same way. Idempotent: a second run finds no match left.
        """
        from shared.query_registry.self_traffic import match_self_template
        from shared.query_registry.sql_normalizer import references_user_relations

        pruned: set = set()
        for row in conn.execute(
            "SELECT id, hash, sql, original_sql, question, source,"
            " readyset_query_id FROM query_identity"
        ).fetchall():
            if row["source"] not in _SYSTEM_ADMITTED_SOURCES:
                continue
            sql_text = row["original_sql"] or row["sql"]
            if references_user_relations(sql_text) and not match_self_template(
                sql_text
            ):
                continue
            if row["question"]:
                continue
            measured = conn.execute(
                """
                SELECT 1 FROM target_query
                WHERE identity_id = ?
                  AND (last_analyzed_at != '' OR last_compared_at != ''
                       OR analysis_count > 0 OR comparison_count > 0)
                LIMIT 1
                """,
                (row["id"],),
            ).fetchone()
            if measured is not None:
                continue
            conn.execute("DELETE FROM target_query WHERE identity_id = ?", (row["id"],))
            conn.execute("DELETE FROM query_identity WHERE id = ?", (row["id"],))
            pruned.add(row["hash"])
            logger.info(
                "Pruned system statement %s%s: %.60s",
                row["hash"],
                " (cached in Readyset)" if row["readyset_query_id"] else "",
                " ".join(sql_text.split()),
            )
        if pruned:
            self._identity_moves = {
                old: new
                for old, new in self._identity_moves.items()
                if new not in pruned
            }
            logger.info(
                "Library cleanup removed %d system statements", len(pruned)
            )
        return len(pruned)

    def _stale_identity_hashes(self, conn: sqlite3.Connection) -> Dict[str, str]:
        """Map hashes a row's own text minted under the prior normalizer.

        Observation history in cache.db keyed by an identity the placeholder
        migrations re-keyed can only be found again by recognizing the hash
        the old spelling of a surviving row's text digests. Hashes that no
        surviving text accounts for stay where they are, as do hashes two
        rows both account for: nothing in either store proves what those
        named.
        """
        from shared.query_registry.query_registry import _sql_digest
        from shared.query_registry.sql_normalizer import (
            canonicalize_placeholder_style,
        )

        live: set[str] = set()
        stale: Dict[str, set[str]] = {}
        for row in conn.execute(
            "SELECT hash, sql, original_sql FROM query_identity"
        ).fetchall():
            live.add(row["hash"])
            text = row["original_sql"] or row["sql"]
            if not text or not (
                _REPAIR_CANDIDATE.search(text) or _ORDINAL_HINT.search(text)
            ):
                continue
            legacy = _legacy_normalized(text)
            if not legacy:
                continue
            for candidate in (
                _sql_digest(legacy),
                _sql_digest(_legacy_canonicalized(legacy)),
                _sql_digest(canonicalize_placeholder_style(legacy)),
            ):
                if candidate != row["hash"]:
                    stale.setdefault(candidate, set()).add(row["hash"])
        return {
            old: next(iter(claimants))
            for old, claimants in stale.items()
            if old not in live and len(claimants) == 1
        }

    def _rekey_observation_history(self) -> None:
        """Point cache.db's observation history at every re-keyed identity.

        Runs after the migration transaction commits: discovery holds
        cache.db's write lock across its library.db write, so a migration
        must never take the two in the opposite order. cache.db is a
        rebuildable cache, so a failure here is logged and the migrated
        library still stands.
        """
        moves, self._identity_moves = self._identity_moves, {}
        cache_path = self._db_path.parent / "cache.db"
        if not moves or not cache_path.exists():
            return
        from shared.query_registry.observation_store import ObservationStore

        try:
            with ObservationStore(cache_path) as store:
                moved = store.rekey_identities(moves)
        except Exception:
            logger.warning(
                "Observation history at %s keeps its previous query hashes",
                cache_path,
                exc_info=True,
            )
            return
        if moved:
            logger.info(
                "Re-keyed %d observation rows in %s onto migrated query "
                "identities",
                moved,
                cache_path,
            )

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
        starred: Optional[bool] = None,
    ) -> tuple[
        list[Dict[str, Any]],
        Dict[str, Dict[str, int]],
        int,
        Optional[tuple[float, str]],
    ]:
        """Read one indexed target page and full-set facet counts in SQLite.

        ``starred`` narrows the candidate set beside search rather than
        acting as one more facet, so with the star filter on every facet
        count describes the user's shortlist.
        """
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
        if starred is None:
            candidate_clause = search_clause
        else:
            starred_clause = "rm_is_saved" if starred else "NOT rm_is_saved"
            candidate_clause = f"({search_clause}) AND {starred_clause}"
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
            + candidate_clause
            + ") SELECT "
            + ", ".join(aggregates)
            + " FROM candidate"
        )

        sort_column, sort_index = _SORT_COLUMNS[sort]
        page_predicates = [candidate_clause, *selected.values()]
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

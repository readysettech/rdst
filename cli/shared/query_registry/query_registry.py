"""
Query Registry Implementation

Core functionality for storing, retrieving, and managing SQL queries with
normalized hashing, persisted in SQLite (library.db). A pre-existing
queries.toml is imported on first load and kept as a backup; the TOML
projection can be regenerated with ``rdst query export --format=toml``.

The SQLite flip is gated by the RDST_REGISTRY_SQLITE environment variable
(see registry_sqlite_enabled): when disabled, queries.toml remains the
authoritative store and no library.db is created or imported.
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
import logging
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, asdict, field, fields as dataclass_fields
from datetime import datetime, timezone
from pathlib import Path
import shared.constants as shared_constants
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple
import toml
import sqlglot
import sqlparse
from sqlglot.errors import ParseError

from shared.persistence import update_toml
from shared.query_capture_limits import MAX_QUERY_LENGTH
from shared.query_registry.library_store import (
    LibraryMigrationError,
    LibraryStore,
    library_db_path_for,
)

logger = logging.getLogger(__name__)

# Statements that must never enter the registry. Everything downstream treats a
# registry entry as text it may hand back to a database (analyze, cache,
# benchmark), so procedural blocks and anything that changes schema or
# privileges is refused at the boundary. INSERT/UPDATE/DELETE stay storable
# because `rdst top` and `rdst scan` legitimately capture write traffic for
# reporting; the sinks that re-execute registry text enforce read-only
# themselves.
UNSTORABLE_LEAD_KEYWORDS = frozenset(
    {
        "DO",
        "CALL",
        "CREATE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "RENAME",
        "GRANT",
        "REVOKE",
        "COMMENT",
    }
)


def _statement_count(sql: str) -> int:
    """Count statements that carry content, ignoring comments and empty tails."""
    return sum(
        1
        for statement in sqlparse.parse(sql or "")
        if statement.token_first(skip_cm=True) is not None
    )


_GLOBAL_TARGET_KEY = "__global__"


@dataclass
class QueryTargetLifecycle:
    """Durable web/desktop lifecycle state for one query on one target.

    The query hash remains global so SQL patterns still deduplicate, while the
    state that powers the unified Query Library stays target-correct. These
    fields are additive: the CLI continues to read and write the legacy
    QueryEntry fields until its own migration is coordinated separately.
    """

    first_observed_at: str = ""
    last_observed_at: str = ""
    reviewed_at: str = ""
    saved_at: str = ""
    last_analyzed_at: str = ""
    analysis_count: int = 0
    last_compared_at: str = ""
    comparison_count: int = 0
    sources: List[str] = field(default_factory=list)
    # What the most recent comparison found: status plus whichever of
    # ``at``, ``readyset_ms``, ``origin_ms``, and ``detail`` the run
    # measured. Absent keys mean the run reported nothing for them, so the
    # mapping stays TOML-serializable.
    last_compare: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QueryTargetLifecycle":
        """Deserialize lifecycle data written by this or a newer build."""
        known_fields = {item.name for item in dataclass_fields(cls)}
        values = {key: value for key, value in data.items() if key in known_fields}
        if not isinstance(values.get("sources", []), list):
            values["sources"] = []
        if not isinstance(values.get("last_compare", {}), dict):
            values["last_compare"] = {}
        return cls(**values)


def canonicalize_sql(sql: str) -> str:
    """
    Strip comments and normalize whitespace while preserving statement structure.

    This keeps meaningful newlines intact so comment-prefixed queries captured from
    `rdst top` remain parseable after comment removal.
    """
    if not sql or not sql.strip():
        return ""

    stripped = sqlparse.format(sql, strip_comments=True)
    stripped = stripped.replace("\r\n", "\n").replace("\r", "\n")
    stripped = re.sub(r"[ \t\f\v]+", " ", stripped)
    stripped = re.sub(r" *\n *", "\n", stripped)
    return stripped.strip()


def verify_query_completeness(
    sql: str, dialect: str = None
) -> Tuple[bool, Optional[str]]:
    """
    Verify that a SQL query is complete and parseable using sqlglot.

    Detects truncated queries that may have been cut off by database
    capture limits (e.g., track_activity_query_size for PostgreSQL).

    Args:
        sql: SQL query string to verify
        dialect: Optional SQL dialect ('postgres', 'mysql', etc.)

    Returns:
        Tuple of (is_valid, error_message). If is_valid is True, error_message is None.
    """
    canonical_sql = canonicalize_sql(sql)
    if not canonical_sql:
        return False, "Empty query"

    stripped = canonical_sql.strip()
    upper_sql = stripped.upper()

    truncation_suffixes = (
        " AND",
        " OR",
        " WHERE",
        " FROM",
        " JOIN",
        " ON",
        " IN",
        " IN (",
        " LIKE",
        " BETWEEN",
        " NOT",
        " IS",
        " AS",
        " SET",
        " VALUES",
        " VALUES(",
        "(",
        ",",
        "=",
        "<",
        ">",
        "!",
        "+",
        "-",
        "/",
    )
    for suffix in truncation_suffixes:
        if upper_sql.endswith(suffix) or upper_sql.endswith(suffix.strip()):
            return False, f"Query appears truncated (ends with '{suffix.strip()}')"

    try:
        # parse(), not parse_one(): parse_one() keeps only the first statement,
        # so a stacked "SELECT 1; DROP TABLE t" would be stored whole and then
        # replayed whole at every sink that re-executes registry text.
        statements = [s for s in sqlglot.parse(canonical_sql, dialect=dialect) if s]
        if len(statements) > 1:
            return False, "Multiple statements in one query are not allowed"
        return True, None
    except ParseError as e:
        error_str = str(e).lower()
        if "unexpected" in error_str or "expected" in error_str:
            return False, f"Query appears to be truncated or malformed: {e}"
        return False, f"SQL parse error: {e}"
    except Exception as e:
        return False, f"Failed to parse SQL: {e}"


def dialect_for_target(target: Optional[str]) -> Optional[str]:
    """Map a configured target's engine to a sqlglot dialect name.

    Registry callers rarely know the dialect, but almost always know the
    target. Parsing MySQL text with the generic dialect mangles engine
    syntax such as `col->>'$.path'`, so resolve it from the target.
    """
    if not target:
        return None
    try:
        from shared.config.targets import TargetsConfig

        cfg = TargetsConfig()
        cfg.load()
        engine = (cfg.get(target) or {}).get("engine", "")
    except Exception:
        return None
    engine = (engine or "").lower()
    if engine in ("postgresql", "postgres"):
        return "postgres"
    if engine in ("mysql", "mariadb"):
        return "mysql"
    return None


def normalize_sql(query: str, dialect: str = None) -> str:
    """
    Normalize SQL query for consistent hashing and parameterization.

    Uses SQLGlot for robust AST-based normalization that correctly handles
    comments and complex SQL structures. Falls back to regex for edge cases.

    Args:
        query: Raw SQL query string
        dialect: Optional SQL dialect ('postgres', 'mysql', etc.)

    Returns:
        Normalized SQL with :p1, :p2 named placeholders
    """
    from .sql_normalizer import normalize_and_extract

    canonical_query = canonicalize_sql(query)
    if not canonical_query:
        return ""

    normalized, _ = normalize_and_extract(canonical_query, dialect)
    return normalized


def normalize_sql_deep(query: str) -> str:
    """
    Deep normalization for matching queries across different sources.

    This function performs additional normalization beyond normalize_sql()
    to ensure queries from different sources (LLM-generated, SQLAlchemy echo,
    pg_stat_activity) can be matched after normalization.

    Additional normalization steps:
    1. All normalize_sql() steps
    2. Remove column aliases (AS column_alias)
    3. Remove table prefixes (table.column -> column)
    4. Lowercase everything
    5. Replace Python-style params (%(name)s) with ?
    6. Replace MySQL-style params (%s) with ?

    Args:
        query: Raw SQL query string

    Returns:
        Deeply normalized SQL query string
    """
    if not query:
        return ""

    # First apply basic normalization
    normalized = normalize_sql(query)

    # Lowercase everything (SQL keywords and identifiers)
    normalized = normalized.lower()

    # Replace Python DB-API style placeholders %(name)s with ?
    normalized = re.sub(r'%\([^)]+\)s', '?', normalized)

    # Replace MySQL/Python style %s with ?
    normalized = re.sub(r'%s', '?', normalized)

    # Remove column aliases in SELECT clause
    # Pattern: "column_name AS alias_name" -> "column_name"
    # This handles: customer.c_custkey AS customer_c_custkey -> customer.c_custkey
    # Be careful not to remove table aliases (e.g., "FROM customer AS c")
    # Only remove aliases after column names, not after table names
    # Match: word.word AS word or word AS word (in select context)
    normalized = re.sub(r'(\w+(?:\.\w+)?)\s+as\s+\w+', r'\1', normalized)

    # Remove table prefixes from column names
    # Pattern: "table.column" -> "column"
    # This handles: customer.c_custkey -> c_custkey
    # Be careful with function calls like COUNT(table.column)
    normalized = re.sub(r'\b(\w+)\.(\w+)\b', r'\2', normalized)

    # Collapse whitespace again (in case removals left extra spaces)
    normalized = re.sub(r'\s+', ' ', normalized)

    # Final trim
    normalized = normalized.strip()

    return normalized


def hash_sql_deep(query: str) -> str:
    """
    Generate a consistent hash using deep normalization.

    Uses deeply normalized SQL for matching across different sources
    (LLM-generated, ORM output, pg_stat_activity).

    Args:
        query: SQL query string (will be deeply normalized)

    Returns:
        12-character hexadecimal hash string
    """
    normalized = normalize_sql_deep(query)
    # nosemgrep: python.lang.security.insecure-hash-algorithms-md5.insecure-hash-algorithm-md5
    return hashlib.md5(normalized.encode('utf-8'), usedforsecurity=False).hexdigest()[:12]


def _sql_digest(normalized_sql: str) -> str:
    """Digest a normalized SQL text into the registry's 12-hex identity."""
    # nosemgrep: python.lang.security.insecure-hash-algorithms-md5.insecure-hash-algorithm-md5, gitlab.bandit.B303-1
    # MD5 is used for query fingerprinting/deduplication, not cryptographic purposes
    return hashlib.md5(
        normalized_sql.encode("utf-8"), usedforsecurity=False
    ).hexdigest()[
        :12
    ]  # nosemgrep: python.lang.security.insecure-hash-algorithms-md5.insecure-hash-algorithm-md5, gitlab.bandit.B303-1


def hash_sql(query: str) -> str:
    """
    Generate a consistent hash for a SQL query.

    Uses normalized SQL with every placeholder renumbered by textual
    position in one canonical :pN form, so the same logical query always
    produces the same hash regardless of formatting differences or the
    placeholder style its source spells ($N, anonymous ?, or :pN; see
    sql_normalizer.canonicalize_placeholder_style).

    Args:
        query: SQL query string (will be normalized)

    Returns:
        12-character hexadecimal hash string
    """
    from .sql_normalizer import canonicalize_placeholder_style

    return _sql_digest(canonicalize_placeholder_style(normalize_sql(query)))


# Stop words to filter out when generating query names
_STOP_WORDS = {
    # Articles and basic words
    "the",
    "a",
    "an",
    "of",
    "in",
    "on",
    "at",
    "by",
    "for",
    "to",
    "with",
    "from",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "that",
    "which",
    "who",
    "whom",
    "whose",
    "this",
    "these",
    "those",
    "and",
    "or",
    "but",
    "if",
    "then",
    "else",
    "when",
    "where",
    "why",
    "how",
    "all",
    "each",
    "every",
    "both",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "no",
    "not",
    "only",
    "own",
    "same",
    "so",
    "than",
    "too",
    "very",
    "just",
    # Common query words to skip
    "show",
    "find",
    "get",
    "list",
    "display",
    "give",
    "tell",
    "what",
    "me",
    "i",
    "want",
    "need",
    "like",
    "please",
    "can",
    "you",
    "my",
    "your",
    # SQL keywords
    "select",
    "from",
    "where",
    "order",
    "group",
    "having",
    "limit",
    "offset",
    "join",
    "left",
    "right",
    "inner",
    "outer",
    "cross",
    "union",
    "except",
    "insert",
    "update",
    "delete",
    "create",
    "drop",
    "alter",
    "table",
    "index",
}


def generate_query_name(text: str, existing_names: Optional[set] = None) -> str:
    """
    Generate a meaningful name from a natural language question or SQL query.

    Extracts keywords, filters stop words, and creates a snake_case name.
    Handles collisions by appending numeric suffixes.

    Args:
        text: Natural language question or SQL query
        existing_names: Set of names already in use (for collision detection)

    Returns:
        A snake_case name like 'responsive_users' or 'top_customers_revenue'

    Examples:
        >>> generate_query_name("Find the most responsive users")
        'most_responsive_users'
        >>> generate_query_name("Show me top customers by revenue")
        'top_customers_revenue'
        >>> generate_query_name("What are the largest orders")
        'largest_orders'
    """
    if not text:
        return "query"

    existing_names = existing_names or set()

    # Tokenize: extract words (alphanumeric sequences)
    words = re.findall(r"\b[a-zA-Z]+\b", text.lower())

    # Filter out stop words and very short words
    keywords = [w for w in words if w not in _STOP_WORDS and len(w) > 2]

    # Take first 4 keywords
    name_parts = keywords[:4]

    if not name_parts:
        # Fallback if all words were filtered
        name_parts = ["query"]

    # Join with underscore
    base_name = "_".join(name_parts)

    # Truncate if too long (max 30 chars, don't cut mid-word)
    if len(base_name) > 30:
        truncated = base_name[:30]
        # Find last underscore to avoid cutting mid-word
        last_underscore = truncated.rfind("_")
        if last_underscore > 10:  # Keep at least some content
            base_name = truncated[:last_underscore]
        else:
            base_name = truncated

    # Handle collisions
    final_name = base_name
    counter = 1
    while final_name in existing_names:
        final_name = f"{base_name}_{counter}"
        counter += 1

    return final_name


def generate_dummy_parameters(sql: str) -> Dict[str, Any]:
    """
    Generate sensible dummy parameter values for a parameterized SQL query.

    Analyzes the SQL context around each ? placeholder to infer appropriate
    dummy values based on:
    - LIMIT/OFFSET → small integers (10, 0)
    - WHERE col = ? → 1 for IDs, 'example' for strings
    - Date comparisons → reasonable date
    - IN clauses → single value of appropriate type

    Args:
        sql: Parameterized SQL with ? placeholders

    Returns:
        Dictionary of parameter values (param_0, param_1, etc.)
    """
    params = {}

    # Find all ? placeholders and their surrounding context
    # We'll analyze what comes before each ?
    sql_upper = sql.upper()

    # Count placeholders
    placeholder_count = sql.count('?')
    if placeholder_count == 0:
        return params

    # Split by ? and analyze context before each placeholder
    parts = sql_upper.split('?')

    for i in range(placeholder_count):
        context = parts[i].strip() if i < len(parts) else ""

        # Get the last few tokens before the placeholder
        tokens = context.split()
        last_tokens = tokens[-3:] if len(tokens) >= 3 else tokens
        context_str = ' '.join(last_tokens)

        # Infer type based on context
        if 'LIMIT' in context_str:
            params[f'param_{i}'] = 10  # Reasonable limit
        elif 'OFFSET' in context_str:
            params[f'param_{i}'] = 0  # Start from beginning
        elif any(kw in context_str for kw in ['<', '>', '<=', '>=']):
            # Comparison - could be date or number
            if any(date_hint in context_str for date_hint in ['DATE', 'TIME', 'YEAR', 'MONTH', 'DAY', 'ORDERDATE', 'CREATED', 'UPDATED']):
                params[f'param_{i}'] = '2024-01-01'
            else:
                params[f'param_{i}'] = 100
        elif 'LIKE' in context_str:
            params[f'param_{i}'] = '%example%'  # LIKE pattern
        elif 'IN' in context_str and '(' in context_str:
            params[f'param_{i}'] = 1  # Single value for IN clause
        elif any(id_hint in context_str for id_hint in ['_ID', '_KEY', 'KEY =', 'ID =']):
            params[f'param_{i}'] = 1  # ID lookup
        elif any(seg_hint in context_str for seg_hint in ['SEGMENT', 'STATUS', 'TYPE', 'CATEGORY', 'NAME']):
            params[f'param_{i}'] = 'EXAMPLE'  # String enum/category
        elif '=' in context_str:
            # Generic equality - try to guess from column name
            if any(num_hint in context_str for num_hint in ['COUNT', 'PRICE', 'TOTAL', 'AMOUNT', 'QTY', 'QUANTITY', 'BALANCE']):
                params[f'param_{i}'] = 100
            else:
                params[f'param_{i}'] = 1  # Default to integer
        else:
            # Unknown context - use integer as safe default
            params[f'param_{i}'] = 1

    return params


def extract_parameters_from_sql(original_sql: str, parameterized_sql: str) -> Dict[str, Any]:
    """
    Extract parameter values from original SQL by comparing with parameterized version.

    Args:
        original_sql: Original SQL with actual values
        parameterized_sql: Parameterized SQL with ? placeholders

    Returns:
        Dictionary of parameter values
    """
    import re

    # Simple parameter extraction - matches values where ? placeholders are
    # This is a basic implementation; a full SQL parser would be more robust

    # Find all string literals in original
    string_literals = re.findall(r"'([^']*)'", original_sql)
    # Find all numeric literals in original
    numeric_literals = re.findall(r"\b(\d+(?:\.\d+)?)\b", original_sql)

    # Count placeholders in parameterized version
    placeholder_count = parameterized_sql.count("?")

    # Combine all literals in order they appear
    all_literals = []

    # This is a simplified approach - would need more sophisticated parsing
    # for production use, but works for basic cases
    original_tokens = re.findall(r"'[^']*'|\b\d+(?:\.\d+)?\b", original_sql)

    params = {}
    for i, token in enumerate(original_tokens[:placeholder_count]):
        if token.startswith("'") and token.endswith("'"):
            # String literal
            value = token[1:-1]  # Remove quotes
            params[f"param_{i}"] = value
        else:
            # Numeric literal
            try:
                if "." in token:
                    params[f"param_{i}"] = float(token)
                else:
                    params[f"param_{i}"] = int(token)
            except ValueError:
                params[f"param_{i}"] = token

    return params


def _identity_slot_count(identity_sql: str) -> int:
    """Count the parameter slots of an engine-normalized identity text.

    MySQL digest texts carry anonymous '?' slots. pg_stat_statements texts
    carry $N placeholders numbered by text position, so slot k is $k and the
    positional 'pk' mapping holds for both styles. A text mixing the two, or
    whose $N sequence is not exactly 1..n in text order, has no safely
    mappable slots and counts as zero (yielding no values).
    """
    qmark_slots = identity_sql.count("?")
    dollar_slots = [int(number) for number in re.findall(r"\$(\d+)", identity_sql)]
    if not dollar_slots:
        return qmark_slots
    if qmark_slots or dollar_slots != list(range(1, len(dollar_slots) + 1)):
        return 0
    return len(dollar_slots)


_PARAM_KEY = re.compile(r"^(?::?p|param_|\$)(\d+)$")


def canonical_param_key(name: str) -> str:
    """Return the `pN` name a parameter key stands for.

    Callers spell one slot several ways: the registry's own `pN`, the
    interactive prompt's `param_N`, and a statement's `$N` or `:pN`. A named
    parameter (`:since`) keeps its name without the colon.
    """
    match = _PARAM_KEY.match(name.strip())
    return f"p{int(match.group(1))}" if match else name.strip().lstrip(":")


def typed_parameter(value: Any, source: str = "") -> Dict[str, Any]:
    """Store one parameter value in the shape reconstruct_sql reads.

    A string spelling a number is stored as a number, so a slot the engine
    requires to be numeric (LIMIT, OFFSET) gets an unquoted literal. `source`
    records provenance ("user", "suggested", "observed") when the caller
    knows it.
    """
    if isinstance(value, str):
        for cast in (int, float):
            try:
                value = cast(value)
                break
            except ValueError:
                continue
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    param = {"value": value, "type": "number" if is_number else "string"}
    if source:
        param["source"] = source
    return param


def extract_observed_params(
    sample_sql: str, identity_sql: str, dialect: str = None
) -> Dict[str, dict]:
    """Extract observed parameter values from a literal-bearing sample of a
    statement whose identity text is engine-normalized: anonymous '?' slots
    (MySQL DIGEST_TEXT alongside QUERY_SAMPLE_TEXT) or positional $N slots
    (pg_stat_statements text alongside a pg_stat_activity sample).

    The sample's literals map positionally onto identity_sql's slots.
    normalize_and_extract names parameters in AST-traversal order, so the
    values are renamed p1..pN by their textual position first; consumers
    resolve the k-th slot as key 'pk'. Values are returned only when the
    literal count matches the slot count; any mismatch (folded IN lists,
    truncated samples, non-literal slots) yields no values rather than
    misaligned ones.
    """
    from .sql_normalizer import normalize_and_extract

    try:
        normalized_sample, params = normalize_and_extract(
            canonicalize_sql(sample_sql), dialect
        )
    except Exception:
        return {}
    ordered = re.findall(r":p(\d+)\b", normalized_sample)
    if len(ordered) != len(params) or len(ordered) != _identity_slot_count(
        identity_sql
    ):
        return {}
    remapped = {
        f"p{position}": params[f"p{index}"]
        for position, index in enumerate(ordered, 1)
        if f"p{index}" in params
    }
    return remapped if len(remapped) == len(params) else {}


def reconstruct_query_with_params(
    parameterized_sql: str, params: Dict[str, Any]
) -> str:
    """
    Reconstruct executable SQL by substituting parameter values.

    Args:
        parameterized_sql: SQL with ? placeholders
        params: Dictionary of parameter values

    Returns:
        Executable SQL with actual parameter values
    """
    # Simple reconstruction - replace ? with values in order
    result = parameterized_sql

    # Get parameter values in order
    param_values = list(params.values())

    for i, value in enumerate(param_values):
        if isinstance(value, str):
            # String parameters need quotes; double any embedded quote so the
            # value cannot terminate its own literal
            replacement = "'" + value.replace("'", "''") + "'"
        else:
            # Numeric parameters don't need quotes
            replacement = str(value)

        # Replace first occurrence of ?
        result = result.replace("?", replacement, 1)

    return result

@dataclass
class QueryEntry:
    """
    Represents a stored query with metadata.
    """

    sql: str  # Normalized SQL with :p1, :p2 placeholders
    hash: str
    tag: str = ""
    # Original submitted SQL, before parameter normalization (e.g. positional
    # `GROUP BY 1` is preserved, not rewritten to `:p1`). The dedupe `hash` is
    # still computed from the canonical form, so it stays separate: this field
    # only makes hand-offs (re-analyze / re-cache / history re-ask) faithful to
    # what the user actually ran.
    original_sql: str = ""
    question: str = ""  # Original natural-language question (ask-sourced entries)
    first_analyzed: str = ""
    last_analyzed: str = ""
    frequency: int = 0
    source: str = "manual"  # "manual", "top", "file", "stdin"
    last_target: str = ""  # Last target used for analysis
    # Immutable target a question was FIRST asked against (ask-sourced only).
    # last_target is mutable: any re-add of the same SQL hash from another
    # target steals it, which leaked Ask history across databases. History
    # scopes by this stable field instead (rdst-e7s.26).
    ask_target: str = ""
    # SQLGlot-extracted parameters as dict: {'p1': {'value': 'x', 'type': 'string'}, ...}
    parameters: Dict[str, dict] = field(default_factory=dict)
    # Most recent runtime parameter values (for auto-substitution when re-running queries)
    most_recent_params: Dict[str, Any] = field(default_factory=dict)
    # Runtime stats from rdst top (optional, only populated when saved from top)
    max_duration_ms: float = 0.0
    avg_duration_ms: float = 0.0
    observation_count: int = 0
    # ReadySet-side identity (sparse, populated on cache interaction).
    # Source of truth for cache lifecycle ops (DROP CACHE etc.) — fixes CLD-1748/1754.
    readyset_query_id: str = ""           # e.g. "q_13b0714e3f57aa57"
    readyset_supported: str = ""          # "yes" | "pending" | "unsupported: <reason>"
    last_cache_target: str = ""           # cache target where readyset_query_id was last observed
    readyset_last_observed_at: str = ""   # ISO 8601 timestamp
    # Target-scoped lifecycle state for the unified web/desktop Query Library.
    # Legacy timestamps above remain available to the CLI while its contract is
    # migrated independently.
    target_lifecycle: Dict[str, QueryTargetLifecycle] = field(default_factory=dict)

    @property
    def home_target(self) -> str:
        """The database this query belongs to: where it was first asked
        (immutable ask_target) or, failing that, last run. Per-database lists
        scope by this rather than the mutable last_target, which leaked queries
        across databases (rdst-e7s.26, rdst-e7s.31)."""
        return self.ask_target or self.last_target

    @staticmethod
    def target_key(target: str) -> str:
        """Return the stable serialized key for a target or global entry."""
        return target or _GLOBAL_TARGET_KEY

    def lifecycle_for(
        self, target: str = "", *, create: bool = False
    ) -> Optional[QueryTargetLifecycle]:
        """Return lifecycle state for a target without leaking another target."""
        key = self.target_key(target or self.home_target)
        lifecycle = self.target_lifecycle.get(key)
        if lifecycle is None and create:
            lifecycle = QueryTargetLifecycle()
            self.target_lifecycle[key] = lifecycle
        return lifecycle

    def belongs_to_target(self, target: str) -> bool:
        """Whether this deduplicated SQL pattern has activity on ``target``."""
        return self.target_key(target) in self.target_lifecycle or self.home_target == target

    def is_new_for(self, target: str = "") -> bool:
        """A first system observation remains New until explicitly reviewed."""
        lifecycle = self.lifecycle_for(target)
        return bool(
            lifecycle
            and lifecycle.first_observed_at
            and not lifecycle.reviewed_at
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for TOML serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QueryEntry":
        """Create QueryEntry from dictionary (TOML deserialization)."""
        data = copy.deepcopy(data)
        # Handle backward compatibility
        if "last_target" not in data:
            data["last_target"] = ""
        # Legacy writers stored last_target as a list; normalize to a string.
        if isinstance(data["last_target"], list):
            data["last_target"] = data["last_target"][0] if data["last_target"] else ""
        if "most_recent_params" not in data:
            data["most_recent_params"] = {}
        if "parameters" not in data:
            data["parameters"] = {}
        # Original-SQL / question persistence (hand-off faithfulness)
        if "original_sql" not in data:
            data["original_sql"] = ""
        if "question" not in data:
            data["question"] = ""
        # Runtime stats from rdst top (added in CLD-1645)
        if "max_duration_ms" not in data:
            data["max_duration_ms"] = 0.0
        if "avg_duration_ms" not in data:
            data["avg_duration_ms"] = 0.0
        if "observation_count" not in data:
            data["observation_count"] = 0
        # ReadySet identity fields (added in in-request-path branch)
        for key in ("readyset_query_id", "readyset_supported", "last_cache_target",
                    "readyset_last_observed_at"):
            if key not in data:
                data[key] = ""

        lifecycle_data = data.get("target_lifecycle")
        if isinstance(lifecycle_data, dict):
            data["target_lifecycle"] = {
                str(target): QueryTargetLifecycle.from_dict(value)
                for target, value in lifecycle_data.items()
                if isinstance(value, dict)
            }
        else:
            # Before T7A every registry row appeared in Saved. Preserve that
            # intent while only treating database-discovery sources as observed.
            target = data.get("ask_target") or data.get("last_target") or ""
            source = data.get("source", "")
            first_activity = data.get("first_analyzed", "")
            last_activity = data.get("last_analyzed", "") or first_activity
            lifecycle = QueryTargetLifecycle(
                saved_at=first_activity,
                sources=[source] if source else [],
            )
            if source in {"top", "top-historical", "audit"}:
                lifecycle.first_observed_at = first_activity
                lifecycle.last_observed_at = last_activity
            if source == "analyze":
                lifecycle.last_analyzed_at = last_activity
                lifecycle.analysis_count = 1 if last_activity else 0
            data["target_lifecycle"] = {
                cls.target_key(target): lifecycle,
            }

        # Remove deprecated parameter_history if present in old data
        data.pop("parameter_history", None)

        # Drop any keys that aren't declared fields on this build of
        # QueryEntry. A newer rdst may have written fields an older build
        # doesn't know about (e.g. readyset_query_id); silently ignoring them
        # keeps the on-disk registry forward-compatible instead of raising
        # "unexpected keyword argument" and discarding the whole entry.
        known_fields = {f.name for f in dataclass_fields(cls)}
        data = {k: v for k, v in data.items() if k in known_fields}

        return cls(**data)


REGISTRY_SQLITE_ENV_VAR = "RDST_REGISTRY_SQLITE"


def registry_sqlite_enabled(value: Optional[str] = None) -> bool:
    """Whether the SQLite library store backs QueryRegistry (default: yes).

    The RDST_REGISTRY_SQLITE environment variable is the review gate for the
    SQLite flip: a trimmed, case-insensitive "0" or "false" selects the
    legacy TOML backend; any other value, or the variable being unset,
    keeps SQLite authoritative.

    Args:
        value: Flag value to parse. Defaults to the current environment.
    """
    if value is None:
        value = os.environ.get(REGISTRY_SQLITE_ENV_VAR)
    return (value or "").strip().lower() not in {"0", "false"}


class TomlLibraryStore:
    """Legacy TOML persistence behind the same interface as LibraryStore.

    Selected when registry_sqlite_enabled() is False: queries.toml is the
    authoritative store, every save rewrites it atomically through
    shared.persistence.update_toml under its file lock, and no library.db
    is ever created or imported.
    """

    def __init__(self, registry_path: Path) -> None:
        self._registry_path = registry_path

    def load_all(self) -> Dict[str, Dict[str, Any]]:
        """Return every entry as {hash: entry_dict}; a missing file is empty."""
        if not self._registry_path.exists():
            return {}
        with open(self._registry_path, "r", encoding="utf-8") as handle:
            data = toml.load(handle)
        return data.get("queries", {})

    def apply_changes(
        self,
        baseline: Dict[str, Dict[str, Any]],
        current: Dict[str, Dict[str, Any]],
        *,
        validate: Optional[Callable[[Dict[str, Dict[str, Any]]], None]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Merge one writer's baseline->current delta onto the stored TOML.

        Same contract as LibraryStore.apply_changes: the file is re-read
        under the lock, ``validate`` sees the merged queries mapping before
        anything is written, and the merged mapping is returned so the
        caller can adopt it as its new baseline. One call is one file write.
        """

        def validate_document(merged: Dict[str, Any]) -> None:
            if validate is not None:
                validate(merged.get("queries", {}))

        merged = update_toml(
            self._registry_path,
            {"queries": baseline},
            {"queries": current},
            validate=validate_document,
        )
        return merged.get("queries", {})


class QueryRegistry:
    """
    Manages persistent storage and retrieval of SQL queries.

    Stores queries in SQLite at ~/.rdst/library.db (see library_store.py).
    A legacy ~/.rdst/queries.toml is imported once on first load, backed up
    beside itself, and ignored afterwards; the TOML projection is available
    on demand via export_toml_projection / ``rdst query export``.

    When RDST_REGISTRY_SQLITE disables the SQLite store, queries.toml is
    authoritative instead and no library.db is touched. A process is
    expected to run in one mode; flipping the flag between runs switches
    the source of truth, so writes made in one mode are not visible in the
    other (that is the point of the review gate).
    """

    def __init__(
        self, registry_path: Optional[str] = None, use_sqlite: Optional[bool] = None
    ):
        """
        Initialize the query registry.

        Args:
            registry_path: Custom path to the legacy TOML registry file.
                Defaults to ~/.rdst/queries.toml; the authoritative SQLite
                store lives beside it (see library_db_path_for).
            use_sqlite: Backend override, mainly for tests. Defaults to the
                RDST_REGISTRY_SQLITE environment flag
                (see registry_sqlite_enabled).
        """
        if registry_path:
            self.registry_path = Path(registry_path)
        else:
            self.registry_path = shared_constants.rdst_data_dir() / "queries.toml"
        if use_sqlite is None:
            use_sqlite = registry_sqlite_enabled()
        self.sqlite_enabled = use_sqlite
        if use_sqlite:
            self._store = LibraryStore(
                library_db_path_for(self.registry_path), self.registry_path
            )
        else:
            self._store = TomlLibraryStore(self.registry_path)

        # In-memory cache of queries
        self._queries: Dict[str, QueryEntry] = {}
        self._loaded = False
        self._baseline_data: Dict[str, Any] = {"queries": {}}
        self._defer_depth = 0
        self._deferred_dirty = False
        self._write_guard: Optional[Callable[[], AbstractContextManager[Any]]] = None

    @property
    def library_store(self) -> Optional[LibraryStore]:
        """SQLite read-model store, or None while the TOML rollback gate is on."""
        return self._store if isinstance(self._store, LibraryStore) else None

    def set_write_guard(
        self,
        guard: Optional[Callable[[], AbstractContextManager[Any]]],
    ) -> None:
        """Install a context held across each backing-store commit.

        Discovery uses this to keep its cache.db fencing transaction live
        until the independent library.db transaction has committed. Ordinary
        CLI/API registry writers leave the guard unset.
        """
        self._write_guard = guard

    def _ensure_directory(self) -> None:
        """Ensure the registry directory exists."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> None:
        """Load queries from the backing store into memory.

        With SQLite enabled, the first load of a data dir that still has a
        legacy queries.toml imports it (see LibraryStore); a failed import
        leaves the TOML untouched and authoritative for the next attempt.
        A fresh data dir serves an empty registry without creating any
        file; the store file is created by the first write.
        """
        try:
            self._queries = {
                query_hash: QueryEntry.from_dict(query_data)
                for query_hash, query_data in self._store.load_all().items()
            }
        except LibraryMigrationError:
            # Already the whole explanation, including where the backup is.
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load query registry at {self.registry_path}: {exc}"
            ) from exc

        self._loaded = True
        self._baseline_data = self._toml_data()

    def _toml_data(self) -> Dict[str, Any]:
        return {
            "queries": {
                query_hash: entry.to_dict()
                for query_hash, entry in self._queries.items()
            }
        }

    def save(self) -> None:
        """Merge this instance's changes and atomically persist the registry."""
        if not self._loaded:
            self.load()

        current = self._toml_data()
        current_queries = current["queries"]
        baseline_queries = self._baseline_data.get("queries", {})
        dirty_hashes = {
            query_hash
            for query_hash in current_queries.keys() | baseline_queries.keys()
            if current_queries.get(query_hash) != baseline_queries.get(query_hash)
        }

        def validate(merged_queries: Dict[str, Any]) -> None:
            # Entries this writer never touched were either parsed from the
            # store or preserved verbatim by the merge; only the writer's own
            # changes need a round-trip check before they hit the store.
            for query_hash in dirty_hashes:
                query_data = merged_queries.get(query_hash)
                if query_data is not None:
                    QueryEntry.from_dict(query_data)

        guard = self._write_guard() if self._write_guard is not None else nullcontext()
        with guard:
            try:
                merged_queries = self._store.apply_changes(
                    baseline_queries,
                    current_queries,
                    validate=validate,
                )
            except Exception as exc:
                raise RuntimeError(f"Failed to save query registry: {exc}") from exc

        # Adopt the merged result as the new baseline. Entries whose merged
        # form matches what this instance just serialized are already
        # materialized in memory; only entries another writer changed need
        # re-parsing.
        queries: Dict[str, QueryEntry] = {}
        baseline: Dict[str, Any] = {}
        for query_hash, query_data in merged_queries.items():
            existing = self._queries.get(query_hash)
            if existing is not None and current_queries.get(query_hash) == query_data:
                queries[query_hash] = existing
                baseline[query_hash] = query_data
            else:
                entry = QueryEntry.from_dict(copy.deepcopy(query_data))
                queries[query_hash] = entry
                baseline[query_hash] = entry.to_dict()
        self._queries = queries
        self._baseline_data = {"queries": baseline}

    @contextmanager
    def defer_save(self) -> Iterator["QueryRegistry"]:
        """Suppress per-mutation saves inside the block.

        When the outermost block exits cleanly, all deferred mutations are
        persisted with a single save(); a block that mutated nothing writes
        nothing.
        """
        self._defer_depth += 1
        try:
            yield self
        finally:
            self._defer_depth -= 1
        if self._defer_depth == 0 and self._deferred_dirty:
            self._deferred_dirty = False
            self.save()

    def export_toml_projection(self, output_path: Optional[str] = None) -> Tuple[Path, int]:
        """Regenerate the TOML projection of the registry from SQLite.

        Produces the same format the TOML-era writer produced, so older
        tooling can keep reading it. Defaults to the registry's own
        queries.toml path. Returns the path written and the entry count.

        Fails when the SQLite store is disabled: queries.toml is already
        the authoritative registry then, and a "projection" that silently
        rewrote it in place would suggest SQLite was involved.
        """
        from shared.persistence import write_text

        if not self.sqlite_enabled:
            raise RuntimeError(
                "The SQLite registry is disabled (RDST_REGISTRY_SQLITE); "
                f"{self.registry_path} is already the authoritative TOML "
                "registry, so there is nothing to export."
            )

        queries = self._store.load_all()
        path = Path(output_path) if output_path else self.registry_path
        write_text(path, toml.dumps({"queries": queries}))
        return path, len(queries)

    @staticmethod
    def _update_lifecycle(
        entry: QueryEntry,
        *,
        target: str,
        source: str,
        occurred_at: str,
        observed: bool,
        analyzed: bool,
        compared: bool,
        save_intent: bool,
    ) -> None:
        """Update additive Query Library state without changing CLI metadata.

        ``saved_at`` is the star the Query Library shows, so it is stamped
        only for a caller that names a person's save intent, and the first
        stamp is the one kept: a re-add reports the same moment the user
        chose the query. Clearing it is the star toggle's own job.
        """
        lifecycle = entry.lifecycle_for(target, create=True)
        if lifecycle is None:
            return

        if source and source not in lifecycle.sources:
            lifecycle.sources.append(source)
        if observed:
            if not lifecycle.first_observed_at:
                lifecycle.first_observed_at = occurred_at
            lifecycle.last_observed_at = occurred_at
        if save_intent and not lifecycle.saved_at:
            lifecycle.saved_at = occurred_at
        if analyzed:
            lifecycle.last_analyzed_at = occurred_at
            lifecycle.analysis_count += 1
        if compared:
            lifecycle.last_compared_at = occurred_at
            lifecycle.comparison_count += 1

    def add_query(
        self,
        sql: str,
        tag: str = "",
        source: str = "manual",
        frequency: int = 0,
        target: str = "",
        question: str = "",
        ask_target: str = "",
        dialect: str = None,
        max_duration_ms: float = 0.0,
        avg_duration_ms: float = 0.0,
        observation_count: int = 0,
        skip_param_extraction: bool = False,
        observed_params_sql: Optional[str] = None,
        observed: bool = False,
        analyzed: bool = False,
        compared: bool = False,
        save_intent: bool = False,
    ) -> tuple[str, bool]:
        """
        Add a query to the registry with parameter extraction and history.

        Uses SQLGlot for robust parameter extraction that correctly handles
        comments and complex SQL structures.

        Args:
            sql: SQL query string (with actual parameter values)
            tag: Optional tag for the query
            source: Source of the query ("manual", "top", "file", "stdin", "scan")
            frequency: Query frequency from telemetry (if available)
            target: Target database name for this analysis
            skip_param_extraction: Skip parameter extraction (for pre-parameterized queries from scan)
            observed_params_sql: Literal-bearing text of one observed execution
                of the same statement (e.g. MySQL QUERY_SAMPLE_TEXT). When sql
                itself yields no parameter values (engine-normalized text with
                '?' slots), values extracted from this text populate
                parameters/most_recent_params as observed values; the entry's
                identity (hash) still derives from sql alone.
            dialect: Optional SQL dialect ('postgres', 'mysql', etc.)
            max_duration_ms: Maximum observed duration in ms (from rdst top)
            avg_duration_ms: Average observed duration in ms (from rdst top)
            observation_count: Number of times query was observed (from rdst top)
            observed: Record a system observation for the target-scoped web lifecycle
            analyzed: Record a completed analysis for the web lifecycle
            compared: Record a completed cache comparison for the web lifecycle
            save_intent: Star the query for this target, i.e. record that a
                person chose to keep it. Opt-in, so that automatic capture,
                cache runs, and every other caller RDST drives on its own
                leave the user's starred set alone.

        Returns:
            Tuple of (query_hash, is_new) where is_new is True if this was a new query pattern

        Raises:
            ValueError: If query exceeds 4KB size limit
        """
        from .sql_normalizer import normalize_and_extract

        if not self._loaded:
            self.load()

        canonical_sql = canonicalize_sql(sql)

        # Procedural and schema/privilege statements are not analyzable
        # queries; reject them with a clear reason instead of a parse error.
        first_keyword = re.match(r"\s*(\w+)", canonical_sql or "")
        if first_keyword and first_keyword.group(1).upper() in UNSTORABLE_LEAD_KEYWORDS:
            raise ValueError(
                f"{first_keyword.group(1).upper()} statements can't be saved to "
                "the registry; only plain SQL queries can be analyzed."
            )

        # Checked here rather than relying on verify_query_completeness because
        # scan-sourced entries skip that check entirely.
        if _statement_count(canonical_sql) > 1:
            raise ValueError(
                "Multiple statements in one entry can't be saved to the "
                "registry; save one statement at a time."
            )

        # Enforce size limit for registry storage
        query_bytes = len(canonical_sql.encode("utf-8")) if canonical_sql else 0

        if query_bytes > MAX_QUERY_LENGTH:
            raise ValueError(
                f"Query size ({query_bytes:,} bytes) exceeds registry limit "
                f"({MAX_QUERY_LENGTH // 1024}KB)."
            )

        # Resolving a dialect here serves validation and extraction accuracy
        # (postgres accepts TABLESAMPLE, mysql keeps `->>`). The fallback
        # only needs to keep $N digits from being lifted into observed
        # values as literals; dialect choice itself is not identity-neutral
        # in general, so a stored hash can still shift with the resolved
        # dialect.
        if not dialect:
            dialect = dialect_for_target(target)

        if not skip_param_extraction:
            # Only validate syntax for user-provided queries. Audit-captured
            # queries come directly from the database and are known-complete;
            # their MySQL DIGEST_TEXT formatting may trip SQLGlot's parser.
            is_valid, parse_error = verify_query_completeness(canonical_sql, dialect)
            if not is_valid:
                raise ValueError(
                    f"Query appears truncated. {parse_error}\n"
                    "Queries >1KB may be truncated by database settings.\n"
                    "For MySQL: set performance_schema_max_digest_length = 16384 and max_digest_length = 16384\n"
                    "Or provide the full query with: rdst analyze -q '<full query>'"
                )

        # Use SQLGlot for robust normalization and parameter extraction
        if skip_param_extraction:
            # For pre-parameterized queries (from scan), skip extraction
            normalized_sql = canonical_sql
            params = {}
        else:
            normalized_sql, params = normalize_and_extract(canonical_sql, dialect)
            if observed_params_sql and not params:
                params = extract_observed_params(
                    observed_params_sql, normalized_sql, dialect
                )
        query_hash = hash_sql(canonical_sql)
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # Convert new params format to simple values for auto-substitution
        legacy_params = {}
        for param_name, param_info in params.items():
            legacy_params[param_name] = param_info["value"]

        is_new_query = query_hash not in self._queries

        if query_hash in self._queries:
            # Update existing entry
            entry = self._queries[query_hash]
            entry.last_analyzed = now
            entry.frequency = frequency if frequency > 0 else entry.frequency
            if tag and entry.tag and entry.tag != tag:
                logger.info(
                    "Query hash %s already exists with tag '%s'. Overwriting to '%s'.",
                    query_hash[:8], entry.tag, tag,
                )
            if tag:
                entry.tag = tag
                entry.source = source
            if question:
                entry.question = question
            # Backfill the original SQL if this entry predates the field, so
            # later hand-offs match what actually ran (dedupe hash unchanged).
            if not entry.original_sql:
                entry.original_sql = canonical_sql
            if target:  # Update last target used
                entry.last_target = target
            # Set once, at first ask; never stolen by a later re-add from
            # another target (the cross-target Ask-history leak).
            if ask_target and not entry.ask_target:
                entry.ask_target = ask_target

            # Update parameters with new SQLGlot format. An extraction that
            # found values never regresses to empty: observation cycles that
            # lack a literal-bearing sample must not erase observed values.
            if params:
                entry.parameters = params

            # Update runtime stats if provided (keep max values)
            if max_duration_ms > entry.max_duration_ms:
                entry.max_duration_ms = max_duration_ms
            if avg_duration_ms > 0:
                entry.avg_duration_ms = avg_duration_ms
            if observation_count > 0:
                entry.observation_count += observation_count

            # Update most recent params for auto-substitution
            if legacy_params:
                entry.most_recent_params = legacy_params
        else:
            # Create new entry
            entry = QueryEntry(
                sql=normalized_sql,  # Store normalized SQL with :p1, :p2 placeholders
                hash=query_hash,
                tag=tag,
                original_sql=canonical_sql,  # Faithful, un-normalized submitted SQL
                question=question,
                ask_target=ask_target,
                first_analyzed=now,
                last_analyzed=now,
                frequency=frequency,
                source=source,
                last_target=target,
                parameters=params,
                most_recent_params=legacy_params,
                max_duration_ms=max_duration_ms,
                avg_duration_ms=avg_duration_ms,
                observation_count=observation_count,
            )
            self._queries[query_hash] = entry

        self._update_lifecycle(
            entry,
            target=target,
            source=source,
            occurred_at=now,
            observed=observed,
            analyzed=analyzed,
            compared=compared,
            save_intent=save_intent,
        )

        if self._defer_depth:
            self._deferred_dirty = True
        else:
            self.save()
        return query_hash, is_new_query

    def add_queries_batch(
        self, items: Iterable[Dict[str, Any]]
    ) -> List[Tuple[str, bool]]:
        """Add multiple queries with a single registry persistence.

        Each item is a mapping of add_query keyword arguments. Returns the
        add_query result for each item, in order.
        """
        with self.defer_save():
            return [self.add_query(**item) for item in items]

    def get_query(self, query_hash: str) -> Optional[QueryEntry]:
        """
        Get a query by its hash or hash prefix (like git).

        Supports prefix matching: if exact hash not found, tries to match
        hash prefixes. Requires minimum 4 characters for prefix matching.

        Args:
            query_hash: The hash or hash prefix to retrieve

        Returns:
            QueryEntry if found, None otherwise
        """
        if not self._loaded:
            self.load()

        # Try exact match first
        if query_hash in self._queries:
            return self._queries[query_hash]

        # Try prefix matching (minimum 4 characters)
        if len(query_hash) >= 4:
            matches = [
                entry
                for hash_key, entry in self._queries.items()
                if hash_key.startswith(query_hash)
            ]

            if len(matches) == 1:
                return matches[0]
            elif len(matches) > 1:
                # Ambiguous prefix - could add error handling here
                # For now, return None (same as not found)
                return None

        return None

    def get_query_by_tag(self, tag: str) -> Optional[QueryEntry]:
        """
        Get a query by its tag.

        Args:
            tag: The tag to search for

        Returns:
            QueryEntry if found, None otherwise
        """
        if not self._loaded:
            self.load()

        for entry in self._queries.values():
            if entry.tag == tag:
                return entry

        return None

    def list_queries(self, limit: Optional[int] = None) -> List[QueryEntry]:
        """
        List all queries in the registry.

        Args:
            limit: Maximum number of queries to return

        Returns:
            List of QueryEntry objects, sorted by last_analyzed (newest first)
        """
        if not self._loaded:
            self.load()

        queries = list(self._queries.values())

        # Sort by last_analyzed (newest first)
        queries.sort(key=lambda q: q.last_analyzed, reverse=True)

        if limit:
            queries = queries[:limit]

        return queries

    def remove_query(self, query_hash: str) -> bool:
        """
        Remove a query from the registry.

        Args:
            query_hash: Hash of the query to remove

        Returns:
            True if query was found and removed, False otherwise
        """
        if not self._loaded:
            self.load()

        if query_hash in self._queries:
            del self._queries[query_hash]
            self.save()
            return True

        return False

    def mark_reviewed(
        self,
        query_hash: str,
        target: str = "",
        reviewed_at: Optional[str] = None,
    ) -> bool:
        """Mark a query reviewed for one target without affecting other targets."""
        if not self._loaded:
            self.load()

        entry = self.get_query(query_hash)
        if entry is None or not target or not entry.belongs_to_target(target):
            return False

        lifecycle = entry.lifecycle_for(target)
        if lifecycle is None:
            return False
        lifecycle.reviewed_at = reviewed_at or (
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
        self.save()
        return True

    def update_readyset_identity(
        self,
        query_hash: str,
        readyset_query_id: str,
        readyset_supported: str = "",
        cache_target: str = "",
    ) -> bool:
        """Store the canonical ReadySet query_id (q_<hash>) on a registry row.

        Populated whenever RDST has interacted with a ReadySet container for
        this query (cache add, analyze ephemeral, audit ephemeral, cache compare).
        Used as the source-of-truth ID for DROP CACHE and other cache lifecycle ops
        — fixes CLD-1748 / CLD-1754 where we erroneously used our client-side
        hash with DROP CACHE.

        Returns True if the query was found and updated.
        """
        if not self._loaded:
            self.load()
        if query_hash not in self._queries:
            return False
        entry = self._queries[query_hash]
        entry.readyset_query_id = readyset_query_id
        if readyset_supported:
            entry.readyset_supported = readyset_supported
        if cache_target:
            entry.last_cache_target = cache_target
        entry.readyset_last_observed_at = datetime.now(timezone.utc).isoformat()
        self.save()
        return True

    def find_by_readyset_query_id(self, readyset_query_id: str) -> Optional["QueryEntry"]:
        """Reverse lookup: find a registry entry by its ReadySet q_<hash> id."""
        if not self._loaded:
            self.load()
        for entry in self._queries.values():
            if entry.readyset_query_id == readyset_query_id:
                return entry
        return None

    def update_query_tag(self, query_hash: str, tag: str) -> bool:
        """
        Update the tag for an existing query.

        Args:
            query_hash: Hash of the query to update
            tag: New tag to assign

        Returns:
            True if query was found and updated, False otherwise
        """
        if not self._loaded:
            self.load()

        if query_hash in self._queries:
            self._queries[query_hash].tag = tag
            self.save()
            return True

        return False

    def get_or_create_hash(self, sql: str) -> str:
        """
        Get the hash for a SQL query, ensuring consistent normalization.

        This is useful for checking if a query already exists without adding it.

        Args:
            sql: SQL query string

        Returns:
            Hash of the normalized query
        """
        return hash_sql(sql)

    def query_exists(self, sql: str) -> bool:
        """
        Check if a query already exists in the registry.

        Args:
            sql: SQL query string

        Returns:
            True if query exists, False otherwise
        """
        query_hash = hash_sql(sql)
        return self.get_query(query_hash) is not None

    def get_executable_query(
        self, query_hash: str, interactive: bool = True
    ) -> Optional[str]:
        """
        Get an executable query for analysis by reconstructing with parameters.

        Uses SQLGlot for robust reconstruction. If parameters are missing,
        prompts the user to provide values (in interactive mode).

        Args:
            query_hash: Hash of the query to retrieve
            interactive: Whether to prompt user for missing parameters

        Returns:
            Executable SQL query string, or None if not found or missing required params
        """
        from .sql_normalizer import reconstruct_sql, get_placeholder_names

        entry = self.get_query(query_hash)
        if not entry:
            return None

        # Get placeholder names in the normalized SQL
        placeholder_names = get_placeholder_names(entry.sql)

        if not placeholder_names:
            # No placeholders - query is already executable
            return entry.sql

        # Use the new SQLGlot-extracted parameters if available
        params = entry.parameters or {}

        # Find which placeholders are missing values
        missing = placeholder_names - set(params.keys())

        if missing:
            if not interactive:
                # Non-interactive mode - cannot fill missing params
                return None

            # Interactive mode - prompt for missing values
            params = self._prompt_for_missing_params(params, sorted(missing))

        return reconstruct_sql(entry.sql, params, dialect_for_target(entry.last_target))

    def _prompt_for_missing_params(
        self, existing: Dict[str, dict], missing: list
    ) -> Dict[str, dict]:
        """
        Prompt user to fill in missing parameter values.

        Args:
            existing: Already-known parameters {'p1': {'value': x, 'type': t}, ...}
            missing: List of missing parameter names ['p2', 'p3']

        Returns:
            Combined parameters dict with user-provided values
        """
        params = dict(existing)

        print(f"\nQuery requires {len(missing)} parameter(s):")
        for param_name in missing:
            try:
                value = input(f"  Enter value for :{param_name}: ").strip()
                params[param_name] = typed_parameter(value, source="user")
            except KeyboardInterrupt:
                print("\nCancelled.")
                raise

        return params

    def get_executable_query_by_tag(
        self, tag: str, interactive: bool = True
    ) -> Optional[str]:
        """
        Get an executable query for analysis by tag.

        Args:
            tag: Tag to search for
            interactive: Whether to prompt user if multiple parameter sets exist

        Returns:
            Executable SQL query string, or None if not found
        """
        entry = self.get_query_by_tag(tag)
        if not entry:
            return None

        return self.get_executable_query(entry.hash, interactive)

    def update_parameter_history(
        self,
        query_hash: str,
        parameters: Dict[str, Any],
        target: str = "",
        source: str = "",
    ) -> bool:
        """
        Update the stored parameters for an existing query.

        This is used when a user provides parameter values for a
        parameterized query. Both parameter fields are written from the same
        values: most_recent_params for display and auto-substitution, and
        parameters in the typed shape get_executable_query reconstructs from,
        so the values also make the query runnable.

        Args:
            query_hash: Hash of the query to update
            parameters: Dictionary of parameter values (e.g., {'p1': 'value1', 'p2': 123})
            target: Optional target database name
            source: Optional provenance ("user", "suggested", "observed"),
                stored per value alongside its type

        Returns:
            True if update succeeded, False if query not found
        """
        if not self._loaded:
            self.load()

        entry = self.get_query(query_hash)
        if not entry:
            return False

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        values = {
            canonical_param_key(name): value for name, value in parameters.items()
        }
        entry.most_recent_params = values
        entry.parameters = {
            name: typed_parameter(value, source) for name, value in values.items()
        }
        entry.last_analyzed = now

        if target:
            entry.last_target = target
            # Changing last_target makes this identity belong to the target.
            # Persist the matching lifecycle row at the same time so the
            # target-indexed SQLite read model cannot disagree with
            # QueryEntry.belongs_to_target().
            entry.lifecycle_for(target, create=True)

        self.save()
        return True

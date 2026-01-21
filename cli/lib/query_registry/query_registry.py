"""
Query Registry Implementation

Core functionality for storing, retrieving, and managing SQL queries with
normalized hashing and TOML-based persistence.
"""

from __future__ import annotations

import hashlib
import re
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import toml
import sqlglot
from sqlglot.errors import ParseError

from lib.data_manager_service.data_manager_service_command_sets import MAX_QUERY_LENGTH

logger = logging.getLogger(__name__)


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
    if not sql or not sql.strip():
        return False, "Empty query"

    stripped = sql.strip()
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
        sqlglot.parse_one(sql, dialect=dialect)
        return True, None
    except ParseError as e:
        error_str = str(e).lower()
        if "unexpected" in error_str or "expected" in error_str:
            return False, f"Query appears to be truncated or malformed: {e}"
        return False, f"SQL parse error: {e}"
    except Exception as e:
        return False, f"Failed to parse SQL: {e}"


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

    normalized, _ = normalize_and_extract(query, dialect)
    return normalized


def hash_sql(query: str) -> str:
    """
    Generate a consistent hash for a SQL query.

    Uses normalized SQL to ensure the same logical query always
    produces the same hash regardless of formatting differences.

    Args:
        query: SQL query string (will be normalized)

    Returns:
        12-character hexadecimal hash string
    """
    normalized = normalize_sql(query)
    # nosemgrep: python.lang.security.insecure-hash-algorithms-md5.insecure-hash-algorithm-md5, gitlab.bandit.B303-1
    # MD5 is used for query fingerprinting/deduplication, not cryptographic purposes
    return hashlib.md5(
        normalized.encode("utf-8")
    ).hexdigest()[
        :12
    ]  # nosemgrep: python.lang.security.insecure-hash-algorithms-md5.insecure-hash-algorithm-md5, gitlab.bandit.B303-1


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


def extract_parameters_from_sql(
    original_sql: str, parameterized_sql: str
) -> Dict[str, Any]:
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
            # String parameters need quotes
            replacement = f"'{value}'"
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
    first_analyzed: str = ""
    last_analyzed: str = ""
    frequency: int = 0
    source: str = "manual"  # "manual", "top", "file", "stdin"
    last_target: str = ""  # Last target used for analysis
    # SQLGlot-extracted parameters as dict: {'p1': {'value': 'x', 'type': 'string'}, ...}
    parameters: Dict[str, dict] = field(default_factory=dict)
    # Most recent runtime parameter values (for auto-substitution when re-running queries)
    most_recent_params: Dict[str, Any] = field(default_factory=dict)
    # Runtime stats from rdst top (optional, only populated when saved from top)
    max_duration_ms: float = 0.0
    avg_duration_ms: float = 0.0
    observation_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for TOML serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QueryEntry":
        """Create QueryEntry from dictionary (TOML deserialization)."""
        # Handle backward compatibility
        if "last_target" not in data:
            data["last_target"] = ""
        if "most_recent_params" not in data:
            data["most_recent_params"] = {}
        if "parameters" not in data:
            data["parameters"] = {}
        # Runtime stats from rdst top (added in CLD-1645)
        if "max_duration_ms" not in data:
            data["max_duration_ms"] = 0.0
        if "avg_duration_ms" not in data:
            data["avg_duration_ms"] = 0.0
        if "observation_count" not in data:
            data["observation_count"] = 0

        # Remove deprecated parameter_history if present in old data
        data.pop("parameter_history", None)

        return cls(**data)


class QueryRegistry:
    """
    Manages persistent storage and retrieval of SQL queries.

    Stores queries in TOML format at ~/.rdst/queries.toml with structure:
    [queries.{hash}]
    sql = "SELECT * FROM users"
    hash = "{hash}"
    tag = "user_lookup"  # optional
    first_analyzed = "2024-01-15T10:30:00Z"
    last_analyzed = "2024-01-15T10:30:00Z"
    frequency = 1000
    source = "top"
    """

    def __init__(self, registry_path: Optional[str] = None):
        """
        Initialize the query registry.

        Args:
            registry_path: Custom path to registry file. Defaults to ~/.rdst/queries.toml
        """
        if registry_path:
            self.registry_path = Path(registry_path)
        else:
            self.registry_path = Path.home() / ".rdst" / "queries.toml"

        # In-memory cache of queries
        self._queries: Dict[str, QueryEntry] = {}
        self._loaded = False

    def _ensure_directory(self) -> None:
        """Ensure the registry directory exists."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> None:
        """Load queries from TOML file into memory."""
        if not self.registry_path.exists():
            self._queries = {}
            self._loaded = True
            return

        try:
            with open(self.registry_path, "r", encoding="utf-8") as f:
                data = toml.load(f)

            # Load queries from TOML structure
            queries_data = data.get("queries", {})
            self._queries = {}

            for query_hash, query_data in queries_data.items():
                try:
                    self._queries[query_hash] = QueryEntry.from_dict(query_data)
                except Exception as e:
                    # Skip malformed entries but continue loading others
                    print(f"Warning: Skipping malformed query entry {query_hash}: {e}")
                    continue

            self._loaded = True

        except Exception as e:
            # If loading fails, start with empty registry
            print(f"Warning: Could not load query registry: {e}")
            self._queries = {}
            self._loaded = True

    def save(self) -> None:
        """Save queries from memory to TOML file."""
        if not self._loaded:
            self.load()

        self._ensure_directory()

        # Convert to TOML structure
        toml_data = {"queries": {}}

        for query_hash, entry in self._queries.items():
            toml_data["queries"][query_hash] = entry.to_dict()

        try:
            with open(self.registry_path, "w", encoding="utf-8") as f:
                toml.dump(toml_data, f)
        except Exception as e:
            raise RuntimeError(f"Failed to save query registry: {e}")

    def add_query(
        self,
        sql: str,
        tag: str = "",
        source: str = "manual",
        frequency: int = 0,
        target: str = "",
        dialect: str = None,
        max_duration_ms: float = 0.0,
        avg_duration_ms: float = 0.0,
        observation_count: int = 0,
    ) -> tuple[str, bool]:
        """
        Add a query to the registry with parameter extraction and history.

        Uses SQLGlot for robust parameter extraction that correctly handles
        comments and complex SQL structures.

        Args:
            sql: SQL query string (with actual parameter values)
            tag: Optional tag for the query
            source: Source of the query ("manual", "top", "file", "stdin")
            frequency: Query frequency from telemetry (if available)
            target: Target database name for this analysis
            dialect: Optional SQL dialect ('postgres', 'mysql', etc.)
            max_duration_ms: Maximum observed duration in ms (from rdst top)
            avg_duration_ms: Average observed duration in ms (from rdst top)
            observation_count: Number of times query was observed (from rdst top)

        Returns:
            Tuple of (query_hash, is_new) where is_new is True if this was a new query pattern

        Raises:
            ValueError: If query exceeds 4KB size limit
        """
        from .sql_normalizer import normalize_and_extract

        if not self._loaded:
            self.load()

        # Enforce 4KB size limit for registry storage
        query_bytes = len(sql.encode("utf-8")) if sql else 0

        if query_bytes > MAX_QUERY_LENGTH:
            raise ValueError(
                f"Query size ({query_bytes:,} bytes) exceeds registry limit (4KB). "
                "Large queries cannot be saved to the registry. "
                "Use 'rdst analyze --large-query-bypass' for one-time analysis of large queries."
            )

        is_valid, parse_error = verify_query_completeness(sql, dialect)
        if not is_valid:
            raise ValueError(
                f"Query appears truncated. {parse_error}\n"
                "Queries >1KB captured from 'rdst top' may be truncated by database settings.\n"
                "Provide the full query with: rdst analyze -q '<full query>'"
            )

        # Use SQLGlot for robust normalization and parameter extraction
        normalized_sql, params = normalize_and_extract(sql, dialect)
        query_hash = hash_sql(sql)
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
            if tag and not entry.tag:  # Don't overwrite existing tags
                entry.tag = tag
            if target:  # Update last target used
                entry.last_target = target

            # Update parameters with new SQLGlot format
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

        self.save()
        return query_hash, is_new_query

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

        return reconstruct_sql(entry.sql, params)

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
                # Infer type from input
                try:
                    params[param_name] = {"value": int(value), "type": "number"}
                except ValueError:
                    try:
                        params[param_name] = {"value": float(value), "type": "number"}
                    except ValueError:
                        params[param_name] = {"value": value, "type": "string"}
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
        self, query_hash: str, parameters: Dict[str, Any], target: str = ""
    ) -> bool:
        """
        Update the stored parameters for an existing query.

        This is used when a user provides parameter values interactively
        for a parameterized query. The values are stored for auto-substitution
        on subsequent runs.

        Args:
            query_hash: Hash of the query to update
            parameters: Dictionary of parameter values (e.g., {'p1': 'value1', 'p2': 123})
            target: Optional target database name

        Returns:
            True if update succeeded, False if query not found
        """
        if not self._loaded:
            self.load()

        entry = self.get_query(query_hash)
        if not entry:
            return False

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        entry.most_recent_params = parameters
        entry.last_analyzed = now

        if target:
            entry.last_target = target

        self.save()
        return True

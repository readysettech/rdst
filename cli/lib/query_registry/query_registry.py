"""
Query Registry Implementation

Core functionality for storing, retrieving, and managing SQL queries with
normalized hashing and TOML-based persistence.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
import toml
import sqlglot

from lib.data_manager_service.data_manager_service_command_sets import MAX_QUERY_LENGTH


def normalize_sql(query: str) -> str:
    """
    Normalize SQL query for consistent hashing and parameterization.

    Normalization steps:
    1. Strip leading/trailing whitespace
    2. Collapse multiple whitespace characters to single spaces
    3. Remove trailing semicolons
    4. Replace PostgreSQL-style placeholders ($1, $2) with ?
    5. Replace string literals with ? placeholders
    6. Replace numeric literals with ? placeholders

    Args:
        query: Raw SQL query string

    Returns:
        Normalized and parameterized SQL query string
    """
    if not query:
        return ""

    # Strip leading/trailing whitespace
    normalized = query.strip()

    # Collapse multiple whitespace (spaces, tabs, newlines) to single spaces
    normalized = re.sub(r'\s+', ' ', normalized)

    # Remove trailing semicolon and any whitespace after it
    normalized = re.sub(r';\s*$', '', normalized)

    # Replace PostgreSQL-style placeholders ($1, $2, etc.) with ? FIRST
    # This ensures parameterized queries hash the same as queries with values
    normalized = re.sub(r'\$\d+', '?', normalized)

    # Replace string literals with ? placeholders
    normalized = re.sub(r"'[^']*'", '?', normalized)

    # Replace numeric literals with ? placeholders
    normalized = re.sub(r'\b\d+(?:\.\d+)?\b', '?', normalized)

    # Final trim to ensure no trailing whitespace
    normalized = normalized.strip()

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
    return hashlib.md5(normalized.encode('utf-8')).hexdigest()[:12]  # nosemgrep: python.lang.security.insecure-hash-algorithms-md5.insecure-hash-algorithm-md5, gitlab.bandit.B303-1


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
    numeric_literals = re.findall(r'\b(\d+(?:\.\d+)?)\b', original_sql)

    # Count placeholders in parameterized version
    placeholder_count = parameterized_sql.count('?')

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
                if '.' in token:
                    params[f"param_{i}"] = float(token)
                else:
                    params[f"param_{i}"] = int(token)
            except ValueError:
                params[f"param_{i}"] = token

    return params


def reconstruct_query_with_params(parameterized_sql: str, params: Dict[str, Any]) -> str:
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
        result = result.replace('?', replacement, 1)

    return result


@dataclass
class ParameterSet:
    """
    Represents a set of parameter values used with a query.
    """
    values: Dict[str, Any]
    analyzed_at: str
    target: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ParameterSet':
        # Handle backward compatibility - ensure required fields exist
        if 'values' not in data:
            data['values'] = {}
        if 'analyzed_at' not in data:
            data['analyzed_at'] = ""
        if 'target' not in data:
            data['target'] = ""
        return cls(**data)


@dataclass
class QueryEntry:
    """
    Represents a stored query with metadata and parameter history.
    """
    sql: str  # Parameterized SQL with ? placeholders
    hash: str
    tag: str = ""
    first_analyzed: str = ""
    last_analyzed: str = ""
    frequency: int = 0
    source: str = "manual"  # "manual", "top", "file", "stdin"
    last_target: str = ""  # Last target used for analysis
    parameter_history: List[ParameterSet] = field(default_factory=list)  # Up to 10 recent parameter sets
    most_recent_params: Dict[str, Any] = field(default_factory=dict)  # Quick access to latest

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for TOML serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'QueryEntry':
        """Create QueryEntry from dictionary (TOML deserialization)."""
        # Handle backward compatibility
        if 'last_target' not in data:
            data['last_target'] = ""
        if 'parameter_history' not in data:
            data['parameter_history'] = []
        if 'most_recent_params' not in data:
            data['most_recent_params'] = {}

        # Convert parameter history from dicts to ParameterSet objects
        if 'parameter_history' in data and data['parameter_history']:
            param_history = []
            for param_data in data['parameter_history']:
                if isinstance(param_data, dict):
                    param_history.append(ParameterSet.from_dict(param_data))
                else:
                    param_history.append(param_data)  # Already ParameterSet objects
            data['parameter_history'] = param_history

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
            with open(self.registry_path, 'r', encoding='utf-8') as f:
                data = toml.load(f)

            # Load queries from TOML structure
            queries_data = data.get('queries', {})
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
        toml_data = {
            'queries': {}
        }

        for query_hash, entry in self._queries.items():
            toml_data['queries'][query_hash] = entry.to_dict()

        try:
            with open(self.registry_path, 'w', encoding='utf-8') as f:
                toml.dump(toml_data, f)
        except Exception as e:
            raise RuntimeError(f"Failed to save query registry: {e}")

    def add_query(self, sql: str, tag: str = "", source: str = "manual",
                  frequency: int = 0, target: str = "") -> tuple[str, bool]:
        """
        Add a query to the registry with parameter extraction and history.

        Args:
            sql: SQL query string (with actual parameter values)
            tag: Optional tag for the query
            source: Source of the query ("manual", "top", "file", "stdin")
            frequency: Query frequency from telemetry (if available)
            target: Target database name for this analysis

        Returns:
            Tuple of (query_hash, is_new) where is_new is True if this was a new query pattern

        Raises:
            ValueError: If query exceeds 1KB size limit
        """
        if not self._loaded:
            self.load()

        # Enforce 1KB size limit for registry storage
        query_bytes = len(sql.encode('utf-8')) if sql else 0

        if query_bytes > MAX_QUERY_LENGTH:
            raise ValueError(
                f"Query size ({query_bytes:,} bytes) exceeds registry limit (1KB). "
                "Large queries cannot be saved to the registry. "
                "Use 'rdst analyze --large-query-bypass' for one-time analysis of large queries."
            )

        # Normalize SQL for hashing and parameterization
        normalized_sql = normalize_sql(sql)
        query_hash = hash_sql(sql)
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        # Extract parameters from the original SQL
        parameters = extract_parameters_from_sql(sql, normalized_sql)

        # Create parameter set for this analysis
        param_set = ParameterSet(
            values=parameters,
            analyzed_at=now,
            target=target
        )

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

            # Add new parameter set to history (if different from most recent)
            if not entry.parameter_history or entry.most_recent_params != parameters:
                # Add to front of history (most recent first)
                entry.parameter_history.insert(0, param_set)
                # Keep only last 10 parameter sets
                entry.parameter_history = entry.parameter_history[:10]
                entry.most_recent_params = parameters
        else:
            # Create new entry
            entry = QueryEntry(
                sql=normalized_sql,  # Store parameterized version
                hash=query_hash,
                tag=tag,
                first_analyzed=now,
                last_analyzed=now,
                frequency=frequency,
                source=source,
                last_target=target,
                parameter_history=[param_set],
                most_recent_params=parameters
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
                entry for hash_key, entry in self._queries.items()
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

    def get_executable_query(self, query_hash: str, interactive: bool = True) -> Optional[str]:
        """
        Get an executable query for analysis by reconstructing with parameters.

        Args:
            query_hash: Hash of the query to retrieve
            interactive: Whether to prompt user if multiple parameter sets exist

        Returns:
            Executable SQL query string, or None if not found
        """
        entry = self.get_query(query_hash)
        if not entry:
            return None

        param_history = entry.parameter_history
        if not param_history:
            # No parameters stored - return the SQL as-is (shouldn't happen)
            return entry.sql

        if len(param_history) == 1:
            # Single parameter set - use it
            return reconstruct_query_with_params(entry.sql, param_history[0].values)

        if not interactive:
            # Non-interactive mode - use most recent parameters
            return reconstruct_query_with_params(entry.sql, entry.most_recent_params)

        # Interactive mode - let user choose
        print(f"Found {len(param_history)} previous analyses for this query pattern:")
        for i, param_set in enumerate(param_history, 1):
            reconstructed = reconstruct_query_with_params(entry.sql, param_set.values)
            timestamp = param_set.analyzed_at[:19].replace('T', ' ')  # Format timestamp
            print(f"[{i}] {reconstructed} ({timestamp})")

        try:
            choice = input("Which to analyze [1]: ").strip() or "1"
            selected_idx = int(choice) - 1
            if 0 <= selected_idx < len(param_history):
                selected_params = param_history[selected_idx].values
                return reconstruct_query_with_params(entry.sql, selected_params)
            else:
                # Invalid choice - use most recent
                return reconstruct_query_with_params(entry.sql, entry.most_recent_params)
        except (ValueError, KeyboardInterrupt):
            # Invalid input or user cancelled - use most recent
            return reconstruct_query_with_params(entry.sql, entry.most_recent_params)

    def get_executable_query_by_tag(self, tag: str, interactive: bool = True) -> Optional[str]:
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

    def update_parameter_history(self, query_hash: str, parameters: Dict[str, Any],
                                  target: str = "") -> bool:
        """
        Update the parameter history for an existing query.

        This is used when a user provides parameter values interactively
        for a parameterized query that was stored without actual values.

        Args:
            query_hash: Hash of the query to update
            parameters: Dictionary of parameter values (e.g., {'param_1': 'value1', 'param_2': 123})
            target: Optional target database name

        Returns:
            True if update succeeded, False if query not found
        """
        if not self._loaded:
            self.load()

        entry = self.get_query(query_hash)
        if not entry:
            return False

        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        # Create new parameter set
        param_set = ParameterSet(
            values=parameters,
            analyzed_at=now,
            target=target
        )

        # Add to front of history (most recent first)
        entry.parameter_history.insert(0, param_set)

        # Keep only last 10 parameter sets
        entry.parameter_history = entry.parameter_history[:10]

        # Update most recent params
        entry.most_recent_params = parameters

        # Update last_analyzed timestamp
        entry.last_analyzed = now

        if target:
            entry.last_target = target

        self.save()
        return True
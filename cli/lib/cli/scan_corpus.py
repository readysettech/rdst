"""
RDST Scan Corpus Manager

Stores and manages the query corpus - the set of known queries in a codebase.

The corpus uses deep normalization for query hashing to ensure queries can be
matched across different sources:
- LLM-generated SQL (from scan)
- ORM output (SQLAlchemy, Django, etc.)
- Runtime queries (pg_stat_activity, rdst top)
"""

import os
import hashlib
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Any

# Import deep normalization from query_registry for cross-source matching
from lib.query_registry.query_registry import hash_sql_deep, normalize_sql_deep

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


class QueryCorpus:
    """
    Manages the query corpus for a project.

    The corpus is a collection of discovered queries with their metadata,
    stored in ~/.rdst/corpus/<project>.yaml
    """

    def __init__(self, project_name: str = "default"):
        self.project_name = project_name
        self.corpus_dir = Path.home() / ".rdst" / "corpus"
        self.corpus_file = self.corpus_dir / f"{project_name}.yaml"
        self.queries: Dict[str, Dict] = {}  # hash -> query data
        self.metadata: Dict[str, Any] = {}

    def load(self) -> bool:
        """Load corpus from disk. Returns True if loaded successfully."""
        if not self.corpus_file.exists():
            return False

        try:
            content = self.corpus_file.read_text()
            if YAML_AVAILABLE:
                data = yaml.safe_load(content)
            else:
                data = json.loads(content)

            self.metadata = {
                "version": data.get("version", 1),
                "project": data.get("project", self.project_name),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
            }

            # Index queries by hash
            for q in data.get("queries", []):
                if "hash" in q:
                    self.queries[q["hash"]] = q

            return True
        except Exception as e:
            print(f"Warning: Could not load corpus: {e}")
            return False

    def save(self):
        """Save corpus to disk."""
        self.corpus_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.utcnow().isoformat() + "Z"

        data = {
            "version": 1,
            "project": self.project_name,
            "created_at": self.metadata.get("created_at", now),
            "updated_at": now,
            "queries": list(self.queries.values()),
        }

        if YAML_AVAILABLE:
            content = yaml.dump(data, default_flow_style=False, sort_keys=False)
        else:
            content = json.dumps(data, indent=2)

        self.corpus_file.write_text(content)

    def add_query(self, query_data: Dict) -> str:
        """
        Add a query to the corpus.

        Returns the query hash.
        """
        # Generate hash from SQL (normalized)
        sql = query_data.get("sql", "")
        query_hash = self._hash_query(sql)

        now = datetime.utcnow().isoformat() + "Z"

        # Check if already exists
        if query_hash in self.queries:
            # Update last_seen_at
            self.queries[query_hash]["last_seen_at"] = now
            # Update location if changed
            if query_data.get("file"):
                self.queries[query_hash]["file"] = query_data["file"]
            if query_data.get("function"):
                self.queries[query_hash]["function"] = query_data["function"]
        else:
            # New query
            self.queries[query_hash] = {
                "hash": query_hash,
                "file": query_data.get("file", "unknown"),
                "function": query_data.get("function", "unknown"),
                "orm_code": query_data.get("orm_code", ""),
                "sql": sql,
                "issues": query_data.get("issues", []),
                "discovered_at": now,
                "last_seen_at": now,
                "status": "new",
            }

        return query_hash

    def get_query(self, query_hash: str) -> Optional[Dict]:
        """Get a query by hash."""
        return self.queries.get(query_hash)

    def list_queries(
        self,
        with_issues: bool = False,
        file_pattern: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict]:
        """
        List queries with optional filters.

        Args:
            with_issues: Only return queries with issues
            file_pattern: Filter by file path (glob pattern)
            status: Filter by status (new, approved, flagged)
        """
        import fnmatch

        results = []
        for q in self.queries.values():
            # Filter by issues
            if with_issues and not q.get("issues"):
                continue

            # Filter by file pattern
            if file_pattern:
                if not fnmatch.fnmatch(q.get("file", ""), file_pattern):
                    continue

            # Filter by status
            if status and q.get("status") != status:
                continue

            results.append(q)

        return results

    def get_new_queries(self, other_hashes: List[str]) -> List[str]:
        """
        Given a list of query hashes from a scan, return which ones are new.
        """
        return [h for h in other_hashes if h not in self.queries]

    def update_status(self, query_hash: str, status: str):
        """Update the status of a query (new, approved, flagged)."""
        if query_hash in self.queries:
            self.queries[query_hash]["status"] = status

    def get_stats(self) -> Dict:
        """Get corpus statistics."""
        queries = list(self.queries.values())

        issues_count = sum(1 for q in queries if q.get("issues"))
        by_status = {}
        for q in queries:
            status = q.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1

        # Count issue types
        issue_types = {}
        for q in queries:
            for issue in q.get("issues", []):
                if isinstance(issue, str):
                    issue_types[issue] = issue_types.get(issue, 0) + 1
                elif isinstance(issue, dict):
                    t = issue.get("type", "unknown")
                    issue_types[t] = issue_types.get(t, 0) + 1

        return {
            "total_queries": len(queries),
            "queries_with_issues": issues_count,
            "by_status": by_status,
            "issue_types": issue_types,
        }

    def _hash_query(self, sql: str) -> str:
        """
        Generate a hash for a query using deep normalization.

        Uses hash_sql_deep() which normalizes across different SQL sources:
        - Removes column aliases (AS alias)
        - Removes table prefixes (table.column -> column)
        - Lowercases everything
        - Normalizes all parameter styles to ?

        This ensures queries from scan (LLM-generated), ORM output (SQLAlchemy),
        and runtime (pg_stat_activity/rdst top) produce the same hash.
        """
        return hash_sql_deep(sql)

    def clear(self):
        """Clear all queries from corpus."""
        self.queries = {}


def get_corpus(project_name: str = "default") -> QueryCorpus:
    """Get or create a corpus for a project."""
    corpus = QueryCorpus(project_name)
    corpus.load()  # Load if exists, otherwise empty
    return corpus

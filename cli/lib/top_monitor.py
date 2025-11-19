"""
Real-time query activity monitoring for RDST Top command.

This module provides real-time tracking of active database queries by polling
pg_stat_activity (PostgreSQL) or SHOW FULL PROCESSLIST (MySQL) every 200ms.

Key Design:
- Fetch individual process/connection data (no GROUP BY aggregation)
- Track each process ID separately to detect completions
- Aggregate metrics by query hash in Python
- Display top 10 to user

Limitations:
- Only catches queries running longer than poll interval (~200ms)
- Shows "max duration observed" not complete execution time
- Queries <200ms may be missed
"""

import hashlib
import time
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, field


@dataclass
class QueryMetrics:
    """Metrics tracked for each unique query hash."""
    query_hash: str
    query_text: str
    normalized_query: str = ""      # Parameterized query for display
    max_duration_seen: float = 0.0  # Peak duration across all executions (ms)
    sum_of_durations: float = 0.0   # Sum for calculating average
    observation_count: int = 0      # Number of completed executions observed
    avg_duration: float = 0.0       # Average of completed query executions
    current_instances_running: int = 0  # Current number of connections running this query
    last_seen: datetime = field(default_factory=datetime.now)
    first_seen: datetime = field(default_factory=datetime.now)


@dataclass
class ProcessInfo:
    """Information about a single database process/connection."""
    process_id: int
    query_hash: str
    duration_ms: float
    last_seen_poll: int  # Poll number when last seen


def normalize_query(query_text: str) -> str:
    """
    Normalize SQL query by replacing literal values with placeholders.

    This enables grouping of queries that differ only in parameter values.

    Replaces:
    - String literals: 'value' -> ?
    - Numeric literals: 123 -> ?

    Args:
        query_text: Raw SQL query text

    Returns:
        Normalized query with literals replaced by ?
    """
    if not query_text:
        return query_text

    # Replace string literals (single-quoted strings)
    # Handle escaped quotes by matching everything between quotes
    normalized = re.sub(r"'(?:[^'\\]|\\.)*'", '?', query_text)

    # Replace numeric literals (standalone numbers, including decimals and negative)
    # Match numbers that are word-bounded (not part of identifiers)
    normalized = re.sub(r'\b-?\d+\.?\d*\b', '?', normalized)

    # Collapse multiple consecutive ? into single ?
    # (when multiple params are adjacent like LIMIT ?, ?)
    normalized = re.sub(r'\?(\s*,\s*\?)+', '?', normalized)

    return normalized


class ActivityQueryCollector:
    """
    Collects active query data from PostgreSQL pg_stat_activity
    or MySQL SHOW FULL PROCESSLIST.
    """

    # PostgreSQL: Fetch top 250 individual processes by duration
    PG_ACTIVITY_QUERY = """
        SELECT
            pid as process_id,
            query as query_text,
            EXTRACT(EPOCH FROM (NOW() - query_start)) * 1000 as duration_ms
        FROM pg_stat_activity
        WHERE state = 'active'
          AND query NOT LIKE 'autovacuum:%'
          AND query NOT LIKE '%pg_stat_activity%'
          AND query_start IS NOT NULL
          AND datname = current_database()
        ORDER BY duration_ms DESC
        LIMIT 250
    """

    # MySQL: Fetch top 250 individual processes by duration
    MYSQL_ACTIVITY_QUERY = """
        SELECT
            ID as process_id,
            INFO as query_text,
            TIME * 1000 as duration_ms
        FROM information_schema.PROCESSLIST
        WHERE COMMAND != 'Sleep'
          AND COMMAND != 'Daemon'
          AND USER != 'event_scheduler'
          AND INFO IS NOT NULL
          AND INFO NOT LIKE '%PROCESSLIST%'
          AND DB = DATABASE()
        ORDER BY duration_ms DESC
        LIMIT 250
    """

    def __init__(self, db_engine: str, connection):
        """
        Initialize collector.

        Args:
            db_engine: 'postgresql' or 'mysql'
            connection: Direct database connection (psycopg2 or pymysql)
        """
        self.db_engine = db_engine.lower()
        self.connection = connection

        if self.db_engine not in ['postgresql', 'mysql']:
            raise ValueError(f"Unsupported database engine: {db_engine}")

    def fetch_active_queries(self) -> List[Dict]:
        """
        Fetch currently active individual processes.

        Returns:
            List of dicts with keys: process_id, query_hash, query_text, duration_ms
        """
        query = self.PG_ACTIVITY_QUERY if self.db_engine == 'postgresql' else self.MYSQL_ACTIVITY_QUERY

        cursor = self.connection.cursor()
        try:
            cursor.execute(query)

            if self.db_engine == 'postgresql':
                # psycopg2 returns list of tuples
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                results = [dict(zip(columns, row)) for row in rows]
            else:
                # pymysql returns list of dicts with cursor=DictCursor
                # But we need to handle both cases
                if cursor.description:
                    columns = [desc[0] for desc in cursor.description]
                    rows = cursor.fetchall()
                    if rows and isinstance(rows[0], dict):
                        results = rows
                    else:
                        results = [dict(zip(columns, row)) for row in rows]
                else:
                    results = []

            # Compute query_hash from normalized query text
            for row in results:
                query_text = row.get('query_text', '')
                normalized = normalize_query(query_text)
                # nosemgrep: python.lang.security.insecure-hash-algorithms-md5.insecure-hash-algorithm-md5, gitlab.bandit.B303-1
                # MD5 is used for query identification/grouping, not cryptographic purposes
                row['query_hash'] = hashlib.md5(normalized.encode()).hexdigest()  # nosemgrep: python.lang.security.insecure-hash-algorithms-md5.insecure-hash-algorithm-md5, gitlab.bandit.B303-1

            return results

        except Exception as e:
            raise RuntimeError(f"Failed to fetch active queries: {e}")
        finally:
            cursor.close()


class QueryTracker:
    """
    Tracks query metrics over time by monitoring individual processes.

    Detects query completions when process IDs disappear between polls.
    """

    def __init__(self):
        """Initialize query tracker with empty state."""
        self.queries: Dict[str, QueryMetrics] = {}  # query_hash -> metrics
        self.processes: Dict[int, ProcessInfo] = {}  # process_id -> info
        self.start_time = datetime.now()
        self.poll_count = 0

    def update(self, query_data: List[Dict]):
        """
        Update tracker with new poll results.

        Detects completed queries by tracking which process IDs disappeared.

        Args:
            query_data: List of process observations from fetch_active_queries()
        """
        self.poll_count += 1
        current_process_ids: Set[int] = set()

        # First pass: Track current processes and update durations
        for row in query_data:
            process_id = int(row['process_id'])
            query_hash = row['query_hash']
            query_text = row['query_text']
            duration_ms = float(row['duration_ms'])

            current_process_ids.add(process_id)

            # Ensure query metrics exist
            if query_hash not in self.queries:
                normalized = normalize_query(query_text)
                self.queries[query_hash] = QueryMetrics(
                    query_hash=query_hash,
                    query_text=query_text,
                    normalized_query=normalized
                )

            # Update process info
            if process_id in self.processes:
                # Existing process - check if duration decreased (query completed and restarted)
                old_duration = self.processes[process_id].duration_ms
                old_query_hash = self.processes[process_id].query_hash

                # If duration decreased, the query completed and a new one started
                if duration_ms < old_duration:
                    # Record completion metrics for the old query
                    if old_query_hash in self.queries:
                        old_metrics = self.queries[old_query_hash]
                        old_metrics.max_duration_seen = max(old_metrics.max_duration_seen, old_duration)
                        old_metrics.observation_count += 1
                        old_metrics.sum_of_durations += old_duration
                        old_metrics.avg_duration = (
                            old_metrics.sum_of_durations / old_metrics.observation_count
                        )

                # Update to new values (whether duration increased or decreased)
                self.processes[process_id].duration_ms = duration_ms
                self.processes[process_id].query_hash = query_hash
                self.processes[process_id].last_seen_poll = self.poll_count
            else:
                # New process detected
                self.processes[process_id] = ProcessInfo(
                    process_id=process_id,
                    query_hash=query_hash,
                    duration_ms=duration_ms,
                    last_seen_poll=self.poll_count
                )

            # Update last_seen timestamp
            query_metrics = self.queries[query_hash]
            query_metrics.last_seen = datetime.now()

        # Second pass: Detect completed processes (disappeared from this poll)
        completed_process_ids = set(self.processes.keys()) - current_process_ids

        for process_id in completed_process_ids:
            process_info = self.processes[process_id]
            query_hash = process_info.query_hash

            # Query completed! Update max duration, increment observation count and update average
            if query_hash in self.queries:
                query_metrics = self.queries[query_hash]
                # NOW update max duration with final completed duration
                query_metrics.max_duration_seen = max(query_metrics.max_duration_seen, process_info.duration_ms)
                query_metrics.observation_count += 1
                query_metrics.sum_of_durations += process_info.duration_ms
                query_metrics.avg_duration = (
                    query_metrics.sum_of_durations / query_metrics.observation_count
                )

            # Remove completed process from tracking
            del self.processes[process_id]

        # Third pass: Count current instances per query
        instance_counts: Dict[str, int] = {}
        for process_info in self.processes.values():
            query_hash = process_info.query_hash
            instance_counts[query_hash] = instance_counts.get(query_hash, 0) + 1

        # Update instance counts
        for query_hash, query_metrics in self.queries.items():
            query_metrics.current_instances_running = instance_counts.get(query_hash, 0)

    def get_top_n(self, n: int = 10, sort_by: str = 'max') -> List[QueryMetrics]:
        """
        Get top N queries sorted by specified metric.

        Args:
            n: Number of top queries to return
            sort_by: Sort metric - 'max' (max_duration) or 'avg' (avg_duration)

        Returns:
            List of QueryMetrics sorted descending by specified metric
        """
        if sort_by == 'avg':
            sorted_queries = sorted(
                self.queries.values(),
                key=lambda q: q.avg_duration,
                reverse=True
            )
        else:  # default to 'max'
            sorted_queries = sorted(
                self.queries.values(),
                key=lambda q: q.max_duration_seen,
                reverse=True
            )

        return sorted_queries[:n]

    def get_total_queries_tracked(self) -> int:
        """Return total number of unique queries tracked."""
        return len(self.queries)

    def get_runtime_seconds(self) -> float:
        """Return how long tracker has been running in seconds."""
        return (datetime.now() - self.start_time).total_seconds()

    def clear(self):
        """Clear all tracked queries and processes."""
        self.queries.clear()
        self.processes.clear()
        self.start_time = datetime.now()
        self.poll_count = 0

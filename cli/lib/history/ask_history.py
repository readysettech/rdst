"""
Query History Management for RDST Ask Command

Stores query history in ~/.rdst/ask_history.jsonl
"""

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class HistoryEntry:
    """Single history entry."""
    timestamp: str
    question: str
    sql: str
    target: str
    rows: int
    execution_time_ms: float

    @classmethod
    def from_dict(cls, data: dict) -> 'HistoryEntry':
        """Create from dictionary."""
        return cls(**data)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)


class AskHistory:
    """Manages query history storage and retrieval."""

    def __init__(self, history_file: Optional[str] = None):
        """
        Initialize history manager.

        Args:
            history_file: Path to history file (default: ~/.rdst/ask_history.jsonl)
        """
        if history_file is None:
            history_file = Path.home() / '.rdst' / 'ask_history.jsonl'

        self.history_file = Path(history_file)
        self._ensure_directory()

    def _ensure_directory(self):
        """Ensure history directory exists."""
        self.history_file.parent.mkdir(parents=True, exist_ok=True)

        # Create empty file if it doesn't exist
        if not self.history_file.exists():
            self.history_file.touch()

    def add_entry(
        self,
        question: str,
        sql: str,
        target: str,
        rows: int,
        execution_time_ms: float
    ):
        """
        Add new entry to history.

        Args:
            question: Natural language question
            sql: Generated SQL
            target: Target database
            rows: Number of rows returned
            execution_time_ms: Execution time in milliseconds
        """
        entry = HistoryEntry(
            timestamp=datetime.now().isoformat(),
            question=question,
            sql=sql,
            target=target,
            rows=rows,
            execution_time_ms=execution_time_ms
        )

        # Append to file
        with open(self.history_file, 'a') as f:
            f.write(json.dumps(entry.to_dict()) + '\n')

    def get_recent(self, limit: int = 20) -> List[HistoryEntry]:
        """
        Get recent queries.

        Args:
            limit: Maximum number of entries to return

        Returns:
            List of recent entries (newest first)
        """
        if not self.history_file.exists():
            return []

        entries = []
        with open(self.history_file, 'r') as f:
            for line in f:
                if line.strip():
                    try:
                        entry = HistoryEntry.from_dict(json.loads(line))
                        entries.append(entry)
                    except (json.JSONDecodeError, TypeError):
                        # Skip malformed entries
                        continue

        # Return newest first
        return list(reversed(entries[-limit:]))

    def search(self, query: str) -> List[HistoryEntry]:
        """
        Search history by question or SQL.

        Args:
            query: Search term (case-insensitive)

        Returns:
            Matching entries (newest first)
        """
        all_entries = self.get_recent(limit=1000)  # Search all
        query_lower = query.lower()

        matches = [
            entry for entry in all_entries
            if query_lower in entry.question.lower()
            or query_lower in entry.sql.lower()
        ]

        return matches

    def get_by_index(self, index: int) -> Optional[HistoryEntry]:
        """
        Get entry by index (1-based, as shown to user).

        Args:
            index: Entry index (1 = most recent)

        Returns:
            HistoryEntry or None if not found
        """
        entries = self.get_recent(limit=100)

        # Convert to 0-based index
        idx = index - 1

        if 0 <= idx < len(entries):
            return entries[idx]

        return None

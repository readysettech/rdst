"""
Query Defaults - Smart defaults for SQL queries.

Inferred from hypothesis SQL and natural language question to provide
sensible defaults that users can accept or customize.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class QueryFilter:
    """Single filter condition."""

    column: str
    operator: str  # '=', '>', '<', '>=', '<=', 'LIKE', 'ILIKE', 'IN', etc.
    value: Any
    description: str  # Human-readable description

    def to_sql(self) -> str:
        """Convert to SQL WHERE clause fragment."""
        if self.operator.upper() in ('LIKE', 'ILIKE'):
            return f"{self.column} {self.operator} '{self.value}'"
        elif self.operator.upper() == 'IN':
            if isinstance(self.value, (list, tuple)):
                values = ', '.join(f"'{v}'" if isinstance(v, str) else str(v) for v in self.value)
                return f"{self.column} IN ({values})"
            else:
                return f"{self.column} IN ({self.value})"
        elif isinstance(self.value, str):
            return f"{self.column} {self.operator} '{self.value}'"
        else:
            return f"{self.column} {self.operator} {self.value}"


@dataclass
class QueryDefaults:
    """
    Smart defaults for a query.

    Inferred from hypothesis SQL and user's natural language question.
    """

    limit: int = 10
    sort_column: Optional[str] = None
    sort_direction: str = "DESC"  # "ASC" or "DESC"
    filters: List[QueryFilter] = field(default_factory=list)
    date_range: Optional[str] = None  # "all time", "last 7 days", "last 30 days", etc.
    date_column: Optional[str] = None  # Which column to filter by date

    # Original SQL from hypothesis
    original_sql: Optional[str] = None

    # Tracking modifications
    modifications: List[Dict[str, Any]] = field(default_factory=list)

    def to_summary(self) -> str:
        """
        Return human-readable summary of defaults.

        Example: "10 results, sorted by reputation DESC, filtered by tags='bug'"
        """
        parts = [f"{self.limit} results"]

        if self.sort_column:
            parts.append(f"sorted by {self.sort_column} {self.sort_direction}")

        if self.filters:
            filter_descs = [f.description for f in self.filters]
            parts.append(f"filtered by {', '.join(filter_descs)}")

        if self.date_range and self.date_range != "all time":
            parts.append(f"from {self.date_range}")

        return ", ".join(parts)

    def add_modification(self, description: str, field: str, old_value: Any, new_value: Any):
        """Track a modification to the defaults."""
        self.modifications.append({
            'description': description,
            'field': field,
            'old_value': old_value,
            'new_value': new_value
        })

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for session storage."""
        return {
            'limit': self.limit,
            'sort_column': self.sort_column,
            'sort_direction': self.sort_direction,
            'filters': [
                {
                    'column': f.column,
                    'operator': f.operator,
                    'value': f.value,
                    'description': f.description
                }
                for f in self.filters
            ],
            'date_range': self.date_range,
            'date_column': self.date_column,
            'original_sql': self.original_sql,
            'modifications': self.modifications,
            'summary': self.to_summary()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> QueryDefaults:
        """Deserialize from dictionary."""
        filters = [
            QueryFilter(
                column=f['column'],
                operator=f['operator'],
                value=f['value'],
                description=f['description']
            )
            for f in data.get('filters', [])
        ]

        return cls(
            limit=data.get('limit', 10),
            sort_column=data.get('sort_column'),
            sort_direction=data.get('sort_direction', 'DESC'),
            filters=filters,
            date_range=data.get('date_range'),
            date_column=data.get('date_column'),
            original_sql=data.get('original_sql'),
            modifications=data.get('modifications', [])
        )

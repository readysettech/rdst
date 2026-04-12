"""
Shared type definitions for the Ask3 engine.

These dataclasses provide typed structures for the linear flow,
replacing the untyped extra_data dict from the state machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Interpretation:
    """A possible interpretation of the user's question."""

    id: int
    description: str
    assumptions: List[str]
    sql_approach: str
    likelihood: float = 0.5  # 0.0-1.0, how likely this is what user wants

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            'id': self.id,
            'description': self.description,
            'assumptions': self.assumptions,
            'sql_approach': self.sql_approach,
            'likelihood': self.likelihood
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Interpretation:
        """Deserialize from dictionary."""
        return cls(
            id=data.get('id', 0),
            description=data.get('description', ''),
            assumptions=data.get('assumptions', []),
            sql_approach=data.get('sql_approach', ''),
            likelihood=data.get('likelihood', 0.5)
        )


@dataclass
class ValidationError:
    """SQL validation error with suggestions."""

    column: str
    table_alias: Optional[str]
    message: str
    suggestions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            'column': self.column,
            'table_alias': self.table_alias,
            'message': self.message,
            'suggestions': self.suggestions
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ValidationError:
        """Deserialize from dictionary."""
        return cls(
            column=data.get('column', ''),
            table_alias=data.get('table_alias'),
            message=data.get('message', ''),
            suggestions=data.get('suggestions', [])
        )


@dataclass
class SchemaExpansionRequest:
    """LLM request for additional schema information."""

    missing_concepts: List[str] = field(default_factory=list)
    requested_tables: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            'missing_concepts': self.missing_concepts,
            'requested_tables': self.requested_tables,
            'reason': self.reason
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SchemaExpansionRequest':
        """Deserialize from dictionary."""
        return cls(
            missing_concepts=data.get('missing_concepts', []),
            requested_tables=data.get('requested_tables', []),
            reason=data.get('reason', '')
        )


@dataclass
class SchemaInfo:
    """Schema information for a database target."""

    target: str
    db_type: str  # 'postgresql' | 'mysql'
    tables: Dict[str, TableInfo] = field(default_factory=dict)
    formatted_schema: str = ''
    source: str = 'semantic'  # 'semantic' | 'database'
    terminology: Dict[str, Any] = field(default_factory=dict)  # Business terminology

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            'target': self.target,
            'db_type': self.db_type,
            'tables': {k: v.to_dict() for k, v in self.tables.items()},
            'formatted_schema': self.formatted_schema,
            'source': self.source,
            'terminology': self.terminology
        }


@dataclass
class TableInfo:
    """Information about a database table."""

    name: str
    columns: Dict[str, ColumnInfo] = field(default_factory=dict)
    description: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            'name': self.name,
            'columns': {k: v.to_dict() for k, v in self.columns.items()},
            'description': self.description
        }


@dataclass
class ColumnInfo:
    """Information about a database column."""

    name: str
    data_type: str
    description: Optional[str] = None
    is_primary_key: bool = False
    is_foreign_key: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            'name': self.name,
            'data_type': self.data_type,
            'description': self.description,
            'is_primary_key': self.is_primary_key,
            'is_foreign_key': self.is_foreign_key
        }


@dataclass
class ExecutionResult:
    """Result of SQL query execution."""

    columns: List[str] = field(default_factory=list)
    rows: List[List[Any]] = field(default_factory=list)
    row_count: int = 0
    execution_time_ms: float = 0.0
    error: Optional[str] = None
    truncated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            'columns': self.columns,
            'rows': self.rows,
            'row_count': self.row_count,
            'execution_time_ms': self.execution_time_ms,
            'error': self.error,
            'truncated': self.truncated
        }


# Status constants
class Status:
    """Context status values."""
    PENDING = 'pending'
    SUCCESS = 'success'
    ERROR = 'error'
    CANCELLED = 'cancelled'


# Database type constants
class DbType:
    """Database type values."""
    POSTGRESQL = 'postgresql'
    MYSQL = 'mysql'


# Schema source constants
class SchemaSource:
    """Schema source values."""
    SEMANTIC = 'semantic'
    DATABASE = 'database'

"""
Shared type definitions for the Ask3 engine.

These dataclasses provide typed structures for the linear flow,
replacing the untyped extra_data dict from the state machine.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


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
            "id": self.id,
            "description": self.description,
            "assumptions": self.assumptions,
            "sql_approach": self.sql_approach,
            "likelihood": self.likelihood,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Interpretation:
        """Deserialize from dictionary."""
        return cls(
            id=data.get("id", 0),
            description=data.get("description", ""),
            assumptions=data.get("assumptions", []),
            sql_approach=data.get("sql_approach", ""),
            likelihood=data.get("likelihood", 0.5),
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
            "column": self.column,
            "table_alias": self.table_alias,
            "message": self.message,
            "suggestions": self.suggestions,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ValidationError:
        """Deserialize from dictionary."""
        return cls(
            column=data.get("column", ""),
            table_alias=data.get("table_alias"),
            message=data.get("message", ""),
            suggestions=data.get("suggestions", []),
        )


@dataclass
class SchemaInfo:
    """Schema information for a database target."""

    target: str
    db_type: str  # 'postgresql' | 'mysql'
    tables: Dict[str, TableInfo] = field(default_factory=dict)
    formatted_schema: str = ""
    source: str = "semantic"  # 'semantic' | 'database'
    terminology: Dict[str, Any] = field(default_factory=dict)  # Business terminology

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "target": self.target,
            "db_type": self.db_type,
            "tables": {k: v.to_dict() for k, v in self.tables.items()},
            "formatted_schema": self.formatted_schema,
            "source": self.source,
            "terminology": self.terminology,
        }


@dataclass
class TableInfo:
    """Information about a database table."""

    name: str
    columns: Dict[str, ColumnInfo] = field(default_factory=dict)
    description: Optional[str] = None
    business_context: Optional[str] = None
    relationships: List[Any] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "name": self.name,
            "columns": {k: v.to_dict() for k, v in self.columns.items()},
            "description": self.description,
            "business_context": self.business_context,
            "relationships": [
                relationship.to_dict()
                if hasattr(relationship, "to_dict")
                else relationship
                for relationship in self.relationships
            ],
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
            "name": self.name,
            "data_type": self.data_type,
            "description": self.description,
            "is_primary_key": self.is_primary_key,
            "is_foreign_key": self.is_foreign_key,
        }


@dataclass
class ExecutionResult:
    """Result of SQL query execution."""

    columns: List[str] = field(default_factory=list)
    rows: List[List[Any]] = field(default_factory=list)
    row_count: int = 0
    execution_time_ms: float = 0.0
    error: Optional[str] = None
    error_kind: Optional[str] = None
    truncated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "execution_time_ms": self.execution_time_ms,
            "error": self.error,
            "error_kind": self.error_kind,
            "truncated": self.truncated,
        }


class StatementCancel:
    """Database connections running statements for one Ask request.

    Executors register a connection for the duration of its statement. cancel() asks
    the server to stop every registered statement, abort() cuts the connections from
    the client side, and both refuse later registrations, so a stopped request starts
    no new statement.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live: Dict[int, Tuple[Any, str, Optional[str], Dict[str, Any], Any]] = {}
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def register(
        self,
        conn: Any,
        engine: str,
        target: Optional[str],
        target_config: Dict[str, Any],
    ) -> bool:
        """Track a connection about to run a statement; False once cancelled."""
        # MySQL's thread id is read now, while no statement holds the connection.
        try:
            server_id = conn.thread_id() if engine == DbType.MYSQL else None
        except Exception:
            server_id = None
        with self._lock:
            if self._cancelled:
                return False
            self._live[id(conn)] = (conn, engine, target, target_config, server_id)
            return True

    def unregister(self, conn: Any) -> None:
        with self._lock:
            self._live.pop(id(conn), None)

    def cancel(self) -> int:
        """Ask the server to stop every registered statement.

        PostgreSQL gets libpq's protocol cancel and MySQL a KILL QUERY on a side connection,
        each bounded in time, so callers bound the whole call with a thread and fall back to
        abort(). Returns how many statements the server agreed to stop.
        """
        from shared.db_connection import PostgresCancelRequest, cancel_mysql_by_thread_id

        with self._lock:
            self._cancelled = True
            # Requests are built under the lock, which keeps executors from closing a connection.
            live = [
                (engine, target, target_config, server_id,
                 None if engine == DbType.MYSQL else PostgresCancelRequest.for_connection(conn))
                for conn, engine, target, target_config, server_id in self._live.values()
            ]
        sent = 0
        for engine, target, target_config, server_id, request in live:
            try:
                if engine == DbType.MYSQL and server_id is not None:
                    sent += bool(cancel_mysql_by_thread_id(target_config, server_id, target=target))
                elif request is not None:
                    sent += request.send()
            except Exception:
                logger.debug("Failed to cancel Ask statement", exc_info=True)
        return sent

    def abort(self) -> None:
        """Shut down every registered connection's socket; never waits on the server.

        A statement blocked on a server that stopped answering then fails promptly.
        """
        from shared.db_connection import abort_connection

        with self._lock:
            self._cancelled = True
            # Held so no executor closes a connection, freeing its descriptor, mid-abort.
            for conn, *_rest in self._live.values():
                abort_connection(conn)

    def stop(self, wait_seconds: float) -> None:
        """Cancel server-side for up to wait_seconds, then abort the connections still live."""
        worker = threading.Thread(target=self.cancel, name="ask-statement-cancel", daemon=True)
        worker.start()
        worker.join(wait_seconds)
        self.abort()


# Status constants
class Status:
    """Context status values."""

    PENDING = "pending"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


# Database type constants
class DbType:
    """Database type values."""

    POSTGRESQL = "postgresql"
    MYSQL = "mysql"


# Schema source constants
class SchemaSource:
    """Schema source values."""

    SEMANTIC = "semantic"
    DATABASE = "database"

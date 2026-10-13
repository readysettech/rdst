"""Ask feature events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Union

from .models import AskClarificationQuestion, AskInterpretation, AskPhase, AskResult


@dataclass
class AskStatusEvent:
    """Status update during ask execution."""

    type: Literal["status"]
    phase: AskPhase | str
    message: str


@dataclass
class AskSchemaLoadedEvent:
    """Schema has been loaded."""

    type: Literal["schema_loaded"]
    source: str
    table_count: int
    tables: list[str]
    target: str = ""


@dataclass
class AskClarificationNeededEvent:
    """Clarification needed from the user."""

    type: Literal["clarification_needed"]
    session_id: str
    interpretations: list[AskInterpretation]
    questions: list[AskClarificationQuestion]


@dataclass
class AskSqlGeneratedEvent:
    """SQL has been generated."""

    type: Literal["sql_generated"]
    sql: str
    explanation: Optional[str] = None


@dataclass
class AskResultEvent:
    """Ask completed with results."""

    type: Literal["result"]
    success: bool
    sql: str
    rows: list
    columns: list[str]
    row_count: int
    execution_time_ms: float
    llm_calls: int
    total_tokens: int
    query_hash: str = ""
    query_tag: str = ""
    # True when validation injected a LIMIT the user's query did not have, so
    # the UI can say the executed SQL was bounded (T15 explicit BE warning).
    limit_added: bool = False
    # True when validation lowered the query's own LIMIT to the Ask maximum.
    limit_reduced: bool = False
    # True when an Ask-imposed LIMIT cut rows the query would otherwise return.
    truncated: bool = False
    # Effective LIMIT of the executed SQL, if it has one.
    row_limit: Optional[int] = None
    # Database-page query key, set only when monitoring has recorded this statement on the
    # target, so the page has something to show.
    monitor_key: Optional[str] = None

    @classmethod
    def from_result(cls, result: AskResult) -> "AskResultEvent":
        return cls(
            type="result",
            success=result.success,
            sql=result.sql,
            rows=result.rows,
            columns=result.columns,
            row_count=result.row_count,
            execution_time_ms=result.execution_time_ms,
            llm_calls=result.llm_calls,
            total_tokens=result.total_tokens,
            query_hash=result.query_hash,
            query_tag=result.query_tag,
            limit_added=result.limit_added,
            limit_reduced=result.limit_reduced,
            truncated=result.truncated,
            row_limit=result.row_limit,
            monitor_key=result.monitor_key,
        )


@dataclass
class AskErrorEvent:
    """Ask encountered an error."""

    type: Literal["error"]
    message: str
    phase: Optional[AskPhase | str] = None
    code: Optional[str] = None
    category: Optional[str] = None
    target: Optional[str] = None


AskEvent = Union[
    AskStatusEvent,
    AskSchemaLoadedEvent,
    AskClarificationNeededEvent,
    AskSqlGeneratedEvent,
    AskResultEvent,
    AskErrorEvent,
]

"""Top feature events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Union

from .models import TopQueryData


@dataclass
class TopStatusEvent:
    """Progress status update."""

    type: Literal["status"]
    message: str


@dataclass
class TopConnectedEvent:
    """Database connection established."""

    type: Literal["connected"]
    target_name: str
    db_engine: str
    source: str


@dataclass
class TopSourceFallbackEvent:
    """Source fallback occurred."""

    type: Literal["source_fallback"]
    from_source: str
    to_source: str
    reason: str


@dataclass
class TopDbLimitWarningEvent:
    """Database query size limit is below recommended threshold."""

    type: Literal["db_limit_warning"]
    db_limit_bytes: int
    recommended_bytes: int
    setting_name: str
    db_engine: str


@dataclass
class TopQueriesEvent:
    """Batch of top queries."""

    type: Literal["queries"]
    queries: list[TopQueryData]
    source: str
    target_name: str
    db_engine: str
    runtime_seconds: Optional[float] = None
    total_tracked: Optional[int] = None


@dataclass
class TopQuerySavedEvent:
    """Query saved to registry."""

    type: Literal["query_saved"]
    query_hash: str
    is_new: bool


@dataclass
class TopCompleteEvent:
    """Operation completed."""

    type: Literal["complete"]
    success: bool
    queries: list[TopQueryData]
    source: str
    newly_saved: int


@dataclass
class TopErrorEvent:
    """Error occurred.

    ``code`` and ``detail`` mirror the shared error envelope (B7/T24):
    ``message`` stays humane, ``detail`` carries the exception class name for
    correlation — never the raw ``str(e)``, which can embed host/DSN material.
    """

    type: Literal["error"]
    message: str
    code: Optional[str] = None
    detail: Optional[str] = None
    stage: Optional[str] = None


TopEvent = Union[
    TopStatusEvent,
    TopConnectedEvent,
    TopSourceFallbackEvent,
    TopDbLimitWarningEvent,
    TopQueriesEvent,
    TopQuerySavedEvent,
    TopCompleteEvent,
    TopErrorEvent,
]

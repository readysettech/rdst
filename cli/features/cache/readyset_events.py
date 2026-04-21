from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Literal, Optional, Union


@dataclass
class ReadysetProgressEvent:
    type: Literal["progress"]
    stage: str
    message: str


@dataclass
class ReadysetErrorEvent:
    type: Literal["error"]
    message: str


@dataclass
class ReadysetSetupCompleteEvent:
    type: Literal["complete"]
    success: bool
    readyset_port: Optional[int] = None
    test_db_port: Optional[int] = None
    already_running: bool = False


@dataclass
class ReadysetExplainCompleteEvent:
    type: Literal["complete"]
    success: bool
    cacheable: bool
    confidence: str
    explanation: str
    issues: list[str]
    readyset_port: Optional[int] = None


@dataclass
class ReadysetCacheCompleteEvent:
    type: Literal["complete"]
    success: bool
    cached: bool
    message: str
    cache_id: Optional[str] = None
    error: Optional[str] = None
    readyset_port: Optional[int] = None


ReadysetSetupEvent = Union[
    ReadysetProgressEvent,
    ReadysetErrorEvent,
    ReadysetSetupCompleteEvent,
]

ReadysetExplainEvent = Union[
    ReadysetProgressEvent,
    ReadysetErrorEvent,
    ReadysetExplainCompleteEvent,
]

ReadysetCacheEvent = Union[
    ReadysetProgressEvent,
    ReadysetErrorEvent,
    ReadysetCacheCompleteEvent,
]


def event_to_sse(event: Any) -> dict:
    return {"event": event.type, "data": json.dumps(asdict(event))}

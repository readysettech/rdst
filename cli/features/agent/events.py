"""Agent chat service contracts.

Typed events streamed over SSE while a chat message is processed. They
mirror the ChatAgent on_event callback protocol (llm_call, thinking,
tool_call, tool_result) plus terminal response/complete/error events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union


@dataclass
class ChatStatusEvent:
    type: Literal["status"]
    phase: str
    message: str
    iteration: int | None = None


@dataclass
class ChatThinkingEvent:
    type: Literal["thinking"]
    text: str


@dataclass
class ChatToolCallEvent:
    type: Literal["tool_call"]
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatToolResultEvent:
    """Result of one tool execution.

    For query_database, `data` carries sql/columns/rows/row_count/
    execution_time_ms/truncated/query_hash/query_tag; for get_schema it
    carries tables/source.
    """

    type: Literal["tool_result"]
    success: bool
    content: str
    data: dict[str, Any] | None = None


@dataclass
class ChatResponseEvent:
    type: Literal["response"]
    text: str


@dataclass
class ChatCompleteEvent:
    type: Literal["complete"]
    success: bool
    tool_result_count: int = 0


@dataclass
class ChatErrorEvent:
    type: Literal["error"]
    message: str


ChatEvent = Union[
    ChatStatusEvent,
    ChatThinkingEvent,
    ChatToolCallEvent,
    ChatToolResultEvent,
    ChatResponseEvent,
    ChatCompleteEvent,
    ChatErrorEvent,
]

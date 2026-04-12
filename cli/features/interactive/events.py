"""Streaming events for interactive analysis conversations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass
class ChunkEvent:
    type: str
    text: str


@dataclass
class MessageEvent:
    type: str
    text: str


@dataclass
class InteractiveCompleteEvent:
    type: str
    conversation_id: str


@dataclass
class InteractiveErrorEvent:
    type: str
    error: str


InteractiveEvent = Union[ChunkEvent, MessageEvent, InteractiveCompleteEvent, InteractiveErrorEvent]

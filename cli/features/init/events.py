"""Streaming events for the init workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Union

from .models import InitStatus, InitValidationResult


@dataclass
class InitStatusEvent:
    type: str
    message: str


@dataclass
class InitTargetValidationEvent:
    type: str
    name: str
    success: bool
    error: str | None = None


@dataclass
class InitLlmValidationEvent:
    type: str
    result: dict[str, Any]


@dataclass
class InitCompleteEvent:
    type: str
    success: bool
    status: InitStatus | None = None
    validation: InitValidationResult | None = None


@dataclass
class InitErrorEvent:
    type: str
    message: str


InitEvent = Union[
    InitStatusEvent,
    InitTargetValidationEvent,
    InitLlmValidationEvent,
    InitCompleteEvent,
    InitErrorEvent,
]

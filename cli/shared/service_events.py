"""Shared service event dataclasses used across feature slices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional


@dataclass
class ProgressEvent:
    """Progress update during a multi-step operation."""

    type: Literal["progress"]
    stage: str
    percent: int
    message: str


@dataclass
class ErrorEvent:
    """Error event for service workflows."""

    type: Literal["error"]
    message: str
    stage: Optional[str] = None
    partial_results: Optional[dict[str, Any]] = None


__all__ = ["ProgressEvent", "ErrorEvent"]

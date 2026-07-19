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
    """Error event for service workflows.

    ``code`` and ``detail`` mirror the shared HTTP error envelope
    (``shared.api.app._error_envelope``) so an SSE failure carries the same
    {code, message, detail} shape the client normalizes in ``lib/sse.ts``.
    Both stay optional so existing producers that only set ``message`` keep
    working; the client derives a code when one is absent.
    """

    type: Literal["error"]
    message: str
    code: Optional[str] = None
    detail: Optional[Any] = None
    stage: Optional[str] = None
    partial_results: Optional[dict[str, Any]] = None


__all__ = ["ProgressEvent", "ErrorEvent"]

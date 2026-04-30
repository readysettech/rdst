"""Telemetry adapter for analyze: terminal-event detection.

Lives in its own module so `features/analyze/events.py` doesn't have to
import `shared.telemetry_manager` — keeps event dataclasses
telemetry-agnostic. The detector is consumed by
`telemetry.command_run("analyze", terminal_detector=analyze_terminal_detector, ...)`.
"""

from __future__ import annotations

from typing import Any, Optional

from shared.service_events import ErrorEvent
from shared.telemetry_manager import TerminalState

from .events import CompleteEvent


def analyze_terminal_detector(event: Any) -> Optional[TerminalState]:
    """Strict terminal-event detector for analyze SSE streams.

    Recognizes `CompleteEvent` (terminal success/failure) and `ErrorEvent`
    (terminal failure with stage). Other events are non-terminal.
    """
    if isinstance(event, CompleteEvent):
        return TerminalState(
            success=bool(event.success),
            error_type=None if event.success else "complete_unsuccessful",
            extra={"query_hash": event.query_hash} if event.query_hash else {},
        )
    if isinstance(event, ErrorEvent):
        return TerminalState(success=False, error_type=event.stage or "error")
    return None


__all__ = ["analyze_terminal_detector"]

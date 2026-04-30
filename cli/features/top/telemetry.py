"""Telemetry adapter for top: terminal-event detection."""

from __future__ import annotations

from typing import Any, Optional

from shared.telemetry_manager import TerminalState

from .events import TopCompleteEvent, TopErrorEvent


def top_terminal_detector(event: Any) -> Optional[TerminalState]:
    """Strict terminal-event detector for top SSE streams.

    Note: realtime streams typically end via client disconnect without a
    `TopCompleteEvent`; the route handler defaults `success=True` in that
    case before exiting the CM.
    """
    if isinstance(event, TopCompleteEvent):
        return TerminalState(
            success=bool(event.success),
            error_type=None if event.success else "complete_unsuccessful",
            extra={"queries_found": len(event.queries), "newly_saved": event.newly_saved},
        )
    if isinstance(event, TopErrorEvent):
        return TerminalState(success=False, error_type=event.stage or "error")
    return None


__all__ = ["top_terminal_detector"]

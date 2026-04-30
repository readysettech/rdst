"""Telemetry adapter for ask: terminal-event detection."""

from __future__ import annotations

from typing import Any, Optional

from shared.telemetry_manager import TerminalState

from .events import AskErrorEvent, AskResultEvent


def ask_terminal_detector(event: Any) -> Optional[TerminalState]:
    """Strict terminal-event detector for ask SSE streams.

    `AskResultEvent` is the success/failure terminal; `AskErrorEvent` is
    the error terminal. `AskClarificationNeededEvent` is intentionally
    non-terminal — the run continues via `AskService.resume()`.
    """
    if isinstance(event, AskResultEvent):
        extra: dict = {}
        if event.query_hash:
            extra["query_hash"] = event.query_hash
        return TerminalState(
            success=bool(event.success),
            error_type=None if event.success else "result_unsuccessful",
            extra=extra,
        )
    if isinstance(event, AskErrorEvent):
        return TerminalState(
            success=False,
            error_type=str(event.phase) if event.phase else "error",
        )
    return None


__all__ = ["ask_terminal_detector"]

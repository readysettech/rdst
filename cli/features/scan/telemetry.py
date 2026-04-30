"""Telemetry adapter for scan: terminal-event detection."""

from __future__ import annotations

from typing import Any, Optional

from shared.telemetry_manager import TerminalState

from .events import ScanCompleteEvent, ScanErrorEvent


def scan_terminal_detector(event: Any) -> Optional[TerminalState]:
    """Strict terminal-event detector for scan SSE streams."""
    if isinstance(event, ScanCompleteEvent):
        extra: dict = {}
        if isinstance(event.summary, dict):
            for key in ("total_queries", "files_scanned"):
                if key in event.summary:
                    extra[key] = event.summary[key]
        return TerminalState(
            success=bool(event.success),
            error_type=None if event.success else "complete_unsuccessful",
            extra=extra,
        )
    if isinstance(event, ScanErrorEvent):
        return TerminalState(
            success=False,
            error_type=str(event.phase) if event.phase else "error",
        )
    return None


__all__ = ["scan_terminal_detector"]

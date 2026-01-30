"""Renderer for query service events."""

from __future__ import annotations

from lib.services.types import (
    QueryCompleteEvent,
    QueryErrorEvent,
    QueryEvent,
    QueryStatusEvent,
)
from lib.ui import MessagePanel, get_console


class QueryRenderer:
    """Render query service events for CLI output."""

    def __init__(self):
        self._console = get_console()

    def render(self, event: QueryEvent) -> None:
        if isinstance(event, QueryStatusEvent):
            # QueryCommand already renders user-facing output. Suppress service status
            # lines to preserve CLI parity.
            return
        elif isinstance(event, QueryCompleteEvent):
            # QueryCommand owns detailed UX output; renderer only handles failures.
            if not event.success:
                message = event.result.get("message") or "Query command failed"
                self._console.print(MessagePanel(message, variant="error"))
        elif isinstance(event, QueryErrorEvent):
            self._console.print(
                MessagePanel(event.message, variant="error", title="Query Error")
            )

    def cleanup(self) -> None:
        return None

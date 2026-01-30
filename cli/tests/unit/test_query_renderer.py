"""Unit tests for QueryRenderer."""

from unittest.mock import Mock

from lib.cli.query_renderer import QueryRenderer
from lib.services.types import QueryCompleteEvent, QueryErrorEvent, QueryStatusEvent


def test_render_status_event_suppressed():
    """QueryRenderer deliberately suppresses status events (QueryCommand owns output)."""
    renderer = QueryRenderer()
    renderer._console = Mock()

    renderer.render(QueryStatusEvent(type="status", message="Running"))

    renderer._console.print.assert_not_called()


def test_render_complete_failure_prints_error_panel():
    renderer = QueryRenderer()
    renderer._console = Mock()

    renderer.render(
        QueryCompleteEvent(
            type="complete",
            success=False,
            result={"message": "failed", "ok": False, "data": {}},
        )
    )

    renderer._console.print.assert_called_once()


def test_render_error_event_prints():
    renderer = QueryRenderer()
    renderer._console = Mock()

    renderer.render(QueryErrorEvent(type="error", message="boom"))

    renderer._console.print.assert_called_once()

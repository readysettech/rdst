"""Unit tests for SchemaRenderer."""

from unittest.mock import Mock

from lib.cli.schema_renderer import SchemaRenderer
from lib.services.types import (
    SchemaCompleteEvent,
    SchemaDetails,
    SchemaErrorEvent,
    SchemaInitResult,
    SchemaStatusEvent,
)


def test_render_status_event_suppressed():
    """SchemaRenderer deliberately suppresses status events for CLI parity."""
    renderer = SchemaRenderer()
    renderer._console = Mock()

    renderer.render(
        SchemaStatusEvent(type="status", operation="init", message="starting")
    )

    renderer._console.print.assert_not_called()


def test_render_init_complete_prints_summary():
    renderer = SchemaRenderer()
    renderer._console = Mock()

    renderer.render(
        SchemaCompleteEvent(
            type="complete",
            operation="init",
            success=True,
            init_result=SchemaInitResult(
                success=True,
                target="prod",
                tables=1,
                columns=2,
                relationships=0,
                enum_columns=[],
                path="/tmp/schema.yaml",
            ),
        )
    )

    assert renderer._console.print.call_count >= 2


def test_render_error_event_prints():
    renderer = SchemaRenderer()
    renderer._console = Mock()

    renderer.render(SchemaErrorEvent(type="error", operation="show", message="boom"))

    renderer._console.print.assert_called_once()


def test_render_show_complete_prints_schema_details():
    """Show complete renders Rule header, schema details, and closing Rule."""
    renderer = SchemaRenderer()
    renderer._console = Mock()

    renderer.render(
        SchemaCompleteEvent(
            type="complete",
            operation="show",
            success=True,
            details=SchemaDetails(
                target="prod",
                tables=[],
                terminology=[],
                extensions=[],
                custom_types=[],
                metrics=[],
            ),
        )
    )

    # _render_schema_details prints: blank line, Rule header, blank line, closing Rule
    assert renderer._console.print.call_count >= 2

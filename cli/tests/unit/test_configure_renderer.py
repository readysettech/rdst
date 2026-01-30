"""
Unit tests for ConfigureRenderer.

Tests the terminal rendering of configure events including target lists,
connection tests, and success/error messages.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import Any, Dict, List

# Import from lib package (conftest.py adds rdst root to path)
from lib.services.types import (
    ConfigureStatusEvent,
    ConfigureTargetListEvent,
    ConfigureTargetDetailEvent,
    ConfigureConnectionTestEvent,
    ConfigureSuccessEvent,
    ConfigureErrorEvent,
    ConfigureInputNeededEvent,
)
from lib.cli.configure_renderer import ConfigureRenderer


class TestConfigureRendererInit:
    """Tests for ConfigureRenderer initialization."""

    def test_initialization(self):
        """Test renderer initializes correctly."""
        renderer = ConfigureRenderer()
        assert renderer is not None
        assert renderer._console is not None

    def test_has_required_methods(self):
        """Test renderer has required methods."""
        renderer = ConfigureRenderer()
        assert hasattr(renderer, "render")
        assert hasattr(renderer, "cleanup")


class TestConfigureRendererRenderEvents:
    """Tests for render() method with various event types."""

    @pytest.fixture
    def renderer(self):
        """Create ConfigureRenderer instance with mocked console."""
        renderer = ConfigureRenderer()
        renderer._console = Mock()
        return renderer

    def test_render_status_event(self, renderer):
        """Test rendering ConfigureStatusEvent."""
        event = ConfigureStatusEvent(type="status", message="Loading targets...")

        renderer.render(event)

        renderer._console.print.assert_called_once()
        call_args = renderer._console.print.call_args[0][0]
        assert "Loading targets" in call_args

    def test_render_target_list_event_empty(self, renderer):
        """Test rendering empty target list."""
        event = ConfigureTargetListEvent(
            type="target_list",
            targets=[],
            default_target=None,
        )

        renderer.render(event)

        renderer._console.print.assert_called_once()

    def test_render_target_list_event_with_targets(self, renderer):
        """Test rendering target list with targets."""
        event = ConfigureTargetListEvent(
            type="target_list",
            targets=[
                {
                    "name": "prod",
                    "engine": "postgresql",
                    "has_password": True,
                    "is_default": True,
                },
                {
                    "name": "staging",
                    "engine": "mysql",
                    "has_password": False,
                    "is_default": False,
                },
            ],
            default_target="prod",
        )

        renderer.render(event)

        renderer._console.print.assert_called_once()

    def test_render_target_detail_event(self, renderer):
        """Test rendering ConfigureTargetDetailEvent."""
        event = ConfigureTargetDetailEvent(
            type="target_detail",
            target_name="prod",
            engine="postgresql",
            host="localhost",
            port=5432,
            database="mydb",
            user="myuser",
            has_password=True,
            is_default=True,
            tls=False,
        )

        renderer.render(event)

        renderer._console.print.assert_called_once()

    def test_render_connection_test_in_progress(self, renderer):
        """Test rendering connection test in progress."""
        event = ConfigureConnectionTestEvent(
            type="connection_test",
            target_name="prod",
            status="in_progress",
            message=None,
            server_version=None,
        )

        renderer.render(event)

        renderer._console.print.assert_called_once()
        call_args = renderer._console.print.call_args[0][0]
        assert "Testing connection" in call_args

    def test_render_connection_test_success(self, renderer):
        """Test rendering successful connection test."""
        event = ConfigureConnectionTestEvent(
            type="connection_test",
            target_name="prod",
            status="success",
            message="Connected successfully",
            server_version="PostgreSQL 15.2",
        )

        renderer.render(event)

        renderer._console.print.assert_called_once()

    def test_render_connection_test_failed(self, renderer):
        """Test rendering failed connection test."""
        event = ConfigureConnectionTestEvent(
            type="connection_test",
            target_name="prod",
            status="failed",
            message="Connection refused",
            server_version=None,
        )

        renderer.render(event)

        renderer._console.print.assert_called_once()

    def test_render_success_event(self, renderer):
        """Test rendering ConfigureSuccessEvent."""
        event = ConfigureSuccessEvent(
            type="success",
            operation="add",
            target_name="new-target",
            message="Target 'new-target' added successfully",
        )

        renderer.render(event)

        renderer._console.print.assert_called_once()

    def test_render_success_event_different_operations(self, renderer):
        """Test rendering success events for different operations."""
        operations = ["add", "edit", "remove", "test", "list", "default"]

        for op in operations:
            renderer._console.reset_mock()
            event = ConfigureSuccessEvent(
                type="success",
                operation=op,
                target_name="target",
                message=f"Operation {op} succeeded",
            )

            renderer.render(event)

            renderer._console.print.assert_called_once()

    def test_render_error_event(self, renderer):
        """Test rendering ConfigureErrorEvent."""
        event = ConfigureErrorEvent(
            type="error",
            message="Connection failed",
            operation="test",
            target_name="prod",
        )

        renderer.render(event)

        renderer._console.print.assert_called_once()

    def test_render_error_event_different_operations(self, renderer):
        """Test rendering error events for different operations."""
        operations = ["add", "edit", "remove", "test", "list", "default"]

        for op in operations:
            renderer._console.reset_mock()
            event = ConfigureErrorEvent(
                type="error",
                message=f"Failed during {op}",
                operation=op,
                target_name="target",
            )

            renderer.render(event)

            renderer._console.print.assert_called_once()

    def test_render_input_needed_event(self, renderer):
        """Test rendering ConfigureInputNeededEvent."""
        event = ConfigureInputNeededEvent(
            type="input_needed",
            prompt="Enter database host:",
            field_name="host",
            field_type="text",
            choices=None,
            default="localhost",
        )

        renderer.render(event)

        renderer._console.print.assert_called_once()


class TestConfigureRendererCleanup:
    """Tests for cleanup() method."""

    def test_cleanup_no_op(self):
        """Test cleanup is a no-op (no active displays)."""
        renderer = ConfigureRenderer()

        # Should not raise any exception
        renderer.cleanup()


class TestConfigureRendererEventTypes:
    """Tests for configure event type structures."""

    def test_configure_status_event_structure(self):
        """Test ConfigureStatusEvent has correct structure."""
        event = ConfigureStatusEvent(type="status", message="Loading...")
        assert event.type == "status"
        assert event.message == "Loading..."

    def test_configure_target_list_event_structure(self):
        """Test ConfigureTargetListEvent has correct structure."""
        event = ConfigureTargetListEvent(
            type="target_list",
            targets=[{"name": "prod", "engine": "postgresql"}],
            default_target="prod",
        )
        assert event.type == "target_list"
        assert len(event.targets) == 1
        assert event.default_target == "prod"

    def test_configure_target_detail_event_structure(self):
        """Test ConfigureTargetDetailEvent has correct structure."""
        event = ConfigureTargetDetailEvent(
            type="target_detail",
            target_name="prod",
            engine="postgresql",
            host="localhost",
            port=5432,
            database="mydb",
            user="myuser",
            has_password=True,
            is_default=True,
            tls=True,
        )
        assert event.type == "target_detail"
        assert event.target_name == "prod"
        assert event.port == 5432
        assert event.tls is True

    def test_configure_connection_test_event_structure(self):
        """Test ConfigureConnectionTestEvent has correct structure."""
        event = ConfigureConnectionTestEvent(
            type="connection_test",
            target_name="prod",
            status="success",
            message="Connected",
            server_version="PostgreSQL 15.2",
        )
        assert event.type == "connection_test"
        assert event.status == "success"
        assert event.server_version == "PostgreSQL 15.2"

    def test_configure_success_event_structure(self):
        """Test ConfigureSuccessEvent has correct structure."""
        event = ConfigureSuccessEvent(
            type="success",
            operation="add",
            target_name="new-target",
            message="Added successfully",
        )
        assert event.type == "success"
        assert event.operation == "add"
        assert event.target_name == "new-target"

    def test_configure_error_event_structure(self):
        """Test ConfigureErrorEvent has correct structure."""
        event = ConfigureErrorEvent(
            type="error",
            message="Connection refused",
            operation="test",
            target_name="prod",
        )
        assert event.type == "error"
        assert event.message == "Connection refused"
        assert event.operation == "test"

    def test_configure_input_needed_event_structure(self):
        """Test ConfigureInputNeededEvent has correct structure."""
        event = ConfigureInputNeededEvent(
            type="input_needed",
            prompt="Select engine:",
            field_name="engine",
            field_type="choice",
            choices=["postgresql", "mysql"],
            default="postgresql",
        )
        assert event.type == "input_needed"
        assert event.field_type == "choice"
        assert len(event.choices) == 2

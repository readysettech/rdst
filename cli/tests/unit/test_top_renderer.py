"""
Unit tests for TopRenderer.

Tests the terminal rendering of top query events including both
snapshot and realtime modes.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import Any, Dict, List

# Import from lib package (conftest.py adds rdst root to path)
from lib.services.types import (
    TopStatusEvent,
    TopConnectedEvent,
    TopSourceFallbackEvent,
    TopQueriesEvent,
    TopQueryData,
    TopQuerySavedEvent,
    TopCompleteEvent,
    TopErrorEvent,
)
from lib.cli.top_renderer import TopRenderer, render_top_queries_json


class TestTopRendererInit:
    """Tests for TopRenderer initialization."""

    def test_initialization_defaults(self):
        """Test renderer initializes with defaults."""
        renderer = TopRenderer()
        assert renderer._verbose is False
        assert renderer._no_color is False
        assert renderer._realtime is False
        assert renderer._live_started is False

    def test_initialization_with_options(self):
        """Test renderer initializes with custom options."""
        renderer = TopRenderer(verbose=True, no_color=True, realtime=True)
        assert renderer._verbose is True
        assert renderer._no_color is True
        assert renderer._realtime is True


class TestTopRendererRenderEvents:
    """Tests for render() method with various event types."""

    @pytest.fixture
    def renderer(self):
        """Create TopRenderer instance with mocked console."""
        renderer = TopRenderer()
        renderer._console = Mock()
        return renderer

    def test_render_status_event(self, renderer):
        """Test rendering TopStatusEvent."""
        event = TopStatusEvent(type="status", message="Loading configuration...")

        renderer.render(event)

        renderer._console.print.assert_called_once()

    def test_render_connected_event(self, renderer):
        """Test rendering TopConnectedEvent."""
        event = TopConnectedEvent(
            type="connected",
            target_name="prod",
            db_engine="postgresql",
            source="pg_stat",
        )

        renderer.render(event)

        # Should update internal state
        assert renderer._target_name == "prod"
        assert renderer._db_engine == "postgresql"
        assert renderer._source == "pg_stat"

    def test_render_fallback_event_pg_stat_to_activity(self, renderer):
        """Test rendering pg_stat to activity fallback."""
        event = TopSourceFallbackEvent(
            type="source_fallback",
            from_source="pg_stat",
            to_source="activity",
            reason="pg_stat_statements not available",
        )

        renderer.render(event)

        # Should print notice panel
        renderer._console.print.assert_called()

    def test_render_fallback_event_generic(self, renderer):
        """Test rendering generic source fallback."""
        event = TopSourceFallbackEvent(
            type="source_fallback",
            from_source="digest",
            to_source="activity",
            reason="performance_schema not available",
        )

        renderer.render(event)

        renderer._console.print.assert_called()

    def test_render_queries_event_snapshot(self, renderer):
        """Test rendering TopQueriesEvent in snapshot mode."""
        queries = [
            TopQueryData(
                query_hash="abc123",
                query_text="SELECT * FROM users",
                normalized_query="SELECT * FROM users",
                freq=100,
                total_time="1.234s",
                avg_time="0.012s",
                pct_load="5.0%",
            )
        ]
        event = TopQueriesEvent(
            type="queries",
            queries=queries,
            source="pg_stat",
            target_name="prod",
            db_engine="postgresql",
        )

        renderer.render(event)

        # Should print table
        renderer._console.print.assert_called()

    def test_render_queries_event_empty(self, renderer):
        """Test rendering empty queries list."""
        event = TopQueriesEvent(
            type="queries",
            queries=[],
            source="pg_stat",
            target_name="prod",
            db_engine="postgresql",
        )

        renderer.render(event)

        # Should print empty state
        renderer._console.print.assert_called()

    def test_render_query_saved_event_verbose(self, renderer):
        """Test rendering TopQuerySavedEvent in verbose mode."""
        renderer._verbose = True
        event = TopQuerySavedEvent(
            type="query_saved",
            query_hash="abc123",
            is_new=True,
        )

        renderer.render(event)

        # In verbose mode, should print message
        # Note: in realtime mode this is suppressed
        assert renderer._auto_saved_count == 1

    def test_render_complete_event(self, renderer):
        """Test rendering TopCompleteEvent."""
        event = TopCompleteEvent(
            type="complete",
            success=True,
            queries=[],
            source="pg_stat",
            newly_saved=5,
        )

        renderer.render(event)

        # Should print success message if newly_saved > 0
        renderer._console.print.assert_called()

    def test_render_complete_event_no_saves(self, renderer):
        """Test rendering TopCompleteEvent with no saves."""
        event = TopCompleteEvent(
            type="complete",
            success=True,
            queries=[],
            source="pg_stat",
            newly_saved=0,
        )

        renderer.render(event)

        # Should not print anything
        assert renderer._console.print.call_count == 0

    def test_render_error_event(self, renderer):
        """Test rendering TopErrorEvent."""
        event = TopErrorEvent(
            type="error",
            message="Connection failed",
            stage="config",
        )

        renderer.render(event)

        # Should print error message
        renderer._console.print.assert_called()


class TestTopRendererRealtimeMode:
    """Tests for realtime mode functionality."""

    @pytest.fixture
    def realtime_renderer(self):
        """Create TopRenderer in realtime mode with mocked console."""
        renderer = TopRenderer(realtime=True)
        renderer._console = Mock()
        return renderer

    def test_start_live(self, realtime_renderer):
        """Test starting live display sets flag and clears screen."""
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True

            realtime_renderer.start_live()

            assert realtime_renderer._live_started is True
            # Should write ANSI clear screen + home cursor
            mock_stdout.write.assert_called()

    def test_start_live_not_tty(self, realtime_renderer):
        """Test starting live display on non-tty just sets flag."""
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False

            realtime_renderer.start_live()

            assert realtime_renderer._live_started is True
            mock_stdout.write.assert_not_called()

    def test_stop_live(self, realtime_renderer):
        """Test stopping live display clears flag."""
        realtime_renderer._live_started = True

        realtime_renderer.stop_live()

        assert realtime_renderer._live_started is False

    def test_cleanup(self, realtime_renderer):
        """Test cleanup stops live and restores terminal."""
        realtime_renderer._live_started = True

        with patch.object(realtime_renderer, "_restore_terminal"):
            realtime_renderer.cleanup()

        assert realtime_renderer._live_started is False

    def test_get_current_queries(self, realtime_renderer):
        """Test getting current queries."""
        test_queries = [
            TopQueryData(
                query_hash="abc123",
                query_text="SELECT 1",
                normalized_query="SELECT 1",
                freq=10,
                total_time="1s",
                avg_time="0.1s",
                pct_load="1%",
            )
        ]
        realtime_renderer._current_queries = test_queries

        result = realtime_renderer.get_current_queries()

        assert result == test_queries


class TestTopRendererBuildDisplay:
    """Tests for _build_display() method."""

    @pytest.fixture
    def realtime_renderer(self):
        """Create TopRenderer in realtime mode."""
        renderer = TopRenderer(realtime=True)
        renderer._console = Mock()
        renderer._target_name = "prod"
        renderer._db_engine = "postgresql"
        renderer._source = "activity"
        renderer._runtime_seconds = 10.5
        renderer._total_tracked = 25
        return renderer

    def test_build_display_empty_queries(self, realtime_renderer):
        """Test building display with no queries."""
        realtime_renderer._current_queries = []

        display = realtime_renderer._build_display()

        assert display is not None

    def test_build_display_with_queries(self, realtime_renderer):
        """Test building display with queries."""
        realtime_renderer._current_queries = [
            TopQueryData(
                query_hash="abc123",
                query_text="SELECT * FROM users WHERE id = $1",
                normalized_query="SELECT * FROM users WHERE id = ?",
                freq=100,
                total_time="1.234s",
                avg_time="0.012s",
                pct_load="5.0%",
                max_duration_ms=1234.5,
                current_instances=2,
                observation_count=100,
            )
        ]

        display = realtime_renderer._build_display()

        assert display is not None


class TestRenderTopQueriesJson:
    """Tests for render_top_queries_json function."""

    def test_basic_json_output(self):
        """Test basic JSON output structure."""
        queries = [
            TopQueryData(
                query_hash="abc123",
                query_text="SELECT 1",
                normalized_query="SELECT 1",
                freq=10,
                total_time="1s",
                avg_time="0.1s",
                pct_load="1%",
            )
        ]

        result = render_top_queries_json(
            queries=queries,
            target_name="prod",
            db_engine="postgresql",
            source="pg_stat",
        )

        assert result["target"] == "prod"
        assert result["engine"] == "postgresql"
        assert result["source"] == "pg_stat"
        assert len(result["queries"]) == 1
        assert result["queries"][0]["query_hash"] == "abc123"
        assert result["queries"][0]["avg_duration_ms"] == 100.0
        assert result["queries"][0]["max_duration_ms"] == 1000.0
        assert result["queries"][0]["current_instances_running"] == 0
        assert result["queries"][0]["observation_count"] == 10

    def test_json_output_with_realtime_fields(self):
        """Test JSON output includes realtime-specific fields."""
        queries = [
            TopQueryData(
                query_hash="abc123",
                query_text="SELECT 1",
                normalized_query="SELECT 1",
                freq=10,
                total_time="1s",
                avg_time="0.1s",
                pct_load="1%",
                max_duration_ms=1500.0,
                current_instances=3,
                observation_count=50,
            )
        ]

        result = render_top_queries_json(
            queries=queries,
            target_name="prod",
            db_engine="postgresql",
            source="activity",
            runtime_seconds=30.5,
            total_tracked=100,
        )

        assert result["runtime_seconds"] == 30.5
        assert result["total_queries_tracked"] == 100
        assert result["queries"][0]["max_duration_ms"] == 1500.0
        assert result["queries"][0]["avg_duration_ms"] == 100.0
        assert result["queries"][0]["current_instances_running"] == 3
        assert result["queries"][0]["observation_count"] == 50

    def test_json_output_empty_queries(self):
        """Test JSON output with empty queries list."""
        result = render_top_queries_json(
            queries=[],
            target_name="prod",
            db_engine="mysql",
            source="digest",
        )

        assert result["target"] == "prod"
        assert result["queries"] == []


class TestTopRendererRestoreTerminal:
    """Tests for _restore_terminal() method."""

    def test_restore_terminal_no_errors(self):
        """Test restore_terminal doesn't raise errors."""
        renderer = TopRenderer()

        # Should not raise any exception
        renderer._restore_terminal()

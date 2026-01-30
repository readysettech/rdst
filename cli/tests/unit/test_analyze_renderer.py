"""
Unit tests for AnalyzeRenderer.

Tests the terminal rendering of analyze events including progress spinner,
status lines, and completion messages.
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock
from typing import Any, Dict, List

# Import from lib package (conftest.py adds rdst root to path)
from lib.services.types import (
    ProgressEvent,
    ExplainCompleteEvent,
    RewritesTestedEvent,
    ReadysetCheckedEvent,
    CompleteEvent,
    ErrorEvent,
)
from lib.cli.analyze_renderer import AnalyzeRenderer, QuietRenderer


class TestAnalyzeRendererInit:
    """Tests for AnalyzeRenderer initialization."""

    def test_initialization_default(self):
        """Test renderer initializes with defaults."""
        renderer = AnalyzeRenderer()
        assert renderer.verbose is False
        assert renderer._current_status is None
        assert renderer._step_start_time is None

    def test_initialization_verbose(self):
        """Test renderer initializes with verbose flag."""
        renderer = AnalyzeRenderer(verbose=True)
        assert renderer.verbose is True

    def test_has_required_methods(self):
        """Test renderer has required methods."""
        renderer = AnalyzeRenderer()
        assert hasattr(renderer, "render")
        assert hasattr(renderer, "cleanup")


class TestAnalyzeRendererRenderEvents:
    """Tests for render() method with various event types."""

    @pytest.fixture
    def renderer(self):
        """Create AnalyzeRenderer instance with mocked console."""
        renderer = AnalyzeRenderer()
        renderer._console = Mock()
        return renderer

    def test_render_progress_event_starts_spinner(self, renderer):
        """Test rendering ProgressEvent starts spinner."""
        event = ProgressEvent(
            type="progress",
            stage="validating",
            percent=5,
            message="Validating query safety...",
        )

        with patch("lib.cli.analyze_renderer.Status") as mock_status_class:
            mock_status = Mock()
            mock_status_class.return_value = mock_status

            renderer.render(event)

            mock_status_class.assert_called_once()
            mock_status.start.assert_called_once()
            assert renderer._current_status is mock_status

    def test_render_progress_event_updates_spinner(self, renderer):
        """Test rendering ProgressEvent updates existing spinner."""
        # First, start the spinner
        mock_status = Mock()
        renderer._current_status = mock_status
        renderer._step_start_time = time.time() - 2  # 2 seconds ago

        event = ProgressEvent(
            type="progress",
            stage="executing",
            percent=50,
            message="Executing EXPLAIN ANALYZE...",
        )

        renderer.render(event)

        mock_status.update.assert_called()

    def test_progress_message_shows_elapsed_seconds(self, renderer):
        """Test progress message includes elapsed seconds while running."""
        event = ProgressEvent(
            type="progress",
            stage="executing_explain",
            percent=20,
            message="Running EXPLAIN ANALYZE...",
        )

        with patch("lib.cli.analyze_renderer.Status") as mock_status_class:
            with patch("lib.cli.analyze_renderer.time.monotonic", return_value=100.0):
                renderer.render(event)

            status_message = mock_status_class.call_args[0][0]
            with patch("lib.cli.analyze_renderer.time.monotonic", return_value=115.0):
                assert status_message.__rich__() == "Running EXPLAIN ANALYZE... (15s)"

    def test_progress_timer_resets_on_stage_change(self, renderer):
        """Test elapsed timer resets when progress stage changes."""
        mock_status = Mock()
        renderer._current_status = mock_status
        renderer._step_start_time = 10.0
        renderer._current_stage = "validating"
        renderer._current_message = "Validating query safety..."

        event = ProgressEvent(
            type="progress",
            stage="executing_explain",
            percent=20,
            message="Running EXPLAIN ANALYZE...",
        )

        with patch("lib.cli.analyze_renderer.time.monotonic", return_value=80.0):
            renderer.render(event)

        status_message = mock_status.update.call_args[0][0]
        with patch("lib.cli.analyze_renderer.time.monotonic", return_value=81.0):
            assert status_message.__rich__() == "Running EXPLAIN ANALYZE... (1s)"

    def test_render_progress_event_100_percent_stops_spinner(self, renderer):
        """Test rendering 100% progress event stops spinner."""
        mock_status = Mock()
        renderer._current_status = mock_status
        renderer._step_start_time = time.time()

        event = ProgressEvent(
            type="progress",
            stage="complete",
            percent=100,
            message="Analysis complete",
        )

        renderer.render(event)

        mock_status.stop.assert_called_once()
        assert renderer._current_status is None

    def test_render_explain_complete_event(self, renderer):
        """Test rendering ExplainCompleteEvent."""
        mock_status = Mock()
        renderer._current_status = mock_status
        renderer._step_start_time = time.time()

        event = ExplainCompleteEvent(
            type="explain_complete",
            success=True,
            database_engine="postgresql",
            execution_time_ms=15.5,
            rows_examined=1000,
            rows_returned=10,
            cost_estimate=25.0,
            explain_plan={"type": "Seq Scan"},
        )

        renderer.render(event)

        # Should stop spinner and print status line
        mock_status.stop.assert_called_once()
        renderer._console.print.assert_called_once()

    def test_render_explain_complete_event_failure(self, renderer):
        """Test rendering failed ExplainCompleteEvent."""
        event = ExplainCompleteEvent(
            type="explain_complete",
            success=False,
            database_engine="postgresql",
            execution_time_ms=0,
            rows_examined=0,
            rows_returned=0,
            cost_estimate=0,
            explain_plan=None,
        )

        renderer.render(event)

        # Should not print anything on failure
        renderer._console.print.assert_not_called()

    def test_render_rewrites_tested_event(self, renderer):
        """Test rendering RewritesTestedEvent."""
        mock_status = Mock()
        renderer._current_status = mock_status
        renderer._step_start_time = time.time()

        event = RewritesTestedEvent(
            type="rewrites_tested",
            tested=True,
            message="Tested 3 rewrites",
            original_performance={"time_ms": 100},
            rewrite_results=[{"sql": "SELECT...", "time_ms": 50}],
            best_rewrite={"sql": "SELECT...", "improvement": "50%"},
        )

        renderer.render(event)

        mock_status.stop.assert_called_once()
        renderer._console.print.assert_called_once()

    def test_render_rewrites_tested_event_not_tested(self, renderer):
        """Test rendering RewritesTestedEvent when not tested."""
        event = RewritesTestedEvent(
            type="rewrites_tested",
            tested=False,
            skipped_reason="No rewrites suggested",
        )

        renderer.render(event)

        # Should not print anything when not tested
        renderer._console.print.assert_not_called()

    def test_render_readyset_checked_event_cacheable(self, renderer):
        """Test rendering ReadysetCheckedEvent when cacheable."""
        mock_status = Mock()
        renderer._current_status = mock_status
        renderer._step_start_time = time.time()

        event = ReadysetCheckedEvent(
            type="readyset_checked",
            checked=True,
            cacheable=True,
            confidence="high",
            method="readyset_container",
            explanation="Query is fully cacheable",
        )

        renderer.render(event)

        mock_status.stop.assert_called_once()
        renderer._console.print.assert_called_once()

    def test_render_readyset_checked_event_not_cacheable(self, renderer):
        """Test rendering ReadysetCheckedEvent when not cacheable."""
        mock_status = Mock()
        renderer._current_status = mock_status
        renderer._step_start_time = time.time()

        event = ReadysetCheckedEvent(
            type="readyset_checked",
            checked=True,
            cacheable=False,
            confidence="high",
            method="readyset_container",
            explanation="Query uses unsupported function",
        )

        renderer.render(event)

        renderer._console.print.assert_called_once()

    def test_render_readyset_checked_event_not_checked(self, renderer):
        """Test rendering ReadysetCheckedEvent when not checked."""
        event = ReadysetCheckedEvent(
            type="readyset_checked",
            checked=False,
        )

        renderer.render(event)

        # Should not print anything when not checked
        renderer._console.print.assert_not_called()

    def test_render_complete_event(self, renderer):
        """Test rendering CompleteEvent."""
        mock_status = Mock()
        renderer._current_status = mock_status
        renderer._step_start_time = time.time()

        event = CompleteEvent(
            type="complete",
            success=True,
            analysis_id="analysis_123",
            query_hash="hash_456",
        )

        renderer.render(event)

        mock_status.stop.assert_called_once()

    def test_render_error_event(self, renderer):
        """Test rendering ErrorEvent."""
        mock_status = Mock()
        renderer._current_status = mock_status
        renderer._step_start_time = time.time()

        event = ErrorEvent(
            type="error",
            message="Connection failed",
            stage="execution",
        )

        renderer.render(event)

        # Should stop spinner (actual error display handled by CLI command)
        mock_status.stop.assert_called_once()


class TestAnalyzeRendererCleanup:
    """Tests for cleanup() method."""

    def test_cleanup_stops_spinner(self):
        """Test cleanup stops active spinner."""
        renderer = AnalyzeRenderer()
        mock_status = Mock()
        renderer._current_status = mock_status
        renderer._step_start_time = time.time()

        renderer.cleanup()

        mock_status.stop.assert_called_once()
        assert renderer._current_status is None
        assert renderer._step_start_time is None

    def test_cleanup_no_active_spinner(self):
        """Test cleanup when no active spinner."""
        renderer = AnalyzeRenderer()

        # Should not raise any exception
        renderer.cleanup()


class TestQuietRenderer:
    """Tests for QuietRenderer class."""

    def test_initialization(self):
        """Test QuietRenderer initializes correctly."""
        renderer = QuietRenderer()
        assert renderer.verbose is False

    def test_render_only_cleans_up_on_complete(self):
        """Test QuietRenderer only cleans up on complete/error."""
        renderer = QuietRenderer()
        mock_status = Mock()
        renderer._current_status = mock_status
        renderer._step_start_time = time.time()

        # Progress event should not trigger cleanup
        progress_event = ProgressEvent(
            type="progress",
            stage="executing",
            percent=50,
            message="Working...",
        )
        renderer.render(progress_event)

        # Status should still be active
        mock_status.stop.assert_not_called()

        # Complete event should trigger cleanup
        complete_event = CompleteEvent(
            type="complete",
            success=True,
        )
        renderer.render(complete_event)

        mock_status.stop.assert_called_once()

    def test_render_error_triggers_cleanup(self):
        """Test QuietRenderer cleans up on error."""
        renderer = QuietRenderer()
        mock_status = Mock()
        renderer._current_status = mock_status

        error_event = ErrorEvent(
            type="error",
            message="Something failed",
        )
        renderer.render(error_event)

        mock_status.stop.assert_called_once()


class TestAnalyzeRendererEventTypes:
    """Tests for analyze event type structures."""

    def test_progress_event_structure(self):
        """Test ProgressEvent has correct structure."""
        event = ProgressEvent(
            type="progress",
            stage="validating",
            percent=10,
            message="Validating query...",
        )
        assert event.type == "progress"
        assert event.stage == "validating"
        assert event.percent == 10
        assert event.message == "Validating query..."

    def test_explain_complete_event_structure(self):
        """Test ExplainCompleteEvent has correct structure."""
        event = ExplainCompleteEvent(
            type="explain_complete",
            success=True,
            database_engine="postgresql",
            execution_time_ms=15.5,
            rows_examined=1000,
            rows_returned=10,
            cost_estimate=25.0,
            explain_plan={"type": "Index Scan"},
        )
        assert event.type == "explain_complete"
        assert event.execution_time_ms == 15.5
        assert event.rows_examined == 1000

    def test_complete_event_structure(self):
        """Test CompleteEvent has correct structure."""
        event = CompleteEvent(
            type="complete",
            success=True,
            analysis_id="analysis_123",
            query_hash="hash_456",
            explain_results={"success": True},
            llm_analysis={"recommendations": []},
        )
        assert event.type == "complete"
        assert event.success is True
        assert event.analysis_id == "analysis_123"

    def test_error_event_structure(self):
        """Test ErrorEvent has correct structure."""
        event = ErrorEvent(
            type="error",
            message="Database connection failed",
            stage="execution",
            partial_results={"explain": "partial"},
        )
        assert event.type == "error"
        assert event.message == "Database connection failed"
        assert event.stage == "execution"

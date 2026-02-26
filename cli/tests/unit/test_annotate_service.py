"""
Unit tests for AnnotateService.

Tests the LLM-powered schema annotation streaming service including
event yielding, progress tracking, and error scenarios.
"""

import pytest
import os
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from typing import Any, Dict, List

# Import from lib package (conftest.py adds rdst root to path)
from lib.services.types import (
    AnnotateStartedEvent,
    AnnotateProgressEvent,
    AnnotateTableCompleteEvent,
    AnnotateCompleteEvent,
    AnnotateErrorEvent,
)
from lib.services.annotate_service import AnnotateService


class TestAnnotateServiceInit:
    """Tests for AnnotateService initialization."""

    def test_initialization(self):
        """Test service initializes correctly."""
        service = AnnotateService()
        assert service is not None

    def test_has_required_methods(self):
        """Test service has required methods."""
        service = AnnotateService()
        assert hasattr(service, "annotate")


class TestAnnotateServiceAnnotate:
    """Tests for annotate() method."""

    @pytest.fixture
    def service(self):
        """Create AnnotateService instance."""
        return AnnotateService()

    @pytest.fixture
    def target_config(self):
        """Create test target config."""
        return {
            "engine": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "testdb",
            "user": "testuser",
            "password": "secret",
        }

    @pytest.mark.asyncio
    async def test_error_when_no_api_key(self, service, target_config):
        """Test error when ANTHROPIC_API_KEY is not set."""
        events = []

        # Clear ANTHROPIC_API_KEY
        with patch.dict(os.environ, {}, clear=True):
            async for event in service.annotate("test-target", target_config):
                events.append(event)

        assert len(events) == 1
        assert isinstance(events[0], AnnotateErrorEvent)
        assert "ANTHROPIC_API_KEY" in events[0].message

    @pytest.mark.asyncio
    async def test_error_when_schema_not_exists(self, service, target_config):
        """Test error when semantic layer doesn't exist."""
        events = []

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            # Patch at the actual import location (lazy import inside annotate())
            with patch(
                "lib.semantic_layer.manager.SemanticLayerManager"
            ) as MockManager:
                MockManager.return_value.exists.return_value = False

                async for event in service.annotate("nonexistent", target_config):
                    events.append(event)

        assert len(events) == 1
        assert isinstance(events[0], AnnotateErrorEvent)
        assert "No semantic layer found" in events[0].message

    @pytest.mark.asyncio
    async def test_accepts_rdst_trial_token(self, service, target_config):
        """Test RDST_TRIAL_TOKEN is accepted when ANTHROPIC_API_KEY is absent."""
        events = []

        with patch.dict(os.environ, {"RDST_TRIAL_TOKEN": "test-token"}, clear=True):
            with patch(
                "lib.semantic_layer.manager.SemanticLayerManager"
            ) as MockManager:
                MockManager.return_value.exists.return_value = False

                async for event in service.annotate("nonexistent", target_config):
                    events.append(event)

        assert len(events) == 1
        assert isinstance(events[0], AnnotateErrorEvent)
        assert "No semantic layer found" in events[0].message

    @pytest.mark.asyncio
    async def test_yields_started_event(self, service, target_config):
        """Test that annotate() yields AnnotateStartedEvent."""
        events = []

        # Mock layer with tables
        mock_layer = Mock()
        mock_layer.tables = {"users": Mock(), "orders": Mock()}

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch(
                "lib.semantic_layer.manager.SemanticLayerManager"
            ) as MockManager:
                MockManager.return_value.exists.return_value = True
                MockManager.return_value.load.return_value = mock_layer

                with patch(
                    "lib.semantic_layer.ai_annotator.AIAnnotator"
                ) as MockAnnotator:
                    # Mock the annotator to do nothing
                    mock_ai = Mock()
                    mock_ai.generate_table_annotations.return_value = (
                        "desc",
                        "context",
                        {},
                    )
                    MockAnnotator.return_value = mock_ai

                    async for event in service.annotate("test-target", target_config):
                        events.append(event)
                        if isinstance(event, AnnotateStartedEvent):
                            break

        assert len(events) >= 1
        assert isinstance(events[0], AnnotateStartedEvent)
        assert events[0].tables == 2


class TestAnnotateServiceEventTypes:
    """Tests for annotate service event types and dataclasses."""

    def test_annotate_started_event_structure(self):
        """Test AnnotateStartedEvent dataclass."""
        event = AnnotateStartedEvent(
            type="annotate_started",
            tables=5,
            message="Starting annotation...",
        )

        assert event.type == "annotate_started"
        assert event.tables == 5
        assert event.message == "Starting annotation..."

    def test_annotate_progress_event_structure(self):
        """Test AnnotateProgressEvent dataclass."""
        event = AnnotateProgressEvent(
            type="annotate_progress",
            table="users",
            table_index=1,
            total_tables=5,
            message="Annotating users...",
        )

        assert event.type == "annotate_progress"
        assert event.table == "users"
        assert event.table_index == 1
        assert event.total_tables == 5

    def test_annotate_table_complete_event_structure(self):
        """Test AnnotateTableCompleteEvent dataclass."""
        event = AnnotateTableCompleteEvent(
            type="annotate_table_complete",
            table="users",
            table_index=1,
            total_tables=5,
            columns_annotated=5,
        )

        assert event.type == "annotate_table_complete"
        assert event.table == "users"
        assert event.columns_annotated == 5

    def test_annotate_complete_event_structure(self):
        """Test AnnotateCompleteEvent dataclass."""
        event = AnnotateCompleteEvent(
            type="annotate_complete",
            success=True,
            tables_annotated=5,
            columns_annotated=25,
            message="Annotation complete",
        )

        assert event.type == "annotate_complete"
        assert event.success is True
        assert event.tables_annotated == 5
        assert event.columns_annotated == 25

    def test_annotate_error_event_structure(self):
        """Test AnnotateErrorEvent dataclass."""
        event = AnnotateErrorEvent(
            type="annotate_error",
            message="Something went wrong",
        )

        assert event.type == "annotate_error"
        assert event.message == "Something went wrong"

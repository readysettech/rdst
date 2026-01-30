"""
Unit tests for ConfigureService.

Tests the async generator-based configuration management service including
target listing, connection testing, and configuration operations.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from typing import Any, Dict, Optional

# Import from lib package (conftest.py adds rdst root to path)
from lib.services.types import (
    ConfigureInput,
    ConfigureOptions,
    ConfigureStatusEvent,
    ConfigureTargetListEvent,
    ConfigureTargetDetailEvent,
    ConfigureConnectionTestEvent,
    ConfigureSuccessEvent,
    ConfigureErrorEvent,
    ConfigureInputNeededEvent,
)


class TestConfigureServiceInit:
    """Tests for ConfigureService initialization."""

    def test_initialization(self):
        """Test service initializes correctly.

        Verifies that ConfigureService can be instantiated without errors.
        This test will fail until ConfigureService is implemented.
        """
        from lib.services.configure_service import ConfigureService

        service = ConfigureService()
        assert service is not None


class TestConfigureServiceListTargets:
    """Tests for list_targets() method."""

    @pytest.fixture
    def service(self):
        """Create ConfigureService instance.

        Provides a fresh service instance for each test.
        """
        from lib.services.configure_service import ConfigureService

        return ConfigureService()

    @pytest.fixture
    def input_data(self):
        """Create test input data for list operation.

        Returns ConfigureInput with no target specified (for list operation).
        """
        return ConfigureInput(target_name=None)

    @pytest.fixture
    def options(self):
        """Create test options for list operation.

        Returns ConfigureOptions configured for listing targets.
        """
        return ConfigureOptions(operation="list")

    @pytest.mark.asyncio
    async def test_list_targets_yields_events(self, service, input_data, options):
        """Test that list_targets() yields at least one event.

        Verifies that the async generator yields events when listing targets.
        This is the RED phase test - it will fail because ConfigureService
        doesn't exist yet. Once implemented, it should yield at least one
        event (status, target_list, or error).
        """
        events = []

        async for event in service.list_targets(input_data, options):
            events.append(event)

        # RED phase: This assertion will fail until list_targets is implemented
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_list_targets_yields_target_list_event(
        self, service, input_data, options
    ):
        """Test that list_targets() yields a ConfigureTargetListEvent.

        Verifies that the service yields a target list event containing
        the configured targets. This test expects at least one event of
        type ConfigureTargetListEvent.
        """
        events = []

        async for event in service.list_targets(input_data, options):
            events.append(event)

        # RED phase: This will fail until list_targets is implemented
        target_list_events = [
            e for e in events if isinstance(e, ConfigureTargetListEvent)
        ]
        assert len(target_list_events) >= 1

    @pytest.mark.asyncio
    async def test_list_targets_event_has_targets_field(
        self, service, input_data, options
    ):
        """Test that ConfigureTargetListEvent has targets field.

        Verifies that the target list event contains a targets field
        with the expected structure (list of dicts with target info).
        """
        events = []

        async for event in service.list_targets(input_data, options):
            events.append(event)

        # RED phase: This will fail until list_targets is implemented
        target_list_events = [
            e for e in events if isinstance(e, ConfigureTargetListEvent)
        ]
        assert len(target_list_events) > 0
        assert hasattr(target_list_events[0], "targets")
        assert isinstance(target_list_events[0].targets, list)


class TestConfigureServiceEventTypes:
    """Tests for event type structure and validation."""

    def test_configure_status_event_structure(self):
        """Test ConfigureStatusEvent has correct structure.

        Verifies that the status event dataclass has the expected fields
        and type discriminator.
        """
        event = ConfigureStatusEvent(type="status", message="Loading targets...")
        assert event.type == "status"
        assert event.message == "Loading targets..."

    def test_configure_target_list_event_structure(self):
        """Test ConfigureTargetListEvent has correct structure.

        Verifies that the target list event has targets and optional
        default_target fields.
        """
        event = ConfigureTargetListEvent(
            type="target_list",
            targets=[
                {
                    "name": "prod",
                    "engine": "postgresql",
                    "has_password": True,
                    "is_default": True,
                }
            ],
            default_target="prod",
        )
        assert event.type == "target_list"
        assert len(event.targets) == 1
        assert event.default_target == "prod"

    def test_configure_error_event_structure(self):
        """Test ConfigureErrorEvent has correct structure.

        Verifies that error events contain message and optional operation/target fields.
        """
        event = ConfigureErrorEvent(
            type="error",
            message="Connection failed",
            operation="test",
            target_name="prod",
        )
        assert event.type == "error"
        assert event.message == "Connection failed"
        assert event.operation == "test"
        assert event.target_name == "prod"

"""Unit tests for SchemaService event APIs."""

import pytest
from unittest.mock import Mock, patch

from lib.services.schema_service import SchemaService
from lib.services.types import (
    SchemaCompleteEvent,
    SchemaErrorEvent,
    SchemaInitOptions,
    SchemaInitResult,
    SchemaStatusEvent,
)


@pytest.fixture
def service():
    with patch("lib.services.schema_service.SemanticLayerManager"):
        return SchemaService()


@pytest.mark.asyncio
async def test_init_events_success(service):
    with patch.object(
        service,
        "init",
        return_value=SchemaInitResult(
            success=True,
            target="prod",
            tables=1,
            columns=2,
            relationships=0,
            enum_columns=[],
            path="/tmp/schema.yaml",
        ),
    ):
        events = [
            event
            async for event in service.init_events(
                "prod", {"engine": "postgresql"}, SchemaInitOptions()
            )
        ]

    assert isinstance(events[0], SchemaStatusEvent)
    assert isinstance(events[1], SchemaCompleteEvent)
    assert events[1].init_result is not None
    assert events[1].init_result.success is True


@pytest.mark.asyncio
async def test_init_events_error(service):
    with patch.object(
        service,
        "init",
        return_value=SchemaInitResult(
            success=False,
            target="prod",
            tables=0,
            columns=0,
            relationships=0,
            enum_columns=[],
            error="failed",
        ),
    ):
        events = [
            event
            async for event in service.init_events(
                "prod", {"engine": "postgresql"}, SchemaInitOptions()
            )
        ]

    assert isinstance(events[-1], SchemaErrorEvent)
    assert "failed" in events[-1].message


@pytest.mark.asyncio
async def test_list_targets_events(service):
    with patch.object(service, "list_targets") as mock_list:
        mock_list.return_value = Mock(targets=[])
        events = [event async for event in service.list_targets_events()]

    assert isinstance(events[0], SchemaStatusEvent)
    assert isinstance(events[1], SchemaCompleteEvent)
    assert events[1].operation == "list"

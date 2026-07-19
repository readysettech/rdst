"""
Unit tests for AnnotateService.

Tests the LLM-powered schema annotation streaming service including
event yielding, progress tracking, and error scenarios.
"""

import pytest
from unittest.mock import Mock, patch

from features.schema.events import (
    AnnotateStartedEvent,
    AnnotateProgressEvent,
    AnnotateTableCompleteEvent,
    AnnotateCompleteEvent,
    AnnotateErrorEvent,
)
from features.schema.annotate_service import AnnotateService


VALID = {"valid": True, "reason": "ok", "model": "claude-haiku-4-5"}

# Patch targets are the names bound inside the annotate_service module.
VALIDATE = "features.schema.annotate_service.validate_anthropic_key"
MANAGER = "features.schema.annotate_service.create_semantic_layer_manager"
ANNOTATOR = "features.schema.annotate_service.create_ai_annotator"


def _mock_col(description=None, data_type="text"):
    col = Mock()
    col.description = description
    col.data_type = data_type
    return col


def _mock_table(description=None, columns=None):
    table = Mock()
    table.description = description
    table.row_estimate = "100"
    table.columns = columns if columns is not None else {}
    return table


def _mock_layer(tables):
    layer = Mock()
    layer.tables = tables
    return layer


async def _collect(service, target, target_config, **kwargs):
    return [event async for event in service.annotate(target, target_config, **kwargs)]


class TestAnnotateServiceInit:
    """Tests for AnnotateService initialization."""

    def test_initialization(self):
        service = AnnotateService()
        assert service is not None

    def test_has_required_methods(self):
        service = AnnotateService()
        assert hasattr(service, "annotate")


class TestAnnotateServicePreflight:
    """The key must be validated (not merely present) before any march."""

    @pytest.fixture
    def service(self):
        return AnnotateService()

    @pytest.mark.asyncio
    async def test_error_when_no_api_key(self, service):
        with patch(VALIDATE, return_value={"valid": False, "reason": "no_key", "model": None}):
            events = await _collect(service, "t", {})
        assert len(events) == 1
        assert isinstance(events[0], AnnotateErrorEvent)
        assert "ANTHROPIC_API_KEY" in events[0].message

    @pytest.mark.asyncio
    async def test_rejected_key_fails_preflight_without_marching(self, service):
        # The bug's core case: a present-but-rejected key must fail up front,
        # not advance through tables and report a false success (rdst-0yy.11).
        with patch(VALIDATE, return_value={"valid": False, "reason": "rejected", "model": "m"}):
            with patch(MANAGER) as create_manager:
                events = await _collect(service, "t", {})
        assert len(events) == 1
        assert isinstance(events[0], AnnotateErrorEvent)
        assert "rejected" in events[0].message.lower()
        # Never even touched the semantic layer; no march began.
        create_manager.assert_not_called()

    @pytest.mark.asyncio
    async def test_provider_error_fails_preflight(self, service):
        with patch(VALIDATE, return_value={"valid": False, "reason": "provider_error", "model": "m"}):
            events = await _collect(service, "t", {})
        assert len(events) == 1
        assert isinstance(events[0], AnnotateErrorEvent)
        assert "reach anthropic" in events[0].message.lower()

    @pytest.mark.asyncio
    async def test_error_when_schema_not_exists(self, service):
        with patch(VALIDATE, return_value=VALID):
            with patch(MANAGER) as create_manager:
                create_manager.return_value.exists.return_value = False
                events = await _collect(service, "nonexistent", {})
        assert len(events) == 1
        assert isinstance(events[0], AnnotateErrorEvent)
        assert "No semantic layer found" in events[0].message


class TestAnnotateServiceMarch:
    """Behavior once the key is valid and the layer exists."""

    @pytest.fixture
    def service(self):
        return AnnotateService()

    @pytest.mark.asyncio
    async def test_yields_started_event(self, service):
        layer = _mock_layer({"users": _mock_table(), "orders": _mock_table()})
        with patch(VALIDATE, return_value=VALID), patch(MANAGER) as create_manager, patch(
            ANNOTATOR
        ) as create_annotator:
            create_manager.return_value.exists.return_value = True
            create_manager.return_value.load.return_value = layer
            ai = Mock()
            ai.generate_table_description.return_value = "desc"
            ai.generate_column_description.return_value = "desc"
            create_annotator.return_value = ai

            events = []
            async for event in service.annotate("t", {}):
                events.append(event)
                if isinstance(event, AnnotateStartedEvent):
                    break

        assert isinstance(events[0], AnnotateStartedEvent)
        assert events[0].tables == 2

    @pytest.mark.asyncio
    async def test_all_failures_report_error_not_success(self, service):
        # Valid at preflight, but every generate_* call fails mid-run. The run
        # annotated nothing, so it must end in an error, not a green complete.
        cols = {"id": _mock_col(), "name": _mock_col()}
        layer = _mock_layer({"users": _mock_table(columns=cols)})
        with patch(VALIDATE, return_value=VALID), patch(MANAGER) as create_manager, patch(
            ANNOTATOR
        ) as create_annotator:
            create_manager.return_value.exists.return_value = True
            create_manager.return_value.load.return_value = layer
            ai = Mock()
            ai.generate_table_description.return_value = "Error: 401 authentication_error"
            ai.generate_column_description.return_value = "Error: 401 authentication_error"
            create_annotator.return_value = ai

            events = await _collect(service, "t", {})

        assert not any(isinstance(e, AnnotateCompleteEvent) for e in events)
        assert isinstance(events[-1], AnnotateErrorEvent)
        assert "0 of 1" in events[-1].message

    @pytest.mark.asyncio
    async def test_thrown_exceptions_counted_as_failures(self, service):
        layer = _mock_layer({"users": _mock_table(columns={"id": _mock_col()})})
        with patch(VALIDATE, return_value=VALID), patch(MANAGER) as create_manager, patch(
            ANNOTATOR
        ) as create_annotator:
            create_manager.return_value.exists.return_value = True
            create_manager.return_value.load.return_value = layer
            ai = Mock()
            ai.generate_table_description.side_effect = RuntimeError("boom")
            ai.generate_column_description.side_effect = RuntimeError("boom")
            create_annotator.return_value = ai

            events = await _collect(service, "t", {})

        assert isinstance(events[-1], AnnotateErrorEvent)
        assert "Last error" in events[-1].message

    @pytest.mark.asyncio
    async def test_partial_success_completes_with_failure_count(self, service):
        layer = _mock_layer(
            {
                "good": _mock_table(columns={"id": _mock_col()}),
                "bad": _mock_table(columns={"id": _mock_col()}),
            }
        )

        def table_desc(tbl_name, *_args, **_kwargs):
            return "A good description" if tbl_name == "good" else "Error: rate_limit"

        def col_desc(tbl_name, *_args, **_kwargs):
            return "A good column description" if tbl_name == "good" else "Error: rate_limit"

        with patch(VALIDATE, return_value=VALID), patch(MANAGER) as create_manager, patch(
            ANNOTATOR
        ) as create_annotator:
            create_manager.return_value.exists.return_value = True
            create_manager.return_value.load.return_value = layer
            ai = Mock()
            ai.generate_table_description.side_effect = table_desc
            ai.generate_column_description.side_effect = col_desc
            create_annotator.return_value = ai

            events = await _collect(service, "t", {})

        complete = events[-1]
        assert isinstance(complete, AnnotateCompleteEvent)
        assert complete.success is True
        assert complete.tables_annotated == 1
        assert "failed" in complete.message

    @pytest.mark.asyncio
    async def test_noop_run_when_all_preannotated_completes(self, service):
        # Everything already documented: no LLM calls, no failures; an honest
        # zero-work complete, never a false error.
        cols = {"id": _mock_col(description="the id")}
        layer = _mock_layer({"users": _mock_table(description="users table", columns=cols)})
        with patch(VALIDATE, return_value=VALID), patch(MANAGER) as create_manager, patch(
            ANNOTATOR
        ) as create_annotator:
            create_manager.return_value.exists.return_value = True
            create_manager.return_value.load.return_value = layer
            ai = Mock()
            create_annotator.return_value = ai

            events = await _collect(service, "t", {})

        complete = events[-1]
        assert isinstance(complete, AnnotateCompleteEvent)
        assert complete.success is True
        ai.generate_table_description.assert_not_called()


class TestAnnotateServiceEventTypes:
    """Tests for annotate service event types and dataclasses."""

    def test_annotate_started_event_structure(self):
        event = AnnotateStartedEvent(
            type="annotate_started",
            tables=5,
            message="Starting annotation...",
        )
        assert event.type == "annotate_started"
        assert event.tables == 5
        assert event.message == "Starting annotation..."

    def test_annotate_progress_event_structure(self):
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
        event = AnnotateErrorEvent(
            type="annotate_error",
            message="Something went wrong",
        )
        assert event.type == "annotate_error"
        assert event.message == "Something went wrong"

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
from features.schema.semantic_models import ColumnAnnotation, TableAnnotation


VALID = {"valid": True, "reason": "ok", "model": "claude-haiku-4-5"}

# Patch targets are the names bound inside the annotate_service module.
VALIDATE = "features.schema.annotate_service.validate_anthropic_key"
MANAGER = "features.schema.annotate_service.create_semantic_layer_manager"
ANNOTATOR = "features.schema.annotate_service.create_ai_annotator"


def _mock_col(description=None, data_type="text"):
    return ColumnAnnotation(name="col", description=description or "", data_type=data_type)


def _mock_table(description=None, columns=None):
    return TableAnnotation(
        name="table",
        description=description or "",
        row_estimate="100",
        columns=columns if columns is not None else {},
    )


def _mock_layer(tables):
    layer = Mock()
    layer.tables = tables
    return layer


def _ok_result(table_desc="desc", col_names=(), col_desc="desc"):
    """A well-formed AIAnnotator.annotate_table result."""
    return {
        "description": table_desc,
        "business_context": "",
        "columns": {n: {"description": col_desc, "enum_mappings": {}} for n in col_names},
    }


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
            ai.annotate_table.return_value = _ok_result()
            create_annotator.return_value = ai

            events = []
            async for event in service.annotate("t", {}):
                events.append(event)
                if isinstance(event, AnnotateStartedEvent):
                    break

        assert isinstance(events[0], AnnotateStartedEvent)
        assert events[0].tables == 2
        assert events[0].completed_tables == 0

    @pytest.mark.asyncio
    async def test_all_failures_report_error_not_success(self, service):
        # Valid at preflight, but every annotate_table call fails mid-run. The
        # run annotated nothing, so it must end in an error, not a green
        # complete.
        cols = {"id": _mock_col(), "name": _mock_col()}
        layer = _mock_layer({"users": _mock_table(columns=cols)})
        with patch(VALIDATE, return_value=VALID), patch(MANAGER) as create_manager, patch(
            ANNOTATOR
        ) as create_annotator:
            create_manager.return_value.exists.return_value = True
            create_manager.return_value.load.return_value = layer
            ai = Mock()
            ai.annotate_table.side_effect = RuntimeError("401 authentication_error")
            create_annotator.return_value = ai

            events = await _collect(service, "t", {})

        assert not any(isinstance(e, AnnotateCompleteEvent) for e in events)
        assert isinstance(events[-1], AnnotateErrorEvent)
        assert "0 of 1" in events[-1].message
        assert "Last error" in events[-1].message

    @pytest.mark.asyncio
    async def test_partial_success_completes_with_failure_count(self, service):
        layer = _mock_layer(
            {
                "good": _mock_table(columns={"id": _mock_col()}),
                "bad": _mock_table(columns={"id": _mock_col()}),
            }
        )

        def annotate(tbl_name, *_args, **_kwargs):
            if tbl_name == "good":
                return _ok_result("A good description", col_names=("id",))
            raise RuntimeError("rate_limit")

        with patch(VALIDATE, return_value=VALID), patch(MANAGER) as create_manager, patch(
            ANNOTATOR
        ) as create_annotator:
            create_manager.return_value.exists.return_value = True
            create_manager.return_value.load.return_value = layer
            ai = Mock()
            ai.annotate_table.side_effect = annotate
            create_annotator.return_value = ai

            events = await _collect(service, "t", {})

        complete = events[-1]
        assert isinstance(complete, AnnotateCompleteEvent)
        assert complete.success is False
        assert complete.tables_annotated == 1
        assert complete.columns_annotated == 1
        assert complete.tables_failed == 1
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
        assert complete.message == "Schema is already fully annotated"
        started = events[0]
        assert isinstance(started, AnnotateStartedEvent)
        assert started.tables == 1
        assert started.completed_tables == 1
        ai.annotate_table.assert_not_called()

    @pytest.mark.asyncio
    async def test_rerun_derives_progress_from_saved_annotations(self, service):
        complete_table = _mock_table(
            description="already done",
            columns={"id": _mock_col(description="already done")},
        )
        pending_tables = {
            "orders": _mock_table(columns={"id": _mock_col()}),
            "products": _mock_table(columns={"id": _mock_col()}),
        }
        layer = _mock_layer({"users": complete_table, **pending_tables})

        with patch(VALIDATE, return_value=VALID), patch(MANAGER) as create_manager, patch(
            ANNOTATOR
        ) as create_annotator:
            create_manager.return_value.exists.return_value = True
            create_manager.return_value.load.return_value = layer
            ai = Mock()
            ai.annotate_table.return_value = _ok_result(col_names=("id",))
            create_annotator.return_value = ai

            events = await _collect(service, "t", {})

        started = events[0]
        assert isinstance(started, AnnotateStartedEvent)
        assert started.tables == 3
        assert started.completed_tables == 1
        assert "Resuming annotation: 1 of 3" in started.message
        progress = [e for e in events if isinstance(e, AnnotateProgressEvent)]
        assert [e.table_index for e in progress] == [2, 3]
        assert [e.total_tables for e in progress] == [3, 3]
        assert ai.annotate_table.call_count == 2


class TestAnnotateServiceBatching:
    """One batched LLM call per table, concurrent mini-batches, fill-if-empty."""

    async def _run(self, layer, ai):
        with patch(VALIDATE, return_value=VALID), patch(MANAGER) as create_manager, patch(
            ANNOTATOR
        ) as create_annotator:
            create_manager.return_value.exists.return_value = True
            create_manager.return_value.load.return_value = layer
            create_annotator.return_value = ai
            events = await _collect(AnnotateService(), "t", {})
            return events, create_manager.return_value

    @pytest.mark.asyncio
    async def test_one_call_per_table_never_per_column(self):
        cols_a = {"id": _mock_col(), "name": _mock_col(), "email": _mock_col()}
        cols_b = {"id": _mock_col(), "total": _mock_col()}
        layer = _mock_layer(
            {"users": _mock_table(columns=cols_a), "orders": _mock_table(columns=cols_b)}
        )
        ai = Mock()
        ai.annotate_table.side_effect = lambda name, *_a, **_k: _ok_result(
            col_names=("id", "name", "email", "total")
        )

        events, _ = await self._run(layer, ai)

        assert ai.annotate_table.call_count == 2
        ai.generate_table_description.assert_not_called()
        ai.generate_column_description.assert_not_called()
        complete = events[-1]
        assert isinstance(complete, AnnotateCompleteEvent)
        assert complete.tables_annotated == 2
        assert complete.columns_annotated == 5

    @pytest.mark.asyncio
    async def test_existing_descriptions_never_overwritten(self):
        described = _mock_col(description="keep me")
        blank = _mock_col()
        layer = _mock_layer(
            {"users": _mock_table(description=None, columns={"a": described, "b": blank})}
        )
        ai = Mock()
        ai.annotate_table.return_value = _ok_result(
            "new table desc", col_names=("a", "b"), col_desc="overwrite attempt"
        )

        events, _ = await self._run(layer, ai)

        assert described.description == "keep me"
        assert blank.description == "overwrite attempt"
        complete = events[-1]
        assert complete.tables_annotated == 1
        assert complete.columns_annotated == 1

    @pytest.mark.asyncio
    async def test_saves_once_per_concurrent_batch(self):
        # Five tables at a batch size of three: two saves, both incremental.
        tables = {
            f"t{i}": _mock_table(columns={"id": _mock_col()}) for i in range(5)
        }
        layer = _mock_layer(tables)
        ai = Mock()
        ai.annotate_table.return_value = _ok_result(col_names=("id",))

        events, manager = await self._run(layer, ai)

        assert manager.save.call_count == 2
        assert isinstance(events[-1], AnnotateCompleteEvent)

    @pytest.mark.asyncio
    async def test_todo_enum_values_filled_from_mappings(self):
        col = _mock_col()
        col.enum_values = {"A": "TODO: describe", "B": ""}
        layer = _mock_layer({"users": _mock_table(columns={"status": col})})
        result = _ok_result(col_names=("status",))
        result["columns"]["status"]["enum_mappings"] = {
            "A": "Active account",
            "B": "Banned account",
            "Z": "Never asked about",
        }
        ai = Mock()
        ai.annotate_table.return_value = result

        await self._run(layer, ai)

        assert col.enum_values == {"A": "Active account", "B": "Banned account"}

    @pytest.mark.asyncio
    async def test_partial_rerun_requests_only_pending_columns(self):
        # The incremental-save design means reruns hit partially annotated
        # tables; already-described columns must not be re-requested.
        done = _mock_col(description="already described")
        todo = _mock_col()
        layer = _mock_layer(
            {
                "users": _mock_table(
                    description="users table", columns={"done": done, "todo": todo}
                )
            }
        )
        ai = Mock()
        ai.annotate_table.return_value = _ok_result(col_names=("todo",))

        events, _ = await self._run(layer, ai)

        ai.annotate_table.assert_called_once()
        assert ai.annotate_table.call_args.kwargs["only_columns"] == ["todo"]
        assert events[-1].columns_annotated == 1

    @pytest.mark.asyncio
    async def test_described_table_with_todo_enums_not_skipped_and_saved(self):
        # Descriptions alone don't make a table complete: unfilled enum
        # placeholders still need annotation, and an enum-only fill must
        # reach the incremental save.
        col = _mock_col(description="status column")
        col.enum_values = {"A": "TODO: describe"}
        layer = _mock_layer(
            {"users": _mock_table(description="described", columns={"status": col})}
        )
        result = _ok_result(col_names=("status",))
        result["columns"]["status"]["enum_mappings"] = {"A": "Active"}
        ai = Mock()
        ai.annotate_table.return_value = result

        _events, manager = await self._run(layer, ai)

        ai.annotate_table.assert_called_once()
        assert col.enum_values == {"A": "Active"}
        manager.save.assert_called_once()


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
            tables_failed=0,
            message="Annotation complete",
        )
        assert event.type == "annotate_complete"
        assert event.success is True
        assert event.tables_annotated == 5
        assert event.columns_annotated == 25
        assert event.tables_failed == 0

    def test_annotate_error_event_structure(self):
        event = AnnotateErrorEvent(
            type="annotate_error",
            message="Something went wrong",
        )
        assert event.type == "annotate_error"
        assert event.message == "Something went wrong"

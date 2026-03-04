"""
Unit tests for AskService.

Tests the text-to-SQL streaming service including event yielding,
clarification handling, and error scenarios.
"""

import asyncio
import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from typing import Any, Dict, List

# Import from lib package (conftest.py adds rdst root to path)
from lib.services.types import (
    AskInput,
    AskOptions,
    AskStatusEvent,
    AskSchemaLoadedEvent,
    AskClarificationNeededEvent,
    AskSqlGeneratedEvent,
    AskResultEvent,
    AskErrorEvent,
    AskInterpretation,
    AskClarificationQuestion,
)
from lib.services.ask_service import AskService


class TestAskServiceInit:
    """Tests for AskService initialization."""

    def test_initialization(self):
        """Test service initializes correctly."""
        service = AskService()
        assert service is not None

    def test_has_required_methods(self):
        """Test service has required methods."""
        service = AskService()
        assert hasattr(service, "ask")
        assert hasattr(service, "resume")
        assert hasattr(service, "_load_config")


class TestAskServiceAsk:
    """Tests for ask() method."""

    @pytest.fixture
    def service(self):
        """Create AskService instance."""
        return AskService()

    @pytest.fixture
    def input_data(self):
        """Create test AskInput."""
        return AskInput(
            question="How many users signed up last month?",
            target="test-target",
            source="cli",
        )

    @pytest.fixture
    def options(self):
        """Create test AskOptions."""
        return AskOptions(
            dry_run=False,
            timeout_seconds=30,
            verbose=False,
            agent_mode=False,
            no_interactive=False,
        )

    @pytest.mark.asyncio
    async def test_yields_initial_status_event(self, service, input_data, options):
        """Test that ask() yields initial status event."""
        events = []

        # Mock _load_config to return None/None to trigger early error
        async def mock_load_config(target):
            return (None, None)

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            async for event in service.ask(input_data, options):
                events.append(event)

        # Should have at least the initial status event
        assert len(events) >= 1
        assert isinstance(events[0], AskStatusEvent)
        assert events[0].phase == "config"

    @pytest.mark.asyncio
    async def test_error_no_target_configured(self, service, options):
        """Test error when no target and no default."""
        events = []

        input_data = AskInput(
            question="Test?",
            target=None,  # No target specified
            source="cli",
        )

        async def mock_load_config(target):
            return (None, None)  # No default configured

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            async for event in service.ask(input_data, options):
                events.append(event)

        # Should have status event then error
        assert len(events) >= 2
        error_events = [e for e in events if isinstance(e, AskErrorEvent)]
        assert len(error_events) == 1
        assert "No target" in error_events[0].message

    @pytest.mark.asyncio
    async def test_error_target_not_found(self, service, options):
        """Test error when target doesn't exist."""
        events = []

        input_data = AskInput(
            question="Test?",
            target="nonexistent",
            source="cli",
        )

        async def mock_load_config(target):
            return ("nonexistent", None)  # Target name but no config

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            async for event in service.ask(input_data, options):
                events.append(event)

        error_events = [e for e in events if isinstance(e, AskErrorEvent)]
        assert len(error_events) == 1
        assert "not found" in error_events[0].message


class TestAskServiceEventTypes:
    """Tests for service event types and dataclasses."""

    def test_ask_status_event_structure(self):
        """Test AskStatusEvent dataclass."""
        event = AskStatusEvent(
            type="status",
            phase="schema",
            message="Loading schema...",
        )

        assert event.type == "status"
        assert event.phase == "schema"
        assert event.message == "Loading schema..."

    def test_ask_schema_loaded_event_structure(self):
        """Test AskSchemaLoadedEvent dataclass."""
        event = AskSchemaLoadedEvent(
            type="schema_loaded",
            source="semantic",
            table_count=10,
            tables=["users", "orders"],
        )

        assert event.type == "schema_loaded"
        assert event.source == "semantic"
        assert event.table_count == 10
        assert len(event.tables) == 2

    def test_ask_clarification_needed_event_structure(self):
        """Test AskClarificationNeededEvent dataclass."""
        event = AskClarificationNeededEvent(
            type="clarification_needed",
            session_id="abc123",
            questions=[
                AskClarificationQuestion(
                    id="time_range",
                    question="What time period?",
                    options=["Last 30 days", "Last month", "This month"],
                )
            ],
            interpretations=[],
        )

        assert event.type == "clarification_needed"
        assert event.session_id == "abc123"
        assert len(event.questions) == 1
        assert event.questions[0].id == "time_range"

    def test_ask_sql_generated_event_structure(self):
        """Test AskSqlGeneratedEvent dataclass."""
        event = AskSqlGeneratedEvent(
            type="sql_generated",
            sql="SELECT COUNT(*) FROM users",
            explanation="Counts all users",
        )

        assert event.type == "sql_generated"
        assert "SELECT" in event.sql
        assert event.explanation == "Counts all users"

    def test_ask_result_event_structure(self):
        """Test AskResultEvent dataclass."""
        event = AskResultEvent(
            type="result",
            success=True,
            sql="SELECT 1",
            columns=["count"],
            rows=[{"count": 42}],
            row_count=1,
            execution_time_ms=15.5,
            llm_calls=2,
            total_tokens=500,
        )

        assert event.type == "result"
        assert event.success is True
        assert event.row_count == 1
        assert event.execution_time_ms == 15.5
        assert event.llm_calls == 2

    def test_ask_error_event_structure(self):
        """Test AskErrorEvent dataclass."""
        event = AskErrorEvent(
            type="error",
            message="Something went wrong",
            phase="schema",
        )

        assert event.type == "error"
        assert event.message == "Something went wrong"
        assert event.phase == "schema"


class TestAskServiceInterpretation:
    """Tests for AskInterpretation dataclass."""

    def test_interpretation_fields(self):
        """Test AskInterpretation has all required fields."""
        interp = AskInterpretation(
            id=1,
            description="Count unique users",
            assumptions=["Using user_id as unique identifier"],
            likelihood=0.85,
        )

        assert interp.id == 1
        assert interp.description == "Count unique users"
        assert len(interp.assumptions) == 1
        assert interp.likelihood == 0.85


class TestAskServiceClarificationQuestion:
    """Tests for AskClarificationQuestion dataclass."""

    def test_clarification_question_fields(self):
        """Test AskClarificationQuestion has all required fields."""
        question = AskClarificationQuestion(
            id="aggregation",
            question="How should we count users?",
            options=["Total count", "Unique count"],
        )

        assert question.id == "aggregation"
        assert question.question == "How should we count users?"
        assert len(question.options) == 2


class TestAskServiceInputOptions:
    """Tests for AskInput and AskOptions dataclasses."""

    def test_ask_input_defaults(self):
        """Test AskInput with minimal params."""
        input_data = AskInput(
            question="Test question",
        )

        assert input_data.question == "Test question"
        assert input_data.target is None
        assert input_data.source == "cli"

    def test_ask_input_with_target(self):
        """Test AskInput with target."""
        input_data = AskInput(
            question="Test question",
            target="prod",
            source="web",
        )

        assert input_data.target == "prod"
        assert input_data.source == "web"

    def test_ask_options_defaults(self):
        """Test AskOptions has sensible defaults."""
        options = AskOptions()

        assert options.dry_run is False
        assert options.timeout_seconds == 30
        assert options.verbose is False

    def test_ask_options_custom(self):
        """Test AskOptions with custom values."""
        options = AskOptions(
            dry_run=True,
            timeout_seconds=60,
            verbose=True,
            no_interactive=True,
        )

        assert options.dry_run is True
        assert options.timeout_seconds == 60
        assert options.verbose is True
        assert options.no_interactive is True


class TestAskServiceSessionManagement:
    """Tests for session management in AskService."""

    def test_sessions_dict_exists(self):
        """Test that _sessions storage exists."""
        from lib.services.ask_service import _sessions

        assert isinstance(_sessions, dict)


class TestAskServiceLoadConfig:
    """Tests for _load_config() method."""

    @pytest.fixture
    def service(self):
        """Create AskService instance."""
        return AskService()

    @pytest.mark.asyncio
    async def test_load_config_with_target(self, service):
        """Test _load_config with explicit target."""
        mock_cfg = Mock()
        mock_cfg.get_default.return_value = "default-target"
        mock_cfg.get.return_value = {"engine": "postgresql", "host": "localhost"}

        with patch("lib.cli.rdst_cli.TargetsConfig", return_value=mock_cfg):
            target_name, config = await service._load_config("explicit-target")

        assert target_name == "explicit-target"
        # Config should be fetched for explicit target
        mock_cfg.get.assert_called_with("explicit-target")

    @pytest.mark.asyncio
    async def test_load_config_uses_default(self, service):
        """Test _load_config uses default when target is None."""
        mock_cfg = Mock()
        mock_cfg.get_default.return_value = "default-target"
        mock_cfg.get.return_value = {"engine": "postgresql"}

        with patch("lib.cli.rdst_cli.TargetsConfig", return_value=mock_cfg):
            target_name, config = await service._load_config(None)

        assert target_name == "default-target"

    @pytest.mark.asyncio
    async def test_load_config_no_default(self, service):
        """Test _load_config when no default configured."""
        mock_cfg = Mock()
        mock_cfg.get_default.return_value = None
        mock_cfg.get.return_value = None

        with patch("lib.cli.rdst_cli.TargetsConfig", return_value=mock_cfg):
            target_name, config = await service._load_config(None)

        assert target_name is None
        assert config is None


class TestAskServiceBuildRefinedQuestion:
    """Tests for _build_refined_question() method."""

    @pytest.fixture
    def service(self):
        """Create AskService instance."""
        return AskService()

    def test_no_clarifications(self, service):
        """Test with empty clarifications."""
        result = service._build_refined_question("Original question", {})
        assert result == "Original question"

    def test_with_clarifications(self, service):
        """Test with clarifications."""
        result = service._build_refined_question(
            "How many users?",
            {"time_range": "last 30 days", "aggregation": "unique count"},
        )
        assert "How many users?" in result
        assert "time_range: last 30 days" in result
        assert "aggregation: unique count" in result


class TestAskServiceNullSchema:
    """Tests for null/empty schema handling (rdst-9cq.7)."""

    @pytest.fixture
    def service(self):
        return AskService()

    @pytest.fixture
    def input_data(self):
        return AskInput(question="How many users?", target="test-target", source="cli")

    @pytest.fixture
    def options(self):
        return AskOptions(dry_run=False, timeout_seconds=30, verbose=False)

    @pytest.mark.asyncio
    async def test_error_when_schema_info_is_none(self, service, input_data, options):
        """Schema load returns None schema_info without marking error — should yield error event."""
        events = []

        async def mock_load_config(target):
            return ("test-target", {"engine": "postgresql", "host": "localhost"})

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            with patch("lib.engines.ask3.phases.load_schema") as mock_load:
                def fake_load_schema(ctx, presenter, sem_mgr):
                    # Simulate _collect_from_database returning (None, error_string)
                    # without calling mark_error — the bug scenario.
                    ctx.schema_info = None
                    ctx.schema_formatted = "Schema information: Not available (no target config)"
                    return ctx

                mock_load.side_effect = fake_load_schema

                async for event in service.ask(input_data, options):
                    events.append(event)

        error_events = [e for e in events if isinstance(e, AskErrorEvent)]
        assert len(error_events) == 1, (
            f"Expected error event for null schema, got events: "
            f"{[e.type for e in events]}"
        )
        assert "schema" in error_events[0].message.lower() or "schema" in (error_events[0].phase or "")

    @pytest.mark.asyncio
    async def test_error_when_schema_has_no_tables(self, service, input_data, options):
        """Schema loads but has zero tables — should yield error, not continue."""
        events = []

        async def mock_load_config(target):
            return ("test-target", {"engine": "postgresql", "host": "localhost"})

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            with patch("lib.engines.ask3.phases.load_schema") as mock_load:
                def fake_load_schema(ctx, presenter, sem_mgr):
                    from lib.engines.ask3.types import SchemaInfo, SchemaSource
                    ctx.schema_info = SchemaInfo(
                        target="test-target",
                        db_type="postgresql",
                        source=SchemaSource.DATABASE,
                    )
                    # schema_info exists but has no tables
                    ctx.schema_formatted = ""
                    return ctx

                mock_load.side_effect = fake_load_schema

                async for event in service.ask(input_data, options):
                    events.append(event)

        error_events = [e for e in events if isinstance(e, AskErrorEvent)]
        assert len(error_events) == 1, (
            f"Expected error event for empty schema, got events: "
            f"{[e.type for e in events]}"
        )

    @pytest.mark.asyncio
    async def test_no_filter_phase_when_schema_none(self, service, input_data, options):
        """Null schema should stop before filter phase — never send to LLM."""
        events = []

        async def mock_load_config(target):
            return ("test-target", {"engine": "postgresql", "host": "localhost"})

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            with patch("lib.engines.ask3.phases.load_schema") as mock_load:
                def fake_load_schema(ctx, presenter, sem_mgr):
                    ctx.schema_info = None
                    ctx.schema_formatted = "Schema information: Collection failed (connection refused)"
                    return ctx

                mock_load.side_effect = fake_load_schema

                with patch("lib.engines.ask3.phases.filter_schema") as mock_filter:
                    async for event in service.ask(input_data, options):
                        events.append(event)

                    # filter_schema should never be called
                    mock_filter.assert_not_called()


class TestAskServiceErrorHandling:
    """Tests for error handling edge cases."""

    @pytest.fixture
    def service(self):
        """Create AskService instance."""
        return AskService()

    @pytest.fixture
    def input_data(self):
        """Create test AskInput."""
        return AskInput(
            question="How many users signed up?",
            target="test-target",
            source="cli",
        )

    @pytest.fixture
    def options(self):
        """Create test AskOptions."""
        return AskOptions(
            dry_run=False,
            timeout_seconds=30,
            verbose=False,
        )

    @pytest.mark.asyncio
    async def test_config_load_exception(self, service, input_data, options):
        """Test that config loading exceptions yield error events."""
        events = []

        async def mock_load_config(target):
            raise RuntimeError("Config file corrupted")

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            async for event in service.ask(input_data, options):
                events.append(event)

        assert events[-1].type == "error"
        assert "Config file corrupted" in events[-1].message

    @pytest.mark.asyncio
    async def test_schema_load_timeout(self, service, input_data, options):
        """Test handling when schema loading times out."""
        events = []

        async def mock_load_config(target):
            return ("test-target", {"engine": "postgresql", "host": "localhost"})

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            with patch("lib.engines.ask3.Ask3Context") as mock_ctx_class:
                mock_ctx = Mock()
                mock_ctx.status = Mock()
                mock_ctx.status.value = "error"
                from lib.engines.ask3 import Status

                mock_ctx.status = Status.ERROR
                mock_ctx.error_message = "Schema load timed out"
                mock_ctx_class.return_value = mock_ctx

                with patch("lib.engines.ask3.phases.load_schema") as mock_load:
                    mock_load.return_value = mock_ctx

                    async for event in service.ask(input_data, options):
                        events.append(event)

        assert events[-1].type == "error"

    @pytest.mark.asyncio
    async def test_empty_question_handling(self, service, options):
        """Test handling empty question input."""
        input_data = AskInput(
            question="",  # Empty question
            target="test-target",
            source="cli",
        )
        events = []

        async def mock_load_config(target):
            return ("test-target", {"engine": "postgresql"})

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            async for event in service.ask(input_data, options):
                events.append(event)

        # Should handle empty question gracefully (may yield error or process)
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_very_long_question(self, service, options):
        """Test handling very long question input."""
        long_question = "How many users " * 1000  # Very long question
        input_data = AskInput(
            question=long_question,
            target="test-target",
            source="cli",
        )
        events = []

        async def mock_load_config(target):
            return (None, None)  # Trigger early exit

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            async for event in service.ask(input_data, options):
                events.append(event)

        # Should handle long question gracefully
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_special_characters_in_question(self, service, options):
        """Test handling special characters in question."""
        input_data = AskInput(
            question="How many users with name='O'Brien' AND status=\"active\"?",
            target="test-target",
            source="cli",
        )
        events = []

        async def mock_load_config(target):
            return (None, None)

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            async for event in service.ask(input_data, options):
                events.append(event)

        # Should handle special characters
        assert len(events) >= 1


class TestAskServiceDryRun:
    """Tests for --dry-run behavior (rdst-2vr.19)."""

    @pytest.fixture
    def service(self):
        return AskService()

    @pytest.fixture
    def input_data(self):
        return AskInput(question="How many users?", target="test-target", source="cli")

    @pytest.fixture
    def dry_run_options(self):
        return AskOptions(dry_run=True, timeout_seconds=30, verbose=False)

    @pytest.mark.asyncio
    async def test_dry_run_skips_execution(self, service, input_data, dry_run_options):
        """dry_run=True should generate SQL but never call execute_query."""
        events = []

        async def mock_load_config(target):
            return ("test-target", {"engine": "postgresql", "host": "localhost"})

        def fake_load(ctx, p, s):
            from lib.engines.ask3.types import SchemaInfo, SchemaSource
            ctx.schema_info = SchemaInfo(
                target="test-target",
                db_type="postgresql",
                source=SchemaSource.DATABASE,
            )
            ctx.schema_info.tables = {"users": Mock()}
            ctx.schema_formatted = "users table"
            return ctx

        def fake_filter(ctx, p, s):
            return ctx

        def fake_gen(ctx, p, s):
            ctx.sql = "SELECT COUNT(*) FROM users"
            ctx.sql_explanation = "Counts users"
            return ctx

        def fake_val(ctx, p):
            ctx.validation_errors = []
            return ctx

        with patch.object(service, "_load_config", side_effect=mock_load_config), \
             patch("lib.engines.ask3.phases.load_schema", side_effect=fake_load), \
             patch("lib.engines.ask3.phases.filter_schema", side_effect=fake_filter), \
             patch("lib.engines.ask3.phases.generate_sql", side_effect=fake_gen), \
             patch("lib.engines.ask3.phases.validate_sql", side_effect=fake_val), \
             patch("lib.engines.ask3.phases.execute_query") as mock_exec:

            async for event in service.ask(input_data, dry_run_options):
                events.append(event)

            # execute_query must NOT be called
            mock_exec.assert_not_called()

        # Should have a result event with SQL but no rows
        result_events = [e for e in events if isinstance(e, AskResultEvent)]
        assert len(result_events) == 1
        result = result_events[0]
        assert result.sql == "SELECT COUNT(*) FROM users"
        assert result.rows == []
        assert result.row_count == 0


class TestAskServiceTimeoutScenarios:
    """Tests for timeout handling scenarios."""

    @pytest.fixture
    def service(self):
        """Create AskService instance."""
        return AskService()

    @pytest.fixture
    def input_data(self):
        """Create test AskInput."""
        return AskInput(
            question="Test question",
            target="test-target",
            source="cli",
        )

    @pytest.mark.asyncio
    async def test_config_timeout(self, service, input_data):
        """Test handling when config loading times out."""
        options = AskOptions(timeout_seconds=1)
        events = []

        async def slow_config(*args):
            import asyncio

            await asyncio.sleep(0.1)
            raise asyncio.TimeoutError("Config timed out")

        with patch.object(service, "_load_config", side_effect=slow_config):
            async for event in service.ask(input_data, options):
                events.append(event)

        assert events[-1].type == "error"

    @pytest.mark.asyncio
    async def test_llm_timeout(self, service, input_data):
        """Test handling when LLM call times out."""
        options = AskOptions(timeout_seconds=1)
        events = []

        async def mock_load_config(target):
            return ("test-target", {"engine": "postgresql", "host": "localhost"})

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            # Mock the Ask3Context and phases
            with patch("lib.engines.ask3.Ask3Context") as mock_ctx_class:
                mock_ctx = Mock()
                mock_ctx.status = Mock()
                from lib.engines.ask3 import Status

                mock_ctx.status = Status.ERROR
                mock_ctx.error_message = "LLM request timed out"
                mock_ctx_class.return_value = mock_ctx

                with patch("lib.engines.ask3.phases.load_schema") as mock_load:
                    mock_load.return_value = mock_ctx

                    async for event in service.ask(input_data, options):
                        events.append(event)

        # Should yield error event
        assert events[-1].type == "error"

    @pytest.mark.asyncio
    async def test_cancelled_operation(self, service, input_data):
        """Test handling when operation is cancelled."""
        options = AskOptions()
        events = []

        async def mock_load_config(target):
            import asyncio

            raise asyncio.CancelledError("Operation cancelled")

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            try:
                async for event in service.ask(input_data, options):
                    events.append(event)
            except asyncio.CancelledError:
                pass

        # Should have at least initial status before cancellation
        assert len(events) >= 1


class TestAskServiceNetworkFailures:
    """Tests for network failure simulations."""

    @pytest.fixture
    def service(self):
        """Create AskService instance."""
        return AskService()

    @pytest.fixture
    def input_data(self):
        """Create test AskInput."""
        return AskInput(
            question="Test question",
            target="test-target",
            source="cli",
        )

    @pytest.fixture
    def options(self):
        """Create test AskOptions."""
        return AskOptions()

    @pytest.mark.asyncio
    async def test_database_connection_failure(self, service, input_data, options):
        """Test handling database connection failures."""
        events = []

        async def mock_load_config(target):
            return ("test-target", {"engine": "postgresql", "host": "localhost"})

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            with patch("lib.engines.ask3.Ask3Context") as mock_ctx_class:
                mock_ctx = Mock()
                from lib.engines.ask3 import Status

                mock_ctx.status = Status.ERROR
                mock_ctx.error_message = "Connection refused"
                mock_ctx_class.return_value = mock_ctx

                with patch("lib.engines.ask3.phases.load_schema") as mock_load:
                    mock_load.return_value = mock_ctx

                    async for event in service.ask(input_data, options):
                        events.append(event)

        assert events[-1].type == "error"

    @pytest.mark.asyncio
    async def test_llm_api_network_error(self, service, input_data, options):
        """Test handling LLM API network errors."""
        events = []

        async def mock_load_config(target):
            return ("test-target", {"engine": "postgresql", "host": "localhost"})

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            with patch("lib.engines.ask3.Ask3Context") as mock_ctx_class:
                mock_ctx = Mock()
                from lib.engines.ask3 import Status

                mock_ctx.status = Status.ERROR
                mock_ctx.error_message = "Network error calling LLM API"
                mock_ctx_class.return_value = mock_ctx

                with patch("lib.engines.ask3.phases.load_schema") as mock_load:
                    mock_load.return_value = mock_ctx

                    async for event in service.ask(input_data, options):
                        events.append(event)

        assert events[-1].type == "error"

    @pytest.mark.asyncio
    async def test_authentication_failure(self, service, input_data, options):
        """Test handling database authentication failures."""
        events = []

        async def mock_load_config(target):
            return ("test-target", {"engine": "postgresql", "host": "localhost"})

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            with patch("lib.engines.ask3.Ask3Context") as mock_ctx_class:
                mock_ctx = Mock()
                from lib.engines.ask3 import Status

                mock_ctx.status = Status.ERROR
                mock_ctx.error_message = "Authentication failed"
                mock_ctx_class.return_value = mock_ctx

                with patch("lib.engines.ask3.phases.load_schema") as mock_load:
                    mock_load.return_value = mock_ctx

                    async for event in service.ask(input_data, options):
                        events.append(event)

        assert events[-1].type == "error"

    @pytest.mark.asyncio
    async def test_partial_results_on_network_failure(
        self, service, input_data, options
    ):
        """Test that partial results are preserved on late network failure."""
        events = []

        async def mock_load_config(target):
            return ("test-target", {"engine": "postgresql", "host": "localhost"})

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            with patch("lib.engines.ask3.Ask3Context") as mock_ctx_class:
                mock_ctx = Mock()
                from lib.engines.ask3 import Status

                # Status.SUCCESS means schema loaded OK, but we'll fail on next step
                mock_ctx.status = Status.SUCCESS
                mock_ctx.schema_info = Mock()
                mock_ctx.schema_info.tables = {"users": Mock()}
                mock_ctx.schema_source = "test"
                mock_ctx.all_available_tables = ["users"]
                mock_ctx.error_message = None
                mock_ctx_class.return_value = mock_ctx

                with patch("lib.engines.ask3.phases.load_schema") as mock_load:
                    mock_load.return_value = mock_ctx

                    with patch("lib.engines.ask3.phases.filter_schema") as mock_filter:
                        # Simulate network error during filter
                        mock_filter.side_effect = ConnectionError("Network lost")

                        async for event in service.ask(input_data, options):
                            events.append(event)

        # Should have yielded schema_loaded before error
        schema_events = [e for e in events if e.type == "schema_loaded"]
        assert len(schema_events) == 1
        # Last event should be error
        assert events[-1].type == "error"

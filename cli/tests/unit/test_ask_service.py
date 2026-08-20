"""
Unit tests for AskService.

Tests the text-to-SQL streaming service including event yielding,
clarification handling, and error scenarios.
"""

import asyncio
from unittest.mock import MagicMock, Mock, patch

import pytest

from features.ask.ambiguity_detection import (
    NON_INTERACTIVE_CLARIFICATION_POLICY,
    RANKED_RESOLVER_POLICY,
    Ambiguity,
    AmbiguityOption,
)
from features.ask.events import (
    AskClarificationNeededEvent,
    AskErrorEvent,
    AskResultEvent,
    AskSchemaLoadedEvent,
    AskSqlGeneratedEvent,
    AskStatusEvent,
)
from features.ask.models import (
    AskClarificationQuestion,
    AskInput,
    AskInterpretation,
    AskOptions,
    AskPhase,
)
from features.ask.service import AskService

pytestmark = pytest.mark.usefixtures("run_blocking_inline")


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
            no_interactive=True,
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
        assert "rdst configure add" in error_events[0].message

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
        from features.ask.service import _sessions

        assert isinstance(_sessions, dict)

    @pytest.mark.asyncio
    async def test_ask_resume_runs_generation_with_refined_question(self):
        from features.ask.engine.ask3.types import SchemaInfo, SchemaSource

        sessions = {}
        service = AskService(session_store=sessions, persist_queries=False)
        input_data = AskInput(question="Show active users", target="test")
        options = AskOptions(dry_run=True, no_interactive=False, persist_query=False)
        ambiguity = Ambiguity(
            id="status",
            category="status",
            clarifying_question="What does active mean?",
            possible_interpretations=[
                AmbiguityOption("enabled", "enabled = 1", 0.9),
                AmbiguityOption("recent", "last_seen is recent", 0.1),
            ],
            term="active",
            reason="active is undefined",
        )

        async def mock_load_config(target):
            return (target, {"engine": "postgresql"})

        def fake_load(ctx, _presenter, _manager):
            ctx.schema_info = SchemaInfo(
                target="test",
                db_type="postgresql",
                source=SchemaSource.DATABASE,
            )
            ctx.schema_info.tables = {"users": Mock()}
            ctx.schema_formatted = "users table"
            return ctx

        def fake_generate(ctx, _presenter, _manager):
            assert "What does active mean?" in ctx.refined_question
            assert "Answer: enabled = 1" in ctx.refined_question
            ctx.sql = "SELECT * FROM users WHERE enabled = 1"
            ctx.generated_sql = ctx.sql
            return ctx

        with (
            patch.object(service, "_load_config", side_effect=mock_load_config),
            patch("features.ask.service.load_schema", side_effect=fake_load),
            patch.object(
                service,
                "_detect_ambiguities",
                side_effect=lambda ctx: (ctx, [], [ambiguity]),
            ),
            patch("features.ask.service.generate_sql", side_effect=fake_generate),
            patch(
                "features.ask.service.validate_sql",
                side_effect=lambda ctx, _presenter: ctx,
            ),
        ):
            initial = [event async for event in service.ask(input_data, options)]
            clarification = initial[-1]
            assert isinstance(clarification, AskClarificationNeededEvent)
            resumed = [
                event
                async for event in service.resume(
                    clarification.session_id, {"status": "enabled = 1"}
                )
            ]

        assert isinstance(resumed[-1], AskResultEvent)
        assert resumed[-1].sql == "SELECT * FROM users WHERE enabled = 1"
        assert sessions == {}

    @pytest.mark.asyncio
    async def test_validation_failure_gets_exactly_one_bounded_repair(self):
        from features.ask.engine.ask3.context import Ask3Context
        from features.ask.engine.ask3.types import ValidationError

        service = AskService(persist_queries=False)
        ctx = Ask3Context(
            question="Show user ids",
            target="test",
            dry_run=True,
        )

        def fake_generate(context, _presenter, _manager):
            context.sql = "SELECT id FROM users; SELECT name FROM users"
            context.generated_sql = context.sql
            return context

        validations = 0

        def fake_validate(context, _presenter):
            nonlocal validations
            validations += 1
            context.clear_validation_errors()
            if validations == 1:
                context.validation_errors.append(
                    ValidationError(
                        column="",
                        table_alias=None,
                        message="Only a single statement is allowed",
                    )
                )
            return context

        def fake_repair(context, _presenter, error_message, _manager):
            assert "Only a single statement is allowed" in error_message
            context.sql = "SELECT id FROM users"
            context.generated_sql = context.sql
            return context

        with (
            patch("features.ask.service.generate_sql", side_effect=fake_generate),
            patch("features.ask.service.validate_sql", side_effect=fake_validate),
            patch(
                "features.ask.service.repair_validation_error",
                side_effect=fake_repair,
            ) as repair,
        ):
            events = [
                event
                async for event in service._run_from_generate(ctx, persist_query=False)
            ]

        assert isinstance(events[-1], AskResultEvent)
        assert events[-1].sql == "SELECT id FROM users"
        assert validations == 2
        assert repair.call_count == 1
        assert ctx.retry_count == 1

    @pytest.mark.asyncio
    async def test_failed_validation_repair_does_not_execute_invalid_sql(self):
        from features.ask.engine.ask3.context import Ask3Context
        from features.ask.engine.ask3.types import ValidationError

        service = AskService(persist_queries=False)
        ctx = Ask3Context(question="Show users", target="test")

        def fake_generate(context, _presenter, _manager):
            context.sql = "SELECT 1; SELECT 2"
            return context

        validations = 0

        def fake_validate(context, _presenter):
            nonlocal validations
            validations += 1
            context.clear_validation_errors()
            context.validation_errors.append(
                ValidationError(
                    column="",
                    table_alias=None,
                    message="Only a single statement is allowed",
                )
            )
            return context

        with (
            patch("features.ask.service.generate_sql", side_effect=fake_generate),
            patch("features.ask.service.validate_sql", side_effect=fake_validate),
            patch(
                "features.ask.service.repair_validation_error",
                side_effect=lambda context, *_args: context,
            ),
            patch("features.ask.service.execute_query") as execute,
        ):
            events = [
                event
                async for event in service._run_from_generate(ctx, persist_query=False)
            ]

        assert validations == 2
        assert isinstance(events[-1], AskErrorEvent)
        assert events[-1].phase == AskPhase.VALIDATE
        execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_generation_error_preserves_model_limit_envelope(self):
        from features.ask.engine.ask3.context import Ask3Context

        service = AskService(persist_queries=False)
        ctx = Ask3Context(question="Show users", target="warehouse")

        def fake_generate(context, _presenter, _manager):
            context.mark_error(
                "The complete database schema could not fit within the model context.",
                code="ANTHROPIC_CONTEXT_WINDOW_EXCEEDED",
                category="model-limit",
            )
            return context

        with patch("features.ask.service.generate_sql", side_effect=fake_generate):
            events = [
                event
                async for event in service._run_from_generate(ctx, persist_query=False)
            ]

        error = events[-1]
        assert isinstance(error, AskErrorEvent)
        assert error.phase == AskPhase.GENERATE
        assert error.code == "ANTHROPIC_CONTEXT_WINDOW_EXCEEDED"
        assert error.category == "model-limit"
        assert error.target == "warehouse"

    @pytest.mark.asyncio
    async def test_literal_provenance_failure_gets_one_bounded_repair(self):
        from features.ask.engine.ask3.context import Ask3Context
        from features.ask.engine.ask3.types import ColumnInfo, SchemaInfo, TableInfo

        service = AskService(persist_queries=False)
        ctx = Ask3Context(
            question=(
                "How many students are enrolled at the State Special School school "
                "for the 2014-2015 academic year?"
            ),
            target="california_schools",
            db_type="mysql",
            dry_run=True,
            enforce_result_limit=False,
        )
        ctx.schema_formatted = """Table: frpm
  School Name (text)
  County Name (text)
  Educational Option Type (enum) [enum: State Special School]
  Academic Year (enum) [enum: 2014-2015]
  Enrollment (Ages 5-17) (double)
"""
        ctx.schema_info = SchemaInfo(
            target=ctx.target,
            db_type=ctx.db_type,
            tables={
                "frpm": TableInfo(
                    name="frpm",
                    columns={
                        name: ColumnInfo(name=name, data_type=data_type)
                        for name, data_type in {
                            "School Name": "text",
                            "County Name": "text",
                            "Educational Option Type": "enum",
                            "Academic Year": "enum",
                            "Enrollment (Ages 5-17)": "double",
                        }.items()
                    },
                )
            },
        )

        def fake_generate(context, _presenter, _manager):
            context.sql = (
                "SELECT `Enrollment (Ages 5-17)` FROM frpm "
                "WHERE `School Name` = 'State Special School' "
                "AND `County Name` = 'Alameda' "
                "AND `Academic Year` = '2014-2015'"
            )
            context.generated_sql = context.sql
            return context

        def fake_repair(context, _presenter, error_message, _manager):
            assert "free-text column" in error_message
            assert "Alameda" in error_message
            context.sql = (
                "SELECT `Enrollment (Ages 5-17)` FROM frpm "
                "WHERE `Educational Option Type` = 'State Special School' "
                "AND `Academic Year` = '2014-2015'"
            )
            context.generated_sql = context.sql
            return context

        with (
            patch("features.ask.service.generate_sql", side_effect=fake_generate),
            patch(
                "features.ask.service.repair_validation_error",
                side_effect=fake_repair,
            ) as repair,
        ):
            events = [
                event
                async for event in service._run_from_generate(ctx, persist_query=False)
            ]

        assert isinstance(events[-1], AskResultEvent)
        assert "Educational Option Type" in events[-1].sql
        assert repair.call_count == 1
        assert ctx.retry_count == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("propagate", [False, True])
    async def test_resume_preserves_unexpected_error_policy(self, propagate):
        from features.ask.engine.ask3.context import Ask3Context
        from features.ask.service import _PendingAskSession

        sessions = {
            "pending": _PendingAskSession(
                context=Ask3Context(question="question", target="test"),
                persist_query=False,
                raise_unexpected_errors=propagate,
            )
        }
        service = AskService(session_store=sessions)

        async def broken_run(_ctx, *, persist_query=True):
            raise RuntimeError("resume failed")
            if False:
                yield None

        with patch.object(service, "_run_from_generate", side_effect=broken_run):
            if propagate:
                with pytest.raises(RuntimeError, match="resume failed"):
                    _ = [event async for event in service.resume("pending")]
            else:
                events = [event async for event in service.resume("pending")]
                assert isinstance(events[-1], AskErrorEvent)
                assert events[-1].message == "resume failed"

    def test_abandon_removes_injected_session(self):
        sessions = {"pending": Mock()}
        service = AskService(session_store=sessions)

        assert service.abandon("pending") is True
        assert sessions == {}
        assert service.abandon("pending") is False

    @pytest.mark.asyncio
    async def test_resume_adds_answers_to_refined_question(self):
        from features.ask.engine.ask3.context import Ask3Context
        from features.ask.service import _PendingAskSession

        ctx = Ask3Context(question="Show active users", target="test")
        sessions = {"pending": _PendingAskSession(context=ctx, persist_query=False)}
        service = AskService(session_store=sessions)

        async def fake_run(resumed_ctx, *, persist_query=True):
            assert persist_query is False
            if False:
                yield None

        with patch.object(service, "_run_from_generate", side_effect=fake_run):
            events = [
                event
                async for event in service.resume(
                    "pending", {"status": "active means enabled = 1"}
                )
            ]

        assert events == []
        assert ctx.refined_question == (
            "Show active users (status: active means enabled = 1)"
        )
        assert sessions == {}


class TestAskServiceDependencyInjection:
    @pytest.mark.asyncio
    async def test_injected_dependencies_reach_every_phase(self):
        from features.ask.engine.ask3.types import (
            ExecutionResult,
            SchemaInfo,
            SchemaSource,
        )

        llm_manager = Mock()
        semantic_manager = Mock()
        db_executor = Mock()

        service = AskService(
            llm_manager=llm_manager,
            semantic_manager=semantic_manager,
            db_executor=db_executor,
            persist_queries=False,
        )
        input_data = AskInput(question="Count users", target="test")
        options = AskOptions(no_interactive=True, persist_query=False)

        async def mock_load_config(target):
            return (target, {"engine": "postgresql", "host": "localhost"})

        def fake_load(ctx, presenter, dependency):
            assert dependency is semantic_manager
            ctx.schema_info = SchemaInfo(
                target="test",
                db_type="postgresql",
                source=SchemaSource.DATABASE,
            )
            ctx.schema_info.tables = {"users": Mock()}
            ctx.schema_formatted = "users table"
            return ctx

        def fake_generate(ctx, presenter, dependency):
            assert dependency is llm_manager
            assert list(ctx.schema_info.tables) == ["users"]
            ctx.sql = "SELECT COUNT(*) FROM users"
            return ctx

        def fake_execute(ctx, presenter, dependency):
            assert dependency is db_executor
            ctx.execution_result = ExecutionResult(
                columns=["count"], rows=[(1,)], row_count=1
            )
            return ctx

        with (
            patch.object(service, "_load_config", side_effect=mock_load_config),
            patch.object(
                service,
                "_detect_ambiguities",
                side_effect=lambda ctx: (ctx, [], []),
            ),
            patch("features.ask.service.load_schema", side_effect=fake_load),
            patch("features.ask.service.generate_sql", side_effect=fake_generate),
            patch("features.ask.service.validate_sql", side_effect=lambda ctx, _: ctx),
            patch("features.ask.service.execute_query", side_effect=fake_execute),
        ):
            events = [event async for event in service.ask(input_data, options)]

        assert isinstance(events[-1], AskResultEvent)
        assert events[-1].query_hash == ""

    def test_ambiguity_detection_uses_injected_manager(self):
        llm_manager = Mock()
        service = AskService(llm_manager=llm_manager)
        ctx = Mock(
            question="Which users?",
            schema_formatted="users table",
            db_type="postgresql",
            provided_context="Active means enabled = true.",
            no_interactive=False,
        )

        with patch(
            "features.ask.service.detect_ambiguities",
            return_value={"success": False, "error": "truncated response"},
        ) as detect:
            returned_ctx, interpretations, ambiguities = service._detect_ambiguities(
                ctx
            )

        assert detect.call_args.kwargs["llm_manager"] is llm_manager
        assert detect.call_args.kwargs["provided_context"] == ctx.provided_context
        assert returned_ctx is ctx
        assert interpretations == []
        assert ambiguities == []
        assert ctx.ambiguity_report == {
            "error": "truncated response",
            "fallback": "fail_closed",
        }
        assert ctx.clarification_policy == RANKED_RESOLVER_POLICY
        ctx.mark_error.assert_called_once_with(
            "Failed to analyze whether the question requires clarification"
        )

    @pytest.mark.asyncio
    async def test_duplicate_clarification_categories_receive_unique_answer_keys(self):
        sessions = {}
        service = AskService(session_store=sessions)
        input_data = AskInput(question="Show users", target="test")
        options = AskOptions(no_interactive=False)
        ambiguities = [
            Ambiguity(
                id="status-1",
                category="status",
                term="status",
                reason="status is ambiguous",
                clarifying_question="Which status?",
                possible_interpretations=[
                    AmbiguityOption("active", "active", 0.7),
                    AmbiguityOption("disabled", "disabled", 0.3),
                ],
            ),
            Ambiguity(
                id="status-2",
                category="status",
                term="other status",
                reason="status is ambiguous",
                clarifying_question="Which other status?",
                possible_interpretations=[
                    AmbiguityOption("enabled", "enabled", 0.7),
                    AmbiguityOption("disabled", "disabled", 0.3),
                ],
            ),
        ]

        async def mock_load_config(target):
            return (target, {"engine": "postgresql"})

        def fake_load(ctx, _presenter, _manager):
            from features.ask.engine.ask3.types import SchemaInfo, SchemaSource

            ctx.schema_info = SchemaInfo(
                target="test",
                db_type="postgresql",
                source=SchemaSource.DATABASE,
            )
            ctx.schema_info.tables = {"users": Mock()}
            return ctx

        with (
            patch.object(service, "_load_config", side_effect=mock_load_config),
            patch("features.ask.service.load_schema", side_effect=fake_load),
            patch.object(
                service,
                "_detect_ambiguities",
                side_effect=lambda ctx: (ctx, [], ambiguities),
            ),
        ):
            events = [event async for event in service.ask(input_data, options)]

        assert isinstance(events[-1], AskClarificationNeededEvent)
        assert [question.id for question in events[-1].questions] == [
            "status",
            "status:2",
        ]
        assert list(sessions) == [events[-1].session_id]
        assert service.abandon(events[-1].session_id)

    @pytest.mark.asyncio
    async def test_non_interactive_mode_skips_llm_detector_and_generates(self):
        sessions = {}
        observed_contexts = []
        service = AskService(
            session_store=sessions,
            persist_queries=False,
            phase_observer=lambda _phase, ctx: observed_contexts.append(ctx),
        )
        input_data = AskInput(question="Show users", target="test")
        options = AskOptions(
            no_interactive=True,
            dry_run=True,
            persist_query=False,
        )

        async def mock_load_config(target):
            return (target, {"engine": "postgresql"})

        def fake_load(ctx, _presenter, _manager):
            from features.ask.engine.ask3.types import SchemaInfo, SchemaSource

            ctx.schema_info = SchemaInfo(
                target="test",
                db_type="postgresql",
                source=SchemaSource.DATABASE,
            )
            ctx.schema_info.tables = {"users": Mock()}
            return ctx

        def fake_generate(ctx, _presenter, _manager):
            assert ctx.question == "Show users"
            assert ctx.refined_question is None
            assert ctx.clarifications == {}
            ctx.sql = "SELECT * FROM users"
            ctx.generated_sql = ctx.sql
            return ctx

        with (
            patch.object(service, "_load_config", side_effect=mock_load_config),
            patch("features.ask.service.load_schema", side_effect=fake_load),
            patch.object(
                service,
                "_detect_ambiguities",
                side_effect=AssertionError(
                    "Non-interactive mode must not invoke the LLM detector"
                ),
            ),
            patch(
                "features.ask.service.generate_sql",
                side_effect=fake_generate,
            ),
            patch(
                "features.ask.service.validate_sql",
                side_effect=lambda ctx, _presenter: ctx,
            ),
        ):
            events = [event async for event in service.ask(input_data, options)]

        assert isinstance(events[-1], AskResultEvent)
        assert not any(
            isinstance(event, AskClarificationNeededEvent) for event in events
        )
        assert sessions == {}
        ctx = observed_contexts[-1]
        assert ctx.clarifications == {}
        assert ctx.refined_question is None
        assert ctx.clarification_resolutions == []
        assert ctx.clarification_policy == NON_INTERACTIVE_CLARIFICATION_POLICY
        assert ctx.ambiguity_report == {
            "ambiguities": [],
            "total_ambiguities": 0,
            "requires_clarification": False,
            "can_proceed_with_assumptions": True,
            "overall_confidence": 1.0,
            "decision": "deterministic_only_non_interactive",
            "llm_detector_invoked": False,
        }

    @pytest.mark.asyncio
    async def test_non_interactive_mode_stops_for_deterministic_missing_intent(self):
        observed_contexts = []
        service = AskService(
            session_store={},
            persist_queries=False,
            phase_observer=lambda _phase, ctx: observed_contexts.append(ctx),
        )

        async def mock_load_config(target):
            return (target, {"engine": "postgresql"})

        def fake_load(ctx, _presenter, _manager):
            from features.ask.engine.ask3.types import SchemaInfo, SchemaSource

            ctx.schema_info = SchemaInfo(
                target="test",
                db_type="postgresql",
                source=SchemaSource.DATABASE,
            )
            ctx.schema_info.tables = {"users": Mock()}
            return ctx

        with (
            patch.object(service, "_load_config", side_effect=mock_load_config),
            patch("features.ask.service.load_schema", side_effect=fake_load),
            patch.object(
                service,
                "_detect_ambiguities",
                side_effect=AssertionError(
                    "Non-interactive mode must not invoke the LLM detector"
                ),
            ),
            patch(
                "features.ask.service.generate_sql",
                side_effect=AssertionError(
                    "Missing sort direction must stop before generation"
                ),
            ),
            patch(
                "features.ask.service.validate_sql",
                side_effect=lambda ctx, _presenter: ctx,
            ),
        ):
            events = [
                event
                async for event in service.ask(
                    AskInput(
                        question="Show users sorted by score",
                        target="test",
                    ),
                    AskOptions(
                        no_interactive=True,
                        dry_run=True,
                        persist_query=False,
                    ),
                )
            ]

        assert isinstance(events[-1], AskErrorEvent)
        assert events[-1].code == "clarification_required"
        resolution = observed_contexts[-1].clarification_resolutions[0]
        assert resolution["ambiguity_id"] == "intent-sort-direction"
        assert resolution["action"] == "abstain"
        assert resolution["source"] == "deterministic_intent"
        assert resolution["applied"] is False

    @pytest.mark.asyncio
    async def test_benchmark_policy_propagates_unexpected_phase_errors(self):
        service = AskService()
        input_data = AskInput(question="Show users", target="test")
        options = AskOptions(raise_unexpected_errors=True)

        async def mock_load_config(target):
            return (target, {"engine": "postgresql"})

        with (
            patch.object(service, "_load_config", side_effect=mock_load_config),
            patch(
                "features.ask.service.load_schema",
                side_effect=RuntimeError("transport failed"),
            ),
            pytest.raises(RuntimeError, match="transport failed"),
        ):
            _ = [event async for event in service.ask(input_data, options)]

    @pytest.mark.asyncio
    async def test_options_reach_context(self):
        service = AskService()
        options = AskOptions(
            max_rows=321,
            no_interactive=True,
            enforce_result_limit=False,
        )
        input_data = AskInput(
            question="Count users",
            target="test",
            provided_context="Users means enabled accounts.",
        )

        async def mock_load_config(target):
            return (target, {"engine": "postgresql"})

        with (
            patch.object(service, "_load_config", side_effect=mock_load_config),
            patch("features.ask.service.create_context") as create,
            patch("features.ask.service.load_schema") as load,
        ):
            ctx = Mock()
            from features.ask.engine.ask3 import Status

            ctx.status = Status.ERROR
            ctx.error_message = "stop"
            create.return_value = ctx
            load.return_value = ctx
            _ = [event async for event in service.ask(input_data, options)]

        assert create.call_args.kwargs["max_rows"] == 321
        assert create.call_args.kwargs["no_interactive"] is True
        assert create.call_args.kwargs["enforce_result_limit"] is False
        assert (
            create.call_args.kwargs["provided_context"]
            == "Users means enabled accounts."
        )


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

        with patch("features.ask.service.create_targets_config", return_value=mock_cfg):
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

        with patch("features.ask.service.create_targets_config", return_value=mock_cfg):
            target_name, config = await service._load_config(None)

        assert target_name == "default-target"

    @pytest.mark.asyncio
    async def test_load_config_no_default(self, service):
        """Test _load_config when no default configured."""
        mock_cfg = Mock()
        mock_cfg.get_default.return_value = None
        mock_cfg.get.return_value = None

        with patch("features.ask.service.create_targets_config", return_value=mock_cfg):
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
            with patch("features.ask.service.load_schema") as mock_load:

                def fake_load_schema(ctx, presenter, sem_mgr):
                    # Simulate _collect_from_database returning (None, error_string)
                    # without calling mark_error — the bug scenario.
                    ctx.schema_info = None
                    ctx.schema_formatted = (
                        "Schema information: Not available (no target config)"
                    )
                    return ctx

                mock_load.side_effect = fake_load_schema

                async for event in service.ask(input_data, options):
                    events.append(event)

        error_events = [e for e in events if isinstance(e, AskErrorEvent)]
        assert len(error_events) == 1, (
            f"Expected error event for null schema, got events: "
            f"{[e.type for e in events]}"
        )
        assert "schema" in error_events[0].message.lower() or "schema" in (
            error_events[0].phase or ""
        )

    @pytest.mark.asyncio
    async def test_error_when_schema_has_no_tables(self, service, input_data, options):
        """Schema loads but has zero tables — should yield error, not continue."""
        events = []

        async def mock_load_config(target):
            return ("test-target", {"engine": "postgresql", "host": "localhost"})

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            with patch("features.ask.service.load_schema") as mock_load:

                def fake_load_schema(ctx, presenter, sem_mgr):
                    from features.ask.engine.ask3.types import SchemaInfo, SchemaSource

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
    async def test_null_schema_stops_before_generation(
        self, service, input_data, options
    ):
        events = []

        async def mock_load_config(target):
            return ("test-target", {"engine": "postgresql", "host": "localhost"})

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            with patch("features.ask.service.load_schema") as mock_load:

                def fake_load_schema(ctx, presenter, sem_mgr):
                    ctx.schema_info = None
                    ctx.schema_formatted = (
                        "Schema information: Collection failed (connection refused)"
                    )
                    return ctx

                mock_load.side_effect = fake_load_schema

                async for event in service.ask(input_data, options):
                    events.append(event)

        assert events[-1].type == "error"


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
            with patch("features.ask.service.create_context") as mock_create_context:
                mock_ctx = Mock()
                mock_ctx.status = Mock()
                mock_ctx.status.value = "error"
                from features.ask.engine.ask3 import Status

                mock_ctx.status = Status.ERROR
                mock_ctx.error_message = "Schema load timed out"
                mock_create_context.return_value = mock_ctx

                with patch("features.ask.service.load_schema") as mock_load:
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
            from features.ask.engine.ask3.types import SchemaInfo, SchemaSource

            ctx.schema_info = SchemaInfo(
                target="test-target",
                db_type="postgresql",
                source=SchemaSource.DATABASE,
            )
            ctx.schema_info.tables = {"users": Mock()}
            ctx.schema_formatted = "users table"
            return ctx

        def fake_gen(ctx, p, s):
            ctx.sql = "SELECT COUNT(*) FROM users"
            ctx.sql_explanation = "Counts users"
            return ctx

        def fake_val(ctx, p):
            ctx.validation_errors = []
            return ctx

        with (
            patch.object(service, "_load_config", side_effect=mock_load_config),
            patch("features.ask.service.load_schema", side_effect=fake_load),
            patch.object(
                service,
                "_detect_ambiguities",
                side_effect=lambda ctx: (ctx, [], []),
            ),
            patch("features.ask.service.generate_sql", side_effect=fake_gen),
            patch("features.ask.service.validate_sql", side_effect=fake_val),
            patch("features.ask.service.execute_query") as mock_exec,
        ):
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

    @pytest.mark.asyncio
    async def test_diagnostic_schema_formatter_is_forwarded_only_when_configured(
        self, input_data, dry_run_options
    ):
        formatter = Mock(name="compact-schema-formatter")
        service = AskService(diagnostic_schema_formatter_fn=formatter)

        async def mock_load_config(target):
            return ("test-target", {"engine": "postgresql", "host": "localhost"})

        def fake_load(ctx, _presenter, _semantic_manager, received_formatter):
            from features.ask.engine.ask3.types import SchemaInfo, SchemaSource

            assert received_formatter is formatter
            ctx.schema_info = SchemaInfo(
                target="test-target",
                db_type="postgresql",
                source=SchemaSource.SEMANTIC,
            )
            ctx.schema_info.tables = {"users": Mock()}
            ctx.schema_formatted = "compact users schema"
            return ctx

        def fake_generate(ctx, _presenter, _llm_manager):
            ctx.sql = "SELECT COUNT(*) FROM users"
            ctx.sql_explanation = "Counts users"
            return ctx

        with (
            patch.object(service, "_load_config", side_effect=mock_load_config),
            patch("features.ask.service.load_schema", side_effect=fake_load),
            patch.object(
                service,
                "_detect_ambiguities",
                side_effect=lambda ctx: (ctx, [], []),
            ),
            patch("features.ask.service.generate_sql", side_effect=fake_generate),
            patch("features.ask.service.validate_sql", side_effect=lambda ctx, _: ctx),
        ):
            events = [event async for event in service.ask(input_data, dry_run_options)]

        assert isinstance(events[-1], AskResultEvent)
        assert events[-1].sql == "SELECT COUNT(*) FROM users"

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
            # Mock the Ask3 context factory and phases
            with patch("features.ask.service.create_context") as mock_create_context:
                mock_ctx = Mock()
                mock_ctx.status = Mock()
                from features.ask.engine.ask3 import Status

                mock_ctx.status = Status.ERROR
                mock_ctx.error_message = "LLM request timed out"
                mock_create_context.return_value = mock_ctx

                with patch("features.ask.service.load_schema") as mock_load:
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
            with patch("features.ask.service.create_context") as mock_create_context:
                mock_ctx = Mock()
                from features.ask.engine.ask3 import Status

                mock_ctx.status = Status.ERROR
                mock_ctx.error_message = "Connection refused"
                mock_create_context.return_value = mock_ctx

                with patch("features.ask.service.load_schema") as mock_load:
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
            with patch("features.ask.service.create_context") as mock_create_context:
                mock_ctx = Mock()
                from features.ask.engine.ask3 import Status

                mock_ctx.status = Status.ERROR
                mock_ctx.error_message = "Network error calling LLM API"
                mock_create_context.return_value = mock_ctx

                with patch("features.ask.service.load_schema") as mock_load:
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
            with patch("features.ask.service.create_context") as mock_create_context:
                mock_ctx = Mock()
                from features.ask.engine.ask3 import Status

                mock_ctx.status = Status.ERROR
                mock_ctx.error_message = "Authentication failed"
                mock_create_context.return_value = mock_ctx

                with patch("features.ask.service.load_schema") as mock_load:
                    mock_load.return_value = mock_ctx

                    async for event in service.ask(input_data, options):
                        events.append(event)

        assert events[-1].type == "error"


class TestAskServiceDryRunNoSave:
    """Tests that dry-run does NOT auto-save queries to the registry."""

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
    async def test_dry_run_does_not_call_auto_save(
        self, service, input_data, dry_run_options
    ):
        """dry_run=True should NOT call _auto_save_query — query is never executed."""
        events = []

        async def mock_load_config(target):
            return ("test-target", {"engine": "postgresql", "host": "localhost"})

        def fake_load(ctx, p, s):
            from features.ask.engine.ask3.types import SchemaInfo, SchemaSource

            ctx.schema_info = SchemaInfo(
                target="test-target",
                db_type="postgresql",
                source=SchemaSource.DATABASE,
            )
            ctx.schema_info.tables = {"users": Mock()}
            ctx.schema_formatted = "users table"
            return ctx

        def fake_gen(ctx, p, s):
            ctx.sql = "SELECT COUNT(*) FROM users"
            ctx.sql_explanation = "Counts users"
            return ctx

        def fake_val(ctx, p):
            ctx.validation_errors = []
            return ctx

        with (
            patch.object(service, "_load_config", side_effect=mock_load_config),
            patch("features.ask.service.load_schema", side_effect=fake_load),
            patch.object(
                service,
                "_detect_ambiguities",
                side_effect=lambda ctx: (ctx, [], []),
            ),
            patch("features.ask.service.generate_sql", side_effect=fake_gen),
            patch("features.ask.service.validate_sql", side_effect=fake_val),
            patch.object(service, "_auto_save_query") as mock_save,
        ):
            async for event in service.ask(input_data, dry_run_options):
                events.append(event)

            # _auto_save_query must NOT be called in dry-run mode
            mock_save.assert_not_called()

        # Verify we still got a result event
        result_events = [e for e in events if isinstance(e, AskResultEvent)]
        assert len(result_events) == 1
        assert result_events[0].rows == []
        assert result_events[0].execution_time_ms == 0.0


class TestAskServiceTargetNotFoundHint:
    """Tests that target-not-found error includes a helpful hint."""

    @pytest.fixture
    def service(self):
        return AskService()

    @pytest.fixture
    def options(self):
        return AskOptions(dry_run=False, timeout_seconds=30, verbose=False)

    @pytest.mark.asyncio
    async def test_target_not_found_includes_configure_list_hint(
        self, service, options
    ):
        """Error for nonexistent target should include 'rdst configure list' hint."""
        events = []

        input_data = AskInput(
            question="How many orders?",
            target="nonexistent-target",
            source="cli",
        )

        async def mock_load_config(target):
            return ("nonexistent-target", None)

        with patch.object(service, "_load_config", side_effect=mock_load_config):
            async for event in service.ask(input_data, options):
                events.append(event)

        error_events = [e for e in events if isinstance(e, AskErrorEvent)]
        assert len(error_events) == 1
        assert "nonexistent-target" in error_events[0].message
        assert "rdst configure list" in error_events[0].message


class TestAskRendererDryRunMessage:
    """Tests that the renderer shows 'Dry run' for dry-run results."""

    def test_dry_run_result_shows_dry_run_message(self):
        """When result has execution_time_ms=0.0 and empty columns, show 'Dry run' message."""
        from features.ask.engine.ask3.renderer import AskRenderer

        # Create a dry-run result event
        event = AskResultEvent(
            type="result",
            success=True,
            sql="SELECT COUNT(*) FROM users",
            rows=[],
            columns=[],
            row_count=0,
            execution_time_ms=0.0,
            llm_calls=2,
            total_tokens=500,
            query_hash="",
            query_tag="",
        )

        renderer = AskRenderer(verbose=False)

        # Capture console output
        with patch.object(renderer, "_console") as mock_console:
            printed_texts = []

            def capture_print(*args, **kwargs):
                for arg in args:
                    printed_texts.append(str(arg))

            mock_console.print = capture_print
            renderer.render(event)

        combined = " ".join(printed_texts)
        assert "Dry run" in combined, f"Expected 'Dry run' in output, got: {combined!r}"
        assert "No results returned" not in combined, (
            f"'No results returned' should NOT appear for dry-run, got: {combined!r}"
        )

    def test_empty_result_with_execution_time_shows_no_results(self):
        """Non-dry-run empty result (execution_time > 0) should show 'No results returned'."""
        from features.ask.engine.ask3.renderer import AskRenderer

        event = AskResultEvent(
            type="result",
            success=True,
            sql="SELECT * FROM users WHERE id = -1",
            rows=[],
            columns=["id", "name"],
            row_count=0,
            execution_time_ms=12.5,
            llm_calls=1,
            total_tokens=200,
            query_hash="",
            query_tag="",
        )

        renderer = AskRenderer(verbose=False)

        printed_texts = []

        def capture_print(*args, **kwargs):
            for arg in args:
                printed_texts.append(str(arg))

        with patch.object(renderer, "_console") as mock_console:
            mock_console.print = capture_print
            renderer.render(event)

        combined = " ".join(printed_texts)
        assert "No results returned" in combined, (
            f"Expected 'No results returned' for real empty result, got: {combined!r}"
        )
        assert "Dry run" not in combined


class TestAskDryRunMetadataSuppressed:
    """Tests that dry-run mode does NOT print Rows: or Execution time: metadata."""

    def test_dry_run_message_excludes_rows_and_execution_time(self):
        """Bug fix: when dry_run=True, the CLI result message built in
        RdstCLI.ask() should NOT contain 'Rows:' or 'Execution time:'.

        These metadata lines are only meaningful when the query is actually
        executed.
        """
        from unittest.mock import patch

        from features.ask.events import AskResultEvent
        from shared.cli.rdst_cli import RdstCLI

        result_event = AskResultEvent(
            type="result",
            success=True,
            sql="SELECT COUNT(*) FROM users",
            columns=[],
            rows=[],
            row_count=0,
            execution_time_ms=0.0,
            llm_calls=2,
            total_tokens=500,
            query_hash="",
            query_tag="",
        )

        cli = RdstCLI()

        # Mock the service to yield our result event
        async def fake_ask_gen(*args, **kwargs):
            yield result_event

        with (
            patch("features.ask.service.AskService") as MockAskService,
            patch("features.ask.engine.ask3.renderer.AskRenderer") as MockRenderer,
        ):
            mock_service_instance = MockAskService.return_value
            mock_service_instance.ask = fake_ask_gen
            MockRenderer.return_value.render = MagicMock()

            result = cli.ask(
                question="How many users?",
                target="test",
                dry_run=True,
            )

        assert result.ok is True
        assert "Rows:" not in result.message, (
            f"Dry-run message should NOT contain 'Rows:'. Got: {result.message!r}"
        )
        assert "Execution time:" not in result.message, (
            f"Dry-run message should NOT contain 'Execution time:'. Got: {result.message!r}"
        )
        # SQL should still be present
        assert "SELECT COUNT(*) FROM users" in result.message


class TestAskEngineImports:
    """Tests that ask engine imports resolve correctly after broken import fix."""

    def test_ask3_engine_imports_successfully(self):
        """Bug fix: importing Ask3Engine from the engine module should not
        raise ModuleNotFoundError. Previously broken imports prevented
        the ask engine from loading.
        """
        from features.ask.engine.ask3.engine import Ask3Engine

        assert Ask3Engine is not None

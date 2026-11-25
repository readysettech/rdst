"""
Unit tests for AskService.

Tests the text-to-SQL streaming service including event yielding,
clarification handling, and error scenarios.
"""

import asyncio
import json
from dataclasses import replace
from unittest.mock import MagicMock, Mock, patch

import pytest

from features.ask.ambiguity_detection import (
    NON_INTERACTIVE_CLARIFICATION_POLICY,
    RANKED_RESOLVER_POLICY,
    Ambiguity,
    AmbiguityOption,
    AmbiguityReport,
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
    AskClarification,
    AskClarificationQuestion,
    AskInput,
    AskInterpretation,
    AskOptions,
    AskPhase,
)
from features.ask.service import AskService, _with_option_hints

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

    def test_query_timeout_is_not_reported_as_a_connection_failure(self):
        event = AskService._database_error(
            "Query exceeded the 30-second execution limit.",
            AskPhase.EXECUTE,
            "warehouse",
            {"engine": "postgresql"},
            "query_timeout",
        )

        assert event.code == "query_timeout"
        assert event.category == "query_timeout"
        assert "connection" not in event.message.casefold()


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
    async def test_an_answer_asked_again_keeps_its_clarifications(self):
        from features.ask.engine.ask3.types import SchemaInfo, SchemaSource

        service = AskService(session_store={}, persist_queries=False)
        options = AskOptions(dry_run=True, no_interactive=False, persist_query=False)
        ambiguity = Ambiguity(
            id="ranking",
            category="ranking",
            clarifying_question="Rank the best titles by what?",
            possible_interpretations=[
                AmbiguityOption("votes", "most user votes", 0.5),
                AmbiguityOption("rating", "highest average rating", 0.5),
            ],
            term="best",
            reason="best is undefined",
        )
        detections = []
        refined = []

        async def mock_load_config(target):
            return (target, {"engine": "postgresql"})

        def fake_load(ctx, _presenter, _manager):
            ctx.schema_info = SchemaInfo(
                target="test", db_type="postgresql", source=SchemaSource.DATABASE
            )
            ctx.schema_info.tables = {"titles": Mock()}
            ctx.schema_formatted = "titles table"
            return ctx

        def fake_detect(ctx):
            detections.append(ctx.question)
            return ctx, [], [ambiguity]

        def fake_generate(ctx, _presenter, _manager):
            refined.append(ctx.refined_question)
            ctx.sql = "SELECT title FROM titles ORDER BY numvotes DESC"
            ctx.generated_sql = ctx.sql
            return ctx

        with (
            patch.object(service, "_load_config", side_effect=mock_load_config),
            patch("features.ask.service.load_schema", side_effect=fake_load),
            patch.object(service, "_detect_ambiguities", side_effect=fake_detect),
            patch("features.ask.service.generate_sql", side_effect=fake_generate),
            patch(
                "features.ask.service.validate_sql",
                side_effect=lambda ctx, _presenter: ctx,
            ),
        ):
            question = AskInput(question="Show me the best titles", target="test")
            initial = [event async for event in service.ask(question, options)]
            session_id = initial[-1].session_id
            answered = [
                event
                async for event in service.resume(
                    session_id, {"ranking": "most user votes"}
                )
            ][-1]
            again = AskInput(
                question="Show me the best titles",
                target="test",
                clarifications=answered.clarifications,
            )
            asked_again = [event async for event in service.ask(again, options)][-1]

        assert answered.clarifications == [
            AskClarification(
                id="ranking",
                term="best",
                question="Rank the best titles by what?",
                answer="most user votes",
                options=["most user votes", "highest average rating"],
            )
        ]
        assert isinstance(asked_again, AskResultEvent)
        assert asked_again.clarifications == answered.clarifications
        assert detections == ["Show me the best titles"]
        assert len(refined) == 2 and refined[0] == refined[1]
        assert "Answer: most user votes" in refined[1]

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
    async def test_validation_repair_quotes_the_definition_of_a_metric_used_as_column(
        self,
    ):
        from features.ask.engine.ask3.context import Ask3Context
        from features.ask.engine.ask3.types import ValidationError

        service = AskService(persist_queries=False)
        ctx = Ask3Context(question="Top devices by WPR", target="test", dry_run=True)
        ctx.provided_context = "Metrics:\nWPR = wlrangem / 10 * wlsignal"

        def fake_generate(context, _presenter, _manager):
            context.sql = "SELECT d.WPR FROM deviceidentity d"
            return context

        def fake_validate(context, _presenter):
            context.clear_validation_errors()
            if "WPR" in context.sql:
                context.validation_errors.append(
                    ValidationError(column="wpr", table_alias=None, message="Column not found")
                )
            return context

        prompts = []

        def fake_repair(context, _presenter, error_message, _manager):
            prompts.append(error_message)
            context.sql = "SELECT d.wlrangem / 10 * d.wlsignal FROM deviceidentity d"
            return context

        with (
            patch("features.ask.service.generate_sql", side_effect=fake_generate),
            patch("features.ask.service.validate_sql", side_effect=fake_validate),
            patch(
                "features.ask.service.repair_validation_error", side_effect=fake_repair
            ),
        ):
            events = [
                event
                async for event in service._run_from_generate(ctx, persist_query=False)
            ]

        assert isinstance(events[-1], AskResultEvent)
        assert len(prompts) == 1
        assert prompts[0].startswith("SQL Validation Errors:\n- wpr: Column not found")
        assert prompts[0].endswith("\n- WPR = wlrangem / 10 * wlsignal")

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
        assert events[-1].message == (
            "The generated SQL failed validation: Only a single statement is allowed."
        )
        assert events[-1].phase == AskPhase.VALIDATE
        assert events[-1].code == "sql_validation_failed"
        assert events[-1].category == "sql_validation"
        execute.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("sql", "returned", "expected"),
        [
            (
                "SELECT id FROM users",
                101,
                {"limit_added": True, "limit_reduced": False, "truncated": True,
                 "row_limit": 100, "row_count": 100},
            ),
            (
                "SELECT id FROM users",
                40,
                {"limit_added": True, "limit_reduced": False, "truncated": False,
                 "row_limit": 100, "row_count": 40},
            ),
            (
                "SELECT id FROM users LIMIT 5000",
                1001,
                {"limit_added": False, "limit_reduced": True, "truncated": True,
                 "row_limit": 1000, "row_count": 1000},
            ),
            (
                "SELECT id FROM users LIMIT 100",
                100,
                {"limit_added": False, "limit_reduced": False, "truncated": False,
                 "row_limit": 100, "row_count": 100},
            ),
            # Ties past the boundary come back beyond the query's own count; Ask's row
            # cap still cuts the answer.
            (
                "SELECT id FROM users ORDER BY id FETCH FIRST 1000 ROWS WITH TIES",
                1500,
                {"limit_added": False, "limit_reduced": False, "truncated": True,
                 "row_limit": 1000, "row_count": 1000},
            ),
        ],
    )
    async def test_result_reports_the_row_cap(self, sql, returned, expected):
        from features.ask.api.routes import _event_to_sse
        from features.ask.engine.ask3.context import Ask3Context
        from shared.query_registry import hash_sql

        def execute(_sql, _config):
            return {"success": True, "rows": [[1]] * returned, "columns": ["id"]}

        def fake_generate(context, _presenter, _manager):
            context.sql = sql
            return context

        service = AskService(db_executor=execute, persist_queries=False)
        ctx = Ask3Context(
            question="Show user ids", target="test", target_config={"engine": "x"}
        )
        with (
            patch("features.ask.service.generate_sql", side_effect=fake_generate),
            patch(
                "features.ask.service.validate_sql",
                side_effect=lambda context, _presenter: context,
            ),
        ):
            events = [event async for event in service._run_from_generate(ctx)]

        result = events[-1]
        assert isinstance(result, AskResultEvent)
        assert {key: getattr(result, key) for key in expected} == expected
        # Monitoring never saw this statement on the target.
        assert result.monitor_key is None
        payload = json.loads(_event_to_sse(result)["data"])
        assert {key: payload[key] for key in expected if key != "row_count"} == {
            key: value for key, value in expected.items() if key != "row_count"
        }
        assert payload["monitor_key"] == result.monitor_key

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("sql", "executed", "message", "code"),
        [
            # Refused at the execution gate: the agent and escalation paths hand SQL over
            # without the validate phase.
            ("DROP TABLE users", None, "The generated SQL failed validation: ",
             "sql_validation_failed"),
            # Ran but produced no result shape.
            ("SELECT id FROM users", {"success": True, "rows": [], "columns": []},
             "The query didn't return a result.", None),
        ],
    )
    async def test_refused_or_shapeless_execution_ends_in_a_sentence_not_a_table(
        self, sql, executed, message, code
    ):
        from features.ask.engine.ask3.context import Ask3Context

        calls = []
        service = AskService(
            db_executor=lambda run_sql, _config: calls.append(run_sql) or executed,
            persist_queries=False,
        )
        ctx = Ask3Context(question="Q", target="test", target_config={"engine": "x"})

        def fake_generate(context, _presenter, _manager):
            context.sql = sql
            return context

        with (
            patch("features.ask.service.generate_sql", side_effect=fake_generate),
            patch(
                "features.ask.service.validate_sql",
                side_effect=lambda context, _presenter: context,
            ),
        ):
            events = [event async for event in service._run_from_generate(ctx)]

        assert not any(isinstance(event, AskResultEvent) for event in events)
        error = events[-1]
        assert isinstance(error, AskErrorEvent)
        assert error.message.startswith(message) and error.message.endswith(".")
        assert "{" not in error.message and "[" not in error.message
        assert error.code == code
        assert (calls == []) == (executed is None)

    @pytest.mark.asyncio
    async def test_monitor_key_is_set_only_for_a_statement_the_monitor_recorded(
        self, tmp_path, monkeypatch
    ):
        from features.ask.engine.ask3.context import Ask3Context
        from shared.monitor import monitor_store
        from shared.query_registry import hash_sql

        sql = "SELECT id FROM users LIMIT 100"
        store_path = tmp_path / "monitor.db"
        monkeypatch.setattr(monitor_store, "default_monitor_db_path", lambda: store_path)
        store = monitor_store.MonitorStore(store_path)
        store.ensure_open()
        store._write(lambda conn: conn.execute(
            "INSERT INTO query_text VALUES (?,?,?,?,?)", ("watched", hash_sql(sql), sql, 0, 1)
        ))

        async def monitor_key(target):
            service = AskService(
                db_executor=lambda _sql, _config: {
                    "success": True, "rows": [[1]], "columns": ["id"]
                },
                persist_queries=False,
            )
            ctx = Ask3Context(
                question="Show user ids", target=target, target_config={"engine": "x"}
            )

            def fake_generate(context, _presenter, _manager):
                context.sql = sql
                return context

            with (
                patch("features.ask.service.generate_sql", side_effect=fake_generate),
                patch(
                    "features.ask.service.validate_sql",
                    side_effect=lambda context, _presenter: context,
                ),
            ):
                events = [event async for event in service._run_from_generate(ctx)]
            assert isinstance(events[-1], AskResultEvent)
            return events[-1]

        watched = await monitor_key("watched")
        assert watched.monitor_key == hash_sql(sql)
        unwatched = await monitor_key("elsewhere")
        assert unwatched.monitor_key is None

    def test_monitor_key_lookup_tolerates_an_unreadable_store(self, tmp_path, monkeypatch):
        from features.ask.service import _monitored_key
        from shared.monitor import monitor_store

        store_path = tmp_path / "monitor.db"
        store_path.write_bytes(b"not a database")
        monkeypatch.setattr(monitor_store, "default_monitor_db_path", lambda: store_path)

        assert _monitored_key("db", "SELECT 1") is None
        assert _monitored_key("db", "") is None

    def test_monitor_key_matches_captured_statement_text(self):
        from features.ask.service import _monitor_key
        from shared.query_registry import hash_sql

        executed = "SELECT primarytitle FROM title_basics WHERE titletype = 'movie' LIMIT 100"
        assert _monitor_key(executed) == hash_sql(
            "SELECT primarytitle FROM title_basics WHERE titletype = $1 LIMIT $2"
        )

    def test_monitor_key_is_the_statement_ask_ran_on_the_target(self):
        from features.ask.service import _monitor_key
        from shared.query_registry import hash_sql

        # The target runs Ask's capped SQL with its one-row probe, never the SQL without
        # Ask's LIMIT; the LIMIT count is a placeholder in the key.
        answered = "SELECT id FROM users WHERE active LIMIT 100"
        assert _monitor_key(answered) == hash_sql("SELECT id FROM users WHERE active LIMIT 101")
        assert _monitor_key(answered) != hash_sql("SELECT id FROM users WHERE active")

    @pytest.mark.asyncio
    async def test_a_slow_monitor_store_does_not_hold_up_the_result(self, monkeypatch):
        import threading
        import time

        from features.ask import service as service_module
        from features.ask.engine.ask3.context import Ask3Context

        released = threading.Event()

        def slow_lookup(target, sql):
            released.wait(5)
            return "key"

        monkeypatch.setattr(service_module, "_monitored_key", slow_lookup)
        # The lookup must run off the event loop for the answer to go out without it.
        monkeypatch.setattr(asyncio, "to_thread", asyncio.threads.to_thread)
        service = AskService(
            db_executor=lambda _sql, _config: {
                "success": True, "rows": [[1]], "columns": ["id"]
            },
            persist_queries=False,
        )
        ctx = Ask3Context(question="Q", target="db", target_config={"engine": "x"})

        def fake_generate(context, _presenter, _manager):
            context.sql = "SELECT id FROM users"
            return context

        started = time.monotonic()
        try:
            with (
                patch("features.ask.service.generate_sql", side_effect=fake_generate),
                patch(
                    "features.ask.service.validate_sql",
                    side_effect=lambda context, _presenter: context,
                ),
            ):
                events = [event async for event in service._run_from_generate(ctx)]
        finally:
            released.set()

        assert isinstance(events[-1], AskResultEvent)
        assert events[-1].monitor_key is None
        assert time.monotonic() - started < 1.5

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

    @pytest.mark.asyncio
    async def test_resume_refuses_a_different_database(self):
        from features.ask.engine.ask3.context import Ask3Context
        from features.ask.service import _PendingAskSession

        sessions = {
            "pending": _PendingAskSession(
                context=Ask3Context(question="question", target="imdb"),
                persist_query=False,
                clarification_context={"q1": {}},
            )
        }
        service = AskService(session_store=sessions)

        with patch.object(service, "_run_from_generate") as run:
            events = [
                event
                async for event in service.resume("pending", {"q1": "a"}, target="tpcds")
            ]

        [error] = events
        assert error.code == "clarification_target_mismatch"
        assert error.message == (
            "This clarification belongs to imdb. Ask again on tpcds."
        )
        run.assert_not_called()
        assert "pending" in sessions

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
        sessions = {
            "pending": _PendingAskSession(
                context=ctx,
                persist_query=False,
                clarification_context={
                    "status": {
                        "ambiguity_id": "status",
                        "term": "active",
                        "question": "What does active mean?",
                    }
                },
            )
        }
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
            "Show active users\n\nResolved user clarifications:\n"
            "- active: What does active mean? Answer: active means enabled = 1"
        )
        assert ctx.clarification_resolutions == [
            {
                "ambiguity_id": "status",
                "answer_key": "status",
                "term": "active",
                "question": "What does active mean?",
                "action": "answer",
                "answer": "active means enabled = 1",
                "answer_type": "choice",
                "origin": "detector",
                "source": "user",
                "applied": True,
                "offered": {},
                "minor": False,
            }
        ]
        assert sessions == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("enabled", "answers", "draft_question"),
        [(True, {"status": "enabled = 1"}, "Show active users"), (True, {}, ""),
         (False, {"status": "enabled = 1"}, "")],
    )
    async def test_resume_drafts_from_the_question_before_the_answers(
        self, enabled, answers, draft_question
    ):
        from features.ask.engine.ask3.context import Ask3Context
        from features.ask.service import _PendingAskSession

        ctx = Ask3Context(question="Show active users", target="test")
        context = {"status": {"ambiguity_id": "status", "term": "active",
                              "question": "What does active mean?"}}
        service = AskService(
            session_store={"pending": _PendingAskSession(
                context=ctx, persist_query=False, clarification_context=context)},
            clarification_draft_edit_enabled=enabled,
        )
        seen = []

        async def fake_run(resumed_ctx, *, persist_query=True):
            seen.append(resumed_ctx.clarification_draft_question)
            if False:
                yield None

        with patch.object(service, "_run_from_generate", side_effect=fake_run):
            _ = [event async for event in service.resume("pending", answers)]

        assert seen == [draft_question]

    @pytest.mark.asyncio
    async def test_resume_rejects_unknown_answer_key_without_consuming_session(self):
        from features.ask.engine.ask3.context import Ask3Context
        from features.ask.service import _PendingAskSession

        sessions = {
            "pending": _PendingAskSession(
                context=Ask3Context(question="Show users", target="test"),
                persist_query=False,
                clarification_context={
                    "status": {
                        "ambiguity_id": "status",
                        "term": "active",
                        "question": "What does active mean?",
                    }
                },
            )
        }
        service = AskService(session_store=sessions)

        events = [
            event async for event in service.resume("pending", {"unknown": "enabled"})
        ]

        assert isinstance(events[-1], AskErrorEvent)
        assert events[-1].code == "invalid_clarification_answer"
        assert "pending" in sessions

    @pytest.mark.asyncio
    async def test_resume_records_skipped_questions_without_inventing_answers(self):
        from features.ask.engine.ask3.context import Ask3Context
        from features.ask.service import _PendingAskSession

        ctx = Ask3Context(question="Show active users", target="test")
        sessions = {
            "pending": _PendingAskSession(
                context=ctx,
                persist_query=False,
                clarification_context={
                    "status": {
                        "ambiguity_id": "status",
                        "term": "active",
                        "question": "What does active mean?",
                    },
                    "window": {
                        "ambiguity_id": "window",
                        "term": "recent",
                        "question": "What time window should recent mean?",
                    },
                },
            )
        }
        service = AskService(session_store=sessions)

        async def fake_run(_ctx, *, persist_query=True):
            assert persist_query is False
            if False:
                yield None

        with patch.object(service, "_run_from_generate", side_effect=fake_run):
            events = [
                event
                async for event in service.resume(
                    "pending", {"status": "Use enabled accounts."}
                )
            ]

        assert events == []
        assert ctx.clarifications == {"status": "Use enabled accounts."}
        assert [
            (item["ambiguity_id"], item["action"], item["applied"])
            for item in ctx.clarification_resolutions
        ] == [
            ("status", "answer", True),
            ("window", "skip", False),
        ]
        assert "Use enabled accounts." in ctx.refined_question
        assert "time window" not in ctx.refined_question


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
            "error_code": None,
            "fallback": "fail_closed",
        }
        assert ctx.clarification_policy == RANKED_RESOLVER_POLICY
        ctx.mark_error.assert_called_once_with(
            "Failed to analyze whether the question requires clarification",
            code=None,
            category=None,
        )

    @pytest.mark.parametrize("code", ["PROMPT_NOT_ALLOWED", "OPERATION_LIMIT_EXCEEDED"])
    def test_a_detector_request_keyservice_does_not_admit_proceeds(self, code):
        service = AskService(llm_manager=Mock())
        ctx = Mock(question="Which users?", schema_formatted="users table",
                   db_type="postgresql", provided_context="", no_interactive=False)

        with patch(
            "features.ask.service.detect_ambiguities",
            return_value={"success": False, "error": "not admitted", "error_code": code},
        ):
            _, interpretations, ambiguities = service._detect_ambiguities(ctx)

        assert (interpretations, ambiguities) == ([], [])
        assert ctx.ambiguity_report["fallback"] == "proceed_without_clarification"
        ctx.mark_error.assert_not_called()

    @pytest.mark.parametrize("compact", [False, True])
    def test_ambiguity_detection_requests_the_configured_reply(self, compact):
        service = AskService(llm_manager=Mock(), compact_clarification_enabled=compact)
        ctx = Mock(question="Which users?", schema_formatted="users", db_type="mysql")

        with patch(
            "features.ask.service.detect_ambiguities",
            return_value={"success": True, "report": None},
        ) as detect:
            service._detect_ambiguities(ctx)

        assert detect.call_args.kwargs["compact"] is compact

    @pytest.mark.parametrize("refuses", [False, True])
    def test_clarification_draft_is_reviewed_by_the_detector(self, refuses):
        from features.ask.engine.ask3.context import Ask3Context
        from features.ask.engine.ask3.types import Status

        service = AskService(llm_manager=Mock(), clarification_after_draft_enabled=True)
        ctx = Ask3Context(question="Which users?", target="t", schema_formatted="users")

        def fake_generate(gen_ctx, presenter, llm_manager):
            if refuses:
                gen_ctx.mark_error("cannot answer", code="ask_cannot_answer")
                gen_ctx.missing_schema = ["users"]
            else:
                gen_ctx.sql = gen_ctx.generated_sql = "SELECT * FROM users"
            return gen_ctx

        with patch("features.ask.service.generate_sql", side_effect=fake_generate):
            service._write_clarification_draft(ctx)
        with patch("features.ask.service.detect_ambiguities",
                   return_value={"success": True, "report": None}) as detect:
            service._detect_ambiguities(ctx)

        assert ctx.clarification_draft_ready is not refuses
        assert ctx.clarification_draft_failed is refuses
        assert detect.call_args.kwargs["draft_sql"] == ("" if refuses else "SELECT * FROM users")
        assert ctx.status != Status.ERROR
        assert (ctx.sql, ctx.missing_schema) == (
            (None, []) if refuses else ("SELECT * FROM users", []))

    def test_the_draft_keeps_the_compact_schema_for_the_revision(self):
        from features.ask.engine.ask3.context import Ask3Context

        service = AskService(llm_manager=Mock(), clarification_after_draft_enabled=True)
        ctx = Ask3Context(question="Which users?", target="t", schema_formatted="users",
                          schema_compact_fallback="u")

        def fake_generate(gen_ctx, presenter, llm_manager):
            gen_ctx.sql = gen_ctx.generated_sql = "SELECT * FROM users"
            gen_ctx.schema_compact_fallback = ""
            return gen_ctx

        with patch("features.ask.service.generate_sql", side_effect=fake_generate):
            service._write_clarification_draft(ctx)

        assert ctx.schema_compact_fallback == "u"

    @pytest.mark.parametrize(
        ("ceiling", "confidence", "asks"),
        [(None, 0.9, True), (0.55, 0.55, True), (0.55, 0.6, False)],
    )
    def test_clarification_confidence_ceiling_gates_asking(self, ceiling, confidence, asks):
        service = AskService(
            llm_manager=Mock(), clarification_confidence_ceiling=ceiling
        )
        ctx = Mock(
            question="Which users?", schema_formatted="users", db_type="mysql",
            answer_first=False,
        )
        ambiguity = Ambiguity(
            id="status",
            category="status",
            clarifying_question="What does active mean?",
            possible_interpretations=[
                AmbiguityOption("enabled", "enabled = 1", 0.6),
                AmbiguityOption("recent", "last_seen is recent", 0.4),
            ],
            term="active",
            reason="active is undefined",
        )
        report = AmbiguityReport(
            ambiguities=[ambiguity],
            total_ambiguities=1,
            requires_clarification=True,
            overall_confidence=confidence,
        )

        with patch(
            "features.ask.service.detect_ambiguities",
            return_value={"success": True, "report": report},
        ):
            _, _, ambiguities = service._detect_ambiguities(ctx)

        assert ambiguities == ([ambiguity] if asks else [])
        assert ("confidence_ceiling_proceeded" in ctx.ambiguity_report) is not asks

    def test_unparseable_detector_reply_proceeds_without_clarification(self):
        service = AskService(llm_manager=Mock())
        ctx = Mock(
            question="Which users?",
            schema_formatted="users table",
            db_type="postgresql",
            provided_context="",
            no_interactive=False,
        )

        with patch(
            "features.ask.service.detect_ambiguities",
            return_value={
                "success": False,
                "error": "Invalid JSON from LLM: Expecting value",
                "parse_failure": True,
                "raw_response": "not json",
            },
        ):
            returned_ctx, interpretations, ambiguities = service._detect_ambiguities(
                ctx
            )

        assert (returned_ctx, interpretations, ambiguities) == (ctx, [], [])
        assert ctx.ambiguity_report == {
            "error": "Invalid JSON from LLM: Expecting value",
            "error_code": None,
            "fallback": "proceed_without_clarification",
        }
        ctx.mark_error.assert_not_called()

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
            "status-1",
            "status-2",
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

    def test_truncated_result_names_the_row_cap(self):
        from features.ask.engine.ask3.renderer import AskRenderer

        event = AskResultEvent(
            type="result",
            success=True,
            sql="SELECT id FROM users LIMIT 100",
            rows=[[1]],
            columns=["id"],
            row_count=1,
            execution_time_ms=1.0,
            llm_calls=1,
            total_tokens=1,
            limit_added=True,
            truncated=True,
            row_limit=100,
        )
        renderer = AskRenderer(verbose=False)
        with patch.object(renderer, "_console") as console:
            renderer.render(event)

        printed = " ".join(str(call.args[0]) for call in console.print.call_args_list)
        assert "Showing the first 100 rows" in printed

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
            patch("shared.cli.ai_access.ensure_cli_ai_access") as mock_ai_access,
        ):
            mock_ai_access.return_value.ok = True
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


def _fake_schema_load(ctx, _presenter, _manager):
    from features.ask.engine.ask3.types import SchemaInfo, SchemaSource

    ctx.schema_info = SchemaInfo(
        target="test", db_type="postgresql", source=SchemaSource.DATABASE
    )
    ctx.schema_info.tables = {"lab": Mock()}
    return ctx


async def _load_test_config(target):
    return (target, {"engine": "postgresql"})


@pytest.mark.asyncio
async def test_open_question_is_asked_without_options_and_resumes_with_text():
    sessions = {}
    service = AskService(session_store=sessions, persist_queries=False)
    cutoff = Ambiguity(
        id="glucose-cutoff",
        category="unclear_value_reference",
        term="high glucose",
        reason="No stated cutoff defines high glucose.",
        clarifying_question="What glucose level counts as high?",
        possible_interpretations=[],
        answer_type="value",
    )
    generated = []

    def fake_generate(ctx, _presenter, _manager):
        generated.append(ctx.refined_question)
        ctx.sql = "SELECT COUNT(*) FROM lab WHERE glu > 180"
        ctx.generated_sql = ctx.sql
        return ctx

    with (
        patch.object(service, "_load_config", side_effect=_load_test_config),
        patch("features.ask.service.load_schema", side_effect=_fake_schema_load),
        patch.object(
            service, "_detect_ambiguities", side_effect=lambda ctx: (ctx, [], [cutoff])
        ),
        patch("features.ask.service.generate_sql", side_effect=fake_generate),
        patch(
            "features.ask.service.validate_sql", side_effect=lambda ctx, _presenter: ctx
        ),
    ):
        events = [
            event
            async for event in service.ask(
                AskInput(question="How many patients have high glucose?", target="t"),
                AskOptions(no_interactive=False, dry_run=True, persist_query=False),
            )
        ]
        clarification = events[-1]
        assert isinstance(clarification, AskClarificationNeededEvent)
        [question] = clarification.questions
        assert question.options == []
        resumed = [
            event
            async for event in service.resume(
                clarification.session_id, {question.id: "Above 180 mg/dL"}
            )
        ]

    assert isinstance(resumed[-1], AskResultEvent)
    assert "Above 180 mg/dL" in generated[0]


@pytest.mark.asyncio
async def test_validation_repair_quotes_the_answers_definition_not_the_questions_filter():
    from features.ask.engine.ask3.types import ValidationError

    service = AskService(session_store={}, persist_queries=False)
    wpr = Ambiguity(
        id="wpr",
        category="unclear_knowledge_source",
        term="WPR",
        reason="No definition of WPR is given.",
        clarifying_question="How is WPR computed?",
        possible_interpretations=[],
        answer_type="definition",
    )
    validations = []
    repairs = []

    def fake_generate(ctx, _presenter, _manager):
        ctx.sql = ctx.generated_sql = "SELECT wpr FROM devices WHERE category = 'toys'"
        return ctx

    def fake_validate(ctx, _presenter):
        validations.append(ctx.sql)
        ctx.clear_validation_errors()
        if len(validations) == 1:
            for name in ("wpr", "category"):
                ctx.validation_errors.append(ValidationError(
                    column=name, table_alias=None, message=f"Column '{name}' not found"))
        return ctx

    def fake_repair(ctx, _presenter, message, _manager):
        repairs.append(message)
        return ctx

    with (
        patch.object(service, "_load_config", side_effect=_load_test_config),
        patch("features.ask.service.load_schema", side_effect=_fake_schema_load),
        patch.object(service, "_detect_ambiguities", side_effect=lambda ctx: (ctx, [], [wpr])),
        patch("features.ask.service.generate_sql", side_effect=fake_generate),
        patch("features.ask.service.validate_sql", side_effect=fake_validate),
        patch("features.ask.service.repair_validation_error", side_effect=fake_repair),
    ):
        events = [
            event
            async for event in service.ask(
                AskInput(question="Show devices where category = 'toys' by WPR", target="t"),
                AskOptions(no_interactive=False, dry_run=True, persist_query=False),
            )
        ]
        clarification = events[-1]
        [question] = clarification.questions
        [event async for event in service.resume(
            clarification.session_id, {question.id: "WPR = wlrangem / 10 * wlsignal"})]

    [message] = repairs
    assert "WPR = wlrangem / 10 * wlsignal" in message
    assert "category = 'toys'" not in message


async def _writer_dialogue(
    refusals, answer=None, *, no_interactive=False, missing=("elderly",), best_effort=None
):
    """Ask a question whose SQL writer cannot find ``missing`` for the first ``refusals``
    calls, and answer each question Ask raises with ``answer``. ``best_effort`` collects
    whether each call was told to approximate instead of refusing."""
    generated = []

    def fake_generate(ctx, _presenter, _manager):
        generated.append(ctx.refined_question or ctx.question)
        if best_effort is not None:
            best_effort.append(ctx.best_effort)
        if len(generated) <= refusals:
            ctx.missing_schema = list(missing)
            ctx.missing_schema_suggestions = ["Older than 65", "65 or older"]
            ctx.mark_error(
                "No column defines elderly.",
                code="ask_cannot_answer",
                category="missing_schema",
            )
            return ctx
        ctx.sql = "SELECT COUNT(*) FROM lab WHERE age > 65"
        ctx.generated_sql = ctx.sql
        return ctx

    service = AskService(session_store={}, persist_queries=False)
    rounds = []
    with (
        patch.object(service, "_load_config", side_effect=_load_test_config),
        patch("features.ask.service.load_schema", side_effect=_fake_schema_load),
        patch.object(
            service, "_detect_ambiguities", side_effect=lambda ctx: (ctx, [], [])
        ),
        patch("features.ask.service.generate_sql", side_effect=fake_generate),
        patch(
            "features.ask.service.validate_sql", side_effect=lambda ctx, _presenter: ctx
        ),
    ):
        events = [
            event
            async for event in service.ask(
                AskInput(question="How many elderly patients are there?", target="t"),
                AskOptions(
                    no_interactive=no_interactive, dry_run=True, persist_query=False
                ),
            )
        ]
        rounds.append(events)
        while isinstance(events[-1], AskClarificationNeededEvent) and len(rounds) < 4:
            clarification = events[-1]
            answers = {q.id: answer for q in clarification.questions if answer}
            events = [
                event
                async for event in service.resume(clarification.session_id, answers)
            ]
            rounds.append(events)
    return rounds, generated


@pytest.mark.asyncio
async def test_writer_refusal_asks_about_the_missing_concept_and_retries():
    rounds, generated = await _writer_dialogue(refusals=1, answer="Older than 65")

    asked, answered = rounds
    assert not any(isinstance(event, AskErrorEvent) for event in asked)
    [question] = asked[-1].questions
    assert question.options == []
    assert "elderly" in question.question
    assert isinstance(answered[-1], AskResultEvent)
    assert "- elderly: " in generated[-1] and "Answer: Older than 65" in generated[-1]


@pytest.mark.asyncio
async def test_writer_question_suggests_answers_and_the_answer_is_worked_out():
    best_effort = []
    rounds, _ = await _writer_dialogue(
        refusals=1, answer="Older than 65", best_effort=best_effort
    )

    [question] = rounds[0][-1].questions
    assert question.examples == ["Older than 65", "65 or older"]
    # The writer may refuse before the user is asked, but not after they answered.
    assert best_effort == [False, True]


@pytest.mark.asyncio
async def test_writer_refusal_names_every_missing_concept_in_one_question():
    missing = (
        "weather forecast data",
        "weather conditions for Monaco",
        "Weather forecast data",
        "forecast date for tomorrow",
        "forecast provider",
    )
    rounds, generated = await _writer_dialogue(
        refusals=2, answer="There is none", missing=missing
    )

    asked, refused = rounds
    [question] = asked[-1].questions
    assert question.question == (
        "I couldn't find weather forecast data, weather conditions for Monaco or forecast "
        "date for tomorrow in this database. Where are they recorded, or how should I "
        "work them out?"
    )
    assert "Answer: There is none" in generated[-1]
    error = refused[-1]
    assert isinstance(error, AskErrorEvent)
    assert error.code == "ask_cannot_answer"


@pytest.mark.asyncio
async def test_writer_refusal_is_asked_about_once():
    rounds, generated = await _writer_dialogue(refusals=2, answer="Older than 65")

    assert len(rounds) == 2 and len(generated) == 2
    error = rounds[-1][-1]
    assert isinstance(error, AskErrorEvent)
    assert error.code == "ask_cannot_answer"


@pytest.mark.asyncio
async def test_unanswered_writer_question_keeps_the_refusal():
    rounds, generated = await _writer_dialogue(refusals=1, answer=None)

    assert len(generated) == 1
    error = rounds[-1][-1]
    assert isinstance(error, AskErrorEvent)
    assert (error.code, error.category) == ("ask_cannot_answer", "missing_schema")


@pytest.mark.asyncio
async def test_non_interactive_writer_refusal_is_an_error():
    rounds, generated = await _writer_dialogue(refusals=1, no_interactive=True)

    assert len(rounds) == 1 and len(generated) == 1
    assert isinstance(rounds[0][-1], AskErrorEvent)


def _customer_id_ambiguity():
    return Ambiguity(
        id="customer",
        category="unclear_schema_reference",
        term="ID",
        reason="Two tables hold a customer reference.",
        clarifying_question="Which ID should identify each customer?",
        possible_interpretations=[
            AmbiguityOption(
                "main", "Customer number on the main record", 0.5, hint="core_record.clientref"
            ),
            AmbiguityOption(
                "income",
                "Client reference on the income record",
                0.5,
                hint="employment_and_income.emplcoreref->ref",
            ),
        ],
    )


def test_clarification_options_show_schema_hints_with_an_example_value():
    from features.ask.engine.ask3.context import Ask3Context
    from features.ask.engine.ask3.types import ColumnInfo, SchemaInfo, TableInfo

    queries = []

    def db_executor(sql, _config):
        queries.append(sql)
        return {"success": True, "rows": [["CU241436"]], "columns": ["clientref"]}

    service = AskService(persist_queries=False, db_executor=db_executor)
    ctx = Ask3Context(question="Show each customer's ID", target="credit", dry_run=False)
    ctx.target_config = {"engine": "postgresql"}
    ctx.db_type = "postgresql"
    ctx.schema_info = SchemaInfo(
        target="credit",
        db_type="postgresql",
        tables={
            "core_record": TableInfo(
                name="core_record", columns={"clientref": ColumnInfo("clientref", "text")}
            )
        },
    )

    [question] = service._clarification_questions(ctx, [_customer_id_ambiguity()])

    assert question.options == [
        "Customer number on the main record",
        "Client reference on the income record",
    ]
    # Only a hint that names a schema column is read; the JSON path is shown as written.
    assert question.hints == [
        "core_record.clientref, e.g. CU241436",
        "employment_and_income.emplcoreref->ref",
    ]
    assert len(queries) == 1 and '"clientref"' in queries[0]


def test_a_json_document_is_no_example_value():
    from features.ask.engine.ask3.context import Ask3Context
    from features.ask.engine.ask3.types import ColumnInfo, SchemaInfo, TableInfo

    def db_executor(_sql, _config):
        return {"success": True, "rows": [['{"autopay": "Yes"}']], "columns": ["doc"]}

    service = AskService(persist_queries=False, db_executor=db_executor)
    ctx = Ask3Context(question="Who invests?", target="credit", dry_run=False)
    ctx.target_config = {"engine": "postgresql"}
    ctx.db_type = "postgresql"
    ctx.schema_info = SchemaInfo(
        target="credit",
        db_type="postgresql",
        tables={"bank": TableInfo(name="bank", columns={"doc": ColumnInfo("doc", "jsonb")})},
    )
    ambiguity = Ambiguity(
        id="focus",
        category="unclear_value_reference",
        term="investment focus",
        reason="Several signals.",
        clarifying_question="How should we decide who invests?",
        possible_interpretations=[
            AmbiguityOption("portfolio", "A recorded portfolio profile", 0.6, hint="bank.doc"),
            AmbiguityOption("amount", "A positive amount", 0.4),
        ],
    )

    [question] = service._clarification_questions(ctx, [ambiguity])

    assert question.hints == ["bank.doc", ""]


@pytest.mark.asyncio
async def test_a_chosen_option_gives_the_writer_its_schema_hint():
    from features.ask.engine.ask3.types import SchemaInfo, SchemaSource

    service = AskService(session_store={}, persist_queries=False)
    options = AskOptions(dry_run=True, no_interactive=False, persist_query=False)
    refined = []

    async def mock_load_config(target):
        return (target, {"engine": "postgresql"})

    def fake_load(ctx, _presenter, _manager):
        ctx.schema_info = SchemaInfo(
            target="credit", db_type="postgresql", source=SchemaSource.DATABASE
        )
        ctx.schema_info.tables = {"core_record": Mock()}
        ctx.schema_formatted = "core_record table"
        return ctx

    def fake_generate(ctx, _presenter, _manager):
        refined.append(ctx.refined_question)
        ctx.sql = "SELECT clientref FROM core_record"
        ctx.generated_sql = ctx.sql
        return ctx

    with (
        patch.object(service, "_load_config", side_effect=mock_load_config),
        patch("features.ask.service.load_schema", side_effect=fake_load),
        patch.object(
            service,
            "_detect_ambiguities",
            side_effect=lambda ctx: (ctx, [], [_customer_id_ambiguity()]),
        ),
        patch("features.ask.service.generate_sql", side_effect=fake_generate),
        patch("features.ask.service.validate_sql", side_effect=lambda ctx, _p: ctx),
    ):
        question = AskInput(question="Show each customer's ID", target="credit")
        initial = [event async for event in service.ask(question, options)]
        clarification = initial[-1]
        assert clarification.questions[0].hints == [
            "core_record.clientref",
            "employment_and_income.emplcoreref->ref",
        ]
        result = [
            event
            async for event in service.resume(
                clarification.session_id,
                {"customer": "Customer number on the main record"},
            )
        ][-1]

    assert isinstance(result, AskResultEvent)
    assert (
        "Answer: Customer number on the main record (core_record.clientref)" in refined[0]
    )
    assert result.clarifications[0].answer == "Customer number on the main record"


def test_each_chosen_option_carries_its_schema_hint():
    hints = {"Average DTI": "ei.debincratio", "Loan amount": "ca.totloanamt"}

    assert _with_option_hints("Average DTI", hints) == "Average DTI (ei.debincratio)"
    assert (
        _with_option_hints("Average DTI; Median DTI; Loan amount", hints)
        == "Average DTI (ei.debincratio); Median DTI; Loan amount (ca.totloanamt)"
    )
    assert _with_option_hints("Debt over income; but capped", hints) == (
        "Debt over income; but capped"
    )


async def _answer_first(
    ambiguities,
    *,
    refusals=0,
    clarifications=(),
    sql="SELECT ei.debincratio FROM ei",
    derived=(),
):
    """Ask with answer_first set: the detector finds ``ambiguities`` and the SQL writer
    refuses ``refusals`` times, then writes ``sql`` and reports the measures it
    ``derived``. Returns the events, each prompt the writer saw, whether it was told to
    approximate, and how often detection ran."""
    sessions = {}
    service = AskService(session_store=sessions, persist_queries=False)
    generated, best_effort, detections = [], [], []

    def fake_detect(ctx):
        detections.append(ctx.question)
        return ctx, [], list(ambiguities)

    def fake_generate(ctx, _presenter, _manager):
        generated.append(ctx.refined_question or ctx.question)
        best_effort.append(ctx.best_effort)
        if len(generated) <= refusals:
            ctx.missing_schema = ["housing costs"]
            ctx.missing_schema_suggestions = ["Mortgage balance divided by 12", "Rent"]
            ctx.mark_error(
                "No housing costs.", code="ask_cannot_answer", category="missing_schema"
            )
            return ctx
        ctx.sql = ctx.generated_sql = sql
        ctx.derived_measures = [dict(item) for item in derived]
        return ctx

    with (
        patch.object(service, "_load_config", side_effect=_load_test_config),
        patch("features.ask.service.load_schema", side_effect=_fake_schema_load),
        patch.object(service, "_detect_ambiguities", side_effect=fake_detect),
        patch("features.ask.service.generate_sql", side_effect=fake_generate),
        patch("features.ask.service.validate_sql", side_effect=lambda ctx, _p: ctx),
    ):
        events = [
            event
            async for event in service.ask(
                AskInput(
                    question="Rank customers by debt",
                    target="t",
                    clarifications=list(clarifications),
                ),
                AskOptions(
                    no_interactive=False, dry_run=True, persist_query=False,
                    answer_first=True,
                ),
            )
        ]
    assert sessions == {}
    return events, generated, best_effort, detections


def _ranking_ambiguities():
    return [
        Ambiguity(
            id="ranking",
            category="ranking",
            term="debt",
            reason="Several debt measures.",
            clarifying_question="Rank by which debt measure?",
            possible_interpretations=[
                AmbiguityOption("loans", "Total loan amount", 0.4, hint="ca.totloanamt"),
                AmbiguityOption("dti", "Debt-to-income ratio", 0.7, hint="ei.debincratio"),
            ],
        ),
        Ambiguity(
            id="measures",
            category="output",
            term="debt metrics",
            reason="Several metrics.",
            clarifying_question="Which measures should be shown?",
            possible_interpretations=[
                AmbiguityOption("a", "Average DTI", 0.8),
                AmbiguityOption("b", "Median DTI", 0.5),
                AmbiguityOption("c", "Loan amount", 0.2),
            ],
            answer_type="choices",
        ),
        Ambiguity(
            id="top",
            category="count",
            term="top",
            reason="No count.",
            clarifying_question="How many customers?",
            possible_interpretations=[],
            answer_type="value",
            answer_examples=["10", "25"],
        ),
        Ambiguity(
            id="segment",
            category="output",
            term="segment",
            reason="No rule.",
            clarifying_question="What makes a segment small?",
            possible_interpretations=[],
            answer_type="value",
        ),
    ]


@pytest.mark.asyncio
async def test_answer_first_answers_with_its_best_guesses_and_lists_them():
    events, generated, _, _ = await _answer_first(_ranking_ambiguities())

    assert not any(isinstance(event, AskClarificationNeededEvent) for event in events)
    result = events[-1]
    assert isinstance(result, AskResultEvent)
    assert [(item.id, item.answer, item.assumed) for item in result.clarifications] == [
        ("ranking", "Debt-to-income ratio", True),
        ("measures", "Average DTI; Median DTI", True),
        ("top", "10", True),
        # Nothing to choose: the SQL writer decides, and the user can still say.
        ("segment", "", True),
    ]
    ranking = result.clarifications[0]
    assert ranking.options == ["Total loan amount", "Debt-to-income ratio"]
    assert ranking.hints == ["ca.totloanamt", "ei.debincratio"]
    assert result.clarifications[1].multiple is True
    assert result.clarifications[2].examples == ["10", "25"]
    assert "Answer: Debt-to-income ratio (ei.debincratio)" in generated[0]
    assert "What makes a segment small?" not in generated[0]


@pytest.mark.asyncio
async def test_answer_first_leaves_out_a_guess_the_sql_does_not_read():
    events, _, _, _ = await _answer_first(_ranking_ambiguities(), sql="SELECT 1")

    # No reading of the debt measure is in the SQL, so that guess did not shape it.
    assert [item.id for item in events[-1].clarifications] == ["measures", "top", "segment"]


@pytest.mark.asyncio
async def test_answer_first_lists_the_measures_the_writer_derived():
    events, _, _, _ = await _answer_first(
        _ranking_ambiguities()[:1],
        derived=[
            {
                "term": "total debt service ratio",
                "used": "Total liabilities divided by annual income",
                "alternatives": ["Mortgage balance divided by 12, over monthly income"],
            },
            # The detector already asked about this term.
            {"term": "Debt", "used": "Total loan amount", "alternatives": []},
        ],
    )

    ranking, derived = events[-1].clarifications
    assert ranking.id == "ranking"
    assert (derived.id, derived.question, derived.answer, derived.assumed) == (
        "derived-total-debt-service-ratio",
        "How should Ask work out total debt service ratio?",
        "Total liabilities divided by annual income",
        True,
    )
    assert derived.options == [
        "Total liabilities divided by annual income",
        "Mortgage balance divided by 12, over monthly income",
    ]


@pytest.mark.asyncio
async def test_answer_first_lists_points_that_only_change_what_shows_last():
    ambiguities = _ranking_ambiguities()[:3]
    ambiguities[0].priority = "low"

    events, _, _, _ = await _answer_first(ambiguities)

    assert [(item.id, item.minor) for item in events[-1].clarifications] == [
        ("measures", False),
        ("top", False),
        ("ranking", True),
    ]


@pytest.mark.asyncio
async def test_answer_first_works_a_missing_concept_out_as_the_writer_suggested():
    events, generated, best_effort, _ = await _answer_first([], refusals=1)

    result = events[-1]
    assert isinstance(result, AskResultEvent)
    [missing] = result.clarifications
    assert (missing.id, missing.answer, missing.assumed) == (
        "missing-1", "Mortgage balance divided by 12", True,
    )
    assert missing.question == "How should Ask work out housing costs?"
    assert missing.examples == ["Mortgage balance divided by 12", "Rent"]
    assert "Answer: Mortgage balance divided by 12" in generated[-1]
    assert best_effort == [False, True]


@pytest.mark.asyncio
async def test_answer_first_asks_when_there_is_no_way_to_work_it_out():
    sessions = {}
    service = AskService(session_store=sessions, persist_queries=False)

    def refuse(ctx, _presenter, _manager):
        ctx.missing_schema = ["weather"]
        ctx.mark_error("No weather.", code="ask_cannot_answer", category="missing_schema")
        return ctx

    with (
        patch.object(service, "_load_config", side_effect=_load_test_config),
        patch("features.ask.service.load_schema", side_effect=_fake_schema_load),
        patch.object(service, "_detect_ambiguities", side_effect=lambda ctx: (ctx, [], [])),
        patch("features.ask.service.generate_sql", side_effect=refuse),
    ):
        events = [
            event
            async for event in service.ask(
                AskInput(question="Weather in Monaco?", target="t"),
                AskOptions(dry_run=True, persist_query=False, answer_first=True),
            )
        ]

    assert isinstance(events[-1], AskClarificationNeededEvent)
    [question] = events[-1].questions
    assert "I couldn't find weather" in question.question
    assert len(sessions) == 1


@pytest.mark.asyncio
async def test_a_changed_assumption_runs_again_without_asking_and_keeps_the_rest():
    first, _, _, _ = await _answer_first(_ranking_ambiguities())
    changed = [
        replace(item, answer="Total loan amount", assumed=False)
        if item.id == "ranking"
        else item
        for item in first[-1].clarifications
    ]

    events, generated, best_effort, detections = await _answer_first(
        _ranking_ambiguities(), clarifications=changed
    )

    assert detections == []
    assert best_effort == [True]
    assert "Answer: Total loan amount (ca.totloanamt)" in generated[0]
    assert events[-1].clarifications == changed

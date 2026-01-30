"""
Integration tests for CLI commands.

Tests end-to-end CLI command execution with mocked dependencies.
Verifies that commands properly integrate services and renderers.
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from typing import Any, Dict, List

# Note: These tests use the full CLI command classes but mock external dependencies


class TestTopCommandIntegration:
    """Integration tests for rdst top command."""

    @pytest.fixture
    def mock_targets_config(self):
        """Create mock TargetsConfig."""
        cfg = Mock()
        cfg.get_default.return_value = "test-target"
        cfg.get.return_value = {
            "engine": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "testdb",
            "user": "testuser",
            "password_env": "DB_PASS",
        }
        cfg.load = Mock()
        return cfg

    def test_top_command_initialization(self):
        """Test TopCommand can be instantiated."""
        from lib.cli.top import TopCommand

        cmd = TopCommand()
        assert cmd is not None

    def test_top_command_snapshot_mode(self, mock_targets_config):
        """Test top command service and event types are available."""
        from lib.services.top_service import TopService
        from lib.services.types import (
            TopStatusEvent,
            TopConnectedEvent,
            TopQueriesEvent,
            TopCompleteEvent,
            TopQueryData,
        )

        # Verify service can be instantiated
        service = TopService()
        assert service is not None
        assert hasattr(service, "get_top_queries")
        assert hasattr(service, "stream_realtime")

        # Verify event types can be constructed
        status_event = TopStatusEvent(type="status", message="Loading...")
        assert status_event.type == "status"

        connected_event = TopConnectedEvent(
            type="connected",
            target_name="test-target",
            db_engine="postgresql",
            source="pg_stat",
        )
        assert connected_event.type == "connected"

        query_data = TopQueryData(
            query_hash="abc123",
            query_text="SELECT 1",
            normalized_query="SELECT 1",
            freq=100,
            total_time="1s",
            avg_time="0.01s",
            pct_load="5%",
        )
        assert query_data.query_hash == "abc123"

        queries_event = TopQueriesEvent(
            type="queries",
            queries=[query_data],
            source="pg_stat",
            target_name="test-target",
            db_engine="postgresql",
        )
        assert len(queries_event.queries) == 1

        complete_event = TopCompleteEvent(
            type="complete",
            success=True,
            queries=[],
            source="pg_stat",
            newly_saved=0,
        )
        assert complete_event.success is True


class TestInitCommandIntegration:
    """Integration tests for rdst init command."""

    @pytest.fixture
    def mock_targets_config(self):
        """Create mock TargetsConfig."""
        cfg = Mock()
        cfg.is_init_completed.return_value = False
        cfg.get_default.return_value = None
        cfg.list_targets.return_value = []
        cfg.get_llm_config.return_value = {}
        cfg.load = Mock()
        return cfg

    def test_init_status_uses_init_service(self, mock_targets_config):
        """Test init status command uses InitService."""
        from lib.services.init_service import InitService
        from lib.services.types import InitStatus

        service = InitService()

        with patch.object(service, "_load_config", return_value=mock_targets_config):
            status = service.get_status()

        assert isinstance(status, InitStatus)
        assert status.initialized is False

    def test_init_validate_uses_init_service(self, mock_targets_config):
        """Test init validate command uses InitService."""
        from lib.services.init_service import InitService
        from lib.services.types import InitValidationResult

        mock_targets_config.list_targets.return_value = ["test-target"]
        mock_targets_config.get.return_value = {"engine": "postgresql"}
        mock_targets_config.upsert = Mock()
        mock_targets_config.save = Mock()

        service = InitService()

        with patch.object(service, "_load_config", return_value=mock_targets_config):
            with patch.object(service, "_test_target", return_value=(True, "OK", {})):
                with patch.object(service, "check_llm", return_value={"success": True}):
                    result = service.validate_all()

        assert isinstance(result, InitValidationResult)
        assert len(result.target_results) == 1


class TestConfigureCommandIntegration:
    """Integration tests for rdst configure command."""

    @pytest.fixture
    def mock_targets_config(self):
        """Create mock TargetsConfig."""
        cfg = Mock()
        cfg.get_default.return_value = "prod"
        cfg.list_targets.return_value = ["prod", "staging"]
        cfg.get.side_effect = lambda name: {
            "prod": {"engine": "postgresql", "host": "localhost"},
            "staging": {"engine": "mysql", "host": "staging.db"},
        }.get(name)
        cfg.load = Mock()
        return cfg

    def test_configure_list_uses_service(self, mock_targets_config):
        """Test configure list uses ConfigureService."""
        from lib.services.configure_service import ConfigureService
        from lib.services.types import (
            ConfigureInput,
            ConfigureOptions,
            ConfigureTargetListEvent,
        )

        service = ConfigureService()

        async def collect_events():
            events = []
            async for event in service.list_targets(
                ConfigureInput(), ConfigureOptions(operation="list")
            ):
                events.append(event)
            return events

        # Use asyncio to run the async generator
        import asyncio

        with patch.object(service, "_load_config", return_value=mock_targets_config):
            events = asyncio.run(collect_events())

        # Should have at least one target_list event
        target_list_events = [
            e for e in events if isinstance(e, ConfigureTargetListEvent)
        ]
        assert len(target_list_events) >= 1


class TestAnalyzeCommandIntegration:
    """Integration tests for rdst analyze command."""

    @pytest.fixture
    def mock_targets_config(self):
        """Create mock TargetsConfig."""
        cfg = Mock()
        cfg.get_default.return_value = "test-target"
        cfg.get.return_value = {
            "engine": "postgresql",
            "host": "localhost",
        }
        cfg.load = Mock()
        return cfg

    def test_analyze_command_uses_service(self, mock_targets_config):
        """Test analyze command uses AnalyzeService."""
        from lib.services.analyze_service import AnalyzeService
        from lib.services.types import (
            AnalyzeInput,
            AnalyzeOptions,
            ProgressEvent,
            ErrorEvent,
        )

        service = AnalyzeService()

        input_data = AnalyzeInput(
            sql="SELECT * FROM users",
            normalized_sql="SELECT * FROM users",
            source="test",
        )
        options = AnalyzeOptions(target="test-target")

        async def collect_events():
            events = []
            async for event in service.analyze(input_data, options):
                events.append(event)
                # Stop after error or complete
                if isinstance(event, ErrorEvent):
                    break
            return events

        import asyncio

        # Mock _load_config to return no target (will produce error)
        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (None, None)
            events = asyncio.run(collect_events())

        # Should have progress and error events
        progress_events = [e for e in events if isinstance(e, ProgressEvent)]
        error_events = [e for e in events if isinstance(e, ErrorEvent)]

        assert len(progress_events) >= 1
        assert len(error_events) == 1


class TestAskCommandIntegration:
    """Integration tests for rdst ask command."""

    @pytest.fixture
    def mock_targets_config(self):
        """Create mock TargetsConfig."""
        cfg = Mock()
        cfg.get_default.return_value = "test-target"
        cfg.get.return_value = {
            "engine": "postgresql",
            "host": "localhost",
        }
        cfg.load = Mock()
        return cfg

    def test_ask_command_uses_service(self, mock_targets_config):
        """Test ask command uses AskService."""
        from lib.services.ask_service import AskService
        from lib.services.types import (
            AskInput,
            AskOptions,
            AskStatusEvent,
            AskErrorEvent,
        )

        service = AskService()

        input_data = AskInput(
            question="How many users signed up last month?",
            target="test-target",
        )
        options = AskOptions(timeout_seconds=30)

        async def collect_events():
            events = []
            async for event in service.ask(input_data, options):
                events.append(event)
                # Stop after error
                if isinstance(event, AskErrorEvent):
                    break
            return events

        import asyncio

        # Mock _load_config to return no target config
        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = ("test-target", None)
            events = asyncio.run(collect_events())

        # Should have status and error events
        status_events = [e for e in events if isinstance(e, AskStatusEvent)]
        error_events = [e for e in events if isinstance(e, AskErrorEvent)]

        assert len(status_events) >= 1
        assert len(error_events) == 1


class TestCLIServiceIntegration:
    """Tests for CLI and Service layer integration patterns."""

    def test_service_yields_typed_events(self):
        """Test that services yield properly typed events."""
        from lib.services.types import (
            TopStatusEvent,
            TopConnectedEvent,
            TopQueriesEvent,
            TopCompleteEvent,
            TopErrorEvent,
        )

        # Verify event types can be instantiated
        status = TopStatusEvent(type="status", message="Test")
        assert status.type == "status"

        connected = TopConnectedEvent(
            type="connected",
            target_name="test",
            db_engine="postgresql",
            source="pg_stat",
        )
        assert connected.type == "connected"

    def test_renderer_can_handle_all_event_types(self):
        """Test that renderers handle all event types without error."""
        from lib.cli.top_renderer import TopRenderer
        from lib.services.types import (
            TopStatusEvent,
            TopConnectedEvent,
            TopQueriesEvent,
            TopCompleteEvent,
            TopErrorEvent,
        )

        renderer = TopRenderer()
        renderer._console = Mock()

        # All event types should be renderable
        events = [
            TopStatusEvent(type="status", message="Test"),
            TopConnectedEvent(
                type="connected",
                target_name="test",
                db_engine="postgresql",
                source="pg_stat",
            ),
            TopQueriesEvent(
                type="queries",
                queries=[],
                source="pg_stat",
                target_name="test",
                db_engine="postgresql",
            ),
            TopCompleteEvent(
                type="complete",
                success=True,
                queries=[],
                source="pg_stat",
                newly_saved=0,
            ),
            TopErrorEvent(
                type="error",
                message="Test error",
                stage="test",
            ),
        ]

        for event in events:
            # Should not raise any exception
            renderer.render(event)


class TestErrorHandlingIntegration:
    """Tests for error handling across CLI and Service layers."""

    def test_service_errors_are_typed(self):
        """Test that service errors use typed ErrorEvent."""
        from lib.services.analyze_service import AnalyzeService
        from lib.services.types import AnalyzeInput, AnalyzeOptions, ErrorEvent

        service = AnalyzeService()

        async def get_error_event():
            async for event in service.analyze(
                AnalyzeInput(sql="SELECT 1", normalized_sql="SELECT 1", source="test"),
                AnalyzeOptions(target="nonexistent"),
            ):
                if isinstance(event, ErrorEvent):
                    return event
            return None

        import asyncio

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (None, None)
            error = asyncio.run(get_error_event())

        assert error is not None
        assert isinstance(error, ErrorEvent)
        assert error.type == "error"

    def test_renderer_handles_error_events(self):
        """Test that renderer properly handles error events."""
        from lib.cli.analyze_renderer import AnalyzeRenderer
        from lib.services.types import ErrorEvent

        renderer = AnalyzeRenderer()
        renderer._console = Mock()
        mock_status = Mock()
        renderer._current_status = mock_status

        error = ErrorEvent(
            type="error",
            message="Connection failed",
            stage="execution",
        )

        renderer.render(error)

        # Should stop spinner
        mock_status.stop.assert_called_once()

"""
Integration tests for CLI commands.

Tests end-to-end CLI command execution with mocked dependencies.
Verifies that commands properly integrate services and renderers.
"""

import json

import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from types import SimpleNamespace
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
        from features.top.cli.command import TopCommand

        cmd = TopCommand()
        assert cmd is not None

    def test_top_command_snapshot_mode(self, mock_targets_config):
        """Test top command service and event types are available."""
        from features.top.service import TopService
        from features.top.events import (
            TopCompleteEvent,
            TopConnectedEvent,
            TopQueriesEvent,
            TopStatusEvent,
        )
        from features.top.models import TopQueryData

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
        from features.init.service import InitService
        from features.init.models import InitStatus

        service = InitService()

        with patch.object(service, "_load_config", return_value=mock_targets_config):
            status = service.get_status()

        assert isinstance(status, InitStatus)
        assert status.initialized is False

    def test_init_validate_uses_init_service(self, mock_targets_config):
        """Test init validate command uses InitService."""
        from features.init.service import InitService
        from features.init.models import InitValidationResult

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
        from features.configure.service import ConfigureService
        from features.configure.events import ConfigureTargetListEvent
        from features.configure.models import ConfigureInput, ConfigureOptions

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

    def test_analyze_command_uses_service(self, mock_targets_config):
        """Test analyze command uses AnalyzeService."""
        from features.analyze.service import AnalyzeService
        from features.analyze.models import AnalyzeInput, AnalyzeOptions
        from shared.service_events import ErrorEvent, ProgressEvent

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

    def test_ask_command_uses_service(self, mock_targets_config):
        """Test ask command uses AskService."""
        from features.ask.service import AskService
        from features.ask.events import AskErrorEvent, AskStatusEvent
        from features.ask.models import AskInput, AskOptions

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


class TestFleetCommandIntegration:
    """Integration tests for FleetCommand orchestration and persistence."""

    @staticmethod
    def _audit_args(*, output_json=True):
        return SimpleNamespace(
            group="test-cluster",
            tag=None,
            save_name=None,
            no_save=True,
            output_json=output_json,
            no_insights=True,
            duration="10s",
            verbose=not output_json,
            auto_yes=False,
        )

    @staticmethod
    def _audit_config():
        config = Mock()
        config.list_fleet_targets.return_value = ["writer", "reader"]
        config.get.side_effect = lambda name: {
            "writer": {"engine": "postgresql", "host": "writer.example.com"},
            "reader": {"engine": "postgresql", "host": "reader.example.com"},
        }.get(name)
        return config

    @staticmethod
    def _audit_service():
        service = Mock()
        service.audit_target.side_effect = lambda name, config: {
            "target_name": name,
            "engine": config["engine"],
            "metrics": {},
            "sizing": {},
            "cache_opportunity": {},
            "top_queries": [],
        }
        return service

    def test_duration_capture_then_benchmark_runs_for_each_target(self, capsys):
        """Duration captures complete before each target is benchmarked."""
        from features.fleet.cli.command import FleetCommand

        events = []

        class FakeCaptureService:
            async def run_capture(self, **kwargs):
                events.append(("capture", kwargs["target_name"]))
                yield SimpleNamespace(
                    type="complete",
                    summary={
                        "duration_seconds": kwargs["duration_seconds"],
                        "queries": [
                            {
                                "query_hash": f"{kwargs['target_name']}-query",
                                "query_text": "SELECT 1",
                                "avg_time_ms": 10.0,
                            }
                        ],
                    },
                    analysis=None,
                )

        config = self._audit_config()
        service = self._audit_service()
        benchmark_results = [
            {
                "query_hash": "query",
                "cacheable": True,
                "baseline_ms": 10.0,
                "readyset_ms": 1.0,
                "speedup": 10.0,
            }
        ]

        def run_benchmark(console, target, queries, **kwargs):
            events.append(("benchmark", target))
            return benchmark_results

        with (
            patch("features.fleet.cli.command.TargetsConfig", return_value=config),
            patch("features.fleet.cli.command.AuditService", return_value=service),
            patch("features.fleet.cli.command.CaptureService", FakeCaptureService),
            patch("shared.db_connection.create_direct_connection") as connect,
            patch(
                "features.audit.readyset_benchmark.docker_available",
                return_value=True,
            ),
            patch(
                "features.audit.readyset_benchmark.build_comparison",
                return_value={"cacheable_queries": 1},
            ),
            patch(
                "features.audit.cli.command.AuditCommand._run_readyset_testing",
                side_effect=run_benchmark,
            ) as benchmark,
        ):
            result = FleetCommand()._handle_audit(self._audit_args())

        assert result.ok is True
        assert {target for phase, target in events if phase == "capture"} == {
            "writer",
            "reader",
        }
        assert benchmark.call_count == 2
        assert {call.args[1] for call in benchmark.call_args_list} == {
            "writer",
            "reader",
        }
        first_benchmark = next(i for i, event in enumerate(events) if event[0] == "benchmark")
        assert all(phase == "capture" for phase, _target in events[:first_benchmark])
        assert len(events[:first_benchmark]) == 2
        output = capsys.readouterr().out
        payload = json.loads(output)
        assert {item["target_name"] for item in payload["results"]} == {
            "writer",
            "reader",
        }
        assert all("readyset_comparison" in item for item in payload["results"])
        assert connect.call_count == 2

    def test_duration_skips_benchmarks_without_docker(self, capsys):
        """Fleet duration audit reports the Docker gate and does not benchmark."""
        from features.fleet.cli.command import FleetCommand

        class FakeCaptureService:
            async def run_capture(self, **kwargs):
                yield SimpleNamespace(
                    type="complete",
                    summary={
                        "duration_seconds": kwargs["duration_seconds"],
                        "queries": [{"query_hash": "query", "query_text": "SELECT 1"}],
                    },
                    analysis=None,
                )

        config = self._audit_config()
        service = self._audit_service()

        with (
            patch("features.fleet.cli.command.TargetsConfig", return_value=config),
            patch("features.fleet.cli.command.AuditService", return_value=service),
            patch("features.fleet.cli.command.CaptureService", FakeCaptureService),
            patch("shared.db_connection.create_direct_connection"),
            patch(
                "features.audit.readyset_benchmark.docker_available",
                return_value=False,
            ),
            patch(
                "features.audit.cli.command.AuditCommand._run_readyset_testing"
            ) as benchmark,
        ):
            result = FleetCommand()._handle_audit(
                self._audit_args(output_json=False)
            )

        assert result.ok is True
        assert "Skipping Readyset cache benchmarks" in capsys.readouterr().out
        benchmark.assert_not_called()

    def test_discover_persists_cluster_groups_and_role_tags(self):
        """Discovered Aurora members retain cluster and role metadata."""
        from features.fleet.cli.command import FleetCommand
        from features.fleet.models import FleetMember

        members = [
            FleetMember(
                name="orders-writer",
                engine="postgresql",
                host="orders.cluster.example.com",
                port=5432,
                database="orders",
                user="postgres",
                password_env="FLEET_PASS",
                group="orders-cluster",
                tags=["aurora", "writer", "role:writer"],
            ),
            FleetMember(
                name="orders-reader",
                engine="postgresql",
                host="orders.reader.example.com",
                port=5432,
                database="orders",
                user="postgres",
                password_env="FLEET_PASS",
                group="orders-cluster",
                tags=["aurora", "reader", "role:reader"],
            ),
        ]
        config = Mock()
        config.list_targets.return_value = []
        config.get.return_value = None
        args = SimpleNamespace(
            regions="us-east-1",
            engine_filter="all",
            name_pattern=None,
            password_env="FLEET_PASS",
            user=None,
            group=None,
            default_database=None,
            dry_run=False,
        )

        with (
            patch(
                "features.fleet.cli.command.detect_aws_credentials",
                return_value=(True, "ok"),
            ),
            patch(
                "features.fleet.cli.command.discover_rds_instances",
                return_value=members,
            ),
            patch("features.fleet.cli.command.TargetsConfig", return_value=config),
            patch.object(FleetCommand, "_setup_credentials_after_discover"),
        ):
            result = FleetCommand()._handle_discover(args)

        assert result.ok is True
        saved = {call.args[0]: call.args[1] for call in config.upsert.call_args_list}
        assert saved["orders-writer"]["group"] == "orders-cluster"
        assert "role:writer" in saved["orders-writer"]["tags"]
        assert saved["orders-reader"]["group"] == "orders-cluster"
        assert "role:reader" in saved["orders-reader"]["tags"]
        config.save.assert_called_once_with()


class TestCLIServiceIntegration:
    """Tests for CLI and Service layer integration patterns."""

    def test_service_yields_typed_events(self):
        """Test that services yield properly typed events."""
        from features.top.events import (
            TopCompleteEvent,
            TopConnectedEvent,
            TopErrorEvent,
            TopQueriesEvent,
            TopStatusEvent,
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
        from features.top.cli.renderer import TopRenderer
        from features.top.events import (
            TopCompleteEvent,
            TopConnectedEvent,
            TopErrorEvent,
            TopQueriesEvent,
            TopStatusEvent,
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
        from features.analyze.service import AnalyzeService
        from features.analyze.models import AnalyzeInput, AnalyzeOptions
        from shared.service_events import ErrorEvent

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
        from features.analyze.cli.renderer import AnalyzeRenderer
        from shared.service_events import ErrorEvent

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

"""
Unit tests for TopService.

Tests the async generator-based top queries service including event yielding,
source selection, fallback handling, and real-time streaming.
"""

import asyncio
import pytest
import pandas as pd
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from typing import Any, Dict, List, Optional

# Import from lib package (conftest.py adds rdst root to path)
from lib.data_manager_service.data_manager_service_command_sets import COMMAND_SETS
from lib.services.types import (
    TopInput,
    TopOptions,
    TopStatusEvent,
    TopConnectedEvent,
    TopSourceFallbackEvent,
    TopQueriesEvent,
    TopQueryData,
    TopQuerySavedEvent,
    TopCompleteEvent,
    TopErrorEvent,
)
from lib.services.top_service import TopService


class TestTopServiceInit:
    """Tests for TopService initialization."""

    def test_initialization(self):
        """Test service initializes correctly."""
        service = TopService()
        assert service is not None

    def test_initialization_no_attributes_required(self):
        """Test service has no required constructor parameters."""
        # TopService is stateless
        service = TopService()
        assert hasattr(service, "get_top_queries")
        assert hasattr(service, "stream_realtime")


class TestTopServiceGetTopQueries:
    """Tests for get_top_queries() method."""

    @pytest.fixture
    def service(self):
        """Create TopService instance."""
        return TopService()

    @pytest.fixture
    def input_data(self):
        """Create test input data."""
        return TopInput(target="test-target", source="auto")

    @pytest.fixture
    def options(self):
        """Create test options."""
        return TopOptions(limit=10, auto_save_registry=False)

    @pytest.mark.asyncio
    async def test_yields_initial_status_event(self, service, input_data, options):
        """Test that get_top_queries() yields initial status event."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (None, None, None)

            async for event in service.get_top_queries(input_data, options):
                events.append(event)
                if len(events) >= 1:
                    break

        assert len(events) >= 1
        assert events[0].type == "status"
        assert "Loading configuration" in events[0].message

    @pytest.mark.asyncio
    async def test_error_no_target_configured(self, service, input_data, options):
        """Test error when no target specified and no default configured."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (None, None, None)

            async for event in service.get_top_queries(input_data, options):
                events.append(event)

        # Should have status then error
        assert len(events) == 2
        assert events[0].type == "status"
        assert events[1].type == "error"
        assert "No target specified" in events[1].message

    @pytest.mark.asyncio
    async def test_error_target_not_found(self, service, input_data, options):
        """Test error when target is not found in config."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = ("test-target", None, None)

            async for event in service.get_top_queries(input_data, options):
                events.append(event)

        assert events[-1].type == "error"
        assert "not found" in events[-1].message

    @pytest.mark.asyncio
    async def test_error_invalid_source_for_engine(self, service, options):
        """Test error when source is invalid for database engine."""
        input_data = TopInput(target="test-target", source="pg_stat")
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = ("test-target", {"host": "localhost"}, "mysql")

            async for event in service.get_top_queries(input_data, options):
                events.append(event)

        assert events[-1].type == "error"
        assert "not supported" in events[-1].message

    @pytest.mark.asyncio
    async def test_yields_connected_event(self, service, input_data, options):
        """Test that get_top_queries() yields TopConnectedEvent."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "localhost"},
                "postgresql",
            )

            with patch.object(
                service, "_execute_top_query", new_callable=AsyncMock
            ) as mock_exec:
                mock_exec.return_value = (
                    {"success": True, "data": []},
                    "pg_stat",
                    None,
                )

                with patch.object(service, "_process_top_data", return_value=[]):
                    async for event in service.get_top_queries(input_data, options):
                        events.append(event)

        connected_events = [e for e in events if e.type == "connected"]
        assert len(connected_events) == 1
        assert connected_events[0].target_name == "test-target"
        assert connected_events[0].db_engine == "postgresql"

    @pytest.mark.asyncio
    async def test_yields_queries_event(self, service, input_data, options):
        """Test that get_top_queries() yields TopQueriesEvent."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "localhost"},
                "postgresql",
            )

            with patch.object(
                service, "_execute_top_query", new_callable=AsyncMock
            ) as mock_exec:
                mock_exec.return_value = (
                    {"success": True, "data": []},
                    "pg_stat",
                    None,
                )

                mock_data = [
                    {
                        "query_hash": "abc123",
                        "query_text": "SELECT 1",
                        "normalized_query": "SELECT 1",
                        "freq": 100,
                        "total_time": "1.234s",
                        "avg_time": "0.012s",
                        "pct_load": "5.0%",
                    }
                ]
                with patch.object(service, "_process_top_data", return_value=mock_data):
                    async for event in service.get_top_queries(input_data, options):
                        events.append(event)

        queries_events = [e for e in events if e.type == "queries"]
        assert len(queries_events) == 1
        assert len(queries_events[0].queries) == 1
        assert queries_events[0].queries[0].query_hash == "abc123"

    @pytest.mark.asyncio
    async def test_yields_complete_event(self, service, input_data, options):
        """Test that get_top_queries() yields TopCompleteEvent."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "localhost"},
                "postgresql",
            )

            with patch.object(
                service, "_execute_top_query", new_callable=AsyncMock
            ) as mock_exec:
                mock_exec.return_value = (
                    {"success": True, "data": []},
                    "pg_stat",
                    None,
                )

                with patch.object(service, "_process_top_data", return_value=[]):
                    async for event in service.get_top_queries(input_data, options):
                        events.append(event)

        complete_events = [e for e in events if e.type == "complete"]
        assert len(complete_events) == 1
        assert complete_events[0].success is True

    @pytest.mark.asyncio
    async def test_yields_fallback_event(self, service, input_data, options):
        """Test that get_top_queries() yields fallback event when source changes."""
        events = []

        fallback_event = TopSourceFallbackEvent(
            type="source_fallback",
            from_source="pg_stat",
            to_source="activity",
            reason="pg_stat_statements not available",
        )

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "localhost"},
                "postgresql",
            )

            with patch.object(
                service, "_execute_top_query", new_callable=AsyncMock
            ) as mock_exec:
                mock_exec.return_value = (
                    {"success": True, "data": []},
                    "activity",
                    fallback_event,
                )

                with patch.object(service, "_process_top_data", return_value=[]):
                    async for event in service.get_top_queries(input_data, options):
                        events.append(event)

        fallback_events = [e for e in events if e.type == "source_fallback"]
        assert len(fallback_events) == 1
        assert fallback_events[0].from_source == "pg_stat"
        assert fallback_events[0].to_source == "activity"

    @pytest.mark.asyncio
    async def test_auto_saves_queries_to_registry(self, service, input_data):
        """Test that queries are auto-saved to registry when enabled."""
        options = TopOptions(limit=10, auto_save_registry=True)
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "localhost"},
                "postgresql",
            )

            with patch.object(
                service, "_execute_top_query", new_callable=AsyncMock
            ) as mock_exec:
                mock_exec.return_value = (
                    {"success": True, "data": []},
                    "pg_stat",
                    None,
                )

                mock_data = [
                    {
                        "query_hash": "abc123",
                        "query_text": "SELECT 1",
                        "normalized_query": "SELECT 1",
                        "freq": 100,
                        "total_time": "1.234s",
                        "avg_time": "0.012s",
                        "pct_load": "5.0%",
                    }
                ]
                with patch.object(service, "_process_top_data", return_value=mock_data):
                    with patch.object(
                        service,
                        "_save_query_to_registry",
                        new_callable=AsyncMock,
                        return_value=True,
                    ):
                        async for event in service.get_top_queries(input_data, options):
                            events.append(event)

        saved_events = [e for e in events if e.type == "query_saved"]
        assert len(saved_events) == 1
        assert saved_events[0].is_new is True

    @pytest.mark.asyncio
    async def test_handles_exception_yields_error(self, service, input_data, options):
        """Test that exceptions are caught and yield ErrorEvent."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.side_effect = Exception("Test exception")

            async for event in service.get_top_queries(input_data, options):
                events.append(event)

        # Should have status then error
        assert events[-1].type == "error"
        assert "Test exception" in events[-1].message


class TestTopServiceSourceSelection:
    """Tests for source selection logic."""

    @pytest.fixture
    def service(self):
        """Create TopService instance."""
        return TopService()

    def test_auto_select_source_postgresql(self, service):
        """Test auto source selection for PostgreSQL."""
        source = service._auto_select_source("postgresql", {})
        assert source == "pg_stat"

    def test_auto_select_source_mysql(self, service):
        """Test auto source selection for MySQL."""
        source = service._auto_select_source("mysql", {})
        assert source == "digest"

    def test_auto_select_source_unknown(self, service):
        """Test auto source selection for unknown engine."""
        source = service._auto_select_source("unknown", {})
        assert source == "activity"


class TestTopServiceValidation:
    """Tests for source validation."""

    @pytest.fixture
    def service(self):
        """Create TopService instance."""
        return TopService()

    def test_validate_source_postgresql_valid(self, service):
        """Test valid sources for PostgreSQL."""
        assert service._validate_source_for_engine("pg_stat", "postgresql") is True
        assert service._validate_source_for_engine("activity", "postgresql") is True
        assert service._validate_source_for_engine("auto", "postgresql") is True

    def test_validate_source_postgresql_invalid(self, service):
        """Test invalid sources for PostgreSQL."""
        assert service._validate_source_for_engine("digest", "postgresql") is False

    def test_validate_source_mysql_valid(self, service):
        """Test valid sources for MySQL."""
        assert service._validate_source_for_engine("digest", "mysql") is True
        assert service._validate_source_for_engine("activity", "mysql") is True
        assert service._validate_source_for_engine("auto", "mysql") is True

    def test_validate_source_mysql_invalid(self, service):
        """Test invalid sources for MySQL."""
        assert service._validate_source_for_engine("pg_stat", "mysql") is False

    def test_get_valid_sources_postgresql(self, service):
        """Test getting valid sources for PostgreSQL."""
        sources = service._get_valid_sources_for_engine("postgresql")
        assert "pg_stat" in sources
        assert "activity" in sources
        assert "auto" in sources

    def test_get_valid_sources_mysql(self, service):
        """Test getting valid sources for MySQL."""
        sources = service._get_valid_sources_for_engine("mysql")
        assert "digest" in sources
        assert "activity" in sources
        assert "auto" in sources


class TestTopServiceCommandSets:
    """Tests for command set selection."""

    @pytest.fixture
    def service(self):
        """Create TopService instance."""
        return TopService()

    def test_get_command_set_postgresql_pg_stat(self, service):
        """Test command set for PostgreSQL pg_stat."""
        cmd_set = service._get_command_set_for_source("postgresql", "pg_stat")
        assert cmd_set == "rdst_top_pg_stat"

    def test_get_command_set_postgresql_activity(self, service):
        """Test command set for PostgreSQL activity."""
        cmd_set = service._get_command_set_for_source("postgresql", "activity")
        assert cmd_set == "rdst_top_pg_activity"

    def test_get_command_set_mysql_digest(self, service):
        """Test command set for MySQL digest."""
        cmd_set = service._get_command_set_for_source("mysql", "digest")
        assert cmd_set == "rdst_top_mysql_digest"

    def test_get_command_set_mysql_activity(self, service):
        """Test command set for MySQL activity."""
        cmd_set = service._get_command_set_for_source("mysql", "activity")
        assert cmd_set == "rdst_top_mysql_activity"

    def test_get_command_set_invalid_raises(self, service):
        """Test command set for invalid combination raises ValueError."""
        with pytest.raises(ValueError):
            service._get_command_set_for_source("invalid", "invalid")

    def test_raw_query_command_sets_preserve_newlines(self, service):
        """Top command sets should not flatten whitespace for raw SQL sources."""
        assert "LEFT(query, 4096) as query_text" in COMMAND_SETS["rdst_top_pg_stat"][
            "commands"
        ]["pg_stat_queries"]["query"]
        assert "LEFT(query, 4096) as query_text" in COMMAND_SETS["rdst_top_pg_activity"][
            "commands"
        ]["pg_activity_queries"]["query"]
        assert "LEFT(INFO, 4096) as query_text" in COMMAND_SETS[
            "rdst_top_mysql_activity"
        ]["commands"]["mysql_activity_queries"]["query"]
        assert "LEFT(sql_text, 4096) as query_text" in COMMAND_SETS[
            "rdst_top_mysql_slowlog"
        ]["commands"]["mysql_slowlog_queries"]["query"]

    def test_process_top_data_preserves_multiline_query_text(self, service):
        """Top processing should keep raw multiline SQL intact."""
        df = pd.DataFrame(
            [
                {
                    "query_text": "-- note\nSELECT * FROM users WHERE id = 1",
                    "calls": 3,
                    "total_time": 1.5,
                    "mean_time": 0.5,
                    "pct_load": 75.0,
                }
            ]
        )

        result = service._process_top_data(
            {"success": True, "data": df},
            "pg_stat",
            limit=10,
            sort="total_time",
            filter_pattern=None,
        )

        assert result[0]["query_text"] == "-- note\nSELECT * FROM users WHERE id = 1"


class TestTopServiceEventTypes:
    """Tests for event type structure."""

    def test_top_status_event_structure(self):
        """Test TopStatusEvent has correct structure."""
        event = TopStatusEvent(type="status", message="Loading...")
        assert event.type == "status"
        assert event.message == "Loading..."

    def test_top_connected_event_structure(self):
        """Test TopConnectedEvent has correct structure."""
        event = TopConnectedEvent(
            type="connected",
            target_name="prod",
            db_engine="postgresql",
            source="pg_stat",
        )
        assert event.type == "connected"
        assert event.target_name == "prod"
        assert event.db_engine == "postgresql"
        assert event.source == "pg_stat"

    def test_top_queries_event_structure(self):
        """Test TopQueriesEvent has correct structure."""
        queries = [
            TopQueryData(
                query_hash="abc123",
                query_text="SELECT 1",
                normalized_query="SELECT 1",
                freq=100,
                total_time="1.234s",
                avg_time="0.012s",
                pct_load="5.0%",
            )
        ]
        event = TopQueriesEvent(
            type="queries",
            queries=queries,
            source="pg_stat",
            target_name="prod",
            db_engine="postgresql",
        )
        assert event.type == "queries"
        assert len(event.queries) == 1
        assert event.queries[0].query_hash == "abc123"

    def test_top_complete_event_structure(self):
        """Test TopCompleteEvent has correct structure."""
        event = TopCompleteEvent(
            type="complete",
            success=True,
            queries=[],
            source="pg_stat",
            newly_saved=5,
        )
        assert event.type == "complete"
        assert event.success is True
        assert event.newly_saved == 5

    def test_top_error_event_structure(self):
        """Test TopErrorEvent has correct structure."""
        event = TopErrorEvent(
            type="error",
            message="Connection failed",
            stage="config",
        )
        assert event.type == "error"
        assert event.message == "Connection failed"
        assert event.stage == "config"


class TestTopServiceErrorHandling:
    """Tests for error handling edge cases."""

    @pytest.fixture
    def service(self):
        """Create TopService instance."""
        return TopService()

    @pytest.fixture
    def input_data(self):
        """Create test input data."""
        return TopInput(target="test-target", source="auto")

    @pytest.fixture
    def options(self):
        """Create test options."""
        return TopOptions(limit=10, auto_save_registry=False)

    @pytest.mark.asyncio
    async def test_config_load_exception_yields_error(
        self, service, input_data, options
    ):
        """Test that config loading exceptions yield proper error events."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.side_effect = RuntimeError("Config file corrupted")

            async for event in service.get_top_queries(input_data, options):
                events.append(event)

        assert events[-1].type == "error"
        assert "Config file corrupted" in events[-1].message

    @pytest.mark.asyncio
    async def test_database_connection_error(self, service, input_data, options):
        """Test that database connection errors are handled gracefully."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "localhost"},
                "postgresql",
            )

            with patch.object(
                service, "_execute_top_query", new_callable=AsyncMock
            ) as mock_exec:
                mock_exec.side_effect = ConnectionError("Connection refused")

                async for event in service.get_top_queries(input_data, options):
                    events.append(event)

        assert events[-1].type == "error"
        assert "Connection refused" in events[-1].message

    @pytest.mark.asyncio
    async def test_query_execution_returns_failed_result(
        self, service, input_data, options
    ):
        """Test handling when query execution returns success=False."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "localhost"},
                "postgresql",
            )

            with patch.object(
                service, "_execute_top_query", new_callable=AsyncMock
            ) as mock_exec:
                mock_exec.return_value = (
                    {"success": False, "error": "Permission denied"},
                    "pg_stat",
                    None,
                )

                async for event in service.get_top_queries(input_data, options):
                    events.append(event)

        # Should still complete but with error in data
        assert any(e.type == "connected" for e in events)

    @pytest.mark.asyncio
    async def test_process_data_exception(self, service, input_data, options):
        """Test exception during data processing yields error."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "localhost"},
                "postgresql",
            )

            with patch.object(
                service, "_execute_top_query", new_callable=AsyncMock
            ) as mock_exec:
                mock_exec.return_value = (
                    {"success": True, "data": []},
                    "pg_stat",
                    None,
                )

                with patch.object(service, "_process_top_data") as mock_process:
                    mock_process.side_effect = ValueError("Invalid data format")

                    async for event in service.get_top_queries(input_data, options):
                        events.append(event)

        assert events[-1].type == "error"
        assert "Invalid data format" in events[-1].message

    @pytest.mark.asyncio
    async def test_empty_target_config_keys(self, service, options):
        """Test handling when target config has missing keys."""
        input_data = TopInput(target="test-target", source="auto")
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            # Config with minimal/empty data
            mock_load.return_value = (
                "test-target",
                {},  # Empty config
                "postgresql",
            )

            with patch.object(
                service, "_execute_top_query", new_callable=AsyncMock
            ) as mock_exec:
                mock_exec.return_value = (
                    {"success": True, "data": []},
                    "pg_stat",
                    None,
                )

                with patch.object(service, "_process_top_data", return_value=[]):
                    async for event in service.get_top_queries(input_data, options):
                        events.append(event)

        # Should handle empty config gracefully
        assert events[-1].type == "complete"

    @pytest.mark.asyncio
    async def test_malformed_query_data(self, service, input_data, options):
        """Test handling malformed query data in response."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "localhost"},
                "postgresql",
            )

            with patch.object(
                service, "_execute_top_query", new_callable=AsyncMock
            ) as mock_exec:
                mock_exec.return_value = (
                    {"success": True, "data": [{"malformed": "data"}]},
                    "pg_stat",
                    None,
                )

                # Return processed data with required fields
                mock_data = [
                    {
                        "query_hash": "abc123",
                        "query_text": "SELECT 1",
                        "normalized_query": "SELECT 1",
                        "freq": 100,
                        "total_time": "1.234s",
                        "avg_time": "0.012s",
                        "pct_load": "5.0%",
                    }
                ]
                with patch.object(service, "_process_top_data", return_value=mock_data):
                    async for event in service.get_top_queries(input_data, options):
                        events.append(event)

        assert events[-1].type == "complete"


class TestTopServiceTimeoutScenarios:
    """Tests for timeout handling scenarios."""

    @pytest.fixture
    def service(self):
        """Create TopService instance."""
        return TopService()

    @pytest.fixture
    def input_data(self):
        """Create test input data."""
        return TopInput(target="test-target", source="auto")

    @pytest.fixture
    def options(self):
        """Create test options."""
        return TopOptions(limit=10, auto_save_registry=False)

    @pytest.mark.asyncio
    async def test_config_load_timeout(self, service, input_data, options):
        """Test handling when config loading times out."""
        events = []

        async def slow_load(*args):
            await asyncio.sleep(0.1)
            raise asyncio.TimeoutError("Config load timed out")

        with patch.object(service, "_load_config", side_effect=slow_load):
            async for event in service.get_top_queries(input_data, options):
                events.append(event)

        assert events[-1].type == "error"
        assert (
            "timed out" in events[-1].message.lower()
            or "timeout" in events[-1].message.lower()
        )

    @pytest.mark.asyncio
    async def test_database_query_timeout(self, service, input_data, options):
        """Test handling when database query times out."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "localhost"},
                "postgresql",
            )

            async def slow_query(*args):
                await asyncio.sleep(0.1)
                raise asyncio.TimeoutError("Query execution timed out")

            with patch.object(service, "_execute_top_query", side_effect=slow_query):
                async for event in service.get_top_queries(input_data, options):
                    events.append(event)

        assert events[-1].type == "error"

    @pytest.mark.asyncio
    async def test_cancelled_error_handling(self, service, input_data, options):
        """Test handling when operation is cancelled."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "localhost"},
                "postgresql",
            )

            with patch.object(
                service, "_execute_top_query", new_callable=AsyncMock
            ) as mock_exec:
                mock_exec.side_effect = asyncio.CancelledError("Operation cancelled")

                try:
                    async for event in service.get_top_queries(input_data, options):
                        events.append(event)
                except asyncio.CancelledError:
                    pass  # Expected for CancelledError

        # Should have at least initial status event before cancellation
        assert len(events) >= 1
        assert events[0].type == "status"


class TestTopServiceNetworkFailures:
    """Tests for network failure simulations."""

    @pytest.fixture
    def service(self):
        """Create TopService instance."""
        return TopService()

    @pytest.fixture
    def input_data(self):
        """Create test input data."""
        return TopInput(target="test-target", source="auto")

    @pytest.fixture
    def options(self):
        """Create test options."""
        return TopOptions(limit=10, auto_save_registry=False)

    @pytest.mark.asyncio
    async def test_network_unreachable(self, service, input_data, options):
        """Test handling when network is unreachable."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "192.168.1.100"},
                "postgresql",
            )

            with patch.object(
                service, "_execute_top_query", new_callable=AsyncMock
            ) as mock_exec:
                mock_exec.side_effect = OSError("Network is unreachable")

                async for event in service.get_top_queries(input_data, options):
                    events.append(event)

        assert events[-1].type == "error"
        assert "unreachable" in events[-1].message.lower()

    @pytest.mark.asyncio
    async def test_connection_reset(self, service, input_data, options):
        """Test handling when connection is reset by peer."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "localhost"},
                "postgresql",
            )

            with patch.object(
                service, "_execute_top_query", new_callable=AsyncMock
            ) as mock_exec:
                mock_exec.side_effect = ConnectionResetError("Connection reset by peer")

                async for event in service.get_top_queries(input_data, options):
                    events.append(event)

        assert events[-1].type == "error"

    @pytest.mark.asyncio
    async def test_dns_resolution_failure(self, service, input_data, options):
        """Test handling when DNS resolution fails."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "nonexistent.invalid.domain"},
                "postgresql",
            )

            with patch.object(
                service, "_execute_top_query", new_callable=AsyncMock
            ) as mock_exec:
                mock_exec.side_effect = OSError("Name or service not known")

                async for event in service.get_top_queries(input_data, options):
                    events.append(event)

        assert events[-1].type == "error"

    @pytest.mark.asyncio
    async def test_ssl_certificate_error(self, service, input_data, options):
        """Test handling SSL certificate errors."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "localhost", "tls": True},
                "postgresql",
            )

            with patch.object(
                service, "_execute_top_query", new_callable=AsyncMock
            ) as mock_exec:
                mock_exec.side_effect = Exception("SSL: CERTIFICATE_VERIFY_FAILED")

                async for event in service.get_top_queries(input_data, options):
                    events.append(event)

        assert events[-1].type == "error"
        assert "SSL" in events[-1].message or "CERTIFICATE" in events[-1].message

    @pytest.mark.asyncio
    async def test_authentication_failure(self, service, input_data, options):
        """Test handling authentication failures."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "localhost", "user": "invalid"},
                "postgresql",
            )

            with patch.object(
                service, "_execute_top_query", new_callable=AsyncMock
            ) as mock_exec:
                mock_exec.side_effect = Exception("authentication failed for user")

                async for event in service.get_top_queries(input_data, options):
                    events.append(event)

        assert events[-1].type == "error"
        assert "authentication" in events[-1].message.lower()

    @pytest.mark.asyncio
    async def test_registry_save_network_error(self, service, input_data):
        """Test that network errors during registry save result in error event.

        Note: Registry save failures are currently fatal - they cause the entire
        operation to fail and yield an error event. This test validates that
        behavior, though a future enhancement could make them non-fatal.
        """
        options = TopOptions(limit=10, auto_save_registry=True)
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "localhost"},
                "postgresql",
            )

            with patch.object(
                service, "_execute_top_query", new_callable=AsyncMock
            ) as mock_exec:
                mock_exec.return_value = (
                    {"success": True, "data": []},
                    "pg_stat",
                    None,
                )

                mock_data = [
                    {
                        "query_hash": "abc123",
                        "query_text": "SELECT 1",
                        "normalized_query": "SELECT 1",
                        "freq": 100,
                        "total_time": "1.234s",
                        "avg_time": "0.012s",
                        "pct_load": "5.0%",
                    }
                ]
                with patch.object(service, "_process_top_data", return_value=mock_data):
                    with patch.object(
                        service,
                        "_save_query_to_registry",
                        new_callable=AsyncMock,
                        side_effect=OSError("Network error during save"),
                    ):
                        async for event in service.get_top_queries(input_data, options):
                            events.append(event)

        # Registry save failure causes error event (currently fatal)
        assert events[-1].type == "error"
        assert "Network error" in events[-1].message

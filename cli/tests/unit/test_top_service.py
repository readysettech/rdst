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

from features.top.command_sets import TOP_COMMAND_SETS
from features.top.service import TopService
from features.top.events import (
    TopCompleteEvent,
    TopConnectedEvent,
    TopErrorEvent,
    TopQueriesEvent,
    TopQuerySavedEvent,
    TopSourceFallbackEvent,
    TopStatusEvent,
)
from features.top.models import (
    TopInput,
    TopOptions,
    TopQueryData,
)


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
        assert "rdst configure add" in events[1].message

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
        # Envelope (B7/T24): humane message; class name in detail; raw stays out.
        assert events[-1].message == "The slow-query lookup could not be completed."
        assert events[-1].detail == "Exception"
        assert "Test exception" not in events[-1].message


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
        from shared.data_manager_service.data_manager_service_command_sets import (
            MAX_QUERY_LENGTH,
        )

        pg_stat_sql = TOP_COMMAND_SETS["rdst_top_pg_stat"]["commands"]["pg_stat_queries"]["query"]
        assert f", {MAX_QUERY_LENGTH}) as query_text" in pg_stat_sql

        pg_activity_sql = TOP_COMMAND_SETS["rdst_top_pg_activity"]["commands"]["pg_activity_queries"]["query"]
        assert f", {MAX_QUERY_LENGTH}) as query_text" in pg_activity_sql

        mysql_activity_sql = TOP_COMMAND_SETS["rdst_top_mysql_activity"]["commands"]["mysql_activity_queries"]["query"]
        assert f", {MAX_QUERY_LENGTH}) as query_text" in mysql_activity_sql

        mysql_slowlog_sql = TOP_COMMAND_SETS["rdst_top_mysql_slowlog"]["commands"]["mysql_slowlog_queries"]["query"]
        assert f", {MAX_QUERY_LENGTH}) as query_text" in mysql_slowlog_sql

    def test_process_top_data_preserves_multiline_query_text(self, service):
        """Top processing should keep raw multiline SQL intact."""
        import pandas as pd

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
            TopOptions(limit=10, sort="total_time", filter_pattern=None),
        )

        assert result[0]["query_text"] == "-- note\nSELECT * FROM users WHERE id = 1"

    def test_process_top_data_min_freq_filter(self, service):
        """min_freq option filters out low-frequency queries."""
        df = pd.DataFrame(
            [
                {"query_text": "SELECT 1", "calls": 10, "total_time": 5.0, "mean_time": 0.5},
                {"query_text": "SELECT 2", "calls": 2, "total_time": 1.0, "mean_time": 0.5},
                {"query_text": "SELECT 3", "calls": 50, "total_time": 20.0, "mean_time": 0.4},
            ]
        )

        result = service._process_top_data(
            {"success": True, "data": df},
            "pg_stat",
            TopOptions(limit=10, sort="total_time", min_freq=5),
        )

        texts = [r["query_text"] for r in result]
        assert "SELECT 1" in texts
        assert "SELECT 3" in texts
        assert "SELECT 2" not in texts

    def test_process_top_data_min_load_pct_filter(self, service):
        """min_load_pct option filters out low-load queries."""
        df = pd.DataFrame(
            [
                {"query_text": "SELECT heavy", "calls": 1, "total_time": 90.0, "mean_time": 90.0, "pct_load": 90.0},
                {"query_text": "SELECT light", "calls": 1, "total_time": 1.0, "mean_time": 1.0, "pct_load": 1.0},
            ]
        )

        result = service._process_top_data(
            {"success": True, "data": df},
            "pg_stat",
            TopOptions(limit=10, sort="total_time", min_load_pct=5.0),
        )

        texts = [r["query_text"] for r in result]
        assert "SELECT heavy" in texts
        assert "SELECT light" not in texts

    def test_process_top_data_qps_zero_in_historical_mode(self, service):
        """QPS is always 0 in historical mode (sample_seconds is unreliable)."""
        df = pd.DataFrame(
            [
                {"query_text": "SELECT 1", "calls": 100, "total_time": 5.0, "mean_time": 0.05, "sample_seconds": 10.0},
            ]
        )

        result = service._process_top_data(
            {"success": True, "data": df},
            "pg_stat",
            TopOptions(limit=10, sort="total_time"),
        )

        assert len(result) == 1
        assert result[0].get("qps", 0) == 0.0

    def test_process_top_data_empty_when_all_filtered(self, service):
        """Returns empty list when all queries filtered by min_freq."""
        df = pd.DataFrame(
            [
                {"query_text": "SELECT 1", "calls": 1, "total_time": 0.1, "mean_time": 0.1},
            ]
        )

        result = service._process_top_data(
            {"success": True, "data": df},
            "pg_stat",
            TopOptions(limit=10, sort="total_time", min_freq=100),
        )

        assert result == []

class TestTopServiceUnitConversion:
    """B2 — Slow-Queries ms->s unit fix.

    `pg_stat_statements` reports timings in milliseconds; the historical web
    path used to append an "s" suffix without dividing by 1000, so a
    ``153.183 ms`` average rendered as ``153.183s`` (1000x too large). The
    MySQL ``digest`` path is genuinely seconds and must be left unchanged.
    """

    @pytest.fixture
    def service(self):
        return TopService()

    def test_pg_stat_milliseconds_converted_to_seconds(self, service):
        """A 1225.465 ms pg_stat total renders as 1.225s, not 1225.465s."""
        df = pd.DataFrame(
            [
                {
                    "query_text": "SELECT * FROM votes GROUP BY user_id",
                    "calls": 42,
                    "total_time": 1225.465,  # milliseconds, per pg_stat_statements
                    "mean_time": 153.183,  # milliseconds
                }
            ]
        )

        result = service._process_top_data(
            {"success": True, "data": df},
            "pg_stat",
            TopOptions(limit=10, sort="total_time"),
        )

        assert len(result) == 1
        # 1225.465 ms -> 1.225 s ; 153.183 ms -> 0.153 s
        assert result[0]["total_time"] == "1.225s"
        assert result[0]["avg_time"] == "0.153s"

    def test_mysql_digest_seconds_not_divided(self, service):
        """The MySQL digest path is already seconds and must not be converted."""
        df = pd.DataFrame(
            [
                {
                    "query_text": "SELECT 1",
                    "count_star": 10,
                    "sum_timer_wait": 2.5,  # already seconds on the digest path
                    "avg_timer_wait": 0.25,  # already seconds
                }
            ]
        )

        result = service._process_top_data(
            {"success": True, "data": df},
            "digest",
            TopOptions(limit=10, sort="total_time"),
        )

        assert len(result) == 1
        assert result[0]["total_time"] == "2.500s"
        assert result[0]["avg_time"] == "0.250s"

    def test_pg_stat_min_total_time_filter_uses_seconds(self, service):
        """min_total_time_s must compare against seconds, not raw ms.

        A 500 ms query (0.5 s) is below a 1 s floor and must be filtered out;
        before the ms->s fix the raw 500 (ms) compared > 1 and leaked through.
        """
        df = pd.DataFrame(
            [
                {"query_text": "SELECT slow", "calls": 5, "total_time": 5000.0, "mean_time": 1000.0},
                {"query_text": "SELECT fast", "calls": 5, "total_time": 500.0, "mean_time": 100.0},
            ]
        )

        result = service._process_top_data(
            {"success": True, "data": df},
            "pg_stat",
            TopOptions(limit=10, sort="total_time", min_total_time_s=1.0),
        )

        texts = [r["query_text"] for r in result]
        assert "SELECT slow" in texts  # 5.0 s >= 1 s floor
        assert "SELECT fast" not in texts  # 0.5 s < 1 s floor


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
        assert events[-1].message == "The slow-query lookup could not be completed."
        assert events[-1].detail == "RuntimeError"
        assert "Config file corrupted" not in events[-1].message

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
        assert events[-1].message == "The slow-query lookup could not be completed."
        assert events[-1].detail == "ConnectionError"
        assert "Connection refused" not in events[-1].message

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
        assert events[-1].message == "The slow-query lookup could not be completed."
        assert events[-1].detail == "ValueError"
        assert "Invalid data format" not in events[-1].message

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
        assert events[-1].message == "The slow-query lookup could not be completed."
        assert events[-1].detail == "TimeoutError"

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
        assert events[-1].message == "The slow-query lookup could not be completed."
        assert events[-1].detail == "OSError"
        assert "unreachable" not in events[-1].message.lower()

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
        assert events[-1].message == "The slow-query lookup could not be completed."
        assert events[-1].detail == "Exception"
        assert "CERTIFICATE" not in events[-1].message

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
        assert events[-1].message == "The slow-query lookup could not be completed."
        assert events[-1].detail == "Exception"
        assert "authentication" not in events[-1].message.lower()

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
        assert events[-1].message == "The slow-query lookup could not be completed."
        assert events[-1].detail == "OSError"
        assert "Network error" not in events[-1].message


class TestTopImpactMetrics:
    """Impact telemetry must survive the trip into the registry so the Queries
    workbench can rank by impact (rdst-41p.2)."""

    @pytest.fixture
    def service(self):
        return TopService()

    def test_process_top_data_carries_impact_metrics(self, service):
        df = pd.DataFrame(
            [{"query_text": "SELECT slow", "calls": 4200, "total_time": 100.0, "mean_time": 142.0}]
        )
        result = service._process_top_data(
            {"success": True, "data": df},
            "pg_stat",
            TopOptions(limit=10, sort="total_time"),
        )
        row = result[0]
        assert row["observation_count"] == 4200
        # Persisted avg_duration_ms mirrors the displayed avg_time (seconds -> ms).
        avg_time_s = float(row["avg_time"].rstrip("s"))
        assert row["avg_duration_ms"] == pytest.approx(avg_time_s * 1000)
        assert row["avg_duration_ms"] > 0

    @pytest.mark.asyncio
    async def test_save_query_to_registry_forwards_metrics(self, service):
        with patch("shared.query_registry.QueryRegistry") as MockRegistry:
            MockRegistry.return_value.add_query.return_value = ("h", True)
            await service._save_query_to_registry(
                {
                    "query_text": "SELECT 1",
                    "avg_duration_ms": 142.0,
                    "observation_count": 4200,
                    "max_duration_ms": 190.0,
                },
                "imdb",
                "top",
            )
            _, kwargs = MockRegistry.return_value.add_query.call_args
            assert kwargs["avg_duration_ms"] == 142.0
            assert kwargs["observation_count"] == 4200
            assert kwargs["max_duration_ms"] == 190.0

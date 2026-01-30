"""
Unit tests for AnalyzeService.

Tests the async generator-based query analysis service including event yielding,
parallel execution, error handling, and fast mode.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from dataclasses import dataclass
from typing import Any, Dict, Optional

# Import from lib package (conftest.py adds rdst root to path)
from lib.services.types import (
    AnalyzeInput,
    AnalyzeOptions,
    ProgressEvent,
    CompleteEvent,
    ErrorEvent,
    ExplainCompleteEvent,
    RewritesTestedEvent,
    ReadysetCheckedEvent,
)
from lib.services.analyze_service import (
    AnalyzeService,
    STEP_PROGRESS,
    _serialize_for_json,
)


class TestAnalyzeServiceInit:
    """Tests for AnalyzeService initialization."""

    def test_initialization(self):
        """Test service initializes correctly."""
        service = AnalyzeService()
        assert service is not None

    def test_get_workflow_path(self):
        """Test workflow path is correctly resolved."""
        service = AnalyzeService()
        path = service._get_workflow_path()

        assert isinstance(path, Path)
        assert "analyze_workflow_simple.json" in str(path)


class TestStepProgress:
    """Tests for STEP_PROGRESS mapping."""

    def test_step_progress_structure(self):
        """Test STEP_PROGRESS has expected structure."""
        assert "ValidateQuerySafety" in STEP_PROGRESS
        assert "ExecuteExplainAnalyze" in STEP_PROGRESS
        assert "PerformLLMAnalysis" in STEP_PROGRESS
        assert "FormatFinalResults" in STEP_PROGRESS

    def test_step_progress_values(self):
        """Test STEP_PROGRESS values are tuples with correct format."""
        for step_name, (stage, percent, message) in STEP_PROGRESS.items():
            assert isinstance(stage, str)
            assert isinstance(percent, int)
            assert 0 <= percent <= 100
            assert isinstance(message, str)


class TestAnalyzeServiceEvents:
    """Tests for analyze() event yielding."""

    @pytest.fixture
    def service(self):
        """Create AnalyzeService instance."""
        return AnalyzeService()

    @pytest.fixture
    def input_data(self):
        """Create test input data."""
        return AnalyzeInput(
            sql="SELECT * FROM users WHERE id = 1",
            normalized_sql="select * from users where id = ?",
            source="test",
            hash="test_hash_123",
        )

    @pytest.fixture
    def options(self):
        """Create test options."""
        return AnalyzeOptions(target="test-target")

    @pytest.mark.asyncio
    async def test_analyze_yields_initial_progress(self, service, input_data, options):
        """Test that analyze() yields initial progress event."""
        events = []

        # Mock _load_config to return None (no target)
        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (None, None)

            async for event in service.analyze(input_data, options):
                events.append(event)

        # First event should be progress
        assert len(events) >= 1
        assert events[0].type == "progress"
        assert events[0].stage == "loading_config"
        assert events[0].percent == 2

    @pytest.mark.asyncio
    async def test_analyze_error_no_target(self, service, input_data, options):
        """Test that analyze() yields error when no target configured."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (None, None)

            async for event in service.analyze(input_data, options):
                events.append(event)

        # Should have progress then error
        assert len(events) == 2
        assert events[0].type == "progress"
        assert events[1].type == "error"
        assert "No target specified" in events[1].message

    @pytest.mark.asyncio
    async def test_analyze_error_target_not_found(self, service, input_data, options):
        """Test that analyze() yields error when target not found."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = ("test-target", None)

            async for event in service.analyze(input_data, options):
                events.append(event)

        # Should have progress then error
        assert len(events) == 2
        assert events[0].type == "progress"
        assert events[1].type == "error"
        assert "not found" in events[1].message

    @pytest.mark.asyncio
    async def test_analyze_error_workflow_not_found(self, service, input_data, options):
        """Test that analyze() yields error when workflow file not found."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = ("test-target", {"host": "localhost"})

            # Mock workflow path to non-existent file
            mock_path = Mock()
            mock_path.exists.return_value = False
            with patch.object(service, "_get_workflow_path", return_value=mock_path):
                async for event in service.analyze(input_data, options):
                    events.append(event)

        # Should have progress then error
        assert len(events) == 2
        assert events[0].type == "progress"
        assert events[1].type == "error"
        assert "Workflow file not found" in events[1].message

    @pytest.mark.asyncio
    async def test_analyze_exception_yields_error(self, service, input_data, options):
        """Test that exceptions during analyze yield ErrorEvent."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.side_effect = Exception("Test exception")

            async for event in service.analyze(input_data, options):
                events.append(event)

        # Should have progress event then error event (progress is yielded before exception)
        assert len(events) == 2
        assert events[0].type == "progress"
        assert events[1].type == "error"
        assert "Test exception" in events[1].message


class TestAnalyzeServiceParallelExecution:
    """Tests for parallel execution in _run_parallel_analysis."""

    @pytest.fixture
    def service(self):
        """Create AnalyzeService instance."""
        return AnalyzeService()

    @pytest.fixture
    def input_data(self):
        """Create test input data."""
        return AnalyzeInput(
            sql="SELECT * FROM users WHERE id = 1",
            normalized_sql="select * from users where id = ?",
            source="test",
            hash="test_hash_123",
        )

    @pytest.fixture
    def options(self):
        """Create test options."""
        return AnalyzeOptions(target="test-target")

    @pytest.fixture
    def options_with_readyset(self):
        """Create test options with readyset cache enabled."""
        return AnalyzeOptions(target="test-target", readyset_cache=True)

    @pytest.mark.asyncio
    async def test_parallel_analysis_workflow_only(self, service, input_data, options):
        """Test parallel analysis with workflow only (no readyset)."""
        events = []

        # Mock workflow execution
        workflow_result = {
            "success": True,
            "result": {
                "explain_results": {"success": True, "execution_time_ms": 10.5},
                "FormatFinalResults": {"summary": "test"},
            },
        }

        async def mock_progress_gen():
            yield ProgressEvent(
                type="progress",
                stage="validating",
                percent=5,
                message="Validating query safety...",
            )

        with patch.object(
            service,
            "_run_workflow_with_progress",
            new_callable=AsyncMock,
            return_value=(mock_progress_gen(), workflow_result),
        ):
            mock_path = Mock()
            mock_path.exists.return_value = True

            async for event in service._run_parallel_analysis(
                input=input_data,
                options=options,
                target_name="test-target",
                target_config={"host": "localhost"},
                workflow_path=mock_path,
            ):
                events.append(event)

        # Should have events from _process_results
        assert len(events) >= 1
        # Last event should be complete
        assert events[-1].type == "complete"
        assert events[-1].success is True

    @pytest.mark.asyncio
    async def test_parallel_analysis_workflow_failure(
        self, service, input_data, options
    ):
        """Test parallel analysis when workflow fails."""
        events = []

        # Mock workflow execution to fail
        workflow_result = {
            "success": False,
            "error": "Workflow execution failed",
        }

        async def mock_progress_gen():
            raise Exception("Workflow execution failed")
            yield

        with patch.object(
            service,
            "_run_workflow_with_progress",
            new_callable=AsyncMock,
            return_value=(mock_progress_gen(), workflow_result),
        ):
            mock_path = Mock()
            mock_path.exists.return_value = True

            async for event in service._run_parallel_analysis(
                input=input_data,
                options=options,
                target_name="test-target",
                target_config={"host": "localhost"},
                workflow_path=mock_path,
            ):
                events.append(event)

        # Should have error event
        assert len(events) == 1
        assert events[0].type == "error"
        assert "Workflow execution failed" in events[0].message

    @pytest.mark.asyncio
    async def test_parallel_analysis_with_readyset(
        self, service, input_data, options_with_readyset
    ):
        """Test parallel analysis with readyset cache enabled."""
        events = []

        # Mock workflow execution
        workflow_result = {
            "success": True,
            "result": {
                "explain_results": {"success": True, "execution_time_ms": 10.5},
                "FormatFinalResults": {"summary": "test"},
            },
        }

        # Mock readyset execution
        readyset_result = {
            "success": True,
            "checked": True,
            "final_verdict": {
                "cacheable": True,
                "confidence": "high",
                "method": "readyset_container",
            },
            "explain_cache_result": {
                "explanation": "Query is cacheable",
            },
        }

        async def mock_progress_gen():
            yield ProgressEvent(
                type="progress",
                stage="validating",
                percent=5,
                message="Validating query safety...",
            )

        with patch.object(
            service,
            "_run_workflow_with_progress",
            new_callable=AsyncMock,
            return_value=(mock_progress_gen(), workflow_result),
        ):
            with patch.object(
                service, "_run_readyset_analysis_sync", return_value=readyset_result
            ):
                mock_path = Mock()
                mock_path.exists.return_value = True

                async for event in service._run_parallel_analysis(
                    input=input_data,
                    options=options_with_readyset,
                    target_name="test-target",
                    target_config={"host": "localhost"},
                    workflow_path=mock_path,
                ):
                    events.append(event)

        # Should have readyset_checked event
        readyset_events = [e for e in events if e.type == "readyset_checked"]
        assert len(readyset_events) == 1
        assert readyset_events[0].cacheable is True

    @pytest.mark.asyncio
    async def test_parallel_analysis_readyset_failure_non_fatal(
        self, service, input_data, options_with_readyset
    ):
        """Test that readyset failure is non-fatal."""
        events = []

        # Mock workflow execution
        workflow_result = {
            "success": True,
            "result": {
                "explain_results": {"success": True, "execution_time_ms": 10.5},
                "FormatFinalResults": {"summary": "test"},
            },
        }

        async def mock_progress_gen():
            yield ProgressEvent(
                type="progress",
                stage="validating",
                percent=5,
                message="Validating query safety...",
            )

        with patch.object(
            service,
            "_run_workflow_with_progress",
            new_callable=AsyncMock,
            return_value=(mock_progress_gen(), workflow_result),
        ):
            with patch.object(
                service,
                "_run_readyset_analysis_sync",
                side_effect=Exception("Readyset failed"),
            ):
                mock_path = Mock()
                mock_path.exists.return_value = True

                async for event in service._run_parallel_analysis(
                    input=input_data,
                    options=options_with_readyset,
                    target_name="test-target",
                    target_config={"host": "localhost"},
                    workflow_path=mock_path,
                ):
                    events.append(event)

        # Should still complete (readyset failure is non-fatal)
        complete_events = [e for e in events if e.type == "complete"]
        assert len(complete_events) == 1
        assert complete_events[0].success is True


class TestAnalyzeServiceProcessResults:
    """Tests for _process_results event generation."""

    @pytest.fixture
    def service(self):
        """Create AnalyzeService instance."""
        return AnalyzeService()

    @pytest.fixture
    def input_data(self):
        """Create test input data."""
        return AnalyzeInput(
            sql="SELECT * FROM users WHERE id = 1",
            normalized_sql="select * from users where id = ?",
            source="test",
            hash="test_hash_123",
        )

    @pytest.mark.asyncio
    async def test_process_results_explain_complete(self, service, input_data):
        """Test _process_results yields ExplainCompleteEvent."""
        events = []

        workflow_result = {
            "success": True,
            "result": {
                "explain_results": {
                    "success": True,
                    "database_engine": "postgresql",
                    "execution_time_ms": 15.5,
                    "rows_examined": 1000,
                    "rows_returned": 10,
                    "cost_estimate": 25.0,
                    "explain_plan": {"type": "Seq Scan"},
                },
                "FormatFinalResults": {},
            },
        }

        async for event in service._process_results(
            workflow_result=workflow_result,
            readyset_result=None,
            input=input_data,
        ):
            events.append(event)

        explain_events = [e for e in events if e.type == "explain_complete"]
        assert len(explain_events) == 1
        assert explain_events[0].database_engine == "postgresql"
        assert explain_events[0].execution_time_ms == 15.5
        assert explain_events[0].rows_examined == 1000

    @pytest.mark.asyncio
    async def test_process_results_rewrites_tested(self, service, input_data):
        """Test _process_results yields RewritesTestedEvent."""
        events = []

        workflow_result = {
            "success": True,
            "result": {
                "rewrite_test_results": {
                    "tested": True,
                    "message": "Tested 3 rewrites",
                    "original_performance": {"time_ms": 100},
                    "rewrite_results": [{"sql": "SELECT...", "time_ms": 50}],
                    "best_rewrite": {"sql": "SELECT...", "improvement": "50%"},
                },
                "FormatFinalResults": {},
            },
        }

        async for event in service._process_results(
            workflow_result=workflow_result,
            readyset_result=None,
            input=input_data,
        ):
            events.append(event)

        rewrite_events = [e for e in events if e.type == "rewrites_tested"]
        assert len(rewrite_events) == 1
        assert rewrite_events[0].tested is True
        assert rewrite_events[0].message == "Tested 3 rewrites"

    @pytest.mark.asyncio
    async def test_process_results_rewrites_tested_without_tested_flag(
        self, service, input_data
    ):
        """Test rewrite events still emit when payload omits `tested`."""
        events = []

        workflow_result = {
            "success": True,
            "result": {
                "rewrite_test_results": {
                    "success": True,
                    "message": "Tested 1 rewrite",
                    "original_performance": {"execution_time_ms": 100},
                    "rewrite_results": [
                        {
                            "success": True,
                            "sql": "SELECT * FROM users",
                            "performance": {"execution_time_ms": 50},
                        }
                    ],
                    "best_rewrite": {
                        "success": True,
                        "sql": "SELECT * FROM users",
                        "performance": {"execution_time_ms": 50},
                    },
                },
                "FormatFinalResults": {},
            },
        }

        async for event in service._process_results(
            workflow_result=workflow_result,
            readyset_result=None,
            input=input_data,
        ):
            events.append(event)

        rewrite_events = [e for e in events if e.type == "rewrites_tested"]
        assert len(rewrite_events) == 1
        assert rewrite_events[0].tested is True

        complete_events = [e for e in events if e.type == "complete"]
        assert len(complete_events) == 1
        assert complete_events[0].rewrite_testing is not None
        assert complete_events[0].rewrite_testing.get("tested") is True

    @pytest.mark.asyncio
    async def test_process_results_readyset_checked(self, service, input_data):
        """Test _process_results yields ReadysetCheckedEvent from readyset result."""
        events = []

        workflow_result = {
            "success": True,
            "result": {
                "FormatFinalResults": {},
            },
        }

        readyset_result = {
            "success": True,
            "final_verdict": {
                "cacheable": True,
                "confidence": "high",
                "method": "readyset_container",
            },
            "explain_cache_result": {
                "explanation": "Query is fully cacheable",
                "issues": [],
                "warnings": ["Consider adding index"],
            },
        }

        async for event in service._process_results(
            workflow_result=workflow_result,
            readyset_result=readyset_result,
            input=input_data,
        ):
            events.append(event)

        readyset_events = [e for e in events if e.type == "readyset_checked"]
        assert len(readyset_events) == 1
        assert readyset_events[0].cacheable is True
        assert readyset_events[0].confidence == "high"
        assert readyset_events[0].method == "readyset_container"

    @pytest.mark.asyncio
    async def test_process_results_complete_event(self, service, input_data):
        """Test _process_results yields CompleteEvent with all data."""
        events = []

        workflow_result = {
            "success": True,
            "result": {
                "explain_results": {"success": True, "execution_time_ms": 10},
                "llm_analysis": {"recommendations": ["Add index"]},
                "rewrite_test_results": {"tested": False},
                "readyset_cacheability": {"checked": True, "cacheable": True},
                "storage_result": {"analysis_id": "analysis_123"},
                "registry_normalization": {"hash": "hash_456"},
                "FormatFinalResults": {"summary": "Analysis complete"},
            },
        }

        async for event in service._process_results(
            workflow_result=workflow_result,
            readyset_result=None,
            input=input_data,
        ):
            events.append(event)

        complete_events = [e for e in events if e.type == "complete"]
        assert len(complete_events) == 1
        assert complete_events[0].success is True
        assert complete_events[0].analysis_id == "analysis_123"
        assert complete_events[0].query_hash == "hash_456"

    @pytest.mark.asyncio
    async def test_process_results_workflow_failure(self, service, input_data):
        """Test _process_results yields ErrorEvent on workflow failure."""
        events = []

        workflow_result = {
            "success": False,
            "error": "Database connection failed",
            "result": {"partial": "data"},
        }

        async for event in service._process_results(
            workflow_result=workflow_result,
            readyset_result=None,
            input=input_data,
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == "error"
        assert "Database connection failed" in events[0].message
        assert events[0].partial_results is not None


class TestAnalyzeServiceFastMode:
    """Tests for fast mode behavior."""

    @pytest.fixture
    def service(self):
        """Create AnalyzeService instance."""
        return AnalyzeService()

    @pytest.fixture
    def input_data(self):
        """Create test input data."""
        return AnalyzeInput(
            sql="SELECT * FROM users WHERE id = 1",
            normalized_sql="select * from users where id = ?",
            source="test",
        )

    @pytest.fixture
    def fast_options(self):
        """Create test options with fast mode enabled."""
        return AnalyzeOptions(target="test-target", fast=True)

    @pytest.mark.asyncio
    async def test_fast_mode_passed_to_workflow(
        self, service, input_data, fast_options
    ):
        """Test that fast mode is passed to workflow execution."""
        # Mock the workflow manager
        mock_mgr = Mock()
        mock_mgr.run_async.return_value = "workflow_123"
        mock_execution = Mock()
        mock_execution.status.value = "completed"
        mock_execution.context = {"success": True}
        mock_mgr.get_workflow_status.return_value = mock_execution

        # WorkflowManager is imported inside the method, so we need to patch it there
        with patch(
            "lib.workflow_manager.workflow_manager.WorkflowManager"
        ) as mock_wm_class:
            mock_wm_class.from_file.return_value = mock_mgr

            mock_path = Mock()
            mock_path.__str__ = Mock(return_value="/path/to/workflow.json")

            result = await service._run_workflow_with_progress(
                input=input_data,
                options=fast_options,
                target_name="test-target",
                target_config={"host": "localhost"},
                workflow_path=mock_path,
            )

            # Verify run_async was called with fast_mode in initial_input
            call_args = mock_mgr.run_async.call_args
            initial_input = call_args.kwargs.get("initial_input", {})
            assert initial_input.get("fast_mode") is True


class TestSerializeForJson:
    """Tests for _serialize_for_json helper."""

    def test_serialize_dict(self):
        """Test serializing dict."""
        result = _serialize_for_json({"key": "value"})
        assert result == {"key": "value"}

    def test_serialize_list(self):
        """Test serializing list."""
        result = _serialize_for_json([1, 2, 3])
        assert result == [1, 2, 3]

    def test_serialize_nested(self):
        """Test serializing nested structures."""
        data = {"outer": {"inner": [1, 2, {"deep": "value"}]}}
        result = _serialize_for_json(data)
        assert result == data

    def test_serialize_object_with_dict(self):
        """Test serializing object with __dict__ becomes string."""

        class CustomObj:
            def __init__(self):
                self.value = 42

        obj = CustomObj()
        result = _serialize_for_json(obj)
        assert isinstance(result, str)

    def test_serialize_primitives(self):
        """Test serializing primitives."""
        assert _serialize_for_json("string") == "string"
        assert _serialize_for_json(42) == 42
        assert _serialize_for_json(3.14) == 3.14
        assert _serialize_for_json(True) is True
        assert _serialize_for_json(None) is None


class TestAnalyzeServiceErrorHandling:
    """Tests for error handling edge cases."""

    @pytest.fixture
    def service(self):
        """Create AnalyzeService instance."""
        return AnalyzeService()

    @pytest.fixture
    def input_data(self):
        """Create test input data."""
        return AnalyzeInput(
            sql="SELECT * FROM users WHERE id = 1",
            normalized_sql="select * from users where id = ?",
            source="test",
            hash="test_hash_123",
        )

    @pytest.fixture
    def options(self):
        """Create test options."""
        return AnalyzeOptions(target="test-target")

    @pytest.mark.asyncio
    async def test_config_load_runtime_error(self, service, input_data, options):
        """Test handling RuntimeError during config loading."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.side_effect = RuntimeError("Config file corrupted")

            async for event in service.analyze(input_data, options):
                events.append(event)

        assert events[-1].type == "error"
        assert "Config file corrupted" in events[-1].message

    @pytest.mark.asyncio
    async def test_workflow_file_permission_error(self, service, input_data, options):
        """Test handling permission errors reading workflow file."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = ("test-target", {"host": "localhost"})

            mock_path = Mock()
            mock_path.exists.return_value = True
            mock_path.read_text.side_effect = PermissionError("Permission denied")

            with patch.object(service, "_get_workflow_path", return_value=mock_path):
                async for event in service.analyze(input_data, options):
                    events.append(event)

        assert events[-1].type == "error"

    @pytest.mark.asyncio
    async def test_invalid_sql_syntax(self, service, options):
        """Test handling invalid SQL syntax."""
        input_data = AnalyzeInput(
            sql="SELEC * FORM users",  # Invalid SQL
            normalized_sql="selec * form users",
            source="test",
            hash="invalid_hash",
        )
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (None, None)

            async for event in service.analyze(input_data, options):
                events.append(event)

        # Should eventually yield error (either from validation or config)
        assert events[-1].type == "error"

    @pytest.mark.asyncio
    async def test_very_long_sql_query(self, service, options):
        """Test handling very long SQL queries."""
        long_sql = (
            "SELECT * FROM users WHERE id IN ("
            + ",".join(str(i) for i in range(10000))
            + ")"
        )
        input_data = AnalyzeInput(
            sql=long_sql,
            normalized_sql="select * from users where id in (?)",
            source="test",
            hash="long_hash",
        )
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (None, None)

            async for event in service.analyze(input_data, options):
                events.append(event)

        # Should handle gracefully
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_empty_sql_query(self, service, options):
        """Test handling empty SQL queries."""
        input_data = AnalyzeInput(
            sql="",
            normalized_sql="",
            source="test",
            hash="empty_hash",
        )
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (None, None)

            async for event in service.analyze(input_data, options):
                events.append(event)

        # Should handle empty SQL
        assert len(events) >= 1


class TestAnalyzeServiceTimeoutScenarios:
    """Tests for timeout handling scenarios."""

    @pytest.fixture
    def service(self):
        """Create AnalyzeService instance."""
        return AnalyzeService()

    @pytest.fixture
    def input_data(self):
        """Create test input data."""
        return AnalyzeInput(
            sql="SELECT * FROM users WHERE id = 1",
            normalized_sql="select * from users where id = ?",
            source="test",
            hash="test_hash_123",
        )

    @pytest.fixture
    def options(self):
        """Create test options."""
        return AnalyzeOptions(target="test-target")

    @pytest.mark.asyncio
    async def test_workflow_execution_timeout(self, service, input_data, options):
        """Test handling workflow execution timeout."""
        events = []
        import asyncio

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = ("test-target", {"host": "localhost"})

            mock_path = Mock()
            mock_path.exists.return_value = True

            async def slow_workflow_gen(*args, **kwargs):
                await asyncio.sleep(0.1)
                raise asyncio.TimeoutError("Workflow timed out")
                yield  # Make it an async generator

            with patch.object(service, "_get_workflow_path", return_value=mock_path):
                with patch.object(
                    service,
                    "_run_parallel_analysis",
                    side_effect=slow_workflow_gen,
                ):
                    async for event in service.analyze(input_data, options):
                        events.append(event)

        assert events[-1].type == "error"

    @pytest.mark.asyncio
    async def test_database_query_timeout(self, service, input_data, options):
        """Test handling database query timeout during EXPLAIN."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = ("test-target", {"host": "localhost"})

            mock_path = Mock()
            mock_path.exists.return_value = True

            workflow_result = {
                "success": False,
                "error": "Query execution timed out after 30 seconds",
            }

            async def mock_progress_gen():
                yield ProgressEvent(
                    type="progress",
                    stage="explain",
                    percent=50,
                    message="Running EXPLAIN...",
                )

            with patch.object(service, "_get_workflow_path", return_value=mock_path):
                with patch.object(
                    service,
                    "_run_workflow_with_progress",
                    new_callable=AsyncMock,
                    return_value=(mock_progress_gen(), workflow_result),
                ):
                    async for event in service._run_parallel_analysis(
                        input=input_data,
                        options=options,
                        target_name="test-target",
                        target_config={"host": "localhost"},
                        workflow_path=mock_path,
                    ):
                        events.append(event)

        assert events[-1].type == "error"
        assert "timed out" in events[-1].message.lower()

    @pytest.mark.asyncio
    async def test_cancelled_operation(self, service, input_data, options):
        """Test handling cancelled operations."""
        events = []
        import asyncio

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.side_effect = asyncio.CancelledError("Operation cancelled")

            try:
                async for event in service.analyze(input_data, options):
                    events.append(event)
            except asyncio.CancelledError:
                pass

        # Should have at least initial progress event
        assert len(events) >= 1


class TestAnalyzeServiceNetworkFailures:
    """Tests for network failure simulations."""

    @pytest.fixture
    def service(self):
        """Create AnalyzeService instance."""
        return AnalyzeService()

    @pytest.fixture
    def input_data(self):
        """Create test input data."""
        return AnalyzeInput(
            sql="SELECT * FROM users WHERE id = 1",
            normalized_sql="select * from users where id = ?",
            source="test",
            hash="test_hash_123",
        )

    @pytest.fixture
    def options(self):
        """Create test options."""
        return AnalyzeOptions(target="test-target")

    @pytest.mark.asyncio
    async def test_database_connection_lost(self, service, input_data, options):
        """Test handling lost database connection."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = ("test-target", {"host": "localhost"})

            mock_path = Mock()
            mock_path.exists.return_value = True

            async def failing_analysis_gen(*args, **kwargs):
                raise ConnectionResetError("Connection reset by peer")
                yield  # Make it an async generator

            with patch.object(service, "_get_workflow_path", return_value=mock_path):
                with patch.object(
                    service, "_run_parallel_analysis", side_effect=failing_analysis_gen
                ):
                    async for event in service.analyze(input_data, options):
                        events.append(event)

        assert events[-1].type == "error"

    @pytest.mark.asyncio
    async def test_network_unreachable(self, service, input_data, options):
        """Test handling unreachable network."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = ("test-target", {"host": "192.168.1.100"})

            mock_path = Mock()
            mock_path.exists.return_value = True

            async def network_error_gen(*args, **kwargs):
                raise OSError("Network is unreachable")
                yield  # Make it an async generator

            with patch.object(service, "_get_workflow_path", return_value=mock_path):
                with patch.object(
                    service, "_run_parallel_analysis", side_effect=network_error_gen
                ):
                    async for event in service.analyze(input_data, options):
                        events.append(event)

        assert events[-1].type == "error"
        assert "unreachable" in events[-1].message.lower()

    @pytest.mark.asyncio
    async def test_dns_resolution_failure(self, service, input_data, options):
        """Test handling DNS resolution failures."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = (
                "test-target",
                {"host": "nonexistent.invalid.domain"},
            )

            mock_path = Mock()
            mock_path.exists.return_value = True

            async def dns_error_gen(*args, **kwargs):
                raise OSError("Name or service not known")
                yield  # Make it an async generator

            with patch.object(service, "_get_workflow_path", return_value=mock_path):
                with patch.object(
                    service, "_run_parallel_analysis", side_effect=dns_error_gen
                ):
                    async for event in service.analyze(input_data, options):
                        events.append(event)

        assert events[-1].type == "error"

    @pytest.mark.asyncio
    async def test_readyset_connection_failure_non_fatal(self, service, input_data):
        """Test that Readyset connection failure is handled gracefully."""
        options = AnalyzeOptions(target="test-target", readyset_cache=True)
        events = []

        # Mock workflow execution to succeed
        workflow_result = {
            "success": True,
            "result": {
                "explain_results": {"success": True, "execution_time_ms": 10.5},
                "FormatFinalResults": {"summary": "test"},
            },
        }

        async def mock_progress_gen():
            yield ProgressEvent(
                type="progress",
                stage="validating",
                percent=5,
                message="Validating...",
            )

        with patch.object(
            service,
            "_run_workflow_with_progress",
            new_callable=AsyncMock,
            return_value=(mock_progress_gen(), workflow_result),
        ):
            # Readyset fails with network error
            with patch.object(
                service,
                "_run_readyset_analysis_sync",
                side_effect=ConnectionError("Readyset unreachable"),
            ):
                mock_path = Mock()
                mock_path.exists.return_value = True

                async for event in service._run_parallel_analysis(
                    input=input_data,
                    options=options,
                    target_name="test-target",
                    target_config={"host": "localhost"},
                    workflow_path=mock_path,
                ):
                    events.append(event)

        # Should complete despite Readyset failure
        complete_events = [e for e in events if e.type == "complete"]
        assert len(complete_events) == 1
        assert complete_events[0].success is True

    @pytest.mark.asyncio
    async def test_partial_results_preserved_on_failure(
        self, service, input_data, options
    ):
        """Test that partial results are preserved when later steps fail."""
        events = []

        with patch.object(service, "_load_config", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = ("test-target", {"host": "localhost"})

            # Workflow returns partial success with error
            workflow_result = {
                "success": False,
                "error": "LLM analysis failed",
                "result": {
                    "explain_results": {
                        "success": True,
                        "execution_time_ms": 10.5,
                        "rows_examined": 100,
                    },
                    # LLM analysis missing due to failure
                },
            }

            async for event in service._process_results(
                workflow_result=workflow_result,
                readyset_result=None,
                input=input_data,
            ):
                events.append(event)

        # Should have error with partial results
        assert events[-1].type == "error"
        assert events[-1].partial_results is not None

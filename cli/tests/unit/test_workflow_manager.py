"""
Unit tests for WorkflowManager.

Tests the workflow execution engine including step execution, retry logic,
template rendering, and parallel branch execution.
"""

import pytest
import importlib.util
import sys
import time
from pathlib import Path

# Import module directly to avoid package __init__.py issues
def _import_module_directly(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

_lib_path = Path(__file__).parent.parent.parent / "lib"
workflow_manager = _import_module_directly(
    "workflow_manager",
    _lib_path / "workflow_manager" / "workflow_manager.py"
)

WorkflowManager = workflow_manager.WorkflowManager
WorkflowStatus = workflow_manager.WorkflowStatus
WorkflowError = workflow_manager.WorkflowError
RetryableError = workflow_manager.RetryableError
StepResult = workflow_manager.StepResult
WorkflowExecution = workflow_manager.WorkflowExecution
_stringify = workflow_manager._stringify


class TestWorkflowStatus:
    """Tests for WorkflowStatus enum."""

    def test_status_values(self):
        """Test all status values exist."""
        assert WorkflowStatus.PENDING.value == "pending"
        assert WorkflowStatus.RUNNING.value == "running"
        assert WorkflowStatus.COMPLETED.value == "completed"
        assert WorkflowStatus.FAILED.value == "failed"
        assert WorkflowStatus.RETRYING.value == "retrying"


class TestStepResult:
    """Tests for StepResult dataclass."""

    def test_default_values(self):
        """Test default values."""
        result = StepResult(step_name="test", status=WorkflowStatus.PENDING)

        assert result.step_name == "test"
        assert result.status == WorkflowStatus.PENDING
        assert result.result is None
        assert result.error is None
        assert result.retry_count == 0


class TestWorkflowExecution:
    """Tests for WorkflowExecution dataclass."""

    def test_default_values(self):
        """Test default values."""
        execution = WorkflowExecution(workflow_id="test-123")

        assert execution.workflow_id == "test-123"
        assert execution.status == WorkflowStatus.PENDING
        assert execution.current_step is None
        assert execution.steps == {}
        assert execution.context == {}


class TestStringify:
    """Tests for _stringify helper function."""

    def test_none(self):
        """Test None becomes empty string."""
        assert _stringify(None) == ""

    def test_string(self):
        """Test string is returned as-is."""
        assert _stringify("hello") == "hello"

    def test_int(self):
        """Test int is converted to string."""
        assert _stringify(42) == "42"

    def test_float(self):
        """Test float is converted to string."""
        assert _stringify(3.14) == "3.14"

    def test_bool(self):
        """Test bool is converted to string."""
        assert _stringify(True) == "True"
        assert _stringify(False) == "False"

    def test_dict(self):
        """Test dict is JSON serialized."""
        result = _stringify({"key": "value"})
        assert result == '{"key": "value"}'

    def test_list(self):
        """Test list is JSON serialized."""
        result = _stringify([1, 2, 3])
        assert result == '[1, 2, 3]'


class TestWorkflowManagerBasic:
    """Basic tests for WorkflowManager."""

    def test_initialization(self):
        """Test manager initialization."""
        manager = WorkflowManager()

        assert manager.resources is not None
        assert manager.llm_manager is not None
        assert manager._executions == {}

    def test_register_default_functions(self):
        """Test default functions are registered."""
        manager = WorkflowManager()

        assert "get_db_size" in manager.resources
        assert "get_table_count" in manager.resources
        assert "get_query_stats" in manager.resources
        assert "analyze_schema" in manager.resources
        assert "call_llm" in manager.resources

    def test_custom_resources(self):
        """Test custom resources can be provided."""
        custom_resources = {"my_func": lambda: "result"}
        manager = WorkflowManager(resources=custom_resources)

        assert "my_func" in manager.resources

    def test_from_dict(self):
        """Test creating manager from dict."""
        workflow = {
            "StartAt": "FirstStep",
            "States": {
                "FirstStep": {
                    "Type": "Task",
                    "Resource": "get_db_size",
                    "End": True
                }
            }
        }

        manager = WorkflowManager.from_dict(workflow)
        assert hasattr(manager, "_workflow")


class TestWorkflowManagerExecution:
    """Tests for workflow execution."""

    @pytest.fixture
    def simple_workflow(self):
        """Create a simple test workflow."""
        return {
            "StartAt": "GetSize",
            "States": {
                "GetSize": {
                    "Type": "Task",
                    "Resource": "get_db_size",
                    "End": True
                }
            }
        }

    @pytest.fixture
    def multi_step_workflow(self):
        """Create a multi-step workflow."""
        return {
            "StartAt": "Step1",
            "States": {
                "Step1": {
                    "Type": "Task",
                    "Resource": "get_db_size",
                    "Next": "Step2"
                },
                "Step2": {
                    "Type": "Task",
                    "Resource": "get_table_count",
                    "End": True
                }
            }
        }

    def test_run_simple_workflow(self, simple_workflow):
        """Test running a simple workflow."""
        manager = WorkflowManager.from_dict(simple_workflow)
        result = manager.run()

        assert "GetSize" in result
        assert result["States"]["GetSize"]["size_mb"] is not None

    def test_run_multi_step_workflow(self, multi_step_workflow):
        """Test running multi-step workflow."""
        manager = WorkflowManager.from_dict(multi_step_workflow)
        result = manager.run()

        assert "Step1" in result["States"]
        assert "Step2" in result["States"]

    def test_workflow_with_initial_input(self, simple_workflow):
        """Test workflow with initial input."""
        manager = WorkflowManager.from_dict(simple_workflow)
        result = manager.run(initial_input={"custom_key": "custom_value"})

        assert result.get("custom_key") == "custom_value"

    def test_missing_start_at_raises(self):
        """Test missing StartAt raises error."""
        workflow = {"States": {"Step1": {"Type": "Task", "End": True}}}
        manager = WorkflowManager.from_dict(workflow)

        with pytest.raises(WorkflowError, match="StartAt"):
            manager.run()

    def test_missing_state_raises(self):
        """Test missing state raises error."""
        workflow = {
            "StartAt": "NonExistent",
            "States": {}
        }
        manager = WorkflowManager.from_dict(workflow)

        with pytest.raises(WorkflowError, match="not found"):
            manager.run()

    def test_missing_resource_raises(self):
        """Test missing resource raises error."""
        workflow = {
            "StartAt": "Step1",
            "States": {
                "Step1": {
                    "Type": "Task",
                    "Resource": "nonexistent_function",
                    "End": True
                }
            }
        }
        manager = WorkflowManager.from_dict(workflow)

        with pytest.raises(WorkflowError, match="not found"):
            manager.run()


class TestPathSelection:
    """Tests for JSONPath-like path selection."""

    def test_select_full_data(self):
        """Test selecting full data with $."""
        manager = WorkflowManager()
        data = {"key": "value"}

        result = manager._select_path(data, "$")
        assert result == data

    def test_select_nested_path(self):
        """Test selecting nested path."""
        manager = WorkflowManager()
        data = {"a": {"b": {"c": "value"}}}

        result = manager._select_path(data, "$.a.b.c")
        assert result == "value"

    def test_select_none_returns_full_data(self):
        """Test None path returns full data."""
        manager = WorkflowManager()
        data = {"key": "value"}

        result = manager._select_path(data, None)
        assert result == data

    def test_select_array_index(self):
        """Test selecting array index."""
        manager = WorkflowManager()
        data = {"items": ["a", "b", "c"]}

        result = manager._select_path(data, "$.items.1")
        assert result == "b"

    def test_select_missing_key(self):
        """Test selecting missing key returns None."""
        manager = WorkflowManager()
        data = {"key": "value"}

        result = manager._select_path(data, "$.nonexistent")
        assert result is None


class TestPathAssignment:
    """Tests for JSONPath-like path assignment."""

    def test_assign_simple_path(self):
        """Test assigning to simple path."""
        manager = WorkflowManager()
        data = {}

        manager._assign_path(data, "$.result", "value")
        assert data["result"] == "value"

    def test_assign_nested_path(self):
        """Test assigning to nested path."""
        manager = WorkflowManager()
        data = {}

        manager._assign_path(data, "$.a.b.c", "nested")
        assert data["a"]["b"]["c"] == "nested"

    def test_invalid_path_raises(self):
        """Test invalid path raises error."""
        manager = WorkflowManager()
        data = {}

        with pytest.raises(WorkflowError):
            manager._assign_path(data, "invalid", "value")


class TestTemplateRendering:
    """Tests for template placeholder rendering."""

    def test_simple_placeholder(self):
        """Test rendering simple placeholder."""
        manager = WorkflowManager()
        context = {"name": "John"}

        result = manager._render_templates("Hello {{ name }}", context)
        assert result == "Hello John"

    def test_dotted_path_placeholder(self):
        """Test rendering dotted path placeholder."""
        manager = WorkflowManager()
        context = {"user": {"name": "Jane"}}

        result = manager._render_templates("Hello {{ user.name }}", context)
        assert result == "Hello Jane"

    def test_absolute_path_placeholder(self):
        """Test rendering absolute path placeholder."""
        manager = WorkflowManager()
        context = {"data": {"value": 42}}

        result = manager._render_templates("Value: {{ $.data.value }}", context)
        assert result == "Value: 42"

    def test_dict_placeholder(self):
        """Test rendering dict placeholder."""
        manager = WorkflowManager()
        context = {"config": {"a": 1, "b": 2}}

        result = manager._render_templates("Config: {{ config }}", context)
        # Should be JSON serialized
        assert '"a": 1' in result

    def test_nested_dict_rendering(self):
        """Test rendering templates in nested dicts."""
        manager = WorkflowManager()
        context = {"value": "test"}

        obj = {"outer": {"inner": "{{ value }}"}}
        result = manager._render_templates(obj, context)

        assert result["outer"]["inner"] == "test"

    def test_list_rendering(self):
        """Test rendering templates in lists."""
        manager = WorkflowManager()
        context = {"item": "x"}

        obj = ["{{ item }}", "static"]
        result = manager._render_templates(obj, context)

        assert result == ["x", "static"]


class TestRetryLogic:
    """Tests for retry logic."""

    @pytest.fixture
    def retry_workflow(self):
        """Create a workflow with retry configuration."""
        return {
            "StartAt": "RetryStep",
            "States": {
                "RetryStep": {
                    "Type": "Task",
                    "Resource": "flaky_function",
                    "Retry": {
                        "MaxAttempts": 3,
                        "IntervalSeconds": 0.01,  # Short for testing
                        "BackoffRate": 2.0
                    },
                    "End": True
                }
            }
        }

    def test_successful_after_retry(self, retry_workflow):
        """Test function succeeds after retry."""
        attempt_count = [0]

        def flaky_function(**_):
            attempt_count[0] += 1
            if attempt_count[0] < 3:
                raise RetryableError("Temporary failure")
            return {"success": True}

        resources = {"flaky_function": flaky_function}
        manager = WorkflowManager.from_dict(retry_workflow, resources=resources)

        result = manager.run()

        assert result["States"]["RetryStep"]["success"] is True
        assert attempt_count[0] == 3

    def test_fail_after_max_retries(self, retry_workflow):
        """Test failure after max retries exhausted."""
        def always_fails(**_):
            raise RetryableError("Always fails")

        resources = {"always_fails": always_fails}
        workflow = retry_workflow.copy()
        workflow["States"]["RetryStep"]["Resource"] = "always_fails"

        manager = WorkflowManager.from_dict(workflow, resources=resources)

        with pytest.raises(WorkflowError):
            manager.run()


class TestAsyncExecution:
    """Tests for async workflow execution."""

    @pytest.fixture
    def simple_workflow(self):
        """Create a simple workflow for async testing."""
        return {
            "StartAt": "Step1",
            "States": {
                "Step1": {
                    "Type": "Task",
                    "Resource": "get_db_size",
                    "End": True
                }
            }
        }

    def test_run_async(self, simple_workflow):
        """Test async workflow execution."""
        manager = WorkflowManager.from_dict(simple_workflow)
        exec_id = manager.run_async()

        assert exec_id is not None

        # Wait for completion
        time.sleep(0.5)

        status = manager.get_workflow_status(exec_id)
        assert status is not None
        assert status.status in [WorkflowStatus.COMPLETED, WorkflowStatus.RUNNING]

    def test_get_workflow_status(self, simple_workflow):
        """Test getting workflow status."""
        manager = WorkflowManager.from_dict(simple_workflow)
        exec_id = manager.run_async()

        status = manager.get_workflow_status(exec_id)

        assert status is not None
        assert status.workflow_id == exec_id

    def test_nonexistent_workflow_status(self):
        """Test getting status of nonexistent workflow."""
        manager = WorkflowManager()

        status = manager.get_workflow_status("nonexistent-id")
        assert status is None


class TestParallelExecution:
    """Tests for parallel branch execution."""

    @pytest.fixture
    def parallel_workflow(self):
        """Create a workflow with parallel branches."""
        return {
            "StartAt": "ParallelStep",
            "States": {
                "ParallelStep": {
                    "Type": "Parallel",
                    "Branches": [
                        {
                            "StartAt": "Branch1",
                            "States": {
                                "Branch1": {
                                    "Type": "Task",
                                    "Resource": "get_db_size",
                                    "End": True
                                }
                            }
                        },
                        {
                            "StartAt": "Branch2",
                            "States": {
                                "Branch2": {
                                    "Type": "Task",
                                    "Resource": "get_table_count",
                                    "End": True
                                }
                            }
                        }
                    ],
                    "End": True
                }
            }
        }

    def test_parallel_execution(self, parallel_workflow):
        """Test parallel branch execution."""
        manager = WorkflowManager.from_dict(parallel_workflow)
        result = manager.run()

        # Result should contain array of branch results
        parallel_result = result.get("ParallelStep")
        assert isinstance(parallel_result, list)
        assert len(parallel_result) == 2

    def test_parallel_empty_branches_raises(self):
        """Test parallel with empty branches raises error."""
        workflow = {
            "StartAt": "ParallelStep",
            "States": {
                "ParallelStep": {
                    "Type": "Parallel",
                    "Branches": [],
                    "End": True
                }
            }
        }

        manager = WorkflowManager.from_dict(workflow)

        with pytest.raises(WorkflowError, match="non-empty"):
            manager.run()


class TestDefaultFunctions:
    """Tests for default workflow functions."""

    def test_get_db_size(self):
        """Test get_db_size returns expected structure."""
        result = workflow_manager.get_db_size()

        assert "size_mb" in result
        assert "size_gb" in result
        assert "last_updated" in result

    def test_get_table_count(self):
        """Test get_table_count returns expected structure."""
        result = workflow_manager.get_table_count()

        assert "table_count" in result
        assert "view_count" in result
        assert "last_updated" in result

    def test_get_query_stats(self):
        """Test get_query_stats returns expected structure."""
        result = workflow_manager.get_query_stats()

        assert "total_queries" in result
        assert "avg_response_time_ms" in result
        assert "slow_queries" in result

    def test_analyze_schema(self):
        """Test analyze_schema returns expected structure."""
        result = workflow_manager.analyze_schema()

        assert "tables" in result
        assert "indexes" in result
        assert "recommendations" in result

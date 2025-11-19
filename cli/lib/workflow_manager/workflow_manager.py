from __future__ import annotations
import json, re, threading, time, logging
from typing import Any, Callable, Dict, Optional
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from lib.llm_manager.llm_manager import LLMManager

Json = Dict[str, Any]
ResourceRegistry = Dict[str, Callable[..., Any]]

class WorkflowStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"

@dataclass
class StepResult:
    step_name: str
    status: WorkflowStatus
    result: Any = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    retry_count: int = 0

@dataclass
class WorkflowExecution:
    workflow_id: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    current_step: Optional[str] = None
    steps: Dict[str, StepResult] = field(default_factory=dict)
    context: Json = field(default_factory=dict)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    thread: Optional[threading.Thread] = None

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")

class WorkflowError(Exception):
    pass

class RetryableError(WorkflowError):
    pass

def get_db_size(**kwargs) -> Dict[str, Any]:
    """Get database size information"""
    # TODO: Implement actual database size query
    return {
        "size_mb": 1024.5,
        "size_gb": 1.0,
        "last_updated": datetime.now().isoformat()
    }

def get_table_count(**kwargs) -> Dict[str, Any]:
    """Get number of tables in database"""
    # TODO: Implement actual table count query
    return {
        "table_count": 42,
        "view_count": 15,
        "last_updated": datetime.now().isoformat()
    }

def get_query_stats(**kwargs) -> Dict[str, Any]:
    """Get query performance statistics"""
    # TODO: Implement actual query stats
    return {
        "total_queries": 12456,
        "avg_response_time_ms": 45.2,
        "slow_queries": 23,
        "last_updated": datetime.now().isoformat()
    }

def analyze_schema(**kwargs) -> Dict[str, Any]:
    """Analyze database schema"""
    # TODO: Implement actual schema analysis
    return {
        "tables": 42,
        "indexes": 156,
        "foreign_keys": 89,
        "recommendations": ["Add index on users.email", "Consider partitioning logs table"],
        "last_updated": datetime.now().isoformat()
    }

def call_llm(prompt: str, model: str = "gpt-4", **kwargs) -> Dict[str, Any]:
    """Call LLM with given prompt"""
    llm = LLMManager()
    return llm.generate_response(prompt, model=model, **kwargs)

DEFAULT_FUNCTIONS = {
    "get_db_size": get_db_size,
    "get_table_count": get_table_count,
    "get_query_stats": get_query_stats,
    "analyze_schema": analyze_schema,
    "call_llm": call_llm,
}


class WorkflowManager:
    """
    Enhanced StepFunctions-like workflow execution engine.

    Supported fields per state:
      Type: "Task"
      Resource: str  (name in registry)
      Parameters: dict (static args, templated)
      InputPath: "$.foo.bar"
      ResultPath: "$.path.to.put.result" (defaults to "$.<StateName>")
      Next: str
      End: bool
      Retry: dict (retry configuration)
      TimeoutSeconds: int (step timeout)
    """

    def __init__(self, resources: Optional[ResourceRegistry] = None, llm_manager: Optional[LLMManager] = None):
        self.resources = resources or DEFAULT_FUNCTIONS.copy()
        self.llm_manager = llm_manager or LLMManager()
        self._executions: Dict[str, WorkflowExecution] = {}
        self._execution_lock = threading.Lock()
        self.logger = logging.getLogger(self.__class__.__name__)
        self._executor = ThreadPoolExecutor(max_workers=4)

    @classmethod
    def from_file(cls, path: str, resources: Optional[ResourceRegistry] = None) -> "WorkflowManager":
        with open(path, "r") as f:
            wf = json.load(f)
        mgr = cls(resources)
        mgr._workflow = wf
        return mgr

    @classmethod
    def from_dict(cls, workflow: Json, resources: Optional[ResourceRegistry] = None) -> "WorkflowManager":
        mgr = cls(resources)
        mgr._workflow = workflow
        return mgr

    def run_async(self, workflow_id: Optional[str] = None, initial_input: Optional[Json] = None) -> str:
        """Start workflow execution in a separate thread and return execution ID"""
        exec_id = workflow_id or f"workflow_{int(datetime.now().timestamp() * 1000)}"

        with self._execution_lock:
            execution = WorkflowExecution(
                workflow_id=exec_id,
                status=WorkflowStatus.PENDING,
                context=initial_input.copy() if initial_input else {},
                started_at=datetime.now()
            )
            self._executions[exec_id] = execution

        # Start execution in separate thread
        thread = threading.Thread(
            target=self._run_workflow_thread,
            args=(exec_id, initial_input),
            daemon=True
        )
        thread.start()
        execution.thread = thread

        return exec_id

    def get_workflow_status(self, workflow_id: str) -> Optional[WorkflowExecution]:
        """Get current status of a workflow execution"""
        return self._executions.get(workflow_id)

    def _run_workflow_thread(self, workflow_id: str, initial_input: Optional[Json]):
        """Execute workflow in thread with full error handling and retry logic"""
        execution = self._executions[workflow_id]

        try:
            execution.status = WorkflowStatus.RUNNING
            result = self._run_workflow_with_retry(execution, initial_input)
            execution.context.update(result)
            execution.status = WorkflowStatus.COMPLETED
            execution.completed_at = datetime.now()
        except Exception as e:
            execution.status = WorkflowStatus.FAILED
            execution.completed_at = datetime.now()
            self.logger.error(f"Workflow {workflow_id} failed: {e}", exc_info=True)

    def _run_workflow_with_retry(self, execution: WorkflowExecution, initial_input: Optional[Json]) -> Json:
        """Execute workflow with retry logic and step tracking"""
        wf = getattr(self, "_workflow", None)
        if not wf:
            raise WorkflowError("No workflow loaded. Use from_file or from_dict.")

        states: Json = wf.get("States") or {}
        current = wf.get("StartAt")
        if not current:
            raise WorkflowError("Workflow missing required 'StartAt'.")

        # Initialize execution context
        context: Json = execution.context.copy()
        if initial_input:
            context.update(initial_input)
        context.setdefault("States", {})

        # Initialize step tracking
        for state_name in states.keys():
            execution.steps[state_name] = StepResult(
                step_name=state_name,
                status=WorkflowStatus.PENDING
            )

        while current:
            execution.current_step = current
            state_name = current
            state = states.get(state_name)
            if not state:
                raise WorkflowError(f"State '{state_name}' not found in workflow.")

            step_result = execution.steps[state_name]
            step_result.status = WorkflowStatus.RUNNING
            step_result.started_at = datetime.now()

            try:
                # Execute step with retry logic
                result = self._execute_step_with_retry(state, context, state_name)

                # Store result
                result_path = state.get("ResultPath") or f"$.{state_name}"
                self._assign_path(context, result_path, result)
                context["States"][state_name] = result

                step_result.result = result
                step_result.status = WorkflowStatus.COMPLETED
                step_result.completed_at = datetime.now()

            except Exception as e:
                step_result.error = str(e)
                step_result.status = WorkflowStatus.FAILED
                step_result.completed_at = datetime.now()
                raise WorkflowError(f"Step '{state_name}' failed: {e}")

            # Transition
            if state.get("End") is True:
                break
            current = state.get("Next")
            if not current:
                raise WorkflowError(f"State '{state_name}' has no Next and is not End=true.")

        execution.current_step = None
        return context

    def _execute_step_with_retry(self, state: Json, context: Json, state_name: str) -> Any:
        """Execute a single step with retry logic"""
        retry_config = state.get("Retry", {})
        max_attempts = retry_config.get("MaxAttempts", 1)
        backoff_rate = retry_config.get("BackoffRate", 2.0)
        interval_seconds = retry_config.get("IntervalSeconds", 1.0)

        last_exception = None

        for attempt in range(max_attempts):
            try:
                if attempt > 0:
                    # Wait before retry
                    wait_time = interval_seconds * (backoff_rate ** (attempt - 1))
                    time.sleep(wait_time)

                return self._execute_single_step(state, context, state_name)

            except RetryableError as e:
                last_exception = e
                if attempt == max_attempts - 1:
                    break
                # Mark as retrying
                continue
            except Exception as e:
                # Non-retryable error
                raise e

        raise last_exception or WorkflowError(f"Step '{state_name}' failed after {max_attempts} attempts")

    def _execute_single_step(self, state: Json, context: Json, state_name: str) -> Any:
        """Execute a single workflow step"""
        state_type = state.get("Type")

        # Built-in Parallel support: run branches concurrently and return list of branch contexts
        if state_type == "Parallel":
            branches = state.get("Branches") or []
            if not isinstance(branches, list) or not branches:
                raise WorkflowError(f"State '{state_name}' Parallel requires non-empty 'Branches'.")

            # Run each branch as its own workflow with a copy of the current context
            futures = []
            results = [None] * len(branches)

            def run_branch(idx: int, branch_def: Json) -> Json:
                # Validate branch
                start_at = branch_def.get("StartAt")
                states = branch_def.get("States") or {}
                if not start_at or not states:
                    raise WorkflowError(f"Parallel branch {idx} missing StartAt/States")

                # Child manager shares the same resources/LLM manager
                child = WorkflowManager(resources=self.resources, llm_manager=self.llm_manager)
                child._workflow = {"StartAt": start_at, "States": states}

                # Pass a shallow copy of parent context to avoid cross-branch mutation
                branch_input = context.copy() if isinstance(context, dict) else {}
                branch_ctx = child.run(initial_input=branch_input)
                return branch_ctx

            for i, br in enumerate(branches):
                futures.append(self._executor.submit(run_branch, i, br))

            # Collect results (propagate first exception)
            for i, fut in enumerate(futures):
                results[i] = fut.result()

            return results

        if state_type != "Task":
            raise WorkflowError(f"Only Type=Task or Parallel is supported (got {state_type}).")

        resource_name: str = state.get("Resource")
        if resource_name not in self.resources:
            raise WorkflowError(f"Resource '{resource_name}' not found in registry.")
        func = self.resources[resource_name]

        # Resolve Input
        selected_input = self._select_path(context, state.get("InputPath"))
        # Resolve Parameters (with template expansion)
        raw_params = state.get("Parameters") or {}
        params = self._render_templates(raw_params, context)

        # Merge: function receives both selected_input and params.
        call_args: Any
        if isinstance(selected_input, dict) and isinstance(params, dict):
            call_args = {**selected_input, **params}
        elif selected_input is None:
            call_args = params
        elif params == {}:
            call_args = selected_input
        else:
            call_args = {"input": selected_input, **(params if isinstance(params, dict) else {"params": params})}

        # Call the resource with timeout if specified
        timeout = state.get("TimeoutSeconds")
        if timeout:
            try:
                future = self._executor.submit(
                    lambda: func(**call_args) if isinstance(call_args, dict) else func(call_args)
                )
                return future.result(timeout=timeout)
            except FutureTimeoutError:
                # Format timeout message nicely (show minutes if >= 60 seconds)
                if timeout >= 60:
                    timeout_str = f"{timeout // 60} minute{'s' if timeout // 60 != 1 else ''}"
                    if timeout % 60 > 0:
                        timeout_str += f" {timeout % 60} second{'s' if timeout % 60 != 1 else ''}"
                else:
                    timeout_str = f"{timeout} second{'s' if timeout != 1 else ''}"
                raise WorkflowError(f"Step '{state_name}' timed out after {timeout_str}. This query is taking too long to execute on the database. Consider adding indexes or optimizing the query before analyzing.")
        else:
            return func(**call_args) if isinstance(call_args, dict) else func(call_args)

    def run(self, initial_input: Optional[Json] = None) -> Json:
        """Synchronous workflow execution (for backward compatibility)"""
        workflow_id = f"sync_workflow_{int(datetime.now().timestamp() * 1000)}"

        # Create execution tracking
        execution = WorkflowExecution(
            workflow_id=workflow_id,
            status=WorkflowStatus.RUNNING,
            context=initial_input.copy() if initial_input else {},
            started_at=datetime.now()
        )

        with self._execution_lock:
            self._executions[workflow_id] = execution

        try:
            result = self._run_workflow_with_retry(execution, initial_input)
            execution.status = WorkflowStatus.COMPLETED
            execution.completed_at = datetime.now()
            return result
        except Exception as e:
            execution.status = WorkflowStatus.FAILED
            execution.completed_at = datetime.now()
            raise e

    def _select_path(self, data: Json, path: Optional[str]) -> Any:
        """Very small subset of JSONPath: '$.a.b.c' or '$.a.0.b'. None -> full data."""
        if not path:
            return data
        if path == "$":
            return data
        if not path.startswith("$."):
            raise WorkflowError(f"Unsupported InputPath/ResultPath '{path}'. Use '$.a.b.c'.")
        cur: Any = data
        for key in path[2:].split("."):
            if key == "":
                continue
            # Handle array indexing (e.g., $.parallel_results.0)
            if isinstance(cur, list):
                try:
                    index = int(key)
                    if 0 <= index < len(cur):
                        cur = cur[index]
                    else:
                        return None
                except (ValueError, TypeError):
                    return None
            elif isinstance(cur, dict):
                cur = cur.get(key)
            else:
                return None
        return cur

    def _assign_path(self, data: Json, path: str, value: Any) -> None:
        if not path or not path.startswith("$."):
            raise WorkflowError(f"Unsupported ResultPath '{path}'. Use '$.a.b.c'.")
        parts = path[2:].split(".")
        cur = data
        for p in parts[:-1]:
            if p not in cur or not isinstance(cur[p], dict):
                cur[p] = {}
            cur = cur[p]
        cur[parts[-1]] = value

    def _render_templates(self, obj: Any, context: Json) -> Any:
        """
        Recursively replace {{ ... }} placeholders in strings.
        Expressions support dotted lookups, e.g. States.SummarizeData.summary
        and absolute paths like $.summary.
        """
        if isinstance(obj, dict):
            return {k: self._render_templates(v, context) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._render_templates(v, context) for v in obj]
        if isinstance(obj, str):
            def repl(match: re.Match) -> str:
                expr = match.group(1).strip()
                if expr.startswith("$"):
                    val = self._select_path(context, expr)
                else:
                    # dotted path starting from context root
                    val = self._lookup_dotted(context, expr)
                return _stringify(val)
            return _PLACEHOLDER_RE.sub(repl, obj)
        return obj

    def _lookup_dotted(self, data: Json, dotted: str) -> Any:
        cur: Any = data
        for part in dotted.split("."):
            # Handle array indexing (e.g., parallel_results.0)
            if isinstance(cur, list):
                try:
                    index = int(part)
                    if 0 <= index < len(cur):
                        cur = cur[index]
                    else:
                        return None
                except (ValueError, TypeError):
                    return None
            elif isinstance(cur, dict):
                cur = cur.get(part)
            else:
                return None
        return cur


def _stringify(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, (str, int, float, bool)):
        return str(val)
    try:
        return json.dumps(val, ensure_ascii=False)
    except Exception:
        return str(val)

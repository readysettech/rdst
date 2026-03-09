"""AnalyzeService - Async generator-based query analysis service.

This service provides the core analysis logic extracted from the API and CLI,
exposing an async generator interface that yields events during execution.
"""

import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional, Tuple

from .types import (
    AnalyzeEvent,
    AnalyzeInput,
    AnalyzeOptions,
    CompleteEvent,
    ErrorEvent,
    ExplainCompleteEvent,
    ProgressEvent,
    ReadysetCheckedEvent,
    RewritesTestedEvent,
)


# Step progress mapping (extracted from API routes)
STEP_PROGRESS: Dict[str, tuple[str, int, str]] = {
    "ValidateQuerySafety": ("validating", 5, "Validating query safety..."),
    "NormalizeForRegistry": ("normalizing", 10, "Normalizing query..."),
    "ParameterizeForLLM": ("normalizing", 12, "Parameterizing for LLM..."),
    "ExecuteExplainAnalyze": ("executing_explain", 20, "Running EXPLAIN ANALYZE..."),
    "CollectQueryMetrics": ("collecting_metrics", 30, "Collecting query metrics..."),
    "CollectDatabaseSchema": ("collecting_schema", 35, "Collecting schema context..."),
    "PerformLLMAnalysis": ("analyzing_llm", 50, "Analyzing with AI..."),
    "TestQueryRewrites": ("testing_rewrites", 70, "Testing query rewrites..."),
    "CheckReadysetCacheability": (
        "checking_readyset",
        85,
        "Checking Readyset cacheability...",
    ),
    "StoreAnalysisResults": ("storing_results", 95, "Storing results..."),
    "FormatFinalResults": ("complete", 100, "Formatting results..."),
}


def _serialize_for_json(obj: Any) -> Any:
    """Recursively serialize objects for JSON compatibility."""
    if isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_serialize_for_json(v) for v in obj]
    elif hasattr(obj, "__dict__") and not isinstance(obj, type):
        return str(obj)
    return obj


def _normalize_rewrite_testing_results(rewrite_results: Any) -> Dict[str, Any]:
    """Normalize rewrite testing payload to a stable schema for consumers."""
    if not isinstance(rewrite_results, dict):
        return {}

    normalized = dict(rewrite_results)
    tested = normalized.get("tested")
    if isinstance(tested, bool):
        return normalized

    if normalized.get("skipped_reason"):
        normalized["tested"] = False
        return normalized

    if normalized.get("success") is False:
        normalized["tested"] = False
        return normalized

    rewrite_candidates = normalized.get("rewrite_results")
    has_rewrites = isinstance(rewrite_candidates, list) and len(rewrite_candidates) > 0
    normalized["tested"] = bool(
        normalized.get("success") and (has_rewrites or normalized.get("best_rewrite"))
    )
    return normalized


class AnalyzeService:
    """Service for query analysis with async event streaming.

    This service wraps the blocking WorkflowManager execution and provides
    an async generator interface that yields typed events during analysis.

    Usage:
        service = AnalyzeService()
        async for event in service.analyze(input, options):
            if event.type == "progress":
                print(f"Progress: {event.percent}%")
            elif event.type == "complete":
                print(f"Analysis complete: {event.analysis_id}")
    """

    def __init__(self) -> None:
        """Initialize the analyze service."""
        pass

    async def analyze(
        self,
        input: AnalyzeInput,
        options: AnalyzeOptions,
    ) -> AsyncGenerator[AnalyzeEvent, None]:
        """Analyze query and yield events during execution.

        This async generator yields events as the analysis progresses:
        - ProgressEvent: Progress updates with stage, percent, and message
        - ExplainCompleteEvent: EXPLAIN ANALYZE results available
        - RewritesTestedEvent: Query rewrite testing complete
        - ReadysetCheckedEvent: Readyset cacheability check complete
        - CompleteEvent: Analysis complete (final event on success)
        - ErrorEvent: Error occurred (final event on failure)

        Args:
            input: Resolved analysis input with SQL and metadata
            options: Analysis options (target, fast mode, etc.)

        Yields:
            AnalyzeEvent: Typed events during analysis execution
        """
        try:
            # Initial progress
            yield ProgressEvent(
                type="progress",
                stage="loading_config",
                percent=2,
                message="Loading configuration...",
            )

            # Load configuration
            target_name, target_config = await self._load_config(options.target)
            if target_name is None:
                yield ErrorEvent(
                    type="error",
                    message="No target specified and no default configured",
                )
                return

            if target_config is None:
                yield ErrorEvent(
                    type="error",
                    message=f"Target '{target_name}' not found",
                )
                return

            # Load workflow
            workflow_path = self._get_workflow_path()
            if not workflow_path.exists():
                yield ErrorEvent(
                    type="error",
                    message=f"Workflow file not found: {workflow_path}",
                )
                return

            # Run parallel analysis (workflow + optional readyset)
            async for event in self._run_parallel_analysis(
                input=input,
                options=options,
                target_name=target_name,
                target_config=target_config,
                workflow_path=workflow_path,
            ):
                yield event

        except Exception as e:
            yield ErrorEvent(
                type="error",
                message=str(e),
            )

    async def _load_config(
        self, target: Optional[str]
    ) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Load target configuration.

        Args:
            target: Target name or None for default

        Returns:
            Tuple of (target_name, target_config) or (None, None) on error
        """
        from ..cli.rdst_cli import TargetsConfig

        cfg = TargetsConfig()
        cfg.load()
        target_name = target or cfg.get_default()

        if not target_name:
            return None, None

        target_config = cfg.get(target_name)
        return target_name, target_config

    def _get_workflow_path(self) -> Path:
        """Get path to the analyze workflow definition."""
        return (
            Path(__file__).parent.parent / "workflows" / "analyze_workflow_simple.json"
        )

    async def _run_parallel_analysis(
        self,
        input: AnalyzeInput,
        options: AnalyzeOptions,
        target_name: str,
        target_config: Dict[str, Any],
        workflow_path: Path,
    ) -> AsyncGenerator[AnalyzeEvent, None]:
        """Run workflow and optional readyset analysis in parallel.

        The workflow runs with async polling for progress updates.
        Readyset analysis (if enabled) runs in a separate thread.

        Args:
            input: Analysis input
            options: Analysis options
            target_name: Resolved target name
            target_config: Target configuration dict
            workflow_path: Path to workflow JSON file

        Yields:
            AnalyzeEvent: Events during parallel execution
        """
        progress_gen, result_holder = await self._run_workflow_with_progress(
            input=input,
            options=options,
            target_name=target_name,
            target_config=target_config,
            workflow_path=workflow_path,
        )

        readyset_task = None
        if options.readyset_cache:
            readyset_task = asyncio.to_thread(
                self._run_readyset_analysis_sync,
                input=input,
                target_name=target_name,
                target_config=target_config,
            )

        workflow_result = None
        try:
            async for event in progress_gen:
                yield event
            workflow_result = result_holder
        except Exception as e:
            yield ErrorEvent(
                type="error",
                message=f"Workflow failed: {str(e)}",
            )
            return

        if not workflow_result:
            workflow_result = result_holder

        readyset_result = None
        if readyset_task:
            try:
                readyset_result = await readyset_task
                if isinstance(readyset_result, Exception):
                    readyset_result = {
                        "success": False,
                        "error": f"Readyset analysis failed: {readyset_result}",
                    }
            except Exception as e:
                readyset_result = {
                    "success": False,
                    "error": f"Readyset analysis failed: {str(e)}",
                }

        async for event in self._process_results(
            workflow_result=workflow_result,
            readyset_result=readyset_result,
            input=input,
        ):
            yield event

    async def _run_workflow_with_progress(
        self,
        input: AnalyzeInput,
        options: AnalyzeOptions,
        target_name: str,
        target_config: Dict[str, Any],
        workflow_path: Path,
    ) -> Tuple[AsyncGenerator[ProgressEvent, None], Dict[str, Any]]:
        """Async workflow execution with progress polling.

        Uses WorkflowManager.run_async() to start workflow in background thread
        and polls for progress updates, yielding ProgressEvent for each step.

        Args:
            input: Analysis input
            options: Analysis options
            target_name: Target name
            target_config: Target configuration
            workflow_path: Path to workflow JSON

        Returns:
            Tuple of (async generator of progress events, workflow result dict)
        """
        from ..workflow_manager.workflow_manager import (
            WorkflowManager,
            DEFAULT_FUNCTIONS,
            WorkflowStatus,
        )
        from ..functions import ANALYZE_WORKFLOW_FUNCTIONS

        workflow_functions = {
            **DEFAULT_FUNCTIONS,
            **ANALYZE_WORKFLOW_FUNCTIONS,
        }

        mgr = WorkflowManager.from_file(
            str(workflow_path), resources=workflow_functions
        )

        initial_input = {
            "query": input.sql,
            "normalized_query": input.normalized_sql,
            "target": target_name,
            "target_config": target_config,
            "test_rewrites": options.test_rewrites,
            "llm_model": options.model,
            "save_as": input.save_as or "",
            "source": input.source,
            "fast_mode": options.fast,
        }

        workflow_id = mgr.run_async(initial_input=initial_input)
        result_holder: Dict[str, Any] = {}

        async def progress_events() -> AsyncGenerator[ProgressEvent, None]:
            last_step = None

            while True:
                await asyncio.sleep(0.3)
                execution = mgr.get_workflow_status(workflow_id)

                if not execution:
                    raise Exception(f"Workflow {workflow_id} not found")

                current_step = execution.current_step
                if current_step and current_step != last_step:
                    last_step = current_step
                    step_info = STEP_PROGRESS.get(
                        current_step,
                        (current_step.lower(), 50, f"Running {current_step}..."),
                    )
                    yield ProgressEvent(
                        type="progress",
                        stage=step_info[0],
                        percent=step_info[1],
                        message=step_info[2],
                    )

                if execution.status == WorkflowStatus.COMPLETED:
                    result_holder["success"] = True
                    result_holder["result"] = execution.context
                    break
                elif execution.status == WorkflowStatus.FAILED:
                    error_msg = "Workflow failed"
                    if current_step and current_step in execution.steps:
                        step_error = execution.steps[current_step].error
                        if step_error:
                            error_msg = f"Workflow failed: {step_error}"
                    result_holder["success"] = False
                    result_holder["error"] = error_msg
                    break

        return progress_events(), result_holder

    def _run_readyset_analysis_sync(
        self,
        input: AnalyzeInput,
        target_name: str,
        target_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Synchronous readyset analysis using shallow mode (runs in thread).

        Uses shallow caching - single Readyset container connecting directly
        to the upstream database without a test sub-container.

        Args:
            input: Analysis input with query
            target_name: Target database name
            target_config: Target configuration

        Returns:
            Readyset analysis result dict
        """
        import os

        try:
            from ..functions.readyset_container import (
                start_readyset_container_direct,
                wait_for_readyset_ready_shallow,
                check_readyset_container_status,
            )
            from ..functions.readyset_explain_cache import (
                explain_create_cache_readyset,
                create_cache_readyset,
                drop_cache_readyset,
                get_cache_id_for_query,
                warm_cache_and_measure,
            )

            # Resolve password from environment
            password = target_config.get("password", "")
            password_env = target_config.get("password_env")
            if password_env:
                password = os.environ.get(password_env, "")

            resolved_config = {**target_config, "password": password}

            engine = target_config.get("engine", "postgresql")
            readyset_port = 5433 if engine == "postgresql" else 3307
            container_name = f"rdst-readyset-{target_name}"

            # Check if container already running
            status = check_readyset_container_status(
                readyset_container_name=container_name
            )

            if not status.get("running"):
                # Start container in shallow mode (direct upstream connection)
                start_result = start_readyset_container_direct(
                    target_config=resolved_config,
                    readyset_port=readyset_port,
                    readyset_container_name=container_name,
                )

                if not start_result.get("success"):
                    return {
                        "success": False,
                        "error": start_result.get("error", "Failed to start Readyset"),
                        "error_type": start_result.get("error_type"),
                        "remediation": start_result.get("remediation"),
                    }

            # Wait for readyset to be ready
            ready_result = wait_for_readyset_ready_shallow(
                readyset_container_name=container_name,
                timeout=120,
            )

            if not ready_result.get("success"):
                return {
                    "success": False,
                    "error": ready_result.get("error", "Readyset not ready"),
                    "error_type": ready_result.get("error_type"),
                    "remediation": ready_result.get("remediation"),
                }

            # Build config for Readyset connection
            test_db_config = {
                "engine": engine,
                "host": "localhost",
                "port": readyset_port,
                "database": target_config.get("database"),
                "user": target_config.get("user"),
                "password": password,
            }

            # Run EXPLAIN CREATE CACHE
            explain_result = explain_create_cache_readyset(
                query=input.sql,
                readyset_port=readyset_port,
                test_db_config=test_db_config,
            )

            # Try to create cache if cacheable
            create_result = {}
            cache_id = None
            if explain_result.get("cacheable", False):
                already_cached = (
                    "already cached" in explain_result.get("explanation", "").lower()
                )
                if already_cached:
                    create_result = {
                        "success": True,
                        "cached": True,
                        "already_cached": True,
                        "message": "Query already cached",
                    }
                    # Get the cache ID for cleanup
                    cache_id = get_cache_id_for_query(
                        query=input.sql,
                        readyset_port=readyset_port,
                        db_config=test_db_config,
                    )
                else:
                    create_result = create_cache_readyset(
                        query=input.sql,
                        readyset_port=readyset_port,
                        test_db_config=test_db_config,
                    )
                    if create_result.get("success"):
                        cache_id = get_cache_id_for_query(
                            query=input.sql,
                            readyset_port=readyset_port,
                            db_config=test_db_config,
                        )

                # Warm the cache and measure performance
                warm_result = {}
                if create_result.get("success") or create_result.get("already_cached"):
                    warm_result = warm_cache_and_measure(
                        query=input.sql,
                        readyset_port=readyset_port,
                        test_db_config=test_db_config,
                        warmup_runs=2,
                        measure_runs=3,
                    )

                # Drop the cache after testing (ephemeral container)
                if cache_id:
                    drop_cache_readyset(
                        cache_name=cache_id,
                        readyset_port=readyset_port,
                        test_db_config=test_db_config,
                    )

            return {
                "success": True,
                "checked": True,
                "readyset_port": readyset_port,
                "shallow_mode": True,
                "explain_cache_result": explain_result,
                "create_cache_result": create_result,
                "warm_cache_result": warm_result,
                "final_verdict": {
                    "cacheable": explain_result.get("cacheable", False),
                    "confidence": explain_result.get("confidence", "unknown"),
                    "method": "readyset_shallow",
                    "cached": create_result.get("cached", False),
                    "warm_time_ms": warm_result.get("avg_warm_time_ms"),
                },
            }

        except Exception as e:
            return {"success": False, "error": f"Readyset analysis failed: {str(e)}"}

    async def _process_results(
        self,
        workflow_result: Dict[str, Any],
        readyset_result: Optional[Dict[str, Any]],
        input: AnalyzeInput,
    ) -> AsyncGenerator[AnalyzeEvent, None]:
        """Process workflow and readyset results, yielding appropriate events.

        Args:
            workflow_result: Result from workflow execution
            readyset_result: Result from readyset analysis (optional)
            input: Original analysis input

        Yields:
            AnalyzeEvent: Events based on results
        """
        if not workflow_result.get("success"):
            yield ErrorEvent(
                type="error",
                message=workflow_result.get("error", "Workflow failed"),
                partial_results=_serialize_for_json(workflow_result.get("result", {})),
            )
            return

        context = workflow_result.get("result", {})

        # Yield ExplainCompleteEvent if explain results available
        explain_results = context.get("explain_results", {})
        if explain_results.get("success"):
            yield ExplainCompleteEvent(
                type="explain_complete",
                success=True,
                database_engine=explain_results.get("database_engine", "unknown"),
                execution_time_ms=explain_results.get("execution_time_ms", 0.0),
                rows_examined=explain_results.get("rows_examined", 0),
                rows_returned=explain_results.get("rows_returned", 0),
                cost_estimate=explain_results.get("cost_estimate", 0.0),
                explain_plan=explain_results.get("explain_plan"),
            )

        # Yield RewritesTestedEvent if rewrite results available
        rewrite_results = _normalize_rewrite_testing_results(
            context.get("rewrite_test_results", {})
        )
        if rewrite_results.get("tested"):
            yield RewritesTestedEvent(
                type="rewrites_tested",
                tested=True,
                skipped_reason=rewrite_results.get("skipped_reason"),
                message=rewrite_results.get("message"),
                original_performance=rewrite_results.get("original_performance"),
                rewrite_results=rewrite_results.get("rewrite_results"),
                best_rewrite=rewrite_results.get("best_rewrite"),
            )

        # Yield ReadysetCheckedEvent if readyset results available
        readyset_cacheability = context.get("readyset_cacheability", {})
        if readyset_result and readyset_result.get("success"):
            # Use actual readyset container result
            final_verdict = readyset_result.get("final_verdict", {})
            explain_cache = readyset_result.get("explain_cache_result", {})
            yield ReadysetCheckedEvent(
                type="readyset_checked",
                checked=True,
                cacheable=final_verdict.get("cacheable"),
                confidence=final_verdict.get("confidence"),
                method=final_verdict.get("method"),
                explanation=explain_cache.get("explanation"),
                issues=explain_cache.get("issues"),
                warnings=explain_cache.get("warnings"),
            )
        elif readyset_cacheability.get("checked"):
            # Use static analysis result from workflow
            yield ReadysetCheckedEvent(
                type="readyset_checked",
                checked=True,
                cacheable=readyset_cacheability.get("cacheable"),
                confidence=readyset_cacheability.get("confidence"),
                method=readyset_cacheability.get("method"),
                explanation=readyset_cacheability.get("explanation"),
                issues=readyset_cacheability.get("issues"),
                warnings=readyset_cacheability.get("warnings"),
            )

        # Merge readyset result into context if available (success or error)
        if readyset_result and (readyset_result.get("success") or readyset_result.get("error")):
            context["readyset_analysis"] = readyset_result
            formatted = context.get("FormatFinalResults", {})
            if isinstance(formatted, dict):
                formatted["readyset_analysis"] = readyset_result

        # Yield final progress
        yield ProgressEvent(
            type="progress",
            stage="complete",
            percent=100,
            message="Analysis complete",
        )

        # Yield CompleteEvent
        formatted = context.get("FormatFinalResults", {})
        yield CompleteEvent(
            type="complete",
            success=True,
            analysis_id=context.get("storage_result", {}).get("analysis_id"),
            query_hash=context.get("registry_normalization", {}).get("hash")
            or input.hash,
            explain_results=_serialize_for_json(explain_results),
            llm_analysis=_serialize_for_json(context.get("llm_analysis", {})),
            rewrite_testing=_serialize_for_json(rewrite_results),
            readyset_cacheability=_serialize_for_json(
                readyset_result if readyset_result else readyset_cacheability
            ),
            formatted=_serialize_for_json(formatted),
        )

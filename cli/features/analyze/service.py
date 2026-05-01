"""AnalyzeService - Async generator-based query analysis service.

This service provides the core analysis logic extracted from the API and CLI,
exposing an async generator interface that yields events during execution.
"""

import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional, Tuple

from shared.config.targets import TargetsConfig
from shared.password_resolver import resolve_password_value
from features.cache.readyset_explain_cache import (
    create_cache_readyset,
    drop_cache_readyset,
    explain_create_cache_readyset,
    get_cache_id_for_query,
    warm_cache_and_measure,
)
from shared.workflow_manager import get_workflow_components

from .events import (
    AnalyzeEvent,
    CompleteEvent,
    ErrorEvent,
    ExplainCompleteEvent,
    ProgressEvent,
    ReadysetCheckedEvent,
    RewritesTestedEvent,
)
from .functions import ANALYZE_WORKFLOW_FUNCTIONS
from .models import AnalyzeInput, AnalyzeOptions


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
                    message=f"Target '{target_name}' not found. Run 'rdst configure add' to set one up.",
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
        cfg = TargetsConfig()
        cfg.load()
        target_name = target or cfg.get_default()

        if not target_name:
            return None, None

        target_config = cfg.get(target_name)

        # Never run EXPLAIN ANALYZE against a Readyset cache target.
        # If user specified a cache target, resolve to its upstream.
        if target_config and target_config.get("target_type") == "readyset":
            upstream_name = target_config.get("upstream_target")
            if upstream_name:
                target_name = upstream_name
                target_config = cfg.get(upstream_name)

        return target_name, target_config

    def _get_workflow_path(self) -> Path:
        """Get path to the analyze workflow definition."""
        return Path(__file__).resolve().parent / "workflows" / "analyze_workflow_simple.json"

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

        # Always attempt Readyset analysis in parallel.
        # Failures are non-fatal — handled when awaited at line ~272.
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
                # Wait up to 60s for Readyset after workflow completes.
                # If Docker is pulling an image or hanging, don't block the output.
                readyset_result = await asyncio.wait_for(readyset_task, timeout=60)
                if isinstance(readyset_result, Exception):
                    readyset_result = {
                        "success": False,
                        "error": f"Readyset analysis failed: {readyset_result}",
                    }
            except asyncio.TimeoutError:
                readyset_result = {
                    "success": False,
                    "error": "Readyset analysis timed out (Docker may still be starting). "
                             "Run 'rdst cache deploy' to set up Readyset separately.",
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
        WorkflowManager, DEFAULT_FUNCTIONS, WorkflowStatus = (
            get_workflow_components()
        )

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
        """Synchronous readyset analysis (runs in thread).

        Uses an existing cache container if one is deployed and running.
        Otherwise spins up an *ephemeral* container for the duration of the
        analysis (deploy → run → tear down). If the container existed but was
        stopped, starts it for the run and stops it again afterwards.

        Flow:
        1. Find a cache target name for this upstream (config or convention)
        2. ephemeral_lifecycle context manager ensures it's running
        3. Test cacheability, warm, measure, drop the cache (analysis is ephemeral
           regardless of whether the *container* was ephemeral or not)
        4. ephemeral_lifecycle restores original container state on exit
        """
        from shared.deploy.lifecycle import ephemeral_lifecycle, ProbeState

        cfg = TargetsConfig()
        cfg.load()

        cache_target_name = None
        cache_config = None
        for name, config in cfg._data.get("targets", {}).items():
            if (
                config.get("target_type") == "readyset"
                and config.get("upstream_target") == target_name
            ):
                cache_target_name = name
                cache_config = config
                break
        if not cache_config:
            conventional = f"{target_name}-cache"
            check = cfg.get(conventional)
            if check and check.get("target_type") == "readyset":
                cache_target_name = conventional
                cache_config = check

        # Ephemeral lifecycle around the readyset run. If no cache target is
        # configured at all, this will deploy one with the conventional name
        # and tear it down at the end. If it's stopped, start + stop. If
        # running, leave it alone.
        with ephemeral_lifecycle(target_name, mode="docker") as outcome:
            if outcome.error:
                # Could not bring up a container — surface the original
                # remediation message so the user gets actionable guidance.
                return {
                    "success": False,
                    "no_cache_target": True,
                    "error": outcome.error,
                    "remediation": (
                        f"Deploy a cache to see Readyset performance:\n"
                        f"  rdst cache deploy --target {target_name} --mode docker"
                    ),
                }

            # If we just deployed, the cache target row was created during deploy.
            # Reload config to pick it up.
            if outcome.we_deployed and not cache_config:
                cfg.load()
                conventional = f"{target_name}-cache"
                cache_config = cfg.get(conventional)
                if cache_config:
                    cache_target_name = conventional

            if not cache_config:
                return {
                    "success": False,
                    "no_cache_target": True,
                    "error": "No Readyset cache target configured after deploy.",
                    "remediation": (
                        f"  rdst cache deploy --target {target_name} --mode docker"
                    ),
                }

            return self._run_readyset_analysis_inner(
                input=input, target_name=target_name, target_config=target_config,
                cache_target_name=cache_target_name, cache_config=cache_config,
                ephemeral_outcome=outcome,
            )

    def _run_readyset_analysis_inner(
        self,
        input: AnalyzeInput,
        target_name: str,
        target_config: Dict[str, Any],
        cache_target_name: str,
        cache_config: Dict[str, Any],
        ephemeral_outcome=None,
    ) -> Dict[str, Any]:
        """Runs the actual readyset cacheability test + measure flow.

        Caller is responsible for cache container lifecycle (see
        _run_readyset_analysis_sync).
        """
        try:

            # Cache target exists — try to connect
            engine = cache_config.get("engine", target_config.get("engine", "postgresql"))
            cache_host = cache_config.get("host", "localhost")
            cache_port = cache_config.get("port", 5433 if engine == "postgresql" else 3307)
            cache_password = resolve_password_value(cache_config)

            cache_db_config = {
                "engine": engine,
                "host": cache_host,
                "port": int(cache_port),
                "database": cache_config.get("database", target_config.get("database")),
                "user": cache_config.get("user", target_config.get("user")),
                "password": cache_password,
            }

            # Run EXPLAIN CREATE CACHE (connectivity failures caught by outer except)
            # All calls use quiet=True — this runs in a background thread
            # and should not print to stdout (the main workflow handles display)
            explain_result = explain_create_cache_readyset(
                query=input.sql,
                readyset_port=int(cache_port),
                readyset_host=cache_host,
                test_db_config=cache_db_config,
                quiet=True,
            )

            # Try to create cache, warm, measure, then DROP (analyze is ephemeral)
            create_result = {}
            warm_result = {}
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
                    cache_id = get_cache_id_for_query(
                        query=input.sql,
                        readyset_port=int(cache_port),
                        db_config=cache_db_config,
                    )
                else:
                    create_result = create_cache_readyset(
                        query=input.sql,
                        readyset_port=int(cache_port),
                        readyset_host=cache_host,
                        test_db_config=cache_db_config,
                        quiet=True,
                    )
                    if create_result.get("success"):
                        cache_id = get_cache_id_for_query(
                            query=input.sql,
                            readyset_port=int(cache_port),
                            db_config=cache_db_config,
                        )

                # Warm and measure
                if create_result.get("success") or create_result.get("already_cached"):
                    warm_result = warm_cache_and_measure(
                        query=input.sql,
                        readyset_port=int(cache_port),
                        readyset_host=cache_host,
                        test_db_config=cache_db_config,
                        warmup_runs=2,
                        measure_runs=3,
                        quiet=True,
                    )

                # Always drop — analyze is not an explicit cache add
                if cache_id and not already_cached:
                    drop_cache_readyset(
                        cache_name=cache_id,
                        readyset_port=int(cache_port),
                        readyset_host=cache_host,
                        test_db_config=cache_db_config,
                    )

            return {
                "success": True,
                "checked": True,
                "cache_target": cache_target_name,
                "readyset_port": int(cache_port),
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
        if not explain_results.get("success") and explain_results.get("error"):
            error_msg = explain_results["error"]
            yield ExplainCompleteEvent(
                type="explain_complete",
                success=False,
                database_engine=explain_results.get("database_engine", "unknown"),
                execution_time_ms=0.0,
                rows_examined=0,
                rows_returned=0,
                cost_estimate=0.0,
                explain_plan=None,
                explain_analyze_skipped=False,
                error=error_msg,
            )
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
                explain_analyze_skipped=bool(explain_results.get("explain_analyze_skipped", False)),
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

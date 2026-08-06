"""AnalyzeService - Async generator-based query analysis service.

This service provides the core analysis logic extracted from the API and CLI,
exposing an async generator interface that yields events during execution.
"""

import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional, Tuple

from shared.config.targets import TargetsConfig
from shared.query_safety import validate_query_safety
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
        target_name = options.target
        target_config: Optional[Dict[str, Any]] = None
        try:
            # Refuse unsafe SQL before anything else happens. The workflow runs
            # the same check and raises, which is the backstop for every other
            # caller, but reaching it costs a stack trace for what is a normal
            # refusal -- and analyze executes what it is given, since EXPLAIN
            # ANALYZE runs the statement rather than just planning it.
            if input.sql:
                safety = validate_query_safety(input.sql)
                if not safety.get("safe"):
                    issues = "; ".join(safety.get("issues") or []) or "failed safety validation"
                    yield ErrorEvent(
                        type="error",
                        message=f"Refusing to analyze this query: {issues}",
                    )
                    return

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
            from shared.api.ssh_errors import connectivity_error_payload

            failure = connectivity_error_payload(
                e, target_name or options.target or "target", target_config or {}
            )
            yield ErrorEvent(
                type="error",
                message=failure["message"] if failure else str(e),
                code=failure["category"] if failure else None,
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
        """Run the workflow followed by optional Readyset verification.

        The workflow runs with async polling for progress updates. Explicit
        Readyset verification runs in a separate thread after workflow success.

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

        # Container-backed verification is explicit. Start it only after the
        # core workflow succeeds so a workflow failure cannot orphan Docker work.

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
        if options.readyset_cache and workflow_result.get("success"):
            try:
                readyset_result = await asyncio.to_thread(
                    self._run_readyset_analysis_sync,
                    input=input,
                    target_name=target_name,
                    target_config=target_config,
                )
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
            target_name=target_name,
            target_config=target_config,
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
        """Synchronous adapter for an explicit manager-owned speed test.

        Web Analyze does not call this path. CLI callers that explicitly request
        Readyset use the same temporary experiment service as the web speed-test
        job, including exact cache cleanup and dirty-sandbox replacement.
        """
        import hashlib

        async def _run() -> Dict[str, Any]:
            from features.cache.events import CacheRunCompleteEvent
            from features.cache.experiment_service import ReadysetExperimentService
            from shared.deploy.sandbox_manager import sandbox_manager
            from shared.service_events import ErrorEvent

            await sandbox_manager.start()
            try:
                final = None
                failure = None
                async for event in ReadysetExperimentService(
                    sandbox_manager
                ).compare(
                    owner_id=(
                        "analyze-"
                        + hashlib.sha256(input.sql.encode()).hexdigest()[:12]
                    ),
                    target=target_name,
                    query=input.sql,
                    iterations=3,
                    warmup=1,
                ):
                    if isinstance(event, CacheRunCompleteEvent):
                        final = event
                    elif isinstance(event, ErrorEvent):
                        failure = event.message
                if final is None:
                    return {
                        "success": False,
                        "cacheable": False,
                        "error": failure or "Readyset verification did not complete",
                    }
                return {
                    "success": True,
                    "cacheable": True,
                    "confidence": "high",
                    "method": "readyset_speed_test",
                    "explanation": (
                        "Verified with a temporary Readyset cache; "
                        f"measured {final.speedup_mean:.1f}x mean speedup."
                    ),
                    "performance_comparison": {
                        "original": {"stats": final.origin_stats},
                        "readyset": {"stats": final.cache_stats},
                        "speedup": {
                            "mean": final.speedup_mean,
                            "median": final.speedup_median,
                            "improvement_pct": final.improvement_pct,
                        },
                        "winner": final.winner,
                    },
                }
            finally:
                await sandbox_manager.stop()

        return asyncio.run(_run())

    async def _process_results(
        self,
        workflow_result: Dict[str, Any],
        readyset_result: Optional[Dict[str, Any]],
        input: AnalyzeInput,
        target_name: str = "",
        target_config: Optional[Dict[str, Any]] = None,
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
            from shared.api.ssh_errors import connectivity_error_payload

            failure = connectivity_error_payload(
                RuntimeError(error_msg), target_name, target_config
            )
            if failure:
                yield ErrorEvent(
                    type="error",
                    message=failure["message"],
                    code=failure["category"],
                    stage="executing_explain",
                )
                return
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

        # Only an explicitly requested, successful Readyset experiment is a
        # verified verdict. Static SQL inspection is useful screening, but the
        # web Analyze path must never imply that it ran a container.
        readyset_cacheability = context.get("readyset_cacheability", {})
        cacheability_payload: Dict[str, Any]
        if readyset_result and readyset_result.get("success"):
            # Accept both the current experiment-service shape and the legacy
            # nested shape while CLI callers migrate.
            final_verdict = readyset_result.get("final_verdict") or readyset_result
            explain_cache = readyset_result.get("explain_cache_result", {})
            cacheability_payload = {
                "checked": True,
                "cacheable": final_verdict.get("cacheable"),
                "confidence": final_verdict.get("confidence"),
                "method": final_verdict.get("method"),
                "explanation": readyset_result.get("explanation")
                or explain_cache.get("explanation"),
                "issues": readyset_result.get("issues")
                or explain_cache.get("issues", []),
                "warnings": readyset_result.get("warnings")
                or explain_cache.get("warnings", []),
            }
        elif readyset_result:
            cacheability_payload = {
                "checked": False,
                "cacheable": None,
                "confidence": "unknown",
                "method": "readyset_unavailable",
                "explanation": readyset_result.get(
                    "error", "Readyset cacheability verification was unavailable."
                ),
                # Raw driver/client text stays out of the primary message; the
                # UI shows it behind a technical-details expander (P41).
                "detail": readyset_result.get("error_detail"),
                "issues": [],
                "warnings": [],
            }
        else:
            static_checked = bool(
                readyset_cacheability.get(
                    "checked", "cacheable" in readyset_cacheability
                )
            )
            cacheability_payload = {
                "checked": static_checked,
                "cacheable": readyset_cacheability.get("cacheable"),
                "confidence": readyset_cacheability.get(
                    "confidence", "unknown"
                ),
                "method": "static_analysis",
                "explanation": readyset_cacheability.get(
                    "explanation",
                    "Static Readyset screening was not available.",
                ),
                "issues": readyset_cacheability.get("issues", []),
                "warnings": readyset_cacheability.get("warnings", []),
            }

        if cacheability_payload:
            yield ReadysetCheckedEvent(
                type="readyset_checked",
                **cacheability_payload,
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
        if isinstance(formatted, dict):
            formatted["readyset_cacheability"] = cacheability_payload
        yield CompleteEvent(
            type="complete",
            success=True,
            analysis_id=context.get("storage_result", {}).get("analysis_id"),
            query_hash=context.get("registry_normalization", {}).get("hash")
            or input.hash,
            explain_results=_serialize_for_json(explain_results),
            llm_analysis=_serialize_for_json(context.get("llm_analysis", {})),
            rewrite_testing=_serialize_for_json(rewrite_results),
            readyset_cacheability=_serialize_for_json(cacheability_payload),
            formatted=_serialize_for_json(formatted),
        )

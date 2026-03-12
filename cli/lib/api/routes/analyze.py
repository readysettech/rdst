import json
from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from ..models import AnalyzeRequest
from .target_guard import TargetGuard, require_target_body
from ...services.analyze_service import AnalyzeService
from ...services.types import (
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

router = APIRouter()


def _serialize_for_json(obj):
    if isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_serialize_for_json(v) for v in obj]
    elif hasattr(obj, "__dict__") and not isinstance(obj, type):
        return str(obj)
    return obj


def _event_to_sse(event: AnalyzeEvent) -> dict:
    if isinstance(event, ProgressEvent):
        return {
            "event": "progress",
            "data": json.dumps(
                {
                    "stage": event.stage,
                    "percent": event.percent,
                    "message": event.message,
                }
            ),
        }
    elif isinstance(event, ExplainCompleteEvent):
        return {
            "event": "explain_complete",
            "data": json.dumps(
                _serialize_for_json(
                    {
                        "success": event.success,
                        "database_engine": event.database_engine,
                        "execution_time_ms": event.execution_time_ms,
                        "rows_examined": event.rows_examined,
                        "rows_returned": event.rows_returned,
                        "cost_estimate": event.cost_estimate,
                        "explain_plan": event.explain_plan,
                    }
                )
            ),
        }
    elif isinstance(event, RewritesTestedEvent):
        return {
            "event": "rewrites_tested",
            "data": json.dumps(
                _serialize_for_json(
                    {
                        "tested": event.tested,
                        "skipped_reason": event.skipped_reason,
                        "message": event.message,
                        "original_performance": event.original_performance,
                        "rewrite_results": event.rewrite_results,
                        "best_rewrite": event.best_rewrite,
                    }
                )
            ),
        }
    elif isinstance(event, ReadysetCheckedEvent):
        return {
            "event": "readyset_checked",
            "data": json.dumps(
                _serialize_for_json(
                    {
                        "checked": event.checked,
                        "cacheable": event.cacheable,
                        "confidence": event.confidence,
                        "method": event.method,
                        "explanation": event.explanation,
                        "issues": event.issues,
                        "warnings": event.warnings,
                    }
                )
            ),
        }
    elif isinstance(event, CompleteEvent):
        return {
            "event": "complete",
            "data": json.dumps(
                _serialize_for_json(
                    {
                        "success": event.success,
                        "analysis_id": event.analysis_id,
                        "query_hash": event.query_hash,
                        "explain_results": event.explain_results,
                        "llm_analysis": event.llm_analysis,
                        "rewrite_testing": event.rewrite_testing,
                        "readyset_cacheability": event.readyset_cacheability,
                        "formatted": event.formatted,
                    }
                )
            ),
        }
    elif isinstance(event, ErrorEvent):
        error_data: dict = {"message": event.message}
        if event.stage:
            error_data["stage"] = event.stage
        if event.partial_results:
            error_data["partial_results"] = event.partial_results
        return {
            "event": "error",
            "data": json.dumps(error_data),
        }
    else:
        return {
            "event": "error",
            "data": json.dumps({"message": f"Unknown event type: {type(event)}"}),
        }


async def _analyze_generator(
    input_data: AnalyzeInput, options: AnalyzeOptions
) -> AsyncGenerator[dict, None]:
    try:
        from lib.telemetry import telemetry
        telemetry.track("analyze_run", {
            "source": "web",
            "target": options.target,
            "fast": options.fast,
            "readyset_cache": options.readyset_cache,
            "test_rewrites": options.test_rewrites,
        })
    except Exception:
        pass

    try:
        service = AnalyzeService()
        async for event in service.analyze(input_data, options):
            yield _event_to_sse(event)
    except Exception as e:
        yield {"event": "error", "data": json.dumps({"message": str(e)})}


async def _quick_analyze_generator(
    input_data: AnalyzeInput, options: AnalyzeOptions
) -> AsyncGenerator[dict, None]:
    options.test_rewrites = False
    async for event in _analyze_generator(input_data, options):
        yield event


@router.post("/analyze")
async def analyze(request: AnalyzeRequest, guard: TargetGuard = Depends(require_target_body)):
    input_data = AnalyzeInput(sql=request.query, normalized_sql=request.query, source="web")
    options = AnalyzeOptions(
        target=guard.target_name,
        fast=request.fast,
        readyset_cache=getattr(request, "readyset_cache", False),
        test_rewrites=not request.skip_rewrites,
        model=request.model,
    )
    return EventSourceResponse(_analyze_generator(input_data, options))


@router.post("/analyze/quick")
async def analyze_quick(request: AnalyzeRequest, guard: TargetGuard = Depends(require_target_body)):
    input_data = AnalyzeInput(sql=request.query, normalized_sql=request.query, source="web")
    options = AnalyzeOptions(
        target=guard.target_name,
        fast=request.fast,
        readyset_cache=getattr(request, "readyset_cache", False),
        model=request.model,
    )
    return EventSourceResponse(_quick_analyze_generator(input_data, options))

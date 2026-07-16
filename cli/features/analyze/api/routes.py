import json
from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from shared.api.models import AnalyzeRequest
from shared.api.target_guard import TargetGuard, require_target_body
from shared.telemetry import telemetry

from ..events import (
    AnalyzeEvent,
    CompleteEvent,
    ErrorEvent,
    ExplainCompleteEvent,
    ProgressEvent,
    ReadysetCheckedEvent,
    RewritesTestedEvent,
)
from ..telemetry import analyze_terminal_detector
from ..models import AnalyzeInput, AnalyzeOptions
from ..service import AnalyzeService

router = APIRouter()


def _serialize_for_json(obj):
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    elif isinstance(obj, dict):
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
    input_data: AnalyzeInput,
    options: AnalyzeOptions,
    target_engine: str,
) -> AsyncGenerator[dict, None]:
    mode = "fast" if options.fast else "standard"
    async with telemetry.command_run(
        "analyze",
        source="web",
        target_engine=target_engine,
        mode=mode,
        terminal_detector=analyze_terminal_detector,
    ) as run:
        try:
            async for event in AnalyzeService().analyze(input_data, options):
                run.observe(event)
                yield _event_to_sse(event)
        except Exception as e:
            run.error(e)
            yield {"event": "error", "data": json.dumps({"message": str(e)})}


async def _quick_analyze_generator(
    input_data: AnalyzeInput,
    options: AnalyzeOptions,
    target_engine: str,
) -> AsyncGenerator[dict, None]:
    options.test_rewrites = False
    async for event in _analyze_generator(input_data, options, target_engine):
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
    return EventSourceResponse(_analyze_generator(input_data, options, guard.target_engine))


@router.post("/analyze/quick")
async def analyze_quick(request: AnalyzeRequest, guard: TargetGuard = Depends(require_target_body)):
    input_data = AnalyzeInput(sql=request.query, normalized_sql=request.query, source="web")
    options = AnalyzeOptions(
        target=guard.target_name,
        fast=request.fast,
        readyset_cache=getattr(request, "readyset_cache", False),
        model=request.model,
    )
    return EventSourceResponse(_quick_analyze_generator(input_data, options, guard.target_engine))

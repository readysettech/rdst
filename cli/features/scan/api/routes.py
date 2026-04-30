"""Scan API endpoint with SSE streaming.

Provides codebase scanning for ORM queries with real-time progress
via Server-Sent Events.
"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Optional, Union

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from shared.api.target_guard import TargetGuard, require_target_body
from shared.telemetry import telemetry

from ..events import (
    ScanCompleteEvent,
    ScanErrorEvent,
    ScanEvent,
    ScanFilesFoundEvent,
    ScanProgressEvent,
    ScanQueryResultEvent,
    ScanRegistryEvent,
    ScanStatusEvent,
)
from ..telemetry import scan_terminal_detector
from ..models import ScanInput, ScanOptions
from ..service import ScanService

router = APIRouter()


class ScanRequest(BaseModel):
    """Request body for scan endpoint."""

    target: str
    directory: str
    analyze: bool = False
    shallow: bool = False
    dry_run: bool = Field(False, alias="dry_run")
    diff: Optional[str] = None
    check: bool = False
    warn_threshold: int = 60
    fail_threshold: int = 40
    file_pattern: Optional[str] = None
    nosave: bool = False

    def to_scan_options(self) -> ScanOptions:
        """Convert request to ScanOptions for the service layer."""
        return ScanOptions(
            analyze=self.analyze,
            shallow=self.shallow,
            dry_run=self.dry_run,
            diff=self.diff,
            check=self.check,
            warn_threshold=self.warn_threshold,
            fail_threshold=self.fail_threshold,
            file_pattern=self.file_pattern,
            nosave=self.nosave,
        )


class ScanJsonSuccess(BaseModel):
    success: bool
    files: list[dict[str, Any]]
    queries: list[dict[str, Any]]
    summary: dict[str, Any]


class ScanJsonError(BaseModel):
    success: bool
    error: str


def _event_to_sse(event: ScanEvent) -> dict:
    """Convert ScanEvent to SSE format."""
    if isinstance(event, ScanStatusEvent):
        return {
            "event": "status",
            "data": json.dumps({"phase": event.phase, "message": event.message}),
        }
    elif isinstance(event, ScanFilesFoundEvent):
        return {
            "event": "files_found",
            "data": json.dumps({"files": event.files, "total": event.total}),
        }
    elif isinstance(event, ScanProgressEvent):
        return {
            "event": "progress",
            "data": json.dumps({
                "phase": event.phase,
                "current": event.current,
                "total": event.total,
                "message": event.message,
            }),
        }
    elif isinstance(event, ScanQueryResultEvent):
        return {
            "event": "query_result",
            "data": json.dumps({"query": event.query}),
        }
    elif isinstance(event, ScanRegistryEvent):
        return {
            "event": "registry",
            "data": json.dumps({
                "new_queries": event.new_queries,
                "updated_queries": event.updated_queries,
                "total_queries": event.total_queries,
                "skipped": event.skipped,
            }),
        }
    elif isinstance(event, ScanCompleteEvent):
        return {
            "event": "complete",
            "data": json.dumps({
                "success": event.success,
                "summary": event.summary,
            }),
        }
    elif isinstance(event, ScanErrorEvent):
        error_data = {"message": event.message}
        if event.phase:
            error_data["phase"] = event.phase
        return {
            "event": "error",
            "data": json.dumps(error_data),
        }
    else:
        return {
            "event": "error",
            "data": json.dumps({"message": f"Unknown event type: {type(event)}"}),
        }


async def _scan_generator(
    directory: str,
    target: str,
    target_engine: str,
    options: ScanOptions,
) -> AsyncGenerator[dict, None]:
    """Generate SSE events for scan streaming."""
    async with telemetry.command_run(
        "scan",
        source="web",
        target_engine=target_engine,
        terminal_detector=scan_terminal_detector,
        analyze=options.analyze,
        shallow=options.shallow,
        dry_run=options.dry_run,
    ) as run:
        service = ScanService()
        input_data = ScanInput(directory=directory, target=target, source="web")
        try:
            async for event in service.scan_directory(input_data, options):
                run.observe(event)
                yield _event_to_sse(event)
        except Exception as e:
            run.error(e)
            yield {"event": "error", "data": json.dumps({"message": str(e)})}


@router.post("/scan")
async def scan_directory(
    request: ScanRequest,
    guard: TargetGuard = Depends(require_target_body),
):
    """Scan a directory for ORM queries via SSE streaming.

    Streams real-time progress as the scan progresses through phases:
    - `status`: Phase status messages
    - `files_found`: Files with ORM patterns discovered
    - `progress`: Per-phase progress (current/total)
    - `query_result`: Individual query results
    - `registry`: Registry save results
    - `complete`: Scan complete with summary
    - `error`: Error occurred
    """
    return EventSourceResponse(
        _scan_generator(
            request.directory,
            guard.target_name,
            guard.target_engine,
            request.to_scan_options(),
        )
    )


@router.post("/scan/json")
async def scan_directory_json(
    request: ScanRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> Union[ScanJsonSuccess, ScanJsonError]:
    """Scan a directory and return JSON response (non-streaming).

    Collects all events and returns the final summary.
    """
    service = ScanService()
    input_data = ScanInput(
        directory=request.directory, target=guard.target_name, source="web"
    )
    options = request.to_scan_options()

    queries = []
    files = []
    summary = None
    error_message = None

    async for event in service.scan_directory(input_data, options):
        if isinstance(event, ScanFilesFoundEvent):
            files = event.files
        elif isinstance(event, ScanQueryResultEvent):
            queries.append(event.query)
        elif isinstance(event, ScanCompleteEvent):
            summary = event.summary
        elif isinstance(event, ScanErrorEvent):
            error_message = event.message
            break

    if error_message:
        return {"success": False, "error": error_message}

    if summary is None:
        return {"success": False, "error": "No results collected"}

    return {
        "success": True,
        "files": files,
        "queries": queries,
        "summary": summary,
    }

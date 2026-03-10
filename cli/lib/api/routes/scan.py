"""Scan API endpoint with SSE streaming.

Provides codebase scanning for ORM queries with real-time progress
via Server-Sent Events.
"""

from __future__ import annotations

import json
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .target_guard import TargetGuard, require_target_body
from ...services.scan_service import ScanService
from ...services.types import (
    ScanCompleteEvent,
    ScanErrorEvent,
    ScanEvent,
    ScanFilesFoundEvent,
    ScanInput,
    ScanOptions,
    ScanProgressEvent,
    ScanQueryResultEvent,
    ScanRegistryEvent,
    ScanStatusEvent,
)

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
    options: ScanOptions,
) -> AsyncGenerator[dict, None]:
    """Generate SSE events for scan streaming."""
    service = ScanService()
    input_data = ScanInput(directory=directory, target=target, source="web")

    try:
        async for event in service.scan_directory(input_data, options):
            yield _event_to_sse(event)
    except Exception as e:
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
        _scan_generator(request.directory, guard.target_name, request.to_scan_options())
    )


@router.post("/scan/json")
async def scan_directory_json(
    request: ScanRequest,
    guard: TargetGuard = Depends(require_target_body),
):
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

"""API routes for ask functionality."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from features.ask.events import (
    AskClarificationNeededEvent,
    AskErrorEvent,
    AskEvent,
    AskResultEvent,
    AskSchemaLoadedEvent,
    AskSqlGeneratedEvent,
    AskStatusEvent,
)
from features.ask.models import AskInput, AskOptions
from features.ask.service import AskService
from features.ask.telemetry import ask_terminal_detector
from shared.api.models import AskRequest
from shared.api.target_guard import TargetGuard, require_target_body
from shared.telemetry import telemetry

router = APIRouter()


def _phase_value(phase: object | None) -> str | None:
    """Serialize enum phases without leaking ``AskPhase.GENERATE``."""
    if phase is None:
        return None
    value = getattr(phase, "value", phase)
    return str(value)


def _serialize_value(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, bytes):
        return f"<binary: {len(val)} bytes>"
    return val


def _serialize_rows(rows: list) -> list:
    return [[_serialize_value(cell) for cell in row] for row in rows]


def _event_to_sse(event: AskEvent) -> dict:
    if isinstance(event, AskStatusEvent):
        return {
            "event": "status",
            "data": json.dumps(
                {"phase": _phase_value(event.phase), "message": event.message}
            ),
        }
    if isinstance(event, AskSchemaLoadedEvent):
        return {
            "event": "schema_loaded",
            "data": json.dumps(
                {
                    "source": event.source,
                    "table_count": event.table_count,
                    "tables": event.tables,
                }
            ),
        }
    if isinstance(event, AskClarificationNeededEvent):
        return {
            "event": "clarification_needed",
            "data": json.dumps(
                {
                    "session_id": event.session_id,
                    "interpretations": [
                        {
                            "id": interp.id,
                            "description": interp.description,
                            "likelihood": interp.likelihood,
                            "assumptions": interp.assumptions,
                        }
                        for interp in event.interpretations
                    ],
                    "questions": [
                        {
                            "id": question.id,
                            "question": question.question,
                            "options": question.options,
                        }
                        for question in event.questions
                    ],
                }
            ),
        }
    if isinstance(event, AskSqlGeneratedEvent):
        return {
            "event": "sql_generated",
            "data": json.dumps(
                {"sql": event.sql, "explanation": event.explanation}
            ),
        }
    if isinstance(event, AskResultEvent):
        return {
            "event": "result",
            "data": json.dumps(
                {
                    "success": event.success,
                    "sql": event.sql,
                    "rows": _serialize_rows(event.rows),
                    "columns": event.columns,
                    "row_count": event.row_count,
                    "execution_time_ms": event.execution_time_ms,
                    "llm_calls": event.llm_calls,
                    "total_tokens": event.total_tokens,
                }
            ),
        }
    if isinstance(event, AskErrorEvent):
        return {
            "event": "error",
            "data": json.dumps(
                {"message": event.message, "phase": _phase_value(event.phase)}
            ),
        }
    return {
        "event": "error",
        "data": json.dumps({"message": f"Unknown event type: {type(event)}"}),
    }


async def _ask_generator(
    input_data: AskInput | None,
    options: AskOptions | None,
    target_engine: str = "unknown",
    session_id: str | None = None,
    clarification_answers: dict | None = None,
) -> AsyncGenerator[dict, None]:
    extra: dict = {
        "is_resume": bool(session_id),
        "agent_mode": options.agent_mode if options else False,
        "dry_run": options.dry_run if options else False,
    }
    async with telemetry.command_run(
        "ask",
        source="web",
        target_engine=target_engine,
        terminal_detector=ask_terminal_detector,
        **extra,
    ) as run:
        try:
            service = AskService()
            if session_id:
                async for event in service.resume(
                    session_id=session_id,
                    clarification_answers=clarification_answers,
                ):
                    run.observe(event)
                    yield _event_to_sse(event)
                return

            async for event in service.ask(input_data, options):
                run.observe(event)
                yield _event_to_sse(event)
        except Exception as exc:
            run.error(exc)
            # Shared {code, message, detail} envelope (B7/T24); never raw str(e).
            yield {
                "event": "error",
                "data": json.dumps(
                    {
                        "code": "internal_error",
                        "message": "The question could not be answered.",
                        "detail": type(exc).__name__,
                    }
                ),
            }


@router.post("/ask")
async def ask(request: AskRequest, guard: TargetGuard = Depends(require_target_body)):
    if request.session_id:
        return EventSourceResponse(
            _ask_generator(
                None,
                None,
                target_engine=guard.target_engine,
                session_id=request.session_id,
                clarification_answers=request.clarification_answers,
            )
        )

    input_data = AskInput(question=request.question, target=guard.target_name, source="web")
    options = AskOptions(
        dry_run=request.dry_run,
        timeout_seconds=request.timeout,
        verbose=False,
        agent_mode=request.agent_mode,
        no_interactive=False,
    )
    return EventSourceResponse(
        _ask_generator(input_data, options, target_engine=guard.target_engine)
    )

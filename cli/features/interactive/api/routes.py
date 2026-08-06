"""Interactive API routes for RDST web client."""

from fastapi import APIRouter, Request
from fastapi_ai_sdk import create_ai_stream_response
from fastapi_ai_sdk.models import (
    ErrorEvent,
    FinishEvent,
    StartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
)

from features.interactive.events import (
    ChunkEvent,
    InteractiveCompleteEvent,
    InteractiveErrorEvent,
)
from features.interactive.service import InteractiveService
from shared.api.guards import require_local_request
from shared.api.models import (
    ConversationHistoryResponse,
    ConversationStatusResponse,
    DeleteResponse,
    InteractiveMessageRequest,
    MessageResponse,
)

router = APIRouter()


@router.get("/interactive/{query_hash}/status")
async def get_conversation_status(
    http_request: Request, query_hash: str
) -> ConversationStatusResponse:
    require_local_request(http_request)

    service = InteractiveService()
    status = service.get_conversation_status(query_hash)
    return ConversationStatusResponse(**status)


@router.get("/interactive/{query_hash}/history")
async def get_conversation_history(
    http_request: Request, query_hash: str
) -> ConversationHistoryResponse:
    require_local_request(http_request)

    service = InteractiveService()
    history = service.get_conversation_history(query_hash)
    messages = [MessageResponse(**msg) for msg in history]
    return ConversationHistoryResponse(messages=messages)


@router.post("/interactive/{query_hash}/message")
async def send_message(
    http_request: Request, query_hash: str, request: InteractiveMessageRequest
):
    require_local_request(http_request)

    async def event_generator():
        text_id = f"txt_{query_hash}"
        yield StartEvent(message_id=f"msg_{query_hash}").to_sse()
        yield TextStartEvent(id=text_id).to_sse()

        try:
            service = InteractiveService()
            async for event in service.send_message(
                query_hash=query_hash,
                message=request.message,
                analysis_results=request.analysis_results or {},
                continue_existing=request.continue_existing,
            ):
                if isinstance(event, ChunkEvent):
                    yield TextDeltaEvent(id=text_id, delta=event.text).to_sse()
                elif isinstance(event, InteractiveErrorEvent):
                    yield ErrorEvent(error_text=event.error).to_sse()
                    return
                elif isinstance(event, InteractiveCompleteEvent):
                    pass
            yield TextEndEvent(id=text_id).to_sse()
            yield FinishEvent().to_sse()
        except Exception as exc:
            yield ErrorEvent(error_text=str(exc)).to_sse()

    response = create_ai_stream_response(event_generator())
    response.headers["x-vercel-ai-ui-message-stream"] = "v1"
    return response


@router.delete("/interactive/{query_hash}")
async def delete_conversation(
    http_request: Request, query_hash: str
) -> DeleteResponse:
    require_local_request(http_request)

    service = InteractiveService()
    success = service.delete_conversation(query_hash)
    return DeleteResponse(success=success)

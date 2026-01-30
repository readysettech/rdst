"""Interactive API routes for RDST web client.

Provides endpoints for interactive analysis conversations:
- Check conversation status
- Load conversation history
- Send messages (SSE streaming)
- Delete conversations
"""

from fastapi import APIRouter
from fastapi_ai_sdk import create_ai_stream_response
from fastapi_ai_sdk.models import (
    StartEvent,
    TextStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    FinishEvent,
    ErrorEvent,
)

from ..models import (
    ConversationHistoryResponse,
    ConversationStatusResponse,
    DeleteResponse,
    InteractiveMessageRequest,
    MessageResponse,
)
from ...services.interactive_service import InteractiveService
from ...services.types import (
    ChunkEvent,
    InteractiveCompleteEvent,
    InteractiveErrorEvent,
)

router = APIRouter()


@router.get("/interactive/{query_hash}/status")
async def get_conversation_status(query_hash: str) -> ConversationStatusResponse:
    """Check if conversation exists and get metadata.

    Args:
        query_hash: Hash of the query being discussed

    Returns:
        ConversationStatusResponse with exists flag and metadata
    """
    service = InteractiveService()
    status = service.get_conversation_status(query_hash)
    return ConversationStatusResponse(**status)


@router.get("/interactive/{query_hash}/history")
async def get_conversation_history(query_hash: str) -> ConversationHistoryResponse:
    """Load conversation history.

    Args:
        query_hash: Hash of the query being discussed

    Returns:
        ConversationHistoryResponse with list of messages
    """
    service = InteractiveService()
    history = service.get_conversation_history(query_hash)
    messages = [MessageResponse(**msg) for msg in history]
    return ConversationHistoryResponse(messages=messages)


@router.post("/interactive/{query_hash}/message")
async def send_message(query_hash: str, request: InteractiveMessageRequest):
    """Stream message response using Vercel AI SDK UI Message Stream protocol.

    Args:
        query_hash: Hash of the query being discussed
        request: Message request with content and options

    Returns:
        StreamingResponse with Vercel AI SDK Data Stream Protocol compatible events
    """

    async def event_generator():
        text_id = f"txt_{query_hash}"

        # Start message event
        yield StartEvent(message_id=f"msg_{query_hash}").to_sse()

        # Start text block
        yield TextStartEvent(id=text_id).to_sse()

        try:
            # Get service and stream response
            service = InteractiveService()
            async for event in service.send_message(
                query_hash=query_hash,
                message=request.message,
                analysis_results=request.analysis_results or {},
                continue_existing=request.continue_existing,
            ):
                if isinstance(event, ChunkEvent):
                    # Yield text delta for each chunk
                    yield TextDeltaEvent(id=text_id, delta=event.text).to_sse()
                elif isinstance(event, InteractiveErrorEvent):
                    # Stream error from service
                    yield ErrorEvent(error_text=event.error).to_sse()
                    return  # Stop streaming on error
                elif isinstance(event, InteractiveCompleteEvent):
                    # Stream complete - handled below
                    pass

            # End text block (only on success)
            yield TextEndEvent(id=text_id).to_sse()

            # Finish message event
            yield FinishEvent().to_sse()
        except Exception as e:
            # Catch any unexpected errors
            yield ErrorEvent(error_text=str(e)).to_sse()

    response = create_ai_stream_response(event_generator())
    # REQUIRED header for custom backends (per Vercel docs)
    response.headers["x-vercel-ai-ui-message-stream"] = "v1"
    return response


@router.delete("/interactive/{query_hash}")
async def delete_conversation(query_hash: str) -> DeleteResponse:
    """Delete conversation to start fresh.

    Args:
        query_hash: Hash of the query being discussed

    Returns:
        DeleteResponse with success flag
    """
    service = InteractiveService()
    success = service.delete_conversation(query_hash)
    return DeleteResponse(success=success)

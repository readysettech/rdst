"""API routes for data agent management and one-shot questions.

Agents are stored in ~/.rdst/agents/*.yaml. The ask endpoint runs the full
NL-to-SQL pipeline (LLM + database) in a worker thread and matches the
response shape of the `rdst agent serve` HTTP mode.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from features.agent.manager import (
    AgentExistsError,
    AgentManager,
    AgentManagerError,
    AgentNotFoundError,
    InvalidAgentNameError,
    TargetNotFoundError,
)
from shared.api.target_guard import ensure_target_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentSafetyModel(BaseModel):
    read_only: bool = True
    max_rows: int = 1000
    timeout_seconds: int = 30


class AgentRestrictionsModel(BaseModel):
    allowed_tables: Optional[list[str]] = None
    denied_columns: Optional[list[str]] = None
    masked_columns: Optional[dict[str, str]] = None


class AgentDetail(BaseModel):
    name: str
    target: str
    description: str = ""
    created_at: Optional[str] = None
    guard: Optional[str] = None
    safety: AgentSafetyModel = Field(default_factory=AgentSafetyModel)
    restrictions: AgentRestrictionsModel = Field(default_factory=AgentRestrictionsModel)
    semantic_layer: Optional[str] = None


class AgentSummary(BaseModel):
    name: str
    target: str
    guard: Optional[str] = None
    description: str = ""
    max_rows: int = 1000
    created_at: Optional[str] = None


class AgentListResponse(BaseModel):
    agents: list[AgentSummary]
    count: int


class AgentCreateRequest(BaseModel):
    name: str
    target: str
    description: str = ""
    max_rows: int = 1000
    timeout_seconds: int = 30
    denied_columns: Optional[list[str]] = None
    allowed_tables: Optional[list[str]] = None
    masked_columns: Optional[dict[str, str]] = None
    guard: Optional[str] = None


class AgentWriteResponse(BaseModel):
    success: bool
    name: str


class AgentAskRequest(BaseModel):
    question: str


class AgentAskResponse(BaseModel):
    success: bool
    sql: Optional[str] = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    execution_time_ms: float = 0.0
    truncated: bool = False
    error: Optional[str] = None
    explanation: Optional[str] = None


def _config_to_detail(config) -> AgentDetail:
    return AgentDetail(
        name=config.name,
        target=config.target,
        description=config.description,
        created_at=config.created_at,
        guard=config.guard,
        safety=AgentSafetyModel(
            read_only=config.safety.read_only,
            max_rows=config.safety.max_rows,
            timeout_seconds=config.safety.timeout_seconds,
        ),
        restrictions=AgentRestrictionsModel(
            allowed_tables=config.restrictions.allowed_tables,
            denied_columns=config.restrictions.denied_columns,
            masked_columns=config.restrictions.masked_columns,
        ),
        semantic_layer=config.semantic_layer,
    )


def _get_agent(name: str):
    try:
        return AgentManager().get(name)
    except AgentNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")


@router.get("")
async def list_agents() -> AgentListResponse:
    """List all agents."""
    manager = AgentManager()
    summaries = []
    for name in manager.list():
        try:
            config = manager.get(name)
        except Exception as e:
            logger.warning("Skipping unreadable agent '%s': %s", name, e)
            continue
        summaries.append(
            AgentSummary(
                name=config.name,
                target=config.target,
                guard=config.guard,
                description=config.description,
                max_rows=config.safety.max_rows,
                created_at=config.created_at,
            )
        )
    return AgentListResponse(agents=summaries, count=len(summaries))


@router.get("/{name}")
async def get_agent(name: str) -> AgentDetail:
    """Get full agent configuration."""
    return _config_to_detail(_get_agent(name))


@router.post("")
async def create_agent(request: AgentCreateRequest) -> AgentWriteResponse:
    """Create a new agent. Validates that the target and guard exist."""
    try:
        AgentManager().create(
            name=request.name,
            target=request.target,
            description=request.description,
            max_rows=request.max_rows,
            timeout_seconds=request.timeout_seconds,
            denied_columns=request.denied_columns,
            allowed_tables=request.allowed_tables,
            masked_columns=request.masked_columns,
            guard=request.guard,
        )
    except InvalidAgentNameError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except AgentExistsError:
        raise HTTPException(
            status_code=409, detail=f"Agent '{request.name}' already exists"
        )
    except TargetNotFoundError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except AgentManagerError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return AgentWriteResponse(success=True, name=request.name)


@router.delete("/{name}")
async def delete_agent(name: str) -> AgentWriteResponse:
    """Delete an agent."""
    if not AgentManager().delete(name):
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    return AgentWriteResponse(success=True, name=name)


@router.post("/{name}/ask")
async def ask_agent(name: str, request: AgentAskRequest) -> AgentAskResponse:
    """Ask the agent a one-shot natural-language question.

    Runs NL-to-SQL, guard/safety validation, execution, and masking; the
    response matches the `rdst agent serve` /ask payload.
    """
    config = _get_agent(name)
    ensure_target_password(config.target)

    from features.agent.runtime import AgentRuntime

    runtime = AgentRuntime(config)
    response = await asyncio.to_thread(runtime.ask, request.question)
    return AgentAskResponse(**response.to_dict())


@router.get("/{name}/schema")
async def get_agent_schema(name: str) -> dict[str, Any]:
    """Get the schema summary visible to an agent."""
    config = _get_agent(name)
    ensure_target_password(config.target)

    from features.agent.runtime import AgentRuntime

    runtime = AgentRuntime(config)
    return await asyncio.to_thread(runtime.get_schema_summary)


class ChatSessionCreateResponse(BaseModel):
    session_id: str
    agent: str


class ChatMessageRequest(BaseModel):
    message: str


class ChatHistoryMessage(BaseModel):
    role: str
    summary: str


class ChatSessionInfo(BaseModel):
    session_id: str
    agent: str
    message_count: int
    history: list[ChatHistoryMessage]


def _event_to_sse(event) -> dict:
    return {"event": event.type, "data": json.dumps(asdict(event), default=str)}


@router.post("/{name}/chat/sessions")
async def create_chat_session(name: str) -> ChatSessionCreateResponse:
    """Start a chat session with an agent.

    Sessions are held in server memory; history is ephemeral and dies with
    the process, matching CLI chat semantics.
    """
    config = _get_agent(name)
    ensure_target_password(config.target)

    from features.agent.service import ChatSessionCapacityError, chat_sessions

    try:
        session = chat_sessions.create(config)
    except ChatSessionCapacityError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return ChatSessionCreateResponse(session_id=session.session_id, agent=name)


@router.post("/chat/sessions/{session_id}/message")
async def send_chat_message(session_id: str, request: ChatMessageRequest):
    """Send one message to a chat session and stream progress via SSE.

    stream_chat rejects the turn with an error event if the session is
    already processing a message.
    """
    from features.agent.service import chat_sessions, stream_chat

    session = chat_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    async def _generator():
        async for event in stream_chat(session, request.message):
            yield _event_to_sse(event)

    return EventSourceResponse(_generator())


@router.get("/chat/sessions/{session_id}")
async def get_chat_session(session_id: str) -> ChatSessionInfo:
    """Get a chat session's history summary."""
    from features.agent.service import chat_sessions

    session = chat_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    history = [
        ChatHistoryMessage(role=m["role"], summary=m["summary"])
        for m in session.agent.get_history_summary()
    ]
    return ChatSessionInfo(
        session_id=session_id,
        agent=session.agent_name,
        message_count=len(history),
        history=history,
    )


@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session(session_id: str) -> AgentWriteResponse:
    """End a chat session and discard its history."""
    from features.agent.service import chat_sessions

    if not chat_sessions.delete(session_id):
        raise HTTPException(status_code=404, detail="Chat session not found")
    return AgentWriteResponse(success=True, name=session_id)

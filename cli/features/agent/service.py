"""Chat session service for the web API.

Holds in-memory chat sessions (one ChatAgent each, keyed by uuid) and
bridges the synchronous ChatAgent.chat callback protocol into an async
event stream. Sessions are ephemeral by design, matching the CLI chat
semantics: history lives in server memory and dies with the process.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator

from features.agent.chat_agent import ChatAgent
from features.agent.config import AgentConfig
from features.agent.events import (
    ChatCompleteEvent,
    ChatErrorEvent,
    ChatEvent,
    ChatResponseEvent,
    ChatStatusEvent,
    ChatThinkingEvent,
    ChatToolCallEvent,
    ChatToolResultEvent,
)


@dataclass
class ChatSession:
    session_id: str
    agent_name: str
    agent: ChatAgent
    created_at: float
    last_used: float
    # True while a chat turn's worker thread is alive. The worker clears it
    # when it terminates, so a client disconnect mid-turn cannot admit a
    # second concurrent turn against the same ChatAgent.
    busy: bool = False


class ChatSessionCapacityError(RuntimeError):
    """Raised when the session store is full and every session is busy."""


class ChatSessionStore:
    """In-memory chat session registry with idle eviction and an LRU cap.

    All access happens on the event loop; no internal locking is needed as
    long as methods do not await between check and mutation.
    """

    def __init__(self, max_sessions: int = 50, idle_ttl_seconds: float = 1800.0):
        self._sessions: dict[str, ChatSession] = {}
        self._max_sessions = max_sessions
        self._idle_ttl = idle_ttl_seconds

    def _evict_stale(self) -> None:
        now = time.monotonic()
        stale = [
            sid
            for sid, session in self._sessions.items()
            if not session.busy and now - session.last_used > self._idle_ttl
        ]
        for sid in stale:
            del self._sessions[sid]

    def _make_room(self) -> None:
        while len(self._sessions) >= self._max_sessions:
            idle = [s for s in self._sessions.values() if not s.busy]
            if not idle:
                raise ChatSessionCapacityError(
                    f"All {self._max_sessions} chat sessions are busy; try again later."
                )
            oldest = min(idle, key=lambda s: s.last_used)
            del self._sessions[oldest.session_id]

    def create(self, agent_config: AgentConfig) -> ChatSession:
        self._evict_stale()
        self._make_room()
        now = time.monotonic()
        session = ChatSession(
            session_id=uuid.uuid4().hex,
            agent_name=agent_config.name,
            agent=ChatAgent(agent_config),
            created_at=now,
            last_used=now,
        )
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> ChatSession | None:
        self._evict_stale()
        session = self._sessions.get(session_id)
        if session is not None:
            session.last_used = time.monotonic()
        return session

    def delete(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def clear(self) -> None:
        self._sessions.clear()


# Shared store for the API process.
chat_sessions = ChatSessionStore()


def _map_callback_event(event: str, data: dict[str, Any]) -> ChatEvent | None:
    if event == "llm_call":
        return ChatStatusEvent(
            type="status",
            phase="llm",
            message="Thinking...",
            iteration=data.get("iteration"),
        )
    if event == "thinking":
        return ChatThinkingEvent(type="thinking", text=data.get("text", ""))
    if event == "tool_call":
        return ChatToolCallEvent(
            type="tool_call",
            name=data.get("name", ""),
            input=data.get("input", {}),
        )
    if event == "tool_result":
        result = data.get("result")
        if result is None:
            return None
        return ChatToolResultEvent(
            type="tool_result",
            success=result.success,
            content=result.content,
            data=result.data,
        )
    return None


async def stream_chat(session: ChatSession, message: str) -> AsyncIterator[ChatEvent]:
    """Run one chat turn, yielding typed events as the agent works.

    ChatAgent.chat runs in a worker thread; its callback events are handed
    to the event loop through a queue. The worker enqueues a terminal item
    itself, so the stream drains fully before finishing. The session stays
    busy until the worker terminates, even if the client disconnects and
    this generator is cancelled mid-stream.
    """
    if session.busy:
        yield ChatErrorEvent(
            type="error",
            message="A message is already being processed for this session.",
        )
        return

    session.busy = True
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    def on_event(event: str, data: dict[str, Any]) -> None:
        mapped = _map_callback_event(event, data)
        if mapped is not None:
            loop.call_soon_threadsafe(queue.put_nowait, ("event", mapped))

    def run() -> None:
        try:
            response = session.agent.chat(message, on_event=on_event)
            loop.call_soon_threadsafe(queue.put_nowait, ("done", response))
        except Exception as e:
            loop.call_soon_threadsafe(queue.put_nowait, ("error", e))
        finally:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(_release)

    def _release() -> None:
        session.busy = False
        session.last_used = time.monotonic()

    def _on_worker_done(task: asyncio.Task) -> None:
        # to_thread can only be cancelled before the thread starts; in that
        # case run()'s finally never fires, so release here instead.
        if task.cancelled():
            _release()
        else:
            task.exception()

    # run() catches all exceptions itself; if the client disconnects
    # mid-stream the thread finishes on its own and the queue is dropped.
    worker = asyncio.create_task(asyncio.to_thread(run))
    worker.add_done_callback(_on_worker_done)

    while True:
        kind, payload = await queue.get()
        if kind == "event":
            yield payload
        elif kind == "done":
            yield ChatResponseEvent(type="response", text=payload.text)
            yield ChatCompleteEvent(
                type="complete",
                success=True,
                tool_result_count=len(payload.tool_results),
            )
            return
        else:
            yield ChatErrorEvent(type="error", message=str(payload))
            return

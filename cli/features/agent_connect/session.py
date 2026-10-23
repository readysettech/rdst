"""Prompt attempts and the agent progress feed.

Both live in this process only, and the feed only reaches the UI windows open right now.
"""

from __future__ import annotations

import asyncio
import secrets
import threading
import time
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

# The agent run RDST started that the current request comes from, if any: its events say so,
# so the paste prompt's progress doesn't mistake them for its own agent.
run_source: ContextVar[str | None] = ContextVar("agent_connect_run_source", default=None)

EVENT_HISTORY = 50
STEPS = ("connected", "database_added", "tested", "needs_setup", "awaiting_user",
         "monitoring_enabled", "settings_changed", "error")

# Tool calls this long after a prompt was issued still count toward that prompt's attempt.
ATTEMPT_WINDOW_SECONDS = 2 * 60 * 60
# Recent prompts remembered, so an agent using an older copied prompt still joins its attempt.
ATTEMPT_LIMIT = 20

_lock = threading.Lock()
_attempt: tuple[str, float] | None = None
# Prompt nonce -> when it was issued, oldest first.
_issued: dict[str, float] = {}
# The latest prompt's nonce until an agent's first /mcp request after it.
_awaiting: str | None = None


def issue_attempt(now: float | None = None) -> str:
    """A nonce naming one copied prompt; the prompt's MCP URL carries it as ``?a=``."""
    global _awaiting
    now = time.time() if now is None else now
    nonce = secrets.token_hex(8)
    with _lock:
        _prune_attempts(now)
        _issued[nonce] = now
        while len(_issued) > ATTEMPT_LIMIT:
            del _issued[next(iter(_issued))]
        _awaiting = nonce
    return nonce


def attempt_known(nonce: str, now: float | None = None) -> bool:
    """Whether ``nonce`` names a recent prompt."""
    with _lock:
        _prune_attempts(time.time() if now is None else now)
        return nonce in _issued


def agent_request(nonce: str | None, now: float | None = None) -> bool:
    """Note the attempt an /mcp request belongs to; True for the first request
    since the latest prompt was shown, which is when the UI ticks "Agent connected".

    A request naming a recent prompt (``nonce``, from ``?a=``) joins that prompt's attempt;
    one that doesn't (an agent registered from Settings, or from before a restart) joins the
    latest prompt while no agent has answered it yet.
    """
    global _attempt, _awaiting
    from shared import product_telemetry

    now = time.time() if now is None else now
    with _lock:
        _prune_attempts(now)
        first = _awaiting is not None
        attempt = nonce if nonce and nonce in _issued else _awaiting
        _awaiting = None
        if attempt is not None:
            _attempt = (product_telemetry.agent_attempt_id(attempt), _issued[attempt])
    return first


def _prune_attempts(now: float) -> None:
    global _awaiting
    for nonce, issued in list(_issued.items()):
        if now - issued > ATTEMPT_WINDOW_SECONDS:
            del _issued[nonce]
    if _awaiting not in _issued:
        _awaiting = None


def current_attempt(now: float | None = None) -> str | None:
    """The attempt the agent's latest prompt started, while it's recent; else None."""
    now = time.time() if now is None else now
    with _lock:
        if _attempt is None or now - _attempt[1] > ATTEMPT_WINDOW_SECONDS:
            return None
        return _attempt[0]


@dataclass
class _Subscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue


class EventBus:
    """Fan-out of agent progress events to every open /api/agent-connect/events stream."""

    def __init__(self) -> None:
        self._subscribers: list[_Subscriber] = []
        self._history: deque[dict[str, Any]] = deque(maxlen=EVENT_HISTORY)
        self._seq = 0
        self._lock = threading.Lock()

    def publish(self, step: str, target: str | None = None, message: str | None = None,
                **fields: Any) -> dict:
        """Send ``step``; ``fields`` carries step-specific data (needs_setup's instructions)."""
        if step not in STEPS:
            raise ValueError(f"Unknown agent-connect step: {step}")
        with self._lock:
            self._seq += 1
            event = {"id": self._seq, "ts": time.time(), "step": step}
            if target is not None:
                event["target"] = target
            if message is not None:
                event["message"] = message
            event.update({key: value for key, value in fields.items() if value is not None})
            if run := run_source.get():
                event["run"] = run
            self._history.append(event)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            subscriber.loop.call_soon_threadsafe(_offer, subscriber.queue, event)
        return event

    def subscribe(self, since_id: int | None = None,
                  since_ts: float | None = None) -> tuple[_Subscriber, list[dict]]:
        """Register a stream; returns it with the recent events it asked to replay."""
        subscriber = _Subscriber(asyncio.get_running_loop(), asyncio.Queue(maxsize=100))
        with self._lock:
            self._subscribers.append(subscriber)
            if since_id is not None:
                backlog = [event for event in self._history if event["id"] > since_id]
            elif since_ts is not None:
                backlog = [event for event in self._history if event["ts"] >= since_ts]
            else:
                backlog = []
        return subscriber, backlog

    def unsubscribe(self, subscriber: _Subscriber) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)


def _offer(queue: asyncio.Queue, event: dict) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(event)


events = EventBus()

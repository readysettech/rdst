"""Unit tests for the in-memory chat session store."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from features.agent.config import AgentConfig
from features.agent.service import ChatSessionCapacityError, ChatSessionStore


def _config(name: str = "bot") -> AgentConfig:
    return AgentConfig(name=name, target="db")


@pytest.fixture(autouse=True)
def _no_runtime():
    # ChatAgent builds an AgentRuntime; keep the store tests hermetic.
    with patch("features.agent.service.ChatAgent") as fake:
        fake.side_effect = lambda cfg: object()
        yield


async def test_create_get_delete_roundtrip():
    store = ChatSessionStore()
    session = store.create(_config())

    assert store.get(session.session_id) is session
    assert store.delete(session.session_id) is True
    assert store.get(session.session_id) is None
    assert store.delete(session.session_id) is False


async def test_idle_sessions_evicted_after_ttl():
    store = ChatSessionStore(idle_ttl_seconds=100.0)

    with patch("features.agent.service.time.monotonic", return_value=1000.0):
        session = store.create(_config())

    with patch("features.agent.service.time.monotonic", return_value=1050.0):
        assert store.get(session.session_id) is session

    # get() above touched last_used to 1050; expire well past it.
    with patch("features.agent.service.time.monotonic", return_value=1200.0):
        assert store.get(session.session_id) is None


async def test_lru_eviction_at_capacity():
    store = ChatSessionStore(max_sessions=2, idle_ttl_seconds=10_000.0)

    with patch("features.agent.service.time.monotonic", return_value=1.0):
        oldest = store.create(_config("a"))
    with patch("features.agent.service.time.monotonic", return_value=2.0):
        newer = store.create(_config("b"))
    with patch("features.agent.service.time.monotonic", return_value=3.0):
        newest = store.create(_config("c"))

    with patch("features.agent.service.time.monotonic", return_value=4.0):
        assert store.get(oldest.session_id) is None
        assert store.get(newer.session_id) is newer
        assert store.get(newest.session_id) is newest


async def test_busy_sessions_survive_eviction():
    store = ChatSessionStore(idle_ttl_seconds=100.0)

    with patch("features.agent.service.time.monotonic", return_value=1000.0):
        session = store.create(_config())

    session.busy = True
    try:
        with patch("features.agent.service.time.monotonic", return_value=5000.0):
            assert store.get(session.session_id) is session
    finally:
        session.busy = False


async def test_create_raises_when_every_session_is_busy():
    store = ChatSessionStore(max_sessions=2, idle_ttl_seconds=10_000.0)

    for name in ("a", "b"):
        store.create(_config(name)).busy = True

    with pytest.raises(ChatSessionCapacityError):
        store.create(_config("c"))

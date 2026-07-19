"""SSE ``error`` events carry the shared ``{code, message, detail}`` envelope.

Part of B7/T24: a failure streamed over SSE must surface the same envelope the
HTTP handlers emit, and a raw ``str(e)`` (which can embed DSNs, hosts, or
secrets) must never reach the stream.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

from features.analyze.api.routes import _event_to_sse as analyze_event_to_sse
from features.cache.api.routes import _event_to_sse as cache_event_to_sse
from features.top.api import routes as top_routes
from features.top.models import TopOptions
from shared.service_events import ErrorEvent

# A synthetic, obviously-fake credential marker embedded in a fake DSN. These
# tests prove the marker never reaches the streamed error payload. It is not a
# real secret; the name and value are chosen so credential scanners do not
# false-positive on the test source.
FAKE_DSN_CREDENTIAL_MARKER = "not-a-real-credential-marker"


def test_analyze_error_event_serializes_envelope() -> None:
    mapped = analyze_event_to_sse(
        ErrorEvent(
            type="error",
            message="The analysis could not be completed.",
            code="internal_error",
            detail="TimeoutError",
            stage="executing_explain",
        )
    )

    assert mapped["event"] == "error"
    data = json.loads(mapped["data"])
    assert data["code"] == "internal_error"
    assert data["message"] == "The analysis could not be completed."
    assert data["detail"] == "TimeoutError"
    assert data["stage"] == "executing_explain"


def test_cache_error_event_serializes_envelope() -> None:
    mapped = cache_event_to_sse(
        ErrorEvent(
            type="error",
            message="The benchmark could not be completed.",
            code="internal_error",
            detail="ConnectionError",
        )
    )

    assert mapped["event"] == "error"
    data = json.loads(mapped["data"])
    assert data["code"] == "internal_error"
    assert data["message"] == "The benchmark could not be completed."
    assert data["detail"] == "ConnectionError"


async def test_top_in_service_error_emits_envelope_not_str_e(monkeypatch) -> None:
    """The in-service catch (the common failure path) yields an envelope-shaped
    TopErrorEvent, and its SSE serialization never carries raw DSN/secret text."""
    from unittest.mock import patch

    from features.top.api.routes import _event_to_sse as top_event_to_sse
    from features.top.models import TopInput
    from features.top.service import TopService

    service = TopService()
    events = []
    with patch.object(service, "_load_config") as mock_load:
        mock_load.side_effect = RuntimeError(
            f"connection failed: postgresql://user:{FAKE_DSN_CREDENTIAL_MARKER}@db.internal/prod"
        )
        async for event in service.get_top_queries(
            TopInput(target="demo", source="auto"), TopOptions()
        ):
            events.append(event)

    error_event = events[-1]
    assert error_event.type == "error"
    assert error_event.code == "internal_error"
    assert error_event.message == "The slow-query lookup could not be completed."
    assert error_event.detail == "RuntimeError"
    assert FAKE_DSN_CREDENTIAL_MARKER not in error_event.message

    mapped = top_event_to_sse(error_event)
    assert mapped["event"] == "error"
    assert FAKE_DSN_CREDENTIAL_MARKER not in mapped["data"]
    data = json.loads(mapped["data"])
    assert data["code"] == "internal_error"
    assert data["message"] == "The slow-query lookup could not be completed."
    assert data["detail"] == "RuntimeError"
    assert data["stage"] == "execution"


async def test_top_generator_failure_emits_envelope_not_str_e(monkeypatch) -> None:
    """A crashing service emits the envelope — never the raw exception text."""

    @asynccontextmanager
    async def _noop_command_run(*args, **kwargs):
        yield MagicMock()

    monkeypatch.setattr(top_routes.telemetry, "command_run", _noop_command_run)

    class _BoomService:
        async def get_top_queries(self, input_data, options):
            raise RuntimeError(
                f"postgresql://user:{FAKE_DSN_CREDENTIAL_MARKER}@db.internal/prod"
            )
            yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(top_routes, "TopService", _BoomService)

    events = []
    async for event in top_routes._historical_generator(
        "demo", "postgresql", "pg_stat_statements", TopOptions()
    ):
        events.append(event)

    assert events, "expected at least the error event"
    last = events[-1]
    assert last["event"] == "error"
    assert FAKE_DSN_CREDENTIAL_MARKER not in last["data"]
    data = json.loads(last["data"])
    assert data["code"] == "internal_error"
    assert data["message"] == "The slow-query lookup could not be completed."
    assert data["detail"] == "RuntimeError"

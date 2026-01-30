"""Tests for init route SSE mapping."""

import json

from lib.api.routes.init import _event_to_sse


class _UnknownInitEvent:
    pass


def test_unknown_init_event_maps_to_unknown():
    mapped = _event_to_sse(_UnknownInitEvent())

    assert mapped["event"] == "unknown"
    payload = json.loads(mapped["data"])
    assert "Unknown event type" in payload["message"]

"""Tests for semantic layer route SSE mapping."""

import json

from lib.api.routes.semantic_layer import _annotate_event_to_sse, _schema_event_to_sse


class _UnknownEvent:
    pass


def test_annotate_unknown_event_maps_to_unknown():
    mapped = _annotate_event_to_sse(_UnknownEvent())

    assert mapped["event"] == "unknown"
    payload = json.loads(mapped["data"])
    assert "Unknown event type" in payload["message"]


def test_schema_unknown_event_maps_to_unknown():
    mapped = _schema_event_to_sse(_UnknownEvent())

    assert mapped["event"] == "unknown"
    payload = json.loads(mapped["data"])
    assert "Unknown event type" in payload["message"]

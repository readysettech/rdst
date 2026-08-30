from __future__ import annotations

import json

import pytest

from features.ask.sql_generation import _parse_json_object_response


def test_parser_accepts_single_object():
    assert _parse_json_object_response('{"sql": "SELECT 1"}') == {"sql": "SELECT 1"}


def test_parser_accepts_markdown_fence():
    assert _parse_json_object_response('```json\n{"sql": "SELECT 1"}\n```') == {
        "sql": "SELECT 1"
    }


def test_parser_uses_final_object_after_model_self_correction():
    response = """```json
{"sql": "SELECT wrong FROM t"}
```

Wait, that is wrong. The corrected response is:

```json
{"sql": "SELECT correct FROM t"}
```"""
    assert _parse_json_object_response(response) == {"sql": "SELECT correct FROM t"}


def test_parser_accepts_trailing_commentary_after_one_object():
    assert _parse_json_object_response('{"sql": "SELECT 1"}\nDone.') == {
        "sql": "SELECT 1"
    }


def test_parser_rejects_no_json_object():
    with pytest.raises(json.JSONDecodeError):
        _parse_json_object_response("not json")


def test_parser_rejects_json_array():
    with pytest.raises(TypeError, match="JSON object"):
        _parse_json_object_response("[]")

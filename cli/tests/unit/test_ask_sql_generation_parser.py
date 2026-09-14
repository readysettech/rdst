from __future__ import annotations

import json

import pytest

from features.ask.sql_generation import _parse_json_object_response, generate_sql_from_nl
from shared.llm_manager.base import LLMError


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


def test_parser_ignores_unrelated_trailing_object_when_schema_is_known():
    response = (
        '{"sql":"SELECT correct FROM t","explanation":"grounded"}\n'
        '{"debug":true}'
    )

    assert _parse_json_object_response(
        response,
        required_fields={"sql", "explanation"},
        allowed_fields={"sql", "explanation"},
    ) == {"sql": "SELECT correct FROM t", "explanation": "grounded"}


def test_parser_rejects_no_json_object():
    with pytest.raises(json.JSONDecodeError):
        _parse_json_object_response("not json")


def test_parser_rejects_json_array():
    with pytest.raises(TypeError, match="JSON object"):
        _parse_json_object_response("[]")


class _BenchmarkManager:
    propagate_query_errors = True

    def __init__(self, response):
        self.response = response

    def generate_response(self, **kwargs):
        if isinstance(self.response, Exception):
            raise self.response
        return {"response": json.dumps(self.response)}


def _generate(response):
    return generate_sql_from_nl(
        "List events", "Table: events (name text)", "mysql", "fixture",
        _BenchmarkManager(response),
    )


@pytest.mark.parametrize("invalid", [
    {"additionalProperties": False},
    {"sql": 123},
    {"explanation": []},
    {"confidence": True},
    {"confidence": 2},
    {"assumptions": "none"},
    {"cannot_answer": "false"},
    {"cannot_answer_reason": []},
    {"missing_schema": "events"},
])
def test_invalid_model_fields_return_failure_in_benchmark_mode(invalid):
    response = {
        "sql": "SELECT name FROM events", "explanation": "List names.",
        "confidence": 0.9, "assumptions": [], "cannot_answer": False,
        "cannot_answer_reason": "", "missing_schema": [],
        **invalid,
    }
    result = _generate(response)
    assert result["success"] is False
    assert result["sql"] == ""
    assert result["error"]


def test_json_array_returns_failure_in_benchmark_mode():
    result = _generate([])
    assert result["success"] is False
    assert "JSON object" in result["error"]


@pytest.mark.parametrize("error", [
    ValueError("internal adapter failure"),
    LLMError("provider unavailable", code="PROVIDER_HTTP"),
])
def test_benchmark_still_propagates_adapter_and_provider_errors(error):
    with pytest.raises(type(error), match=str(error)):
        _generate(error)

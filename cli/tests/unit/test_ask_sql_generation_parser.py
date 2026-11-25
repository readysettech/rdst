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


class _TextManager(_BenchmarkManager):
    def generate_response(self, **kwargs):
        return {"response": self.response}


def _generate(response):
    return generate_sql_from_nl(
        "List events", "Table: events (name text)", "mysql", "fixture",
        _BenchmarkManager(response),
    )


_ANSWER = {
    "sql": "SELECT name FROM events", "explanation": "List names.",
    "confidence": 0.9, "assumptions": [], "cannot_answer": False,
    "cannot_answer_reason": "", "missing_schema": [],
}


@pytest.mark.parametrize("invalid", [
    {"sql": 123},
    {"cannot_answer": "false"},
])
def test_invalid_model_fields_return_failure_in_benchmark_mode(invalid):
    result = _generate({**_ANSWER, **invalid})
    assert result["success"] is False
    assert result["sql"] == ""
    assert result["error"]


@pytest.mark.parametrize(("slip", "relaxed"), [
    ({"additionalProperties": False}, {}),
    ({"explanation": []}, {"explanation": ""}),
    ({"confidence": True}, {"confidence": 0.0}),
    ({"confidence": 2}, {"confidence": 1.0}),
    ({"confidence": -0.5}, {"confidence": 0.0}),
    ({"confidence": "high"}, {"confidence": 0.0}),
    ({"assumptions": "Names are unique."}, {"assumptions": ["Names are unique."]}),
    ({"assumptions": ["Names are unique.", 3]}, {"assumptions": ["Names are unique."]}),
    ({"cannot_answer_reason": "missing_schema"}, {}),
    ({"cannot_answer_reason": []}, {}),
    ({"missing_schema": ["event dates"]}, {}),
    ({"missing_schema": "events"}, {}),
    ({"cannot_answer": None}, {}),
])
def test_format_slips_beside_usable_sql_keep_the_answer(slip, relaxed):
    result = _generate({**_ANSWER, **slip})
    assert result["success"] is True
    assert result["sql"] == "SELECT name FROM events"
    expected = {**_ANSWER, **relaxed}
    assert {key: result[key] for key in expected} == expected
    assert result["raw_response"] == {**_ANSWER, **slip}


@pytest.mark.parametrize("missing", ["explanation", "confidence", "missing_schema"])
def test_missing_fields_beside_usable_sql_keep_the_answer(missing):
    response = {key: value for key, value in _ANSWER.items() if key != missing}
    result = _generate(response)
    assert result["success"] is True
    assert result["sql"] == "SELECT name FROM events"


def test_measures_the_sql_derives_are_reported_without_malformed_entries():
    derived = [
        {"term": " debt service ratio ", "used": "Liabilities over income",
         "alternatives": ["Loans over income", 3, "Mortgage over income", "Rent"]},
        {"term": "", "used": "Nothing", "alternatives": []},
        {"term": "score", "used": 1, "alternatives": []},
        "loose text",
    ]
    result = _generate({**_ANSWER, "derived": derived})
    assert result["success"] is True
    assert result["derived"] == [
        {"term": "debt service ratio", "used": "Liabilities over income",
         "alternatives": ["Loans over income", "Mortgage over income"]},
    ]


@pytest.mark.parametrize("response", [
    _ANSWER,
    {**_ANSWER, "sql": "", "cannot_answer": True,
     "cannot_answer_reason": "missing_schema", "missing_schema": ["event dates"]},
])
def test_a_reply_without_derived_measures_reports_none(response):
    result = _generate(response)
    assert result["success"] is True
    assert result["derived"] == []


def test_answer_object_with_stray_fields_is_parsed_from_prose():
    answer = {**_ANSWER, "notes": "checked"}
    text = f"Here is the query: {json.dumps(answer)} It lists every name."
    result = generate_sql_from_nl(
        "List events", "Table: events (name text)", "mysql", "fixture",
        _TextManager(text),
    )
    assert result["success"] is True
    assert result["sql"] == "SELECT name FROM events"


@pytest.mark.parametrize(("refusal", "error"), [
    ({"sql": "", "cannot_answer": True, "cannot_answer_reason": "missing_schema",
      "confidence": 2}, "confidence must be a number between 0 and 1"),
    ({"cannot_answer": True, "cannot_answer_reason": "missing_schema"},
     "cannot_answer responses must leave sql empty"),
])
def test_refusals_keep_the_strict_contract(refusal, error):
    result = _generate({**_ANSWER, **refusal})
    assert result["success"] is False
    assert result["error"] == error


def test_genuine_refusal_stays_a_refusal():
    result = _generate({
        **_ANSWER, "sql": "", "cannot_answer": True,
        "cannot_answer_reason": "missing_schema", "missing_schema": ["event dates"],
    })
    assert result["success"] is True
    assert result["cannot_answer"] is True
    assert result["cannot_answer_reason"] == "missing_schema"
    assert result["missing_schema"] == ["event dates"]


def test_relaxed_answers_still_refuse_writes():
    result = _generate({
        **_ANSWER, "sql": "DELETE FROM events", "cannot_answer_reason": "missing_schema",
    })
    assert result["success"] is False
    assert result["error_code"] == "ask_unsafe_sql"


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


@pytest.mark.parametrize("reply", [
    '{"corrected_sql": "SELECT 1", "confidence": 0.8}',
    'The column was misspelled. Here is the fix:\n\n```json\n'
    '{"corrected_sql": "SELECT 1", "confidence": 0.8}\n```\nThis should run.',
])
def test_error_recovery_accepts_json_inside_prose(reply):
    from features.ask.sql_generation import recover_from_error

    class Manager:
        def generate_response(self, **kwargs):
            assert kwargs["extra"]["response_format"] == {"type": "json_object"}
            return {"response": reply}

    result = recover_from_error(
        nl_question="How many users?",
        failed_sql="SELECT count(*) FROM users",
        error_message="query returned no rows",
        filtered_schema="Table: users",
        database_engine="postgresql",
        llm_manager=Manager(),
    )
    assert result["success"] is True
    assert result["corrected_sql"] == "SELECT 1"
    assert result["confidence"] == 0.8


def test_error_recovery_never_mistakes_a_nested_object_for_the_answer():
    from features.ask.sql_generation import recover_from_error

    # The outer object is broken by an unescaped quote; only its nested diagnosis decodes.
    reply = '{"diagnosis": {"root_cause": "typo"}, "corrected_sql": "SELECT "name" FROM t"}'

    class Manager:
        def generate_response(self, **kwargs):
            return {"response": reply}

    result = recover_from_error(
        nl_question="How many users?",
        failed_sql="SELECT count(*) FROM users",
        error_message="query returned no rows",
        filtered_schema="Table: users",
        database_engine="postgresql",
        llm_manager=Manager(),
    )
    assert result["success"] is False

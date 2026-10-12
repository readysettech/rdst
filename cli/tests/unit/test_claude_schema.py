"""Claude JSON Schema restatement shared by own-key and hosted requests."""

import importlib.util
import sys
from pathlib import Path

import pytest

from shared.llm_manager.claude_schema import claude_json_schema

SCHEMAS = [
    {
        "type": "object",
        "properties": {
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "options": {"type": "array", "minItems": 2, "maxItems": 3,
                        "items": {"type": "string", "maxLength": 240}},
            "evidence": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/fact"}},
            "number": {"type": ["string", "null"], "description": "Requested number"},
            "kind": {"const": "answer"},
            "choice": {"oneOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]},
        },
        "required": ["confidence", "options", "evidence", "number", "kind", "choice"],
        "$defs": {"fact": {"type": "object", "properties": {"index": {"type": "integer"}}}},
    },
    {"type": "string", "format": "text", "description": "Free text"},
    {"type": ["object", "null"], "properties": {"a": {"type": "string"}}, "required": ["a"]},
    {"type": ["string", "null"], "enum": ["x", None]},
    {"type": "array", "items": {"type": ["integer", "null"], "minimum": 0}, "maxItems": 2},
    {"allOf": [{"type": "object", "properties": {}}]},
    {"$ref": "#/$defs/x", "$defs": {"x": {"type": "object", "properties": {
        "n": {"type": "number", "exclusiveMinimum": 0}}}}},
    {"$ref": "#/definitions/x", "definitions": {"x": {"type": "object", "properties": {}}}},
    {"type": ["string", "null"], "const": "answer"},
    {"type": ["string", "null"], "enum": ["a", "b"]},
    {"type": ["string", "null"], "const": None},
]


def _keyservice_module():
    path = Path(__file__).parents[2] / "keyservice" / "src" / "hosted_inference.py"
    spec = importlib.util.spec_from_file_location("keyservice_hosted_inference", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("schema", SCHEMAS)
def test_own_key_and_hosted_requests_restate_schemas_identically(schema):
    assert claude_json_schema(schema) == _keyservice_module().claude_json_schema(schema)


def test_restatement_keeps_the_supported_subset():
    restated = claude_json_schema(SCHEMAS[0])
    properties = restated["properties"]
    assert restated["additionalProperties"] is False
    assert restated["$defs"]["fact"]["additionalProperties"] is False
    assert properties["confidence"] == {
        "type": "number", "description": "{minimum: 0.0, maximum: 1.0}"}
    assert properties["options"]["description"] == "{minItems: 2, maxItems: 3}"
    assert properties["evidence"]["minItems"] == 1
    assert properties["number"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert properties["choice"]["anyOf"][0] == {"type": "integer", "description": "{minimum: 0}"}
    assert claude_json_schema(SCHEMAS[1]) == {
        "type": "string", "description": 'Free text\n\n{format: "text"}'}


def test_composed_objects_have_no_claude_restatement_on_either_path():
    schema = {"allOf": [{"type": "object", "properties": {"a": {"type": "string"}}},
                        {"type": "object", "properties": {"b": {"type": "integer"}}}]}
    with pytest.raises(ValueError):
        claude_json_schema(schema)
    with pytest.raises(ValueError):
        _keyservice_module().claude_json_schema(schema)


def test_open_objects_have_no_claude_restatement_on_either_path():
    schema = {"type": "object", "properties": {
        "filters": {"type": "object", "additionalProperties": {"type": "string"}}}}
    with pytest.raises(ValueError):
        claude_json_schema(schema)
    with pytest.raises(ValueError):
        _keyservice_module().claude_json_schema(schema)


def test_type_unions_keep_each_keyword_on_the_branch_it_applies_to():
    assert claude_json_schema(SCHEMAS[2]) == {"anyOf": [
        {"type": "object", "properties": {"a": {"type": "string"}},
         "additionalProperties": False, "required": ["a"]},
        {"type": "null"},
    ]}
    assert claude_json_schema(SCHEMAS[3]) == {"anyOf": [
        {"type": "string", "enum": ["x"]}, {"type": "null"}]}
    # A branch the const or enum rules out is dropped, so null stays rejected.
    assert claude_json_schema(SCHEMAS[8]) == {"anyOf": [{"type": "string", "const": "answer"}]}
    assert claude_json_schema(SCHEMAS[9]) == {"anyOf": [{"type": "string", "enum": ["a", "b"]}]}
    assert claude_json_schema(SCHEMAS[10]) == {"anyOf": [{"type": "null"}]}
    assert set(claude_json_schema(SCHEMAS[7])) == {"definitions", "$ref"}

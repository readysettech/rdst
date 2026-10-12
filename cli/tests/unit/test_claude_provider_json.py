"""ClaudeProvider structured-output request tests."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from shared.llm_manager.base import ProviderRequest
from shared.llm_manager.claude_provider import ClaudeProvider


def _request(response_format, model="claude-haiku-4-5-20251001"):
    return ProviderRequest(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are a database expert. Return only valid JSON.",
            },
            {
                "role": "user",
                "content": 'Analyze this question: "show me all users"',
            },
        ],
        max_tokens=200,
        temperature=0.0,
        extra={"response_format": response_format},
    )


def _sdk_client(text='{"suggested_tables": ["users"]}'):
    message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        to_dict=lambda: {"content": [{"type": "text", "text": text}]},
        _request_id="req_test",
    )
    raw = SimpleNamespace(
        parse=Mock(return_value=message),
        request_id="req_test",
        headers={},
    )
    create = Mock(return_value=raw)
    client = SimpleNamespace(
        messages=SimpleNamespace(
            with_raw_response=SimpleNamespace(create=create),
        )
    )
    return client, create


class TestJsonObjectResponseFormat:
    def test_json_object_format_enforces_json_output(self):
        client, create = _sdk_client()
        with patch(
            "shared.llm_manager.claude_provider.anthropic.Anthropic",
            return_value=client,
        ):
            ClaudeProvider().complete(
                _request({"type": "json_object"}), api_key="test-key"
            )

        payload = create.call_args.kwargs
        assert "respond with valid json" in payload["system"].lower()

    def test_json_object_does_not_leak_response_format_to_api(self):
        client, create = _sdk_client()
        with patch(
            "shared.llm_manager.claude_provider.anthropic.Anthropic",
            return_value=client,
        ):
            ClaudeProvider().complete(
                _request({"type": "json_object"}), api_key="test-key"
            )

        payload = create.call_args.kwargs
        assert "response_format" not in payload
        assert "extra_body" not in payload

    def test_json_schema_uses_forced_tool(self):
        client, create = _sdk_client()
        request = _request(
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "table_selection",
                    "schema": {
                        "type": "object",
                        "properties": {"tables": {"type": "array"}},
                    },
                },
            }
        )

        with patch(
            "shared.llm_manager.claude_provider.anthropic.Anthropic",
            return_value=client,
        ):
            ClaudeProvider().complete(request, api_key="test-key")

        payload = create.call_args.kwargs
        assert payload["tools"][0]["name"] == "table_selection"
        assert payload["tool_choice"] == {
            "type": "tool",
            "name": "table_selection",
        }


class TestNativeStructuredOutput:
    SCHEMA = {
        "type": "object",
        "properties": {
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "sql": {"type": "string"},
        },
        "required": ["confidence", "sql"],
        "additionalProperties": False,
    }

    def test_current_models_reason_and_enforce_a_restated_schema(self):
        client, create = _sdk_client(text='{"confidence": 0.9, "sql": "SELECT 1"}')
        request = _request(
            {"type": "json_schema", "json_schema": {
                "name": "sql_generation", "strict": True, "schema": self.SCHEMA}},
            model="claude-haiku-5-5",
        )
        with patch(
            "shared.llm_manager.claude_provider.anthropic.Anthropic",
            return_value=client,
        ):
            response = ClaudeProvider().complete(request, api_key="test-key")

        payload = create.call_args.kwargs
        assert "tools" not in payload and "tool_choice" not in payload
        assert payload["output_config"] == {
            "effort": "medium",
            "format": {"type": "json_schema", "schema": {
                "type": "object",
                "properties": {
                    "confidence": {"type": "number", "description": "{minimum: 0, maximum: 1}"},
                    "sql": {"type": "string"},
                },
                "additionalProperties": False,
                "required": ["confidence", "sql"],
            }},
        }
        assert response.text == '{"confidence": 0.9, "sql": "SELECT 1"}'

    def test_open_object_schemas_are_stated_in_the_prompt_instead(self):
        client, create = _sdk_client()
        schema = {"type": "object", "additionalProperties": {"type": "string"}}
        request = _request(
            {"type": "json_schema", "json_schema": {
                "name": "filters", "strict": True, "schema": schema}},
            model="claude-haiku-5-5",
        )
        with patch(
            "shared.llm_manager.claude_provider.anthropic.Anthropic",
            return_value=client,
        ):
            ClaudeProvider().complete(request, api_key="test-key")

        payload = create.call_args.kwargs
        assert payload["output_config"] == {"effort": "medium"}
        assert "tools" not in payload
        assert '{"type":"object","additionalProperties":{"type":"string"}}' in payload["system"]

    def test_json_object_on_current_models_is_prompted_without_a_format(self):
        client, create = _sdk_client()
        with patch(
            "shared.llm_manager.claude_provider.anthropic.Anthropic",
            return_value=client,
        ):
            ClaudeProvider().complete(
                _request({"type": "json_object"}, model="claude-haiku-5-5"),
                api_key="test-key",
            )

        payload = create.call_args.kwargs
        # Claude has no JSON mode: the prompt asks for JSON and reasoning is kept.
        assert payload["output_config"] == {"effort": "medium"}
        assert "tools" not in payload and "tool_choice" not in payload
        assert "respond with valid json" in payload["system"].lower()

    def test_no_response_format_sends_no_format(self):
        client, create = _sdk_client()
        with patch(
            "shared.llm_manager.claude_provider.anthropic.Anthropic",
            return_value=client,
        ):
            ClaudeProvider().complete(
                _request(None, model="claude-haiku-5-5"), api_key="test-key"
            )

        payload = create.call_args.kwargs
        assert payload["output_config"] == {"effort": "medium"}
        assert "tools" not in payload

"""ClaudeProvider structured-output request tests."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from shared.llm_manager.base import ProviderRequest
from shared.llm_manager.claude_provider import ClaudeProvider


def _request(response_format):
    return ProviderRequest(
        model="claude-haiku-4-5-20251001",
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

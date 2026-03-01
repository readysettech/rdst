"""
Unit tests for ClaudeProvider json_object response format handling.

Bug rdst-9cq.1: filter.py sends extra={"response_format": {"type": "json_object"}}
but ClaudeProvider only handles "json_schema" type — silently dropping "json_object".
This causes semantic extraction to get free-form text instead of JSON, which breaks
json.loads() and makes the ask pipeline hallucinate table names.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from lib.llm_manager.claude_provider import ClaudeProvider
from lib.llm_manager.base import ProviderRequest


class TestJsonObjectResponseFormat:
    """Tests that json_object response format produces JSON output from Claude."""

    def _make_request_with_json_object(self) -> ProviderRequest:
        """Create a request that mirrors filter.py's semantic extraction call.

        NOTE: At runtime, messages are List[Dict], not List[ProviderMessage].
        See claude_provider.py stream() docstring.
        """
        return ProviderRequest(
            model="claude-haiku-4-5-20251001",
            messages=[
                {"role": "system", "content": "You are a database expert. Return only valid JSON."},
                {"role": "user", "content": 'Analyze this question: "show me all users"'},
            ],
            max_tokens=200,
            temperature=0.0,
            extra={"response_format": {"type": "json_object"}},
        )

    def test_json_object_format_enforces_json_output(self):
        """Bug rdst-9cq.1: json_object response format must enforce JSON output.

        When extra contains {"response_format": {"type": "json_object"}},
        the provider should modify the request so Claude returns valid JSON.
        Currently this is silently dropped and Claude returns free-form text.
        """
        provider = ClaudeProvider()
        request = self._make_request_with_json_object()

        # Mock requests.post to capture what's actually sent to Claude
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": [{"type": "text", "text": '{"suggested_tables": ["users"]}'}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        mock_response.headers = {}

        with patch("lib.llm_manager.claude_provider.requests.post", return_value=mock_response) as mock_post:
            provider.complete(request, api_key="test-key")

            # Inspect the payload sent to Claude
            call_args = mock_post.call_args
            payload = json.loads(call_args.kwargs.get("data") or call_args[1].get("data"))

            # The payload must enforce JSON output in some way.
            # Either: (a) system prompt includes JSON instruction, or
            #         (b) tools/tool_choice are set (like json_schema does), or
            #         (c) some other mechanism.
            #
            # Currently FAILS: json_object is silently stripped and the payload
            # has no JSON enforcement beyond the user's own system message.
            has_json_enforcement = False

            # Check if system prompt was augmented with JSON instruction
            if "system" in payload:
                system_text = payload["system"]
                if "json" in system_text.lower() and "must" in system_text.lower():
                    has_json_enforcement = True
                # Also check for a structured JSON instruction appended by the provider
                if "respond with valid json" in system_text.lower():
                    has_json_enforcement = True
                if "return valid json" in system_text.lower():
                    has_json_enforcement = True

            # Check if tools/tool_choice are set (tool-use JSON enforcement)
            if "tools" in payload and "tool_choice" in payload:
                has_json_enforcement = True

            assert has_json_enforcement, (
                "json_object response format was silently dropped! "
                "The payload sent to Claude has no JSON enforcement. "
                "Payload system: {!r}, tools: {!r}".format(
                    payload.get("system", "<none>"),
                    payload.get("tools", "<none>"),
                )
            )

    def test_json_object_does_not_leak_response_format_to_api(self):
        """response_format is not a valid Claude API parameter and must be stripped."""
        provider = ClaudeProvider()
        request = self._make_request_with_json_object()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": [{"type": "text", "text": '{"suggested_tables": []}'}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        mock_response.headers = {}

        with patch("lib.llm_manager.claude_provider.requests.post", return_value=mock_response) as mock_post:
            provider.complete(request, api_key="test-key")

            payload = json.loads(mock_post.call_args.kwargs.get("data") or mock_post.call_args[1].get("data"))

            # response_format must NOT be passed to Claude API (it's OpenAI-specific)
            assert "response_format" not in payload, (
                "response_format leaked into Claude API payload — this is an OpenAI-specific parameter"
            )

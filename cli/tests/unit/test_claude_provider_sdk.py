"""Anthropic SDK transport tests for ClaudeProvider."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import anthropic
import httpx
import pytest

from shared.llm_manager.base import LLMError, ProviderRequest
from shared.llm_manager.claude_provider import ClaudeProvider


def _request(*, max_tokens=800, extra=None):
    return ProviderRequest(
        model="claude-sonnet-4-6",
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "question"},
        ],
        max_tokens=max_tokens,
        temperature=0.0,
        top_p=0.9,
        stop_sequences=["stop"],
        extra=extra or {},
    )


def _message(*, blocks=None, usage=None):
    return SimpleNamespace(
        content=blocks or [SimpleNamespace(type="text", text="answer")],
        usage=usage
        or SimpleNamespace(
            input_tokens=10,
            output_tokens=4,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
        ),
        to_dict=lambda: {"content": [{"type": "text", "text": "answer"}]},
        _request_id="req_message",
    )


def _completion_client(*, message=None, headers=None):
    raw = SimpleNamespace(
        parse=Mock(return_value=message or _message()),
        request_id="req_raw",
        headers=headers or {},
    )
    create = Mock(return_value=raw)
    client = SimpleNamespace(
        messages=SimpleNamespace(
            with_raw_response=SimpleNamespace(create=create),
        )
    )
    return client, create


def test_supported_anthropic_sdk_version_is_installed():
    assert anthropic.__version__ == "0.122.0"


def test_real_sdk_posts_once_to_messages_endpoint():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"request-id": "req_transport"},
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "answer"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 4},
            },
        )

    transport = httpx.MockTransport(handle)
    real_client = anthropic.Anthropic

    def client_factory(**kwargs):
        return real_client(
            **kwargs,
            http_client=httpx.Client(transport=transport),
        )

    with patch(
        "shared.llm_manager.claude_provider.anthropic.Anthropic",
        side_effect=client_factory,
    ):
        response = ClaudeProvider().complete(_request(), api_key="sk-ant-test")

    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.anthropic.com/v1/messages"
    assert requests[0].headers["x-api-key"] == "sk-ant-test"
    assert json.loads(requests[0].content)["model"] == "claude-sonnet-4-6"
    assert response.raw["_request_id"] == "req_transport"


def test_real_sdk_joins_trial_origin_to_messages_endpoint():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"request-id": "req_trial"},
            json={
                "id": "msg_trial",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "answer"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 4},
            },
        )

    transport = httpx.MockTransport(handle)
    real_client = anthropic.Anthropic

    def client_factory(**kwargs):
        return real_client(
            **kwargs,
            http_client=httpx.Client(transport=transport),
        )

    with patch(
        "shared.llm_manager.claude_provider.anthropic.Anthropic",
        side_effect=client_factory,
    ):
        ClaudeProvider().complete(
            _request(),
            api_key="trial-token",
            base_url="https://trial.example",
            extra_headers={"X-RDST-Signature": "123.sig"},
        )

    assert len(requests) == 1
    assert str(requests[0].url) == "https://trial.example/v1/messages"
    assert requests[0].headers["x-api-key"] == "trial-token"
    assert requests[0].headers["x-rdst-signature"] == "123.sig"


def test_direct_client_is_pinned_to_origin_timeout_and_zero_retries():
    client, create = _completion_client()
    with patch(
        "shared.llm_manager.claude_provider.anthropic.Anthropic",
        return_value=client,
    ) as anthropic_client:
        response = ClaudeProvider().complete(_request(), api_key="sk-ant-test")

    constructor = anthropic_client.call_args.kwargs
    assert constructor["api_key"] == "sk-ant-test"
    assert constructor["base_url"] == "https://api.anthropic.com"
    assert constructor["timeout"] == 60
    assert constructor["max_retries"] == 0
    assert constructor["default_headers"]["anthropic-version"] == "2023-06-01"
    assert response.text == "answer"
    assert response.raw["_request_id"] == "req_raw"

    payload = create.call_args.kwargs
    assert payload["model"] == "claude-sonnet-4-6"
    assert payload["system"] == "system"
    assert payload["messages"] == [{"role": "user", "content": "question"}]
    assert payload["temperature"] == 0.0
    assert payload["top_p"] == 0.9
    assert payload["stop_sequences"] == ["stop"]


def test_malformed_success_response_is_normalized_as_parse_error():
    client, _ = _completion_client()
    client.messages.with_raw_response.create.return_value.parse.side_effect = (
        ValueError("malformed body")
    )

    with (
        patch(
            "shared.llm_manager.claude_provider.anthropic.Anthropic",
            return_value=client,
        ),
        pytest.raises(LLMError) as raised,
    ):
        ClaudeProvider().complete(_request(), api_key="sk-ant-test")

    assert raised.value.code == "PARSE_ERROR"
    assert "malformed body" in str(raised.value)


def test_trial_client_uses_origin_attestation_and_balance_headers():
    client, _ = _completion_client(
        headers={
            "X-RDST-Trial-Remaining-Cents": "123",
            "X-RDST-Trial-Limit-Cents": "500",
        }
    )
    with patch(
        "shared.llm_manager.claude_provider.anthropic.Anthropic",
        return_value=client,
    ) as anthropic_client:
        response = ClaudeProvider().complete(
            _request(),
            api_key="trial-token",
            base_url="https://trial.example",
            extra_headers={"X-RDST-Signature": "123.sig"},
        )

    constructor = anthropic_client.call_args.kwargs
    assert constructor["base_url"] == "https://trial.example"
    assert constructor["default_headers"]["X-RDST-Signature"] == "123.sig"
    assert response.raw["_trial_remaining_cents"] == 123
    assert response.raw["_trial_limit_cents"] == 500


def test_large_completion_uses_extended_timeout():
    client, _ = _completion_client()
    with patch(
        "shared.llm_manager.claude_provider.anthropic.Anthropic",
        return_value=client,
    ) as anthropic_client:
        ClaudeProvider().complete(_request(max_tokens=4097), api_key="sk-ant-test")

    assert anthropic_client.call_args.kwargs["timeout"] == 256


def test_tool_output_and_cache_usage_are_preserved():
    message = _message(
        blocks=[SimpleNamespace(type="tool_use", input={"tables": ["users"]})],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=4,
            cache_creation_input_tokens=3,
            cache_read_input_tokens=7,
        ),
    )
    client, _ = _completion_client(message=message)
    with patch(
        "shared.llm_manager.claude_provider.anthropic.Anthropic",
        return_value=client,
    ):
        response = ClaudeProvider().complete(_request(), api_key="sk-ant-test")

    assert response.text == '{"tables": ["users"]}'
    assert response.usage == {
        "prompt_tokens": 20,
        "completion_tokens": 4,
        "total_tokens": 24,
        "cache_creation_input_tokens": 3,
        "cache_read_input_tokens": 7,
    }


def test_provider_extra_uses_sdk_extra_body():
    client, create = _completion_client()
    with patch(
        "shared.llm_manager.claude_provider.anthropic.Anthropic",
        return_value=client,
    ):
        ClaudeProvider().complete(
            _request(extra={"metadata": {"user_id": "test"}}),
            api_key="sk-ant-test",
        )

    assert create.call_args.kwargs["extra_body"] == {"metadata": {"user_id": "test"}}


def test_stream_uses_sdk_text_stream_and_zero_retries():
    stream = SimpleNamespace(text_stream=iter(["one", " two"]))
    manager = Mock()
    manager.__enter__ = Mock(return_value=stream)
    manager.__exit__ = Mock(return_value=False)
    stream_call = Mock(return_value=manager)
    client = SimpleNamespace(messages=SimpleNamespace(stream=stream_call))

    with patch(
        "shared.llm_manager.claude_provider.anthropic.Anthropic",
        return_value=client,
    ) as anthropic_client:
        tokens = list(ClaudeProvider().stream(_request(), api_key="sk-ant-test"))

    assert tokens == ["one", " two"]
    constructor = anthropic_client.call_args.kwargs
    assert constructor["timeout"] == 120
    assert constructor["max_retries"] == 0
    assert stream_call.call_args.kwargs["model"] == "claude-sonnet-4-6"

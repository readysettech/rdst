from unittest.mock import MagicMock, patch

import pytest

from shared.llm_manager.base import LLMError, ProviderRequest
from shared.llm_manager.claude_provider import ClaudeProvider


def _request() -> ProviderRequest:
    return ProviderRequest(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=20,
    )


def test_trial_proxy_401_is_not_labeled_as_claude_error():
    response = MagicMock()
    response.status_code = 401
    response.json.return_value = {
        "code": "UNAUTHORIZED",
        "detail": "Invalid trial token",
    }

    with patch(
        "shared.llm_manager.claude_provider.requests.post",
        return_value=response,
    ):
        with pytest.raises(LLMError) as raised:
            ClaudeProvider().complete(
                _request(),
                api_key="trial-token",
                base_url="https://trial.example/v1/messages",
            )

    assert raised.value.code == "TRIAL_AUTH_INVALID"
    assert raised.value.status == 401
    assert "trial access" in str(raised.value)
    assert "Claude error" not in str(raised.value)


def test_direct_anthropic_401_identifies_api_key_problem():
    response = MagicMock()
    response.status_code = 401
    response.json.return_value = {"error": {"message": "invalid x-api-key"}}

    with patch(
        "shared.llm_manager.claude_provider.requests.post",
        return_value=response,
    ):
        with pytest.raises(LLMError) as raised:
            ClaudeProvider().complete(_request(), api_key="bad-key")

    assert raised.value.code == "ANTHROPIC_AUTH_INVALID"
    assert "rejected the configured API key" in str(raised.value)


def test_streaming_400_preserves_provider_detail_status_and_request_id():
    response = MagicMock()
    response.status_code = 400
    response.headers = {"request-id": "req_abc123"}
    response.json.return_value = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "model: retired-model is not available",
        },
        "request_id": "req_abc123",
    }

    with patch(
        "shared.llm_manager.claude_provider.requests.post",
        return_value=response,
    ):
        with pytest.raises(LLMError) as raised:
            list(ClaudeProvider().stream(_request(), api_key="test-key"))

    error = raised.value
    assert error.code == "ANTHROPIC_INVALID_REQUEST"
    assert error.status == 400
    assert error.request_id == "req_abc123"
    assert "retired-model is not available" in str(error)
    assert "claude-sonnet-4-6" in str(error)
    assert "request ID: req_abc123" in str(error)


def test_streaming_proxy_error_uses_keyservice_detail():
    response = MagicMock()
    response.status_code = 429
    response.headers = {}
    response.json.return_value = {
        "code": "IP_RATE_LIMIT",
        "detail": "Too many trial accounts used from this IP address.",
    }

    with patch(
        "shared.llm_manager.claude_provider.requests.post",
        return_value=response,
    ):
        with pytest.raises(LLMError) as raised:
            list(
                ClaudeProvider().stream(
                    _request(),
                    api_key="trial-token",
                    base_url="https://trial.example/v1/messages",
                )
            )

    assert raised.value.code == "IP_RATE_LIMIT"
    assert raised.value.status == 429
    assert "Too many trial accounts" in str(raised.value)


def test_streaming_sse_error_is_not_silently_ignored():
    response = MagicMock()
    response.status_code = 200
    response.headers = {}
    response.iter_lines.return_value = [
        b'data: {"type":"error","error":{"type":"overloaded_error",'
        b'"message":"Temporarily overloaded"},"request_id":"req_stream"}'
    ]

    with patch(
        "shared.llm_manager.claude_provider.requests.post",
        return_value=response,
    ):
        with pytest.raises(LLMError) as raised:
            list(ClaudeProvider().stream(_request(), api_key="test-key"))

    assert raised.value.code == "ANTHROPIC_OVERLOADED_ERROR"
    assert raised.value.request_id == "req_stream"
    assert "Temporarily overloaded" in str(raised.value)

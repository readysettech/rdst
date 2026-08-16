"""ClaudeProvider Anthropic SDK error mapping tests."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import anthropic
import httpx
import pytest

from shared.llm_manager.base import LLMError, ProviderRequest
from shared.llm_manager.claude_provider import ClaudeProvider


def _request():
    return ProviderRequest(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=100,
    )


def _status_error(status, body, request_id="req_test"):
    request = httpx.Request("POST", "https://example.test/v1/messages")
    response = httpx.Response(
        status,
        request=request,
        headers={"request-id": request_id},
        json=body,
    )
    return anthropic.APIStatusError("provider error", response=response, body=body)


def _completion_client(error):
    create = Mock(side_effect=error)
    return SimpleNamespace(
        messages=SimpleNamespace(
            with_raw_response=SimpleNamespace(create=create),
        )
    )


def _stream_client(error):
    manager = Mock()
    manager.__enter__ = Mock(side_effect=error)
    manager.__exit__ = Mock(return_value=False)
    return SimpleNamespace(messages=SimpleNamespace(stream=Mock(return_value=manager)))


def _patch_client(client):
    return patch(
        "shared.llm_manager.claude_provider.anthropic.Anthropic",
        return_value=client,
    )


def test_proxy_401_preserves_trial_specific_error():
    error = _status_error(
        401,
        {"code": "INVALID_TRIAL_TOKEN", "detail": "Invalid trial token"},
    )
    with _patch_client(_completion_client(error)), pytest.raises(LLMError) as raised:
        ClaudeProvider().complete(
            _request(),
            api_key="trial-token",
            base_url="https://trial.example",
        )

    assert raised.value.code == "TRIAL_AUTH_INVALID"
    assert raised.value.status == 401
    assert raised.value.request_id == "req_test"


def test_direct_401_preserves_anthropic_error():
    error = _status_error(401, {"error": {"message": "invalid x-api-key"}})
    with _patch_client(_completion_client(error)), pytest.raises(LLMError) as raised:
        ClaudeProvider().complete(_request(), api_key="bad-key")

    assert raised.value.code == "ANTHROPIC_AUTH_INVALID"
    assert "rejected the configured API key" in str(raised.value)


def test_streaming_400_preserves_detail_status_and_request_id():
    error = _status_error(
        400,
        {
            "error": {
                "type": "invalid_request_error",
                "message": "model: retired-model is not available",
            },
            "request_id": "req_abc123",
        },
    )
    with _patch_client(_stream_client(error)), pytest.raises(LLMError) as raised:
        list(ClaudeProvider().stream(_request(), api_key="test-key"))

    assert raised.value.code == "ANTHROPIC_INVALID_REQUEST"
    assert raised.value.status == 400
    assert raised.value.request_id == "req_abc123"
    assert "retired-model is not available" in str(raised.value)


def test_proxy_error_preserves_keyservice_code():
    error = _status_error(
        403,
        {
            "code": "IP_RATE_LIMIT",
            "detail": "Too many trial accounts used from this IP address.",
        },
    )
    with _patch_client(_stream_client(error)), pytest.raises(LLMError) as raised:
        list(
            ClaudeProvider().stream(
                _request(),
                api_key="trial-token",
                base_url="https://trial.example",
            )
        )

    assert raised.value.code == "IP_RATE_LIMIT"
    assert raised.value.status == 403
    assert "Too many trial accounts" in str(raised.value)


def test_sse_error_preserves_anthropic_type_and_request_id():
    error = _status_error(
        200,
        {
            "type": "error",
            "error": {
                "type": "overloaded_error",
                "message": "Temporarily overloaded",
            },
            "request_id": "req_stream",
        },
    )
    with _patch_client(_stream_client(error)), pytest.raises(LLMError) as raised:
        list(ClaudeProvider().stream(_request(), api_key="test-key"))

    assert raised.value.code == "ANTHROPIC_OVERLOADED_ERROR"
    assert raised.value.request_id == "req_stream"
    assert "Temporarily overloaded" in str(raised.value)

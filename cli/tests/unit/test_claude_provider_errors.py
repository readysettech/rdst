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

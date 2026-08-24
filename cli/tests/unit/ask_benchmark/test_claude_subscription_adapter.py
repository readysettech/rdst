import json
import subprocess
from dataclasses import replace
from decimal import Decimal

import pytest

from devtools.ask_benchmark.claude_subscription_adapter import (
    CLAUDE_SUBSCRIPTION_EFFORT,
    CLAUDE_SUBSCRIPTION_MODEL,
    CLAUDE_SUBSCRIPTION_TRANSPORT,
    PINNED_CLAUDE_CODE_VERSION,
    ClaudeSubscriptionAdapter,
)
from devtools.ask_benchmark.models import ModelSpec, Pricing
from devtools.ask_benchmark.pydantic_adapter import (
    BenchmarkTransportError,
    RouteMismatchError,
)
from shared.llm_manager.base import LLMError


def _spec(*, name="sonnet-subscription", max_tokens=4000):
    return ModelSpec(
        name=name,
        model=CLAUDE_SUBSCRIPTION_MODEL,
        transport=CLAUDE_SUBSCRIPTION_TRANSPORT,
        provider_order=(),
        pricing=Pricing(
            Decimal("0.000003"),
            Decimal("0.000015"),
            Decimal("0.0000003"),
        ),
        temperature=0.0,
        max_tokens=max_tokens,
        timeout_seconds=60,
        reasoning_effort=CLAUDE_SUBSCRIPTION_EFFORT,
        provider_data_training=False,
        provider_retains_prompts=True,
    )


def _payload(*, models=None, result='{"answer":"OK"}', thinking_tokens=7):
    model_usage = models or {
        CLAUDE_SUBSCRIPTION_MODEL: {
            "inputTokens": 100,
            "outputTokens": 20,
            "cacheReadInputTokens": 10,
            "canonicalModel": CLAUDE_SUBSCRIPTION_MODEL,
            "provider": "firstParty",
            "costUSD": 0.0006,
        }
    }
    return {
        "is_error": False,
        "num_turns": 1,
        "subtype": "success",
        "session_id": "session-1",
        "uuid": "result-1",
        "result": result,
        "modelUsage": model_usage,
        "service_tier": "standard",
        "stop_reason": "end_turn",
        "terminal_reason": "completed",
        "total_cost_usd": 0.0006,
        "usage": {"output_tokens_details": {"thinking_tokens": thinking_tokens}},
    }


def _adapter(tmp_path, runner, **kwargs):
    return ClaudeSubscriptionAdapter(
        _spec(),
        oauth_token="subscription-token",
        claude_path="/usr/bin/claude",
        command_runner=runner,
        cli_version=PINNED_CLAUDE_CODE_VERSION,
        working_directory=tmp_path,
        **kwargs,
    )


def test_subscription_query_is_one_pinned_sonnet_turn(tmp_path, monkeypatch):
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps(_payload()), "")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING", "1")
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_THINKING", "1")
    monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "max")
    monkeypatch.setenv("MAX_THINKING_TOKENS", "32000")
    adapter = _adapter(tmp_path, runner)
    response = adapter.query(
        system_message="Return structured output.",
        user_query="Answer the question.",
        context="schema context",
        max_tokens=512,
        temperature=0.0,
        purpose="generation",
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "schema": {
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                    }
                },
            }
        },
    )

    assert response["text"] == '{"answer":"OK"}'
    assert response["usage"]["total_tokens"] == 120
    command, kwargs = calls[0]
    assert command[command.index("--model") + 1] == CLAUDE_SUBSCRIPTION_MODEL
    assert command[command.index("--max-turns") + 1] == "1"
    assert command[command.index("--effort") + 1] == "medium"
    assert "--safe-mode" in command
    assert command[command.index("--tools") + 1] == ""
    system_prompt = command[command.index("--system-prompt") + 1]
    assert '"required":["answer"]' in system_prompt
    assert kwargs["input"].startswith("[CONTEXT]\nschema context")
    assert "ANTHROPIC_API_KEY" not in kwargs["env"]
    assert kwargs["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "subscription-token"
    assert kwargs["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING" not in kwargs["env"]
    assert "CLAUDE_CODE_DISABLE_THINKING" not in kwargs["env"]
    assert kwargs["env"]["CLAUDE_CODE_EFFORT_LEVEL"] == "medium"
    assert "MAX_THINKING_TOKENS" not in kwargs["env"]
    assert kwargs["env"]["CLAUDE_CODE_MAX_RETRIES"] == "0"
    assert kwargs["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "512"

    [receipt] = adapter.drain_call_records()
    assert receipt.transport == CLAUDE_SUBSCRIPTION_TRANSPORT
    assert receipt.returned_model == CLAUDE_SUBSCRIPTION_MODEL
    assert receipt.request_id == "claude-code-session:session-1"
    assert receipt.input_tokens == 100
    assert receipt.output_tokens == 20
    assert receipt.reasoning_tokens == 7
    assert receipt.cached_input_tokens == 10
    assert receipt.actual_cost_usd is None
    assert receipt.expected_billed_cost_usd == 0
    assert receipt.response_sha256 is not None
    assert receipt.effective_settings["billing"] == "claude_subscription_credit"
    assert receipt.effective_settings["reasoning_effort"] == "medium"
    assert receipt.effective_settings["extended_thinking_enabled"] is True


def test_subscription_route_rejects_auxiliary_model_calls(tmp_path):
    models = _payload()["modelUsage"] | {
        "claude-haiku-4-5-20251001": {
            "inputTokens": 1,
            "outputTokens": 1,
            "cacheReadInputTokens": 0,
            "canonicalModel": "claude-haiku-4-5",
            "provider": "firstParty",
        }
    }

    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(
            command, 0, json.dumps(_payload(models=models)), ""
        )

    adapter = _adapter(tmp_path, runner)

    with pytest.raises(RouteMismatchError, match="expected only"):
        adapter.query(system_message="system", user_query="question")

    [receipt] = adapter.drain_call_records()
    assert receipt.error_kind == "route_mismatch"
    assert receipt.route_error is not None


def test_subscription_alias_still_routes_filter_to_sonnet(tmp_path):
    observed = []

    def runner(command, **_kwargs):
        observed.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(_payload()), "")

    filter_spec = _spec(name="subscription-filter", max_tokens=300)
    adapter = _adapter(
        tmp_path,
        runner,
        model_aliases={"claude-haiku-4-5-20251001": filter_spec},
    )

    adapter.query(
        system_message="system",
        user_query="question",
        model="claude-haiku-4-5-20251001",
    )

    command = observed[0]
    assert command[command.index("--model") + 1] == CLAUDE_SUBSCRIPTION_MODEL
    [receipt] = adapter.drain_call_records()
    assert receipt.model_name == "subscription-filter"


def test_subscription_nonzero_exit_is_transport_error(tmp_path):
    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, "", "subscription limit reached")

    adapter = _adapter(tmp_path, runner)

    with pytest.raises(BenchmarkTransportError, match="subscription limit reached"):
        adapter.query(system_message="system", user_query="question")

    [receipt] = adapter.drain_call_records()
    assert receipt.error_kind == "transport"


def test_subscription_nonzero_context_overflow_is_retryable(tmp_path):
    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            "",
            "prompt is too long: 1000001 tokens > 1000000 maximum",
        )

    adapter = _adapter(tmp_path, runner)

    with pytest.raises(LLMError) as raised:
        adapter.query(system_message="system", user_query="question")

    assert raised.value.code == "ANTHROPIC_CONTEXT_WINDOW_EXCEEDED"
    [receipt] = adapter.drain_call_records()
    assert receipt.error_kind == "anthropic_context_window_exceeded"


def test_subscription_payload_context_overflow_preserves_session_id(tmp_path):
    error_payload = {
        "is_error": True,
        "subtype": "error_during_execution",
        "result": "The model context window has been exceeded.",
        "session_id": "session-overflow",
    }

    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(error_payload), "")

    adapter = _adapter(tmp_path, runner)

    with pytest.raises(LLMError) as raised:
        adapter.query(system_message="system", user_query="question")

    assert raised.value.code == "ANTHROPIC_CONTEXT_WINDOW_EXCEEDED"
    assert raised.value.request_id == "claude-code-session:session-overflow"
    [receipt] = adapter.drain_call_records()
    assert receipt.request_id == "claude-code-session:session-overflow"


def test_subscription_provider_requires_pinned_cli_version(tmp_path):
    with pytest.raises(ValueError, match="version mismatch"):
        ClaudeSubscriptionAdapter(
            _spec(),
            oauth_token="subscription-token",
            claude_path="/usr/bin/claude",
            command_runner=lambda *_args, **_kwargs: None,
            cli_version="2.1.999",
            working_directory=tmp_path,
        )


def test_subscription_provider_requires_medium_effort(tmp_path):
    with pytest.raises(ValueError, match="explicit medium effort"):
        ClaudeSubscriptionAdapter(
            replace(_spec(), reasoning_effort="max"),
            oauth_token="subscription-token",
            claude_path="/usr/bin/claude",
            command_runner=lambda *_args, **_kwargs: None,
            cli_version=PINNED_CLAUDE_CODE_VERSION,
            working_directory=tmp_path,
        )

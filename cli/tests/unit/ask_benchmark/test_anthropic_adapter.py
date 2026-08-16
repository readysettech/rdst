from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from devtools.ask_benchmark.anthropic_adapter import AnthropicSDKAdapter
from devtools.ask_benchmark.models import ModelSpec, Pricing
from devtools.ask_benchmark.pydantic_adapter import (
    BenchmarkTransportError,
    RouteMismatchError,
)
from shared.llm_manager.base import LLMError


def _spec():
    return ModelSpec(
        name="sonnet-direct",
        model="claude-sonnet-4-6",
        transport="anthropic",
        provider_order=(),
        pricing=Pricing(
            input_usd_per_token=Decimal("0.000003"),
            output_usd_per_token=Decimal("0.000015"),
            cached_input_usd_per_token=Decimal("0.0000003"),
        ),
        temperature=0.0,
        max_tokens=4000,
        provider_data_training=False,
        provider_retains_prompts=True,
    )


def _response(model="claude-sonnet-4-6"):
    return {
        "text": '{"sql": "SELECT 1"}',
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cache_read_input_tokens": 10,
        },
        "provider": "claude",
        "model": model,
        "raw": {"model": model, "_request_id": "req_direct"},
    }


def test_direct_adapter_captures_durable_receipt():
    manager = Mock()
    manager.query.return_value = _response()
    receipts = []
    adapter = AnthropicSDKAdapter(
        _spec(),
        manager=manager,
        api_key="sk-ant-test",
        call_receipt_sink=receipts.append,
    )
    adapter.set_attempt_id("attempt-1")

    result = adapter.query(
        system_message="system",
        user_query="question",
        max_tokens=4000,
        temperature=0.0,
        purpose="sql_generation",
        extra={"response_format": {"type": "json_object"}},
    )

    assert result["text"] == '{"sql": "SELECT 1"}'
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.transport == "anthropic"
    assert receipt.request_id == "req_direct"
    assert receipt.returned_model == "claude-sonnet-4-6"
    assert len(receipt.response_sha256 or "") == 64
    assert receipt.attempt_id == "attempt-1"
    assert receipt.transport_attempts == 1
    assert receipt.effective_settings["timeout_seconds"] == 60
    assert receipt.normalized_cold_cost_usd == Decimal("0.0006")
    assert receipt.actual_cost_usd is None
    assert manager.query.call_args.kwargs["debug"] is True
    assert manager.query.call_args.kwargs["api_key"] == "sk-ant-test"


def test_direct_adapter_constructs_current_llm_manager_interface():
    manager = Mock()
    with patch(
        "devtools.ask_benchmark.anthropic_adapter.LLMManager",
        return_value=manager,
    ) as manager_type:
        adapter = AnthropicSDKAdapter(_spec(), api_key="sk-ant-test")

    assert adapter.manager is manager
    manager_type.assert_called_once_with(
        defaults={"provider": "claude", "model": "claude-sonnet-4-6"}
    )


def test_direct_adapter_maps_filter_alias_to_anthropic():
    manager = Mock()
    manager.query.return_value = _response("claude-haiku-4-5-20251001")
    filter_spec = ModelSpec(
        name="haiku-filter",
        model="anthropic/claude-haiku-4.5",
        transport="openrouter",
        provider_order=("anthropic",),
        pricing=Pricing(Decimal("0.000001"), Decimal("0.000005")),
    )
    adapter = AnthropicSDKAdapter(
        _spec(),
        model_aliases={"claude-haiku-4-5-20251001": filter_spec},
        manager=manager,
        api_key="sk-ant-test",
    )

    adapter.query(
        system_message="system",
        user_query="question",
        model="claude-haiku-4-5-20251001",
        purpose="schema_filter",
    )

    assert manager.query.call_args.kwargs["model"] == "claude-haiku-4-5-20251001"
    assert adapter.call_records[0].requested_model == "claude-haiku-4-5-20251001"
    assert adapter.call_records[0].model_name == "haiku-filter"
    assert adapter.call_records[0].benchmark_model_name == "sonnet-direct"
    assert adapter.call_records[0].normalized_cold_cost_usd == Decimal("0.0002")


def test_direct_adapter_fails_closed_on_returned_model_change():
    manager = Mock()
    manager.query.return_value = _response("claude-opus-4-6")
    adapter = AnthropicSDKAdapter(_spec(), manager=manager, api_key="sk-ant-test")

    with pytest.raises(RouteMismatchError, match="claude-opus"):
        adapter.query(system_message="system", user_query="question")

    assert adapter.call_records[0].route_error is not None


def test_direct_adapter_persists_transport_failure_before_raising():
    manager = Mock()
    manager.query.side_effect = LLMError(
        "rate limited",
        code="ANTHROPIC_RATE_LIMIT",
        status=429,
        request_id="req_failed",
    )
    receipts = []
    adapter = AnthropicSDKAdapter(
        _spec(),
        manager=manager,
        api_key="sk-ant-test",
        call_receipt_sink=receipts.append,
    )

    with pytest.raises(BenchmarkTransportError, match="rate limited"):
        adapter.query(system_message="system", user_query="question")

    assert len(receipts) == 1
    assert receipts[0].error == "rate limited"
    assert receipts[0].request_id == "req_failed"
    assert receipts[0].transport_attempts == 1

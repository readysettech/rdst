import asyncio
import json
from dataclasses import replace
from decimal import Decimal

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from devtools.ask_benchmark.models import ModelSpec, Pricing
from devtools.ask_benchmark.pydantic_adapter import (
    BenchmarkModelOutputError,
    BenchmarkTransportError,
    PydanticAIAdapter,
    RouteMismatchError,
    _model_error_kind,
)


def _spec(transport: str = "openrouter"):
    return ModelSpec(
        name="test",
        model="vendor/test-model",
        transport=transport,
        provider_order=("expected-provider",),
        pricing=Pricing(
            input_usd_per_token=Decimal("0.01"),
            output_usd_per_token=Decimal("0.02"),
        ),
        temperature=0.0,
        max_tokens=100,
    )


def test_text_response_and_usage_are_normalized():
    def respond(_messages, info):
        assert info.model_settings["temperature"] == 0.0
        assert info.model_settings["max_tokens"] == 50
        return ModelResponse(
            parts=[TextPart("SELECT 1")],
            usage=RequestUsage(
                input_tokens=20,
                output_tokens=4,
                cache_read_tokens=8,
                details={"reasoning_tokens": 2},
            ),
            model_name="returned-model",
            provider_name="openrouter",
            provider_response_id="generation-1",
            provider_details={
                "downstream_provider": "Expected Provider",
                "cost": "0.0011",
            },
        )

    adapter = PydanticAIAdapter(
        _spec(),
        model_factory=lambda _spec: FunctionModel(respond),
        verify_route=False,
    )
    with override_allow_model_requests(False):
        result = adapter.query(
            system_message="Return SQL",
            user_query="one",
            max_tokens=50,
        )

    assert result["text"] == "SELECT 1"
    assert result["usage"] == {
        "prompt_tokens": 20,
        "completion_tokens": 4,
        "total_tokens": 24,
        "cache_read_tokens": 8,
    }
    record = adapter.call_records[0]
    assert record.request_id == "generation-1"
    assert record.actual_cost_usd == Decimal("0.0011")
    assert record.normalized_cold_cost_usd == Decimal("0.28")
    assert record.reasoning_tokens == 2
    assert record.expected_billed_cost_usd == Decimal("0.20")
    assert record.effective_settings["model_settings"]["max_tokens"] == 50
    assert len(record.configuration_fingerprint) == 64


def test_omitted_completion_limit_is_not_sent():
    def respond(_messages, info):
        assert "max_tokens" not in info.model_settings
        assert "temperature" not in info.model_settings
        return ModelResponse(
            parts=[TextPart("SELECT 1")],
            model_name="vendor/test-model",
            provider_details={"downstream_provider": "Expected Provider"},
        )

    adapter = PydanticAIAdapter(
        replace(_spec(), max_tokens=None, temperature=None),
        model_factory=lambda _spec: FunctionModel(
            respond, model_name="vendor/test-model"
        ),
    )
    with override_allow_model_requests(False):
        adapter.query(system_message="Return SQL", user_query="one", temperature=0.25)

    record = adapter.call_records[0]
    assert record.effective_settings["model_settings"].get("max_tokens") is None
    assert record.effective_settings["omitted_requested_settings"]["temperature"] == {
        "value": 0.25,
        "reason": "unsupported_by_pinned_route",
    }


def test_direct_anthropic_records_required_default_completion_limit():
    def respond(_messages, info):
        assert info.model_settings["max_tokens"] == 4096
        return ModelResponse(parts=[TextPart("SELECT 1")])

    spec = replace(
        _spec("anthropic"),
        model="claude-test",
        provider_order=(),
        max_tokens=None,
    )
    adapter = PydanticAIAdapter(
        spec,
        model_factory=lambda _spec: FunctionModel(respond),
        verify_route=False,
    )
    with override_allow_model_requests(False):
        adapter.query(system_message="Return SQL", user_query="one")

    assert (
        adapter.call_records[0].effective_settings["model_settings"]["max_tokens"]
        == 4096
    )


def test_json_mode_serializes_structured_output_for_ask3():
    def respond(_messages, info):
        output_tool = info.output_tools[0]
        payload = {"sql": "SELECT 1", "confidence": 1.0}
        if output_tool.outer_typed_dict_key:
            payload = {output_tool.outer_typed_dict_key: payload}
        return ModelResponse(parts=[ToolCallPart(output_tool.name, payload)])

    adapter = PydanticAIAdapter(
        _spec("mock"),
        model_factory=lambda _spec: FunctionModel(respond),
        verify_route=False,
    )
    with override_allow_model_requests(False):
        result = adapter.generate_response(
            "generate",
            purpose="sql_generation",
            extra={"response_format": {"type": "json_object"}},
        )

    assert json.loads(result["response"]) == {
        "sql": "SELECT 1",
        "confidence": 1.0,
    }
    assert adapter.call_records[0].stage == "sql_generation"
    assert adapter.call_records[0].effective_settings["structured_output"] is True


def test_prompted_json_schema_parses_text_without_model_profile_support():
    def respond(_messages, info):
        assert info.output_tools == []
        return ModelResponse(parts=[TextPart('{"sql":"SELECT 1"}')])

    adapter = PydanticAIAdapter(
        replace(_spec("mock"), structured_output_mode="prompted"),
        model_factory=lambda _spec: FunctionModel(respond),
        verify_route=False,
    )
    with override_allow_model_requests(False):
        result = adapter.generate_response(
            "generate",
            purpose="sql_generation",
            extra={
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "sql_generation",
                        "strict": False,
                        "schema": {
                            "type": "object",
                            "properties": {"sql": {"type": "string"}},
                            "required": ["sql"],
                        },
                    },
                }
            },
        )

    assert json.loads(result["response"]) == {"sql": "SELECT 1"}
    assert (
        adapter.call_records[0].effective_settings["structured_output_mode"]
        == "prompted"
    )


def test_json_schema_is_forwarded_to_the_output_tool():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "sql": {"type": "string"},
            "cannot_answer_reason": {
                "type": "string",
                "enum": ["", "missing_schema"],
            },
        },
        "required": ["sql", "cannot_answer_reason"],
    }

    def respond(_messages, info):
        output_tool = info.output_tools[0]
        assert output_tool.name == "sql_generation"
        assert output_tool.strict is True
        assert output_tool.parameters_json_schema["additionalProperties"] is False
        assert output_tool.parameters_json_schema["required"] == [
            "sql",
            "cannot_answer_reason",
        ]
        assert output_tool.parameters_json_schema["properties"]["cannot_answer_reason"][
            "enum"
        ] == ["", "missing_schema"]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {"sql": "SELECT 1", "cannot_answer_reason": ""},
                )
            ]
        )

    adapter = PydanticAIAdapter(
        _spec("mock"),
        model_factory=lambda _spec: FunctionModel(respond),
        verify_route=False,
    )
    with override_allow_model_requests(False):
        result = adapter.generate_response(
            "generate",
            purpose="sql_generation",
            extra={
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "sql_generation",
                        "strict": True,
                        "schema": schema,
                    },
                }
            },
        )

    assert json.loads(result["response"]) == {
        "sql": "SELECT 1",
        "cannot_answer_reason": "",
    }


def test_structured_output_failure_retains_billable_usage():
    receipts = []

    def respond(_messages, _info):
        return ModelResponse(
            parts=[TextPart("not structured")],
            usage=RequestUsage(input_tokens=10, output_tokens=2),
        )

    adapter = PydanticAIAdapter(
        _spec("mock"),
        model_factory=lambda _spec: FunctionModel(respond),
        verify_route=False,
        call_receipt_sink=receipts.append,
    )
    with override_allow_model_requests(False), pytest.raises(BenchmarkModelOutputError):
        adapter.generate_response(
            "generate",
            extra={"response_format": {"type": "json_object"}},
        )

    record = adapter.call_records[0]
    assert record.input_tokens == 10
    assert record.output_tokens == 2
    assert record.normalized_cold_cost_usd == Decimal("0.14")
    assert receipts == [record]


def test_hard_request_timeout_is_repairable():
    async def respond(_messages, _info):
        await asyncio.sleep(0.05)
        return ModelResponse(parts=[TextPart("SELECT 1")])

    adapter = PydanticAIAdapter(
        replace(_spec("mock"), timeout_seconds=0.01),
        model_factory=lambda _spec: FunctionModel(respond),
        verify_route=False,
    )
    with override_allow_model_requests(False), pytest.raises(BenchmarkTransportError):
        adapter.query(system_message="Return SQL", user_query="one")

    assert adapter.call_records[0].error_kind == "timeout"


def test_non_retryable_http_errors_are_route_policy_failures():
    assert _model_error_kind(asyncio.TimeoutError()) == "timeout"
    assert (
        _model_error_kind(ModelHTTPError(404, "test", {"message": "no endpoint"}))
        == "route_policy"
    )
    assert _model_error_kind(ModelHTTPError(429, "test", {})) == "rate_limit"
    assert _model_error_kind(ModelHTTPError(503, "test", {})) == "provider_5xx"


def test_unexpected_openrouter_provider_fails_closed():
    def respond(_messages, _info):
        return ModelResponse(
            parts=[TextPart("SELECT 1")],
            model_name="vendor/test-model",
            provider_details={"downstream_provider": "other-provider"},
        )

    adapter = PydanticAIAdapter(
        _spec(),
        model_factory=lambda _spec: FunctionModel(
            respond, model_name="vendor/test-model"
        ),
    )
    with (
        override_allow_model_requests(False),
        pytest.raises(RouteMismatchError, match="returned provider"),
    ):
        adapter.query(system_message="Return SQL", user_query="one")

    assert adapter.call_records[0].route_error is not None

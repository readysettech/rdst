from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock, patch

from devtools.ask_benchmark.models import ModelSpec, Pricing
from devtools.ask_benchmark.openrouter import (
    _model_catalog,
    check_model_route,
    check_openrouter_route,
)


def _spec(reasoning_effort: str = "max", cached_input_price: str = "0.001"):
    return ModelSpec(
        name="model-max",
        model="vendor/model",
        transport="openrouter",
        provider_order=("provider",),
        pricing=Pricing(
            input_usd_per_token=Decimal("0.01"),
            output_usd_per_token=Decimal("0.02"),
            cached_input_usd_per_token=Decimal(cached_input_price),
        ),
        temperature=None,
        max_tokens=None,
        reasoning_effort=reasoning_effort,
        provider_data_training=False,
        provider_retains_prompts=False,
    )


def _direct_spec():
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
        provider_data_training=False,
        provider_retains_prompts=True,
    )


def test_direct_anthropic_route_uses_pinned_sdk_policy_and_pricing():
    check = check_model_route(
        _direct_spec(),
        structured_output=True,
        runtime_parameters={"max_tokens"},
    )

    assert check.scoreable is True
    assert check.matched_provider == "anthropic"
    assert check.endpoint_model_ids == ("claude-sonnet-4-6",)
    assert check.missing_parameters == ()


def test_direct_anthropic_route_fails_closed_on_pricing_drift():
    spec = _direct_spec()
    drifted = replace(
        spec,
        pricing=Pricing(Decimal("0.000004"), Decimal("0.000015")),
    )

    check = check_model_route(drifted)

    assert check.pricing_changed is True
    assert check.scoreable is False


def _response(payload):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


def test_route_check_validates_reasoning_value_and_metadata():
    endpoint_response = _response(
        {
            "data": {
                "endpoints": [
                    {
                        "name": "Provider | snapshot",
                        "model_id": "vendor/model",
                        "max_completion_tokens": 100_000,
                        "supported_parameters": ["reasoning_effort"],
                        "pricing": {
                            "prompt": "0.01",
                            "completion": "0.02",
                            "input_cache_read": "0.001",
                        },
                    }
                ]
            }
        }
    )
    catalog_response = _response(
        {
            "data": [
                {
                    "id": "vendor/model",
                    "reasoning": {
                        "mandatory": True,
                        "default_effort": "high",
                        "supported_efforts": ["max", "high"],
                    },
                }
            ]
        }
    )
    _model_catalog.cache_clear()
    with patch(
        "devtools.ask_benchmark.openrouter.requests.get",
        side_effect=[endpoint_response, catalog_response],
    ):
        check = check_openrouter_route(_spec())

    assert check.scoreable is True
    assert check.supported_reasoning_efforts == ("max", "high")
    assert check.default_reasoning_effort == "high"
    assert check.reasoning_mandatory is True
    assert check.max_completion_tokens == 100_000
    assert check.provider_data_training is False


def test_route_check_rejects_unsupported_reasoning_value():
    endpoint_response = _response(
        {
            "data": {
                "endpoints": [
                    {
                        "name": "Provider | snapshot",
                        "model_id": "vendor/model",
                        "supported_parameters": ["reasoning_effort"],
                        "pricing": {
                            "prompt": "0.01",
                            "completion": "0.02",
                            "input_cache_read": "0.001",
                        },
                    }
                ]
            }
        }
    )
    catalog_response = _response(
        {
            "data": [
                {
                    "id": "vendor/model",
                    "reasoning": {"supported_efforts": ["high", "low"]},
                }
            ]
        }
    )
    _model_catalog.cache_clear()
    with patch(
        "devtools.ask_benchmark.openrouter.requests.get",
        side_effect=[endpoint_response, catalog_response],
    ):
        check = check_openrouter_route(_spec("max"))

    assert check.scoreable is False
    assert "reasoning_effort=max" in check.missing_parameters


def test_route_check_treats_missing_cache_price_as_regular_input_price():
    endpoint_response = _response(
        {
            "data": {
                "endpoints": [
                    {
                        "name": "Provider | snapshot",
                        "model_id": "vendor/model",
                        "supported_parameters": ["reasoning_effort"],
                        "pricing": {"prompt": "0.01", "completion": "0.02"},
                    }
                ]
            }
        }
    )
    catalog_response = _response(
        {
            "data": [
                {
                    "id": "vendor/model",
                    "reasoning": {"supported_efforts": ["max"]},
                }
            ]
        }
    )
    _model_catalog.cache_clear()
    with patch(
        "devtools.ask_benchmark.openrouter.requests.get",
        side_effect=[endpoint_response, catalog_response],
    ):
        check = check_openrouter_route(_spec(cached_input_price="0.01"))

    assert check.scoreable is True

from decimal import Decimal
from pathlib import Path

import pytest

from devtools.ask_benchmark.config import (
    ConfigurationError,
    load_filter_spec,
    load_model_specs,
)


def test_default_model_matrix_has_controlled_routes():
    specs = load_model_specs()

    assert len(specs) == 19
    assert "kimi-k3-max" in specs
    assert "grok-4.6-xhigh" in specs
    assert specs["mistral-medium-3.5-high"].model == "mistralai/mistral-medium-3-5"
    assert "gemini-3.1-pro-high" not in specs
    assert "claude-sonnet-4.6-historical-default" in specs
    assert sum(spec.historical for spec in specs.values()) == 1
    assert specs["gpt-5.6-sol-max"].model == "openai/gpt-5.6-sol"
    assert specs["gpt-5.6-terra-max"].model == "openai/gpt-5.6-terra"
    assert all("sol-pro" not in spec.model for spec in specs.values())
    assert all(spec.controlled_route for spec in specs.values())
    assert specs["kimi-k3-max"].pricing.input_usd_per_token == Decimal("0.000003")
    current_specs = [spec for spec in specs.values() if not spec.historical]
    openrouter_specs = [
        spec for spec in current_specs if spec.transport == "openrouter"
    ]
    assert all(spec.max_tokens is None for spec in openrouter_specs)
    direct = specs["claude-sonnet-4.6-anthropic-sdk"]
    assert direct.model == "claude-sonnet-4-6"
    assert direct.transport == "anthropic"
    assert direct.max_tokens == 4000
    assert direct.timeout_seconds == 60
    subscription = specs["claude-sonnet-4.6-subscription-medium"]
    assert subscription.model == "claude-sonnet-4-6"
    assert subscription.transport == "claude-subscription"
    assert subscription.max_tokens == 4000
    assert subscription.reasoning_effort == "medium"
    default_reasoning_specs = {
        "claude-sonnet-4.6-default-no-cap",
        "claude-sonnet-4.6-anthropic-sdk",
    }
    assert all(
        spec.reasoning_effort is not None or spec.name in default_reasoning_specs
        for spec in current_specs
    )
    assert all(spec.provider_data_training is not None for spec in current_specs)
    assert all(spec.provider_retains_prompts is not None for spec in current_specs)
    assert specs["gpt-5.6-sol-max"].reasoning_effort == "max"
    assert {
        specs[f"claude-sonnet-4.6-{effort}"].reasoning_effort
        for effort in ("low", "medium", "high", "max")
    } == {"low", "medium", "high", "max"}

    filter_spec, aliases = load_filter_spec()
    assert filter_spec.model == "anthropic/claude-haiku-4.5"
    assert aliases == ("claude-haiku-4-5-20251001",)


def test_router_alias_is_rejected(tmp_path: Path):
    matrix = tmp_path / "models.toml"
    matrix.write_text(
        """
[[models]]
name = "alias"
model = "openrouter/auto:auto"
transport = "openrouter"
provider_order = ["OpenRouter"]
input_usd_per_token = "0"
output_usd_per_token = "0"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="router alias"):
        load_model_specs(matrix)

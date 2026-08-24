from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Any

import requests

from .models import ModelSpec

CLAUDE_SUBSCRIPTION_TRANSPORT = "claude-subscription"

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
_ANTHROPIC_PRICING = {
    "claude-sonnet-4-6": (
        Decimal("0.000003"),
        Decimal("0.000015"),
        Decimal("0.0000003"),
    ),
    "claude-haiku-4-5-20251001": (
        Decimal("0.000001"),
        Decimal("0.000005"),
        Decimal("0.0000001"),
    ),
}

_PROVIDER_SLUGS = {
    "Anthropic": "anthropic",
    "Google AI Studio": "google-ai-studio",
    "Moonshot AI": "moonshotai",
    "OpenAI": "openai",
    "xAI": "xai",
    "Z.AI": "z-ai",
}


@dataclass(frozen=True)
class RouteCheck:
    model: str
    available: bool
    controlled: bool
    matched_provider: str | None
    endpoint_model_ids: tuple[str, ...]
    supported_parameters: tuple[str, ...]
    missing_parameters: tuple[str, ...]
    pricing_changed: bool
    supported_reasoning_efforts: tuple[str, ...] = ()
    default_reasoning_effort: str | None = None
    reasoning_mandatory: bool | None = None
    max_completion_tokens: int | None = None
    provider_data_training: bool | None = None
    provider_retains_prompts: bool | None = None
    eligible_endpoints: tuple[dict[str, Any], ...] = ()
    error: str | None = None

    @property
    def scoreable(self) -> bool:
        return (
            self.available
            and self.controlled
            and not self.missing_parameters
            and not self.pricing_changed
            and self.error is None
        )


def check_model_route(
    spec: ModelSpec,
    *,
    structured_output: bool = False,
    runtime_parameters: set[str] | None = None,
    timeout_seconds: float = 20.0,
) -> RouteCheck:
    if spec.transport == "openrouter":
        return check_openrouter_route(
            spec,
            structured_output=structured_output,
            runtime_parameters=runtime_parameters,
            timeout_seconds=timeout_seconds,
        )
    if spec.transport == CLAUDE_SUBSCRIPTION_TRANSPORT:
        return _check_claude_subscription_route(
            spec,
            structured_output=structured_output,
            runtime_parameters=runtime_parameters,
        )
    if spec.transport != "anthropic":
        return RouteCheck(
            model=spec.model,
            available=False,
            controlled=False,
            matched_provider=spec.transport,
            endpoint_model_ids=(),
            supported_parameters=(),
            missing_parameters=(),
            pricing_changed=False,
            provider_data_training=spec.provider_data_training,
            provider_retains_prompts=spec.provider_retains_prompts,
            error="Direct-provider route and pricing preflight is not implemented",
        )

    canonical_pricing = _ANTHROPIC_PRICING.get(spec.model)
    available = canonical_pricing is not None
    pricing_changed = canonical_pricing is not None and canonical_pricing != (
        spec.pricing.input_usd_per_token,
        spec.pricing.output_usd_per_token,
        spec.pricing.cached_input_usd_per_token,
    )
    policy_error = None
    if (
        spec.provider_data_training is not False
        or spec.provider_retains_prompts is not True
    ):
        policy_error = (
            "Direct Anthropic data-policy metadata differs from the pinned policy"
        )
    supported = (
        "max_tokens",
        "response_format",
        "stop_sequences",
        "temperature",
        "tool_choice",
        "tools",
        "top_p",
    )
    required = set(runtime_parameters or ())
    if structured_output:
        required.update(("tools", "tool_choice"))
    missing = tuple(sorted(required - set(supported)))
    return RouteCheck(
        model=spec.model,
        available=available,
        controlled=available,
        matched_provider="anthropic",
        endpoint_model_ids=(spec.model,) if available else (),
        supported_parameters=supported,
        missing_parameters=missing,
        pricing_changed=pricing_changed,
        provider_data_training=False,
        provider_retains_prompts=True,
        error=(
            policy_error
            if policy_error
            else None
            if available
            else f"Unsupported direct Anthropic model: {spec.model}"
        ),
    )


def _check_claude_subscription_route(
    spec: ModelSpec,
    *,
    structured_output: bool,
    runtime_parameters: set[str] | None,
) -> RouteCheck:
    canonical_pricing = _ANTHROPIC_PRICING.get(spec.model)
    available = (
        spec.model == "claude-sonnet-4-6"
        and spec.reasoning_effort == "medium"
        and canonical_pricing is not None
    )
    pricing_changed = canonical_pricing is not None and canonical_pricing != (
        spec.pricing.input_usd_per_token,
        spec.pricing.output_usd_per_token,
        spec.pricing.cached_input_usd_per_token,
    )
    supported = {"max_tokens", "reasoning_effort", "response_format"}
    required = set(runtime_parameters or ())
    if spec.reasoning_effort is not None:
        required.add("reasoning_effort")
    if structured_output:
        required.add("response_format")
    policy_error = None
    if (
        spec.provider_data_training is not False
        or spec.provider_retains_prompts is not True
    ):
        policy_error = (
            "Claude subscription data-policy metadata differs from the pinned policy"
        )
    return RouteCheck(
        model=spec.model,
        available=available,
        controlled=available,
        matched_provider="anthropic-claude-code-subscription",
        endpoint_model_ids=(spec.model,) if available else (),
        supported_parameters=tuple(sorted(supported)),
        missing_parameters=tuple(sorted(required - supported)),
        pricing_changed=pricing_changed,
        max_completion_tokens=32000 if available else None,
        provider_data_training=False,
        provider_retains_prompts=True,
        error=(
            policy_error
            if policy_error
            else None
            if available
            else "Claude subscription requires claude-sonnet-4-6 at medium effort"
        ),
    )


def check_openrouter_route(
    spec: ModelSpec,
    *,
    structured_output: bool = False,
    runtime_parameters: set[str] | None = None,
    timeout_seconds: float = 20.0,
) -> RouteCheck:
    if spec.transport != "openrouter":
        return RouteCheck(
            model=spec.model,
            available=False,
            controlled=False,
            matched_provider=spec.transport,
            endpoint_model_ids=(),
            supported_parameters=(),
            missing_parameters=(),
            pricing_changed=False,
            provider_data_training=spec.provider_data_training,
            provider_retains_prompts=spec.provider_retains_prompts,
            error="Direct-provider route and pricing preflight is not implemented",
        )

    url = f"{OPENROUTER_API_BASE}/models/{spec.model}/endpoints"
    try:
        response = requests.get(url, timeout=timeout_seconds)
        response.raise_for_status()
        data = response.json()["data"]
    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
        return RouteCheck(
            model=spec.model,
            available=False,
            controlled=spec.controlled_route,
            matched_provider=None,
            endpoint_model_ids=(),
            supported_parameters=(),
            missing_parameters=(),
            pricing_changed=False,
            provider_data_training=spec.provider_data_training,
            provider_retains_prompts=spec.provider_retains_prompts,
            error=str(exc),
        )

    endpoints = data.get("endpoints", [])
    matched_provider = None
    matching_endpoints: list[dict[str, Any]] = []
    for provider in spec.provider_order:
        provider_endpoints = [
            endpoint
            for endpoint in endpoints
            if _provider_name(endpoint.get("name", "")) == provider
        ]
        exact_tag_endpoints = [
            endpoint
            for endpoint in provider_endpoints
            if endpoint.get("tag") == provider
        ]
        matching_endpoints = exact_tag_endpoints or provider_endpoints
        if matching_endpoints:
            matched_provider = provider
            break

    parameter_sets = [
        set(endpoint.get("supported_parameters") or [])
        for endpoint in matching_endpoints
    ]
    supported = set.intersection(*parameter_sets) if parameter_sets else set()

    required = set()
    if spec.max_tokens is not None or structured_output:
        required.add("max_tokens")
    if spec.temperature is not None:
        required.add("temperature")
    if spec.reasoning_effort is not None:
        required.add("reasoning_effort")
    if structured_output:
        required.update({"tool_choice", "tools"})
    required.update(runtime_parameters or set())

    try:
        model_metadata = _model_catalog(timeout_seconds).get(spec.model, {})
    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
        return RouteCheck(
            model=spec.model,
            available=bool(endpoints),
            controlled=matched_provider is not None,
            matched_provider=matched_provider,
            endpoint_model_ids=(),
            supported_parameters=tuple(sorted(supported)),
            missing_parameters=tuple(sorted(required - supported)),
            pricing_changed=False,
            provider_data_training=spec.provider_data_training,
            provider_retains_prompts=spec.provider_retains_prompts,
            error=f"Unable to load OpenRouter model catalog: {exc}",
        )
    reasoning = model_metadata.get("reasoning") or {}
    supported_efforts = tuple(reasoning.get("supported_efforts") or ())
    if spec.reasoning_effort and spec.reasoning_effort not in supported_efforts:
        required.add(f"reasoning_effort={spec.reasoning_effort}")
        supported.update(f"reasoning_effort={effort}" for effort in supported_efforts)

    endpoint_model_ids = tuple(
        sorted(
            {
                str(endpoint["model_id"])
                for endpoint in matching_endpoints
                if endpoint.get("model_id")
            }
        )
    )
    return RouteCheck(
        model=spec.model,
        available=bool(endpoints),
        controlled=matched_provider is not None,
        matched_provider=matched_provider,
        endpoint_model_ids=endpoint_model_ids,
        supported_parameters=tuple(sorted(supported)),
        missing_parameters=tuple(sorted(required - supported)),
        pricing_changed=not matching_endpoints
        or any(
            _pricing_changed(spec, endpoint.get("pricing", {}))
            for endpoint in matching_endpoints
        ),
        supported_reasoning_efforts=supported_efforts,
        default_reasoning_effort=reasoning.get("default_effort"),
        reasoning_mandatory=reasoning.get("mandatory"),
        max_completion_tokens=(
            min(
                int(endpoint["max_completion_tokens"])
                for endpoint in matching_endpoints
            )
            if matching_endpoints
            and all(
                endpoint.get("max_completion_tokens") is not None
                for endpoint in matching_endpoints
            )
            else None
        ),
        provider_data_training=spec.provider_data_training,
        provider_retains_prompts=spec.provider_retains_prompts,
        eligible_endpoints=tuple(
            {
                "name": endpoint.get("name"),
                "tag": endpoint.get("tag"),
                "model_id": endpoint.get("model_id"),
                "pricing": endpoint.get("pricing"),
                "max_completion_tokens": endpoint.get("max_completion_tokens"),
                "supported_parameters": endpoint.get("supported_parameters"),
                "status": endpoint.get("status"),
                "quantization": endpoint.get("quantization"),
            }
            for endpoint in matching_endpoints
        ),
    )


@lru_cache(maxsize=1)
def _model_catalog(timeout_seconds: float) -> dict[str, dict[str, Any]]:
    response = requests.get(f"{OPENROUTER_API_BASE}/models", timeout=timeout_seconds)
    response.raise_for_status()
    data = response.json()["data"]
    return {str(model["id"]): model for model in data if model.get("id")}


def _provider_name(endpoint_name: str) -> str:
    display_name = endpoint_name.split(" | ", 1)[0].strip()
    return _PROVIDER_SLUGS.get(display_name, display_name.lower().replace(" ", "-"))


def _pricing_changed(spec: ModelSpec, current: dict[str, Any]) -> bool:
    try:
        prompt_price = Decimal(str(current["prompt"]))
        cache_read_price = Decimal(str(current.get("input_cache_read", prompt_price)))
        return (
            prompt_price != spec.pricing.input_usd_per_token
            or Decimal(str(current["completion"])) != spec.pricing.output_usd_per_token
            or cache_read_price != spec.pricing.cached_input_usd_per_token
        )
    except (KeyError, TypeError, ValueError):
        return True

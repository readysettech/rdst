from __future__ import annotations

from decimal import Decimal, InvalidOperation
from math import isfinite
from pathlib import Path
from typing import Any

import toml

from .models import ModelSpec, Pricing

DEFAULT_MODEL_MATRIX = Path(__file__).with_name("model_matrix.toml")
_SUPPORTED_TRANSPORTS = {
    "openrouter",
    "anthropic",
    "claude-subscription",
    "openai",
    "google",
    "gemini",
}
_REASONING_EFFORTS = {"max", "xhigh", "high", "medium", "low", "minimal", "none"}


class ConfigurationError(ValueError):
    pass


def load_model_specs(path: Path = DEFAULT_MODEL_MATRIX) -> dict[str, ModelSpec]:
    raw = _load_matrix(path)
    entries = raw.get("models")
    if not isinstance(entries, list) or not entries:
        raise ConfigurationError(f"Model matrix {path} has no models")

    specs: dict[str, ModelSpec] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ConfigurationError(f"Model entry {index} must be a table")
        spec = _parse_model(entry, index)
        if spec.name in specs:
            raise ConfigurationError(f"Duplicate model name: {spec.name}")
        specs[spec.name] = spec
    return specs


def load_filter_spec(
    path: Path = DEFAULT_MODEL_MATRIX,
) -> tuple[ModelSpec, tuple[str, ...]]:
    entry = _load_matrix(path).get("filter_model")
    if not isinstance(entry, dict):
        raise ConfigurationError(f"Model matrix {path} has no filter_model")
    spec = _parse_model(entry, -1)
    aliases = entry.get("aliases", [])
    if not isinstance(aliases, list) or not all(
        isinstance(alias, str) and alias for alias in aliases
    ):
        raise ConfigurationError("filter_model has invalid aliases")
    return spec, tuple(aliases)


def select_models(
    specs: dict[str, ModelSpec], names: list[str] | None
) -> list[ModelSpec]:
    if not names:
        return list(specs.values())
    missing = [name for name in names if name not in specs]
    if missing:
        available = ", ".join(sorted(specs))
        raise ConfigurationError(
            f"Unknown models: {', '.join(missing)}. Available models: {available}"
        )
    return [specs[name] for name in names]


def _load_matrix(path: Path) -> dict[str, Any]:
    try:
        return toml.load(path)
    except (OSError, toml.TomlDecodeError) as exc:
        raise ConfigurationError(f"Unable to load model matrix {path}: {exc}") from exc


def _parse_model(entry: dict[str, Any], index: int) -> ModelSpec:
    name = _required_string(entry, "name", index)
    model = _required_string(entry, "model", index)
    transport = _required_string(entry, "transport", index)
    if transport not in _SUPPORTED_TRANSPORTS:
        raise ConfigurationError(f"Model {name} has unsupported transport: {transport}")
    if model.startswith("~") or ":" in model:
        raise ConfigurationError(f"Model {name} uses a router alias: {model}")

    providers = entry.get("provider_order", [])
    if not isinstance(providers, list) or not all(
        isinstance(provider, str) and provider for provider in providers
    ):
        raise ConfigurationError(f"Model {name} has invalid provider_order")
    if transport == "openrouter" and not providers:
        raise ConfigurationError(f"Model {name} requires provider_order")
    if transport != "openrouter" and providers:
        raise ConfigurationError(
            f"Direct model {name} cannot configure OpenRouter provider_order"
        )

    pricing = Pricing(
        input_usd_per_token=_decimal(entry, "input_usd_per_token", name),
        output_usd_per_token=_decimal(entry, "output_usd_per_token", name),
        cached_input_usd_per_token=_decimal(
            entry, "cached_input_usd_per_token", name, default="0"
        ),
        reasoning_usd_per_token=_optional_decimal(
            entry, "reasoning_usd_per_token", name
        ),
    )

    temperature = entry.get("temperature")
    if temperature is not None and (
        not isinstance(temperature, (int, float))
        or isinstance(temperature, bool)
        or not isfinite(float(temperature))
    ):
        raise ConfigurationError(f"Model {name} has invalid temperature")

    max_tokens = entry.get("max_tokens")
    if max_tokens is not None and (
        not isinstance(max_tokens, int)
        or isinstance(max_tokens, bool)
        or max_tokens <= 0
    ):
        raise ConfigurationError(f"Model {name} has invalid max_tokens")

    timeout_seconds = entry.get("timeout_seconds", 300.0)
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not isfinite(float(timeout_seconds))
        or timeout_seconds <= 0
    ):
        raise ConfigurationError(f"Model {name} has invalid timeout_seconds")

    reasoning_effort = entry.get("reasoning_effort")
    if reasoning_effort is not None and reasoning_effort not in _REASONING_EFFORTS:
        supported = ", ".join(sorted(_REASONING_EFFORTS))
        raise ConfigurationError(
            f"Model {name} has invalid reasoning_effort; expected one of {supported}"
        )

    provider_data_training = _optional_bool(entry, "provider_data_training", name)
    provider_retains_prompts = _optional_bool(entry, "provider_retains_prompts", name)
    require_parameters = _bool(entry, "require_parameters", name, default=True)
    allow_fallbacks = _bool(entry, "allow_fallbacks", name, default=False)
    historical = _bool(entry, "historical", name, default=False)

    return ModelSpec(
        name=name,
        model=model,
        transport=transport,
        provider_order=tuple(providers),
        pricing=pricing,
        temperature=float(temperature) if temperature is not None else None,
        max_tokens=max_tokens,
        timeout_seconds=float(timeout_seconds),
        reasoning_effort=reasoning_effort,
        require_parameters=require_parameters,
        allow_fallbacks=allow_fallbacks,
        provider_data_training=provider_data_training,
        provider_retains_prompts=provider_retains_prompts,
        historical=historical,
    )


def _bool(entry: dict[str, Any], key: str, model: str, *, default: bool) -> bool:
    value = entry.get(key, default)
    if not isinstance(value, bool):
        raise ConfigurationError(f"Model {model} has invalid {key}")
    return value


def _optional_bool(entry: dict[str, Any], key: str, model: str) -> bool | None:
    value = entry.get(key)
    if value is not None and not isinstance(value, bool):
        raise ConfigurationError(f"Model {model} has invalid {key}")
    return value


def _required_string(entry: dict[str, Any], key: str, index: int) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"Model entry {index} requires {key}")
    return value


def _decimal(
    entry: dict[str, Any], key: str, model: str, *, default: str | None = None
) -> Decimal:
    value = entry.get(key, default)
    if value is None:
        raise ConfigurationError(f"Model {model} requires {key}")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ConfigurationError(f"Model {model} has invalid {key}") from exc
    if not parsed.is_finite():
        raise ConfigurationError(f"Model {model} has non-finite {key}")
    if parsed < 0:
        raise ConfigurationError(f"Model {model} has negative {key}")
    return parsed


def _optional_decimal(entry: dict[str, Any], key: str, model: str) -> Decimal | None:
    if key not in entry:
        return None
    return _decimal(entry, key, model)

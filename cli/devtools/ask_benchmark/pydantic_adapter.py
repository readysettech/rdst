from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from time import perf_counter
from typing import Any

from anthropic import AsyncAnthropic
from google.genai.types import HttpRetryOptions
from openai import AsyncOpenAI
from pydantic_ai import Agent, ToolOutput
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
)
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.usage import RunUsage

from .models import ModelCallRecord, ModelSpec

ModelFactory = Callable[[ModelSpec], Any]
_PROVIDER_DISPLAY_SLUGS = {
    "Anthropic": "anthropic",
    "Google AI Studio": "google-ai-studio",
    "Moonshot AI": "moonshotai",
    "OpenAI": "openai",
    "xAI": "xai",
    "Z.AI": "z-ai",
}


class RouteMismatchError(RuntimeError):
    pass


class BenchmarkTransportError(RuntimeError):
    pass


class BenchmarkModelOutputError(RuntimeError):
    pass


class _CapturingModel(WrapperModel):
    def __init__(self, wrapped, *, timeout_seconds: float):
        super().__init__(wrapped)
        self.timeout_seconds = timeout_seconds
        self.responses: list[ModelResponse] = []
        self.response_callback: Callable[[ModelResponse, float], None] | None = None

    async def request(self, messages, model_settings, model_request_parameters):
        start = perf_counter()
        response = await asyncio.wait_for(
            super().request(messages, model_settings, model_request_parameters),
            timeout=self.timeout_seconds,
        )
        latency_ms = (perf_counter() - start) * 1000
        self.responses.append(response)
        if self.response_callback:
            self.response_callback(response, latency_ms)
        return response

    def drain_responses(self):
        responses = self.responses
        self.responses = []
        return responses


class PydanticAIAdapter:
    propagate_query_errors = True

    def __init__(
        self,
        default_spec: ModelSpec,
        *,
        model_aliases: dict[str, ModelSpec] | None = None,
        model_factory: ModelFactory | None = None,
        verify_route: bool = True,
        call_receipt_sink: Callable[[ModelCallRecord], None] | None = None,
    ):
        self.default_spec = default_spec
        self.model_aliases = model_aliases or {}
        self.model_factory = model_factory or _build_model
        self.verify_route = verify_route
        self.call_receipt_sink = call_receipt_sink
        self.call_records: list[ModelCallRecord] = []
        self._models: dict[ModelSpec, Any] = {}
        self._attempt_id: str | None = None

    def set_attempt_id(self, attempt_id: str | None) -> None:
        self._attempt_id = attempt_id

    def set_call_receipt_sink(
        self, sink: Callable[[ModelCallRecord], None] | None
    ) -> None:
        self.call_receipt_sink = sink

    def query(
        self,
        *,
        system_message: str,
        user_query: str,
        context: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        stop_sequences: Sequence[str] | None = None,
        provider: str | None = None,
        model: str | None = None,
        debug: bool | None = None,
        api_key: str | None = None,
        purpose: str | None = None,
        extra: dict[str, Any] | None = None,
        history: Sequence[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        del provider, debug, api_key
        spec = self._resolve_spec(model)
        structured = _requests_json(extra)
        stage = purpose or "general"
        output_type = ToolOutput(dict[str, Any], max_retries=0) if structured else str
        model_instance = self._model(spec)
        model_instance.drain_responses()
        agent = Agent(
            model_instance,
            output_type=output_type,
            instructions=system_message or None,
            retries=0,
        )
        message_history = _build_history(context, history)
        settings = _model_settings(
            spec,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stop_sequences=stop_sequences,
        )
        requested_settings = _requested_settings(
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stop_sequences=stop_sequences,
        )
        effective_settings = _effective_settings(
            spec,
            settings,
            requested_settings=requested_settings,
            stage=stage,
            structured=structured,
        )
        configuration_fingerprint = _configuration_fingerprint(effective_settings)
        prompt_hash = _prompt_hash(
            system_message=system_message,
            user_query=user_query,
            context=context,
            history=history,
            settings=effective_settings,
        )
        started_at = _now_iso()
        start = perf_counter()
        run_usage = RunUsage()
        immediate_records: list[ModelCallRecord] = []

        def persist_response(
            response: ModelResponse, response_latency_ms: float
        ) -> None:
            records = self._record_responses(
                spec=spec,
                stage=stage,
                prompt_sha256=prompt_hash,
                messages=[response],
                fallback_latency_ms=response_latency_ms,
                requested_settings=requested_settings,
                effective_settings=effective_settings,
                configuration_fingerprint=configuration_fingerprint,
                started_at=started_at,
                completed_at=_now_iso(),
            )
            immediate_records.extend(records)
            self._capture_records(records)

        model_instance.response_callback = persist_response
        try:
            result = agent.run_sync(
                user_query,
                message_history=message_history,
                model_settings=settings,
                usage=run_usage,
            )
        # Usage accumulated before an output or validation failure is still billable.
        except Exception as exc:
            latency_ms = (perf_counter() - start) * 1000
            completed_at = _now_iso()
            error_kind = _model_error_kind(exc)
            responses = model_instance.drain_responses()
            model_instance.response_callback = None
            if immediate_records:
                if route_error := next(
                    (
                        record.route_error
                        for record in immediate_records
                        if record.route_error
                    ),
                    None,
                ):
                    raise RouteMismatchError(route_error) from exc
            elif responses:
                raise RuntimeError("Model response receipt was not persisted") from exc
            else:
                reasoning_tokens = _reasoning_tokens(run_usage.details)
                record = ModelCallRecord(
                    stage=stage,
                    transport=spec.transport,
                    requested_model=spec.model,
                    returned_model=None,
                    upstream_provider=None,
                    request_id=None,
                    prompt_sha256=prompt_hash,
                    input_tokens=run_usage.input_tokens,
                    output_tokens=run_usage.output_tokens,
                    reasoning_tokens=reasoning_tokens,
                    cached_input_tokens=run_usage.cache_read_tokens,
                    latency_ms=latency_ms,
                    transport_attempts=run_usage.requests,
                    actual_cost_usd=None,
                    normalized_cold_cost_usd=spec.pricing.cold_cost(
                        input_tokens=run_usage.input_tokens,
                        output_tokens=run_usage.output_tokens,
                        reasoning_tokens=reasoning_tokens,
                    ),
                    expected_billed_cost_usd=spec.pricing.expected_billed_cost(
                        input_tokens=run_usage.input_tokens,
                        output_tokens=run_usage.output_tokens,
                        reasoning_tokens=reasoning_tokens,
                        cached_input_tokens=run_usage.cache_read_tokens,
                    ),
                    model_name=spec.name,
                    benchmark_model_name=self.default_spec.name,
                    attempt_id=self._attempt_id,
                    requested_settings=requested_settings,
                    effective_settings=effective_settings,
                    configuration_fingerprint=configuration_fingerprint,
                    started_at=started_at,
                    completed_at=completed_at,
                    error=str(exc),
                    error_kind=error_kind,
                )
                self._capture_records([record])
            _raise_model_error(exc, error_kind)
        model_instance.drain_responses()
        model_instance.response_callback = None
        if not immediate_records:
            raise RuntimeError("Model returned without a durable call receipt")
        if route_error := next(
            (record.route_error for record in immediate_records if record.route_error),
            None,
        ):
            raise RouteMismatchError(route_error)

        output = json.dumps(result.output) if structured else str(result.output)
        usage = result.usage
        return {
            "text": output,
            "usage": {
                "prompt_tokens": usage.input_tokens,
                "completion_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
                "cache_read_tokens": usage.cache_read_tokens,
            },
            "provider": spec.transport,
            "model": result.response.model_name or spec.model,
        }

    def generate_response(
        self, prompt: str, model: str | None = None, **kwargs
    ) -> dict[str, Any]:
        callback = kwargs.pop("callback", None)
        result = self.query(
            system_message=kwargs.pop("system_message", "You are a helpful assistant."),
            user_query=prompt,
            model=model,
            **kwargs,
        )
        response = {
            "response": result["text"],
            "tokens_used": result["usage"]["total_tokens"],
            "usage": result["usage"],
            "model": result["model"],
        }
        if callback:
            callback(
                prompt=prompt,
                response=response["response"],
                tokens=response["tokens_used"],
                model=response["model"],
            )
        return response

    def drain_call_records(self) -> list[ModelCallRecord]:
        records = self.call_records
        self.call_records = []
        return records

    def _capture_records(self, records: list[ModelCallRecord]) -> None:
        self.call_records.extend(records)
        if self.call_receipt_sink:
            for record in records:
                self.call_receipt_sink(record)

    def _resolve_spec(self, requested_model: str | None) -> ModelSpec:
        if requested_model is None or requested_model == self.default_spec.model:
            return self.default_spec
        try:
            return self.model_aliases[requested_model]
        except KeyError as exc:
            raise ValueError(
                f"Model override {requested_model!r} has no pinned evaluation mapping"
            ) from exc

    def _model(self, spec: ModelSpec):
        if spec not in self._models:
            self._models[spec] = _CapturingModel(
                self.model_factory(spec), timeout_seconds=spec.timeout_seconds
            )
        return self._models[spec]

    def _record_responses(
        self,
        *,
        spec: ModelSpec,
        stage: str,
        prompt_sha256: str,
        messages,
        fallback_latency_ms: float,
        requested_settings: dict[str, Any],
        effective_settings: dict[str, Any],
        configuration_fingerprint: str,
        started_at: str,
        completed_at: str,
        error: str | None = None,
        error_kind: str | None = None,
    ) -> list[ModelCallRecord]:
        responses = [
            message for message in messages if isinstance(message, ModelResponse)
        ]
        records = []
        for response in responses:
            details = response.provider_details or {}
            upstream = details.get("downstream_provider")
            upstream_slug = _provider_slug(upstream) if upstream else None
            route_error = None
            if self.verify_route:
                if not response.model_name:
                    route_error = f"{spec.transport} response omitted model identity"
                elif response.model_name != spec.model:
                    route_error = (
                        f"{spec.transport} returned model {response.model_name!r}; "
                        f"expected {spec.model!r}"
                    )
                elif spec.transport == "openrouter" and not upstream_slug:
                    route_error = (
                        "OpenRouter response omitted upstream provider identity"
                    )
                elif (
                    spec.transport == "openrouter"
                    and upstream_slug not in spec.provider_order
                ):
                    route_error = (
                        f"OpenRouter returned provider {upstream!r}; expected one of "
                        f"{', '.join(spec.provider_order)}"
                    )
            usage = response.usage
            reasoning_tokens = _reasoning_tokens(usage.details)
            actual_cost = _decimal_or_none(details.get("cost"))
            record = ModelCallRecord(
                stage=stage,
                transport=spec.transport,
                requested_model=spec.model,
                returned_model=response.model_name,
                upstream_provider=upstream_slug,
                request_id=response.provider_response_id,
                prompt_sha256=prompt_sha256,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=reasoning_tokens,
                cached_input_tokens=usage.cache_read_tokens,
                latency_ms=fallback_latency_ms / max(len(responses), 1),
                transport_attempts=1,
                actual_cost_usd=actual_cost,
                normalized_cold_cost_usd=spec.pricing.cold_cost(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    reasoning_tokens=reasoning_tokens,
                ),
                expected_billed_cost_usd=spec.pricing.expected_billed_cost(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    reasoning_tokens=reasoning_tokens,
                    cached_input_tokens=usage.cache_read_tokens,
                ),
                response_sha256=_model_response_sha256(response),
                model_name=spec.name,
                benchmark_model_name=self.default_spec.name,
                attempt_id=self._attempt_id,
                requested_settings=requested_settings,
                effective_settings=effective_settings,
                configuration_fingerprint=configuration_fingerprint,
                started_at=started_at,
                completed_at=completed_at,
                error=route_error or error,
                error_kind="route" if route_error else error_kind,
                route_error=route_error,
            )
            records.append(record)
        return records


def _build_model(spec: ModelSpec):
    if spec.transport == "openrouter":
        provider_settings: dict[str, Any] = {
            "order": list(spec.provider_order),
            "only": list(spec.provider_order),
            "allow_fallbacks": spec.allow_fallbacks,
            "require_parameters": spec.require_parameters,
        }
        if spec.provider_data_training is not None:
            provider_settings["data_collection"] = (
                "allow" if spec.provider_data_training else "deny"
            )
        if spec.provider_retains_prompts is not None:
            provider_settings["zdr"] = not spec.provider_retains_prompts
        settings: OpenRouterModelSettings = {
            "openrouter_usage": {"include": True},
            "openrouter_provider": provider_settings,
        }
        if spec.reasoning_effort:
            settings["openrouter_reasoning"] = {"effort": spec.reasoning_effort}
        client = AsyncOpenAI(
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url="https://openrouter.ai/api/v1",
            max_retries=0,
            default_headers={
                "HTTP-Referer": "https://readyset.io",
                "X-Title": "RDST Text-to-SQL Benchmark",
            },
        )
        return OpenRouterModel(
            spec.model,
            provider=OpenRouterProvider(openai_client=client),
            settings=settings,
        )
    if spec.transport == "anthropic":
        client = AsyncAnthropic(max_retries=0)
        return AnthropicModel(
            spec.model,
            provider=AnthropicProvider(anthropic_client=client),
        )
    if spec.transport == "openai":
        client = AsyncOpenAI(max_retries=0)
        return OpenAIChatModel(
            spec.model,
            provider=OpenAIProvider(openai_client=client),
        )
    if spec.transport in {"google", "gemini"}:
        return GoogleModel(
            spec.model,
            provider=GoogleProvider(retry_options=HttpRetryOptions(attempts=1)),
        )
    raise ValueError(f"Unsupported evaluation transport: {spec.transport}")


def _model_settings(
    spec: ModelSpec,
    *,
    max_tokens: int | None,
    temperature: float | None,
    top_p: float | None,
    stop_sequences: Sequence[str] | None,
):
    settings: dict[str, Any] = {"timeout": spec.timeout_seconds}
    effective_max_tokens = max_tokens if max_tokens is not None else spec.max_tokens
    if effective_max_tokens is None and spec.transport == "anthropic":
        effective_max_tokens = 4096
    if effective_max_tokens is not None:
        settings["max_tokens"] = effective_max_tokens
    if spec.temperature is not None:
        settings["temperature"] = (
            temperature if temperature is not None else spec.temperature
        )
    if spec.reasoning_effort and spec.transport == "anthropic":
        settings["anthropic_effort"] = spec.reasoning_effort
        settings["anthropic_thinking"] = {"type": "adaptive"}
    elif spec.reasoning_effort and spec.transport == "openai":
        settings["openai_reasoning_effort"] = spec.reasoning_effort
    elif spec.reasoning_effort and spec.transport in {"google", "gemini"}:
        settings["thinking"] = spec.reasoning_effort
    if top_p is not None:
        settings["top_p"] = top_p
    if stop_sequences:
        settings["stop_sequences"] = list(stop_sequences)
    return settings


def _requested_settings(
    *,
    max_tokens: int | None,
    temperature: float | None,
    top_p: float | None,
    stop_sequences: Sequence[str] | None,
):
    return {
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "stop_sequences": list(stop_sequences) if stop_sequences else None,
    }


def _effective_settings(
    spec: ModelSpec,
    model_settings: dict[str, Any],
    *,
    requested_settings: dict[str, Any],
    stage: str,
    structured: bool,
):
    settings = {
        "schema_version": 1,
        "configuration_name": spec.name,
        "stage": stage,
        "model": spec.model,
        "transport": spec.transport,
        "provider_order": list(spec.provider_order),
        "reasoning_effort": spec.reasoning_effort,
        "allow_fallbacks": spec.allow_fallbacks,
        "require_parameters": spec.require_parameters,
        "provider_data_training": spec.provider_data_training,
        "provider_retains_prompts": spec.provider_retains_prompts,
        "structured_output": structured,
        "sdk_retries": 0,
        "agent_retries": 0,
        "model_settings": model_settings,
        "omitted_requested_settings": {
            "temperature": {
                "value": requested_settings["temperature"],
                "reason": "unsupported_by_pinned_route",
            }
        }
        if requested_settings.get("temperature") is not None
        and "temperature" not in model_settings
        else None,
    }
    return _drop_none(settings)


def _drop_none(value: Any):
    if isinstance(value, dict):
        return {
            key: _drop_none(item) for key, item in value.items() if item is not None
        }
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value


def _configuration_fingerprint(settings: dict[str, Any]) -> str:
    encoded = json.dumps(
        settings, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _model_error_kind(exc: Exception) -> str:
    # asyncio.TimeoutError became an alias of the built-in TimeoutError in
    # Python 3.11. RDST's CI still exercises Python 3.10, where they are
    # distinct classes.
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if isinstance(exc, ModelHTTPError):
        if exc.status_code == 429:
            return "rate_limit"
        if exc.status_code in {408, 504}:
            return "timeout"
        if exc.status_code >= 500:
            return "provider_5xx"
        if exc.status_code in {400, 401, 403, 404, 422}:
            return "route_policy"
        return "transport"
    if isinstance(exc, ModelAPIError):
        return "transport"
    if isinstance(exc, UnexpectedModelBehavior):
        return "model_output"
    return "internal"


def _raise_model_error(exc: Exception, error_kind: str):
    if error_kind in {"transport", "rate_limit", "timeout", "provider_5xx"}:
        raise BenchmarkTransportError(str(exc)) from exc
    if error_kind == "model_output":
        raise BenchmarkModelOutputError(str(exc)) from exc
    raise exc


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_history(context: str | None, history: Sequence[dict[str, str]] | None):
    messages = []
    if context:
        messages.append(ModelRequest(parts=[UserPromptPart(f"[CONTEXT]\n{context}")]))
    for message in history or ():
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, str) or not content:
            continue
        if role == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content)]))
        elif role == "assistant":
            messages.append(ModelResponse(parts=[TextPart(content)]))
    return messages


def _requests_json(extra: dict[str, Any] | None) -> bool:
    response_format = (extra or {}).get("response_format")
    return isinstance(response_format, dict) and response_format.get("type") in {
        "json_object",
        "json_schema",
    }


def _prompt_hash(**parts: Any) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _model_response_sha256(response: ModelResponse) -> str:
    def serialize(part: Any) -> Any:
        if is_dataclass(part):
            return asdict(part)
        if hasattr(part, "model_dump"):
            return part.model_dump(mode="json")
        return repr(part)

    payload = json.dumps(
        [serialize(part) for part in response.parts],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _reasoning_tokens(details: dict[str, int] | None) -> int:
    details = details or {}
    for key in ("reasoning_tokens", "thinking_tokens", "thoughts_tokens"):
        if key in details:
            return int(details[key])
    return 0


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _provider_slug(value: Any) -> str:
    text = str(value)
    return _PROVIDER_DISPLAY_SLUGS.get(text, text.lower().replace(" ", "-"))

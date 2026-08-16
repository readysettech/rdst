from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from shared.llm_manager import LLMManager
from shared.llm_manager.base import LLMError
from shared.llm_manager.claude_provider import normalize_anthropic_model

from .models import ModelCallRecord, ModelSpec
from .pydantic_adapter import (
    BenchmarkModelOutputError,
    BenchmarkTransportError,
    RouteMismatchError,
)


class AnthropicSDKAdapter:
    """Evaluation receipt wrapper around production LLMManager and ClaudeProvider."""

    propagate_query_errors = True

    def __init__(
        self,
        default_spec: ModelSpec,
        *,
        model_aliases: dict[str, ModelSpec] | None = None,
        manager: LLMManager | None = None,
        api_key: str | None = None,
        call_receipt_sink: Callable[[ModelCallRecord], None] | None = None,
    ):
        if default_spec.transport != "anthropic":
            raise ValueError("AnthropicSDKAdapter requires an anthropic model spec")
        self.default_spec = default_spec
        self.model_aliases = model_aliases or {}
        self.manager = manager or LLMManager(
            defaults={"provider": "claude", "model": default_spec.model},
        )
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for direct benchmark calls")
        self.call_receipt_sink = call_receipt_sink
        self.call_records: list[ModelCallRecord] = []
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
        del provider, debug
        spec = self._resolve_spec(model)
        stage = purpose or "general"
        settings = {
            "max_tokens": max_tokens
            if max_tokens is not None
            else spec.max_tokens or 800,
            "temperature": temperature
            if temperature is not None
            else spec.temperature
            if spec.temperature is not None
            else 0.2,
            "top_p": top_p,
            "stop_sequences": list(stop_sequences or ()),
            "timeout_seconds": 120
            if (max_tokens if max_tokens is not None else spec.max_tokens or 800) > 4096
            else 60,
        }
        effective_settings = {
            "stage": stage,
            "transport": "anthropic",
            "model": spec.model,
            **settings,
            "structured_output": bool(
                extra and isinstance(extra.get("response_format"), dict)
            ),
            "sdk_retries": 0,
        }
        configuration_fingerprint = _hash(effective_settings)
        prompt_sha256 = _hash(
            {
                "system_message": system_message,
                "user_query": user_query,
                "context": context,
                "history": list(history or ()),
                "settings": effective_settings,
            }
        )
        started_at = _now_iso()
        started = perf_counter()
        response = None
        error: Exception | None = None
        try:
            response = self.manager.query(
                system_message=system_message,
                user_query=user_query,
                context=context,
                max_tokens=settings["max_tokens"],
                temperature=settings["temperature"],
                top_p=top_p,
                stop_sequences=stop_sequences,
                provider="claude",
                model=spec.model,
                debug=True,
                api_key=api_key or self.api_key,
                purpose=purpose,
                extra=extra,
                history=history,
            )
            raw = response.get("raw") or {}
            returned_model = normalize_anthropic_model(
                str(raw.get("model") or response.get("model") or "")
            )
            expected_model = normalize_anthropic_model(spec.model)
            if returned_model != expected_model:
                raise RouteMismatchError(
                    f"Anthropic returned {returned_model!r}; expected {expected_model!r}"
                )
            return response
        except Exception as exc:
            error = exc
            raise
        finally:
            record = self._record(
                spec=spec,
                stage=stage,
                prompt_sha256=prompt_sha256,
                settings=settings,
                effective_settings=effective_settings,
                configuration_fingerprint=configuration_fingerprint,
                started_at=started_at,
                latency_ms=(perf_counter() - started) * 1000,
                response=response,
                error=error,
            )
            self._capture(record)
            if error is not None:
                self._raise_classified(error)

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

    def _resolve_spec(self, requested_model: str | None) -> ModelSpec:
        if requested_model is None or normalize_anthropic_model(
            requested_model
        ) == normalize_anthropic_model(self.default_spec.model):
            return self.default_spec
        try:
            alias = self.model_aliases[requested_model]
        except KeyError as exc:
            raise ValueError(
                f"Model override {requested_model!r} has no pinned evaluation mapping"
            ) from exc
        return replace(
            alias,
            model=normalize_anthropic_model(requested_model),
            transport="anthropic",
            provider_order=(),
            reasoning_effort=None,
        )

    def _record(
        self,
        *,
        spec: ModelSpec,
        stage: str,
        prompt_sha256: str,
        settings: dict[str, Any],
        effective_settings: dict[str, Any],
        configuration_fingerprint: str,
        started_at: str,
        latency_ms: float,
        response: dict[str, Any] | None,
        error: Exception | None,
    ) -> ModelCallRecord:
        usage = (response or {}).get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        cached_input_tokens = int(usage.get("cache_read_input_tokens") or 0)
        raw = (response or {}).get("raw") or {}
        returned_model = raw.get("model") or (response or {}).get("model")
        request_id = raw.get("_request_id")
        if request_id is None and isinstance(error, LLMError):
            request_id = error.request_id
        response_text = (response or {}).get("text")
        return ModelCallRecord(
            stage=stage,
            transport="anthropic",
            requested_model=spec.model,
            returned_model=str(returned_model) if returned_model else None,
            upstream_provider="anthropic",
            request_id=request_id,
            prompt_sha256=prompt_sha256,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=0,
            cached_input_tokens=cached_input_tokens,
            latency_ms=latency_ms,
            transport_attempts=1,
            actual_cost_usd=None,
            normalized_cold_cost_usd=spec.pricing.cold_cost(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
            expected_billed_cost_usd=spec.pricing.expected_billed_cost(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
            ),
            response_sha256=(
                hashlib.sha256(response_text.encode("utf-8")).hexdigest()
                if isinstance(response_text, str)
                else None
            ),
            model_name=spec.name,
            benchmark_model_name=self.default_spec.name,
            attempt_id=self._attempt_id,
            requested_settings=settings,
            effective_settings=effective_settings,
            configuration_fingerprint=configuration_fingerprint,
            started_at=started_at,
            completed_at=_now_iso(),
            error=str(error) if error else None,
            error_kind=_error_kind(error),
            route_error=str(error) if isinstance(error, RouteMismatchError) else None,
        )

    def _capture(self, record: ModelCallRecord) -> None:
        self.call_records.append(record)
        if self.call_receipt_sink:
            self.call_receipt_sink(record)

    @staticmethod
    def _raise_classified(error: Exception) -> None:
        if isinstance(
            error,
            (RouteMismatchError, BenchmarkTransportError, BenchmarkModelOutputError),
        ):
            raise error
        if isinstance(error, LLMError):
            if error.status in {408, 429} or (error.status and error.status >= 500):
                raise BenchmarkTransportError(str(error)) from error
            if error.code in {
                "HTTP_ERROR",
                "PROXY_UNREACHABLE",
                "STREAM_INTERRUPTED",
            }:
                raise BenchmarkTransportError(str(error)) from error
            if error.code == "PARSE_ERROR":
                raise BenchmarkModelOutputError(str(error)) from error
        raise error


def _error_kind(error: Exception | None):
    if error is None:
        return None
    if isinstance(error, RouteMismatchError):
        return "route_mismatch"
    if isinstance(error, BenchmarkTransportError):
        return "transport"
    if isinstance(error, BenchmarkModelOutputError):
        return "model_output"
    if isinstance(error, LLMError):
        return error.code.lower()
    return type(error).__name__


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

from __future__ import annotations

import json
import logging
import os
from collections.abc import Generator
from enum import Enum
from typing import Any

import anthropic

from shared.shell import environment_assignment

from .base import LLMError, Provider, ProviderRequest, ProviderResponse

logger = logging.getLogger(__name__)


class AnthropicModel(str, Enum):
    """Supported Anthropic models for RDST.

    RDST uses Claude Sonnet 4.6 as the default model for query analysis.
    Same pricing as Sonnet 4.5, better performance.

    https://docs.anthropic.com/en/docs/about-claude/models/overview
    """

    # Latest models (4.6) - RDST defaults
    SONNET_4_6 = "claude-sonnet-4-6"  # Default - $3/$15 per MTok
    OPUS_4_6 = "claude-opus-4-6"  # $5/$25 per MTok

    # Previous generation
    SONNET_4_5 = "claude-sonnet-4-5-20250929"  # Previous default
    SONNET_4 = "claude-sonnet-4-20250514"  # Previous version
    OPUS_4 = "claude-opus-4-20250514"  # $15/$75 per MTok
    HAIKU_4_5 = "claude-haiku-4-5-20251001"  # Fast and cheap for filters

    # Legacy aliases for backward compatibility
    CLAUDE_4_SONNET = "claude-sonnet-4-20250514"  # noqa: PIE796
    CLAUDE_4_OPUS = "claude-opus-4-20250514"  # noqa: PIE796


_RETIRED_MODEL_REPLACEMENTS = {
    AnthropicModel.SONNET_4.value: AnthropicModel.SONNET_4_6.value,
    AnthropicModel.OPUS_4.value: AnthropicModel.OPUS_4_6.value,
}


def normalize_anthropic_model(model: str | AnthropicModel) -> str:
    """Map models retired by Anthropic to RDST's current equivalents."""
    value = model.value if isinstance(model, AnthropicModel) else str(model)
    return _RETIRED_MODEL_REPLACEMENTS.get(value, value)


class ClaudeProvider(Provider):
    """Anthropic Messages API provider backed by the official Python SDK."""

    _DEFAULT_MODEL = os.getenv("RDST_ANTHROPIC_MODEL", AnthropicModel.SONNET_4_6.value)
    _BASE_URL = "https://api.anthropic.com"
    _API_VERSION = os.getenv("ANTHROPIC_VERSION", "2023-06-01")

    def default_model(self) -> str:
        return normalize_anthropic_model(self._DEFAULT_MODEL)

    @staticmethod
    def _timeout(request: ProviderRequest) -> int:
        # Non-streaming calls hold the socket until the full response is
        # generated. Scale the read timeout with the requested response size,
        # using ~16 tokens/second as a conservative generation floor.
        return min(600, max(60, int((request.max_tokens or 0) / 16)))

    def _client(
        self,
        *,
        api_key: str,
        base_url: str | None,
        extra_headers: dict | None,
        timeout: int,
    ):
        headers = {"anthropic-version": self._API_VERSION}
        if extra_headers:
            headers.update(extra_headers)
        return anthropic.Anthropic(
            api_key=api_key,
            base_url=base_url or self._BASE_URL,
            default_headers=headers,
            timeout=timeout,
            max_retries=0,
        )

    @staticmethod
    def _request_payload(request: ProviderRequest) -> dict[str, Any]:
        system_parts = [
            message["content"]
            for message in request.messages
            if message.get("role") == "system"
        ]
        system = "\n".join(system_parts) if system_parts else None
        messages = [
            {"role": message["role"], "content": message["content"]}
            for message in request.messages
            if message.get("role") in ("user", "assistant")
        ]

        payload: dict[str, Any] = {
            "model": normalize_anthropic_model(request.model),
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens or 800,
        }
        if system:
            payload["system"] = system
        if request.stop_sequences:
            payload["stop_sequences"] = list(request.stop_sequences)
        if request.top_p is not None:
            payload["top_p"] = request.top_p

        extra = dict(request.extra or {})
        response_format = extra.pop("response_format", None)
        if response_format:
            if response_format.get("type") == "json_schema":
                json_schema = response_format.get("json_schema", {})
                tool_name = json_schema.get("name", "json_response")
                payload["tools"] = [
                    {
                        "name": tool_name,
                        "description": f"Return a {tool_name} response",
                        "input_schema": json_schema.get("schema", {}),
                    }
                ]
                payload["tool_choice"] = {"type": "tool", "name": tool_name}
            elif response_format.get("type") == "json_object":
                instruction = (
                    "\n\nYou MUST respond with valid JSON only. "
                    "No markdown, no explanation, no code fences - just the JSON object."
                )
                payload["system"] = payload.get("system", "") + instruction
        if extra:
            payload["extra_body"] = extra
        return payload

    @staticmethod
    def _error_payload(exc: anthropic.APIStatusError):
        payload = exc.body if isinstance(exc.body, dict) else {}
        error = payload.get("error")
        error_obj = error if isinstance(error, dict) else {}
        detail = (
            error_obj.get("message")
            or payload.get("detail")
            or (error if isinstance(error, str) else None)
            or str(exc)
        )
        detail = " ".join(str(detail).split())[:500]
        request_id = payload.get("request_id")
        if not isinstance(request_id, str):
            request_id = exc.request_id if isinstance(exc.request_id, str) else None
        return payload, error_obj, detail, request_id

    @classmethod
    def _raise_status_error(
        cls,
        exc: anthropic.APIStatusError,
        *,
        base_url: str | None,
        model: str,
    ) -> None:
        payload, error_obj, detail, request_id = cls._error_payload(exc)
        status = exc.status_code
        request_suffix = f" (request ID: {request_id})" if request_id else ""

        if base_url:
            code = payload.get("code") if isinstance(payload.get("code"), str) else None
            if status == 401:
                message = (
                    "RDST's AI service could not validate your trial access. "
                    "Refresh your trial token or configure your own Anthropic API key."
                )
                code = "TRIAL_AUTH_INVALID"
            elif code == "TRIAL_EXHAUSTED":
                message = (
                    f"{detail}\n\nTo continue, configure your own Anthropic API key "
                    "or email hello@readyset.io about trial access."
                )
            elif code == "INVALID_CLIENT":
                message = f"Trial client authentication failed: {detail}"
            elif status == 429:
                message = f"RDST's AI service is rate limited: {detail}"
            elif status >= 500:
                message = f"RDST's AI service is temporarily unavailable: {detail}"
            else:
                message = f"RDST's AI service rejected the request: {detail}"
            raise LLMError(
                message + request_suffix,
                code=code or "PROXY_HTTP",
                status=status,
                request_id=request_id,
                cause=exc,
            )

        provider_type = error_obj.get("type")
        if status == 200 and isinstance(provider_type, str):
            raise LLMError(
                f"Anthropic streaming error: {detail}{request_suffix}",
                code=f"ANTHROPIC_{provider_type.upper()}",
                status=status,
                request_id=request_id,
                cause=exc,
            )

        messages = {
            400: (
                f"Anthropic rejected the request for model '{model}': {detail}. "
                "Check the configured model and request parameters."
            ),
            401: "Anthropic rejected the configured API key. Check the key and try again.",
            402: f"Anthropic reports a billing or credit problem: {detail}",
            403: f"The Anthropic API key is not authorized to use model '{model}': {detail}",
            404: f"Anthropic could not find model or endpoint '{model}': {detail}",
            429: f"Anthropic rate limited the request: {detail}",
        }
        if status in messages:
            message = messages[status]
        elif status >= 500:
            message = f"Anthropic is temporarily unavailable: {detail}"
        else:
            message = f"Anthropic API error: {detail}"
        code = {
            400: "ANTHROPIC_INVALID_REQUEST",
            401: "ANTHROPIC_AUTH_INVALID",
            402: "ANTHROPIC_BILLING",
            403: "ANTHROPIC_PERMISSION",
            404: "ANTHROPIC_NOT_FOUND",
            413: "ANTHROPIC_REQUEST_TOO_LARGE",
            422: "ANTHROPIC_UNPROCESSABLE_ENTITY",
            429: "ANTHROPIC_RATE_LIMIT",
            529: "ANTHROPIC_OVERLOADED",
        }.get(status, "ANTHROPIC_UNAVAILABLE" if status >= 500 else "PROVIDER_HTTP")
        raise LLMError(
            message + request_suffix,
            code=code,
            status=status,
            request_id=request_id,
            cause=exc,
        )

    @staticmethod
    def _raise_connection_error(
        exc: anthropic.APIConnectionError,
        *,
        base_url: str | None,
    ) -> None:
        if base_url:
            raise LLMError(
                "Unable to reach RDST trial service.\n\n"
                "Options:\n"
                "  1. Try again in a few minutes\n"
                f"  2. Set your own key: {environment_assignment('ANTHROPIC_API_KEY', 'sk-ant-...')}\n"
                "     Get one at: https://console.anthropic.com/",
                code="PROXY_UNREACHABLE",
                cause=exc,
            )
        raise LLMError(f"Claude request error: {exc}", code="HTTP_ERROR", cause=exc)

    @staticmethod
    def _usage(message) -> dict[str, int]:
        usage = message.usage
        cache_creation = getattr(usage, "cache_creation_input_tokens", None) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", None) or 0
        uncached = getattr(usage, "input_tokens", None) or 0
        output = getattr(usage, "output_tokens", None) or 0
        prompt = uncached + cache_creation + cache_read
        return {
            "prompt_tokens": prompt,
            "completion_tokens": output,
            "total_tokens": prompt + output,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
        }

    def complete(
        self,
        request: ProviderRequest,
        *,
        api_key: str,
        base_url: str | None = None,
        extra_headers: dict | None = None,
        debug: bool = False,
    ) -> ProviderResponse:
        model = normalize_anthropic_model(request.model)
        client = self._client(
            api_key=api_key,
            base_url=base_url,
            extra_headers=extra_headers,
            timeout=self._timeout(request),
        )
        try:
            raw_response = client.messages.with_raw_response.create(
                **self._request_payload(request)
            )
            try:
                message = raw_response.parse()
            except Exception as exc:
                raise LLMError(
                    f"Claude response parse error: {exc}",
                    code="PARSE_ERROR",
                    cause=exc,
                ) from exc
        except anthropic.APIStatusError as exc:
            self._raise_status_error(exc, base_url=base_url, model=model)
        except anthropic.APIConnectionError as exc:
            self._raise_connection_error(exc, base_url=base_url)
        except anthropic.APIError as exc:
            raise LLMError(f"Claude request error: {exc}", code="HTTP_ERROR", cause=exc)

        try:
            text_segments: list[str] = []
            tool_result = None
            for block in message.content:
                if block.type == "tool_use":
                    tool_result = json.dumps(block.input)
                elif block.type == "text":
                    text_segments.append(block.text)
            text = tool_result if tool_result is not None else "\n".join(text_segments)
            usage = self._usage(message)
        except Exception as exc:  # noqa: BLE001 - normalize SDK parse failures
            raise LLMError(
                f"Claude response parse error: {exc}", code="PARSE_ERROR", cause=exc
            )

        raw = message.to_dict() if debug else {}
        request_id = raw_response.request_id or getattr(message, "_request_id", None)
        if request_id:
            raw["_request_id"] = request_id

        trial_remaining = raw_response.headers.get("X-RDST-Trial-Remaining-Cents")
        trial_limit = raw_response.headers.get("X-RDST-Trial-Limit-Cents")
        if trial_remaining is not None:
            try:
                raw["_trial_remaining_cents"] = int(trial_remaining)
            except (TypeError, ValueError):
                pass
            if trial_limit is not None:
                try:
                    raw["_trial_limit_cents"] = int(trial_limit)
                except (TypeError, ValueError):
                    pass

        return ProviderResponse(text=text, usage=usage, raw=raw)

    def stream(
        self,
        request: ProviderRequest,
        *,
        api_key: str,
        base_url: str | None = None,
        extra_headers: dict | None = None,
    ) -> Generator[str, None, None]:
        """Stream response text from Claude using the official SDK."""
        model = normalize_anthropic_model(request.model)
        client = self._client(
            api_key=api_key,
            base_url=base_url,
            extra_headers=extra_headers,
            timeout=120,
        )
        try:
            with client.messages.stream(**self._request_payload(request)) as stream:
                yield from stream.text_stream
        except anthropic.APIStatusError as exc:
            self._raise_status_error(exc, base_url=base_url, model=model)
        except anthropic.APIConnectionError as exc:
            target = "RDST trial service" if base_url else "Anthropic"
            raise LLMError(
                f"Unable to reach {target}: {exc}", code="HTTP_ERROR", cause=exc
            )
        except anthropic.APIError as exc:
            raise LLMError(
                f"The Anthropic response stream was interrupted: {exc}",
                code="STREAM_INTERRUPTED",
                cause=exc,
            )

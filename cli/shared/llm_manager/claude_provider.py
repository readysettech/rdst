from __future__ import annotations

from enum import Enum
import logging
import os
from typing import Any, Dict, Generator

import json
import requests

from shared.shell import environment_assignment

from .base import LLMError, Provider, ProviderRequest, ProviderResponse

logger = logging.getLogger(__name__)


class AnthropicModel(str, Enum):
    """Supported Anthropic models for RDST.

    RDST uses Claude Sonnet 4.6 as the default model for query analysis.
    Same pricing as Sonnet 4.5, better performance.

    https://docs.anthropic.com/en/docs/about-claude/models/overview
    """

    # Latest models (4.6) — RDST defaults
    SONNET_4_6 = "claude-sonnet-4-6"  # Default - $3/$15 per MTok
    OPUS_4_6 = "claude-opus-4-6"  # $5/$25 per MTok

    # Previous generation
    SONNET_4_5 = "claude-sonnet-4-5-20250929"  # Previous default
    SONNET_4 = "claude-sonnet-4-20250514"  # Previous version
    OPUS_4 = "claude-opus-4-20250514"  # $15/$75 per MTok
    HAIKU_4_5 = "claude-haiku-4-5-20251001"  # Fast & cheap - for scan, help, filters

    # Legacy aliases for backward compatibility
    CLAUDE_4_SONNET = "claude-sonnet-4-20250514"
    CLAUDE_4_OPUS = "claude-opus-4-20250514"


_RETIRED_MODEL_REPLACEMENTS = {
    AnthropicModel.SONNET_4.value: AnthropicModel.SONNET_4_6.value,
    AnthropicModel.OPUS_4.value: AnthropicModel.OPUS_4_6.value,
}


def normalize_anthropic_model(model: str | AnthropicModel) -> str:
    """Map models retired by Anthropic to RDST's current equivalents."""
    value = model.value if isinstance(model, AnthropicModel) else str(model)
    return _RETIRED_MODEL_REPLACEMENTS.get(value, value)


class ClaudeProvider(Provider):
    """
    Anthropic Claude Messages API wrapper.

    Default: Sonnet 4.6 (fast, cost-effective for query analysis)
    Override via RDST_ANTHROPIC_MODEL env var to use Opus for more sophisticated analysis.
    """

    _DEFAULT_MODEL = os.getenv("RDST_ANTHROPIC_MODEL", AnthropicModel.SONNET_4_6.value)

    _BASE_URL = "https://api.anthropic.com/v1/messages"
    _API_VERSION = os.getenv("ANTHROPIC_VERSION", "2023-06-01")

    def default_model(self) -> str:
        return normalize_anthropic_model(self._DEFAULT_MODEL)

    @staticmethod
    def _error_payload(response) -> tuple[dict[str, Any], str, str | None]:
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}

        error = payload.get("error")
        error_obj = error if isinstance(error, dict) else {}
        message = (
            error_obj.get("message")
            or payload.get("detail")
            or (error if isinstance(error, str) else None)
            or f"HTTP {response.status_code}"
        )
        # Provider bodies are useful, but never let an upstream HTML error page
        # flood the CLI/web response.
        message = " ".join(str(message).split())[:500]
        request_id = payload.get("request_id")
        if not isinstance(request_id, str):
            header_id = response.headers.get("request-id")
            request_id = header_id if isinstance(header_id, str) else None
        return payload, message, request_id

    @classmethod
    def _raise_http_error(
        cls,
        response,
        *,
        base_url: str | None,
        model: str,
    ) -> None:
        if response.status_code < 400:
            return

        payload, detail, request_id = cls._error_payload(response)
        request_suffix = f" (request ID: {request_id})" if request_id else ""

        if base_url:
            code = payload.get("code") if isinstance(payload.get("code"), str) else None
            if response.status_code == 401:
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
            elif response.status_code == 429:
                message = f"RDST's AI service is rate limited: {detail}"
            elif response.status_code >= 500:
                message = f"RDST's AI service is temporarily unavailable: {detail}"
            else:
                message = f"RDST's AI service rejected the request: {detail}"
            raise LLMError(
                message + request_suffix,
                code=code or "PROXY_HTTP",
                status=response.status_code,
                request_id=request_id,
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
        if response.status_code in messages:
            message = messages[response.status_code]
        elif response.status_code >= 500:
            message = f"Anthropic is temporarily unavailable: {detail}"
        else:
            message = f"Anthropic API error: {detail}"

        error_obj = payload.get("error")
        provider_type = (
            error_obj.get("type")
            if isinstance(error_obj, dict) and isinstance(error_obj.get("type"), str)
            else None
        )
        code = {
            400: "ANTHROPIC_INVALID_REQUEST",
            401: "ANTHROPIC_AUTH_INVALID",
            402: "ANTHROPIC_BILLING",
            403: "ANTHROPIC_PERMISSION",
            404: "ANTHROPIC_NOT_FOUND",
            429: "ANTHROPIC_RATE_LIMIT",
        }.get(
            response.status_code,
            "ANTHROPIC_UNAVAILABLE" if response.status_code >= 500 else "PROVIDER_HTTP",
        )
        if provider_type:
            logger.debug(
                "Anthropic request failed: status=%s type=%s request_id=%s",
                response.status_code,
                provider_type,
                request_id or "unknown",
            )
        raise LLMError(
            message + request_suffix,
            code=code,
            status=response.status_code,
            request_id=request_id,
        )

    def complete(
        self,
        request: ProviderRequest,
        *,
        api_key: str,
        base_url: str | None = None,
        extra_headers: dict | None = None,
        debug: bool = False,
    ) -> ProviderResponse:
        headers = {
            "x-api-key": api_key,
            "anthropic-version": self._API_VERSION,
            "content-type": "application/json",
        }
        # Merge attestation headers for trial proxy requests
        if extra_headers:
            headers.update(extra_headers)

        # Map provider-agnostic messages into Claude-style:
        # - Claude supports a "system" string and "messages" user/assistant turns.
        system_parts = [
            m["content"] for m in request.messages if m.get("role") == "system"
        ]
        system = "\n".join(system_parts) if system_parts else None

        # keep user messages in order; assistant messages (none here) would pass through
        msg_list = [
            {"role": m["role"], "content": m["content"]}
            for m in request.messages
            if m.get("role") in ("user", "assistant")
        ]

        payload: Dict[str, Any] = {
            "model": normalize_anthropic_model(request.model),
            "messages": msg_list,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if system:
            payload["system"] = system
        if request.stop_sequences:
            payload["stop_sequences"] = list(request.stop_sequences)
        if request.top_p is not None:
            payload["top_p"] = request.top_p

        # Support JSON mode via tool use pattern
        # Anthropic uses tools with forced tool_choice for structured JSON output
        # https://docs.anthropic.com/en/docs/build-with-claude/tool-use
        if request.extra and "response_format" in request.extra:
            response_format = request.extra["response_format"]
            if response_format.get("type") == "json_schema":
                # Convert JSON schema format to Anthropic tool format
                json_schema = response_format.get("json_schema", {})
                tool_name = json_schema.get("name", "json_response")
                schema = json_schema.get("schema", {})

                # Create a tool with the desired schema
                payload["tools"] = [
                    {
                        "name": tool_name,
                        "description": f"Return a {tool_name} response",
                        "input_schema": schema,
                    }
                ]

                # Force the model to use this tool
                payload["tool_choice"] = {"type": "tool", "name": tool_name}

            elif response_format.get("type") == "json_object":
                # json_object mode: no schema provided, just enforce JSON output.
                # Claude doesn't have a native json_object mode like OpenAI,
                # so we add an explicit instruction to the system prompt.
                json_instruction = (
                    "\n\nYou MUST respond with valid JSON only. "
                    "No markdown, no explanation, no code fences — just the JSON object."
                )
                existing_system = payload.get("system", "")
                payload["system"] = existing_system + json_instruction

            # Add other extra parameters (excluding response_format)
            extra_without_response_format = {
                k: v for k, v in request.extra.items() if k != "response_format"
            }
            if extra_without_response_format:
                payload.update(extra_without_response_format)
        elif request.extra:
            payload.update(request.extra)

        target_url = base_url or self._BASE_URL
        # Scale timeout with max_tokens — large analysis prompts need more time
        timeout = 60
        if request.max_tokens and request.max_tokens > 4096:
            timeout = 120
        try:
            resp = requests.post(
                target_url, headers=headers, data=json.dumps(payload), timeout=timeout
            )
        except requests.exceptions.ConnectionError as e:
            if base_url:
                raise LLMError(
                    "Unable to reach RDST trial service.\n\n"
                    "Options:\n"
                    "  1. Try again in a few minutes\n"
                    f"  2. Set your own key: {environment_assignment('ANTHROPIC_API_KEY', 'sk-ant-...')}\n"
                    "     Get one at: https://console.anthropic.com/",
                    code="PROXY_UNREACHABLE",
                    cause=e,
                )
            raise LLMError(f"Claude request error: {e}", code="HTTP_ERROR", cause=e)
        except Exception as e:
            raise LLMError(f"Claude request error: {e}", code="HTTP_ERROR", cause=e)

        self._raise_http_error(
            resp,
            base_url=base_url,
            model=normalize_anthropic_model(request.model),
        )

        data = resp.json()
        try:
            # Anthropic returns content as a list of blocks
            # For tool use (JSON mode), extract tool_use input as JSON
            # For regular text, join text blocks
            blocks = data.get("content", []) or []
            text_segments = []
            tool_result = None

            for b in blocks:
                if b.get("type") == "tool_use":
                    # JSON mode response - return the tool input as JSON string
                    tool_result = json.dumps(b.get("input", {}))
                elif b.get("type") == "text":
                    text_segments.append(b.get("text", ""))

            # Prefer tool result (JSON mode) over text
            text = (
                tool_result
                if tool_result
                else "\n".join([t for t in text_segments if t])
            )

            usage = data.get("usage", {}) or {}
            out_usage = {
                "prompt_tokens": usage.get("input_tokens"),
                "completion_tokens": usage.get("output_tokens"),
                "total_tokens": (usage.get("input_tokens") or 0)
                + (usage.get("output_tokens") or 0),
            }
        except Exception as e:
            raise LLMError(
                f"Claude response parse error: {e}", code="PARSE_ERROR", cause=e
            )

        # Capture trial balance from proxy response headers
        raw = data if debug else {}
        trial_remaining = resp.headers.get("X-RDST-Trial-Remaining-Cents")
        trial_limit = resp.headers.get("X-RDST-Trial-Limit-Cents")
        if trial_remaining is not None:
            if not raw:
                raw = {}
            try:
                raw["_trial_remaining_cents"] = int(trial_remaining)
            except (ValueError, TypeError):
                pass
            if trial_limit is not None:
                try:
                    raw["_trial_limit_cents"] = int(trial_limit)
                except (ValueError, TypeError):
                    pass

        return ProviderResponse(text=text, usage=out_usage, raw=raw)

    def stream(
        self,
        request: ProviderRequest,
        *,
        api_key: str,
        base_url: str | None = None,
        extra_headers: dict | None = None,
    ) -> Generator[str, None, None]:
        """Stream response tokens from Claude (SYNC generator).

        NOTE: request.messages is actually List[Dict] at runtime, not List[ProviderMessage].
        This matches how complete() handles messages (see lines 62-70).
        """
        headers = {
            "x-api-key": api_key,
            "anthropic-version": self._API_VERSION,
            "content-type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)

        # Reuse message transformation from complete() (lines 62-74)
        # request.messages is List[Dict] with keys "role", "content"
        system_parts = [
            m["content"] for m in request.messages if m.get("role") == "system"
        ]
        system = "\n".join(system_parts) if system_parts else None

        msg_list = [
            {"role": m["role"], "content": m["content"]}
            for m in request.messages
            if m.get("role") in ("user", "assistant")
        ]

        payload: Dict[str, Any] = {
            "model": normalize_anthropic_model(request.model),
            "messages": msg_list,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens or 2000,
            "stream": True,
        }
        if system:
            payload["system"] = system
        if request.stop_sequences:
            payload["stop_sequences"] = list(request.stop_sequences)
        if request.top_p is not None:
            payload["top_p"] = request.top_p

        url = base_url or self._BASE_URL
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                stream=True,
                timeout=120,
            )
        except requests.exceptions.ConnectionError as e:
            target = "RDST trial service" if base_url else "Anthropic"
            raise LLMError(f"Unable to reach {target}: {e}", code="HTTP_ERROR", cause=e)

        self._raise_http_error(
            response,
            base_url=base_url,
            model=normalize_anthropic_model(request.model),
        )

        try:
            for line in response.iter_lines():
                if line:
                    line_str = line.decode("utf-8")
                    if line_str.startswith("data: "):
                        try:
                            data = json.loads(line_str[6:])
                            if data.get("type") == "content_block_delta":
                                delta = data.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    yield delta.get("text", "")
                            elif data.get("type") == "error":
                                error = data.get("error") or {}
                                detail = error.get("message", "Unknown streaming error")
                                error_type = error.get("type", "stream_error")
                                request_id = data.get("request_id")
                                suffix = (
                                    f" (request ID: {request_id})"
                                    if isinstance(request_id, str)
                                    else ""
                                )
                                raise LLMError(
                                    f"Anthropic streaming error: {detail}{suffix}",
                                    code=f"ANTHROPIC_{str(error_type).upper()}",
                                    request_id=request_id
                                    if isinstance(request_id, str)
                                    else None,
                                )
                        except json.JSONDecodeError:
                            continue
        except requests.exceptions.RequestException as e:
            raise LLMError(
                f"The Anthropic response stream was interrupted: {e}",
                code="STREAM_INTERRUPTED",
                cause=e,
            )

from __future__ import annotations

from enum import Enum
import logging
import os
from typing import Any, Dict, Generator

import json
import requests

logger = logging.getLogger(__name__)

from .base import LLMError, Provider, ProviderRequest, ProviderResponse


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


class ClaudeProvider(Provider):
    """
    Anthropic Claude Messages API wrapper.

    Default: Sonnet 4.5 (fast, cost-effective for query analysis)
    Override via RDST_ANTHROPIC_MODEL env var to use Opus for more sophisticated analysis.
    """

    _DEFAULT_MODEL = AnthropicModel(
        os.getenv("RDST_ANTHROPIC_MODEL", AnthropicModel.SONNET_4_5.value)
    )

    _BASE_URL = "https://api.anthropic.com/v1/messages"
    _API_VERSION = os.getenv("ANTHROPIC_VERSION", "2023-06-01")

    def default_model(self) -> str:
        return self._DEFAULT_MODEL.value

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
            "model": request.model,
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
        try:
            resp = requests.post(target_url, headers=headers, data=json.dumps(payload), timeout=60)
        except requests.exceptions.ConnectionError as e:
            if base_url:
                raise LLMError(
                    "Unable to reach RDST trial service.\n\n"
                    "Options:\n"
                    "  1. Try again in a few minutes\n"
                    '  2. Set your own key: export ANTHROPIC_API_KEY="sk-ant-..."\n'
                    "     Get one at: https://console.anthropic.com/",
                    code="PROXY_UNREACHABLE",
                    cause=e,
                )
            raise LLMError(f"Claude request error: {e}", code="HTTP_ERROR", cause=e)
        except Exception as e:
            raise LLMError(f"Claude request error: {e}", code="HTTP_ERROR", cause=e)

        if resp.status_code == 403 and base_url:
            # Check for trial-specific errors from our proxy
            try:
                err_json = resp.json()
            except Exception:
                err_json = {}
            if err_json.get("code") == "TRIAL_EXHAUSTED":
                detail = err_json.get("detail", "Trial credits exhausted.")
                raise LLMError(
                    f"{detail}\n\n"
                    "To continue using RDST:\n"
                    "  1. Get your own key: https://console.anthropic.com/\n"
                    '  2. Set it: export ANTHROPIC_API_KEY="sk-ant-..."\n\n'
                    "Want more trial credits? Email hello@readyset.io",
                    code="TRIAL_EXHAUSTED",
                    status=403,
                )
            if err_json.get("code") == "INVALID_CLIENT":
                raise LLMError(
                    f"Trial authentication failed: {err_json.get('detail', 'unknown')}",
                    code="INVALID_CLIENT",
                    status=403,
                )

        if resp.status_code >= 400:
            try:
                err_json = resp.json()
            except Exception:
                err_json = {"error": {"message": resp.text}}
            # Anthropic puts message under "error": {"message": "..."}
            msg = (err_json.get("error") or {}).get(
                "message", f"HTTP {resp.status_code}"
            )
            logger.debug(f"Full API error response: {json.dumps(err_json, indent=2)}")
            logger.debug(f"Status code: {resp.status_code}")
            raise LLMError(
                f"Claude error: {msg}", code="PROVIDER_HTTP", status=resp.status_code
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
            "model": request.model,
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
            response.raise_for_status()
        except Exception as e:
            raise LLMError(
                f"Claude streaming request error: {e}", code="HTTP_ERROR", cause=e
            )

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
                    except json.JSONDecodeError:
                        continue

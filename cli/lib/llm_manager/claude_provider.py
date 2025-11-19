from __future__ import annotations

from enum import Enum
import os
from typing import Any, Dict

import json
import requests

from .base import LLMError, Provider, ProviderRequest, ProviderResponse


class AnthropicModel(str, Enum):
    """Supported Anthropic models."""
    # https://docs.anthropic.com/en/docs/about-claude/models/overview
    CLAUDE4_1_OPUS = "claude-opus-4-1-20250805"
    CLAUDE_4_OPUS = "claude-opus-4-20250514"
    CLAUDE_4_SONNET = "claude-sonnet-4-20250514"
    CLAUDE_3_7_SONNET = "claude-3-7-sonnet-20250219"
    CLAUDE_3_5_SONNET_LATEST = "claude-sonnet-4-20250514"
    CLAUDE_3_5_HAIKU_LATEST = "claude-3-5-haiku-latest"
    CLAUDE_3_OPUS = "claude-3-opus-20240229"
    CLAUDE_3_SONNET = "claude-3-sonnet-20240229"
    CLAUDE_3_HAIKU = "claude-3-haiku-20240307"
    


class ClaudeProvider(Provider):
    """
    Anthropic Claude Messages API wrapper.
    """

    _DEFAULT_MODEL = AnthropicModel(
        os.getenv("RDST_ANTHROPIC_MODEL", AnthropicModel.CLAUDE_3_5_SONNET_LATEST.value)
    )

    _BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1/messages")
    _API_VERSION = os.getenv("ANTHROPIC_VERSION", "2023-06-01")

    def default_model(self) -> str:
        return self._DEFAULT_MODEL

    def complete(self, request: ProviderRequest, *, api_key: str, debug: bool = False) -> ProviderResponse:
        headers = {
            "x-api-key": api_key,
            "anthropic-version": self._API_VERSION,
            "content-type": "application/json",
        }

        # Map provider-agnostic messages into Claude-style:
        # - Claude supports a "system" string and "messages" user/assistant turns.
        system_parts = [m["content"] for m in request.messages if m.get("role") == "system"]
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
                payload["tools"] = [{
                    "name": tool_name,
                    "description": f"Return a {tool_name} response",
                    "input_schema": schema
                }]

                # Force the model to use this tool
                payload["tool_choice"] = {"type": "tool", "name": tool_name}

            # Add other extra parameters (excluding response_format)
            extra_without_response_format = {k: v for k, v in request.extra.items() if k != "response_format"}
            if extra_without_response_format:
                payload.update(extra_without_response_format)
        elif request.extra:
            payload.update(request.extra)

        try:
            resp = requests.post(self._BASE_URL, headers=headers, data=json.dumps(payload), timeout=60)
        except Exception as e:
            raise LLMError(f"Claude request error: {e}", code="HTTP_ERROR", cause=e)

        if resp.status_code >= 400:
            try:
                err_json = resp.json()
            except Exception:
                err_json = {"error": {"message": resp.text}}
            # Anthropic puts message under "error": {"message": "..."}
            msg = (err_json.get("error") or {}).get("message", f"HTTP {resp.status_code}")
            # DEBUG: Log full error response for troubleshooting
            print(f"DEBUG: Full API error response: {json.dumps(err_json, indent=2)}")
            print(f"DEBUG: Status code: {resp.status_code}")
            raise LLMError(f"Claude error: {msg}", code="PROVIDER_HTTP", status=resp.status_code)

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
            text = tool_result if tool_result else "\n".join([t for t in text_segments if t])

            usage = data.get("usage", {}) or {}
            out_usage = {
                "prompt_tokens": usage.get("input_tokens"),
                "completion_tokens": usage.get("output_tokens"),
                "total_tokens": (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0),
            }
        except Exception as e:
            raise LLMError(f"Claude response parse error: {e}", code="PARSE_ERROR", cause=e)

        return ProviderResponse(text=text, usage=out_usage, raw=data if debug else {})
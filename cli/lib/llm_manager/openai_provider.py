from __future__ import annotations

from enum import Enum
import os
from typing import Any, Dict

import json
import requests

from .base import Provider, ProviderRequest, ProviderResponse, LLMError


class OpenAIModel(str, Enum):
    """Supported OpenAI models."""
    # https://platform.openai.com/docs/models
    GPT_5 = "gpt-5"
    GPT_5_MINI = "gpt-5-mini"
    GPT_5_NANO = "gpt-5-nano"
    GPT_4_1 = "gpt-4.1"
    GPT_4_1_MINI = "gpt-4.1-mini"
    GPT_4_1_NANO = "gpt-4.1-nano"
    GPT_4O = "gpt-4o"
    GPT_4O_MINI = "gpt-4o-mini"


class OpenAIProvider(Provider):
    """
    OpenAI Chat Completions wrapper (provider-agnostic mapping).
    """

    _DEFAULT_MODEL = os.getenv("RDST_OPENAI_MODEL", OpenAIModel.GPT_4O_MINI.value)
    _BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1/chat/completions")

    def default_model(self) -> str:
        return self._DEFAULT_MODEL

    def complete(self, request: ProviderRequest, *, api_key: str, debug: bool = False) -> ProviderResponse:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop_sequences:
            payload["stop"] = list(request.stop_sequences)

        # allow passthrough extras (e.g., response_format)
        payload.update(request.extra or {})

        try:
            resp = requests.post(self._BASE_URL, headers=headers, data=json.dumps(payload), timeout=60)
        except Exception as e:
            raise LLMError(f"OpenAI request error: {e}", code="HTTP_ERROR", cause=e)

        if resp.status_code >= 400:
            try:
                err_json = resp.json()
            except Exception:
                err_json = {"error": {"message": resp.text}}
            msg = err_json.get("error", {}).get("message", f"HTTP {resp.status_code}")
            raise LLMError(f"OpenAI error: {msg}", code="PROVIDER_HTTP", status=resp.status_code)

        data = resp.json()
        try:
            choice = data["choices"][0]
            text = choice.get("message", {}).get("content", "") or ""
            usage = data.get("usage", {}) or {}
            out_usage = {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
        except Exception as e:
            raise LLMError(f"OpenAI response parse error: {e}", code="PARSE_ERROR", cause=e)

        return ProviderResponse(text=text, usage=out_usage, raw=data if debug else {})
from __future__ import annotations

import os
from typing import Any, Dict

import json
import requests

from .base import Provider, ProviderRequest, ProviderResponse, LLMError


class LMStudioProvider(Provider):
    """
    LM Studio local server wrapper (OpenAI-compatible API).

    By default connects to http://localhost:1234/v1/chat/completions
    Override with LMSTUDIO_BASE_URL environment variable.
    """

    def _get_base_url(self) -> str:
        """Get base URL from config file, environment variable, or default."""
        # Environment variable takes precedence
        env_url = os.getenv("LMSTUDIO_BASE_URL")
        if env_url:
            return env_url

        # Try to read from config file
        try:
            from ..cli.rdst_cli import TargetsConfig
            config = TargetsConfig()
            config.load()
            config_url = config.get_llm_base_url()
            if config_url:
                return config_url
        except Exception:
            pass

        # Default fallback
        return "http://localhost:1234/v1/chat/completions"

    def default_model(self) -> str:
        return os.getenv("RDST_LMSTUDIO_MODEL", "openai/gpt-oss-20b")

    def get_current_model(self) -> str:
        """Get the currently loaded model from LM Studio."""
        base_url = self._get_base_url()
        models_url = base_url.replace("/v1/chat/completions", "/v1/models")

        try:
            resp = requests.get(models_url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data", [])
                if models and len(models) > 0:
                    # Return the first model (typically the loaded one)
                    return models[0].get("id", "unknown")
        except Exception:
            pass

        # Fallback to environment variable or default
        return self.default_model()

    def complete(self, request: ProviderRequest, *, api_key: str, debug: bool = False) -> ProviderResponse:
        """
        LM Studio typically doesn't require an API key for local usage,
        but we accept it for interface compatibility.
        """
        headers = {
            "Content-Type": "application/json",
        }

        # Some LM Studio setups may use API keys
        if api_key and api_key != "not-needed":
            headers["Authorization"] = f"Bearer {api_key}"

        # LM Studio uses whatever model is currently loaded, so we query for it
        # rather than trusting the passed model parameter
        current_model = self.get_current_model()

        payload: Dict[str, Any] = {
            "model": current_model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop_sequences:
            payload["stop"] = list(request.stop_sequences)

        # allow passthrough extras
        payload.update(request.extra or {})

        base_url = self._get_base_url()
        try:
            resp = requests.post(base_url, headers=headers, data=json.dumps(payload), timeout=120)
        except requests.exceptions.ConnectionError as e:
            raise LLMError(
                f"LM Studio connection error: {e}. Is LM Studio running on {base_url}?",
                code="CONNECTION_ERROR",
                cause=e
            )
        except Exception as e:
            raise LLMError(f"LM Studio request error: {e}", code="HTTP_ERROR", cause=e)

        if resp.status_code >= 400:
            try:
                err_json = resp.json()
            except Exception:
                err_json = {"error": {"message": resp.text}}
            msg = err_json.get("error", {}).get("message", f"HTTP {resp.status_code}")
            raise LLMError(f"LM Studio error: {msg}", code="PROVIDER_HTTP", status=resp.status_code)

        data = resp.json()
        try:
            choice = data["choices"][0]
            message = choice.get("message", {})

            # Try content first, then reasoning (for reasoning models), then concatenate both
            content = message.get("content", "") or ""
            reasoning = message.get("reasoning", "") or ""

            # If both exist, combine them; otherwise use whichever is present
            if content and reasoning:
                text = f"{reasoning}\n\n{content}"
            else:
                text = reasoning or content

            usage = data.get("usage", {}) or {}
            out_usage = {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
        except Exception as e:
            raise LLMError(f"LM Studio response parse error: {e}", code="PARSE_ERROR", cause=e)
        
        # Include current model in response metadata
        response_data = {"raw": data} if debug else {}
        response_data["current_model"] = self.get_current_model()

        return ProviderResponse(text=text, usage=out_usage, raw=response_data)

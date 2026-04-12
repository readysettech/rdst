from __future__ import annotations

from enum import Enum
import os
from typing import Any, Dict

import json
import requests

from .base import Provider, ProviderRequest, ProviderResponse, LLMError


class GeminiModel(str, Enum):
    """Supported Google Gemini models."""
    # https://ai.google.dev/gemini-api/docs/models/gemini
    GEMINI_2_0_FLASH_EXP = "gemini-2.0-flash-exp"
    GEMINI_1_5_PRO = "gemini-1.5-pro"
    GEMINI_1_5_FLASH = "gemini-1.5-flash"
    GEMINI_1_5_FLASH_8B = "gemini-1.5-flash-8b"


class GeminiProvider(Provider):
    """
    Google Gemini API wrapper (provider-agnostic mapping).
    """

    _DEFAULT_MODEL = os.getenv("RDST_GEMINI_MODEL", GeminiModel.GEMINI_2_0_FLASH_EXP.value)
    _BASE_URL = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/models")

    def default_model(self) -> str:
        return self._DEFAULT_MODEL

    def complete(self, request: ProviderRequest, *, api_key: str, debug: bool = False) -> ProviderResponse:
        # Gemini uses API key as query parameter
        url = f"{self._BASE_URL}/{request.model}:generateContent?key={api_key}"

        headers = {
            "Content-Type": "application/json",
        }

        # Convert messages to Gemini format
        # Gemini expects: {"contents": [{"role": "user", "parts": [{"text": "..."}]}]}
        contents = []

        # Extract system message if present and prepend it to first user message
        system_content = None
        for msg in request.messages:
            if msg.get("role") == "system":
                system_content = msg.get("content", "")
                break

        for msg in request.messages:
            role = msg.get("role", "user")
            if role == "system":
                continue  # Already handled

            # Map OpenAI/Claude roles to Gemini roles
            gemini_role = "model" if role == "assistant" else "user"

            content = msg.get("content", "")
            # If this is the first user message and we have system content, prepend it
            if gemini_role == "user" and system_content and not contents:
                content = f"{system_content}\n\n{content}"
                system_content = None  # Only prepend once

            contents.append({
                "role": gemini_role,
                "parts": [{"text": content}]
            })

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            }
        }

        if request.top_p is not None:
            payload["generationConfig"]["topP"] = request.top_p
        if request.stop_sequences:
            payload["generationConfig"]["stopSequences"] = list(request.stop_sequences)

        # Handle extras - filter out unsupported OpenAI-specific parameters
        if request.extra:
            # response_format is OpenAI-specific, Gemini uses responseMimeType instead
            if "response_format" in request.extra:
                response_format = request.extra["response_format"]
                # Convert to Gemini's JSON mode if applicable
                if response_format.get("type") in ("json_object", "json_schema"):
                    payload["generationConfig"]["responseMimeType"] = "application/json"

            # Add other extras, excluding unsupported parameters
            unsupported_params = {"response_format"}
            filtered_extras = {k: v for k, v in request.extra.items() if k not in unsupported_params}
            if filtered_extras:
                payload.update(filtered_extras)

        try:
            resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
        except Exception as e:
            raise LLMError(f"Gemini request error: {e}", code="HTTP_ERROR", cause=e)

        if resp.status_code >= 400:
            try:
                err_json = resp.json()
            except Exception:
                err_json = {"error": {"message": resp.text}}
            msg = err_json.get("error", {}).get("message", f"HTTP {resp.status_code}")
            raise LLMError(f"Gemini error: {msg}", code="PROVIDER_HTTP", status=resp.status_code)

        data = resp.json()
        try:
            # Gemini response format: {"candidates": [{"content": {"parts": [{"text": "..."}]}}]}
            candidate = data.get("candidates", [{}])[0]
            parts = candidate.get("content", {}).get("parts", [])
            text = "".join(part.get("text", "") for part in parts)

            # Extract usage metadata
            usage_metadata = data.get("usageMetadata", {})
            out_usage = {
                "prompt_tokens": usage_metadata.get("promptTokenCount"),
                "completion_tokens": usage_metadata.get("candidatesTokenCount"),
                "total_tokens": usage_metadata.get("totalTokenCount"),
            }
        except Exception as e:
            raise LLMError(f"Gemini response parse error: {e}", code="PARSE_ERROR", cause=e)

        return ProviderResponse(text=text, usage=out_usage, raw=data if debug else {})

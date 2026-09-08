"""Readyset-hosted GLM provider backed by the bounded Keyservice endpoint."""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Generator
from typing import Any

import requests

from shared import account_session
from shared.keyservice import keyservice_base_url

from .base import LLMError, Provider, ProviderRequest, ProviderResponse
from .key_resolution import HOSTED_MODEL

_HTTP_SESSION = requests.Session()
# Schema annotation deliberately uses worker threads, while an interactive Ask
# can start before annotation finishes. DeepInfra may reject that local burst,
# so keep Readyset-hosted requests from one RDST process in a single fair-ish
# queue. Claude BYOK is unaffected and separate RDST clients remain independent.
_HOSTED_REQUEST_LOCK = threading.Lock()


def _client_version() -> str:
    override = os.getenv("RDST_VERSION", "").strip()
    if override:
        return override
    try:
        from _version import __version__

        return str(__version__)
    except Exception:
        try:
            from _version_build import __version__

            return str(__version__)
        except Exception:
            return "0.1.0"


class HostedGLMProvider(Provider):
    """Call the Readyset-funded route without exposing its OpenRouter key."""

    def default_model(self) -> str:
        return HOSTED_MODEL

    @staticmethod
    def _messages(request: ProviderRequest) -> list[dict[str, str]]:
        messages = request.as_chat_dicts()
        extra = dict(request.extra or {})
        response_format = extra.get("response_format")
        if not isinstance(response_format, dict):
            return messages

        if response_format.get("type") == "json_schema":
            schema = response_format.get("json_schema") or {}
            instruction = (
                "Return only a JSON object that conforms to this JSON Schema. "
                "Do not use markdown or code fences.\n"
                + json.dumps(schema.get("schema") or {}, separators=(",", ":"))
            )
        elif response_format.get("type") == "json_object":
            instruction = "Return only a valid JSON object. Do not use markdown or code fences."
        else:
            return messages

        if messages and messages[0]["role"] == "system":
            messages[0] = {
                "role": "system",
                "content": f"{messages[0]['content']}\n\n{instruction}",
            }
        else:
            messages.insert(0, {"role": "system", "content": instruction})
        return messages

    @staticmethod
    def _raise_response_error(response: requests.Response) -> None:
        try:
            body = response.json()
        except ValueError:
            body = {}
        code = str(body.get("code") or "HOSTED_INFERENCE_ERROR")
        detail = str(body.get("detail") or f"Hosted inference returned HTTP {response.status_code}")
        raise LLMError(detail, code=code, status=response.status_code)

    def complete(
        self,
        request: ProviderRequest,
        *,
        api_key: str,
        base_url: str | None = None,
        extra_headers: dict | None = None,
        debug: bool = False,
    ) -> ProviderResponse:
        with _HOSTED_REQUEST_LOCK:
            return self._complete_serialized(
                request,
                api_key=api_key,
                base_url=base_url,
                extra_headers=extra_headers,
                debug=debug,
            )

    def _complete_serialized(
        self,
        request: ProviderRequest,
        *,
        api_key: str,
        base_url: str | None = None,
        extra_headers: dict | None = None,
        debug: bool = False,
    ) -> ProviderResponse:
        del extra_headers
        payload: dict[str, Any] = {
            "request_id": str(uuid.uuid4()),
            "messages": self._messages(request),
            "max_tokens": request.max_tokens or 800,
            "temperature": request.temperature,
        }
        attribution = (request.extra or {}).get("_rdst_attribution")
        schema_key = (request.extra or {}).get("_rdst_schema_cache_key")
        if isinstance(schema_key, str):
            payload["schema_cache_key"] = schema_key
        if isinstance(attribution, dict):
            payload["attribution"] = dict(attribution)
        try:
            from shared.telemetry import telemetry

            telemetry_enabled = telemetry.is_enabled()
            payload.setdefault("attribution", {})["analytics_disabled"] = (
                not telemetry_enabled
            )
            if telemetry_enabled:
                payload["attribution"]["installation_id"] = telemetry.device_id
        except Exception:
            payload.setdefault("attribution", {})["analytics_disabled"] = True
        payload.setdefault("attribution", {})["client_version"] = _client_version()
        response_format = (request.extra or {}).get("response_format")
        if isinstance(response_format, dict) and response_format.get("type") in {
            "json_object",
            "json_schema",
        }:
            payload["response_format"] = response_format
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop_sequences:
            payload["stop"] = list(request.stop_sequences)

        endpoint = f"{(base_url or keyservice_base_url()).rstrip('/')}/v1/inference"
        token = api_key
        response = None
        for attempt in range(2):
            try:
                response = _HTTP_SESSION.post(
                    endpoint,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                    timeout=max(60, min(600, int((request.max_tokens or 800) / 16))),
                )
            except requests.RequestException as exc:
                raise LLMError(
                    f"Readyset-hosted inference is unavailable: {exc}",
                    code="HOSTED_INFERENCE_UNAVAILABLE",
                    cause=exc,
                ) from exc
            # Keyservice owns inference retries. Refresh only a rejected login
            # token, never an upstream provider authentication failure.
            if response.status_code != 401 or attempt == 1:
                break
            try:
                code = response.json().get("code")
            except (ValueError, AttributeError):
                break
            if code != "UNAUTHORIZED":
                break
            refreshed = account_session.access_token(force_refresh=True)
            if not refreshed:
                break
            token = refreshed

        assert response is not None
        if response.status_code != 200:
            self._raise_response_error(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMError(
                "Readyset-hosted inference returned invalid JSON",
                code="HOSTED_INFERENCE_INVALID_RESPONSE",
                cause=exc,
            ) from exc
        text = body.get("text")
        if not isinstance(text, str):
            raise LLMError(
                "Readyset-hosted inference returned no text",
                code="HOSTED_INFERENCE_INVALID_RESPONSE",
            )
        analytics_account_id = str(body.get("analytics_account_id") or "")
        if analytics_account_id:
            try:
                account_session.save_account_metadata_for_access_token(
                    {"analytics_account_id": analytics_account_id},
                    token,
                )
            except Exception:
                pass
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        normalized_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        prompt_details = usage.get("prompt_tokens_details")
        if isinstance(prompt_details, dict):
            cached_tokens = prompt_details.get("cached_tokens")
            if type(cached_tokens) is int and cached_tokens >= 0:
                normalized_usage["cache_read_input_tokens"] = cached_tokens
            cache_write_tokens = prompt_details.get("cache_write_tokens")
            if type(cache_write_tokens) is int and cache_write_tokens >= 0:
                normalized_usage["cache_creation_input_tokens"] = cache_write_tokens
        raw = body if debug else {"quota": body.get("quota"), "usage": usage}
        return ProviderResponse(text=text, usage=normalized_usage, raw=raw)

    def stream(
        self,
        request: ProviderRequest,
        *,
        api_key: str,
        base_url: str | None = None,
        extra_headers: dict | None = None,
    ) -> Generator[str, None, None]:
        response = self.complete(
            request,
            api_key=api_key,
            base_url=base_url,
            extra_headers=extra_headers,
        )
        yield response.text

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .base import LLMDefaults, LLMError, Provider, ProviderRequest, ProviderResponse
from .claude_provider import ClaudeProvider


class LLMManager:
    """
    Unified LLM facade for RDST.

    RDST uses Claude (Anthropic) exclusively for AI-powered query analysis.
    Users must provide their own API key via the ANTHROPIC_API_KEY environment variable.

    Default Model: Claude Sonnet 4 (fast, cost-effective)
    Optional: Claude Opus 4 (more sophisticated analysis via RDST_ANTHROPIC_MODEL env var)

    Environment Variables
    --------------------
    ANTHROPIC_API_KEY: Your Anthropic API key (required)
    RDST_ANTHROPIC_MODEL: Override default model (optional, e.g., "claude-opus-4-20250514")

    Public API
    ----------
    query(system_message, user_query, context, max_tokens, temperature, ...) -> dict
    """

    def __init__(self, defaults: Optional[Dict[str, Any]] = None, logger: Optional[logging.Logger] = None):
        d = LLMDefaults(**(defaults or {}))

        # Load model from config file if set
        try:
            from ..cli.rdst_cli import TargetsConfig
            config = TargetsConfig()
            config.load()
            llm_config = config.get_llm_config()

            # Only model can be overridden from config (provider is always Claude)
            if llm_config.get("model"):
                d.model = llm_config["model"]

            self._config = config
        except Exception:
            self._config = None

        # Environment variable takes precedence for model
        env_model = os.getenv("RDST_ANTHROPIC_MODEL")
        if env_model:
            d.model = env_model

        # Provider is always Claude (BYOK)
        d.provider = "claude"

        self.defaults = d
        self.logger = logger or logging.getLogger("llm_manager")
        self.logger.addHandler(logging.NullHandler())

        # Claude is the only provider
        self._providers: Dict[str, Provider] = {}
        self.register_provider("claude", ClaudeProvider())

    # Provider registry
    def register_provider(self, name: str, provider: Provider) -> None:
        self._providers[name.lower()] = provider

    def provider(self, name: Optional[str] = None) -> Provider:
        p = (name or self.defaults.provider or "claude").lower()
        if p not in self._providers:
            raise LLMError(f"Unknown provider '{p}'. RDST only supports Claude.", code="NO_SUCH_PROVIDER")
        return self._providers[p]

    def query(
        self,
        *,
        system_message: str,
        user_query: str,
        context: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        stop_sequences: Optional[Sequence[str]] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        debug: Optional[bool] = None,
        api_key: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Provider-agnostic query interface.

        Returns a dict:
        {
          "text": "<llm response>",
          "usage": {"prompt_tokens": int|None, "completion_tokens": int|None, "total_tokens": int|None},
          "provider": "openai" | "claude",
          "model": "<resolved model>",
          "raw": {...}  # present if debug=True
        }
        """
        name = (provider or self.defaults.provider).lower()
        prov = self.provider(name)

        resolved = {
            "max_tokens": int(max_tokens if max_tokens is not None else self.defaults.max_tokens),
            "temperature": float(temperature if temperature is not None else self.defaults.temperature),
            "top_p": top_p if top_p is not None else self.defaults.top_p,
            "stop_sequences": list(stop_sequences or self.defaults.stop_sequences or []),
            "model": model or self.defaults.model or prov.default_model(),
            "debug": bool(self.defaults.debug if debug is None else debug),
        }

        # Logging LLM parameters (not credentials). max_tokens = generation limit, not API token
        self.logger.debug(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            "LLM request meta: provider=%s model=%s max_tokens=%s temperature=%s top_p=%s stop#=%s",
            name, resolved["model"], resolved["max_tokens"], resolved["temperature"], resolved["top_p"], len(resolved["stop_sequences"])
        )

        # prefer explicit api_key, else load from vault
        key = api_key or self._safe_load_key_for_query(name)

        # normalize into a ProviderRequest
        messages = _assemble_messages(system_message, user_query, context)
        req = ProviderRequest(
            messages=messages,
            model=resolved["model"],
            max_tokens=resolved["max_tokens"],
            temperature=resolved["temperature"],
            top_p=resolved["top_p"],
            stop_sequences=resolved["stop_sequences"],
            extra=extra or {},
        )

        try:
            resp: ProviderResponse = prov.complete(req, api_key=key, debug=resolved["debug"])
        except LLMError:
            raise
        except Exception as e:
            raise LLMError(f"Provider '{name}' failed: {e}", code="PROVIDER_FAILURE", cause=e)

        # For LM Studio, use the actual loaded model if available
        actual_model = resolved["model"]
        if name == "lmstudio" and hasattr(resp, 'raw') and resp.raw:
            actual_model = resp.raw.get("current_model", resolved["model"])

        out = {
            "text": resp.text,
            "usage": resp.usage,
            "provider": name,
            "model": actual_model,
        }
        if resolved["debug"]:
            out["raw"] = resp.raw

        # Track LLM usage for telemetry
        try:
            from ..telemetry import telemetry
            usage = resp.usage or {}
            telemetry.track_llm_usage(
                provider=name,
                model=actual_model,
                tokens_in=usage.get("prompt_tokens") or 0,
                tokens_out=usage.get("completion_tokens") or 0,
                duration_ms=0,  # TODO: Add timing if needed
                purpose=extra.get("purpose", "general") if extra else "general",
            )
        except Exception:
            pass  # Don't fail LLM call if telemetry fails

        return out

    def generate_response(self, prompt: str, model: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """
        Simple interface for workflow manager compatibility.

        Args:
            prompt: The prompt to send to the LLM
            model: Model to use (optional)
            **kwargs: Additional parameters

        Returns:
            Dict with response, tokens_used, and model fields
        """
        try:
            # Filter kwargs to only pass valid parameters to query()
            valid_query_params = {
                "system_message", "context", "max_tokens", "temperature",
                "top_p", "stop_sequences", "provider", "debug", "api_key", "extra"
            }

            filtered_kwargs = {k: v for k, v in kwargs.items() if k in valid_query_params}

            result = self.query(
                system_message=filtered_kwargs.get("system_message", "You are a helpful assistant."),
                user_query=prompt,
                context=filtered_kwargs.get("context"),
                model=model,
                **{k: v for k, v in filtered_kwargs.items() if k not in ["system_message", "context"]}
            )

            # Transform to workflow manager expected format
            return {
                "response": result["text"],
                "tokens_used": result["usage"].get("total_tokens"),
                "model": result["model"]
            }
        except Exception as e:
            raise e

    def _safe_load_key_for_query(self, provider: str) -> str:
        """Load API key from ANTHROPIC_API_KEY environment variable.

        RDST uses Bring Your Own Key (BYOK) - users must set their own API key.
        """
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            return api_key

        raise LLMError(
            "No LLM API key configured.\n\n"
            "Please provide your Anthropic API key to enable query analysis.\n"
            "You can get one at: https://console.anthropic.com/",
            code="NO_API_KEY"
        )


def _assemble_messages(system_message: str, user_query: str, context: Optional[str]) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = []
    if system_message:
        msgs.append({"role": "system", "content": system_message})
    if context:
        msgs.append({"role": "user", "content": f"[CONTEXT]\n{context}"})
    msgs.append({"role": "user", "content": user_query})
    return msgs
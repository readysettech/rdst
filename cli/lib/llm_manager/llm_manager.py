from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .base import LLMDefaults, LLMError, Provider, ProviderRequest, ProviderResponse
from .openai_provider import OpenAIProvider
from .claude_provider import ClaudeProvider
from .lmstudio_provider import LMStudioProvider
from .gemini_provider import GeminiProvider
from .security import encrypt, decrypt, LLMKeyVault


class LLMManager:
    """
    Unified, provider-agnostic LLM facade.

    Key Management (POC, static encryption)
   -----------------------------------
    - Keys are encrypted with a static shared secret and stored locally per provider.
    - The shared secret can be passed explicitly or via env: RDST_LLM_SHARED_KEY.
    - Storage path defaults to: ~/.rdst/keys/<provider>.key.enc (overridable).

    Env Overrides
   ---------
    RDST_LLM_PROVIDER: default provider name (openai|claude|lmstudio|gemini)
    RDST_LLM_SHARED_KEY: static shared secret for encrypt/decrypt (POC)
    RDST_LLM_KEYS_DIR: where encrypted keys are stored (default: ~/.rdst/keys)
    LMSTUDIO_BASE_URL: LM Studio server URL (default: http://localhost:1234/v1/chat/completions)

    Public API
   ------
    save_encrypted_api_key(provider, plaintext_key, shared_secret=None) -> Path
    load_api_key(provider, shared_secret=None) -> str  # decrypted key
    query(system_message, user_query, context, max_tokens, temperature, top_p=None, stop_sequences=None, provider=None, model=None, debug=None) -> dict
    """

    def __init__(self, defaults: Optional[Dict[str, Any]] = None, logger: Optional[logging.Logger] = None):
        d = LLMDefaults(**(defaults or {}))

        # Load from config file first, then allow env to override
        try:
            from ..cli.rdst_cli import TargetsConfig
            config = TargetsConfig()
            config.load()
            llm_config = config.get_llm_config()

            # Apply config file settings
            if llm_config.get("provider"):
                d.provider = llm_config["provider"]
            if llm_config.get("model"):
                d.model = llm_config["model"]

            # Store config reference for dynamic lookups
            self._config = config

        except Exception:
            self._config = None

        # Environment variables still take precedence
        d.provider = os.getenv("RDST_LLM_PROVIDER", d.provider)
        d.model = os.getenv("RDST_LMSTUDIO_MODEL", d.model) if d.provider == "lmstudio" else d.model

        self.defaults = d
        self.logger = logger or logging.getLogger("llm_manager")
        self.logger.addHandler(logging.NullHandler())

        # registry llm providers
        self._providers: Dict[str, Provider] = {}
        self.register_provider("openai", OpenAIProvider())
        self.register_provider("claude", ClaudeProvider())
        self.register_provider("lmstudio", LMStudioProvider())
        self.register_provider("gemini", GeminiProvider())

        # key vault (filesystem-based, static-secret encryption in POC)
        keys_dir = Path(os.getenv("RDST_LLM_KEYS_DIR", Path.home() / ".rdst" / "keys"))
        self.vault = LLMKeyVault(keys_dir)

    # Provider registry
    def register_provider(self, name: str, provider: Provider) -> None:
        self._providers[name.lower()] = provider

    def provider(self, name: Optional[str] = None) -> Provider:
        p = (name or self.defaults.provider or "").lower()
        if p not in self._providers:
            raise LLMError(f"Unknown provider '{p}'", code="NO_SUCH_PROVIDER")
        return self._providers[p]

    # Key management (POC)
    def save_encrypted_api_key(self, provider: str, plaintext_key: str, shared_secret: Optional[str] = None) -> Path:
        secret = shared_secret or os.getenv("RDST_LLM_SHARED_KEY", "")
        if not secret:
            raise LLMError("Missing shared secret for key encryption (RDST_LLM_SHARED_KEY)", code="NO_SHARED_SECRET")
        enc = encrypt(plaintext_key.encode("utf-8"), secret)
        path = self.vault.write_encrypted_key(provider, enc)
        return path

    def load_api_key(self, provider: str, shared_secret: Optional[str] = None) -> str:
        secret = shared_secret or os.getenv("RDST_LLM_SHARED_KEY", "")
        if not secret:
            raise LLMError("Missing shared secret for key decryption (RDST_LLM_SHARED_KEY)", code="NO_SHARED_SECRET")
        enc = self.vault.read_encrypted_key(provider)
        if enc is None:
            raise LLMError(f"No API key configured for provider '{provider}'", code="NO_API_KEY")
        try:
            return decrypt(enc, secret).decode("utf-8")
        except Exception as e:
            raise LLMError("Failed to decrypt stored API key (check shared secret)", code="DECRYPT_FAILED", cause=e)

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
        # LM Studio doesn't require an API key for local usage
        if provider == "lmstudio":
            return "not-needed"

        # Check standard environment variables first (no encryption needed)
        env_var_map = {
            "claude": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
        }
        env_var = env_var_map.get(provider)
        if env_var:
            api_key = os.getenv(env_var)
            if api_key:
                # Found API key in environment variable
                return api_key

        # Try to load from encrypted vault
        try:
            return self.load_api_key(provider)
        except LLMError as e:
            if e.code in ("NO_API_KEY", "NO_SHARED_SECRET"):
                # No API key found anywhere
                raise LLMError(
                    f"No API key configured for provider '{provider}'. "
                    f"Set ${env_var} environment variable or run 'rdst configure llm' to save an encrypted key",
                    code="NO_API_KEY"
                )
            raise


def _assemble_messages(system_message: str, user_query: str, context: Optional[str]) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = []
    if system_message:
        msgs.append({"role": "system", "content": system_message})
    if context:
        msgs.append({"role": "user", "content": f"[CONTEXT]\n{context}"})
    msgs.append({"role": "user", "content": user_query})
    return msgs
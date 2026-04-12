"""Shared LLM facade."""

from __future__ import annotations

from shared.llm_manager.claude_provider import AnthropicModel
from shared.llm_manager.trial_display import cents_to_tokens, format_tokens


def create_llm_manager(*args, **kwargs):
    from shared.llm_manager.llm_manager import LLMManager

    return LLMManager(*args, **kwargs)


def resolve_api_key():
    from shared.llm_manager.key_resolution import resolve_api_key as _resolve_api_key

    return _resolve_api_key()


__all__ = [
    "AnthropicModel",
    "create_llm_manager",
    "cents_to_tokens",
    "format_tokens",
    "resolve_api_key",
]

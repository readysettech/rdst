"""
Unit tests for enum name leak in model display (rdst-2vr.14).

The analyze header must show a clean model string like 'claude-sonnet-4-5-20250929',
not the Python enum repr 'AnthropicModel.SONNET_4_5'.
"""

from enum import Enum


class TestDefaultModelReturnType:
    """ClaudeProvider.default_model() must return a plain str, not an enum."""

    def test_default_model_is_plain_str(self):
        """default_model() should return a plain string, not an AnthropicModel member."""
        from lib.llm_manager.claude_provider import ClaudeProvider

        provider = ClaudeProvider()
        model = provider.default_model()

        # Must be a str
        assert isinstance(model, str)
        # Must NOT be an Enum member
        assert not isinstance(model, Enum), (
            f"default_model() returned {type(model).__name__}.{model.name} — "
            f"should return a plain str like '{model.value}'"
        )

    def test_default_model_starts_with_claude(self):
        """default_model() value should be a real model ID, not an enum repr."""
        from lib.llm_manager.claude_provider import ClaudeProvider

        provider = ClaudeProvider()
        model = provider.default_model()

        assert model.startswith("claude-"), (
            f"default_model() returned '{model}' — expected 'claude-...' model ID"
        )


class TestStringifyEnum:
    """Workflow _stringify() must produce the enum value, not its repr."""

    def test_stringify_str_enum_uses_value(self):
        """_stringify(AnthropicModel.SONNET_4_5) must return the value string."""
        from lib.workflow_manager.workflow_manager import _stringify
        from lib.llm_manager.claude_provider import AnthropicModel

        result = _stringify(AnthropicModel.SONNET_4_5)

        assert "AnthropicModel" not in result, (
            f"_stringify leaked enum name: '{result}'"
        )
        assert result == AnthropicModel.SONNET_4_5.value

    def test_stringify_plain_str_unchanged(self):
        """_stringify on a plain string should return it unchanged."""
        from lib.workflow_manager.workflow_manager import _stringify

        assert _stringify("claude-sonnet-4-5") == "claude-sonnet-4-5"

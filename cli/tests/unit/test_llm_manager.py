"""
Unit tests for LLM manager base module.

Tests base provider infrastructure (LLMError, LLMDefaults, Conversation, etc.)
"""

import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from shared.llm_manager import base

# Import classes
LLMError = base.LLMError
LLMDefaults = base.LLMDefaults
ProviderMessage = base.ProviderMessage
ProviderRequest = base.ProviderRequest
ProviderResponse = base.ProviderResponse
Conversation = base.Conversation


class TestLLMError:
    """Tests for LLMError exception class."""

    def test_basic_error(self):
        """Test creating basic error."""
        error = LLMError("Something went wrong")
        assert str(error) == "Something went wrong"
        assert error.code == "LLM_ERROR"
        assert error.status is None
        assert error.cause is None

    def test_error_with_code(self):
        """Test error with custom code."""
        error = LLMError("No key", code="NO_API_KEY")
        assert error.code == "NO_API_KEY"

    def test_error_with_status(self):
        """Test error with HTTP status."""
        error = LLMError("Unauthorized", code="AUTH_ERROR", status=401)
        assert error.status == 401

    def test_error_with_cause(self):
        """Test error with underlying cause."""
        original = ValueError("original error")
        error = LLMError("Wrapped", cause=original)
        assert error.cause is original


class TestLLMDefaults:
    """Tests for LLMDefaults dataclass."""

    def test_default_values(self):
        """Test default values are set."""
        defaults = LLMDefaults()

        # RDST now uses Claude exclusively (BYOK)
        assert defaults.provider == "claude"
        assert defaults.model is None
        assert defaults.max_tokens == 800
        assert defaults.temperature == 0.2
        assert defaults.top_p is None
        assert defaults.stop_sequences is None
        assert defaults.debug is False

    def test_custom_values(self):
        """Test custom values are applied."""
        defaults = LLMDefaults(
            provider="claude", model="claude-3-opus", max_tokens=2000, temperature=0.7
        )

        assert defaults.provider == "claude"
        assert defaults.model == "claude-3-opus"
        assert defaults.max_tokens == 2000
        assert defaults.temperature == 0.7


class TestProviderMessage:
    """Tests for ProviderMessage dataclass."""

    def test_user_message(self):
        """Test creating user message."""
        msg = ProviderMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_system_message(self):
        """Test creating system message."""
        msg = ProviderMessage(role="system", content="You are helpful")
        assert msg.role == "system"
        assert msg.content == "You are helpful"


class TestProviderRequest:
    """Tests for ProviderRequest dataclass."""

    def test_basic_request(self):
        """Test creating basic request."""
        messages = [ProviderMessage(role="user", content="Hi")]
        request = ProviderRequest(model="gpt-4", messages=messages)

        assert request.model == "gpt-4"
        assert len(request.messages) == 1
        assert request.temperature == 0.2  # Default

    def test_as_chat_dicts(self):
        """Test converting to chat dictionaries."""
        messages = [
            ProviderMessage(role="system", content="Be helpful"),
            ProviderMessage(role="user", content="Hi"),
        ]
        request = ProviderRequest(model="gpt-4", messages=messages)

        dicts = request.as_chat_dicts()

        assert len(dicts) == 2
        assert dicts[0] == {"role": "system", "content": "Be helpful"}
        assert dicts[1] == {"role": "user", "content": "Hi"}


class TestProviderResponse:
    """Tests for ProviderResponse dataclass."""

    def test_basic_response(self):
        """Test creating basic response."""
        response = ProviderResponse(text="Hello there!")
        assert response.text == "Hello there!"
        assert response.usage is None
        assert response.raw is None

    def test_response_with_usage(self):
        """Test response with token usage."""
        usage = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
        response = ProviderResponse(text="Response", usage=usage)

        assert response.usage["total_tokens"] == 30


class TestConversation:
    """Tests for Conversation class."""

    @pytest.fixture
    def mock_provider(self):
        """Create a mock provider."""
        provider = MagicMock()
        provider.default_model.return_value = "default-model"
        provider.complete.return_value = ProviderResponse(
            text="AI response", usage={"total_tokens": 50}
        )
        return provider

    def test_conversation_creation(self, mock_provider):
        """Test creating conversation."""
        conv = Conversation(provider=mock_provider, api_key="test-key")

        assert conv.provider == mock_provider
        assert conv.api_key == "test-key"
        assert conv.messages == []

    def test_add_messages(self, mock_provider):
        """Test adding messages."""
        conv = Conversation(provider=mock_provider, api_key="key")

        conv.system("You are helpful")
        conv.user("Hi")
        conv.assistant("Hello!")

        assert len(conv.messages) == 3
        assert conv.messages[0].role == "system"
        assert conv.messages[1].role == "user"
        assert conv.messages[2].role == "assistant"

    def test_reset(self, mock_provider):
        """Test resetting conversation."""
        conv = Conversation(provider=mock_provider, api_key="key")
        conv.user("Message 1")
        conv.user("Message 2")

        conv.reset()

        assert conv.messages == []

    def test_with_model(self, mock_provider):
        """Test creating conversation with different model."""
        conv = Conversation(provider=mock_provider, api_key="key", model="model-a")
        new_conv = conv.with_model("model-b")

        assert new_conv.model == "model-b"
        assert conv.model == "model-a"  # Original unchanged

    def test_complete_calls_provider(self, mock_provider):
        """Test complete calls provider."""
        conv = Conversation(provider=mock_provider, api_key="test-key")
        conv.system("Be helpful")
        conv.user("Hello")

        response = conv.complete()

        assert response.text == "AI response"
        assert mock_provider.complete.called

    def test_complete_adds_assistant_message(self, mock_provider):
        """Test complete adds assistant response to history."""
        conv = Conversation(provider=mock_provider, api_key="test-key")
        conv.user("Hello")

        conv.complete()

        assert len(conv.messages) == 2
        assert conv.messages[1].role == "assistant"
        assert conv.messages[1].content == "AI response"


class TestStreamingBridge:
    def test_preserves_llm_error_metadata_without_thread_exception(self, tmp_rdst_home):
        from shared.llm_manager.llm_manager import LLMManager

        failure = LLMError(
            "Anthropic rejected this request",
            code="ANTHROPIC_INVALID_REQUEST",
            status=400,
            request_id="req_test",
        )
        provider = MagicMock()
        provider.default_model.return_value = "claude-sonnet-4-6"
        provider.stream.side_effect = failure

        manager = LLMManager(defaults={"model": "claude-sonnet-4-6"})
        manager.register_provider("claude", provider)
        thread_errors = []
        previous_hook = threading.excepthook
        threading.excepthook = lambda args: thread_errors.append(args.exc_value)

        async def consume():
            async for _token in manager.query_stream(
                system_message="system",
                user_query="question",
                api_key="test-key",
            ):
                pass

        try:
            with pytest.raises(LLMError) as raised:
                asyncio.run(consume())
        finally:
            threading.excepthook = previous_hook

        assert raised.value is failure
        assert raised.value.status == 400
        assert raised.value.request_id == "req_test"
        assert thread_errors == []

    def test_history_is_sent_before_current_question(self, tmp_rdst_home):
        from shared.llm_manager.llm_manager import LLMManager

        captured = []
        provider = MagicMock()
        provider.default_model.return_value = "claude-sonnet-4-6"

        def stream(request, **_kwargs):
            captured.extend(request.messages)
            yield "ok"

        provider.stream = stream
        manager = LLMManager(defaults={"model": "claude-sonnet-4-6"})
        manager.register_provider("claude", provider)

        async def consume():
            return [
                token
                async for token in manager.query_stream(
                    system_message="system",
                    user_query="follow up",
                    history=[
                        {"role": "user", "content": "first question"},
                        {"role": "assistant", "content": "first answer"},
                    ],
                    api_key="test-key",
                )
            ]

        assert asyncio.run(consume()) == ["ok"]
        assert captured == [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "follow up"},
        ]

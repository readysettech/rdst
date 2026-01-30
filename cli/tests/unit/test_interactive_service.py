"""
Unit tests for InteractiveService.

Tests the interactive analysis conversation service including message sending,
conversation persistence, history retrieval, and error handling.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Import from lib package (conftest.py adds rdst root to path)
from lib.services.interactive_service import (
    InteractiveService,
    _get_interactive_mode_prompt,
)
from lib.services.types import (
    ChunkEvent,
    MessageEvent,
    InteractiveCompleteEvent,
    InteractiveErrorEvent,
    InteractiveEvent,
)


class TestInteractiveEvent:
    """Tests for InteractiveEvent types."""

    def test_message_event(self):
        """Test creating message event."""
        event = MessageEvent(type="message", text="Hello")
        assert event.type == "message"
        assert event.text == "Hello"

    def test_complete_event(self):
        """Test creating complete event."""
        event = InteractiveCompleteEvent(type="complete", conversation_id="conv_123")
        assert event.type == "complete"
        assert event.conversation_id == "conv_123"

    def test_error_event(self):
        """Test creating error event."""
        event = InteractiveErrorEvent(type="error", error="Something went wrong")
        assert event.type == "error"
        assert event.error == "Something went wrong"


class TestInteractiveModePrompt:
    """Tests for the interactive mode prompt."""

    def test_prompt_contains_key_sections(self):
        """Test prompt contains expected sections."""
        prompt = _get_interactive_mode_prompt()

        assert "INTERACTIVE MODE ACTIVATED" in prompt
        assert "COMMUNICATION STYLE" in prompt
        assert "CRITICAL" in prompt
        assert "YOU CAN" in prompt
        assert "YOU CANNOT" in prompt
        assert "BOUNDARIES" in prompt

    def test_prompt_is_string(self):
        """Test prompt is a non-empty string."""
        prompt = _get_interactive_mode_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 100  # Should be substantial


class TestInteractiveServiceInit:
    """Tests for InteractiveService initialization."""

    def test_initialization_default(self):
        """Test service initializes with defaults."""
        service = InteractiveService()
        assert service.conv_registry is not None
        assert service.llm_manager is not None

    def test_initialization_with_custom_registry(self):
        """Test service initializes with custom registry."""
        mock_registry = Mock()
        service = InteractiveService(conv_registry=mock_registry)
        assert service.conv_registry is mock_registry

    def test_initialization_with_custom_llm_manager(self):
        """Test service initializes with custom LLM manager."""
        mock_llm = Mock()
        service = InteractiveService(llm_manager=mock_llm)
        assert service.llm_manager is mock_llm

    def test_default_provider_and_model(self):
        """Test default provider and model constants."""
        assert InteractiveService.DEFAULT_PROVIDER == "claude"
        assert "claude" in InteractiveService.DEFAULT_MODEL.lower()


class TestInteractiveServiceSendMessage:
    """Tests for send_message method."""

    @pytest.fixture
    def mock_registry(self):
        """Create mock ConversationRegistry."""
        registry = Mock()
        registry.conversation_exists.return_value = False
        registry.delete_conversation.return_value = True
        return registry

    @pytest.fixture
    def mock_llm_manager(self):
        """Create mock LLMManager with streaming support."""
        llm = Mock()

        # Mock query_stream as an async generator
        async def mock_stream(*args, **kwargs):
            for token in ["AI ", "response ", "text"]:
                yield token

        llm.query_stream = mock_stream
        return llm

    @pytest.fixture
    def mock_conversation(self):
        """Create mock InteractiveConversation."""
        conv = Mock()
        conv.conversation_id = "conv_123"
        # Use a real list for messages to support pop()
        conv.messages = []
        conv.add_message = Mock(
            side_effect=lambda role, content: conv.messages.append(
                Mock(role=role, content=content)
            )
        )
        conv.add_exchange = Mock()
        conv.get_messages_for_llm = Mock(
            return_value=[
                {"role": "system", "content": "System prompt"},
                {"role": "user", "content": "Hello"},
            ]
        )
        return conv

    @pytest.fixture
    def service(self, mock_registry, mock_llm_manager):
        """Create InteractiveService with mocks."""
        return InteractiveService(
            conv_registry=mock_registry,
            llm_manager=mock_llm_manager,
        )

    @pytest.mark.asyncio
    async def test_send_message_new_conversation(
        self, service, mock_registry, mock_conversation
    ):
        """Test sending message creates new conversation."""
        mock_registry.create_conversation.return_value = mock_conversation
        mock_registry.load_conversation.return_value = None

        events = []
        async for event in service.send_message(
            query_hash="test_hash",
            message="What about this query?",
            analysis_results={"query_sql": "SELECT 1"},
        ):
            events.append(event)

        # Should have chunk events and complete event
        chunk_events = [e for e in events if e.type == "chunk"]
        complete_events = [e for e in events if e.type == "complete"]

        assert len(chunk_events) == 3  # "AI ", "response ", "text"
        assert len(complete_events) == 1
        assert complete_events[0].conversation_id == "conv_123"

        # Verify conversation was saved
        mock_registry.save_conversation.assert_called()

    @pytest.mark.asyncio
    async def test_send_message_continue_existing(
        self, service, mock_registry, mock_conversation
    ):
        """Test sending message continues existing conversation."""
        mock_registry.conversation_exists.return_value = True
        mock_registry.load_conversation.return_value = mock_conversation

        # Add interactive mode message to existing conversation
        mock_msg = Mock()
        mock_msg.role = "system"
        mock_msg.content = "INTERACTIVE MODE ACTIVATED"
        mock_conversation.messages = [mock_msg]

        events = []
        async for event in service.send_message(
            query_hash="test_hash",
            message="Follow-up question",
            analysis_results={},
            continue_existing=True,
        ):
            events.append(event)

        chunk_events = [e for e in events if e.type == "chunk"]
        assert len(chunk_events) == 3
        # Should not create new conversation
        mock_registry.create_conversation.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_message_fresh_start(
        self, service, mock_registry, mock_conversation
    ):
        """Test sending message with continue_existing=False deletes old."""
        mock_registry.create_conversation.return_value = mock_conversation
        mock_registry.load_conversation.return_value = None

        events = []
        async for event in service.send_message(
            query_hash="test_hash",
            message="Start fresh",
            analysis_results={},
            continue_existing=False,
        ):
            events.append(event)

        # Should delete existing conversation
        mock_registry.delete_conversation.assert_called_with("test_hash", "claude")

    @pytest.mark.asyncio
    async def test_send_message_llm_failure(
        self, service, mock_registry, mock_conversation
    ):
        """Test error event when LLM stream fails."""
        mock_registry.create_conversation.return_value = mock_conversation
        mock_registry.load_conversation.return_value = None

        # Mock LLM to raise exception during streaming
        async def failing_stream(*args, **kwargs):
            raise Exception("LLM API error")
            yield  # Make it a generator

        service.llm_manager.query_stream = failing_stream

        events = []
        async for event in service.send_message(
            query_hash="test_hash",
            message="Question",
            analysis_results={},
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == "error"
        assert "LLM API error" in events[0].error

    @pytest.mark.asyncio
    async def test_send_message_exception(self, service, mock_registry):
        """Test error event when exception occurs."""
        mock_registry.load_conversation.side_effect = Exception("DB error")

        events = []
        async for event in service.send_message(
            query_hash="test_hash",
            message="Question",
            analysis_results={},
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == "error"
        assert "DB error" in events[0].error


class TestInteractiveServiceConversationStatus:
    """Tests for get_conversation_status method."""

    @pytest.fixture
    def mock_registry(self):
        """Create mock ConversationRegistry."""
        return Mock()

    @pytest.fixture
    def service(self, mock_registry):
        """Create InteractiveService with mock registry."""
        return InteractiveService(conv_registry=mock_registry)

    def test_status_not_exists(self, service, mock_registry):
        """Test status when conversation doesn't exist."""
        mock_registry.conversation_exists.return_value = False

        status = service.get_conversation_status("test_hash")

        assert status == {"exists": False}

    def test_status_exists_but_load_fails(self, service, mock_registry):
        """Test status when conversation exists but load fails."""
        mock_registry.conversation_exists.return_value = True
        mock_registry.load_conversation.return_value = None

        status = service.get_conversation_status("test_hash")

        assert status == {"exists": False}

    def test_status_exists_with_data(self, service, mock_registry):
        """Test status when conversation exists with data."""
        mock_registry.conversation_exists.return_value = True

        mock_conv = Mock()
        mock_conv.conversation_id = "conv_123"
        mock_conv.messages = [Mock(), Mock(), Mock()]
        mock_conv.total_exchanges = 5
        mock_conv.started_at = "2024-01-01T10:00:00"
        mock_conv.last_updated = "2024-01-01T11:00:00"
        mock_conv.provider = "claude"
        mock_conv.model = "claude-sonnet-4-20250514"

        mock_registry.load_conversation.return_value = mock_conv

        status = service.get_conversation_status("test_hash")

        assert status["exists"] is True
        assert status["conversation_id"] == "conv_123"
        assert status["message_count"] == 3
        assert status["total_exchanges"] == 5
        assert status["started_at"] == "2024-01-01T10:00:00"
        assert status["provider"] == "claude"


class TestInteractiveServiceConversationHistory:
    """Tests for get_conversation_history method."""

    @pytest.fixture
    def mock_registry(self):
        """Create mock ConversationRegistry."""
        return Mock()

    @pytest.fixture
    def service(self, mock_registry):
        """Create InteractiveService with mock registry."""
        return InteractiveService(conv_registry=mock_registry)

    def test_history_not_exists(self, service, mock_registry):
        """Test history when conversation doesn't exist."""
        mock_registry.conversation_exists.return_value = False

        history = service.get_conversation_history("test_hash")

        assert history == []

    def test_history_load_fails(self, service, mock_registry):
        """Test history when load fails."""
        mock_registry.conversation_exists.return_value = True
        mock_registry.load_conversation.return_value = None

        history = service.get_conversation_history("test_hash")

        assert history == []

    def test_history_with_messages(self, service, mock_registry):
        """Test history returns formatted messages."""
        mock_registry.conversation_exists.return_value = True

        # Create mock messages
        mock_msg1 = Mock()
        mock_msg1.role = "user"
        mock_msg1.content = "Question 1"
        mock_msg1.timestamp = "2024-01-01T10:00:00"

        mock_msg2 = Mock()
        mock_msg2.role = "assistant"
        mock_msg2.content = "Answer 1"
        mock_msg2.timestamp = "2024-01-01T10:00:05"

        mock_conv = Mock()
        mock_conv.get_user_assistant_messages.return_value = [mock_msg1, mock_msg2]

        mock_registry.load_conversation.return_value = mock_conv

        history = service.get_conversation_history("test_hash")

        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Question 1"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "Answer 1"


class TestInteractiveServiceDeleteConversation:
    """Tests for delete_conversation method."""

    @pytest.fixture
    def mock_registry(self):
        """Create mock ConversationRegistry."""
        return Mock()

    @pytest.fixture
    def service(self, mock_registry):
        """Create InteractiveService with mock registry."""
        return InteractiveService(conv_registry=mock_registry)

    def test_delete_success(self, service, mock_registry):
        """Test successful deletion."""
        mock_registry.delete_conversation.return_value = True

        result = service.delete_conversation("test_hash")

        assert result is True
        mock_registry.delete_conversation.assert_called_with("test_hash", "claude")

    def test_delete_not_found(self, service, mock_registry):
        """Test deletion when not found."""
        mock_registry.delete_conversation.return_value = False

        result = service.delete_conversation("test_hash")

        assert result is False


class TestInteractiveServiceLoadOrCreateConversation:
    """Tests for _load_or_create_conversation method."""

    @pytest.fixture
    def mock_registry(self):
        """Create mock ConversationRegistry."""
        return Mock()

    @pytest.fixture
    def service(self, mock_registry):
        """Create InteractiveService with mock registry."""
        return InteractiveService(conv_registry=mock_registry)

    def test_create_new_conversation(self, service, mock_registry):
        """Test creating new conversation."""
        mock_conv = Mock()
        mock_conv.messages = []
        mock_conv.add_message = Mock()
        mock_registry.load_conversation.return_value = None
        mock_registry.create_conversation.return_value = mock_conv

        result = service._load_or_create_conversation(
            query_hash="test_hash",
            analysis_results={"query_sql": "SELECT 1", "analysis_id": "a123"},
            continue_existing=True,
        )

        assert result is mock_conv
        mock_registry.create_conversation.assert_called_once()
        # Should add system messages
        assert mock_conv.add_message.call_count >= 1

    def test_load_existing_conversation(self, service, mock_registry):
        """Test loading existing conversation."""
        mock_msg = Mock()
        mock_msg.role = "system"
        mock_msg.content = "INTERACTIVE MODE ACTIVATED"

        mock_conv = Mock()
        mock_conv.messages = [mock_msg]
        mock_registry.load_conversation.return_value = mock_conv

        result = service._load_or_create_conversation(
            query_hash="test_hash",
            analysis_results={},
            continue_existing=True,
        )

        assert result is mock_conv
        mock_registry.create_conversation.assert_not_called()

    def test_load_existing_adds_interactive_prompt_if_missing(
        self, service, mock_registry
    ):
        """Test that interactive prompt is added if missing."""
        mock_msg = Mock()
        mock_msg.role = "system"
        mock_msg.content = "Some other system message"

        mock_conv = Mock()
        mock_conv.messages = [mock_msg]
        mock_conv.add_message = Mock()
        mock_registry.load_conversation.return_value = mock_conv

        result = service._load_or_create_conversation(
            query_hash="test_hash",
            analysis_results={},
            continue_existing=True,
        )

        # Should add interactive mode prompt
        mock_conv.add_message.assert_called()
        call_args = mock_conv.add_message.call_args
        assert call_args[0][0] == "system"
        assert "INTERACTIVE MODE ACTIVATED" in call_args[0][1]


class TestInteractiveServiceHasInteractiveModeMessage:
    """Tests for _has_interactive_mode_message method."""

    @pytest.fixture
    def service(self):
        """Create InteractiveService."""
        return InteractiveService(
            conv_registry=Mock(),
            llm_manager=Mock(),
        )

    def test_has_message_true(self, service):
        """Test returns True when message exists."""
        mock_msg = Mock()
        mock_msg.role = "system"
        mock_msg.content = "INTERACTIVE MODE ACTIVATED\nMore content..."

        mock_conv = Mock()
        mock_conv.messages = [mock_msg]

        result = service._has_interactive_mode_message(mock_conv)
        assert result is True

    def test_has_message_false(self, service):
        """Test returns False when message doesn't exist."""
        mock_msg = Mock()
        mock_msg.role = "system"
        mock_msg.content = "Some other system message"

        mock_conv = Mock()
        mock_conv.messages = [mock_msg]

        result = service._has_interactive_mode_message(mock_conv)
        assert result is False

    def test_has_message_empty(self, service):
        """Test returns False when no messages."""
        mock_conv = Mock()
        mock_conv.messages = []

        result = service._has_interactive_mode_message(mock_conv)
        assert result is False


class TestInteractiveServiceBuildAnalysisContextPrompt:
    """Tests for _build_analysis_context_prompt method."""

    @pytest.fixture
    def service(self):
        """Create InteractiveService."""
        return InteractiveService(
            conv_registry=Mock(),
            llm_manager=Mock(),
        )

    def test_empty_results(self, service):
        """Test returns None for empty results."""
        result = service._build_analysis_context_prompt({})
        assert result is None

    def test_with_query_sql(self, service):
        """Test includes query SQL."""
        result = service._build_analysis_context_prompt(
            {"query_sql": "SELECT * FROM users"}
        )

        assert result is not None
        assert "SELECT * FROM users" in result
        assert "QUERY:" in result

    def test_with_explain_results(self, service):
        """Test includes explain results."""
        result = service._build_analysis_context_prompt(
            {
                "explain_results": {
                    "execution_time_ms": 15.5,
                    "rows_examined": 1000,
                    "rows_returned": 10,
                }
            }
        )

        assert result is not None
        assert "PERFORMANCE METRICS" in result
        assert "15.5ms" in result
        assert "1,000" in result

    def test_with_llm_analysis(self, service):
        """Test includes LLM analysis."""
        result = service._build_analysis_context_prompt(
            {
                "llm_analysis": {
                    "index_recommendations": [
                        {"sql": "CREATE INDEX idx_users_email ON users(email)"}
                    ],
                    "rewrite_suggestions": [{"description": "Use LIMIT clause"}],
                }
            }
        )

        assert result is not None
        assert "INDEX RECOMMENDATIONS" in result
        assert "CREATE INDEX" in result
        assert "QUERY REWRITES" in result
        assert "Use LIMIT" in result

    def test_with_no_rewrites(self, service):
        """Test shows 'None recommended' when no rewrites."""
        result = service._build_analysis_context_prompt(
            {
                "llm_analysis": {
                    "rewrite_suggestions": [],
                }
            }
        )

        assert result is not None
        assert "None recommended" in result


class TestInteractiveServiceCallLLMSync:
    """Tests for _call_llm_sync method."""

    @pytest.fixture
    def mock_llm_manager(self):
        """Create mock LLMManager."""
        llm = Mock()
        llm.query.return_value = {"text": "LLM response"}
        return llm

    @pytest.fixture
    def service(self, mock_llm_manager):
        """Create InteractiveService with mock LLM."""
        return InteractiveService(
            conv_registry=Mock(),
            llm_manager=mock_llm_manager,
        )

    @pytest.fixture
    def mock_conversation(self):
        """Create mock conversation."""
        conv = Mock()
        # Use a real list that we can track
        messages_list = []
        conv.messages = messages_list
        conv.add_message = Mock(
            side_effect=lambda role, content: messages_list.append(
                Mock(role=role, content=content)
            )
        )
        conv.get_messages_for_llm = Mock(
            return_value=[
                {"role": "system", "content": "System prompt 1"},
                {"role": "system", "content": "System prompt 2"},
            ]
        )
        return conv

    def test_call_llm_success(self, service, mock_llm_manager, mock_conversation):
        """Test successful LLM call."""
        result = service._call_llm_sync(mock_conversation, "User question")

        assert result == "LLM response"
        mock_llm_manager.query.assert_called_once()

        # Verify system messages were combined
        call_kwargs = mock_llm_manager.query.call_args.kwargs
        assert "System prompt 1" in call_kwargs["system_message"]
        assert "System prompt 2" in call_kwargs["system_message"]

    def test_call_llm_removes_temp_message(
        self, service, mock_llm_manager, mock_conversation
    ):
        """Test that temporary user message is removed after call."""
        initial_count = len(mock_conversation.messages)

        service._call_llm_sync(mock_conversation, "User question")

        # Message was added then removed, so count should be same
        assert len(mock_conversation.messages) == initial_count

    def test_call_llm_no_text_in_response(
        self, service, mock_llm_manager, mock_conversation
    ):
        """Test fallback when response has no text."""
        mock_llm_manager.query.return_value = {}

        result = service._call_llm_sync(mock_conversation, "User question")

        assert "Sorry" in result
        assert "try again" in result

    def test_call_llm_exception(self, service, mock_llm_manager, mock_conversation):
        """Test returns None on exception."""
        mock_llm_manager.query.side_effect = Exception("API error")

        result = service._call_llm_sync(mock_conversation, "User question")

        assert result is None

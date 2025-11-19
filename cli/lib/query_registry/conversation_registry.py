"""
Conversation Registry for Interactive Analysis Sessions

Stores full conversation history separately from analysis results TOML.
Conversations are tied to query_hash + LLM provider (since messages are provider-specific).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any


@dataclass
class ConversationMessage:
    """Single message in a conversation."""
    role: str  # "user", "assistant", or "system"
    content: str
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ConversationMessage':
        return cls(**data)


@dataclass
class InteractiveConversation:
    """
    Full conversation history for an interactive analysis session.

    Tied to query_hash + provider (e.g., abc123def456_claude)
    because LLM message formats are provider-specific.
    """
    conversation_id: str  # format: {query_hash}_{provider}
    query_hash: str
    provider: str  # "claude", "openai", "lmstudio"
    model: str  # "claude-3-5-sonnet-latest", etc.

    # Analysis context
    analysis_id: str  # Which analysis triggered this conversation
    target: str
    query_sql: str  # Original query

    # Conversation history
    messages: List[ConversationMessage]

    # Metadata
    started_at: str
    last_updated: str
    total_exchanges: int = 0  # Number of Q&A pairs (user asks, assistant responds)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "conversation_id": self.conversation_id,
            "query_hash": self.query_hash,
            "provider": self.provider,
            "model": self.model,
            "analysis_id": self.analysis_id,
            "target": self.target,
            "query_sql": self.query_sql,
            "messages": [msg.to_dict() for msg in self.messages],
            "started_at": self.started_at,
            "last_updated": self.last_updated,
            "total_exchanges": self.total_exchanges
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'InteractiveConversation':
        """Create from dict (JSON deserialization)."""
        messages_data = data.pop('messages', [])
        messages = [ConversationMessage.from_dict(msg) for msg in messages_data]

        # Handle backward compatibility
        for key, default in [
            ('total_exchanges', 0)
        ]:
            if key not in data:
                data[key] = default

        return cls(messages=messages, **data)

    def add_message(self, role: str, content: str) -> None:
        """Add a single message to the conversation."""
        timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        self.messages.append(ConversationMessage(role, content, timestamp))
        self.last_updated = timestamp

    def add_exchange(self, user_message: str, assistant_message: str) -> None:
        """Add a Q&A exchange to the conversation."""
        timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        self.messages.append(ConversationMessage("user", user_message, timestamp))
        self.messages.append(ConversationMessage("assistant", assistant_message, timestamp))
        self.total_exchanges += 1
        self.last_updated = timestamp

    def get_messages_for_llm(self) -> List[Dict[str, str]]:
        """
        Get messages in LLM API format (list of dicts with role and content).

        Returns:
            List of message dicts suitable for LLM API calls
        """
        return [{"role": msg.role, "content": msg.content} for msg in self.messages]

    def get_user_assistant_messages(self) -> List[ConversationMessage]:
        """
        Get only user and assistant messages (filter out system messages).
        Useful for displaying conversation history to users.

        Returns:
            List of ConversationMessage objects (only user and assistant roles)
        """
        return [msg for msg in self.messages if msg.role in ["user", "assistant"]]


class ConversationRegistry:
    """
    Registry for storing and retrieving interactive conversation history.

    Stores full conversations in JSON files:
    ~/.rdst/conversations/{query_hash}_{provider}.json
    """

    def __init__(self, conversations_dir: Optional[str] = None):
        """
        Initialize conversation registry.

        Args:
            conversations_dir: Custom directory for conversations.
                             Defaults to ~/.rdst/conversations/
        """
        if conversations_dir:
            self.conversations_dir = Path(conversations_dir)
        else:
            self.conversations_dir = Path.home() / ".rdst" / "conversations"

        self._ensure_directory()

    def _ensure_directory(self) -> None:
        """Ensure conversations directory exists."""
        self.conversations_dir.mkdir(parents=True, exist_ok=True)

    def _get_conversation_path(self, conversation_id: str) -> Path:
        """Get path to conversation JSON file."""
        return self.conversations_dir / f"{conversation_id}.json"

    def conversation_exists(self, query_hash: str, provider: str) -> bool:
        """Check if a conversation exists for this query + provider."""
        conversation_id = f"{query_hash}_{provider}"
        return self._get_conversation_path(conversation_id).exists()

    def load_conversation(self, query_hash: str, provider: str) -> Optional[InteractiveConversation]:
        """
        Load existing conversation for query + provider.

        Args:
            query_hash: Hash of the query
            provider: LLM provider name ("claude", "openai", etc.)

        Returns:
            InteractiveConversation or None if not found
        """
        conversation_id = f"{query_hash}_{provider}"
        path = self._get_conversation_path(conversation_id)

        if not path.exists():
            return None

        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return InteractiveConversation.from_dict(data)
        except Exception as e:
            print(f"Warning: Could not load conversation {conversation_id}: {e}")
            return None

    def save_conversation(self, conversation: InteractiveConversation) -> None:
        """
        Save conversation to JSON file.

        Args:
            conversation: InteractiveConversation to save
        """
        self._ensure_directory()
        path = self._get_conversation_path(conversation.conversation_id)

        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(conversation.to_dict(), f, indent=2, ensure_ascii=False)
        except Exception as e:
            raise RuntimeError(f"Failed to save conversation {conversation.conversation_id}: {e}")

    def create_conversation(self, query_hash: str, provider: str, model: str,
                          analysis_id: str, target: str, query_sql: str) -> InteractiveConversation:
        """
        Create a new conversation.

        Args:
            query_hash: Hash of the query
            provider: LLM provider name
            model: LLM model name
            analysis_id: ID of the analysis that triggered this conversation
            target: Database target name
            query_sql: Original SQL query

        Returns:
            New InteractiveConversation instance (not yet saved)
        """
        conversation_id = f"{query_hash}_{provider}"
        timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        conversation = InteractiveConversation(
            conversation_id=conversation_id,
            query_hash=query_hash,
            provider=provider,
            model=model,
            analysis_id=analysis_id,
            target=target,
            query_sql=query_sql,
            messages=[],
            started_at=timestamp,
            last_updated=timestamp,
            total_exchanges=0
        )

        return conversation

    def delete_conversation(self, query_hash: str, provider: str) -> bool:
        """
        Delete a conversation.

        Args:
            query_hash: Hash of the query
            provider: LLM provider name

        Returns:
            True if deleted, False if not found
        """
        conversation_id = f"{query_hash}_{provider}"
        path = self._get_conversation_path(conversation_id)

        if path.exists():
            path.unlink()
            return True
        return False

    def list_conversations(self, query_hash: Optional[str] = None) -> List[str]:
        """
        List all conversation IDs, optionally filtered by query hash.

        Args:
            query_hash: Optional query hash to filter by

        Returns:
            List of conversation IDs
        """
        self._ensure_directory()

        conversation_ids = []
        for path in self.conversations_dir.glob("*.json"):
            conversation_id = path.stem
            if query_hash is None or conversation_id.startswith(query_hash):
                conversation_ids.append(conversation_id)

        return sorted(conversation_ids)

    def get_conversation_metadata(self, query_hash: str, provider: str) -> Optional[Dict[str, Any]]:
        """
        Get metadata about a conversation without loading full message history.

        Args:
            query_hash: Hash of the query
            provider: LLM provider name

        Returns:
            Dict with metadata or None if not found
        """
        conversation = self.load_conversation(query_hash, provider)
        if not conversation:
            return None

        return {
            "conversation_id": conversation.conversation_id,
            "query_hash": conversation.query_hash,
            "provider": conversation.provider,
            "model": conversation.model,
            "analysis_id": conversation.analysis_id,
            "target": conversation.target,
            "started_at": conversation.started_at,
            "last_updated": conversation.last_updated,
            "total_exchanges": conversation.total_exchanges,
            "message_count": len(conversation.messages)
        }

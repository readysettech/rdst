"""
Unit tests for lib/agent/conversation.py

Tests ConversationTurn and ConversationSession classes.
"""

from __future__ import annotations

import pytest
from dataclasses import dataclass

from lib.agent.conversation import ConversationTurn, ConversationSession


class TestConversationTurn:
    """Tests for ConversationTurn dataclass."""

    def test_minimal_turn(self):
        """Test creating turn with only question."""
        turn = ConversationTurn(question="How many users?")
        assert turn.question == "How many users?"
        assert turn.sql is None
        assert turn.result_summary == ""
        assert turn.timestamp  # Should have default timestamp

    def test_full_turn(self):
        """Test creating turn with all fields."""
        turn = ConversationTurn(
            question="How many users?",
            sql="SELECT COUNT(*) FROM users",
            result_summary="1 row: count",
            timestamp="2025-01-10T10:00:00",
        )
        assert turn.question == "How many users?"
        assert turn.sql == "SELECT COUNT(*) FROM users"
        assert turn.result_summary == "1 row: count"
        assert turn.timestamp == "2025-01-10T10:00:00"

    def test_timestamp_auto_generated(self):
        """Test that timestamp is auto-generated if not provided."""
        turn1 = ConversationTurn(question="Q1")
        turn2 = ConversationTurn(question="Q2")
        # Both should have timestamps
        assert turn1.timestamp
        assert turn2.timestamp


class TestConversationSession:
    """Tests for ConversationSession class."""

    def test_empty_session(self):
        """Test creating empty session."""
        session = ConversationSession(agent_name="test-agent")
        assert session.agent_name == "test-agent"
        assert session.turns == []
        assert session.max_turns == 10

    def test_custom_max_turns(self):
        """Test session with custom max_turns."""
        session = ConversationSession(agent_name="test", max_turns=5)
        assert session.max_turns == 5

    def test_add_turn(self):
        """Test adding turns to session."""
        session = ConversationSession(agent_name="test")
        session.add_turn(ConversationTurn(question="Q1"))
        session.add_turn(ConversationTurn(question="Q2"))
        assert len(session.turns) == 2
        assert session.turns[0].question == "Q1"
        assert session.turns[1].question == "Q2"

    def test_prunes_old_turns(self):
        """Test that old turns are pruned when max_turns exceeded."""
        session = ConversationSession(agent_name="test", max_turns=3)

        for i in range(5):
            session.add_turn(ConversationTurn(question=f"Q{i}"))

        assert len(session.turns) == 3
        # Should keep Q2, Q3, Q4 (last 3)
        assert session.turns[0].question == "Q2"
        assert session.turns[1].question == "Q3"
        assert session.turns[2].question == "Q4"

    def test_clear(self):
        """Test clearing conversation history."""
        session = ConversationSession(agent_name="test")
        session.add_turn(ConversationTurn(question="Q1"))
        session.add_turn(ConversationTurn(question="Q2"))
        assert len(session.turns) == 2

        session.clear()
        assert session.turns == []

    def test_format_history_empty(self):
        """Test formatting empty history."""
        session = ConversationSession(agent_name="test")
        assert session.format_history() == ""

    def test_format_history_single_turn(self):
        """Test formatting history with one turn."""
        session = ConversationSession(agent_name="test")
        session.add_turn(
            ConversationTurn(
                question="How many users?",
                sql="SELECT COUNT(*) FROM users",
                result_summary="1 row: count",
            )
        )
        history = session.format_history()

        assert "Previous Conversation Context" in history
        assert "How many users?" in history
        assert "SELECT COUNT(*)" in history
        assert "1 row: count" in history
        assert "Exchange 1" in history

    def test_format_history_multiple_turns(self):
        """Test formatting history with multiple turns."""
        session = ConversationSession(agent_name="test")
        session.add_turn(
            ConversationTurn(
                question="How many users?",
                sql="SELECT COUNT(*) FROM users",
                result_summary="1 row: count",
            )
        )
        session.add_turn(
            ConversationTurn(
                question="Break that down by country",
                sql="SELECT country, COUNT(*) FROM users GROUP BY country",
                result_summary="5 rows: country, count",
            )
        )

        history = session.format_history()

        assert "Exchange 1" in history
        assert "Exchange 2" in history
        assert "How many users?" in history
        assert "Break that down by country" in history

    def test_format_history_without_sql(self):
        """Test formatting history when SQL is None."""
        session = ConversationSession(agent_name="test")
        session.add_turn(
            ConversationTurn(
                question="What time is it?",
                sql=None,
                result_summary="Error: Cannot answer this question",
            )
        )
        history = session.format_history()

        assert "What time is it?" in history
        assert "Error: Cannot answer" in history


class TestSummarizeResult:
    """Tests for result summarization."""

    @dataclass
    class MockResponse:
        """Mock AgentResponse for testing."""

        success: bool = True
        columns: list = None
        rows: list = None
        row_count: int = 0
        truncated: bool = False
        error: str | None = None

        def __post_init__(self):
            if self.columns is None:
                self.columns = []
            if self.rows is None:
                self.rows = []

    def test_summarize_error_response(self):
        """Test summarizing error response."""
        session = ConversationSession(agent_name="test")
        response = self.MockResponse(success=False, error="Connection failed")
        summary = session.summarize_result(response)
        assert summary == "Error: Connection failed"

    def test_summarize_no_rows(self):
        """Test summarizing empty result."""
        session = ConversationSession(agent_name="test")
        response = self.MockResponse(success=True, rows=[], row_count=0)
        summary = session.summarize_result(response)
        assert summary == "No rows returned"

    def test_summarize_single_row(self):
        """Test summarizing single row result."""
        session = ConversationSession(agent_name="test")
        response = self.MockResponse(
            success=True,
            columns=["count"],
            rows=[[42]],
            row_count=1,
        )
        summary = session.summarize_result(response)
        assert "1 row" in summary
        assert "count" in summary

    def test_summarize_multiple_rows(self):
        """Test summarizing multi-row result."""
        session = ConversationSession(agent_name="test")
        response = self.MockResponse(
            success=True,
            columns=["id", "name", "email"],
            rows=[[1, "Alice", "a@x.com"], [2, "Bob", "b@x.com"]],
            row_count=2,
        )
        summary = session.summarize_result(response)
        assert "2 rows" in summary
        assert "id" in summary
        assert "name" in summary

    def test_summarize_many_columns(self):
        """Test summarizing result with many columns (truncated)."""
        session = ConversationSession(agent_name="test")
        response = self.MockResponse(
            success=True,
            columns=["c1", "c2", "c3", "c4", "c5", "c6", "c7"],
            rows=[[1, 2, 3, 4, 5, 6, 7]],
            row_count=1,
        )
        summary = session.summarize_result(response)
        assert "c1" in summary
        assert "c5" in summary
        assert "7 total columns" in summary

    def test_summarize_truncated_result(self):
        """Test summarizing truncated result."""
        session = ConversationSession(agent_name="test")
        response = self.MockResponse(
            success=True,
            columns=["id"],
            rows=[[i] for i in range(100)],
            row_count=100,
            truncated=True,
        )
        summary = session.summarize_result(response)
        assert "truncated" in summary

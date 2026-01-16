"""Tests for ChatAgent and chat tools."""

import os
import pytest
from unittest.mock import MagicMock, patch

from lib.agent.chat_tools import (
    CHAT_TOOLS,
    ChatToolExecutor,
    ToolResult,
    format_tool_result_for_display,
)
from lib.agent.chat_agent import ChatAgent, ChatMessage, ChatResponse


class TestChatTools:
    """Test chat tool definitions."""

    def test_tools_defined(self):
        """Should have all expected tools."""
        tool_names = [t["name"] for t in CHAT_TOOLS]
        assert "query_database" in tool_names
        assert "get_schema" in tool_names
        assert "run_sql" in tool_names

    def test_query_database_schema(self):
        """query_database should have question parameter."""
        tool = next(t for t in CHAT_TOOLS if t["name"] == "query_database")
        assert "question" in tool["input_schema"]["properties"]
        assert "question" in tool["input_schema"]["required"]

    def test_get_schema_schema(self):
        """get_schema should have optional table_name parameter."""
        tool = next(t for t in CHAT_TOOLS if t["name"] == "get_schema")
        assert "table_name" in tool["input_schema"]["properties"]
        assert tool["input_schema"]["required"] == []

    def test_run_sql_schema(self):
        """run_sql should have sql parameter."""
        tool = next(t for t in CHAT_TOOLS if t["name"] == "run_sql")
        assert "sql" in tool["input_schema"]["properties"]
        assert "sql" in tool["input_schema"]["required"]


class TestChatToolExecutor:
    """Test tool execution."""

    def test_execute_unknown_tool(self):
        """Should return error for unknown tool."""
        runtime = MagicMock()
        executor = ChatToolExecutor(runtime)

        result = executor.execute("unknown_tool", {}, "test-id")

        assert not result.success
        assert "Unknown tool" in result.content

    def test_query_database_success(self):
        """Should execute query_database successfully."""
        runtime = MagicMock()
        runtime.ask.return_value = MagicMock(
            success=True,
            sql="SELECT COUNT(*) FROM users",
            columns=["count"],
            rows=[[100]],
            row_count=1,
            truncated=False,
        )

        executor = ChatToolExecutor(runtime)
        result = executor.execute(
            "query_database",
            {"question": "How many users?"},
            "test-id",
        )

        assert result.success
        assert "SELECT COUNT(*)" in result.content
        assert result.data["sql"] == "SELECT COUNT(*) FROM users"
        assert result.data["row_count"] == 1

    def test_query_database_empty_question(self):
        """Should fail for empty question."""
        runtime = MagicMock()
        executor = ChatToolExecutor(runtime)

        result = executor.execute("query_database", {"question": ""}, "test-id")

        assert not result.success
        assert "No question" in result.content

    def test_get_schema_success(self):
        """Should execute get_schema successfully."""
        runtime = MagicMock()
        runtime.get_schema_summary.return_value = {
            "tables": [
                {"name": "users", "columns": ["id", "name", "email"]},
                {"name": "orders", "columns": ["id", "user_id", "total"]},
            ],
            "source": "semantic_layer",
        }

        executor = ChatToolExecutor(runtime)
        result = executor.execute("get_schema", {}, "test-id")

        assert result.success
        assert "users" in result.content
        assert "orders" in result.content
        assert result.data["source"] == "semantic_layer"

    def test_get_schema_specific_table(self):
        """Should filter to specific table."""
        runtime = MagicMock()
        runtime.get_schema_summary.return_value = {
            "tables": [
                {"name": "users", "columns": ["id", "name", "email"]},
                {"name": "orders", "columns": ["id", "user_id", "total"]},
            ],
            "source": "database",
        }

        executor = ChatToolExecutor(runtime)
        result = executor.execute(
            "get_schema",
            {"table_name": "users"},
            "test-id",
        )

        assert result.success
        assert "users" in result.content
        # Should only have users table in data
        assert len(result.data["tables"]) == 1
        assert result.data["tables"][0]["name"] == "users"


class TestToolResult:
    """Test ToolResult formatting."""

    def test_format_error_result(self):
        """Should format error result."""
        result = ToolResult(
            tool_use_id="test",
            success=False,
            content="Query failed: connection timeout",
        )

        formatted = format_tool_result_for_display(result)
        assert "Error:" in formatted
        assert "connection timeout" in formatted

    def test_format_query_result(self):
        """Should format query result as table."""
        result = ToolResult(
            tool_use_id="test",
            success=True,
            content="Results",
            data={
                "sql": "SELECT id, name FROM users",
                "columns": ["id", "name"],
                "rows": [[1, "Alice"], [2, "Bob"]],
                "row_count": 2,
                "execution_time_ms": 15.5,
                "truncated": False,
            },
        )

        formatted = format_tool_result_for_display(result)
        assert "SELECT id, name FROM users" in formatted
        assert "Alice" in formatted
        assert "Bob" in formatted
        assert "2 rows" in formatted
        assert "15.5ms" in formatted


class TestChatAgent:
    """Test ChatAgent class."""

    def test_init(self):
        """Should initialize with agent config."""
        config = MagicMock()
        config.target = "test-db"

        agent = ChatAgent(config)

        assert agent.config == config
        assert agent.messages == []

    def test_clear_history(self):
        """Should clear message history."""
        config = MagicMock()
        agent = ChatAgent(config)

        agent.messages = [{"role": "user", "content": "test"}]
        agent.clear_history()

        assert agent.messages == []

    def test_get_history_summary_empty(self):
        """Should return empty list for no history."""
        config = MagicMock()
        agent = ChatAgent(config)

        summary = agent.get_history_summary()

        assert summary == []

    def test_get_history_summary_with_messages(self):
        """Should summarize messages."""
        config = MagicMock()
        agent = ChatAgent(config)

        agent.messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        summary = agent.get_history_summary()

        assert len(summary) == 2
        assert summary[0]["role"] == "user"
        assert "Hello" in summary[0]["summary"]
        assert summary[1]["role"] == "assistant"

    def test_extract_tool_uses(self):
        """Should extract tool_use blocks."""
        config = MagicMock()
        agent = ChatAgent(config)

        response = {
            "content": [
                {"type": "text", "text": "Let me check that."},
                {
                    "type": "tool_use",
                    "id": "tool-123",
                    "name": "query_database",
                    "input": {"question": "How many users?"},
                },
            ]
        }

        tool_uses = agent._extract_tool_uses(response)

        assert len(tool_uses) == 1
        assert tool_uses[0]["id"] == "tool-123"
        assert tool_uses[0]["name"] == "query_database"
        assert tool_uses[0]["input"]["question"] == "How many users?"

    def test_extract_text(self):
        """Should extract text content."""
        config = MagicMock()
        agent = ChatAgent(config)

        response = {
            "content": [
                {"type": "text", "text": "Here are the results:"},
                {"type": "text", "text": "100 users found."},
            ]
        }

        text = agent._extract_text(response)

        assert "Here are the results:" in text
        assert "100 users found" in text

    def test_extract_text_string_content(self):
        """Should handle string content."""
        config = MagicMock()
        agent = ChatAgent(config)

        response = {"content": "Simple response"}

        text = agent._extract_text(response)

        assert text == "Simple response"


class TestChatAgentIntegration:
    """Integration tests for chat flow (mocked LLM)."""

    def test_chat_conversational_response(self):
        """Should return text when LLM doesn't use tools."""
        import sys

        # Create mock anthropic module
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Hello! How can I help?")]
        mock_response.stop_reason = "end_turn"
        mock_client.messages.create.return_value = mock_response

        # Patch anthropic in sys.modules and provide fake API key
        with patch.dict(sys.modules, {"anthropic": mock_anthropic}), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            # Create agent with mocked config
            config = MagicMock()
            config.target = "test"
            agent = ChatAgent(config)

            # Chat
            response = agent.chat("Hello")

            assert response.text == "Hello! How can I help?"
            assert response.tool_results == []
            assert len(agent.messages) == 2  # user + assistant

    def test_chat_with_tool_use(self):
        """Should execute tools and get final response."""
        import sys

        # Create mock anthropic module
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        # First call: tool use
        tool_response = MagicMock()
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "tool-123"
        tool_block.name = "get_schema"
        tool_block.input = {}
        tool_response.content = [tool_block]
        tool_response.stop_reason = "tool_use"

        # Second call: final text
        final_response = MagicMock()
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "You have 2 tables: users and orders."
        final_response.content = [text_block]
        final_response.stop_reason = "end_turn"

        mock_client.messages.create.side_effect = [tool_response, final_response]

        # Patch anthropic in sys.modules and provide fake API key
        with patch.dict(sys.modules, {"anthropic": mock_anthropic}), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            # Create agent with mocked runtime
            config = MagicMock()
            config.target = "test"
            agent = ChatAgent(config)

            # Mock the runtime's get_schema_summary
            agent.runtime.get_schema_summary = MagicMock(return_value={
                "tables": [
                    {"name": "users", "columns": ["id", "name"]},
                    {"name": "orders", "columns": ["id", "user_id"]},
                ],
                "source": "database",
            })

            # Chat
            response = agent.chat("What tables do you have?")

            assert "2 tables" in response.text
            assert len(response.tool_results) == 1
            assert response.tool_results[0].success

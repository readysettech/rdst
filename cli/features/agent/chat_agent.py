"""
Conversational Chat Agent

A tool-using agent that maintains conversation context and decides
when to query the database vs respond conversationally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable
import logging

if TYPE_CHECKING:
    from .config import AgentConfig

from .chat_tools import CHAT_TOOLS, ChatToolExecutor, ToolResult, format_tool_result_for_display
from .runtime import AgentRuntime

logger = logging.getLogger(__name__)

# Callback type for streaming events during chat processing.
# event is one of: "thinking", "tool_call", "tool_result"
# data varies by event type.
ChatEventCallback = Callable[[str, dict[str, Any]], None]


SYSTEM_PROMPT = """You are a helpful data assistant with access to a database. You can help users:

1. Query data using natural language (use the query_database tool)
2. Explore the database schema (use the get_schema tool)
3. Discuss and explain query results, SQL syntax, and data patterns

## When to use tools:

**USE query_database when:**
- User wants to retrieve, count, or analyze data
- User asks questions like "show me...", "how many...", "what is the average..."
- User asks to "select", "find", "list", or "get" data
- User provides explicit SQL — rephrase it as a natural language question

**USE get_schema when:**
- User asks about available tables or columns
- User asks "what data do you have?" or "what tables exist?"
- User needs to know column names or types

**DON'T use tools when:**
- User asks about previous results (you have the context)
- User asks for explanations ("why did that return 0 rows?")
- User asks about SQL syntax or best practices
- User is having a general conversation

## Exploration style:

When answering broad questions that require multiple queries:
1. FIRST, announce your plan: "I'll explore this from N angles: (1) ..., (2) ..., (3) ..."
2. Before each query, explain WHY you're running it and how it connects to previous results
3. After getting results, note anything surprising or anomalous before moving on
4. At the end, synthesize findings into a structured summary

## Result handling:

- The user sees a compact summary of query results (row count, column count, timing).
  Small tables (≤20 rows) are shown in full. Larger results are summarized.
- YOUR job is to interpret the data: highlight key patterns, trends, and anomalies.
  Don't just describe what the table shows — add insight.
- Note data quality issues: unexpected nulls, missing date ranges, skewed distributions
- Connect results to previous queries: "This confirms the growth trend we saw earlier..."
- Prefer queries that return concise, meaningful results over massive data dumps.
  Use GROUP BY, aggregations, and LIMIT to keep results focused.

## Follow-ups:

After completing your analysis, suggest 2-3 specific follow-up questions
the user might want to explore next.

## Important:
- You have access to the full conversation history. Reference it naturally.
- When explaining results, use the actual data you've seen.
- If a query returns unexpected results, explain possible reasons.
- Be concise but helpful in your responses.
"""


@dataclass
class ChatMessage:
    """A message in the chat conversation."""

    role: str  # "user", "assistant"
    content: Any  # str or list of content blocks


@dataclass
class ChatResponse:
    """Response from the chat agent."""

    text: str
    tool_results: list[ToolResult] = field(default_factory=list)


class ChatAgent:
    """
    Conversational agent with tool-use capabilities.

    Maintains conversation history in Anthropic message format and
    uses an LLM to decide when to use tools vs respond directly.
    """

    def __init__(self, agent_config: "AgentConfig"):
        """
        Initialize the chat agent.

        Args:
            agent_config: Configuration for the agent.
        """
        self.config = agent_config
        self.runtime = AgentRuntime(agent_config)
        self.tool_executor = ChatToolExecutor(self.runtime)
        self.messages: list[dict[str, Any]] = []
        self._llm_manager = None

    def _get_llm(self):
        """Lazy-load LLM manager."""
        if self._llm_manager is None:
            from shared.llm_manager import LLMManager
            self._llm_manager = LLMManager()
        return self._llm_manager

    def _resolve_model(self) -> str:
        """Resolve the chat model through the shared LLM configuration.

        Follows the same chain as LLMManager.query: config-file llm.model,
        then RDST_ANTHROPIC_MODEL, then the Claude provider default.
        """
        llm = self._get_llm()
        return llm.defaults.model or llm.provider("claude").default_model()

    def clear_history(self) -> None:
        """Clear conversation history."""
        self.messages = []

    def get_history_summary(self) -> list[dict[str, str]]:
        """
        Get a summary of conversation history.

        Returns:
            List of dicts with role and content summary.
        """
        summaries = []
        for msg in self.messages:
            role = msg["role"]
            content = msg["content"]

            if isinstance(content, str):
                summary = content[:100] + "..." if len(content) > 100 else content
            elif isinstance(content, list):
                # Handle tool use/result blocks
                texts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            texts.append(block.get("text", "")[:50])
                        elif block.get("type") == "tool_use":
                            texts.append(f"[tool: {block.get('name')}]")
                        elif block.get("type") == "tool_result":
                            texts.append("[tool result]")
                summary = " | ".join(texts)
            else:
                summary = str(content)[:100]

            summaries.append({"role": role, "summary": summary})

        return summaries

    def chat(
        self,
        user_message: str,
        on_event: ChatEventCallback | None = None,
    ) -> ChatResponse:
        """
        Process a user message and return a response.

        Args:
            user_message: The user's message.
            on_event: Optional callback for streaming progress events.
                Events emitted:
                - ("thinking", {"text": "..."}) - LLM reasoning text between tool calls
                - ("tool_call", {"name": "...", "input": {...}}) - tool about to execute
                - ("tool_result", {"result": ToolResult}) - tool finished

        Returns:
            ChatResponse with text and any tool results.
        """
        def _emit(event: str, data: dict[str, Any]) -> None:
            if on_event:
                on_event(event, data)

        # Add user message to history
        self.messages.append({
            "role": "user",
            "content": user_message,
        })

        # Get LLM response with tool use loop
        tool_results = []
        max_iterations = 15

        for iteration in range(max_iterations):
            _emit("llm_call", {"iteration": iteration})
            try:
                response = self._call_llm()
            except KeyboardInterrupt:
                return ChatResponse(
                    text="Interrupted.",
                    tool_results=tool_results,
                )

            # Check for tool use
            tool_uses = self._extract_tool_uses(response)

            if not tool_uses:
                # No tools - extract text and return
                text = self._extract_text(response)
                self.messages.append({
                    "role": "assistant",
                    "content": response.get("content", text),
                })
                return ChatResponse(text=text, tool_results=tool_results)

            # Emit any thinking text that accompanies tool calls
            thinking_text = self._extract_text(response)
            if thinking_text.strip():
                _emit("thinking", {"text": thinking_text, "iteration": iteration})

            # Execute tools and continue loop
            assistant_content = response.get("content", [])
            self.messages.append({
                "role": "assistant",
                "content": assistant_content,
            })

            # Execute each tool and collect results
            tool_result_blocks = []
            for tool_use in tool_uses:
                _emit("tool_call", {
                    "name": tool_use["name"],
                    "input": tool_use["input"],
                    "iteration": iteration,
                })

                try:
                    result = self.tool_executor.execute(
                        tool_name=tool_use["name"],
                        tool_input=tool_use["input"],
                        tool_use_id=tool_use["id"],
                    )
                except KeyboardInterrupt:
                    from .chat_tools import ToolResult
                    result = ToolResult(
                        tool_use_id=tool_use["id"],
                        success=False,
                        content="Query interrupted by user",
                    )
                tool_results.append(result)

                _emit("tool_result", {
                    "result": result,
                    "iteration": iteration,
                })

                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use["id"],
                    "content": result.content,
                    "is_error": not result.success,
                })

            # Add tool results to messages
            self.messages.append({
                "role": "user",
                "content": tool_result_blocks,
            })

        # If we hit max iterations, return what we have
        logger.warning("Chat agent hit max iterations")
        return ChatResponse(
            text="I encountered an issue processing your request. Please try rephrasing.",
            tool_results=tool_results,
        )

    def _get_api_key(self) -> str:
        """
        Get API key, checking env vars then trial config.

        Uses the shared key resolution module for consistent behavior
        across LLMManager and ChatAgent.

        Returns:
            API key string.

        Raises:
            ValueError: If no API key found.
        """
        try:
            from shared.llm_manager.key_resolution import resolve_api_key
            resolution = resolve_api_key()
            self._key_resolution = resolution
            return resolution.api_key
        except Exception as e:
            raise ValueError(str(e))

    def _call_llm(self) -> dict[str, Any]:
        """
        Call the LLM with current messages and tools.

        Returns:
            LLM response dict.
        """
        # Use Anthropic client directly for tool use
        try:
            import anthropic
        except ImportError:
            raise ImportError("anthropic package required. Install with: pip install anthropic")

        # Explicitly pass API key to avoid env conflicts
        api_key = self._get_api_key()

        # Route based on key type (direct vs trial proxy)
        kwargs = {"api_key": api_key}
        if hasattr(self, "_key_resolution") and self._key_resolution.is_trial:
            from shared.llm_manager.key_resolution import _trial_proxy_base
            kwargs["base_url"] = _trial_proxy_base()
            kwargs["default_headers"] = self._key_resolution.extra_headers

        client = anthropic.Anthropic(**kwargs)

        response = client.messages.create(
            model=self._resolve_model(),
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=CHAT_TOOLS,
            tool_choice={"type": "auto"},
            messages=self.messages,
        )

        # Convert to dict format
        return {
            "content": [self._content_block_to_dict(block) for block in response.content],
            "stop_reason": response.stop_reason,
        }

    def _content_block_to_dict(self, block) -> dict[str, Any]:
        """Convert Anthropic content block to dict."""
        if hasattr(block, "type"):
            if block.type == "text":
                return {"type": "text", "text": block.text}
            elif block.type == "tool_use":
                return {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
        return {"type": "unknown"}

    def _extract_tool_uses(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract tool use blocks from response."""
        tool_uses = []
        content = response.get("content", [])

        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_uses.append({
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input": block.get("input", {}),
                    })

        return tool_uses

    def _extract_text(self, response: dict[str, Any]) -> str:
        """Extract text content from response."""
        content = response.get("content", [])

        if isinstance(content, str):
            return content

        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
            return "\n".join(texts)

        return ""

    def get_schema_summary(self) -> dict[str, Any]:
        """Get database schema summary."""
        return self.runtime.get_schema_summary()

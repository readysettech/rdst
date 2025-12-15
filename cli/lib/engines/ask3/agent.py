"""
Ask3Agent - Iterative exploration agent for complex NL-to-SQL queries.

When the linear flow struggles (zero rows, low confidence, etc.), this agent
takes over and uses a think-act-observe loop to find the correct query.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import Ask3Context
    from .presenter import Ask3Presenter

from .agent_context import AgentExplorationContext
from .agent_tools import AgentToolExecutor, get_tool_definitions
from .types import Status

logger = logging.getLogger(__name__)


class Ask3Agent:
    """
    Iterative exploration agent for complex NL-to-SQL queries.

    Uses tool calling to let the LLM explore the schema, sample data,
    and iteratively refine queries until finding the correct answer.
    """

    def __init__(
        self,
        llm_manager,
        presenter: 'Ask3Presenter',
        db_executor=None
    ):
        """
        Initialize the agent.

        Args:
            llm_manager: LLM client for tool-use completions
            presenter: For user output
            db_executor: Optional custom database executor (for testing)
        """
        self.llm_manager = llm_manager
        self.presenter = presenter
        self.db_executor = db_executor

    def run(self, ctx: AgentExplorationContext) -> 'Ask3Context':
        """
        Run the agent exploration loop.

        Args:
            ctx: Agent context with question and escalation info

        Returns:
            Ask3Context with final query (or error)
        """
        from ...prompts.agent_prompts import (
            AGENT_SYSTEM_PROMPT,
            build_initial_message,
        )

        self.presenter.info(f"Entering agent exploration mode ({ctx.escalation_reason})")

        # Initialize tool executor
        tool_executor = AgentToolExecutor(
            schema_info=ctx.schema_info,
            target_config=ctx.linear_context.target_config if ctx.linear_context else {},
            db_type=ctx.db_type,
            presenter=self.presenter,
            timeout_seconds=ctx.linear_context.timeout_seconds if ctx.linear_context else 30
        )

        # Build initial message
        previous_sql = None
        previous_error = None
        previous_row_count = None

        if ctx.attempted_queries:
            last = ctx.attempted_queries[-1]
            previous_sql = last.sql
            previous_error = last.error
            previous_row_count = last.result_rows

        initial_message = build_initial_message(
            question=ctx.question,
            db_type=ctx.db_type,
            target=ctx.target,
            initial_tables=list(ctx.explored_tables),
            escalation_reason=ctx.escalation_reason,
            previous_sql=previous_sql,
            previous_error=previous_error,
            previous_row_count=previous_row_count
        )

        # Conversation state - Anthropic uses separate system parameter
        # Only user/assistant messages go in the messages array
        system_prompt = AGENT_SYSTEM_PROMPT
        messages = [
            {"role": "user", "content": initial_message},
        ]

        # Agent loop
        while ctx.can_continue():
            try:
                # Think: Get next action from LLM
                response = self._call_llm_with_tools(messages, system_prompt)

                if not response:
                    logger.error("LLM returned empty response")
                    break

                # Check for tool calls
                tool_calls = response.get('tool_calls', [])

                if not tool_calls:
                    # No tool call - LLM gave a text response
                    text = response.get('text', '')
                    logger.debug(f"LLM text response: {text[:200]}")

                    # Add assistant message and prompt for action
                    messages.append({"role": "assistant", "content": text})
                    messages.append({
                        "role": "user",
                        "content": "Please use one of the available tools to explore or submit your query."
                    })
                    continue

                # Process tool calls
                for tool_call in tool_calls:
                    tool_name = tool_call.get('function', {}).get('name', '')
                    tool_args_str = tool_call.get('function', {}).get('arguments', '{}')
                    tool_id = tool_call.get('id', '')

                    try:
                        tool_args = json.loads(tool_args_str)
                    except json.JSONDecodeError:
                        tool_args = {}

                    logger.info(f"Agent tool call: {tool_name}({list(tool_args.keys())})")

                    # Act: Execute the tool
                    result = tool_executor.execute(tool_name, tool_args, ctx)

                    # Check for final query submission
                    if result == "FINAL_QUERY_SUBMITTED":
                        self.presenter.info("Agent found a solution")
                        return self._finalize(ctx)

                    # Observe: Add tool result to conversation
                    # Anthropic format: assistant message with tool_use blocks,
                    # then user message with tool_result blocks
                    messages.append({
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": tool_id,
                                "name": tool_name,
                                "input": tool_args
                            }
                        ]
                    })

                    # Tool results come in a user message with tool_result content blocks
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": result
                            }
                        ]
                    })

                    # Track LLM call
                    ctx.add_llm_call(
                        prompt_preview=f"[Tool call: {tool_name}]",
                        response_preview=result[:200],
                        tokens=0,  # We don't have exact token count here
                        latency_ms=0,
                        model=self.llm_manager.defaults.model or 'unknown'
                    )

            except KeyboardInterrupt:
                self.presenter.cancelled()
                break

            except Exception as e:
                logger.error(f"Agent loop error: {e}", exc_info=True)
                self.presenter.error(f"Agent error: {e}")
                break

        # Max iterations reached or error
        if not ctx.final_sql:
            self.presenter.warning(
                f"Agent reached {ctx.tool_call_count} tool calls without finding a solution. "
                "Consider rephrasing your question or providing more context."
            )

            # Try to salvage something from the last query attempt
            if ctx.attempted_queries:
                last = ctx.attempted_queries[-1]
                if not last.error and last.result_rows > 0:
                    ctx.final_sql = last.sql
                    ctx.final_explanation = "Best attempt found during exploration"

        return self._finalize(ctx)

    def _call_llm_with_tools(self, messages: List[Dict[str, Any]], system_prompt: str) -> Dict[str, Any]:
        """
        Call LLM with tool definitions.

        Args:
            messages: Conversation messages (user/assistant only, no system)
            system_prompt: System prompt to pass separately

        Returns:
            Dict with 'text' and/or 'tool_calls'
        """
        # Get tool definitions
        tools = get_tool_definitions()

        # Build request
        try:
            start = time.time()

            # Use the query interface with tools in extra
            # Anthropic format: system is top-level, not in messages
            result = self.llm_manager.query(
                system_message="",  # Will override with 'system' in extra
                user_query="",  # Will override with 'messages' in extra
                max_tokens=1500,
                temperature=0.0,
                debug=True,  # Need raw response to get tool_use blocks
                extra={
                    "system": system_prompt,  # Anthropic top-level system param
                    "messages": messages,  # Override with full conversation
                    "tools": tools,
                    "tool_choice": {"type": "auto"},
                }
            )

            latency = (time.time() - start) * 1000
            logger.debug(f"LLM call took {latency:.0f}ms")

            # Parse Anthropic response format
            # Claude returns content as list of blocks: [{"type": "text", ...}, {"type": "tool_use", ...}]
            raw = result.get('raw', {})
            content_blocks = raw.get('content', [])

            text_parts = []
            tool_calls = []

            for block in content_blocks:
                if block.get('type') == 'text':
                    text_parts.append(block.get('text', ''))
                elif block.get('type') == 'tool_use':
                    # Convert Anthropic tool_use to our internal format
                    tool_calls.append({
                        'id': block.get('id', ''),
                        'function': {
                            'name': block.get('name', ''),
                            'arguments': json.dumps(block.get('input', {}))
                        }
                    })

            return {
                'text': '\n'.join(text_parts),
                'tool_calls': tool_calls
            }

        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return {}

    def _finalize(self, ctx: AgentExplorationContext) -> 'Ask3Context':
        """
        Convert agent context back to Ask3Context.

        Args:
            ctx: Completed agent context

        Returns:
            Ask3Context with results
        """
        if ctx.linear_context is None:
            # Create a minimal context if we don't have one
            from .context import Ask3Context
            ask_ctx = Ask3Context(
                question=ctx.question,
                target=ctx.target,
                db_type=ctx.db_type
            )
        else:
            ask_ctx = ctx.to_ask3_context()

        # Set final SQL
        if ctx.final_sql:
            ask_ctx.sql = ctx.final_sql
            ask_ctx.sql_explanation = ctx.final_explanation or "Found through agent exploration"
            ask_ctx.status = Status.PENDING  # Ready for execution
        else:
            ask_ctx.mark_error("Agent could not find a valid query")

        return ask_ctx


def create_agent(
    llm_manager,
    presenter: 'Ask3Presenter',
    db_executor=None
) -> Ask3Agent:
    """
    Factory function to create an Ask3Agent.

    Args:
        llm_manager: LLM client
        presenter: Output presenter
        db_executor: Optional custom executor

    Returns:
        Configured Ask3Agent
    """
    return Ask3Agent(
        llm_manager=llm_manager,
        presenter=presenter,
        db_executor=db_executor
    )

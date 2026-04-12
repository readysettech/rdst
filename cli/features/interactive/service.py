"""Interactive Service for RDST analysis conversations."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from shared.llm import create_llm_manager
from shared.query_registry import ConversationRegistry, InteractiveConversation

from .events import (
    ChunkEvent,
    InteractiveCompleteEvent,
    InteractiveErrorEvent,
    InteractiveEvent,
)


def get_interactive_mode_prompt() -> str:
    """Get the interactive mode transition prompt."""
    return """INTERACTIVE MODE ACTIVATED

The user wants to understand your recommendations in depth.

YOUR ROLE: Database performance expert answering questions about this specific query analysis.

COMMUNICATION STYLE:
- Be direct and technical. Avoid storytelling phrases like "But Here's the Reality Check", "The Real Question", "My Honest Assessment"
- Skip dramatic intros. Start with the answer.
- Use concrete numbers from the analysis data
- Explain reasoning, not just conclusions
- When discussing tradeoffs, be matter-of-fact, not dramatic

CRITICAL - ABOUT QUERY REWRITES & ANALYSIS INTEGRITY:
- The original analysis was thorough and correct based on available information
- Query rewrites MUST produce IDENTICAL results - this is a hard constraint
- If no rewrites were found, that's the correct answer given the constraint
- You CAN question the analysis, but ONLY when user provides NEW information:
  * "Actually, the query runs 50 times per second" → may change index recommendations
  * "We're planning to add a column X" → may unlock new rewrites
  * "The table is partitioned by date" → may change execution plan analysis
  * "We can change the query requirements" → now alternative queries are valid

TONE GUIDELINES:
✓ GOOD: "The analysis is correct given the constraint. However, if you're open to changing X, we could consider Y..."
✓ GOOD: "Based on the schema shown, there are no equivalent rewrites. Is there additional context about your use case that might open up options?"
❌ BAD: "The analysis missed obvious rewrites like..." (dismissive, assumes error)
❌ BAD: "There are several rewrites that should have been suggested" (contradicts without new info)

EXAMPLES OF CORRECT RESPONSES:

Scenario: User asks "Why no rewrites?"
❌ WRONG: "There are obvious rewrites like adding ORDER BY"
✓ CORRECT: "The analysis found no equivalent rewrites because adding ORDER BY would change which rows are returned with LIMIT. That makes it a different query, not an optimization. The original analysis is correct. If you need deterministic results and are willing to change the query behavior, I can suggest adding ORDER BY - but that's changing requirements, not optimizing."

Scenario: User says "We can relax the exact output requirement"
✓ CORRECT: "Ah, that changes things! If you're open to different output, here are approaches that might be faster: [suggestions]. Note these produce different results than the original query."

Scenario: User asks "Could we use a different index?"
✓ CORRECT: "The analysis already considered the available indexes. With the current schema, a covering index on (score, id) would help. Are there other indexes I should know about, or are you asking if we should create new ones?"

YOU CAN:
✓ Ask clarifying questions about their use case
✓ Request context not in the analysis (traffic patterns, replication setup, etc.)
✓ Probe the analysis with questions: "Is there additional schema info? Different use case constraints?"
✓ Revise recommendations when user provides NEW information that changes the analysis
✓ Say "I don't know" or "The analysis doesn't show that" when appropriate
✓ Suggest additional tests or metrics to gather
✓ Suggest alternative queries when user indicates they're open to changing requirements
✓ Challenge assumptions - but only when user provides contradictory evidence

YOU CANNOT:
✗ Dismiss the original analysis without new information from the user
✗ Make assumptions about data not in the analysis
✗ Recommend changes without explaining risks and tradeoffs
✗ Use phrases like "game-changer", "unlock", "transform", "journey"
✗ Suggest rewrites that would change query output (unless user explicitly wants different output)
✗ Be overly deferential - you can question, just respectfully and with cause

BALANCE:
- The original analysis is correct given available information
- New user input CAN invalidate parts of the analysis - that's fine
- Question to understand, not to dismiss
- If user says "but I think X would work", explore it: "Let's think through X. Here's what would happen..."
- Default: trust the analysis. Override: user provides new facts.

BOUNDARIES:
- Only answer questions about DATABASE PERFORMANCE and the ANALYSIS RESULTS
- If asked about unrelated topics: "I can only discuss this query's performance. What would you like to know about the analysis?"
- If you need information not in the analysis, ask for it directly

TONE: Experienced database engineer explaining to another engineer. Direct, technical, helpful. Trust but verify when new information emerges.
"""


_get_interactive_mode_prompt = get_interactive_mode_prompt


class InteractiveService:
    """Service for interactive analysis conversations."""

    DEFAULT_PROVIDER = "claude"
    DEFAULT_MODEL = "claude-sonnet-4-20250514"

    def __init__(self, conv_registry: ConversationRegistry | None = None, llm_manager=None):
        self.conv_registry = conv_registry or ConversationRegistry()
        self.llm_manager = llm_manager or create_llm_manager()

    async def send_message(
        self,
        query_hash: str,
        message: str,
        analysis_results: dict[str, Any],
        continue_existing: bool = True,
    ) -> AsyncGenerator[InteractiveEvent, None]:
        try:
            conversation = self._load_or_create_conversation(
                query_hash, analysis_results, continue_existing
            )
            conversation.add_message("user", message)
            messages = conversation.get_messages_for_llm()
            system_messages = [
                msg["content"] for msg in messages if msg["role"] == "system"
            ]
            combined_system_message = "\n\n".join(system_messages)
            conversation.messages.pop()

            full_response = ""
            async for token in self.llm_manager.query_stream(
                system_message=combined_system_message,
                user_query=message,
                context="",
                max_tokens=2000,
                temperature=0.1,
            ):
                full_response += token
                yield ChunkEvent(type="chunk", text=token)

            conversation.add_exchange(message, full_response)
            self.conv_registry.save_conversation(conversation)
            yield InteractiveCompleteEvent(
                type="complete",
                conversation_id=conversation.conversation_id,
            )
        except Exception as exc:
            yield InteractiveErrorEvent(type="error", error=str(exc))

    def get_conversation_status(self, query_hash: str) -> dict[str, Any]:
        provider = self.DEFAULT_PROVIDER
        exists = self.conv_registry.conversation_exists(query_hash, provider)
        if not exists:
            return {"exists": False}

        conversation = self.conv_registry.load_conversation(query_hash, provider)
        if not conversation:
            return {"exists": False}

        return {
            "exists": True,
            "conversation_id": conversation.conversation_id,
            "message_count": len(conversation.messages),
            "total_exchanges": conversation.total_exchanges,
            "started_at": conversation.started_at,
            "last_updated": conversation.last_updated,
            "provider": conversation.provider,
            "model": conversation.model,
        }

    def get_conversation_history(self, query_hash: str) -> list[dict[str, Any]]:
        provider = self.DEFAULT_PROVIDER
        if not self.conv_registry.conversation_exists(query_hash, provider):
            return []
        conversation = self.conv_registry.load_conversation(query_hash, provider)
        if not conversation:
            return []
        return [
            {"role": msg.role, "content": msg.content, "timestamp": msg.timestamp}
            for msg in conversation.get_user_assistant_messages()
        ]

    def delete_conversation(self, query_hash: str) -> bool:
        provider = self.DEFAULT_PROVIDER
        return self.conv_registry.delete_conversation(query_hash, provider)

    def _load_or_create_conversation(
        self,
        query_hash: str,
        analysis_results: dict[str, Any],
        continue_existing: bool,
    ) -> InteractiveConversation:
        provider = self.DEFAULT_PROVIDER
        model = self.DEFAULT_MODEL

        if not continue_existing:
            self.conv_registry.delete_conversation(query_hash, provider)

        if continue_existing:
            conversation = self.conv_registry.load_conversation(query_hash, provider)
            if conversation:
                if not self._has_interactive_mode_message(conversation):
                    interactive_prompt = get_interactive_mode_prompt()
                    conversation.add_message("system", interactive_prompt)
                    self.conv_registry.save_conversation(conversation)
                return conversation

        analysis_id = analysis_results.get("analysis_id", "unknown")
        target = analysis_results.get("target", "unknown")
        query_sql = analysis_results.get("query_sql", "")

        conversation = self.conv_registry.create_conversation(
            query_hash=query_hash,
            provider=provider,
            model=model,
            analysis_id=analysis_id,
            target=target,
            query_sql=query_sql,
        )

        if analysis_results:
            context_prompt = self._build_analysis_context_prompt(analysis_results)
            if context_prompt:
                conversation.add_message("system", context_prompt)

        conversation.add_message("system", get_interactive_mode_prompt())
        self.conv_registry.save_conversation(conversation)
        return conversation

    def _has_interactive_mode_message(self, conversation: InteractiveConversation) -> bool:
        for msg in conversation.messages:
            if msg.role == "system" and "INTERACTIVE MODE ACTIVATED" in msg.content:
                return True
        return False

    def _build_analysis_context_prompt(
        self, analysis_results: dict[str, Any]
    ) -> str | None:
        parts = []
        query_sql = analysis_results.get("query_sql", "")
        if query_sql:
            parts.append(f"QUERY:\n```sql\n{query_sql}\n```")

        explain_results = analysis_results.get("explain_results", {})
        if explain_results:
            exec_time = explain_results.get("execution_time_ms", 0)
            rows_examined = explain_results.get("rows_examined", 0)
            rows_returned = explain_results.get("rows_returned", 0)
            parts.append(
                f"PERFORMANCE METRICS:\n"
                f"- Execution time: {exec_time:.1f}ms\n"
                f"- Rows examined: {rows_examined:,}\n"
                f"- Rows returned: {rows_returned:,}"
            )

        llm_analysis = analysis_results.get("llm_analysis", {})
        if llm_analysis:
            index_recs = llm_analysis.get("index_recommendations", [])
            if index_recs:
                index_lines = [f"  - {rec.get('sql', 'N/A')}" for rec in index_recs]
                parts.append(f"INDEX RECOMMENDATIONS:\n" + "\n".join(index_lines))

            rewrite_sug = llm_analysis.get("rewrite_suggestions", [])
            if rewrite_sug:
                rewrite_lines = [
                    f"  - {sug.get('description', 'N/A')}" for sug in rewrite_sug
                ]
                parts.append(f"QUERY REWRITES:\n" + "\n".join(rewrite_lines))
            else:
                parts.append("QUERY REWRITES: None recommended")

        if not parts:
            return None
        return "ANALYSIS CONTEXT:\n\n" + "\n\n".join(parts)

    def _call_llm_sync(
        self, conversation: InteractiveConversation, user_question: str
    ) -> str | None:
        try:
            conversation.add_message("user", user_question)
            messages = conversation.get_messages_for_llm()
            system_messages = [
                msg["content"] for msg in messages if msg["role"] == "system"
            ]
            combined_system_message = "\n\n".join(system_messages)
            response_data = self.llm_manager.query(
                system_message=combined_system_message,
                user_query=user_question,
                context="",
                max_tokens=2000,
                temperature=0.1,
            )
            conversation.messages.pop()
            if response_data and "text" in response_data:
                return response_data["text"]
            return "Sorry, I couldn't generate a response. Please try again."
        except Exception:
            if conversation.messages and conversation.messages[-1].role == "user":
                conversation.messages.pop()
            return None

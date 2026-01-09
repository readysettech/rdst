"""
Interactive Mode for RDST Analyze

Provides educational conversation mode after query analysis where users can ask
questions about recommendations and understand performance implications.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any

from ..query_registry.conversation_registry import (
    ConversationRegistry,
    InteractiveConversation,
)
from ..query_registry.query_registry import QueryRegistry
from ..llm_manager.llm_manager import LLMManager

# Import UI system - handles Rich availability internally
from lib.ui import (
    get_console,
    MarkdownContent,
    StyleTokens,
    Prompt,
    Banner,
    MessagePanel,
    SectionHeader,
    StatusLine,
    EmptyState,
    Rule,
    SectionBox,
    NextSteps,
)

# Module-level console
console = get_console()


def run_interactive_mode(
    conversation: InteractiveConversation,
    analysis_results: Dict[str, Any],
    llm_manager: Optional[LLMManager] = None,
) -> None:
    """
    Enter interactive mode for educational Q&A about analysis results.

    Args:
        conversation: InteractiveConversation object (may have existing messages from analyze)
        analysis_results: Full analysis results from workflow
        llm_manager: Optional LLMManager instance (creates new if not provided)
    """
    conv_registry = ConversationRegistry()

    # Initialize LLM manager if not provided
    if llm_manager is None:
        llm_manager = LLMManager()

    # Add interactive mode transition message to conversation
    if not _has_interactive_mode_message(conversation):
        interactive_prompt = _get_interactive_mode_prompt()
        conversation.add_message("system", interactive_prompt)
        conv_registry.save_conversation(conversation)

    # Display header
    console.print()
    console.print(Banner("Interactive Mode - Explore the analysis"))

    # RDST uses Claude exclusively
    provider_name = "Claude"

    if conversation.total_exchanges == 0:
        console.print(
            f"\nYou can now interact with [{StyleTokens.STATUS_SUCCESS}]{provider_name}[/{StyleTokens.STATUS_SUCCESS}] to explore this query analysis."
        )
    else:
        console.print(
            f"\nContinuing conversation with [{StyleTokens.STATUS_SUCCESS}]{provider_name}[/{StyleTokens.STATUS_SUCCESS}]."
        )

    # If continuing conversation, show recent exchanges
    if conversation.total_exchanges > 0:
        console.print(
            f"\n[{StyleTokens.MUTED}]Continuing conversation ({conversation.total_exchanges} exchanges so far)[/{StyleTokens.MUTED}]"
        )
        console.print(SectionHeader("Recent conversation"))
        console.print(Rule())

        # Get user/assistant messages (not system messages)
        user_assistant_msgs = conversation.get_user_assistant_messages()

        # Show last 3 exchanges (6 messages: 3 user + 3 assistant)
        recent_count = min(6, len(user_assistant_msgs))
        start_idx = len(user_assistant_msgs) - recent_count

        for msg in user_assistant_msgs[start_idx:]:
            if msg.role == "user":
                console.print(
                    f"\n[{StyleTokens.HEADER}]You:[/{StyleTokens.HEADER}] {msg.content}"
                )
            elif msg.role == "assistant":
                console.print(
                    f"\n[{StyleTokens.STATUS_SUCCESS}]{provider_name}:[/{StyleTokens.STATUS_SUCCESS}]"
                )
                console.print(MarkdownContent(msg.content))

        console.print()
        console.print(Rule())

    # Show command hints
    console.print(
        f"\n[{StyleTokens.MUTED}]Ask questions about the recommendations, or type[/{StyleTokens.MUTED}] [{StyleTokens.STATUS_WARNING}]help[/{StyleTokens.STATUS_WARNING}] [{StyleTokens.MUTED}]for commands.[/{StyleTokens.MUTED}]"
    )
    console.print(
        f"[{StyleTokens.MUTED}]Type[/{StyleTokens.MUTED}] [{StyleTokens.STATUS_WARNING}]exit[/{StyleTokens.STATUS_WARNING}] [{StyleTokens.MUTED}]or[/{StyleTokens.MUTED}] [{StyleTokens.STATUS_WARNING}]quit[/{StyleTokens.STATUS_WARNING}] [{StyleTokens.MUTED}]to end the session.[/{StyleTokens.MUTED}]\n"
    )

    # REPL loop
    while True:
        try:
            user_input = Prompt.ask(">", default="", show_default=False).strip()

            if not user_input:
                continue

            # Handle exit
            if user_input.lower() in ["exit", "quit", "q"]:
                conv_registry.save_conversation(conversation)
                saved_name = _prompt_for_tag_if_needed(conversation.query_hash)
                _print_exit_message(conversation.query_hash, saved_name)
                break

            # Handle help
            if user_input.lower() == "help":
                _show_help()
                continue

            # Handle summary
            if user_input.lower() == "summary":
                _show_analysis_summary(analysis_results)
                continue

            # Handle review
            if user_input.lower() == "review":
                display_conversation_history(conversation)
                continue

            # Free-form question - send to LLM (always uses Claude)
            console.print(
                StatusLine("Getting response", f"{provider_name}..."),
                end="",
            )

            response = _ask_llm(conversation, user_input, llm_manager)

            if response:
                # Clear the "Calling AI..." line
                console.print("\r" + " " * 30 + "\r", end="")
                # Render response as markdown with syntax highlighting
                console.print("\n")
                console.print(MarkdownContent(response))
                console.print()

                # Add exchange to conversation and save
                conversation.add_exchange(user_input, response)
                conv_registry.save_conversation(conversation)

                # Simple warning for long conversations
                if conversation.total_exchanges >= 50:
                    console.print(
                        MessagePanel(
                            "This conversation has 50+ exchanges. Consider starting fresh if responses slow down.",
                            variant="warning",
                        )
                    )

        except KeyboardInterrupt:
            console.print(MessagePanel("Exiting interactive mode.", variant="info"))
            conv_registry.save_conversation(conversation)
            saved_name = _prompt_for_tag_if_needed(conversation.query_hash)
            _print_exit_message(conversation.query_hash, saved_name)
            break
        except Exception as e:
            console.print(MessagePanel(f"Error: {e}", variant="error"))
            continue


def display_conversation_history(
    conversation: InteractiveConversation, show_system_messages: bool = False
) -> None:
    """
    Display conversation history.

    Args:
        conversation: InteractiveConversation to display
        show_system_messages: If True, show system messages (default: False)
    """
    console.print()
    console.print(Banner("Conversation History"))
    console.print(StatusLine("Started", str(conversation.started_at)))
    console.print(StatusLine("Total exchanges", str(conversation.total_exchanges)))
    console.print(
        StatusLine("Provider", f"{conversation.provider} ({conversation.model})")
    )
    console.print()

    if not conversation.messages:
        console.print(EmptyState("No messages yet."))
        console.print()
        return

    # Get messages to display (filter system if not requested)
    if show_system_messages:
        messages_to_show = conversation.messages
    else:
        messages_to_show = conversation.get_user_assistant_messages()

    # Display messages
    for msg in messages_to_show:
        timestamp_str = _format_timestamp(msg.timestamp)

        if msg.role == "user":
            console.print(f"[{StyleTokens.MUTED}]{timestamp_str}[/{StyleTokens.MUTED}]")
            console.print(
                f"[{StyleTokens.HEADER}]You:[/{StyleTokens.HEADER}] {msg.content}"
            )
            console.print()
        elif msg.role == "assistant":
            console.print(
                f"[{StyleTokens.STATUS_SUCCESS}]AI:[/{StyleTokens.STATUS_SUCCESS}] {msg.content}"
            )
            console.print()
        elif msg.role == "system" and show_system_messages:
            console.print(
                f"[{StyleTokens.MUTED}]{timestamp_str}[/{StyleTokens.MUTED}] [SYSTEM MESSAGE]"
            )
            console.print(f"{msg.content[:200]}...")  # Truncate system messages
            console.print()

    console.print(Rule())
    console.print()


def _has_interactive_mode_message(conversation: InteractiveConversation) -> bool:
    """Check if conversation already has the interactive mode transition message."""
    for msg in conversation.messages:
        if msg.role == "system" and "INTERACTIVE MODE ACTIVATED" in msg.content:
            return True
    return False


def _get_interactive_mode_prompt() -> str:
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


def _ask_llm(
    conversation: InteractiveConversation, user_question: str, llm_manager: LLMManager
) -> Optional[str]:
    """
    Send user question to LLM with full conversation context.

    Args:
        conversation: InteractiveConversation with full history
        user_question: User's question
        llm_manager: LLMManager instance

    Returns:
        LLM response string or None if error
    """
    try:
        # Add user question to conversation temporarily (for LLM API call)
        conversation.add_message("user", user_question)

        # Get messages in LLM format
        messages = conversation.get_messages_for_llm()

        # Build system message from all system messages in conversation
        system_messages = [
            msg["content"] for msg in messages if msg["role"] == "system"
        ]
        combined_system_message = "\n\n".join(system_messages)

        # Call LLM with full conversation context
        # Always use Claude (default provider) regardless of what's stored in old conversations
        response_data = llm_manager.query(
            system_message=combined_system_message,
            user_query=user_question,
            context="",  # Context is already in system message
            max_tokens=2000,
            temperature=0.1,  # Low temperature for consistent, deterministic responses
        )

        # Remove the temporarily added user message (we'll add it properly with the response)
        conversation.messages.pop()

        # LLM query() returns a dict with "text" key, not "response"
        if response_data and "text" in response_data:
            return response_data["text"]
        else:
            return "Sorry, I couldn't generate a response. Please try again."

    except Exception as e:
        # Remove the temporarily added user message
        if conversation.messages and conversation.messages[-1].role == "user":
            conversation.messages.pop()
        console.print(MessagePanel(f"Error calling LLM: {e}", variant="error"))
        return None


def _show_help() -> None:
    """Display help for interactive mode commands."""
    console.print()
    console.print(Banner("Interactive Mode Commands"))
    console.print(
        SectionBox(
            "Commands",
            content="\n".join(
                [
                    "  help          Show this help message",
                    "  exit / quit   Exit interactive mode",
                    "  summary       Re-display analysis summary",
                    "  review        Show full conversation history",
                ]
            ),
        )
    )
    console.print(
        SectionBox(
            "Free-form Questions",
            content="  Just type your question and press Enter",
        )
    )
    console.print(
        SectionBox(
            "Examples",
            content="\n".join(
                [
                    '  "Why did you recommend an index on post_type_id?"',
                    '  "What\'s the tradeoff of adding this index?"',
                    '  "What if my table has heavy writes?"',
                    '  "Can you explain what a full table scan means?"',
                ]
            ),
        )
    )
    console.print()


def _show_analysis_summary(analysis_results: Dict[str, Any]) -> None:
    """
    Display a brief summary of the analysis results.

    Args:
        analysis_results: Analysis results from workflow
    """
    console.print()
    console.print(Banner("Analysis Summary"))

    # Extract key information
    explain_results = analysis_results.get("explain_results", {})
    llm_analysis = analysis_results.get("llm_analysis", {})

    # Performance metrics
    exec_time = explain_results.get("execution_time_ms", 0)
    rows_examined = explain_results.get("rows_examined", 0)
    rows_returned = explain_results.get("rows_returned", 0)

    console.print(StatusLine("Execution Time", f"{exec_time:.1f}ms"))
    console.print(StatusLine("Rows Examined", f"{rows_examined:,}"))
    console.print(StatusLine("Rows Returned", f"{rows_returned:,}"))
    console.print()

    # Index recommendations
    index_recs = llm_analysis.get("index_recommendations", [])
    if index_recs:
        index_content = "\n".join(
            [f"  [{i}] {rec.get('sql', 'N/A')}" for i, rec in enumerate(index_recs, 1)]
        )
        console.print(SectionBox("Index Recommendations", content=index_content))
        console.print()

    # Rewrite suggestions
    rewrite_sug = llm_analysis.get("rewrite_suggestions", [])
    if rewrite_sug:
        rewrite_content = "\n".join(
            [
                f"  [{i}] {sug.get('description', 'N/A')}"
                for i, sug in enumerate(rewrite_sug, 1)
            ]
        )
        console.print(SectionBox("Query Rewrites", content=rewrite_content))
        console.print()
    else:
        console.print(SectionBox("Query Rewrites", content="None recommended"))
        console.print()


def _format_timestamp(timestamp_str: str) -> str:
    """Format ISO timestamp for display."""
    try:
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return timestamp_str


def _prompt_for_tag_if_needed(query_hash: str) -> Optional[str]:
    """
    Prompt user to tag the query if it doesn't already have a tag.

    Args:
        query_hash: Hash of the query to potentially tag

    Returns:
        The tag name if saved (new or existing), None otherwise
    """
    try:
        registry = QueryRegistry()
        entry = registry.get_query(query_hash)

        if not entry:
            return None

        # Already has a tag - return it
        if entry.tag:
            return entry.tag

        # Prompt for tag
        console.print(
            f"\n[{StyleTokens.MUTED}]Save this query with a name for easy access later?[/{StyleTokens.MUTED}]"
        )
        tag_name = Prompt.ask(
            "   Name (leave blank to skip)", default="", show_default=False
        ).strip()

        if tag_name:
            # Check if tag already exists
            existing = registry.get_query_by_tag(tag_name)
            if existing and existing.hash != query_hash:
                console.print(
                    MessagePanel(
                        f"Name '{tag_name}' already used by another query. Skipping.",
                        variant="warning",
                    )
                )
                return None

            # Update the tag
            registry.update_query_tag(query_hash, tag_name)
            console.print(
                f"   [{StyleTokens.SUCCESS}]Saved as[/{StyleTokens.SUCCESS}] '{tag_name}'"
            )
            return tag_name

        return None
    except Exception:
        # Don't fail the exit flow if tagging fails
        return None


def _print_exit_message(query_hash: str, saved_name: Optional[str]) -> None:
    """
    Print the exit message with continue command(s).

    Args:
        query_hash: Hash of the query
        saved_name: Name if saved, None otherwise
    """
    steps = []
    if saved_name:
        steps.append(
            (
                f"rdst analyze --name {saved_name} --interactive",
                "Continue interactive analysis",
            )
        )

    steps.append(
        (
            f"rdst analyze --hash {query_hash} --interactive",
            "Continue interactive analysis",
        )
    )

    console.print(NextSteps(steps, title="Continue with"))

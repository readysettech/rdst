"""
Interactive Mode for RDST Analyze

Provides educational conversation mode after query analysis where users can ask
questions about recommendations and understand performance implications.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional, Dict, Any

from ..query_registry.conversation_registry import (
    ConversationRegistry,
    InteractiveConversation,
)
from ..query_registry.query_registry import QueryRegistry
from ..services.interactive_service import InteractiveService
from ..services.types import ChunkEvent, InteractiveCompleteEvent, InteractiveErrorEvent

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
    Live,
)

# Module-level console
console = get_console()


def _ask_llm_streaming(
    service: InteractiveService,
    query_hash: str,
    message: str,
    analysis_results: Dict[str, Any],
) -> str:
    """Send message to LLM via InteractiveService with streaming display.

    Args:
        service: InteractiveService instance
        query_hash: Hash of the query being discussed
        message: User's message/question
        analysis_results: Full analysis results for context

    Returns:
        Complete response text from LLM
    """

    async def _send_with_display() -> str:
        response_text = ""

        with Live(MarkdownContent(""), console=console, refresh_per_second=10) as live:
            async for event in service.send_message(
                query_hash=query_hash,
                message=message,
                analysis_results=analysis_results,
                continue_existing=True,
            ):
                if isinstance(event, ChunkEvent):
                    response_text += event.text
                    live.update(MarkdownContent(response_text))
                elif isinstance(event, InteractiveErrorEvent):
                    raise Exception(event.error)

        return response_text

    return asyncio.run(_send_with_display())


def run_interactive_mode(
    conversation: InteractiveConversation,
    analysis_results: Dict[str, Any],
) -> None:
    """
    Enter interactive mode for educational Q&A about analysis results.

    Args:
        conversation: InteractiveConversation object (may have existing messages from analyze)
        analysis_results: Full analysis results from workflow
    """
    conv_registry = ConversationRegistry()
    service = InteractiveService(conv_registry=conv_registry)

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
            console.print()
            response = _ask_llm_streaming(
                service=service,
                query_hash=conversation.query_hash,
                message=user_input,
                analysis_results=analysis_results,
            )
            console.print()

            # Reload conversation to sync state (service saves the exchange)
            reloaded = conv_registry.load_conversation(
                conversation.query_hash, conversation.provider
            )
            if reloaded:
                conversation = reloaded

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

    if not analysis_results or not analysis_results.get("explain_results"):
        console.print(
            MessagePanel(
                "Analysis results not available. Run full analysis to see summary.",
                variant="info",
            )
        )
        return

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

"""
Interactive Mode for RDST Analyze

Provides educational conversation mode after query analysis where users can ask
questions about recommendations and understand performance implications.
"""
from __future__ import annotations

import sys
from datetime import datetime
from typing import Optional, Dict, Any

from ..query_registry.conversation_registry import ConversationRegistry, InteractiveConversation
from ..query_registry.query_registry import QueryRegistry
from ..llm_manager.llm_manager import LLMManager

# Try to import Rich for colored output
try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    _RICH_AVAILABLE = True
    console = Console()
except ImportError:
    _RICH_AVAILABLE = False
    console = None


def run_interactive_mode(conversation: InteractiveConversation,
                        analysis_results: Dict[str, Any],
                        llm_manager: Optional[LLMManager] = None) -> None:
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

    # Display header with color
    if _RICH_AVAILABLE and console:
        console.print("\n" + "=" * 80, style="bold cyan")
        console.print("Interactive Mode - Explore the analysis", style="bold cyan")
        console.print("=" * 80, style="bold cyan")
    else:
        print("\n" + "=" * 80)
        print("Interactive Mode - Explore the analysis")
        print("=" * 80)

    # RDST uses Claude exclusively
    provider_name = "Claude"

    if conversation.total_exchanges == 0:
        if _RICH_AVAILABLE and console:
            console.print(f"\nYou can now interact with [bold green]{provider_name}[/bold green] to explore this query analysis.")
        else:
            print(f"\nYou can now interact with {provider_name} to explore this query analysis.")
    else:
        if _RICH_AVAILABLE and console:
            console.print(f"\nContinuing conversation with [bold green]{provider_name}[/bold green].")
        else:
            print(f"\nContinuing conversation with {provider_name}.")

    # If continuing conversation, show recent exchanges
    if conversation.total_exchanges > 0:
        if _RICH_AVAILABLE and console:
            console.print(f"\n[dim]Continuing conversation ({conversation.total_exchanges} exchanges so far)[/dim]")
            console.print("\nRecent conversation:", style="bold")
            console.print("-" * 80, style="dim")
        else:
            print(f"\nContinuing conversation ({conversation.total_exchanges} exchanges so far)")
            print("\nRecent conversation:")
            print("-" * 80)

        # Get user/assistant messages (not system messages)
        user_assistant_msgs = conversation.get_user_assistant_messages()

        # Show last 3 exchanges (6 messages: 3 user + 3 assistant)
        recent_count = min(6, len(user_assistant_msgs))
        start_idx = len(user_assistant_msgs) - recent_count

        for msg in user_assistant_msgs[start_idx:]:
            if msg.role == "user":
                if _RICH_AVAILABLE and console:
                    console.print(f"\n[bold cyan]You:[/bold cyan] {msg.content}")
                else:
                    print(f"\nYou: {msg.content}")
            elif msg.role == "assistant":
                if _RICH_AVAILABLE and console:
                    console.print(f"\n[bold green]{provider_name}:[/bold green]")
                    console.print(Markdown(msg.content))
                else:
                    print(f"\n{provider_name}: {msg.content}")

        if _RICH_AVAILABLE and console:
            console.print("\n" + "-" * 80, style="dim")
        else:
            print("\n" + "-" * 80)

    # Show command hints with color
    if _RICH_AVAILABLE and console:
        console.print("\n[dim]Ask questions about the recommendations, or type[/dim] [bold yellow]'help'[/bold yellow] [dim]for commands.[/dim]")
        console.print("[dim]Type[/dim] [bold yellow]'exit'[/bold yellow] [dim]or[/dim] [bold yellow]'quit'[/bold yellow] [dim]to end the session.[/dim]\n")
    else:
        print("\nAsk questions about the recommendations, or type 'help' for commands.")
        print("Type 'exit' or 'quit' to end the session.")
        print()

    # REPL loop
    while True:
        try:
            user_input = input("> ").strip()

            if not user_input:
                continue

            # Handle exit
            if user_input.lower() in ['exit', 'quit', 'q']:
                conv_registry.save_conversation(conversation)
                saved_name = _prompt_for_tag_if_needed(conversation.query_hash)
                _print_exit_message(conversation.query_hash, saved_name)
                break

            # Handle help
            if user_input.lower() == 'help':
                _show_help()
                continue

            # Handle summary
            if user_input.lower() == 'summary':
                _show_analysis_summary(analysis_results)
                continue

            # Handle review
            if user_input.lower() == 'review':
                display_conversation_history(conversation)
                continue

            # Free-form question - send to LLM (always uses Claude)

            if _RICH_AVAILABLE and console:
                console.print(f"\n[dim]Getting response from {provider_name}...[/dim]", end="")
            else:
                print(f"\nGetting response from {provider_name}...", end="", flush=True)

            response = _ask_llm(conversation, user_input, llm_manager)

            if response:
                # Clear the "Calling AI..." line
                if _RICH_AVAILABLE and console:
                    console.print("\r" + " " * 30 + "\r", end='')
                    # Render response as markdown with syntax highlighting
                    console.print("\n")
                    console.print(Markdown(response))
                    console.print()
                else:
                    print("\r" + " " * 30 + "\r", end='')  # Clear the line
                    print(f"\n{response}\n")

                # Add exchange to conversation and save
                conversation.add_exchange(user_input, response)
                conv_registry.save_conversation(conversation)

                # Simple warning for long conversations
                if conversation.total_exchanges >= 50:
                    print("Note: This conversation has 50+ exchanges. Consider starting fresh if responses slow down.\n")

        except KeyboardInterrupt:
            print("\n\nExiting interactive mode.")
            conv_registry.save_conversation(conversation)
            saved_name = _prompt_for_tag_if_needed(conversation.query_hash)
            _print_exit_message(conversation.query_hash, saved_name)
            break
        except Exception as e:
            print(f"\nError: {e}\n")
            continue


def display_conversation_history(conversation: InteractiveConversation,
                                show_system_messages: bool = False) -> None:
    """
    Display conversation history.

    Args:
        conversation: InteractiveConversation to display
        show_system_messages: If True, show system messages (default: False)
    """
    print("\n" + "=" * 80)
    print("Conversation History")
    print("=" * 80)
    print(f"Started: {conversation.started_at}")
    print(f"Total exchanges: {conversation.total_exchanges}")
    print(f"Provider: {conversation.provider} ({conversation.model})")
    print()

    if not conversation.messages:
        print("No messages yet.")
        print()
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
            print(f"[{timestamp_str}]")
            print(f"You: {msg.content}")
            print()
        elif msg.role == "assistant":
            print(f"AI: {msg.content}")
            print()
        elif msg.role == "system" and show_system_messages:
            print(f"[{timestamp_str}] [SYSTEM MESSAGE]")
            print(f"{msg.content[:200]}...")  # Truncate system messages
            print()

    print("=" * 80 + "\n")


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


def _ask_llm(conversation: InteractiveConversation,
            user_question: str,
            llm_manager: LLMManager) -> Optional[str]:
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
        system_messages = [msg["content"] for msg in messages if msg["role"] == "system"]
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
        print(f"Error calling LLM: {e}")
        return None


def _show_help() -> None:
    """Display help for interactive mode commands."""
    print("\n" + "=" * 80)
    print("Interactive Mode Commands")
    print("=" * 80)
    print()
    print("Commands:")
    print("  help          Show this help message")
    print("  exit / quit   Exit interactive mode")
    print("  summary       Re-display analysis summary")
    print("  review        Show full conversation history")
    print()
    print("Free-form Questions:")
    print("  Just type your question and press Enter")
    print()
    print("Examples:")
    print('  "Why did you recommend an index on post_type_id?"')
    print('  "What\'s the tradeoff of adding this index?"')
    print('  "What if my table has heavy writes?"')
    print('  "Can you explain what a full table scan means?"')
    print()
    print("=" * 80 + "\n")


def _show_analysis_summary(analysis_results: Dict[str, Any]) -> None:
    """
    Display a brief summary of the analysis results.

    Args:
        analysis_results: Analysis results from workflow
    """
    print("\n" + "=" * 80)
    print("Analysis Summary")
    print("=" * 80)
    print()

    # Extract key information
    explain_results = analysis_results.get("explain_results", {})
    llm_analysis = analysis_results.get("llm_analysis", {})

    # Performance metrics
    exec_time = explain_results.get("execution_time_ms", 0)
    rows_examined = explain_results.get("rows_examined", 0)
    rows_returned = explain_results.get("rows_returned", 0)

    print(f"Execution Time: {exec_time:.1f}ms")
    print(f"Rows Examined: {rows_examined:,}")
    print(f"Rows Returned: {rows_returned:,}")
    print()

    # Index recommendations
    index_recs = llm_analysis.get("index_recommendations", [])
    if index_recs:
        print(f"Index Recommendations: {len(index_recs)}")
        for i, rec in enumerate(index_recs, 1):
            print(f"  [{i}] {rec.get('sql', 'N/A')}")
        print()

    # Rewrite suggestions
    rewrite_sug = llm_analysis.get("rewrite_suggestions", [])
    if rewrite_sug:
        print(f"Query Rewrites: {len(rewrite_sug)}")
        for i, sug in enumerate(rewrite_sug, 1):
            print(f"  [{i}] {sug.get('description', 'N/A')}")
        print()
    else:
        print("Query Rewrites: None recommended")
        print()

    print("=" * 80 + "\n")


def _format_timestamp(timestamp_str: str) -> str:
    """Format ISO timestamp for display."""
    try:
        dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
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
        if _RICH_AVAILABLE and console:
            console.print("\n[dim]💾 Save this query with a name for easy access later?[/dim]")
            tag_name = input("   Name (leave blank to skip): ").strip()
        else:
            print("\n💾 Save this query with a name for easy access later?")
            tag_name = input("   Name (leave blank to skip): ").strip()

        if tag_name:
            # Check if tag already exists
            existing = registry.get_query_by_tag(tag_name)
            if existing and existing.hash != query_hash:
                print(f"   Name '{tag_name}' already used by another query. Skipping.")
                return None

            # Update the tag
            registry.update_query_tag(query_hash, tag_name)
            if _RICH_AVAILABLE and console:
                console.print(f"   [green]✓ Saved as '{tag_name}'[/green]")
            else:
                print(f"   ✓ Saved as '{tag_name}'")
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
    print(f"\nConversation saved. Continue with:")

    if saved_name:
        # Show both options - name first (easier), then hash
        if _RICH_AVAILABLE and console:
            console.print(f"  [cyan]rdst analyze --name {saved_name} --interactive[/cyan]")
            console.print(f"  [dim]rdst analyze --hash {query_hash} --interactive[/dim]")
        else:
            print(f"  rdst analyze --name {saved_name} --interactive")
            print(f"  rdst analyze --hash {query_hash} --interactive")
    else:
        # Only hash available
        if _RICH_AVAILABLE and console:
            console.print(f"  [cyan]rdst analyze --hash {query_hash} --interactive[/cyan]")
        else:
            print(f"  rdst analyze --hash {query_hash} --interactive")
    print()

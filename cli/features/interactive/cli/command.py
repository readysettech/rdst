"""Interactive mode helpers for analyze CLI."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from features.interactive.events import (
    ChunkEvent,
    InteractiveErrorEvent,
)
from features.interactive.service import InteractiveService
from shared.query_registry import ConversationRegistry, InteractiveConversation, QueryRegistry
from shared.ui import (
    Banner,
    EmptyState,
    Live,
    MarkdownContent,
    MessagePanel,
    NextSteps,
    Prompt,
    Rule,
    SectionBox,
    SectionHeader,
    StatusLine,
    StyleTokens,
    get_console,
)

console = get_console()


def _ask_llm_streaming(
    service: InteractiveService,
    query_hash: str,
    message: str,
    analysis_results: dict[str, Any],
) -> str:
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
    analysis_results: dict[str, Any],
) -> None:
    conv_registry = ConversationRegistry()
    service = InteractiveService(conv_registry=conv_registry)
    console.print()
    console.print(Banner("Interactive Mode - Explore the analysis"))

    provider_name = "Claude"
    if conversation.total_exchanges == 0:
        console.print(
            f"\nYou can now interact with [{StyleTokens.STATUS_SUCCESS}]{provider_name}[/{StyleTokens.STATUS_SUCCESS}] to explore this query analysis."
        )
    else:
        console.print(
            f"\nContinuing conversation with [{StyleTokens.STATUS_SUCCESS}]{provider_name}[/{StyleTokens.STATUS_SUCCESS}]."
        )

    if conversation.total_exchanges > 0:
        console.print(
            f"\n[{StyleTokens.MUTED}]Continuing conversation ({conversation.total_exchanges} exchanges so far)[/{StyleTokens.MUTED}]"
        )
        console.print(SectionHeader("Recent conversation"))
        console.print(Rule())
        user_assistant_msgs = conversation.get_user_assistant_messages()
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

    console.print(
        f"\n[{StyleTokens.MUTED}]Ask questions about the recommendations, or type[/{StyleTokens.MUTED}] [{StyleTokens.STATUS_WARNING}]help[/{StyleTokens.STATUS_WARNING}] [{StyleTokens.MUTED}]for commands.[/{StyleTokens.MUTED}]"
    )
    console.print(
        f"[{StyleTokens.MUTED}]Type[/{StyleTokens.MUTED}] [{StyleTokens.STATUS_WARNING}]exit[/{StyleTokens.STATUS_WARNING}] [{StyleTokens.MUTED}]or[/{StyleTokens.MUTED}] [{StyleTokens.STATUS_WARNING}]quit[/{StyleTokens.STATUS_WARNING}] [{StyleTokens.MUTED}]to end the session.[/{StyleTokens.MUTED}]\n"
    )

    while True:
        try:
            user_input = Prompt.ask(">", default="", show_default=False).strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit", "q"]:
                conv_registry.save_conversation(conversation)
                saved_name = _prompt_for_tag_if_needed(conversation.query_hash)
                _print_exit_message(conversation.query_hash, saved_name)
                break
            if user_input.lower() == "help":
                _show_help()
                continue
            if user_input.lower() == "summary":
                _show_analysis_summary(analysis_results)
                continue
            if user_input.lower() == "review":
                display_conversation_history(conversation)
                continue

            console.print()
            _ask_llm_streaming(service, conversation.query_hash, user_input, analysis_results)
            console.print()
            reloaded = conv_registry.load_conversation(
                conversation.query_hash, conversation.provider
            )
            if reloaded:
                conversation = reloaded
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
        except Exception as exc:
            console.print(MessagePanel(f"Error: {exc}", variant="error"))
            continue


def display_conversation_history(
    conversation: InteractiveConversation, show_system_messages: bool = False
) -> None:
    console.print()
    console.print(Banner("Conversation History"))
    console.print(StatusLine("Started", str(conversation.started_at)))
    console.print(StatusLine("Total exchanges", str(conversation.total_exchanges)))
    console.print(StatusLine("Provider", f"{conversation.provider} ({conversation.model})"))
    console.print()

    if not conversation.messages:
        console.print(EmptyState("No messages yet."))
        console.print()
        return

    messages_to_show = conversation.messages if show_system_messages else conversation.get_user_assistant_messages()
    for msg in messages_to_show:
        timestamp_str = _format_timestamp(msg.timestamp)
        if msg.role == "user":
            console.print(f"[{StyleTokens.MUTED}]{timestamp_str}[/{StyleTokens.MUTED}]")
            console.print(f"[{StyleTokens.HEADER}]You:[/{StyleTokens.HEADER}] {msg.content}")
            console.print()
        elif msg.role == "assistant":
            console.print(f"[{StyleTokens.STATUS_SUCCESS}]AI:[/{StyleTokens.STATUS_SUCCESS}] {msg.content}")
            console.print()
        elif msg.role == "system" and show_system_messages:
            console.print(f"[{StyleTokens.MUTED}]{timestamp_str}[/{StyleTokens.MUTED}] [SYSTEM MESSAGE]")
            console.print(f"{msg.content[:200]}...")
            console.print()

    console.print(Rule())
    console.print()


def _show_help() -> None:
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
    console.print(SectionBox("Free-form Questions", content="  Just type your question and press Enter"))
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


def _show_analysis_summary(analysis_results: dict[str, Any]) -> None:
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
    explain_results = analysis_results.get("explain_results", {})
    llm_analysis = analysis_results.get("llm_analysis", {})
    exec_time = explain_results.get("execution_time_ms", 0)
    rows_examined = explain_results.get("rows_examined", 0)
    rows_returned = explain_results.get("rows_returned", 0)
    console.print(StatusLine("Execution Time", f"{exec_time:.1f}ms"))
    console.print(StatusLine("Rows Examined", f"{rows_examined:,}"))
    console.print(StatusLine("Rows Returned", f"{rows_returned:,}"))
    console.print()
    index_recs = llm_analysis.get("index_recommendations", [])
    if index_recs:
        index_content = "\n".join([f"  [{i}] {rec.get('sql', 'N/A')}" for i, rec in enumerate(index_recs, 1)])
        console.print(SectionBox("Index Recommendations", content=index_content))
        console.print()
    rewrite_sug = llm_analysis.get("rewrite_suggestions", [])
    if rewrite_sug:
        rewrite_content = "\n".join([f"  [{i}] {sug.get('description', 'N/A')}" for i, sug in enumerate(rewrite_sug, 1)])
        console.print(SectionBox("Query Rewrites", content=rewrite_content))
        console.print()
    else:
        console.print(SectionBox("Query Rewrites", content="None recommended"))
        console.print()


def _format_timestamp(timestamp_str: str) -> str:
    try:
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return timestamp_str


def _prompt_for_tag_if_needed(query_hash: str) -> str | None:
    try:
        registry = QueryRegistry()
        entry = registry.get_query(query_hash)
        if not entry:
            return None
        if entry.tag:
            return entry.tag
        console.print(
            f"\n[{StyleTokens.MUTED}]Save this query with a name for easy access later?[/{StyleTokens.MUTED}]"
        )
        tag_name = Prompt.ask("   Name (leave blank to skip)", default="", show_default=False).strip()
        if tag_name:
            existing = registry.get_query_by_tag(tag_name)
            if existing and existing.hash != query_hash:
                console.print(
                    MessagePanel(
                        f"Name '{tag_name}' already used by another query. Skipping.",
                        variant="warning",
                    )
                )
                return None
            registry.update_query_tag(query_hash, tag_name)
            console.print(f"   [{StyleTokens.SUCCESS}]Saved as[/{StyleTokens.SUCCESS}] '{tag_name}'")
            return tag_name
        return None
    except Exception:
        return None


def _print_exit_message(query_hash: str, saved_name: str | None) -> None:
    steps = []
    if saved_name:
        steps.append((f"rdst analyze --name {saved_name} --interactive", "Continue interactive analysis"))
    steps.append((f"rdst analyze --hash {query_hash} --interactive", "Continue interactive analysis"))
    console.print(NextSteps(steps, title="Continue with"))

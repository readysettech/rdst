"""
Conversation Session Management for Agent Chat

Tracks conversation history to enable follow-up questions like
"break that down by month" or "same query but for last year".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ConversationTurn:
    """Single Q&A exchange in a conversation."""

    question: str
    sql: str | None = None
    result_summary: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ConversationSession:
    """
    Active chat session with history.

    Maintains a sliding window of recent conversation turns to provide
    context for follow-up questions without exceeding token limits.
    """

    agent_name: str
    turns: list[ConversationTurn] = field(default_factory=list)
    max_turns: int = 10

    def add_turn(self, turn: ConversationTurn) -> None:
        """Add a turn, pruning old history if needed."""
        self.turns.append(turn)
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns :]

    def clear(self) -> None:
        """Clear all conversation history."""
        self.turns = []

    def format_history(self) -> str:
        """
        Format conversation history for LLM context injection.

        Returns empty string if no history, otherwise returns a formatted
        summary of previous exchanges.
        """
        if not self.turns:
            return ""

        lines = ["## Previous Conversation Context"]
        lines.append("")
        lines.append(
            "The following exchanges have already occurred in this conversation. "
            "Use this context to understand references like 'that', 'those results', "
            "'same query but...', 'break that down by...', etc."
        )
        lines.append("")

        for i, turn in enumerate(self.turns, 1):
            lines.append(f"### Exchange {i}")
            lines.append(f"**User asked:** {turn.question}")
            if turn.sql:
                lines.append(f"**SQL generated:** `{turn.sql}`")
            if turn.result_summary:
                lines.append(f"**Result:** {turn.result_summary}")
            lines.append("")

        lines.append("---")
        lines.append("")

        return "\n".join(lines)

    def summarize_result(self, response: Any) -> str:
        """
        Create a compact summary of query results for history.

        Args:
            response: AgentResponse object with query results.

        Returns:
            Compact summary string like "5 rows: customer_id, name, total"
        """
        if not response.success:
            return f"Error: {response.error}"

        if not response.rows:
            return "No rows returned"

        # Summarize columns (first 5)
        col_str = ", ".join(response.columns[:5])
        if len(response.columns) > 5:
            col_str += f", ... ({len(response.columns)} total columns)"

        # Summarize row count
        row_info = f"{response.row_count} row{'s' if response.row_count != 1 else ''}"
        if response.truncated:
            row_info += " (truncated)"

        return f"{row_info}: {col_str}"

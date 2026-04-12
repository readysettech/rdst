"""
AgentExplorationContext - State for the agentic exploration mode.

When the linear flow fails (zero rows, low confidence, etc.), we escalate
to an agent that can iteratively explore the schema, sample data, and
refine its approach based on observations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import Ask3Context
    from .types import SchemaInfo


@dataclass
class QueryAttempt:
    """Record of a SQL query attempt during agent exploration."""

    sql: str
    result_rows: int = 0
    error: Optional[str] = None
    columns: List[str] = field(default_factory=list)
    sample_data: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            'sql': self.sql,
            'result_rows': self.result_rows,
            'error': self.error,
            'columns': self.columns,
            'sample_data': self.sample_data[:3]  # Limit for context size
        }


@dataclass
class AgentExplorationContext:
    """
    Tracks the agent's exploration state during iterative query refinement.

    This context is created when the linear flow escalates to agent mode.
    It preserves the original Ask3Context and adds agent-specific state.
    """

    # === From Ask3Context (preserved) ===
    question: str
    target: str
    db_type: str
    schema_info: Optional[SchemaInfo] = None

    # === Agent Exploration State ===
    explored_tables: Set[str] = field(default_factory=set)
    sampled_tables: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    attempted_queries: List[QueryAttempt] = field(default_factory=list)
    clarifications: Dict[str, str] = field(default_factory=dict)

    # === Iteration Control ===
    tool_call_count: int = 0
    max_tool_calls: int = 10

    # === Escalation Info ===
    escalation_reason: str = ""
    linear_context: Optional[Ask3Context] = None  # Original context before escalation

    # === Final Result ===
    final_sql: Optional[str] = None
    final_explanation: Optional[str] = None

    # === LLM Tracking ===
    agent_llm_calls: List[Dict[str, Any]] = field(default_factory=list)
    total_agent_tokens: int = 0

    @classmethod
    def from_ask3_context(
        cls,
        ctx: 'Ask3Context',
        reason: str
    ) -> 'AgentExplorationContext':
        """
        Create agent context from linear context at escalation point.

        Args:
            ctx: The Ask3Context from the linear flow
            reason: Why we escalated (e.g., 'zero_rows', 'low_confidence')

        Returns:
            AgentExplorationContext ready for agent exploration
        """
        agent_ctx = cls(
            question=ctx.question,
            target=ctx.target,
            db_type=ctx.db_type,
            schema_info=ctx.schema_info,
            escalation_reason=reason,
            linear_context=ctx,
        )

        # Carry over already explored tables
        if ctx.filtered_tables:
            agent_ctx.explored_tables = set(ctx.filtered_tables)

        # Carry over any clarifications collected in linear flow
        if ctx.clarifications:
            agent_ctx.clarifications = ctx.clarifications.copy()

        # Record the failed query attempt if there was one
        if ctx.sql:
            attempt = QueryAttempt(
                sql=ctx.sql,
                result_rows=ctx.execution_result.row_count if ctx.execution_result else 0,
                error=ctx.execution_result.error if ctx.execution_result else None,
                columns=ctx.execution_result.columns if ctx.execution_result else [],
            )
            agent_ctx.attempted_queries.append(attempt)

        return agent_ctx

    def to_ask3_context(self) -> 'Ask3Context':
        """
        Convert back to Ask3Context after agent finds a solution.

        Merges agent findings into the original context.
        """
        if self.linear_context is None:
            raise ValueError("Cannot convert to Ask3Context without linear_context")

        ctx = self.linear_context

        # Update with agent findings
        if self.final_sql:
            ctx.sql = self.final_sql
            ctx.sql_explanation = self.final_explanation

        # Merge clarifications
        ctx.clarifications.update(self.clarifications)

        # Track agent LLM usage
        ctx.total_tokens += self.total_agent_tokens
        for call in self.agent_llm_calls:
            ctx.llm_calls.append(call)

        return ctx

    def increment_tool_call(self) -> None:
        """Increment tool call counter."""
        self.tool_call_count += 1

    def can_continue(self) -> bool:
        """Check if agent can make more tool calls."""
        return self.tool_call_count < self.max_tool_calls

    def record_table_exploration(self, table_name: str) -> None:
        """Record that a table was explored."""
        self.explored_tables.add(table_name)

    def record_table_sample(
        self,
        table_name: str,
        sample_data: List[Dict[str, Any]]
    ) -> None:
        """Record sample data from a table."""
        self.sampled_tables[table_name] = sample_data
        self.explored_tables.add(table_name)

    def record_query_attempt(
        self,
        sql: str,
        result_rows: int = 0,
        error: Optional[str] = None,
        columns: Optional[List[str]] = None,
        sample_data: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """Record a SQL query attempt."""
        self.attempted_queries.append(QueryAttempt(
            sql=sql,
            result_rows=result_rows,
            error=error,
            columns=columns or [],
            sample_data=sample_data or []
        ))

    def record_clarification(self, question: str, answer: str) -> None:
        """Record a user clarification."""
        # Use a sanitized key
        key = question[:50].replace(' ', '_').lower()
        self.clarifications[key] = answer

    def add_llm_call(
        self,
        prompt_preview: str,
        response_preview: str,
        tokens: int,
        latency_ms: float,
        model: str
    ) -> None:
        """Track an agent LLM call."""
        self.agent_llm_calls.append({
            'prompt_preview': prompt_preview[:200],
            'response_preview': response_preview[:200],
            'tokens': tokens,
            'latency_ms': latency_ms,
            'model': model,
            'phase': 'agent'
        })
        self.total_agent_tokens += tokens

    def get_exploration_summary(self) -> str:
        """
        Get a summary of what the agent has explored so far.

        Used in agent prompts to provide context.
        """
        lines = []

        if self.explored_tables:
            lines.append(f"Explored tables: {', '.join(sorted(self.explored_tables))}")

        if self.sampled_tables:
            for table, samples in self.sampled_tables.items():
                if samples:
                    cols = list(samples[0].keys()) if samples else []
                    lines.append(f"Sampled {table}: {len(samples)} rows, columns: {cols}")

        if self.attempted_queries:
            lines.append(f"Query attempts: {len(self.attempted_queries)}")
            for i, attempt in enumerate(self.attempted_queries[-3:], 1):  # Last 3
                status = f"{attempt.result_rows} rows" if not attempt.error else f"error: {attempt.error[:50]}"
                lines.append(f"  {i}. {status}")

        if self.clarifications:
            lines.append(f"Clarifications: {len(self.clarifications)}")
            for q, a in list(self.clarifications.items())[:3]:
                lines.append(f"  - {q}: {a}")

        return '\n'.join(lines) if lines else "No exploration yet"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for logging/debugging."""
        return {
            'question': self.question,
            'target': self.target,
            'db_type': self.db_type,
            'escalation_reason': self.escalation_reason,
            'explored_tables': list(self.explored_tables),
            'sampled_tables': list(self.sampled_tables.keys()),
            'attempted_queries': [a.to_dict() for a in self.attempted_queries],
            'clarifications': self.clarifications,
            'tool_call_count': self.tool_call_count,
            'max_tool_calls': self.max_tool_calls,
            'final_sql': self.final_sql,
            'total_agent_tokens': self.total_agent_tokens,
        }

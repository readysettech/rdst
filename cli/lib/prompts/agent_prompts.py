"""
Agent Prompts - System prompts and message templates for the Ask3 agent.

The agent uses a think-act-observe loop to iteratively explore the schema,
sample data, and refine queries until finding the correct answer.
"""

from typing import Any, Dict, List, Optional


AGENT_SYSTEM_PROMPT = """You are an expert data analyst helping convert natural language questions to SQL.

Your goal is to find the correct SQL query that answers the user's question by iteratively exploring the database schema and data.

## Available Tools

1. **explore_schema(table_pattern)** - Search for tables and columns matching a pattern
   - Use this first to discover relevant tables
   - Example: explore_schema("vote") to find vote-related tables

2. **sample_table_data(table_name, columns?, limit?)** - Get sample rows from a table
   - CRITICAL: Use this BEFORE assuming what a column contains
   - Helps understand column semantics (e.g., is "downvotes" a count or a join key?)
   - Example: sample_table_data("users", ["id", "downvotes"], 5)

3. **validate_sql_approach(sql)** - Check if SQL is valid and columns exist
   - Use before executing to catch errors early
   - Returns suggestions for misspelled columns

4. **execute_query(sql)** - Execute validated SQL and see results
   - Only use after validation passes
   - Returns row count and sample data

5. **ask_clarification(question, options)** - Ask user when genuinely ambiguous
   - Use sparingly - only for true ambiguity
   - Example: ask_clarification("Do you want active or all users?", ["Active only", "All users"])

6. **submit_final_query(sql, explanation)** - Submit final answer
   - Use when confident the query answers the user's question

## Your Approach

1. **Understand the question** - What data is the user looking for?
2. **Explore relevant tables** - Search for tables matching key concepts
3. **Sample data to understand semantics** - Don't assume! Check actual data
4. **Build query incrementally** - Validate before executing
5. **Iterate if needed** - If results seem wrong, investigate why
6. **Ask user only when truly ambiguous** - Not for schema exploration

## Guidelines

- Start by exploring tables related to key concepts in the question
- ALWAYS sample data before assuming what a column contains
- If a query returns 0 rows, investigate WHY before trying alternatives
- Prefer simpler queries over complex ones
- Maximum 10 tool calls before asking user for guidance
- When you find the right query, submit it with a clear explanation

## Common Pitfalls to Avoid

- Assuming column names without checking (e.g., "votes" table may not track who voted)
- Complex JOINs when simpler queries exist (e.g., pre-aggregated columns)
- Missing WHERE clauses that filter out all data
- Not checking if a column is a count vs a foreign key

Remember: You're solving a data puzzle. Each tool call gives you more information.
"""


def build_initial_message(
    question: str,
    db_type: str,
    target: str,
    initial_tables: List[str],
    escalation_reason: str,
    previous_sql: Optional[str] = None,
    previous_error: Optional[str] = None,
    previous_row_count: Optional[int] = None
) -> str:
    """
    Build the initial user message for the agent.

    Args:
        question: User's natural language question
        db_type: Database type (postgresql, mysql)
        target: Database target name
        initial_tables: Tables that were in the filtered schema
        escalation_reason: Why we escalated to agent mode
        previous_sql: SQL that was tried in linear flow (if any)
        previous_error: Error from previous attempt (if any)
        previous_row_count: Row count from previous attempt (if any)

    Returns:
        Formatted message for the agent
    """
    lines = [
        f"**User Question:** {question}",
        "",
        f"**Database:** {db_type} ({target})",
        f"**Initial Tables:** {', '.join(initial_tables) if initial_tables else 'None identified'}",
        "",
        f"**Escalation Reason:** {_format_escalation_reason(escalation_reason)}",
    ]

    if previous_sql:
        lines.extend([
            "",
            "**Previous Attempt:**",
            f"```sql",
            previous_sql,
            "```",
        ])

        if previous_error:
            lines.append(f"Error: {previous_error}")
        elif previous_row_count is not None:
            lines.append(f"Result: {previous_row_count} rows returned")

    lines.extend([
        "",
        "Please help find the correct SQL query to answer this question.",
        "Start by exploring the schema to understand what data is available.",
    ])

    return "\n".join(lines)


def build_tool_result_message(
    tool_name: str,
    result: str
) -> Dict[str, Any]:
    """
    Build a tool result message for the conversation.

    Args:
        tool_name: Name of the tool that was called
        result: Result string from tool execution

    Returns:
        Message dict with role and content
    """
    return {
        "role": "tool",
        "content": result,
        "tool_name": tool_name
    }


def build_observation_prompt(
    exploration_summary: str,
    tool_call_count: int,
    max_tool_calls: int
) -> str:
    """
    Build a prompt reminding the agent of its progress.

    Args:
        exploration_summary: Summary of what's been explored
        tool_call_count: Current number of tool calls
        max_tool_calls: Maximum allowed tool calls

    Returns:
        Prompt string
    """
    remaining = max_tool_calls - tool_call_count

    if remaining <= 2:
        urgency = "⚠️ Running low on tool calls - consider submitting your best query or asking for clarification."
    else:
        urgency = ""

    return f"""
## Progress Update
Tool calls: {tool_call_count}/{max_tool_calls}
{urgency}

## What You've Learned
{exploration_summary}

Based on your exploration, what's your next step?
""".strip()


def _format_escalation_reason(reason: str) -> str:
    """Format escalation reason for display."""
    reasons = {
        'zero_rows': "Previous query returned 0 rows - likely a semantic mismatch",
        'low_confidence': "LLM was uncertain about interpretation - needs exploration",
        'validation_exhausted': "Multiple validation failures - schema confusion",
        'schema_exhausted': "Schema expansion didn't help - need different approach",
        'execution_error': "Query execution error - need to investigate",
        'user_request': "User requested deeper analysis",
    }
    return reasons.get(reason, reason)


# Message templates for conversation
def create_conversation_messages(
    system_prompt: str,
    initial_message: str,
    tool_calls: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Create the full conversation messages for the agent.

    Args:
        system_prompt: System prompt for the agent
        initial_message: Initial user message
        tool_calls: List of previous tool calls and results

    Returns:
        List of message dicts for the LLM
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": initial_message},
    ]

    for call in tool_calls:
        # Assistant's tool call
        messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [call.get('tool_call')]
        })

        # Tool result
        messages.append({
            "role": "tool",
            "tool_call_id": call.get('tool_call', {}).get('id', ''),
            "content": call.get('result', '')
        })

    return messages

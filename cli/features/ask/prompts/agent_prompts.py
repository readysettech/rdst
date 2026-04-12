"""
Agent prompts and message templates for the Ask3 agent.
"""

from typing import Any, Dict, List, Optional


AGENT_SYSTEM_PROMPT = """You are an expert data analyst helping convert natural language questions to SQL.

Your goal is to find the correct SQL query that answers the user's question by iteratively exploring the database schema and data.

## Available Tools

1. **explore_schema(table_pattern)** - Search for tables and columns matching a pattern
2. **sample_table_data(table_name, columns?, limit?)** - Get sample rows from a table
3. **validate_sql_approach(sql)** - Check if SQL is valid and columns exist
4. **execute_query(sql)** - Execute validated SQL and see results
5. **ask_clarification(question, options)** - Ask user when genuinely ambiguous
6. **submit_final_query(sql, explanation)** - Submit final answer

## Your Approach

1. Understand the question
2. Explore relevant tables
3. Sample data to understand semantics
4. Build query incrementally
5. Iterate if needed
6. Ask user only when truly ambiguous

## Guidelines

- Start by exploring tables related to key concepts in the question
- ALWAYS sample data before assuming what a column contains
- If a query returns 0 rows, investigate WHY before trying alternatives
- Prefer simpler queries over complex ones
- Maximum 10 tool calls before asking user for guidance
- When you find the right query, submit it with a clear explanation

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
    previous_row_count: Optional[int] = None,
) -> str:
    lines = [
        f"**User Question:** {question}",
        "",
        f"**Database:** {db_type} ({target})",
        f"**Initial Tables:** {', '.join(initial_tables) if initial_tables else 'None identified'}",
        "",
        f"**Escalation Reason:** {_format_escalation_reason(escalation_reason)}",
    ]

    if previous_sql:
        lines.extend(
            [
                "",
                "**Previous Attempt:**",
                "```sql",
                previous_sql,
                "```",
            ]
        )
        if previous_error:
            lines.append(f"Error: {previous_error}")
        elif previous_row_count is not None:
            lines.append(f"Result: {previous_row_count} rows returned")

    lines.extend(
        [
            "",
            "Please help find the correct SQL query to answer this question.",
            "Start by exploring the schema to understand what data is available.",
        ]
    )
    return "\n".join(lines)


def build_tool_result_message(tool_name: str, result: str) -> Dict[str, Any]:
    return {"role": "tool", "content": result, "tool_name": tool_name}


def build_observation_prompt(
    exploration_summary: str, tool_call_count: int, max_tool_calls: int
) -> str:
    remaining = max_tool_calls - tool_call_count
    urgency = (
        "⚠️ Running low on tool calls - consider submitting your best query or asking for clarification."
        if remaining <= 2
        else ""
    )
    return f"""
## Progress Update
Tool calls: {tool_call_count}/{max_tool_calls}
{urgency}

## What You've Learned
{exploration_summary}

Based on your exploration, what's your next step?
""".strip()


def _format_escalation_reason(reason: str) -> str:
    reasons = {
        "zero_rows": "Previous query returned 0 rows - likely a semantic mismatch",
        "low_confidence": "LLM was uncertain about interpretation - needs exploration",
        "validation_exhausted": "Multiple validation failures - schema confusion",
        "schema_exhausted": "Schema expansion didn't help - need different approach",
        "execution_error": "Query execution error - need to investigate",
        "user_request": "User requested deeper analysis",
    }
    return reasons.get(reason, reason)


def create_conversation_messages(
    system_prompt: str, initial_message: str, tool_calls: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": initial_message},
    ]

    for call in tool_calls:
        messages.append(call)

    return messages

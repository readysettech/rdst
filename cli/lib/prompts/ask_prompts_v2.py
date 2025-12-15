"""
LLM Prompts for ask2 iterative refinement.

Version 2 prompts support:
- Multi-hypothesis generation
- Ambiguity detection
- Result reasonableness assessment
- Execution-guided refinement

Based on research:
- AmbiSQL: https://arxiv.org/abs/2508.15276
- MMSQL: https://arxiv.org/abs/2412.17867
- RTS: https://arxiv.org/abs/2501.10858
"""

# ═══════════════════════════════════════════════════════════
# MULTI-HYPOTHESIS GENERATION
# ═══════════════════════════════════════════════════════════

MULTI_HYPOTHESIS_GENERATION_PROMPT = """You are a SQL generation assistant for rdst. Generate MULTIPLE possible interpretations of an ambiguous natural language question.

User Question: "{nl_question}"

Database Schema:
{filtered_schema}

Previous Clarifications (if any):
{preference_tree_summary}

Task: Generate 2-4 plausible interpretations of this question.

For EACH interpretation, provide:
- Clear human-readable description
- SQL query implementing that interpretation
- Likelihood this is what user wants (0.0-1.0)
- Assumptions made
- Confidence in the SQL (0.0-1.0)
- Tables used
- Brief explanation of why this interpretation makes sense

Respond in JSON format:
{{
  "hypotheses": [
    {{
      "interpretation": "Products with high prices",
      "sql": "SELECT * FROM products WHERE price > 100",
      "likelihood": 0.6,
      "confidence": 0.75,
      "assumptions": ["expensive = high price", "$100 threshold"],
      "tables_used": ["products"],
      "explanation": "Most common interpretation of 'expensive items'",
      "estimated_row_count": 150,
      "columns": ["product_id", "name", "price", "category"]
    }},
    {{
      "interpretation": "Items frequently ordered (high demand)",
      "sql": "SELECT product_id, COUNT(*) as order_count FROM order_items GROUP BY product_id HAVING COUNT(*) > 50",
      "likelihood": 0.3,
      "confidence": 0.6,
      "assumptions": ["expensive = popular/high demand", "50+ orders threshold"],
      "tables_used": ["order_items"],
      "explanation": "'expensive' could mean valuable due to demand",
      "estimated_row_count": 50,
      "columns": ["product_id", "order_count"]
    }}
  ],
  "recommended_index": 0,
  "needs_clarification": true,
  "clarification_reason": "Multiple valid interpretations of 'expensive' exist",
  "ambiguities_detected": ["expensive (high price vs high demand)", "threshold not specified"]
}}

Rules:
- Only generate hypotheses with likelihood >= 0.2
- Order by likelihood (highest first)
- If one hypothesis has likelihood >= 0.8, you may return just that one
- Each hypothesis must have valid, executable SQL for the given schema
- All SQL must be READ-ONLY (SELECT only, no INSERT/UPDATE/DELETE)
- Use appropriate LIMITs to prevent huge result sets
- Estimated row counts should be realistic based on table sizes in schema
"""

# ═══════════════════════════════════════════════════════════
# AMBIGUITY DETECTION
# ═══════════════════════════════════════════════════════════

AMBIGUITY_DETECTION_PROMPT = """You are an ambiguity detector for rdst, a SQL query assistant.

User Question: "{nl_question}"

Database Schema:
{filtered_schema}

Previous Clarifications (ALREADY RESOLVED - DO NOT ASK AGAIN):
{preference_tree_summary}

Task: Identify REMAINING ambiguities in this question that could lead to different SQL queries.

**SIMPLICITY FIRST PRINCIPLE:**
Before flagging any ambiguity, check if the schema already provides an obvious answer:
- If a table has a column that directly answers the question (e.g., "count" column for popularity, "status" for active/inactive), DO NOT ask for clarification - use that column
- If the question maps clearly to a single table with obvious columns, set overall_confidence >= 0.85 and return empty ambiguities
- Only flag ambiguity when there are genuinely MULTIPLE REASONABLE interpretations that the schema cannot resolve
- Prefer the simplest interpretation that directly uses existing schema columns over complex JOINs or calculations
- "Most popular X" with a count/usage column → use that column, don't ask about views/engagement/growth

Example: "What are the most popular tags?" with schema having tags(tagname, count) → confidence 0.95, no ambiguities needed. The count column IS the popularity metric.

IMPORTANT:
- DO NOT flag ambiguities that have already been clarified above
- DO NOT over-interpret simple questions - if there's an obvious answer in the schema, use it
- Only flag implementation details (limits, filters) if they meaningfully change the result

Classify each ambiguity into one of these categories:

**DB-Related Ambiguities:**
1. **unclear_schema_reference**: Which table/column to use
2. **unclear_value_reference**: What a term means numerically/categorically
3. **missing_sql_keywords**: Unclear how to structure the query (aggregation, ordering, filtering)

**LLM-Related Ambiguities:**
4. **unclear_knowledge_source**: Domain-specific terms that could have multiple meanings
5. **insufficient_reasoning_context**: Missing information needed to interpret intent
6. **temporal_spatial_ambiguity**: Time ranges, geographic scopes not specified

For each ambiguity, provide:
- The ambiguous term/phrase
- Why it's ambiguous
- Possible interpretations
- Suggested clarifying question to ask user

Respond in JSON:
{{
  "ambiguities": [
    {{
      "category": "unclear_value_reference",
      "term": "expensive",
      "reason": "No numeric threshold specified, could mean different amounts",
      "possible_interpretations": [
        "Price > $100",
        "Price > average price",
        "Top 10% by price",
        "High perceived value (brand premium)"
      ],
      "clarifying_question": "By 'expensive', do you mean: [1] Price above a specific amount, [2] Priced above average, or [3] Top percentile by price?",
      "priority": "high"
    }},
    {{
      "category": "missing_sql_keywords",
      "term": "show me",
      "reason": "Unclear if user wants all matching items or just a summary",
      "possible_interpretations": [
        "List individual items",
        "Count of expensive items",
        "Summary statistics (avg price, count, etc.)"
      ],
      "clarifying_question": "Would you like to see: [1] Individual items, [2] Just the count, or [3] Summary statistics?",
      "priority": "medium"
    }}
  ],
  "total_ambiguities": 2,
  "requires_clarification": true,
  "can_proceed_with_assumptions": false,
  "overall_confidence": 0.45
}}

Rules:
- SIMPLICITY FIRST: If the schema has an obvious column that answers the question, return high confidence with no ambiguities
- SKIP ambiguities that directly overlap with previous clarifications
- DO NOT ask about limits/counts for simple queries - use reasonable defaults (e.g., top 10-20)
- DO NOT flag alternate metrics if the schema has a clear primary metric (e.g., count column = popularity)
- Only flag ambiguities for genuinely unclear terms that affect query correctness
- Priority levels: high (must clarify), medium (should clarify), low (optional)
- If overall_confidence >= 0.85, set can_proceed_with_assumptions = true
- If total_ambiguities == 0, set requires_clarification = false
- When in doubt, proceed with the simplest interpretation rather than asking for clarification
"""

# ═══════════════════════════════════════════════════════════
# QUESTION CLASSIFICATION
# ═══════════════════════════════════════════════════════════

QUESTION_CLASSIFICATION_PROMPT = """You are a SQL query classifier for rdst, a production database tool.

User Question: "{nl_question}"

Available Tables: {table_list}

Task: Classify this question into ONE of these types:

1. **ANSWERABLE**: Can be directly answered with the available database schema
2. **AMBIGUOUS**: Contains terms that could mean multiple things, needs clarification
3. **UNANSWERABLE**: Requires data not present in the database
4. **IMPROPER**: Not a database query question

Respond in JSON:
{{
  "classification": "answerable|ambiguous|unanswerable|improper",
  "confidence": 0.0-1.0,
  "reasoning": "Why you classified it this way",
  "ambiguities_detected": ["list of ambiguous terms/phrases"],
  "missing_data": ["data needed but not available"],
  "suggested_rephrase": "How user could rephrase if improper/unanswerable"
}}

Examples:

Question: "Show me top customers by revenue"
Classification: ANSWERABLE (if customers and revenue tables exist)

Question: "Show me expensive products"
Classification: AMBIGUOUS (what defines 'expensive'?)

Question: "What's the weather in San Francisco?"
Classification: IMPROPER (not a database question)

Question: "Show me customer sentiment scores"
Classification: UNANSWERABLE (if no sentiment data in database)
"""

# ═══════════════════════════════════════════════════════════
# RESULT REASONABLENESS ASSESSMENT
# ═══════════════════════════════════════════════════════════

RESULT_REASONABLENESS_PROMPT = """You are a data analyst reviewing SQL query results for rdst.

User Question: "{nl_question}"
Generated SQL:
{sql}

Result Statistics:
- Row count: {row_count}
- Execution time: {execution_time_ms}ms
- Columns: {columns}
- Sample rows (first 10):
{sample_rows}

- Quick stats:
{stats}

Expected Characteristics (estimated before execution):
- Estimated row count: {estimated_row_count}
- Expected columns: {expected_columns}

Task: Assess whether these results are REASONABLE given the user's question.

Consider:
1. Does the row count make sense?
2. Are the returned columns what user likely expected?
3. Do the data values look correct?
4. Are there any obvious anomalies?
5. Does this answer the user's question?

Respond in JSON:
{{
  "is_reasonable": true,
  "confidence": 0.9,
  "score": 0.85,
  "concerns": [
    {{
      "type": "row_count_mismatch",
      "severity": "warning",
      "description": "Expected ~100 rows but got 1,250",
      "suggestion": "Consider adding filters to narrow results"
    }}
  ],
  "insights": [
    "Most common category is Electronics (450 items)",
    "Price range is $10-$2,500",
    "75% of items are under $500"
  ],
  "explanation": "Results look reasonable overall. Row count is higher than expected but not unrealistic for this dataset."
}}

Concern Types:
- row_count_mismatch: Actual rows very different from estimate
- unexpected_values: Data values seem wrong or anomalous
- missing_columns: Expected columns not in results
- wrong_aggregation: Looks like wrong GROUP BY or aggregation
- empty_result: No rows returned but expected results
- excessive_result: Suspiciously large result set

Severity Levels:
- critical: Results are likely wrong
- warning: Potential issue worth flagging
- info: Minor observation

Be conservative: When in doubt, flag potential issues rather than auto-approving.
"""

# ═══════════════════════════════════════════════════════════
# EXECUTION-GUIDED REFINEMENT
# ═══════════════════════════════════════════════════════════

REFINEMENT_WITH_RESULTS_PROMPT = """You are a SQL query refiner for rdst.

Original User Question: "{nl_question}"

Original SQL:
{original_sql}

Execution Results:
- Row count: {row_count}
- Sample rows:
{sample_rows}

User Feedback: "{user_feedback}"

Previous Clarifications:
{preference_tree_summary}

Task: Generate a REFINED SQL query based on the execution results and user feedback.

The user has indicated the results are incorrect. Analyze:
1. What went wrong with the original query?
2. What does the user feedback tell us about their intent?
3. How should the SQL be modified?

Respond in JSON:
{{
  "refined_sql": "SELECT ... (corrected query)",
  "changes_made": [
    "Added filter: last_login_at >= 30 days ago",
    "Changed WHERE clause to include active status check",
    "Added ORDER BY for most recent first"
  ],
  "new_understanding": "User wants users with active status who have logged in within 30 days, not just status='active'",
  "confidence": 0.85,
  "assumptions": ["30 days means current date - 30 days", "active means both status='active' AND recent login"],
  "explanation": "Original query only checked status field. User feedback indicates they want temporal filtering too."
}}

Rules:
- Preserve parts of original query that were correct
- Make minimal changes to fix the issue
- Explain what changed and why
- New query must be valid, executable SQL
- All SQL must be READ-ONLY (SELECT only)
"""

# ═══════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════

def format_schema_for_prompt(filtered_schema: str, max_length: int = 3000) -> str:
    """
    Format schema for LLM prompt, truncating if too long.

    Args:
        filtered_schema: Schema string from schema collection
        max_length: Maximum characters to include

    Returns:
        Formatted schema string
    """
    if len(filtered_schema) <= max_length:
        return filtered_schema

    # Truncate and add note
    truncated = filtered_schema[:max_length]
    return f"{truncated}\n\n... (schema truncated for brevity)"


def format_preference_tree_for_prompt(preference_tree) -> str:
    """
    Format preference tree for LLM context.

    Args:
        preference_tree: PreferenceTree instance

    Returns:
        Formatted string for LLM prompt
    """
    if not preference_tree or len(preference_tree) == 0:
        return "No clarifications provided yet."

    return preference_tree.to_context_for_llm()


def format_sample_rows_for_prompt(rows: list, columns: list, max_rows: int = 10) -> str:
    """
    Format sample rows for reasonableness assessment.

    Args:
        rows: List of row tuples
        columns: List of column names
        max_rows: Maximum rows to include

    Returns:
        Formatted table string
    """
    if not rows:
        return "(No rows returned)"

    # Limit rows
    sample = rows[:max_rows]

    # Build simple table
    lines = []
    lines.append(" | ".join(columns))
    lines.append("-" * 60)

    for row in sample:
        lines.append(" | ".join(str(val) for val in row))

    if len(rows) > max_rows:
        lines.append(f"... ({len(rows) - max_rows} more rows)")

    return "\n".join(lines)

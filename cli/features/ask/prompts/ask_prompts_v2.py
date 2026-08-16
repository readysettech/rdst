AMBIGUITY_DETECTION_PROMPT = """You detect ambiguities that materially change SQL.

User Question: "{nl_question}"{provided_context_block}

Database Engine: {database_engine}

Database Schema:
{filtered_schema}

Previous Clarifications (ALREADY RESOLVED - DO NOT ASK AGAIN):
{preference_tree_summary}

Task: Identify only remaining ambiguities that could lead to different result sets.

**SIMPLICITY FIRST PRINCIPLE:**
Before flagging any ambiguity, check if the schema already provides an obvious answer:
- If a table has a column that directly answers the question (e.g., "count" column for popularity, "status" for active/inactive), DO NOT ask for clarification - use that column
- If the question maps clearly to a single table with obvious columns, set overall_confidence >= 0.85 and return empty ambiguities
- Only flag ambiguity when there are genuinely MULTIPLE REASONABLE interpretations that the schema cannot resolve
- Prefer the simplest interpretation that directly uses existing schema columns over complex JOINs or calculations
- "Most popular X" with a count/usage column -> use that column, don't ask about views/engagement/growth

Example: "What are the most popular tags?" with schema having tags(tagname, count) -> confidence 0.95, no ambiguities needed. The count column IS the popularity metric.

IMPORTANT:
- DO NOT flag ambiguities that have already been clarified above
- DO NOT over-interpret simple questions - if there's an obvious answer in the schema, use it
- Ask about missing user intent, not database implementation. Do not ask the user to
  choose tables, columns, joins, storage encodings, or SQL formulas unless those
  choices represent different business meanings that the question and schema cannot
  resolve.
- Treat an explicitly requested operation with a missing result-changing parameter as
  a genuine ambiguity. For example, "sorted by score" needs ascending versus
  descending direction, and "top products" needs a result count when no visible
  product default defines one.
- Do not ask for sort direction when the question already implies it. "Highest",
  "latest", "most", and "top" imply descending order. "Lowest", "earliest",
  "least", and "bottom" imply ascending order.
- Phrase every question and option in terms a product user can answer without knowing
  the database schema.

Classify each ambiguity into one of these categories:

**DB-Related Ambiguities:**
1. **unclear_schema_reference**: Which table/column to use
2. **unclear_value_reference**: What a term means numerically/categorically
3. **missing_sql_keywords**: Unclear how to structure the query (aggregation, ordering, filtering)

**LLM-Related Ambiguities:**
4. **unclear_knowledge_source**: Domain-specific terms that could have multiple meanings
5. **insufficient_reasoning_context**: Missing information needed to interpret intent
6. **temporal_spatial_ambiguity**: Time ranges, geographic scopes not specified

For every interpretation that truly requires user input:
- Give it a stable ID that does not depend on array position.
- Give a relative plausibility score from 0 to 1. Scores are ranking signals, not
  calibrated probabilities.
- Give one short supporting phrase from the question or schema.
- State the SQL effect that distinguishes it from the other options.
- Rank the most strongly supported interpretation first, but do not rely on order as
  the decision signal.

Rules:
- SIMPLICITY FIRST: If the schema has an obvious column that answers the question, return high confidence with no ambiguities
- SKIP ambiguities that directly overlap with previous clarifications
- Ask for a missing result count only when the user explicitly requests a bounded or
  ranked subset and the product has no stated semantic default. A safety row cap is
  not a semantic default.
- DO NOT flag alternate metrics if the schema has a clear primary metric (e.g., count column = popularity)
- Only flag ambiguities for genuinely unclear terms that affect query correctness
- Priority levels: high (must clarify), medium (should clarify), low (optional)
- If overall_confidence >= 0.85, set can_proceed_with_assumptions = true
- If total_ambiguities == 0, set requires_clarification = false
- `requires_clarification` means user input is actually needed before generating SQL;
  it may be false when lower-priority alternatives are reported but one interpretation
  is sufficiently supported to proceed
- If requires_clarification is false, set can_proceed_with_assumptions = true
- When requires_clarification is false, return an empty ambiguities array. Do not
  report hypothetical or diagnostic alternatives that will not be shown or used.
- Never set requires_clarification true when the ambiguities array is empty
- A high-priority ambiguity requires clarification
- If there are at least two medium-priority ambiguities and overall_confidence is
  below 0.85, require clarification because their uncertainty is cumulative
- A single medium-priority ambiguity requires clarification when its best option has
  score below 0.70 or leads the runner-up by less than 0.20
- Proceed with the simplest interpretation only when the remaining uncertainty is
  low and does not compound across multiple material choices
- Provide at least two interpretations for each genuine ambiguity
- Return at most three material ambiguities and at most three concise options per ambiguity
- Keep each option text, reason, SQL effect, and clarifying question to one short sentence
- Return at most one evidence phrase per option, using fewer than 12 words
- Use distinct option IDs and non-identical scores when the evidence supports a ranking
- DO NOT include "Something else", "Other", or open-ended options
- Return only the response object required by the supplied JSON schema
"""


AMBIGUITY_DETECTION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "ambiguities": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "unclear_schema_reference",
                            "unclear_value_reference",
                            "missing_sql_keywords",
                            "unclear_knowledge_source",
                            "insufficient_reasoning_context",
                            "temporal_spatial_ambiguity",
                            "schema_insufficient",
                        ],
                    },
                    "term": {"type": "string"},
                    "reason": {"type": "string"},
                    "possible_interpretations": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 3,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "id": {"type": "string"},
                                "text": {"type": "string"},
                                "score": {
                                    "type": "number",
                                    "minimum": 0.0,
                                    "maximum": 1.0,
                                },
                                "evidence": {
                                    "type": "array",
                                    "maxItems": 1,
                                    "items": {"type": "string"},
                                },
                                "sql_effect": {"type": "string"},
                            },
                            "required": [
                                "id",
                                "text",
                                "score",
                                "evidence",
                                "sql_effect",
                            ],
                        },
                    },
                    "clarifying_question": {"type": "string"},
                    "priority": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                },
                "required": [
                    "id",
                    "category",
                    "term",
                    "reason",
                    "possible_interpretations",
                    "clarifying_question",
                    "priority",
                ],
            },
        },
        "total_ambiguities": {"type": "integer", "minimum": 0},
        "requires_clarification": {"type": "boolean"},
        "can_proceed_with_assumptions": {"type": "boolean"},
        "overall_confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
    },
    "required": [
        "ambiguities",
        "total_ambiguities",
        "requires_clarification",
        "can_proceed_with_assumptions",
        "overall_confidence",
    ],
}


def format_preference_tree_for_prompt(preference_tree) -> str:
    """Format preference tree context for an LLM prompt."""
    if not preference_tree or len(preference_tree) == 0:
        return "No clarifications provided yet."

    return preference_tree.to_context_for_llm()

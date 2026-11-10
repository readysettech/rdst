_DETECTION_TASK = """You detect ambiguities that materially change SQL.

User Question: "{nl_question}"{provided_context_block}{matched_database_values_block}

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
- A term, metric, threshold or rule that the provided context defines is settled; do not
  ask about it
- DO NOT over-interpret simple questions - if there's an obvious answer in the schema, use it
- The user knows this database. When the schema leaves unclear which table, column,
  JSON key or join holds a concept the question needs, ask the user and name the
  candidate identifiers as options.
- Treat an explicitly requested operation with a missing result-changing parameter as
  a genuine ambiguity. For example, "top products", "some", "a few", "the biggest" or
  "the best ones" need a result count when no visible product default defines one.
- A vague qualifier that filters rows ("large", "high", "severe", "sensitive", "good",
  "frequent") needs its threshold or qualifying values unless the provided context
  defines it; ask for them as a `value` question.
- When the question asks to calculate or compute a metric, a stored column with a similar
  name does not settle it unless the provided context says so; ask whether to use that
  column or how to compute the metric.
- When the question asks to label or classify rows without giving the labels, ask for
  the exact labels and the rule for each as a `value` question.
- Ask for sort direction only when the question asks for an order without settling it.
  "Highest", "latest", "most", and "top" imply descending order, "lowest", "earliest",
  "least", and "bottom" ascending order.
- Treat a vague output request as a genuine ambiguity: "details", "IDs", "info",
  "relevant indicators" or "related values" when several columns or JSON keys could
  fill it, JSON output whose key names the question does not give, and a proportion or
  share that could be a fraction or a percentage. Ask which columns, keys or unit to
  return as a `value` question that names the candidates.
- A computed term the question relies on (a score, index, rate, measure, label or
  classification) that neither the schema nor the provided context defines is high
  priority; ask for its definition.
- Every option must be a complete reading. When the answer is a count, threshold or
  formula you do not know, ask a `value` or `definition` question instead of offering an
  option such as "a count you state" or "a formula you provide".

"""

_DETECTION_CATEGORIES = """Classify each ambiguity into one of these categories:

**DB-Related Ambiguities:**
1. **unclear_schema_reference**: Which user-facing entity or attribute a term denotes
2. **unclear_value_reference**: What a term means numerically/categorically
3. **missing_sql_keywords**: Unclear how to structure the query (aggregation, ordering, filtering)

**LLM-Related Ambiguities:**
4. **unclear_knowledge_source**: Domain-specific terms that could have multiple meanings
5. **insufficient_reasoning_context**: Missing information needed to interpret intent
6. **temporal_spatial_ambiguity**: Time ranges, geographic scopes not specified

"""

_DETECTION_ANSWER_TYPES = """Choose how the user answers each ambiguity (`answer_type`):
- `choice`: the question has two or three genuinely different readings that you can list.
  Give them as options.
- `value`: the answer is a code, category, threshold, cutoff, count, date or unit that
  neither the question nor the schema states, and you cannot list its plausible values
  (for example "Which code marks a running contract?" or "What glucose level counts as
  high, and does that level itself count?"). Give no options.
- `definition`: the answer is a formula or business definition the question relies on
  but nothing in the question or schema defines (for example "How is the consumption
  rate calculated?"). Give no options.
Prefer `value` or `definition` over inventing options for a fact you do not know; the
user can state it directly. Do not ask for values the schema or the matched database
values already show.

"""

AMBIGUITY_DETECTION_PROMPT = (
    _DETECTION_TASK
    + _DETECTION_CATEGORIES
    + _DETECTION_ANSWER_TYPES
    + """For every interpretation that truly requires user input:
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
- Provide two or three interpretations for each `choice` ambiguity and none for `value`
  or `definition` ambiguities
- Every interpretation must produce a materially different SQL operation or result.
  Do not ask when the options have the same SQL effect.
- Return at most four material ambiguities and at most three concise options per ambiguity
- Keep each option text, reason, SQL effect, and clarifying question to one short sentence
- Return at most one evidence phrase per option, using fewer than 12 words
- Use distinct option IDs and non-identical scores when the evidence supports a ranking
- DO NOT include "Something else", "Other", or open-ended options; the user can always
  answer in their own words
- Return only the response object required by the supplied JSON schema
"""
)

# A shorter reply for models whose long reports cost the most latency. Ask maps it into
# the same report.
COMPACT_AMBIGUITY_DETECTION_PROMPT = (
    _DETECTION_TASK
    + _DETECTION_ANSWER_TYPES
    + """Rules:
- SIMPLICITY FIRST: If the schema has an obvious column that answers the question, return
  no ambiguities
- SKIP ambiguities that directly overlap with previous clarifications
- Only report ambiguities for genuinely unclear terms that change the result
- Set requires_clarification to true only when user input is needed before generating
  SQL; when it is false, return an empty ambiguities array
- Never set requires_clarification true when the ambiguities array is empty
- overall_confidence is how confident you are, from 0 to 1, that the question can be
  answered as asked
- Return at most two ambiguities. For each, give the term from the question, one short
  clarifying question, and its answer_type
- Give two or three options for each `choice` ambiguity, most likely first, each a few
  words that state a distinct reading with a different result; give no options for
  `value` or `definition` ambiguities
- DO NOT include "Something else", "Other", or open-ended options; the user can always
  answer in their own words
- Return only the response object required by the supplied JSON schema
"""
)


AMBIGUITY_DETECTION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "ambiguities": {
            "type": "array",
            "maxItems": 4,
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
                    "answer_type": {
                        "type": "string",
                        "enum": ["choice", "value", "definition"],
                    },
                    "possible_interpretations": {
                        "type": "array",
                        "minItems": 0,
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
                    "answer_type",
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

COMPACT_AMBIGUITY_DETECTION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "requires_clarification": {"type": "boolean"},
        "overall_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "ambiguities": {
            "type": "array",
            "maxItems": 2,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "term": {"type": "string"},
                    "clarifying_question": {"type": "string"},
                    "answer_type": {
                        "type": "string",
                        "enum": ["choice", "value", "definition"],
                    },
                    "options": {
                        "type": "array",
                        "maxItems": 3,
                        "items": {"type": "string"},
                    },
                },
                "required": ["term", "clarifying_question", "answer_type", "options"],
            },
        },
    },
    "required": ["requires_clarification", "overall_confidence", "ambiguities"],
}


def format_preference_tree_for_prompt(preference_tree) -> str:
    """Format preference tree context for an LLM prompt."""
    if not preference_tree or len(preference_tree) == 0:
        return "No clarifications provided yet."

    return preference_tree.to_context_for_llm()

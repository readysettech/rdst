"""
LLM Prompts for RDST Ask Command (Natural Language to SQL)

Contains structured prompts for text-to-SQL generation with schema awareness,
disambiguation detection, and safety validation.
"""


SQL_GENERATION_PROMPT_VERSION = "concise-semantic-audit-v1"


def format_provided_context_block(provided_context: str | None) -> str:
    """Render caller-provided facts separately from the user's question."""
    value = (provided_context or "").strip()
    if not value:
        return ""
    return f"\n\nAUTHORITATIVE CALLER-PROVIDED CONTEXT:\n{value}"


def format_clarification_draft(sql: str) -> str:
    """The pre-clarification SQL, as context the answered question revises."""
    return (
        "SQL written for this question before the user answered the clarification "
        "questions:\n"
        f"{sql.strip()}\n"
        "Start from this SQL. Change only what the user's answers require, and keep its "
        "tables, joins, filters, output columns and LIMIT otherwise. If the answers agree "
        "with it, return it unchanged."
    )


BEST_EFFORT_CONTEXT = (
    "The user has answered Ask's questions and expects an answer. When a concept has no "
    "exact column, work it out from the closest related columns and list it in "
    "`derived`; do not set `cannot_answer` for `missing_schema` or `ambiguous`."
)


ANSWER_FIRST_DETECTION_NOTE = (
    "\n\nASK ANSWERS FIRST:\n"
    "Ask answers with the best reading of each point you report and lists the points "
    "under the answer for the user to change, so a reported point costs the user "
    "nothing. Report every point where another reading changes the result, even when "
    "one reading is clearly best, including a measure, ratio or score the question names "
    "that no column holds. Mark a point low priority when it changes only which columns "
    "are shown or how rows are ordered. Set requires_clarification to true when you "
    "report any point."
)


def format_detection_draft_block(sql: str) -> str:
    """The SQL drafted for the question, for the clarification detector to review."""
    if not sql.strip():
        return ""
    return (
        "\n\nSQL DRAFTED FOR THIS QUESTION (before any clarification):\n"
        f"{sql.strip()}\n"
        "Review this draft. Report an ambiguity only for a fact the draft had to guess (a "
        "code, stored value, threshold, formula, time range or scope that neither the "
        "question nor the schema states) where another reading changes the result. Do not "
        "ask about anything the question or schema already settles. Set overall_confidence "
        "to how likely this draft returns exactly what the user asked for."
    )


def format_matched_database_values_block(value_context: str | None) -> str:
    """Render database-derived value matches without promoting them to user intent."""
    value = (value_context or "").strip()
    if not value:
        return ""
    return f"\n\nQUESTION-MATCHED DATABASE VALUES:\n{value}"


def format_query_grounding_block(query_grounding: str | None) -> str:
    """Append a rendered grounding block when the caller supplied one."""
    value = (query_grounding or "").strip()
    if not value:
        return ""
    return f"\n\n{value}"


SQL_GENERATION_SYSTEM_PROMPT = (
    "You are an expert text-to-SQL system. Generate exactly one read-only SQL "
    "query using the requested database dialect and only identifiers present in "
    "the supplied schema. Follow the required structured response exactly."
)

COMPREHENSIVE_ASK_PROMPT = """You generate one accurate, read-only SQL query.

DATABASE ENGINE: {database_engine}
TARGET DATABASE: {target_database}

USER QUESTION:
{nl_question}{provided_context_block}{matched_database_values_block}

RELEVANT SCHEMA:
{filtered_schema}

Requirements:
- Return exactly one SELECT or WITH statement in `sql`.
- Return only the columns explicitly requested by the user. Do not add diagnostic,
  descriptive, identifier, grouping, or ordering columns unless requested or required
  to express the result.
- Do not add filters, joins, limits, ordering, or assumptions that are not supported by
  the question, supplied clarification, or schema.
- When the question lists rows by a computed measure without stating an order, order by
  that measure, highest first.
- Before emitting the response, silently audit the SQL for exact use of authoritative
  mappings and complete literals, the minimum sufficient table set, the requested
  output columns and cardinality, and dialect-correct non-integer arithmetic. Remove
  any unsupported join, predicate, limit, exclusion, rounding, or intermediate output.
- Use only tables and columns present in the schema and the requested database dialect.
- A question about the database itself (its tables, row counts, sizes, indexes, I/O)
  may also read the engine's system catalogs: information_schema, on PostgreSQL the
  pg_catalog views and size functions, and on MySQL performance_schema and sys. Keep
  catalog results to this database's own schemas.
- Keep `explanation` to one short sentence.
- Put every unavoidable interpretation in `assumptions`; do not encode a hedge as an
  invented SQL predicate.
- In `derived`, list each measure, ratio or score the question asks for that no column
  holds and the provided context does not define, which the SQL works out from other
  columns: `term` in the question's words, `used` saying how the SQL works it out in
  business words (for example "Total liabilities divided by annual income"), and up to
  two other reasonable ways from the data in `alternatives`. A word that only selects or
  ranks rows ("wealthy", "top", "active") is not a measure. Leave `derived` empty when
  every value the SQL returns reads a column of that meaning.
- Set `cannot_answer` to true with `cannot_answer_reason` = `missing_schema` only when
  a value the answer needs is in neither the schema nor the provided context; leave
  `sql` empty, identify the missing concepts in `missing_schema`, and in `assumptions`
  give up to three ways a user might work them out from the data that exists, each a
  short complete answer in business words (for example "Mortgage balance divided by
  12"). Tables that
  reference a shared parent (for example both carry a key of the same site or plant)
  are joinable through that parent: join through it and state the approximation in
  `assumptions`. A formula or term the provided context defines is computable from the
  columns it names.
- Use `cannot_answer_reason` = `ambiguous` only when several readings change the result
  and neither the question nor the provided context settles them, and list the unsettled
  terms in `missing_schema`; otherwise take the reading they support and record it in
  `assumptions`.
- Use `cannot_answer_reason` = `unsupported_request` only when the request asks to change
  data or asks for something a read-only query cannot do.
- A clarification answer that declines (the question is out of scope, unknown or does not
  matter) settles nothing: take the reading the question and context support.
- When the question or a clarification answer uses a term the provided context defines,
  apply the whole definition, every condition it states.
- When a clarification answer explains what a stored column means, select that column;
  do not recompute it from other columns.
- Use labels and category values exactly as the question, the answers or the context
  give them, without added prefixes or decoration.
- `confidence` is diagnostic only. It does not override `cannot_answer`.
- Treat matched database values as storage candidates for phrases already present in
  the question. Do not turn an incidental match into a new filter or assumption.

Return only the response object required by the supplied JSON schema."""


PLAIN_SQL_ASK_PROMPT = """You generate one accurate, read-only SQL query.

DATABASE ENGINE: {database_engine}
TARGET DATABASE: {target_database}

USER QUESTION:
{nl_question}{provided_context_block}{matched_database_values_block}

RELEVANT SCHEMA:
{filtered_schema}

Requirements:
- Return exactly one SELECT or WITH statement.
- Return only the columns explicitly requested by the user. Do not add diagnostic,
  descriptive, identifier, grouping, or ordering columns unless requested or required
  to express the result.
- Do not add filters, joins, limits, ordering, or assumptions that are not supported by
  the question, supplied clarification, or schema.
- Use only tables and columns present in the schema and the requested database dialect.
- Return `CANNOT_ANSWER:` followed by one short reason only when a value the answer
  needs is in neither the schema nor the provided context. Tables that reference a
  shared parent are joinable through that parent, and a formula or term the provided
  context defines is computable from the columns it names.
- Treat matched database values as storage candidates for phrases already present in
  the question. Do not turn an incidental match into a new filter or assumption.
- Return only SQL with no explanation, JSON, markdown, or commentary when answerable."""

ENUM_GROUNDING_RULES = """- Treat listed enum values as authoritative database values. If the user's
  wording exactly or closely matches a listed enum value, filter that enum column
  instead of treating the wording as a literal value in an unrelated free-text column.
- Never invent a value for a listed enum column. Use only a listed value unless the
  user explicitly supplied an exact database value."""


SQL_GENERATION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sql": {"type": "string"},
        "explanation": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "cannot_answer": {"type": "boolean"},
        "cannot_answer_reason": {
            "type": "string",
            "enum": ["", "missing_schema", "unsupported_request", "ambiguous"],
        },
        "missing_schema": {"type": "array", "items": {"type": "string"}},
        "derived": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "term": {"type": "string"},
                    "used": {"type": "string"},
                    "alternatives": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["term", "used", "alternatives"],
            },
        },
    },
    "required": [
        "sql",
        "explanation",
        "confidence",
        "assumptions",
        "cannot_answer",
        "cannot_answer_reason",
        "missing_schema",
        "derived",
    ],
}

VALIDATION_REPAIR_PROMPT = """Repair one SQL query using deterministic validator feedback.

DATABASE ENGINE: {database_engine}

USER QUESTION:
{nl_question}{provided_context_block}{matched_database_values_block}

FAILED SQL:
{failed_sql}

VALIDATOR ERRORS:
{error_message}

RELEVANT SCHEMA:
{filtered_schema}

Return exactly one read-only SELECT or WITH statement. Change only what is necessary
to fix the listed validation errors. Preserve the user's requested projection and do
not introduce unsupported filters, joins, limits, or assumptions. Return only the
object required by the supplied JSON schema."""

VALIDATION_REPAIR_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sql": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["sql", "explanation"],
}

SQL_REFINEMENT_PROMPT = """You are an expert SQL query refiner. A user has reviewed your generated SQL and requested changes.

ORIGINAL QUESTION:
{original_question}

GENERATED SQL:
{generated_sql}

USER FEEDBACK:
{user_feedback}

RELEVANT SCHEMA:
{filtered_schema}

TASK: Refine the SQL query based on user feedback while maintaining correctness.

Return your refined query in the following JSON format:

{{
  "refined_sql": "the updated SQL query",
  "changes_made": ["list", "of", "specific", "changes"],
  "explanation": "Plain English explanation of the refined query",
  "confidence": 0.0-1.0,
  "validation": {{
    "syntax_valid": true/false,
    "semantically_equivalent": true/false/unclear,
    "addresses_feedback": true/false,
    "potential_issues": ["any concerns with the refined query"]
  }}
}}

GUIDELINES:
1. Carefully consider the user's feedback
2. Maintain the core intent of the original question
3. Ensure the refined SQL is syntactically valid
4. Explain what changed and why
5. Flag any concerns or trade-offs"""

ERROR_RECOVERY_PROMPT = """You are an expert SQL debugger. A generated query failed or returned unexpected results.

ORIGINAL QUESTION:
{nl_question}

GENERATED SQL:
{failed_sql}

ERROR/ISSUE:
{error_message}

SCHEMA INFORMATION:
{filtered_schema}

EXECUTION CONTEXT:
- Database Engine: {database_engine}
- Rows Returned: {rows_returned}
- Execution Time: {execution_time_ms} ms

TASK: Diagnose the issue and generate a corrected query.

Return your analysis in the following JSON format:

{{
  "diagnosis": {{
    "issue_type": "syntax_error|semantic_error|performance_issue|no_results|wrong_results",
    "root_cause": "Detailed explanation of what went wrong",
    "likely_fixes": ["list", "of", "potential", "solutions"]
  }},
  "corrected_sql": "The fixed SQL query",
  "explanation": "What was changed and why",
  "confidence": 0.0-1.0,
  "testing_recommendations": ["How to verify the corrected query works"],
  "prevention": "How to avoid this issue in the future"
}}

COMMON ISSUES TO CHECK:
1. Column names misspelled or don't exist
2. Missing JOINs between tables
3. Type mismatches in comparisons
4. Missing WHERE clause for large tables
5. Incorrect aggregation or GROUP BY
6. Ambiguous column names in multi-table queries
7. Case sensitivity issues
8. Missing quotes around string literals"""

# Template validation
PROMPT_REQUIRED_FIELDS = {
    "COMPREHENSIVE_ASK_PROMPT": [
        "database_engine",
        "target_database",
        "nl_question",
        "filtered_schema",
    ],
    "PLAIN_SQL_ASK_PROMPT": [
        "database_engine",
        "target_database",
        "nl_question",
        "filtered_schema",
    ],
    "SQL_REFINEMENT_PROMPT": [
        "original_question",
        "generated_sql",
        "user_feedback",
        "filtered_schema",
    ],
    "ERROR_RECOVERY_PROMPT": [
        "nl_question",
        "failed_sql",
        "error_message",
        "filtered_schema",
        "database_engine",
        "rows_returned",
        "execution_time_ms",
    ],
}


def validate_prompt_template(template: str, required_fields: list) -> bool:
    """
    Validate that a prompt template contains all required field placeholders.

    Args:
        template: The prompt template string
        required_fields: List of required field names

    Returns:
        True if all required fields are present
    """
    for field in required_fields:
        if f"{{{field}}}" not in template:
            return False
    return True

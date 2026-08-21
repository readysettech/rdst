"""
SQL Generation from Natural Language

Handles conversion of natural language questions to SQL queries with schema awareness,
disambiguation detection, and iterative refinement.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from shared.constants import rdst_data_dir
from shared.error_parser import parse_syntax_error
from shared.llm_manager.base import LLMError

from .prompts.ask_prompts import (
    COMPREHENSIVE_ASK_PROMPT,
    ERROR_RECOVERY_PROMPT,
    SQL_GENERATION_RESPONSE_SCHEMA,
    SQL_GENERATION_SYSTEM_PROMPT,
    SQL_REFINEMENT_PROMPT,
    VALIDATION_REPAIR_PROMPT,
    VALIDATION_REPAIR_RESPONSE_SCHEMA,
    format_matched_database_values_block,
    format_provided_context_block,
)
from .sql_validation import check_read_only

logger = logging.getLogger(__name__)


# The completion budget includes provider reasoning tokens on reasoning-capable
# routes. Eight hundred tokens is sufficient for the visible response, but can
# expire before those routes emit any structured output. Keep the response
# contract concise while allowing the same Ask pipeline to evaluate such models.
SQL_GENERATION_MAX_TOKENS = 4000
_SCHEMA_SIZE_ERROR_CODES = {
    "ANTHROPIC_CONTEXT_WINDOW_EXCEEDED",
    "ANTHROPIC_REQUEST_TOO_LARGE",
}


def _generation_response_format() -> Dict[str, Any]:
    """Return the structured-output contract sent with generation requests."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "sql_generation",
            "strict": True,
            "schema": SQL_GENERATION_RESPONSE_SCHEMA,
        },
    }


def _format_generation_prompt(
    *,
    nl_question: str,
    schema: str,
    database_engine: str,
    target_database: str,
    provided_context: str,
    matched_database_values: str = "",
) -> str:
    return COMPREHENSIVE_ASK_PROMPT.format(
        database_engine=database_engine,
        target_database=target_database,
        nl_question=nl_question,
        provided_context_block=format_provided_context_block(provided_context),
        matched_database_values_block=format_matched_database_values_block(
            matched_database_values
        ),
        filtered_schema=schema,
    )


def _is_schema_size_error(error: BaseException) -> bool:
    return isinstance(error, LLMError) and error.code in _SCHEMA_SIZE_ERROR_CODES


def _schema_size_failure(
    *,
    error: LLMError,
    initial_schema: str,
    initial_schema_format: str,
    compact_schema: str,
    compact_attempted: bool,
    diagnostics: Dict[str, Any],
) -> Dict[str, Any]:
    limit = (
        "Anthropic's model context window"
        if error.code == "ANTHROPIC_CONTEXT_WINDOW_EXCEEDED"
        else "Anthropic's Messages API request-body limit"
    )
    attempts = (
        "RDST tried both complete lossless schema formats."
        if compact_attempted
        else "No smaller complete lossless schema representation was available."
    )
    request_id = f" Request ID: {error.request_id}." if error.request_id else ""
    compact_bytes = len(compact_schema.encode("utf-8")) if compact_schema else 0
    initial_label = (
        "compact schema"
        if "compact" in initial_schema_format.lower()
        else "verbose schema"
    )
    return {
        "success": False,
        "sql": "",
        "explanation": "",
        "confidence": 0.0,
        "error": (
            f"The complete database schema could not fit within {limit}. "
            f"{attempts} The {initial_label} is "
            f"{len(initial_schema.encode('utf-8')):,} bytes"
            + (
                f" and the compact schema is {compact_bytes:,} bytes."
                if compact_bytes
                else "."
            )
            + " Limit the configured database role to the schemas and tables "
            "Ask should access, or configure a model with a larger context window. "
            "RDST did not truncate the schema." + request_id
        ),
        "schema_request_failure_code": error.code,
        "schema_request_failure_request_id": error.request_id or "",
        **diagnostics,
    }


@dataclass
class SQLGenerationResult:
    """Result from SQL generation process."""

    success: bool
    sql: str = ""
    explanation: str = ""
    confidence: float = 0.0
    assumptions: List[str] = field(default_factory=list)
    cannot_answer: bool = False
    cannot_answer_reason: str = ""
    missing_schema: List[str] = field(default_factory=list)
    error: str = ""
    raw_response: Dict[str, Any] = field(default_factory=dict)


def generate_sql_from_nl(
    nl_question: str,
    filtered_schema: str,
    database_engine: str,
    target_database: str,
    llm_manager,
    callback=None,
    provided_context: str = "",
    matched_database_values: str = "",
    **kwargs,
) -> Dict[str, Any]:
    """
    Generate SQL from natural language question using LLM.

    This is the main entry point for NL→SQL conversion. It uses the comprehensive
    prompt that handles disambiguation, generation, and explanation in a single LLM call.

    Args:
        nl_question: Natural language question from user
        filtered_schema: Relevant schema information (tables, columns, indexes)
        database_engine: 'postgresql' or 'mysql'
        target_database: Name of the target database
        llm_manager: LLMManager instance for API calls
        callback: Optional callback function for LLM call logging
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing:
        - success: bool
        - sql: Generated SQL query
        - explanation: Plain English explanation
        - confidence: 0.0-1.0 confidence score
        - assumptions: List of assumptions made
        - cannot_answer: Whether the request cannot be answered from this schema
        - cannot_answer_reason: Machine-readable refusal reason
        - missing_schema: Missing concepts when schema is insufficient
        - error: Error message if failed
    """
    purpose = kwargs.pop("purpose", "sql_generation")
    system_message = kwargs.pop("system_message", SQL_GENERATION_SYSTEM_PROMPT)
    compact_fallback_schema = kwargs.pop("compact_fallback_schema", "")
    compact_fallback_format = kwargs.pop("compact_fallback_format", "")
    schema_format = kwargs.pop("schema_format", "")
    try:
        prompt = _format_generation_prompt(
            nl_question=nl_question,
            schema=filtered_schema,
            database_engine=database_engine,
            target_database=target_database,
            provided_context=provided_context,
            matched_database_values=matched_database_values,
        )
        response_format = _generation_response_format()
        context_fallback_used = False
        context_fallback_reason = ""

        # Call LLM with JSON mode for structured output
        # LLMManager uses generate_response() method
        import time

        start_time = time.time()
        try:
            llm_result = llm_manager.generate_response(
                prompt=prompt,
                system_message=system_message,
                temperature=0.0,
                max_tokens=SQL_GENERATION_MAX_TOKENS,
                purpose=purpose,
                extra={"response_format": response_format},
            )
        except LLMError as first_error:
            if not _is_schema_size_error(first_error):
                raise
            if not compact_fallback_schema:
                diagnostics = {
                    "schema_context_fallback_used": False,
                    "schema_context_fallback_reason": "",
                    "schema_format": "",
                    "prompt_utf8_bytes": len(prompt.encode("utf-8")),
                }
                return _schema_size_failure(
                    error=first_error,
                    initial_schema=filtered_schema,
                    initial_schema_format=schema_format,
                    compact_schema="",
                    compact_attempted=False,
                    diagnostics=diagnostics,
                )

            prompt = _format_generation_prompt(
                nl_question=nl_question,
                schema=compact_fallback_schema,
                database_engine=database_engine,
                target_database=target_database,
                provided_context=provided_context,
                matched_database_values=matched_database_values,
            )
            context_fallback_used = True
            context_fallback_reason = (
                "context_window"
                if first_error.code == "ANTHROPIC_CONTEXT_WINDOW_EXCEEDED"
                else "request_body_limit"
            )
            try:
                llm_result = llm_manager.generate_response(
                    prompt=prompt,
                    system_message=system_message,
                    temperature=0.0,
                    max_tokens=SQL_GENERATION_MAX_TOKENS,
                    purpose=purpose,
                    extra={"response_format": response_format},
                )
            except LLMError as compact_error:
                if not _is_schema_size_error(compact_error):
                    raise
                diagnostics = {
                    "schema_context_fallback_used": True,
                    "schema_context_fallback_reason": context_fallback_reason,
                    "schema_format": compact_fallback_format,
                    "prompt_utf8_bytes": len(prompt.encode("utf-8")),
                }
                return _schema_size_failure(
                    error=compact_error,
                    initial_schema=filtered_schema,
                    initial_schema_format=schema_format,
                    compact_schema=compact_fallback_schema,
                    compact_attempted=True,
                    diagnostics=diagnostics,
                )

        latency_ms = (time.time() - start_time) * 1000
        size_diagnostics = {
            "schema_context_fallback_used": context_fallback_used,
            "schema_context_fallback_reason": context_fallback_reason,
            "schema_format": compact_fallback_format if context_fallback_used else "",
            "prompt_utf8_bytes": len(prompt.encode("utf-8")),
        }

        # Invoke callback for LLM call tracking
        if callback:
            response_text = llm_result.get("response", "")
            tokens = llm_result.get("tokens_used", 0)
            model = llm_result.get("model", "unknown")

            try:
                callback(
                    prompt=prompt,
                    response=response_text,
                    tokens=tokens,
                    latency_ms=latency_ms,
                    model=model,
                    metadata={"state": "generating_sql", "question": nl_question},
                )
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(f"Callback invocation failed: {e}")

        # Parse JSON response
        response_text = llm_result.get("response", "")

        # Debug: print what we got from LLM
        if not response_text:
            return {
                "success": False,
                "sql": "",
                "explanation": "",
                "confidence": 0.0,
                "error": f"LLM returned empty response. Full result: {llm_result}",
            }

        logger.debug(f"LLM response (first 500 chars): {response_text[:500]}")

        # Strip markdown code fences if present (Claude often wraps JSON in ```json...```)
        response_text = response_text.strip()
        if response_text.startswith("```"):
            # Find the first newline after opening fence
            first_newline = response_text.find("\n")
            if first_newline != -1:
                response_text = response_text[first_newline + 1 :]
            # Remove closing fence
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

        result_data = json.loads(response_text, strict=False)

        required_fields = set(SQL_GENERATION_RESPONSE_SCHEMA["required"])
        missing_fields = sorted(required_fields - result_data.keys())
        if missing_fields:
            return {
                "success": False,
                "sql": "",
                "explanation": "",
                "confidence": 0.0,
                "error": (
                    "SQL generation response is missing required fields: "
                    + ", ".join(missing_fields)
                ),
                "raw_response": result_data,
            }
        unexpected_fields = sorted(
            result_data.keys() - SQL_GENERATION_RESPONSE_SCHEMA["properties"].keys()
        )
        if unexpected_fields:
            raise ValueError(
                "SQL generation response has unexpected fields: "
                + ", ".join(unexpected_fields)
            )

        result = SQLGenerationResult(
            success=True,
            sql=result_data.get("sql", ""),
            explanation=result_data.get("explanation", ""),
            confidence=result_data.get("confidence", 0.0),
            assumptions=result_data.get("assumptions", []),
            cannot_answer=result_data.get("cannot_answer", False),
            cannot_answer_reason=result_data.get("cannot_answer_reason", ""),
            missing_schema=result_data.get("missing_schema", []),
            raw_response=result_data,
        )

        if not isinstance(result.sql, str) or not isinstance(result.explanation, str):
            raise ValueError("sql and explanation must be strings")
        if (
            isinstance(result.confidence, bool)
            or not isinstance(result.confidence, (int, float))
            or not 0.0 <= float(result.confidence) <= 1.0
        ):
            raise ValueError("confidence must be a number between 0 and 1")
        if not isinstance(result.assumptions, list) or not all(
            isinstance(item, str) for item in result.assumptions
        ):
            raise ValueError("assumptions must be an array of strings")
        if not isinstance(result.cannot_answer, bool):
            raise ValueError("cannot_answer must be a boolean")
        if result.cannot_answer_reason not in {
            "",
            "missing_schema",
            "unsupported_request",
            "ambiguous",
        }:
            raise ValueError("invalid cannot_answer_reason")
        if not isinstance(result.missing_schema, list) or not all(
            isinstance(item, str) for item in result.missing_schema
        ):
            raise ValueError("missing_schema must be an array of strings")

        if result.cannot_answer:
            if result.sql.strip():
                result.success = False
                result.error = "cannot_answer responses must leave sql empty"
            elif not result.cannot_answer_reason:
                result.success = False
                result.error = "cannot_answer responses must provide a reason"
        elif not result.sql.strip():
            result.success = False
            result.error = "answerable SQL generation response returned empty sql"
        elif result.cannot_answer_reason or result.missing_schema:
            result.success = False
            result.error = (
                "answerable responses cannot include refusal reasons or missing schema"
            )

        # Validate safety. The model's own safety_assessment is not evidence --
        # the same model wrote the SQL -- so check the generated statement.
        if result.sql:
            read_only_check = check_read_only(result.sql)
            if not read_only_check["is_read_only"]:
                result.success = False
                result.error = "Generated query is not read-only (safety violation)"

        # Convert to dict for workflow compatibility
        return {
            "success": result.success,
            "sql": result.sql,
            "explanation": result.explanation,
            "confidence": result.confidence,
            "assumptions": result.assumptions,
            "cannot_answer": result.cannot_answer,
            "cannot_answer_reason": result.cannot_answer_reason,
            "missing_schema": result.missing_schema,
            "error": result.error,
            "raw_response": result_data,
            **size_diagnostics,
        }

    except json.JSONDecodeError as e:
        # Print more context around the error
        if "response_text" in locals():
            error_pos = e.pos if hasattr(e, "pos") else 0
            context_start = max(0, error_pos - 100)
            context_end = min(len(response_text), error_pos + 100)
            error_context = response_text[context_start:context_end]
            logger.debug(
                f"JSON error context around position {error_pos}: ...{error_context}..."
            )
            logger.debug(f"Full response length: {len(response_text)} chars")
            # Save to file for inspection. The response can carry schema and
            # sampled values, so it stays in the user's own directory rather
            # than a world-readable temp path.
            try:
                debug_dir = rdst_data_dir() / "debug"
                debug_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                debug_path = debug_dir / "ask_llm_response.json"
                with open(debug_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(response_text)
                logger.debug(f"Full response saved to {debug_path}")
            except Exception:
                pass

        return {
            "success": False,
            "sql": "",
            "explanation": "",
            "confidence": 0.0,
            "error": f"Failed to parse LLM response as JSON: {str(e)}",
            "raw_response": response_text if "response_text" in locals() else "",
        }

    except Exception as e:
        if getattr(llm_manager, "propagate_query_errors", False):
            raise
        return {
            "success": False,
            "sql": "",
            "explanation": "",
            "confidence": 0.0,
            "error": str(e),
        }


def repair_sql_after_validation(
    *,
    nl_question: str,
    failed_sql: str,
    error_message: str,
    filtered_schema: str,
    database_engine: str,
    llm_manager,
    callback=None,
    provided_context: str = "",
    matched_database_values: str = "",
) -> Dict[str, Any]:
    """Perform one narrowly scoped repair from deterministic validator feedback."""
    import time

    prompt = VALIDATION_REPAIR_PROMPT.format(
        nl_question=nl_question,
        provided_context_block=format_provided_context_block(provided_context),
        matched_database_values_block=format_matched_database_values_block(
            matched_database_values
        ),
        failed_sql=failed_sql,
        error_message=error_message,
        filtered_schema=filtered_schema,
        database_engine=database_engine,
    )
    try:
        started = time.time()
        result = llm_manager.generate_response(
            prompt=prompt,
            temperature=0.0,
            max_tokens=SQL_GENERATION_MAX_TOKENS,
            purpose="sql_validation_repair",
            extra={
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "sql_validation_repair",
                        "strict": True,
                        "schema": VALIDATION_REPAIR_RESPONSE_SCHEMA,
                    },
                }
            },
        )
        response_text = result.get("response", "")
        if callback:
            callback(
                prompt=prompt,
                response=response_text,
                tokens=result.get("tokens_used", 0),
                latency_ms=(time.time() - started) * 1000,
                model=result.get("model", "unknown"),
            )
        parsed = json.loads(response_text)
        if set(parsed) != {"sql", "explanation"}:
            raise ValueError("validation repair response has unexpected fields")
        if not all(isinstance(parsed[field], str) for field in parsed):
            raise ValueError("validation repair fields must be strings")
        safety = check_read_only(parsed["sql"])
        if not safety["is_read_only"]:
            raise ValueError("validation repair did not return one read-only statement")
        return {"success": True, **parsed, "raw_response": parsed}
    except Exception as exc:
        if getattr(llm_manager, "propagate_query_errors", False):
            raise
        return {"success": False, "error": str(exc)}


def refine_sql_with_feedback(
    original_question: str,
    generated_sql: str,
    user_feedback: str,
    filtered_schema: str,
    llm_manager,
    **kwargs,
) -> Dict[str, Any]:
    """
    Refine a generated SQL query based on user feedback.

    Used in the refinement loop when user says "modify the query" or provides
    specific feedback about what to change.

    Args:
        original_question: Original natural language question
        generated_sql: Previously generated SQL
        user_feedback: User's feedback or modification request
        filtered_schema: Relevant schema information
        llm_manager: LLMManager instance
        **kwargs: Additional parameters

    Returns:
        Dict with refined SQL and explanation
    """
    try:
        # Extract callback from kwargs if provided
        callback = kwargs.get("callback")

        prompt = SQL_REFINEMENT_PROMPT.format(
            original_question=original_question,
            generated_sql=generated_sql,
            user_feedback=user_feedback,
            filtered_schema=filtered_schema,
        )

        # Call with callback if provided
        llm_kwargs = {
            "prompt": prompt,
            "temperature": 0.0,
            "max_tokens": 2000,  # Refinement responses are typically shorter
            "purpose": "sql_refinement",
            "extra": {"response_format": {"type": "json_object"}},
        }
        if callback:
            llm_kwargs["callback"] = callback

        llm_result = llm_manager.generate_response(**llm_kwargs)

        response_text = llm_result.get("response", "")

        # Strip markdown code fences if present
        response_text = response_text.strip()
        if response_text.startswith("```"):
            first_newline = response_text.find("\n")
            if first_newline != -1:
                response_text = response_text[first_newline + 1 :]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

        result_data = json.loads(response_text, strict=False)

        return {
            "success": True,
            "refined_sql": result_data.get("refined_sql", ""),
            "changes_made": result_data.get("changes_made", []),
            "explanation": result_data.get("explanation", ""),
            "confidence": result_data.get("confidence", 0.0),
            "validation": result_data.get("validation", {}),
            "raw_response": result_data,
        }

    except Exception as e:
        return {"success": False, "error": f"SQL refinement failed: {str(e)}"}


def recover_from_error(
    nl_question: str,
    failed_sql: str,
    error_message: str,
    filtered_schema: str,
    database_engine: str,
    rows_returned: int = 0,
    execution_time_ms: float = 0.0,
    llm_manager=None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Attempt to recover from SQL execution error by generating corrected query.

    Used when:
    - Query has syntax error
    - Query returns zero results unexpectedly
    - Query times out or has performance issues

    Args:
        nl_question: Original natural language question
        failed_sql: SQL that failed or returned unexpected results
        error_message: Error message or issue description
        filtered_schema: Relevant schema information
        database_engine: 'postgresql' or 'mysql'
        rows_returned: Number of rows returned (0 for errors)
        execution_time_ms: Execution time in milliseconds
        llm_manager: LLMManager instance
        **kwargs: Additional parameters

    Returns:
        Dict with diagnosis and corrected SQL
    """
    try:
        # Check for syntax errors first
        syntax_error = parse_syntax_error(error_message, failed_sql, database_engine)

        if syntax_error and syntax_error.get("corrected_sql"):
            # We can auto-correct this syntax error
            return {
                "success": True,
                "diagnosis": {
                    "root_cause": syntax_error["diagnosis"],
                    "error_type": "syntax_error",
                },
                "corrected_sql": syntax_error["corrected_sql"],
                "explanation": f"Auto-corrected syntax error: {syntax_error['suggestion']}",
                "confidence": syntax_error["confidence"],
                "original_sql": syntax_error["original_sql"],
            }

        # Check for schema mismatch errors second
        schema_mismatch = _detect_schema_mismatch(
            error_message, failed_sql, filtered_schema
        )

        if schema_mismatch and schema_mismatch["found_suggestions"]:
            # We found similar column/table names, use those
            return {
                "success": True,
                "diagnosis": {
                    "root_cause": schema_mismatch["diagnosis"],
                    "error_type": "schema_mismatch",
                },
                "corrected_sql": schema_mismatch["corrected_sql"],
                "explanation": schema_mismatch["explanation"],
                "confidence": schema_mismatch["confidence"],
                "suggestions": schema_mismatch["suggestions"],
            }

        # Fall back to LLM-based recovery
        prompt = ERROR_RECOVERY_PROMPT.format(
            nl_question=nl_question,
            failed_sql=failed_sql,
            error_message=error_message,
            filtered_schema=filtered_schema,
            database_engine=database_engine,
            rows_returned=rows_returned,
            execution_time_ms=execution_time_ms,
        )

        llm_result = llm_manager.generate_response(
            prompt=prompt,
            temperature=0.0,
            max_tokens=2000,  # Error recovery responses are typically shorter
            purpose="sql_recovery",
            extra={"response_format": {"type": "json_object"}},
        )

        response_text = llm_result.get("response", "")

        # Strip markdown code fences if present
        response_text = response_text.strip()
        if response_text.startswith("```"):
            first_newline = response_text.find("\n")
            if first_newline != -1:
                response_text = response_text[first_newline + 1 :]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

        result_data = json.loads(response_text, strict=False)

        return {
            "success": True,
            "diagnosis": result_data.get("diagnosis", {}),
            "corrected_sql": result_data.get("corrected_sql", ""),
            "explanation": result_data.get("explanation", ""),
            "confidence": result_data.get("confidence", 0.0),
            "testing_recommendations": result_data.get("testing_recommendations", []),
            "prevention": result_data.get("prevention", ""),
            "raw_response": result_data,
        }

    except Exception as e:
        return {"success": False, "error": f"Error recovery failed: {str(e)}"}


# Helper functions


def _extract_table_names_from_schema(schema: str) -> List[str]:
    """Extract table names from schema information string."""
    table_names = []

    # Pattern for "Table: table_name" or "CREATE TABLE table_name"
    patterns = [
        r"Table:\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z_][a-zA-Z0-9_]*)",
        r"## ([a-zA-Z_][a-zA-Z0-9_]*)\s+\(",
    ]

    for pattern in patterns:
        matches = re.findall(pattern, schema, re.IGNORECASE)
        table_names.extend(matches)

    # Deduplicate and return
    return list(set(table_names))


def _calculate_similarity(str1: str, str2: str) -> float:
    """
    Calculate similarity between two strings (0.0 to 1.0).

    Uses Levenshtein distance normalized by length.
    """
    # Convert to lowercase for case-insensitive comparison
    s1, s2 = str1.lower(), str2.lower()

    # Quick exact match
    if s1 == s2:
        return 1.0

    # Levenshtein distance implementation
    if len(s1) < len(s2):
        s1, s2 = s2, s1

    if len(s2) == 0:
        return 0.0

    # Build distance matrix
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            # Cost of insertions, deletions, or substitutions
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    # Normalize by max length
    distance = previous_row[-1]
    max_len = max(len(s1), len(s2))
    similarity = 1.0 - (distance / max_len)

    return similarity


def find_similar_names(
    wrong_name: str,
    available_names: List[str],
    threshold: float = 0.5,
    max_results: int = 5,
) -> List[Tuple[str, float]]:
    """
    Find similar names using fuzzy matching.

    Args:
        wrong_name: The incorrect name
        available_names: List of valid names
        threshold: Minimum similarity score (0.0 to 1.0)
        max_results: Maximum number of suggestions

    Returns:
        List of (name, similarity_score) tuples, sorted by score
    """
    scores = []

    for name in available_names:
        score = _calculate_similarity(wrong_name, name)
        if score >= threshold:
            scores.append((name, score))

    # Sort by similarity (highest first)
    scores.sort(key=lambda x: x[1], reverse=True)

    return scores[:max_results]


def _extract_column_names_from_schema(schema: str) -> List[str]:
    """Extract all column names from schema string."""
    import re

    columns = []

    # Pattern for column definitions
    # Matches: "column_name TYPE" or "  column_name:"
    patterns = [
        r"^\s+(\w+)\s+(?:INT|VARCHAR|TEXT|TIMESTAMP|DECIMAL|BIGINT|FLOAT|DOUBLE|DATE|DATETIME|BOOLEAN|BOOL)",
        r"^\s+-\s+(\w+):",
        r"`(\w+)`\s+(?:INT|VARCHAR|TEXT|TIMESTAMP|DECIMAL|BIGINT|FLOAT|DOUBLE|DATE|DATETIME|BOOLEAN|BOOL)",
    ]

    for line in schema.split("\n"):
        for pattern in patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                columns.append(match.group(1))
                break

    return list(set(columns))  # Deduplicate


def _detect_schema_mismatch(
    error_message: str, failed_sql: str, filtered_schema: str
) -> Optional[Dict[str, Any]]:
    """
    Detect if error is due to schema mismatch (wrong column/table name).

    Returns:
        Dict with diagnosis and suggestions, or None if not a schema error
    """
    error_lower = error_message.lower()

    # Pattern 1: Unknown column 'column_name'
    import re

    unknown_col_pattern = r"unknown column ['\"]?(\w+)['\"]?"
    col_match = re.search(unknown_col_pattern, error_lower)

    if col_match:
        wrong_col = col_match.group(1)

        # Extract available columns from schema
        available_cols = _extract_column_names_from_schema(filtered_schema)

        # Find similar column names
        suggestions = find_similar_names(wrong_col, available_cols, threshold=0.4)

        if suggestions:
            best_match = suggestions[0][0]

            # Generate corrected SQL
            corrected_sql = re.sub(
                r"\b" + wrong_col + r"\b", best_match, failed_sql, flags=re.IGNORECASE
            )

            suggestions_text = "\n".join(
                [
                    f"  - {name} (similarity: {score * 100:.0f}%)"
                    for name, score in suggestions[:3]
                ]
            )

            return {
                "found_suggestions": True,
                "diagnosis": f"Column '{wrong_col}' not found. Did you mean '{best_match}'?",
                "corrected_sql": corrected_sql,
                "explanation": f"Replaced '{wrong_col}' with '{best_match}'",
                "confidence": suggestions[0][1],
                "suggestions": suggestions_text,
            }

    # Pattern 2: Table doesn't exist
    table_pattern = r"table ['\"]?(\w+)['\"]? doesn't exist"
    table_match = re.search(table_pattern, error_lower)

    if table_match:
        wrong_table = table_match.group(1)

        # Extract available tables from schema
        available_tables = _extract_table_names_from_schema(filtered_schema)

        # Find similar table names
        suggestions = find_similar_names(wrong_table, available_tables, threshold=0.4)

        if suggestions:
            best_match = suggestions[0][0]

            # Generate corrected SQL
            corrected_sql = re.sub(
                r"\b" + wrong_table + r"\b", best_match, failed_sql, flags=re.IGNORECASE
            )

            suggestions_text = "\n".join(
                [
                    f"  - {name} (similarity: {score * 100:.0f}%)"
                    for name, score in suggestions[:3]
                ]
            )

            return {
                "found_suggestions": True,
                "diagnosis": f"Table '{wrong_table}' not found. Did you mean '{best_match}'?",
                "corrected_sql": corrected_sql,
                "explanation": f"Replaced table '{wrong_table}' with '{best_match}'",
                "confidence": suggestions[0][1],
                "suggestions": suggestions_text,
            }

    return None

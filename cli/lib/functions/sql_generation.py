"""
SQL Generation from Natural Language

Handles conversion of natural language questions to SQL queries with schema awareness,
disambiguation detection, and iterative refinement.
"""

import json
import re
from typing import Dict, Any, List, Tuple, Optional
from dataclasses import dataclass, field

from ..prompts.ask_prompts import (
    COMPREHENSIVE_ASK_PROMPT,
    SQL_REFINEMENT_PROMPT,
    ERROR_RECOVERY_PROMPT,
    SCHEMA_FILTER_PROMPT
)
from .error_parser import parse_syntax_error


@dataclass
class SQLGenerationResult:
    """Result from SQL generation process."""
    success: bool
    sql: str = ""
    explanation: str = ""
    confidence: float = 0.0
    needs_clarification: bool = False
    clarifications: List[Dict[str, Any]] = field(default_factory=list)
    ambiguities: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    tables_used: List[str] = field(default_factory=list)
    columns_used: List[str] = field(default_factory=list)
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""
    raw_response: Dict[str, Any] = field(default_factory=dict)


def generate_sql_from_nl(
    nl_question: str,
    filtered_schema: str,
    database_engine: str,
    target_database: str,
    llm_manager,
    callback=None,
    **kwargs
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
        - needs_clarification: Whether user input is needed
        - clarifications: List of clarification questions
        - ambiguities: List of identified ambiguities
        - assumptions: List of assumptions made
        - warnings: List of warnings
        - error: Error message if failed
    """
    try:
        # Format the comprehensive prompt
        prompt = COMPREHENSIVE_ASK_PROMPT.format(
            database_engine=database_engine,
            target_database=target_database,
            nl_question=nl_question,
            filtered_schema=filtered_schema
        )

        # Call LLM with JSON mode for structured output
        # LLMManager uses generate_response() method
        import time
        start_time = time.time()

        llm_result = llm_manager.generate_response(
            prompt=prompt,
            temperature=0.0,  # Deterministic for consistent generation
            max_tokens=4000,  # Ensure enough space for complete JSON response
            extra={"response_format": {"type": "json_object"}}  # Request JSON mode
        )

        latency_ms = (time.time() - start_time) * 1000

        # Invoke callback for LLM call tracking
        if callback:
            response_text = llm_result.get('response', '')
            tokens = llm_result.get('total_tokens', 0)
            model = llm_result.get('model', 'unknown')

            try:
                callback(
                    prompt=prompt,
                    response=response_text,
                    tokens=tokens,
                    latency_ms=latency_ms,
                    model=model,
                    metadata={'state': 'generating_sql', 'question': nl_question}
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Callback invocation failed: {e}")

        # Parse JSON response
        response_text = llm_result.get('response', '')

        # Debug: print what we got from LLM
        if not response_text:
            return {
                'success': False,
                'sql': '',
                'explanation': '',
                'confidence': 0.0,
                'error': f'LLM returned empty response. Full result: {llm_result}'
            }

        # Debug: print first 500 chars
        print(f"DEBUG: LLM response (first 500 chars): {response_text[:500]}")

        # Strip markdown code fences if present (Claude often wraps JSON in ```json...```)
        response_text = response_text.strip()
        if response_text.startswith('```'):
            # Find the first newline after opening fence
            first_newline = response_text.find('\n')
            if first_newline != -1:
                response_text = response_text[first_newline + 1:]
            # Remove closing fence
            if response_text.endswith('```'):
                response_text = response_text[:-3]
            response_text = response_text.strip()

        result_data = json.loads(response_text)

        # Extract key fields with safe defaults
        analysis = result_data.get('analysis', {})
        clarifications_list = result_data.get('clarifications', [])
        sql_gen = result_data.get('sql_generation', {})
        safety = result_data.get('safety_assessment', {})
        alternatives = result_data.get('alternatives', [])

        # Build result object
        result = SQLGenerationResult(
            success=True,
            sql=sql_gen.get('sql', ''),
            explanation=sql_gen.get('explanation', ''),
            confidence=sql_gen.get('confidence', 0.0),
            needs_clarification=analysis.get('needs_clarification', False),
            clarifications=clarifications_list,
            ambiguities=analysis.get('ambiguities', []),
            assumptions=sql_gen.get('assumptions', []),
            warnings=safety.get('warnings', []),
            tables_used=sql_gen.get('tables_used', []),
            columns_used=sql_gen.get('columns_used', []),
            alternatives=alternatives,
            raw_response=result_data
        )

        # Validate safety
        if not safety.get('is_read_only', True):
            result.success = False
            result.error = "Generated query is not read-only (safety violation)"
            result.warnings.append("LLM attempted to generate non-SELECT query")

        # Convert to dict for workflow compatibility
        return {
            'success': result.success,
            'sql': result.sql,
            'explanation': result.explanation,
            'confidence': result.confidence,
            'needs_clarification': result.needs_clarification,
            'clarifications': result.clarifications,
            'ambiguities': result.ambiguities,
            'assumptions': result.assumptions,
            'warnings': result.warnings,
            'tables_used': result.tables_used,
            'columns_used': result.columns_used,
            'alternatives': result.alternatives,
            'error': result.error,
            'raw_response': result_data
        }

    except json.JSONDecodeError as e:
        # Print more context around the error
        if 'response_text' in locals():
            error_pos = e.pos if hasattr(e, 'pos') else 0
            context_start = max(0, error_pos - 100)
            context_end = min(len(response_text), error_pos + 100)
            error_context = response_text[context_start:context_end]
            print(f"DEBUG: JSON error context around position {error_pos}:")
            print(f"  ...{error_context}...")
            print(f"DEBUG: Full response length: {len(response_text)} chars")
            # Save to file for inspection
            try:
                with open('/tmp/rdst_ask_llm_response.json', 'w') as f:
                    f.write(response_text)
                print(f"DEBUG: Full response saved to /tmp/rdst_ask_llm_response.json")
            except:
                pass

        return {
            'success': False,
            'sql': '',
            'explanation': '',
            'confidence': 0.0,
            'error': f'Failed to parse LLM response as JSON: {str(e)}',
            'raw_response': response_text if 'response_text' in locals() else ''
        }

    except Exception as e:
        return {
            'success': False,
            'sql': '',
            'explanation': '',
            'confidence': 0.0,
            'error': f'SQL generation failed: {str(e)}'
        }


def refine_sql_with_feedback(
    original_question: str,
    generated_sql: str,
    user_feedback: str,
    filtered_schema: str,
    llm_manager,
    **kwargs
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
        callback = kwargs.get('callback')

        prompt = SQL_REFINEMENT_PROMPT.format(
            original_question=original_question,
            generated_sql=generated_sql,
            user_feedback=user_feedback,
            filtered_schema=filtered_schema
        )

        # Call with callback if provided
        llm_kwargs = {
            'prompt': prompt,
            'temperature': 0.0,
            'max_tokens': 2000,  # Refinement responses are typically shorter
            'extra': {"response_format": {"type": "json_object"}}
        }
        if callback:
            llm_kwargs['callback'] = callback

        llm_result = llm_manager.generate_response(**llm_kwargs)

        response_text = llm_result.get('response', '')

        # Strip markdown code fences if present
        response_text = response_text.strip()
        if response_text.startswith('```'):
            first_newline = response_text.find('\n')
            if first_newline != -1:
                response_text = response_text[first_newline + 1:]
            if response_text.endswith('```'):
                response_text = response_text[:-3]
            response_text = response_text.strip()

        result_data = json.loads(response_text)

        return {
            'success': True,
            'refined_sql': result_data.get('refined_sql', ''),
            'changes_made': result_data.get('changes_made', []),
            'explanation': result_data.get('explanation', ''),
            'confidence': result_data.get('confidence', 0.0),
            'validation': result_data.get('validation', {}),
            'raw_response': result_data
        }

    except Exception as e:
        return {
            'success': False,
            'error': f'SQL refinement failed: {str(e)}'
        }


def recover_from_error(
    nl_question: str,
    failed_sql: str,
    error_message: str,
    filtered_schema: str,
    database_engine: str,
    rows_returned: int = 0,
    execution_time_ms: float = 0.0,
    llm_manager=None,
    **kwargs
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
        syntax_error = parse_syntax_error(
            error_message,
            failed_sql,
            database_engine
        )

        if syntax_error and syntax_error.get('corrected_sql'):
            # We can auto-correct this syntax error
            return {
                'success': True,
                'diagnosis': {
                    'root_cause': syntax_error['diagnosis'],
                    'error_type': 'syntax_error'
                },
                'corrected_sql': syntax_error['corrected_sql'],
                'explanation': f"Auto-corrected syntax error: {syntax_error['suggestion']}",
                'confidence': syntax_error['confidence'],
                'original_sql': syntax_error['original_sql']
            }

        # Check for schema mismatch errors second
        schema_mismatch = _detect_schema_mismatch(
            error_message,
            failed_sql,
            filtered_schema
        )

        if schema_mismatch and schema_mismatch['found_suggestions']:
            # We found similar column/table names, use those
            return {
                'success': True,
                'diagnosis': {
                    'root_cause': schema_mismatch['diagnosis'],
                    'error_type': 'schema_mismatch'
                },
                'corrected_sql': schema_mismatch['corrected_sql'],
                'explanation': schema_mismatch['explanation'],
                'confidence': schema_mismatch['confidence'],
                'suggestions': schema_mismatch['suggestions']
            }

        # Fall back to LLM-based recovery
        prompt = ERROR_RECOVERY_PROMPT.format(
            nl_question=nl_question,
            failed_sql=failed_sql,
            error_message=error_message,
            filtered_schema=filtered_schema,
            database_engine=database_engine,
            rows_returned=rows_returned,
            execution_time_ms=execution_time_ms
        )

        llm_result = llm_manager.generate_response(
            prompt=prompt,
            temperature=0.0,
            max_tokens=2000,  # Error recovery responses are typically shorter
            extra={"response_format": {"type": "json_object"}}
        )

        response_text = llm_result.get('response', '')

        # Strip markdown code fences if present
        response_text = response_text.strip()
        if response_text.startswith('```'):
            first_newline = response_text.find('\n')
            if first_newline != -1:
                response_text = response_text[first_newline + 1:]
            if response_text.endswith('```'):
                response_text = response_text[:-3]
            response_text = response_text.strip()

        result_data = json.loads(response_text)

        return {
            'success': True,
            'diagnosis': result_data.get('diagnosis', {}),
            'corrected_sql': result_data.get('corrected_sql', ''),
            'explanation': result_data.get('explanation', ''),
            'confidence': result_data.get('confidence', 0.0),
            'testing_recommendations': result_data.get('testing_recommendations', []),
            'prevention': result_data.get('prevention', ''),
            'raw_response': result_data
        }

    except Exception as e:
        return {
            'success': False,
            'error': f'Error recovery failed: {str(e)}'
        }


def filter_relevant_schema(
    nl_question: str,
    full_schema: str,
    llm_manager=None,
    **kwargs
) -> Dict[str, Any]:
    """
    Filter schema to include only tables relevant to the natural language question.

    This reduces token usage and helps the LLM focus on relevant parts of the schema.
    Uses simple heuristics first, falls back to LLM if needed.

    Args:
        nl_question: Natural language question
        full_schema: Complete schema information string
        llm_manager: LLMManager instance (optional)
        **kwargs: Additional parameters

    Returns:
        Dict containing:
        - success: bool
        - filtered_schema: Schema subset relevant to question
        - tables_included: List of table names included
        - method: 'heuristic' or 'llm'
    """
    try:
        # Try heuristic approach first: extract table names from question
        table_names = _extract_table_names_from_schema(full_schema)
        relevant_tables = _match_tables_heuristic(nl_question, table_names)

        if relevant_tables:
            # Filter schema to include only relevant tables
            filtered = _filter_schema_by_tables(full_schema, relevant_tables)
            return {
                'success': True,
                'filtered_schema': filtered,
                'tables_included': relevant_tables,
                'method': 'heuristic'
            }

        # Fallback to LLM if heuristic fails and LLM is available
        if llm_manager:
            table_list = "\n".join([f"- {name}" for name in table_names])
            prompt = SCHEMA_FILTER_PROMPT.format(
                nl_question=nl_question,
                table_list=table_list
            )

            response = llm_manager.chat(
                prompt=prompt,
                temperature=0.0,
                json_mode=True
            )

            result_data = json.loads(response)
            relevant_tables = result_data.get('relevant_tables', [])

            if relevant_tables:
                filtered = _filter_schema_by_tables(full_schema, relevant_tables)
                return {
                    'success': True,
                    'filtered_schema': filtered,
                    'tables_included': relevant_tables,
                    'method': 'llm',
                    'confidence': result_data.get('confidence', 0.0)
                }

        # If all else fails, return full schema
        return {
            'success': True,
            'filtered_schema': full_schema,
            'tables_included': table_names,
            'method': 'fallback_full'
        }

    except Exception as e:
        # On error, return full schema
        return {
            'success': True,
            'filtered_schema': full_schema,
            'tables_included': [],
            'method': 'error_fallback',
            'error': str(e)
        }


# Helper functions

def _extract_table_names_from_schema(schema: str) -> List[str]:
    """Extract table names from schema information string."""
    table_names = []

    # Pattern for "Table: table_name" or "CREATE TABLE table_name"
    patterns = [
        r'Table:\s+([a-zA-Z_][a-zA-Z0-9_]*)',
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z_][a-zA-Z0-9_]*)',
        r'## ([a-zA-Z_][a-zA-Z0-9_]*)\s+\('
    ]

    for pattern in patterns:
        matches = re.findall(pattern, schema, re.IGNORECASE)
        table_names.extend(matches)

    # Deduplicate and return
    return list(set(table_names))


def _match_tables_heuristic(question: str, table_names: List[str]) -> List[str]:
    """
    Match table names to natural language question using heuristics.

    Simple approach: check if table name (or singular/plural variants) appear in question.
    """
    question_lower = question.lower()
    relevant = []

    for table in table_names:
        table_lower = table.lower()

        # Direct match
        if table_lower in question_lower:
            relevant.append(table)
            continue

        # Try singular/plural variants
        if table_lower.endswith('s') and table_lower[:-1] in question_lower:
            relevant.append(table)
            continue

        if table_lower + 's' in question_lower:
            relevant.append(table)
            continue

    return relevant


def _filter_schema_by_tables(full_schema: str, table_names: List[str]) -> str:
    """
    Filter schema string to include only specified tables.

    Preserves the structure of the schema output but only includes relevant sections.
    """
    if not table_names:
        return full_schema

    # Split schema into table sections
    lines = full_schema.split('\n')
    filtered_lines = []
    include_section = False
    current_table = None

    for line in lines:
        # Check if line is a table header
        for table in table_names:
            if f'Table: {table}' in line or f'## {table}' in line or f'CREATE TABLE {table}' in line.upper():
                include_section = True
                current_table = table
                break

        # Include line if we're in a relevant section
        if include_section:
            filtered_lines.append(line)

            # Check if section ends (next table or empty lines)
            if line.strip() == '' and len(filtered_lines) > 10:
                # Might be end of table section, prepare to check next table
                include_section = False

    if filtered_lines:
        return '\n'.join(filtered_lines)
    else:
        # If filtering failed, return full schema
        return full_schema


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
    max_results: int = 5
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
        r'^\s+(\w+)\s+(?:INT|VARCHAR|TEXT|TIMESTAMP|DECIMAL|BIGINT|FLOAT|DOUBLE|DATE|DATETIME|BOOLEAN|BOOL)',
        r'^\s+-\s+(\w+):',
        r'`(\w+)`\s+(?:INT|VARCHAR|TEXT|TIMESTAMP|DECIMAL|BIGINT|FLOAT|DOUBLE|DATE|DATETIME|BOOLEAN|BOOL)'
    ]

    for line in schema.split('\n'):
        for pattern in patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                columns.append(match.group(1))
                break

    return list(set(columns))  # Deduplicate


def _detect_schema_mismatch(
    error_message: str,
    failed_sql: str,
    filtered_schema: str
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
                r'\b' + wrong_col + r'\b',
                best_match,
                failed_sql,
                flags=re.IGNORECASE
            )

            suggestions_text = "\n".join([
                f"  - {name} (similarity: {score*100:.0f}%)"
                for name, score in suggestions[:3]
            ])

            return {
                'found_suggestions': True,
                'diagnosis': f"Column '{wrong_col}' not found. Did you mean '{best_match}'?",
                'corrected_sql': corrected_sql,
                'explanation': f"Replaced '{wrong_col}' with '{best_match}'",
                'confidence': suggestions[0][1],
                'suggestions': suggestions_text
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
                r'\b' + wrong_table + r'\b',
                best_match,
                failed_sql,
                flags=re.IGNORECASE
            )

            suggestions_text = "\n".join([
                f"  - {name} (similarity: {score*100:.0f}%)"
                for name, score in suggestions[:3]
            ])

            return {
                'found_suggestions': True,
                'diagnosis': f"Table '{wrong_table}' not found. Did you mean '{best_match}'?",
                'corrected_sql': corrected_sql,
                'explanation': f"Replaced table '{wrong_table}' with '{best_match}'",
                'confidence': suggestions[0][1],
                'suggestions': suggestions_text
            }

    return None

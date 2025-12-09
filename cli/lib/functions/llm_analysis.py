"""
LLM Analysis Functions for RDST Analyze

Provides functions to analyze query performance using LLMs and extract
actionable insights including query rewrites, index suggestions, and
ReadySet caching recommendations.
"""

import json
import os
import re
from typing import Dict, Any, List, Optional


def estimate_tokens(text: str) -> int:
    """
    Estimate token count for text (Claude/GPT tokenizers average ~4 chars per token).

    This is a rough estimate - actual tokens may vary by ±20%.
    """
    if not text:
        return 0
    # Claude tokenizer is roughly 4 characters per token for English text
    # JSON/code tends to be slightly more tokens per character
    return len(text) // 4


# Claude pricing (as of 2025) - $ per million tokens
CLAUDE_PRICING = {
    "claude-sonnet-4-5-20250929": {"input": 3.0, "output": 15.0},  # Default - same price as 4
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-opus-4-20250514": {"input": 15.0, "output": 75.0},
    # Fallback for unknown models (assume Sonnet pricing)
    "default": {"input": 3.0, "output": 15.0},
}


def estimate_cost(tokens_in: int, tokens_out: int, model: str = "default") -> float:
    """
    Estimate cost in USD for a given token count.

    Args:
        tokens_in: Input tokens
        tokens_out: Output tokens
        model: Model name for pricing lookup

    Returns:
        Estimated cost in USD
    """
    pricing = CLAUDE_PRICING.get(model, CLAUDE_PRICING["default"])
    cost_in = (tokens_in / 1_000_000) * pricing["input"]
    cost_out = (tokens_out / 1_000_000) * pricing["output"]
    return cost_in + cost_out


from ..llm_manager.llm_manager import LLMManager
from ..prompts.analyze_prompts import (
    EXPLAIN_ANALYSIS_PROMPT,
    HOTSPOT_IDENTIFICATION_PROMPT,
    REWRITE_SUGGESTION_PROMPT,
    INDEX_SUGGESTION_PROMPT,
    READYSET_CACHING_PROMPT
)


def analyze_with_llm(explain_results: Dict[str, Any], query_metrics: Dict[str, Any],
                     parameterized_sql: str, original_sql: str = None, schema_info: str = None, **kwargs) -> Dict[str, Any]:
    """
    Perform comprehensive LLM analysis of query performance.

    Args:
        explain_results: Results from EXPLAIN ANALYZE execution
        query_metrics: Additional metrics from database telemetry
        parameterized_sql: The SQL query (parameterized for LLM safety)
        original_sql: Original SQL with actual values (for rewrites)
        schema_info: Schema information from collect_target_schema step
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing:
        - success: boolean indicating if analysis succeeded
        - analysis_results: comprehensive analysis from LLM
        - hotspots: identified performance hotspots
        - optimization_suggestions: list of optimization recommendations
        - error: error message if failed
    """
    try:
        # Use provided schema_info or default
        if not schema_info:
            schema_info = "Schema information: Not available"
        # Handle case where WorkflowManager passes explain_results as string
        if isinstance(explain_results, str):
            # Try to parse as JSON if it's a string
            try:
                import json
                explain_results = json.loads(explain_results)
            except (json.JSONDecodeError, TypeError):
                # If parsing fails, create a basic structure
                explain_results = {
                    'success': False,
                    'database_engine': 'unknown',
                    'execution_time_ms': 0,
                    'rows_examined': 0,
                    'rows_returned': 0,
                    'cost_estimate': 0
                }

        # Check if EXPLAIN ANALYZE failed - don't proceed with LLM analysis
        if not explain_results.get('success', False):
            error_msg = explain_results.get('error', 'EXPLAIN ANALYZE failed')
            return {
                "success": False,
                "error": f"Cannot perform LLM analysis: {error_msg}",
                "analysis_results": {},
                "rewrite_suggestions": [],
                "index_recommendations": [],
                "optimization_opportunities": []
            }

        # Use the direct approach that's been working in fallback
        llm_manager = LLMManager()

        # Send real values to LLM by default - this produces more accurate analysis
        # and makes rewrites directly testable. The parameterized version is only used
        # for storage/display when we want to protect PII.
        sql_for_analysis = original_sql if original_sql else parameterized_sql
        sql_for_rewrites = original_sql if original_sql else parameterized_sql

        # Enhanced analysis with rewrite suggestions
        # Calculate performance metrics for context
        execution_time_ms = explain_results.get('execution_time_ms', 0)
        rows_examined = explain_results.get('rows_examined', 0)
        rows_returned = explain_results.get('rows_returned', 0)

        # Extract table size from schema_info if available
        row_estimate = 0
        if 'Row estimate:' in schema_info:
            import re
            match = re.search(r'Row estimate:\s*([\d,]+)', schema_info)
            if match:
                row_estimate = int(match.group(1).replace(',', ''))

        # Calculate scan efficiency percentage
        scan_pct = (rows_examined / row_estimate * 100) if row_estimate > 0 else 0

        # Performance classification
        if execution_time_ms < 10:
            perf_class = "FAST (already well-optimized)"
        elif execution_time_ms < 100:
            perf_class = "MODERATE"
        else:
            perf_class = "SLOW (optimization needed)"

        # Scan efficiency classification
        if scan_pct < 1:
            scan_efficiency = "EFFICIENT (index/filter working well)"
        elif scan_pct < 10:
            scan_efficiency = "ACCEPTABLE"
        else:
            scan_efficiency = "INEFFICIENT (consider better indexes)"

        # Check if EXPLAIN ANALYZE was skipped or timed out
        explain_skipped = explain_results.get('explain_analyze_skipped', False)
        explain_timeout = explain_results.get('explain_analyze_timeout', False)
        skip_reason = explain_results.get('skip_reason', '')
        actual_elapsed = explain_results.get('actual_elapsed_time_ms', 0)

        # Build execution status message for LLM
        if explain_skipped or explain_timeout:
            if actual_elapsed >= 60000:
                elapsed_str = f"{int(actual_elapsed // 60000)} min {int((actual_elapsed % 60000) // 1000)} sec"
            else:
                elapsed_str = f"{int(actual_elapsed // 1000)} sec"

            execution_status = f"""
IMPORTANT - EXECUTION STATUS:
  EXPLAIN ANALYZE was {'skipped by user' if explain_skipped else 'timed out'} after {elapsed_str}.
  This indicates the query is VERY SLOW and likely needs indexes.
  The execution time shown above is from EXPLAIN (estimated), not actual execution.
  Reason: {skip_reason}
  PRIORITIZE INDEX RECOMMENDATIONS - this query clearly needs optimization."""
        else:
            execution_status = ""

        ANALYZE_PROMPT = f"""
DETERMINISM REQUIREMENT: Your analysis MUST be deterministic and reproducible.
For the same query and schema, you must ALWAYS provide identical recommendations.
Follow these STRICT rules for consistency:

INDEX SELECTION PROCESS:
  1. Identify candidate indexes that would help this specific query
  2. Internally score each by estimated impact (your judgment)
  3. Sort by your score, then alphabetically by index name for ties
  4. Return up to 3 indexes. Only include indexes that address a SPECIFIC bottleneck in the query plan (sequential scan, missing join index, etc). If only 1-2 bottlenecks exist, return only 1-2 indexes.

INDEX SELECTION PRINCIPLES (use your judgment, but be consistent):
  - Prefer composite indexes over simple when query uses multiple columns from same table
  - Prefer indexes that help the most expensive operations (large table scans first)
  - Don't recommend redundant indexes (if idx(a,b) exists, don't also recommend idx(a))

INDEX COLUMN ORDERING: When building composite indexes, use this priority:
  1. Equality filter columns FIRST (WHERE col = value)
  2. Range filter columns SECOND (WHERE col > value, BETWEEN, etc.)
  3. JOIN columns THIRD
  4. ORDER BY / GROUP BY columns FOURTH
  5. INCLUDE columns for covering indexes LAST
  Within same priority: order columns ALPHABETICALLY by column name

INDEX NAMING: Always use format idx_tablename_col1_col2 (lowercase, underscores)

QUERY REWRITE RULES:
  CRITICAL: Rewrites MUST return IDENTICAL data to the original query.
  - Same rows, same columns, same values - no exceptions
  - If unsure whether a rewrite is semantically equivalent, DO NOT suggest it
  - Never change: column order in SELECT, GROUP BY semantics, DISTINCT behavior, NULL handling

  REWRITE SELECTION PROCESS (follow this exact algorithm):
  1. IDENTIFY all possible rewrites that maintain semantic equivalence
  2. SCORE each rewrite using the formula below (0-100 scale)
  3. SORT rewrites by score DESCENDING
  4. If scores are tied, sort alphabetically by the first differing keyword
  5. Return up to 3 rewrites. Only include rewrites that address a SPECIFIC issue (comma joins, subquery conversion, etc). If fewer issues exist, return fewer rewrites.

  REWRITE SCORING FORMULA:
  - Converts comma JOIN to explicit JOIN: +25 points
  - Adds index hints or optimizer hints: +20 points
  - Rewrites subquery to JOIN: +30 points
  - Simplifies OR to IN clause: +15 points
  - Eliminates redundant operations: +20 points
  - Improves predicate pushdown opportunity: +20 points
  - SUBTRACT 50 points if rewrite might change results (don't recommend these)

  REWRITE FORMATTING: For deterministic output:
  - NEVER reorder columns in SELECT clause (must match original exactly)
  - Order JOIN tables alphabetically by alias (a, b, c) when converting comma joins
  - Order WHERE conditions alphabetically by column name

Analyze this SQL query performance and distinguish between immediate query rewrites vs database optimization recommendations:

Query for Analysis: {sql_for_analysis}
Database: {explain_results.get('database_engine', 'unknown')}
{execution_status}
PERFORMANCE METRICS:
  Execution Time: {execution_time_ms}ms → {perf_class}
  Rows Examined: {rows_examined:,} {f'({scan_pct:.2f}% of table)' if row_estimate > 0 else ''}
  Rows Returned: {rows_returned:,}
  Cost Estimate: {explain_results.get('cost_estimate', 0)}
  Scan Efficiency: {scan_efficiency}

{schema_info}

Original Query for Rewrites: {sql_for_rewrites}

Provide analysis in JSON format with:
{{
  "performance_assessment": {{
    "overall_rating": "excellent/good/fair/poor",
    "efficiency_score": 85,
    "primary_concerns": ["concern1", "concern2"]
  }},
  "execution_analysis": {{
    "bottlenecks": ["bottleneck1"],
    "scan_efficiency": "analysis"
  }},
  "optimization_opportunities": [
    // General database optimization suggestions (not query rewrites or indexes)
    // Use standard categories for 'type' when applicable:
    //   - "caching": query result caching, materialized views
    //   - "partitioning": table/index partitioning strategies
    //   - "normalization": schema design improvements
    //   - "statistics": ANALYZE/VACUUM operations
    //   - "configuration": database parameter tuning
    //   - "replication": read replica strategies
    {{
      "priority": "high",
      "description": "suggestion",
      "type": "caching"
    }}
  ],
  "rewrite_suggestions": [
    {{
      "rewritten_sql": "SELECT ... (complete rewritten query that should perform better)",
      "explanation": "why this rewrite should be faster",
      "expected_improvement": "estimated percentage improvement",
      "priority": "high",
      "optimization_type": "query_restructure"
    }}
  ],
  "index_recommendations": [
    {{
      "sql": "CREATE INDEX idx_name ON table_name(col1, col2)",
      "table": "table_name",
      "columns": ["col1", "col2"],
      "index_type": "btree",
      "rationale": "explanation of why this index helps",
      "estimated_impact": "high/medium/low",
      "caveats": ["workload_context: This index helps this query but consider full workload analysis", "other relevant warnings"]
    }}
  ]
}}

CRITICAL DISTINCTIONS:
- REWRITE_SUGGESTIONS: Only include query modifications that work with EXISTING database structure (no new indexes/tables)
  * Provide the TOP 3 best rewrites (if they exist), ordered by expected impact
  * Examples: JOIN reordering, WHERE clause optimization, subquery elimination, EXISTS vs IN, LIMIT additions
  * Use the schema information above to understand existing columns and indexes
  * Modern JOIN syntax (INNER JOIN instead of comma joins), add LIMIT to unbounded ORDER BY queries
  * These must be executable immediately without database schema changes

  ** CRITICAL REQUIREMENT: Each rewrite MUST produce IDENTICAL results to the original query **
  * Same rows, same columns, same order (if ORDER BY is specified)
  * WHERE clause optimization is OK (order doesn't affect results)
  * DO NOT change LIMIT values, column selections, or filtering logic
  * DO NOT suggest rewrites that alter result semantics in any way
  * If you cannot guarantee identical results, DO NOT include that rewrite
  * If no valid rewrites exist, return empty array

  ** SPECIAL RULE FOR SELECT * QUERIES **
  * If the original query uses SELECT *, rewrites MUST preserve SELECT * or include ALL table columns
  * NEVER suggest partial column lists for SELECT * rewrites - this changes the output
  * Valid SELECT * rewrites: add index hints, change JOIN order, optimize WHERE clause
  * Example VALID rewrite: SELECT * FROM posts FORCE INDEX (idx) WHERE ... (keeps SELECT *)
  * Example INVALID rewrite: SELECT id, title FROM posts WHERE ... (changes columns from SELECT *)
  * If suggesting to replace SELECT * with explicit columns, provide this as a separate recommendation
    in "optimization_opportunities" with type "column_selection", NOT as a query rewrite

  ** CRITICAL: DO NOT INTRODUCE CTEs TO FLAT QUERIES **

  If the original query has NO subqueries or nested SELECTs:
  - NEVER suggest rewriting it to use CTEs (WITH clauses)
  - CTEs add overhead and prevent optimizer from pushing filters down
  - A flat query gives PostgreSQL maximum optimization flexibility

  CTEs are ONLY acceptable in rewrites when:
  - Original query ALREADY contains subqueries/nested SELECTs (restructuring, not introducing)
  - AND the rewrite is tested and shows actual improvement
  - AND for PostgreSQL >= 12, consider suggesting NOT MATERIALIZED hint

  ** CRITICAL ANTI-PATTERN: DISTINCT + LIMIT → GROUP BY + LIMIT **

  NEVER suggest replacing DISTINCT with GROUP BY when ALL of these are true:
  1. Query has a LIMIT clause
  2. Query has NO aggregate functions (COUNT, SUM, AVG, MAX, MIN, etc.)
  3. Query execution is already fast (< 100ms)

  WHY THIS IS CRITICAL:
  - DISTINCT + LIMIT can short-circuit: MySQL stops scanning after finding N distinct values
    Example: DISTINCT ... LIMIT 1000 → stops after finding 1000 unique values

  - GROUP BY + LIMIT must complete ALL grouping before LIMIT is applied:
    Example: GROUP BY ... LIMIT 1000 → groups ALL distinct values (could be millions),
             then discards all but 1000. This is 100x-10000x slower!

  - On a table with 9M rows and 1.5M distinct values:
    DISTINCT + LIMIT 1000: scans ~10K rows, returns in 2ms
    GROUP BY + LIMIT 1000: scans 3.3M rows, groups 1.5M values, returns in 19,000ms (10,000x slower!)

  WHEN YOU CAN SUGGEST GROUP BY:
  ✓ Query has aggregate functions: SELECT col, COUNT(*) FROM table GROUP BY col
  ✓ No LIMIT clause present: SELECT DISTINCT col FROM table (GROUP BY may use better execution plan)
  ✓ Converting to add aggregates: SELECT col, COUNT(*) ... (was just SELECT DISTINCT col)

  GENERAL PRINCIPLE FOR FAST QUERIES:
  - If execution time < 10ms and rows examined < 1% of table → query is ALREADY OPTIMAL
  - Only suggest rewrites if they address specific inefficiencies (full table scan, missing index usage, etc.)
  - Do NOT suggest "alternative syntax" rewrites for already-optimized queries

  ** CRITICAL CORRECTNESS ISSUE: LIMIT WITHOUT ORDER BY **

  ALWAYS check if the query has LIMIT without ORDER BY:
  - Query contains a LIMIT clause
  - Query has NO ORDER BY clause

  This causes NON-DETERMINISTIC RESULTS - the query returns different rows on each execution.
  This is a CORRECTNESS/RELIABILITY problem, not just a performance issue.

  DETECTION:
  - Include in "key_issues": "Query uses LIMIT without ORDER BY, causing non-deterministic results"
  - Severity: HIGH (affects correctness, not just performance)
  - Explain: "Without ORDER BY, the database may return different rows each time the query runs"

  RECOMMENDATIONS:
  - Add to "optimization_opportunities" with type "correctness":
    {{
      "type": "correctness",
      "priority": "high",
      "description": "Add ORDER BY clause to ensure deterministic results",
      "rationale": "LIMIT without ORDER BY returns arbitrary rows; add ORDER BY to guarantee consistent results",
      "example": "ORDER BY id  -- or another column to ensure consistent ordering"
    }}

  QUERY REWRITES WITH LIMIT (no ORDER BY):
  - You CAN still suggest performance rewrites (index hints, FORCE INDEX, etc.)
  - These improve performance but don't fix the non-determinism
  - Include disclaimer in rewrite description:
    "Note: This improves performance but doesn't fix the non-deterministic LIMIT issue. Add ORDER BY for consistent results."

  ** CRITICAL: SEMANTIC EQUIVALENCE IN REWRITES **

  Rewrites MUST produce IDENTICAL results to the original query.
  This means: same rows, same columns, same count, same aggregates.

  EXPLICITLY FORBIDDEN in rewrites:
  ✗ Adding WHERE conditions: "WHERE score > 100" → "WHERE score > 100 AND post_type_id = 1" (WRONG!)
  ✗ Adding WHERE conditions: "WHERE score > 100" → "WHERE score > 100 AND post_type_id IS NOT NULL" (WRONG!)
  ✗ Changing filter values: "WHERE score > 100" → "WHERE score > 50" (WRONG!)
  ✗ Removing WHERE conditions: "WHERE a = 1 AND b = 2" → "WHERE a = 1" (WRONG!)
  ✗ Adding/removing columns from SELECT: "SELECT a" → "SELECT a, b" (WRONG!)
  ✗ Adding/removing DISTINCT
  ✗ Adding/removing GROUP BY, HAVING
  ✗ Adding/removing ORDER BY (unless original has none)
  ✗ Changing LIMIT value

  ALLOWED in rewrites:
  ✓ Index hints: SELECT /*+ INDEX(posts idx_score) */ ... WHERE score > 100
  ✓ FORCE INDEX: SELECT * FROM posts FORCE INDEX (idx_score) WHERE score > 100
  ✓ JOIN order hints: SELECT /*+ LEADING(t1 t2) */ ...
  ✓ Reordering WHERE conditions: "WHERE a = 1 AND b = 2" → "WHERE b = 2 AND a = 1" (same filters)
  ✓ Subquery → JOIN conversion (ONLY if semantically equivalent)

  If you want to suggest a query with DIFFERENT filters (e.g., adding a WHERE condition):
  - Put it in "optimization_opportunities" with type "query_pattern"
  - Explain it would change the result set
  - Do NOT include it in "rewrite_suggestions"

- INDEX_RECOMMENDATIONS: Suggest new indexes or database structure changes
  * Use the schema information to see what indexes already exist - do NOT suggest duplicates
  * These require CREATE INDEX statements or schema modifications
  * Put ALL index suggestions here, not in rewrite_suggestions
  * Provide full CREATE INDEX statements with proper syntax

- OPTIMIZATION_OPPORTUNITIES: General recommendations like query caching, connection pooling, etc.

CRITICAL ANTI-HALLUCINATION RULES (from Gautam's validation):
- Check existing indexes CAREFULLY before suggesting new ones - look at the USING clause
- Pay attention to index types: HASH indexes CANNOT be used for JOINs or range scans in PostgreSQL
- If you see "USING hash" on a join column, suggest REPLACING it with BTREE, not creating duplicate
- Consider table size (row_estimate) when explaining impact - "matters more with 59M rows than 100 rows"
- NEVER suggest an index that already exists - if index exists but wrong type, suggest DROP + CREATE
- Focus on actionable recommendations that can be executed immediately

Base rewrites on "Original Query for Rewrites" with exact values, no placeholders.
Use schema information to make informed recommendations about columns and existing indexes.
Return empty rewrite_suggestions array if no immediate query improvements are possible without indexes.
"""

        # Define JSON schema for structured output (Anthropic/OpenAI native JSON mode)
        json_schema = {
            "type": "json_schema",
            "json_schema": {
                "name": "query_performance_analysis",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "performance_assessment": {
                            "type": "object",
                            "properties": {
                                "overall_rating": {"type": "string", "enum": ["excellent", "good", "fair", "poor"]},
                                "efficiency_score": {"type": "number"},
                                "primary_concerns": {"type": "array", "items": {"type": "string"}}
                            },
                            "required": ["overall_rating", "efficiency_score", "primary_concerns"],
                            "additionalProperties": False
                        },
                        "execution_analysis": {
                            "type": "object",
                            "properties": {
                                "bottlenecks": {"type": "array", "items": {"type": "string"}},
                                "scan_efficiency": {"type": "string"}
                            },
                            "required": ["bottlenecks", "scan_efficiency"],
                            "additionalProperties": False
                        },
                        "optimization_opportunities": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                                    "description": {"type": "string"},
                                    "type": {"type": "string"}
                                },
                                "required": ["priority", "description", "type"],
                                "additionalProperties": False
                            }
                        },
                        "rewrite_suggestions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "rewritten_sql": {"type": "string"},
                                    "explanation": {"type": "string"},
                                    "expected_improvement": {"type": "string"},
                                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                                    "optimization_type": {"type": "string"}
                                },
                                "required": ["rewritten_sql", "explanation", "expected_improvement", "priority", "optimization_type"],
                                "additionalProperties": False
                            }
                        },
                        "index_recommendations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "sql": {"type": "string"},
                                    "table": {"type": "string"},
                                    "columns": {"type": "array", "items": {"type": "string"}},
                                    "index_type": {"type": "string"},
                                    "rationale": {"type": "string"},
                                    "estimated_impact": {"type": "string", "enum": ["high", "medium", "low"]}
                                },
                                "required": ["sql", "table", "columns", "index_type", "rationale", "estimated_impact"],
                                "additionalProperties": False
                            }
                        }
                    },
                    "required": ["performance_assessment", "execution_analysis", "optimization_opportunities", "rewrite_suggestions", "index_recommendations"],
                    "additionalProperties": False
                }
            }
        }

        # Call LLM with enhanced approach and JSON mode
        # Don't pass a default model for LM Studio - let it use whatever is loaded
        model_param = kwargs.get('model')

        # Determine provider to check if JSON mode is supported
        provider = kwargs.get('provider', 'claude').lower()
        use_json_mode = provider in ['claude', 'openai'] and os.getenv("RDST_USE_JSON_MODE", "true").lower() == "true"

        extra_params = {}
        if use_json_mode:
            extra_params["response_format"] = json_schema

        # Estimate input tokens before call (for progress display)
        system_msg = "You are a database performance expert. Respond with valid JSON only. Be proactive in suggesting query rewrites when you see opportunities like: old-style comma JOINs, missing LIMIT on ORDER BY, inefficient subqueries, or non-optimal WHERE clause ordering."
        estimated_input_tokens = estimate_tokens(system_msg) + estimate_tokens(ANALYZE_PROMPT)

        # Store estimate in kwargs for progress display access
        kwargs['_estimated_input_tokens'] = estimated_input_tokens

        llm_response = llm_manager.generate_response(
            prompt=ANALYZE_PROMPT,
            model=model_param,
            system_message=system_msg,
            max_tokens=2000,
            temperature=0.0,  # Deterministic output for consistent recommendations
            extra=extra_params if extra_params else None
        )

        if not llm_response or 'response' not in llm_response:
            return {
                "success": False,
                "error": "No response from LLM",
                "analysis_results": {},
                "hotspots": []
            }

        # Parse LLM response
        try:
            analysis_json = _extract_json_from_response(llm_response['response'])
            if not analysis_json:
                # If JSON parsing fails, return raw response with error flag
                return {
                    "success": False,
                    "error": "Failed to parse LLM response as JSON",
                    "raw_response": llm_response['response'],
                    "analysis_results": {},
                    "hotspots": []
                }

            # Validate analysis structure
            validated_analysis = _validate_analysis_structure(analysis_json)

            # Extract top-level recommendations
            rewrite_suggestions = analysis_json.get('rewrite_suggestions', [])
            index_recommendations = analysis_json.get('index_recommendations', [])

            # Get detailed token usage
            tokens_used = llm_response.get('tokens_used') or 0
            model_used = llm_response.get('model') or model_param or 'claude-sonnet-4-5-20250929'

            # Try to get actual token breakdown if available
            # tokens_used is usually total, but we can estimate breakdown
            actual_input = estimated_input_tokens  # Use estimate for input
            actual_output = tokens_used - actual_input if tokens_used > actual_input else tokens_used // 2

            # Calculate cost
            cost_usd = estimate_cost(actual_input, actual_output, model_used)

            return {
                "success": True,
                "analysis_results": validated_analysis,
                "hotspots": validated_analysis.get('execution_analysis', {}),
                "optimization_suggestions": validated_analysis.get('optimization_opportunities', []),
                "rewrite_suggestions": rewrite_suggestions,
                "index_recommendations": index_recommendations,
                "llm_model": model_used,
                "tokens_used": tokens_used,
                "token_usage": {
                    "input": actual_input,
                    "output": actual_output,
                    "total": tokens_used if tokens_used else actual_input + actual_output,
                    "estimated_cost_usd": cost_usd,
                },
            }

        except Exception as parse_error:
            return {
                "success": False,
                "error": f"Failed to parse LLM analysis: {str(parse_error)}",
                "raw_response": llm_response.get('response', ''),
                "analysis_results": {},
                "hotspots": []
            }


    except Exception as e:
        print(f"DEBUG: LLM analysis failed with error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": f"LLM analysis failed: {str(e)}",
            "analysis_results": {},
            "hotspots": []
        }


def extract_rewrites(analysis_results: Dict[str, Any], parameterized_sql: str,
                     database_engine: str, **kwargs) -> Dict[str, Any]:
    """
    Extract query rewrite suggestions from LLM analysis results.

    NOTE: Rewrites and index recommendations are now generated directly in analyze_with_llm
    with schema context. This function just extracts them from the analysis results.

    Args:
        analysis_results: Results from previous LLM analysis (contains rewrite_suggestions and index_recommendations)
        parameterized_sql: The SQL query (parameterized for LLM safety)
        database_engine: Database engine type
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing:
        - success: boolean indicating if extraction succeeded
        - rewrite_suggestions: list of suggested query rewrites
        - index_suggestions: list of suggested indexes
        - caching_recommendations: ReadySet caching analysis
        - error: error message if failed
    """
    try:
        llm_manager = LLMManager()

        # Extract rewrites and index recommendations that were already generated with schema context
        # They can be at top level or inside analysis_results
        rewrite_suggestions = analysis_results.get('rewrite_suggestions', [])
        if not rewrite_suggestions and 'analysis_results' in analysis_results:
            rewrite_suggestions = analysis_results['analysis_results'].get('rewrite_suggestions', [])

        index_recommendations = analysis_results.get('index_recommendations', [])
        if not index_recommendations and 'analysis_results' in analysis_results:
            index_recommendations = analysis_results['analysis_results'].get('index_recommendations', [])

        results = {
            "success": True,
            "rewrite_suggestions": rewrite_suggestions,
            "index_suggestions": index_recommendations,
            "caching_recommendations": {},
            "extraction_results": {}
        }

        # 3. ReadySet Caching Analysis
        caching_results = _get_caching_recommendations(
            llm_manager, parameterized_sql, analysis_results, kwargs
        )
        results['caching_recommendations'] = caching_results
        results['extraction_results']['caching_analysis'] = caching_results

        return results

    except Exception as e:
        return {
            "success": False,
            "error": f"Rewrite extraction failed: {str(e)}",
            "rewrite_suggestions": [],
            "index_suggestions": [],
            "caching_recommendations": {}
        }


def _get_rewrite_suggestions(llm_manager: LLMManager, parameterized_sql: str,
                            performance_analysis: str, identified_issues: List[Dict],
                            database_engine: str, kwargs: Dict) -> Dict[str, Any]:
    """Get query rewrite suggestions from LLM."""
    try:
        # Format issues for prompt
        issues_text = "\n".join([
            f"- {issue.get('description', 'Unknown issue')}"
            for issue in identified_issues[:5]  # Limit to top 5 issues
        ])

        # Get schema info if available from workflow context
        schema_info = kwargs.get('schema_info', 'Schema information not available')

        prompt = REWRITE_SUGGESTION_PROMPT.format(
            parameterized_sql=parameterized_sql,
            performance_analysis=performance_analysis,
            identified_issues=issues_text,
            database_engine=database_engine,
            schema_info=schema_info
        )

        llm_response = llm_manager.generate_response(
            prompt=prompt,
            model=kwargs.get('model'),  # Use provider's default model
            system_message="You are an expert SQL optimization consultant.",
            max_tokens=1500,
            temperature=0.0  # Deterministic output for consistent recommendations
        )

        if llm_response and 'response' in llm_response:
            rewrite_json = _extract_json_from_response(llm_response['response'])
            return rewrite_json or {"rewrite_suggestions": []}

        return {"rewrite_suggestions": []}

    except Exception:
        return {"rewrite_suggestions": []}


def _get_index_suggestions(llm_manager: LLMManager, parameterized_sql: str,
                          analysis_results: Dict[str, Any], database_engine: str,
                          kwargs: Dict) -> Dict[str, Any]:
    """Get index suggestions from LLM."""
    try:
        # Extract relevant data for index analysis
        execution_analysis = json.dumps(analysis_results.get('execution_analysis', {}), indent=2)
        performance_metrics = json.dumps(analysis_results.get('performance_assessment', {}), indent=2)

        # Extract table information (simplified)
        tables_involved = _extract_table_names_from_query(parameterized_sql)
        existing_indexes = kwargs.get('existing_indexes', 'Index information not available')

        prompt = INDEX_SUGGESTION_PROMPT.format(
            parameterized_sql=parameterized_sql,
            execution_analysis=execution_analysis,
            performance_metrics=performance_metrics,
            database_engine=database_engine,
            tables_involved=json.dumps(tables_involved),
            existing_indexes=existing_indexes
        )

        llm_response = llm_manager.generate_response(
            prompt=prompt,
            model=kwargs.get('model'),  # Use provider's default model
            system_message="You are a database indexing expert.",
            max_tokens=1500,
            temperature=0.0  # Deterministic output for consistent recommendations
        )

        if llm_response and 'response' in llm_response:
            index_json = _extract_json_from_response(llm_response['response'])
            return index_json or {"index_recommendations": []}

        return {"index_recommendations": []}

    except Exception:
        return {"index_recommendations": []}


def _get_caching_recommendations(llm_manager: LLMManager, parameterized_sql: str,
                                analysis_results: Dict[str, Any], kwargs: Dict) -> Dict[str, Any]:
    """Get ReadySet caching recommendations from LLM."""
    try:
        # Analyze query characteristics for caching
        query_characteristics = _analyze_query_characteristics(parameterized_sql)
        performance_metrics = json.dumps(analysis_results.get('performance_assessment', {}), indent=2)

        # Default values for caching analysis
        execution_frequency = kwargs.get('execution_frequency', 'unknown')
        read_write_ratio = kwargs.get('read_write_ratio', 'unknown')

        prompt = READYSET_CACHING_PROMPT.format(
            parameterized_sql=parameterized_sql,
            query_characteristics=json.dumps(query_characteristics, indent=2),
            performance_metrics=performance_metrics,
            execution_frequency=execution_frequency,
            read_write_ratio=read_write_ratio
        )

        llm_response = llm_manager.generate_response(
            prompt=prompt,
            model=kwargs.get('model'),  # Use provider's default model
            system_message="You are a ReadySet caching optimization expert.",
            max_tokens=1500,
            temperature=0.0  # Deterministic output for consistent recommendations
        )

        if llm_response and 'response' in llm_response:
            caching_json = _extract_json_from_response(llm_response['response'])
            return caching_json or {}

        return {}

    except Exception:
        return {}


# Utility functions
def _format_additional_metrics(query_metrics: Dict[str, Any]) -> str:
    """Format additional metrics for LLM prompt."""
    if not query_metrics or not query_metrics.get('success', False):
        return "Additional metrics not available"

    metrics = query_metrics.get('metrics', {})
    if not metrics:
        return "No additional metrics collected"

    # Format metrics by source
    formatted = []
    available_sources = query_metrics.get('available_sources', [])

    for source in available_sources:
        source_metrics = {k: v for k, v in metrics.items() if k.startswith(source.replace('_', '_').split('_')[0])}
        if source_metrics:
            formatted.append(f"{source.upper()}:")
            for key, value in list(source_metrics.items())[:10]:  # Limit to prevent prompt overflow
                formatted.append(f"  - {key}: {value}")

    return "\n".join(formatted) if formatted else "Metrics collected but not formatted"


def _extract_json_from_response(response: str) -> Optional[Dict[str, Any]]:
    """Extract JSON from LLM response, handling various formats."""
    try:
        # Try direct JSON parsing first
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # Try to find JSON within markdown code blocks
    json_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
    match = re.search(json_pattern, response, re.DOTALL | re.IGNORECASE)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find JSON within the response text
    # Look for content between first { and last }
    first_brace = response.find('{')
    last_brace = response.rfind('}')

    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        try:
            json_str = response[first_brace:last_brace + 1]
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    return None


def _validate_analysis_structure(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize analysis structure."""
    # Ensure required top-level keys exist
    validated = {
        "performance_assessment": analysis.get("performance_assessment", {}),
        "execution_analysis": analysis.get("execution_analysis", {}),
        "optimization_opportunities": analysis.get("optimization_opportunities", []),
        "rewrite_suggestions": analysis.get("rewrite_suggestions", []),
        "explanation": analysis.get("explanation", "Analysis completed")
    }

    # Validate performance assessment structure
    perf_assessment = validated["performance_assessment"]
    if not isinstance(perf_assessment.get("overall_rating"), str):
        perf_assessment["overall_rating"] = "unknown"
    if not isinstance(perf_assessment.get("efficiency_score"), (int, float)):
        perf_assessment["efficiency_score"] = 0

    # Ensure optimization opportunities is a list
    if not isinstance(validated["optimization_opportunities"], list):
        validated["optimization_opportunities"] = []

    # Ensure rewrite suggestions is a list
    if not isinstance(validated["rewrite_suggestions"], list):
        validated["rewrite_suggestions"] = []

    return validated


def _extract_table_names_from_query(sql: str) -> List[str]:
    """Extract table names from SQL query using simple regex."""
    patterns = [
        r'\bFROM\s+([`"]?)(\w+)\1',
        r'\bJOIN\s+([`"]?)(\w+)\1',
        r'\bINTO\s+([`"]?)(\w+)\1',
        r'\bUPDATE\s+([`"]?)(\w+)\1',
    ]

    tables = set()
    sql_upper = sql.upper()

    for pattern in patterns:
        matches = re.findall(pattern, sql_upper, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                table_name = match[1]
            else:
                table_name = match

            if table_name and len(table_name) <= 64:
                tables.add(table_name.lower())

    return list(tables)[:10]


def _analyze_query_characteristics(sql: str) -> Dict[str, Any]:
    """Analyze query characteristics for caching assessment."""
    sql_upper = sql.upper()

    characteristics = {
        "is_select": "SELECT" in sql_upper,
        "has_joins": any(join in sql_upper for join in ["JOIN", "INNER JOIN", "LEFT JOIN", "RIGHT JOIN"]),
        "has_subqueries": "(" in sql and "SELECT" in sql_upper,
        "has_aggregations": any(agg in sql_upper for agg in ["COUNT", "SUM", "AVG", "MAX", "MIN", "GROUP BY"]),
        "has_order_by": "ORDER BY" in sql_upper,
        "has_limit": "LIMIT" in sql_upper,
        "query_complexity": "simple"  # Default, could be enhanced with more analysis
    }

    # Determine complexity
    complexity_score = sum([
        characteristics["has_joins"] * 2,
        characteristics["has_subqueries"] * 2,
        characteristics["has_aggregations"] * 1,
        len(re.findall(r'\bJOIN\b', sql_upper)),
        len(re.findall(r'\bSELECT\b', sql_upper)) - 1  # Subqueries
    ])

    if complexity_score <= 2:
        characteristics["query_complexity"] = "simple"
    elif complexity_score <= 5:
        characteristics["query_complexity"] = "moderate"
    else:
        characteristics["query_complexity"] = "complex"

    return characteristics
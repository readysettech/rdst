"""
Shallow LLM Analysis for RDST Scan

Performs schema-only analysis using LLM without requiring EXPLAIN ANALYZE output.
Used for CI pipelines where database connection is not available at scan time.
"""

import json
import logging
import os
from typing import Dict, Any

from ..llm_manager.llm_manager import LLMManager
from ..llm_manager.claude_provider import AnthropicModel
from ..prompts.analyze_prompts import SHALLOW_ANALYSIS_PROMPT

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """Estimate token count for text (~4 chars per token)."""
    if not text:
        return 0
    return len(text) // 4


# Pricing for cost estimation
CLAUDE_PRICING = {
    "claude-sonnet-4-5-20250929": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.0},
    "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
    "default": {"input": 3.0, "output": 15.0},
}


def estimate_cost(tokens_in: int, tokens_out: int, model: str = "default") -> float:
    """Estimate cost in USD for token usage."""
    pricing = CLAUDE_PRICING.get(model, CLAUDE_PRICING["default"])
    cost_in = (tokens_in / 1_000_000) * pricing["input"]
    cost_out = (tokens_out / 1_000_000) * pricing["output"]
    return cost_in + cost_out


def analyze_shallow_with_llm(
    parameterized_sql: str,
    original_sql: str = None,
    schema_info: str = None,
    database_engine: str = "postgresql",
    model: str = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Perform shallow LLM analysis using schema information only.

    This is the shallow-mode alternative to analyze_with_llm.
    Does NOT require EXPLAIN ANALYZE output - uses schema + indexes
    to predict potential performance issues.

    Args:
        parameterized_sql: The SQL query (parameterized for safety)
        original_sql: Original SQL with actual values (for rewrites)
        schema_info: Schema information from collect_schema_from_yaml
        database_engine: Database engine type (postgresql, mysql)
        model: LLM model to use (default: use provider's default)
        **kwargs: Additional parameters (ignored)

    Returns:
        Dict containing:
        - success: boolean indicating if analysis succeeded
        - analysis_results: comprehensive analysis from LLM
        - rewrite_suggestions: list of query rewrites
        - index_recommendations: list of index suggestions
        - error: error message if failed
    """
    try:
        if not schema_info:
            schema_info = "Schema information: Not available"

        # Use original SQL for analysis if available
        sql_for_analysis = original_sql if original_sql else parameterized_sql

        # Format the prompt
        formatted_prompt = SHALLOW_ANALYSIS_PROMPT.format(
            database_engine=database_engine,
            sql=sql_for_analysis,
            schema_info=schema_info
        )

        # Initialize LLM manager
        llm_manager = LLMManager()

        # Define JSON schema for structured output
        json_schema = {
            "type": "json_schema",
            "json_schema": {
                "name": "shallow_query_analysis",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "performance_assessment": {
                            "type": "object",
                            "properties": {
                                "overall_rating": {"type": "string", "enum": ["excellent", "good", "fair", "poor", "critical"]},
                                "risk_score": {"type": "number"},
                                "primary_concerns": {"type": "array", "items": {"type": "string"}}
                            },
                            "required": ["overall_rating", "risk_score", "primary_concerns"],
                            "additionalProperties": False
                        },
                        "structural_analysis": {
                            "type": "object",
                            "properties": {
                                "has_select_star": {"type": "boolean"},
                                "has_limit": {"type": "boolean"},
                                "has_order_by": {"type": "boolean"},
                                "uses_explicit_joins": {"type": "boolean"},
                                "subquery_count": {"type": "number"},
                                "potential_bottlenecks": {"type": "array", "items": {"type": "string"}}
                            },
                            "required": ["has_select_star", "has_limit", "has_order_by", "uses_explicit_joins", "subquery_count", "potential_bottlenecks"],
                            "additionalProperties": False
                        },
                        "index_coverage": {
                            "type": "object",
                            "properties": {
                                "where_columns_indexed": {"type": "boolean"},
                                "join_columns_indexed": {"type": "boolean"},
                                "order_by_columns_indexed": {"type": "boolean"},
                                "missing_indexes": {"type": "array", "items": {"type": "string"}}
                            },
                            "required": ["where_columns_indexed", "join_columns_indexed", "order_by_columns_indexed", "missing_indexes"],
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
                    "required": ["performance_assessment", "structural_analysis", "index_coverage", "optimization_opportunities", "rewrite_suggestions", "index_recommendations"],
                    "additionalProperties": False
                }
            }
        }

        # Determine if JSON mode is supported
        provider = kwargs.get('provider', 'claude').lower()
        use_json_mode = provider in ['claude', 'openai'] and os.getenv("RDST_USE_JSON_MODE", "true").lower() == "true"

        extra_params = {}
        if use_json_mode:
            extra_params["response_format"] = json_schema

        # Estimate input tokens
        system_msg = "You are a database performance expert. Analyze the query for potential performance issues based on structure and schema. Respond with valid JSON only."
        estimated_input_tokens = estimate_tokens(system_msg) + estimate_tokens(formatted_prompt)

        # Call LLM
        llm_response = llm_manager.generate_response(
            prompt=formatted_prompt,
            model=model,
            system_message=system_msg,
            max_tokens=2000,
            temperature=0.0,  # Deterministic output
            extra=extra_params if extra_params else None
        )

        if not llm_response or 'response' not in llm_response:
            return {
                "success": False,
                "error": "No response from LLM",
                "analysis_results": {},
                "rewrite_suggestions": [],
                "index_recommendations": []
            }

        # Parse LLM response
        try:
            analysis_json = _extract_json_from_response(llm_response['response'])
            if not analysis_json:
                return {
                    "success": False,
                    "error": "Failed to parse LLM response as JSON",
                    "raw_response": llm_response['response'],
                    "analysis_results": {},
                    "rewrite_suggestions": [],
                    "index_recommendations": []
                }

            # Extract recommendations
            rewrite_suggestions = analysis_json.get('rewrite_suggestions', [])
            index_recommendations = analysis_json.get('index_recommendations', [])

            # Get token usage
            tokens_used = llm_response.get('tokens_used') or 0
            model_used = llm_response.get('model') or model or AnthropicModel.SONNET_4_5.value
            actual_input = estimated_input_tokens
            actual_output = tokens_used - actual_input if tokens_used > actual_input else tokens_used // 2
            cost_usd = estimate_cost(actual_input, actual_output, model_used)

            return {
                "success": True,
                "analysis_results": analysis_json,
                "rewrite_suggestions": rewrite_suggestions,
                "index_recommendations": index_recommendations,
                "optimization_suggestions": analysis_json.get('optimization_opportunities', []),
                "llm_model": model_used,
                "tokens_used": tokens_used,
                "token_usage": {
                    "input": actual_input,
                    "output": actual_output,
                    "total": tokens_used if tokens_used else actual_input + actual_output,
                    "estimated_cost_usd": cost_usd,
                },
                "analysis_mode": "shallow"
            }

        except Exception as parse_error:
            return {
                "success": False,
                "error": f"Failed to parse LLM analysis: {str(parse_error)}",
                "raw_response": llm_response.get('response', ''),
                "analysis_results": {},
                "rewrite_suggestions": [],
                "index_recommendations": []
            }

    except Exception as e:
        logger.debug(f"Shallow LLM analysis failed with error: {e}", exc_info=True)
        return {
            "success": False,
            "error": f"Shallow LLM analysis failed: {str(e)}",
            "analysis_results": {},
            "rewrite_suggestions": [],
            "index_recommendations": []
        }


def _extract_json_from_response(response: str) -> Dict[str, Any]:
    """
    Extract JSON from LLM response, handling markdown code blocks.

    Args:
        response: Raw LLM response string

    Returns:
        Parsed JSON dict or None if parsing fails
    """
    if not response:
        return None

    # Try direct parse first
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    import re
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding JSON object in response
    try:
        start = response.find('{')
        end = response.rfind('}') + 1
        if start >= 0 and end > start:
            return json.loads(response[start:end])
    except json.JSONDecodeError:
        pass

    return None

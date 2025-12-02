"""
LLM Prompts for RDST Query Analysis

Contains structured prompts for different analysis tasks with template
placeholders for dynamic content substitution.
"""

EXPLAIN_ANALYSIS_PROMPT = """You are a database performance expert analyzing SQL query execution plans.

TASK: Analyze the following EXPLAIN ANALYZE output and provide insights about query performance.

DATABASE ENGINE: {database_engine}

QUERY (parameterized for analysis):
{parameterized_sql}

EXPLAIN PLAN DATA:
{explain_plan}

EXECUTION METRICS:
- Execution Time: {execution_time_ms} ms
- Rows Examined: {rows_examined}
- Rows Returned: {rows_returned}
- Cost Estimate: {cost_estimate}

ADDITIONAL METRICS (if available):
{additional_metrics}

Please provide your analysis in the following JSON format:

{{
  "performance_assessment": {{
    "overall_rating": "excellent|good|fair|poor|critical",
    "execution_time_rating": "fast|acceptable|slow|very_slow",
    "efficiency_score": 1-100,
    "primary_concerns": ["list", "of", "main", "performance", "issues"]
  }},
  "execution_analysis": {{
    "most_expensive_operations": [
      {{
        "operation": "operation_name",
        "cost_percentage": 0-100,
        "description": "why this operation is expensive",
        "impact": "high|medium|low"
      }}
    ],
    "scan_analysis": {{
      "sequential_scans": 0,
      "index_scans": 0,
      "index_effectiveness": "excellent|good|fair|poor"
    }},
    "join_analysis": {{
      "join_methods": ["hash|nested_loop|merge"],
      "join_efficiency": "optimal|suboptimal|poor",
      "large_cartesian_products": false
    }}
  }},
  "optimization_opportunities": [
    {{
      "type": "missing_index|query_rewrite|schema_change|configuration",
      "priority": "high|medium|low",
      "description": "specific optimization suggestion",
      "expected_improvement": "description of expected benefit"
    }}
  ],
  "explanation": "Human-readable explanation of the query performance and key findings"
}}

Focus on actionable insights and be specific about performance bottlenecks."""

HOTSPOT_IDENTIFICATION_PROMPT = """You are a database performance expert identifying query hotspots and performance bottlenecks.

TASK: Identify performance hotspots in the following query analysis data.

QUERY ANALYSIS RESULTS:
{analysis_results}

EXECUTION PLAN BREAKDOWN:
{execution_breakdown}

Please identify hotspots and provide optimization priorities in JSON format:

{{
  "hotspots": [
    {{
      "type": "table_scan|index_scan|join|sort|aggregation|subquery",
      "severity": "critical|high|medium|low",
      "location": "description of where in the query this occurs",
      "impact_description": "how this affects performance",
      "estimated_time_percentage": 0-100,
      "optimization_priority": 1-10
    }}
  ],
  "optimization_priority_order": [
    "ordered list of optimizations by expected impact"
  ],
  "quick_wins": [
    "list of low-effort, high-impact optimizations"
  ],
  "summary": "Brief summary of key hotspots and priorities"
}}"""

REWRITE_SUGGESTION_PROMPT = """You are an expert SQL optimization consultant. Your task is to suggest query rewrites that improve performance.

TASK: Analyze the following query and suggest optimized alternatives.

ORIGINAL QUERY (parameterized):
{parameterized_sql}

PERFORMANCE ANALYSIS:
{performance_analysis}

IDENTIFIED ISSUES:
{identified_issues}

DATABASE ENGINE: {database_engine}
ENGINE VERSION: {engine_version}
AVAILABLE SCHEMA INFO: {schema_info}

Please provide rewrite suggestions in the following JSON format:

{{
  "rewrite_suggestions": [
    {{
      "suggestion_id": "unique_identifier",
      "type": "join_reorder|subquery_to_join|index_hint|where_clause|select_optimization|cte_optimization",
      "priority": "high|medium|low",
      "confidence": "high|medium|low",
      "rewritten_sql": "the optimized SQL query",
      "explanation": "why this rewrite should improve performance",
      "expected_improvement": "estimated performance improvement",
      "trade_offs": "any potential downsides or considerations",
      "test_recommended": true
    }}
  ],
  "rewrite_strategy": "overall approach taken for optimization",
  "notes": "additional considerations or warnings",
  "requires_testing": true
}}

IMPORTANT GUIDELINES:
1. Only suggest syntactically correct SQL
2. Maintain query semantics - results must be identical
3. Consider database-specific optimization features
4. Prioritize suggestions by expected impact
5. Always recommend testing rewrites against actual data
6. If no beneficial rewrites are possible, return empty rewrite_suggestions array

CRITICAL - COMPLETE SQL ONLY:
7. ALWAYS provide COMPLETE, EXECUTABLE SQL statements in rewritten_sql
8. NEVER truncate or abbreviate SQL with "..." or similar placeholders
9. Include ALL clauses from the original query (SELECT, FROM, WHERE, JOIN, GROUP BY, ORDER BY, etc.)
10. If a rewrite is too long, provide FEWER alternatives but ensure each is COMPLETE

Be conservative and only suggest rewrites you're confident will help."""

INDEX_SUGGESTION_PROMPT = """You are a database indexing expert. Analyze query performance and suggest optimal indexing strategies.

TASK: Suggest database indexes to improve query performance.

QUERY (parameterized):
{parameterized_sql}

EXECUTION PLAN ANALYSIS:
{execution_analysis}

PERFORMANCE METRICS:
{performance_metrics}

DATABASE ENGINE: {database_engine}
TABLES INVOLVED: {tables_involved}
CURRENT INDEXES: {existing_indexes}

Please provide index recommendations in JSON format:

{{
  "index_recommendations": [
    {{
      "recommendation_id": "unique_identifier",
      "table": "table_name",
      "index_type": "btree|hash|gin|gist|covering|partial",
      "columns": ["ordered", "list", "of", "columns"],
      "include_columns": ["additional", "columns", "for", "covering"],
      "where_clause": "conditions for partial index (if applicable)",
      "estimated_benefit": "high|medium|low",
      "rationale": "why this index will help",
      "sql_statement": "CREATE INDEX statement",
      "maintenance_cost": "high|medium|low",
      "storage_impact": "estimated size impact"
    }}
  ],
  "indexing_strategy": "overall approach and priorities",
  "existing_index_analysis": {{
    "unused_indexes": ["list of indexes that appear unused"],
    "redundant_indexes": ["list of potentially redundant indexes"],
    "recommendations": "suggestions for existing index optimization"
  }},
  "warnings": [
    "important considerations about suggested indexes"
  ],
  "summary": "key recommendations and expected impact"
}}

GUIDELINES:
1. Consider query patterns, not just this single query
2. Balance query performance vs. write/storage overhead
3. Suggest composite indexes with optimal column ordering
4. Consider covering indexes for key queries
5. Flag potential index maintenance issues
6. Never suggest dropping existing indexes without analysis"""

READYSET_CACHING_PROMPT = """You are a ReadySet caching optimization expert. Analyze queries for ReadySet caching opportunities.

TASK: Evaluate whether this query is a good candidate for ReadySet caching and provide optimization recommendations.

QUERY (parameterized):
{parameterized_sql}

QUERY CHARACTERISTICS:
{query_characteristics}

PERFORMANCE METRICS:
{performance_metrics}

EXECUTION FREQUENCY: {execution_frequency}
READ/WRITE RATIO: {read_write_ratio}

Please provide ReadySet caching analysis in JSON format:

{{
  "caching_assessment": {{
    "cache_suitability": "excellent|good|fair|poor|not_suitable",
    "suitability_score": 1-100,
    "primary_benefits": ["list", "of", "expected", "benefits"],
    "concerns": ["list", "of", "potential", "issues"]
  }},
  "query_analysis": {{
    "deterministic": true,
    "has_non_deterministic_functions": false,
    "has_user_specific_data": false,
    "result_set_size": "small|medium|large",
    "query_complexity": "simple|moderate|complex",
    "join_patterns": ["description", "of", "joins"]
  }},
  "caching_recommendations": {{
    "recommend_caching": true,
    "cache_strategy": "full_result|partial_result|materialized_view",
    "expected_hit_ratio": 0-100,
    "expected_latency_improvement": "percentage or description",
    "cache_invalidation_frequency": "high|medium|low",
    "memory_requirements": "estimated memory usage"
  }},
  "optimization_suggestions": [
    {{
      "type": "query_modification|cache_configuration|readyset_specific",
      "suggestion": "specific optimization recommendation",
      "rationale": "why this helps with caching"
    }}
  ],
  "readyset_specific_notes": [
    "any ReadySet-specific considerations or limitations"
  ],
  "summary": "overall caching recommendation and key points"
}}

READYSET CACHING GUIDELINES:
1. ReadySet works best with frequently executed, relatively stable queries
2. Avoid caching queries with rapidly changing data
3. Consider query complexity vs. caching benefit
4. Account for cache warm-up time
5. Consider memory constraints
6. Evaluate cache invalidation patterns
7. Prefer queries with predictable access patterns"""

# Template validation and utility functions
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
        if f'{{{field}}}' not in template:
            return False
    return True


# Required fields for each prompt template
PROMPT_REQUIRED_FIELDS = {
    'EXPLAIN_ANALYSIS_PROMPT': [
        'database_engine', 'parameterized_sql', 'explain_plan',
        'execution_time_ms', 'rows_examined', 'rows_returned',
        'cost_estimate', 'additional_metrics'
    ],
    'HOTSPOT_IDENTIFICATION_PROMPT': [
        'analysis_results', 'execution_breakdown'
    ],
    'REWRITE_SUGGESTION_PROMPT': [
        'parameterized_sql', 'performance_analysis', 'identified_issues',
        'database_engine', 'engine_version', 'schema_info'
    ],
    'INDEX_SUGGESTION_PROMPT': [
        'parameterized_sql', 'execution_analysis', 'performance_metrics',
        'database_engine', 'tables_involved', 'existing_indexes'
    ],
    'READYSET_CACHING_PROMPT': [
        'parameterized_sql', 'query_characteristics', 'performance_metrics',
        'execution_frequency', 'read_write_ratio'
    ]
}
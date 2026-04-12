"""Shared query registry package."""

from .analysis_results import AnalysisResult, AnalysisResultsRegistry, create_analysis_result
from .conversation_registry import ConversationRegistry, InteractiveConversation
from .query_registry import (
    QueryEntry,
    QueryRegistry,
    extract_parameters_from_sql,
    generate_query_name,
    hash_sql,
    hash_sql_deep,
    normalize_sql,
    normalize_sql_deep,
    reconstruct_query_with_params,
)
from .sql_normalizer import (
    get_placeholder_names,
    normalize_and_extract,
    reconstruct_sql,
)

__all__ = [
    "AnalysisResult",
    "AnalysisResultsRegistry",
    "ConversationRegistry",
    "InteractiveConversation",
    "QueryEntry",
    "QueryRegistry",
    "create_analysis_result",
    "extract_parameters_from_sql",
    "generate_query_name",
    "get_placeholder_names",
    "hash_sql",
    "hash_sql_deep",
    "normalize_and_extract",
    "normalize_sql",
    "normalize_sql_deep",
    "reconstruct_query_with_params",
    "reconstruct_sql",
]

"""Analyze feature models."""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class AnalyzeInput:
    """Input for analyze service after resolution."""

    sql: str
    normalized_sql: str
    source: str
    hash: Optional[str] = None
    tag: Optional[str] = None
    save_as: Optional[str] = None


@dataclass
class AnalyzeOptions:
    """Options for analyze service execution."""

    target: Optional[str] = None
    fast: bool = False
    readyset_cache: bool = False
    test_rewrites: bool = False
    model: Optional[str] = None


@dataclass
class AnalyzeResult:
    """Final result from analyze service."""

    success: bool
    analysis_id: Optional[str] = None
    query_hash: Optional[str] = None
    explain_results: Optional[Dict[str, Any]] = None
    llm_analysis: Optional[Dict[str, Any]] = None
    rewrite_testing: Optional[Dict[str, Any]] = None
    readyset_cacheability: Optional[Dict[str, Any]] = None
    formatted: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

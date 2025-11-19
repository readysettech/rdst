"""
Prompts Library for RDST LLM Analysis

This module contains structured prompts for various LLM analysis tasks
in the RDST analyze workflow. Prompts are organized by function and
support template substitution for dynamic content.
"""

from .analyze_prompts import (
    EXPLAIN_ANALYSIS_PROMPT,
    HOTSPOT_IDENTIFICATION_PROMPT,
    REWRITE_SUGGESTION_PROMPT,
    INDEX_SUGGESTION_PROMPT,
    READYSET_CACHING_PROMPT
)

# Export all prompts for easy access
__all__ = [
    'EXPLAIN_ANALYSIS_PROMPT',
    'HOTSPOT_IDENTIFICATION_PROMPT',
    'REWRITE_SUGGESTION_PROMPT',
    'INDEX_SUGGESTION_PROMPT',
    'READYSET_CACHING_PROMPT'
]
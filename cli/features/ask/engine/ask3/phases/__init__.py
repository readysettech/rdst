"""
Ask3 Engine Phases

Each phase is a pure function that takes context and returns updated context.
This makes the flow easy to understand, test, and debug.
"""

from .clarify import clarify_question
from .execute import execute_query
from .generate import generate_sql
from .present import present_results
from .schema import load_schema
from .validate import validate_sql

__all__ = [
    "load_schema",
    "clarify_question",
    "generate_sql",
    "validate_sql",
    "execute_query",
    "present_results",
]

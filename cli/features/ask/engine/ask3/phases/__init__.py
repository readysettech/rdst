"""
Ask3 Engine Phases

Each phase is a pure function that takes context and returns updated context.
This makes the flow easy to understand, test, and debug.
"""

from .schema import load_schema
from .filter import filter_schema
from .clarify import clarify_question
from .generate import generate_sql
from .validate import validate_sql
from .execute import execute_query
from .present import present_results
from .expand import expand_schema

__all__ = [
    'load_schema',
    'filter_schema',
    'clarify_question',
    'generate_sql',
    'validate_sql',
    'execute_query',
    'present_results',
    'expand_schema',
]

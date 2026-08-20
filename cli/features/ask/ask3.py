"""Feature entrypoints for the Ask3 engine."""

from __future__ import annotations

from features.ask.engine.ask3 import Ask3Context, Status
from features.ask.engine.ask3.phases import (
    execute_query as _execute_query,
)
from features.ask.engine.ask3.phases import (
    generate_sql as _generate_sql,
)
from features.ask.engine.ask3.phases import (
    load_schema as _load_schema,
)
from features.ask.engine.ask3.phases import (
    validate_sql as _validate_sql,
)
from features.ask.engine.ask3.types import Interpretation


def create_context(**kwargs):
    """Create an Ask3 context."""
    return Ask3Context(**kwargs)


def get_status_enum():
    """Return the status enum."""
    return Status


def load_schema(ctx, presenter, semantic_manager=None, semantic_schema_formatter=None):
    return _load_schema(ctx, presenter, semantic_manager, semantic_schema_formatter)


def generate_sql(ctx, presenter, llm_manager=None):
    return _generate_sql(ctx, presenter, llm_manager)


def validate_sql(ctx, presenter):
    return _validate_sql(ctx, presenter)


def execute_query(ctx, presenter, db_executor=None):
    return _execute_query(ctx, presenter, db_executor)


def create_interpretation(**kwargs):
    return Interpretation(**kwargs)

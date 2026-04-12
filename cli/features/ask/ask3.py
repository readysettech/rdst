"""Feature entrypoints for the Ask3 engine."""

from __future__ import annotations

from features.ask.engine.ask3 import Ask3Context, Status
from features.ask.engine.ask3.phases import (
    execute_query as _execute_query,
    filter_schema as _filter_schema,
    generate_sql as _generate_sql,
    load_schema as _load_schema,
    validate_sql as _validate_sql,
)
from features.ask.engine.ask3.types import Interpretation


def create_context(**kwargs):
    """Create an Ask3 context."""
    return Ask3Context(**kwargs)


def get_status_enum():
    """Return the status enum."""
    return Status


def load_schema(ctx, presenter, semantic_manager=None):
    return _load_schema(ctx, presenter, semantic_manager)


def filter_schema(ctx, presenter, semantic_manager=None):
    return _filter_schema(ctx, presenter, semantic_manager)


def generate_sql(ctx, presenter, semantic_manager=None):
    return _generate_sql(ctx, presenter, semantic_manager)


def validate_sql(ctx, presenter):
    return _validate_sql(ctx, presenter)


def execute_query(ctx, presenter, semantic_manager=None):
    return _execute_query(ctx, presenter, semantic_manager)


def create_interpretation(**kwargs):
    return Interpretation(**kwargs)

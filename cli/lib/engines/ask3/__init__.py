"""
Ask3 Engine - Linear Flow Natural Language to SQL

A simplified replacement for the state machine-based ask3_engine.py.
Uses a 6-phase linear flow instead of 15 states.

Usage:
    from lib.engines.ask3 import Ask3Engine, Ask3Context, Ask3Presenter

    # Simple usage
    engine = Ask3Engine()
    result = engine.run(
        question="Find users named John",
        target="mydb",
        target_config={
            'host': 'localhost',
            'port': 5432,
            'user': 'postgres',
            'password': 'secret',
            'database': 'mydb'
        }
    )

    if result.status == 'success':
        print(f"Found {result.execution_result.row_count} rows")
    else:
        print(f"Error: {result.error_message}")

    # With custom presenter
    presenter = Ask3Presenter(verbose=True, use_rich=True)
    engine = Ask3Engine(presenter=presenter)

Flow:
    1. SCHEMA  - Load schema from semantic layer or database
    2. CLARIFY - Detect ambiguities, collect user clarifications
    3. GENERATE - Generate SQL using LLM
    4. VALIDATE - Validate SQL (read-only, column existence, LIMIT)
    5. EXECUTE  - Run query against database
    6. PRESENT  - Display results

Key differences from ask3_engine.py:
    - Single Ask3Context instead of dual state (EngineState + Ask3Session)
    - Pure functions for each phase (easy to test)
    - All output via Ask3Presenter (separated concerns)
    - Typed dataclasses instead of untyped extra_data dict
    - Clear retry logic in one place
"""

from .engine import Ask3Engine, create_engine
from .context import Ask3Context
from .presenter import Ask3Presenter, QuietPresenter
from .types import (
    Interpretation,
    ValidationError,
    ExecutionResult,
    SchemaInfo,
    TableInfo,
    ColumnInfo,
    Status,
    DbType,
    SchemaSource,
)

__all__ = [
    # Main engine
    'Ask3Engine',
    'create_engine',

    # Context
    'Ask3Context',

    # Presenters
    'Ask3Presenter',
    'QuietPresenter',

    # Types
    'Interpretation',
    'ValidationError',
    'ExecutionResult',
    'SchemaInfo',
    'TableInfo',
    'ColumnInfo',

    # Constants
    'Status',
    'DbType',
    'SchemaSource',
]

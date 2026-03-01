"""
Unit tests for SQL generation phase.

Bug rdst-9cq.2: When the LLM returns very low confidence (schema can't answer
the question), generate_sql() should mark the context as error instead of
continuing with fabricated SQL.
"""

import pytest
from unittest.mock import patch, MagicMock

from lib.engines.ask3.context import Ask3Context
from lib.engines.ask3.types import Status


class TestGenerateSqlConfidenceGate:
    """Tests that generate_sql gates on low confidence."""

    def _make_ctx(self) -> Ask3Context:
        """Create a minimal context for generation."""
        ctx = Ask3Context(
            question="What is the box office revenue of Inception?",
            target="testdb",
            db_type="postgresql",
            schema_formatted="CREATE TABLE title_basics (tconst TEXT, primarytitle TEXT, startyear INT);",
        )
        return ctx

    def _mock_llm_response(self, confidence: float, sql: str, assumptions: list) -> dict:
        """Create a mock LLM generation response."""
        return {
            'success': True,
            'sql': sql,
            'explanation': 'Query searches title_basics',
            'confidence': confidence,
            'needs_clarification': confidence < 0.5,
            'clarifications': [],
            'ambiguities': [],
            'assumptions': assumptions,
            'warnings': [],
            'tables_used': ['title_basics'],
            'columns_used': ['primarytitle'],
            'alternatives': [],
            'error': None,
            'raw_response': {
                'analysis': {
                    'needs_clarification': confidence < 0.5,
                    'ambiguities': assumptions,
                },
                'sql_generation': {
                    'sql': sql,
                    'confidence': confidence,
                    'assumptions': assumptions,
                },
            },
        }

    def test_very_low_confidence_marks_error(self):
        """Bug rdst-9cq.2: confidence=0.0 should mark context as error.

        When the LLM returns confidence=0.0 (schema can't answer the question),
        generate_sql() should NOT continue with fabricated SQL. It should mark
        the context as error so the pipeline stops.
        """
        from lib.engines.ask3.phases.generate import generate_sql

        ctx = self._make_ctx()
        mock_presenter = MagicMock()

        mock_result = self._mock_llm_response(
            confidence=0.0,
            sql="SELECT primarytitle FROM title_basics WHERE primarytitle = 'Inception'",
            assumptions=["Schema doesn't contain box office revenue data"],
        )

        with patch(
            "lib.functions.sql_generation.generate_sql_from_nl",
            return_value=mock_result,
        ):
            result_ctx = generate_sql(ctx, mock_presenter, llm_manager=MagicMock())

        # With confidence=0.0, the context should be marked as error
        assert result_ctx.status == Status.ERROR, (
            f"Expected Status.ERROR for confidence=0.0, got {result_ctx.status}. "
            "The pipeline continued with fabricated SQL instead of refusing."
        )
        assert result_ctx.error_message is not None

    def test_moderate_confidence_continues(self):
        """Moderate confidence (0.7+) should proceed normally."""
        from lib.engines.ask3.phases.generate import generate_sql

        ctx = self._make_ctx()
        mock_presenter = MagicMock()

        mock_result = self._mock_llm_response(
            confidence=0.8,
            sql="SELECT primarytitle FROM title_basics WHERE primarytitle ILIKE '%inception%'",
            assumptions=["Searching by title"],
        )

        with patch(
            "lib.functions.sql_generation.generate_sql_from_nl",
            return_value=mock_result,
        ):
            result_ctx = generate_sql(ctx, mock_presenter, llm_manager=MagicMock())

        # Moderate confidence should proceed
        assert result_ctx.status != Status.ERROR
        assert result_ctx.sql != ''

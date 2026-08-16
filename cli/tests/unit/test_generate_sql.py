"""Unit tests for the explicit SQL-generation answerability contract."""

import json
from unittest.mock import MagicMock, patch

from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import Status


class TestGenerateSqlAnswerabilityGate:
    """Confidence is diagnostic; explicit cannot_answer controls refusal."""

    def _make_ctx(self) -> Ask3Context:
        """Create a minimal context for generation."""
        ctx = Ask3Context(
            question="What is the box office revenue of Inception?",
            target="testdb",
            db_type="postgresql",
            schema_formatted="CREATE TABLE title_basics (tconst TEXT, primarytitle TEXT, startyear INT);",
        )
        return ctx

    def _mock_llm_response(
        self,
        confidence: float,
        sql: str,
        assumptions: list,
        *,
        cannot_answer: bool = False,
    ) -> dict:
        """Create a mock LLM generation response."""
        return {
            "success": True,
            "sql": sql,
            "explanation": "Query searches title_basics",
            "confidence": confidence,
            "assumptions": assumptions,
            "cannot_answer": cannot_answer,
            "cannot_answer_reason": "missing_schema" if cannot_answer else "",
            "missing_schema": ["box office revenue"] if cannot_answer else [],
            "error": None,
            "raw_response": {
                "sql": sql,
                "explanation": "Query searches title_basics",
                "confidence": confidence,
                "assumptions": assumptions,
                "cannot_answer": cannot_answer,
                "cannot_answer_reason": "missing_schema" if cannot_answer else "",
                "missing_schema": ["box office revenue"] if cannot_answer else [],
            },
        }

    def test_explicit_cannot_answer_marks_error(self):
        from features.ask.engine.ask3.phases.generate import generate_sql

        ctx = self._make_ctx()
        mock_presenter = MagicMock()

        mock_result = self._mock_llm_response(
            confidence=0.0,
            sql="",
            assumptions=["Schema doesn't contain box office revenue data"],
            cannot_answer=True,
        )

        with patch(
            "features.ask.sql_generation.generate_sql_from_nl",
            return_value=mock_result,
        ):
            result_ctx = generate_sql(ctx, mock_presenter, llm_manager=MagicMock())

        assert result_ctx.status == Status.ERROR, (
            f"Expected Status.ERROR for cannot_answer, got {result_ctx.status}."
        )
        assert result_ctx.error_message is not None

    def test_low_confidence_alone_is_diagnostic(self):
        from features.ask.engine.ask3.phases.generate import generate_sql

        ctx = self._make_ctx()
        mock_result = self._mock_llm_response(
            confidence=0.0,
            sql="SELECT primarytitle FROM title_basics",
            assumptions=[],
        )
        with patch(
            "features.ask.sql_generation.generate_sql_from_nl",
            return_value=mock_result,
        ):
            result_ctx = generate_sql(ctx, MagicMock(), llm_manager=MagicMock())

        assert result_ctx.status != Status.ERROR
        assert result_ctx.generation_confidence == 0.0

    def test_moderate_confidence_continues(self):
        """Moderate confidence (0.7+) should proceed normally."""
        from features.ask.engine.ask3.phases.generate import generate_sql

        ctx = self._make_ctx()
        mock_presenter = MagicMock()

        mock_result = self._mock_llm_response(
            confidence=0.8,
            sql="SELECT primarytitle FROM title_basics WHERE primarytitle ILIKE '%inception%'",
            assumptions=["Searching by title"],
        )

        with patch(
            "features.ask.sql_generation.generate_sql_from_nl",
            return_value=mock_result,
        ):
            result_ctx = generate_sql(ctx, mock_presenter, llm_manager=MagicMock())

        # Moderate confidence should proceed
        assert result_ctx.status != Status.ERROR
        assert result_ctx.sql != ""


def test_generation_purpose_is_forwarded_to_receipts():
    from features.ask.sql_generation import generate_sql_from_nl

    class Manager:
        def generate_response(self, **kwargs):
            assert kwargs["purpose"] == "ablation_full_structured"
            assert kwargs["system_message"].startswith(
                "You are an expert text-to-SQL system."
            )
            return {
                "response": json.dumps(
                    {
                        "sql": "SELECT 1",
                        "explanation": "Returns one.",
                        "confidence": 1.0,
                        "assumptions": [],
                        "cannot_answer": False,
                        "cannot_answer_reason": "",
                        "missing_schema": [],
                    }
                ),
                "tokens_used": 10,
                "model": "fixture",
            }

    result = generate_sql_from_nl(
        nl_question="Return one",
        filtered_schema="Table: fixture",
        database_engine="mysql",
        target_database="fixture",
        llm_manager=Manager(),
        purpose="ablation_full_structured",
    )

    assert result["success"] is True


def test_generation_receives_authoritative_context_separately() -> None:
    from features.ask.sql_generation import generate_sql_from_nl

    class Manager:
        def generate_response(self, **kwargs):
            prompt = kwargs["prompt"]
            assert "USER QUESTION:\nReturn active users" in prompt
            assert "AUTHORITATIVE CALLER-PROVIDED CONTEXT:" in prompt
            assert "Active means enabled = true." in prompt
            return {
                "response": json.dumps(
                    {
                        "sql": "SELECT * FROM users WHERE enabled = true",
                        "explanation": "Returns enabled users.",
                        "confidence": 1.0,
                        "assumptions": [],
                        "cannot_answer": False,
                        "cannot_answer_reason": "",
                        "missing_schema": [],
                    }
                ),
                "tokens_used": 10,
                "model": "fixture",
            }

    result = generate_sql_from_nl(
        nl_question="Return active users",
        filtered_schema="Table: users(enabled BOOLEAN)",
        database_engine="postgresql",
        target_database="fixture",
        llm_manager=Manager(),
        provided_context="Active means enabled = true.",
    )

    assert result["success"] is True


def test_context_round_trip_preserves_provided_context() -> None:
    context = Ask3Context(
        question="Return active users",
        target="fixture",
        provided_context="Active means enabled = true.",
    )

    restored = Ask3Context.from_dict(context.to_dict())

    assert restored.provided_context == context.provided_context


def test_generation_system_message_can_be_overridden_for_ablation():
    from features.ask.sql_generation import generate_sql_from_nl

    class Manager:
        def generate_response(self, **kwargs):
            assert kwargs["system_message"] == "legacy generic system"
            return {
                "response": json.dumps(
                    {
                        "sql": "SELECT 1",
                        "explanation": "Returns one.",
                        "confidence": 1.0,
                        "assumptions": [],
                        "cannot_answer": False,
                        "cannot_answer_reason": "",
                        "missing_schema": [],
                    }
                ),
                "tokens_used": 10,
                "model": "fixture",
            }

    result = generate_sql_from_nl(
        nl_question="Return one",
        filtered_schema="Table: fixture",
        database_engine="mysql",
        target_database="fixture",
        llm_manager=Manager(),
        system_message="legacy generic system",
    )

    assert result["success"] is True

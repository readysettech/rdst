"""Unit tests for the explicit SQL-generation answerability contract."""

import json
from unittest.mock import MagicMock, patch

from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import Status
from shared.llm_manager.base import LLMError


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
            assert kwargs["max_tokens"] == 4000
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


def _successful_generation_response() -> dict:
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


def test_generation_retries_lossless_compact_schema_after_context_rejection() -> None:
    from features.ask.sql_generation import generate_sql_from_nl

    compact_schema = "COMPACT_SCHEMA_MARKER"

    class Manager:
        def __init__(self):
            self.prompts = []

        def generate_response(self, **kwargs):
            self.prompts.append(kwargs["prompt"])
            if len(self.prompts) == 1:
                raise LLMError(
                    "prompt is too long",
                    code="ANTHROPIC_CONTEXT_WINDOW_EXCEEDED",
                    status=400,
                    request_id="req_verbose",
                )
            return _successful_generation_response()

    manager = Manager()
    result = generate_sql_from_nl(
        nl_question="Return one",
        filtered_schema="VERBOSE_SCHEMA_MARKER" * 10_000,
        compact_fallback_schema=compact_schema,
        compact_fallback_format="rdst-compact-schema-v2",
        database_engine="mysql",
        target_database="fixture",
        llm_manager=manager,
    )

    assert result["success"] is True
    assert result["schema_context_fallback_used"] is True
    assert result["schema_context_fallback_reason"] == "context_window"
    assert result["schema_format"] == "rdst-compact-schema-v2"
    assert result["prompt_utf8_bytes"] == len(manager.prompts[1].encode("utf-8"))
    assert len(manager.prompts) == 2
    assert "VERBOSE_SCHEMA_MARKER" in manager.prompts[0]
    assert compact_schema in manager.prompts[1]
    assert "VERBOSE_SCHEMA_MARKER" not in manager.prompts[1]


def test_generation_reports_when_both_lossless_formats_exceed_context() -> None:
    from features.ask.sql_generation import generate_sql_from_nl

    compact_schema = "COMPACT_SCHEMA_MARKER" * 10_000

    class Manager:
        def __init__(self):
            self.calls = 0

        def generate_response(self, **_kwargs):
            self.calls += 1
            raise LLMError(
                "prompt is too long",
                code="ANTHROPIC_CONTEXT_WINDOW_EXCEEDED",
                status=400,
                request_id=f"req_{self.calls}",
            )

    manager = Manager()

    result = generate_sql_from_nl(
        nl_question="Return one",
        filtered_schema="VERBOSE_SCHEMA_MARKER" * 20_000,
        compact_fallback_schema=compact_schema,
        compact_fallback_format="rdst-compact-schema-v2",
        database_engine="mysql",
        target_database="fixture",
        llm_manager=manager,
    )

    assert result["success"] is False
    assert manager.calls == 2
    assert result["schema_request_failure_code"] == (
        "ANTHROPIC_CONTEXT_WINDOW_EXCEEDED"
    )
    assert result["schema_request_failure_request_id"] == "req_2"
    assert result["schema_context_fallback_reason"] == "context_window"
    assert "tried both complete lossless schema formats" in result["error"]
    assert "Request ID: req_2" in result["error"]
    assert "did not truncate" in result["error"]


def test_generation_labels_an_initial_compact_schema_correctly() -> None:
    from features.ask.sql_generation import generate_sql_from_nl

    class Manager:
        def generate_response(self, **_kwargs):
            raise LLMError(
                "prompt is too long",
                code="ANTHROPIC_CONTEXT_WINDOW_EXCEEDED",
                status=400,
            )

    result = generate_sql_from_nl(
        nl_question="Return one",
        filtered_schema="COMPACT_SCHEMA_MARKER",
        schema_format="rdst-compact-schema-v2",
        database_engine="mysql",
        target_database="fixture",
        llm_manager=Manager(),
    )

    assert result["success"] is False
    assert "The compact schema is" in result["error"]
    assert "The verbose schema is" not in result["error"]


def test_generation_retries_compact_schema_after_request_body_rejection() -> None:
    from features.ask.sql_generation import generate_sql_from_nl

    class Manager:
        def __init__(self):
            self.prompts = []

        def generate_response(self, **kwargs):
            self.prompts.append(kwargs["prompt"])
            if len(self.prompts) == 1:
                raise LLMError(
                    "request body exceeds 32 MB",
                    code="ANTHROPIC_REQUEST_TOO_LARGE",
                    status=413,
                )
            return _successful_generation_response()

    manager = Manager()
    result = generate_sql_from_nl(
        nl_question="Return one",
        filtered_schema="VERBOSE_SCHEMA_MARKER" * 10_000,
        compact_fallback_schema="COMPACT_SCHEMA_MARKER",
        compact_fallback_format="rdst-compact-schema-v2",
        database_engine="mysql",
        target_database="fixture",
        llm_manager=manager,
    )

    assert result["success"] is True
    assert result["schema_context_fallback_used"] is True
    assert result["schema_context_fallback_reason"] == "request_body_limit"
    assert len(manager.prompts) == 2


def test_generation_does_not_retry_unrelated_invalid_request() -> None:
    from features.ask.sql_generation import generate_sql_from_nl

    class Manager:
        def __init__(self):
            self.calls = 0

        def generate_response(self, **_kwargs):
            self.calls += 1
            raise LLMError(
                "invalid tool schema",
                code="ANTHROPIC_INVALID_REQUEST",
                status=400,
            )

    manager = Manager()
    result = generate_sql_from_nl(
        nl_question="Return one",
        filtered_schema="verbose schema",
        compact_fallback_schema="compact schema",
        compact_fallback_format="rdst-compact-schema-v2",
        database_engine="mysql",
        target_database="fixture",
        llm_manager=manager,
    )

    assert result["success"] is False
    assert result["error"] == "invalid tool schema"
    assert manager.calls == 1


def test_generation_phase_surfaces_provider_context_error() -> None:
    from features.ask.engine.ask3.phases.generate import generate_sql

    class Manager:
        def __init__(self):
            self.calls = 0

        def generate_response(self, **_kwargs):
            self.calls += 1
            raise LLMError(
                "prompt is too long",
                code="ANTHROPIC_CONTEXT_WINDOW_EXCEEDED",
                status=400,
            )

    ctx = Ask3Context(
        question="Return one",
        target="fixture",
        db_type="mysql",
        schema_formatted="verbose schema",
        schema_compact_fallback="compact schema",
    )
    presenter = MagicMock()

    result = generate_sql(ctx, presenter, llm_manager=Manager())

    assert result.status == Status.ERROR
    assert "tried both complete lossless schema formats" in result.error_message
    assert result.schema_context_fallback_used is True
    assert result.schema_context_fallback_reason == "context_window"
    assert result.schema_prompt_utf8_bytes > 0
    assert result.error_code == "ANTHROPIC_CONTEXT_WINDOW_EXCEEDED"
    assert result.error_category == "model-limit"
    presenter.error.assert_called_once_with(result.error_message)


def test_context_round_trip_preserves_provided_context() -> None:
    context = Ask3Context(
        question="Return active users",
        target="fixture",
        provided_context="Active means enabled = true.",
        schema_format="rdst-compact-schema-v2",
        schema_format_policy="rdst-adaptive-schema-v2",
        schema_verbose_chars=1000,
        schema_compact_chars=700,
        schema_compact_savings_ratio=0.3,
        schema_context_fallback_used=True,
        schema_context_fallback_reason="context_window",
        schema_prompt_utf8_bytes=50_000,
        error_code="ANTHROPIC_CONTEXT_WINDOW_EXCEEDED",
        error_category="model-limit",
    )

    restored = Ask3Context.from_dict(context.to_dict())

    assert restored.provided_context == context.provided_context
    assert restored.schema_format == "rdst-compact-schema-v2"
    assert restored.schema_format_policy == "rdst-adaptive-schema-v2"
    assert restored.schema_verbose_chars == 1000
    assert restored.schema_compact_chars == 700
    assert restored.schema_compact_savings_ratio == 0.3
    assert restored.schema_context_fallback_used is True
    assert restored.schema_context_fallback_reason == "context_window"
    assert restored.schema_prompt_utf8_bytes == 50_000
    assert restored.error_code == "ANTHROPIC_CONTEXT_WINDOW_EXCEEDED"
    assert restored.error_category == "model-limit"


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

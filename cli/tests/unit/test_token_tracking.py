"""
Unit tests for token tracking in SQL generation (rdst-2vr.18).

The callback must receive actual token counts from LLM responses,
not 0 due to a key name mismatch.
"""

from unittest.mock import MagicMock


class TestSqlGenerationTokenTracking:
    """generate_sql_from_nl must pass correct token count to callback."""

    def test_callback_receives_nonzero_tokens(self):
        """Callback tokens arg must match what LLM returns, not default to 0."""
        from lib.functions.sql_generation import generate_sql_from_nl

        # Mock LLM manager that returns a valid response with token count
        mock_llm = MagicMock()
        mock_llm.generate_response.return_value = {
            "response": '{"sql": "SELECT 1", "explanation": "test", "confidence": 0.9}',
            "tokens_used": 1234,
            "model": "test-model",
        }

        captured = {}

        def capture_callback(**kwargs):
            captured.update(kwargs)

        generate_sql_from_nl(
            nl_question="test question",
            filtered_schema="table: test (id INT)",
            database_engine="postgresql",
            target_database="testdb",
            llm_manager=mock_llm,
            callback=capture_callback,
        )

        assert "tokens" in captured, "Callback was not invoked"
        assert captured["tokens"] == 1234, (
            f"Callback received tokens={captured['tokens']}, expected 1234. "
            f"Key mismatch: generate_response returns 'tokens_used' but "
            f"sql_generation.py reads 'total_tokens'"
        )

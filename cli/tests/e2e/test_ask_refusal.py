"""E2E tests for rdst ask — refusal of unanswerable questions and success paths.

These tests exercise the full pipeline: CLI → service → LLM → confidence gate → renderer.
They require a live database with IMDb data and a valid ANTHROPIC_API_KEY.

Run with:
    cd src && .venv/bin/python3 -m pytest tests/e2e/ -v
"""

import re

import pytest

# LLM calls can be slow; 90s is a generous upper bound (typical: 5-15s).
_LLM_TIMEOUT = 90


@pytest.mark.e2e
class TestAskRefusal:
    """Test that the ask command refuses unanswerable questions."""

    def test_refuse_unanswerable_question(self, tmux, e2e_target):
        """IMDb has no revenue data — LLM should return low confidence and refuse."""
        output = tmux.run_rdst(
            f'ask --target {e2e_target} --no-interactive '
            f'"What is the total box office revenue for Marvel movies?"',
            timeout=_LLM_TIMEOUT,
        )

        # Should show the refusal error with low confidence.
        assert "Cannot answer this question" in output, (
            f"Expected refusal message in output, got:\n{output[-500:]}"
        )
        assert re.search(r"confidence: 0\.\d", output), (
            f"Expected low confidence score in output, got:\n{output[-500:]}"
        )

        # Should NOT show query results.
        assert not re.search(r"Results \(\d+ rows", output), (
            f"Refused question should not produce result rows:\n{output[-500:]}"
        )


@pytest.mark.e2e
class TestAskSuccess:
    """Test that the ask command succeeds for answerable questions."""

    def test_succeed_answerable_question(self, tmux, e2e_target):
        """title_basics exists with rows — simple count should succeed."""
        output = tmux.run_rdst(
            f'ask --target {e2e_target} --no-interactive '
            f'"How many titles are in the database?"',
            timeout=_LLM_TIMEOUT,
        )

        assert "Generated SQL" in output, (
            f"Expected 'Generated SQL' section in output, got:\n{output[-500:]}"
        )
        assert re.search(r"Results \(\d+ rows", output) or "No results returned" in output, (
            f"Expected results or 'No results returned' in output, got:\n{output[-500:]}"
        )

    def test_succeed_filtered_question(self, tmux, e2e_target):
        """startyear column exists — filtered count should succeed."""
        output = tmux.run_rdst(
            f'ask --target {e2e_target} --no-interactive '
            f'"How many movies were released in 2020?"',
            timeout=_LLM_TIMEOUT,
        )

        assert "Generated SQL" in output, (
            f"Expected 'Generated SQL' section in output, got:\n{output[-500:]}"
        )
        assert re.search(r"Results", output) or "No results returned" in output, (
            f"Expected results in output, got:\n{output[-500:]}"
        )

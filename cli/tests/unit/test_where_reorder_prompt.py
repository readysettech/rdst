"""
Unit test for WHERE clause reorder prohibition in analyze prompt (rdst-2vr.27).

PostgreSQL's optimizer evaluates all WHERE conditions and chooses the
optimal plan regardless of condition order in the SQL text. Suggesting
WHERE clause reordering is a no-op that wastes user time and undermines
tool credibility.
"""

import re


class TestWhereReorderNotAllowed:
    """The LLM prompt must NOT allow WHERE clause reordering as an optimization."""

    def _get_prompt_source(self) -> str:
        import inspect
        from lib.functions import llm_analysis

        return inspect.getsource(llm_analysis)

    def test_where_reorder_not_in_allowed_section(self):
        """WHERE clause reordering must not be listed as an ALLOWED rewrite.

        PostgreSQL's query optimizer reorders conditions automatically.
        Suggesting this as an optimization is misleading.
        """
        source = self._get_prompt_source()

        allowed_section = re.search(
            r"ALLOWED in rewrites:.*?(?:CORRELATED|If you want)",
            source,
            re.DOTALL,
        )
        assert allowed_section, "Could not find ALLOWED section in prompt"

        allowed_text = allowed_section.group(0)

        has_where_reorder = bool(
            re.search(r"(?i)reorder.*WHERE|WHERE.*reorder", allowed_text)
        )

        assert not has_where_reorder, (
            "Prompt lists 'Reordering WHERE conditions' as ALLOWED in rewrites. "
            "PostgreSQL's optimizer handles condition ordering automatically — "
            "this is a no-op that wastes user time."
        )

    def test_prompt_warns_against_where_reordering(self):
        """Prompt should explicitly note that WHERE reordering is not a real optimization."""
        source = self._get_prompt_source()

        # Should mention that condition order doesn't matter / optimizer handles it
        has_warning = bool(
            re.search(
                r"(?i)(WHERE.*order.*optimizer|optimizer.*WHERE.*order|"
                r"condition.*order.*no.?op|WHERE.*reorder.*forbidden|"
                r"WHERE.*reorder.*not.*optim)",
                source,
            )
        )

        assert has_warning, (
            "Prompt should explicitly warn that WHERE clause reordering is not "
            "a real optimization (the optimizer handles condition ordering)."
        )

"""
Unit test for correlated subquery → JOIN+GROUP BY rewrite guidance (rdst-2vr.25).

The analyze prompt must NOT blanket-forbid adding GROUP BY, because
converting a correlated subquery to JOIN inherently requires GROUP BY
to maintain semantic equivalence.
"""

import re


class TestCorrelatedSubqueryRewriteGuidance:
    """The LLM prompt must explicitly allow GROUP BY when rewriting correlated subqueries."""

    def _get_analyze_prompt(self) -> str:
        """Extract the ANALYZE_PROMPT string from llm_analysis.py source."""
        import inspect
        from lib.functions import llm_analysis

        source = inspect.getsource(llm_analysis)
        return source

    def test_prompt_does_not_blanket_forbid_group_by(self):
        """GROUP BY must not be unconditionally forbidden in rewrites.

        The prompt previously listed 'Adding/removing GROUP BY' as explicitly
        forbidden. This blocks the most important rewrite: correlated subquery
        to JOIN+GROUP BY. The prompt must either remove this blanket ban or
        add a clear exception for subquery→JOIN conversions.
        """
        source = self._get_analyze_prompt()

        # Check for blanket GROUP BY prohibition without exception
        # The old text: "Adding/removing GROUP BY, HAVING"
        # It should NOT appear as a simple forbidden item
        forbidden_section = re.search(
            r"EXPLICITLY FORBIDDEN.*?ALLOWED in rewrites",
            source,
            re.DOTALL,
        )
        assert forbidden_section, "Could not find EXPLICITLY FORBIDDEN section in prompt"

        forbidden_text = forbidden_section.group(0)

        # The blanket "Adding/removing GROUP BY" should not be in the forbidden list
        # OR if it is, there must be an exception for subquery→JOIN conversions
        has_blanket_ban = bool(
            re.search(r"Adding/removing GROUP BY", forbidden_text)
        )
        has_exception = bool(
            re.search(r"(?i)subquery.*JOIN.*GROUP BY|correlated.*GROUP BY|GROUP BY.*subquery.*JOIN", source)
        )

        assert not has_blanket_ban or has_exception, (
            "Prompt blanket-forbids 'Adding/removing GROUP BY' in rewrites "
            "without an exception for correlated subquery → JOIN conversions. "
            "This prevents the LLM from suggesting the most impactful rewrite "
            "for N+1 anti-patterns."
        )

    def test_prompt_has_correlated_subquery_rewrite_guidance(self):
        """Prompt must explicitly guide the LLM to detect and rewrite N+1 patterns.

        The scoring rule 'Rewrites subquery to JOIN: +30 points' is too vague.
        The prompt should include specific guidance for correlated subquery
        detection and the JOIN+GROUP BY rewrite pattern.
        """
        source = self._get_analyze_prompt()

        # Must mention correlated subquery pattern explicitly
        has_correlated_guidance = bool(
            re.search(r"(?i)correlated.{0,20}subquer", source)
        )
        has_group_by_join_pattern = bool(
            re.search(r"(?i)(JOIN.*GROUP BY|GROUP BY.*JOIN).*subquer|subquer.*JOIN.*GROUP BY", source)
        )

        assert has_correlated_guidance and has_group_by_join_pattern, (
            "Prompt lacks explicit guidance for correlated subquery → JOIN+GROUP BY "
            "rewrite. Must include detection rules and rewrite template for N+1 patterns."
        )

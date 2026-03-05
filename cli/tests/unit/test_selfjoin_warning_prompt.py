"""
Unit test for self-join complexity warning in analyze prompt (rdst-2vr.28).

Self-joins (table joined to itself) have O(n^2) characteristics per group.
No amount of indexing will make them fast on large tables. The LLM prompt
must instruct the model to detect and warn about this pattern.
"""

import re


class TestSelfJoinComplexityWarning:
    """The LLM prompt must instruct the model to warn about self-join O(n^2) complexity."""

    def _get_prompt_source(self) -> str:
        import inspect
        from lib.functions import llm_analysis

        return inspect.getsource(llm_analysis)

    def test_prompt_mentions_self_join_detection(self):
        """Prompt should instruct the LLM to detect self-join patterns."""
        source = self._get_prompt_source()

        has_self_join = bool(
            re.search(r"(?i)self.?join", source)
        )

        assert has_self_join, (
            "Prompt should mention self-join detection. Self-joins have "
            "fundamentally different performance characteristics than regular "
            "joins and need explicit warnings."
        )

    def test_prompt_warns_about_quadratic_complexity(self):
        """Prompt should warn about O(n^2) or quadratic complexity of self-joins."""
        source = self._get_prompt_source()

        has_complexity_warning = bool(
            re.search(
                r"(?i)(O\(n.?2\)|O\(n\^2\)|quadratic|n\*\(n-1\)/2|n squared)",
                source,
            )
        )

        assert has_complexity_warning, (
            "Prompt should warn about the O(n^2) or quadratic nature of "
            "self-joins. Users need to know that indexing alone won't make "
            "these queries fast on large tables."
        )

    def test_prompt_suggests_materialization_for_self_joins(self):
        """Prompt should suggest pre-computation/materialization for self-joins."""
        source = self._get_prompt_source()

        has_materialization = bool(
            re.search(
                r"(?i)(pre.?comput|materializ|pre.?aggregat)",
                source,
            )
        )

        assert has_materialization, (
            "Prompt should suggest pre-computation or materialization as the "
            "recommended approach for self-join queries on large tables."
        )

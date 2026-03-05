"""
Unit tests for missing DBA insights in analyze prompt (rdst-2vr.30).

The LLM analysis should provide DBA-level insights about:
1. Estimate vs actual row discrepancies (stale statistics)
2. work_mem impact on sorts, hashes, and aggregations
"""

import re


class TestEstimateVsActualGuidance:
    """Prompt should guide the LLM to analyze estimate vs actual row discrepancies."""

    def _get_prompt_source(self) -> str:
        import inspect
        from lib.functions import llm_analysis

        return inspect.getsource(llm_analysis)

    def test_prompt_mentions_estimate_vs_actual(self):
        """Prompt should instruct LLM to compare estimated vs actual rows."""
        source = self._get_prompt_source()

        has_estimate_actual = bool(
            re.search(
                r"(?i)(estimate.*actual|actual.*estimate|"
                r"planner.*estimate|row.*estimate.*discrep|"
                r"stale.*statistic)",
                source,
            )
        )

        assert has_estimate_actual, (
            "Prompt should instruct the LLM to compare estimated vs actual "
            "row counts and flag large discrepancies as stale statistics."
        )

    def test_prompt_connects_discrepancy_to_analyze(self):
        """Prompt should suggest running ANALYZE when stats seem stale."""
        source = self._get_prompt_source()

        # Should mention ANALYZE (the SQL command) in context of stale stats
        has_analyze_suggestion = bool(
            re.search(
                r"(?i)(stale.*ANALYZE|ANALYZE.*stale|"
                r"statistic.*ANALYZE|ANALYZE.*statistic|"
                r"VACUUM.*ANALYZE)",
                source,
            )
        )

        assert has_analyze_suggestion, (
            "Prompt should connect estimate/actual discrepancies to running "
            "ANALYZE to refresh table statistics."
        )


class TestWorkMemGuidance:
    """Prompt should guide the LLM to flag work_mem impact on sorts/hashes."""

    def _get_prompt_source(self) -> str:
        import inspect
        from lib.functions import llm_analysis

        return inspect.getsource(llm_analysis)

    def test_prompt_mentions_work_mem(self):
        """Prompt should mention work_mem as a tuning opportunity."""
        source = self._get_prompt_source()

        has_work_mem = bool(
            re.search(r"work_mem", source)
        )

        assert has_work_mem, (
            "Prompt should mention work_mem as a tuning recommendation "
            "for queries with sorts, hashes, or large aggregations."
        )

    def test_prompt_connects_work_mem_to_operations(self):
        """Prompt should connect work_mem to sorts, hashes, and aggregations."""
        source = self._get_prompt_source()

        has_operations = bool(
            re.search(
                r"(?i)work_mem.{0,200}(sort|hash|aggregat)|"
                r"(sort|hash|aggregat).{0,200}work_mem",
                source,
                re.DOTALL,
            )
        )

        assert has_operations, (
            "Prompt should connect work_mem tuning to specific operations "
            "(sorts, hashes, aggregations) that spill to disk when "
            "work_mem is insufficient."
        )

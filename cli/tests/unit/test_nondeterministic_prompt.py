"""
Unit test for non-deterministic function detection in analyze prompt (rdst-2vr.29).

Queries using CURRENT_DATE, NOW(), RANDOM(), etc. have cacheability and plan
stability implications that the LLM analysis should flag. The static checker
in readyset_cacheability.py catches these, but the LLM prompt should also
guide the model to note these functions and their impact.
"""

import re


class TestNonDeterministicFunctionGuidance:
    """The LLM prompt must instruct the model to flag non-deterministic functions."""

    def _get_prompt_source(self) -> str:
        import inspect
        from lib.functions import llm_analysis

        return inspect.getsource(llm_analysis)

    def test_prompt_mentions_nondeterministic_functions(self):
        """Prompt should mention key non-deterministic functions by name."""
        source = self._get_prompt_source()

        # Should mention at least CURRENT_DATE and NOW as non-deterministic
        has_current_date = bool(
            re.search(r"CURRENT_DATE", source)
        )
        has_now = bool(
            re.search(r"NOW\(\)", source)
        )

        assert has_current_date and has_now, (
            "Prompt should mention CURRENT_DATE and NOW() as non-deterministic "
            "functions that affect cacheability. Currently, only the static "
            "checker flags these — the LLM analysis should too."
        )

    def test_prompt_links_nondeterministic_to_cacheability(self):
        """Prompt should explain that non-deterministic functions affect cacheability."""
        source = self._get_prompt_source()

        # Should connect non-deterministic functions to caching/cacheability
        has_cache_link = bool(
            re.search(
                r"(?i)(non.?deterministic.*cach|cach.*non.?deterministic|"
                r"CURRENT_DATE.*cach|NOW.*cach|"
                r"cach.*CURRENT_DATE|cach.*NOW)",
                source,
            )
        )

        assert has_cache_link, (
            "Prompt should explain that non-deterministic functions (CURRENT_DATE, "
            "NOW()) affect cacheability. The LLM needs to flag these for Readyset "
            "cache invalidation analysis."
        )

    def test_prompt_has_nondeterministic_detection_section(self):
        """Prompt should have a dedicated section for non-deterministic function detection."""
        source = self._get_prompt_source()

        has_section = bool(
            re.search(
                r"(?i)NON.?DETERMINISTIC.*FUNCTION.*DETECT|"
                r"DETECT.*NON.?DETERMINISTIC.*FUNCTION",
                source,
            )
        )

        assert has_section, (
            "Prompt should have a dedicated section for non-deterministic function "
            "detection, instructing the LLM to scan for CURRENT_DATE, NOW(), "
            "RANDOM() and flag their implications."
        )

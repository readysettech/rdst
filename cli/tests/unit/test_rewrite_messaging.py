"""
Unit tests for rewrite section messaging in analyze output (rdst-2vr.24).

When no query rewrite is applicable, the output should NOT say
'No rewrites were tested successfully' (implies failure). It should
say something like 'No query rewrites needed' or omit the negative
framing.
"""


class TestRewriteMessagingNoRewrites:
    """When no rewrites are suggested, message should be positive, not failure-framing."""

    def _render(self, rewrite_testing):
        from lib.cli.output_formatter import _format_tested_optimizations

        lines = _format_tested_optimizations(rewrite_testing)
        return "\n".join(lines)

    def test_empty_rewrites_no_failure_message(self):
        """Empty rewrite list should NOT show 'No rewrites were tested successfully'."""
        rewrite_testing = {
            "success": True,
            "rewrite_results": [],
            "original_performance": {"execution_time_ms": 50.0},
            "baseline_skipped": False,
        }
        output = self._render(rewrite_testing)

        assert "No rewrites were tested successfully" not in output, (
            "Empty rewrite list shows 'No rewrites were tested successfully' "
            "which implies failure. Should use positive framing like "
            "'No query rewrites needed'."
        )

    def test_empty_rewrites_positive_framing(self):
        """Empty rewrite list should show a positive/neutral message."""
        rewrite_testing = {
            "success": True,
            "rewrite_results": [],
            "original_performance": {"execution_time_ms": 50.0},
            "baseline_skipped": False,
        }
        output = self._render(rewrite_testing).lower()

        # Should contain positive framing
        has_positive = (
            "no rewrites needed" in output
            or "no query rewrites needed" in output
            or "already optimal" in output
            or "no rewrite opportunities" in output
        )
        assert has_positive, (
            f"Expected positive framing for empty rewrite list, "
            f"but got: {output[:200]}"
        )

    def test_baseline_skipped_no_double_negative(self):
        """When baseline was skipped, don't show redundant 'no rewrites tested' after the warning."""
        rewrite_testing = {
            "success": True,
            "rewrite_results": [
                {"success": True, "recommendation": "index_access", "was_skipped": False,
                 "performance": {"execution_time_ms": 10, "was_skipped": False}},
            ],
            "original_performance": None,
            "baseline_skipped": True,
        }
        output = self._render(rewrite_testing)

        # The baseline-skipped warning should appear
        assert "skipped" in output.lower()

        # But the negative "no rewrites tested successfully" should NOT also appear
        assert "No rewrites were tested successfully" not in output, (
            "When baseline is skipped, the 'slow execution' warning is shown. "
            "Don't also show 'No rewrites were tested successfully' — it's redundant."
        )

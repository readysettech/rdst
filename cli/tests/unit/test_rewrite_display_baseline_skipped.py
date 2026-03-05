"""
Unit test for rewrite display when baseline is skipped (rdst-2vr.32).

When the original query is too slow to baseline (e.g., correlated subquery N+1),
the renderer should still show LLM-suggested rewrites — just without timing
comparison. Currently, all rewrites are hidden when baseline_skipped=True.
"""

from lib.cli.output_formatter import _format_tested_optimizations


def _make_rewrite_testing(*, baseline_skipped=False, rewrites=None):
    """Build a minimal rewrite_testing dict."""
    if rewrites is None:
        rewrites = []
    return {
        "baseline_skipped": baseline_skipped,
        "original_performance": {"execution_time_ms": 0},
        "rewrite_results": rewrites,
    }


def _make_successful_rewrite(sql="SELECT 1", explanation="Test rewrite"):
    """Build a successful rewrite result."""
    return {
        "success": True,
        "sql": sql,
        "recommendation": "apply",
        "was_skipped": False,
        "performance": {
            "execution_time_ms": 500,
            "was_skipped": False,
        },
        "improvement": {
            "overall": {"improvement_pct": 0, "is_better": False, "significant": False},
            "execution_time": {"improvement_pct": 0, "is_better": False},
            "cost_estimate": {"improvement_pct": 0, "is_better": False},
            "rows_examined": {"improvement_pct": 0, "is_better": False},
        },
        "suggestion_metadata": {
            "explanation": explanation,
            "type": "query_restructure",
        },
    }


class TestRewriteDisplayBaselineSkipped:
    """Rewrites must be shown even when baseline was skipped."""

    def test_rewrite_shown_when_baseline_skipped(self):
        """When baseline is skipped but rewrites succeeded, show the rewrite SQL.

        This is the core bug: correlated subquery queries time out on the baseline
        (because they're N+1), but the JOIN+GROUP BY rewrite is fast. The rewrite
        should still be shown to the user.
        """
        rewrite_sql = (
            "SELECT tb.primarytitle, tb.startyear, COUNT(tp.tconst) as cast_size "
            "FROM title_basics tb "
            "LEFT JOIN title_principals tp ON tp.tconst = tb.tconst "
            "WHERE tb.titletype = 'movie' AND tb.startyear >= 2020 "
            "GROUP BY tb.tconst, tb.primarytitle, tb.startyear "
            "ORDER BY cast_size DESC LIMIT 20"
        )
        rewrite_testing = _make_rewrite_testing(
            baseline_skipped=True,
            rewrites=[_make_successful_rewrite(
                sql=rewrite_sql,
                explanation="Convert correlated subquery to JOIN + GROUP BY",
            )],
        )
        parts = _format_tested_optimizations(rewrite_testing)

        # The rewrite SQL should appear somewhere in the rendered output
        rendered = "\n".join(str(p) for p in parts)
        assert "LEFT JOIN" in rendered or "cast_size" in rendered, (
            "Rewrite SQL should be displayed even when baseline was skipped. "
            "Currently, all rewrites are hidden when baseline_skipped=True, "
            "which hides the most important rewrite for N+1 correlated subqueries."
        )

    def test_baseline_skipped_warning_still_shown(self):
        """The baseline skipped warning should still appear alongside rewrites."""
        rewrite_testing = _make_rewrite_testing(
            baseline_skipped=True,
            rewrites=[_make_successful_rewrite()],
        )
        parts = _format_tested_optimizations(rewrite_testing)
        rendered = "\n".join(str(p) for p in parts)
        assert "skipped" in rendered.lower(), (
            "Should still show baseline skipped warning"
        )

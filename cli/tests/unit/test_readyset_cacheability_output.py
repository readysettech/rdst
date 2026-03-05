"""
Unit tests for Readyset cacheability in default analyze output (rdst-2vr.26).

The analyze command runs CheckReadysetCacheability (pure SQL pattern matching,
no Docker) but its result never reaches the output formatter because
FormatFinalResults doesn't pass it through. All the infrastructure exists —
the plumbing is just disconnected.
"""

import json


class TestWorkflowPassesCacheability:
    """FormatFinalResults must include readyset_cacheability in its parameters."""

    def test_workflow_json_passes_readyset_cacheability(self):
        """Workflow must pass readyset_cacheability to FormatFinalResults step."""
        import pathlib

        workflow_path = (
            pathlib.Path(__file__).parents[2]
            / "lib"
            / "workflows"
            / "analyze_workflow_simple.json"
        )
        workflow = json.loads(workflow_path.read_text())

        format_step = workflow["States"]["FormatFinalResults"]
        params = format_step["Parameters"]

        assert "readyset_cacheability" in params, (
            "FormatFinalResults step does not pass readyset_cacheability to the "
            "formatter. The CheckReadysetCacheability step stores its result at "
            "$.readyset_cacheability but FormatFinalResults doesn't include it "
            "in Parameters, so the cacheability data is computed but never displayed."
        )


class TestFormatReadysetCacheabilityFlat:
    """_format_readyset_cacheability must handle the flat dict from check_readyset_cacheability."""

    def _get_formatter(self):
        from lib.functions.workflow_integration import _format_readyset_cacheability

        return _format_readyset_cacheability

    def _make_flat_result(self, cacheable=True):
        """Create a flat dict matching check_readyset_cacheability output."""
        return {
            "cacheable": cacheable,
            "confidence": "high",
            "issues": [] if cacheable else ["Uses NOW() function"],
            "warnings": [],
            "create_cache_command": "CREATE CACHE FROM SELECT 1" if cacheable else None,
            "explanation": "Query is cacheable" if cacheable else "Non-deterministic function",
            "query_parameterized": False,
            "recommended_options": {},
        }

    def test_flat_cacheable_result_has_checked_true(self):
        """Flat dict with cacheable=True must produce checked=True."""
        fmt = self._get_formatter()
        result = fmt(self._make_flat_result(cacheable=True))
        assert result.get("checked") is True, (
            f"Expected checked=True, got {result.get('checked')}"
        )

    def test_flat_cacheable_result_preserves_cacheable(self):
        """Flat dict must preserve the cacheable boolean."""
        fmt = self._get_formatter()
        result = fmt(self._make_flat_result(cacheable=True))
        assert result.get("cacheable") is True, (
            f"Expected cacheable=True, got {result.get('cacheable')}. "
            f"The formatter likely looks for 'final_verdict.cacheable' "
            f"but the simple workflow returns a flat dict."
        )

    def test_flat_not_cacheable_result(self):
        """Flat dict with cacheable=False must preserve that."""
        fmt = self._get_formatter()
        result = fmt(self._make_flat_result(cacheable=False))
        assert result.get("cacheable") is False

    def test_flat_result_includes_explanation(self):
        """Flat dict explanation must flow through."""
        fmt = self._get_formatter()
        result = fmt(self._make_flat_result(cacheable=True))
        assert result.get("explanation"), (
            f"Explanation missing from formatted result. "
            f"Formatter may look for 'static_analysis.explanation' but "
            f"the flat dict has 'explanation' at the top level."
        )

    def test_flat_result_includes_confidence(self):
        """Flat dict confidence must flow through."""
        fmt = self._get_formatter()
        result = fmt(self._make_flat_result(cacheable=True))
        assert result.get("confidence") == "high", (
            f"Expected confidence='high', got {result.get('confidence')}"
        )

    def test_flat_result_has_method_static_analysis(self):
        """Flat result (no Docker) should report method as static_analysis."""
        fmt = self._get_formatter()
        result = fmt(self._make_flat_result(cacheable=True))
        assert result.get("method") == "static_analysis", (
            f"Expected method='static_analysis', got {result.get('method')}"
        )

    def test_flat_result_has_success_for_renderer(self):
        """Formatted dict needs 'success' key for output_formatter rendering gate."""
        fmt = self._get_formatter()
        result = fmt(self._make_flat_result(cacheable=True))
        # output_formatter.py:1099 checks readyset_cacheability.get("success")
        assert result.get("success") is True, (
            f"Expected success=True for the output_formatter rendering gate. "
            f"output_formatter.py:1099 checks readyset_cacheability.get('success') "
            f"but the formatted dict only has 'checked'."
        )

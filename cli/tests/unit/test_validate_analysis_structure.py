"""
Unit tests for _validate_analysis_structure in llm_analysis.py.

Verifies that all LLM response fields are preserved through validation,
particularly index_recommendations which scan_command depends on.
"""

from lib.functions.llm_analysis import _validate_analysis_structure


class TestValidateAnalysisStructure:
    """Tests for _validate_analysis_structure."""

    def test_index_recommendations_preserved(self):
        """index_recommendations must survive validation.

        scan_command.py reads index_recommendations from analysis_results
        (the validated dict). If validation drops them, scan gets zero indexes.
        """
        analysis = {
            "performance_assessment": {
                "overall_rating": "poor",
                "efficiency_score": 25,
                "primary_concerns": ["Sequential scan on large table"],
            },
            "execution_analysis": {},
            "optimization_opportunities": [],
            "rewrite_suggestions": [],
            "index_recommendations": [
                {
                    "sql": "CREATE INDEX idx_test ON foo(bar, baz)",
                    "table": "foo",
                    "columns": ["bar", "baz"],
                    "index_type": "btree",
                    "rationale": "Covers equality + range filter",
                    "estimated_impact": "high",
                }
            ],
            "explanation": "Test analysis",
        }

        result = _validate_analysis_structure(analysis)

        assert "index_recommendations" in result, (
            "index_recommendations missing from validated output — "
            "scan_command reads from analysis_results and will get zero indexes"
        )
        assert len(result["index_recommendations"]) == 1
        assert result["index_recommendations"][0]["table"] == "foo"

    def test_empty_index_recommendations_preserved(self):
        """Empty index_recommendations list should be present, not missing."""
        analysis = {
            "performance_assessment": {"overall_rating": "excellent", "efficiency_score": 95},
            "execution_analysis": {},
            "optimization_opportunities": [],
            "rewrite_suggestions": [],
            "index_recommendations": [],
            "explanation": "No indexes needed",
        }

        result = _validate_analysis_structure(analysis)
        assert "index_recommendations" in result
        assert result["index_recommendations"] == []

    def test_missing_index_recommendations_defaults_to_empty(self):
        """If LLM omits index_recommendations, validation should default to []."""
        analysis = {
            "performance_assessment": {"overall_rating": "good", "efficiency_score": 70},
            "execution_analysis": {},
            "optimization_opportunities": [],
            "rewrite_suggestions": [],
            "explanation": "Minimal response",
        }

        result = _validate_analysis_structure(analysis)
        assert "index_recommendations" in result
        assert result["index_recommendations"] == []

    def test_non_list_index_recommendations_normalized(self):
        """Non-list index_recommendations should be normalized to []."""
        analysis = {
            "performance_assessment": {"overall_rating": "good", "efficiency_score": 70},
            "execution_analysis": {},
            "optimization_opportunities": [],
            "rewrite_suggestions": [],
            "index_recommendations": "not a list",
            "explanation": "Bad format",
        }

        result = _validate_analysis_structure(analysis)
        assert isinstance(result["index_recommendations"], list)

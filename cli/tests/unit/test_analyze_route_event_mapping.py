import json

from features.analyze.api.routes import _event_to_sse
from features.analyze.event_payloads import LLMAnalysis, PerformanceAssessment
from features.analyze.events import CompleteEvent


def test_complete_event_serializes_nested_models_as_json_objects() -> None:
    event = CompleteEvent(
        type="complete",
        success=True,
        llm_analysis=LLMAnalysis(
            success=True,
            performance_assessment=PerformanceAssessment(
                overall_rating="good",
                efficiency_score=80,
                primary_concerns=["Sequential scan"],
            ),
        ),
    )

    mapped = _event_to_sse(event)
    data = json.loads(mapped["data"])

    assert data["llm_analysis"] == {
        "success": True,
        "performance_assessment": {
            "overall_rating": "good",
            "efficiency_score": 80.0,
            "execution_time_rating": None,
            "primary_concerns": ["Sequential scan"],
        },
        "execution_analysis": None,
        "rewrite_suggestions": None,
        "index_recommendations": None,
        "optimization_opportunities": None,
        "llm_model": None,
        "token_usage": None,
    }

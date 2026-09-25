"""A deep analysis survives one malformed JSON answer from the model."""

import json
from unittest.mock import MagicMock

from features.analyze.functions import llm_analysis

# A hosted answer seen in the live E2E: performance_assessment is never closed.
MALFORMED = """{
  "performance_assessment": {
    "overall_rating": "good",
    "primary_concerns": ["Sort on 200 rows"
  ],
  "execution_analysis": {"bottlenecks": []},
  "index_recommendations": []
}"""


def _analyze(monkeypatch, *answers: str) -> tuple[dict, MagicMock]:
    manager = MagicMock()
    manager.generate_response.side_effect = [
        {"response": answer, "provider": "readyset", "model": "z-ai/glm-5.3-flash",
         "tokens_used": 100, "usage": {}} for answer in answers]
    monkeypatch.setattr(llm_analysis, "LLMManager", lambda: manager)
    result = llm_analysis.analyze_with_llm(
        {"database_engine": "postgresql", "success": True}, {}, "SELECT 1", target="t")
    return result, manager


def test_a_malformed_answer_is_asked_again(monkeypatch):
    valid = json.dumps({"performance_assessment": {"overall_rating": "good"},
                        "explanation": "Uses the index."})
    result, manager = _analyze(monkeypatch, MALFORMED, valid)
    assert result["success"] is True
    assert result["analysis_results"]["explanation"] == "Uses the index."
    assert manager.generate_response.call_count == 2


def test_a_second_malformed_answer_fails_without_more_calls(monkeypatch):
    result, manager = _analyze(monkeypatch, MALFORMED, MALFORMED)
    assert result["success"] is False
    assert result["error"] == "Failed to parse LLM response as JSON"
    assert manager.generate_response.call_count == llm_analysis.ANALYZE_JSON_ATTEMPTS == 2


def test_a_readable_answer_takes_one_call(monkeypatch):
    result, manager = _analyze(monkeypatch, json.dumps({"performance_assessment": {}}))
    assert result["success"] is True
    assert manager.generate_response.call_count == 1

"""Which AI answered a deep analysis and what it cost: the hosted model through Keyservice
with its charged cost, never Claude prices for a hosted call."""

import json
from unittest.mock import MagicMock

import pytest

from features.analyze.functions import llm_analysis
from features.analyze.functions.workflow_integration import _llm_info


def _analyze(monkeypatch, response: dict) -> dict:
    manager = MagicMock()
    answer = {"performance_assessment": {"overall_score": 70}}
    manager.generate_response.return_value = {"response": json.dumps(answer), **response}
    monkeypatch.setattr(llm_analysis, "LLMManager", lambda: manager)
    return llm_analysis.analyze_with_llm(
        {"database_engine": "postgresql", "success": True}, {}, "SELECT 1", target="t")


def test_hosted_analysis_records_the_hosted_model_and_charged_cost(monkeypatch):
    result = _analyze(monkeypatch, {
        "provider": "readyset", "model": "z-ai/glm-5.3-flash", "cost_usd": 0.0021,
        "tokens_used": 4400, "usage": {"prompt_tokens": 3900, "completion_tokens": 500}})
    usage = result["token_usage"]
    assert (usage["provider"], usage["model"]) == ("readyset", "z-ai/glm-5.3-flash")
    assert (usage["input"], usage["output"], usage["total"]) == (3900, 500, 4400)
    assert usage["cost_usd"] == usage["estimated_cost_usd"] == 0.0021
    assert _llm_info(result) == {"model": "z-ai/glm-5.3-flash", "tokens": 4400,
                                 "provider": "readyset", "cost": 0.0021,
                                 "cost_estimated": False}


def test_hosted_analysis_without_a_charge_never_uses_claude_prices(monkeypatch):
    result = _analyze(monkeypatch, {"provider": "readyset", "model": "z-ai/glm-5.3-flash",
                                    "tokens_used": 4400, "usage": {}})
    assert result["token_usage"]["cost_usd"] is None
    assert result["token_usage"]["estimated_cost_usd"] == 0.0
    assert _llm_info(result)["cost"] is None


def test_own_claude_key_is_estimated_from_published_prices(monkeypatch):
    result = _analyze(monkeypatch, {
        "provider": "claude", "model": "claude-sonnet-4-5", "tokens_used": 2000,
        "usage": {"prompt_tokens": 1500, "completion_tokens": 500}})
    info = _llm_info(result)
    assert info["provider"] == "claude" and info["cost_estimated"] is True
    assert info["cost"] == llm_analysis.estimate_cost(1500, 500, "claude-sonnet-4-5")


def test_each_rewrite_runs_under_the_runs_statement_timeout(monkeypatch):
    from features.analyze.functions import rewrite_testing

    calls = []

    def explain(**kwargs):
        calls.append(kwargs)
        return {"success": True, "execution_time_ms": 5.0, "rows_returned": 1,
                "rows_examined": 1, "cost_estimate": 1.0}

    monkeypatch.setattr(rewrite_testing, "execute_explain_analyze", explain)
    result = rewrite_testing.test_query_rewrites(
        "SELECT 1", [{"rewritten_sql": "SELECT 2"}], target="t",
        baseline_result={"success": True, "execution_time_ms": 10.0},
        statement_timeout_ms="1500", non_interactive="True")
    assert result["success"] and result["rewrite_results"][0]["success"]
    assert calls[0]["rewrite_max_time_ms"] == 1500
    assert calls[0]["statement_timeout_ms"] == 1500
    assert calls[0]["non_interactive"] == "True"


def test_haiku_prompts_past_100k_tokens_are_priced_at_the_long_prompt_rate():
    from features.analyze.functions import shallow_analysis

    for estimate in (llm_analysis.estimate_cost, shallow_analysis.estimate_cost):
        assert estimate(100_000, 10_000, "claude-haiku-5-5") == pytest.approx(0.015)
        assert estimate(120_000, 10_000, "claude-haiku-5-5") == pytest.approx(0.085)
        assert estimate(120_000, 10_000, "claude-haiku-4-5-20251001") == pytest.approx(0.17)

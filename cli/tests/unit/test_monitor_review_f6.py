"""Immediate deep-analysis retries keep their claim and count usable generations."""

from types import SimpleNamespace
from unittest.mock import MagicMock
import uuid

import pytest

from features.monitor.analyze_queue import AnalyzeQueue, analysis_retry, retry_plan
from features.monitor.api.routes import _jobs_with_reasons
from features.monitor.presentation import analysis_error_kind, analysis_reason
from shared.llm_manager.base import ProviderRequest
from shared.llm_manager.hosted_provider import HostedProvider
from shared.llm_manager.inference_attribution import record_billable_call
from shared.monitor.monitor_store import MonitorStore

NOW = 1_800_000_000
ERROR = "UPSTREAM_ERROR: LLM analysis failed: OpenRouter returned an unusable response; quota is held for reconciliation"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["new", "report", "user"])
@pytest.mark.parametrize("second_fails", [False, True])
@pytest.mark.parametrize("shape", ["event", "complete", "exception", "provider"])
async def test_immediate_retry(tmp_path, monkeypatch, reason, second_fails, shape):
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get", lambda self, target: {})
    monkeypatch.setattr("features.monitor.database_limit._signed_in", lambda: True)
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.record("db", NOW, [{"query_key": "q", "calls": 1, "total_ms": 10}],
                 {"q": "SELECT 1"}, [], None)
    store.enqueue_analysis("db", "q", reason, 10, NOW)
    job = store.claim_analysis("db", NOW)

    request_ids = []

    def post(*args, **kwargs):
        request_ids.append(kwargs["json"]["request_id"])
        response = MagicMock()
        failed = len(request_ids) == 1 or second_fails
        response.status_code = 502 if failed else 200
        response.json.return_value = ({"code": "UPSTREAM_ERROR", "detail": ERROR} if failed
                                      else {"text": "answer", "usage": {}})
        return response

    monkeypatch.setattr("shared.llm_manager.hosted_provider._HTTP_SESSION.post", post)

    class Service:
        attempts = 0

        async def suggest_parameter_values(self, *args):
            return {"placeholders": []}

        async def analyze(self, *args):
            self.attempts += 1
            current = store.analyze_jobs("db")[0]
            assert current["state"] == "running"
            assert current["owner"] == job["owner"] and current["attempt"] == job["attempt"]
            if self.attempts == 2:
                assert current["error"] == "retrying"
                assert analysis_error_kind("running", current["error"]) == "retrying"
                assert "trying again" in _jobs_with_reasons(store, "db", [current])[0]["reason_text"]
            if shape == "provider":
                HostedProvider().complete(
                    ProviderRequest(model="unused", messages=[{"role": "user", "content": "Analyze"}]),
                    api_key="test-token", base_url="https://keyservice.example")
            if self.attempts == 1 or second_fails:
                if shape == "exception":
                    raise RuntimeError(ERROR)
                if shape == "event":
                    yield SimpleNamespace(type="error", message=ERROR)
                else:
                    yield SimpleNamespace(type="complete", llm_analysis={"success": False,
                                          "error_code": "UPSTREAM_ERROR", "error": ERROR})
            else:
                if shape != "provider":
                    record_billable_call()
                yield SimpleNamespace(type="complete", query_hash="q", stored_analysis_id="saved",
                                      llm_analysis={"success": True}, explain_results={"success": True})

    service = Service()
    await AnalyzeQueue(store, service=service, clock=lambda: NOW)._process(job)
    result = store.analyze_jobs("db")[0]
    assert service.attempts == 2
    if shape == "provider":
        assert len(set(request_ids)) == 2
        assert all(str(uuid.UUID(value)) == value for value in request_ids)
    assert result["state"] == ("failed" if second_fails else "done")
    assert result["billable_calls"] == (0 if second_fails else 1)
    if second_fails:
        assert analysis_reason("failed", result["error"]) == (
            "The AI service didn't return an answer for this query. RDST tries again later.")
        assert analysis_retry(result, retry_plan(store, "db", NOW)["q"])["error_kind"] == "retrying"
    else:
        assert result["error"] is None
        assert result["analysis_ref"] == "/api/query-registry/q/analysis/saved"


@pytest.mark.asyncio
async def test_lost_claim_prevents_retry(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get", lambda self, target: {})
    monkeypatch.setattr("features.monitor.database_limit._signed_in", lambda: True)
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.record("db", NOW, [], {"q": "SELECT 1"}, [], None)
    store.enqueue_analysis("db", "q", "user", 10, NOW)
    job = store.claim_analysis("db", NOW)

    class Service:
        attempts = 0

        async def suggest_parameter_values(self, *args):
            return {"placeholders": []}

        async def analyze(self, *args):
            self.attempts += 1
            store.recover_analysis(NOW + 601, 600)
            store.claim_analysis("db", NOW + 601)
            yield SimpleNamespace(type="error", message=ERROR)

    service = Service()
    await AnalyzeQueue(store, service=service, clock=lambda: NOW)._process(job)
    assert service.attempts == 1
    assert not store.retry_analysis(job)
    result = store.analyze_jobs("db")[0]
    assert result["state"] == "running" and result["attempt"] > job["attempt"]


def test_retry_preserves_restart_recovery_limit(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.enqueue_analysis("db", "q", "user", 10, NOW)
    store.claim_analysis("db", NOW)
    store.recover_analysis(NOW + 601, 600)
    job = store.claim_analysis("db", NOW + 601)
    assert store.retry_analysis(job)
    current = store.analyze_jobs("db")[0]
    assert analysis_error_kind("running", current["error"]) == "retrying"
    store.recover_analysis(NOW + 1202, 600)
    assert store.analyze_jobs("db")[0]["state"] == "failed"

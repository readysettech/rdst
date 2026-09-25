"""Monitor provider routing with real clients and isolated transport receivers."""

import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from features.monitor import database_limit
from shared import account_session
from shared.config.targets import TargetsConfig
from shared.llm_manager import hosted_glm_provider, inference_attribution
from shared.llm_manager.jev_client import JevClient
from shared.llm_manager.llm_manager import LLMManager
from shared.secret_store_service import SecretStoreService


@pytest.mark.parametrize("source,preference,provider", [
    ("hosted", None, "readyset"),
    ("env", None, "claude"),
    ("keyring", None, "claude"),
    ("env", "readyset", "readyset"),
    ("keyring", "readyset", "readyset"),
])
def test_monitor_transport_routing(monkeypatch, source, preference, provider):
    # Register restoration even when key resolution exports a keyring secret.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    if source == "env":
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-own-env")
    monkeypatch.setenv("RDST_KEYSERVICE_URL", "https://service.example")
    monkeypatch.setattr(TargetsConfig, "load", lambda self: None)
    monkeypatch.setattr(TargetsConfig, "get_llm_provider", lambda self: preference)
    monkeypatch.setattr(SecretStoreService, "get_secret", lambda self, name:
                        "test-own-keyring" if source == "keyring" else None)
    monkeypatch.setattr(account_session, "access_token", lambda **kw: "test-service-token")
    monkeypatch.setattr("shared.agent_activity.record_jev_call", lambda *a, **kw: None)
    calls = []
    hosted_cost = []
    operation = [None]

    def direct(request):
        payload = json.loads(request.content)
        calls.append((operation[0], str(request.url), dict(request.headers), payload))
        return httpx.Response(200, json={
            "id": "msg-test", "type": "message", "role": "assistant",
            "model": payload["model"], "content": [{"type": "text", "text": "answer"}],
            "stop_reason": "end_turn", "usage": {"input_tokens": 17, "output_tokens": 5},
        })

    sdk = anthropic.Anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw:
                        sdk(**kw, http_client=httpx.Client(transport=httpx.MockTransport(direct))))

    class ServiceTransport:
        def post(self, url, *, json, headers, timeout):
            calls.append((json["attribution"]["operation"], url, headers, json))
            if url.endswith("/v1/inference"):
                hosted_cost.append(23)
                body = {"text": "answer", "usage": {
                    "prompt_tokens": 17, "completion_tokens": 5, "cost_microusd": 23}}
            else:
                body = {"request_id": json["request_id"], "model": "jev-1.13.0",
                        "rubric_version": json["rubric_version"], "event_scope": "single",
                        "queries": [], "open_items": [],
                        "usage": {"input_tokens": 11, "output_tokens": 3}}
            return SimpleNamespace(status_code=200, json=lambda: body, headers={})

    transport = ServiceTransport()
    monkeypatch.setattr(hosted_glm_provider, "_HTTP_SESSION", transport)
    manager = LLMManager()
    with inference_attribution.monitoring_database("a" * 64):
        for purpose in ("monitor_analyze", "monitor_report"):
            operation[0] = purpose
            with inference_attribution.inference_workflow("monitor", "web", operation=purpose), \
                    inference_attribution.count_billable_calls() as billed:
                response = manager.query(system_message="system", user_query="query",
                                         max_tokens=123, purpose=purpose)
            assert response["provider"] == provider
            assert response["usage"]["prompt_tokens"] == 17
            assert response["usage"]["completion_tokens"] == 5
            assert billed.count == (1 if provider == "readyset" else 0)
        with inference_attribution.count_billable_calls() as billed:
            JevClient(transport).attention(request_id="attention-test", target_numbers={},
                                           index_changes=[], open_items=[], queries=[])
        assert billed.count == 1
    assert [call[0] for call in calls] == ["monitor_analyze", "monitor_report", "monitor_attention"]
    for _, url, headers, payload in calls[:2]:
        assert payload["max_tokens"] == 123
        if provider == "claude":
            assert url == "https://api.anthropic.com/v1/messages"
            assert headers["x-api-key"] == f"test-own-{source}"
            assert "authorization" not in headers
            assert "x-rdst-monitor-database" not in headers
            assert "attribution" not in payload
        else:
            assert url == "https://service.example/v1/inference"
            assert headers["Authorization"] == "Bearer test-service-token"
            assert headers["X-RDST-Monitor-Database"] == "a" * 64
    _, url, headers, _ = calls[-1]
    assert url == "https://service.example/v1/jev/attention"
    assert headers["Authorization"] == "Bearer test-service-token"
    assert headers["X-RDST-Monitor-Database"] == "a" * 64
    assert hosted_cost == ([23, 23] if provider == "readyset" else [])
    assert database_limit.own_key() is (provider == "claude")


@pytest.mark.parametrize("code,status,detail", [
    ("MONITOR_DATABASE_LIMIT", 403, "You can use AI monitoring on up to 3 databases."),
    ("HOSTED_CAP_REACHED", 403, "Readyset-hosted inference limit reached for this month"),
    ("HOSTED_GLOBAL_CAP_REACHED", 503, "Readyset-hosted inference is temporarily unavailable"),
])
@pytest.mark.parametrize("purpose", ["monitor_analyze", "monitor_report"])
def test_monitor_transport_refusal_preserves_message_and_never_bills(
        monkeypatch, code, status, detail, purpose):
    from shared.llm_manager.base import LLMError

    monkeypatch.setattr(TargetsConfig, "load", lambda self: None)
    monkeypatch.setattr(TargetsConfig, "get_llm_provider", lambda self: "readyset")
    monkeypatch.setattr(account_session, "access_token", lambda **kw: "test-service-token")
    sent = []

    class Transport:
        def post(self, url, *, json, headers, timeout):
            sent.append(json["attribution"]["operation"])
            return SimpleNamespace(status_code=status, headers={},
                                   json=lambda: {"code": code, "detail": detail})

    monkeypatch.setattr(hosted_glm_provider, "_HTTP_SESSION", Transport())
    with inference_attribution.inference_workflow("monitor", "web", operation=purpose), \
            inference_attribution.count_billable_calls() as billed:
        with pytest.raises(LLMError) as error:
            LLMManager().query(system_message="system", user_query="query", purpose=purpose)
    assert error.value.code == code
    assert str(error.value) == detail
    assert billed.count == 0
    assert sent == [purpose]

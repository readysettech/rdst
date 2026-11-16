from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.ai_data_consent import AI_DATA_CONSENT_VERSION
from shared.api.routes import settings as settings_routes
from shared.api.routes.settings import router as settings_router
from shared.config.targets import TargetsConfig
from shared.llm_manager import LLMError, LLMManager


@pytest.fixture(autouse=True)
def store_build(monkeypatch):
    monkeypatch.setenv("RDST_CONSENT", "opt_in")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(settings_router, prefix="/api")
    return TestClient(app)


def test_missing_ai_data_consent_is_pending(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    with patch.object(settings_routes, "require_local_request"):
        response = _client().get("/api/settings/ai-data")

    assert response.status_code == 200
    assert response.json() == {
        "consent": "pending",
        "enabled": False,
        "consent_version": AI_DATA_CONSENT_VERSION,
        "consent_required": True,
    }


def test_ai_data_consent_can_be_granted_and_withdrawn(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    client = _client()

    with patch.object(settings_routes, "require_local_request"):
        granted = client.put("/api/settings/ai-data", json={"consent": "granted"})
        denied = client.put("/api/settings/ai-data", json={"consent": "denied"})

    assert granted.status_code == 200
    assert granted.json()["enabled"] is True
    assert denied.status_code == 200
    assert denied.json()["enabled"] is False
    cfg = TargetsConfig()
    cfg.load()
    assert cfg.get_ai_data_consent() == "denied"
    assert cfg.get_ai_data_consent_version() == AI_DATA_CONSENT_VERSION


def test_stale_ai_data_grant_requires_consent_again(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    cfg = TargetsConfig()
    cfg.load()
    cfg.set_ai_data_consent("granted", AI_DATA_CONSENT_VERSION - 1)
    cfg.save()

    with patch.object(settings_routes, "require_local_request"):
        response = _client().get("/api/settings/ai-data")

    assert response.status_code == 200
    assert response.json()["consent"] == "pending"
    assert response.json()["enabled"] is False


def test_desktop_llm_blocks_provider_before_consent(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("RDST_DESKTOP", "1")
    provider = MagicMock()
    provider.default_model.return_value = "test-model"
    manager = LLMManager()
    manager._providers = {"claude": provider}

    with pytest.raises(LLMError) as exc_info:
        manager.query(system_message="system", user_query="sensitive SQL")

    assert exc_info.value.code == "AI_DATA_CONSENT_REQUIRED"
    provider.complete.assert_not_called()


@pytest.mark.parametrize("surface", ["RDST_DESKTOP", "RDST_WEB_SERVER"])
def test_builds_outside_the_store_need_no_ai_data_consent(tmp_path, monkeypatch, surface):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.delenv("RDST_CONSENT")
    monkeypatch.setenv(surface, "1")

    with patch.object(settings_routes, "require_local_request"):
        response = _client().get("/api/settings/ai-data")

    assert LLMManager()._enforce_data_consent is False
    assert response.json()["consent_required"] is False
    assert response.json()["enabled"] is True


def test_desktop_llm_allows_provider_after_consent(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("RDST_DESKTOP", "1")
    cfg = TargetsConfig()
    cfg.load()
    cfg.set_ai_data_consent("granted", AI_DATA_CONSENT_VERSION)
    cfg.save()
    provider = MagicMock()
    provider.default_model.return_value = "test-model"
    provider.complete.return_value.text = "response"
    provider.complete.return_value.usage = {}
    provider.complete.return_value.raw = {}
    manager = LLMManager()
    manager._providers = {"claude": provider}

    with patch.object(manager, "_safe_load_key_for_query") as key:
        key.return_value.api_key = "test-key"
        key.return_value.provider = "claude"
        key.return_value.proxy_url = None
        key.return_value.extra_headers = None
        result = manager.query(system_message="system", user_query="sensitive SQL")

    assert result["text"] == "response"
    provider.complete.assert_called_once()


@pytest.mark.asyncio
async def test_desktop_stream_blocks_provider_before_consent(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("RDST_DESKTOP", "1")
    provider = MagicMock()
    provider.default_model.return_value = "test-model"
    manager = LLMManager()
    manager._providers = {"claude": provider}

    stream = manager.query_stream(system_message="system", user_query="sensitive SQL")
    with pytest.raises(LLMError) as exc_info:
        await anext(stream)

    assert exc_info.value.code == "AI_DATA_CONSENT_REQUIRED"
    provider.stream.assert_not_called()


def test_direct_anthropic_agent_call_blocks_before_client_creation(
    tmp_path, monkeypatch
):
    from features.agent.chat_agent import ChatAgent

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("RDST_DESKTOP", "1")
    agent = object.__new__(ChatAgent)
    agent._llm_manager = LLMManager()
    agent.messages = [{"role": "user", "content": "show customer rows"}]
    anthropic = MagicMock()

    with patch.dict("sys.modules", {"anthropic": anthropic}):
        with pytest.raises(LLMError) as exc_info:
            agent._call_llm()

    assert exc_info.value.code == "AI_DATA_CONSENT_REQUIRED"
    anthropic.Anthropic.assert_not_called()


def test_provider_validation_can_explicitly_bypass_data_gate(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("RDST_DESKTOP", "1")
    provider = MagicMock()
    provider.default_model.return_value = "test-model"
    provider.complete.return_value.text = "ok"
    provider.complete.return_value.usage = {}
    provider.complete.return_value.raw = {}
    manager = LLMManager(enforce_data_consent=False)
    manager._providers = {"claude": provider}

    with patch.object(manager, "_safe_load_key_for_query") as key:
        key.return_value.api_key = "test-key"
        key.return_value.provider = "claude"
        key.return_value.proxy_url = None
        key.return_value.extra_headers = None
        manager.query(system_message="ping", user_query="ping")

    provider.complete.assert_called_once()

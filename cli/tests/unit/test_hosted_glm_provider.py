import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from shared.llm_manager.base import LLMError, ProviderRequest
from shared.llm_manager.hosted_glm_provider import HostedGLMProvider


def _request(extra=None):
    return ProviderRequest(
        model="user-selected-model-is-ignored",
        messages=[
            {"role": "system", "content": "Generate SQL."},
            {"role": "user", "content": "Count users."},
        ],
        max_tokens=900,
        temperature=0,
        extra=extra or {},
    )


def _response(status_code=200, body=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body or {
        "id": "generation-1",
        "model": "z-ai/glm-5.3-flash",
        "text": "SELECT COUNT(*) FROM users",
        "usage": {"prompt_tokens": 20, "completion_tokens": 8},
        "quota": {"remaining_microusd": 200000},
    }
    return response


def test_calls_only_bounded_keyservice_protocol(monkeypatch):
    post = MagicMock(return_value=_response())
    monkeypatch.setattr("shared.llm_manager.hosted_glm_provider._HTTP_SESSION.post", post)

    result = HostedGLMProvider().complete(
        _request(extra={"_rdst_schema_cache_key": "a" * 64}),
        api_key="supabase-access",
        base_url="https://keyservice.example",
        debug=True,
    )

    assert result.text == "SELECT COUNT(*) FROM users"
    assert result.usage == {
        "prompt_tokens": 20,
        "completion_tokens": 8,
        "total_tokens": 28,
    }
    url = post.call_args.args[0]
    payload = post.call_args.kwargs["json"]
    headers = post.call_args.kwargs["headers"]
    assert url == "https://keyservice.example/v1/inference"
    assert headers["Authorization"] == "Bearer supabase-access"
    assert "model" not in payload
    assert "provider" not in payload
    assert "reasoning" not in payload
    assert payload["schema_cache_key"] == "a" * 64


def test_preserves_provider_reported_cache_hits(monkeypatch):
    response = _response(body={
        "text": "SELECT 1",
        "usage": {
            "prompt_tokens": 4000,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 3500, "cache_write_tokens": 500},
        },
    })
    monkeypatch.setattr(
        "shared.llm_manager.hosted_glm_provider._HTTP_SESSION.post",
        MagicMock(return_value=response),
    )
    result = HostedGLMProvider().complete(
        _request(), api_key="fixture", base_url="https://keyservice.example",
    )
    assert result.usage["cache_read_input_tokens"] == 3500
    assert result.usage["cache_creation_input_tokens"] == 500
    assert result.usage["prompt_tokens"] == 4000
    assert result.usage["total_tokens"] == 4020


def test_saves_analytics_identity_only_for_the_response_session(monkeypatch):
    body = _response().json.return_value
    body["analytics_account_id"] = "rdst_account_hash"
    post = MagicMock(return_value=_response(body=body))
    save = MagicMock(return_value=True)
    monkeypatch.setattr("shared.llm_manager.hosted_glm_provider._HTTP_SESSION.post", post)
    monkeypatch.setattr(
        "shared.llm_manager.hosted_glm_provider.account_session."
        "save_account_metadata_for_access_token",
        save,
    )

    HostedGLMProvider().complete(
        _request(),
        api_key="supabase-access",
        base_url="https://keyservice.example",
    )

    save.assert_called_once_with(
        {"analytics_account_id": "rdst_account_hash"},
        "supabase-access",
    )


def test_forwards_only_reserved_attribution_metadata(monkeypatch):
    post = MagicMock(return_value=_response())
    monkeypatch.setattr("shared.llm_manager.hosted_glm_provider._HTTP_SESSION.post", post)
    monkeypatch.setenv("RDST_TELEMETRY", "false")

    HostedGLMProvider().complete(
        _request({
            "_rdst_attribution": {
                "feature": "ask",
                "operation": "sql_generation",
                "surface": "cli",
                "workflow_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            },
            "response_format": {"type": "json_object"},
        }),
        api_key="supabase-access",
        base_url="https://keyservice.example",
    )

    payload = post.call_args.kwargs["json"]
    assert payload["attribution"]["feature"] == "ask"
    assert payload["attribution"]["operation"] == "sql_generation"
    assert payload["attribution"]["analytics_disabled"] is True
    assert "_rdst_attribution" not in payload
    assert "response_format" not in payload["attribution"]


def test_json_schema_is_prompted_and_forwarded(monkeypatch):
    post = MagicMock(return_value=_response())
    monkeypatch.setattr("shared.llm_manager.hosted_glm_provider._HTTP_SESSION.post", post)
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "sql",
            "schema": {"type": "object", "required": ["sql"]},
        },
    }

    HostedGLMProvider().complete(
        _request({"response_format": schema}),
        api_key="supabase-access",
        base_url="https://keyservice.example",
    )

    payload = post.call_args.kwargs["json"]
    assert payload["response_format"] == schema
    assert "JSON Schema" in payload["messages"][0]["content"]
    assert '"required":["sql"]' in payload["messages"][0]["content"]


def test_preserves_keyservice_error_code(monkeypatch):
    post = MagicMock(return_value=_response(
        403,
        {"code": "HOSTED_CAP_REACHED", "detail": "Monthly limit reached"},
    ))
    monkeypatch.setattr("shared.llm_manager.hosted_glm_provider._HTTP_SESSION.post", post)

    with pytest.raises(LLMError) as exc:
        HostedGLMProvider().complete(
            _request(),
            api_key="supabase-access",
            base_url="https://keyservice.example",
        )

    assert exc.value.code == "HOSTED_CAP_REACHED"
    assert exc.value.status == 403


@pytest.mark.parametrize("status,code", [
    (429, "HOSTED_UPSTREAM_BUSY"), (504, "HOSTED_DEADLINE_EXCEEDED"),
    (502, "UPSTREAM_ERROR"), (401, "UPSTREAM_ERROR"),
])
def test_does_not_repeat_keyservice_inference_failures(monkeypatch, status, code):
    post = MagicMock(return_value=_response(status, {"code": code, "detail": "Unavailable"}))
    refresh = MagicMock()
    monkeypatch.setattr("shared.llm_manager.hosted_glm_provider._HTTP_SESSION.post", post)
    monkeypatch.setattr("shared.llm_manager.hosted_glm_provider.account_session.access_token", refresh)
    with pytest.raises(LLMError) as exc:
        HostedGLMProvider().complete(_request(), api_key="token")
    assert exc.value.code == code
    post.assert_called_once()
    refresh.assert_not_called()


def test_refreshes_only_account_auth_rejection_using_same_request_id(monkeypatch):
    post = MagicMock(side_effect=[
        _response(401, {"code": "UNAUTHORIZED", "detail": "Expired token"}), _response(),
    ])
    refresh = MagicMock(return_value="new-token")
    monkeypatch.setattr("shared.llm_manager.hosted_glm_provider._HTTP_SESSION.post", post)
    monkeypatch.setattr("shared.llm_manager.hosted_glm_provider.account_session.access_token", refresh)
    HostedGLMProvider().complete(_request(), api_key="old-token")
    assert post.call_count == 2
    assert post.call_args_list[0].kwargs["json"]["request_id"] == post.call_args_list[1].kwargs["json"]["request_id"]
    assert post.call_args_list[1].kwargs["headers"]["Authorization"] == "Bearer new-token"
    refresh.assert_called_once_with(force_refresh=True)


def test_does_not_repeat_transport_failure(monkeypatch):
    import requests

    post = MagicMock(side_effect=requests.Timeout())
    monkeypatch.setattr("shared.llm_manager.hosted_glm_provider._HTTP_SESSION.post", post)
    with pytest.raises(LLMError):
        HostedGLMProvider().complete(_request(), api_key="token")
    post.assert_called_once()


def test_serializes_hosted_requests_from_one_process(monkeypatch):
    guard = threading.Lock()
    active = 0
    max_active = 0

    def post(*_args, **_kwargs):
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with guard:
            active -= 1
        return _response()

    monkeypatch.setattr(
        "shared.llm_manager.hosted_glm_provider._HTTP_SESSION.post", post
    )
    provider = HostedGLMProvider()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _index: provider.complete(
                    _request(),
                    api_key="supabase-access",
                    base_url="https://keyservice.example",
                ),
                range(4),
            )
        )

    assert [result.text for result in results] == [
        "SELECT COUNT(*) FROM users"
    ] * 4
    assert max_active == 1


def test_strict_contracts_from_real_callers_reach_the_keyservice_unchanged(monkeypatch):
    """Callers that validate a reply strictly must have their strict schema enforced by
    the host. Ask's clarification step is the canary: a plain JSON reply that drifts from
    its schema fails every question, while the keyservice records the call as a success.
    """
    import json

    from features.ask.ambiguity_detection import detect_ambiguities

    class CapturingManager:
        def __init__(self):
            self.extra = None

        def generate_response(self, *, prompt, **kwargs):
            self.extra = kwargs.get("extra") or {}
            return {
                "response": json.dumps({
                    "ambiguities": [],
                    "total_ambiguities": 0,
                    "requires_clarification": False,
                    "can_proceed_with_assumptions": True,
                    "overall_confidence": 1.0,
                }),
                "usage": {"total_tokens": 1},
                "model": "test-model",
            }

    manager = CapturingManager()
    detect_ambiguities(
        nl_question="How many orders shipped last month?",
        filtered_schema="CREATE TABLE orders (id int, status text, created_at timestamptz);",
        database_engine="postgresql",
        llm_manager=manager,
    )
    contract = manager.extra["response_format"]
    assert contract["type"] == "json_schema"
    assert contract["json_schema"]["strict"] is True

    post = MagicMock(return_value=_response())
    monkeypatch.setattr("shared.llm_manager.hosted_glm_provider._HTTP_SESSION.post", post)
    HostedGLMProvider().complete(
        _request({"response_format": contract}),
        api_key="supabase-access",
        base_url="https://keyservice.example",
    )
    payload = post.call_args.kwargs["json"]
    assert payload["response_format"] == contract

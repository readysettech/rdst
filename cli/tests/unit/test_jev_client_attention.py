"""Local attention client contract; no Worker or provider calls."""

from unittest.mock import Mock

import pytest
import requests

from shared.llm_manager import jev_client
from shared.llm_manager.inference_attribution import count_billable_calls


def inputs():
    return {
        "request_id": "12345678-1234-1234-1234-123456789abc",
        "target_numbers": {"total_qps": 1},
        "index_changes": [],
        "open_items": [{"id": "item-1"}],
        "queries": [{"query_key": "query-1"}],
    }


def response(body, status=200, headers=None):
    result = Mock()
    result.status_code = status
    result.headers = headers or {}
    result.json.return_value = body
    return result


def verdict():
    return {
        "request_id": inputs()["request_id"], "model": "jev-1.13.0",
        "rubric_version": jev_client.ATTENTION_RUBRIC_VERSION,
        "event_scope": "single",
        "queries": [{"query_key": "query-1", "attention": "inbox",
                     "kind": "latency_regression", "severity": 0.5}],
        "open_items": [{"id": "item-1", "status": "improving"}],
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }


def test_attention_posts_versioned_bounded_request(monkeypatch):
    monkeypatch.setattr(jev_client.account_session, "access_token", lambda **kwargs: "token")
    session = Mock(spec=requests.Session)
    session.post.return_value = response(verdict())
    with count_billable_calls() as calls:
        assert jev_client.JevClient(session).attention(**inputs()) == verdict()
    assert calls.count == 1
    call = session.post.call_args
    assert call.args[0].endswith("/v1/jev/attention")
    assert call.kwargs["timeout"] == jev_client.REQUEST_TIMEOUT_SECONDS
    assert call.kwargs["json"]["attribution"]["operation"] == "monitor_attention"
    assert call.kwargs["json"]["rubric_version"] == "query-attention-v1"


def test_attention_refreshes_once_and_maps_errors(monkeypatch):
    tokens = iter(["old", "new"])
    monkeypatch.setattr(jev_client.account_session, "access_token", lambda **kwargs: next(tokens))
    session = Mock(spec=requests.Session)
    session.post.side_effect = [response({"code": "UNAUTHORIZED"}, 401), response(verdict())]
    assert jev_client.JevClient(session).attention(**inputs()) == verdict()
    assert session.post.call_count == 2
    assert session.post.call_args.kwargs["headers"]["Authorization"] == "Bearer new"

    monkeypatch.setattr(jev_client.account_session, "access_token", lambda **kwargs: "token")
    session.post.side_effect = None
    session.post.return_value = response({"code": "JEV_RATE_LIMITED"}, 429, {"Retry-After": "60"})
    with pytest.raises(jev_client.JevClientError) as error:
        jev_client.JevClient(session).attention(**inputs())
    assert (error.value.code, error.value.status, error.value.retry_after) == ("JEV_RATE_LIMITED", 429, 60)


def test_attention_rejects_oversize_and_bad_response(monkeypatch):
    monkeypatch.setattr(jev_client.account_session, "access_token", lambda **kwargs: "token")
    session = Mock(spec=requests.Session)
    large = inputs()
    large["queries"] = [{"query_key": "query-1", "normalized_sql": "x" * jev_client.MAX_REQUEST_BYTES}]
    with pytest.raises(jev_client.JevClientError) as error:
        jev_client.JevClient(session).attention(**large)
    assert error.value.code == "JEV_CONTEXT_TOO_LARGE"
    session.post.assert_not_called()

    wrong = verdict()
    wrong["queries"] = [{"query_key": "query-1", "attention": "notify",
                         "kind": "invented", "severity": 0.5}]
    session.post.return_value = response(wrong)
    with pytest.raises(jev_client.JevClientError) as error:
        jev_client.JevClient(session).attention(**inputs())
    assert error.value.code == "JEV_INVALID_RESPONSE"


def test_expired_session_maps_to_login_required(monkeypatch):
    def unavailable(**kwargs):
        raise jev_client.account_session.AccountSessionError("expired")

    monkeypatch.setattr(jev_client.account_session, "access_token", unavailable)
    with pytest.raises(jev_client.JevClientError) as error:
        jev_client.JevClient(Mock(spec=requests.Session)).attention(**inputs())
    assert error.value.code == "LOGIN_REQUIRED"

    monkeypatch.setattr(jev_client.account_session, "access_token", lambda **kwargs: "token")
    session = Mock(spec=requests.Session)
    session.post.return_value = response({"code": "UNAUTHORIZED"}, status=401)
    with pytest.raises(jev_client.JevClientError) as error:
        jev_client.JevClient(session).attention(**inputs())
    assert error.value.code == "LOGIN_REQUIRED"

    def network_failure(**kwargs):
        try:
            raise requests.ConnectionError("offline")
        except requests.ConnectionError as exc:
            raise jev_client.account_session.AccountSessionError("refresh failed") from exc

    monkeypatch.setattr(jev_client.account_session, "access_token", network_failure)
    with pytest.raises(jev_client.JevClientError) as error:
        jev_client.JevClient(session).attention(**inputs())
    assert error.value.code == "JEV_SERVICE_UNAVAILABLE"


def test_attention_rubric_is_pinned_to_worker():
    from pathlib import Path
    import sys

    source = Path(__file__).parents[2] / "keyservice" / "src"
    sys.path.insert(0, str(source))
    try:
        import jev_attention

        assert jev_client.ATTENTION_RUBRIC_VERSION == jev_attention.RUBRIC_VERSION
        assert jev_client.ATTENTION_KINDS == set(jev_attention.KINDS)
    finally:
        sys.path.remove(str(source))


def test_attention_sends_spills_and_nullable_lock_waits(monkeypatch):
    monkeypatch.setattr(jev_client.account_session, "access_token", lambda **kwargs: "token")
    session = Mock(spec=requests.Session)
    session.post.return_value = response(verdict())
    payload = inputs()
    payload["target_numbers"].update({"lock_waits": None, "lock_waits_max_5m": 4})
    payload["queries"][0].update({"current_5m": {"temp_blks": 7},
                                  "baseline": {"1h": {"temp_blks": 20}, "1d": None, "7d": None}})
    jev_client.JevClient(session).attention(**payload)
    posted = session.post.call_args.kwargs["json"]
    assert posted["target_numbers"]["lock_waits"] is None
    assert posted["target_numbers"]["lock_waits_max_5m"] == 4
    assert posted["queries"][0]["current_5m"]["temp_blks"] == 7
    assert posted["queries"][0]["baseline"]["1h"]["temp_blks"] == 20

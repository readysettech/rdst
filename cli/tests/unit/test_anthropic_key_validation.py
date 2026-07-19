"""Unit tests for Anthropic key validity checking.

Presence is not validity: a stale or mistyped key still resolves. These cover
the reason mapping and caching without a live provider by stubbing the resolved
key and the LLM ping. (Ports the key-validity probe from CL 14060, adapted to
our module layout.)
"""

from __future__ import annotations

import shared.anthropic_env as ae
from shared.llm_manager import LLMError


def _stub_key(monkeypatch, key: str = "sk-test") -> None:
    monkeypatch.setattr(ae, "get_anthropic_api_key", lambda **kw: key)


def _stub_query(monkeypatch, behavior) -> None:
    """Replace LLMManager so .query() runs `behavior` (return dict or raise)."""
    import shared.llm_manager as llm

    class _Fake:
        def query(self, **kw):
            return behavior()

    monkeypatch.setattr(llm, "LLMManager", lambda *a, **k: _Fake())


def test_no_key_returns_no_key(monkeypatch):
    ae.clear_anthropic_validity_cache()
    monkeypatch.setattr(ae, "get_anthropic_api_key", lambda **kw: None)
    assert ae.validate_anthropic_key() == {
        "valid": False,
        "reason": "no_key",
        "model": None,
    }


def test_valid_key_returns_ok(monkeypatch):
    ae.clear_anthropic_validity_cache()
    _stub_key(monkeypatch)
    _stub_query(monkeypatch, lambda: {"text": "x"})
    out = ae.validate_anthropic_key()
    assert out["valid"] is True
    assert out["reason"] == "ok"
    assert out["model"]


def test_direct_401_is_rejected(monkeypatch):
    ae.clear_anthropic_validity_cache()
    _stub_key(monkeypatch)

    def _raise():
        raise LLMError("no", code="ANTHROPIC_AUTH_INVALID", status=401)

    _stub_query(monkeypatch, _raise)
    out = ae.validate_anthropic_key()
    assert out["valid"] is False
    assert out["reason"] == "rejected"


def test_trial_proxy_401_is_rejected(monkeypatch):
    ae.clear_anthropic_validity_cache()
    _stub_key(monkeypatch)

    def _raise():
        raise LLMError("no", code="TRIAL_AUTH_INVALID", status=401)

    _stub_query(monkeypatch, _raise)
    assert ae.validate_anthropic_key()["reason"] == "rejected"


def test_other_llm_error_is_provider_error(monkeypatch):
    ae.clear_anthropic_validity_cache()
    _stub_key(monkeypatch)

    def _raise():
        raise LLMError("boom", code="PROVIDER_HTTP", status=500)

    _stub_query(monkeypatch, _raise)
    assert ae.validate_anthropic_key()["reason"] == "provider_error"


def test_result_is_cached_per_key(monkeypatch):
    ae.clear_anthropic_validity_cache()
    _stub_key(monkeypatch)
    calls = {"n": 0}

    def _count():
        calls["n"] += 1
        return {"text": "x"}

    _stub_query(monkeypatch, _count)
    ae.validate_anthropic_key()
    ae.validate_anthropic_key()
    assert calls["n"] == 1  # second call served from the cache

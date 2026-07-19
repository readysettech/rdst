"""Unit tests for the Readyset EXPLAIN CREATE CACHE verdict parser (P69).

A cacheability check that errored (e.g. a returned ``no: db error`` row from a
Readyset startup / connectivity / timeout failure) must be reported as a check
that did not complete — surfaced upstream as UNAVAILABLE — never as a definitive
"Not Cacheable / BLOCKED" verdict. A clean ``no`` stays a definitive block.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from features.cache import readyset_explain_cache as rec


@dataclass
class _FakeProc:
    returncode: int
    stdout: str
    stderr: str = ""


def _patch_stdout(monkeypatch: pytest.MonkeyPatch, stdout: str, returncode: int = 0) -> None:
    proc = _FakeProc(returncode=returncode, stdout=stdout)
    monkeypatch.setattr(rec, "_run_explain_postgres", lambda **_: proc)


def _explain(query: str = "SELECT 1") -> dict:
    return rec.explain_create_cache_readyset(
        query=query,
        readyset_port=5433,
        readyset_host="localhost",
        test_db_config={"engine": "postgresql", "database": "demo", "user": "postgres"},
        quiet=True,
    )


def test_db_error_row_is_a_failed_check_not_a_blocked_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stdout(monkeypatch, "q_abc123\tSELECT 1\tno: db error")
    result = _explain()
    # Check did not complete -> success False -> upstream renders UNAVAILABLE.
    assert result["success"] is False
    assert result.get("cacheable") is False
    # Human-readable message; the raw row text rides only in error_detail (P41).
    assert result["error"] == "Readyset could not complete the cacheability check."
    assert result["error_detail"] == "no: db error"


def test_timeout_row_is_a_failed_check(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stdout(monkeypatch, "q_abc123\tSELECT 1\tno: connection timed out")
    result = _explain()
    assert result["success"] is False


def test_clean_no_is_a_definitive_block(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stdout(monkeypatch, "q_abc123\tSELECT 1\tno")
    result = _explain()
    # A clean, completed check with a definitive "not supported" answer.
    assert result["success"] is True
    assert result["cacheable"] is False
    assert result["confidence"] == "high"


def test_yes_is_cacheable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stdout(monkeypatch, "q_abc123\tSELECT 1\tyes")
    result = _explain()
    assert result["success"] is True
    assert result["cacheable"] is True


def test_bare_error_line_is_a_failed_check(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stdout(monkeypatch, "ERROR: server closed the connection unexpectedly")
    result = _explain()
    assert result["success"] is False

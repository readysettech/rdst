"""Tests for the shared AI credential preflight."""

from __future__ import annotations

from dataclasses import dataclass

from shared.ai_access import AIAccessCheck, AIAccessState
from shared.llm_manager.base import LLMError
from shared.llm_manager.key_resolution import KeyResolution


def test_check_ai_access_reports_readyset(monkeypatch):
    from shared import ai_access

    monkeypatch.setattr(
        ai_access,
        "resolve_api_key",
        lambda provider: KeyResolution(
            api_key="account-token",
            is_trial=False,
            provider="readyset",
        ),
    )

    result = ai_access.check_ai_access()

    assert result.ok
    assert result.state is AIAccessState.READYSET


def test_check_ai_access_reports_claude(monkeypatch):
    from shared import ai_access

    monkeypatch.setattr(
        ai_access,
        "resolve_api_key",
        lambda provider: KeyResolution(
            api_key="anthropic-key",
            is_trial=False,
            provider="claude",
        ),
    )

    result = ai_access.check_ai_access()

    assert result.ok
    assert result.state is AIAccessState.CLAUDE


def test_missing_access_names_both_setup_options(monkeypatch):
    from shared import ai_access

    def missing(_provider):
        raise LLMError("missing", code="LOGIN_REQUIRED")

    monkeypatch.setattr(ai_access, "resolve_api_key", missing)

    result = ai_access.check_ai_access()

    assert result.state is AIAccessState.MISSING
    assert result.code == "LOGIN_REQUIRED"
    assert "rdst account login" in result.message
    assert "ANTHROPIC_API_KEY" in result.message


def test_claude_only_access_does_not_offer_readyset_login(monkeypatch):
    from shared import ai_access

    def missing(_provider):
        raise LLMError("missing", code="NO_API_KEY")

    monkeypatch.setattr(ai_access, "resolve_api_key", missing)

    result = ai_access.check_ai_access(require_claude=True)

    assert result.state is AIAccessState.CLAUDE_REQUIRED
    assert result.code == "ANTHROPIC_KEY_REQUIRED"
    assert "ANTHROPIC_API_KEY" in result.message
    assert "rdst account login" not in result.message


def test_transient_session_error_is_not_reported_as_logged_out(monkeypatch):
    from shared import ai_access

    def unavailable(_provider):
        raise RuntimeError("keyservice unavailable")

    monkeypatch.setattr(ai_access, "resolve_api_key", unavailable)

    result = ai_access.check_ai_access()

    assert result.state is AIAccessState.ERROR
    assert result.code == "AI_ACCESS_UNAVAILABLE"
    assert "keyservice unavailable" in result.message
    assert "rdst account login" not in result.message


@dataclass
class _TTY:
    interactive: bool

    def isatty(self) -> bool:
        return self.interactive


def test_noninteractive_preflight_never_prompts(monkeypatch):
    from shared.cli import ai_access as cli_ai_access

    missing = AIAccessCheck(
        AIAccessState.MISSING,
        message="missing",
        code="LOGIN_REQUIRED",
    )
    monkeypatch.setattr(cli_ai_access, "check_ai_access", lambda **_kwargs: missing)

    result = cli_ai_access.ensure_cli_ai_access(
        stdin=_TTY(False),
        prompt_fn=lambda _prompt: (_ for _ in ()).throw(AssertionError("prompted")),
    )

    assert result is missing


def test_noninteractive_environment_never_prompts_on_tty(monkeypatch):
    from shared.cli import ai_access as cli_ai_access

    missing = AIAccessCheck(
        AIAccessState.MISSING,
        message="missing",
        code="LOGIN_REQUIRED",
    )
    monkeypatch.setattr(cli_ai_access, "check_ai_access", lambda **_kwargs: missing)
    monkeypatch.setenv("RDST_NON_INTERACTIVE", "1")

    result = cli_ai_access.ensure_cli_ai_access(
        stdin=_TTY(True),
        prompt_fn=lambda _prompt: (_ for _ in ()).throw(AssertionError("prompted")),
        login_runner=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("login")),
    )

    assert result is missing


def test_access_check_failure_never_starts_login(monkeypatch):
    from shared.cli import ai_access as cli_ai_access

    unavailable = AIAccessCheck(
        AIAccessState.ERROR,
        message="keyring unavailable",
        code="AI_ACCESS_UNAVAILABLE",
    )
    monkeypatch.setattr(cli_ai_access, "check_ai_access", lambda **_kwargs: unavailable)

    result = cli_ai_access.ensure_cli_ai_access(
        stdin=_TTY(True),
        prompt_fn=lambda _prompt: (_ for _ in ()).throw(AssertionError("prompted")),
        login_runner=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("login")),
    )

    assert result is unavailable


def test_interactive_preflight_logs_in_and_rechecks(monkeypatch):
    from shared.cli import ai_access as cli_ai_access

    missing = AIAccessCheck(
        AIAccessState.MISSING,
        message="missing",
        code="LOGIN_REQUIRED",
    )
    ready = AIAccessCheck(AIAccessState.READYSET)
    checks = iter([missing, ready])
    monkeypatch.setattr(
        cli_ai_access, "check_ai_access", lambda **_kwargs: next(checks)
    )
    opened: list[str] = []

    def login_runner(*, on_ready):
        on_ready("http://127.0.0.1:43210/account-login")
        opened.append("login")
        return "http://127.0.0.1:43210/account-login"

    result = cli_ai_access.ensure_cli_ai_access(
        stdin=_TTY(True),
        prompt_fn=lambda _prompt: "",
        login_runner=login_runner,
    )

    assert result is ready
    assert opened == ["login"]


def test_claude_only_preflight_never_starts_readyset_login(monkeypatch):
    from shared.cli import ai_access as cli_ai_access

    required = AIAccessCheck(
        AIAccessState.CLAUDE_REQUIRED,
        message="claude required",
        code="ANTHROPIC_KEY_REQUIRED",
    )
    monkeypatch.setattr(cli_ai_access, "check_ai_access", lambda **_kwargs: required)

    result = cli_ai_access.ensure_cli_ai_access(
        require_claude=True,
        stdin=_TTY(True),
        prompt_fn=lambda _prompt: (_ for _ in ()).throw(AssertionError("prompted")),
        login_runner=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("login")),
    )

    assert result is required

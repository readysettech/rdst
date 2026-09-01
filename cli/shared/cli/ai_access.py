"""Shared CLI preflight for commands that invoke an LLM."""

from __future__ import annotations

import os
import sys
from typing import Any, Callable

from shared.ai_access import (
    AIAccessCheck,
    AIAccessState,
    ai_access_instructions,
    check_ai_access,
    claude_required_instructions,
)


def ensure_cli_ai_access(
    *,
    require_claude: bool = False,
    allow_login_prompt: bool = True,
    console: Any | None = None,
    stdin: Any | None = None,
    prompt_fn: Callable[[str], str] | None = None,
    login_runner: Callable[..., str] | None = None,
) -> AIAccessCheck:
    """Check access and optionally offer browser sign-in on an interactive TTY."""

    result = check_ai_access(require_claude=require_claude)
    if (
        result.ok
        or result.state is not AIAccessState.MISSING
        or require_claude
        or not allow_login_prompt
    ):
        return result

    input_stream = stdin if stdin is not None else sys.stdin
    if os.getenv("RDST_NON_INTERACTIVE"):
        return result
    if not getattr(input_stream, "isatty", lambda: False)():
        return result

    ask = prompt_fn or input
    try:
        answer = ask("AI access is required. Sign in to Readyset now? [Y/n]: ")
    except (EOFError, KeyboardInterrupt):
        return result
    if answer.strip().lower() not in {"", "y", "yes"}:
        return result

    if login_runner is None:
        from features.account.browser_login import run_browser_login

        login_runner = run_browser_login

    def show_url(url: str) -> None:
        message = f"Open this RDST sign-in page:\n{url}"
        if console is not None:
            console.print(message)
        else:
            print(message)

    try:
        login_runner(on_ready=show_url)
    except Exception as exc:
        return AIAccessCheck(
            state=AIAccessState.ERROR,
            message=f"Readyset sign-in failed: {exc}",
            code="LOGIN_FAILED",
        )
    return check_ai_access()


__all__ = [
    "AIAccessCheck",
    "AIAccessState",
    "ai_access_instructions",
    "check_ai_access",
    "claude_required_instructions",
    "ensure_cli_ai_access",
]

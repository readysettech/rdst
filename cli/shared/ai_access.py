"""Credential preflight shared by AI-backed RDST features."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from shared.llm_manager.base import LLMError
from shared.llm_manager.key_resolution import KeyResolution, resolve_api_key
from shared.shell import environment_assignment


class AIAccessState(str, Enum):
    """Credential state relevant to an inference operation."""

    READYSET = "readyset"
    CLAUDE = "claude"
    MISSING = "missing"
    CLAUDE_REQUIRED = "claude_required"
    ERROR = "error"


@dataclass(frozen=True)
class AIAccessCheck:
    state: AIAccessState
    message: str = ""
    code: str | None = None
    resolution: KeyResolution | None = None

    @property
    def ok(self) -> bool:
        return self.state in {AIAccessState.READYSET, AIAccessState.CLAUDE}


def ai_access_instructions() -> str:
    return (
        "AI access is required.\n\n"
        "Sign in for Readyset-hosted AI:\n"
        "  rdst account login\n\n"
        "Or use your own Anthropic key:\n"
        f"  {environment_assignment('ANTHROPIC_API_KEY', 'sk-ant-...')}"
    )


def claude_required_instructions() -> str:
    return (
        "This command requires your own Anthropic API key because its "
        "tool-calling mode is not supported by Readyset-hosted AI.\n\n"
        "Set your key and run the command again:\n"
        f"  {environment_assignment('ANTHROPIC_API_KEY', 'sk-ant-...')}"
    )


def check_ai_access(*, require_claude: bool = False) -> AIAccessCheck:
    """Resolve AI access without making a paid provider request."""

    try:
        resolution = resolve_api_key("claude" if require_claude else "auto")
    except LLMError as exc:
        if require_claude:
            return AIAccessCheck(
                state=AIAccessState.CLAUDE_REQUIRED,
                message=claude_required_instructions(),
                code="ANTHROPIC_KEY_REQUIRED",
            )
        return AIAccessCheck(
            state=AIAccessState.MISSING,
            message=ai_access_instructions(),
            code=exc.code or "LOGIN_REQUIRED",
        )
    except Exception as exc:  # A stale session or keyring failure is not "logged out".
        return AIAccessCheck(
            state=AIAccessState.ERROR,
            message=f"Could not check AI access: {exc}",
            code="AI_ACCESS_UNAVAILABLE",
        )

    state = (
        AIAccessState.READYSET
        if resolution.provider == "readyset"
        else AIAccessState.CLAUDE
    )
    return AIAccessCheck(state=state, resolution=resolution)


__all__ = [
    "AIAccessCheck",
    "AIAccessState",
    "ai_access_instructions",
    "check_ai_access",
    "claude_required_instructions",
]

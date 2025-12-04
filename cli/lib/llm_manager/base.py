from __future__ import annotations
from dataclasses import dataclass, field, replace
from typing import List, Optional, Protocol, Any, Dict, Sequence


class Provider(Protocol):
    def default_model(self) -> str: ...
    def complete(
        self, request: "ProviderRequest", *, api_key: str, debug: bool = False
    ) -> "ProviderResponse": ...


class LLMError(Exception):
    def __init__(
        self,
        message: str,
        code: str = "LLM_ERROR",
        status: Optional[int] = None,
        cause: Optional[Exception] = None,
    ):
        super().__init__(message)
        self.code = code
        self.status = status
        self.cause = cause


@dataclass
class ProviderMessage:
    # "system" | "user" | "assistant"
    role: str
    content: str


@dataclass
class ProviderRequest:
    model: str
    messages: List[ProviderMessage]
    max_tokens: Optional[int] = None
    temperature: float = 0.2
    top_p: Optional[float] = None
    stop_sequences: Optional[Sequence[str]] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_chat_dicts(self) -> List[Dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in self.messages]


@dataclass
class ProviderResponse:
    text: str
    usage: Optional[Dict[str, Optional[int]]] = None
    raw: Optional[Dict[str, Any]] = None


@dataclass
class Conversation:
    provider: Provider
    api_key: str
    model: Optional[str] = None
    debug: bool = False
    messages: List[ProviderMessage] = field(default_factory=list)

    def reset(self) -> None:
        self.messages.clear()

    def with_model(self, model: str) -> "Conversation":
        return replace(self, model=model)

    def extend(self, msgs: Sequence[ProviderMessage]) -> None:
        self.messages.extend(msgs)

    def system(self, text: str) -> None:
        self.messages.append(ProviderMessage(role="system", content=text))

    def user(self, text: str) -> None:
        self.messages.append(ProviderMessage(role="user", content=text))

    def assistant(self, text: str) -> None:
        self.messages.append(ProviderMessage(role="assistant", content=text))

    def complete(
        self,
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        stop_sequences: Optional[Sequence[str]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> ProviderResponse:
        req = ProviderRequest(
            model=(self.model or self.provider.default_model()),
            messages=list(self.messages),
            max_tokens=max_tokens,
            temperature=temperature if temperature is not None else 0.2,
            top_p=top_p,
            stop_sequences=stop_sequences,
            extra=extra or {},
        )
        try:
            resp = self.provider.complete(req, api_key=self.api_key, debug=self.debug)
        except Exception as e:
            raise LLMError("Provider call failed", cause=e)

        self.assistant(resp.text)
        return resp
    

@dataclass
class LLMDefaults:
    # Claude is the only supported provider (BYOK with ANTHROPIC_API_KEY)
    provider: str = "claude"
    model: Optional[str] = None
    max_tokens: int = 800
    temperature: float = 0.2
    top_p: Optional[float] = None
    stop_sequences: Optional[Sequence[str]] = None
    debug: bool = False
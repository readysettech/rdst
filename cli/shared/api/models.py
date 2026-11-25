from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class AnalyzeRequest(BaseModel):
    query: str
    target: Optional[str] = None
    fast: bool = False
    skip_rewrites: bool = False
    readyset_cache: bool = False
    skip_readyset: bool = False
    skip_storage: bool = False
    model: Optional[str] = None


class AnalyzeProgress(BaseModel):
    stage: str
    percent: int
    message: Optional[str] = None


class AnalyzeResult(BaseModel):
    success: bool
    analysis_id: Optional[str] = None
    query_hash: Optional[str] = None
    explain_results: Optional[dict[str, Any]] = None
    llm_analysis: Optional[dict[str, Any]] = None
    rewrite_testing: Optional[dict[str, Any]] = None
    readyset_cacheability: Optional[dict[str, Any]] = None
    formatted: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    failed_stage: Optional[str] = None


class InteractiveMessageRequest(BaseModel):
    """Request to send a message in interactive mode."""

    message: str
    continue_existing: bool = True
    analysis_results: Optional[dict[str, Any]] = None


class ConversationStatusResponse(BaseModel):
    """Response for conversation status check."""

    exists: bool
    conversation_id: Optional[str] = None
    message_count: Optional[int] = None
    total_exchanges: Optional[int] = None
    started_at: Optional[str] = None
    last_updated: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None


class MessageResponse(BaseModel):
    """Single message in conversation history."""

    role: str
    content: str
    timestamp: str


class ConversationHistoryResponse(BaseModel):
    """Response for conversation history."""

    messages: list[MessageResponse]


class DeleteResponse(BaseModel):
    """Response for delete operations."""

    success: bool


class AskClarificationAnswer(BaseModel):
    """A clarification answered by the user or assumed by Ask, as an answer reports it."""

    id: str = Field(min_length=1)
    term: str
    question: str
    # Empty only for a point Ask left to the SQL writer when it answered first.
    answer: str
    options: list[str] = Field(default_factory=list)
    hints: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    multiple: bool = False
    assumed: bool = False
    # Changes only which columns show or how rows are ordered.
    minor: bool = False

    @model_validator(mode="after")
    def _answered_unless_assumed(self) -> "AskClarificationAnswer":
        if not self.assumed and not self.answer.strip():
            raise ValueError("answer must not be empty")
        return self


class AskRequest(BaseModel):
    """Request for text-to-SQL conversion."""

    question: str
    target: Optional[str] = None
    dry_run: bool = False
    timeout: int = 30
    session_id: Optional[str] = None
    selected_interpretation_id: Optional[int] = None
    clarification_answers: Optional[dict[str, str]] = None
    # Answers from an earlier run of the same question, applied in place of asking again.
    clarifications: Optional[list[AskClarificationAnswer]] = None
    # Answer with Ask's best guesses and list them, instead of asking first.
    answer_first: bool = False


__all__ = [
    "AnalyzeProgress",
    "AnalyzeRequest",
    "AnalyzeResult",
    "AskClarificationAnswer",
    "AskRequest",
    "ConversationHistoryResponse",
    "ConversationStatusResponse",
    "DeleteResponse",
    "InteractiveMessageRequest",
    "MessageResponse",
]

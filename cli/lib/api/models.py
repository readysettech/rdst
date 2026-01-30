from pydantic import BaseModel
from typing import Optional, Any


class AnalyzeRequest(BaseModel):
    query: str
    fast: bool = False
    skip_rewrites: bool = False
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


# ============================================================================
# Interactive Mode Models
# ============================================================================


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


# ============================================================================
# Ask Mode Models
# ============================================================================


class AskRequest(BaseModel):
    """Request for text-to-SQL conversion."""

    question: str
    dry_run: bool = False
    timeout: int = 30
    agent_mode: bool = False
    # For resuming after clarification
    session_id: Optional[str] = None
    selected_interpretation_id: Optional[int] = None
    clarification_answers: Optional[dict[str, str]] = None

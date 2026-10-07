"""Response shapes of /api/agent-activity."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

AgentAction = Literal["deep_analysis", "ask", "classification", "attention_check",
                      "daily_snapshot", "explain_actions", "benchmark"]
AgentOutcome = Literal["done", "failed", "skipped", "cancelled"]
AgentFold = Literal["attention_check", "classification", "skipped_analysis"]
AgentGroup = Literal["day", "hour", "none"]


class AgentActivityItem(BaseModel):
    """One line of the log: a single agent decision, or the attention checks,
    classifications or skipped deep analyses of one database folded into one line per
    day or hour (`kind: rollup`)."""

    key: str
    kind: Literal["entry", "rollup"]
    id: int
    ts: int
    first_ts: int
    count: int
    done: int
    failed: int
    skipped: int
    target: str
    action: AgentAction
    subject: str
    outcome: AgentOutcome
    summary: str
    source: Literal["live", "backfill"]
    statement_count: int
    ai_call_count: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    items_filed: int
    # A single entry: what it decided and what it was about.
    decision: Optional[str] = None
    question: Optional[str] = None
    query_key: Optional[str] = None
    analysis_ref: Optional[str] = None
    report_id: Optional[int] = None
    period_start: Optional[int] = Field(None, description="The day a daily report covers")
    period_end: Optional[int] = None
    parameters: Optional[str] = None
    note: Optional[str] = None
    finding: Optional[str] = Field(
        None, description="A finished deep analysis's key finding, such as 'suggested an "
                          "index on events(event_type, created_at)'")
    # A folded line: what it holds and the local day or hour it covers.
    fold: Optional[AgentFold] = None
    group: Optional[AgentGroup] = None
    bucket_start: Optional[int] = None
    bucket_end: Optional[int] = None


class AgentActivityPage(BaseModel):
    items: list[AgentActivityItem]
    next_cursor: Optional[str] = None
    targets: list[str]


class AgentStatement(BaseModel):
    """A statement RDST ran on the database. Literals are replaced by `?`; parameters
    and results are never kept."""

    sql: str
    kind: str
    executions: int
    duration_ms: Optional[float] = None
    max_ms: Optional[float] = None
    timeout_ms: Optional[int] = None
    error: Optional[str] = None


class AgentAiCall(BaseModel):
    ts: Optional[int] = None
    provider: str
    model: str
    operation: str
    purpose: str
    data_kinds: list[str]
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cost_usd: Optional[float] = None
    cost_estimated: bool
    duration_ms: Optional[float] = None
    request_id: Optional[str] = None
    error: Optional[str] = None


class AgentActivityDetail(BaseModel):
    id: int
    ts: int
    finished_at: Optional[int] = None
    target: str
    action: AgentAction
    subject: str
    outcome: AgentOutcome
    summary: str
    decision: Optional[str] = None
    details: dict[str, Any]
    run_ref: Optional[str] = None
    source: Literal["live", "backfill"]
    statement_count: int
    ai_call_count: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    items_filed: int
    statements: list[AgentStatement]
    ai_calls: list[AgentAiCall]

"""
RDST Report API Route

Allows web users to submit feedback about RDST analysis results.
Feedback is sent to PostHog for analytics and Slack for immediate visibility.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


class ReportRequest(BaseModel):
    """Request body for submitting feedback."""

    reason: str  # Required - feedback text
    sentiment: str = "neutral"  # "positive" | "negative" | "neutral"
    query_hash: Optional[str] = None  # Optional - reference a specific query
    email: Optional[str] = None  # Optional - for follow-up
    include_query: bool = False  # Whether to include raw SQL
    include_plan: bool = False  # Whether to include execution plan


class ReportResponse(BaseModel):
    """Response from feedback submission."""

    success: bool
    error: Optional[str] = None


@router.post("/report")
async def submit_report(request: ReportRequest) -> ReportResponse:
    """
    Submit user feedback.

    Feedback is sent to PostHog for analytics and Slack for immediate visibility.
    If a query_hash is provided, the query context is loaded from the registry.
    """
    try:
        from lib.telemetry import telemetry

        # Load query context if hash provided
        query_sql = None
        plan_json = None
        suggestion_text = None

        if request.query_hash:
            query_sql, plan_json, suggestion_text = _load_query_context(
                request.query_hash
            )

        # Submit feedback via telemetry
        telemetry.submit_feedback(
            reason=request.reason,
            query_hash=request.query_hash,
            query_sql=query_sql,
            plan_json=plan_json,
            suggestion_text=suggestion_text,
            sentiment=request.sentiment,
            email=request.email,
            include_query=request.include_query,
            include_plan=request.include_plan,
            flags_used=["web"],  # Indicate this came from web UI
        )

        return ReportResponse(success=True)

    except Exception as e:
        return ReportResponse(success=False, error=str(e))


def _load_query_context(query_hash: str):
    """Load query context from registry."""
    try:
        from lib.query_registry.query_registry import QueryRegistry

        registry = QueryRegistry()

        # Find the query (supports both exact hash and prefix matching)
        entry = registry.get_query(query_hash)
        if not entry:
            return None, None, None

        query_sql = entry.sql  # Parameterized SQL with ? placeholders

        # Note: Analysis results (suggestions, plans) aren't currently persisted
        # Just return the query SQL for now
        return query_sql, None, None

    except Exception:
        return None, None, None

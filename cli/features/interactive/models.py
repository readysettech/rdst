"""Interactive models.

This slice owns follow-up Q&A conversations about a specific query analysis.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class InteractiveRequest:
    query_hash: str
    message: str
    continue_existing: bool = False


@dataclass
class InteractiveResponse:
    response_text: str
    conversation_id: str
    error: str | None = None

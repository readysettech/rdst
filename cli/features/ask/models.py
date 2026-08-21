"""Ask feature models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class AskPhase(str, Enum):
    SCHEMA = "schema"
    CLARIFY = "clarify"
    GENERATE = "generate"
    VALIDATE = "validate"
    EXECUTE = "execute"
    CONFIG = "config"


@dataclass
class AskInput:
    """Input for ask service."""

    question: str
    target: Optional[str] = None
    source: str = "cli"
    provided_context: str = ""
    matched_database_values: str = ""


@dataclass
class AskOptions:
    """Options for ask service execution."""

    dry_run: bool = False
    timeout_seconds: int = 30
    max_rows: int = 100
    verbose: bool = False
    no_interactive: bool = False
    enforce_result_limit: bool = True
    persist_query: bool = True
    raise_unexpected_errors: bool = False


@dataclass
class AskInterpretation:
    """A possible interpretation of the user's question."""

    id: int
    description: str
    likelihood: float
    assumptions: list[str]


@dataclass
class AskClarificationQuestion:
    """A clarification question for the user."""

    id: str
    question: str
    options: list[str]


@dataclass
class AskResult:
    """Final ask result payload."""

    success: bool
    sql: str
    rows: list[Any]
    columns: list[str]
    row_count: int
    execution_time_ms: float
    llm_calls: int
    total_tokens: int
    query_hash: str = ""
    query_tag: str = ""
    limit_added: bool = False

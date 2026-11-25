"""Ask feature models."""

from __future__ import annotations

from dataclasses import dataclass, field
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
class AskClarification:
    """A clarification question answered for a question, by the user or, when Ask answers
    first, by Ask's best guess."""

    id: str
    term: str
    question: str
    answer: str
    # What the question offered, so an answer can be changed without asking again.
    options: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    multiple: bool = False
    # Ask chose this answer itself; an empty answer leaves the point to the SQL writer.
    assumed: bool = False
    # Changes only which columns show or how rows are ordered.
    minor: bool = False


@dataclass
class AskInput:
    """Input for ask service."""

    question: str
    target: Optional[str] = None
    source: str = "cli"
    provided_context: str = ""
    matched_database_values: str = ""
    # Answers already given for this question, as an answer reports them; asking with them
    # applies them instead of asking for clarification again.
    clarifications: list[AskClarification] = field(default_factory=list)


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
    # Answer with Ask's best guess for each clarifying question instead of asking first;
    # the result lists those guesses so the user can change them.
    answer_first: bool = False


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
    # One per option when present: the schema identifier the option reads, with an example
    # value when Ask could read one, for users who know the schema.
    hints: list[str] = field(default_factory=list)
    # Suggested answers to a question without options; the user may pick one or type
    # their own.
    examples: list[str] = field(default_factory=list)
    # The user may pick several options; the answer lists them separated by "; ".
    multiple: bool = False


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
    limit_reduced: bool = False
    truncated: bool = False
    row_limit: Optional[int] = None
    monitor_key: Optional[str] = None

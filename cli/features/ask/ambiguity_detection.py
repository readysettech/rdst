"""
Ambiguity detection helpers shared across ask flows.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Dict, List

from shared.ui import (
    MessagePanel,
    Prompt,
    SectionHeader,
    SelectionTable,
    StyleTokens,
    get_console,
)

from .prompts.ask_prompts import format_provided_context_block
from .prompts.ask_prompts_v2 import (
    AMBIGUITY_DETECTION_PROMPT,
    AMBIGUITY_DETECTION_RESPONSE_SCHEMA,
    format_preference_tree_for_prompt,
)

logger = logging.getLogger(__name__)

RANKED_RESOLVER_POLICY = "ranked-v3-safe-abstention"
NON_INTERACTIVE_CLARIFICATION_POLICY = "deterministic-intent-stop-v2-no-llm-detector"
RANKED_RESOLVER_MIN_SCORE = 0.90
RANKED_RESOLVER_MIN_MARGIN = 0.20
CUMULATIVE_MEDIUM_AMBIGUITY_COUNT = 2
CUMULATIVE_AMBIGUITY_CONFIDENCE_CEILING = 0.85
MEDIUM_AMBIGUITY_MIN_PROCEED_SCORE = 0.70
MEDIUM_AMBIGUITY_MIN_PROCEED_MARGIN = 0.20
AMBIGUITY_RESPONSE_MAX_TOKENS = 1600
MAX_MATERIAL_AMBIGUITIES = 3

_UNDIRECTED_SORT_PATTERN = re.compile(
    r"\b(?P<verb>sort(?:ed)?|order(?:ed)?)\s+by\s+"
    r"(?P<key>[^?.;,]+)",
    re.IGNORECASE,
)
_SORT_DIRECTION_PATTERN = re.compile(
    r"\b(?:asc(?:ending)?|desc(?:ending)?|increasing|decreasing|"
    r"highest|lowest|latest|earliest|newest|oldest|most|least|top|bottom|"
    r"high(?:est)?\s+to\s+low(?:est)?|low(?:est)?\s+to\s+high(?:est)?|"
    r"reverse\s+chronological(?:ly)?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AmbiguityOption:
    """A stable, scored interpretation proposed by the detector."""

    id: str
    text: str
    score: float
    evidence: List[str] = field(default_factory=list)
    sql_effect: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.text.strip():
            raise ValueError("ambiguity option id and text must be non-empty")
        if not math.isfinite(self.score) or not 0.0 <= self.score <= 1.0:
            raise ValueError("ambiguity option score must be between 0 and 1")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "score": self.score,
            "evidence": list(self.evidence),
            "sql_effect": self.sql_effect,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AmbiguityOption":
        if set(data) != {"id", "text", "score", "evidence", "sql_effect"}:
            raise ValueError("ambiguity option has unexpected fields")
        if not all(
            isinstance(data[field], str) for field in ("id", "text", "sql_effect")
        ):
            raise ValueError("ambiguity option text fields must be strings")
        if isinstance(data["score"], bool) or not isinstance(
            data["score"], (int, float)
        ):
            raise ValueError("ambiguity option score must be numeric")
        if not isinstance(data["evidence"], list) or not all(
            isinstance(item, str) for item in data["evidence"]
        ):
            raise ValueError("ambiguity option evidence must be strings")
        return cls(
            id=data["id"],
            text=data["text"],
            score=float(data["score"]),
            evidence=list(data["evidence"]),
            sql_effect=data["sql_effect"],
        )


@dataclass
class Ambiguity:
    """Single detected ambiguity."""

    id: str
    category: str
    term: str
    reason: str
    possible_interpretations: List[AmbiguityOption]
    clarifying_question: str
    priority: str = "medium"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "term": self.term,
            "reason": self.reason,
            "possible_interpretations": [
                option.to_dict() for option in self.possible_interpretations
            ],
            "clarifying_question": self.clarifying_question,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Ambiguity":
        if not all(
            isinstance(data[field], str)
            for field in (
                "id",
                "category",
                "term",
                "reason",
                "clarifying_question",
                "priority",
            )
        ):
            raise ValueError("ambiguity text fields must be strings")
        if not isinstance(data["possible_interpretations"], list):
            raise ValueError("possible_interpretations must be an array")
        return cls(
            id=data["id"],
            category=data["category"],
            term=data["term"],
            reason=data["reason"],
            possible_interpretations=[
                AmbiguityOption.from_dict(option)
                for option in data["possible_interpretations"]
            ],
            clarifying_question=data["clarifying_question"],
            priority=data.get("priority", "medium"),
        )


@dataclass
class AmbiguityReport:
    """Complete ambiguity detection report."""

    ambiguities: List[Ambiguity] = field(default_factory=list)
    total_ambiguities: int = 0
    requires_clarification: bool = False
    can_proceed_with_assumptions: bool = True
    overall_confidence: float = 1.0

    def get_high_priority(self) -> List[Ambiguity]:
        return [a for a in self.ambiguities if a.priority == "high"]

    def get_medium_priority(self) -> List[Ambiguity]:
        return [a for a in self.ambiguities if a.priority == "medium"]

    def get_low_priority(self) -> List[Ambiguity]:
        return [a for a in self.ambiguities if a.priority == "low"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ambiguities": [a.to_dict() for a in self.ambiguities],
            "total_ambiguities": self.total_ambiguities,
            "requires_clarification": self.requires_clarification,
            "can_proceed_with_assumptions": self.can_proceed_with_assumptions,
            "overall_confidence": self.overall_confidence,
        }


def clarification_required_for_report(report: AmbiguityReport) -> bool:
    """Apply deterministic safety rules to the detector's clarification flags."""
    if not report.ambiguities:
        return False
    if report.requires_clarification or not report.can_proceed_with_assumptions:
        return True
    if report.get_high_priority():
        return True
    if (
        len(report.get_medium_priority()) >= CUMULATIVE_MEDIUM_AMBIGUITY_COUNT
        and report.overall_confidence < CUMULATIVE_AMBIGUITY_CONFIDENCE_CEILING
    ):
        return True
    return any(
        resolve_ranked_ambiguity(
            ambiguity,
            min_score=MEDIUM_AMBIGUITY_MIN_PROCEED_SCORE,
            min_margin=MEDIUM_AMBIGUITY_MIN_PROCEED_MARGIN,
        ).action
        == "abstain"
        for ambiguity in report.get_medium_priority()
    )


@dataclass(frozen=True)
class RankedResolution:
    """Auditable outcome of the provisional ranked resolver."""

    ambiguity_id: str
    action: str
    selected_option_id: str | None
    selected_text: str | None
    top_score: float
    runner_up_score: float
    margin: float
    reason: str
    policy: str = RANKED_RESOLVER_POLICY

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ambiguity_id": self.ambiguity_id,
            "action": self.action,
            "selected_option_id": self.selected_option_id,
            "selected_text": self.selected_text,
            "top_score": self.top_score,
            "runner_up_score": self.runner_up_score,
            "margin": self.margin,
            "reason": self.reason,
            "policy": self.policy,
        }


def resolve_ranked_ambiguity(
    ambiguity: Ambiguity,
    *,
    min_score: float = RANKED_RESOLVER_MIN_SCORE,
    min_margin: float = RANKED_RESOLVER_MIN_MARGIN,
) -> RankedResolution:
    """Select only a strongly ranked option; ties and weak rankings abstain."""
    ranked = sorted(
        ambiguity.possible_interpretations,
        key=lambda option: (-option.score, option.id),
    )
    if len(ranked) < 2:
        return RankedResolution(
            ambiguity_id=ambiguity.id,
            action="abstain",
            selected_option_id=None,
            selected_text=None,
            top_score=ranked[0].score if ranked else 0.0,
            runner_up_score=0.0,
            margin=0.0,
            reason="fewer_than_two_options",
        )

    top, runner_up = ranked[:2]
    margin = top.score - runner_up.score
    if top.score < min_score:
        reason = "top_score_below_threshold"
    elif margin < min_margin:
        reason = "ranking_margin_below_threshold"
    else:
        return RankedResolution(
            ambiguity_id=ambiguity.id,
            action="select",
            selected_option_id=top.id,
            selected_text=top.text,
            top_score=top.score,
            runner_up_score=runner_up.score,
            margin=margin,
            reason="provisional_rank_gate_passed",
        )
    return RankedResolution(
        ambiguity_id=ambiguity.id,
        action="abstain",
        selected_option_id=None,
        selected_text=None,
        top_score=top.score,
        runner_up_score=runner_up.score,
        margin=margin,
        reason=reason,
    )


def detect_missing_intent_ambiguities(nl_question: str) -> List[Ambiguity]:
    """Find explicit operations whose user-facing parameters are missing."""
    match = _UNDIRECTED_SORT_PATTERN.search(nl_question)
    if match is None or _SORT_DIRECTION_PATTERN.search(nl_question):
        return []

    key = " ".join(match.group("key").split()).strip()
    term = " ".join(match.group(0).split()).strip()
    if not key:
        return []
    return [
        Ambiguity(
            id="intent-sort-direction",
            category="missing_sql_keywords",
            term=term,
            reason="The requested sort does not state which direction to use.",
            possible_interpretations=[
                AmbiguityOption(
                    id="intent-sort-descending",
                    text="Sort from highest to lowest in descending order.",
                    score=0.5,
                    evidence=[f'"{term}" has no direction.'],
                    sql_effect=f"ORDER BY {key} DESC.",
                ),
                AmbiguityOption(
                    id="intent-sort-ascending",
                    text="Sort from lowest to highest in ascending order.",
                    score=0.5,
                    evidence=[f'"{term}" has no direction.'],
                    sql_effect=f"ORDER BY {key} ASC.",
                ),
            ],
            clarifying_question=(
                "Should the results be sorted from highest to lowest or lowest to "
                f"highest by {key}?"
            ),
            priority="medium",
        )
    ]


def _merge_intent_ambiguities(
    detected: List[Ambiguity],
    deterministic: List[Ambiguity],
) -> tuple[List[Ambiguity], List[Dict[str, Any]]]:
    merged = list(detected)
    normalizations = []
    for candidate in deterministic:
        if any(_ambiguity_covers_sort_direction(item) for item in merged):
            continue
        merged.append(candidate)
        normalizations.append(
            {
                "field": "ambiguities",
                "action": "add_deterministic_missing_intent",
                "ambiguity_id": candidate.id,
            }
        )

    priority = {"high": 0, "medium": 1, "low": 2}
    merged.sort(
        key=lambda ambiguity: (
            priority.get(ambiguity.priority, 3),
            0 if ambiguity.id.startswith("intent-") else 1,
        )
    )
    if len(merged) > MAX_MATERIAL_AMBIGUITIES:
        removed = merged[MAX_MATERIAL_AMBIGUITIES:]
        merged = merged[:MAX_MATERIAL_AMBIGUITIES]
        normalizations.append(
            {
                "field": "ambiguities",
                "action": "cap_material_ambiguities",
                "removed_ids": [item.id for item in removed],
            }
        )
    return merged, normalizations


def _ambiguity_covers_sort_direction(ambiguity: Ambiguity) -> bool:
    content = " ".join(
        [
            ambiguity.term,
            ambiguity.reason,
            ambiguity.clarifying_question,
            *(option.text for option in ambiguity.possible_interpretations),
            *(option.sql_effect for option in ambiguity.possible_interpretations),
        ]
    )
    lowered = content.casefold()
    has_sort = "sort" in lowered or "order" in lowered
    has_directions = ("ascending" in lowered or " asc" in lowered) and (
        "descending" in lowered or " desc" in lowered
    )
    return has_sort and has_directions


def detect_ambiguities(
    nl_question: str,
    filtered_schema: str,
    database_engine: str,
    llm_manager,
    preference_tree=None,
    callback=None,
    provided_context: str = "",
) -> Dict[str, Any]:
    try:
        pref_context = format_preference_tree_for_prompt(preference_tree)
        provided_context_block = format_provided_context_block(provided_context)
        if provided_context_block:
            provided_context_block += (
                "\n\nContext rules:\n"
                "- Treat explicit facts and definitions above as resolved constraints.\n"
                "- Do not report an ambiguity whose SQL effect is determined by this "
                "context.\n"
                "- Do not create a new ambiguity merely because the context mentions "
                "a concept.\n"
                "- The context may be partial; do not extrapolate beyond what it states."
            )
        prompt = AMBIGUITY_DETECTION_PROMPT.format(
            nl_question=nl_question,
            database_engine=database_engine,
            filtered_schema=filtered_schema,
            preference_tree_summary=pref_context,
            provided_context_block=provided_context_block,
        )

        logger.info("Detecting ambiguities in: %s", nl_question)
        started = perf_counter()
        response = llm_manager.generate_response(
            prompt=prompt,
            temperature=0.0,
            max_tokens=AMBIGUITY_RESPONSE_MAX_TOKENS,
            purpose="clarification",
            extra={
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "ambiguity_detection",
                        "strict": True,
                        "schema": AMBIGUITY_DETECTION_RESPONSE_SCHEMA,
                    },
                }
            },
        )

        if callback and response:
            try:
                callback(
                    prompt=prompt,
                    response=response.get("response", ""),
                    tokens=response.get("tokens_used")
                    or response.get("usage", {}).get("total_tokens", 0),
                    latency_ms=(perf_counter() - started) * 1000,
                    model=response.get("model", "unknown"),
                )
            except Exception:
                logger.debug("Ambiguity detection callback failed", exc_info=True)

        if not response:
            return {
                "success": False,
                "error": "LLM call failed",
                "raw_response": str(response),
            }

        raw_text = response.get("response", "{}")
        json_text = raw_text

        if "```" in raw_text:
            json_match = re.search(
                r"```(?:json)?\s*\n?(.*?)\n?```",
                raw_text,
                re.DOTALL,
            )
            if json_match:
                json_text = json_match.group(1).strip()

        if not json_text.strip().startswith("{"):
            start = raw_text.find("{")
            end = raw_text.rfind("}")
            if start != -1 and end != -1 and end > start:
                json_text = raw_text[start : end + 1]

        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as exc:
            logger.error("JSON parse error: %s", exc)
            logger.error("Raw response: %s", raw_text[:500])
            return {
                "success": False,
                "error": f"Invalid JSON from LLM: {str(exc)}",
                "raw_response": raw_text,
            }

        required_report_fields = {
            "ambiguities",
            "total_ambiguities",
            "requires_clarification",
            "can_proceed_with_assumptions",
            "overall_confidence",
        }
        missing_report_fields = required_report_fields - parsed.keys()
        if missing_report_fields:
            return {
                "success": False,
                "error": "LLM response missing fields: "
                + ", ".join(sorted(missing_report_fields)),
                "raw_response": raw_text,
            }
        if set(parsed) != set(AMBIGUITY_DETECTION_RESPONSE_SCHEMA["properties"]):
            return {
                "success": False,
                "error": "LLM ambiguity response has unexpected fields",
                "raw_response": raw_text,
            }
        if not isinstance(parsed["ambiguities"], list):
            raise ValueError("ambiguities must be an array")
        if isinstance(parsed["total_ambiguities"], bool) or not isinstance(
            parsed["total_ambiguities"], int
        ):
            raise ValueError("total_ambiguities must be an integer")
        if not isinstance(parsed["requires_clarification"], bool) or not isinstance(
            parsed["can_proceed_with_assumptions"], bool
        ):
            raise ValueError("clarification flags must be booleans")
        confidence = parsed["overall_confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("overall_confidence must be numeric")

        ambiguities = []
        ambiguity_ids = set()
        for ambiguity_data in parsed["ambiguities"]:
            try:
                required_fields = [
                    "id",
                    "category",
                    "term",
                    "reason",
                    "possible_interpretations",
                    "clarifying_question",
                    "priority",
                ]
                missing = [
                    field for field in required_fields if field not in ambiguity_data
                ]
                if missing:
                    raise ValueError(f"ambiguity missing fields: {missing}")
                if set(ambiguity_data) != {
                    "id",
                    "category",
                    "term",
                    "reason",
                    "possible_interpretations",
                    "clarifying_question",
                    "priority",
                }:
                    raise ValueError("ambiguity has unexpected fields")
                ambiguity = Ambiguity.from_dict(ambiguity_data)
                if ambiguity.id in ambiguity_ids:
                    raise ValueError(f"duplicate ambiguity id: {ambiguity.id}")
                option_ids = [
                    option.id for option in ambiguity.possible_interpretations
                ]
                if len(option_ids) != len(set(option_ids)):
                    raise ValueError(
                        f"duplicate option id for ambiguity {ambiguity.id}"
                    )
                ambiguity_ids.add(ambiguity.id)
                ambiguities.append(ambiguity)
            except Exception as exc:
                return {
                    "success": False,
                    "error": f"Invalid ambiguity response: {exc}",
                    "raw_response": raw_text,
                }

        declared_total = int(parsed["total_ambiguities"])
        declared_requires = bool(parsed["requires_clarification"])
        ambiguities, intent_normalizations = _merge_intent_ambiguities(
            ambiguities,
            detect_missing_intent_ambiguities(nl_question),
        )
        derived_total = len(ambiguities)
        normalizations = list(intent_normalizations)
        if declared_total != derived_total:
            normalizations.append(
                {
                    "field": "total_ambiguities",
                    "declared": declared_total,
                    "derived": derived_total,
                }
            )
        report = AmbiguityReport(
            ambiguities=ambiguities,
            total_ambiguities=derived_total,
            requires_clarification=declared_requires and bool(ambiguities),
            can_proceed_with_assumptions=bool(parsed["can_proceed_with_assumptions"]),
            overall_confidence=float(parsed["overall_confidence"]),
        )
        if intent_normalizations:
            report.can_proceed_with_assumptions = False
            report.overall_confidence = min(report.overall_confidence, 0.5)
        if not 0.0 <= report.overall_confidence <= 1.0:
            raise ValueError("overall_confidence must be between 0 and 1")
        effective_requires = clarification_required_for_report(report)
        if declared_requires != effective_requires:
            normalizations.append(
                {
                    "field": "requires_clarification",
                    "declared": declared_requires,
                    "derived": effective_requires,
                    "policy": RANKED_RESOLVER_POLICY,
                }
            )
        report.requires_clarification = effective_requires

        logger.info(
            "Detected %s ambiguities (confidence: %.0f%%)",
            len(ambiguities),
            report.overall_confidence * 100,
        )
        return {
            "success": True,
            "report": report,
            "raw_response": raw_text,
            "token_count": response.get("tokens_used")
            or response.get("usage", {}).get("total_tokens", 0),
            "normalizations": normalizations,
        }
    except ValueError as exc:
        return {
            "success": False,
            "error": f"Invalid ambiguity response: {exc}",
            "raw_response": locals().get("raw_text", ""),
        }
    except Exception as exc:
        if getattr(llm_manager, "propagate_query_errors", False):
            raise
        logger.error("Ambiguity detection error: %s", exc, exc_info=True)
        return {
            "success": False,
            "error": f"Unexpected error: {str(exc)}",
            "raw_response": "",
        }


def collect_clarifications(
    ambiguities: List[Ambiguity],
    preference_tree,
    console=None,
    no_interactive: bool = False,
) -> Dict[str, str]:
    answers: Dict[str, str] = {}
    console = console or get_console()

    try:
        console.print(
            MessagePanel(
                "I need some clarifications to better understand your question.",
                variant="warning",
            )
        )

        for ambiguity in ambiguities:
            if preference_tree and preference_tree.has_answer_for(ambiguity.category):
                logger.debug("Skipping %s - already answered", ambiguity.category)
                continue

            if ambiguity.possible_interpretations:
                question_text = ambiguity.clarifying_question.split(":")[0].strip()
                if not question_text.endswith("?"):
                    question_text += "?"

                console.print(SectionHeader(question_text))
                console.print()
                option_texts = [
                    option.text for option in ambiguity.possible_interpretations
                ]
                all_options = option_texts + ["Other (type your own answer)"]
                console.print(SelectionTable(all_options))
                console.print()

                if no_interactive:
                    resolution = resolve_ranked_ambiguity(ambiguity)
                    if resolution.action != "select":
                        continue
                    answer = resolution.selected_text or ""
                else:
                    choice = Prompt.ask(
                        "Your choice",
                        choices=[str(i) for i in range(1, len(all_options) + 1)],
                    )
                    choice_idx = int(choice) - 1
                    if choice_idx == len(ambiguity.possible_interpretations):
                        answer = input("Describe what you mean: ")
                    else:
                        answer = option_texts[choice_idx]
            else:
                console.print(SectionHeader(ambiguity.clarifying_question))
                answer = "" if no_interactive else Prompt.ask("Your answer")

            if preference_tree:
                preference_tree.add_preference(
                    category=ambiguity.category,
                    question=ambiguity.clarifying_question,
                    answer=answer,
                    confidence=1.0,
                )

            answers[ambiguity.category] = answer
            console.print()

        logger.info("Collected %s clarifications", len(answers))
        return answers
    except KeyboardInterrupt:
        console.print(
            f"\n[{StyleTokens.MUTED}]Clarification cancelled.[/{StyleTokens.MUTED}]"
        )
        return answers
    except Exception as exc:
        logger.error("Error collecting clarifications: %s", exc)
        return answers


__all__ = [
    "NON_INTERACTIVE_CLARIFICATION_POLICY",
    "RANKED_RESOLVER_POLICY",
    "Ambiguity",
    "AmbiguityOption",
    "AmbiguityReport",
    "RankedResolution",
    "clarification_required_for_report",
    "collect_clarifications",
    "detect_ambiguities",
    "detect_missing_intent_ambiguities",
    "resolve_ranked_ambiguity",
]

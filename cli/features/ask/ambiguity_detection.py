"""
Ambiguity detection helpers shared across ask flows.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from shared.ui import (
    MessagePanel,
    Prompt,
    SectionHeader,
    SelectionTable,
    StyleTokens,
    get_console,
)
from .prompts.ask_prompts_v2 import (
    AMBIGUITY_DETECTION_PROMPT,
    format_preference_tree_for_prompt,
    format_schema_for_prompt,
)

logger = logging.getLogger(__name__)


@dataclass
class Ambiguity:
    """Single detected ambiguity."""

    category: str
    term: str
    reason: str
    possible_interpretations: List[str]
    clarifying_question: str
    priority: str = "medium"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "term": self.term,
            "reason": self.reason,
            "possible_interpretations": self.possible_interpretations,
            "clarifying_question": self.clarifying_question,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Ambiguity":
        return cls(
            category=data["category"],
            term=data["term"],
            reason=data["reason"],
            possible_interpretations=data["possible_interpretations"],
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
def detect_ambiguities(
    nl_question: str,
    filtered_schema: str,
    database_engine: str,
    llm_manager,
    preference_tree=None,
    confidence_threshold: float = 0.85,
    callback=None,
) -> Dict[str, Any]:
    try:
        pref_context = format_preference_tree_for_prompt(preference_tree)
        schema_formatted = format_schema_for_prompt(filtered_schema, max_length=8000)
        prompt = AMBIGUITY_DETECTION_PROMPT.format(
            nl_question=nl_question,
            filtered_schema=schema_formatted,
            preference_tree_summary=pref_context,
        )

        logger.info("Detecting ambiguities in: %s", nl_question)
        response = llm_manager.generate_response(
            prompt=prompt,
            temperature=0.0,
            max_tokens=1500,
            extra={"response_format": {"type": "json_object"}},
        )

        if callback and response:
            try:
                callback(
                    prompt=prompt,
                    response=response.get("response", ""),
                    tokens=response.get("usage", {}).get("total_tokens", 0),
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
            import re

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

        if "ambiguities" not in parsed:
            return {
                "success": False,
                "error": 'LLM response missing "ambiguities" field',
                "raw_response": raw_text,
            }

        ambiguities = []
        for ambiguity_data in parsed["ambiguities"]:
            try:
                required_fields = [
                    "category",
                    "term",
                    "reason",
                    "possible_interpretations",
                    "clarifying_question",
                ]
                missing = [field for field in required_fields if field not in ambiguity_data]
                if missing:
                    logger.warning("Ambiguity missing fields %s, skipping", missing)
                    continue
                ambiguities.append(Ambiguity.from_dict(ambiguity_data))
            except Exception as exc:
                logger.warning("Error creating ambiguity: %s", exc)
                continue

        report = AmbiguityReport(
            ambiguities=ambiguities,
            total_ambiguities=parsed.get("total_ambiguities", len(ambiguities)),
            requires_clarification=parsed.get(
                "requires_clarification",
                len(ambiguities) > 0,
            ),
            can_proceed_with_assumptions=parsed.get(
                "can_proceed_with_assumptions",
                False,
            ),
            overall_confidence=parsed.get("overall_confidence", 0.5),
        )

        if report.overall_confidence >= confidence_threshold:
            report.can_proceed_with_assumptions = True
            report.requires_clarification = False

        logger.info(
            "Detected %s ambiguities (confidence: %.0f%%)",
            len(ambiguities),
            report.overall_confidence * 100,
        )
        return {
            "success": True,
            "report": report,
            "raw_response": raw_text,
            "token_count": response.get("usage", {}).get("total_tokens", 0),
        }
    except Exception as exc:
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
    delegated = _maybe_delegate_to_legacy(
        "collect_clarifications",
        ambiguities=ambiguities,
        preference_tree=preference_tree,
        console=console,
        no_interactive=no_interactive,
    )
    if delegated is not None:
        return delegated

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
                all_options = list(ambiguity.possible_interpretations) + [
                    "Other (type your own answer)"
                ]
                console.print(SelectionTable(all_options))
                console.print()

                if no_interactive:
                    answer = ambiguity.possible_interpretations[0]
                else:
                    choice = Prompt.ask(
                        "Your choice",
                        choices=[str(i) for i in range(1, len(all_options) + 1)],
                    )
                    choice_idx = int(choice) - 1
                    if choice_idx == len(ambiguity.possible_interpretations):
                        answer = input("Describe what you mean: ")
                    else:
                        answer = ambiguity.possible_interpretations[choice_idx]
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
    "Ambiguity",
    "AmbiguityReport",
    "collect_clarifications",
    "detect_ambiguities",
]

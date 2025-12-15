"""
Ambiguity Detection - Identify unclear terms in natural language questions.

Based on AmbiSQL paper: https://arxiv.org/abs/2508.15276

Detects two types of ambiguities:
1. DB-related: Schema references, value thresholds, SQL structure
2. LLM-related: Knowledge sources, reasoning context, temporal/spatial scope
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

from ..prompts.ask_prompts_v2 import (
    AMBIGUITY_DETECTION_PROMPT,
    format_schema_for_prompt,
    format_preference_tree_for_prompt
)

logger = logging.getLogger(__name__)


@dataclass
class Ambiguity:
    """Single detected ambiguity."""

    category: str  # unclear_schema_reference, unclear_value_reference, etc.
    term: str  # The ambiguous term
    reason: str  # Why it's ambiguous
    possible_interpretations: List[str]  # Different ways to interpret
    clarifying_question: str  # Question to ask user
    priority: str = "medium"  # high|medium|low

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            'category': self.category,
            'term': self.term,
            'reason': self.reason,
            'possible_interpretations': self.possible_interpretations,
            'clarifying_question': self.clarifying_question,
            'priority': self.priority
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Ambiguity:
        """Deserialize from dictionary."""
        return cls(
            category=data['category'],
            term=data['term'],
            reason=data['reason'],
            possible_interpretations=data['possible_interpretations'],
            clarifying_question=data['clarifying_question'],
            priority=data.get('priority', 'medium')
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
        """Get high-priority ambiguities that must be clarified."""
        return [a for a in self.ambiguities if a.priority == "high"]

    def get_medium_priority(self) -> List[Ambiguity]:
        """Get medium-priority ambiguities (should clarify)."""
        return [a for a in self.ambiguities if a.priority == "medium"]

    def get_low_priority(self) -> List[Ambiguity]:
        """Get low-priority ambiguities (optional)."""
        return [a for a in self.ambiguities if a.priority == "low"]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            'ambiguities': [a.to_dict() for a in self.ambiguities],
            'total_ambiguities': self.total_ambiguities,
            'requires_clarification': self.requires_clarification,
            'can_proceed_with_assumptions': self.can_proceed_with_assumptions,
            'overall_confidence': self.overall_confidence
        }


def detect_ambiguities(
    nl_question: str,
    filtered_schema: str,
    database_engine: str,
    llm_manager,
    preference_tree=None,
    confidence_threshold: float = 0.85,
    callback=None
) -> Dict[str, Any]:
    """
    Detect ambiguities in a natural language question.

    Args:
        nl_question: Natural language question
        filtered_schema: Database schema
        database_engine: postgres|mysql
        llm_manager: LLM manager instance
        preference_tree: Optional PreferenceTree with prior clarifications
        confidence_threshold: Confidence level to proceed without clarification

    Returns:
        Dict with:
            success: bool
            report: AmbiguityReport (if successful)
            error: str (if failed)
            raw_response: str (LLM raw output for debugging)
    """
    try:
        # Format preference tree context
        pref_context = format_preference_tree_for_prompt(preference_tree)

        # Format schema - use larger limit since we already filtered to relevant tables
        # 8000 chars is enough for ~10-15 tables with descriptions
        schema_formatted = format_schema_for_prompt(filtered_schema, max_length=8000)

        # Build prompt
        prompt = AMBIGUITY_DETECTION_PROMPT.format(
            nl_question=nl_question,
            filtered_schema=schema_formatted,
            preference_tree_summary=pref_context
        )

        # Call LLM with JSON mode
        logger.info(f"Detecting ambiguities in: {nl_question}")

        response = llm_manager.generate_response(
            prompt=prompt,
            temperature=0.0,  # Deterministic detection
            max_tokens=1500,
            extra={"response_format": {"type": "json_object"}}
        )

        if not response:
            return {
                'success': False,
                'error': 'LLM call failed',
                'raw_response': str(response)
            }

        # Parse JSON response
        raw_text = response.get('response', '{}')

        # Extract JSON from response - handle various formats:
        # 1. Pure JSON
        # 2. JSON wrapped in ```json ... ```
        # 3. Text before/after JSON block
        json_text = raw_text

        # Try to extract JSON from markdown code fence
        if '```' in raw_text:
            import re
            # Match ```json ... ``` or ``` ... ```
            json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw_text, re.DOTALL)
            if json_match:
                json_text = json_match.group(1).strip()

        # If no code fence, try to find JSON object directly
        if not json_text.strip().startswith('{'):
            # Look for first { and last }
            start = raw_text.find('{')
            end = raw_text.rfind('}')
            if start != -1 and end != -1 and end > start:
                json_text = raw_text[start:end + 1]

        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            logger.error(f"Raw response: {raw_text[:500]}")
            return {
                'success': False,
                'error': f'Invalid JSON from LLM: {str(e)}',
                'raw_response': raw_text
            }

        # Validate response structure
        if 'ambiguities' not in parsed:
            return {
                'success': False,
                'error': 'LLM response missing "ambiguities" field',
                'raw_response': raw_text
            }

        # Build Ambiguity objects
        ambiguities = []

        for a_data in parsed['ambiguities']:
            try:
                # Validate required fields
                required_fields = ['category', 'term', 'reason', 'possible_interpretations', 'clarifying_question']
                missing = [f for f in required_fields if f not in a_data]

                if missing:
                    logger.warning(f"Ambiguity missing fields {missing}, skipping")
                    continue

                ambiguity = Ambiguity.from_dict(a_data)
                ambiguities.append(ambiguity)

            except Exception as e:
                logger.warning(f"Error creating ambiguity: {e}")
                continue

        # Create report
        report = AmbiguityReport(
            ambiguities=ambiguities,
            total_ambiguities=parsed.get('total_ambiguities', len(ambiguities)),
            requires_clarification=parsed.get('requires_clarification', len(ambiguities) > 0),
            can_proceed_with_assumptions=parsed.get('can_proceed_with_assumptions', False),
            overall_confidence=parsed.get('overall_confidence', 0.5)
        )

        # Override can_proceed based on confidence threshold
        if report.overall_confidence >= confidence_threshold:
            report.can_proceed_with_assumptions = True
            report.requires_clarification = False

        logger.info(f"Detected {len(ambiguities)} ambiguities (confidence: {report.overall_confidence:.0%})")

        return {
            'success': True,
            'report': report,
            'raw_response': raw_text,
            'token_count': response.get('usage', {}).get('total_tokens', 0)
        }

    except Exception as e:
        logger.error(f"Ambiguity detection error: {e}", exc_info=True)
        return {
            'success': False,
            'error': f'Unexpected error: {str(e)}',
            'raw_response': ''
        }


def collect_clarifications(
    ambiguities: List[Ambiguity],
    preference_tree,
    console=None,
    no_interactive: bool = False
) -> Dict[str, str]:
    """
    Collect clarifications from user for detected ambiguities.

    Args:
        ambiguities: List of Ambiguity objects
        preference_tree: PreferenceTree to store answers
        console: Optional Rich Console for formatting

    Returns:
        Dict mapping category -> user answer
    """
    answers = {}

    try:
        if console:
            from rich.prompt import Prompt, Confirm

            console.print("\n[bold yellow]I need some clarifications to better understand your question:[/bold yellow]\n")

            for amb in ambiguities:
                # Skip if we already have an answer
                if preference_tree and preference_tree.has_answer_for(amb.category):
                    logger.debug(f"Skipping {amb.category} - already answered")
                    continue

                # If interpretations are provided, offer as choices
                if amb.possible_interpretations and len(amb.possible_interpretations) <= 4:
                    # Extract just the question part (before the colon if present)
                    # E.g., "How many users: [1] ... [2] ..." → "How many users"
                    question_text = amb.clarifying_question.split(':')[0].strip()
                    if not question_text.endswith('?'):
                        question_text += '?'

                    console.print(f"[bold]{question_text}[/bold]\n")

                    for i, interp in enumerate(amb.possible_interpretations, 1):
                        console.print(f"  [{i}] {interp}")
                    console.print()

                    # Get choice
                    choice = Prompt.ask(
                        "Your choice",
                        choices=[str(i) for i in range(1, len(amb.possible_interpretations) + 1)]
                    )

                    answer = amb.possible_interpretations[int(choice) - 1]

                else:
                    # Free-form answer - show full question
                    console.print(f"[bold]{amb.clarifying_question}[/bold]")
                    answer = Prompt.ask("Your answer")

                # Store in preference tree
                if preference_tree:
                    preference_tree.add_preference(
                        category=amb.category,
                        question=amb.clarifying_question,
                        answer=answer,
                        confidence=1.0  # User-provided = max confidence
                    )

                answers[amb.category] = answer

                console.print()  # Blank line between questions

        else:
            # Fallback to plain text
            print("\nI need some clarifications to better understand your question:\n")

            for amb in ambiguities:
                # Skip if we already have an answer
                if preference_tree and preference_tree.has_answer_for(amb.category):
                    continue

                # If interpretations provided, offer as choices
                if amb.possible_interpretations and len(amb.possible_interpretations) <= 4:
                    # Extract just the question part (before the colon if present)
                    question_text = amb.clarifying_question.split(':')[0].strip()
                    if not question_text.endswith('?'):
                        question_text += '?'

                    print(f"{question_text}\n")

                    for i, interp in enumerate(amb.possible_interpretations, 1):
                        print(f"  [{i}] {interp}")
                    print()

                    # Get choice
                    while True:
                        choice_str = input(f"Your choice [1-{len(amb.possible_interpretations)}]: ").strip()
                        try:
                            choice_int = int(choice_str)
                            if 1 <= choice_int <= len(amb.possible_interpretations):
                                answer = amb.possible_interpretations[choice_int - 1]
                                break
                            else:
                                print(f"Please enter a number between 1 and {len(amb.possible_interpretations)}")
                        except ValueError:
                            print("Please enter a valid number")

                else:
                    # Free-form answer - show full question
                    print(f"{amb.clarifying_question}\n")
                    answer = input("Your answer: ").strip()

                # Store in preference tree
                if preference_tree:
                    preference_tree.add_preference(
                        category=amb.category,
                        question=amb.clarifying_question,
                        answer=answer,
                        confidence=1.0
                    )

                answers[amb.category] = answer
                print()

        logger.info(f"Collected {len(answers)} clarifications")

        return answers

    except KeyboardInterrupt:
        print("\n\nClarification cancelled.")
        return answers

    except Exception as e:
        logger.error(f"Error collecting clarifications: {e}")
        return answers

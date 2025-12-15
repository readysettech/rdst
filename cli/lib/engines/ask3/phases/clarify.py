"""
Phase 2: Question Clarification

Detects ambiguities in the natural language question and collects
clarifications from the user if needed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..context import Ask3Context
    from ..presenter import Ask3Presenter

from ..types import Interpretation, Status

logger = logging.getLogger(__name__)


def clarify_question(
    ctx: 'Ask3Context',
    presenter: 'Ask3Presenter',
    llm_manager=None
) -> 'Ask3Context':
    """
    Detect ambiguities and collect clarifications.

    If confidence is high (>85%), proceeds without asking.
    Otherwise, presents interpretations and gets user choice.

    Args:
        ctx: Ask3Context with question and schema
        presenter: For output and user input
        llm_manager: LLMManager instance (optional, creates default)

    Returns:
        Updated context with clarifications or cancelled status
    """
    ctx.phase = 'clarify'
    presenter.analyzing_question()

    # Import here to avoid circular imports
    # Path: lib/engines/ask3/phases/clarify.py -> lib/functions/, lib/llm_manager/
    from ....functions.ambiguity_detection import detect_ambiguities, collect_clarifications, Ambiguity
    from ....llm_manager import LLMManager

    if llm_manager is None:
        llm_manager = LLMManager()

    # Detect ambiguities
    result = detect_ambiguities(
        nl_question=ctx.question,
        filtered_schema=ctx.schema_formatted,
        database_engine=ctx.db_type,
        llm_manager=llm_manager,
        preference_tree=None,  # Could integrate with preference tree later
        confidence_threshold=0.85,
        callback=lambda **kw: _track_llm_call(ctx, 'clarify', **kw)
    )

    if not result.get('success'):
        error = result.get('error', 'Unknown error')
        logger.error(f"Ambiguity detection failed: {error}")
        # Continue without clarification rather than failing
        presenter.warning(f"Could not analyze question for ambiguities: {error}")
        return ctx

    report = result.get('report')
    if not report:
        return ctx

    # High confidence - proceed without asking
    if report.overall_confidence >= 0.85 and not report.requires_clarification:
        presenter.high_confidence_proceed(report.overall_confidence)
        return ctx

    # No ambiguities found
    if not report.ambiguities:
        return ctx

    # Check for schema insufficiency - skip clarification, let generate phase handle expansion
    schema_insufficient_ambs = [a for a in report.ambiguities if a.category == 'schema_insufficient']
    if schema_insufficient_ambs:
        logger.info("Schema insufficiency detected in clarify phase, deferring to generate phase for expansion")
        # Store hints for expansion phase
        for amb in schema_insufficient_ambs:
            if hasattr(amb, 'missing_concepts'):
                ctx.clarifications['missing_concepts'] = ','.join(getattr(amb, 'missing_concepts', []))
            if hasattr(amb, 'requested_tables'):
                ctx.clarifications['requested_tables'] = ','.join(getattr(amb, 'requested_tables', []))
        presenter.info("Schema may be incomplete for this query, will attempt expansion...")
        return ctx

    # Convert to our Interpretation type for presentation
    interpretations = _build_interpretations(report.ambiguities)
    ctx.interpretations = interpretations

    # In non-interactive mode, use first option
    if ctx.no_interactive:
        if interpretations:
            ctx.selected_interpretation = interpretations[0]
            presenter.clarification_selected(interpretations[0].description)
        return ctx

    # Present interpretations and get user choice
    presenter.interpretations(interpretations)

    try:
        # Collect clarifications using existing function
        answers = collect_clarifications(
            ambiguities=report.ambiguities,
            preference_tree=None,
            console=None,  # Use plain text
            no_interactive=ctx.no_interactive
        )

        ctx.clarifications = answers

        # Build refined question with clarifications
        if answers:
            ctx.refined_question = _build_refined_question(ctx.question, answers)

    except KeyboardInterrupt:
        presenter.cancelled()
        ctx.mark_cancelled()
        return ctx

    except Exception as e:
        logger.error(f"Error collecting clarifications: {e}")
        presenter.warning(f"Error collecting clarifications: {e}")

    return ctx


def _build_interpretations(ambiguities) -> list[Interpretation]:
    """Convert ambiguity objects to Interpretation dataclass."""
    interpretations = []

    for i, amb in enumerate(ambiguities, 1):
        # Each ambiguity's possible_interpretations become Interpretation objects
        for j, interp_opt in enumerate(amb.possible_interpretations):
            # Handle both string and object formats
            if isinstance(interp_opt, str):
                description = interp_opt
                likelihood = 0.5  # Default likelihood for string format
            else:
                description = getattr(interp_opt, 'text', str(interp_opt))
                likelihood = getattr(interp_opt, 'likelihood', 0.5)

            interpretations.append(Interpretation(
                id=i * 10 + j,
                description=description,
                assumptions=[amb.reason] if amb.reason else [],
                sql_approach=amb.category,
                likelihood=likelihood
            ))

    # Deduplicate and limit
    seen = set()
    unique = []
    for interp in interpretations:
        if interp.description not in seen:
            seen.add(interp.description)
            unique.append(interp)
            if len(unique) >= 5:  # Max 5 interpretations
                break

    return unique


def _build_refined_question(original: str, clarifications: dict) -> str:
    """
    Build refined question incorporating clarifications.

    Example:
        Original: "Find active users"
        Clarifications: {"unclear_value_reference": "active means status='A'"}
        Result: "Find active users (where active means status='A')"
    """
    if not clarifications:
        return original

    clarification_text = "; ".join(
        f"{category}: {answer}"
        for category, answer in clarifications.items()
    )

    return f"{original} ({clarification_text})"


def _track_llm_call(ctx: 'Ask3Context', phase: str, **kwargs) -> None:
    """Track LLM call for debugging and cost analysis."""
    try:
        ctx.add_llm_call(
            prompt=kwargs.get('prompt', ''),
            response=kwargs.get('response', ''),
            tokens=kwargs.get('tokens', 0),
            latency_ms=kwargs.get('latency_ms', 0),
            model=kwargs.get('model', 'unknown'),
            phase=phase
        )
    except Exception as e:
        logger.warning(f"Failed to track LLM call: {e}")

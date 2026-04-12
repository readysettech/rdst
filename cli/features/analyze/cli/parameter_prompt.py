"""Backward-compatible exports for parameter prompting helpers."""

from shared.parameter_prompt import (
    _prompt_for_parameters_plain,
    _prompt_for_parameters_rich,
    detect_placeholders,
    extract_placeholder_context,
    has_unresolved_placeholders,
    infer_parameter_type,
    prompt_for_parameters,
    substitute_placeholders,
    validate_value,
)

__all__ = [
    "_prompt_for_parameters_plain",
    "_prompt_for_parameters_rich",
    "detect_placeholders",
    "extract_placeholder_context",
    "has_unresolved_placeholders",
    "infer_parameter_type",
    "prompt_for_parameters",
    "substitute_placeholders",
    "validate_value",
]

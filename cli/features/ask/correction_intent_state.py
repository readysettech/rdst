"""Fail-closed helpers for reading model-routed correction intent state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CORRECTION_INTENTS = (
    "percentage_output",
    "ratio_output",
    "scalar_difference_output",
    "all_rows_population",
    "entity_at_extremum",
    "all_matching_categories",
    "shared_scope_all_answers",
    "encoded_identifier_storage",
    "temporal_text_storage",
    "month_axis_storage",
)


def selected_correction_intents(diagnostic: Any) -> tuple[str, ...]:
    """Read an active, allowed canonical intent set from router diagnostics."""
    if isinstance(diagnostic, Mapping):
        values = diagnostic
    elif callable(getattr(diagnostic, "to_dict", None)):
        values = diagnostic.to_dict()
        if not isinstance(values, Mapping):
            return ()
    else:
        return ()
    if values.get("activation_allowed", False) is not True:
        return ()
    if values.get("status") != "activate" or values.get("verdict") != "activate":
        return ()

    raw_selected = values.get("selected_intents")
    if isinstance(raw_selected, (list, tuple)):
        selected = tuple(raw_selected)
    else:
        raw_activations = values.get("activations")
        if isinstance(raw_activations, (list, tuple)):
            selected = tuple(
                activation.get("intent")
                for activation in raw_activations
                if isinstance(activation, Mapping)
            )
        else:
            legacy = values.get("selected_intent")
            selected = (legacy,) if legacy in CORRECTION_INTENTS else ()

    legacy_selected = values.get("selected_intent")
    expected_legacy = selected[0] if len(selected) == 1 else "none"
    if legacy_selected is not None and legacy_selected != expected_legacy:
        return ()

    if (
        not selected
        or len(set(selected)) != len(selected)
        or any(intent not in CORRECTION_INTENTS for intent in selected)
        or {"percentage_output", "ratio_output"}.issubset(selected)
    ):
        return ()
    selected_set = set(selected)
    return tuple(intent for intent in CORRECTION_INTENTS if intent in selected_set)

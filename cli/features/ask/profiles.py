"""Shared Ask configuration for product entrypoints and benchmarks."""

from features.ask.correction_intent_routing import CORRECTION_INTENT_EXPERIMENTAL_SCOPE

CANDIDATE_V4_FLAGS = {
    "correction_intent_routing_enabled": True,
    "dual_candidate_selection_enabled": False,
    "explicit_ratio_normalization_enabled": True,
    "scalar_derived_metric_normalization_enabled": False,
    "all_rows_aggregate_normalization_enabled": False,
    "extremum_entity_normalization_enabled": False,
    "unbounded_categorical_normalization_enabled": False,
    "shared_entity_scope_normalization_enabled": False,
    "value_location_normalization_enabled": False,
    "encoded_identifier_storage_enabled": False,
    "temporal_text_storage_enabled": False,
    "month_axis_storage_enabled": False,
    "period_literal_enabled": True,
    "metric_source_enabled": True,
    "list_membership_enabled": True,
    "outer_rounding_enabled": True,
    "integer_mean_precision_enabled": True,
    "text_mean_precision_enabled": True,
    "count_name_completion_enabled": False,
    "identifier_quoting_enabled": True,
    "percentage_threshold_enabled": True,
    "projection_contract_enabled": True,
    "grouped_extremum_enabled": True,
    "name_format_enabled": True,
    "fraction_precision_enabled": True,
    "comparison_ratio_enabled": True,
    "state_lookup_enabled": True,
    "month_component_enabled": True,
    "endpoint_component_enabled": True,
    "null_extremum_enabled": True,
    "projection_order_enabled": True,
    "output_completion_enabled": True,
    "occurrence_percentage_enabled": True,
    "scaled_ratio_enabled": True,
    "matched_percentage_enabled": True,
    "ranked_union_enabled": True,
    "calendar_day_enabled": True,
    "resolved_column_validation_enabled": True,
    "stable_first_enabled": True,
    "declared_count_enabled": False,
}


def default_ask_service_options() -> dict:
    """Return candidate-v4 settings without changing interaction or execution limits."""
    return {
        **CANDIDATE_V4_FLAGS,
        "enum_overlap_advisory": True,
        "correction_intent_routing_intent_scope": CORRECTION_INTENT_EXPERIMENTAL_SCOPE,
    }

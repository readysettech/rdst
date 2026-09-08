"""Check values from one fresh combined native count statement; no saved-proof authority."""
from decimal import Decimal
from typing import Any

BOUNDS = {
    "max_data_statements": 1,
    "timeout_nanoseconds": 2_000_000_000,
    "max_rows": 2,
    "max_result_bytes": 16384,
    "retries": 0,
}
COUNTERS = (
    "selected_parent_count", "grouped_parent_count", "unique_grouped_parent_count",
    "omitted_selected_parents", "unexpected_grouped_parents", "duplicate_parent_groups",
    "selected_incidence_rows", "selected_null_child_keys", "nonnull_child_orphans",
    "ambiguous_child_reference_rows", "matched_child_reference_rows", "raw_count_total",
    "distinct_parent_child_total", "matched_parent_child_total",
    "per_parent_key_comparison_mismatches", "per_parent_count_inconsistencies",
    "duplicate_reference_difference",
)
ZERO_COUNTERS = (
    "omitted_selected_parents", "unexpected_grouped_parents", "duplicate_parent_groups",
    "selected_null_child_keys", "nonnull_child_orphans", "ambiguous_child_reference_rows",
    "per_parent_key_comparison_mismatches", "per_parent_count_inconsistencies",
)
ROLE_COUNTERS = ("reference_rows", "null_keys", "orphan_keys", "ambiguous_matches", "matched_rows")
SCALARS = ("raw_native_average", "distinct_native_average", "candidate_native_scalar")

class _Invalid(ValueError):
    pass

def _require(condition: bool, issue: str) -> None:
    if not condition:
        raise _Invalid(issue)

def _object(value: Any, keys: set[str], path: str) -> dict:
    _require(type(value) is dict and set(value) == keys, path + ":exact_fields_required")
    return value

def _integer(value: Any, path: str, *, minimum: int = 0) -> int:
    _require(type(value) is int and value >= minimum, path + ":plain_nonnegative_integer_required")
    return value

def _number(value: Any, path: str) -> Decimal | int:
    # Do not convert floats, strings, rational fixture pairs, bools or subclasses.
    _require(type(value) is int or (type(value) is Decimal and value.is_finite()),
             path + ":exact_finite_native_numeric_required")
    return value

def _same(value: Any, expected: Any, path: str) -> None:
    _require(type(value) is type(expected) and value == expected, path + ":mismatch")

def check_count_row(row, declared_parent_role_ids, original_scalar):
    row = _object(row, set(COUNTERS) | set(SCALARS) | {"endpoint_roles"}, "proof")
    for key in COUNTERS:
        _integer(row[key], "proof." + key)
    for key in SCALARS:
        _number(row[key], "proof." + key)
    for key in ZERO_COUNTERS:
        _require(row[key] == 0, "proof." + key + ":must_be_zero")
    parents = row["selected_parent_count"]
    _require(parents > 0, "proof:selected_parent_population_empty")
    _require(parents == row["grouped_parent_count"] == row["unique_grouped_parent_count"],
             "proof:parent_group_coverage_mismatch")
    incidence, raw, distinct = (row[key] for key in (
        "selected_incidence_rows", "raw_count_total", "distinct_parent_child_total"))
    _require(incidence == raw == row["matched_child_reference_rows"], "proof:child_reference_total_mismatch")
    _require(parents <= distinct < raw, "proof:nonempty_per_parent_distinct_totals_or_duplicates_invalid")
    _require(distinct == row["matched_parent_child_total"], "proof:physical_child_distinct_total_mismatch")
    _require(row["duplicate_reference_difference"] == raw - distinct > 0,
             "proof:positive_per_parent_duplicate_difference_required")
    roles = _object(row["endpoint_roles"], set(declared_parent_role_ids), "proof.endpoint_roles")
    for role_id, role in roles.items():
        role = _object(role, set(ROLE_COUNTERS), "proof.endpoint_roles." + role_id)
        for key in ROLE_COUNTERS:
            _integer(role[key], "proof.endpoint_roles." + role_id + "." + key)
        _require(role["reference_rows"] == role["matched_rows"] == incidence,
                 "proof.endpoint_roles." + role_id + ":reference_coverage_mismatch")
        _require(role["null_keys"] == role["orphan_keys"] == role["ambiguous_matches"] == 0,
                 "proof.endpoint_roles." + role_id + ":invalid_reference")
    old = _number(original_scalar, "original.scalar")
    raw_avg, distinct_avg, candidate = (row[key] for key in SCALARS)
    _same(raw_avg, old, "proof.historical_original")
    _same(candidate, distinct_avg, "proof.independent_native_candidate")
    _require(1 <= distinct_avg < raw_avg and distinct_avg <= distinct and raw_avg <= raw,
             "proof:native_scalar_range_or_changed_result_invalid")
    return candidate

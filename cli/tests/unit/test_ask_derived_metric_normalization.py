from types import SimpleNamespace

import pytest

from features.ask.derived_metric_normalization import (
    normalize_context,
    normalize_scalar_derived_metric_sql,
)

Q1149 = """SELECT
SUM(CASE WHEN Admission = '+' THEN 1 ELSE 0 END) AS inpatient_male_count,
SUM(CASE WHEN Admission = '-' THEN 1 ELSE 0 END) AS outpatient_male_count,
ROUND(
  CAST(SUM(CASE WHEN Admission = '+' THEN 1 ELSE 0 END) * 100.0 AS DOUBLE)
  / NULLIF(SUM(CASE WHEN Admission = '-' THEN 1 ELSE 0 END), 0),
  2
) AS percentage
FROM Patient WHERE SEX = 'M'"""

Q1149_WITH_COMPARISON = """SELECT
SUM(CASE WHEN Admission = '+' THEN 1 ELSE 0 END) AS inpatient_male_count,
SUM(CASE WHEN Admission = '-' THEN 1 ELSE 0 END) AS outpatient_male_count,
CASE
  WHEN SUM(CASE WHEN Admission = '+' THEN 1 ELSE 0 END)
       > SUM(CASE WHEN Admission = '-' THEN 1 ELSE 0 END) THEN 'in-patient'
  WHEN SUM(CASE WHEN Admission = '-' THEN 1 ELSE 0 END)
       > SUM(CASE WHEN Admission = '+' THEN 1 ELSE 0 END) THEN 'outpatient'
  ELSE 'equal'
END AS more_group,
ROUND(
  CAST(SUM(CASE WHEN Admission = '+' THEN 1 ELSE 0 END) * 100.0 AS DOUBLE)
  / NULLIF(SUM(CASE WHEN Admission = '-' THEN 1 ELSE 0 END), 0),
  2
) AS percentage
FROM Patient WHERE SEX = 'M'"""


def normalize(sql=Q1149, question="What is the deviation in percentage?"):
    return normalize_scalar_derived_metric_sql(
        question=question,
        provided_context=(
            "percentage = DIVIDE(COUNT(ID) where Admission = '+', "
            "COUNT(ID) where Admission = '-')"
        ),
        sql=sql,
        dialect="mysql",
    )


def test_collapses_scalar_intermediate_aggregates_and_unrequested_rounding():
    sql, diagnostics = normalize()

    assert sql == (
        "SELECT CAST(SUM(CASE WHEN Admission = '+' THEN 1 ELSE 0 END) * 100.0 "
        "AS DOUBLE) / NULLIF(SUM(CASE WHEN Admission = '-' THEN 1 ELSE 0 END), "
        "0) AS percentage FROM Patient WHERE SEX = 'M'"
    )
    assert diagnostics["status"] == "normalized"
    assert diagnostics["removed_intermediate_projections"] == 2
    assert diagnostics["removed_unrequested_rounds"] == 1


def test_explicit_rounding_intent_preserves_round():
    sql, diagnostics = normalize(
        question="Give the percentage rounded to 2 decimal places"
    )

    assert "ROUND(" in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["removed_unrequested_rounds"] == 0


def test_removes_literal_comparison_of_exact_ratio_aggregates():
    sql, diagnostics = normalize(
        Q1149_WITH_COMPARISON,
        question=(
            "Are there more in-patient or outpatient male patients? "
            "What is the percentage?"
        ),
    )

    assert sql.startswith("SELECT CAST(SUM(")
    assert "more_group" not in sql
    assert "inpatient_male_count" not in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["removed_intermediate_projections"] == 2
    assert diagnostics["removed_redundant_comparison_projections"] == 1


def test_comparison_label_normalization_generalizes_to_order_fulfillment():
    original = """SELECT
SUM(CASE WHEN fulfillment_status = 'fulfilled' THEN 1 ELSE 0 END) AS fulfilled_count,
SUM(CASE WHEN fulfillment_status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_count,
CASE
  WHEN SUM(CASE WHEN fulfillment_status = 'fulfilled' THEN 1 ELSE 0 END)
       > SUM(CASE WHEN fulfillment_status = 'cancelled' THEN 1 ELSE 0 END)
    THEN 'fulfilled'
  WHEN SUM(CASE WHEN fulfillment_status = 'cancelled' THEN 1 ELSE 0 END)
       > SUM(CASE WHEN fulfillment_status = 'fulfilled' THEN 1 ELSE 0 END)
    THEN 'cancelled'
  ELSE 'equal'
END AS larger_group,
CAST(
  SUM(CASE WHEN fulfillment_status = 'fulfilled' THEN 1 ELSE 0 END) * 100.0
  AS DOUBLE
) / NULLIF(
  SUM(CASE WHEN fulfillment_status = 'cancelled' THEN 1 ELSE 0 END), 0
) AS fulfillment_percentage
FROM order_fulfillments
WHERE sales_region = 'West'"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question=(
            "Were there more fulfilled or cancelled orders in the West sales "
            "region, and what was the fulfillment percentage?"
        ),
        provided_context=(
            "fulfillment percentage = DIVIDE(fulfilled order count, "
            "cancelled order count)"
        ),
        sql=original,
        dialect="mysql",
    )

    assert sql == (
        "SELECT CAST(SUM(CASE WHEN fulfillment_status = 'fulfilled' THEN 1 "
        "ELSE 0 END) * 100.0 AS DOUBLE) / NULLIF(SUM(CASE WHEN "
        "fulfillment_status = 'cancelled' THEN 1 ELSE 0 END), 0) AS "
        "fulfillment_percentage FROM order_fulfillments WHERE sales_region = "
        "'West'"
    )
    assert diagnostics["status"] == "normalized"
    assert diagnostics["removed_intermediate_projections"] == 2
    assert diagnostics["removed_redundant_comparison_projections"] == 1
    assert diagnostics["removed_projection_names"] == [
        "fulfilled_count",
        "cancelled_count",
        "larger_group",
    ]


def test_comparison_label_requires_authoritative_metric_definition():
    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question="Are there more in-patient or outpatient? Give the percentage.",
        provided_context="in-patient means Admission = '+'",
        sql=Q1149_WITH_COMPARISON,
        dialect="mysql",
    )

    assert sql == Q1149_WITH_COMPARISON
    assert diagnostics["reason"] == "non-intermediate-output-present"


def test_comparison_label_with_unrelated_aggregate_is_unchanged():
    original = Q1149_WITH_COMPARISON.replace(
        "ELSE 'equal'\nEND AS more_group",
        "WHEN MAX(ID) > 0 THEN 'present' ELSE 'equal'\nEND AS more_group",
    )
    sql, diagnostics = normalize(
        original,
        question="Are there more in-patient or outpatient? Give the percentage.",
    )

    assert sql == original
    assert diagnostics["reason"] == "non-intermediate-output-present"


def test_comparison_label_with_nonliteral_output_is_unchanged():
    original = Q1149_WITH_COMPARISON.replace(
        "THEN 'in-patient'",
        "THEN MAX(ID)",
    )
    sql, diagnostics = normalize(
        original,
        question="Are there more in-patient or outpatient? Give the percentage.",
    )

    assert sql == original
    assert diagnostics["reason"] == "non-intermediate-output-present"


def test_unrequested_ratio_rounding_inside_safety_case_is_removed():
    original = """SELECT
    SUM(CASE WHEN Admission = '+' THEN 1 ELSE 0 END) AS inpatient_male_count,
    SUM(CASE WHEN Admission = '-' THEN 1 ELSE 0 END) AS outpatient_male_count,
    CASE
      WHEN SUM(CASE WHEN Admission = '-' THEN 1 ELSE 0 END) = 0 THEN NULL
      ELSE ROUND(
        CAST(SUM(CASE WHEN Admission = '+' THEN 1 ELSE 0 END) * 100.0 AS DOUBLE)
        / SUM(CASE WHEN Admission = '-' THEN 1 ELSE 0 END),
        2
      )
    END AS percentage
    FROM Patient WHERE SEX = 'M'"""

    sql, diagnostics = normalize(original)

    assert "ROUND(" not in sql
    assert "CASE WHEN" in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["removed_unrequested_rounds"] == 1


def test_rounding_inside_ratio_input_is_preserved():
    original = (
        "SELECT SUM(ROUND(amount, 2)) AS rounded_total, COUNT(*) AS row_count, "
        "SUM(ROUND(amount, 2)) / COUNT(*) AS percentage FROM payments"
    )

    sql, diagnostics = normalize(original)

    assert "ROUND(amount, 2)" in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["removed_unrequested_rounds"] == 0


def test_grouped_ratio_report_is_unchanged():
    original = (
        "SELECT Segment, SUM(old_value) AS old_total, SUM(new_value) AS new_total, "
        "(SUM(new_value) - SUM(old_value)) / SUM(old_value) * 100 AS percentage "
        "FROM metrics GROUP BY Segment"
    )
    sql, diagnostics = normalize(original)

    assert sql == original
    assert diagnostics["reason"] == "grouped-result"


def test_single_ratio_projection_is_unchanged():
    original = "SELECT ROUND(CAST(SUM(x) AS DOUBLE) / COUNT(*), 5) AS percentage FROM t"
    sql, diagnostics = normalize(original)

    assert sql == original
    assert diagnostics["reason"] == "no-intermediate-projections"


def test_non_intermediate_output_prevents_rewrite():
    original = Q1149.replace(
        "FROM Patient",
        ", MAX(ID) + 1 AS unrelated FROM Patient",
    )
    sql, diagnostics = normalize(original)

    assert sql == original
    assert diagnostics["reason"] == "non-intermediate-output-present"


def test_collapses_two_component_counts_into_requested_scalar_difference():
    original = """SELECT
    SUM(CASE WHEN currency = 'USD' THEN 1 ELSE 0 END) AS usd_count,
    SUM(CASE WHEN currency = 'EUR' THEN 1 ELSE 0 END) AS eur_count,
    SUM(CASE WHEN currency = 'USD' THEN 1 ELSE 0 END)
      - SUM(CASE WHEN currency = 'EUR' THEN 1 ELSE 0 END) AS difference
    FROM customer_payments WHERE segment = 'SMB'"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question="How many more SMB customers paid in USD than in EUR?",
        provided_context="",
        sql=original,
        dialect="mysql",
    )

    assert sql == (
        "SELECT SUM(CASE WHEN currency = 'USD' THEN 1 ELSE 0 END) - "
        "SUM(CASE WHEN currency = 'EUR' THEN 1 ELSE 0 END) AS difference "
        "FROM customer_payments WHERE segment = 'SMB'"
    )
    assert diagnostics["status"] == "normalized"
    assert diagnostics["metric_kind"] == "difference"
    assert diagnostics["removed_intermediate_projections"] == 2

    second_sql, second_diagnostics = normalize_scalar_derived_metric_sql(
        question="How many more SMB customers paid in USD than in EUR?",
        provided_context="",
        sql=sql,
        dialect="mysql",
    )
    assert second_sql == sql
    assert second_diagnostics["reason"] == "no-intermediate-projections"


def test_scalar_difference_cleanup_generalizes_to_postgres_amounts():
    original = '''SELECT
    SUM("amount") FILTER (WHERE "status" = 'completed') AS completed_total,
    SUM("amount") FILTER (WHERE "status" = 'refunded') AS refunded_total,
    SUM("amount") FILTER (WHERE "status" = 'completed')
      - SUM("amount") FILTER (WHERE "status" = 'refunded') AS amount_difference
    FROM "payments"'''

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question="What is the difference between completed and refunded amounts?",
        provided_context="",
        sql=original,
        dialect="postgresql",
    )

    assert sql.startswith('SELECT SUM("amount") FILTER(WHERE "status" =')
    assert "completed_total" not in sql
    assert "refunded_total" not in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["metric_kind"] == "difference"


def test_explicit_request_for_both_counts_and_difference_is_preserved():
    original = """SELECT
    SUM(CASE WHEN state = 'done' THEN 1 ELSE 0 END) AS done_count,
    SUM(CASE WHEN state = 'failed' THEN 1 ELSE 0 END) AS failed_count,
    SUM(CASE WHEN state = 'done' THEN 1 ELSE 0 END)
      - SUM(CASE WHEN state = 'failed' THEN 1 ELSE 0 END) AS difference
    FROM jobs"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question=(
            "Give both the done and failed counts and the difference between them."
        ),
        provided_context="",
        sql=original,
        dialect="mysql",
    )

    assert sql == original
    assert diagnostics["reason"] == "no-explicit-derived-metric-intent"


@pytest.mark.parametrize(
    ("question", "provided_context"),
    [
        (
            "How many succeeded, how many failed, and how many more succeeded than failed?",
            "",
        ),
        (
            "Show all three figures: successes, failures, and their difference.",
            "",
        ),
        (
            "What is the difference between successes and failures?",
            "Return the two component counts as well as the difference.",
        ),
    ],
)
def test_component_output_requests_are_preserved(question, provided_context):
    original = """SELECT
    SUM(success) AS success_total,
    SUM(failure) AS failure_total,
    SUM(success) - SUM(failure) AS difference
    FROM job_stats"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question=question,
        provided_context=provided_context,
        sql=original,
        dialect="mysql",
    )

    assert sql == original
    assert diagnostics["reason"] == "no-explicit-derived-metric-intent"


def test_rate_word_does_not_authorize_scalar_difference_cleanup():
    original = """SELECT
    SUM(success) AS success_total,
    SUM(failure) AS failure_total,
    SUM(success) - SUM(failure) AS difference
    FROM job_stats"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question="What is the difference rate between successes and failures?",
        provided_context="",
        sql=original,
        dialect="mysql",
    )

    assert sql == original
    assert diagnostics["reason"] == "no-explicit-derived-metric-intent"


def test_by_how_many_is_explicit_scalar_difference_intent():
    original = """SELECT
    COUNT(CASE WHEN state = 'ready' THEN 1 END) AS ready_count,
    COUNT(CASE WHEN state = 'blocked' THEN 1 END) AS blocked_count,
    COUNT(CASE WHEN state = 'ready' THEN 1 END)
      - COUNT(CASE WHEN state = 'blocked' THEN 1 END) AS difference
    FROM jobs"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question="By how many did ready jobs exceed blocked jobs?",
        provided_context="",
        sql=original,
        dialect="mysql",
    )

    assert diagnostics["status"] == "normalized"
    assert sql.count(" AS ") == 1


def test_multiple_difference_outputs_are_preserved():
    original = """SELECT
    SUM(success) AS success_total,
    SUM(failure) AS failure_total,
    SUM(success) - SUM(failure) AS difference,
    SUM(failure) - SUM(success) AS reverse_difference
    FROM job_stats"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question="What is the difference between successes and failures?",
        provided_context="",
        sql=original,
        dialect="mysql",
    )

    assert sql == original
    assert diagnostics["reason"] == "not-one-derived-aggregate-difference"


def test_difference_with_unrelated_output_is_preserved():
    original = """SELECT
    SUM(success) AS success_total,
    SUM(failure) AS failure_total,
    SUM(success) - SUM(failure) AS difference,
    MAX(updated_at) AS last_update
    FROM job_stats"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question="How many more successes than failures were there?",
        provided_context="",
        sql=original,
        dialect="mysql",
    )

    assert sql == original
    assert diagnostics["reason"] == "not-exact-difference-projection-triple"


@pytest.mark.parametrize(
    "order_by",
    ["success_total", "2", "difference DESC"],
)
def test_ordered_difference_result_is_preserved(order_by):
    original = f"""SELECT
    SUM(success) AS success_total,
    SUM(failure) AS failure_total,
    SUM(success) - SUM(failure) AS difference
    FROM job_stats ORDER BY {order_by}"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question="What is the difference between successes and failures?",
        provided_context="",
        sql=original,
        dialect="mysql",
    )

    assert sql == original
    assert diagnostics["reason"] == "unsupported-difference-result-shape"


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_casted_difference_operands_are_matched_consistently(dialect):
    original = """SELECT
    CAST(SUM(success) AS DOUBLE) AS success_total,
    CAST(SUM(failure) AS DOUBLE) AS failure_total,
    CAST(SUM(success) AS DOUBLE) - CAST(SUM(failure) AS DOUBLE) AS difference
    FROM job_stats"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question="What is the difference between successes and failures?",
        provided_context="",
        sql=original,
        dialect=dialect,
    )

    assert diagnostics["status"] == "normalized"
    assert "success_total" not in sql
    assert "failure_total" not in sql


def test_grouped_difference_report_is_preserved():
    original = """SELECT region,
    SUM(success) AS success_total,
    SUM(failure) AS failure_total,
    SUM(success) - SUM(failure) AS difference
    FROM job_stats GROUP BY region"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question="What is the difference between successes and failures by region?",
        provided_context="",
        sql=original,
        dialect="mysql",
    )

    assert sql == original
    assert diagnostics["reason"] == "grouped-result"


def test_difference_requires_exactly_two_matching_intermediate_aggregates():
    original = """SELECT
    SUM(success) AS success_total,
    COUNT(*) AS row_count,
    SUM(success) - SUM(failure) AS difference
    FROM job_stats"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question="How many more successes than failures were there?",
        provided_context="",
        sql=original,
        dialect="mysql",
    )

    assert sql == original
    assert diagnostics["reason"] == "non-intermediate-output-present"


def test_context_wrapper_updates_sql_and_diagnostics():
    ctx = SimpleNamespace(
        sql=Q1149,
        generated_sql=Q1149,
        question="What is the deviation in percentage?",
        refined_question=None,
        provided_context="percentage = DIVIDE(inpatient, outpatient)",
        db_type="mysql",
        scalar_derived_metric_normalization={},
    )

    result = normalize_context(ctx)

    assert result is ctx
    assert result.sql != Q1149
    assert result.generated_sql == result.sql
    assert result.scalar_derived_metric_normalization["status"] == "normalized"


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
@pytest.mark.parametrize("intent", ["percentage_output", "ratio_output"])
def test_ratio_intent_hint_does_not_authorize_projection_cleanup(dialect, intent):
    original = """SELECT
    SUM(active) AS active_total,
    COUNT(*) AS member_total,
    CAST(SUM(active) AS DOUBLE) / COUNT(*) AS member_share
    FROM members"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question="Give the active-member share.",
        provided_context="",
        sql=original,
        dialect=dialect,
        intent_hints=(intent,),
    )

    assert sql == original
    assert diagnostics["status"] == "unchanged"
    assert diagnostics["reason"] == "no-explicit-derived-metric-intent"
    assert diagnostics["ratio_router_hint_authorized_cleanup"] is False


@pytest.mark.parametrize(
    "question",
    [
        "Return both component totals and the average member share.",
        "Return the active total, member total, and their ratio.",
        "How many are active, how many members are there, and what is their ratio?",
        "Return the numerator, denominator, and ratio.",
        "Return the ratio along with the active total and member total.",
        "Return active and total counts with their average.",
        "Output the active count plus the member share.",
    ],
)
def test_ratio_hint_preserves_explicitly_requested_component_totals(question):
    original = """SELECT
    SUM(active) AS active_total,
    COUNT(*) AS member_total,
    CAST(SUM(active) AS DOUBLE) / COUNT(*) AS member_share
    FROM members"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question=question,
        provided_context="",
        sql=original,
        dialect="mysql",
        intent_hints=("ratio_output",),
    )

    assert sql == original
    assert diagnostics["reason"] in {
        "explicit-component-output-request",
        "no-explicit-derived-metric-intent",
    }


def test_ratio_hint_does_not_confuse_operand_names_with_requested_outputs():
    original = """SELECT
    SUM(active) AS active_total,
    COUNT(*) AS member_total,
    CAST(SUM(active) AS DOUBLE) / COUNT(*) AS member_share
    FROM members"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question="Return the ratio of the active total to the member total.",
        provided_context="",
        sql=original,
        dialect="mysql",
        intent_hints=("ratio_output",),
    )

    assert diagnostics["status"] == "normalized"
    assert "active_total" not in sql
    assert "member_total" not in sql


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_difference_hint_handles_excess_paraphrase(dialect):
    original = """SELECT
    SUM(success) AS success_total,
    SUM(failure) AS failure_total,
    SUM(success) - SUM(failure) AS difference
    FROM job_stats"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question="Return the excess of successful jobs compared with failed jobs.",
        provided_context="",
        sql=original,
        dialect=dialect,
        intent_hints=("scalar_difference_output",),
    )

    assert diagnostics["status"] == "normalized"
    assert diagnostics["metric_kind"] == "difference"
    assert "success_total" not in sql
    assert "failure_total" not in sql


def test_difference_hint_bypasses_english_component_output_pattern():
    original = """SELECT
    SUM(success) AS success_total,
    SUM(failure) AS failure_total,
    SUM(success) - SUM(failure) AS difference
    FROM job_stats"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question="Return both component totals and their excess.",
        provided_context="",
        sql=original,
        dialect="mysql",
        intent_hints=("scalar_difference_output",),
    )

    assert diagnostics["status"] == "normalized"
    assert "success_total" not in sql
    assert "failure_total" not in sql


def test_difference_hint_keeps_grouped_output_unchanged():
    original = """SELECT region,
    SUM(success) AS success_total,
    SUM(failure) AS failure_total,
    SUM(success) - SUM(failure) AS difference
    FROM job_stats GROUP BY region"""

    sql, diagnostics = normalize_scalar_derived_metric_sql(
        question="Return the excess of successful jobs compared with failed jobs.",
        provided_context="",
        sql=original,
        dialect="mysql",
        intent_hints=("scalar_difference_output",),
    )

    assert sql == original
    assert diagnostics["reason"] == "grouped-result"


def test_context_wrapper_uses_activated_difference_hint():
    original = """SELECT
    SUM(success) AS success_total,
    SUM(failure) AS failure_total,
    SUM(success) - SUM(failure) AS difference
    FROM job_stats"""
    ctx = SimpleNamespace(
        sql=original,
        generated_sql=original,
        question="Return the excess of successful jobs compared with failed jobs.",
        refined_question=None,
        provided_context="",
        db_type="mysql",
        correction_intent_routing={
            "status": "activate",
            "verdict": "activate",
            "selected_intent": "scalar_difference_output",
            "activation_allowed": True,
        },
        scalar_derived_metric_normalization={},
    )

    result = normalize_context(ctx)

    assert result.scalar_derived_metric_normalization["status"] == "normalized"
    assert result.scalar_derived_metric_normalization["metric_kind"] == "difference"

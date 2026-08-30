from __future__ import annotations

from types import SimpleNamespace

import pytest

from features.ask.numeric_normalization import (
    normalize_context,
    normalize_explicit_ratio_sql,
)


def normalize(question: str, sql: str, dialect: str = "mysql"):
    return normalize_explicit_ratio_sql(
        question=question,
        provided_context="",
        sql=sql,
        dialect=dialect,
    )


def test_explicit_percentage_uses_floating_division_mysql():
    sql, diagnostics = normalize(
        "What percentage are active?",
        "SELECT SUM(active) * 100.0 / COUNT(*) FROM users",
    )
    assert "CAST(SUM(active) * 100.0 AS DOUBLE) / COUNT(*)" in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["scaled_percentages"] == 0


def test_missing_percentage_scale_is_added_once():
    sql, diagnostics = normalize(
        "What is the deviation in percentage?",
        "SELECT SUM(inpatient) * 1.0 / SUM(outpatient) AS percentage FROM visits",
    )

    assert "* 1.0 * 100 AS DOUBLE" in sql
    assert sql.count("100") == 1
    assert diagnostics["scaled_percentages"] == 1


def test_outer_percentage_scale_is_not_duplicated():
    original = (
        "SELECT CAST(SUM(inpatient) AS DOUBLE) / SUM(outpatient) * 100 "
        "AS percentage FROM visits"
    )

    sql, diagnostics = normalize("What percentage are in-patient?", original)

    assert sql == original
    assert diagnostics["reason"] == "no-eligible-division"


def test_ratio_and_proportion_are_not_scaled_to_percentage():
    for question in (
        "What is the ratio of active users?",
        "What proportion of users are active?",
    ):
        sql, diagnostics = normalize(
            question,
            "SELECT SUM(active) / COUNT(*) FROM users",
        )

        assert "100" not in sql
        assert diagnostics["scaled_percentages"] == 0


def test_multiple_percentage_divisions_fail_closed_for_scaling():
    sql, diagnostics = normalize(
        "What percentages are active and verified?",
        "SELECT SUM(active) / COUNT(*), SUM(verified) / COUNT(*) FROM users",
    )

    assert "100" not in sql
    assert diagnostics["scaled_percentages"] == 0


def test_nested_subquery_division_is_not_treated_as_root_percentage():
    sql, diagnostics = normalize(
        "What percentage is stored for this user?",
        "SELECT (SELECT SUM(active) / COUNT(*) FROM events) FROM users LIMIT 1",
    )

    assert "100" not in sql
    assert diagnostics["scaled_percentages"] == 0


def test_explicit_ratio_uses_double_precision_postgres():
    sql, diagnostics = normalize(
        "What is the ratio of active users?",
        "SELECT SUM(active) / COUNT(*) FROM users",
        "postgresql",
    )
    assert "DOUBLE PRECISION" in sql
    assert diagnostics["changed_divisions"] == 1


def test_non_ratio_question_is_unchanged():
    original = "SELECT revenue / units FROM sales"
    sql, diagnostics = normalize("Show revenue per unit.", original)
    assert sql == original
    assert diagnostics["reason"] == "no-explicit-ratio-intent"


def test_existing_double_cast_can_receive_missing_percentage_scale():
    original = "SELECT CAST(SUM(active) AS DOUBLE) / COUNT(*) FROM users"
    sql, diagnostics = normalize("What percentage are active?", original)
    assert "CAST(SUM(active) AS DOUBLE) * 100 / COUNT(*)" in sql
    assert diagnostics["changed_divisions"] == 0
    assert diagnostics["scaled_percentages"] == 1


def test_existing_double_cast_ratio_is_unchanged():
    original = "SELECT CAST(SUM(active) AS DOUBLE) / COUNT(*) FROM users"
    sql, diagnostics = normalize("What is the ratio of active users?", original)
    assert sql == original
    assert diagnostics["reason"] == "no-eligible-division"


def test_ratio_without_division_is_unchanged():
    original = "SELECT ratio FROM metrics"
    sql, diagnostics = normalize("Show the ratio.", original)
    assert sql == original
    assert diagnostics["reason"] == "no-eligible-division"


def test_context_divide_pseudofunction_becomes_floating_division():
    sql, diagnostics = normalize_explicit_ratio_sql(
        question="What is the percentage?",
        provided_context="percentage = DIVIDE(inpatient, outpatient)",
        sql="SELECT DIVIDE(SUM(inpatient), SUM(outpatient)) * 100 FROM visits",
        dialect="mysql",
    )

    assert "DIVIDE" not in sql
    assert "CAST(SUM(inpatient) AS DOUBLE) / SUM(outpatient)" in sql
    assert diagnostics["converted_divide_functions"] == 1
    assert diagnostics["changed_divisions"] == 1


def test_untrusted_sql_divide_is_not_rewritten_without_context_definition():
    original = "SELECT DIVIDE(SUM(inpatient), SUM(outpatient)) FROM visits"
    sql, diagnostics = normalize("What is the percentage?", original)

    assert sql == original
    assert diagnostics["reason"] == "no-eligible-division"


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_percentage_hint_handles_a_misspelled_request(dialect):
    sql, diagnostics = normalize_explicit_ratio_sql(
        question="What precentage of members are active?",
        provided_context="",
        sql="SELECT SUM(active) / COUNT(*) AS member_share FROM members",
        dialect=dialect,
        intent_hints=("percentage_output",),
    )

    assert diagnostics["status"] == "normalized"
    assert diagnostics["scaled_percentages"] == 1
    assert "100" in sql
    assert "DOUBLE" in sql


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_ratio_hint_does_not_add_percentage_scaling(dialect):
    sql, diagnostics = normalize_explicit_ratio_sql(
        question="Give the active-member share.",
        provided_context="",
        sql="SELECT SUM(active) / COUNT(*) AS member_share FROM members",
        dialect=dialect,
        intent_hints=("ratio_output",),
    )

    assert diagnostics["status"] == "normalized"
    assert diagnostics["scaled_percentages"] == 0
    assert "100" not in sql


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
@pytest.mark.parametrize(
    "question",
    [
        "Devuelve el promedio mensual.",
        "Retournez la valuer moyenne.",
        "月ごとの平均を返してください。",
    ],
)
def test_ratio_hint_casts_multilingual_or_misspelled_requests_only(dialect, question):
    sql, diagnostics = normalize_explicit_ratio_sql(
        question=question,
        provided_context="",
        sql="SELECT SUM(amount) / COUNT(*) AS result_value FROM payments",
        dialect=dialect,
        intent_hints=("ratio_output",),
    )

    assert diagnostics["status"] == "normalized"
    assert diagnostics["routed_effect"] == "floating-cast-only"
    assert diagnostics["converted_divide_functions"] == 0
    assert diagnostics["scaled_percentages"] == 0
    assert "CAST(SUM(amount) AS DOUBLE" in sql


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
@pytest.mark.parametrize(
    "question",
    [
        "Devuelve el promedio mensual.",
        "月ごとの平均を返してください。",
        "Return the avergae monthly amount.",
    ],
)
def test_ratio_hint_rewrites_root_divide_function_without_lexical_gate(
    dialect,
    question,
):
    original = "SELECT DIVIDE(SUM(amount), COUNT(*)) AS result_value FROM payments"

    sql, diagnostics = normalize_explicit_ratio_sql(
        question=question,
        provided_context="",
        sql=original,
        dialect=dialect,
        intent_hints=("ratio_output",),
    )

    assert diagnostics["status"] == "normalized"
    assert diagnostics["converted_divide_functions"] == 1
    assert diagnostics["changed_divisions"] == 1
    assert diagnostics["division_scope"] == "root-projection"
    assert "DIVIDE" not in sql
    assert "CAST(SUM(amount) AS DOUBLE" in sql


def test_ratio_hint_keeps_a_query_without_division_unchanged():
    original = "SELECT active_members FROM member_summary"

    sql, diagnostics = normalize_explicit_ratio_sql(
        question="Give the active-member share.",
        provided_context="",
        sql=original,
        dialect="mysql",
        intent_hints=("ratio_output",),
    )

    assert sql == original
    assert diagnostics["reason"] == "no-eligible-division"


def test_ratio_hint_casts_only_root_result_divisions():
    original = (
        "SELECT SUM(amount) / COUNT(*) AS average_amount, "
        "(SELECT bucket / 10 FROM settings LIMIT 1) AS display_bucket "
        "FROM payments"
    )

    sql, diagnostics = normalize_explicit_ratio_sql(
        question="Return the average amount.",
        provided_context="",
        sql=original,
        dialect="mysql",
        intent_hints=("ratio_output",),
    )

    assert "CAST(SUM(amount) AS DOUBLE) / COUNT(*)" in sql
    assert "SELECT bucket / 10 FROM settings" in sql
    assert "CAST(bucket AS DOUBLE)" not in sql
    assert diagnostics["changed_divisions"] == 1
    assert diagnostics["division_scope"] == "root-projection"


def test_ratio_hint_does_not_cast_a_case_condition_division():
    original = (
        "SELECT CASE WHEN failures / attempts > 0 "
        "THEN SUM(amount) / COUNT(*) END AS average_amount FROM payments"
    )

    sql, diagnostics = normalize_explicit_ratio_sql(
        question="Return the average successful amount.",
        provided_context="",
        sql=original,
        dialect="mysql",
        intent_hints=("ratio_output",),
    )

    assert "failures / attempts > 0" in sql
    assert "CAST(failures AS DOUBLE)" not in sql
    assert "CAST(SUM(amount) AS DOUBLE) / COUNT(*)" in sql
    assert diagnostics["changed_divisions"] == 1


def test_ratio_hint_does_not_cast_an_aggregate_filter_division():
    original = (
        "SELECT COUNT(*) FILTER (WHERE failures / attempts > 0) / COUNT(*) "
        "AS completion_rate FROM jobs"
    )

    sql, diagnostics = normalize_explicit_ratio_sql(
        question="Return the completion rate.",
        provided_context="",
        sql=original,
        dialect="postgresql",
        intent_hints=("ratio_output",),
    )

    assert "failures / attempts > 0" in sql
    assert "CAST(failures AS DOUBLE PRECISION)" not in sql
    assert "CAST(COUNT(*) FILTER" in sql
    assert diagnostics["changed_divisions"] == 1


def test_context_wrapper_uses_only_an_activated_routing_hint():
    original = "SELECT SUM(active) / COUNT(*) AS member_share FROM members"
    ctx = SimpleNamespace(
        question="What precentage of members are active?",
        refined_question=None,
        provided_context="",
        sql=original,
        generated_sql=original,
        db_type="mysql",
        correction_intent_routing={
            "status": "activate",
            "verdict": "activate",
            "selected_intent": "percentage_output",
            "activation_allowed": True,
        },
        explicit_ratio_normalization={},
    )

    result = normalize_context(ctx)

    assert result.explicit_ratio_normalization["status"] == "normalized"
    assert result.explicit_ratio_normalization["scaled_percentages"] == 1

    blocked = SimpleNamespace(**vars(ctx))
    blocked.sql = original
    blocked.generated_sql = original
    blocked.correction_intent_routing = {
        **ctx.correction_intent_routing,
        "activation_allowed": False,
    }

    blocked_result = normalize_context(blocked)

    assert blocked_result.sql == original
    assert blocked_result.explicit_ratio_normalization["reason"] == (
        "no-explicit-ratio-intent"
    )

from types import SimpleNamespace

import pytest

from features.ask.aggregate_domain_normalization import (
    normalize_all_rows_aggregate_sql,
    normalize_context,
)

QUESTION = "What is the average weight of all female superheroes?"
EVIDENCE = "female refers to gender = 'Female'; average weight refers to AVG(weight_kg)"
SQL = (
    "SELECT AVG(s.weight_kg) FROM superhero s JOIN gender g "
    "ON s.gender_id = g.id WHERE g.gender = 'Female' AND s.weight_kg > 0"
)


def normalize(sql=SQL, *, question=QUESTION, evidence=EVIDENCE):
    return normalize_all_rows_aggregate_sql(
        question=question,
        provided_context=evidence,
        sql=sql,
        dialect="mysql",
    )


def test_removes_unrequested_positive_filter_from_all_rows_average():
    sql, diagnostics = normalize()

    assert sql == (
        "SELECT AVG(s.weight_kg) FROM superhero AS s JOIN gender AS g "
        "ON s.gender_id = g.id WHERE g.gender = 'Female'"
    )
    assert diagnostics["status"] == "normalized"
    assert diagnostics["removed_domain_filters"] == 1
    assert diagnostics["aggregate_column"] == "weight_kg"
    assert diagnostics["model_calls"] == 0
    assert diagnostics["execution_feedback"] is False


def test_removes_unrequested_positive_filter_for_employee_salaries():
    sql, diagnostics = normalize(
        sql=(
            "SELECT AVG(e.base_salary) FROM employees e JOIN departments d "
            "ON e.department_id = d.id WHERE d.name = 'Support' "
            "AND e.base_salary > 0"
        ),
        question="What is the average salary of all employees in Support?",
        evidence=(
            "average salary refers to AVG(base_salary); "
            "Support refers to departments.name = 'Support'"
        ),
    )

    assert sql == (
        "SELECT AVG(e.base_salary) FROM employees AS e JOIN departments AS d "
        "ON e.department_id = d.id WHERE d.name = 'Support'"
    )
    assert diagnostics["status"] == "normalized"
    assert diagnostics["removed_domain_filters"] == 1
    assert diagnostics["aggregate_column"] == "base_salary"


def test_explicit_positive_domain_is_preserved():
    sql, diagnostics = normalize(
        question="What is the average positive weight of all female superheroes?"
    )

    assert sql == SQL
    assert diagnostics["reason"] == "no-unqualified-all-rows-intent"


def test_requires_all_rows_wording_and_evidence_mapping():
    sql, diagnostics = normalize(
        question="What is the average weight of female superheroes?"
    )
    assert sql == SQL
    assert diagnostics["reason"] == "no-unqualified-all-rows-intent"

    sql, diagnostics = normalize(evidence="female refers to gender = 'Female'")
    assert sql == SQL
    assert diagnostics["reason"] == "no-unique-evidence-average-column"


def test_unrelated_positive_filter_is_preserved():
    original = SQL.replace("s.weight_kg > 0", "s.height_cm > 0")

    sql, diagnostics = normalize(original)

    assert sql == original
    assert diagnostics["reason"] == "no-unsupported-positive-domain-filter"


def test_or_predicate_is_not_treated_as_top_level_conjunct():
    original = SQL.replace(
        "g.gender = 'Female' AND s.weight_kg > 0",
        "g.gender = 'Female' OR s.weight_kg > 0",
    )

    sql, diagnostics = normalize(original)

    assert sql == original
    assert diagnostics["reason"] == "no-unsupported-positive-domain-filter"


def test_context_wrapper_updates_sql_and_diagnostics():
    ctx = SimpleNamespace(
        question=QUESTION,
        refined_question=None,
        provided_context=EVIDENCE,
        sql=SQL,
        generated_sql=SQL,
        db_type="postgresql",
        all_rows_aggregate_normalization={},
    )

    result = normalize_context(ctx)

    assert result is ctx
    assert "weight_kg > 0" not in result.sql
    assert result.generated_sql == result.sql
    assert result.all_rows_aggregate_normalization["status"] == "normalized"


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_all_rows_hint_handles_every_row_paraphrase(dialect):
    sql, diagnostics = normalize_all_rows_aggregate_sql(
        question="Include every superhero when finding the female average weight.",
        provided_context=EVIDENCE,
        sql=SQL,
        dialect=dialect,
        intent_hints=("all_rows_population",),
    )

    assert diagnostics["status"] == "normalized"
    assert "weight_kg > 0" not in sql


@pytest.mark.parametrize(
    "question",
    [
        "Include every superhero in the average.",
        "Tüm süper kahramanları ortalamaya dahil et.",
        "Incluye a todos los superhéroes en el promedio.",
        "incldue evry superhero in teh avergae",
    ],
)
def test_source_bound_all_rows_hint_needs_no_caller_evidence(question):
    sql, diagnostics = normalize_all_rows_aggregate_sql(
        question=question,
        provided_context="",
        sql=SQL,
        dialect="mysql",
        intent_hints=("all_rows_population",),
    )

    assert diagnostics["status"] == "normalized"
    assert diagnostics["aggregate_column"] == "weight_kg"
    assert "weight_kg > 0" not in sql


def test_all_rows_hint_bypasses_english_domain_patterns():
    sql, diagnostics = normalize_all_rows_aggregate_sql(
        question="Find the average recorded positive weight for female superheroes.",
        provided_context=EVIDENCE,
        sql=SQL,
        dialect="mysql",
        intent_hints=("all_rows_population",),
    )

    assert diagnostics["status"] == "normalized"
    assert "weight_kg > 0" not in sql


def test_all_rows_hint_keeps_a_grouped_average_unchanged():
    original = (
        "SELECT g.gender, AVG(s.weight_kg) FROM superhero s JOIN gender g "
        "ON s.gender_id = g.id WHERE s.weight_kg > 0 GROUP BY g.gender"
    )

    sql, diagnostics = normalize_all_rows_aggregate_sql(
        question="Include every superhero in the report.",
        provided_context="average weight refers to AVG(weight_kg)",
        sql=original,
        dialect="mysql",
        intent_hints=("all_rows_population",),
    )

    assert sql == original
    assert diagnostics["reason"] == "not-one-scalar-average"


def test_context_wrapper_uses_activated_all_rows_hint():
    ctx = SimpleNamespace(
        question="Include every superhero when finding the female average weight.",
        refined_question=None,
        provided_context=EVIDENCE,
        sql=SQL,
        generated_sql=SQL,
        db_type="mysql",
        correction_intent_routing={
            "status": "activate",
            "verdict": "activate",
            "selected_intent": "all_rows_population",
            "activation_allowed": True,
        },
        all_rows_aggregate_normalization={},
    )

    result = normalize_context(ctx)

    assert result.all_rows_aggregate_normalization["status"] == "normalized"
    assert "weight_kg > 0" not in result.sql

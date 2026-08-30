from __future__ import annotations

from types import SimpleNamespace

import pytest
import sqlglot

from features.ask.ranking_normalization import (
    normalize_context,
    normalize_extremum_entity_sql,
)


def normalize(question: str, sql: str, dialect: str = "mysql"):
    return normalize_extremum_entity_sql(
        question=question,
        sql=sql,
        dialect=dialect,
    )


def test_unbounded_highest_entity_becomes_tie_preserving_max_filter():
    sql, diagnostics = normalize(
        "Which schools have the highest enrollment?",
        "SELECT s.name, s.enrollment FROM school s ORDER BY s.enrollment DESC",
    )
    assert "SELECT s.name FROM school AS s" in sql
    assert "s.enrollment = (SELECT MAX(s.enrollment)" in sql
    assert "ORDER BY" not in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["tie_policy"] == "return-all-extremum-ties-v1"


def test_unbounded_lowest_entity_uses_min_filter_postgres():
    sql, diagnostics = normalize(
        "Who has the lowest score?",
        "SELECT p.name FROM person p ORDER BY p.score ASC",
        "postgresql",
    )
    assert "MIN(p.score)" in sql
    assert diagnostics["extremum"] == "minimum"
    sqlglot.parse_one(sql, read="postgres")


def test_existing_limit_is_unchanged():
    original = "SELECT name FROM school ORDER BY enrollment DESC LIMIT 1"
    sql, diagnostics = normalize("Which school has the highest enrollment?", original)
    assert sql == original
    assert diagnostics["reason"] == "bounded-or-complex-query"


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
@pytest.mark.parametrize(
    "question",
    [
        "Who has the highest salary?",
        "Who is the person with the highest salary?",
        "Who has the top salary?",
    ],
)
def test_who_wording_without_router_selection_keeps_a_singleton_limit(
    dialect,
    question,
):
    original = "SELECT name FROM employee ORDER BY salary DESC LIMIT 1"

    sql, diagnostics = normalize_extremum_entity_sql(
        question=question,
        sql=original,
        dialect=dialect,
    )

    assert sql == original
    assert diagnostics["reason"] in {
        "bounded-or-complex-query",
        "no-entity-superlative-intent",
    }


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
@pytest.mark.parametrize(
    "question",
    [
        "¿Qué empleados tienen el salario máximo?",
        "Whcih employes have teh higest salary?",
    ],
)
def test_extremum_hint_removes_singleton_limit_without_english_plural_gate(
    dialect,
    question,
):
    original = "SELECT name FROM employee ORDER BY salary DESC LIMIT 1"

    sql, diagnostics = normalize_extremum_entity_sql(
        question=question,
        sql=original,
        dialect=dialect,
        intent_hints=("entity_at_extremum",),
    )

    assert diagnostics["status"] == "normalized"
    assert diagnostics["removed_unrequested_singleton_limit"] is True
    assert "LIMIT" not in sql
    assert "MAX(salary)" in sql


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_who_with_an_explicit_plural_noun_can_return_ties(dialect):
    original = "SELECT name FROM employee ORDER BY salary DESC LIMIT 1"

    sql, diagnostics = normalize_extremum_entity_sql(
        question="Who are the employees with the highest salary?",
        sql=original,
        dialect=dialect,
    )

    assert "LIMIT" not in sql
    assert "MAX(salary)" in sql
    assert diagnostics["removed_unrequested_singleton_limit"] is True


def test_plural_entity_limit_becomes_tie_preserving_filter():
    original = (
        "SELECT s.School, f.`Enrollment (K-12)` FROM schools s JOIN frpm f "
        "ON s.CDSCode = f.CDSCode WHERE s.DOC = '31' "
        "ORDER BY f.`Enrollment (K-12)` DESC LIMIT 1"
    )

    sql, diagnostics = normalize(
        "Which state special schools have the highest number of enrollees?",
        original,
    )

    assert "SELECT s.School FROM schools AS s" in sql
    assert "MAX(f.`Enrollment (K-12)`)" in sql
    assert "LIMIT" not in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["removed_unrequested_singleton_limit"] is True


def test_non_entity_question_is_unchanged():
    original = "SELECT name FROM school ORDER BY enrollment DESC"
    sql, diagnostics = normalize("List schools by highest enrollment.", original)
    assert sql == original
    assert diagnostics["reason"] == "no-entity-superlative-intent"


def test_grouped_ranking_is_unchanged():
    original = (
        "SELECT region, COUNT(*) FROM school GROUP BY region ORDER BY COUNT(*) DESC"
    )
    sql, diagnostics = normalize("Which region has the most schools?", original)
    assert sql == original
    assert diagnostics["reason"] == "bounded-or-complex-query"


def test_only_order_metric_projection_is_unchanged():
    original = "SELECT enrollment FROM school ORDER BY enrollment DESC"
    sql, diagnostics = normalize("Which enrollment is highest?", original)
    assert sql == original
    assert diagnostics["reason"] == "no-entity-projection-remains"


def test_existing_scalar_max_filter_drops_unrequested_metric_projection():
    original = (
        "SELECT s.School, f.`Enrollment (K-12)` FROM schools s JOIN frpm f "
        "ON s.CDSCode = f.CDSCode WHERE s.DOC = '31' AND "
        "f.`Enrollment (K-12)` = (SELECT MAX(f2.`Enrollment (K-12)`) "
        "FROM frpm f2 JOIN schools s2 ON f2.CDSCode = s2.CDSCode "
        "WHERE s2.DOC = '31')"
    )

    sql, diagnostics = normalize(
        "Which state special schools have the highest number of enrollees?",
        original,
    )

    assert sql.startswith("SELECT s.School FROM schools AS s")
    assert "f.`Enrollment (K-12)` = (SELECT MAX(" in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["removed_order_metric_projections"] == 1
    assert diagnostics["existing_extremum_filter_preserved"] is True
    assert diagnostics["tie_policy"] == "preserve-existing-extremum-filter-v1"


def test_existing_scalar_min_filter_drops_metric_postgres():
    original = (
        "SELECT p.name, p.score FROM person p WHERE p.score = "
        "(SELECT MIN(p2.score) FROM person p2)"
    )

    sql, diagnostics = normalize(
        "Who has the lowest score?",
        original,
        "postgresql",
    )

    assert sql == (
        "SELECT p.name FROM person AS p WHERE p.score = "
        "(SELECT MIN(p2.score) FROM person AS p2)"
    )
    assert diagnostics["status"] == "normalized"
    assert diagnostics["extremum"] == "minimum"


@pytest.mark.parametrize(
    "question",
    [
        "¿Qué empleado tiene el salario máximo?",
        "Which employee has the higest salary?",
    ],
)
def test_extremum_hint_prunes_existing_filter_without_english_direction_gate(
    question,
):
    original = (
        "SELECT e.name, e.salary FROM employee e WHERE e.salary = "
        "(SELECT MAX(e2.salary) FROM employee e2)"
    )

    sql, diagnostics = normalize_extremum_entity_sql(
        question=question,
        sql=original,
        dialect="mysql",
        intent_hints=("entity_at_extremum",),
    )

    assert sql == (
        "SELECT e.name FROM employee AS e WHERE e.salary = "
        "(SELECT MAX(e2.salary) FROM employee AS e2)"
    )
    assert diagnostics["status"] == "normalized"
    assert diagnostics["extremum"] == "maximum"


def test_existing_extremum_filter_keeps_explicitly_requested_value():
    original = (
        "SELECT p.name, p.score FROM person p WHERE p.score = "
        "(SELECT MAX(p2.score) FROM person p2)"
    )

    sql, diagnostics = normalize(
        "Who has the highest score, and what is that score?",
        original,
    )

    assert sql == original
    assert diagnostics["reason"] == "not-one-order-expression"


def test_existing_extremum_filter_requires_matching_direction():
    original = (
        "SELECT p.name, p.score FROM person p WHERE p.score = "
        "(SELECT MIN(p2.score) FROM person p2)"
    )

    sql, diagnostics = normalize("Who has the highest score?", original)

    assert sql == original
    assert diagnostics["reason"] == "not-one-order-expression"


def test_existing_extremum_filter_rejects_or_predicate():
    original = (
        "SELECT p.name, p.score FROM person p WHERE p.active = 1 OR p.score = "
        "(SELECT MAX(p2.score) FROM person p2)"
    )

    sql, diagnostics = normalize("Who has the highest score?", original)

    assert sql == original
    assert diagnostics["reason"] == "not-one-order-expression"


def test_existing_extremum_filter_requires_projected_metric():
    original = (
        "SELECT p.name FROM person p WHERE p.score = "
        "(SELECT MAX(p2.score) FROM person p2)"
    )

    sql, diagnostics = normalize("Who has the highest score?", original)

    assert sql == original
    assert diagnostics["reason"] == "not-one-order-expression"


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_extremum_hint_handles_top_rank_paraphrase(dialect):
    sql, diagnostics = normalize_extremum_entity_sql(
        question="Name the school at the top of the enrollment ranking.",
        sql="SELECT s.name, s.enrollment FROM school s ORDER BY s.enrollment DESC",
        dialect=dialect,
        intent_hints=("entity_at_extremum",),
    )

    assert diagnostics["status"] == "normalized"
    assert "MAX(s.enrollment)" in sql
    assert "ORDER BY" not in sql


def test_extremum_hint_keeps_grouped_ranking_unchanged():
    original = (
        "SELECT region, COUNT(*) FROM school GROUP BY region ORDER BY COUNT(*) DESC"
    )

    sql, diagnostics = normalize_extremum_entity_sql(
        question="Name the region at the top of the school-count ranking.",
        sql=original,
        dialect="mysql",
        intent_hints=("entity_at_extremum",),
    )

    assert sql == original
    assert diagnostics["reason"] == "bounded-or-complex-query"


def test_context_wrapper_uses_activated_extremum_hint():
    original = "SELECT s.name, s.enrollment FROM school s ORDER BY s.enrollment DESC"
    ctx = SimpleNamespace(
        question="Name the school at the top of the enrollment ranking.",
        refined_question=None,
        sql=original,
        generated_sql=original,
        db_type="mysql",
        correction_intent_routing={
            "status": "activate",
            "verdict": "activate",
            "selected_intent": "entity_at_extremum",
            "activation_allowed": True,
        },
        extremum_entity_normalization={},
    )

    result = normalize_context(ctx)

    assert result.extremum_entity_normalization["status"] == "normalized"
    assert "MAX(s.enrollment)" in result.sql

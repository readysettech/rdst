from types import SimpleNamespace

import pytest

from features.ask.categorical_normalization import (
    normalize_context,
    normalize_unbounded_categorical_sql,
)

QUESTION = "Does this team have a slow, balanced or fast speed class?"
EVIDENCE = "speed class refers to buildUpPlaySpeedClass"
SQL = (
    "SELECT ta.buildUpPlaySpeedClass FROM Team t JOIN Team_Attributes ta "
    "ON t.team_api_id = ta.team_api_id WHERE t.team_long_name = 'Example' LIMIT 1"
)


def normalize(sql=SQL, question=QUESTION, evidence=EVIDENCE, dialect="mysql"):
    return normalize_unbounded_categorical_sql(
        question=question,
        provided_context=evidence,
        sql=sql,
        dialect=dialect,
    )


def test_returns_all_distinct_evidence_mapped_categories():
    sql, diagnostics = normalize()

    assert sql.startswith("SELECT DISTINCT ta.buildUpPlaySpeedClass")
    assert "LIMIT" not in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["projected_category"] == "buildUpPlaySpeedClass"


def test_returns_all_distinct_product_lifecycle_states():
    sql, diagnostics = normalize(
        sql=(
            "SELECT p.lifecycle_state FROM products p WHERE p.catalog_id = 42 LIMIT 1"
        ),
        question=("Which product lifecycle states are planned, active, or retired?"),
        evidence="lifecycle state refers to products.lifecycle_state",
    )

    assert sql == (
        "SELECT DISTINCT p.lifecycle_state FROM products AS p WHERE p.catalog_id = 42"
    )
    assert diagnostics["status"] == "normalized"
    assert diagnostics["projected_category"] == "lifecycle_state"
    assert diagnostics["removed_limit"] == 1


def test_keeps_ordered_top_one_query():
    original = SQL.replace(" LIMIT 1", " ORDER BY ta.created_at DESC LIMIT 1")
    sql, diagnostics = normalize(original)

    assert sql == original
    assert diagnostics["reason"] == "ordered-or-complex-query"


def test_keeps_explicit_singleton_request():
    sql, diagnostics = normalize(question="Give one result: slow or fast speed class")

    assert sql == SQL
    assert diagnostics["reason"] == "no-unbounded-alternatives-intent"


def test_requires_exact_evidence_column_mapping():
    sql, diagnostics = normalize(evidence="speed class is a business category")

    assert sql == SQL
    assert diagnostics["reason"] == "projection-not-evidence-mapped"


def test_requires_one_direct_projection():
    original = SQL.replace(
        "ta.buildUpPlaySpeedClass",
        "ta.buildUpPlaySpeedClass, ta.team_api_id",
        1,
    )
    sql, diagnostics = normalize(original)

    assert sql == original
    assert diagnostics["reason"] == "not-one-categorical-projection"


def test_context_wrapper_supports_postgres():
    ctx = SimpleNamespace(
        question=QUESTION,
        refined_question=None,
        provided_context=EVIDENCE,
        db_type="postgresql",
        sql=SQL,
        generated_sql=SQL,
        unbounded_categorical_normalization={},
    )

    result = normalize_context(ctx)

    assert result is ctx
    assert result.sql.startswith("SELECT DISTINCT")
    assert result.generated_sql == result.sql
    assert result.unbounded_categorical_normalization["status"] == "normalized"


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_category_hint_handles_every_state_paraphrase(dialect):
    original = (
        "SELECT p.lifecycle_state FROM products p WHERE p.catalog_id = 42 LIMIT 1"
    )

    sql, diagnostics = normalize_unbounded_categorical_sql(
        question="Return every lifecycle state available in this catalog.",
        provided_context="lifecycle state refers to products.lifecycle_state",
        sql=original,
        dialect=dialect,
        intent_hints=("all_matching_categories",),
    )

    assert diagnostics["status"] == "normalized"
    assert sql.startswith("SELECT DISTINCT")
    assert "LIMIT" not in sql


@pytest.mark.parametrize(
    "question",
    [
        "Devuelve todos los estados disponibles.",
        "Retrun evry avalable state.",
    ],
)
def test_category_hint_needs_no_evidence_or_english_keyword(question):
    original = (
        "SELECT p.lifecycle_state FROM products p WHERE p.catalog_id = 42 LIMIT 1"
    )

    sql, diagnostics = normalize_unbounded_categorical_sql(
        question=question,
        provided_context="",
        sql=original,
        dialect="mysql",
        intent_hints=("all_matching_categories",),
    )

    assert diagnostics["status"] == "normalized"
    assert sql.startswith("SELECT DISTINCT")
    assert "LIMIT" not in sql


def test_category_hint_bypasses_english_singleton_patterns():
    sql, diagnostics = normalize_unbounded_categorical_sql(
        question="Give a single lifecycle state from this catalog.",
        provided_context="lifecycle state refers to products.lifecycle_state",
        sql=(
            "SELECT p.lifecycle_state FROM products p WHERE p.catalog_id = 42 LIMIT 1"
        ),
        dialect="mysql",
        intent_hints=("all_matching_categories",),
    )

    assert "DISTINCT" in sql
    assert diagnostics["status"] == "normalized"


def test_category_hint_keeps_an_ordered_top_one_query_unchanged():
    original = SQL.replace(" LIMIT 1", " ORDER BY ta.created_at DESC LIMIT 1")

    sql, diagnostics = normalize_unbounded_categorical_sql(
        question="Return every speed class available for this team.",
        provided_context=EVIDENCE,
        sql=original,
        dialect="mysql",
        intent_hints=("all_matching_categories",),
    )

    assert sql == original
    assert diagnostics["reason"] == "ordered-or-complex-query"


def test_context_wrapper_uses_activated_category_hint():
    original = (
        "SELECT p.lifecycle_state FROM products p WHERE p.catalog_id = 42 LIMIT 1"
    )
    ctx = SimpleNamespace(
        question="Return every lifecycle state available in this catalog.",
        refined_question=None,
        provided_context="lifecycle state refers to products.lifecycle_state",
        db_type="mysql",
        sql=original,
        generated_sql=original,
        correction_intent_routing={
            "status": "activate",
            "verdict": "activate",
            "selected_intent": "all_matching_categories",
            "activation_allowed": True,
        },
        unbounded_categorical_normalization={},
    )

    result = normalize_context(ctx)

    assert result.unbounded_categorical_normalization["status"] == "normalized"
    assert result.sql.startswith("SELECT DISTINCT")

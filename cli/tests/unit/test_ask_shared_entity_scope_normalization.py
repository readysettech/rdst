from types import SimpleNamespace

import pytest

from features.ask.engine.ask3.types import ColumnInfo, SchemaInfo, TableInfo
from features.ask.shared_entity_scope_normalization import (
    normalize_context,
    normalize_shared_entity_scope_sql,
)

QUESTION = (
    "How often does account number 3 request an account statement to be "
    "released? What was the aim of debiting 3539 in total?"
)
SQL = (
    "SELECT (SELECT frequency FROM account WHERE account_id = 3) "
    "AS statement_frequency, (SELECT k_symbol FROM `order` GROUP BY k_symbol "
    "HAVING SUM(amount) = 3539) AS debit_purpose"
)
CROSS_JOIN_SQL = (
    "SELECT a.frequency, o.k_symbol FROM "
    "(SELECT frequency FROM account WHERE account_id = 3) a CROSS JOIN "
    "(SELECT k_symbol FROM `order` GROUP BY k_symbol "
    "HAVING SUM(amount) = 3539) o"
)
DIRECT_TABLE_CROSS_JOIN_SQL = (
    "SELECT a.frequency, o.k_symbol FROM account a CROSS JOIN "
    "(SELECT k_symbol FROM `order` GROUP BY k_symbol "
    "HAVING SUM(amount) = 3539) o WHERE a.account_id = 3"
)


def schema(*, target_type="int"):
    return SchemaInfo(
        target="financial",
        db_type="mysql",
        tables={
            "account": TableInfo(
                name="account",
                columns={
                    "account_id": ColumnInfo("account_id", "int"),
                    "frequency": ColumnInfo("frequency", "text"),
                },
            ),
            "order": TableInfo(
                name="order",
                columns={
                    "account_id": ColumnInfo("account_id", target_type),
                    "k_symbol": ColumnInfo("k_symbol", "text"),
                    "amount": ColumnInfo("amount", "double"),
                },
            ),
        },
    )


def normalize(sql=SQL, *, question=QUESTION, schema_info=None):
    return normalize_shared_entity_scope_sql(
        question=question,
        sql=sql,
        dialect="mysql",
        schema_info=schema_info or schema(),
    )


def test_propagates_source_grounded_shared_identifier_to_aggregate_branch():
    sql, diagnostics = normalize()

    assert "FROM `order` WHERE account_id = 3 GROUP BY" in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["propagated_scopes"] == 1
    assert diagnostics["source_table"] == "account"
    assert diagnostics["target_table"] == "order"
    assert diagnostics["column"] == "account_id"
    assert "literal" not in diagnostics
    assert diagnostics["model_calls"] == 0
    assert diagnostics["execution_feedback"] is False


def test_propagates_tenant_scope_across_invoice_and_payment_tables():
    billing_schema = SchemaInfo(
        target="billing",
        db_type="mysql",
        tables={
            "invoices": TableInfo(
                name="invoices",
                columns={
                    "invoice_id": ColumnInfo("invoice_id", "int"),
                    "tenant_id": ColumnInfo("tenant_id", "int"),
                    "currency": ColumnInfo("currency", "text"),
                },
            ),
            "payments": TableInfo(
                name="payments",
                columns={
                    "tenant_id": ColumnInfo("tenant_id", "int"),
                    "method": ColumnInfo("method", "text"),
                    "amount": ColumnInfo("amount", "double"),
                },
            ),
        },
    )
    question = (
        "What currency is invoice 734 for tenant 42? "
        "Which payment method totals 900 for that tenant?"
    )
    original_sql = (
        "SELECT (SELECT currency FROM invoices WHERE invoice_id = 734 "
        "AND tenant_id = 42) AS invoice_currency, "
        "(SELECT method FROM payments GROUP BY method HAVING SUM(amount) = 900) "
        "AS payment_method"
    )

    sql, diagnostics = normalize_shared_entity_scope_sql(
        question=question,
        sql=original_sql,
        dialect="mysql",
        schema_info=billing_schema,
    )

    assert "FROM payments WHERE tenant_id = 42 GROUP BY" in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["source_table"] == "invoices"
    assert diagnostics["target_table"] == "payments"
    assert diagnostics["column"] == "tenant_id"


def test_single_question_is_unchanged():
    sql, diagnostics = normalize(question="What is the debit purpose for account 3?")

    assert sql == SQL
    assert diagnostics["reason"] == "not-a-paired-question"


def test_non_scalar_union_shape_is_unchanged():
    union = (
        "SELECT frequency FROM account WHERE account_id = 3 UNION ALL "
        "SELECT k_symbol FROM `order` WHERE account_to = 3539"
    )

    sql, diagnostics = normalize(union)

    assert sql == union
    assert diagnostics["reason"] == "not-two-supported-answer-branches"


def test_propagates_into_outer_grouped_answer_branch():
    outer = (
        "SELECT (SELECT frequency FROM account WHERE account_id = 3) "
        "AS statement_frequency, k_symbol AS debit_purpose FROM `order` "
        "GROUP BY k_symbol HAVING SUM(amount) = 3539"
    )

    sql, diagnostics = normalize(outer)

    assert "FROM `order` WHERE account_id = 3 GROUP BY" in sql
    assert diagnostics["status"] == "normalized"


def test_outer_answer_requires_direct_projection_to_be_grouped():
    not_grouped = (
        "SELECT (SELECT frequency FROM account WHERE account_id = 3) "
        "AS statement_frequency, k_symbol AS debit_purpose FROM `order` "
        "GROUP BY amount HAVING SUM(amount) = 3539"
    )

    sql, diagnostics = normalize(not_grouped)

    assert sql == not_grouped
    assert diagnostics["reason"] == "not-two-supported-answer-branches"


def test_outer_answer_requires_exactly_one_scalar_branch():
    no_scalar = (
        "SELECT account_id, k_symbol FROM `order` GROUP BY account_id, k_symbol "
        "HAVING SUM(amount) = 3539"
    )

    sql, diagnostics = normalize(no_scalar)

    assert sql == no_scalar
    assert diagnostics["reason"] == "not-two-supported-answer-branches"


def test_propagates_across_two_scalar_derived_tables():
    sql, diagnostics = normalize(CROSS_JOIN_SQL)

    assert "FROM `order` WHERE account_id = 3 GROUP BY" in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["source_table"] == "account"
    assert diagnostics["target_table"] == "order"


def test_propagates_from_direct_table_into_cross_joined_grouped_branch():
    sql, diagnostics = normalize(DIRECT_TABLE_CROSS_JOIN_SQL)

    assert "FROM `order` WHERE account_id = 3 GROUP BY" in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["source_table"] == "account"
    assert diagnostics["target_table"] == "order"


def test_direct_table_cross_join_target_with_existing_scope_is_unchanged():
    scoped = DIRECT_TABLE_CROSS_JOIN_SQL.replace(
        "FROM `order` GROUP BY",
        "FROM `order` WHERE account_id = 3 GROUP BY",
    )

    sql, diagnostics = normalize(scoped)

    assert sql == scoped
    assert diagnostics["reason"] == "no-eligible-shared-scope"


def test_direct_table_cross_join_requires_cross_presentation_join():
    joined = DIRECT_TABLE_CROSS_JOIN_SQL.replace(" CROSS JOIN ", " JOIN ").replace(
        ") o WHERE", ") o ON a.frequency = o.k_symbol WHERE"
    )

    sql, diagnostics = normalize(joined)

    assert sql == joined
    assert diagnostics["reason"] == "not-two-supported-answer-branches"


def test_direct_table_cross_join_requires_one_projection_per_branch():
    extra = DIRECT_TABLE_CROSS_JOIN_SQL.replace(
        "SELECT a.frequency, o.k_symbol",
        "SELECT a.frequency, a.account_id, o.k_symbol",
    )

    sql, diagnostics = normalize(extra)

    assert sql == extra
    assert diagnostics["reason"] == "not-two-supported-answer-branches"


def test_direct_table_cross_join_requires_safe_source_equality():
    unsafe = DIRECT_TABLE_CROSS_JOIN_SQL.replace(
        "WHERE a.account_id = 3",
        "WHERE a.account_id = 3 OR a.account_id = 4",
    )

    sql, diagnostics = normalize(unsafe)

    assert sql == unsafe
    assert diagnostics["reason"] == "no-eligible-shared-scope"


def test_cross_join_target_with_existing_scope_is_unchanged():
    scoped = CROSS_JOIN_SQL.replace(
        "FROM `order` GROUP BY",
        "FROM `order` WHERE account_id = 3 GROUP BY",
    )

    sql, diagnostics = normalize(scoped)

    assert sql == scoped
    assert diagnostics["reason"] == "no-eligible-shared-scope"


def test_ordinary_joined_derived_tables_are_unchanged():
    joined = CROSS_JOIN_SQL.replace(" CROSS JOIN ", " JOIN ").replace(
        ") o", ") o ON a.frequency = o.k_symbol"
    )

    sql, diagnostics = normalize(joined)

    assert sql == joined
    assert diagnostics["reason"] == "not-two-supported-answer-branches"


def test_cross_join_requires_outer_projection_to_match_each_branch():
    mismatched = CROSS_JOIN_SQL.replace("o.k_symbol", "o.amount", 1)

    sql, diagnostics = normalize(mismatched)

    assert sql == mismatched
    assert diagnostics["reason"] == "not-two-supported-answer-branches"


def test_existing_target_scope_is_unchanged():
    scoped = SQL.replace(
        "FROM `order` GROUP BY",
        "FROM `order` WHERE account_id = 3 GROUP BY",
    )

    sql, diagnostics = normalize(scoped)

    assert sql == scoped
    assert diagnostics["reason"] == "no-eligible-shared-scope"


def test_nonaggregate_target_is_unchanged():
    ungrouped = SQL.replace(
        " GROUP BY k_symbol HAVING SUM(amount) = 3539",
        " WHERE amount = 3539",
    )

    sql, diagnostics = normalize(ungrouped)

    assert sql == ungrouped
    assert diagnostics["reason"] == "no-eligible-shared-scope"


def test_missing_or_incompatible_shared_schema_column_is_unchanged():
    missing = schema()
    del missing.tables["order"].columns["account_id"]
    sql, diagnostics = normalize(schema_info=missing)
    assert sql == SQL
    assert diagnostics["reason"] == "no-eligible-shared-scope"

    sql, diagnostics = normalize(schema_info=schema(target_type="varchar"))
    assert sql == SQL
    assert diagnostics["reason"] == "no-eligible-shared-scope"


def test_scope_literal_must_appear_in_question():
    sql, diagnostics = normalize(
        question=(
            "How often does this account request a statement? "
            "What was the aim of debiting 3539 in total?"
        )
    )

    assert sql == SQL
    assert diagnostics["reason"] == "no-eligible-shared-scope"


def test_routed_scope_still_requires_the_exact_literal_in_the_question():
    sql, diagnostics = normalize_shared_entity_scope_sql(
        question=(
            "Devuelve la frecuencia de la cuenta y el propósito del débito "
            "cuyo total es 3539."
        ),
        sql=SQL,
        dialect="mysql",
        schema_info=schema(),
        intent_hints=("shared_scope_all_answers",),
    )

    assert sql == SQL
    assert diagnostics["reason"] == "no-eligible-shared-scope"


def test_context_wrapper_updates_sql_and_diagnostics():
    ctx = SimpleNamespace(
        sql=SQL,
        generated_sql=SQL,
        question=QUESTION,
        refined_question=None,
        db_type="mysql",
        schema_info=schema(),
        shared_entity_scope_normalization={},
    )

    result = normalize_context(ctx)

    assert result is ctx
    assert result.sql == result.generated_sql
    assert "WHERE account_id = 3 GROUP BY" in result.sql
    assert result.shared_entity_scope_normalization["status"] == "normalized"


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_shared_scope_hint_handles_one_sentence_paraphrase(dialect):
    original = SQL if dialect == "mysql" else SQL.replace("`order`", '"order"')

    sql, diagnostics = normalize_shared_entity_scope_sql(
        question=(
            "For account 3, return the statement frequency and the debit purpose "
            "whose total is 3539."
        ),
        sql=original,
        dialect=dialect,
        schema_info=schema(),
        intent_hints=("shared_scope_all_answers",),
    )

    assert diagnostics["status"] == "normalized"
    assert "WHERE account_id = 3 GROUP BY" in sql


@pytest.mark.parametrize(
    "question",
    [
        (
            "Para la cuenta 3, devuelve la frecuencia y el propósito del débito "
            "cuyo total es 3539."
        ),
        "For acount 3, retrun frequency and debit purpose totaling 3539.",
    ],
)
def test_shared_scope_hint_needs_literal_but_not_english_identifier_term(question):
    sql, diagnostics = normalize_shared_entity_scope_sql(
        question=question,
        sql=SQL,
        dialect="mysql",
        schema_info=schema(),
        intent_hints=("shared_scope_all_answers",),
    )

    assert diagnostics["status"] == "normalized"
    assert "WHERE account_id = 3 GROUP BY" in sql


def test_shared_scope_hint_keeps_incompatible_columns_unchanged():
    sql, diagnostics = normalize_shared_entity_scope_sql(
        question=(
            "For account 3, return the statement frequency and the debit purpose "
            "whose total is 3539."
        ),
        sql=SQL,
        dialect="mysql",
        schema_info=schema(target_type="varchar"),
        intent_hints=("shared_scope_all_answers",),
    )

    assert sql == SQL
    assert diagnostics["reason"] == "no-eligible-shared-scope"


def test_context_wrapper_uses_activated_shared_scope_hint():
    ctx = SimpleNamespace(
        sql=SQL,
        generated_sql=SQL,
        question=(
            "For account 3, return the statement frequency and the debit purpose "
            "whose total is 3539."
        ),
        refined_question=None,
        db_type="mysql",
        schema_info=schema(),
        correction_intent_routing={
            "status": "activate",
            "verdict": "activate",
            "selected_intent": "shared_scope_all_answers",
            "activation_allowed": True,
        },
        shared_entity_scope_normalization={},
    )

    result = normalize_context(ctx)

    assert result.shared_entity_scope_normalization["status"] == "normalized"
    assert "WHERE account_id = 3 GROUP BY" in result.sql

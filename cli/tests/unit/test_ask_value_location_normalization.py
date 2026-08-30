from types import SimpleNamespace

from features.ask.engine.ask3.types import ColumnInfo, SchemaInfo, TableInfo
from features.ask.value_location_normalization import (
    normalize_ambiguous_value_location_sql,
    normalize_context,
)

Q412_SQL = """SELECT fd.name
FROM foreign_data fd JOIN cards c ON fd.uuid = c.uuid
WHERE fd.language = 'French'
  AND c.type LIKE '%Creature%'
  AND c.layout = 'normal'
  AND c.borderColor = 'black'
  AND c.artist = 'Matthew D. Wilson'"""


def schema():
    return SchemaInfo(
        target="card_games",
        db_type="mysql",
        tables={
            "cards": TableInfo(
                name="cards",
                columns={
                    "type": ColumnInfo("type", "text"),
                    "types": ColumnInfo("types", "text"),
                    "layout": ColumnInfo("layout", "text"),
                },
            ),
            "foreign_data": TableInfo(
                name="foreign_data",
                columns={"name": ColumnInfo("name", "text")},
            ),
        },
    )


def dominant_sibling_executor(sql, _config):
    support = 101 if "`types`" in sql or '"types"' in sql else 1
    return {"success": True, "rows": [[support]], "columns": ["COUNT(*)"]}


def normalize(sql=Q412_SQL, *, question=None, executor=dominant_sibling_executor):
    return normalize_ambiguous_value_location_sql(
        question=question
        or (
            "What is the foreign name of the card in French of type Creature, "
            "normal layout and black border color, by artist Matthew D. Wilson?"
        ),
        provided_context="in French refers to language = 'French'",
        sql=sql,
        dialect="mysql",
        schema_info=schema(),
        db_executor=executor,
        target_config={"engine": "mysql"},
    )


def test_relocates_unsupported_contains_to_dominant_exact_sibling():
    sql, diagnostics = normalize()

    assert "c.types = 'Creature'" in sql
    assert "c.type LIKE" not in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["probe_count"] == 2
    assert "support" not in diagnostics
    assert diagnostics["execution_feedback"] is False


def test_relocates_product_category_to_dominant_categories_column():
    catalog_schema = SchemaInfo(
        target="catalog",
        db_type="mysql",
        tables={
            "products": TableInfo(
                name="products",
                columns={
                    "product_name": ColumnInfo("product_name", "text"),
                    "category": ColumnInfo("category", "text"),
                    "categories": ColumnInfo("categories", "text"),
                    "status": ColumnInfo("status", "text"),
                },
            )
        },
    )
    original_sql = (
        "SELECT p.product_name FROM products p "
        "WHERE p.category LIKE '%Hardware%' AND p.status = 'active'"
    )

    def executor(sql, _config):
        support = 101 if "`categories`" in sql else 1
        return {"success": True, "rows": [[support]], "columns": ["COUNT(*)"]}

    sql, diagnostics = normalize_ambiguous_value_location_sql(
        question="Which active products are in the Hardware category?",
        provided_context="Hardware is a product category.",
        sql=original_sql,
        dialect="mysql",
        schema_info=catalog_schema,
        db_executor=executor,
        target_config={"engine": "mysql"},
    )

    assert "p.categories = 'Hardware'" in sql
    assert "p.category LIKE" not in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["source_column"] == "products.category"
    assert diagnostics["candidate_column"] == "products.categories"
    assert diagnostics["probe_count"] == 2


def test_prefix_pattern_is_unchanged_without_probes():
    calls = []

    def executor(sql, config):
        calls.append((sql, config))
        return dominant_sibling_executor(sql, config)

    sql, diagnostics = normalize(
        Q412_SQL.replace("'%Creature%'", "'Creature%'"), executor=executor
    )

    assert sql == Q412_SQL.replace("'%Creature%'", "'Creature%'")
    assert diagnostics["probe_count"] == 0
    assert not calls


def test_explicit_partial_match_intent_is_unchanged_without_probes():
    calls = []

    def executor(sql, config):
        calls.append((sql, config))
        return dominant_sibling_executor(sql, config)

    sql, diagnostics = normalize(
        question="Find cards whose type contains Creature", executor=executor
    )

    assert sql == Q412_SQL
    assert diagnostics["probe_count"] == 0
    assert not calls


def test_weak_support_is_unchanged():
    def executor(sql, _config):
        support = 7 if "`types`" in sql else 2
        return {"success": True, "rows": [[support]]}

    sql, diagnostics = normalize(executor=executor)

    assert sql == Q412_SQL
    assert diagnostics["reason"] == "candidate-support-not-dominant"


def test_probe_failure_is_fail_open():
    sql, diagnostics = normalize(
        executor=lambda *_: {"success": False, "rows": [], "error": "timeout"}
    )

    assert sql == Q412_SQL
    assert diagnostics["reason"] == "database-probe-failed"


def test_raised_database_error_is_fail_open():
    def executor(*_args):
        raise RuntimeError("database connection dropped")

    sql, diagnostics = normalize(executor=executor)

    assert sql == Q412_SQL
    assert diagnostics["status"] == "unchanged"
    assert diagnostics["reason"] == "database-probe-failed"


def test_context_wrapper_updates_sql_and_diagnostics():
    ctx = SimpleNamespace(
        sql=Q412_SQL,
        generated_sql=Q412_SQL,
        question=(
            "What is the foreign name of the card in French of type Creature, "
            "normal layout and black border color, by artist Matthew D. Wilson?"
        ),
        refined_question=None,
        provided_context="in French refers to language = 'French'",
        db_type="mysql",
        schema_info=schema(),
        target_config={"engine": "mysql"},
        value_location_normalization={},
    )

    result = normalize_context(ctx, dominant_sibling_executor)

    assert result is ctx
    assert result.sql != Q412_SQL
    assert result.generated_sql == result.sql
    assert result.value_location_normalization["status"] == "normalized"

"""Focused contracts for audit index recommendation normalization."""

from features.audit.index_dedupe import normalize_index_rec_sql


def test_normalize_index_rec_sql_synthesizes_runnable_create_index():
    recommendations = [
        {
            "table": "public.orders",
            "columns": ["customer_id", "created_at"],
            "reason": "Frequent customer history lookup",
        }
    ]

    normalized = normalize_index_rec_sql(recommendations)

    expected = (
        "CREATE INDEX idx_public_orders_customer_id_created_at "
        "ON public.orders (customer_id, created_at);"
    )
    assert normalized[0]["create_index_sql"] == expected
    assert normalized[0]["sql"] == expected
    assert expected.startswith("CREATE INDEX ")
    assert expected.endswith(";")


def test_normalize_index_rec_sql_canonicalizes_model_sql_field():
    recommendations = [
        {
            "table": "orders",
            "columns": ["customer_id"],
            "sql": "CREATE INDEX orders_customer_idx ON orders (customer_id);",
        }
    ]

    normalized = normalize_index_rec_sql(recommendations)

    assert normalized[0]["create_index_sql"] == normalized[0]["sql"]

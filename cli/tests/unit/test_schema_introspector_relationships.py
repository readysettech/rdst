from unittest.mock import Mock

from features.schema.semantic_layer.introspector import SchemaIntrospector
from features.schema.semantic_models import SemanticLayer, TableAnnotation


def _postgres_introspector() -> SchemaIntrospector:
    return SchemaIntrospector(
        {
            "engine": "postgresql",
            "host": "localhost",
            "port": 5432,
            "user": "user",
            "password": "password",
            "database": "fixture",
        }
    )


def test_postgres_relationships_use_catalog_object_ids_and_paired_columns():
    cursor = Mock()
    cursor.fetchall.return_value = [
        ("order_items", "account_id", "orders", "account_id"),
        ("order_items", "order_id", "orders", "id"),
    ]
    layer = SemanticLayer(
        target="fixture",
        tables={"order_items": TableAnnotation(name="order_items")},
    )

    _postgres_introspector()._add_postgres_relationships(cursor, layer)

    query = cursor.execute.call_args.args[0]
    assert "FROM pg_constraint" in query
    assert "constraint_row.conrelid" in query
    assert "constraint_row.confrelid" in query
    assert "unnest(" in query
    assert "source_namespace.nspname = 'public'" in query
    assert [
        relationship.join_pattern
        for relationship in layer.tables["order_items"].relationships
    ] == [
        "order_items.account_id = orders.account_id",
        "order_items.order_id = orders.id",
    ]

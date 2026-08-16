from unittest.mock import MagicMock, Mock, patch

from features.schema.semantic_layer.introspector import SchemaIntrospector
from features.schema.semantic_models import TableAnnotation


def _introspector():
    return SchemaIntrospector(
        {
            "engine": "mysql",
            "host": "localhost",
            "port": 3306,
            "user": "user",
            "password": "password",
            "database": "fixture",
        }
    )


def test_mysql_introspection_includes_views_without_index_queries():
    cursor = Mock()
    cursor.fetchall.return_value = [("orders_view", "VIEW")]
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor
    introspector = _introspector()
    introspector._introspect_mysql_table = Mock(
        return_value=TableAnnotation(name="orders_view")
    )
    introspector._add_mysql_relationships = Mock()

    with patch(
        "features.schema.semantic_layer.introspector.create_mysql_connection_from_params",
        return_value=connection,
    ):
        layer = introspector._introspect_mysql("fixture", 20, True)

    schema_query = cursor.execute.call_args_list[0].args[0]
    assert "'BASE TABLE', 'VIEW'" in schema_query
    introspector._introspect_mysql_table.assert_called_once_with(
        cursor,
        "orders_view",
        "fixture",
        20,
        True,
        include_indexes=False,
    )
    assert list(layer.tables) == ["orders_view"]
    connection.close.assert_called_once()


def test_mysql_view_with_unknown_row_estimate_keeps_structural_columns():
    cursor = Mock()
    cursor.fetchone.return_value = (None,)
    cursor.fetchall.return_value = [("id", "int", "NO", "", None, "")]

    table = _introspector()._introspect_mysql_table(
        cursor,
        "orders_view",
        "fixture",
        20,
        True,
        include_indexes=False,
    )

    assert table.row_estimate == "0"
    assert table.columns["id"].data_type == "int"
    assert all(
        "SHOW INDEX" not in call.args[0] for call in cursor.execute.call_args_list
    )


def test_mysql_view_with_unknown_row_estimate_still_samples_enum_values():
    cursor = Mock()
    cursor.fetchone.return_value = (None,)
    cursor.fetchall.return_value = [("status", "varchar(20)", "YES", "", None, "")]
    introspector = _introspector()
    introspector._sample_mysql_enum_values = Mock(return_value=["active", "paused"])

    table = introspector._introspect_mysql_table(
        cursor,
        "orders_view",
        "fixture",
        20,
        True,
        include_indexes=False,
    )

    introspector._sample_mysql_enum_values.assert_called_once_with(
        cursor, "orders_view", "status", 20
    )
    assert table.columns["status"].data_type == "enum"
    assert table.columns["status"].enum_values == {
        "active": "active",
        "paused": "paused",
    }

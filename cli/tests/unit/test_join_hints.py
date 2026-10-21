from features.schema.join_hints import PossibleJoin, possible_joins
from features.schema.prompt_serialization import (
    _format_semantic_schema,
    format_semantic_schema_compact,
)
from features.schema.semantic_models import (
    ColumnAnnotation,
    IndexAnnotation,
    Relationship,
    SemanticLayer,
    TableAnnotation,
)


def _table(name, columns, relationships=(), primary=None):
    return TableAnnotation(
        name=name,
        columns={
            column: ColumnAnnotation(name=column, data_type=data_type)
            for column, data_type in columns.items()
        },
        relationships=list(relationships),
        indexes=(
            {
                f"{name}_pkey": IndexAnnotation(
                    f"{name}_pkey", [primary], is_primary=True
                )
            }
            if primary
            else {}
        ),
    )


def _to_sites(table):
    return Relationship("sites", f"{table}.zoneref = sites.zoneregistry", "many_to_one")


def _archeology():
    return SemanticLayer(
        target="archeology",
        tables={
            "sites": _table("sites", {"zoneregistry": "string", "name": "string"}),
            "scanmesh": _table(
                "scanmesh",
                {"zoneref": "string", "name": "string", "facets": "integer"},
                [_to_sites("scanmesh")],
            ),
            "scanprocessing": _table(
                "scanprocessing",
                {"zoneref": "string", "name": "string", "facets": "integer"},
                [_to_sites("scanprocessing")],
            ),
        },
    )


def test_columns_that_reference_the_same_key_are_a_possible_join():
    assert possible_joins(_archeology()) == [
        PossibleJoin(
            (("scanmesh", "zoneref"), ("scanprocessing", "zoneref")),
            ("sites", "zoneregistry"),
        )
    ]


def test_shared_key_names_join_but_generic_and_attribute_names_do_not():
    layer = SemanticLayer(
        target="shop",
        tables={
            "orders": _table(
                "orders", {"id": "integer", "CustomerID": "bigint", "status": "text"}
            ),
            "invoices": _table(
                "invoices", {"id": "integer", "CustomerID": "int", "status": "text"}
            ),
            "notes": _table("notes", {"CustomerID": "text"}),
            "customers": _table("customers", {"number": "integer"}, primary="number"),
            "visits": _table("visits", {"number": "integer"}),
        },
    )

    assert possible_joins(layer) == [
        PossibleJoin((("invoices", "CustomerID"), ("orders", "CustomerID"))),
        PossibleJoin((("customers", "number"), ("visits", "number"))),
    ]


def test_two_columns_of_one_table_never_join_each_other():
    def to_airports(table, column):
        return Relationship(
            "airports", f"{table}.{column} = airports.id", "many_to_one"
        )

    layer = SemanticLayer(
        target="air",
        tables={
            "airports": _table("airports", {"id": "integer"}),
            "flights": _table(
                "flights",
                {"origin_ref": "integer", "dest_ref": "integer"},
                [to_airports("flights", "origin_ref"), to_airports("flights", "dest_ref")],
            ),
            "hubs": _table("hubs", {"airport_ref": "integer"}, [to_airports("hubs", "airport_ref")]),
        },
    )

    joins = possible_joins(layer)

    assert [join.columns for join in joins] == [
        (("flights", "dest_ref"), ("hubs", "airport_ref")),
        (("flights", "origin_ref"), ("hubs", "airport_ref")),
    ]
    assert {join.references for join in joins} == {("airports", "id")}


def test_own_primary_keys_and_amounts_sharing_a_name_are_not_joins():
    layer = SemanticLayer(
        target="shop",
        tables={
            name: _table(name, {"uuid": "uuid", "paid": "numeric(10,2)"}, primary="uuid")
            for name in ("users", "orders", "invoices")
        },
    )

    assert possible_joins(layer) == []


def test_flags_and_words_named_like_keys_are_not_joins():
    layer = SemanticLayer(
        target="shop",
        tables={
            # MySQL's introspection stores tinyint(1) as tinyint.
            name: _table(
                name, {"paid": "boolean", "is_valid": "tinyint", "amount_paid": "integer"}
            )
            for name in ("orders", "invoices")
        },
    )

    assert possible_joins(layer) == []


def test_uuid_and_text_columns_do_not_join_by_name():
    layer = SemanticLayer(
        target="shop",
        tables={
            "orders": _table("orders", {"user_uuid": "uuid"}),
            "events": _table("events", {"user_uuid": "varchar"}),
            "visits": _table("visits", {"user_uuid": "uuid"}),
        },
    )

    assert possible_joins(layer) == [
        PossibleJoin((("orders", "user_uuid"), ("visits", "user_uuid")))
    ]


def test_declared_foreign_keys_are_not_repeated_as_possible_joins():
    layer = SemanticLayer(
        target="shop",
        tables={
            "customers": _table("customers", {"customer_id": "integer"}),
            "orders": _table(
                "orders",
                {"customer_id": "integer"},
                [
                    Relationship(
                        "customers",
                        "orders.customer_id = customers.customer_id",
                        "many_to_one",
                    )
                ],
            ),
        },
    )

    assert possible_joins(layer) == []


def test_both_schema_texts_show_possible_joins_apart_from_foreign_keys():
    layer = _archeology()

    verbose = _format_semantic_schema(layer)
    compact = format_semantic_schema_compact(layer)

    assert (
        "Possible joins (not declared foreign keys; the columns hold the same key):\n"
        "  scanmesh.zoneref = scanprocessing.zoneref (all reference sites.zoneregistry)"
    ) in verbose
    assert "many_to_one to sites: scanmesh.zoneref = sites.zoneregistry" in verbose
    assert "\n~scanmesh.zoneref\tscanprocessing.zoneref\t=sites.zoneregistry" in compact
    assert "~table.column<TAB>table.column... possible join" in compact.splitlines()[0]

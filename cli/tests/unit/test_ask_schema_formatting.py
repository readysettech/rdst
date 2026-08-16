from features.ask.engine.ask3.phases.schema import _format_semantic_schema
from features.schema.semantic_models import (
    ColumnAnnotation,
    SemanticLayer,
    TableAnnotation,
)


def test_semantic_schema_includes_every_discovered_enum_value() -> None:
    values = {f"CODE_{index}": f"Meaning {index}" for index in range(6)}
    layer = SemanticLayer(
        target="fixture",
        tables={
            "schools": TableAnnotation(
                name="schools",
                columns={
                    "category": ColumnAnnotation(
                        name="category",
                        data_type="enum",
                        enum_values=values,
                    )
                },
            )
        },
    )

    formatted = _format_semantic_schema(layer)

    for value, meaning in values.items():
        assert f"{value}={meaning}" in formatted


def test_semantic_schema_does_not_expose_todo_enum_placeholders() -> None:
    layer = SemanticLayer(
        target="fixture",
        tables={
            "schools": TableAnnotation(
                name="schools",
                columns={
                    "category": ColumnAnnotation(
                        name="category",
                        data_type="enum",
                        enum_values={"SSS": "TODO: describe 'SSS'"},
                    )
                },
            )
        },
    )

    formatted = _format_semantic_schema(layer)

    assert "[enum: SSS]" in formatted
    assert "TODO:" not in formatted


def test_semantic_schema_does_not_repeat_self_describing_enum_meaning() -> None:
    layer = SemanticLayer(
        target="fixture",
        tables={
            "expense": TableAnnotation(
                name="expense",
                columns={
                    "description": ColumnAnnotation(
                        name="description",
                        data_type="enum",
                        enum_values={
                            "Water, Veggie tray, supplies": "Water, Veggie tray, supplies"
                        },
                    )
                },
            )
        },
    )

    formatted = _format_semantic_schema(layer)

    assert "[enum: Water, Veggie tray, supplies]" in formatted
    assert "supplies=Water" not in formatted

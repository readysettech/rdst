import json
from unittest.mock import Mock, patch

from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.phases.schema import (
    ADAPTIVE_SCHEMA_FORMAT_VERSION,
    COMPACT_SCHEMA_FORMAT_VERSION,
    VERBOSE_SCHEMA_FORMAT_VERSION,
    _choose_semantic_schema_text,
    _compact_relationship_line,
    _compact_schema_code,
    _compact_schema_escape,
    _format_semantic_schema,
    format_semantic_schema_compact,
    load_schema,
    select_semantic_schema_serialization,
)
from features.schema.semantic_models import (
    ColumnAnnotation,
    CustomType,
    Extension,
    Relationship,
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


def test_compact_schema_retains_every_effective_semantic_field() -> None:
    layer = SemanticLayer(
        target="fixture",
        tables={
            'odd|table "name"': TableAnnotation(
                name='odd|table "name"',
                description="Table description",
                business_context="Business context",
                columns={
                    "status,value": ColumnAnnotation(
                        name="status,value",
                        data_type="custom type",
                        description="Column description",
                        enum_values={
                            "A": "Active",
                            "B": "B",
                            "C": "TODO: describe C",
                        },
                        value_pattern="comma,separated",
                        null_fraction=0.125,
                        distinct_count=1234,
                    )
                },
                relationships=[
                    Relationship(
                        target_table="other",
                        join_pattern='"odd|table".id = other.odd_id',
                        relationship_type="one_to_many",
                    )
                ],
            )
        },
        extensions={
            "pgx_ulid": Extension(
                name="pgx_ulid",
                version="1.0",
                description="ULID support",
                types_provided=["ulid"],
            )
        },
        custom_types={
            "status": CustomType(
                name="status",
                type_category="enum",
                enum_values=["A", "B"],
            )
        },
    )

    compact = format_semantic_schema_compact(layer)
    (
        header,
        default_type_line,
        table_line,
        table_metadata_line,
        column_metadata_line,
        relationship_line,
        type_context_line,
    ) = compact.splitlines()
    type_context = json.loads(type_context_line.partition("=")[2])

    assert header.startswith(COMPACT_SCHEMA_FORMAT_VERSION)
    assert default_type_line == "!default_type\tcustom type"
    assert table_line == '@odd|table "name"\tstatus,value'
    assert table_metadata_line == "#\td=Table description\tb=Business context"
    assert column_metadata_line == (
        "+status,value\td=Column description\t"
        'e=[["A","Active"],"B","C"]\tp=comma,separated\tn=12%\tk=1,234'
    )
    assert relationship_line == (
        '>!\tone_to_many\tother\t"odd|table".id = other.odd_id'
    )
    assert type_context_line.startswith("!database_type_context=")
    assert "pgx_ulid v1.0" in type_context
    assert "Types: ulid" in type_context
    assert "status (enum): [A, B]" in type_context


def test_compact_schema_keeps_all_tables_columns_and_relationships() -> None:
    layer = SemanticLayer(
        target="fixture",
        tables={
            "a": TableAnnotation(
                name="a",
                columns={
                    "id": ColumnAnnotation(name="id", data_type="bigint"),
                    "value": ColumnAnnotation(name="value", data_type="text"),
                },
                relationships=[
                    Relationship(
                        target_table="b",
                        join_pattern="a.b_id = b.id",
                        relationship_type="many_to_one",
                    )
                ],
            ),
            "b": TableAnnotation(
                name="b",
                columns={"id": ColumnAnnotation(name="id", data_type="bigint")},
            ),
        },
    )

    lines = format_semantic_schema_compact(layer).splitlines()[1:]

    assert lines == [
        "!default_type\tbigint",
        "!type\t0\ttext",
        "@a\tid\tvalue:0",
        ">b_id\tb",
        "@b\tid",
    ]


def test_compact_schema_escapes_every_line_delimiter() -> None:
    assert _compact_schema_escape("a:b\\c\td\re\nf") == r"a\:b\\c\td\re\nf"


def test_compact_schema_codes_are_unique_beyond_single_character_alphabet() -> None:
    codes = [_compact_schema_code(index) for index in range(130)]

    assert len(codes) == len(set(codes))
    assert codes[0] == "0"
    assert codes[61] == "z"
    assert codes[62] == "10"


def test_compact_relationship_records_non_default_target_and_type() -> None:
    relationship = Relationship(
        target_table="accounts",
        join_pattern="events.owner = accounts.slug",
        relationship_type="one_to_one",
    )

    assert _compact_relationship_line("events", relationship) == (
        ">owner\taccounts\tslug\tone_to_one"
    )


def test_compact_relationship_falls_back_without_losing_unusual_join() -> None:
    relationship = Relationship(
        target_table="accounts",
        join_pattern="lower(events.owner) = accounts.slug",
        relationship_type="one_to_one",
    )

    assert _compact_relationship_line("events", relationship) == (
        ">!\tone_to_one\taccounts\tlower(events.owner) = accounts.slug"
    )


def test_adaptive_schema_keeps_verbose_at_exactly_fifteen_percent_savings() -> None:
    formatted, version = _choose_semantic_schema_text("v" * 100, "c" * 85)

    assert formatted == "v" * 100
    assert version == VERBOSE_SCHEMA_FORMAT_VERSION


def test_adaptive_schema_uses_compact_above_fifteen_percent_savings() -> None:
    formatted, version = _choose_semantic_schema_text("v" * 100, "c" * 84)

    assert formatted == "c" * 84
    assert version == COMPACT_SCHEMA_FORMAT_VERSION


def test_adaptive_schema_records_real_serialization_measurements() -> None:
    columns = {
        f"column_{index}": ColumnAnnotation(name=f"column_{index}", data_type="bigint")
        for index in range(200)
    }
    layer = SemanticLayer(
        target="fixture",
        tables={"events": TableAnnotation(name="events", columns=columns)},
    )

    serialization = select_semantic_schema_serialization(layer)

    assert serialization.policy == ADAPTIVE_SCHEMA_FORMAT_VERSION
    assert serialization.format_version == COMPACT_SCHEMA_FORMAT_VERSION
    assert serialization.compact_chars < serialization.verbose_chars * 0.85
    assert serialization.compact_savings_ratio > 0.15


def test_adaptive_schema_keeps_verbose_when_compact_has_header_overhead() -> None:
    layer = SemanticLayer(
        target="fixture",
        tables={
            "events": TableAnnotation(
                name="events",
                columns={"id": ColumnAnnotation(name="id", data_type="bigint")},
            )
        },
    )

    serialization = select_semantic_schema_serialization(layer)

    assert serialization.policy == ADAPTIVE_SCHEMA_FORMAT_VERSION
    assert serialization.format_version == VERBOSE_SCHEMA_FORMAT_VERSION
    assert serialization.formatted == _format_semantic_schema(layer)


def test_adaptive_schema_retains_lossless_compact_context_fallback() -> None:
    layer = SemanticLayer(target="fixture")

    with (
        patch(
            "features.ask.engine.ask3.phases.schema._format_semantic_schema",
            return_value="verbose" * 100,
        ),
        patch(
            "features.ask.engine.ask3.phases.schema.format_semantic_schema_compact",
            return_value="compact" * 100,
        ),
    ):
        serialization = select_semantic_schema_serialization(layer)

    assert serialization.format_version == VERBOSE_SCHEMA_FORMAT_VERSION
    assert serialization.formatted == "verbose" * 100
    assert serialization.compact_fallback == "compact" * 100


def test_schema_phase_uses_adaptive_format_and_records_measurements() -> None:
    columns = {
        f"column_{index}": ColumnAnnotation(name=f"column_{index}", data_type="bigint")
        for index in range(200)
    }
    layer = SemanticLayer(
        target="fixture",
        tables={"events": TableAnnotation(name="events", columns=columns)},
    )
    manager = Mock()
    manager.exists.return_value = True
    manager.load.return_value = layer
    ctx = Ask3Context(question="show events", target="fixture")

    result = load_schema(ctx, Mock(), manager)

    assert result.schema_format == COMPACT_SCHEMA_FORMAT_VERSION
    assert result.schema_format_policy == ADAPTIVE_SCHEMA_FORMAT_VERSION
    assert result.schema_compact_savings_ratio > 0.15
    assert result.schema_formatted == format_semantic_schema_compact(layer)


def test_schema_phase_auto_initializes_persists_and_continues() -> None:
    columns = {
        f"column_{index}": ColumnAnnotation(name=f"column_{index}", data_type="bigint")
        for index in range(200)
    }
    layer = SemanticLayer(
        target="fixture",
        tables={
            "events": TableAnnotation(
                name="events",
                columns=columns,
            )
        },
    )
    manager = Mock()
    manager.exists.return_value = False
    introspector = Mock()
    introspector.introspect.return_value = layer
    ctx = Ask3Context(
        question="show events",
        target="fixture",
        target_config={"engine": "postgresql", "database": "fixture"},
    )

    with patch(
        "features.schema.semantic_layer.introspector.SchemaIntrospector",
        return_value=introspector,
    ) as introspector_class:
        result = load_schema(ctx, Mock(), manager)

    introspector_class.assert_called_once_with(ctx.target_config)
    introspector.introspect.assert_called_once_with(
        target_name="fixture", enum_threshold=20, sample_enums=True
    )
    manager.save.assert_called_once_with(layer)
    assert list(result.schema_info.tables) == ["events"]
    assert result.schema_formatted
    assert result.schema_format_policy == ADAPTIVE_SCHEMA_FORMAT_VERSION
    assert result.schema_format == COMPACT_SCHEMA_FORMAT_VERSION
    assert result.schema_formatted == format_semantic_schema_compact(layer)


def test_schema_phase_does_not_reinitialize_complete_existing_layer() -> None:
    layer = SemanticLayer(
        target="fixture",
        tables={
            "events": TableAnnotation(
                name="events",
                columns={"id": ColumnAnnotation(name="id", data_type="bigint")},
            )
        },
    )
    manager = Mock()
    manager.exists.return_value = True
    manager.load.return_value = layer
    ctx = Ask3Context(
        question="show events",
        target="fixture",
        target_config={"engine": "postgresql", "database": "fixture"},
    )

    with patch(
        "features.schema.semantic_layer.introspector.SchemaIntrospector"
    ) as introspector_class:
        result = load_schema(ctx, Mock(), manager)

    introspector_class.assert_not_called()
    manager.save.assert_not_called()
    assert list(result.schema_info.tables) == ["events"]


def test_schema_phase_does_not_overwrite_incomplete_existing_layer() -> None:
    incomplete = SemanticLayer(
        target="fixture",
        tables={
            "events": TableAnnotation(
                name="events",
                columns={"id": ColumnAnnotation(name="id")},
            )
        },
    )
    refreshed = SemanticLayer(
        target="fixture",
        tables={
            "events": TableAnnotation(
                name="events",
                columns={"id": ColumnAnnotation(name="id", data_type="bigint")},
            )
        },
    )
    manager = Mock()
    manager.exists.return_value = True
    manager.load.return_value = incomplete
    introspector = Mock()
    introspector.introspect.return_value = refreshed
    ctx = Ask3Context(
        question="show events",
        target="fixture",
        target_config={"engine": "postgresql", "database": "fixture"},
    )

    with patch(
        "features.schema.semantic_layer.introspector.SchemaIntrospector",
        return_value=introspector,
    ):
        result = load_schema(ctx, Mock(), manager)

    manager.save.assert_not_called()
    assert result.schema_info.tables["events"].columns["id"].data_type == "bigint"

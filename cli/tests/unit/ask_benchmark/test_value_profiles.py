import hashlib
import json

from devtools.ask_benchmark.bird_dataset import DATASET_REVISION
from devtools.ask_benchmark.executor import MySQLConnectionConfig
from devtools.ask_benchmark.semantic import semantic_content_hash
from devtools.ask_benchmark.value_profiles import (
    VALUE_GROUNDING_CONTEXT_VERSION,
    VALUE_PROFILE_CONTEXT_VERSION,
    VALUE_PROFILE_FORMAT_VERSION,
    ExactValueProfile,
    build_exact_value_profiles,
)
from features.schema.semantic_models import (
    ColumnAnnotation,
    Relationship,
    SemanticLayer,
    TableAnnotation,
)


def _write_profile(tmp_path, columns):
    path = tmp_path / "school.values.json"
    path.write_text(
        json.dumps(
            {
                "format_version": VALUE_PROFILE_FORMAT_VERSION,
                "target": "school",
                "columns": columns,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_exact_value_profile_matches_question_phrases_and_column_collisions(tmp_path):
    profile = ExactValueProfile(
        _write_profile(
            tmp_path,
            [
                {
                    "table": "schools",
                    "column": "County",
                    "values": [{"value": "Monterey", "count": 42}],
                },
                {
                    "table": "schools",
                    "column": "City",
                    "values": [{"value": "Monterey", "count": 7}],
                },
                {
                    "table": "schools",
                    "column": "StatusType",
                    "values": [{"value": "Active", "count": 100}],
                },
            ],
        )
    )

    result = profile.match("List active schools in Monterey")

    assert [match.value for match in result.matches] == ["Monterey"]
    assert [item.column for item in result.matches[0].occurrences] == [
        "City",
        "County",
    ]
    assert [item.rank for item in result.matches[0].occurrences] == [1, 2]
    assert result.context_version == VALUE_GROUNDING_CONTEXT_VERSION
    assert "schools.City" in result.context
    assert "schools.County" in result.context
    assert "rows" not in result.context
    assert result.to_dict()["context_chars"] == len(result.context)
    assert (
        result.to_dict()["context_sha256"]
        == hashlib.sha256(result.context.encode()).hexdigest()
    )


def test_exact_value_profile_normalizes_connectors_and_punctuation(tmp_path):
    profile = ExactValueProfile(
        _write_profile(
            tmp_path,
            [
                {
                    "table": "expense",
                    "column": "expense_description",
                    "values": [{"value": "Water, Veggie tray, supplies", "count": 1}],
                }
            ],
        )
    )

    result = profile.match("Who paid for water, veggie tray and supplies?")

    assert len(result.matches) == 1
    assert result.matches[0].match_kind == "connector-normalized"
    assert result.matches[0].value == "Water, Veggie tray, supplies"


def test_exact_value_profile_ignores_numeric_and_generic_single_tokens(tmp_path):
    profile = ExactValueProfile(
        _write_profile(
            tmp_path,
            [
                {
                    "table": "events",
                    "column": "label",
                    "values": [
                        {"value": "2013", "count": 4},
                        {"value": "name", "count": 3},
                    ],
                }
            ],
        )
    )

    result = profile.match("What name was used in 2013?")

    assert result.matches == ()
    assert result.context == ""


def test_exact_value_profile_ignores_lowercase_singleton_collision(tmp_path):
    profile = ExactValueProfile(
        _write_profile(
            tmp_path,
            [
                {
                    "table": "schools",
                    "column": "surname",
                    "values": [
                        {"value": "Price", "count": 7},
                        {"value": "Monterey", "count": 130},
                    ],
                }
            ],
        )
    )

    result = profile.match("List high schools in Monterey with reduced price meals")

    assert [match.value for match in result.matches] == ["Monterey"]


def test_exact_value_profile_allows_quoted_lowercase_singleton(tmp_path):
    profile = ExactValueProfile(
        _write_profile(
            tmp_path,
            [
                {
                    "table": "cards",
                    "column": "name",
                    "values": [{"value": "solitude", "count": 1}],
                }
            ],
        )
    )

    result = profile.match('Find the card named "solitude"')

    assert [match.value for match in result.matches] == ["solitude"]


def test_exact_value_profile_deduplicates_connector_variant(tmp_path):
    profile = ExactValueProfile(
        _write_profile(
            tmp_path,
            [
                {
                    "table": "expense",
                    "column": "description",
                    "values": [{"value": "Water and supplies", "count": 1}],
                }
            ],
        )
    )

    result = profile.match('Find "Water and supplies"')

    assert [match.value for match in result.matches] == ["Water and supplies"]


def test_exact_value_profile_ranks_plural_column_by_question_and_support(tmp_path):
    layer = SemanticLayer(
        target="school",
        tables={
            "cards": TableAnnotation(
                name="cards",
                columns={
                    "type": ColumnAnnotation(name="type", data_type="text"),
                    "types": ColumnAnnotation(name="types", data_type="text"),
                },
            )
        },
    )
    profile = ExactValueProfile(
        _write_profile(
            tmp_path,
            [
                {
                    "table": "cards",
                    "column": "type",
                    "values": [{"value": "Creature", "count": 1}],
                },
                {
                    "table": "cards",
                    "column": "types",
                    "values": [{"value": "Creature", "count": 24_229}],
                },
            ],
        ),
        semantic_layer=layer,
    )

    result = profile.match("Find the card of type Creature")

    assert [item.column for item in result.matches[0].occurrences] == [
        "types",
        "type",
    ]
    assert [item.question_overlap for item in result.matches[0].occurrences] == [
        3,
        3,
    ]
    assert "1. cards.types; 2. cards.type" in result.context
    assert "24229" not in result.context


def test_exact_value_profile_suppresses_non_value_schema_name_collision(tmp_path):
    layer = SemanticLayer(
        target="school",
        tables={
            "frpm": TableAnnotation(
                name="frpm",
                columns={
                    "Enrollment (K-12)": ColumnAnnotation(
                        name="Enrollment (K-12)", data_type="int"
                    ),
                    "School Type": ColumnAnnotation(
                        name="School Type", data_type="text"
                    ),
                },
            )
        },
    )
    profile = ExactValueProfile(
        _write_profile(
            tmp_path,
            [
                {
                    "table": "frpm",
                    "column": "School Type",
                    "values": [{"value": "K-12", "count": 100}],
                }
            ],
        ),
        semantic_layer=layer,
    )

    result = profile.match("Compare K-12 enrollment")

    assert result.matches == ()
    assert result.context == ""
    assert [item.normalized_value for item in result.suppressed_schema_matches] == [
        "k 12"
    ]
    assert result.suppressed_schema_matches[0].schema_locations == (
        "frpm.Enrollment (K-12)",
    )


def test_exact_value_profile_emits_only_declared_join_paths(tmp_path):
    cards = TableAnnotation(
        name="cards",
        columns={"artist": ColumnAnnotation(name="artist", data_type="text")},
        relationships=[
            Relationship(
                target_table="foreign_data",
                join_pattern="cards.uuid = foreign_data.uuid",
                relationship_type="one_to_many",
            )
        ],
    )
    foreign_data = TableAnnotation(
        name="foreign_data",
        columns={"language": ColumnAnnotation(name="language", data_type="text")},
    )
    unrelated = TableAnnotation(
        name="unrelated",
        columns={"label": ColumnAnnotation(name="label", data_type="text")},
    )
    layer = SemanticLayer(
        target="school",
        tables={
            "cards": cards,
            "foreign_data": foreign_data,
            "unrelated": unrelated,
        },
    )
    profile = ExactValueProfile(
        _write_profile(
            tmp_path,
            [
                {
                    "table": "cards",
                    "column": "artist",
                    "values": [{"value": "Matthew Wilson", "count": 10}],
                },
                {
                    "table": "foreign_data",
                    "column": "language",
                    "values": [{"value": "French", "count": 20}],
                },
                {
                    "table": "unrelated",
                    "column": "label",
                    "values": [{"value": "French", "count": 1}],
                },
            ],
        ),
        semantic_layer=layer,
    )

    result = profile.match("Find the French card by Matthew Wilson")

    assert len(result.declared_join_paths) == 1
    assert result.declared_join_paths[0].tables == ("cards", "foreign_data")
    assert result.declared_join_paths[0].joins == ("cards.uuid = foreign_data.uuid",)
    assert "cards.uuid = foreign_data.uuid" in result.context
    assert "unrelated" not in result.context.split("Declared join paths", 1)[1]


def test_complete_frozen_profiles_are_verified_without_rewriting(tmp_path):
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    output_dir.mkdir()
    schema = "target: fixture\ntables: {}\n"
    source_path = source_dir / "fixture.yaml"
    output_path = output_dir / "fixture.yaml"
    source_path.write_text(schema)
    output_path.write_text(schema)
    profile_path = _write_profile(output_dir, [])
    profile_path.rename(output_dir / "fixture.values.json")
    profile_path = output_dir / "fixture.values.json"
    schema_hash = semantic_content_hash(source_path)
    provenance_path = output_dir / "provenance.json"
    provenance_path.write_text(
        json.dumps(
            {
                "format_version": VALUE_PROFILE_FORMAT_VERSION,
                "context_version": VALUE_PROFILE_CONTEXT_VERSION,
                "dataset_revision": DATASET_REVISION,
                "source_context": "auto-init",
                "output_context": "auto-init-profiled-values",
                "construction_policy": "local-exact-distinct-values-v1",
                "max_values_per_column": 10_000,
                "max_value_chars": 160,
                "indexed_data_types": ["enum", "text", "varchar"],
                "contains_questions": False,
                "contains_evidence": False,
                "contains_gold_sql": False,
                "contains_bird_curated_descriptions": False,
                "contains_database_values": True,
                "source_schema_hashes": {"fixture": schema_hash},
                "output_schema_hashes": {"fixture": schema_hash},
                "value_profile_hashes": {
                    "fixture": hashlib.sha256(profile_path.read_bytes()).hexdigest()
                },
                "database_stats": {
                    "fixture": {
                        "indexed_columns": 0,
                        "indexed_values": 0,
                        "truncated_columns": 0,
                    }
                },
                "wall_time_seconds": 12.5,
            }
        )
    )
    original = provenance_path.read_bytes()

    result = build_exact_value_profiles(
        ["fixture"],
        source_dir,
        output_dir,
        MySQLConnectionConfig(
            host="127.0.0.1",
            port=3306,
            user="unused",
            password="unused",
        ),
    )

    assert result["_reused"] is True
    assert result["wall_time_seconds"] == 12.5
    assert provenance_path.read_bytes() == original

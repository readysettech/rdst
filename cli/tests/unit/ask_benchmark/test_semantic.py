import hashlib
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from devtools.ask_benchmark import semantic as semantic_module
from devtools.ask_benchmark.executor import MySQLConnectionConfig
from devtools.ask_benchmark.models import ContextMode
from devtools.ask_benchmark.provision import (
    DESCRIPTION_PREFIX,
    TABLES_ENTRY,
    ProvisioningError,
)
from devtools.ask_benchmark.schema import load_semantic_schema
from devtools.ask_benchmark.semantic import (
    build_auto_init_layers,
    build_llm_enriched_layers,
    build_semantic_layers,
    install_frozen_llm_enriched_layers,
    semantic_content_hash,
    semantic_dir_for_context,
    semantic_layer_content_hash,
)
from features.schema.semantic_layer.manager import SemanticLayerManager
from features.schema.semantic_models import (
    ColumnAnnotation,
    SemanticLayer,
    TableAnnotation,
)


def test_schema_provenance_contexts_use_separate_directories(tmp_path: Path):
    assert semantic_dir_for_context(tmp_path, ContextMode.AUTO_INIT) == (
        tmp_path / "semantic-auto-init"
    )
    assert semantic_dir_for_context(tmp_path, ContextMode.LLM_ENRICHED) == (
        tmp_path / "semantic-llm-enriched"
    )
    assert semantic_dir_for_context(tmp_path, ContextMode.BIRD_CURATED) == (
        tmp_path / "semantic"
    )


def test_installs_checked_in_llm_enriched_snapshot_with_hash_validation(
    tmp_path: Path, monkeypatch
):
    source_dir = tmp_path / "source"
    frozen_dir = tmp_path / "frozen"
    output_dir = tmp_path / "output"
    source_manager = SemanticLayerManager(base_dir=source_dir)

    source_layer = SemanticLayer(target="fixture")
    source_layer.tables["users"] = TableAnnotation(
        name="users",
        columns={"id": ColumnAnnotation(name="id", data_type="int")},
    )
    source_manager.save(source_layer)
    enriched_layer = deepcopy(source_layer)
    enriched_layer.tables["users"].description = "Annotated users"
    enriched_layer.tables["users"].business_context = "User records"
    enriched_layer.tables["users"].columns["id"].description = "User identifier"

    snapshot_id = "fixture-snapshot"
    annotations = {
        "artifact_schema_version": 1,
        "snapshot_id": snapshot_id,
        "databases": {
            "fixture": {
                "tables": {
                    "users": {
                        "description": "Annotated users",
                        "business_context": "User records",
                        "columns": {"id": "User identifier"},
                    }
                }
            }
        },
    }
    annotation_text = json.dumps(annotations, indent=2, sort_keys=True) + "\n"
    frozen_dir.mkdir()
    (frozen_dir / "annotations.json").write_text(annotation_text, encoding="utf-8")
    provenance = {
        "snapshot_id": snapshot_id,
        "dataset_revision": semantic_module.DATASET_REVISION,
        "source_context": ContextMode.AUTO_INIT.value,
        "output_context": ContextMode.LLM_ENRICHED.value,
        "model": {"model": "claude-sonnet-4-6"},
        "content_audit": {
            field: False
            for field in (
                "contains_raw_sample_rows",
                "contains_questions",
                "contains_evidence",
                "contains_gold_sql",
                "contains_bird_curated_descriptions",
                "contains_call_receipts",
                "contains_credentials",
            )
        },
        "source_content_hashes": {
            "fixture": semantic_content_hash(source_dir / "fixture.yaml")
        },
        "annotations_sha256": hashlib.sha256(annotation_text.encode()).hexdigest(),
        "output_content_hashes": {
            "fixture": semantic_layer_content_hash(enriched_layer)
        },
        "annotated_tables": 1,
        "annotated_columns": 1,
    }
    (frozen_dir / "provenance.json").write_text(
        json.dumps(provenance), encoding="utf-8"
    )
    monkeypatch.setattr(semantic_module, "FROZEN_LLM_ENRICHED_SNAPSHOT_ID", snapshot_id)
    monkeypatch.setattr(semantic_module, "FROZEN_LLM_ENRICHED_DIR", frozen_dir)

    installed = install_frozen_llm_enriched_layers(["fixture"], source_dir, output_dir)

    assert installed == [output_dir / "fixture.yaml"]
    assert semantic_content_hash(installed[0]) == semantic_layer_content_hash(
        enriched_layer
    )
    assert json.loads((output_dir / "provenance.json").read_text()) == provenance

    installed[0].write_text("version: 1\ntarget: fixture\n", encoding="utf-8")
    with pytest.raises(ProvisioningError, match="differs.*use --force"):
        install_frozen_llm_enriched_layers(["fixture"], source_dir, output_dir)
    install_frozen_llm_enriched_layers(["fixture"], source_dir, output_dir, force=True)
    assert semantic_content_hash(installed[0]) == semantic_layer_content_hash(
        enriched_layer
    )


def test_checked_in_llm_enriched_snapshot_matches_sanitized_provenance():
    frozen_dir = semantic_module.FROZEN_LLM_ENRICHED_DIR
    provenance = json.loads(
        (frozen_dir / "provenance.json").read_text(encoding="utf-8")
    )
    annotation_path = frozen_dir / "annotations.json"
    annotation_bytes = annotation_path.read_bytes()
    annotations = json.loads(annotation_bytes)

    assert provenance["snapshot_id"] == (
        semantic_module.FROZEN_LLM_ENRICHED_SNAPSHOT_ID
    )
    assert provenance["model"]["model"] == "claude-sonnet-4-6"
    assert provenance["annotated_tables"] == 75
    assert provenance["annotated_columns"] == 798
    assert (
        hashlib.sha256(annotation_bytes).hexdigest() == provenance["annotations_sha256"]
    )
    assert set(annotations["databases"]) == set(provenance["output_content_hashes"])
    assert not list(frozen_dir.glob("*.yaml"))
    table_count = sum(
        len(database["tables"]) for database in annotations["databases"].values()
    )
    column_count = sum(
        len(table["columns"])
        for database in annotations["databases"].values()
        for table in database["tables"].values()
    )
    assert table_count == 75
    assert column_count == 798


def test_build_llm_enriched_layers_uses_rdst_annotation_results(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "source"
    output = tmp_path / "output"
    manager = SemanticLayerManager(base_dir=source)
    layer = SemanticLayer(target="fixture")
    layer.tables["users"] = TableAnnotation(
        name="users",
        columns={"id": ColumnAnnotation(name="id", data_type="int")},
    )
    manager.save(layer)

    class Annotator:
        def annotate_table(self, table_name, table, sample_data, context, only_columns):
            assert table_name == "users"
            assert sample_data == [{"id": 1}]
            assert context == "fixture database"
            assert only_columns == ["id"]
            return {
                "description": "User records",
                "business_context": "Created for each user",
                "columns": {"id": {"description": "User identifier"}},
            }

    monkeypatch.setattr(
        "devtools.ask_benchmark.semantic.AnnotateService._create_sample_data_function",
        lambda _self, _target, _config, _rows, **_kwargs: lambda _table: [{"id": 1}],
    )
    paths, stats = build_llm_enriched_layers(
        ["fixture"],
        source,
        output,
        MySQLConnectionConfig("localhost", 3306, "user", "password"),
        Annotator(),
    )

    enriched = SemanticLayerManager(base_dir=output).load("fixture")
    assert paths == [output / "fixture.yaml"]
    assert enriched.tables["users"].description == "User records"
    assert enriched.tables["users"].columns["id"].description == "User identifier"
    assert stats["annotated_tables"] == 1
    assert stats["annotated_columns"] == 1
    assert stats["sample_rows_fetched_total"] == 1
    assert stats["sample_order_policy"] == "primary-key-else-row-sha256-v2"
    assert stats["sample_inputs"]["fixture"]["users"] == {
        "rows_fetched": 1,
        "rows_in_prompt": 1,
        "rows_sha256": (
            "bb41eeeedb7789a3482cc74a1ac8d84effb2a508b753948130e3958c39004120"
        ),
        "order_by_columns": ["id"],
        "order_strategy": "row-sha256",
    }


def test_build_auto_init_layers_freezes_actual_introspection(tmp_path: Path):
    layer = SemanticLayer(target="fixture")
    layer.tables["users"] = TableAnnotation(
        name="users",
        columns={"id": ColumnAnnotation(name="id", data_type="int")},
    )
    introspector = Mock()
    introspector.introspect.return_value = layer
    connection = MySQLConnectionConfig(
        "localhost", 3306, "rdst_bird", "password", database_prefix="bird_"
    )

    with patch(
        "devtools.ask_benchmark.semantic.SchemaIntrospector",
        return_value=introspector,
    ) as introspector_type:
        paths = build_auto_init_layers(
            ["fixture", "fixture"],
            tmp_path / "semantic-auto-init",
            connection,
        )

    assert len(paths) == 1
    assert SemanticLayer.load(paths[0]).tables["users"].columns["id"].data_type == "int"
    target_config = introspector_type.call_args.args[0]
    assert target_config["database"] == "bird_fixture"
    assert target_config["user"].startswith("rdst_bird_")
    introspector.introspect.assert_called_once_with(
        target_name="fixture", enum_threshold=20, sample_enums=True
    )


def test_build_semantic_layer_from_bird_metadata(tmp_path: Path):
    extracted = tmp_path / "extracted"
    tables_path = extracted / TABLES_ENTRY
    tables_path.parent.mkdir(parents=True)
    tables_path.write_text(
        json.dumps(
            [
                {
                    "db_id": "fixture",
                    "table_names_original": ["parents", "children"],
                    "column_names_original": [
                        [-1, "*"],
                        [0, "id"],
                        [1, "id"],
                        [1, "parent_id"],
                    ],
                    "column_types": ["text", "integer", "integer", "integer"],
                    "primary_keys": [1, 2],
                    "foreign_keys": [[3, 1]],
                }
            ]
        ),
        encoding="utf-8",
    )
    descriptions = extracted / DESCRIPTION_PREFIX / "fixture" / "database_description"
    descriptions.mkdir(parents=True)
    (descriptions / "children.csv").write_text(
        "original_column_name,column_name,column_description,data_format,value_description\n"
        "id,,Child identifier,integer,\n"
        "parent_id,,Owning parent,integer,\n",
        encoding="utf-8",
    )

    paths = build_semantic_layers(extracted, tmp_path / "semantic")
    layer = SemanticLayer.load(paths[0])
    provenance = json.loads(
        (tmp_path / "semantic" / "provenance.json").read_text(encoding="utf-8")
    )

    assert provenance["output_context"] == ContextMode.BIRD_CURATED.value
    assert provenance["contains_questions"] is False
    assert provenance["contains_evidence"] is False
    assert provenance["contains_gold_sql"] is False
    assert (
        provenance["output_hashes"]["fixture"]
        == hashlib.sha256(paths[0].read_bytes()).hexdigest()
    )
    assert set(provenance["source_hashes"]) == {
        TABLES_ENTRY,
        f"{DESCRIPTION_PREFIX}fixture/database_description/children.csv",
    }
    assert (
        layer.tables["children"].columns["id"].description
        == "Child identifier; Format: integer"
    )
    relationship = layer.tables["children"].relationships[0]
    assert relationship.join_pattern == "children.parent_id = parents.id"
    assert relationship.relationship_type == "many_to_one"
    formatted = load_semantic_schema(tmp_path / "semantic", "fixture")
    assert "many_to_one to parents: children.parent_id = parents.id" in formatted
